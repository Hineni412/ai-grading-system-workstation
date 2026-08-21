"""027 迁移：class_roster_memberships 重建为稳定标识键的纯花名册状态表。

合成数据覆盖两种旧行分支：
- 旧键（成绩库内部编号）的身份链接有稳定标识明文指纹 → 换算为稳定标识；
- 身份链接缺失或指纹仍是旧二进制格式 → 直接置 historical，保留原键不强删。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.schema_migrations import ensure_schema_current


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = PROJECT_ROOT / "migrations" / "student_affairs"

_TS = "2026-08-01T00:00:00+00:00"


def _legacy_database(tmp_path: Path) -> Path:
    """应用 027 之前的全部迁移，得到旧结构（含 subject_id 列）的库。"""
    released = tmp_path / "released-migrations"
    released.mkdir()
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        if migration.name[:3] >= "027":
            continue
        shutil.copy2(migration, released / migration.name)
    database = tmp_path / "student_affairs.db"
    ensure_schema_current(
        "student_affairs",
        database,
        migrations_dir=released,
        backup_dir=tmp_path / "backups",
        logger_override=logging.getLogger("test.roster-memberships-migration"),
    )
    return database


def _seed_legacy_rows(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        for object_id in ("obj-stable", "obj-historical", "obj-legacy"):
            connection.execute(
                """
                INSERT INTO encrypted_objects (
                    object_id, object_type, format_version,
                    cek_nonce, wrapped_cek, payload_nonce, payload_ciphertext,
                    revision, created_at, updated_at
                ) VALUES (?, 'student_subject', 1, X'', X'', X'', X'7b7d', 1, ?, ?)
                """,
                (object_id, _TS, _TS),
            )
        # sub-stable / sub-historical 的指纹已是稳定标识明文；sub-legacy 仍是旧二进制指纹
        connection.execute(
            "INSERT INTO student_subject_links VALUES ('sub-stable', '一班|A001', 'obj-stable', 'active', ?, ?)",
            (_TS, _TS),
        )
        connection.execute(
            "INSERT INTO student_subject_links VALUES ('sub-historical', '一班|A002', 'obj-historical', 'active', ?, ?)",
            (_TS, _TS),
        )
        connection.execute(
            "INSERT INTO student_subject_links VALUES ('sub-legacy', X'00112233', 'obj-legacy', 'active', ?, ?)",
            (_TS, _TS),
        )
        connection.executemany(
            "INSERT INTO class_roster_memberships VALUES (?, ?, 'rev1', ?, ?, ?, ?)",
            [
                ("1", "sub-stable", "active", _TS, None, _TS),
                ("2", "sub-historical", "historical", _TS, _TS, _TS),
                ("99", "sub-legacy", "active", _TS, None, _TS),
            ],
        )
        connection.commit()


def test_membership_rebuild_converts_keys_and_retires_subject_id(
    tmp_path: Path,
) -> None:
    database = _legacy_database(tmp_path)
    _seed_legacy_rows(database)

    ensure_schema_current(
        "student_affairs",
        database,
        migrations_dir=MIGRATIONS,
        backup_dir=tmp_path / "backups",
        logger_override=logging.getLogger("test.roster-memberships-migration"),
    )

    with closing(sqlite3.connect(database)) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(class_roster_memberships)"
            ).fetchall()
        }
        assert "subject_id" not in columns
        assert columns == {
            "source_student_key",
            "state",
            "activated_at",
            "historical_at",
            "updated_at",
        }
        rows = {
            (str(row[0]), str(row[1]), row[2])
            for row in connection.execute(
                "SELECT source_student_key, state, historical_at FROM class_roster_memberships"
            ).fetchall()
        }
    # 有可换算稳定标识的旧行：换成稳定标识键，状态保留
    assert ("一班|A001", "active", None) in rows
    assert ("一班|A002", "historical", _TS) in rows
    # 指纹仍是旧二进制格式的旧行：直接置 historical，保留原键不强删
    legacy_row = next(row for row in rows if row[0] == "99")
    assert legacy_row[1] == "historical"
    assert legacy_row[2] is not None
    assert len(rows) == 3
