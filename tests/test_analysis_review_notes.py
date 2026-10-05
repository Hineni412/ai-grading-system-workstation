from __future__ import annotations

import json
import sqlite3
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from backend.analysis_review_notes import (
    load_session_notes,
    merge_session_notes,
    session_notes_path,
)
from backend.repositories.grading_database import open_grading_repositories
from tests.test_analysis_report import (
    FakeLLMClient,
    PERSONAL_NARRATIVE,
    _seed_analysis_session,
)


def _note_item(student_id: int, question_id: str, **overrides) -> dict:
    item = {
        "student_id": student_id,
        "student_code": f"S{student_id:03d}",
        "student_name": f"学生{student_id}",
        "class_name": "1班",
        "question_id": question_id,
        "display_label": f"第{question_id}题",
        "note": "建议核对给分",
        "lock_revision": 0,
    }
    item.update(overrides)
    return item


def test_load_session_notes_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_session_notes(tmp_path / "reports", 7) is None


def test_merge_session_notes_replaces_scoped_students(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    total = merge_session_notes(
        reports_dir,
        5,
        score_revision="rev-a",
        items=[
            _note_item(1, "Q1", note="旧提示"),
            _note_item(2, "Q2"),
        ],
        scoped_student_ids={1, 2},
    )
    assert total == 2

    total = merge_session_notes(
        reports_dir,
        5,
        score_revision="rev-b",
        items=[_note_item(1, "Q3", note="新提示")],
        scoped_student_ids={1},
    )
    assert total == 2
    payload = load_session_notes(reports_dir, 5)
    assert payload is not None
    assert payload["score_revision"] == "rev-b"
    assert payload["generated_at"]
    by_student = {item["student_id"]: item for item in payload["items"]}
    assert by_student[1]["question_id"] == "Q3"
    assert by_student[1]["note"] == "新提示"
    assert by_student[2]["question_id"] == "Q2"


def test_merge_session_notes_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    merge_session_notes(
        reports_dir, 3, score_revision="r", items=[], scoped_student_ids=set()
    )
    target = session_notes_path(reports_dir, 3)
    assert target.is_file()
    assert not list(target.parent.glob("*.tmp"))


def test_collect_review_notes_maps_label_and_lock() -> None:
    from backend.reporting.analysis_report_exporter import _collect_review_notes
    from backend.session_analysis import QuestionInfo, StudentReportData

    info_by_qid = {
        "Q2": QuestionInfo(
            question_id="Q2",
            question_type="proof",
            max_score=40,
            stem_summary="",
            canonical_answer="",
        )
    }
    student = StudentReportData(
        result_id=1,
        student_id=9,
        student_code="009",
        student_name="测试学生",
        class_name="2班",
        student_score=80,
        needs_review=False,
        graded_at="",
    )
    narrative = {
        "question_analyses": [
            {"question_id": "2", "review_note": "步骤分给分偏高，建议核对"},
            {"question_id": "Q1", "review_note": "   "},
            {"question_id": "Q9", "note_only": "x"},
        ]
    }
    items = _collect_review_notes(
        narrative, student, info_by_qid, {(9, "Q2"): 3}
    )
    assert len(items) == 1
    item = items[0]
    assert item["student_id"] == 9
    assert item["question_id"] == "Q2"
    assert item["display_label"]
    assert item["note"] == "步骤分给分偏高，建议核对"
    assert item["lock_revision"] == 3


@pytest.fixture
def analysis_db(tmp_path: Path):
    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    return db, session_id, tmp_path


def _narrative_with_note(note: str) -> dict:
    narrative = dict(PERSONAL_NARRATIVE)
    narrative["question_analyses"] = [
        {
            "question_id": "Q2",
            "analysis": "证明过程缺关键步骤。",
            "review_note": note,
        }
    ]
    return narrative


def _student_ids(db) -> dict[str, int]:
    with sqlite3.connect(db.db_path) as conn:
        return {
            name: int(student_id)
            for student_id, name in conn.execute("SELECT id, name FROM students")
        }


def test_personal_export_writes_review_notes_sidecar(
    analysis_db, tmp_path: Path
) -> None:
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator

    db, session_id, root = analysis_db
    reports_dir = tmp_path / "reports"
    client = FakeLLMClient(narrative=_narrative_with_note("建议核对证明步骤给分"))
    generator = AnalysisReportGenerator(
        db,
        tmp_path / "out",
        llm_client_factory=lambda: client,
        narrative_cache_dir=tmp_path / "cache",
        data_root=root,
        reports_dir=reports_dir,
    )
    generator.export_session(
        session_id, "personal_analysis_html", score_revision="rev-test"
    )

    payload = load_session_notes(reports_dir, session_id)
    assert payload is not None
    assert payload["score_revision"] == "rev-test"
    students = _student_ids(db)
    expected = {students["张三"], students["李四"]}
    assert {item["student_id"] for item in payload["items"]} == expected
    assert all(item["question_id"] == "Q2" for item in payload["items"])
    assert all(item["note"] == "建议核对证明步骤给分" for item in payload["items"])
    assert all(item["lock_revision"] == 0 for item in payload["items"])
    assert generator.last_review_note_count == len(payload["items"])


def test_personal_export_merge_scoped_and_lock_revision(
    analysis_db, tmp_path: Path
) -> None:
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator

    db, session_id, root = analysis_db
    reports_dir = tmp_path / "reports"
    students = _student_ids(db)
    lisi = students["李四"]

    client = FakeLLMClient(narrative=_narrative_with_note("第一轮提示"))
    generator = AnalysisReportGenerator(
        db,
        tmp_path / "out",
        llm_client_factory=lambda: client,
        narrative_cache_dir=tmp_path / "cache",
        data_root=root,
        reports_dir=reports_dir,
    )
    generator.export_session(
        session_id, "personal_analysis_html", score_revision="rev-1"
    )
    first = load_session_notes(reports_dir, session_id)
    assert first is not None
    assert len(first["items"]) == 2

    # 教师复核锁定后重新导出：该学生条目被替换并记录当前锁修订号。
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id, revision
            ) VALUES (?, 'batch-x', ?, 'Q2', 20, 40, '教师确认', 'exam_paper', 1, 2)""",
            (session_id, lisi),
        )

    second_client = FakeLLMClient(narrative=_narrative_with_note("第二轮提示"))
    generator2 = AnalysisReportGenerator(
        db,
        tmp_path / "out2",
        llm_client_factory=lambda: second_client,
        narrative_cache_dir=tmp_path / "cache2",
        data_root=root,
        reports_dir=reports_dir,
    )
    generator2.export_session(
        session_id,
        "personal_analysis_html",
        score_revision="rev-2",
        student_ids={lisi},
    )
    merged = load_session_notes(reports_dir, session_id)
    assert merged is not None
    assert len(merged["items"]) == 2
    by_student = {item["student_id"]: item for item in merged["items"]}
    assert by_student[lisi]["note"] == "第二轮提示"
    assert by_student[lisi]["lock_revision"] == 2
    assert by_student[students["张三"]]["note"] == "第一轮提示"


def test_report_export_job_result_includes_review_note_count(
    tmp_path: Path,
) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.repositories.grading_database import open_grading_repositories

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)

    class FakeAnalysisExporter:
        last_review_note_count = 0

        def __init__(self, db, output_dir: Path, **_kwargs) -> None:
            self.output_dir = output_dir

        def export_session(self, session_id: int, report_type: str, **_kw) -> Path:
            self.last_review_note_count = 3
            output_path = self.output_dir / "个人分析报告.zip"
            output_path.write_bytes(b"zip")
            return output_path

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=db.db_path,
        reports_dir=tmp_path / "reports",
        analysis_report_exporter_factory=FakeAnalysisExporter,
        analysis_llm_client_factory=lambda: FakeLLMClient(),
    )
    job = manager.submit(
        "report_export",
        {"session_id": session_id, "report_type": "personal_analysis_html"},
    )
    manager.wait(job.id, timeout=10)
    loaded = manager.get(job.id)
    assert loaded.status == "succeeded"
    assert loaded.result["review_note_count"] == 3
    manager.shutdown()


@pytest.fixture
def notes_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_reports_dir,
        get_review_application_service,
        get_scan_grading_workspace,
    )
    from backend.scan_grading.workspace import ScanGradingWorkspaceError

    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    session_id = db.sessions.create_grading_session("单元测试", "r", "a")
    reports_dir = tmp_path / "reports"

    class StubReviewService:
        def list_items(self, _session_id, _session, **_kwargs):
            return [
                SimpleNamespace(
                    student_id=11,
                    question_id="Q2",
                    review_item_id="batch-1:11:Q2",
                )
            ]

    class StubWorkspace:
        def get_preflight(self, _session_id):
            raise ScanGradingWorkspaceError("no batch")

    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    app.dependency_overrides[get_review_application_service] = (
        lambda: StubReviewService()
    )
    app.dependency_overrides[get_scan_grading_workspace] = (
        lambda: StubWorkspace()
    )
    with TestClient(app) as client:
        yield client, db, session_id, reports_dir


def test_analysis_review_notes_endpoint_missing_file(notes_client) -> None:
    client, _db, session_id, _reports_dir = notes_client
    response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-review-notes"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["generated_at"] is None


def test_analysis_review_notes_endpoint_status_and_item_id(
    notes_client,
) -> None:
    client, db, session_id, reports_dir = notes_client
    merge_session_notes(
        reports_dir,
        session_id,
        score_revision="rev",
        items=[
            _note_item(11, "Q2", lock_revision=0),
            _note_item(12, "Q1", lock_revision=5),
        ],
        scoped_student_ids={11, 12},
    )
    # 学生 11 的 Q2 锁已升过修订号 → 已核对；学生 12 无锁 → 待核对。
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id, revision
            ) VALUES (?, 'batch-1', 11, 'Q2', 20, 40, '教师确认', 'exam_paper', 1, 1)""",
            (session_id,),
        )

    response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-review-notes"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_at"]
    items = {item["student_id"]: item for item in payload["items"]}
    assert items[11]["status"] == "confirmed"
    assert items[11]["review_item_id"] == "batch-1:11:Q2"
    assert items[12]["status"] == "pending"
    assert items[12]["review_item_id"] is None


def test_analysis_review_notes_endpoint_unknown_session(notes_client) -> None:
    client, _db, _session_id, _reports_dir = notes_client
    response = client.get("/api/sessions/9999/reports/analysis-review-notes")
    assert response.status_code == 404
