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
