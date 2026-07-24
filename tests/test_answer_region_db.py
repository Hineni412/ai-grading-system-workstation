from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.schema_migrations import SchemaVersionError
from db_manager import DBManager


def _make_session(db: DBManager) -> tuple[int, int]:
    session_id = db.create_grading_session("regions", "rubric.json", "answer.json")
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    return session_id, template_id


def _region(region_uuid: str, order: int, question_id: str) -> dict[str, object]:
    return {
        "region_uuid": region_uuid,
        "page": "front",
        "region_order": order,
        "x": order * 10,
        "y": order * 20,
        "w": 100,
        "h": 50,
        "detected_question_id": question_id,
        "mapped_question_id": question_id,
        "confidence": 0.9,
        "is_confirmed": True,
        "mapping_status": "manual",
        "multi_region_confirmed": order == 2,
    }


def test_initialize_rejects_incomplete_unregistered_answer_region_schema(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE session_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL UNIQUE,
                front_template_path TEXT NOT NULL,
                back_template_path TEXT NOT NULL,
                ai_analysis_path TEXT,
                template_config_path TEXT,
                regions_path TEXT,
                is_confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE answer_regions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                template_id INTEGER NOT NULL,
                page TEXT NOT NULL,
                region_order INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                w INTEGER NOT NULL,
                h INTEGER NOT NULL,
                detected_question_id TEXT,
                mapped_question_id TEXT,
                confidence REAL NOT NULL DEFAULT 0,
                is_confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            INSERT INTO session_templates (
                id, session_id, front_template_path, back_template_path
            ) VALUES (7, 9, 'front.png', 'back.png');
            INSERT INTO answer_regions (
                session_id, template_id, page, region_order, x, y, w, h, mapped_question_id
            ) VALUES
                (9, 7, 'front', 1, 1, 2, 30, 40, NULL),
                (9, 7, 'back', 2, 5, 6, 70, 80, 'Q2');
            """
        )

    db = DBManager(db_path)
    with pytest.raises(
        SchemaVersionError,
        match="migration 000_baseline_schema failed after backup",
    ):
        db.initialize()

    with sqlite3.connect(db_path) as conn:
        answer_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(answer_regions)")
        }
        assert "region_uuid" not in answer_columns
        assert conn.execute("SELECT COUNT(*) FROM answer_regions").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()[0]
            == 0
        )
    assert len(list((tmp_path / "backups").glob("legacy_before_migration_*.db"))) == 1


def test_initialize_backfills_stable_unique_region_uuid_and_mapping_defaults(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    db.add_answer_region(session_id, template_id, {"page": "front", "region_order": 1})

    with db._connect() as conn:
        conn.execute("DROP TRIGGER answer_regions_region_uuid_required_insert")
        conn.execute(
            """
            INSERT INTO answer_regions (
                region_uuid, session_id, template_id, page, region_order, x, y, w, h,
                mapped_question_id
            ) VALUES ('', ?, ?, 'back', 2, 1, 2, 3, 4, 'Q2')
            """,
            (session_id, template_id),
        )
        conn.execute("DROP TABLE schema_migrations")
        conn.commit()

    db.initialize()
    rows = db.list_answer_regions(session_id)
    first_uuids = [str(row["region_uuid"]) for row in rows]

    assert all(value.strip() for value in first_uuids)
    assert len(first_uuids) == len(set(first_uuids))
    assert [row["mapping_status"] for row in rows] == ["unbound", "manual"]
    assert [row["multi_region_confirmed"] for row in rows] == [0, 0]
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0

    db.initialize()
    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == first_uuids


def test_replace_answer_regions_atomic_persists_metadata_and_marks_snapshot_pending(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    regions = [_region("region-one", 1, "Q1"), _region("region-two", 2, "Q2")]
    regions[0]["mapping_status"] = "auto"

    snapshot_token = db.replace_answer_regions_atomic(
        session_id, template_id, regions, confirmed=True
    )

    saved = db.list_answer_regions(session_id)
    template = db.get_session_template(session_id)
    assert [row["region_uuid"] for row in saved] == ["region-one", "region-two"]
    assert [row["mapping_status"] for row in saved] == ["auto", "manual"]
    assert [row["multi_region_confirmed"] for row in saved] == [0, 1]
    assert db.is_template_ready(session_id) is True
    assert template["is_confirmed"] == 1
    assert template["regions_snapshot_pending"] == 1
    assert template["regions_snapshot_token"] == snapshot_token
    assert isinstance(snapshot_token, str) and snapshot_token

    assert db.mark_region_snapshot_complete(session_id, expected_token=snapshot_token) is True
    completed_template = db.get_session_template(session_id)
    assert completed_template["regions_snapshot_pending"] == 0
    assert completed_template["regions_snapshot_token"] is None


def test_confirmed_region_replacement_promotes_bound_rows_to_ready(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    regions = [_region("region-one", 1, "Q1"), _region("region-two", 2, "Q2")]
    for region in regions:
        region["is_confirmed"] = False

    db.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    saved = db.list_answer_regions(session_id)
    assert [row["is_confirmed"] for row in saved] == [1, 1]
    assert db.is_template_ready(session_id) is True


def test_replacement_tokens_are_unique_and_stale_completion_cannot_clear_newer_pending(
    tmp_path: Path,
) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)

    first_token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("first", 1, "Q1")],
        confirmed=True,
    )
    second_token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("second", 1, "Q2")],
        confirmed=True,
    )

    assert first_token != second_token
    assert db.mark_region_snapshot_complete(session_id, expected_token=first_token) is False
    pending_template = db.get_session_template(session_id)
    assert pending_template["regions_snapshot_pending"] == 1
    assert pending_template["regions_snapshot_token"] == second_token

    assert db.mark_region_snapshot_complete(session_id, expected_token=second_token) is True
    completed_template = db.get_session_template(session_id)
    assert completed_template["regions_snapshot_pending"] == 0
    assert completed_template["regions_snapshot_token"] is None


def test_template_reupload_clears_stale_pending_snapshot_generation(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("formal", 1, "Q1")],
        confirmed=True,
    )

    db.upsert_session_template(session_id, "replacement-front.png", "replacement-back.png")

    template = db.get_session_template(session_id)
    assert template["front_template_path"] == "replacement-front.png"
    assert template["back_template_path"] == "replacement-back.png"
    assert template["is_confirmed"] == 0
    assert template["regions_snapshot_pending"] == 0
    assert template["regions_snapshot_token"] is None


def test_template_mapping_refresh_clears_stale_pending_snapshot_generation(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("formal", 1, "Q1")],
        confirmed=True,
    )

    db.update_session_template_analysis(
        session_id,
        ai_analysis_path="replacement-analysis.json",
        template_config_path="replacement-config.json",
        regions_path="replacement-regions.json",
    )

    template = db.get_session_template(session_id)
    assert template["ai_analysis_path"] == "replacement-analysis.json"
    assert template["template_config_path"] == "replacement-config.json"
    assert template["regions_path"] == "replacement-regions.json"
    assert template["is_confirmed"] == 0
    assert template["regions_snapshot_pending"] == 0
    assert template["regions_snapshot_token"] is None


def test_snapshot_completion_requires_nonblank_matching_token(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("formal", 1, "Q1")],
        confirmed=True,
    )

    with pytest.raises(TypeError):
        db.mark_region_snapshot_complete(session_id)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        db.mark_region_snapshot_complete(session_id, expected_token="")
    with pytest.raises(ValueError):
        db.mark_region_snapshot_complete(session_id, expected_token=" ")

    pending = db.get_session_template(session_id)
    assert pending["regions_snapshot_pending"] == 1
    assert pending["regions_snapshot_token"] == token


def test_initialize_assigns_token_to_existing_pending_snapshot(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, _template_id = _make_session(db)
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE session_templates
            SET regions_snapshot_pending = 1,
                regions_snapshot_token = NULL
            WHERE session_id = ?
            """,
            (session_id,),
        )
        conn.commit()

    db.initialize()

    template = db.get_session_template(session_id)
    assert template["regions_snapshot_pending"] == 1
    assert isinstance(template["regions_snapshot_token"], str)
    assert template["regions_snapshot_token"]


