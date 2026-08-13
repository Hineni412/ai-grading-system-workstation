"""P3-16 legacy CLI retirement behavior."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_TOOL = PROJECT_ROOT / "tools" / "export_legacy_cli_data.py"
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"

sys.path.insert(0, str(PROJECT_ROOT / "update_tools"))

from migrate_db import run_migrations  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_legacy_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE exam_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                front_image TEXT NOT NULL,
                back_image TEXT NOT NULL,
                total_score REAL NOT NULL,
                student_score REAL NOT NULL,
                needs_human_review INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE grading_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_result_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                score_awarded REAL NOT NULL,
                deduction_reason TEXT,
                knowledge_id TEXT,
                knowledge_ids TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(exam_result_id) REFERENCES exam_results(id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO exam_results (
                id, student_name, front_image, back_image, total_score,
                student_score, needs_human_review, raw_json, created_at
            ) VALUES (7, '匿名学生', 'front.jpg', 'back.jpg', 100, 86.5, 1,
                      '{"source":"legacy"}', '2026-07-01 08:00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO grading_details (
                id, exam_result_id, question_id, score_awarded,
                deduction_reason, knowledge_id, knowledge_ids
            ) VALUES (11, 7, 'Q1', 8.5, '计算错误', 'K1', '["K1"]')
            """
        )


def _bootstrap_through_007(tmp_path: Path) -> Path:
    migrations = tmp_path / "migrations-through-007"
    migrations.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 7:
            shutil.copy2(source, migrations / source.name)
    database = tmp_path / "grading-through-007.db"
    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=migrations,
    )
    assert report.error is None, report.error
    return database


