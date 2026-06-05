from __future__ import annotations

import re
from pathlib import Path

from docx import Document

from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
from question_bank.models.question import QuestionCreate
from question_bank.services.question_service import QuestionService


def test_grouped_docx_export_renumbers_questions_after_grouping(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    solution_id = service.add_question(
        QuestionCreate(
            question_number="3",
            question_type="解答题",
            question_text="解答题正文",
            answer_text="解答题答案",
        )
    )
    choice_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_type="选择题",
            question_text="选择题正文",
            answer_text="选择题答案",
        )
    )
    blank_id = service.add_question(
        QuestionCreate(
            question_number="2",
            question_type="填空题",
            question_text="填空题正文",
            answer_text="填空题答案",
        )
    )

    output_path = export_question_paper_docx(
        db_path,
        [solution_id, choice_id, blank_id],
        tmp_path,
        title="分组题号测试",
        grouped_by_type=True,
    )

    paragraphs = [paragraph.text for paragraph in Document(output_path).paragraphs]
    numbered = [text for text in paragraphs if re.match(r"^\d+\.\s", text)]

    assert [text.split(".", 1)[0] for text in numbered] == ["1", "2", "3"]
