"""整题分析写入预测错法；保留教师调整和已有学生作答证据。"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from question_bank.models.tag_schema import PREDICTED_TRIGGER_KINDS
from question_bank.services.error_pattern_service import _content_fingerprint, _stale_prediction

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
    """整题重打时替换无实证的预测候选，返回新增行数。

    - 已确认、教师调整、驳回与合并记录保留；教师驳回不会被重打恢复。
    - 选项/错误答案同一触发位只保留已有条目，教师修改优先。
    - 表缺失（未迁移）返回 0，不报错。
    """

    if not _table_exists(conn):
        return 0
    qid = int(question_id)
    question = conn.execute(
        "SELECT question_text, answer_text, question_type FROM questions WHERE id=?", (qid,),
    ).fetchone()
    if question is None:
        return 0
    content_token = "content:" + _content_fingerprint(
        question["question_text"], question["answer_text"]
    )
    from backend.error_patterns import (
        extract_canonical_option, normalize_option_answer, parse_option_letters,
    )
    is_choice = str(question["question_type"] or "").strip() in {
        "choice", "single_choice", "选择题", "单选题",
    }
    option_letters = set(parse_option_letters(question["question_text"])) if is_choice else set()
    correct_option = (
        normalize_option_answer(question["answer_text"])
        or extract_canonical_option(question["answer_text"])
    ) if is_choice else ""
    conn.execute(
        f"UPDATE {_PATTERN_TABLE} SET status='confirmed' WHERE question_id=?"
        " AND source=? AND status='candidate' AND occurrences_json <> '[]'",
        (qid, PREDICTED_SOURCE),
    )
    conn.execute(
        f"DELETE FROM {_PATTERN_TABLE} WHERE question_id=? AND source=?"
        " AND status='candidate' AND occurrences_json='[]'",
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
        if trigger_kind == "option" and (
            not is_choice or trigger_value.upper() not in option_letters
            or trigger_value.upper() == correct_option
        ):
            continue
        if trigger_kind == "option":
            trigger_value = trigger_value.upper()
        if trigger_kind in ("option", "wrong_answer"):
            matches = conn.execute(
                f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                " AND trigger_kind=? AND trigger_value=?",
                (qid, trigger_kind, trigger_value),
            ).fetchall()
        else:
            matches = conn.execute(
                f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                " AND trigger_kind=? AND trigger_value=? AND pattern=?",
                (qid, trigger_kind, trigger_value, pattern),
            ).fetchall()
        if any(not _stale_prediction(conn, row) for row in matches):
            continue
        # 内容修改后，旧题上确实出现过的同名错法不能删除；新分析明确再次
        # 给出同一名称时，更新其内容指纹并保留原出现证据。
        reusable = next((
            row for row in matches
            if row["source"] == PREDICTED_SOURCE
            and row["status"] == "confirmed"
            and row["pattern"] == pattern
            and _stale_prediction(conn, row)
        ), None)
        if reusable is not None:
            conn.execute(
                f"UPDATE {_PATTERN_TABLE} SET confirm_token=?, category=?,"
                " explanation=?, updated_at=datetime('now','localtime') WHERE id=?",
                (
                    content_token, str(item.get("category") or "") or None,
                    str(item.get("explanation") or ""), int(reusable["id"]),
                ),
            )
            continue
        cursor = conn.execute(
            f"INSERT OR IGNORE INTO {_PATTERN_TABLE} (question_id, category, pattern,"
            " explanation, trigger_kind, trigger_value, status, source,"
            " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
            " VALUES (?,?,?,?,?,?,'candidate',?,'[]',?,'',"
            "datetime('now','localtime'))",
            (
                qid,
                str(item.get("category") or "") or None,
                pattern,
                str(item.get("explanation") or ""),
                trigger_kind,
                trigger_value,
                PREDICTED_SOURCE,
                content_token,
            ),
        )
        inserted += cursor.rowcount
    return inserted


__all__ = ["PREDICTED_SOURCE", "record_predicted_patterns"]
