"""期中复习讲义批量生成工具。

只读访问题库数据库与 rich_content 快照，按"章节 → 小节 → 题型"组织题目，
生成 A/B/C 三卷、教师版与学生版 docx。本模块不写题库，不调用模型。

组卷策略（难度取题库 difficulty 1-9 档，考频取 question_frequency_cache 的
贝叶斯加权分；小节按授课顺序、节内题型按期中出现卷数排序，各小节轮流出题
保证章内前后都有覆盖）：
  A卷 典型卷   难度 2-5 为主，每题型取考频最高题，高频题型补第二题，
               高频且稍难（难度6）的题最多 3 道；
  B卷 巩固卷   难度 ≤4 硬封顶，每题型按难度递增连取最多 3 题，含基础解答题；
  C卷 培优卷   难度 4-6 为主体，纳入难度 7-9 综合题（最多 4 道），不含送分题。
三卷之间同章不重复选题。
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor
from lxml import etree

from question_bank.database.paths import project_data_root
from question_bank.document_pipeline.word_renderer import (
    SharedWordQuestionRenderer,
    WordStyleProfile,
    _strip_leading_question_number,
    add_answer_space,
    answer_space_lines,
    validate_docx,
)
from question_bank.services.rich_content_service import load_question_rich_content
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

MIDTERM_GRADE = "八年级"
MIDTERM_SEMESTER = "上学期"
MIDTERM_CHAPTERS = ("c01", "c02", "c03", "c04", "c05")
_MIDTERM_PAPERS = 38
_FINAL_PAPERS = 26
_SCORE_PREFIX = re.compile(r"（(\d+)分）")
_VOLUME_SUBTITLE = {
    "A": "典型卷 · 面向平均得分率约65%的班级：按授课顺序排布高频考点，难度2-5为主，高频题型含少量难度6题",
    "B": "基础巩固卷 · 面向薄弱生：难度≤4封顶，同题型按难度递增梯度连练，巩固基础",
    "C": "培优卷 · 面向尖子生：中档（4-6）为主体，纳入综合难题，不含送分题",
}


def difficulty_tag(value: int | None) -> str:
    level = int(value or 0)
    if level <= 3:
        return "基础"
    if level <= 5:
        return "中档"
    return "综合"


@dataclass
class Question:
    id: int
    paper_id: int
    number: str
    question_type: str
    text: str
    difficulty: int
    score: int | None
    midterm_freq: float
    final_freq: float
    school: str
    year_pair: str
    exam_type: str
    section_id: str
    kp_value: str
    rich: dict | None = field(default=None, repr=False, compare=False)


@dataclass
class Group:
    section_id: str
    kp_value: str
    questions: list[Question] = field(default_factory=list)
    midterm_papers: int = 0
    final_papers: int = 0
    error_notes: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        short = self.kp_value.split("｜")[-1]
        return re.sub(r"^[☆*]?\d+\s*", "", short) or short


@dataclass
class Section:
    section_id: str
    display_name: str
    midterm_papers: int
    final_papers: int
    groups: list[Group] = field(default_factory=list)

    @property
    def label(self) -> str:
        chapter_no = int(self.section_id.split("-c")[1][:2])
        short = self.display_name.split("｜")[-1]
        match = re.match(r"[☆*]?(\d+)\s*(.+)", short)
        if match:
            return f"{chapter_no}.{match.group(1)} {match.group(2)}"
        return short


def _parse_school_year(title: str) -> tuple[str, str]:
    match = re.match(r"(\d{4})-(\d{4})学年", title)
    year_pair = f"{match.group(1)}-{match.group(2)}" if match else ""
    start = title.find("广东省")
    end = title.find("八年级")
    school = title[start + 3 : end] if 0 <= start < end else ""
    return school, year_pair


def load_chapter(db_path: Path, chapter_id: str) -> list[Section]:
    volume_prefix = f"bnu24-math-g8-upper-{chapter_id}-"
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT q.id, q.paper_id, q.question_number, q.question_type, q.question_text,
               q.difficulty, p.title, p.exam_type, f.score_midterm, f.score_final,
               sec.tag_value AS section_id
        FROM questions q
        JOIN papers p ON p.id = q.paper_id AND p.deleted_at IS NULL
        JOIN question_tags sec ON sec.question_id = q.id
          AND sec.tag_type = 'curriculum_section'
          AND sec.tag_value LIKE ?
        LEFT JOIN question_frequency_cache f ON f.question_id = q.id
        WHERE q.is_deleted = 0
        GROUP BY q.id
        """,
        (volume_prefix + "%",),
    ).fetchall()

    kp_rows = cur.execute(
        """
        SELECT t.question_id, t.tag_value FROM question_tags t
        JOIN questions q ON q.id = t.question_id AND q.is_deleted = 0
        JOIN question_tags sec ON sec.question_id = q.id
          AND sec.tag_type = 'curriculum_section' AND sec.tag_value LIKE ?
        WHERE t.tag_type = 'knowledge_point'
        """,
        (volume_prefix + "%",),
    ).fetchall()
    error_rows = cur.execute(
        """
        SELECT t.question_id, t.tag_value FROM question_tags t
        JOIN questions q ON q.id = t.question_id AND q.is_deleted = 0
        JOIN question_tags sec ON sec.question_id = q.id
          AND sec.tag_type = 'curriculum_section' AND sec.tag_value LIKE ?
        WHERE t.tag_type = 'error_type'
        """,
        (volume_prefix + "%",),
    ).fetchall()
    coverage = cur.execute(
        """
        SELECT sec.tag_value AS section_id, p.exam_type, COUNT(DISTINCT p.id) AS papers
        FROM question_tags sec
        JOIN questions q ON q.id = sec.question_id AND q.is_deleted = 0
        JOIN papers p ON p.id = q.paper_id AND p.deleted_at IS NULL
        WHERE sec.tag_type = 'curriculum_section' AND sec.tag_value LIKE ?
          AND p.grade = ? AND p.semester = ?
        GROUP BY sec.tag_value, p.exam_type
        """,
        (volume_prefix + "%", MIDTERM_GRADE, MIDTERM_SEMESTER),
    ).fetchall()
    group_coverage = cur.execute(
        """
        SELECT sec.tag_value AS section_id, t.tag_value AS kp, p.exam_type,
               COUNT(DISTINCT p.id) AS papers
        FROM question_tags t
        JOIN questions q ON q.id = t.question_id AND q.is_deleted = 0
        JOIN papers p ON p.id = q.paper_id AND p.deleted_at IS NULL
        JOIN question_tags sec ON sec.question_id = q.id
          AND sec.tag_type = 'curriculum_section' AND sec.tag_value LIKE ?
        WHERE t.tag_type = 'knowledge_point'
          AND p.grade = ? AND p.semester = ?
        GROUP BY sec.tag_value, t.tag_value, p.exam_type
        """,
        (volume_prefix + "%", MIDTERM_GRADE, MIDTERM_SEMESTER),
    ).fetchall()
    conn.close()

    sections_meta: dict[str, dict] = {}
    chapter_key = f"bnu24-math-g8-upper-{chapter_id}"
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume.get("chapters", []):
            if chapter["id"] == chapter_key:
                for section in chapter["sections"]:
                    sections_meta[section["id"]] = section
    section_shorts = {
        str(meta.get("display_name") or "").split("｜")[-1] for meta in sections_meta.values()
    }

    kp_by_question: dict[int, list[str]] = {}
    for row in kp_rows:
        value = str(row["tag_value"])
        parts = value.split("｜")
        if len(parts) >= 3 and parts[2] in section_shorts:
            kp_by_question.setdefault(int(row["question_id"]), []).append(value)
    error_by_question: dict[int, list[str]] = {}
    for row in error_rows:
        error_by_question.setdefault(int(row["question_id"]), []).append(str(row["tag_value"]))

    sections: dict[str, Section] = {}
    groups: dict[tuple[str, str], Group] = {}
    for row in rows:
        section_id = str(row["section_id"])
        if section_id not in sections_meta:
            continue
        qid = int(row["id"])
        kps = kp_by_question.get(qid, [])
        kp_value = kps[0] if kps else f"八年级上册｜本章｜本节综合"
        school, year_pair = _parse_school_year(str(row["title"]))
        text = str(row["question_text"] or "")
        score_match = _SCORE_PREFIX.search(text.split("\n")[0])
        question = Question(
            id=qid,
            paper_id=int(row["paper_id"]),
            number=str(row["question_number"]),
            question_type=str(row["question_type"] or ""),
            text=text,
            difficulty=int(row["difficulty"] or 0),
            score=int(score_match.group(1)) if score_match else None,
            midterm_freq=float(row["score_midterm"] or 0.0),
            final_freq=float(row["score_final"] or 0.0),
            school=school,
            year_pair=year_pair,
            exam_type=str(row["exam_type"] or ""),
            section_id=section_id,
            kp_value=kp_value,
        )
        if section_id not in sections:
            meta = sections_meta[section_id]
            sections[section_id] = Section(
                section_id=section_id,
                display_name=str(meta.get("display_name") or ""),
                midterm_papers=0,
                final_papers=0,
            )
        key = (section_id, kp_value)
        if key not in groups:
            groups[key] = Group(section_id=section_id, kp_value=kp_value)
        groups[key].questions.append(question)

    for row in coverage:
        section = sections.get(str(row["section_id"]))
        if section is None:
            continue
        if row["exam_type"] == "期中":
            section.midterm_papers = int(row["papers"])
        elif row["exam_type"] == "期末":
            section.final_papers = int(row["papers"])
    for row in group_coverage:
        group = groups.get((str(row["section_id"]), str(row["kp"])))
        if group is None:
            continue
        if row["exam_type"] == "期中":
            group.midterm_papers = int(row["papers"])
        elif row["exam_type"] == "期末":
            group.final_papers = int(row["papers"])
    for group in groups.values():
        group.questions.sort(key=lambda q: (-q.midterm_freq, q.difficulty, q.id))
        for question in group.questions:
            for note in error_by_question.get(question.id, []):
                if note not in group.error_notes:
                    group.error_notes.append(note)

    result: list[Section] = []
    for section_id in sorted(sections):
        section = sections[section_id]
        section.groups = sorted(
            (g for g in groups.values() if g.section_id == section_id),
            key=lambda g: (-g.midterm_papers, g.kp_value),
        )
        if section.midterm_papers > 0:
            result.append(section)
    return result


