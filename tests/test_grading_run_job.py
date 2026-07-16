from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import pytest


def _seed_session(tmp_path: Path):
    from db_manager import DBManager

    (tmp_path / "databases").mkdir(parents=True, exist_ok=True)
    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    return db, session_id


def test_run_grading_job_uses_saved_scan_payload_and_manual_decisions(tmp_path) -> None:
    from backend.jobs.grading_run import run_grading_job

    db, session_id = _seed_session(tmp_path)
    (tmp_path / "rubric.json").write_text("{}", encoding="utf-8")
    (tmp_path / "answer.json").write_text("{}", encoding="utf-8")
    work_dir = tmp_path / "templates" / f"session_{session_id}"
    work_dir.mkdir(parents=True)
    analysis_path = work_dir / "scan_analysis_latest.json"
    analysis_path.write_text(
        json.dumps({"groups": [{"student_name": "Alice"}], "issues": [], "total_pages": 2}),
        encoding="utf-8",
    )
    (work_dir / "scan_manual_decisions_latest.json").write_text(
        json.dumps([{"issue_id": "stale", "action": "invalid"}]),
        encoding="utf-8",
    )
    (work_dir / "scan_decisions_state.json").write_text(
        json.dumps({
            "analysis_identity": hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
            "revision": 2,
            "internal_decisions": [{"issue_id": "issue-1", "action": "invalid"}],
        }),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class FakeService:
        def __init__(self, db_manager: Any, llm_client: Any, question_bank_db_path: Path | None = None) -> None:
            captured["db_path"] = db_manager.db_path
            captured["llm_client"] = llm_client
            captured["question_bank_db_path"] = question_bank_db_path

        def run_session_grading(self, **kwargs: Any):
            captured.update(kwargs)
            yield {"event": "batch_grading_config", "total": 1, "grading_mode": "full_paper"}
            yield {"event": "grading_started", "student_name": "Alice", "current": 1, "total": 1}
            yield {
                "event": "graded",
                "student_name": "Alice",
                "result_id": 9,
                "score": 8,
                "total_score": 10,
                "current": 1,
                "total": 1,
            }
            yield {"event": "session_completed", "progress": {"completed": 1}}

    reports: list[tuple[float, str, str]] = []
    should_cancel = lambda: False
    result = run_grading_job(
        db=db,
        session_id=session_id,
        exams_dir=tmp_path / "exams" / f"session_{session_id}" / "uploaded_scans",
        session_work_dir=work_dir,
        data_root=tmp_path,
        question_bank_db_path=tmp_path / "databases" / "question_bank.db",
        llm_client_factory=lambda: "llm",
        service_factory=FakeService,
        report=lambda progress, stage, detail: reports.append((progress, stage, detail)),
        enhance_images=False,
        max_workers=2,
        requests_per_minute=120,
        grading_mode="full_paper",
        should_cancel=should_cancel,
    )

    assert captured["llm_client"] == "llm"
    assert captured["session_id"] == session_id
    assert captured["rubric_path"] == tmp_path / "rubric.json"
    assert captured["answer_key_path"] == tmp_path / "answer.json"
    assert captured["scan_analysis"]["groups"][0]["student_name"] == "Alice"
    assert captured["manual_decisions"] == [{"issue_id": "issue-1", "action": "invalid"}]
    assert captured["enhance_images"] is False
    assert captured["max_workers"] == 2
    assert captured["requests_per_minute"] == 120
    assert captured["should_cancel"] is should_cancel
    assert result["state"] == "completed"
    assert result["summary"]["graded"] == 1
    assert reports[-1][1] == "grading_completed"


def test_run_grading_job_confirms_cancel_when_service_stops(tmp_path) -> None:
    from backend.jobs.grading_run import run_grading_job
    from backend.jobs.manager import JobCancellationRequested

    db, session_id = _seed_session(tmp_path)
    checks = 0
    captured: dict[str, Any] = {}

    class FakeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run_session_grading(self, **kwargs: Any):
            captured.update(kwargs)
            yield {"event": "session_cancelled", "run_id": 3, "progress": {}}

    def raise_if_cancelled() -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise JobCancellationRequested("cancelled")

    should_cancel = lambda: True
    with pytest.raises(JobCancellationRequested):
        run_grading_job(
            db=db,
            session_id=session_id,
            exams_dir=tmp_path / "exams",
            session_work_dir=tmp_path / "templates" / f"session_{session_id}",
            data_root=tmp_path,
            question_bank_db_path=tmp_path / "databases" / "question_bank.db",
            llm_client_factory=lambda: object(),
            service_factory=FakeService,
            failed_only=True,
            raise_if_cancelled=raise_if_cancelled,
            should_cancel=should_cancel,
        )

    assert captured["should_cancel"] is should_cancel
    assert checks == 2


def test_run_grading_job_requires_scan_for_normal_run(tmp_path) -> None:
    from backend.jobs.grading_run import run_grading_job

    db, session_id = _seed_session(tmp_path)

    with pytest.raises(ValueError, match="scan_analysis_latest"):
        run_grading_job(
            db=db,
            session_id=session_id,
            exams_dir=tmp_path / "exams" / f"session_{session_id}" / "uploaded_scans",
            session_work_dir=tmp_path / "templates" / f"session_{session_id}",
            data_root=tmp_path,
            question_bank_db_path=tmp_path / "databases" / "question_bank.db",
            llm_client_factory=lambda: object(),
        )


def test_run_grading_job_allows_failed_only_without_scan(tmp_path) -> None:
    from backend.jobs.grading_run import run_grading_job

    db, session_id = _seed_session(tmp_path)
    captured: dict[str, Any] = {}

    class FakeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run_session_grading(self, **kwargs: Any):
            captured.update(kwargs)
            yield {"event": "session_completed", "progress": {"completed": 0}}

    result = run_grading_job(
        db=db,
        session_id=session_id,
        exams_dir=tmp_path / "exams" / f"session_{session_id}" / "uploaded_scans",
        session_work_dir=tmp_path / "templates" / f"session_{session_id}",
        data_root=tmp_path,
        question_bank_db_path=tmp_path / "databases" / "question_bank.db",
        llm_client_factory=lambda: object(),
        service_factory=FakeService,
        failed_only=True,
    )

    assert captured["scan_analysis"] is None
    assert captured["manual_decisions"] is None
    assert captured["failed_only"] is True
    assert result["state"] == "completed"


def test_grading_run_handler_persists_result(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    def fake_grading_runner(**kwargs: Any) -> dict[str, object]:
        assert callable(kwargs["raise_if_cancelled"])
        assert callable(kwargs["should_cancel"])
        return {
            "session_id": kwargs["session_id"],
            "state": "completed",
            "summary": {"graded": 2, "failed": 0, "skipped": 0, "conflicts": 0, "scan_issues": 0},
        }

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "databases" / "grading.db",
        reports_dir=tmp_path / "reports",
        exams_dir=tmp_path / "exams",
        templates_dir=tmp_path / "templates",
        data_root=tmp_path,
        grading_runner=fake_grading_runner,
        llm_client_factory=lambda: object(),
    )

    job = manager.submit("grading_run", {"session_id": 5, "grading_mode": "full_paper", "failed_only": False})
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["session_id"] == 5
    assert loaded.result["summary"]["graded"] == 2
