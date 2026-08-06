from __future__ import annotations

from pathlib import Path

from question_bank.exporters.paper_markdown_exporter import (
    export_question_paper_markdown,
)
from question_bank.models.question import QuestionCreate
from question_bank.services.assembly_basket_state import SectionSpec
from tests.question_bank_support import QuestionBankTestStore


def test_markdown_export_preserves_sections_answers_and_hides_asset_paths(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionBankTestStore(db_path)
    first_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_type="选择题",
            question_text="（5分）第一题[[IMAGE:C:/private/missing.png]]",
            answer_text="A",
            image_paths=["C:/private/missing.png"],
        )
    )
    second_id = service.add_question(
        QuestionCreate(
            question_number="2",
            question_type="解答题",
            question_text="第二题",
            answer_text="解题过程",
        )
    )

    output = export_question_paper_markdown(
        db_path,
        [second_id, first_id],
        tmp_path / "output",
        title="单元练习",
        header_text="匿名学校",
        include_answer=True,
        sections=[
            SectionSpec(title="基础题", question_ids=[first_id]),
            SectionSpec(title="综合题", question_ids=[second_id]),
        ],
    )
    text = output.read_text(encoding="utf-8")

    assert text.index("## 一、基础题") < text.index("## 二、综合题")
    assert text.index("1. （5分）第一题") < text.index("2. 第二题")
    assert "## 答案" in text
    assert "1. A" in text
    assert "2. 解题过程" in text
    assert "图像素材未嵌入" in text
    assert "C:/private" not in text
    assert "[[IMAGE:" not in text
