from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.skill_migration_service import (
    SkillMigrationConfig,
    SkillMigrationService,
)
from update_tools.migrate_skill_catalog import build_parser


KNOWN_SKILL_KEY = "math.geometry.line_angle.bisector"
KNOWN_SKILL_NAME = "\u89d2\u5e73\u5206\u7ebf\u6027\u8d28"


def test_migration_cli_exposes_guarded_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["rollback", "--question-bank-db", "q.db", "--batch-id", "b"]).command == "rollback"
    with pytest.raises(SystemExit):
        parser.parse_args(["apply", "--grading-db", "g.db", "--question-bank-db", "q.db", "--report-dir", "reports"])


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gold_file(path: Path, *, count: int = 100, correct: int | None = None) -> Path:
    correct = count if correct is None else correct
    items = [
        {
            "case_id": f"gold-{index:03d}",
            "raw_label": KNOWN_SKILL_NAME,
            "expected_stable_key": KNOWN_SKILL_KEY if index < correct else "math.invalid",
        }
        for index in range(count)
    ]
    path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def _question_bank(path: Path, *, include_unknown: bool = False) -> Path:
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, source_file, grade, import_status)
            VALUES (1, 'migration paper', 'migration.docx', 'grade-8', 'ready')
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text,
                answer_text, difficulty
            ) VALUES (1, 1, '1', 'solution', 'bisect an angle', 'proof', '5')
            """
        )
        conn.execute(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, source)
            VALUES (1, 'canonical_knowledge_id', ?, 'manual')
            """,
            (KNOWN_SKILL_KEY,),
        )
        if include_unknown:
            conn.execute(
                """
                INSERT INTO questions (
                    id, paper_id, question_number, question_type, question_text,
                    answer_text, difficulty
                ) VALUES (2, 1, '2', 'solution', 'school-only procedure', 'unknown', '5')
                """
            )
            conn.execute(
                """
                INSERT INTO question_tags (question_id, tag_type, tag_value, source)
                VALUES (2, 'knowledge_point', 'school-only unknown procedure', 'manual')
                """
            )
    return path


