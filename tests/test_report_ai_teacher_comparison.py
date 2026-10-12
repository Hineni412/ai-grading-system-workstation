from __future__ import annotations
import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories
import json

from pathlib import Path
import pandas
from backend.repositories.db_manager import DBManager
from backend.reporting.report import ReportGenerator
import threading


from backend.report_results import PersonalReportStore, resolve_result_state


@pytest.fixture
def api_report_jobs_personal_api(tmp_path):
    from tests.test_api_review_routes import analysis_report__seed_analysis_session as _seed_analysis_session, analysis_report__add_personal_report_scans as _add_personal_report_scans
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager, get_reports_dir, get_job_file_service
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.files.service import JobFileService
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    papers = _add_personal_report_scans(db, sid, tmp_path)
    reports = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register("report_export", lambda _c: dict(generated=1, failed=0, skipped=0))
    def bundle_handler(context):
        target = reports / "test-personal-download.html"
        return dict(file_path=str(target), filename=target.name, generated=1, failed=0, skipped=0)
    manager.register("personal_report_bundle", bundle_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports
    app.dependency_overrides[get_job_file_service] = lambda: JobFileService(reports)
    with TestClient(app) as client:
        try:
            yield client, db, sid, papers, reports, manager
        finally:
            manager.shutdown()
            db.close()


def test_saved_personal_report_can_open_and_export_without_current_grade_or_model(api_report_jobs_personal_api, monkeypatch):
    from backend.report_results import PersonalReportStore
    from tests.test_api_review_routes import analysis_report_PERSONAL_NARRATIVE as PERSONAL_NARRATIVE
    client, db, sid, papers, reports, manager = api_report_jobs_personal_api
    student_id = papers[0]['student_id']
    store = PersonalReportStore(reports)
    store.save(sid, student_id, narrative=PERSONAL_NARRATIVE,
               source={'student_name': 'TEST-saved-name', 'session_name': 'TEST-saved-exam'})
    with sqlite3.connect(db.db_path) as connection:
        connection.execute("UPDATE session_attendance SET attendance_status='absent' WHERE session_id=? AND student_id=?", (sid, student_id))
    monkeypatch.setattr('backend.model_profiles.content_generation.resolve_content_generation_settings',
                        lambda: pytest.fail('读取和导出保存正文不得初始化模型配置'))
    before = {path: path.read_bytes() for path in reports.rglob('*.json')}
    state = client.get(f'/api/sessions/{sid}/personal-reports').json()['students']
    assert next(row for row in state if row['student_id'] == student_id)['status'] == 'current'
    html = client.get(f'/api/sessions/{sid}/personal-reports/{student_id}/html')
    assert html.status_code == 200
    assert '选择题全对，基础扎实' in html.text
    assert 'TEST-saved-name' in html.text
    assert '当前没有可展示的成绩' in html.text
    exported = client.post(f'/api/sessions/{sid}/reports/export', json={'report_type': 'personal_analysis_html'})
    assert exported.status_code == 202
    manager.wait(exported.json()['id'], timeout=5)
    assert before == {path: path.read_bytes() for path in reports.rglob('*.json')}


def _write_rubric(tmp_path: Path) -> Path:
    from path_manager import get_path_manager

    controlled_dir = (
        get_path_manager().data_root / "config" / "uploaded" / "report-test"
    )
    controlled_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = controlled_dir / f"rubric-{tmp_path.name}.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 5, "parts": []},
                    {"question_id": "Q2", "max_score": 5, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return rubric_path


def _seed_mixed_session(tmp_path: Path) -> Path:
    """AI-graded session where Q1 was overridden by a teacher lock."""
    db_path = tmp_path / "grading.db"
    rubric_path = _write_rubric(tmp_path)
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path)
            VALUES (1, '期末测试', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                ai_student_score, needs_human_review, raw_json
            ) VALUES (1, 1, 1, 1, 10, 8, 7, 0, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, ai_score_awarded,
                deduction_reason, knowledge_ids, error_category, error_summary
            ) VALUES
                (1, 1, 'Q1', 5, 4, '教师认定满分', '["K1"]', '教师已确认', 'teacher_score_locked'),
                (2, 1, 'Q2', 3, 3, '计算错误', '["K2"]', NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id
            ) VALUES (1, 'batch-1', 1, 'Q1', 5, 5, '教师认定满分', 'session_detail', 1)
            """
        )
        conn.commit()
    return db_path


def _seed_manual_only_session(tmp_path: Path) -> Path:
    """No AI results at all; only teacher score locks exist."""
    db_path = tmp_path / "grading.db"
    rubric_path = _write_rubric(tmp_path)
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path)
            VALUES (1, '纯人工测试', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id
            ) VALUES
                (1, 'batch-1', 1, 'Q1', 4, 5, '步骤分', 'exam_paper', 1),
                (1, 'batch-1', 1, 'Q2', 3, 5, NULL, 'exam_paper', 1)
            """
        )
        conn.commit()
    return db_path


