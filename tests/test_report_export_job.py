from __future__ import annotations

import threading
from pathlib import Path


def test_report_export_handler_publishes_annotated_original_pdf(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    class FakeOriginalPaperExporter:
        def __init__(self, db, output_dir: Path) -> None:
            self.db = db
            self.output_dir = output_dir

        def export_session_originals(self, session_id: int) -> Path:
            output_path = self.output_dir / f"七年级期中_{session_id}_批注原卷.pdf"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-1.7 fake")
            return output_path

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        original_paper_exporter_factory=FakeOriginalPaperExporter,
    )

    job = manager.submit(
        "report_export",
        {
            "session_id": 42,
            "report_type": "annotated_original_pdf",
            "score_revision": "revision-1",
        },
    )
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["session_id"] == 42
    assert loaded.result["report_type"] == "annotated_original_pdf"
    assert loaded.result["score_revision"] == "revision-1"
    assert loaded.result["filename"] == f"七年级期中_42_批注原卷_job-{job.id}.pdf"
    assert Path(loaded.result["file_path"]).read_bytes().startswith(b"%PDF")


def test_report_cancel_during_export_does_not_publish_xlsx(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    started = threading.Event()
    release = threading.Event()

    class BlockingReportGenerator:
        def __init__(self, _db_path: Path, reports_dir: Path) -> None:
            self.reports_dir = reports_dir

        def export_session(self, session_id: int) -> Path:
            output_path = self.reports_dir / f"session-{session_id}.xlsx"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"staged")
            started.set()
            assert release.wait(3)
            return output_path

    reports_dir = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=reports_dir,
        report_generator_factory=BlockingReportGenerator,
    )
    try:
        job = manager.submit("report_export", {"session_id": 42})
        assert started.wait(3)
        assert manager.cancel(job.id) is True
        requested = manager.get(job.id)
        assert requested is not None
        assert requested.status == "running"

        release.set()
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert list(reports_dir.glob("*.xlsx")) == []
        assert list(reports_dir.glob(".job-*")) == []
    finally:
        release.set()
        manager.shutdown()


def test_personal_report_export_survives_cause_failures(tmp_path) -> None:
    """错因整理整体异常不阻断报告导出；单题失败也不重发。"""
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.repositories.grading_database import open_grading_repositories
    from tests.test_analysis_report import _seed_analysis_session

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)

    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, prompt, **kwargs):
            self.calls += 1
            raise TimeoutError("synthetic")

    class FakeAnalysisExporter:
        def __init__(self, db, output_dir: Path, **_kwargs) -> None:
            self.output_dir = output_dir

        def export_session(self, session_id: int, report_type: str, **_kwargs) -> Path:
            output_path = self.output_dir / "个人分析报告.zip"
            output_path.write_bytes(b"zip")
            return output_path

    failing = FailingClient()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=db.db_path,
        reports_dir=tmp_path / "reports",
        analysis_report_exporter_factory=FakeAnalysisExporter,
        analysis_llm_client_factory=lambda: failing,
    )
    job = manager.submit(
        "report_export",
        {"session_id": session_id, "report_type": "personal_analysis_html"},
    )
    manager.wait(job.id, timeout=10)
    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert failing.calls == 2  # 两题各试一次，不重发
    assert loaded.result["cause_analysis"]["status"] == "failed"
    assert Path(loaded.result["file_path"]).is_file()
