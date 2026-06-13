from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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


def test_initialize_upgrades_true_legacy_answer_region_schema(tmp_path: Path) -> None:
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
    db.initialize()

    with db._connect() as conn:
        answer_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(answer_regions)").fetchall()
        }
        template_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(session_templates)").fetchall()
        }
        rows = conn.execute(
            """
            SELECT region_uuid, mapping_status, multi_region_confirmed
            FROM answer_regions
            ORDER BY id
            """
        ).fetchall()
        index_rows = conn.execute("PRAGMA index_list(answer_regions)").fetchall()
        trigger_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'answer_regions'"
            ).fetchall()
        }

        assert {"region_uuid", "mapping_status", "multi_region_confirmed"} <= set(answer_columns)
        assert "regions_snapshot_pending" in template_columns
        assert answer_columns["mapping_status"]["dflt_value"] == "'unbound'"
        assert answer_columns["multi_region_confirmed"]["dflt_value"] == "0"
        assert template_columns["regions_snapshot_pending"]["dflt_value"] == "0"
        assert all(str(row["region_uuid"]).strip() for row in rows)
        assert len({row["region_uuid"] for row in rows}) == 2
        assert [row["mapping_status"] for row in rows] == ["unbound", "manual"]
        assert [row["multi_region_confirmed"] for row in rows] == [0, 0]
        assert any(
            row["name"] == "idx_answer_regions_region_uuid_unique" and row["unique"] == 1
            for row in index_rows
        )
        assert {
            "answer_regions_region_uuid_required_insert",
            "answer_regions_region_uuid_required_update",
        } <= trigger_names

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO answer_regions (
                    region_uuid, session_id, template_id, page, region_order, x, y, w, h
                ) VALUES (' ', 9, 7, 'front', 3, 0, 0, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO answer_regions (
                    region_uuid, session_id, template_id, page, region_order, x, y, w, h
                ) VALUES (?, 9, 7, 'front', 3, 0, 0, 1, 1)
                """,
                (rows[0]["region_uuid"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE answer_regions SET region_uuid = NULL WHERE id = 1")

    first_uuids = [row["region_uuid"] for row in db.list_answer_regions(9)]
    db.initialize()
    assert [row["region_uuid"] for row in db.list_answer_regions(9)] == first_uuids


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

    db.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    saved = db.list_answer_regions(session_id)
    assert [row["region_uuid"] for row in saved] == ["region-one", "region-two"]
    assert [row["mapping_status"] for row in saved] == ["auto", "manual"]
    assert [row["multi_region_confirmed"] for row in saved] == [0, 1]
    assert db.is_template_ready(session_id) is True
    assert db.get_session_template(session_id)["is_confirmed"] == 1
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1

    db.mark_region_snapshot_complete(session_id)
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0


def test_duplicate_uuid_replacement_rolls_back_and_preserves_formal_regions(tmp_path: Path) -> None:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id, template_id = _make_session(db)
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region("formal-one", 1, "Q1"), _region("formal-two", 2, "Q2")],
        confirmed=True,
    )
    db.mark_region_snapshot_complete(session_id)
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
