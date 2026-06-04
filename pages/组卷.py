from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from question_bank.database.paths import project_data_root, question_bank_db_path
from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
from question_bank.services.assembly_record_service import (
    AssemblyRecord,
    AssemblyRecordCreate,
    create_assembly_record,
    delete_assembly_record,
    get_assembly_record,
    list_assembly_records,
)
from question_bank.services.question_service import QuestionService
from question_bank.services.ai_tagging_service import CURRICULUM_CHAPTERS
from export_names import safe_filename_fragment


BASKET_KEY = "qb_question_basket"
ORDER_KEY = "qb_assembly_order"
OUTPUT_DIR_KEY = "qb_assembly_output_dir"
INCLUDE_ANSWER_KEY = "qb_assembly_include_answer"
IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")


def _basket_ids() -> list[int]:
    values = st.session_state.setdefault(BASKET_KEY, [])
    deduped: list[int] = []
    for value in values:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id not in deduped:
            deduped.append(question_id)
    st.session_state[BASKET_KEY] = deduped
    return deduped


def _sync_basket_from_query() -> None:
    raw_ids = st.query_params.get("qb_ids", "")
    if isinstance(raw_ids, list):
        raw_ids = raw_ids[0] if raw_ids else ""
    ids: list[int] = []
    for item in str(raw_ids or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            question_id = int(item)
        except ValueError:
            continue
        if question_id not in ids:
            ids.append(question_id)
    if ids:
        st.session_state[BASKET_KEY] = ids
        st.session_state[ORDER_KEY] = ids

    record_id = st.query_params.get("record_id", "")
    if isinstance(record_id, list):
        record_id = record_id[0] if record_id else ""
    if record_id:
        record = get_assembly_record(str(record_id))
        if record is not None:
            st.session_state[BASKET_KEY] = record.question_ids
            st.session_state[ORDER_KEY] = record.question_ids
            st.session_state["qb_paper_title"] = record.title

    new_order_str = st.query_params.get("qb_order", "")
    if isinstance(new_order_str, list):
        new_order_str = new_order_str[0] if new_order_str else ""
    if new_order_str:
        try:
            new_ids = [int(x) for x in new_order_str.split(",") if x.strip()]
            if set(new_ids) == set(_basket_ids()):
                st.session_state[ORDER_KEY] = new_ids
                st.session_state[BASKET_KEY] = new_ids
                st.query_params.pop("qb_order")
                st.rerun()
        except Exception:
            pass

    page_param = st.query_params.get("assembly_page", "")
    if isinstance(page_param, list):
        page_param = page_param[0] if page_param else ""
    if page_param == "composition":
        st.session_state["assembly_page"] = "composition"
        st.query_params.pop("assembly_page")
        st.rerun()
    elif page_param == "selection":
        st.session_state["assembly_page"] = "selection"
        st.query_params.pop("assembly_page")
        st.rerun()


def _ordered_ids() -> list[int]:
    basket_ids = _basket_ids()
    current_order = [
        int(item)
        for item in st.session_state.get(ORDER_KEY, [])
        if _is_int(item) and int(item) in basket_ids
    ]
    for question_id in basket_ids:
        if question_id not in current_order:
            current_order.append(question_id)
    st.session_state[ORDER_KEY] = current_order
    st.session_state[BASKET_KEY] = current_order
    return current_order


def _is_int(value: object) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _move_question(question_id: int, offset: int) -> None:
    ordered = _ordered_ids()
    if question_id not in ordered:
        return
    index = ordered.index(question_id)
    target = index + offset
    if target < 0 or target >= len(ordered):
        return
    ordered[index], ordered[target] = ordered[target], ordered[index]
    st.session_state[ORDER_KEY] = ordered
    st.session_state[BASKET_KEY] = ordered


def _remove_question(question_id: int) -> None:
    ordered = [item for item in _ordered_ids() if item != question_id]
    st.session_state[ORDER_KEY] = ordered
    st.session_state[BASKET_KEY] = ordered


def _clear_basket() -> None:
    st.session_state[BASKET_KEY] = []
    st.session_state[ORDER_KEY] = []


TAG_FILTER_CONFIG = (
    ("knowledge_point", "知识点"),
    ("method", "思想方法"),
    ("ability", "数学能力"),
    ("model", "数学模型"),
    ("error_type", "易错点"),
    ("exam_scope", "教材章节"),
)


def _cell_text(value: object) -> str:
    return str(value or "").strip()


def _options_from_questions(questions: list[dict[str, Any]], field: str, *, reverse: bool = False) -> list[str]:
    values = [_cell_text(item.get(field)) for item in questions]
    values = [value for value in values if value]
    return sorted(set(values), reverse=reverse)


def _single_filter_row(label: str, options: list[str], *, key: str) -> str:
    normalized = _unique_options(options)
    value = st.pills(label, normalized, default=normalized[0], key=key)
    return str(value or normalized[0])


def _single_value_filter_row(label: str, options: list[str], *, key: str) -> str:
    normalized = _unique_options(options)
    value = st.pills(label, normalized, default=normalized[0], key=key)
    text = str(value or normalized[0])
    return "" if text == "全部" else text


def _unique_options(options: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in options:
        text = _cell_text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result or ["全部"]


def _difficulty_range_for_label(label: str) -> tuple[int, int] | None:
    return {
        "易": (1, 2),
        "较易": (3, 4),
        "中档": (5, 6),
        "较难": (7, 8),
        "难": (9, 10),
    }.get(str(label or ""))


def _question_types_for_label(label: str, raw_options: list[str]) -> list[str]:
    mapping = {
        "选择题": ["choice", "single_choice", "选择题"],
        "多选题": ["multiple_choice", "multi_choice", "多选题"],
        "填空题": ["fill_blank", "blank", "填空题"],
        "解答题": ["solution", "calculation", "proof", "comprehensive", "general_solution", "解答题"],
    }
    text = str(label or "")
    if text == "全部":
        return []
    return [value for value in mapping.get(text, [text]) if value in raw_options or value == text]


def _source_label(question: dict[str, Any]) -> str:
    year = str(question.get("year") or "").strip()
    paper_title = str(question.get("paper_title") or "").strip()
    if not paper_title and question.get("source_file"):
        paper_title = Path(question["source_file"]).stem
        
    # Clean up YYYYMMDD_HHMMSS timestamps from paper titles
    if paper_title:
        paper_title = re.sub(r'_\d{8}_\d{6}$', '', paper_title)
        
    if paper_title:
        if year and (paper_title.startswith(year) or f"{year}·" in paper_title):
            label = paper_title
        else:
            label = f"{year}·{paper_title}" if year else paper_title
    else:
        district = str(question.get("district") or "").strip()
        exam_type = str(question.get("exam_type") or "").strip()
        label_parts = [part for part in (district, exam_type) if part]
        label = "·".join(label_parts) if label_parts else "本地题库"
        if year:
            label = f"{year}·{label}"
            
    number = str(question.get("question_number") or "").strip()
    if number:
        return f"{label} 第{number}题"
    return label


def _short_text(value: object, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _safe_html_format(value: str) -> str:
    if not value:
        return ""
    
    # 1. unescape HTML entities to normalize
    text = str(value)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    # 2. Escape HTML for safety
    escaped = html.escape(text)
    
    # 2.5 Convert continuous spaces (2 or more) to non-folding spaces for underlines
    escaped = re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), escaped)

    # 3. Dynamic restore allowed tags
    allowed_tags = ["sub", "sup", "u", "table", "tbody", "tr", "td", "th"]
    for tag in allowed_tags:
        opening_pattern = re.compile(rf"&lt;({tag})(\s+[^&]*)?&gt;", re.IGNORECASE)
        escaped = opening_pattern.sub(lambda m: f"<{m.group(1)}{html.unescape(m.group(2) or '')}>", escaped)
        
        closing_pattern = re.compile(rf"&lt;/({tag})&gt;", re.IGNORECASE)
        escaped = closing_pattern.sub(rf"</\1>", escaped)
        
    # 4. Normalize <br>
    escaped = re.sub(r"&lt;br\s*/?&gt;", "<br>", escaped, flags=re.IGNORECASE)

    # 5. Convert raw newlines to <br> to prevent Streamlit Markdown block splitting
    escaped = escaped.replace("\n", "<br>").replace("\r", "")

    # 6. Clean up inner newlines and inner <br> inside tables to keep table HTML clean
    def _strip_table_br_newlines(match):
        content = match.group(0)
        content = content.replace("\n", "").replace("\r", "")
        content = content.replace("<br>", "").replace("<br/>", "")
        return content
    escaped = re.sub(r"<table\b[^>]*>.*?</table>", _strip_table_br_newlines, escaped, flags=re.DOTALL | re.IGNORECASE)

    return escaped


def _render_rich_text(value: object) -> None:
    text = IMAGE_MARKER_PATTERN.sub("", str(value or "")).strip()
    if not text:
        return
    st.markdown(
        f'<div class="qb-rich-text">{_safe_html_format(text)}</div>',
        unsafe_allow_html=True,
    )


def _render_images(image_paths: list[str]) -> None:
    paths = _dedupe_paths(image_paths)
    valid_paths = [Path(p) for p in paths if Path(p).exists()]
    if not valid_paths:
        return
        
    if len(valid_paths) == 1:
        st.image(str(valid_paths[0]), width=200)
    else:
        num_cols = min(len(valid_paths), 4)
        cols = st.columns(num_cols)
        for idx, path in enumerate(valid_paths):
            with cols[idx % num_cols]:
                st.image(str(path), width=150)


def _render_premium_question_card(
    index: int, 
    question: dict[str, Any], 
    preview_mode: str, 
    show_basket_toggle: bool = False,
    qid_for_key: int | None = None
) -> None:
    is_teacher = "教师" in preview_mode
    qid = int(question["id"])
    
    # 1. Badges preparation
    badges_html = ""
    diff_val = 0
    try:
        diff_val = float(question.get("difficulty") or 0)
    except ValueError:
        pass
    
    if diff_val > 0:
        if diff_val <= 3:
            badge_class = "qb-badge qb-badge-easy"
            badge_label = f"易 (难度 {diff_val:.1f})"
        elif diff_val <= 7:
            badge_class = "qb-badge qb-badge-medium"
            badge_label = f"中 (难度 {diff_val:.1f})"
        else:
            badge_class = "qb-badge qb-badge-hard"
            badge_label = f"难 (难度 {diff_val:.1f})"
        badges_html += f'<span class="{badge_class}">{badge_label}</span>'
        
    typicality_val = 0
    try:
        typicality_val = float(question.get("typicality") or 0)
    except ValueError:
        pass
    if typicality_val > 0:
        badges_html += f'<span class="qb-badge qb-badge-medium">典型度 {typicality_val:.0f}</span>'

    # Get clean rich text
    q_text = question.get("question_text") or ""
    q_rich = _safe_html_format(IMAGE_MARKER_PATTERN.sub("", q_text).strip())
    source_label = html.escape(_source_label(question))
    
    # Render inside a native Streamlit container with border for premium card feel
    with st.container(border=True):
        if show_basket_toggle:
            card_col, action_col = st.columns([8.8, 1.2])
            with card_col:
                _render_card_body_content(qid, index, source_label, badges_html, q_rich, question, is_teacher, show_basket_toggle)
            with action_col:
                _render_card_action_button(qid, qid_for_key)
        else:
            _render_card_body_content(qid, index, source_label, badges_html, q_rich, question, is_teacher, show_basket_toggle)


def _render_card_body_content(
    qid: int, 
    index: int, 
    source_label: str, 
    badges_html: str, 
    q_rich: str, 
    question: dict[str, Any], 
    is_teacher: bool,
    show_basket_toggle: bool
) -> None:
    # Use different header format for selection vs composition
    header_text = f"ID: {qid} · {source_label}" if show_basket_toggle else f"第 {index} 题 · {source_label}"
    
    st.markdown(
        f"""
        <div class="qb-paper-header" style="border-bottom: 1px dashed #cbd5e1; padding-bottom: 10px; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
            <span class="qb-paper-title-tag" style="font-weight: bold; font-size: 1.05rem; color: #1e293b;">{header_text}</span>
            <div>{badges_html}</div>
        </div>
        <div class="qb-rich-text" style="line-height: 1.8; font-size: 1.05rem; color: #0f172a; font-family: 'Times New Roman', SimSun, serif;">{q_rich}</div>
        """,
        unsafe_allow_html=True
    )
    
    # Render inline/attached images if any
    image_paths = _dedupe_paths(
        [
            *(question.get("image_paths") or []),
            *_image_paths_from_text(question.get("question_text") or ""),
        ]
    )
    if image_paths:
        _render_images(image_paths)
        
    # Render student blanks for solution questions (only in student perspective during composition)
    qtype = _canonical_type_group(question.get("question_type"))
    if not is_teacher and qtype == "解答题":
        st.markdown(
            """
            <div style="border: 1px dashed #cbd5e1; border-radius: 6px; padding: 25px; margin: 15px 0 10px; text-align: center; color: #94a3b8; font-size: 0.9rem;">
                ✍️ 学生作答区域 (预留空白区域)
            </div>
            """,
            unsafe_allow_html=True
        )

    # Render Teacher details box inside a collapsible expander by default
    if is_teacher:
        with st.expander("🔑 查看参考答案与解析 (默认折叠)", expanded=False):
            kp_tags = []
            ability_tags = []
            method_tags = []
            model_tags = []
            chapter_tag = ""
            
            for t in question.get("tags", []):
                tt = t.get("tag_type")
                tv = t.get("tag_value")
                if not tv:
                    continue
                if tt == "knowledge_point":
                    kp_tags.append(tv)
                elif tt == "ability":
                    ability_tags.append(tv)
                elif tt == "method":
                    method_tags.append(tv)
                elif tt == "model":
                    model_tags.append(tv)
                elif tt == "exam_scope":
                    chapter_tag = tv
                    
            tags_parts = []
            if chapter_tag:
                tags_parts.append(f"<b>教材章节:</b> {html.escape(chapter_tag)}")
            if kp_tags:
                tags_parts.append(f"<b>知识点:</b> {', '.join(html.escape(x) for x in kp_tags)}")
            if method_tags:
                tags_parts.append(f"<b>思想方法:</b> {', '.join(html.escape(x) for x in method_tags)}")
            if ability_tags:
                tags_parts.append(f"<b>核心能力:</b> {', '.join(html.escape(x) for x in ability_tags)}")
            if model_tags:
                tags_parts.append(f"<b>解题模型:</b> {', '.join(html.escape(x) for x in model_tags)}")
                
            tags_html = "<br>".join(tags_parts) if tags_parts else "暂无标签"
            
            ans_text = question.get("answer_text") or "暂无填写的参考答案"
            ans_rich = _safe_html_format(IMAGE_MARKER_PATTERN.sub("", ans_text).strip())
            
            reason_html = ""
            reason_text = question.get("reason")
            if reason_text:
                reason_html = f"""
                <div style="margin-top: 8px; border-top: 1px dashed #cbd5e1; padding-top: 8px; color: #475569;">
                    <b>💡 教学诊断与提示:</b><br>{html.escape(reason_text)}
                </div>
                """

            teacher_box_html = f"""
            <div class="qb-teacher-box" style="margin-top: 5px;">
                <div class="qb-teacher-title">🔑 教师参考答案</div>
                <div class="qb-rich-text" style="font-size: 1rem; color: #1e3a8a; margin-bottom: 10px;">{ans_rich}</div>
                <div style="border-top: 1px dashed #cbd5e1; padding-top: 8px; font-size: 0.88rem; color: #475569; line-height: 1.6;">
                    {tags_html}
                </div>
                {reason_html}
            </div>
            """
            st.markdown(teacher_box_html, unsafe_allow_html=True)
            
            ans_images = _image_paths_from_text(ans_text)
            if ans_images:
                _render_images(ans_images)


def _render_card_action_button(qid: int, qid_for_key: int | None = None) -> None:
    in_basket = qid in _basket_ids()
    key_suffix = f"_{qid_for_key}" if qid_for_key is not None else ""
    st.write("")
    st.write("")
    
    # Premium vertical spacer to center it slightly
    st.markdown("<div style='height: 25px;'></div>", unsafe_allow_html=True)
    
    if in_basket:
        if st.button("❌ 移除试卷栏", key=f"assembly_toggle_rem_{qid}{key_suffix}", type="primary", use_container_width=True):
            _remove_question(qid)
            st.rerun()
    else:
        if st.button("➕ 加入试卷栏", key=f"assembly_toggle_add_{qid}{key_suffix}", type="secondary", use_container_width=True):
            _basket_ids().append(qid)
            st.rerun()


def _run_ai_question_sorting(questions: list[dict[str, Any]]) -> None:
    if not questions:
        st.warning("当前试卷没有题目，无法进行排序！")
        return
        
    with st.spinner("🤖 AI 正在根据教学法（题型分组、难度循序渐进、知识点连贯）规划最优试卷顺序..."):
        from question_bank.services.ai_tagging_service import AITaggingService
        tagging_service = AITaggingService()
        
        # 1. Local canonical pedagogical sorting
        if tagging_service.mock_mode:
            choices = []
            blanks = []
            solutions = []
            for q in questions:
                g = _canonical_type_group(q.get("question_type"))
                if g == "选择题":
                    choices.append(q)
                elif g == "填空题":
                    blanks.append(q)
                else:
                    solutions.append(q)
            
            def _get_diff(item):
                try:
                    return float(item.get("difficulty") or 5.0)
                except ValueError:
                    return 5.0
            choices.sort(key=_get_diff)
            blanks.sort(key=_get_diff)
            solutions.sort(key=_get_diff)
            
            sorted_ids = [int(q["id"]) for q in (choices + blanks + solutions)]
            st.session_state[ORDER_KEY] = sorted_ids
            st.session_state[BASKET_KEY] = sorted_ids
            st.success("✨ (本地 Mock 模式) 智能排序已完成！已自动按“选择题 ➡️ 填空题 ➡️ 解答题”且难度循序渐进的梯度重排试卷。")
            st.rerun()
            return
            
        # 2. AI Sorger
        simplified_questions = []
        for q in questions:
            kp_tags = [t.get("tag_value") for t in q.get("tags", []) if t.get("tag_type") == "knowledge_point" and t.get("tag_value")]
            method_tags = [t.get("tag_value") for t in q.get("tags", []) if t.get("tag_type") == "method" and t.get("tag_value")]
            simplified_questions.append({
                "id": int(q["id"]),
                "question_type": _canonical_type_group(q.get("question_type")),
                "difficulty": q.get("difficulty") or 5.0,
                "knowledge_points": kp_tags,
                "methods": method_tags,
                "question_text": _short_text(q.get("question_text"), 150)
            })
            
        prompt = f"""
        You are an expert junior middle-school math curriculum designer and chief examiner.
        Your task is to review the following set of math exam questions and recommend the most pedagogically sound sorting order.
        
        CRITICAL sorting principles:
        1. **Type Grouping (STRICT)**: Group strictly by: Choice (选择题) first, Fill-in-the-Blank (填空题) second, and Solution (解答题) last.
        2. **Difficulty Progression**: Within each group, progress from easier (lower difficulty) to harder (higher difficulty).
        3. **Knowledge Coherence**: Group closely related concepts to avoid sudden context switching.
        
        Here is the list of questions to sort:
        {json.dumps(simplified_questions, ensure_ascii=False, indent=2)}
        
        Return a single JSON object with a single key "sorted_ids" containing a list of the question IDs in the recommended order.
        Do not add or remove any IDs.
        
        Expected response format:
        {{
            "sorted_ids": [3, 1, 5, 2, 4]
        }}
        """
        try:
            from question_bank.services.ai_tagging_service import _model_for_llm_client
            model = _model_for_llm_client(tagging_service.llm_client, tagging_service.model)
            payload = tagging_service.llm_client.json_from_text(prompt, model=model)
            sorted_ids = payload.get("sorted_ids")
            if isinstance(sorted_ids, list):
                input_ids = {int(q["id"]) for q in questions}
                output_ids = [int(x) for x in sorted_ids if _is_int(x)]
                if set(output_ids) == input_ids:
                    st.session_state[ORDER_KEY] = output_ids
                    st.session_state[BASKET_KEY] = output_ids
                    st.success("✨ AI 智能一键排序已完成！已自动为您应用最优教学逻辑排版。")
                    st.rerun()
                    return
                else:
                    st.warning("AI 返回的题目列表与当前试卷不一致，已自动退回本地安全排序。")
        except Exception as exc:
            st.error(f"AI 智能排序失败：{exc}，已自动降级为本地教学法排序。")
            
        # Fallback local sorting
        choices = []
        blanks = []
        solutions = []
        for q in questions:
            g = _canonical_type_group(q.get("question_type"))
            if g == "选择题":
                choices.append(q)
            elif g == "填空题":
                blanks.append(q)
            else:
                solutions.append(q)
        
        def _get_diff(item):
            try:
                return float(item.get("difficulty") or 5.0)
            except ValueError:
                return 5.0
        choices.sort(key=_get_diff)
        blanks.sort(key=_get_diff)
        solutions.sort(key=_get_diff)
        
        sorted_ids = [int(q["id"]) for q in (choices + blanks + solutions)]
        st.session_state[ORDER_KEY] = sorted_ids
        st.session_state[BASKET_KEY] = sorted_ids
        st.success("✨ 智能排序已完成！已按本地‘选择题 ➡️ 填空题 ➡️ 解答题’的难度渐进梯队重排。")
        st.rerun()


def _image_paths_from_text(value: object) -> list[str]:
    return [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(str(value or ""))]


def _dedupe_paths(paths: list[object]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        text = str(path or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _choose_output_folder() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择导出文件夹")
        root.destroy()
        return selected or ""
    except Exception as exc:  # noqa: BLE001
        st.warning(f"无法打开 Windows 文件夹选择窗口：{exc}")
        return ""


def _render_assembly_records(service: QuestionService) -> None:
    records = list_assembly_records()
    with st.expander(f"📚 历史组卷记录（{len(records)}）", expanded=not bool(_basket_ids())):
        if not records:
            st.caption("暂无组卷记录。导出 Word 后会自动生成记录。")
            return
        for record in records[:30]:
            with st.container(border=True):
                st.markdown(f"**{record.title}**")
                
                summary_parts = []
                for k, v in getattr(record, "question_type_summary", {}).items():
                    if v > 0:
                        summary_parts.append(f"{k}: {v}题")
                summary_text = " (" + ", ".join(summary_parts) + ")" if summary_parts else ""
                
                st.caption(f"{record.created_at} · 共 {record.question_count} 题{summary_text}")
                
                cols = st.columns([1, 1, 1, 1])
                with cols[0]:
                    if st.button("预览", key=f"qb_record_preview_{record.id}", use_container_width=True):
                        st.session_state[BASKET_KEY] = record.question_ids
                        st.session_state[ORDER_KEY] = record.question_ids
                        st.session_state["qb_paper_title"] = record.title
                        st.session_state["assembly_page"] = "composition"
                        st.rerun()
                with cols[1]:
                    if st.button("恢复试题篮", key=f"qb_record_restore_{record.id}", use_container_width=True):
                        st.session_state[BASKET_KEY] = record.question_ids
                        st.session_state[ORDER_KEY] = record.question_ids
                        st.session_state["assembly_page"] = "composition"
                        st.rerun()
                with cols[2]:
                    output_path = Path(record.output_path)
                    if output_path.exists():
                        st.download_button(
                            "下载",
                            data=output_path.read_bytes(),
                            file_name=output_path.name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"qb_record_download_{record.id}",
                            use_container_width=True,
                        )
                    else:
                        st.button("下载", key=f"qb_record_missing_{record.id}", disabled=True, use_container_width=True)
                with cols[3]:
                    if st.button("删除记录", key=f"qb_record_delete_{record.id}", use_container_width=True):
                        delete_assembly_record(record.id)
                        st.rerun()


st.set_page_config(page_title="组卷", layout="wide")
st.markdown(
    """
    <style>
    /* Premium Paper Style */
    .qb-paper-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 24px 28px;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(15, 23, 42, 0.05);
        font-family: 'Times New Roman', SimSun, serif;
    }
    
    .qb-paper-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px dashed #cbd5e1;
        padding-bottom: 10px;
        margin-bottom: 15px;
        font-size: 0.9rem;
        color: #64748b;
    }
    
    .qb-paper-title-tag {
        font-weight: bold;
        font-size: 1.05rem;
        color: #1e293b;
    }
    
    .qb-rich-text {
        white-space: pre-wrap;
        line-height: 1.8;
        font-size: 1.05rem;
        color: #0f172a;
    }
    
    .qb-rich-text sub {
        font-size: 70%;
        vertical-align: sub;
        line-height: 0;
    }
    .qb-rich-text sup {
        font-size: 70%;
        vertical-align: super;
        line-height: 0;
    }
    .qb-rich-text u {
        text-decoration: underline;
        text-underline-offset: 3px;
        text-decoration-thickness: 1.5px;
    }
    
    .qb-rich-text table {
        border-collapse: collapse;
        width: auto;
        max-width: 100%;
        margin: 0.45rem 0 0.8rem;
        table-layout: auto;
    }
    
    .qb-rich-text td,
    .qb-rich-text th {
        border: 1px solid #d8dee9;
        padding: 0.4rem 0.55rem;
        vertical-align: top;
        word-break: break-word;
    }
    
    .qb-rich-text tr:nth-child(even) {
        background: #f8fafc;
    }
    
    /* Badge styling */
    .qb-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 9999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        line-height: 1.2;
        margin-right: 6px;
    }
    
    .qb-badge-easy {
        background-color: #f0fdf4;
        color: #166534;
        border: 1px solid #bbf7d0;
    }
    
    .qb-badge-medium {
        background-color: #fef8e6;
        color: #854d0e;
        border: 1px solid #fef08a;
    }
    
    .qb-badge-hard {
        background-color: #fef2f2;
        color: #991b1b;
        border: 1px solid #fecaca;
    }
    
    /* Teacher Mode Box */
    .qb-teacher-box {
        background-color: #f8fafc;
        border-left: 4px solid #3b82f6;
        padding: 12px 16px;
        margin-top: 15px;
        border-radius: 0 6px 6px 0;
        font-size: 0.92rem;
    }
    
    .qb-teacher-title {
        font-weight: bold;
        color: #1e3a8a;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("智能组卷")

service = QuestionService(question_bank_db_path())
service.initialize_database()
_sync_basket_from_query()
_render_assembly_records(service)


def _canonical_type_group(qtype: str | None) -> str:
    qtype = str(qtype or "").strip()
    if qtype in ["choice", "single_choice", "multiple_choice", "multi_choice", "选择题", "多选题"]:
        return "选择题"
    if qtype in ["fill_blank", "blank", "填空题"]:
        return "填空题"
    return "解答题"


def _reset_assembly_filters() -> None:
    keys_to_reset = [
        "assembly_smart_filter_question_type",
        "assembly_smart_filter_difficulty",
        "assembly_smart_filter_year",
        "assembly_smart_filter_ability",
        "assembly_smart_filter_knowledge",
        "assembly_smart_filter_method",
        "assembly_smart_filter_model",
        "assembly_smart_filter_grade",
        "assembly_smart_filter_typicality",
        "assembly_smart_filter_keyword",
        "assembly_curriculum_chapter_filter",
    ]
    for key in keys_to_reset:
        if key in st.session_state:
            st.session_state.pop(key, None)
    st.rerun()


def _move_question_in_group(question_id: int, offset: int, group_questions: list[dict[str, Any]]) -> None:
    ordered = _ordered_ids()
    group_qids = [int(q["id"]) for q in group_questions]
    if question_id not in group_qids:
        return
    idx_in_group = group_qids.index(question_id)
    target_idx_in_group = idx_in_group + offset
    if target_idx_in_group < 0 or target_idx_in_group >= len(group_qids):
        return
    
    other_qid = group_qids[target_idx_in_group]
    
    pos1 = ordered.index(question_id)
    pos2 = ordered.index(other_qid)
    ordered[pos1], ordered[pos2] = ordered[pos2], ordered[pos1]
    
    st.session_state[ORDER_KEY] = ordered
    st.session_state[BASKET_KEY] = ordered


def _render_statistics_panel(questions: list[dict[str, Any]]) -> None:
    if not questions:
        return
    
    total = len(questions)
    choice_cnt = sum(1 for q in questions if _canonical_type_group(q.get("question_type")) == "选择题")
    fill_cnt = sum(1 for q in questions if _canonical_type_group(q.get("question_type")) == "填空题")
    solution_cnt = total - choice_cnt - fill_cnt
    
    diffs = []
    for q in questions:
        try:
            d = float(q.get("difficulty") or 0)
            if d > 0:
                diffs.append(d)
        except ValueError:
            pass
    avg_diff = sum(diffs) / len(diffs) if diffs else 0.0
    
    st.markdown("##### 📊 当前试卷统计信息")
    metric_cols = st.columns(4)
    metric_cols[0].metric("总题数", f"{total} 道")
    metric_cols[1].metric("题型分布", f"选{choice_cnt} / 填{fill_cnt} / 简{solution_cnt}")
    metric_cols[2].metric("平均难度", f"{avg_diff:.1f}" if avg_diff > 0 else "无")
    
    kp_set = set()
    for q in questions:
        for t in q.get("tags", []):
            if t.get("tag_type") == "knowledge_point" and t.get("tag_value"):
                kp_set.add(t["tag_value"])
    metric_cols[3].metric("知识点覆盖", f"{len(kp_set)} 个")
    if kp_set:
        st.caption(f"覆盖知识点: {', '.join(sorted(kp_set))}")


def _sort_question_results(questions: list[dict[str, Any]], sort_mode: str) -> list[dict[str, Any]]:
    if sort_mode == "试题难度":
        return sorted(questions, key=lambda item: _numeric_value(item.get("difficulty")), reverse=True)
    if sort_mode == "典型程度":
        return sorted(questions, key=lambda item: _numeric_value(item.get("typicality")), reverse=True)
    if sort_mode == "题库新增":
        return sorted(questions, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return questions


def _numeric_value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _curriculum_chapter_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for chapter in CURRICULUM_CHAPTERS:
        text = _cell_text(chapter)
        parts = text.split(" ", 1)
        volume = parts[0] if parts else text
        groups.setdefault(volume, []).append(text)
    return groups


def _short_chapter_label(chapter: str) -> str:
    text = _cell_text(chapter)
    parts = text.split(" ", 1)
    return parts[1] if len(parts) > 1 else text


def _curriculum_tree_filter() -> str:
    selected = str(st.session_state.get("assembly_curriculum_chapter_filter") or "")
    st.markdown("#### 教材章节")
    if st.button("全部章节", key="assembly_curriculum_all", use_container_width=True, type="primary" if not selected else "secondary"):
        st.session_state["assembly_curriculum_chapter_filter"] = ""
        st.rerun()

    groups = _curriculum_chapter_groups()
    for volume, chapters in groups.items():
        expanded = bool(selected and selected in chapters)
        with st.expander(volume, expanded=expanded):
            for chapter in chapters:
                chapter_label = _short_chapter_label(chapter)
                button_type = "primary" if chapter == selected else "secondary"
                if st.button(chapter_label, key=f"assembly_curriculum_{safe_filename_fragment(chapter, 'chapter')}", use_container_width=True, type=button_type):
                    st.session_state["assembly_curriculum_chapter_filter"] = chapter
                    st.rerun()
    if selected:
        st.caption(f"当前：{selected}")
    return selected

def _render_question_selection_page(service: QuestionService) -> None:
    # Top navigation bar
    basket_count = len(_basket_ids())
    nav_cols = st.columns([3.2, 0.8])
    with nav_cols[0]:
        st.markdown("### 🔍 智能选题与筛选 (教材目录 + A4纸张真实预览)")
    with nav_cols[1]:
        if basket_count > 0:
            if st.button(f"🛒 去组卷编排与导出 ({basket_count} 题) ➡️", type="primary", key="go_to_comp_top", use_container_width=True):
                st.session_state["assembly_page"] = "composition"
                st.rerun()
        else:
            st.button("🛒 组卷栏为空 (请先选题)", disabled=True, key="go_to_comp_top_disabled", use_container_width=True)

    st.divider()

    # 1. Collapsible Chapter Tree Directory spanning 100% width
    curr_chapter_filter = st.session_state.get("assembly_curriculum_chapter_filter", "")
    expander_label = f"📂 教材目录章节筛选 (当前已选：{curr_chapter_filter}) · 点击展开/收起" if curr_chapter_filter else "📂 教材目录章节筛选 (未选择) · 点击展开/收起"
    with st.expander(expander_label, expanded=bool(curr_chapter_filter)):
        selected_chapter = _curriculum_tree_filter()

    # 2. Load tag options and query variables
    tag_options = service.list_tag_values(tuple(tag_type for tag_type, _ in TAG_FILTER_CONFIG))
    all_questions = service.query_questions()

    question_type_options = _options_from_questions(all_questions, "question_type")
    year_options = _options_from_questions(all_questions, "year", reverse=True)
    exam_type_options = _options_from_questions(all_questions, "exam_type")
    grade_options = _options_from_questions(all_questions, "grade")
    
    ability_options = tag_options.get("ability", [])
    knowledge_options = tag_options.get("knowledge_point", [])
    error_options = tag_options.get("error_type", [])
    method_options = tag_options.get("method", [])
    model_options = tag_options.get("model", [])

    # 3. Flat Pills Filters Grid spanning 100% width in a gorgeous premium container
    with st.container(border=True):
        filter_header_cols = st.columns([5, 1])
        with filter_header_cols[0]:
            st.markdown("#### 🎯 多维属性智能筛选")
        with filter_header_cols[1]:
            if st.button("🧹 重置筛选", key="reset_assembly_filters_btn", use_container_width=True, type="secondary"):
                _reset_assembly_filters()

        selected_question_type_label = _single_filter_row(
            "题型",
            ["全部", "选择题", "多选题", "填空题", "解答题", *question_type_options],
            key="assembly_smart_filter_question_type",
        )
        selected_difficulty_label = _single_filter_row(
            "难度",
            ["全部", "易", "较易", "中档", "较难", "难"],
            key="assembly_smart_filter_difficulty",
        )
        selected_years = _single_value_filter_row(
            "年份",
            ["全部", *year_options[:8]],
            key="assembly_smart_filter_year",
        )
        selected_abilities = _single_value_filter_row(
            "能力",
            ["全部", *ability_options[:12]],
            key="assembly_smart_filter_ability",
        )
        selected_knowledge_val = _single_value_filter_row(
            "知识点",
            ["全部", *knowledge_options[:15]],
            key="assembly_smart_filter_knowledge",
        )
        selected_methods_val = _single_value_filter_row(
            "思想方法",
            ["全部", *method_options[:15]],
            key="assembly_smart_filter_method",
        )
        selected_models_val = _single_value_filter_row(
            "数学模型",
            ["全部", *model_options[:15]],
            key="assembly_smart_filter_model",
        )
        sort_mode = _single_filter_row(
            "排序",
            ["综合排序", "题库新增", "试题难度", "典型程度"],
            key="assembly_smart_filter_sort",
        )

    # 4. Secondary filters
    bottom_cols = st.columns([1.2, 1.8, 3.0])
    with bottom_cols[0]:
        selected_grade = st.selectbox("年级", ["全部", *grade_options], key="assembly_smart_filter_grade")
    with bottom_cols[1]:
        typicality_range = st.slider("典型度", min_value=1, max_value=10, value=(1, 10), step=1, key="assembly_smart_filter_typicality")
    with bottom_cols[2]:
        keyword_filter = st.text_input("关键词", placeholder="输入试题关键词", key="assembly_smart_filter_keyword")

    # 5. Build selected tags dictionary
    selected_tags: dict[str, list[str]] = {}
    if selected_abilities:
        selected_tags["ability"] = [selected_abilities]
    if selected_chapter:
        selected_tags["exam_scope"] = [selected_chapter]
    if selected_knowledge_val:
        selected_tags["knowledge_point"] = [selected_knowledge_val]
    if selected_methods_val:
        selected_tags["method"] = [selected_methods_val]
    if selected_models_val:
        selected_tags["model"] = [selected_models_val]

    # 6. Build filters for querying questions
    question_types = _question_types_for_label(selected_question_type_label, question_type_options)
    filters = {
        "keyword": keyword_filter or None,
        "difficulty_range": _difficulty_range_for_label(selected_difficulty_label),
        "typicality_range": typicality_range if typicality_range != (1, 10) else None,
        "question_types": question_types or None,
        "years": [selected_years] if selected_years else None,
        "grades": None if selected_grade == "全部" else [selected_grade],
        "tag_filters": selected_tags or None,
    }

    # 7. Query count and pagination
    total_count = service.count_questions(**filters)
    page_size = 10
    total_pages = max((total_count + page_size - 1) // page_size, 1)

    if "assembly_select_page" not in st.session_state:
        st.session_state["assembly_select_page"] = 1

    if st.session_state["assembly_select_page"] > total_pages:
        st.session_state["assembly_select_page"] = 1

    current_page = st.session_state["assembly_select_page"]
    offset = (current_page - 1) * page_size

    # 8. Query matched questions for current page
    matched_qs = service.query_questions(**filters, limit=page_size, offset=offset)
    matched_qs = _sort_question_results(matched_qs, sort_mode)

    st.markdown(f"**找到 {total_count} 道匹配的试题**")
    
    if matched_qs:
        # Action controls for page actions & random sampling
        action_cols = st.columns([2.5, 1.2, 2.5, 3.8])
        with action_cols[0]:
            if st.button("➕ 将当前页题目全部加入试卷", key="assembly_add_all_page", use_container_width=True):
                added = 0
                for mq in matched_qs:
                    qid = int(mq["id"])
                    if qid not in _basket_ids():
                        _basket_ids().append(qid)
                        added += 1
                if added > 0:
                    st.success(f"成功将 {added} 道题目加入试卷！")
                    st.rerun()
        with action_cols[1]:
            rand_cnt = st.number_input("随机抽取数", min_value=1, max_value=max(1, total_count), value=min(5, max(1, total_count)), key="assembly_rand_cnt", label_visibility="collapsed")
        with action_cols[2]:
            if st.button("🎲 随机抽取题目并加入", key="assembly_add_rand", use_container_width=True):
                import random
                all_matched = service.query_questions(**filters)
                candidate_ids = [int(q["id"]) for q in all_matched if int(q["id"]) not in _basket_ids()]
                if not candidate_ids:
                    st.warning("所有匹配的题目已在试卷中！")
                else:
                    chosen = random.sample(candidate_ids, min(int(rand_cnt), len(candidate_ids)))
                    for qid in chosen:
                        _basket_ids().append(qid)
                    st.success(f"成功随机抽取并添加 {len(chosen)} 道题目！")
                    st.rerun()

        st.divider()

        # Render each question card in full high-fidelity paper preview style at the root level!
        for mq in matched_qs:
            _render_premium_question_card(
                index=0, 
                question=mq, 
                preview_mode="教师视角 (显示解析、知识点与难度)", 
                show_basket_toggle=True,
                qid_for_key=mq["id"]
            )
            st.write("")

        # Pagination Controls
        st.divider()
        page_cols = st.columns([1, 2, 1])
        with page_cols[0]:
            if current_page > 1:
                if st.button("⬅️ 上一页", key="assembly_select_prev", use_container_width=True):
                    st.session_state["assembly_select_page"] = current_page - 1
                    st.rerun()
        with page_cols[1]:
            st.markdown(f"<div style='text-align: center; line-height: 2.2rem;'>第 {current_page} / {total_pages} 页 (共 {total_count} 道题)</div>", unsafe_allow_html=True)
        with page_cols[2]:
            if current_page < total_pages:
                if st.button("下一页 ➡️", key="assembly_select_next", use_container_width=True):
                    st.session_state["assembly_select_page"] = current_page + 1
                    st.rerun()
    else:
        st.info("当前筛选条件下暂无题目。可以放宽关键词、难度、典型度或标签筛选。")

    # 9. Real-time Floating Shopping Cart Widget
    basket_count_val = len(_basket_ids())
    st.markdown(
        f"""
        <style>
        .floating-basket-btn {{
            position: fixed;
            right: 25px;
            top: 50%;
            transform: translateY(-50%);
            width: 72px;
            height: 125px;
            background: linear-gradient(135deg, #3b82f6, #1d4ed8);
            color: #ffffff !important;
            text-decoration: none !important;
            border-radius: 18px;
            box-shadow: 0 10px 30px rgba(29, 78, 216, 0.35);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 999999;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            cursor: pointer;
        }}
        .floating-basket-btn:hover {{
            transform: translateY(-50%) scale(1.08);
            box-shadow: 0 12px 35px rgba(29, 78, 216, 0.5);
            background: linear-gradient(135deg, #2563eb, #1e40af);
        }}
        .floating-basket-icon {{
            font-size: 1.9rem;
            margin-bottom: 3px;
        }}
        .floating-basket-text {{
            font-size: 0.75rem;
            font-weight: bold;
            letter-spacing: 1px;
            margin-bottom: 4px;
        }}
        .floating-basket-badge {{
            background-color: #ef4444;
            color: white;
            font-size: 0.72rem;
            font-weight: bold;
            border-radius: 9999px;
            padding: 2px 10px;
            min-width: 14px;
            text-align: center;
            box-shadow: 0 2px 5px rgba(0,0,0,0.25);
        }}
        </style>
        <a href="?assembly_page=composition" target="_self" class="floating-basket-btn">
            <div class="floating-basket-icon">🛒</div>
            <div class="floating-basket-text">试卷栏</div>
            <div class="floating-basket-badge">{basket_count_val}</div>
        </a>
        """,
        unsafe_allow_html=True
    )


def _render_question_composition_page(service: QuestionService) -> None:
    # Top navigation bar
    nav_cols = st.columns([3.2, 0.8])
    with nav_cols[0]:
        st.markdown("### 🛠️ 组卷栏试题编排与导出 (顺序调节、A4预览、Word导出)")
    with nav_cols[1]:
        if st.button("⬅️ 返回题目筛选与选题页", type="secondary", key="back_to_select", use_container_width=True):
            st.session_state["assembly_page"] = "selection"
            st.rerun()

    st.divider()

    ordered_ids = _ordered_ids()
    questions = [service.get_question(question_id) for question_id in ordered_ids]
    questions = [question for question in questions if question is not None]
    question_by_id = {int(question["id"]): question for question in questions}
    ordered_ids = [question_id for question_id in ordered_ids if question_id in question_by_id]

    _render_statistics_panel(questions)

    # 1. Split into Main Column (Left, 7.8) and Sidebar Column (Right, 2.2)
    main_col, right_col = st.columns([7.8, 2.2], gap="large")
    
    with right_col:
        st.markdown("#### 🧩 试卷题目拖拽排序")
        st.caption("拖动 ☰ 手柄上下拖拽题目。🔵选择 🟢填空 🟠解答。")
        
        layout_mode = st.radio("组卷编排方式", ["顺序编排", "分题型编排"], index=0, horizontal=True, key="assembly_layout_mode")
        preview_mode = st.radio("预览视图", ["教师视角 (显示解析、知识点与难度)", "学生视角 (最真实的答题排版)"], index=0, horizontal=True, key="assembly_preview_view")
        
        # Build SortableJS HTML lists
        html_lists = ""
        if layout_mode == "顺序编排":
            squares_html = ""
            for index, question_id in enumerate(ordered_ids, start=1):
                q = question_by_id[question_id]
                qtype = _canonical_type_group(q.get("question_type"))
                style_class = "choice" if qtype == "选择题" else ("blank" if qtype == "填空题" else "solution")
                bullet = "🔵" if qtype == "选择题" else ("🟢" if qtype == "填空题" else "🟠")
                preview = _short_text(q.get("question_text"), 20)
                squares_html += f"""
                <div class="draggable-item {style_class}" data-id="{question_id}">
                    <span class="drag-handle">☰</span>
                    <span class="idx-badge">{index}</span>
                    <span class="bullet">{bullet}</span>
                    <span class="text">{html.escape(preview)}</span>
                </div>
                """
            
            html_lists = f"""
            <div class="sort-section">
              <div class="sort-title">📑 顺序编排 (拖动手柄重排)</div>
              <div id="grid-overall" class="sort-grid">
                {squares_html}
              </div>
            </div>
            """
        else:
            choice_squares = ""
            blank_squares = ""
            solution_squares = ""
            
            overall_idx = 1
            for q in questions:
                qid = int(q["id"])
                qtype = _canonical_type_group(q.get("question_type"))
                style_class = "choice" if qtype == "选择题" else ("blank" if qtype == "填空题" else "solution")
                bullet = "🔵" if qtype == "选择题" else ("🟢" if qtype == "填空题" else "🟠")
                preview = _short_text(q.get("question_text"), 20)
                square_el = f"""
                <div class="draggable-item {style_class}" data-id="{qid}">
                    <span class="drag-handle">☰</span>
                    <span class="idx-badge">{overall_idx}</span>
                    <span class="bullet">{bullet}</span>
                    <span class="text">{html.escape(preview)}</span>
                </div>
                """
                
                if qtype == "选择题":
                    choice_squares += square_el
                elif qtype == "填空题":
                    blank_squares += square_el
                else:
                    solution_squares += square_el
                overall_idx += 1
                
            html_lists = f"""
            <div class="sort-section">
              <div class="sort-title">🔵 选择题 (拖动手柄)</div>
              <div id="grid-choice" class="sort-grid">
                {choice_squares}
              </div>
            </div>
            <div class="sort-section">
              <div class="sort-title">🟢 填空题 (拖动手柄)</div>
              <div id="grid-blank" class="sort-grid">
                {blank_squares}
              </div>
            </div>
            <div class="sort-section">
              <div class="sort-title">🟠 解答题 (拖动手柄)</div>
              <div id="grid-solution" class="sort-grid">
                {solution_squares}
              </div>
            </div>
            """
            
        html_code = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <script src="https://cdnjs.cloudflare.com/ajax/libs/sortablejs/1.15.0/Sortable.min.js"></script>
          <style>
            body {{
              font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
              margin: 0; padding: 5px; background-color: transparent;
            }}
            .sort-section {{
              margin-bottom: 15px;
              border: 1px solid #e2e8f0;
              border-radius: 8px;
              background: #ffffff;
              padding: 10px 12px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            }}
            .sort-title {{
              font-size: 0.82rem; font-weight: bold; color: #475569; margin-bottom: 8px;
            }}
            .sort-grid {{
              display: flex; flex-direction: column; gap: 6px; min-height: 50px; padding: 6px;
              border: 1.5px dashed #cbd5e1; border-radius: 8px; background: #f8fafc;
            }}
            .draggable-item {{
              display: flex;
              align-items: center;
              padding: 8px 10px;
              background-color: #ffffff;
              border: 1px solid #e2e8f0;
              border-radius: 6px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04);
              cursor: grab;
              user-select: none;
              transition: all 0.2s ease;
              font-size: 0.85rem;
              color: #334155;
            }}
            .draggable-item:hover {{
              transform: translateY(-1px);
              box-shadow: 0 3px 5px rgba(0,0,0,0.07);
              border-color: #cbd5e1;
            }}
            .draggable-item:active {{
              cursor: grabbing;
            }}
            .drag-handle {{
              color: #94a3b8;
              margin-right: 8px;
              font-weight: bold;
              font-size: 1rem;
              cursor: move;
            }}
            .idx-badge {{
              display: inline-flex;
              align-items: center;
              justify-content: center;
              width: 18px;
              height: 18px;
              background-color: #f1f5f9;
              border-radius: 9999px;
              font-size: 0.7rem;
              font-weight: bold;
              color: #475569;
              margin-right: 6px;
            }}
            .bullet {{
              margin-right: 6px;
            }}
            .text {{
              white-space: nowrap;
              overflow: hidden;
              text-overflow: ellipsis;
              flex: 1;
            }}
            .draggable-item.choice {{ border-left: 3.5px solid #3b82f6; }}
            .draggable-item.blank {{ border-left: 3.5px solid #10b981; }}
            .draggable-item.solution {{ border-left: 3.5px solid #f97316; }}
            .ghost {{ opacity: 0.35; background: #e2e8f0 !important; border: 1.5px dashed #94a3b8 !important; }}
          </style>
        </head>
        <body>
          {html_lists}
          <script>
            function initSortable(elId) {{
              const el = document.getElementById(elId);
              if (!el) return;
              new Sortable(el, {{
                group: 'shared_paper_questions',
                animation: 180,
                ghostClass: 'ghost',
                handle: '.drag-handle',
                onEnd: function() {{
                  const items = Array.from(document.querySelectorAll('.draggable-item'));
                  const newOrder = items.map(item => item.getAttribute('data-id'));
                  
                  let parentUrl;
                  try {{
                      parentUrl = new URL(document.referrer || window.parent.location.href);
                  }} catch (e) {{
                      parentUrl = new URL(window.location.origin);
                  }}
                  parentUrl.searchParams.set("qb_order", newOrder.join(","));
                  parentUrl.searchParams.set("assembly_page", "composition");
                  window.top.location.href = parentUrl.toString();
                }}
              }});
            }}
            
            Array.from(document.querySelectorAll('.sort-grid')).forEach(grid => {{
              initSortable(grid.id);
            }});
          </script>
        </body>
        </html>
        """
        st.components.v1.html(html_code, height=520, scrolling=True)

    with main_col:
        st.markdown("##### ⚙️ 试卷一键操作")
        opt_cols = st.columns([1.5, 1.5, 3.0])
        with opt_cols[0]:
            if st.button("🤖 AI 智能一键排序与优化", type="primary", use_container_width=True, key="assembly_ai_sort_btn"):
                _run_ai_question_sorting(questions)
        with opt_cols[1]:
            if st.button("🗑️ 一键清空试题篮", type="secondary", use_container_width=True, key="assembly_clear_basket_btn"):
                _clear_basket()
                st.success("已成功清空试题篮！")
                st.session_state["assembly_page"] = "selection"
                st.rerun()

        st.divider()

        default_title = f"{datetime.now().strftime('%Y-%m-%d')}习题"
        title_cols = st.columns(2)
        with title_cols[0]:
            title = st.text_input(
                "组卷标题",
                value=st.session_state.get("qb_paper_title", default_title),
                key="qb_paper_title",
            )
        with title_cols[1]:
            header_text = st.text_input(
                "页眉标题 / 学校考试名称",
                placeholder="例如：深圳市南山区第一中学期末考试",
                key="qb_paper_header",
            )

        st.text_input(
            "姓名和班级栏",
            value="姓名：________________    班级：________________",
            disabled=True,
        )

        st.divider()
        st.markdown("### 📄 试卷真实预览 (A4 纸张排版效果)")
        with st.container(border=True):
            if header_text:
                st.markdown(f"<div style='text-align: center; font-size: 1.25rem; font-weight: bold; margin-bottom: 5px; color: #1e293b;'>{html.escape(header_text)}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: center; font-size: 1.6rem; font-weight: bold; margin-bottom: 15px; color: #0f172a;'>{html.escape(title or default_title)}</div>", unsafe_allow_html=True)
            st.markdown("<div style='text-align: center; font-size: 0.95rem; margin-bottom: 20px; color: #475569;'>班级：________________    姓名：________________    学号：________________</div>", unsafe_allow_html=True)
            st.divider()
            
            if layout_mode == "顺序编排":
                for index, question_id in enumerate(ordered_ids, start=1):
                    question = question_by_id[question_id]
                    _render_premium_question_card(index, question, preview_mode, show_basket_toggle=False)
                    st.write("")
            else:
                groups = {"选择题": [], "填空题": [], "解答题": []}
                for q in questions:
                    g = _canonical_type_group(q.get("question_type"))
                    groups[g].append(q)
                    
                overall_idx = 1
                for gname in ["选择题", "填空题", "解答题"]:
                    gqs = groups[gname]
                    if not gqs:
                        continue
                    st.markdown(f"#### {gname}")
                    for q in gqs:
                        _render_premium_question_card(overall_idx, q, preview_mode, show_basket_toggle=False)
                        st.write("")
                        overall_idx += 1

        st.markdown("#### 💾 导出 Word")
        default_output_dir = project_data_root() / "question_bank" / "outputs"
        st.session_state.setdefault(OUTPUT_DIR_KEY, str(default_output_dir))
        st.session_state.setdefault(INCLUDE_ANSWER_KEY, True)
        export_cols = st.columns([2.4, 0.8, 0.8, 1.2])
        with export_cols[0]:
            output_dir_text = st.text_input("导出位置", key=OUTPUT_DIR_KEY)
        with export_cols[1]:
            if st.button("选择文件夹", width="stretch", key="choose_folder_comp"):
                selected_folder = _choose_output_folder()
                if selected_folder:
                    st.session_state[OUTPUT_DIR_KEY] = selected_folder
                    st.rerun()
        with export_cols[2]:
            include_answer = st.checkbox("包含答案", key=INCLUDE_ANSWER_KEY)
        with export_cols[3]:
            export_clicked = st.button("导出 Word 文档", type="primary", width="stretch", key="export_docx_btn_comp")

        if export_clicked:
            output_dir = Path(output_dir_text or str(default_output_dir)).expanduser()
            exported_question_ids = _ordered_ids()
            try:
                with st.spinner("正在按解析内容导出 Word..."):
                    output_path = export_question_paper_docx(
                        service.db_path,
                        exported_question_ids,
                        output_dir,
                        title=title or default_title,
                        include_answer=include_answer,
                        ensure_previews=False,
                        grouped_by_type=(layout_mode == "分题型编排"),
                        header_text=header_text or None,
                    )
                qtype_summary = {}
                for q in questions:
                    g = _canonical_type_group(q.get("question_type"))
                    qtype_summary[g] = qtype_summary.get(g, 0) + 1
                    
                create_assembly_record(
                    AssemblyRecordCreate(
                        title=title or default_title,
                        question_ids=exported_question_ids,
                        output_path=str(output_path),
                        include_answer=include_answer,
                        question_count=len(exported_question_ids),
                        question_type_summary=qtype_summary,
                    )
                )
                _clear_basket()
                st.success(f"已导出：{output_path}")
                st.info("已清空当前试题篮，并保存到组卷记录。")
                st.session_state["assembly_page"] = "selection"
                st.rerun()
            except Exception as exc:
                st.error(f"导出失败：{exc}")


# Main execution flow based on page state
if "assembly_page" not in st.session_state:
    if _basket_ids():
        st.session_state["assembly_page"] = "composition"
    else:
        st.session_state["assembly_page"] = "selection"

if st.session_state["assembly_page"] == "selection":
    _render_question_selection_page(service)
else:
    if not _basket_ids():
        st.session_state["assembly_page"] = "selection"
        st.rerun()
    _render_question_composition_page(service)
