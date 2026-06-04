from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from question_bank.exporters.docx_exporter import (
    AUDIENCE_LABELS,
    STAGE_TITLES,
    _group_by_stage,
    _join_or_dash,
    _normalize_audience,
    _output_path,
    export_training_docx,
    load_export_questions,
    resolve_title_name,
    teaching_tip,
)


def export_training_markdown(
    db_path: str | Path,
    recommendations: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    *,
    audience: str,
    display_name: str | None = None,
    student_id: str | None = None,
    class_id: str | None = None,
    use_real_name: bool = False,
) -> Path:
    """Export selected recommendations to Markdown.

    The signature mirrors ``export_training_docx`` so the Streamlit page can
    switch formats without changing its data flow.
    """

    audience = _normalize_audience(audience)
    items = load_export_questions(db_path, recommendations)
    if not items:
        raise ValueError("没有可导出的推荐题目")

    title_name = resolve_title_name(
        display_name=display_name,
        student_id=student_id,
        class_id=class_id,
        use_real_name=use_real_name,
    )
    output_path = _output_path(output_dir, audience=audience, title_name=title_name, suffix=".md")

    lines = [
        f"# {title_name} 专项训练",
        "",
        "说明：根据最近一次考试薄弱点生成",
        "",
        f"版本：{AUDIENCE_LABELS[audience]}",
        "",
    ]
    for stage, stage_items in _group_by_stage(items).items():
        lines.extend([f"## {STAGE_TITLES.get(stage, stage)}", ""])
        for item in stage_items:
            lines.extend(
                [
                    f"### {item.suggested_order or item.question_id}. 来源：{item.source_paper} 第{item.question_number}题",
                    "",
                    item.question_text,
                    "",
                ]
            )
            for image_path in item.image_paths:
                lines.extend([f"![题目图像]({image_path})", ""])
            if audience == "teacher":
                lines.extend(
                    [
                        f"答案：{item.answer_text or '（暂无答案）'}",
                        "",
                        f"知识点：{_join_or_dash(item.knowledge_points)}",
                        "",
                        f"方法标签：{_join_or_dash(item.method_tags)}",
                        "",
                        f"难度：{item.difficulty or '-'}",
                        "",
                        f"典型程度：{item.typicality or '-'}",
                        "",
                        f"推荐原因：{item.recommend_reason or '-'}",
                        "",
                        f"教学提示：{teaching_tip(item)}",
                        "",
                    ]
                )
            else:
                lines.extend(["答题区：", "", "____________________________________________________________", ""])

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path


__all__ = ["export_training_docx", "export_training_markdown"]
