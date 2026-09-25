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


def _context(tmp_path: Path, payload: dict) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("answer_draft", payload)
    assert store.mark_running(job.id)
    return JobContext(job.id, job.job_type, job.payload, store), store


def test_draft_for_questions_writes_marked_answer_and_needs_review(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    question_id = _insert_question(db_path, question_number="1")
    client = FakeLLMClient()
    service = AnswerDraftService(db_path, llm_client=client)

    result = service.draft_for_questions([question_id])

    assert result["successful"] == [question_id]
    assert result["failed"] == []
    assert result["skipped"] == []
    assert len(client.prompts) == 1
    assert "解方程 2x = 2" in client.prompts[0]
    row = _answer_row(db_path, question_id)
    assert row["needs_review"] == 1
    assert row["answer_text"].startswith("【AI 生成")
    assert "答案：x=1" in row["answer_text"]
    assert "解析：移项得" in row["answer_text"]


def test_draft_for_questions_skips_answered_and_deleted_questions(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    answered = _insert_question(
        db_path,
        question_number="1",
        answer_text="已有答案",
    )
    missing = _insert_question(db_path, question_number="2")
    deleted = _insert_question(db_path, question_number="3", is_deleted=1)
    client = FakeLLMClient()
    service = AnswerDraftService(db_path, llm_client=client)

    result = service.draft_for_questions([answered, missing, deleted])

    assert result["successful"] == [missing]
    assert result["failed"] == []
    assert sorted(result["skipped"]) == sorted([answered, deleted])
    # 已有答案和已删除的题不触发模型调用。
    assert len(client.prompts) == 1
    row = _answer_row(db_path, answered)
    assert row["answer_text"] == "已有答案"
    assert row["needs_review"] == 0


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


def test_draft_for_questions_fails_candidates_when_model_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import analysis_report_exporter

    monkeypatch.setattr(
        analysis_report_exporter,
        "resolve_content_generation_settings",
        lambda: None,
    )
    db_path = _make_db(tmp_path)
    first = _insert_question(db_path, question_number="1")
    second = _insert_question(db_path, question_number="2")
    answered = _insert_question(
        db_path,
        question_number="3",
        answer_text="已有",
    )
    service = AnswerDraftService(db_path, llm_client=None)

    result = service.draft_for_questions([first, second, answered])

    assert result["successful"] == []
    assert result["skipped"] == [answered]
    assert [item["question_id"] for item in result["failed"]] == [first, second]
    assert {item["category"] for item in result["failed"]} == {
        "model_not_configured"
    }
    assert _answer_row(db_path, first)["answer_text"] is None


def test_answer_draft_job_reports_ids_outcome_and_public_messages(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    missing = _insert_question(db_path, question_number="1")
    answered = _insert_question(
        db_path,
        question_number="2",
        answer_text="已有",
    )
    failing = _insert_question(db_path, question_number="3")
    context, _store = _context(
        tmp_path,
        {"question_ids": [missing, answered, failing]},
    )

    result = run_answer_draft_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=tmp_path,
        llm_client_factory=lambda: FakeLLMClient(),
    )

    assert result["outcome"] == "complete"
    assert result["successful_question_ids"] == [missing, failing]
    assert result["skipped_question_ids"] == [answered]
    assert result["failed_question_ids"] == []
    assert result["failures"] == []
    assert result["requested_count"] == 3
    assert result["retryable"] is False
    assert _answer_row(db_path, missing)["needs_review"] == 1
    assert _answer_row(db_path, answered)["answer_text"] == "已有"


def test_answer_draft_job_classifies_model_errors_as_retryable_failures(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    missing = _insert_question(db_path, question_number="1")
    context, _store = _context(tmp_path, {"question_ids": [missing]})
    client = FakeLLMClient(error=RuntimeError("connection timed out"))

    result = run_answer_draft_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=tmp_path,
        llm_client_factory=lambda: client,
    )

    assert result["outcome"] == "failed"
    assert result["successful_question_ids"] == []
    assert result["failed_question_ids"] == [missing]
    assert result["retryable"] is True
    assert result["failures"] == [
        {
            "question_id": missing,
            "category": "timeout",
            "message": "生成答案超时，可稍后重试未完成题目。",
        }
    ]
    assert _answer_row(db_path, missing)["answer_text"] is None


def test_answer_draft_job_without_model_uses_public_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import analysis_report_exporter

    monkeypatch.setattr(
        analysis_report_exporter,
        "resolve_content_generation_settings",
        lambda: None,
    )
    db_path = _make_db(tmp_path)
    missing = _insert_question(db_path, question_number="1")
    context, _store = _context(tmp_path, {"question_ids": [missing]})

    result = run_answer_draft_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=tmp_path,
        llm_client_factory=lambda: None,
    )

    assert result["outcome"] == "failed"
    assert result["failed_question_ids"] == [missing]
    assert result["failures"][0]["category"] == "model_not_configured"
    assert "内容生成模型" in result["failures"][0]["message"]
    assert _answer_row(db_path, missing)["answer_text"] is None


def test_answer_draft_job_stops_when_cancelled_between_questions(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    first = _insert_question(db_path, question_number="1")
    second = _insert_question(db_path, question_number="2")
    context, store = _context(tmp_path, {"question_ids": [first, second]})

    def _cancel_after_first_call() -> None:
        store.request_cancel(context.job_id)

    client = FakeLLMClient(on_call=_cancel_after_first_call)

    with pytest.raises(JobCancellationRequested):
        run_answer_draft_job(
            context=context,
            question_bank_db_path=db_path,
            data_root=tmp_path,
            llm_client_factory=lambda: client,
        )

    # 取消发生在第二题之前：第一题按单题语义已写入，第二题没有动。
    assert "答案：x=1" in _answer_row(db_path, first)["answer_text"]
    assert _answer_row(db_path, second)["answer_text"] is None


def test_answer_draft_job_rejects_invalid_question_ids(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    context, _store = _context(tmp_path, {"question_ids": [0, -3]})

    with pytest.raises(ValueError):
        run_answer_draft_job(
            context=context,
            question_bank_db_path=db_path,
            data_root=tmp_path,
            llm_client_factory=lambda: FakeLLMClient(),
        )
