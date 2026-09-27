from __future__ import annotations

from pathlib import Path

import pytest

from backend.jobs.answer_draft import run_answer_draft_job
from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.store import JobStore
from question_bank.database.schema import connect, initialize_database
from question_bank.services.answer_draft_service import AnswerDraftService


class FakeLLMClient:
    """llm_client.json_from_text 的替身：不触网，固定返回草稿 JSON。"""

    def __init__(
        self,
        payload: dict | None = None,
        *,
        error: BaseException | None = None,
        on_call=None,
    ) -> None:
        self.payload = (
            dict(payload)
            if payload is not None
            else {"answer": "x=1", "analysis": "移项得"}
        )
        self.error = error
        self.on_call = on_call
        self.prompts: list[str] = []

    def json_from_text(self, prompt: str, **_kwargs) -> dict:
        self.prompts.append(prompt)
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return dict(self.payload)


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    return db_path


def _insert_question(
    db_path: Path,
    *,
    question_number: str,
    question_text: str = "解方程 2x = 2",
    question_type: str = "解答题",
    answer_text: str | None = None,
    is_deleted: int = 0,
) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                question_number, question_type, question_text, answer_text,
                is_deleted
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                question_number,
                question_type,
                question_text,
                answer_text,
                is_deleted,
            ),
        )
        return int(cursor.lastrowid)


def _answer_row(db_path: Path, question_id: int):
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT answer_text, needs_review FROM questions WHERE id = ?",
            (int(question_id),),
        ).fetchone()


def test_apply_draft_never_overwrites_an_existing_answer(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    answered = _insert_question(
        db_path,
        question_number="1",
        answer_text="原答案",
    )
    blank = _insert_question(db_path, question_number="2", answer_text="   ")
    service = AnswerDraftService(db_path, llm_client=FakeLLMClient())
    draft_text = "【AI 生成，待教师核对】\n答案：y=2"

    assert service.apply_draft(answered, draft_text) is False
    row = _answer_row(db_path, answered)
    assert row["answer_text"] == "原答案"
    assert row["needs_review"] == 0

    assert service.apply_draft(blank, draft_text) is True
    row = _answer_row(db_path, blank)
    assert row["answer_text"] == draft_text
    assert row["needs_review"] == 1