def test_comparison_sheet_shows_ai_teacher_and_final_scores(tmp_path: Path) -> None:
    db_path = _seed_mixed_session(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    compare = pandas.read_excel(export_path, sheet_name="AI与人工分对比", header=2)

    assert list(compare.columns) == [
        "班级",
        "学号",
        "学生姓名",
        "题号",
        "满分",
        "AI 得分",
        "人工得分",
        "最终得分",
    ]
    rows = {str(row["题号"]): row for _, row in compare.iterrows()}
    # 只保留教师实际打过分的行；Q2 没有人工得分，不再出现在对比页。
    assert set(rows) == {"Q1"}
    assert rows["Q1"]["AI 得分"] == 4
    assert rows["Q1"]["人工得分"] == 5
    assert rows["Q1"]["最终得分"] == 5


def test_manual_only_session_exports_with_empty_ai_track(tmp_path: Path) -> None:
    db_path = _seed_manual_only_session(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)

    compare = pandas.read_excel(export_path, sheet_name="AI与人工分对比", header=2)
    assert len(compare) == 2
    rows = {str(row["题号"]): row for _, row in compare.iterrows()}
    assert pandas.isna(rows["Q1"]["AI 得分"])
    assert rows["Q1"]["人工得分"] == 4
    assert rows["Q1"]["最终得分"] == 4
    assert rows["Q2"]["人工得分"] == 3
    assert rows["Q2"]["最终得分"] == 3

    scores = pandas.read_excel(export_path, sheet_name="班级成绩总表", header=None)
    assert "张三" in scores.astype(str).values
    assert 7 in scores.values


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


def test_personal_report_export_reuses_pipeline_content_without_model_calls(
    tmp_path,
) -> None:
    """报告导出复用「AI 整理」产物：cache_only 口径、不持有客户端、零模型调用。"""
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.repositories.grading_database import open_grading_repositories
    from tests.test_api_review_routes import analysis_report__seed_analysis_session as _seed_analysis_session

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)

    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_text(self, prompt, **kwargs):
            self.calls += 1
            raise TimeoutError("synthetic")

    captured: dict[str, object] = {}

    class FakeAnalysisExporter:
        def __init__(self, db, output_dir: Path, **kwargs) -> None:
            captured.update(kwargs)
            self.output_dir = output_dir

        def export_session(self, session_id: int, report_type: str, **kwargs) -> Path:
            captured["export_kwargs"] = kwargs
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
    assert captured["llm_client_factory"] is None
    assert captured["export_kwargs"]["narrative_mode"] == "cache_only"
    assert failing.calls == 0  # 导出不再运行错因整理或叙述生成
    assert "cause_analysis" not in loaded.result
    assert Path(loaded.result["file_path"]).is_file()


def report_results__tree_contents(root: Path) -> dict:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            if path.is_file() else None for path in root.rglob('*')}


