"""P3-17 legacy skill semantics retirement behavior."""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_TOOL = PROJECT_ROOT / "tools" / "export_legacy_skill_data.py"
QUESTION_BANK_MIGRATIONS = PROJECT_ROOT / "migrations" / "question_bank"
LEGACY_TABLES = (
    "knowledge_concepts",
    "knowledge_relations",
    "knowledge_source_mappings",
    "skill_topics",
    "skills",
    "assessment_item_skills",
    "question_skill_links",
    "skill_resolution_conflicts",
    "skill_neighbors",
    "skill_system_settings",
    "skill_migration_runs",
)

sys.path.insert(0, str(PROJECT_ROOT / "update_tools"))

from migrate_db import run_migrations  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_legacy_skill_database(database: Path) -> None:
    through_008 = database.parent / "question-bank-migrations-through-008"
    through_008.mkdir(exist_ok=True)
    for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 8:
            shutil.copy2(source, through_008 / source.name)
    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=through_008,
    )
    assert report.error is None, report.error
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (501, 'Q1', '匿名题目')
            """
        )
        connection.executemany(
            """
            INSERT INTO knowledge_concepts (id, canonical_key, name)
            VALUES (?, ?, ?)
            """,
            (
                (1, "legacy.concept.one", "旧概念一"),
                (2, "legacy.concept.two", "旧概念二"),
            ),
        )
        connection.execute(
            """
            INSERT INTO knowledge_relations (
                id, source_concept_id, target_concept_id, relation_type
            ) VALUES (1, 1, 2, 'prerequisite')
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_source_mappings (
                id, source_namespace, source_value, normalized_value,
                concept_id, status, confidence
            ) VALUES (1, 'rubric', 'K1', 'k1', 1, 'confirmed', 1.0)
            """
        )
        connection.execute(
            """
            INSERT INTO skill_topics (id, stable_key, name)
            VALUES (1, 'legacy.topic', '旧主题')
            """
        )
        connection.executemany(
            """
            INSERT INTO skills (
                id, stable_key, topic_id, name, origin
            ) VALUES (?, ?, 1, ?, 'local')
            """,
            (
                (1, "legacy.skill.one", "旧技能一"),
                (2, "legacy.skill.two", "旧技能二"),
            ),
        )
        connection.execute(
            """
            INSERT INTO assessment_item_skills (
                id, grading_session_id, source_question_id, skill_id, role
            ) VALUES (1, 'S1', 'Q1', 1, 'measured')
            """
        )
        connection.execute(
            """
            INSERT INTO question_skill_links (
                id, question_id, skill_id, role
            ) VALUES (1, 501, 1, 'measured')
            """
        )
        connection.execute(
            """
            INSERT INTO skill_resolution_conflicts (
                id, source_type, source_ref, raw_label, reason
            ) VALUES (1, 'legacy_term', 'K1', '旧标签', 'ambiguous')
            """
        )
        connection.execute(
            """
            INSERT INTO skill_neighbors (
                id, source_skill_id, target_skill_id, kind
            ) VALUES (1, 1, 2, 'prerequisite')
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO skill_system_settings (key, value)
            VALUES ('p3_17_test', 'legacy')
            """
        )
        connection.execute(
            """
            INSERT INTO skill_migration_runs (
                id, batch_id, mode, status
            ) VALUES (1, 'P3-17-TEST', 'dry_run', 'succeeded')
            """
        )


def _seed_preserved_question_tag_and_training_rows(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (501, 'knowledge_point', 'current-tag', 'manual')
            """
        )
        connection.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, confidence, status
            ) VALUES ('S1', 'Q1', 501, 'manual', 1.0, 'confirmed')
            """
        )
        connection.execute(
            """
            INSERT INTO training_tasks (id, task_code, status)
            VALUES (1, 'P3-17-PRESERVED', 'ready')
            """
        )
        connection.execute(
            """
            INSERT INTO training_variants (
                id, task_id, variant_key, variant_type
            ) VALUES (1, 1, 'student-1', 'individual')
            """
        )
        connection.execute(
            """
            INSERT INTO variant_students (variant_id, student_id)
            VALUES (1, 'student-1')
            """
        )
        connection.execute(
            """
            INSERT INTO training_task_items (
                id, variant_id, task_item_code, bank_question_id,
                item_order, stage
            ) VALUES (1, 1, 'P3-17-ITEM', 501, 1, 'direct')
            """
        )
        connection.execute(
            """
            INSERT INTO training_exports (
                id, task_id, variant_id, audience, export_format, status
            ) VALUES (1, 1, 1, 'student', 'docx', 'succeeded')
            """
        )
        connection.execute(
            """
            INSERT INTO training_attempts (
                id, task_item_code, student_id, score_awarded, full_score
            ) VALUES (1, 'P3-17-ITEM', 'student-1', 8, 10)
            """
        )


def test_export_archives_all_legacy_skill_tables_without_writing_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    archive = tmp_path / "legacy-skills.json"
    _seed_legacy_skill_database(database)
    source_hash = _sha256(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_TOOL),
            "--database",
            str(database),
            "--output",
            str(archive),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _sha256(database) == source_hash
    payload = json.loads(archive.read_text(encoding="utf-8"))
    assert payload["format"] == "ai-grading-legacy-skill-export"
    assert payload["version"] == 1
    assert tuple(payload["tables"]) == LEGACY_TABLES
    assert payload["summary"]["knowledge_concepts"] == 2
    assert payload["summary"]["skills"] == 2
    assert payload["summary"]["question_skill_links"] == 1
    assert any(
        row["key"] == "p3_17_test" and row["value"] == "legacy"
        for row in payload["tables"]["skill_system_settings"]["rows"]
    )
    canonical_tables = json.dumps(
        payload["tables"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert payload["content_sha256"] == hashlib.sha256(
        canonical_tables
    ).hexdigest()


def test_export_does_not_create_sidecars_for_wal_mode_source(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    database = source_dir / "question-bank.db"
    archive = output_dir / "legacy-skills.json"
    _seed_legacy_skill_database(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    source_files_before = tuple(sorted(path.name for path in source_dir.iterdir()))
    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_TOOL),
            "--database",
            str(database),
            "--output",
            str(archive),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert tuple(sorted(path.name for path in source_dir.iterdir())) == (
        source_files_before
    )


def test_export_reads_committed_wal_without_touching_source_files(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    database = source_dir / "question-bank.db"
    archive = output_dir / "legacy-skills.json"
    _seed_legacy_skill_database(database)

    with sqlite3.connect(database) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            """
            INSERT INTO skill_system_settings (key, value)
            VALUES ('committed_in_wal', 'visible')
            """
        )
        writer.commit()
        source_state_before = {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns, _sha256(path))
            for path in source_dir.iterdir()
            if path.is_file()
        }

        completed = subprocess.run(
            [
                sys.executable,
                str(EXPORT_TOOL),
                "--database",
                str(database),
                "--output",
                str(archive),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        payload = json.loads(archive.read_text(encoding="utf-8"))
        assert any(
            row["key"] == "committed_in_wal" and row["value"] == "visible"
            for row in payload["tables"]["skill_system_settings"]["rows"]
        )
        assert {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns, _sha256(path))
            for path in source_dir.iterdir()
            if path.is_file()
        } == source_state_before


def test_export_records_legacy_tables_that_were_never_present(
    tmp_path: Path,
) -> None:
    database = tmp_path / "incomplete.db"
    archive = tmp_path / "legacy-skills.json"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE knowledge_concepts (id INTEGER PRIMARY KEY)"
        )
    source_hash = _sha256(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_TOOL),
            "--database",
            str(database),
            "--output",
            str(archive),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _sha256(database) == source_hash
    payload = json.loads(archive.read_text(encoding="utf-8"))
    assert payload["tables"]["knowledge_concepts"]["rows"] == []
    assert payload["tables"]["skill_topics"] == {
        "present": False,
        "columns": [],
        "rows": [],
    }
    assert payload["summary"]["skill_topics"] == 0


def test_only_question_tag_runtime_contracts_remain() -> None:
    retired_paths = (
        "integration/skill_conflict_inbox_service.py",
        "question_bank/models/knowledge_alignment.py",
        "question_bank/models/skill_catalog.py",
        "question_bank/services/alignment_review_service.py",
        "question_bank/services/concept_alignment_service.py",
        "question_bank/services/skill_catalog_service.py",
        "question_bank/services/skill_context_ranker.py",
        "question_bank/services/skill_link_service.py",
        "question_bank/services/skill_migration_service.py",
        "question_bank/services/skill_resolution_service.py",
        "question_bank/taxonomy/skill_catalog_seed.py",
        "question_bank/taxonomy/data/junior_math_skills_v1.json",
        "update_tools/migrate_skill_catalog.py",
    )
    assert not [
        relative_path
        for relative_path in retired_paths
        if (PROJECT_ROOT / relative_path).exists()
    ]

    from integration.diagnosis_profile_service import DiagnosisProfileService
    from integration.skill_graph_projection import (
        build_question_tag_graph_rows,
    )
    from question_bank.database.schema import initialize_database
    from question_bank.recommendation.practice_plan_service import (
        PracticePlanService,
    )
    from question_bank.services.ai_tagging_service import AITaggingService
    from question_bank.services.question_write_service import QuestionBankWriteService
    import session_manager

    assert hasattr(DiagnosisProfileService, "build_tag_profiles")
    assert hasattr(DiagnosisProfileService, "tag_evidence")
    assert not hasattr(DiagnosisProfileService, "build_legacy_profiles")
    assert not hasattr(DiagnosisProfileService, "build_skill_profiles")
    assert not hasattr(DiagnosisProfileService, "skill_evidence")
    assert not hasattr(AITaggingService, "build_skill_context_ranker")
    assert not hasattr(session_manager, "iter_rubric_skill_requests")
    assert "seed_skills" not in inspect.signature(initialize_database).parameters
    assert "resolve_skills" not in inspect.signature(
        QuestionBankWriteService.save_tag_analysis
    ).parameters
    assert "skill_resolver" not in inspect.signature(
        QuestionBankWriteService.save_tag_analysis
    ).parameters
    assert build_question_tag_graph_rows({"students": []}) == []

    service = PracticePlanService(PROJECT_ROOT / "unused.db")
    try:
        service.generate_variant(
            {"diagnosis_identity": "skill", "students": []},
            question_count=8,
        )
    except ValueError as exc:
        assert "question_tag" in str(exc)
    else:
        raise AssertionError("retired skill diagnosis was accepted")


def test_009_drops_exact_legacy_tables_and_backup_restores_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_legacy_skill_database(database)
    _seed_preserved_question_tag_and_training_rows(database)

    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )

    assert report.error is None, report.error
    expected_pending = [
        source.stem
        for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql"))
        if int(source.name.split("_", 1)[0]) >= 9
    ]
    assert [(item.name, item.status) for item in report.results] == [
        (name, "applied") for name in expected_pending
    ]
    backup = Path(report.results[0].backup_path or "")
    assert backup.is_file()
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        # P4 deliberately reuses the generic knowledge_relations table name
        # for its governed stable-identity graph after migration 009 retires
        # the old concept-id relation table.
        assert not (set(LEGACY_TABLES) - {"knowledge_relations"}).intersection(
            tables
        )
        relation_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(knowledge_relations)")
        }
        assert {"relation_id", "source_key", "target_key", "relation_type"} <= (
            relation_columns
        )
        assert {"id", "source_concept_id", "target_concept_id"}.isdisjoint(
            relation_columns
        )
        assert {
            "question_tags",
            "grading_question_links",
            "training_tasks",
            "training_variants",
            "variant_students",
            "training_task_items",
            "training_exports",
            "training_attempts",
            "training_sets",
            "training_set_items",
        } <= tables
        assert connection.execute(
            "SELECT tag_value FROM question_tags WHERE question_id = 501"
        ).fetchone() == ("current-tag",)
        assert connection.execute(
            "SELECT task_code FROM training_tasks WHERE id = 1"
        ).fetchone() == ("P3-17-PRESERVED",)
        assert connection.execute(
            "SELECT score_awarded, full_score FROM training_attempts WHERE id = 1"
        ).fetchone() == (8.0, 10.0)

    restored = tmp_path / "restored.db"
    shutil.copy2(backup, restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute(
            "SELECT name FROM knowledge_concepts WHERE id = 1"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT name FROM skills WHERE id = 1"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT tag_value FROM question_tags WHERE question_id = 501"
        ).fetchone() == ("current-tag",)

    repeated = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )
    assert repeated.error is None
    assert repeated.results == []


def test_009_schema_drift_rolls_back_without_partial_drop(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_legacy_skill_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE skill_system_settings ADD COLUMN unexpected TEXT"
        )

    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert set(LEGACY_TABLES) <= tables
        assert connection.execute(
            """
            SELECT COUNT(*) FROM schema_migrations
            WHERE migration_name = '009_drop_legacy_skill_semantics'
              AND success = 1
            """
        ).fetchone() == (0,)


def test_009_accepts_the_supported_untracked_legacy_column_order(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_legacy_skill_database(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            ALTER TABLE knowledge_source_mappings
                RENAME TO knowledge_source_mappings_canonical;
            CREATE TABLE knowledge_source_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_namespace TEXT NOT NULL,
                source_value TEXT NOT NULL,
                normalized_value TEXT NOT NULL DEFAULT '',
                concept_id INTEGER,
                status TEXT NOT NULL DEFAULT 'suggested',
                confidence REAL NOT NULL DEFAULT 0.0,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                sub_skill_tags TEXT DEFAULT '[]',
                FOREIGN KEY(concept_id) REFERENCES knowledge_concepts(id)
            );
            INSERT INTO knowledge_source_mappings
            SELECT id, source_namespace, source_value, normalized_value,
                   concept_id, status, confidence, evidence_json,
                   reviewed_by, reviewed_at, created_at, updated_at,
                   sub_skill_tags
            FROM knowledge_source_mappings_canonical;
            DROP TABLE knowledge_source_mappings_canonical;
            PRAGMA foreign_keys = ON;
            """
        )

    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )

    assert report.error is None, report.error
    assert report.results[-1].name == sorted(
        QUESTION_BANK_MIGRATIONS.glob("*.sql")
    )[-1].stem


@pytest.mark.parametrize("mode", ["partial", "extra"])
def test_009_exact_drop_policy_rejects_changed_table_set(
    tmp_path: Path,
    mode: str,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_legacy_skill_database(database)
    migrations = tmp_path / f"migrations-{mode}"
    migrations.mkdir()
    for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 8:
            shutil.copy2(source, migrations / source.name)
    targets = list(LEGACY_TABLES)
    if mode == "partial":
        targets.pop()
    else:
        targets.append("training_tasks")
    (migrations / "009_drop_legacy_skill_semantics.sql").write_text(
        "-- migration-policy: drop-tables "
        + ", ".join(targets)
        + "\n"
        + "\n".join(f"DROP TABLE {name};" for name in targets),
        encoding="utf-8",
    )

    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=migrations,
    )

    assert report.error is not None
    assert report.results[-1].status == "destructive_blocked"
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
    assert set(LEGACY_TABLES) <= tables
    assert "training_tasks" in tables
