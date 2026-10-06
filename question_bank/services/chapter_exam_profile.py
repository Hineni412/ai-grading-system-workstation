"""章节考情：本册期中/期末卷的出题统计与典型题挑选。只读，不调用模型。

统计口径：
- 试卷范围为本教学学期映射到的期中/期末卷（按 normalize_exam_type 归一），
  同源卷（较小一份卷中六成题能在另一份找到近乎相同的题）合并为一组，
  同组内近乎相同的题只计一次；
- 主考＝question_scope_summary.primary_section_id 落在本章节/小节；
  跨章＝主考不在本章、但直接判定点落在本章；
- 每道整题只计入一个主要技能（判定点关联最多者，同数取稳定键小者；
  优先取锚定到该题所属小节的技能）；
- 出卷率＝至少 1 道本节主考题的卷组数 ÷ 该阶段卷组数。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import defaultdict
from typing import Any

from question_bank.services.question_frequency_service import normalize_exam_type
from question_bank.services.question_skill_index import (
    short_node_name,
    skill_anchor_ids,
)
from question_bank.services.similar_question_ranker import clean_text, dice, grams

STAGES = (("midterm", "期中"), ("final", "期末"))
COLS = ("choice_basic", "choice_advanced", "fill", "written")
TIERS = (("basic", 1.0, 4.0), ("mid", 4.0, 7.0), ("hard", 7.0, 10.01))
POS_BUCKETS = [str(i) for i in range(1, 20)] + ["20+"]
SAME_QUESTION = 0.9       # 三字片段 Dice 达到此值视为同一道题
SAME_SOURCE_SHARE = 0.6   # 两卷中较小一份有六成题相同，视为同源卷合并
SAME_TEMPLATE = 0.6       # 模板签名 Dice 达到此值视为同一题型模板
HARD_PICK_MAX = 8.5       # 提高变化题的题库难度上限
# 出现次数超过此值的三元片段不提供候选区分度，只为它们建倒排会拖慢统计；
# 近乎相同的两题仍有大量普通片段可形成候选。
GRAM_POSTING_LIMIT = 200
CANDIDATE_DICE_FLOOR = 0.7
UNLINKED = "__unlinked"


def _norm_type(raw: object) -> str:
    text = str(raw or "")
    if "选" in text:
        return "choice"
    if "填" in text:
        return "fill"
    return "written"


def _tier_of(d: float | None) -> str | None:
    if d is None:
        return None
    if d >= 10.01:
        return "hard"
    return next((key for key, lo, hi in TIERS if lo <= d < hi), None)


def _col_of(qtype: str, tier: str | None) -> str | None:
    if tier is None:
        return None
    if qtype == "choice":
        return "choice_basic" if tier == "basic" else "choice_advanced"
    return qtype


def _qn_int(raw: object) -> int | None:
    match = re.match(r"\s*(\d+)", str(raw or ""))
    return int(match.group(1)) if match else None


def _pos_bucket(n: int | None) -> str | None:
    if n is None:
        return None
    return "20+" if n >= 20 else str(n)


def _percent(part: int, whole: int) -> int | None:
    return int(100 * part / whole + 0.5) if whole else None


def _near_duplicates(gram_sets: dict[int, frozenset[str]]) -> dict[int, set[int]]:
    """近乎相同题的邻接表；只对共享足够片段的候选对计算 Dice。"""
    postings: dict[str, list[int]] = defaultdict(list)
    for qid, grams_set in gram_sets.items():
        for gram in grams_set:
            postings[gram].append(qid)
    kept = {
        qid: [gram for gram in grams_set if len(postings[gram]) <= GRAM_POSTING_LIMIT]
        for qid, grams_set in gram_sets.items()
    }
    neighbors: dict[int, set[int]] = {qid: set() for qid in gram_sets}
    for qid, grams_list in kept.items():
        shared: dict[int, int] = {}
        for gram in grams_list:
            for other in postings[gram]:
                if other != qid:
                    shared[other] = shared.get(other, 0) + 1
        for other, count in shared.items():
            # 候选上界按保留片段估算，再用完整片段 Dice 确认。
            if 2 * count < CANDIDATE_DICE_FLOOR * (len(grams_list) + len(kept[other])):
                continue
            if dice(gram_sets[qid], gram_sets[other]) >= SAME_QUESTION:
                neighbors[qid].add(other)
                neighbors[other].add(qid)
    return neighbors


def _stage_cells() -> dict[str, dict[str, list[int]]]:
    return {stage: {col: [] for col in COLS} for stage, _label in STAGES}


def _stage_positions() -> dict[str, dict[str, list[int]]]:
    return {stage: {bucket: [] for bucket in POS_BUCKETS} for stage, _label in STAGES}


def _placeholders(values: list[int]) -> str:
    return ", ".join("?" for _ in values)


def _skill_home(key: str, skill_sections: dict[str, list[str]],
                section_label: dict[str, str]) -> str:
    anchors = skill_sections.get(key, [])
    return section_label.get(anchors[0], "其他册") if anchors else "其他册"


def _template_grams(text: object) -> frozenset[str]:
    """题型模板签名：数字、字母归一后取三字片段，只保留题面骨架。"""
    value = re.sub(r"\d+", "#", clean_text(text))
    value = re.sub(r"[a-zA-Z]+", "@", value)
    return grams(value)


def _year_of(raw: object) -> int | None:
    match = re.search(r"\d{4}", str(raw or ""))
    return int(match.group(0)) if match else None


def _pick_representative(
    cell: list[int],
    skill_key: str,
    recs: dict[int, dict],
    tpl_sets: dict[int, frozenset[str]],
    point_counts: dict[int, int],
    chosen: list[int],
    *,
    entry: bool,
    window: str,
    ceiling: float | None = None,
) -> int | None:
    """单元内选代表题：先排除与本节已选题同模板者；
    ceiling（提高变化 8.5）是硬上限，超出的题一律不候选；
    window="low"（基础入口/提高变化）只留难度不超过档内中位数的题，
    window="near"（常见变式）只留与中位数差 ≤1 的题——窗口筛完为空则不生效；
    有主要技能判定点占比过半的题时只保留它们，基础入口再优先独立小题
    （判定点总数 ≤4，避免把复杂大题中的容易小问当成基础入口）；
    其后取模板居中、难度贴近档内中位数、卷年新、题号小者。"""
    candidates = [
        qid for qid in cell
        if all(dice(tpl_sets[qid], tpl_sets[c]) < SAME_TEMPLATE for c in chosen)
    ]
    if ceiling is not None:
        candidates = [
            qid for qid in candidates
            if (d := recs[qid]["difficulty"]) is not None and d <= ceiling
        ]
    if not candidates:
        return None
    diffs = [recs[q]["difficulty"] for q in cell if recs[q]["difficulty"] is not None]
    median = statistics.median(diffs) if diffs else None
    if median is not None:
        if window == "near":
            kept = [
                qid for qid in candidates
                if (d := recs[qid]["difficulty"]) is not None
                and abs(d - median) <= 1.0
            ]
        else:
            kept = [
                qid for qid in candidates
                if (d := recs[qid]["difficulty"]) is not None and d <= median
            ]
        if kept:
            candidates = kept
    pure = [
        qid for qid in candidates
        if point_counts.get(qid)
        and recs[qid]["skill_hits"].get(skill_key, 0) / point_counts[qid] >= 0.5
    ]
    if pure:
        candidates = pure
    if entry:
        short = [qid for qid in candidates if (point_counts.get(qid) or 0) <= 4]
        if short:
            candidates = short

    def order(qid: int) -> tuple:
        others = [x for x in cell if x != qid]
        centrality = (
            sum(dice(tpl_sets[qid], tpl_sets[x]) for x in others) / len(others)
            if others else 0.0
        )
        d = recs[qid]["difficulty"]
        gap = abs(d - median) if d is not None and median is not None else float("inf")
        return (-round(centrality, 4), gap, -(recs[qid]["year"] or 0), qid)

    return min(candidates, key=order)


def _section_typical(
    smain: list[dict],
    recs: dict[int, dict],
    tpl_sets: dict[int, frozenset[str]],
    point_counts: dict[int, int],
    *,
    synthesis: bool,
) -> dict[str, list[dict]]:
    """按教师报告口径挑典型题：技能按本节覆盖卷组数排序，先给每个常考
    技能一个基础入口，再补有增量的中档变式，最后按限额补提高变化；
    只从至少 2 题且来自至少 2 个卷组的技能×难度档单元里选。"""
    skills: dict[str, dict] = {}
    names: dict[str, str] = {}
    cells: dict[tuple[str, str], list[int]] = {}
    for rec in smain:
        key = rec["skill_key"]
        if key == UNLINKED:
            continue
        row = skills.setdefault(key, {"groups": set(), "total": 0})
        row["groups"].add(rec["group"])
        row["total"] += 1
        names[key] = rec["skill_name"]
        if rec["tier"]:
            cells.setdefault((key, rec["tier"]), []).append(rec["id"])

    rank = sorted(
        skills,
        key=lambda key: (-len(skills[key]["groups"]), -skills[key]["total"],
                         names[key], key),
    )
    budget = max(1 if synthesis else 2,
                 min(4 if synthesis else 10, round(len(smain) / 9)))
    hard_cap = max(1, budget // 4)

    def is_typical(cell: list[int]) -> bool:
        return len(cell) >= 2 and len({recs[x]["group"] for x in cell}) >= 2

    chosen: list[int] = []
    picks: dict[str, dict[str, dict]] = {}

    def take(key: str, tier: str, slot: str, role: str, *, entry: bool = False,
             window: str = "low", ceiling: float | None = None) -> bool:
        cell = cells[(key, tier)]
        qid = _pick_representative(
            cell, key, recs, tpl_sets, point_counts, chosen, entry=entry,
            window=window, ceiling=ceiling,
        )
        if qid is None:
            return False
        chosen.append(qid)
        cell_groups = len({recs[x]["group"] for x in cell})
        if slot == "entry":
            reason = (f"本节第{rank.index(key) + 1}常考技能，"
                      f"出现在{len(skills[key]['groups'])}套试卷")
        elif slot == "variant":
            reason = f"同技能中档变式，同类{len(cell)}题"
        else:
            reason = f"出现在{cell_groups}套试卷"
        picks.setdefault(key, {})[slot] = {
            "tier": tier,
            "question_id": qid,
            "same_tier_count": len(cell),
            "group_count": cell_groups,
            "role": role,
            "reason": reason,
        }
        return True

    # 基础入口：覆盖至少 2 个卷组的技能取最低的非难档典型单元。
    for key in rank:
        if len(chosen) >= budget:
            break
        if len(skills[key]["groups"]) < 2:
            continue
        tier = next(
            (t for t in ("basic", "mid")
             if (key, t) in cells and is_typical(cells[(key, t)])),
            None,
        )
        if tier is None:
            continue
        take(key, tier, "entry", "基础入口" if tier == "basic" else "常见变式",
             entry=True)
    # 常见变式：中档单元未被入口占用且至少 3 题、2 个卷组。
    for key in rank:
        if len(chosen) >= budget:
            break
        cell = cells.get((key, "mid")) or []
        if len(cell) < 3 or len({recs[x]["group"] for x in cell}) < 2:
            continue
        entry_pick = picks.get(key, {}).get("entry")
        if entry_pick and entry_pick["tier"] == "mid":
            continue
        take(key, "mid", "variant", "常见变式", window="near")
    # 提高变化：限额的难档典型单元（≥2 题且 ≥3 个卷组），覆盖卷组多、题多的单元优先。
    hard_cells = [
        (key, cell) for (key, tier), cell in cells.items()
        if tier == "hard" and is_typical(cell)
        and len({recs[x]["group"] for x in cell}) >= 3
    ]
    hard_cells.sort(
        key=lambda item: (-len({recs[x]["group"] for x in item[1]}),
                          -len(item[1]), item[0])
    )
    hard_taken = 0
    for key, _cell in hard_cells:
        if hard_taken >= hard_cap or len(chosen) >= budget:
            break
        if take(key, "hard", "hard", "提高变化", ceiling=HARD_PICK_MAX):
            hard_taken += 1
    return {
        key: [picks[key][slot] for slot in ("entry", "variant", "hard")
              if slot in picks[key]]
        for key in rank
        if key in picks
    }


def _primary_skill_key(
    hits: dict[str, int],
    section: str,
    skill_sections: dict[str, list[str]],
) -> str | None:
    """主要技能：优先锚定到该题所属小节的技能，判定点最多者，同数取键小者。"""
    if not hits:
        return None
    own = [key for key in hits if section in skill_sections.get(key, [])]
    pool = own or list(hits)
    return sorted(pool, key=lambda key: (-hits[key], key))[0]


def _prepare(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    """期中/期末卷范围、同源卷组、逐题归属与主要技能归一——章节考情与考频共用。"""
    volume_id = str(volume["id"])
    stage_key = {"期中": "midterm", "期末": "final"}
    # 小节归属映射与带章序号的小节名（不改动目录对象本身）。
    section_chapter: dict[str, str] = {}
    section_label: dict[str, str] = {}
    for index, chapter in enumerate(volume["chapters"], start=1):
        cid = chapter["knowledge_id"]
        section_chapter[cid] = cid
        for section in chapter["sections"]:
            sid = section["knowledge_id"]
            section_chapter[sid] = cid
            label = str(section["label"])
            label = f"{index}.{label}" if label[:1].isdigit() else label
            section_label[sid] = label

    volume_qids = set(snapshot["volumes"].get(volume_id, ()))
    raw_paper_q: dict[int, list[int]] = {}
    for paper_id, members in snapshot["members"].items():
        selected = sorted(int(qid) for qid in members if int(qid) in volume_qids)
        if selected:
            raw_paper_q[int(paper_id)] = selected
    paper_ids = sorted(raw_paper_q)
    papers: dict[int, dict[str, Any]] = {}
    if paper_ids:
        for row in conn.execute(
            "SELECT id, title, exam_type, year FROM papers "
            f"WHERE id IN ({_placeholders(paper_ids)}) AND deleted_at IS NULL "
            "AND COALESCE(import_status,'')<>'deleted'",
            paper_ids,
        ):
            stage = stage_key.get(normalize_exam_type(row["exam_type"]))
            if stage:
                papers[int(row["id"])] = {
                    "id": int(row["id"]), "title": str(row["title"] or ""),
                    "stage": stage, "year": _year_of(row["year"]),
                }
    paper_ids = [pid for pid in paper_ids if pid in papers]
    candidate_qids = sorted({qid for pid in paper_ids for qid in raw_paper_q[pid]})
    questions: dict[int, dict[str, Any]] = {}
    if candidate_qids:
        for row in conn.execute(
            "SELECT id, paper_id, question_number, question_type, difficulty, "
            f"question_text FROM questions WHERE id IN ({_placeholders(candidate_qids)}) "
            "AND is_deleted = 0",
            candidate_qids,
        ):
            # 与原卷不在本册期中期末范围内的题不计入（含只通过引用出现的题）。
            if int(row["paper_id"]) in papers:
                questions[int(row["id"])] = dict(row)
    # 卷内题目先按原卷题序（按题号 id），引用出现的题排在后面，与原脚本口径一致。
    paper_q = {}
    for pid in paper_ids:
        originals = [qid for qid in raw_paper_q[pid]
                     if qid in questions and questions[qid]["paper_id"] == pid]
        referenced = [qid for qid in raw_paper_q[pid]
                      if qid in questions and questions[qid]["paper_id"] != pid]
        paper_q[pid] = originals + referenced
    gram_sets = {
        qid: grams(clean_text(row["question_text"]))
        for qid, row in questions.items()
    }
    tpl_sets = {
        qid: _template_grams(row["question_text"])
        for qid, row in questions.items()
    }
    neighbors = _near_duplicates(gram_sets)

    # 同源卷合并：同阶段内，较小一份卷中至少六成题能在另一份找到近乎相同的题。
    parent = {pid: pid for pid in paper_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    paper_sets = {pid: set(ids) for pid, ids in paper_q.items()}
    for index, first in enumerate(paper_ids):
        for second in paper_ids[index + 1:]:
            if papers[first]["stage"] != papers[second]["stage"]:
                continue
            qa, qb = (first, second) if len(paper_sets[first]) <= len(paper_sets[second]) else (second, first)
            small, big = paper_sets[qa], paper_sets[qb]
            if not small:
                continue
            hit = sum(1 for qid in small if neighbors[qid] & big)
            if hit / len(small) >= SAME_SOURCE_SHARE:
                parent[find(second)] = find(first)
    groups: dict[int, list[int]] = defaultdict(list)
    for pid in paper_ids:
        groups[find(pid)].append(pid)
    group_of = {pid: root for root, members in groups.items() for pid in members}

    # 同组卷内近乎相同的题只计一次。
    counted: set[int] = set()
    for root, members in groups.items():
        kept: list[int] = []
        kept_set: set[int] = set()
        for pid in members:
            for qid in paper_q[pid]:
                if neighbors[qid] & kept_set:
                    continue
                kept.append(qid)
                kept_set.add(qid)
        counted.update(kept)

    counted_list = sorted(counted)
    summary: dict[int, dict[str, Any]] = {}
    if counted_list:
        marks = _placeholders(counted_list)
        summary = {
            int(row["question_id"]): dict(row)
            for row in conn.execute(
                "SELECT question_id, primary_section_id, direct_section_ids_json "
                f"FROM question_scope_summary WHERE question_id IN ({marks})",
                counted_list,
            )
        }

    nodes = snapshot["nodes"]
    skill_sections = {
        key: skill_anchor_ids(node, volume)
        for key, node in nodes.items()
        if key.startswith("sk_")
    }
    by_question = snapshot["by_question"]
    point_counts = snapshot.get("point_counts") or {}

    recs: dict[int, dict[str, Any]] = {}
    for qid in counted_list:
        q = questions[qid]
        paper = papers[int(q["paper_id"])]
        try:
            difficulty = float(q["difficulty"])
        except (TypeError, ValueError):
            difficulty = None
        row_summary = summary.get(qid) or {}
        primary = str(row_summary.get("primary_section_id") or "")
        try:
            direct = json.loads(str(row_summary.get("direct_section_ids_json") or "[]"))
        except (json.JSONDecodeError, TypeError):
            direct = []
        qtype = _norm_type(q["question_type"])
        tier = _tier_of(difficulty)
        hits = by_question.get(qid, {})
        recs[qid] = {
            "id": qid,
            "paper_id": int(q["paper_id"]),
            "group": group_of[int(q["paper_id"])],
            "stage": paper["stage"],
            "year": paper["year"],
            "qn": _qn_int(q["question_number"]),
            "type": qtype,
            "difficulty": difficulty,
            "tier": tier,
            "col": _col_of(qtype, tier),
            "section": primary,
            "chapter": section_chapter.get(primary, ""),
            "direct_chapters": sorted(
                {section_chapter[x] for x in direct if x in section_chapter}
            ),
            "skill_hits": {key: len(value) for key, value in hits.items()},
        }

    # 主要技能：优先锚定到该题所属小节的技能，判定点最多者，同数取键小者。
    for rec in recs.values():
        hits = rec["skill_hits"]
        own = [key for key in hits if rec["section"] in skill_sections.get(key, [])]
        best = _primary_skill_key(hits, rec["section"], skill_sections)
        if best is None:
            rec["skill_key"], rec["skill_own"] = UNLINKED, True
            continue
        rec["skill_key"] = best
        rec["skill_own"] = bool(own)
        node = nodes.get(best)
        rec["skill_name"] = (
            "未挂技能" if best == UNLINKED or node is None
            else short_node_name(str(node["display_name"]))
        )
    for rec in recs.values():
        if rec["skill_key"] == UNLINKED:
            rec["skill_name"] = "未挂技能"

    stage_groups = {
        stage: [root for root, members in groups.items()
                if papers[members[0]]["stage"] == stage]
        for stage, _label in STAGES
    }
    unit = {
        stage: ("组" if any(len(groups[root]) > 1 for root in stage_groups[stage])
                else "份")
        for stage, _label in STAGES
    }
    return {
        "volume_id": volume_id,
        "volume_qids": volume_qids,
        "papers": papers,
        "groups": groups,
        "group_of": group_of,
        "counted": counted,
        "nodes": nodes,
        "skill_sections": skill_sections,
        "by_question": by_question,
        "point_counts": point_counts,
        "tpl_sets": tpl_sets,
        "recs": recs,
        "stage_groups": stage_groups,
        "unit": unit,
        "section_label": section_label,
    }


def build_chapter_exam_profile(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    """按本册期中/期末卷统计章节出卷情况；snapshot 为读服务的技能快照。"""
    prep = _prepare(conn, snapshot, volume)
    volume_id = prep["volume_id"]
    section_label = prep["section_label"]
    papers = prep["papers"]
    tpl_sets = prep["tpl_sets"]
    groups = prep["groups"]
    nodes = prep["nodes"]
    skill_sections = prep["skill_sections"]
    point_counts = prep["point_counts"]
    recs = prep["recs"]
    stage_groups = prep["stage_groups"]
    unit = prep["unit"]

    chapters_out: list[dict[str, Any]] = []
    for chapter in volume["chapters"]:
        cid = chapter["knowledge_id"]
        main = [rec for rec in recs.values() if rec["chapter"] == cid]
        cross = [rec for rec in recs.values()
                 if rec["chapter"] != cid and cid in rec["direct_chapters"]]
        if not main:
            continue
        raw_sections = [(section["knowledge_id"], section_label[section["knowledge_id"]], False)
                        for section in chapter["sections"]]
        raw_sections.append((cid, "章内综合", True))
        sections_out = []
        for raw_id, label, synthesis in raw_sections:
            smain = [rec for rec in main if rec["section"] == raw_id]
            if synthesis and not smain:
                continue
            coverage = {}
            overview = _stage_cells()
            for stage, _label in STAGES:
                hit = {rec["group"] for rec in smain if rec["stage"] == stage}
                coverage[stage] = {
                    "groups": len(hit),
                    "of": len(stage_groups[stage]),
                    "percent": _percent(len(hit), len(stage_groups[stage])),
                    "questions": sum(1 for rec in smain if rec["stage"] == stage),
                }
            skill_rows: dict[str, dict[str, Any]] = {}
            for rec in smain:
                key = rec["skill_key"]
                row = skill_rows.setdefault(key, {
                    "key": None if key == UNLINKED else key,
                    "name": rec["skill_name"],
                    "unlinked": key == UNLINKED,
                    "home_section_label": (
                        "" if key == UNLINKED or rec["skill_own"]
                        else _skill_home(key, skill_sections, section_label)
                    ),
                    "definition": (
                        "" if key == UNLINKED
                        else str((nodes.get(key) or {}).get("observable_evidence") or "")
                    ),
                    "total": 0,
                    "cells": _stage_cells(),
                    "positions": _stage_positions(),
                    "typical": [],
                })
                row["total"] += 1
                if rec["col"]:
                    row["cells"][rec["stage"]][rec["col"]].append(rec["id"])
                    overview[rec["stage"]][rec["col"]].append(rec["id"])
                bucket = _pos_bucket(rec["qn"])
                if bucket:
                    row["positions"][rec["stage"]][bucket].append(rec["id"])
            # 技能行按本节覆盖卷组数排序（与典型题技能次序一致），未挂技能居末。
            skill_groups: dict[str, set[int]] = {}
            for rec in smain:
                if rec["skill_key"] != UNLINKED:
                    skill_groups.setdefault(rec["skill_key"], set()).add(rec["group"])
            ordered = sorted(
                skill_rows.values(),
                key=lambda row: (
                    row["unlinked"],
                    -len(skill_groups.get(row["key"] or "", ())),
                    -row["total"], row["name"], row["key"] or "",
                ),
            )
            typical_map = _section_typical(
                smain, recs, tpl_sets, point_counts, synthesis=synthesis,
            )
            for row in ordered:
                row["typical"] = typical_map.get(row["key"] or "", [])
            sections_out.append({
                "id": f"{cid}#synthesis" if synthesis else raw_id,
                "label": label,
                "synthesis": synthesis,
                "main_count": len(smain),
                "coverage": coverage,
                "overview": overview,
                "skills": ordered,
            })
        totals = {
            stage: {
                "main": sum(1 for rec in main if rec["stage"] == stage),
                "cross": sum(1 for rec in cross if rec["stage"] == stage),
            }
            for stage, _label in STAGES
        }
        difficulty = {}
        for stage, _label in STAGES:
            rows = [rec for rec in main if rec["stage"] == stage]
            tiers = {key: sum(1 for rec in rows if rec["tier"] == key) for key, *_ in TIERS}
            difficulty[stage] = {
                "total": len(rows),
                **tiers,
                "choice": sum(1 for rec in rows if rec["type"] == "choice"),
                "fill": sum(1 for rec in rows if rec["type"] == "fill"),
                "written": sum(1 for rec in rows if rec["type"] == "written"),
            }
        chapters_out.append({
            "id": cid,
            "label": str(chapter["label"]),
            "totals": totals,
            "difficulty": difficulty,
            "cross_question_ids": {
                stage: [rec["id"] for rec in cross if rec["stage"] == stage]
                for stage, _label in STAGES
            },
            "sections": sections_out,
        })

    merged = [
        {
            "stage": papers[members[0]]["stage"],
            "papers": [{"id": pid, "title": papers[pid]["title"]} for pid in members],
        }
        for _root, members in groups.items()
        if len(members) > 1
    ]
    return {
        "curriculum_volume_id": volume_id,
        "graph_release_id": snapshot.get("release"),
        "model_calls": 0,
        "counted_question_count": len(recs),
        "stages": [
            {
                "stage": stage,
                "label": label,
                "paper_count": sum(
                    1 for paper in papers.values() if paper["stage"] == stage
                ),
                "group_count": len(stage_groups[stage]),
                "unit": unit[stage],
            }
            for stage, label in STAGES
        ],
        "merged_groups": merged,
        "chapters": chapters_out,
    }


def build_exam_frequency(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[int, tuple[float, float]]:
    """每题考频：(期中同源卷组覆盖率, 期末同源卷组覆盖率)，与章节考情同一口径。

    某阶段覆盖率＝含有同主要技能同难度档计入题的该阶段卷组数 ÷ 该阶段卷组数；
    题自身所属卷组在分子分母中同时剔除，不在正式卷中的题（如阶段练习）
    没有可剔除的自有卷组；无主要技能或无难度档的题不出现。"""
    prep = _prepare(conn, snapshot, volume)
    stage_groups = {
        stage: set(prep["stage_groups"][stage]) for stage, _label in STAGES
    }
    if not any(stage_groups.values()):
        return {}
    recs = prep["recs"]
    group_of = prep["group_of"]
    skill_sections = prep["skill_sections"]
    by_question = prep["by_question"]

    cell_groups: dict[str, dict[tuple[str, str], set[int]]] = {
        stage: defaultdict(set) for stage, _label in STAGES
    }
    for rec in recs.values():
        if rec["skill_key"] != UNLINKED and rec["tier"] is not None:
            cell_groups[rec["stage"]][(rec["skill_key"], rec["tier"])].add(
                rec["group"]
            )

    def shares(skill_key: str, tier: str,
               own_group: int | None) -> tuple[float, float]:
        own = {own_group} if own_group is not None else set()
        out = []
        for stage, _label in STAGES:
            pool = stage_groups[stage] - own
            covered = cell_groups[stage].get((skill_key, tier), set()) - own
            out.append(len(covered) / len(pool) if pool else 0.0)
        return (out[0], out[1])

    results: dict[int, tuple[float, float]] = {}
    for rec in recs.values():
        if rec["skill_key"] == UNLINKED or rec["tier"] is None:
            continue
        results[rec["id"]] = shares(rec["skill_key"], rec["tier"], rec["group"])

    # 计入题之外的本册题（同组去重丢弃者、阶段练习等）也按同一口径取值。
    extras = sorted(prep["volume_qids"] - prep["counted"])
    if not extras:
        return results
    marks = _placeholders(extras)
    extra_rows = {
        int(row["id"]): dict(row)
        for row in conn.execute(
            "SELECT id, paper_id, difficulty FROM questions "
            f"WHERE id IN ({marks}) AND is_deleted = 0",
            extras,
        )
    }
    if not extra_rows:
        return results
    extra_list = sorted(extra_rows)
    extra_section = {
        int(row["question_id"]): str(row["primary_section_id"] or "")
        for row in conn.execute(
            "SELECT question_id, primary_section_id FROM question_scope_summary "
            f"WHERE question_id IN ({_placeholders(extra_list)})",
            extra_list,
        )
    }
    for qid, row in extra_rows.items():
        hits = {
            key: len(value)
            for key, value in by_question.get(qid, {}).items()
        }
        try:
            difficulty = float(row["difficulty"])
        except (TypeError, ValueError):
            continue
        tier = _tier_of(difficulty)
        if tier is None:
            continue
        best = _primary_skill_key(
            hits, extra_section.get(qid, ""), skill_sections
        )
        if best is None:
            continue
        results[qid] = shares(best, tier, group_of.get(int(row["paper_id"])))
    return results
