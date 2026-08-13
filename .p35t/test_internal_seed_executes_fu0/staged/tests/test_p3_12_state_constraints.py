from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from answer_region_models import normalize_regions
from backend.repositories.papers import PaperRepository
from backend.repositories.templates import RegionRepository
from backend.schema_contracts import schema_signature
from backend.status_contracts import audit_status_values, validate_status
from db_manager import DBManager
from tools.audit_status_values import main as audit_main
from update_tools.migrate_db import MigrationFile, _check_destructive
from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"


def test_pytest_default_data_root_is_isolated_from_repository_user_data() -> None:
    from path_manager import get_path_manager

    data_root = get_path_manager().data_root.resolve()

    assert not data_root.is_relative_to(PROJECT_ROOT.resolve())


def test_pytest_data_isolation_restores_an_unset_environment() -> None:
    script = """
import importlib.util
import os
from pathlib import Path

os.environ.pop("AI_GRADING_DATA_DIR", None)
module_path = Path("tests/conftest.py").resolve()
spec = importlib.util.spec_from_file_location("p3_12_conftest_probe", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.pytest_configure()
module.pytest_unconfigure()
assert "AI_GRADING_DATA_DIR" not in os.environ
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def _create_status_audit_fixture(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE grading_sessions (
                id INTEGER PRIMARY KEY,
                status TEXT
            );
            CREATE TABLE exam_papers (
                id INTEGER PRIMARY KEY,
                match_status TEXT,
                processing_status TEXT
            );
            CREATE TABLE answer_regions (
                id INTEGER PRIMARY KEY,
                mapping_status TEXT
            );

            INSERT INTO grading_sessions VALUES
                (1, 'completed'),
                (2, 'completed'),
                (3, 'mystery'),
                (4, NULL);
            INSERT INTO exam_papers VALUES
                (1, 'matched', 'graded'),
                (2, 'matched', '  '),
                (3, 'student_deleted', 'failed');
            INSERT INTO answer_regions VALUES
                (1, 'auto'),
                (2, 'manual'),
                (3, '');
            """
        )


