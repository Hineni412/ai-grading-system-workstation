from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from question_bank.personalized_papers.rendering import PaperRenderError, render_review_docx


def test_review_docx_uses_editable_omml_for_supported_formula(tmp_path: Path) -> None:
    output = tmp_path / "review.docx"
    fallbacks = render_review_docx(
        {
            "paper_instance_id": "a" * 64,
            "series_version": 1,
            "student": {
                "student_id": "1",
                "student_code": "001",
                "student_name": "张三",
                "class_id": "1班",
            },
            "estimated_minutes": 10,
            "budget": {
                "estimated_total_tokens": 100,
                "context_window_tokens": 32768,
            },
            "items": [{
                "task_item_code": "TASK-1",
                "question_id": 1,
                "question_snapshot": {
                    "question_id": 1,
                    "tagging_context": {
                        "question_number": "1",
                        "question_type": "解答题",
                        "question_text": "计算 $x^2+1$ 的值。",
                    },
                    "images": [],
                },
                "recommendation_snapshot": {"question_number": "1"},
            }],
        },
        data_root=tmp_path,
        output_path=output,
    )

    assert fallbacks == ()
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
    assert b"<m:oMath" in document_xml


def test_unsupported_formula_without_source_image_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PaperRenderError, match="source image fallback"):
        render_review_docx(
            {
                "paper_instance_id": "b" * 64,
                "series_version": 1,
                "student": {"student_id": "1", "student_name": "张三"},
                "budget": {"estimated_total_tokens": 100, "context_window_tokens": 32768},
                "items": [{
                    "task_item_code": "TASK-1",
                    "question_id": 1,
                    "question_snapshot": {
                        "question_id": 1,
                        "tagging_context": {
                            "question_number": "1",
                            "question_type": "解答题",
                            "question_text": "计算 $\\input{unsafe}$。",
                        },
                        "images": [],
                    },
                    "recommendation_snapshot": {"question_number": "1"},
                }],
            },
            data_root=tmp_path,
            output_path=tmp_path / "unsafe.docx",
        )