def select_volume(sections: list[Section], volume: str, used_ids: set[int]) -> list:
    """轮流出题保证各小节覆盖，返回 [(Section, [(Group, [Question])])]。"""
    if volume == "A":
        target, per_group, lo, hi = 19, 1, 2, 5
    elif volume == "B":
        target, per_group, lo, hi = 26, 3, 2, 4
    else:
        target, per_group, lo, hi = 15, 1, 4, 6

    quotas = {
        "A": {"选择题": 10, "填空题": 6, "解答题": 7},
        "B": {"选择题": 14, "填空题": 8, "解答题": 4},
        "C": {"选择题": 6, "填空题": 6, "解答题": 7},
    }[volume]
    type_counts: dict[str, int] = {"选择题": 0, "填空题": 0, "解答题": 0}

    def preferred(cands: list[Question]) -> list[Question]:
        """题型配额内优先（软偏好）：返回按剩余配额最大题型置前的候选序列。"""
        def base_type(q: Question) -> str:
            name = q.question_type.split("（")[0]
            return name if name in type_counts else "解答题"
        remaining = {t: quotas[t] - type_counts[t] for t in type_counts}
        best = max(remaining, key=lambda t: remaining[t])
        if remaining[best] <= 0:
            return cands
        preferred_list = [q for q in cands if base_type(q) == best]
        others = [q for q in cands if base_type(q) != best]
        return preferred_list + others

    ordered_groups = [(sec, g) for sec in sections for g in sec.groups]
    picked: dict[tuple[str, str], list[Question]] = {}
    chosen: list[Question] = []
    stems: list[str] = []
    # A/C 卷拒绝高度相似的题干（同一题换出处），B 卷仅拒绝几乎完全相同
    similar_threshold = 0.98 if volume == "B" else 0.85

    def take(question: Question) -> None:
        chosen.append(question)
        used_ids.add(question.id)
        stems.append(re.sub(r"\s+", "", question.text)[:120])
        picked.setdefault((question.section_id, question.kp_value), []).append(question)

    def too_similar(question: Question) -> bool:
        key = re.sub(r"\s+", "", question.text)[:120]
        return any(
            difflib.SequenceMatcher(None, key, seen).ratio() >= similar_threshold
            for seen in stems
        )

    def available(group: Group) -> list[Question]:
        return [
            q
            for q in group.questions
            if q.id not in used_ids and lo <= q.difficulty <= hi
            and not too_similar(q)
        ]

    # 第一轮轮转：每节轮流取其第 r 优先题型
    round_index = 0
    while len(chosen) < target:
        progressed = False
        for sec in sections:
            eligible = [g for g in sec.groups if g.midterm_papers >= 1]
            if round_index >= len(eligible):
                continue
            group = eligible[round_index]
            if volume == "B":
                cands = sorted(available(group), key=lambda q: (q.difficulty, -q.midterm_freq))
            else:
                cands = sorted(available(group), key=lambda q: (-q.midterm_freq, q.difficulty))
            batch = preferred(cands)[:per_group]
            if len(chosen) + len(batch) > target:
                batch = batch[: max(target - len(chosen), 0)]
            for q in batch:
                if q.id not in used_ids:
                    take(q)
                    name = q.question_type.split("（")[0]
                    if name in type_counts:
                        type_counts[name] += 1
                    progressed = True
        if not progressed:
            break
        round_index += 1

    # 卷别专属补充
    if volume == "A":
        for sec, group in ordered_groups:
            if len(chosen) >= 22:
                break
            if group.midterm_papers < 0.4 * _MIDTERM_PAPERS:
                continue
            have = picked.get((sec.section_id, group.kp_value), [])
            cands = [q for q in available(group) if q not in have]
            if cands:
                take(cands[0])
        hard_extra = 0
        for sec, group in ordered_groups:
            if hard_extra >= 3:
                break
            if group.midterm_papers < 0.5 * _MIDTERM_PAPERS:
                continue
            cands = [q for q in available(group) if q.difficulty == 6 and q.midterm_freq >= 0.1]
            if cands:
                take(cands[0])
                hard_extra += 1
    elif volume == "B":
        answer_count = sum(1 for qs in picked.values() for q in qs if q.question_type.startswith("解答题"))
        for sec, group in ordered_groups:
            if answer_count >= 2:
                break
            cands = [
                q for q in group.questions
                if q.id not in used_ids and 2 <= q.difficulty <= 4
                and q.question_type.startswith("解答题")
            ]
            if cands:
                take(cands[0])
                answer_count += 1
    else:
        hard_extra = 0
        for sec, group in ordered_groups:
            if hard_extra >= 4:
                break
            if group.midterm_papers < 2:
                continue
            cands = sorted(
                [q for q in group.questions if q.id not in used_ids and 7 <= q.difficulty <= 9],
                key=lambda q: (-q.midterm_freq, q.difficulty),
            )
            if cands:
                take(cands[0])
                hard_extra += 1
        # 补足目标题量的难度3衔接题
        for sec, group in ordered_groups:
            if len(chosen) >= 16:
                break
            cands = [q for q in available(group) if q.difficulty == 3]
            if not cands and not picked.get((sec.section_id, group.kp_value)):
                cands = [q for q in available(group) if 4 <= q.difficulty <= 6]
            if cands:
                take(cands[0])

    layout: list[tuple[Section, list[tuple[Group, list[Question]]]]] = []
    for sec in sections:
        entries: list[tuple[Group, list[Question]]] = []
        for group in sec.groups:
            items = picked.get((sec.section_id, group.kp_value))
            if items:
                entries.append((group, items))
        layout.append((sec, entries))
    return layout


