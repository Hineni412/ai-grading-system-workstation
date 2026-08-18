from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.class_teacher.roster_ref import legacy_opaque_ref
from update_tools import repair_class_teacher_student_refs as tool


_VMK = b"class-teacher-plaintext-debug-key"


def _legacy_ref(source_key: str) -> str:
    return legacy_opaque_ref(_VMK, source_key)


def _build_grading_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [
                (101, "A001", "合成学生甲", "一班"),
                (102, "", "合成学生乙", "一班"),
            ],
        )
        connection.commit()


def _build_vault_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE encrypted_objects (
                object_id TEXT PRIMARY KEY,
                payload_ciphertext BLOB NOT NULL
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
        payloads = {
            "obj-old-1": {"source_student_id": "10", "display_name": "合成学生甲", "class_label": "一班"},
            "obj-old-2": {"source_student_id": "11", "display_name": "合成学生乙", "class_label": "一班"},
            "obj-gone": {"source_student_id": "12", "display_name": "已转走学生", "class_label": "一班"},
            "obj-stable": {"source_student_id": "13", "display_name": "合成学生丁", "class_label": "一班"},
        }
        for object_id, payload in payloads.items():
            connection.execute(
                "INSERT INTO encrypted_objects VALUES (?, ?)",
                (object_id, json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            )
        links = [
            ("subject-old-1", b"\x01" * 32, "obj-old-1"),
            ("subject-old-2", b"\x02" * 32, "obj-old-2"),
            ("subject-gone", b"\x03" * 32, "obj-gone"),
            # 已是新格式稳定标识的行应跳过。
            ("subject-stable", "一班|A009", "obj-stable"),
        ]
        for subject_id, fingerprint, object_id in links:
            connection.execute(
                "INSERT INTO student_subject_links VALUES (?, ?, ?, 'active', '2026-08-01', '2026-08-01')",
                (subject_id, fingerprint, object_id),
            )
        connection.commit()


def _build_work_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE intake_drafts (
                draft_id TEXT PRIMARY KEY,
                subject_refs_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE intake_handoffs (
                handoff_id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                adoption_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        drafts = [
            # 旧编号可映射 → 重写为新引用
            (
                "draft-mappable",
                [{"kind": "student", "id": _legacy_ref("10"), "revision": "old-rev"}],
                "open",
            ),
            # 旧编号无法映射 → 标记 stale
            (
                "draft-unmappable",
                [{"kind": "student", "id": "f" * 64, "revision": "x"}],
                "open",
            ),
            # 已采用的草稿不动
            (
                "draft-adopted",
                [{"kind": "student", "id": _legacy_ref("11"), "revision": "old-rev"}],
                "adopted",
            ),
            # 新格式引用不动
            (
                "draft-stable",
                [{"kind": "student", "id": "一班|A001", "revision": "r1"}],
                "open",
            ),
        ]
        for draft_id, refs, state in drafts:
            connection.execute(
                "INSERT INTO intake_drafts VALUES (?, ?, ?, '2026-08-01')",
                (draft_id, json.dumps(refs, ensure_ascii=False), state),
            )
            connection.execute(
                "INSERT INTO intake_handoffs VALUES (?, ?, 'pending', '2026-08-01')",
                (f"handoff-{draft_id}", draft_id),
            )
        connection.commit()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault_db = tmp_path / "vault.db"
    work_db = tmp_path / "work.db"
    grading_db = tmp_path / "grading.db"
    _build_vault_db(vault_db)
    _build_work_db(work_db)
    _build_grading_db(grading_db)
    return vault_db, work_db, grading_db


def _fingerprint(db: Path, subject_id: str) -> object:
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(
            "SELECT source_fingerprint FROM student_subject_links WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()[0]


def _draft(db: Path, draft_id: str) -> sqlite3.Row:
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM intake_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    vault_db, work_db, grading_db = _fixture(tmp_path)

    link_stats, draft_stats = tool.run(
        vault_db=vault_db, work_db=work_db, grading_db=grading_db, apply=False
    )

    assert link_stats["links_total"] == 4
    assert link_stats["links_already_stable"] == 1
    assert link_stats["links_rewritten"] == 2
    assert len(link_stats["links_unmatched"]) == 1
    assert link_stats["links_unmatched"][0]["display_name"] == "已转走学生"
    assert draft_stats["drafts_scanned"] == 3
    assert draft_stats["drafts_rewritten"] == 1
    assert draft_stats["drafts_marked_stale"] == 1
    assert draft_stats["refs_rewritten"] == 1

    # dry-run 不写入。
    assert _fingerprint(vault_db, "subject-old-1") == b"\x01" * 32
    assert _draft(work_db, "draft-mappable")["state"] == "open"
    assert json.loads(_draft(work_db, "draft-mappable")["subject_refs_json"])[0]["id"] == _legacy_ref("10")


def test_apply_rewrites_links_and_revives_drafts(tmp_path: Path) -> None:
    vault_db, work_db, grading_db = _fixture(tmp_path)

    link_stats, draft_stats = tool.run(
        vault_db=vault_db, work_db=work_db, grading_db=grading_db, apply=True
    )

    assert link_stats["links_rewritten"] == 2
    # 学号优先与姓名回退两种稳定标识。
    assert _fingerprint(vault_db, "subject-old-1") == "一班|A001"
    assert _fingerprint(vault_db, "subject-old-2") == "一班|合成学生乙"
    # 匹配不到的行保持原样。
    assert _fingerprint(vault_db, "subject-gone") == b"\x03" * 32

    revived = json.loads(_draft(work_db, "draft-mappable")["subject_refs_json"])
    assert revived[0]["id"] == "一班|A001"
    # revision 重写为当前花名册内容哈希，不再是旧值。
    assert revived[0]["revision"] != "old-rev"
    assert len(revived[0]["revision"]) == 64
    assert _draft(work_db, "draft-mappable")["state"] == "open"

    stale = _draft(work_db, "draft-unmappable")
    assert stale["state"] == "stale"
    with closing(sqlite3.connect(work_db)) as connection:
        handoff_state = connection.execute(
            "SELECT adoption_state FROM intake_handoffs WHERE draft_id = 'draft-unmappable'"
        ).fetchone()[0]
    assert handoff_state == "stale"

    # 已采用草稿与新格式引用草稿不变。
    adopted = json.loads(_draft(work_db, "draft-adopted")["subject_refs_json"])
    assert adopted[0]["id"] == _legacy_ref("11")
    stable = json.loads(_draft(work_db, "draft-stable")["subject_refs_json"])
    assert stable[0]["id"] == "一班|A001"

    # 重复运行幂等：已修复的行按「已是稳定标识」跳过。
    second_links, second_drafts = tool.run(
        vault_db=vault_db, work_db=work_db, grading_db=grading_db, apply=True
    )
    assert second_links["links_rewritten"] == 0
    assert second_links["links_already_stable"] == 3
    assert second_drafts["drafts_rewritten"] == 0
    assert second_drafts["drafts_marked_stale"] == 0
