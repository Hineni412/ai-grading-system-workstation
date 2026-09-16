from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import backend.review.service as review_service_module
from backend.review.service import ReviewApplicationService
from db_manager import DBManager


def _seed_large_review_class(
    tmp_path: Path,
) -> tuple[DBManager, int, dict[str, Any], list[int]]:
    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q10", "max_score": 4},
                    {"question_id": "Q2", "max_score": 5},
                    {"question_id": "Q1", "max_score": 10},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.create_grading_session("Large review class", str(rubric_path), "answer.json")
    result_ids: list[int] = []

    with sqlite3.connect(db.db_path) as conn:
        for index in range(60):
            student_code = " nan " if index == 5 else f"S{index:03d}"
            student_name = "" if index == 5 else f"Student {index:03d}"
            class_name = " <NA> " if index == 5 else "Class 1"
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, ?)",
                    (student_code, student_name, class_name),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"front-{index}.jpg",
                        f"back-{index}.jpg",
                        f"OCR Student {index:03d}",
                        student_id,
                    ),
                ).lastrowid
            )
            metadata = (
                {
                    "candidate_scores": [
                        {"score": 8, "confidence": 0.55, "reason": "unclear handwriting"}
                    ],
                    "source": "large-class-test",
                }
                if index == 0
                else {}
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, 10, 1, ?)
                    """,
                    (
                        session_id,
                        student_id,
                        paper_id,
                        json.dumps(
                            {"detail_metadata": {"Q1": metadata}},
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
            )
            result_ids.append(result_id)

            deduction_reason = "ordinary deduction"
            error_category = "ordinary"
            error_summary = "summary"
            confidence = 95.0
            if index == 0:
                deduction_reason = "需复核：字迹不清"
            elif index == 1:
                confidence = 79.9
            elif index == 2:
                confidence = 80.0
            elif index == 3:
                deduction_reason = "需复核：已由教师确认"
                error_category = "已复核"
                confidence = 10.0
            elif index == 4:
                deduction_reason = "需复核：已由教师确认"
                error_category = "人工复核"
                confidence = 10.0
            elif index == 5:
                deduction_reason = " none "
                error_category = " "
                error_summary = "nan"

            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                    error_category, error_summary, confidence_score
                ) VALUES (?, 'Q1', 8, ?, '["K1"]', ?, ?, ?)
                """,
                (result_id, deduction_reason, error_category, error_summary, confidence),
            )
            second_question_id = "Q2" if index % 2 == 0 else "Q10"
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                    error_category, error_summary, confidence_score
                ) VALUES (?, ?, 3, 'calculation error', '["K2"]', 'ordinary', 'summary', 95)
                """,
                (result_id, second_question_id),
            )
        conn.commit()

    session = db.get_grading_session(session_id)
    assert session is not None
    return db, session_id, session, result_ids


def _seed_review_confirmation(
    tmp_path: Path,
) -> tuple[DBManager, int, dict[str, Any], dict[str, int]]:
    db = DBManager(tmp_path / "databases" / "review-confirmation.db")
    db.initialize()
    rubric_path = tmp_path / "review-confirmation-rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.create_grading_session("Review confirmation", str(rubric_path), "answer.json")
    foreign_session_id = db.create_grading_session(
        "Foreign review confirmation",
        str(rubric_path),
        "answer.json",
    )

    ids: dict[str, int] = {"foreign_session_id": foreign_session_id}
    with sqlite3.connect(db.db_path) as conn:
        for label, owner_session_id, score in (
            ("first", session_id, 8.0),
            ("second", session_id, 7.0),
            ("foreign", foreign_session_id, 6.0),
        ):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name) VALUES (?, ?)",
                    (f"S-{label}", f"Student {label}"),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        owner_session_id,
                        f"{label}-front.jpg",
                        f"{label}-back.jpg",
                        f"Student {label}",
                        student_id,
                    ),
                ).lastrowid
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, ?, 1, '{}')
                    """,
                    (owner_session_id, student_id, paper_id, score),
                ).lastrowid
            )
            detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason,
                        knowledge_ids, error_category, error_summary
                    ) VALUES (?, 'Q1', ?, 'original reason', '["K1"]', 'original category', 'original summary')
                    """,
                    (result_id, score),
                ).lastrowid
            )
            ids[f"{label}_result_id"] = result_id
            ids[f"{label}_detail_id"] = detail_id
        conn.commit()

    session = db.get_grading_session(session_id)
    assert session is not None
    return db, session_id, session, ids


def _confirmation_state(db: DBManager) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    with sqlite3.connect(db.db_path) as conn:
        details = conn.execute(
            """
            SELECT id, score_awarded, deduction_reason, error_category, error_summary
            FROM session_details
            ORDER BY id
            """
        ).fetchall()
        results = conn.execute(
            "SELECT id, student_score FROM session_results ORDER BY id"
        ).fetchall()
    return details, results


def test_large_review_class_uses_one_join_query(tmp_path: Path, monkeypatch) -> None:
    db, session_id, session, result_ids = _seed_large_review_class(tmp_path)
    service = ReviewApplicationService(db)
    original_query = db.review_repository.get_session_review_rows
    query_count = 0

    def counted_query(requested_session_id: int):
        nonlocal query_count
        query_count += 1
        return original_query(requested_session_id)

    def fail_old_path(*_args, **_kwargs):
        raise AssertionError("review service must not use the result-by-result query path")

    monkeypatch.setattr(
        db.review_repository,
        "get_session_review_rows",
        counted_query,
    )
    monkeypatch.setattr(db, "get_session_results", fail_old_path)
    monkeypatch.setattr(db, "get_result_details", fail_old_path)

    rows = service.list_items(session_id, session)

    assert len(rows) == 120
    assert len([row for row in rows if row.question_id == "Q1"]) == 60
    assert query_count == 1

    q1_by_result = {row.result_id: row for row in rows if row.question_id == "Q1"}
    assert q1_by_result[result_ids[0]].needs_review is True
    assert q1_by_result[result_ids[1]].needs_review is True
    assert q1_by_result[result_ids[1]].confidence_score == 79.9
    assert q1_by_result[result_ids[2]].needs_review is False
    assert q1_by_result[result_ids[2]].confidence_score == 80.0
    assert q1_by_result[result_ids[3]].needs_review is False
    assert q1_by_result[result_ids[4]].needs_review is False
    assert q1_by_result[result_ids[0]].candidate_scores == [
        {"score": 8, "confidence": 0.55, "reason": "unclear handwriting"}
    ]
    assert q1_by_result[result_ids[0]].metadata == {
        "candidate_scores": [
            {"score": 8, "confidence": 0.55, "reason": "unclear handwriting"}
        ],
    }

    cleaned = q1_by_result[result_ids[5]]
    assert cleaned.student_code is None
    assert cleaned.student_name == "OCR Student 005"
    assert cleaned.class_name is None
    assert cleaned.deduction_reason is None
    assert cleaned.error_category is None
    assert cleaned.error_summary is None


def test_explicit_review_survives_database_read_at_high_confidence(tmp_path: Path) -> None:
    db, session_id, session, result_ids = _seed_large_review_class(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (
            json.dumps({"detail_metadata": {"Q1": {"needs_human_review": True}}}), result_ids[2]))
        conn.execute("UPDATE session_details SET confidence_score=95, error_category='其他', deduction_reason='存在两种评分解释' WHERE result_id=? AND question_id='Q1'", (result_ids[2],))
    items = ReviewApplicationService(db).list_items(session_id, session)
    item = next(row for row in items if row.result_id == result_ids[2] and row.question_id == "Q1")
    assert item.confidence_score == 95
    assert item.needs_review
    assert item.score_status == "ai_review"


def test_review_metadata_exposes_only_sanitized_historical_allowlist(
    tmp_path: Path,
) -> None:
    db, session_id, session, result_ids = _seed_large_review_class(tmp_path)
    private_path = str(tmp_path / "private" / "evidence.png")
    rooted_windows_path = r"\Users\teacher\private\evidence.png"
    embedded_windows_path = r"Cannot inspect C:\private\evidence.png"
    embedded_rooted_path = r"Cannot inspect \Users\teacher\private\evidence.png"
    embedded_unc_path = r"Cannot inspect \\server\share\evidence.png"
    embedded_posix_path = "Cannot inspect /var/tmp/private-evidence.png"
    historical_metadata = {
        "question_id": "Q1",
        "evidence_steps": [
            "student step",
            private_path,
            rooted_windows_path,
            embedded_windows_path,
            embedded_rooted_path,
            embedded_unc_path,
            embedded_posix_path,
        ],
        "missing_steps": ["final conclusion"],
        "candidate_scores": [
            {
                "score": 8,
                "confidence": 0.55,
                "reason": "unclear handwriting",
                "image_path": private_path,
                "configApiKey": "candidate-secret",
            },
            {
                "score": 7,
                "confidence": 0.4,
                "reason": rooted_windows_path,
            },
            {
                "score": 6,
                "confidence": 0.3,
                "reason": embedded_windows_path,
            },
        ],
        "source": "legacy-import",
        "image_path": private_path,
        "configApiKey": "metadata-secret",
        "nested": {"artifact": private_path},
    }
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json = ? WHERE id = ?",
            (
                json.dumps(
                    {"detail_metadata": {"Q1": historical_metadata}},
                    ensure_ascii=False,
                ),
                result_ids[0],
            ),
        )
        conn.commit()

    item = next(
        row
        for row in ReviewApplicationService(db).list_items(
            session_id,
            session,
            requested_question_id="Q1",
        )
        if row.result_id == result_ids[0]
    )

    assert item.metadata == {
        "question_id": "Q1",
        "evidence_steps": ["student step"],
        "missing_steps": ["final conclusion"],
        "candidate_scores": [
            {
                "score": 8,
                "confidence": 0.55,
                "reason": "unclear handwriting",
            },
            {"score": 7, "confidence": 0.4},
            {"score": 6, "confidence": 0.3},
        ],
    }
    assert item.candidate_scores == item.metadata["candidate_scores"]
    assert private_path not in str(item.metadata)
    assert rooted_windows_path not in str(item.metadata)
    assert embedded_windows_path not in str(item.metadata)
    assert embedded_rooted_path not in str(item.metadata)
    assert embedded_unc_path not in str(item.metadata)
    assert embedded_posix_path not in str(item.metadata)
    assert "candidate-secret" not in str(item.metadata)
    assert "metadata-secret" not in str(item.metadata)


def test_review_questions_use_one_batch_read_and_preserve_order_and_max_score(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db, session_id, session, _result_ids = _seed_large_review_class(tmp_path)
    service = ReviewApplicationService(db)
    original_batch_read = db.get_session_review_rows
    batch_read_count = 0

    def counted_batch_read(requested_session_id: int):
        nonlocal batch_read_count
        batch_read_count += 1
        return original_batch_read(requested_session_id)

    def fail_old_path(*_args, **_kwargs):
        raise AssertionError("review service must not use the result-by-result query path")

    monkeypatch.setattr(db, "get_session_review_rows", counted_batch_read)
    monkeypatch.setattr(db, "get_session_results", fail_old_path)
    monkeypatch.setattr(db, "get_result_details", fail_old_path)

    questions = service.list_questions(session_id, session)

    assert batch_read_count == 1
    assert [question.question_id for question in questions] == ["Q1", "Q2", "Q10"]
    assert [(question.total_count, question.max_score) for question in questions] == [
        (60, 10.0),
        (30, 5.0),
        (30, 4.0),
    ]
    assert questions[0].needs_review_count == 2


def test_incomplete_ai_items_surface_as_failed_in_review_questions(
    tmp_path: Path,
) -> None:
    db, session_id, session, _ids = _seed_review_confirmation(tmp_path)
    service = ReviewApplicationService(db)
    with sqlite3.connect(db.db_path) as conn:
        student_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM students WHERE student_code IN ('S-first', 'S-second') ORDER BY id"
            )
        ]
    manual_context = {
        "scan_batch_id": "batch-1",
        "papers": [
            {
                "student_id": student_id,
                "target_type": "",
                "target_id": "",
                "front_media_url": "",
                "back_media_url": None,
            }
            for student_id in student_ids
        ],
    }

    questions = service.list_questions(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    by_question = {question.question_id: question for question in questions}
    # 种子数据里每份答卷只有 Q1 的 AI 明细，Q2 缺失（incomplete），
    # 必须在复核聚合中体现为 failed 而不是 ungraded。
    assert by_question["Q2"].failed_count == len(student_ids)
    assert by_question["Q2"].ungraded_count == 0
    assert by_question["Q1"].failed_count == 0

    items = service.list_items(
        session_id,
        session,
        requested_question_id="Q2",
        scope="all",
        manual_context=manual_context,
    )
    assert len(items) == len(student_ids)
    assert all(item.score_status == "failed" for item in items)
    assert all(item.error_category == "AI 结果未通过校验" for item in items)
    assert all(item.needs_review for item in items)


@pytest.mark.parametrize(
    ("case", "path_question_id"),
    [
        ("foreign_session", "Q1"),
        ("wrong_result", "Q1"),
        ("wrong_question", "Q2"),
    ],
)
def test_prepare_adjustments_rejects_invalid_detail_ownership_without_writes(
    tmp_path: Path,
    case: str,
    path_question_id: str,
) -> None:
    db, session_id, session, ids = _seed_review_confirmation(tmp_path)
    result_id = ids["first_result_id"]
    detail_id = ids["first_detail_id"]
    if case == "foreign_session":
        result_id = ids["foreign_result_id"]
        detail_id = ids["foreign_detail_id"]
    elif case == "wrong_result":
        result_id = ids["second_result_id"]
    item = review_service_module.ReviewConfirmationInput(
        result_id=result_id,
        detail_id=detail_id,
        score_awarded=9.0,
    )
    before = _confirmation_state(db)

    with pytest.raises(review_service_module.ReviewDetailNotFoundError):
        adjustments = ReviewApplicationService(db).prepare_adjustments(
            session_id,
            session,
            path_question_id,
            [item],
        )
        db.apply_session_review_adjustments(session_id, adjustments)

    assert _confirmation_state(db) == before


@pytest.mark.parametrize(
    "invalid_score",
    [float("nan"), float("inf"), -0.01, 10.01],
    ids=["nan", "infinity", "negative", "above-rubric-maximum"],
)
def test_prepare_adjustments_rejects_invalid_scores_without_writes(
    tmp_path: Path,
    invalid_score: float,
) -> None:
    db, session_id, session, ids = _seed_review_confirmation(tmp_path)
    item = review_service_module.ReviewConfirmationInput(
        result_id=ids["first_result_id"],
        detail_id=ids["first_detail_id"],
        score_awarded=invalid_score,
    )
    before = _confirmation_state(db)

    with pytest.raises(review_service_module.ReviewValidationError):
        adjustments = ReviewApplicationService(db).prepare_adjustments(
            session_id,
            session,
            "Q1",
            [item],
        )
        db.apply_session_review_adjustments(session_id, adjustments)

    assert _confirmation_state(db) == before


def test_prepare_adjustments_rejects_duplicate_detail_without_writes(tmp_path: Path) -> None:
    db, session_id, session, ids = _seed_review_confirmation(tmp_path)
    item = review_service_module.ReviewConfirmationInput(
        result_id=ids["first_result_id"],
        detail_id=ids["first_detail_id"],
        score_awarded=9.0,
    )
    before = _confirmation_state(db)

    with pytest.raises(review_service_module.ReviewValidationError, match="duplicate"):
        adjustments = ReviewApplicationService(db).prepare_adjustments(
            session_id,
            session,
            "Q1",
            [item, item],
        )
        db.apply_session_review_adjustments(session_id, adjustments)

    assert _confirmation_state(db) == before


def test_prepare_adjustments_returns_normalized_ownership_and_review_metadata(
    tmp_path: Path,
) -> None:
    db, session_id, session, ids = _seed_review_confirmation(tmp_path)

    adjustments = ReviewApplicationService(db).prepare_adjustments(
        session_id,
        session,
        "Q1",
        [
            review_service_module.ReviewConfirmationInput(
                result_id=ids["first_result_id"],
                detail_id=ids["first_detail_id"],
                score_awarded=9,
            )
        ],
    )

    assert adjustments == [
        {
            "session_id": session_id,
            "result_id": ids["first_result_id"],
            "detail_id": ids["first_detail_id"],
            "question_id": "Q1",
            "score_awarded": 9.0,
            "deduction_reason": "人工复核已确认",
            "error_category": "已复核",
            "error_summary": "manual_review_confirmed",
        }
    ]


def test_source_region_id_normalizes_part_aliases_without_crossing_siblings() -> None:
    assert review_service_module._source_region_id(
        [{"id": 11, "mapped_question_id": "Q1(1)"}],
        "Q1(P1)",
    ) == 11
    assert review_service_module._source_region_id(
        [{"id": 12, "mapped_question_id": "Q1(P1)"}],
        "Q1(1)",
    ) == 12
    assert review_service_module._source_region_id(
        [{"id": 13, "mapped_question_id": "Q1(P2)"}],
        "Q1(P1)",
    ) == 0


def test_source_region_id_uses_parent_only_for_one_unambiguous_child() -> None:
    assert review_service_module._source_region_id(
        [{"id": 21, "mapped_question_id": "Q1(P1)"}],
        "Q1",
    ) == 21
    assert review_service_module._source_region_id(
        [
            {"id": 21, "mapped_question_id": "Q1(P1)"},
            {"id": 22, "mapped_question_id": "Q1(P2)"},
        ],
        "Q1",
    ) == 0


def test_scoring_item_types_come_from_rubric_and_parts_inherit_parent_type(
    tmp_path: Path,
) -> None:
    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 5,
                    },
                    {
                        "question_id": "Q2",
                        "question_type": "fill_blank",
                        "max_score": 6,
                        "parts": [
                            {"part_id": "Q2(P1)", "part_score": 2},
                            {"part_id": "Q2(P2)", "part_score": 4},
                        ],
                    },
                    {
                        "question_id": "Q3",
                        "max_score": 8,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.create_grading_session(
        "Question type contract",
        str(rubric_path),
        "answer.json",
    )
    session = db.get_grading_session(session_id)
    assert session is not None

    score_map, _catalog, _resolved_ids, question_types = (
        review_service_module._load_scoring_item_map(session, db)
    )

    assert score_map == {
        "Q1": 5,
        "Q2(P1)": 2,
        "Q2(P2)": 4,
        "Q3": 8,
    }
    assert question_types == {
        "Q1": "choice",
        "Q2(P1)": "fill_blank",
        "Q2(P2)": "fill_blank",
        "Q3": None,
    }