def render_note(document, text: str, *, size: int = 9, gray: bool = True, bold: bool = False,
                align: WD_ALIGN_PARAGRAPH | None = None, keep_next: bool = False):
    paragraph = document.add_paragraph()
    if align is not None:
        paragraph.alignment = align
    if keep_next:
        paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    if gray:
        run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    return paragraph


def strip_trailing_image_blocks(blocks: list[dict]) -> list[dict]:
    """来源站点的二维码/推广图以纯图片块挂在解析末尾，渲染前剔除。"""
    end = len(blocks)
    for block in reversed(blocks):
        xml = str(block.get("xml") or "")
        text = re.sub(r"\[\[IMAGE:[^\]]*\]\]", "", str(block.get("text") or "")).strip()
        if ("<w:drawing" in xml or "<a:blip" in xml) and not text:
            end -= 1
        else:
            break
    return blocks[:end]


def sanitize_block_xml(block: dict) -> dict:
    """清洗文本节点里的双重转义实体等导入噪音。"""
    xml = str(block.get("xml") or "")
    if "&amp;#160" in xml or "&amp;nbsp" in xml:
        cleaned = xml.replace("MC&amp;#160", "MC")
        cleaned = re.sub(r"([A-Za-z）)])&amp;#160", r"\1", cleaned)
        cleaned = cleaned.replace("&amp;nbsp", " ")
        block = dict(block)
        block["xml"] = cleaned
    return block


