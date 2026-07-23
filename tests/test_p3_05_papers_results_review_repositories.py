"""P3-05 grading-data repositories on temporary SQLite databases."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from backend.domain_models import GradingResult, QuestionGradingDetail
from db_manager import DBManager


def _initialized_database(tmp_path: Path) -> Path:
    database = tmp_path / "grading.db"
    DBManager(database).initialize()
    return database


def _seed_session_and_student(database: Path) -> tuple[int, int]:
    with sqlite3.connect(database) as connection:
        student_id = int(
            connection.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('001', 'Alpha', 'Class-A')
                """
            ).lastrowid
        )
        session_id = int(
            connection.execute(
                """
                INSERT INTO grading_sessions (
                    session_name, rubric_path, answer_key_path, status
                ) VALUES ('Session', '', '', 'created')
                """
            ).lastrowid
        )
    return session_id, student_id


def _grading_result(
    *,
    score: float = 7.0,
    question_id: str = "Q1",
) -> GradingResult:
    return GradingResult(
        student_name="Alpha",
        total_score=10,
        student_score=score,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id=question_id,
                score_awarded=score,
                deduction_reason="worked example",
                knowledge_id="K1",
            )
        ],
        raw_json={"grading_completeness": {"status": "complete"}},
    )


def test_paper_repository_preserves_create_status_and_assignment_guard(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.papers import PaperRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id, student_id = _seed_session_and_student(database)
    repository = PaperRepositoryGateway(SQLiteConnectionFactory(database))

    paper_id = repository.create_exam_paper(
        session_id,
        "front.png",
        "back.png",
        "Alpha",
        student_id,
        "matched",
        "pending",
    )
    assert repository.get_paper_statuses([paper_id]) == [
        {
            "id": paper_id,
            "processing_status": "pending",
            "error_message": None,
        }
    ]
    assert (
        repository.update_exam_paper_status_if_current_assignment(
            paper_id,
            student_id + 1,
            "graded",
        )
        is False
    )
    assert repository.update_exam_paper_status_if_current_assignment(
        paper_id,
        student_id,
        "failed",
        "retry required",
    )
    assert repository.get_paper_statuses([paper_id]) == [
        {
            "id": paper_id,
            "processing_status": "failed",
            "error_message": "retry required",
        }
    ]


def test_result_repository_publishes_only_for_current_paper_assignment(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.papers import PaperRepositoryGateway
    from backend.repositories.results import ResultRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id, student_id = _seed_session_and_student(database)
    sessions = SQLiteConnectionFactory(database)
    papers = PaperRepositoryGateway(sessions)
    results = ResultRepositoryGateway(sessions)
    paper_id = papers.create_exam_paper(
        session_id,
        "front.png",
        "back.png",
        "Alpha",
        student_id,
        "matched",
        "pending",
    )

    assert (
        results.publish_session_result_if_current_assignment(
            session_id,
            student_id + 1,
            paper_id,
            _grading_result(),
        )
        is None
    )
    assert results.get_session_results(session_id) == []

    result_id = results.publish_session_result_if_current_assignment(
        session_id,
        student_id,
        paper_id,
        _grading_result(),
    )
    assert isinstance(result_id, int)
    assert papers.get_paper_statuses([paper_id])[0]["processing_status"] == "graded"
    assert results.get_session_results(session_id)[0]["student_score"] == 7.0
    assert results.get_result_details(result_id)[0]["question_id"] == "Q1"


def test_paper_repository_exposes_progress_projection(tmp_path: Path) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.papers import PaperRepositoryGateway
    from backend.repositories.results import ResultRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id, student_id = _seed_session_and_student(database)
    sessions = SQLiteConnectionFactory(database)
    papers = PaperRepositoryGateway(sessions)
    results = ResultRepositoryGateway(sessions)
    paper_id = papers.create_exam_paper(
        session_id,
        "front.png",
        "back.png",
        "Alpha",
        student_id,
        "matched",
        "pending",
    )
    results.publish_session_result_if_current_assignment(
        session_id,
        student_id,
        paper_id,
        _grading_result(),
    )

    assert papers.get_session_progress_source(session_id) == {
        "total": 1,
        "matched": 1,
        "unmatched": 0,
        "graded": 1,
        "failed": 0,
        "in_progress": 0,
        "review_count": 0,
        "absent": 0,
        "scan_issue": 0,
    }


def test_review_repository_rolls_back_multiple_results_together(
    tmp_path: Path,
) -> None:
    from backend.repositories import SQLiteConnectionFactory
    from backend.repositories.papers import PaperRepositoryGateway
    from backend.repositories.results import ResultRepositoryGateway
    from backend.repositories.review import ReviewRepositoryGateway

    database = _initialized_database(tmp_path)
    session_id, first_student_id = _seed_session_and_student(database)
    with sqlite3.connect(database) as connection:
        second_student_id = int(
            connection.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('002', 'Beta', 'Class-A')
                """
            ).lastrowid
        )
    sessions = SQLiteConnectionFactory(database)
    papers = PaperRepositoryGateway(sessions)
    results = ResultRepositoryGateway(sessions)
    review = ReviewRepositoryGateway(sessions)

    result_ids: list[int] = []
    for student_id, name in (
        (first_student_id, "Alpha"),
        (second_student_id, "Beta"),
    ):
        paper_id = papers.create_exam_paper(
            session_id,
            f"{name}-front.png",
            "",
            name,
            student_id,
            "matched",
            "pending",
        )
        result_id = results.publish_session_result_if_current_assignment(
            session_id,
            student_id,
            paper_id,
            _grading_result(),
        )
        assert isinstance(result_id, int)
        result_ids.append(result_id)
    details = [results.get_result_details(result_id)[0] for result_id in result_ids]
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"""
            CREATE TRIGGER fail_second_review
            BEFORE UPDATE ON session_details
            WHEN OLD.id = {int(details[1]["detail_id"])}
            BEGIN
                SELECT RAISE(ABORT, 'injected review failure');
            END
            """
        )

    adjustments = [
        {
            "session_id": session_id,
            "result_id": result_id,
            "question_id": "Q1",
            "detail_id": int(detail["detail_id"]),
            "score_awarded": score,
            "deduction_reason": "teacher review",
            "error_category": None,
            "error_summary": None,
        }
        for result_id, detail, score in zip(
            result_ids,
            details,
            (5.0, 6.0),
            strict=True,
        )
    ]
    with pytest.raises(sqlite3.IntegrityError, match="injected review failure"):
        review.apply_session_review_adjustments(session_id, adjustments)

    assert [
        results.get_result_details(result_id)[0]["score_awarded"]
        for result_id in result_ids
    ] == [7.0, 7.0]


def test_clear_session_run_data_rolls_back_every_grading_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.repositories.papers import PaperRepository

    database = _initialized_database(tmp_path)
    session_id, student_id = _seed_session_and_student(database)
    manager = DBManager(database)
    paper_id = manager.paper_repository.create_exam_paper(
        session_id,
        "front.png",
        "back.png",
        "Alpha",
        student_id,
        "matched",
        "pending",
    )
    result_id = manager.result_repository.publish_session_result_if_current_assignment(
        session_id,
        student_id,
        paper_id,
        _grading_result(),
    )
    assert isinstance(result_id, int)
    manager.review_repository.upsert_annotated_result(
        session_id,
        result_id,
        "annotated-front.png",
        "annotated-back.png",
    )
    manager.session_repository.replace_session_attendance(
        session_id,
        [
            {
                "student_id": student_id,
                "attendance_status": "present",
                "source_reason": None,
                "matched_paper_id": paper_id,
            }
        ],
    )
    monkeypatch.setattr(manager, "create_backup", lambda _reason: tmp_path)

    def fail_after_prior_deletes(
        _repository: PaperRepository,
        _session_id: int,
    ) -> int:
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(
        PaperRepository,
        "delete_session_papers",
        fail_after_prior_deletes,
    )

    with pytest.raises(RuntimeError, match="injected cleanup failure"):
        manager.clear_session_run_data(session_id)

    with sqlite3.connect(database) as connection:
        counts = [
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "exam_papers",
                "session_results",
                "session_details",
                "annotated_results",
                "session_attendance",
            )
        ]
    assert counts == [1, 1, 1, 1, 1]


def test_db_manager_and_primary_services_expose_grading_repositories(
    tmp_path: Path,
) -> None:
    from grading_service import GradingService
    from manual_review_service import ManualReviewService

    manager = DBManager(_initialized_database(tmp_path))

    assert manager.paper_repository is manager._paper_repository
    assert manager.result_repository is manager._result_repository
    assert manager.review_repository is manager._review_repository

    grading = GradingService(manager, object())  # type: ignore[arg-type]
    review = ManualReviewService(manager, tmp_path / "annotated")
    assert grading.papers is manager.paper_repository
    assert grading.results is manager.result_repository
    assert grading.attendance is manager.session_repository
    assert review.results is manager.result_repository
    assert review.review is manager.review_repository


def test_grading_service_does_not_bypass_repositories_with_raw_connection() -> None:
    from grading_service import GradingService

    source = inspect.getsource(GradingService)

    assert "self.db._connect" not in source


def test_primary_services_use_repository_handles_for_grading_data() -> None:
    from grading_service import GradingService
    from manual_review_service import ManualReviewService

    grading_source = inspect.getsource(GradingService)
    review_source = inspect.getsource(ManualReviewService)
    direct_methods = (
        "create_exam_paper",
        "update_exam_paper_status",
        "update_exam_paper_status_if_current_assignment",
        "publish_session_result_if_current_assignment",
        "replace_result_details_atomic",
        "record_result_retry_failure",
    )
    review_methods = (
        "get_result_context",
        "get_result_details",
        "upsert_annotated_result",
        "update_result_detail",
        "recalculate_result_score",
        "apply_session_review_adjustments",
        "get_session_results",
        "update_session_detail_scores",
    )
    assert all(
        f"self.db.{method}" not in grading_source
        for method in direct_methods
    )
    assert all(
        f"self.db.{method}" not in review_source
        for method in review_methods
    )


def test_db_manager_core_grading_facades_contain_no_sql() -> None:
    method_names = (
        "create_exam_paper",
        "update_exam_paper_status",
        "update_exam_paper_status_if_current_assignment",
        "save_session_result",
        "publish_session_result_if_current_assignment",
        "get_session_results",
        "get_result_details",
        "apply_session_review_adjustments",
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(DBManager, method_name)).upper()
        assert not any(
            keyword in source
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
        ), method_name


def test_db_manager_result_review_facades_contain_no_sql() -> None:
    method_names = (
        "get_session_review_rows",
        "get_review_media_context",
        "get_result_context",
        "replace_result_details_atomic",
        "record_result_retry_failure",
        "update_result_detail",
        "recalculate_result_score",
        "update_session_detail_scores",
        "upsert_annotated_result",
        "is_annotated_result_path_referenced",
        "get_annotated_result",
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(DBManager, method_name)).upper()
        assert not any(
            keyword in source
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
        ), method_name


def test_db_manager_progress_failure_facades_contain_no_sql() -> None:
    method_names = (
        "get_session_progress",
        "list_session_anomalies",
        "list_failed_papers",
        "list_failed_papers_detailed",
        "list_incomplete_results",
        "get_session_weak_points",
        "get_active_global_weak_points",
        "get_active_assessment_evidence",
        "get_active_global_error_points",
        "get_active_student_score_rates",
        "get_active_items_for_knowledge",
        "_query_active_detail_rows",
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(DBManager, method_name)).upper()
        assert not any(
            keyword in source
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")
        ), method_name
