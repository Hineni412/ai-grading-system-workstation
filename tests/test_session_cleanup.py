from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.api.routers.sessions import permanently_delete_session
from backend.api.schemas.sessions import PermanentDeleteSessionRequest
from backend.repositories.sessions import SessionDeletionRevisionConflict
from db_manager import DBManager
from question_bank.database.schema import (
    connect as connect_question_bank,
    initialize_database as initialize_question_bank,
)
from session_cleanup import (
    SessionPermanentDeletionRecoveryFailed,
    SessionStorageDeletionIncomplete,
    hard_delete_session_from_archive,
    hard_delete_session_from_recycle_bin,
    list_pending_session_permanent_deletions,
    preview_session_permanent_deletion,
    recover_interrupted_session_permanent_deletion,
)


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _session_table_counts(db: DBManager, session_id: int) -> dict[str, int]:
    tables = [
        "grading_sessions",
        "session_templates",
        "answer_regions",
        "exam_papers",
        "session_results",
        "session_details",
        "session_attendance",
        "annotated_results",
    ]
    with db._connect() as conn:
        counts: dict[str, int] = {}
        for table in tables:
            if table == "session_details":
                counts[table] = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS c
                        FROM session_details sd
                        JOIN session_results sr ON sr.id = sd.result_id
                        WHERE sr.session_id = ?
                        """,
                        (session_id,),
                    ).fetchone()["c"]
                )
            else:
                counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE session_id = ?", (session_id,)).fetchone()["c"]
                    if table != "grading_sessions"
                    else conn.execute("SELECT COUNT(*) AS c FROM grading_sessions WHERE id = ?", (session_id,)).fetchone()[
                        "c"
                    ]
                )
        return counts


def _make_deleted_session(db: DBManager, data_root: Path) -> tuple[int, list[Path]]:
    rubric = _write(data_root / "config" / "uploaded" / "rubric.json")
    answer_key = _write(data_root / "config" / "uploaded" / "answer.json")
    session_id = db.create_grading_session("old session", str(rubric), str(answer_key))

    template_dir = data_root / "templates" / f"session_{session_id}"
    exams_dir = data_root / "exams" / f"session_{session_id}"
    annotated_dir = data_root / "annotated" / f"session_{session_id}"
    front_template = _write(template_dir / "front.png")
    back_template = _write(template_dir / "back.png")
    analysis = _write(template_dir / "analysis.json")
    regions = _write(template_dir / "regions.json")
    region_draft = _write(template_dir / "region_draft.json")
    front_scan = _write(exams_dir / "front.jpg")
    back_scan = _write(exams_dir / "back.jpg")
    annotated_front = _write(annotated_dir / "front.jpg")
    annotated_back = _write(annotated_dir / "back.jpg")

    template_id = db.upsert_session_template(session_id, str(front_template), str(back_template))
    db.update_session_template_analysis(
        session_id,
        ai_analysis_path=str(analysis),
        template_config_path=str(template_dir / "config.json"),
        regions_path=str(regions),
    )
    db.save_answer_regions(
        session_id,
        template_id,
        [{"page": "front", "region_order": 1, "x": 1, "y": 2, "w": 3, "h": 4, "confidence": 1.0}],
    )

    with db._connect() as conn:
        student_id = conn.execute(
            "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
        ).lastrowid
        paper_id = conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id, match_status, processing_status
            ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
            """,
            (session_id, str(front_scan), str(back_scan), student_id),
        ).lastrowid
        result_id = conn.execute(
            """
            INSERT INTO session_results (
                session_id, student_id, paper_id, total_score, student_score, needs_human_review, raw_json
            ) VALUES (?, ?, ?, 100, 90, 0, '{}')
            """,
            (session_id, student_id, paper_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO session_details (result_id, question_id, score_awarded, deduction_reason, knowledge_ids)
            VALUES (?, '1', 90, '', '["k1"]')
            """,
            (result_id,),
        )
        conn.execute(
            """
            INSERT INTO annotated_results (session_id, result_id, annotated_front_path, annotated_back_path)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, result_id, str(annotated_front), str(annotated_back)),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (session_id, student_id, attendance_status, matched_paper_id)
            VALUES (?, ?, 'present', ?)
            """,
            (session_id, student_id, paper_id),
        )
        conn.commit()

    db.soft_delete_grading_session(session_id)
    return session_id, [
        rubric,
        answer_key,
        front_template,
        back_template,
        analysis,
        regions,
        region_draft,
        front_scan,
        back_scan,
        annotated_front,
        annotated_back,
    ]


def test_hard_delete_session_removes_database_rows_and_owned_files(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)

    result = hard_delete_session_from_recycle_bin(db, session_id, data_root=data_root)

    assert result["db_counts"]["grading_sessions"] == 1
    assert all(count == 0 for count in _session_table_counts(db, session_id).values())
    assert result["deleted_files"] >= len(owned_files)
    assert not (data_root / "templates" / f"session_{session_id}").exists()
    assert not (data_root / "templates" / f"session_{session_id}" / "region_draft.json").exists()
    assert not (data_root / "exams" / f"session_{session_id}").exists()
    assert not (data_root / "annotated" / f"session_{session_id}").exists()
    for path in owned_files:
        assert not path.exists()


def test_hard_delete_rejects_active_session(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id = db.create_grading_session("active", str(data_root / "r.json"), str(data_root / "a.json"))

    with pytest.raises(ValueError, match="archived"):
        hard_delete_session_from_recycle_bin(db, session_id, data_root=data_root)


def _seed_question_bank_link(question_bank_db: Path, session_id: int) -> None:
    initialize_question_bank(question_bank_db)
    with connect_question_bank(question_bank_db) as connection:
        connection.execute(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (501, '1', 'test question')
            """
        )
        connection.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, confidence, status
            ) VALUES (?, 'Q1', 501, 'manual', 1.0, 'confirmed')
            """,
            (str(int(session_id)),),
        )


def _question_bank_link_count(question_bank_db: Path, session_id: int) -> int:
    with connect_question_bank(question_bank_db) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM grading_question_links
                WHERE grading_session_id = ?
                """,
                (str(int(session_id)),),
            ).fetchone()[0]
        )