def test_duplicate_uuid_replacement_rolls_back_and_preserves_formal_regions(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("formal-one", 1, "Q1"), _region("formal-two", 2, "Q2")],
        confirmed=True,
    )
    db.mark_region_snapshot_complete(session_id, expected_token=token)
    regions_before = db.list_answer_regions(session_id)
    template_before = db.get_session_template(session_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.replace_answer_regions_atomic(
            session_id,
            template_id,
            [_region("duplicate", 3, "Q3"), _region("duplicate", 4, "Q4")],
            confirmed=False,
        )

    assert db.list_answer_regions(session_id) == regions_before
    assert db.get_session_template(session_id) == template_before


def test_cross_session_template_replacement_preserves_formal_regions(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_a_id, template_a_id = _make_session(db)
    _session_b_id, template_b_id = _make_session(db)
    token = db.replace_answer_regions_atomic(
        session_a_id,
        template_a_id,
        [_region("formal-one", 1, "Q1"), _region("formal-two", 2, "Q2")],
        confirmed=True,
    )
    db.mark_region_snapshot_complete(session_a_id, expected_token=token)
    regions_before = db.list_answer_regions(session_a_id)
    template_before = db.get_session_template(session_a_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.replace_answer_regions_atomic(
            session_a_id,
            template_b_id,
            [_region("replacement", 3, "Q3")],
            confirmed=False,
        )

    assert db.list_answer_regions(session_a_id) == regions_before
    assert db.get_session_template(session_a_id) == template_before


def test_bulk_update_answer_region_mapping_keeps_mapping_status_in_sync(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [
            _region("region-one", 1, "Q1"),
            _region("region-two", 2, "Q2"),
            _region("region-three", 3, "Q3"),
        ],
        confirmed=True,
    )
    rows = db.list_answer_regions(session_id)

    db.bulk_update_answer_region_mapping(
        session_id,
        [
            {"id": rows[0]["id"], "mapped_question_id": "Q4", "is_confirmed": True},
            {"id": rows[1]["id"], "mapped_question_id": "  ", "is_confirmed": False},
            {
                "id": rows[2]["id"],
                "mapped_question_id": "Q5",
                "mapping_status": "auto",
                "is_confirmed": True,
            },
        ],
    )

    saved = db.list_answer_regions(session_id)
    assert [row["mapped_question_id"] for row in saved] == ["Q4", "  ", "Q5"]
    assert [row["mapping_status"] for row in saved] == ["manual", "unbound", "auto"]
