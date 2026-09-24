"""题目典型错法（question_error_patterns）读写服务。

错因体系改造 P4：本表只存教师确认后的条目；考试侧候选项留在
`.class_analysis` 状态文件中。读取侧的 ``list_patterns`` 附带把旧
``error_type`` 标签换算为“仅预测”候选展示，不写回标签数据。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from question_bank.database.schema import connect

_PATTERN_TABLE = "question_error_patterns"
_PATTERN_COLS = (
    "id", "question_id", "category", "pattern", "explanation",
    "trigger_kind", "trigger_value", "status", "source",
    "occurrences_json", "confirm_token", "confirmed_by", "confirmed_at",
    "created_at", "updated_at",
)


def _row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = {key: row[key] for key in _PATTERN_COLS}
    try:
        occurrences = json.loads(data.get("occurrences_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        occurrences = []
    data["occurrences"] = occurrences if isinstance(occurrences, list) else []
    return data


def _table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_PATTERN_TABLE,),
    ).fetchone() is not None


def list_patterns(
    conn: sqlite3.Connection,
    question_ids: Iterable[int],
    *,
    include_predicted: bool = False,
    statuses: tuple[str, ...] = ("confirmed",),
) -> dict[int, list[dict[str, Any]]]:
    """{question_id: [错法行]}；题库未升级或表缺失时返回空，不报错。"""
    ids = sorted({int(qid) for qid in question_ids if qid})
    result: dict[int, list[dict[str, Any]]] = {qid: [] for qid in ids}
    if not ids or not _table_exists(conn):
        return result
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id IN ({marks})"
        f" AND status IN ({','.join('?' * len(statuses))}) ORDER BY id",
        (*ids, *statuses),
    ).fetchall()
    for row in rows:
        result[int(row["question_id"])].append(_row_to_dict(row))
    if include_predicted:
        for qid, predicted in _legacy_tag_patterns(conn, ids).items():
            existing = {item["pattern"] for item in result[qid]}
            result[qid].extend(
                item for item in predicted if item["pattern"] not in existing
            )
    return result


def _legacy_tag_patterns(
    conn: sqlite3.Connection, question_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """旧 error_type 标签 → 仅预测候选：11 类换算大类，自由文本保留原文。"""
    from backend.error_causes import BANK_ERROR_TYPE_MAP

    marks = ",".join("?" * len(question_ids))
    try:
        rows = conn.execute(
            "SELECT question_id, tag_value FROM question_tags"
            f" WHERE tag_type='error_type' AND question_id IN ({marks})",
            question_ids,
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[int, list[dict[str, Any]]] = {qid: [] for qid in question_ids}
    for row in rows:
        text = str(row["tag_value"] or "").strip()
        if not text:
            continue
        out.setdefault(int(row["question_id"]), []).append({
            "id": None,
            "question_id": int(row["question_id"]),
            "category": BANK_ERROR_TYPE_MAP.get(text),
            "pattern": text,
            "explanation": "",
            "trigger_kind": "observation",
            "trigger_value": "",
            "status": "predicted",
            "source": "legacy_tag",
            "occurrences": [],
        })
    return out


def confirm_pattern(
    db_path: Path,
    *,
    question_id: int,
    category: str | None,
    pattern: str,
    explanation: str = "",
    trigger_kind: str = "observation",
    trigger_value: str = "",
    source: str = "teacher_confirm",
    occurrence: dict[str, Any] | None = None,
    confirm_token: str | None = None,
    confirmed_by: str = "",
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """教师确认写入题库；按 (题, 触发, 错法名) 幂等，重复提交不重复建行。

    同一身份再次确认时更新大类/说明/来源快照并追加出现记录；
    operation_token 仅作审计留痕，幂等由唯一键保证。
    """
    pattern_text = str(pattern or "").strip()
    if not pattern_text:
        raise ValueError("pattern must not be blank")
    if trigger_kind not in ("option", "wrong_answer", "step", "observation"):
        raise ValueError("invalid trigger_kind")

    def work(conn: sqlite3.Connection) -> dict[str, Any]:
        if not _table_exists(conn):
            raise RuntimeError("question_error_patterns table is missing")
        existing = conn.execute(
            f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=? AND trigger_kind=?"
            " AND trigger_value=? AND pattern=?",
            (int(question_id), trigger_kind, str(trigger_value or ""), pattern_text),
        ).fetchone()
        if existing is not None:
            row = _row_to_dict(existing)
            occurrences = list(row["occurrences"])
            if occurrence and occurrence not in occurrences:
                occurrences.append(occurrence)
            conn.execute(
                f"UPDATE {_PATTERN_TABLE} SET category=?, explanation=?, status='confirmed',"
                " source=?, occurrences_json=?, confirm_token=COALESCE(?, confirm_token),"
                " confirmed_by=CASE WHEN ?<>'' THEN ? ELSE confirmed_by END,"
                " confirmed_at=COALESCE(confirmed_at, datetime('now','localtime')),"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (
                    category, str(explanation or ""), source,
                    json.dumps(occurrences, ensure_ascii=False),
                    confirm_token, confirmed_by, confirmed_by,
                    int(row["id"]),
                ),
            )
            return _row_to_dict(conn.execute(
                f"SELECT * FROM {_PATTERN_TABLE} WHERE id=?", (int(row["id"]),),
            ).fetchone())
        occurrences = [occurrence] if occurrence else []
        cursor = conn.execute(
            f"INSERT INTO {_PATTERN_TABLE} (question_id, category, pattern, explanation,"
            " trigger_kind, trigger_value, status, source, occurrences_json,"
            " confirm_token, confirmed_by, confirmed_at)"
            " VALUES (?,?,?,?,?,?,'confirmed',?,?,?,?,datetime('now','localtime'))",
            (
                int(question_id), category, pattern_text, str(explanation or ""),
                trigger_kind, str(trigger_value or ""), source,
                json.dumps(occurrences, ensure_ascii=False),
                confirm_token, confirmed_by,
            ),
        )
        return _row_to_dict(conn.execute(
            f"SELECT * FROM {_PATTERN_TABLE} WHERE id=?", (int(cursor.lastrowid),),
        ).fetchone())

    if connection is not None:
        return work(connection)
    with connect(Path(db_path)) as conn:
        return work(conn)


def reject_pattern(
    db_path: Path,
    *,
    pattern_id: int,
    connection: sqlite3.Connection | None = None,
) -> bool:
    """把已确认条目标记为 rejected（保留记录，不再参与匹配）。"""

    def work(conn: sqlite3.Connection) -> bool:
        if not _table_exists(conn):
            return False
        cursor = conn.execute(
            f"UPDATE {_PATTERN_TABLE} SET status='rejected',"
            " updated_at=datetime('now','localtime') WHERE id=? AND status='confirmed'",
            (int(pattern_id),),
        )
        return cursor.rowcount > 0

    if connection is not None:
        return work(connection)
    with connect(Path(db_path)) as conn:
        return work(conn)


def confirmed_index(
    conn: sqlite3.Connection, question_ids: Iterable[int],
) -> dict[int, dict[str, dict[str, Any]]]:
    """{question_id: {pattern名: 行}}：仅 confirmed，供整理/推荐/页面标注使用。"""
    rows = list_patterns(conn, question_ids, statuses=("confirmed",))
    return {
        qid: {item["pattern"]: item for item in items}
        for qid, items in rows.items()
    }


__all__ = [
    "confirm_pattern",
    "confirmed_index",
    "list_patterns",
    "reject_pattern",
]
