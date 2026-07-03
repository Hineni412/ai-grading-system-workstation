"""批改运行账本的 SQLite 访问：开始/恢复/暂停/记录明细/收尾的原子操作。

与阅卷主库同库；``initialize`` 用 ``CREATE TABLE IF NOT EXISTS`` 兼容直接启动，
发布迁移仍以 ``migrations/grading/002_add_grading_run_ledger.sql`` 为准。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


ITEM_STATUSES = (
    "pending",
    "grading",
    "graded",
    "failed",
    "skipped_existing",
    "skipped_duplicate",
    "conflict",
)


class GradingRunConflictError(RuntimeError):
    """已存在活动运行时再次 begin 抛出。"""


@dataclass(frozen=True, slots=True)
class GradingRun:
    id: int
    run_token: str
    session_id: int
    config_fingerprint: str
    grading_mode: str
    state: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS grading_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_token TEXT NOT NULL UNIQUE,
    session_id INTEGER NOT NULL,
    config_fingerprint TEXT NOT NULL,
    grading_mode TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('running','pause_requested','paused','completed','failed')
    ),
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id)
);
CREATE TABLE IF NOT EXISTS grading_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER,
    student_id INTEGER NOT NULL,
    source_label TEXT NOT NULL,
    paper_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending','grading','graded','failed',
            'skipped_existing','skipped_duplicate','conflict'
        )
    ),
    disposition_reason TEXT,
    result_id INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(run_id, source_label),
    FOREIGN KEY(run_id) REFERENCES grading_runs(id),
    FOREIGN KEY(paper_id) REFERENCES exam_papers(id),
    FOREIGN KEY(student_id) REFERENCES students(id),
    FOREIGN KEY(result_id) REFERENCES session_results(id)
);
CREATE INDEX IF NOT EXISTS idx_grading_runs_session_state
ON grading_runs(session_id, state, id);
CREATE INDEX IF NOT EXISTS idx_grading_run_items_identity
ON grading_run_items(student_id, paper_fingerprint, config_fingerprint, status);
"""


class GradingRunStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        # autocommit 模式，便于显式 BEGIN IMMEDIATE 做并发保护。
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---------- 运行生命周期 ----------
    def begin(self, session_id: int, config_fingerprint: str, grading_mode: str) -> GradingRun:
        run_token = uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = conn.execute(
                    "SELECT id FROM grading_runs "
                    "WHERE session_id = ? AND state IN ('running','pause_requested') LIMIT 1",
                    (session_id,),
                ).fetchone()
                if active is not None:
                    conn.execute("ROLLBACK")
                    raise GradingRunConflictError(
                        f"会话 {session_id} 已有进行中的批改运行，请先暂停或等待其结束"
                    )
                cursor = conn.execute(
                    "INSERT INTO grading_runs (run_token, session_id, config_fingerprint, grading_mode, state) "
                    "VALUES (?, ?, ?, ?, 'running')",
                    (run_token, session_id, config_fingerprint, str(grading_mode)),
                )
                run_id = int(cursor.lastrowid)
                conn.execute("COMMIT")
            except GradingRunConflictError:
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return GradingRun(run_id, run_token, session_id, config_fingerprint, str(grading_mode), "running")

    def control_state(self, run_token: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM grading_runs WHERE run_token = ?", (run_token,)
            ).fetchone()
        return row["state"] if row is not None else None

    def fail_active_runs(self, session_id: int) -> int:
        """把该会话遗留的 running/pause_requested 运行标为 failed（进程崩溃后的清理）。

        不触碰 paused 运行，避免影响正常的暂停-恢复。返回被清理的数量。
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE grading_runs SET state = 'failed', "
                "finished_at = datetime('now','localtime'), "
                "updated_at = datetime('now','localtime') "
                "WHERE session_id = ? AND state IN ('running','pause_requested')",
                (session_id,),
            )
            return cursor.rowcount

    def request_pause(self, session_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE grading_runs SET state = 'pause_requested', "
                "updated_at = datetime('now','localtime') "
                "WHERE session_id = ? AND state = 'running'",
                (session_id,),
            )
            return cursor.rowcount > 0

    def finish(self, run_token: str, state: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE grading_runs SET state = ?, "
                "finished_at = datetime('now','localtime'), "
                "updated_at = datetime('now','localtime') "
                "WHERE run_token = ?",
                (state, run_token),
            )

    def resume(self, session_id: int, config_fingerprint: str, grading_mode: str) -> GradingRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM grading_runs WHERE session_id = ? AND state = 'paused' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["config_fingerprint"] != config_fingerprint
                or row["grading_mode"] != str(grading_mode)
            ):
                return None
            conn.execute(
                "UPDATE grading_runs SET state = 'running', "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (int(row["id"]),),
            )
        return GradingRun(
            int(row["id"]),
            row["run_token"],
            session_id,
            row["config_fingerprint"],
            row["grading_mode"],
            "running",
        )

    def latest(self, session_id: int) -> GradingRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM grading_runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return GradingRun(
            int(row["id"]),
            row["run_token"],
            int(row["session_id"]),
            row["config_fingerprint"],
            row["grading_mode"],
            row["state"],
        )

    def get_run(self, run_id: int) -> GradingRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM grading_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return GradingRun(
            int(row["id"]),
            row["run_token"],
            int(row["session_id"]),
            row["config_fingerprint"],
            row["grading_mode"],
            row["state"],
        )

    # ---------- 明细项 ----------
    def add_item(
        self,
        run_id: int,
        *,
        source_label: str,
        student_id: int,
        paper_fingerprint: str,
        config_fingerprint: str,
        status: str,
        paper_id: int | None = None,
        disposition_reason: str | None = None,
        result_id: int | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO grading_run_items "
                "(run_id, paper_id, student_id, source_label, paper_fingerprint, "
                " config_fingerprint, status, disposition_reason, result_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    paper_id,
                    student_id,
                    source_label,
                    paper_fingerprint,
                    config_fingerprint,
                    status,
                    disposition_reason,
                    result_id,
                ),
            )
            return int(cursor.lastrowid)

    def mark_grading(self, run_item_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE grading_run_items SET status = 'grading', "
                "attempt_count = attempt_count + 1, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (run_item_id,),
            )

    def set_item_status(
        self,
        run_item_id: int,
        status: str,
        *,
        result_id: int | None = None,
        disposition_reason: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE grading_run_items SET status = ?, "
                "result_id = COALESCE(?, result_id), "
                "disposition_reason = COALESCE(?, disposition_reason), "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (status, result_id, disposition_reason, run_item_id),
            )

    def counts(self, run_id: int) -> dict[str, int]:
        result = {status: 0 for status in ITEM_STATUSES}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM grading_run_items "
                "WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall()
        for row in rows:
            result[row["status"]] = int(row["c"])
        result["skipped"] = result["skipped_existing"] + result["skipped_duplicate"]
        return result

    def pending_items(self, run_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM grading_run_items WHERE run_id = ? AND status = 'pending' "
                "ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def conflict_items(self, run_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM grading_run_items WHERE run_id = ? AND status = 'conflict' "
                "ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def graded_identities(self, session_id: int) -> list[dict]:
        """返回该会话历史上已成功批改的三元身份，供跨运行自动跳过判断。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT i.student_id, i.paper_fingerprint, i.config_fingerprint, i.result_id "
                "FROM grading_run_items i JOIN grading_runs r ON r.id = i.run_id "
                "WHERE r.session_id = ? AND i.status = 'graded'",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]
