"""Small test fixture composed from the production read/write boundaries."""

from __future__ import annotations

from pathlib import Path

from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.question_write_service import QuestionBankWriteService


class QuestionBankTestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.reader = QuestionBankReadService(self.db_path, data_root=self.db_path.parent)
        self.writer = QuestionBankWriteService(
            self.db_path,
            data_root=self.db_path.parent,
        )

    def add_question(self, question: QuestionCreate) -> int:
        return self.writer.add_question(question)

    def get_question(self, question_id: int):
        return self.reader.get_question(question_id)

    def save_tag_analysis(self, question_id: int, analysis: TagAnalysis, **kwargs):
        return self.writer.save_tag_analysis(question_id, analysis, **kwargs)

    def delete_question(self, question_id: int) -> bool:
        revision = self.writer.get_revision(question_id)
        self.writer.set_deleted(
            question_id,
            expected_revision=revision,
            deleted=True,
        )
        return True


__all__ = ["QuestionBankTestStore"]