def test_permanent_delete_preview_returns_owned_storage_counts(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)

    counts = preview_session_permanent_deletion(
        db,
        session_id,
        data_root=data_root,
    )

    assert counts["owned_files"] >= len(owned_files)
    assert counts["owned_directories"] == 3
    assert counts["protected_shared_paths"] == 0


def test_grading_delete_failure_rolls_back_question_bank_and_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)
    question_bank_db = data_root / "databases" / "question_bank.db"
    _seed_question_bank_link(question_bank_db, session_id)

    def reject_delete(*_args, **_kwargs):
        raise SessionDeletionRevisionConflict("injected revision conflict")

    monkeypatch.setattr(db, "hard_delete_grading_session", reject_delete)

    with pytest.raises(SessionDeletionRevisionConflict):
        hard_delete_session_from_archive(
            db,
            session_id,
            data_root=data_root,
            question_bank_db_path=question_bank_db,
        )

    assert db.get_grading_session(session_id) is not None
    assert _question_bank_link_count(question_bank_db, session_id) == 1
    assert all(path.exists() for path in owned_files)
    assert not (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).exists()


def test_preview_recovers_storage_left_by_interrupted_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)
    question_bank_db = data_root / "databases" / "question_bank.db"
    _seed_question_bank_link(question_bank_db, session_id)

    def interrupt_delete(*_args, **_kwargs):
        raise SystemExit("injected process exit")

    monkeypatch.setattr(db, "hard_delete_grading_session", interrupt_delete)

    with pytest.raises(SystemExit, match="injected process exit"):
        hard_delete_session_from_archive(
            db,
            session_id,
            data_root=data_root,
            question_bank_db_path=question_bank_db,
        )

    assert not any(path.exists() for path in owned_files)
    assert (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).is_dir()

    counts = preview_session_permanent_deletion(
        db,
        session_id,
        data_root=data_root,
    )

    assert counts["owned_files"] >= len(owned_files)
    assert all(path.exists() for path in owned_files)
    assert _question_bank_link_count(question_bank_db, session_id) == 1
    assert not (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).exists()