def _grading_db(path: Path, rubric_path: Path | None) -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE grading_sessions (
                id INTEGER PRIMARY KEY,
                session_name TEXT NOT NULL,
                rubric_path TEXT NOT NULL,
                answer_key_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                is_deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT);
            CREATE TABLE session_results (
                id INTEGER PRIMARY KEY, session_id INTEGER, student_id INTEGER,
                paper_id INTEGER, total_score REAL, student_score REAL,
                needs_human_review INTEGER, raw_json TEXT
            );
            """
        )
        if rubric_path is not None:
            conn.execute(
                """
                INSERT INTO grading_sessions (
                    id, session_name, rubric_path, answer_key_path, status, is_deleted
                ) VALUES (1, 'migration exam', ?, 'answer.json', 'created', 0)
                """,
                (str(rubric_path),),
            )
    return path


def _rubric(path: Path) -> Path:
    payload = {
        "grade": "grade-8",
        "questions": [
            {
                "question_id": "Q1",
                "knowledge_id": KNOWN_SKILL_KEY,
                "knowledge_name": KNOWN_SKILL_NAME,
                "stem_summary": "prove an angle bisector property",
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _config(tmp_path: Path, *, unknown: bool = False, gold_count: int = 100) -> SkillMigrationConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rubric = _rubric(tmp_path / "rubric.json")
    return SkillMigrationConfig(
        grading_db=_grading_db(tmp_path / "grading.db", rubric),
        question_bank_db=_question_bank(tmp_path / "question_bank.db", include_unknown=unknown),
        report_dir=tmp_path / "reports",
        batch_id="skill-test-batch",
        gold_file=_gold_file(tmp_path / "gold.json", count=gold_count),
    )


def test_dry_run_changes_only_report_and_accounts_for_every_source(tmp_path: Path) -> None:
    config = _config(tmp_path)
    before = {
        "question_bank": _hash(config.question_bank_db),
        "grading": _hash(config.grading_db),
        "rubric": _hash(tmp_path / "rubric.json"),
    }

    report = SkillMigrationService().dry_run(config)

    assert report["status"] == "ready"
    assert report["source_counts"] == {"question_bank": 1, "assessment": 1, "total": 2}
    assert report["result_counts"]["resolved_existing"] == 2
    assert report["coverage"]["value"] == 1.0
    assert report["gold"]["reviewed_count"] == 100
    assert report["gold"]["precision"] == 1.0
    assert before == {
        "question_bank": _hash(config.question_bank_db),
        "grading": _hash(config.grading_db),
        "rubric": _hash(tmp_path / "rubric.json"),
    }
    assert Path(report["report_path"]).is_file()


def test_apply_backs_up_first_is_idempotent_and_keeps_legacy_read_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = SkillMigrationService()

    first = service.apply(config)
    with connect(config.question_bank_db) as conn:
        counts_after_first = {
            "links": conn.execute("SELECT COUNT(*) FROM question_skill_links").fetchone()[0],
            "assessment": conn.execute("SELECT COUNT(*) FROM assessment_item_skills").fetchone()[0],
            "conflicts": conn.execute("SELECT COUNT(*) FROM skill_resolution_conflicts").fetchone()[0],
            "runs": conn.execute("SELECT COUNT(*) FROM skill_migration_runs").fetchone()[0],
        }
        read_mode = conn.execute(
            "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
        ).fetchone()[0]
    second = service.apply(config)
    with connect(config.question_bank_db) as conn:
        counts_after_second = {
            "links": conn.execute("SELECT COUNT(*) FROM question_skill_links").fetchone()[0],
            "assessment": conn.execute("SELECT COUNT(*) FROM assessment_item_skills").fetchone()[0],
            "conflicts": conn.execute("SELECT COUNT(*) FROM skill_resolution_conflicts").fetchone()[0],
            "runs": conn.execute("SELECT COUNT(*) FROM skill_migration_runs").fetchone()[0],
        }

    assert first["status"] == "succeeded"
    assert all(Path(path).is_file() for path in first["backups"].values())
    assert first["backup_created_before_write"] is True
    assert read_mode == "legacy"
    assert second["idempotent"] is True
    assert counts_after_first == counts_after_second


def test_failed_replace_leaves_links_unchanged_and_mode_legacy(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    service = SkillMigrationService()

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(service, "_replace_database", fail_replace)
    report = service.apply(config)

    with connect(config.question_bank_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM question_skill_links").fetchone()[0] == 0
        row = conn.execute(
            "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
        ).fetchone()
    assert report["status"] == "failed"
    assert row is None or row[0] == "legacy"


def test_readiness_gates_require_coverage_and_one_hundred_gold_rows(tmp_path: Path) -> None:
    low_coverage = SkillMigrationService().dry_run(_config(tmp_path / "coverage", unknown=True))
    too_small = SkillMigrationService().dry_run(_config(tmp_path / "gold", gold_count=99))

    assert low_coverage["coverage"]["value"] < 0.95
    assert low_coverage["ready"] is False
    assert "coverage_below_95_percent" in low_coverage["readiness_reasons"]
    assert too_small["gold"]["reviewed_count"] == 99
    assert too_small["ready"] is False
    assert "gold_review_count_below_100" in too_small["readiness_reasons"]


def test_rollback_restores_both_database_backups_and_records_status(tmp_path: Path) -> None:
    config = _config(tmp_path)
    before_question_bank = _hash(config.question_bank_db)
    service = SkillMigrationService()
    applied = service.apply(config)

    conn = sqlite3.connect(config.grading_db)
    try:
        conn.execute("INSERT INTO students (id, student_code, name) VALUES (1, 'S1', 'Changed')")
        conn.commit()
    finally:
        conn.close()
    rolled_back = service.rollback(config.question_bank_db, config.batch_id)

    assert applied["status"] == "succeeded"
    assert rolled_back["status"] == "rolled_back"
    assert _hash(config.question_bank_db) != before_question_bank
    with connect(config.question_bank_db) as conn:
        run = conn.execute(
            "SELECT mode, status FROM skill_migration_runs WHERE batch_id = ?",
            (config.batch_id,),
        ).fetchone()
    assert tuple(run) == ("rollback", "rolled_back")
    assert _hash(config.grading_db) == _hash(Path(applied["backups"]["grading"]))
    assert rolled_back["restored_from"] == applied["backups"]