@pytest.mark.parametrize('failure', ['no_result', 'replace_failed'])
def test_unsuccessful_save_keeps_previous_success_and_leaves_no_temporary_file(tmp_path, monkeypatch, failure):
    import backend.report_results as results
    store = PersonalReportStore(tmp_path)
    previous = store.save(1, 2, narrative={'summary': 'TEST-上次成功'}, needs_update=True)
    before = report_results__tree_contents(tmp_path)
    if failure == 'no_result':
        with pytest.raises(ValueError):
            store.save(1, 2, narrative=None)
    else:
        original = results.os.replace
        def fail_selected(source, target):
            if Path(target) == store._path(1, 2):
                raise OSError('TEST-atomic-replace-failed')
            return original(source, target)
        monkeypatch.setattr(results.os, 'replace', fail_selected)
        with pytest.raises(OSError):
            store.save(1, 2, narrative={'summary': 'TEST-未采用的新内容'})
    assert store.load(1, 2) == previous
    assert report_results__tree_contents(tmp_path) == before


def report_results__write_legacy_results(root: Path, style: str, session_id: int = 7) -> dict[int, dict]:
    if style == 'class_state':
        cache = root / '.cache'
        index = root / '.class_analysis' / f'{session_id}.json'
        key = 'personal_index'
    else:
        cache = root / '.analysis_narrative_cache'
        index = cache / 'personal_index' / f'session_{session_id}.json'
        key = 'students'
    cache.mkdir(parents=True, exist_ok=True)
    index.parent.mkdir(parents=True, exist_ok=True)
    narratives = {11: {'summary': 'TEST-旧缓存正文'}, 22: {'summary': 'TEST-旧内嵌正文'}}
    (cache / 'TEST-key-11.json').write_text(json.dumps({'version': 99, 'narrative': narratives[11]}), encoding='utf-8')
    (cache / 'TEST-orphan.json').write_text(json.dumps({'version': 1, 'narrative': {'summary': 'TEST-无归属正文'}}), encoding='utf-8')
    index.write_text(json.dumps({'version': 99, key: {
        '11': {'cache_key': 'TEST-key-11', 'student_revision': 'TEST-old-value', 'prompt_version': 'TEST-old-method',
               'generated_at': '2026-01-01T00:00:00'},
        '22': {'narrative': narratives[22], 'generated_at': '2026-01-02T00:00:00'},
        'ignored-entry': {'cache_key': 'TEST-orphan'},
    }}), encoding='utf-8')
    return narratives


@pytest.mark.parametrize('style', ['class_state', 'narrative_cache'])
def test_current_success_wins_without_erasing_legacy_sources(tmp_path, style):
    reports = tmp_path / 'TEST-reports'
    narratives = report_results__write_legacy_results(reports, style)
    store = PersonalReportStore(reports)
    legacy_before = report_results__tree_contents(reports)
    saved = store.save(7, 11, narrative={'summary': 'TEST-当前采用正文'})
    current_before = report_results__tree_contents(reports)
    assert store.load(7, 11) == saved
    assert store.list(7)[22]['narrative'] == narratives[22]
    assert set(store.list(7)) == {11, 22}
    assert report_results__tree_contents(reports) == current_before
    assert all(report_results__tree_contents(reports)[name] == value for name, value in legacy_before.items())
    assert len(list(store._session_dir(7).glob('*.json'))) == 1


@pytest.mark.parametrize('style', ['class_state', 'narrative_cache'])
def test_bad_primary_file_can_show_legacy_body_without_repairing_either_file(tmp_path, style):
    reports = tmp_path / 'TEST-reports'
    narratives = report_results__write_legacy_results(reports, style)
    store = PersonalReportStore(reports)
    target = store._path(7, 11)
    target.parent.mkdir(parents=True)
    target.write_text('TEST-bad-json', encoding='utf-8')
    before = report_results__tree_contents(reports)
    saved = store.load(7, 11)
    assert saved['narrative'] == narratives[11] and saved['read_error']
    assert resolve_result_state(saved, 'personal_report')['status'] == 'current'
    assert store.list(7)[11]['narrative'] == narratives[11]
    assert report_results__tree_contents(reports) == before
