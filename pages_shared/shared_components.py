from __future__ import annotations

import html
from typing import Any
from question_bank.services.question_frequency_service import FrequencyMetrics, frequency_summary
from question_bank.services.question_preview_display import image_display_width


def format_difficulty_badge(difficulty: object) -> str:
    try:
        diff_val = float(difficulty)
    except (TypeError, ValueError):
        return '<span class="qb-badge qb-badge-gray">未标注</span>'

    if diff_val <= 0:
        return '<span class="qb-badge qb-badge-gray">未标注</span>'
    elif diff_val <= 3:
        return f'<span class="qb-badge qb-badge-easy">易 ({diff_val:.1f})</span>'
    elif diff_val <= 7:
        return f'<span class="qb-badge qb-badge-medium">中 ({diff_val:.1f})</span>'
    else:
        return f'<span class="qb-badge qb-badge-hard">难 ({diff_val:.1f})</span>'


def format_type_badge(question_type: object) -> str:
    q_type = str(question_type or "未知").strip()
    return f'<span class="qb-badge qb-badge-gray">{html.escape(q_type)}</span>'


def format_frequency_badge(frequency_metrics: object) -> str:
    if frequency_metrics is None or not getattr(frequency_metrics, "available", False):
        return ""
    summary = frequency_summary(frequency_metrics)
    if not summary:
        return ""
        
    # Build detailed tooltip
    exam_type = getattr(frequency_metrics, "exam_type", "")
    matched = getattr(frequency_metrics, "matched_question_count", 0)
    total_papers = getattr(frequency_metrics, "eligible_paper_count", 0)
    weighted_freq = getattr(frequency_metrics, "weighted_frequency", 0.0)
    
    sim_sum = getattr(frequency_metrics, "similarity_sum", 0.0)
    avg_sim = (sim_sum / matched) if matched > 0 else 0.0

    tooltip_lines = [
        "【考频计算明细】",
        f"- 本年级有效{exam_type}试卷：{total_papers} 卷",
        f"- 相似度 >= 55% 的同类题：{matched} 道 (含本题)",
        f"  • 相似度累加值：{sim_sum:.2f} (平均相似度 {avg_sim:.1%})",
        f"- 频次计算(累加相似度/试卷数)：{sim_sum:.2f} / {total_papers} 卷 = {weighted_freq:.1%}"
    ]
    
    if exam_type == "中考" and getattr(frequency_metrics, "shenzhen_fit_available", False):
        sz_score = getattr(frequency_metrics, "shenzhen_fit_score", 0.0)
        tooltip_lines.append(f"- 深圳适配度：{sz_score:.0%}")
        notes = getattr(frequency_metrics, "shenzhen_fit_notes", ())
        if notes:
            tooltip_lines.append("  备注:")
            for note in notes:
                tooltip_lines.append(f"  • {note}")
                
    title_text = "\n".join(tooltip_lines)
    return (
        f'<span class="qb-badge qb-badge-medium" title="{html.escape(title_text)}">'
        f"{html.escape(summary)}</span>"
    )


def render_tag_panel_markdown(st, question: dict[str, Any]) -> None:
    # Parse AI tags
    kp_tags = []
    ability_tags = []
    method_tags = []
    model_tags = []
    error_tags = []
    chapter_tag = ""
    student_level_tag = ""
    tag_model_names = []
    tag_confidences = []

    for t in question.get("tags", []):
        tt = t.get("tag_type")
        tv = t.get("tag_value")
        if not tv:
            continue
        model_name = str(t.get("model_name") or "").strip()
        if model_name and model_name not in tag_model_names:
            tag_model_names.append(model_name)
        try:
            confidence_value = float(t.get("confidence"))
            tag_confidences.append(confidence_value)
        except (TypeError, ValueError):
            pass
        if tt == "knowledge_point":
            kp_tags.append(tv)
        elif tt == "ability":
            ability_tags.append(tv)
        elif tt == "method":
            method_tags.append(tv)
        elif tt == "model":
            model_tags.append(tv)
        elif tt in ("error_type", "error_prone", "error_prone_point"):
            error_tags.append(tv)
        elif tt == "exam_scope":
            chapter_tag = tv
        elif tt == "student_level":
            student_level_tag = tv

    tag_groups_html = []
    if chapter_tag:
        tag_groups_html.append(f'<span class="qb-badge qb-badge-blue" style="margin-bottom: 4px;">{html.escape(chapter_tag)}</span>')
    if kp_tags:
        tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-green" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in kp_tags))
    if method_tags:
        tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-orange" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in method_tags))
    if ability_tags:
        tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-purple" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in ability_tags))
    if model_tags:
        tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-purple" style="margin-bottom: 4px; background-color: #faf5ff; border: 1px solid #e9d5ff; color: #6b21a8;">{html.escape(x)}</span>' for x in model_tags))
    if error_tags:
        tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-hard" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in error_tags))
    if student_level_tag:
        tag_groups_html.append(f'<span class="qb-badge qb-badge-gray" style="margin-bottom: 4px;">🎯 {html.escape(student_level_tag)}</span>')

    if tag_groups_html:
        st.markdown('<div style="display: flex; flex-wrap: wrap; gap: 4px;">' + "".join(tag_groups_html) + '</div>', unsafe_allow_html=True)
        meta_bits = []
        if tag_model_names:
            meta_bits.append("模型：" + " / ".join(tag_model_names[:2]))
        if tag_confidences:
            meta_bits.append(f"置信度：{max(tag_confidences) * 100:.0f}%")
        if meta_bits:
            st.caption(" · ".join(meta_bits))
    else:
        st.caption("💡 暂无 AI 属性标签。")


import streamlit.components.v1 as components
import os

def render_sortable_list(items: list[dict[str, Any]], key: str | None = None) -> list[str] | None:
    parent_dir = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(parent_dir, "sortable_component")
    _component_func = components.declare_component("sortable_list_v3", path=build_dir)
    return _component_func(items=items, default=None, key=key)