def strip_leading_score(blocks: list[dict]) -> tuple[list[dict], int | None]:
    """去掉首块题干开头的（N分），返回 (新块, 分值)。题号由渲染器负责剥离。"""
    if not blocks:
        return blocks, None
    first = dict(blocks[0])
    element = parse_xml(first["xml"])
    _strip_leading_question_number(element)
    runs = [t for t in element.iter(qn("w:t"))]
    full = "".join(t.text or "" for t in runs)
    match = re.match(r"^\s*（(\d+)分）", full)
    score = None
    if match:
        score = int(match.group(1))
        index = match.end()
        for t in runs:
            txt = t.text or ""
            if index <= 0:
                break
            if len(txt) <= index:
                index -= len(txt)
                t.text = ""
            else:
                t.text = txt[index:]
                index = 0
    first["xml"] = etree.tostring(element, encoding="unicode")
    return [first, *blocks[1:]], score


def question_source_line(question: Question) -> str:
    label = "期中" if "期中" in question.exam_type else "期末"
    if question.school:
        return f"来源：{question.school} {question.year_pair} {label} 第{question.number}题"
    return f"来源：{question.year_pair} {label} 第{question.number}题"


def _resolve_asset(source: str) -> Path | None:
    path = Path(source)
    if path.is_file():
        return path
    text = source.replace("\\", "/")
    marker = "user_data/"
    if marker in text:
        candidate = project_data_root() / text.split(marker, 1)[1]
        if candidate.is_file():
            return candidate
    return None


