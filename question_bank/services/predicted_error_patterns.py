"""整题打标签产出的"仅预测"典型错法候选存储（八上重打方案 3.10）。

与 ``error_pattern_service`` 分工：本模块只负责整题打标签流程写出的
``source='ai_predicted'`` 候选行，状态恒为 ``candidate``，不作为学生证据，
不参与已确认错法复用；教师确认的错法读写仍在 error_pattern_service。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from question_bank.models.tag_schema import PREDICTED_TRIGGER_KINDS

_PATTERN_TABLE = "question_error_patterns"
PREDICTED_SOURCE = "ai_predicted"


def _table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_PATTERN_TABLE,),
    ).fetchone() is not None


def record_predicted_patterns(
    conn: sqlite3.Connection,
    question_id: int,
    patterns: Iterable[dict[str, Any]],
) -> int:
    """整题重打时整体替换本题的 ai_predicted 候选行，返回新增行数。

    - 已确认/教师调整/驳回以及其他来源的条目一律不动。
    - 同一 (trigger_kind, trigger_value, pattern) 若已被非预测行占用，
      不重复建行（教师条目优先；表上有对应 UNIQUE 约束）。
    - 表缺失（未迁移）返回 0，不报错。
    """

    if not _table_exists(conn):
        return 0
    qid = int(question_id)
    conn.execute(
        f"DELETE FROM {_PATTERN_TABLE} WHERE question_id=? AND source=?",
        (qid, PREDICTED_SOURCE),
    )
    inserted = 0
    for item in patterns or []:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "").strip()
        if not pattern:
            continue
        trigger_kind = str(item.get("trigger_kind") or "").strip().casefold()
        if trigger_kind not in PREDICTED_TRIGGER_KINDS:
            trigger_kind = "observation"
        trigger_value = str(item.get("trigger_value") or "").strip()
        if trigger_kind == "observation":
            trigger_value = ""
        existing = conn.execute(
            f"SELECT id, source, status FROM {_PATTERN_TABLE} WHERE question_id=?"
            " AND trigger_kind=? AND trigger_value=? AND pattern=?",
            (qid, trigger_kind, trigger_value, pattern),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            f"INSERT INTO {_PATTERN_TABLE} (question_id, category, pattern,"
            " explanation, trigger_kind, trigger_value, status, source,"
            " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
            " VALUES (?,?,?,?,?,?,'candidate',?,'[]',NULL,'',"
            "datetime('now','localtime'))",
            (
                qid,
                str(item.get("category") or "") or None,
                pattern,
                str(item.get("explanation") or ""),
                trigger_kind,
                trigger_value,
                PREDICTED_SOURCE,
            ),
        )
        inserted += 1
    return inserted


__all__ = ["PREDICTED_SOURCE", "record_predicted_patterns"]
