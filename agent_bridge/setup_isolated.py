# -*- coding: utf-8 -*-
"""Build the isolated comparison sandbox for the session-4 re-grade.

Creates ``D:\\week3_regrade_sandbox`` OUTSIDE the project:

    data/            <- isolated data root (AI_GRADING_WORKTREE_DATA_DIR)
        databases/   <- consistent copies of grading_system.db / question_bank.db
        exams/session_4/
        templates/session_4/
        config/
    bridge/session_4/  <- request/response work dirs for the bridge client

All absolute paths stored inside the sandbox DB / work-dir JSON files are
rewritten from the real ``user_data`` root to the sandbox data root so the
pipeline's controlled-path check resolves them inside the sandbox only.
Nothing under the real ``user_data`` is modified.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DATA = ROOT / "user_data"
SANDBOX = Path(os.environ.get("AI_GRADING_SANDBOX_DIR", r"D:\week3_regrade_sandbox"))
DATA = SANDBOX / "data"
BRIDGE = SANDBOX / "bridge" / "session_4"

OLD_ROOTS = [
    str(SRC_DATA).replace("/", "\\"),                       # D:\...\user_data
    str(SRC_DATA).replace("\\", "/"),                       # D:/.../user_data
    str(SRC_DATA).replace("\\", "\\\\"),                    # JSON-escaped
]
NEW_ROOTS = [
    str(DATA).replace("/", "\\"),
    str(DATA).replace("\\", "/"),
    str(DATA).replace("\\", "\\\\"),
]


def _rewrite_text(text: str) -> str:
    for old, new in zip(OLD_ROOTS, NEW_ROOTS):
        text = text.replace(old, new)
    return text


def _backup_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _rewrite_db_paths(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    changed = 0
    try:
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'").fetchall()]
        for table in tables:
            cols = [r[1] for r in conn.execute(
                f'pragma table_info("{table}")').fetchall()]
            text_cols = [c for c in cols]
            pk = [r[1] for r in conn.execute(
                f'pragma table_info("{table}")').fetchall() if r[5]]
            if not pk:
                continue
            rows = conn.execute(f'select * from "{table}"').fetchall()
            for row in rows:
                updates = {}
                for col in text_cols:
                    val = row[col]
                    if isinstance(val, str) and "user_data" in val:
                        new = _rewrite_text(val)
                        if new != val:
                            updates[col] = new
                if updates:
                    sets = ", ".join(f'"{c}"=?' for c in updates)
                    where = " and ".join(f'"{c}"=?' for c in pk)
                    conn.execute(
                        f'update "{table}" set {sets} where {where}',
                        [*updates.values(), *[row[c] for c in pk]],
                    )
                    changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed


def _rewrite_json_files(root: Path) -> int:
    changed = 0
    for p in root.rglob("*.json"):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = _rewrite_text(text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def main() -> int:
    if DATA.exists():
        print(f"sandbox data root already exists: {DATA} (rebuilding in place)")
    (SANDBOX / "bridge" / "session_4" / "requests").mkdir(parents=True, exist_ok=True)
    (SANDBOX / "bridge" / "session_4" / "responses").mkdir(parents=True, exist_ok=True)

    # --- databases via online backup (app may hold the real DB open) ---
    for name in ("grading_system.db", "question_bank.db"):
        src = SRC_DATA / "databases" / name
        dst = DATA / "databases" / name
        print(f"backup {name} -> {dst}")
        _backup_db(src, dst)

    # --- directory copies ---
    for rel in ("exams/session_4", "templates/session_4", "config"):
        src = SRC_DATA / rel
        dst = DATA / rel
        print(f"copy {rel} -> {dst}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    # --- rewrite stored absolute paths inside the sandbox copies ---
    n_db = _rewrite_db_paths(DATA / "databases" / "grading_system.db")
    n_json = _rewrite_json_files(DATA / "templates" / "session_4")
    n_json += _rewrite_json_files(DATA / "exams" / "session_4")
    print(f"rewrote paths: db_rows={n_db} json_files={n_json}")

    # --- sanity: no remaining real-user_data references in sandbox db ---
    conn = sqlite3.connect(str(DATA / "databases" / "grading_system.db"))
    leftover = 0
    try:
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'").fetchall()]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'pragma table_info("{table}")').fetchall()]
            for col in cols:
                cnt = conn.execute(
                    f'select count(*) from "{table}" where "{col}" like ?',
                    ("%user_data%",),
                ).fetchone()[0]
                leftover += cnt
    finally:
        conn.close()
    print(f"leftover real-path refs in sandbox db: {leftover}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