def render_question(
    document,
    renderer: SharedWordQuestionRenderer,
    style: WordStyleProfile,
    index: int,
    question: Question,
    *,
    teacher: bool,
    used_fallbacks: list[str],
) -> None:
    rich = question.rich or load_question_rich_content(question.id)
    question.rich = rich
    blocks = list((rich or {}).get("question_blocks") or [])
    blocks, rich_score = strip_leading_score(blocks) if blocks else ([], None)
    blocks = [sanitize_block_xml(b) for b in blocks]
    score = question.score or rich_score
    tag = difficulty_tag(question.difficulty)
    prefix = f"{index}．（{score}分）【{tag}】" if score else f"{index}．【{tag}】"

    rendered = False
    if blocks:
        before = len(document.paragraphs)
        result = renderer.add_rich_blocks(document, blocks, strip_leading_number=False, inline_prefix=prefix)
        rendered = result.appended and len(document.paragraphs) > before
        if rendered:
            # 题干首段与其后内容同页起步，避免题号孤行
            document.paragraphs[before].paragraph_format.keep_with_next = True
    if not rendered:
        clean = re.sub(r"\[\[IMAGE:[^\]]*\]\]", "", question.text).strip()
        for line_index, line in enumerate(clean.splitlines()):
            if line.strip():
                renderer.add_text(
                    document,
                    f"{prefix} {line.strip()}" if line_index == 0 else line.strip(),
                    question_id=str(question.id),
                )
        used_fallbacks.append(str(question.id))

    render_note(document, question_source_line(question), size=8)

    if teacher:
        answer_blocks = list((rich or {}).get("answer_blocks") or [])
        start = next(
            (
                i
                for i, block in enumerate(answer_blocks)
                if str(block.get("text") or "").strip().startswith(("【分析】", "【解答】", "【答案】"))
            ),
            None,
        )
        if start is not None:
            render_note(document, "—— 以下为解析（学生版不出现）——", size=8,
                        align=WD_ALIGN_PARAGRAPH.CENTER, keep_next=True)
            clean_blocks = [sanitize_block_xml(b) for b in strip_trailing_image_blocks(answer_blocks[start:])]
            renderer.add_rich_blocks(document, clean_blocks)
        else:
            render_note(document, "（题库中该题暂无解析文本）", size=8)
    else:
        lines = answer_space_lines(question.question_type, question.text)
        if lines > 0:
            add_answer_space(document, minimum_lines=lines, content_width_dxa=style.content_width_dxa)