def test_status_audit_is_read_only_and_classifies_all_observed_values(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    _create_status_audit_fixture(database)
    before = database.read_bytes()

    report = audit_status_values(database)

    assert database.read_bytes() == before
    assert report == {
        "answer_regions.mapping_status": {
            "allowed": ["auto", "manual", "unbound"],
            "observed": [
                {"classification": "blank", "count": 1, "value": ""},
                {"classification": "allowed", "count": 1, "value": "auto"},
                {"classification": "allowed", "count": 1, "value": "manual"},
            ],
        },
        "exam_papers.match_status": {
            "allowed": ["matched", "student_deleted", "unmatched"],
            "observed": [
                {"classification": "allowed", "count": 2, "value": "matched"},
                {
                    "classification": "allowed",
                    "count": 1,
                    "value": "student_deleted",
                },
            ],
        },
        "exam_papers.processing_status": {
            "allowed": ["failed", "graded", "grading", "pending", "skipped"],
            "observed": [
                {"classification": "blank", "count": 1, "value": "  "},
                {"classification": "allowed", "count": 1, "value": "failed"},
                {"classification": "allowed", "count": 1, "value": "graded"},
            ],
        },
        "grading_sessions.status": {
            "allowed": ["completed", "created", "failed", "running"],
            "observed": [
                {"classification": "null", "count": 1, "value": None},
                {"classification": "allowed", "count": 2, "value": "completed"},
                {"classification": "unknown", "count": 1, "value": "mystery"},
            ],
        },
    }


def test_status_audit_cli_emits_data_only_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "private-location" / "grading.db"
    database.parent.mkdir()
    _create_status_audit_fixture(database)

    assert audit_main([str(database)]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert str(tmp_path) not in json.dumps(payload)
    assert payload["summary"] == {
        "allowed_rows": 9,
        "blank_rows": 2,
        "null_rows": 1,
        "unknown_rows": 1,
    }
    assert payload["safe_to_migrate"] is False


def test_status_audit_cli_runs_directly_outside_project_root(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private-location" / "grading.db"
    database.parent.mkdir()
    _create_status_audit_fixture(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "audit_status_values.py"),
            str(database),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert str(tmp_path) not in json.dumps(payload)
    assert payload["safe_to_migrate"] is False


@pytest.mark.parametrize(
    ("contract_key", "value"),
    [
        ("grading_sessions.status", "paused"),
        ("exam_papers.match_status", "guessed"),
        ("exam_papers.processing_status", "done"),
        ("answer_regions.mapping_status", "confirmed"),
    ],
)
def test_shared_status_contract_rejects_illegal_persistent_values(
    contract_key: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=f"invalid {contract_key}"):
        validate_status(contract_key, value)


def test_only_the_approved_005_rebuild_can_drop_the_declared_tables(
    tmp_path: Path,
) -> None:
    approved_sql = """
    -- migration-policy: rebuild-tables grading_sessions,exam_papers,answer_regions
    DROP TABLE answer_regions;
    DROP TABLE exam_papers;
    DROP TABLE grading_sessions;
    """
    approved_path = tmp_path / "005_add_status_constraints.sql"
    approved_path.write_text(approved_sql, encoding="utf-8")

    approved = MigrationFile.from_path(approved_path)
    assert _check_destructive(approved) == []

    renamed_path = tmp_path / "006_unapproved_rebuild.sql"
    renamed_path.write_text(approved_sql, encoding="utf-8")
    assert _check_destructive(MigrationFile.from_path(renamed_path))

    overbroad_path = tmp_path / "005_add_status_constraints.sql"
    overbroad_path.write_text(
        approved_sql + "\nDELETE FROM students;",
        encoding="utf-8",
    )
    assert _check_destructive(MigrationFile.from_path(overbroad_path))

    overbroad_path.write_text(
        """
        -- migration-policy: rebuild-tables grading_sessions,exam_papers,answer_regions
        DROP TABLE "students";
        """,
        encoding="utf-8",
    )
    assert _check_destructive(MigrationFile.from_path(overbroad_path))


class _UnexpectedConnection:
    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid status reached SQL")


def test_session_status_boundary_rejects_before_opening_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "must-not-be-created.db"

    with pytest.raises(ValueError, match="invalid grading_sessions.status"):
        DBManager(database).update_session_status(1, "paused")

    assert not database.exists()


@pytest.mark.parametrize(
    ("match_status", "processing_status", "expected_contract"),
    [
        ("guessed", "pending", "exam_papers.match_status"),
        ("matched", "done", "exam_papers.processing_status"),
    ],
)
def test_paper_create_boundary_rejects_before_sql(
    match_status: str,
    processing_status: str,
    expected_contract: str,
) -> None:
    repository = PaperRepository(
        SimpleNamespace(connection=_UnexpectedConnection())
    )

    with pytest.raises(ValueError, match=f"invalid {expected_contract}"):
        repository.create_exam_paper(
            1,
            "front.png",
            "back.png",
            "student",
            None,
            match_status,
            processing_status,
        )


@pytest.mark.parametrize(
    "method_name",
    [
        "update_exam_paper_status",
        "update_exam_paper_status_if_current_assignment",
    ],
)
def test_paper_update_boundaries_reject_before_sql(method_name: str) -> None:
    repository = PaperRepository(
        SimpleNamespace(connection=_UnexpectedConnection())
    )

    with pytest.raises(
        ValueError,
        match="invalid exam_papers.processing_status",
    ):
        if method_name == "update_exam_paper_status":
            repository.update_exam_paper_status(1, "done")
        else:
            repository.update_exam_paper_status_if_current_assignment(
                1,
                2,
                "done",
            )


def test_answer_region_insert_boundary_rejects_before_sql() -> None:
    repository = RegionRepository(
        SimpleNamespace(connection=_UnexpectedConnection())
    )

    with pytest.raises(
        ValueError,
        match="invalid answer_regions.mapping_status",
    ):
        repository.insert_answer_region(
            1,
            2,
            {"mapping_status": "confirmed"},
        )


def test_answer_region_normalization_rejects_illegal_explicit_status() -> None:
    with pytest.raises(
        ValueError,
        match="invalid answer_regions.mapping_status",
    ):
        normalize_regions(
            [
                {
                    "mapped_question_id": "Q1",
                    "mapping_status": "confirmed",
                }
            ]
        )


def test_answer_region_bulk_update_rejects_illegal_status_before_sql() -> None:
    repository = RegionRepository(
        SimpleNamespace(connection=_UnexpectedConnection())
    )

    with pytest.raises(
        ValueError,
        match="invalid answer_regions.mapping_status",
    ):
        repository.bulk_update_answer_region_mapping(
            1,
            [
                {
                    "id": 2,
                    "mapped_question_id": "Q1",
                    "mapping_status": "confirmed",
                }
            ],
        )


def _bootstrap_through_004(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "grading.db"
    migrations = tmp_path / "pre-005-migrations"
    migrations.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) > 4:
            continue
        shutil.copy2(source, migrations / source.name)
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=migrations,
    )
    assert report.error is None, report.error
    return database, migrations


def _seed_legal_status_rows(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status
            ) VALUES (10, 'session', 'rubric', 'answer', 'failed')
            """
        )
        connection.execute(
            """
            INSERT INTO session_templates (
                id, session_id, front_template_path, back_template_path
            ) VALUES (20, 10, 'front', 'back')
            """
        )
        connection.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image,
                match_status, processing_status
            ) VALUES (
                30, 10, 'front.png', 'back.png',
                'student_deleted', 'skipped'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO answer_regions (
                id, region_uuid, session_id, template_id, page,
                region_order, x, y, w, h, mapping_status
            ) VALUES (
                40, 'region-40', 10, 20, 'front',
                0, 1, 2, 3, 4, 'manual'
            )
            """
        )


def test_005_rebuild_preserves_legal_rows_and_restores_schema_objects(
    tmp_path: Path,
) -> None:
    database, _ = _bootstrap_through_004(tmp_path)
    _seed_legal_status_rows(database)

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is None, report.error
    assert [(item.name, item.status) for item in report.results] == [
        ("005_add_status_constraints", "applied"),
        ("006_knowledge_ids_primary", "applied"),
        ("007_drop_legacy_knowledge_id", "applied"),
        ("008_drop_legacy_cli_tables", "applied"),
        ("009_add_teacher_score_locks", "applied"),
    ]
    assert Path(report.results[0].backup_path or "").is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT id, status FROM grading_sessions"
        ).fetchall() == [(10, "failed")]
        assert connection.execute(
            """
            SELECT id, match_status, processing_status
            FROM exam_papers
            """
        ).fetchall() == [(30, "student_deleted", "skipped")]
        assert connection.execute(
            "SELECT id, mapping_status FROM answer_regions"
        ).fetchall() == [(40, "manual")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        objects = {
            (str(kind), str(name))
            for kind, name in connection.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE type IN ('index', 'trigger')
                """
            )
        }
        assert {
            ("index", "idx_grading_sessions_active"),
            ("index", "idx_exam_papers_session_status"),
            ("index", "idx_exam_papers_student"),
            ("index", "idx_answer_regions_region_uuid_unique"),
            ("trigger", "answer_regions_region_uuid_required_insert"),
            ("trigger", "answer_regions_region_uuid_required_update"),
        } <= objects
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE grading_sessions SET status = 'paused' WHERE id = 10"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE exam_papers
                SET match_status = 'guessed'
                WHERE id = 30
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE exam_papers
                SET processing_status = 'done'
                WHERE id = 30
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE answer_regions
                SET mapping_status = 'confirmed'
                WHERE id = 40
                """
            )

    repeated = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    assert repeated.error is None
    assert repeated.results == []


def test_005_rebuild_preserves_autoincrement_sequences(
    tmp_path: Path,
) -> None:
    database, _ = _bootstrap_through_004(tmp_path)
    _seed_legal_status_rows(database)
    expected_sequences = {
        "grading_sessions": 99,
        "exam_papers": 199,
        "answer_regions": 299,
    }
    with sqlite3.connect(database) as connection:
        for table_name, sequence in expected_sequences.items():
            connection.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                (sequence, table_name),
            )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is None, report.error
    with sqlite3.connect(database) as connection:
        actual_sequences = {
            str(name): int(sequence)
            for name, sequence in connection.execute(
                """
                SELECT name, seq
                FROM sqlite_sequence
                WHERE name IN (
                    'grading_sessions',
                    'exam_papers',
                    'answer_regions'
                )
                """
            )
        }
    assert actual_sequences == expected_sequences


