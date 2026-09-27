"""题目典型错法（question_error_patterns）读写服务。

错因体系改造 P5：AI 整理/预分析产出的典型错法由考试侧自动写入本表
（``record_auto_patterns``，来源 ``ai_auto``/``ai_pre_analysis`` 等），
教师无需确认；教师可事后用 ``rename_patterns`` 修改错法名/大类
（来源 ``teacher_edit``）。merged/rejected 条目永不复活。
读取侧的 ``list_patterns`` 附带把旧 ``error_type`` 标签换算为
“仅预测”候选展示，不写回标签数据。
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


def _append_occurrence(
    conn: sqlite3.Connection,
    existing: sqlite3.Row,
    occurrence: dict[str, Any] | None,
) -> None:
    """把出现快照追加到既有行；不改动状态、来源、大类与错法名。"""
    if not occurrence:
        return
    row = _row_to_dict(existing)
    occurrences = list(row["occurrences"])
    if occurrence in occurrences:
        return
    occurrences.append(occurrence)
    conn.execute(
        f"UPDATE {_PATTERN_TABLE} SET occurrences_json=?,"
        " updated_at=datetime('now','localtime') WHERE id=?",
        (json.dumps(occurrences, ensure_ascii=False), int(row["id"])),
    )


def record_auto_patterns(
    db_path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    """AI 产出错法自动回挂题库（P5）；只增不改，返回新增行数。

    每条 row：{question_id, category, pattern, explanation, trigger_kind,
    trigger_value, source, occurrence}。
    - option / wrong_answer：同 (题, 触发, 触发值) 已有任意状态行时不插行，
      仅向该行追加 occurrence（confirmed 行优先，否则最小 id）。
    - step / observation：同 (题, 触发, 触发值, 错法名) 已有任意状态行时
      同样不插行、只追加 occurrence。
    - merged / rejected 行不会被复活；表缺失返回 0。
    """
    batch = [row for row in rows or [] if str(row.get("pattern") or "").strip()]
    if not batch:
        return 0

    def work(conn: sqlite3.Connection) -> int:
        if not _table_exists(conn):
            return 0
        inserted = 0
        for item in batch:
            question_id = int(item["question_id"])
            trigger_kind = str(item.get("trigger_kind") or "observation")
            trigger_value = str(item.get("trigger_value") or "")
            pattern = str(item["pattern"]).strip()
            occurrence = item.get("occurrence")
            if trigger_kind in ("option", "wrong_answer"):
                # 触发相同即视为同一错法位：教师改名后的行优先承接出现记录。
                existing = conn.execute(
                    f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                    " AND trigger_kind=? AND trigger_value=?"
                    " ORDER BY CASE WHEN status='confirmed' THEN 0 ELSE 1 END, id",
                    (question_id, trigger_kind, trigger_value),
                ).fetchone()
            else:
                existing = conn.execute(
                    f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                    " AND trigger_kind=? AND trigger_value=? AND pattern=?"
                    " ORDER BY CASE WHEN status='confirmed' THEN 0 ELSE 1 END, id",
                    (question_id, trigger_kind, trigger_value, pattern),
                ).fetchone()
            if existing is not None:
                _append_occurrence(conn, existing, occurrence)
                continue
            occurrences = [occurrence] if occurrence else []
            conn.execute(
                f"INSERT INTO {_PATTERN_TABLE} (question_id, category, pattern,"
                " explanation, trigger_kind, trigger_value, status, source,"
                " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
                " VALUES (?,?,?,?,?,?,'confirmed',?,?,NULL,'',datetime('now','localtime'))",
                (
                    question_id,
                    item.get("category"),
                    pattern,
                    str(item.get("explanation") or ""),
                    trigger_kind,
                    trigger_value,
                    str(item.get("source") or "ai_auto"),
                    json.dumps(occurrences, ensure_ascii=False),
                ),
            )
            inserted += 1
        return inserted

    if connection is not None:
        return work(connection)
    with connect(Path(db_path)) as conn:
        return work(conn)


def rename_patterns(
    db_path: Path,
    *,
    question_ids: Iterable[int],
    old_pattern: str,
    new_pattern: str,
    category: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> int:
    """教师修改错法：旧行标记 merged，新名以 ``teacher_edit`` 来源生效。

    ``new_pattern == old_pattern`` 时仅在原行上更新大类与来源，不新建行。
    返回处理的旧行数；表缺失或无匹配返回 0。
    """
    old = str(old_pattern or "").strip()
    new = str(new_pattern or "").strip()
    if not old or not new:
        raise ValueError("pattern must not be blank")
    ids = sorted({int(qid) for qid in question_ids if qid})
    if not ids:
        return 0

    def work(conn: sqlite3.Connection) -> int:
        if not _table_exists(conn):
            return 0
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id IN ({marks})"
            " AND pattern=? AND status='confirmed' ORDER BY id",
            (*ids, old),
        ).fetchall()
        for row in rows:
            data = _row_to_dict(row)
            if new == old:
                conn.execute(
                    f"UPDATE {_PATTERN_TABLE} SET category=COALESCE(?, category),"
                    " source='teacher_edit', updated_at=datetime('now','localtime')"
                    " WHERE id=?",
                    (category, int(data["id"])),
                )
                continue
            conn.execute(
                f"UPDATE {_PATTERN_TABLE} SET status='merged',"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (int(data["id"]),),
            )
            conn.execute(
                f"INSERT INTO {_PATTERN_TABLE} (question_id, category, pattern,"
                " explanation, trigger_kind, trigger_value, status, source,"
                " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
                " VALUES (?,?,?,?,?,?,'confirmed','teacher_edit',?,NULL,'',"
                "datetime('now','localtime'))"
                " ON CONFLICT(question_id, trigger_kind, trigger_value, pattern)"
                " DO UPDATE SET status='confirmed', category=excluded.category,"
                " explanation=excluded.explanation, source='teacher_edit',"
                " occurrences_json=excluded.occurrences_json,"
                " updated_at=datetime('now','localtime')",
                (
                    int(data["question_id"]),
                    category if category is not None else data["category"],
                    new,
                    data["explanation"],
                    data["trigger_kind"],
                    data["trigger_value"],
                    json.dumps(data["occurrences"], ensure_ascii=False),
                ),
            )
        return len(rows)

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
    "confirmed_index",
    "list_patterns",
    "record_auto_patterns",
    "reject_pattern",
    "rename_patterns",
]