def render_volume(
    out_path: Path,
    chapter_label: str,
    volume: str,
    teacher: bool,
    layout,
    used_fallbacks: list[str],
) -> None:
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = section.right_margin = Cm(1.8)
    section.top_margin = section.bottom_margin = Cm(1.6)

    style = WordStyleProfile()
    renderer = SharedWordQuestionRenderer(style=style, asset_resolver=_resolve_asset)

    render_note(document, "八年级上册 · 期中复习讲义", size=16, gray=False, bold=True,
                align=WD_ALIGN_PARAGRAPH.CENTER)
    render_note(document, f"{chapter_label} · {volume}卷", size=13, gray=False, bold=True,
                align=WD_ALIGN_PARAGRAPH.CENTER)
    render_note(document, _VOLUME_SUBTITLE[volume] + ("　【教师版】" if teacher else "　【学生版】"),
                size=9, align=WD_ALIGN_PARAGRAPH.CENTER)

    index = 0
    for sec, groups in layout:
        if not groups:
            continue
        heading = document.add_paragraph()
        heading.paragraph_format.keep_with_next = True
        heading.paragraph_format.space_before = Pt(10)
        run = heading.add_run(sec.label)
        run.font.size = Pt(12)
        run.font.bold = True
        run.font.name = "Times New Roman"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        render_note(
            document,
            f"期中出现 {sec.midterm_papers}/{_MIDTERM_PAPERS} 卷 · 期末 {sec.final_papers}/{_FINAL_PAPERS} 卷",
            size=8,
            keep_next=True,
        )
        for group, items in groups:
            group_line = document.add_paragraph()
            group_line.paragraph_format.keep_with_next = True
            group_run = group_line.add_run(
                f"◆ 题型 · {group.display}（期中 {group.midterm_papers}/{_MIDTERM_PAPERS} 卷）"
            )
            group_run.font.size = Pt(10.5)
            group_run.font.bold = True
            group_run.font.name = "Times New Roman"
            group_run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            if group.final_papers >= 0.3 * _FINAL_PAPERS:
                render_note(document, "（期末也高频，保持熟练）", size=8, keep_next=True)
            if group.error_notes:
                render_note(document, "易错提醒：" + "；".join(group.error_notes[:2]), size=8,
                            keep_next=True)
            for question in sorted(items, key=lambda q: (q.difficulty, -q.midterm_freq)):
                index += 1
                render_question(document, renderer, style, index, question,
                                teacher=teacher, used_fallbacks=used_fallbacks)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(out_path)
    errors = validate_docx(out_path)
    if errors:
        raise RuntimeError(f"{out_path.name} 校验失败: {errors}")


def chapter_label(chapter_id: str, chapter_name: str) -> str:
    return f"第{int(chapter_id[1:])}章{chapter_name}"


def generate_chapter(db_path: Path, out_dir: Path, chapter_id: str, chapter_name: str) -> dict:
    sections = load_chapter(db_path, chapter_id)
    used_ids: set[int] = set()
    used_fallbacks: list[str] = []
    stats: dict[str, object] = {}
    label = chapter_label(chapter_id, chapter_name)
    for volume in ("A", "B", "C"):
        layout = select_volume(sections, volume, used_ids)
        chosen = [q for _s, groups in layout for _g, items in groups for q in items]
        stats[volume] = {
            "count": len(chosen),
            "difficulty_avg": round(sum(q.difficulty for q in chosen) / max(len(chosen), 1), 2),
            "difficulty_hist": dict(sorted(Counter(q.difficulty for q in chosen).items())),
            "type_hist": dict(Counter(q.question_type.split("（")[0] for q in chosen)),
        }
        for teacher in (True, False):
            version = "教师版" if teacher else "学生版"
            render_volume(
                out_dir / f"期中-{label}-{volume}卷-{version}.docx",
                label, volume, teacher, layout, used_fallbacks,
            )
    stats["fallbacks"] = used_fallbacks
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="生成期中复习讲义 A/B/C 三卷双版")
    parser.add_argument("--db", type=Path,
                        default=project_data_root() / "databases" / "question_bank.db")
    parser.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "期中讲义v2")
    parser.add_argument("--chapters", nargs="*", default=list(MIDTERM_CHAPTERS))
    args = parser.parse_args()

    catalog_names: dict[str, str] = {}
    for volume in load_curriculum_catalog()["volumes"]:
        if volume["id"] != "bnu24-math-g8-upper":
            continue
        for chapter in volume.get("chapters", []):
            short = str(chapter.get("display_name") or "").split("｜")[-1]
            match = re.match(r"第[一二三四五六七八九十]+章\s*(.+)", short)
            catalog_names[chapter["id"].split("-")[-1]] = match.group(1) if match else short

    for chapter_id in args.chapters:
        chapter_name = catalog_names[chapter_id]
        stats = generate_chapter(args.db, args.out, chapter_id, chapter_name)
        print(f"[{chapter_id} {chapter_name}] " + json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