def test_success_with_pending_storage_cleanup_is_idempotently_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, _owned_files = _make_deleted_session(db, data_root)
    question_bank_db = data_root / "databases" / "question_bank.db"
    _seed_question_bank_link(question_bank_db, session_id)

    monkeypatch.setattr(
        "session_cleanup._purge_staged_storage",
        lambda *_args, **_kwargs: False,
    )

    result = hard_delete_session_from_archive(
        db,
        session_id,
        data_root=data_root,
        question_bank_db_path=question_bank_db,
    )

    assert result["db_counts"]["grading_sessions"] == 1
    assert result["storage_cleanup_pending"] is True
    assert result["recovered_interrupted_delete"] is False
    assert db.get_grading_session(session_id) is None
    assert _question_bank_link_count(question_bank_db, session_id) == 0
    assert (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).is_dir()
    assert list_pending_session_permanent_deletions(db, data_root=data_root) == [
        {
            "session_id": session_id,
            "deleted_files": result["deleted_files"],
            "deleted_dirs": result["deleted_dirs"],
            "skipped_shared": result["skipped_shared"],
        }
    ]

    monkeypatch.undo()
    recovered = recover_interrupted_session_permanent_deletion(
        db,
        session_id,
        data_root=data_root,
        question_bank_db_path=question_bank_db,
    )

    assert recovered is not None
    assert recovered["recovered_interrupted_delete"] is True
    assert recovered["storage_cleanup_pending"] is False
    assert not (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).exists()
    assert list_pending_session_permanent_deletions(db, data_root=data_root) == []


def test_permanent_delete_endpoint_recovers_before_session_existence_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, _owned_files = _make_deleted_session(db, data_root)
    question_bank_db = data_root / "databases" / "question_bank.db"
    _seed_question_bank_link(question_bank_db, session_id)
    monkeypatch.setattr(
        "session_cleanup._purge_staged_storage",
        lambda *_args, **_kwargs: False,
    )
    result = hard_delete_session_from_archive(
        db,
        session_id,
        data_root=data_root,
        question_bank_db_path=question_bank_db,
    )
    assert result["storage_cleanup_pending"] is True
    monkeypatch.undo()

    response = permanently_delete_session(
        session_id,
        PermanentDeleteSessionRequest(
            expected_revision="0" * 64,
            confirmation_phrase="unused for interrupted cleanup",
        ),
        sessions=db.session_repository,
        db=db,
        data_root=data_root,
        question_bank_db_path=question_bank_db,
    )

    assert response.session_id == session_id
    assert response.recovered_interrupted_delete is True
    assert response.storage_cleanup_pending is False


def test_locked_storage_path_restores_every_staged_path_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, owned_files = _make_deleted_session(db, data_root)
    real_replace = os.replace
    staged_moves = 0

    def fail_second_staged_move(source, destination):
        nonlocal staged_moves
        if Path(destination).parent.name == "items":
            staged_moves += 1
            if staged_moves == 2:
                raise PermissionError("injected file lock")
        return real_replace(source, destination)

    monkeypatch.setattr("session_cleanup.os.replace", fail_second_staged_move)

    with pytest.raises(SessionStorageDeletionIncomplete):
        hard_delete_session_from_archive(
            db,
            session_id,
            data_root=data_root,
        )

    assert db.get_grading_session(session_id) is not None
    assert all(path.exists() for path in owned_files)
    assert not (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    ).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "not-an-integer"),
        ("session_id", "not-an-integer"),
        ("deleted_files", "not-an-integer"),
    ],
)
def test_corrupt_staging_manifest_uses_recovery_error(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    data_root = tmp_path / "user_data"
    db = DBManager(data_root / "databases" / "grading_system.db")
    db.db_path.parent.mkdir(parents=True)
    db.initialize()
    session_id, _owned_files = _make_deleted_session(db, data_root)
    staging_dir = (
        data_root / ".session-delete-staging" / f"session_{session_id}"
    )
    staging_dir.mkdir(parents=True)
    manifest = {
        "version": 1,
        "session_id": session_id,
        "entries": [],
        "storage_counts": {
            "deleted_files": 0,
            "deleted_dirs": 0,
            "skipped_shared": 0,
        },
    }
    if field in {"version", "session_id"}:
        manifest[field] = value
    else:
        manifest["storage_counts"][field] = value
    (staging_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(SessionPermanentDeletionRecoveryFailed):
        recover_interrupted_session_permanent_deletion(
            db,
            session_id,
            data_root=data_root,
            question_bank_db_path=None,
        )