def test_export_cli_archives_complete_legacy_rows_without_writing_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    archive = tmp_path / "legacy-export.json"
    _seed_legacy_database(database)
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
    assert payload["format"] == "ai-grading-legacy-cli-export"
    assert payload["version"] == 1
    assert payload["tables"]["exam_results"]["rows"] == [
        {
            "id": 7,
            "student_name": "匿名学生",
            "front_image": "front.jpg",
            "back_image": "back.jpg",
            "total_score": 100.0,
            "student_score": 86.5,
            "needs_human_review": 1,
            "raw_json": '{"source":"legacy"}',
            "created_at": "2026-07-01 08:00:00",
        }
    ]
    assert payload["tables"]["grading_details"]["rows"] == [
        {
            "id": 11,
            "exam_result_id": 7,
            "question_id": "Q1",
            "score_awarded": 8.5,
            "deduction_reason": "计算错误",
            "knowledge_id": "K1",
            "knowledge_ids": '["K1"]',
        }
    ]
    assert payload["summary"] == {
        "exam_results": 1,
        "grading_details": 1,
    }
    canonical_tables = json.dumps(
        payload["tables"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert payload["content_sha256"] == hashlib.sha256(
        canonical_tables
    ).hexdigest()


def test_export_cli_refuses_overwrite_and_missing_table_leaves_no_archive(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    archive = tmp_path / "legacy-export.json"
    _seed_legacy_database(database)
    archive.write_text("keep-existing", encoding="utf-8")

    refused = subprocess.run(
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

    assert refused.returncode == 1
    assert archive.read_text(encoding="utf-8") == "keep-existing"

    incomplete = tmp_path / "incomplete.db"
    with sqlite3.connect(incomplete) as connection:
        connection.execute(
            "CREATE TABLE exam_results (id INTEGER PRIMARY KEY)"
        )
    missing_archive = tmp_path / "missing-export.json"
    missing = subprocess.run(
        [
            sys.executable,
            str(EXPORT_TOOL),
            "--database",
            str(incomplete),
            "--output",
            str(missing_archive),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert missing.returncode == 1
    assert "required legacy table is unavailable" in missing.stderr
    assert not missing_archive.exists()


def test_concurrent_exports_cannot_overwrite_the_same_new_archive(
    tmp_path: Path,
) -> None:
    first_database = tmp_path / "first.db"
    second_database = tmp_path / "second.db"
    archive = tmp_path / "shared-export.json"
    _seed_legacy_database(first_database)
    _seed_legacy_database(second_database)
    with sqlite3.connect(second_database) as connection:
        connection.execute(
            "UPDATE exam_results SET student_name = '另一份数据'"
        )

    command = [
        sys.executable,
        str(EXPORT_TOOL),
        "--output",
        str(archive),
    ]
    first = subprocess.Popen(
        [*command, "--database", str(first_database)],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = subprocess.Popen(
        [*command, "--database", str(second_database)],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first_stdout, first_stderr = first.communicate(timeout=30)
    second_stdout, second_stderr = second.communicate(timeout=30)

    assert sorted([first.returncode, second.returncode]) == [0, 1], (
        first_stdout,
        first_stderr,
        second_stdout,
        second_stderr,
    )
    payload = json.loads(archive.read_text(encoding="utf-8"))
    assert payload["tables"]["exam_results"]["rows"][0]["student_name"] in {
        "匿名学生",
        "另一份数据",
    }


def test_008_drops_only_legacy_tables_and_backup_restores_rows(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_007(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO exam_results (
                id, student_name, front_image, back_image, total_score,
                student_score, needs_human_review, raw_json
            ) VALUES (7, '匿名学生', 'front.jpg', 'back.jpg', 100, 86.5, 1,
                      '{"source":"legacy"}')
            """
        )
        connection.execute(
            """
            INSERT INTO grading_details (
                id, exam_result_id, question_id, score_awarded,
                deduction_reason, knowledge_id, knowledge_ids
            ) VALUES (11, 7, 'Q1', 8.5, '计算错误', 'K1', '["K1"]')
            """
        )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is None, report.error
    assert [(item.name, item.status) for item in report.results] == [
        ("008_drop_legacy_cli_tables", "applied"),
        ("009_add_teacher_score_locks", "applied"),
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
        assert "exam_results" not in tables
        assert "grading_details" not in tables
        assert "session_results" in tables
        assert "session_details" in tables

    restored = tmp_path / "restored.db"
    shutil.copy2(backup, restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute(
            "SELECT student_name, student_score FROM exam_results WHERE id = 7"
        ).fetchone() == ("匿名学生", 86.5)
        assert connection.execute(
            """
            SELECT exam_result_id, question_id, score_awarded
            FROM grading_details
            WHERE id = 11
            """
        ).fetchone() == (7, "Q1", 8.5)

    repeated = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )
    assert repeated.error is None
    assert repeated.results == []


def test_008_rejects_schema_drift_without_partial_drop(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_007(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE grading_details")

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=GRADING_MIGRATIONS,
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert "exam_results" in tables
        assert "grading_details" not in tables
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE migration_name = '008_drop_legacy_cli_tables'
              AND success = 1
            """
        ).fetchone() == (0,)


def test_008_exact_drop_policy_rejects_extra_table(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_007(tmp_path)
    migrations = tmp_path / "migrations-with-extra-drop"
    migrations.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 7:
            shutil.copy2(source, migrations / source.name)
    (migrations / "008_drop_legacy_cli_tables.sql").write_text(
        "\n".join(
            [
                "-- migration-policy: drop-tables grading_details, exam_results, session_results",
                "DROP TABLE grading_details;",
                "DROP TABLE exam_results;",
                "DROP TABLE session_results;",
            ]
        ),
        encoding="utf-8",
    )

    report = run_migrations(
        "grading",
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
    assert {"exam_results", "grading_details", "session_results"} <= tables


def test_008_exact_drop_policy_rejects_partial_table_set(
    tmp_path: Path,
) -> None:
    database = _bootstrap_through_007(tmp_path)
    migrations = tmp_path / "migrations-with-partial-drop"
    migrations.mkdir()
    for source in sorted(GRADING_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 7:
            shutil.copy2(source, migrations / source.name)
    (migrations / "008_drop_legacy_cli_tables.sql").write_text(
        "\n".join(
            [
                "-- migration-policy: drop-tables grading_details, exam_results",
                "DROP TABLE grading_details;",
            ]
        ),
        encoding="utf-8",
    )

    report = run_migrations(
        "grading",
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
    assert {"exam_results", "grading_details"} <= tables


def test_legacy_cli_and_its_public_apis_are_retired() -> None:
    assert not (PROJECT_ROOT / "main.py").exists()

    db_tree = ast.parse(
        (PROJECT_ROOT / "db_manager.py").read_text(encoding="utf-8")
    )
    db_manager = next(
        node
        for node in db_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DBManager"
    )
    db_methods = {
        node.name
        for node in db_manager.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "save_result" not in db_methods

    report_tree = ast.parse(
        (PROJECT_ROOT / "report.py").read_text(encoding="utf-8")
    )
    report_generator = next(
        node
        for node in report_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReportGenerator"
    )
    report_methods = {
        node.name
        for node in report_generator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "export" not in report_methods
    assert "export_session" in report_methods
