"""题目典型错法（question_error_patterns）读写服务。

题目分析写入无作答证据的预测候选；考试侧考后整理写入实际错法，
只在有对应作答时附出现记录。教师可修改或驳回；驳回条目不会复活。
读取侧仍可把旧 ``error_type`` 标签换算为仅预测候选展示。
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


def pattern_preference(row: sqlite3.Row | dict[str, Any]) -> tuple[int, int, int, int]:
    """同一触发位的生效顺序：活动行、教师调整、已发生、较新记录。"""
    return (
        0 if row["status"] in ("confirmed", "candidate") else 1,
        0 if row["source"] == "teacher_edit" else 1,
        0 if row["status"] == "confirmed" else 1,
        -int(row["id"] or 0),
    )


def preferred_active_patterns(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """同题同选项/错误答案只保留当前生效错法，其他触发类型照常显示。"""
    active = [row for row in rows if row["status"] in ("confirmed", "candidate")]
    chosen: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in active:
        if row["trigger_kind"] not in ("option", "wrong_answer"):
            continue
        key = (int(row["question_id"]), row["trigger_kind"], row["trigger_value"])
        if key not in chosen or pattern_preference(row) < pattern_preference(chosen[key]):
            chosen[key] = row
    return [
        row for row in active
        if row["trigger_kind"] not in ("option", "wrong_answer")
        or chosen[(int(row["question_id"]), row["trigger_kind"], row["trigger_value"])]["id"] == row["id"]
    ]


def current_pattern_for_snapshot(
    conn: sqlite3.Connection,
    *,
    question_id: int,
    old_pattern: str,
    trigger_kind: str,
    trigger_value: str,
) -> dict[str, Any] | None:
    """由场次旧名称沿教师改名记录找到这条错法的当前版本。"""
    if not _table_exists(conn):
        return None
    original = conn.execute(
        f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=? AND pattern=?"
        " AND trigger_kind=? AND trigger_value=? ORDER BY id DESC",
        (int(question_id), old_pattern, trigger_kind, trigger_value),
    ).fetchone()
    if original is None:
        return None
    current = original
    seen: set[int] = set()
    followed = False
    while current["status"] == "merged" and int(current["id"]) not in seen:
        seen.add(int(current["id"]))
        successor = conn.execute(
            f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
            " AND trigger_kind=? AND trigger_value=?"
            " AND confirm_token=? AND source='teacher_edit' ORDER BY id DESC LIMIT 1",
            (
                int(question_id), trigger_kind, trigger_value,
                f"renamed_from:{int(current['id'])}",
            ),
        ).fetchone()
        if successor is None:
            break
        followed = True
        current = successor
    if current["status"] in ("confirmed", "candidate") and not _stale_prediction(conn, current):
        return _row_to_dict(current)
    if original["status"] != "merged" or followed:
        return None
    # 兼容此前未记录改名来源的行；只有唯一候选时才按同一触发位承接。
    candidates = conn.execute(
        f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
        " AND trigger_kind=? AND trigger_value=?"
        " AND status IN ('confirmed','candidate') ORDER BY id",
        (int(question_id), trigger_kind, trigger_value),
    ).fetchall()
    candidates = [row for row in candidates if not _stale_prediction(conn, row)]
    matching = [
        row for row in candidates
        if row["explanation"] == original["explanation"]
        and row["occurrences_json"] == original["occurrences_json"]
    ]
    has_context = bool(original["explanation"]) or original["occurrences_json"] != "[]"
    return _row_to_dict(matching[0]) if has_context and len(matching) == 1 else None


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
    if not ids or not statuses or not _table_exists(conn):
        return result
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id IN ({marks})"
        f" AND status IN ({','.join('?' * len(statuses))}) ORDER BY id",
        (*ids, *statuses),
    ).fetchall()
    for row in rows:
        if _stale_prediction(conn, row):
            continue
        result[int(row["question_id"])].append(_row_to_dict(row))
    if include_predicted:
        for qid, predicted in _legacy_tag_patterns(conn, ids).items():
            existing = {item["pattern"] for item in result[qid]}
            result[qid].extend(
                item for item in predicted if item["pattern"] not in existing
            )
    return result


def _content_fingerprint(question_text: Any, answer_text: Any) -> str:
    import hashlib

    payload = json.dumps(
        [str(question_text or ""), str(answer_text or "")],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stale_prediction(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    token = str(row["confirm_token"] or "")
    if row["source"] != "ai_predicted" or not token.startswith("content:"):
        return False
    question = conn.execute(
        "SELECT question_text, answer_text FROM questions WHERE id=?",
        (int(row["question_id"]),),
    ).fetchone()
    return question is None or token != "content:" + _content_fingerprint(
        question["question_text"], question["answer_text"]
    )


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
    """AI 产出错法自动回挂题库，返回新增行数。

    每条 row：{question_id, category, pattern, explanation, trigger_kind,
    trigger_value, source, occurrence}。
    - option / wrong_answer：同 (题, 触发, 触发值) 已有任意状态行时不插行，
      追加真实作答；预测与实际名称不同且未被教师调整时使用实际名称。
    - step / observation：同 (题, 触发, 触发值, 错法名) 已有任意状态行时
      同样不插行，按实际作答追加 occurrence。
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
                matches = conn.execute(
                    f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                    " AND trigger_kind=? AND trigger_value=?"
                    " ORDER BY CASE WHEN status='confirmed' THEN 0 ELSE 1 END, id",
                    (question_id, trigger_kind, trigger_value),
                ).fetchall()
            else:
                matches = conn.execute(
                    f"SELECT * FROM {_PATTERN_TABLE} WHERE question_id=?"
                    " AND trigger_kind=? AND trigger_value=? AND pattern=?"
                    " ORDER BY CASE WHEN status='confirmed' THEN 0 ELSE 1 END, id",
                    (question_id, trigger_kind, trigger_value, pattern),
                ).fetchall()
            for stale in matches:
                if _stale_prediction(conn, stale) and (
                    stale["status"] == "candidate"
                    and not _row_to_dict(stale)["occurrences"]
                ):
                    conn.execute(
                        f"DELETE FROM {_PATTERN_TABLE} WHERE id=?", (int(stale["id"]),)
                    )
            current_matches = [row for row in matches if not _stale_prediction(conn, row)]
            existing = min(current_matches, key=pattern_preference) if current_matches else None
            if existing is not None:
                if existing["status"] in ("rejected", "merged"):
                    continue
                _append_occurrence(conn, existing, occurrence)
                if existing["status"] == "candidate" and occurrence:
                    if existing["source"] == "ai_predicted" and existing["pattern"] != pattern:
                        conn.execute(
                            f"UPDATE {_PATTERN_TABLE} SET status='confirmed',"
                            " pattern=?, category=?, explanation=?, source=?,"
                            " updated_at=datetime('now','localtime') WHERE id=?",
                            (
                                pattern, item.get("category"),
                                str(item.get("explanation") or ""),
                                str(item.get("source") or "ai_auto"), int(existing["id"]),
                            ),
                        )
                    else:
                        conn.execute(
                            f"UPDATE {_PATTERN_TABLE} SET status='confirmed',"
                            " updated_at=datetime('now','localtime') WHERE id=?",
                            (int(existing["id"]),),
                        )
                continue
            occurrences = [occurrence] if occurrence else []
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {_PATTERN_TABLE} (question_id, category, pattern,"
                " explanation, trigger_kind, trigger_value, status, source,"
                " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL,'',datetime('now','localtime'))",
                (
                    question_id,
                    item.get("category"),
                    pattern,
                    str(item.get("explanation") or ""),
                    trigger_kind,
                    trigger_value,
                    "confirmed" if occurrence else "candidate",
                    str(item.get("source") or "ai_auto"),
                    json.dumps(occurrences, ensure_ascii=False),
                ),
            )
            inserted += cursor.rowcount
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
    pattern_id: int | None = None,
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
            " AND pattern=? AND status IN ('confirmed','candidate')"
            " AND (? IS NULL OR id=?) ORDER BY id",
            (*ids, old, pattern_id, pattern_id),
        ).fetchall()
        for row in rows:
            data = _row_to_dict(row)
            if new == old:
                conn.execute(
                    f"UPDATE {_PATTERN_TABLE} SET category=COALESCE(?, category),"
                    " status='confirmed', source='teacher_edit',"
                    " updated_at=datetime('now','localtime')"
                    " WHERE id=?",
                    (category, int(data["id"])),
                )
                continue
            conflict = conn.execute(
                f"SELECT status FROM {_PATTERN_TABLE} WHERE question_id=?"
                " AND trigger_kind=? AND trigger_value=? AND pattern=?",
                (int(data["question_id"]), data["trigger_kind"],
                 data["trigger_value"], new),
            ).fetchone()
            if conflict is not None and conflict["status"] == "rejected":
                raise ValueError("the requested pattern was previously rejected")
            conn.execute(
                f"UPDATE {_PATTERN_TABLE} SET status='merged',"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (int(data["id"]),),
            )
            conn.execute(
                f"INSERT INTO {_PATTERN_TABLE} (question_id, category, pattern,"
                " explanation, trigger_kind, trigger_value, status, source,"
                " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
                " VALUES (?,?,?,?,?,?,'confirmed','teacher_edit',?,?,'',"
                "datetime('now','localtime'))"
                " ON CONFLICT(question_id, trigger_kind, trigger_value, pattern)"
                " DO UPDATE SET status='confirmed', category=excluded.category,"
                " explanation=excluded.explanation, source='teacher_edit',"
                " occurrences_json=excluded.occurrences_json,"
                " confirm_token=excluded.confirm_token,"
                " updated_at=datetime('now','localtime')",
                (
                    int(data["question_id"]),
                    category if category is not None else data["category"],
                    new,
                    data["explanation"],
                    data["trigger_kind"],
                    data["trigger_value"],
                    json.dumps(data["occurrences"], ensure_ascii=False),
                    f"renamed_from:{int(data['id'])}",
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
    question_id: int | None = None,
    connection: sqlite3.Connection | None = None,
) -> bool:
    """把活动条目标记为 rejected（保留记录，不再参与匹配）。"""

    def work(conn: sqlite3.Connection) -> bool:
        if not _table_exists(conn):
            return False
        row = conn.execute(
            f"SELECT question_id, trigger_kind, trigger_value FROM {_PATTERN_TABLE}"
            " WHERE id=? AND status IN ('confirmed','candidate')",
            (int(pattern_id),),
        ).fetchone()
        if row is None or (question_id is not None and int(row["question_id"]) != int(question_id)):
            return False
        if row["trigger_kind"] in ("option", "wrong_answer"):
            cursor = conn.execute(
                f"UPDATE {_PATTERN_TABLE} SET status='rejected',"
                " updated_at=datetime('now','localtime') WHERE question_id=?"
                " AND trigger_kind=? AND trigger_value=?"
                " AND status IN ('confirmed','candidate')",
                (int(row["question_id"]), row["trigger_kind"], row["trigger_value"]),
            )
            return cursor.rowcount > 0
        cursor = conn.execute(
            f"UPDATE {_PATTERN_TABLE} SET status='rejected',"
            " updated_at=datetime('now','localtime') WHERE id=?"
            " AND status IN ('confirmed','candidate')",
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
