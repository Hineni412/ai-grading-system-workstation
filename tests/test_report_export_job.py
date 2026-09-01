from __future__ import annotations

import threading
from pathlib import Path


def test_report_export_handler_persists_generated_file_result(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    class FakeReportGenerator:
        def __init__(self, db_path: Path, reports_dir: Path) -> None:
            self.db_path = db_path
            self.reports_dir = reports_dir

        def export_session(self, session_id: int) -> Path:
            output_path = self.reports_dir / f"session-{session_id}.xlsx"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake xlsx")
            return output_path

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        report_generator_factory=FakeReportGenerator,
    )

    job = manager.submit("report_export", {"session_id": 42})
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["session_id"] == 42
    assert loaded.result["filename"] == f"session-42_job-{job.id}.xlsx"
    assert Path(loaded.result["file_path"]).name == loaded.result["filename"]
    assert Path(loaded.result["file_path"]).exists()


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


def test_report_export_handler_passes_frozen_excel_options_to_generator(
    tmp_path,
) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    received: dict[str, object] = {}

    class CapturingReportGenerator:
        def __init__(self, _db_path: Path, reports_dir: Path) -> None:
            self.reports_dir = reports_dir

        def export_session(
            self,
            session_id: int,
            *,
            score_excel_options: dict[str, object] | None = None,
        ) -> Path:
            received["session_id"] = session_id
            received["options"] = score_excel_options
            output_path = self.reports_dir / "report.xlsx"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"xlsx")
            return output_path

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        report_generator_factory=CapturingReportGenerator,
    )

    job = manager.submit(
        "report_export",
        {
            "session_id": 42,
            "report_type": "score_excel",
            "score_excel_options": {
                "hide_bottom_enabled": True,
                "hide_bottom_n": 8,
                "manual_hidden_student_ids": [3, 7],
            },
        },
    )
    manager.wait(job.id, timeout=5)

    assert manager.get(job.id).status == "succeeded"
    assert received == {
        "session_id": 42,
        "options": {
            "hide_bottom_enabled": True,
            "hide_bottom_n": 8,
            "manual_hidden_student_ids": [3, 7],
        },
    }


def test_report_export_handler_publishes_analysis_report(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    class FakeAnalysisExporter:
        def __init__(
            self,
            db,
            output_dir: Path,
            *,
            llm_client_factory=None,
            narrative_cache_dir=None,
            data_root=None,
        ) -> None:
            self.output_dir = output_dir
            self.narrative_cache_dir = narrative_cache_dir

        def export_session(
            self, session_id: int, report_type: str, *, score_revision: str = ""
        ) -> Path:
            suffix = ".zip" if report_type == "personal_analysis_html" else ".html"
            output_path = self.output_dir / f"单元测试_分析报告{suffix}"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake analysis report")
            return output_path

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        analysis_report_exporter_factory=FakeAnalysisExporter,
        analysis_llm_client_factory=lambda: None,
    )

    job = manager.submit(
        "report_export",
        {
            "session_id": 42,
            "report_type": "personal_analysis_html",
            "score_revision": "revision-9",
        },
    )
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["report_type"] == "personal_analysis_html"
    assert loaded.result["score_revision"] == "revision-9"
    assert loaded.result["filename"] == f"单元测试_分析报告_job-{job.id}.zip"
    assert Path(loaded.result["file_path"]).is_file()


def test_report_export_handler_rejects_unknown_report_type(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
    )

    job = manager.submit(
        "report_export",
        {"session_id": 42, "report_type": "untrusted_html"},
    )
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "failed"
    assert "report_type" in loaded.error
    assert list((tmp_path / "reports").glob("*")) == []


def test_report_export_handler_rejects_missing_session_id(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
    )

    job = manager.submit("report_export", {})
    manager.wait(job.id, timeout=5)

    loaded = manager.get(job.id)
    assert loaded.status == "failed"
    assert "session_id" in loaded.error


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


def test_report_cancel_during_pdf_export_does_not_publish_partial_file(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    started = threading.Event()
    release = threading.Event()

    class BlockingOriginalPaperExporter:
        def __init__(self, _db, output_dir: Path) -> None:
            self.output_dir = output_dir

        def export_session_originals(self, session_id: int) -> Path:
            output_path = self.output_dir / f"session-{session_id}.pdf"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF partial")
            started.set()
            assert release.wait(3)
            return output_path

    reports_dir = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=reports_dir,
        original_paper_exporter_factory=BlockingOriginalPaperExporter,
    )
    try:
        job = manager.submit(
            "report_export",
            {"session_id": 42, "report_type": "annotated_original_pdf"},
        )
        assert started.wait(3)
        assert manager.cancel(job.id) is True
        release.set()
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert list(reports_dir.glob("*.pdf")) == []
        assert list(reports_dir.glob(".job-*")) == []
    finally:
        release.set()
        manager.shutdown()


def test_concurrent_report_exports_publish_distinct_job_owned_files(tmp_path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    barrier = threading.Barrier(2)

    class BarrierReportGenerator:
        def __init__(self, _db_path: Path, reports_dir: Path) -> None:
            self.reports_dir = reports_dir

        def export_session(self, session_id: int) -> Path:
            output_path = self.reports_dir / f"session-{session_id}.xlsx"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"staged")
            barrier.wait(timeout=3)
            return output_path

    reports_dir = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=2)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=reports_dir,
        report_generator_factory=BarrierReportGenerator,
    )
    try:
        first = manager.submit("report_export", {"session_id": 42})
        second = manager.submit("report_export", {"session_id": 42})
        manager.wait(first.id, timeout=5)
        manager.wait(second.id, timeout=5)

        loaded = [manager.get(first.id), manager.get(second.id)]
        assert [job.status for job in loaded] == ["succeeded", "succeeded"]
        published_paths = [Path(job.result["file_path"]) for job in loaded]
        assert len(set(published_paths)) == 2
        assert all(path.is_file() for path in published_paths)
        assert [job.result["filename"] for job in loaded] == [
            path.name for path in published_paths
        ]
        assert set(reports_dir.glob("*.xlsx")) == set(published_paths)
    finally:
        manager.shutdown()
