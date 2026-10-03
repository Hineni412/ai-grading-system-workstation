from __future__ import annotations

import threading
from pathlib import Path
import pytest


@pytest.fixture
def personal_bundle_data(tmp_path):
    from backend.repositories.grading_database import open_grading_repositories
    from tests.test_analysis_report import _seed_analysis_session, FakeLLMClient
    from analysis_report_exporter import AnalysisReportGenerator
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    reports = tmp_path / "reports"
    generator = AnalysisReportGenerator(db, tmp_path / "test-generated", data_root=tmp_path,
        narrative_cache_dir=reports / ".analysis_narrative_cache", llm_client_factory=lambda: FakeLLMClient())
    generator.export_session(sid, "personal_analysis_html", html_only=True)
    students = [s["student_id"] for s in db.results.get_session_results(sid)]
    yield db, sid, students, reports
    db.close()


class PersonalBundleContext:
    def __init__(self, payload, job_id=101):
        self.payload = payload
        self.job_id = job_id
        self.progress = []
        self.cancelled = False

    def raise_if_cancelled(self):
        from backend.jobs.manager import JobCancellationRequested
        if self.cancelled:
            raise JobCancellationRequested()

    def report(self, progress, stage, detail):
        self.progress.append((progress, stage, detail))


def test_personal_bundle_single_and_multiple_sessions_with_checklist(personal_bundle_data):
    import sqlite3
    import zipfile
    from backend.jobs.personal_report_bundle import run_personal_report_bundle
    db, sid, students, reports = personal_bundle_data
    context = PersonalBundleContext(dict(session_ids=[sid], student_ids=[students[0]], scope_label="指定1人"))
    result = run_personal_report_bundle(context=context, db_path=db.db_path, reports_dir=reports, data_root=reports.parent)
    assert result["generated"] == 1 and result["filename"].endswith("_个人报告.html")
    assert "去复核这题" not in Path(result["file_path"]).read_text(encoding="utf-8")
    later = db.sessions.create_grading_session("第二场合成考试", "rubric.json", "answer.json")
    with sqlite3.connect(db.db_path) as connection:
        for student_id in students:
            connection.execute("INSERT INTO session_attendance(session_id,student_id,attendance_status) VALUES (?,?,'absent')", (later, student_id))
    context = PersonalBundleContext(dict(session_ids=[sid, later], student_ids=students, scope_label="指定2人"), job_id=102)
    result = run_personal_report_bundle(context=context, db_path=db.db_path, reports_dir=reports, data_root=reports.parent)
    assert result["generated"] == 2 and result["skipped"] == 2 and result["failed"] == 0
    assert result["filename"] == "本学期_个人报告_2场_2人.zip"
    with zipfile.ZipFile(result["file_path"]) as archive:
        html_names = [n for n in archive.namelist() if n.endswith(".html")]
        assert len(html_names) == 2 and all(n.startswith("1 班/") for n in html_names)
        assert all("单元测试_个人报告.html" in n for n in html_names)
        checklist = archive.read("未导出清单.txt").decode("utf-8")
        assert "第二场合成考试" in checklist and "缺考" in checklist
    assert any("4/4 人次" in detail for _p, _s, detail in context.progress)


def test_personal_bundle_render_failure_isolated_and_cancel_never_publishes(personal_bundle_data, monkeypatch):
    from backend.jobs.personal_report_bundle import run_personal_report_bundle
    from backend.jobs.manager import JobCancellationRequested
    import analysis_report_exporter as exporter
    db, sid, students, reports = personal_bundle_data
    original = exporter._render_personal_html
    def render(data, student, *args, **kwargs):
        if student.student_id == students[0]:
            raise ValueError("synthetic render failure")
        return original(data, student, *args, **kwargs)
    monkeypatch.setattr(exporter, "_render_personal_html", render)
    context = PersonalBundleContext(dict(session_ids=[sid], student_ids=students, scope_label="指定2人"))
    result = run_personal_report_bundle(context=context, db_path=db.db_path, reports_dir=reports, data_root=reports.parent)
    assert (result["generated"], result["failed"]) == (1, 1)
    assert result["missing_items"][0]["reason"] == "渲染失败"
    cancelled = PersonalBundleContext(context.payload, job_id=103)
    report = cancelled.report
    def cancel(progress, stage, detail):
        report(progress, stage, detail)
        cancelled.cancelled = True
    cancelled.report = cancel
    with pytest.raises(JobCancellationRequested):
        run_personal_report_bundle(context=cancelled, db_path=db.db_path, reports_dir=reports, data_root=reports.parent)
    assert not (reports / "personal_report_bundles" / "job-103").exists()


def test_personal_generation_publish_false_has_counts_and_no_artifact(personal_bundle_data, monkeypatch):
    from backend.jobs.default_handlers import _build_report_export_handler
    from analysis_report_exporter import AnalysisReportGenerator
    from tests.test_analysis_report import FakeLLMClient
    db, sid, students, reports = personal_bundle_data
    monkeypatch.setattr("backend.class_analysis.run_cause_analysis", lambda *_args, **_kw: {})
    handler = _build_report_export_handler(db_path=db.db_path, reports_dir=reports,
        report_generator_factory=None, original_paper_exporter_factory=None,
        analysis_report_exporter_factory=AnalysisReportGenerator, analysis_llm_client_factory=lambda: FakeLLMClient(), data_root=reports.parent)
    before = {p for p in reports.rglob("*") if p.suffix in {".html", ".zip"}}
    result = handler(PersonalBundleContext(dict(session_id=sid, student_ids=[students[0]],
        report_type="personal_analysis_html", publish=False)))
    assert result == dict(session_id=sid, report_type="personal_analysis_html", generated=1, failed=0, skipped=0)
    assert {p for p in reports.rglob("*") if p.suffix in {".html", ".zip"}} == before


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