def test_005_foreign_key_failure_rolls_back_before_registration(
    tmp_path: Path,
) -> None:
    database, _ = _bootstrap_through_004(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image,
                match_status, processing_status
            ) VALUES (
                30, 999, 'front.png', 'back.png',
                'unmatched', 'pending'
            )
            """
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error == (
        "migration 005_add_status_constraints failed after backup"
    )
    assert report.results[0].status == "failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT session_id FROM exam_papers WHERE id = 30"
        ).fetchone() == (999,)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '005_add_status_constraints'
              AND success = 1
            """
        ).fetchone() == (0,)
        table_sql = str(
            connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'exam_papers'
                """
            ).fetchone()[0]
        )
        assert "CHECK" not in table_sql.upper()


def test_005_unknown_status_fails_atomically_after_backup(
    tmp_path: Path,
) -> None:
    database, _ = _bootstrap_through_004(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status
            ) VALUES (11, 'legacy', 'rubric', 'answer', 'mystery')
            """
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error == "migration 005_add_status_constraints failed after backup"
    assert report.results[0].status == "failed"
    assert Path(report.results[0].backup_path or "").is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM grading_sessions WHERE id = 11"
        ).fetchone() == ("mystery",)
        table_sql = str(
            connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'grading_sessions'
                """
            ).fetchone()[0]
        )
        assert "CHECK" not in table_sql.upper()
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '005_add_status_constraints'
              AND success = 1
            """
        ).fetchone() == (0,)
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE name LIKE '%_p3_12_new'
            """
        ).fetchone() == (0,)


def test_005_backfills_historical_runtime_columns_before_rebuild(
    tmp_path: Path,
) -> None:
    database, _ = _bootstrap_through_004(tmp_path)
    historical_columns = (
        "source_paper_path",
        "source_paper_sha256",
        "question_bank_sync_state",
        "question_bank_sync_details_json",
        "question_bank_sync_error",
        "question_bank_sync_updated_at",
    )
    with sqlite3.connect(database) as connection:
        for column in historical_columns:
            connection.execute(
                f"ALTER TABLE grading_sessions DROP COLUMN {column}"
            )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is None, report.error
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(grading_sessions)"
            )
        }
        assert set(historical_columns) <= columns

    reference = tmp_path / "reference.db"
    reference_report = run_migrations(
        "grading",
        db_path=reference,
        migrations_dir=GRADING_MIGRATIONS,
    )
    assert reference_report.error is None
    assert schema_signature(database) == schema_signature(reference)
