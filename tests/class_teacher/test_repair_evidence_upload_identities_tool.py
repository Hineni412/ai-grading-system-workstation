from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from update_tools import repair_class_teacher_evidence_upload_identities as tool


def _build_grading_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [
                (20, "20250920", "合成学生甲", "9"),
                (21, "20250921", "合成学生乙", "9"),
                (22, "20250922", "合成学生丙", "9"),
            ],
        )
        connection.commit()


def _insert_subject(
    connection: sqlite3.Connection,
    *,
    subject_id: str,
    fingerprint: str,
    payload: dict[str, object],
) -> None:
    object_id = f"student-subject-{subject_id}"
    connection.execute(
        """
        INSERT INTO encrypted_objects (
            object_id, object_type, format_version, cek_nonce, wrapped_cek,
            payload_nonce, payload_ciphertext, revision, created_at, updated_at
        ) VALUES (?, 'student_subject', 1, ?, ?, ?, ?, 1, '2026-08-01', '2026-08-01')
        """,
        (
            object_id,
            b"",
            b"",
            b"plaintext-json-v1",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ),
    )
    connection.execute(
        "INSERT INTO student_subject_links VALUES (?, ?, ?, 'active', '2026-08-01', '2026-08-01')",
        (subject_id, fingerprint, object_id),
    )


def _build_vault_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE vault_metadata (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE encrypted_objects (
                object_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                format_version INTEGER NOT NULL CHECK (format_version = 1),
                cek_nonce BLOB NOT NULL,
                wrapped_cek BLOB NOT NULL,
                payload_nonce BLOB NOT NULL,
                payload_ciphertext BLOB NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE student_subject_links (
                subject_id TEXT PRIMARY KEY,
                source_fingerprint BLOB NOT NULL UNIQUE,
                payload_object_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # 1. 临时身份档案，可改挂
        _insert_subject(
            connection,
            subject_id="sub-upload-a",
            fingerprint="9|37032129",
            payload={
                "source_student_id": "evidence-upload|9|37032129",
                "display_name": "合成学生甲",
                "class_label": "9",
                "identity_snapshot_at": "2026-08-01",
            },
        )
        # 2. 花名册身份档案，应跳过
        _insert_subject(
            connection,
            subject_id="sub-roster-b",
            fingerprint="9|20250921",
            payload={
                "source_student_id": "21",
                "display_name": "合成学生乙",
                "class_label": "9",
                "identity_snapshot_at": "2026-08-01",
            },
        )
        # 3. 临时身份但花名册查不到，不处理
        _insert_subject(
            connection,
            subject_id="sub-upload-gone",
            fingerprint="9|37032999",
            payload={
                "source_student_id": "evidence-upload|9|37032999",
                "display_name": "已转走学生",
                "class_label": "9",
                "identity_snapshot_at": "2026-08-01",
            },
        )
        # 4. 临时身份 + 目标稳定标识被另一份空档案占用（重复档案），不处理
        _insert_subject(
            connection,
            subject_id="sub-upload-c",
            fingerprint="9|37032131",
            payload={
                "source_student_id": "evidence-upload|9|37032131",
                "display_name": "合成学生丙",
                "class_label": "9",
                "identity_snapshot_at": "2026-08-01",
            },
        )
        _insert_subject(
            connection,
            subject_id="sub-roster-c-dup",
            fingerprint="9|20250922",
            payload={
                "source_student_id": "22",
                "display_name": "合成学生丙",
                "class_label": "9",
                "identity_snapshot_at": "2026-08-01",
            },
        )
        connection.commit()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    vault_db = tmp_path / "vault.db"
    grading_db = tmp_path / "grading.db"
    _build_vault_db(vault_db)
    _build_grading_db(grading_db)
    return vault_db, grading_db


def _link_row(db: Path, subject_id: str) -> sqlite3.Row:
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM student_subject_links WHERE subject_id = ?", (subject_id,)
        ).fetchone()


def _payload(db: Path, subject_id: str) -> tuple[dict[str, object], int]:
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT o.payload_ciphertext, o.revision
            FROM student_subject_links l
            JOIN encrypted_objects o ON o.object_id = l.payload_object_id
            WHERE l.subject_id = ?
            """,
            (subject_id,),
        ).fetchone()
    return json.loads(bytes(row["payload_ciphertext"]).decode("utf-8")), int(row["revision"])


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    vault_db, grading_db = _fixture(tmp_path)

    stats = tool.run(vault_db=vault_db, grading_db=grading_db, apply=False)

    assert stats["links_total"] == 5
    assert stats["links_not_evidence_upload"] == 2
    assert stats["links_rewritten"] == 1
    assert len(stats["links_unmatched"]) == 1
    assert stats["links_unmatched"][0]["display_name"] == "已转走学生"
    assert len(stats["links_conflict"]) == 1
    assert stats["links_conflict"][0]["subject_id"] == "sub-upload-c"

    # dry-run 不写入。
    assert _link_row(vault_db, "sub-upload-a")["source_fingerprint"] == "9|37032129"
    payload, revision = _payload(vault_db, "sub-upload-a")
    assert payload["source_student_id"] == "evidence-upload|9|37032129"
    assert revision == 1


def test_apply_rewrites_identity_and_payload(tmp_path: Path) -> None:
    vault_db, grading_db = _fixture(tmp_path)

    stats = tool.run(vault_db=vault_db, grading_db=grading_db, apply=True)

    assert stats["links_rewritten"] == 1
    # 指纹改挂到花名册稳定标识。
    assert _link_row(vault_db, "sub-upload-a")["source_fingerprint"] == "9|20250920"
    # payload 来源编号更正为花名册来源编号并产生新 revision。
    payload, revision = _payload(vault_db, "sub-upload-a")
    assert payload["source_student_id"] == "20"
    assert payload["display_name"] == "合成学生甲"
    assert payload["identity_snapshot_at"] != "2026-08-01"
    assert revision == 2

    # 花名册身份档案、匹配不到与冲突的行保持原样。
    assert _link_row(vault_db, "sub-roster-b")["source_fingerprint"] == "9|20250921"
    assert _link_row(vault_db, "sub-upload-gone")["source_fingerprint"] == "9|37032999"
    assert _link_row(vault_db, "sub-upload-c")["source_fingerprint"] == "9|37032131"

    # 重复运行幂等：已修复的行按「非临时身份」跳过。
    second = tool.run(vault_db=vault_db, grading_db=grading_db, apply=True)
    assert second["links_rewritten"] == 0
    assert second["links_not_evidence_upload"] == 3


def test_apply_succeeds_after_duplicate_removed(tmp_path: Path) -> None:
    vault_db, grading_db = _fixture(tmp_path)

    # 模拟先删除重复空档案，再运行修复。
    with closing(sqlite3.connect(vault_db)) as connection:
        connection.execute(
            "DELETE FROM student_subject_links WHERE subject_id = 'sub-roster-c-dup'"
        )
        connection.commit()

    stats = tool.run(vault_db=vault_db, grading_db=grading_db, apply=True)

    assert stats["links_rewritten"] == 2
    assert len(stats["links_conflict"]) == 0
    assert _link_row(vault_db, "sub-upload-c")["source_fingerprint"] == "9|20250922"
    payload, _revision = _payload(vault_db, "sub-upload-c")
    assert payload["source_student_id"] == "22"
