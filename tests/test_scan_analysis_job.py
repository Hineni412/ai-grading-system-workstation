from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def test_run_scan_analysis_persists_payload_and_summary(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config_workspace.publish import load_editor_config
    from backend.jobs.scan_analysis import run_scan_analysis
    from db_manager import DBManager, StudentRecord
    from scanner import ExamPaperGroup, ScanAnalysis

    # db lives under a "databases" directory so load_editor_config infers
    # tmp_path as the controlled data root for the session config files.
    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    student_id = db.list_students()[0]["id"]
    rubric_path = tmp_path / "rubric.json"
    answer_key_path = tmp_path / "answer.json"
    rubric_path.write_text("{}", encoding="utf-8")
    answer_key_path.write_text("{}", encoding="utf-8")
    session_id = db.create_grading_session(
        "Exam A",
        str(rubric_path),
        str(answer_key_path),
    )
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    db.add_answer_region(
        session_id,
        template_id,
        {
            "page": "front",
            "region_order": 1,
            "x": 10,
            "y": 20,
            "w": 200,
            "h": 80,
            "mapped_question_id": "Q1",
            "is_confirmed": True,
        },
    )
    db.mark_template_confirmed(session_id, True)
    scan_dir = tmp_path / "exams" / f"session_{session_id}" / "uploaded_scans"
    scan_dir.mkdir(parents=True)
    (scan_dir / "001_front.jpg").write_bytes(b"fake image")
    work_dir = tmp_path / "templates" / f"session_{session_id}"
    work_dir.mkdir(parents=True)
    (work_dir / "scan_upload_batch.json").write_text(
        json.dumps({"batch_id": "anonymous-batch-1"}),
        encoding="utf-8",
    )

    class FakeScanner:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["exams_dir"] == scan_dir
            assert kwargs["enhance_images"] is False
            assert kwargs["ocr_workers"] == 3

        def analyze(self, students: list[dict[str, Any]]) -> ScanAnalysis:
            assert students[0]["name"] == "Alice"
            return ScanAnalysis(
                groups=[
                    ExamPaperGroup(
                        front_image=scan_dir / "front.jpg",
                        back_image=scan_dir / "back.jpg",
                        student_name="Alice",
                        student_id=student_id,
                        detected_name="Alice",
                    )
                ],
                issues=[],
                warnings=[],
                total_pages=2,
            )

    config_revision = load_editor_config(db, session_id).revision
    result = run_scan_analysis(
        db=db,
        session_id=session_id,
        exams_dir=scan_dir,
        session_work_dir=work_dir,
        data_root=tmp_path,
        llm_client_factory=lambda: object(),
        scanner_factory=FakeScanner,
        enhance_images=False,
        ocr_workers=3,
        front_page_parity="odd",
        scan_batch_id="anonymous-batch-1",
        config_revision=config_revision,
    )

    output_path = Path(result["scan_analysis_path"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["enhance_images"] is False
    assert payload["scan_batch_id"] == "anonymous-batch-1"
    assert payload["config_revision"] == config_revision
    assert payload["groups"][0]["student_name"] == "Alice"
    assert result["summary"] == {
        "auto_matched": 1,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 2,
    }


def test_scan_analysis_handler_persists_result(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    def fake_scan_runner(**kwargs: Any) -> dict[str, object]:
        assert callable(kwargs["raise_if_cancelled"])
        return {
            "session_id": kwargs["session_id"],
            "scan_analysis_path": str(tmp_path / "scan_analysis_latest.json"),
            "summary": {"auto_matched": 2, "issues": 1, "absent_candidates": 0, "total_pages": 6},
        }

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        exams_dir=tmp_path / "exams",
        templates_dir=tmp_path / "templates",
        data_root=tmp_path,
        scan_runner=fake_scan_runner,
        llm_client_factory=lambda: object(),
    )

    job = manager.submit("scan_analysis", {"session_id": 7, "enhance_images": False})
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["session_id"] == 7
    assert loaded.result["summary"]["auto_matched"] == 2


def test_scan_cancel_after_analyze_preserves_previous_latest_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs.manager import JobCancellationRequested
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    class FakeDb:
        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(revision="config-revision"),
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    latest = work_dir / "scan_analysis_latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text('{"version":"previous"}', encoding="utf-8")
    cancelled = False

    class CancellingScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            nonlocal cancelled
            cancelled = True
            return ScanAnalysis(total_pages=2)

    def raise_if_cancelled() -> None:
        if cancelled:
            raise JobCancellationRequested("cancelled")

    with pytest.raises(JobCancellationRequested):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=CancellingScanner,
            config_revision="config-revision",
            raise_if_cancelled=raise_if_cancelled,
        )

    assert latest.read_text(encoding="utf-8") == '{"version":"previous"}'
    assert list(latest.parent.glob(".scan_analysis_latest.*.tmp")) == []


def test_stale_scan_batch_cannot_publish_over_the_current_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    class FakeDb:
        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    class FakeScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            return ScanAnalysis(total_pages=1)

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    work_dir.mkdir(parents=True)
    latest = work_dir / "scan_analysis_latest.json"
    latest.write_text('{"version":"current"}', encoding="utf-8")
    (work_dir / "scan_upload_batch.json").write_text(
        json.dumps({"batch_id": "current-batch"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(revision="config-revision"),
    )

    with pytest.raises(ValueError, match="batch"):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=FakeScanner,
            config_revision="config-revision",
            scan_batch_id="stale-batch",
        )

    assert latest.read_text(encoding="utf-8") == '{"version":"current"}'
    assert list(work_dir.glob(".scan_analysis_latest.*.tmp")) == []


def test_batch_bound_scan_cannot_publish_when_manifest_is_missing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    class FakeDb:
        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    class FakeScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            return ScanAnalysis(total_pages=1)

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    work_dir.mkdir(parents=True)
    latest = work_dir / "scan_analysis_latest.json"
    latest.write_text('{"version":"current"}', encoding="utf-8")

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(revision="config-revision"),
    )
    with pytest.raises(ValueError, match="manifest|batch"):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=FakeScanner,
            config_revision="config-revision",
            scan_batch_id="missing-batch",
        )

    assert latest.read_text(encoding="utf-8") == '{"version":"current"}'
    assert list(work_dir.glob(".scan_analysis_latest.*.tmp")) == []


def test_run_scan_analysis_rejects_session_without_confirmed_template(tmp_path) -> None:
    from backend.jobs.scan_analysis import run_scan_analysis
    from db_manager import DBManager, StudentRecord

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    db.upsert_students([StudentRecord("S001", "Alice", "Class 1")])
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")
    scan_dir = tmp_path / "exams" / f"session_{session_id}" / "uploaded_scans"
    scan_dir.mkdir(parents=True)
    (scan_dir / "001_front.jpg").write_bytes(b"fake image")

    with pytest.raises(ValueError, match="template"):
        run_scan_analysis(
            db=db,
            session_id=session_id,
            exams_dir=scan_dir,
            session_work_dir=tmp_path / "templates" / f"session_{session_id}",
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
        )


def test_template_change_during_scan_analysis_preserves_previous_preflight(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    template_changed = False

    class FakeDb:
        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    class ChangingScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            nonlocal template_changed
            template_changed = True
            return ScanAnalysis(total_pages=2)

    def current_template(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            template_id=1,
            template_fingerprint=("b" if template_changed else "a") * 64,
            first_page_role="front",
            is_confirmed=True,
            regions_snapshot_pending=False,
        )

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(revision="config-revision"),
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.TemplateUploadService.load_current",
        current_template,
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    work_dir.mkdir(parents=True)
    latest = work_dir / "scan_analysis_latest.json"
    latest.write_text('{"version":"previous"}', encoding="utf-8")

    with pytest.raises(ValueError, match="template changed"):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=ChangingScanner,
            front_page_parity="odd",
            template_id=1,
            template_fingerprint="a" * 64,
            template_first_page_role="front",
            config_revision="config-revision",
        )

    assert latest.read_text(encoding="utf-8") == '{"version":"previous"}'
    assert list(work_dir.glob(".scan_analysis_latest.*.tmp")) == []
