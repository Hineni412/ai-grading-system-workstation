"""标准难度：逐小问 SOLO 与难度特征的独立评估存储。

产品规则见 docs/product/KNOWLEDGE_AND_TRAINING.md“唯一当前掌握度”：

- 存特征而不只存结果；公式权重固定，日后可按考试数据在本机重算。
- 整题难度取最难小问，不取平均。``questions.difficulty`` 由
  ``save_tag_analysis`` 按公式值原样写入（一位小数，如 "8.8"）。
  逐小问难度也只有这一个来源：掌握度、推荐与组卷的小问难度都读这里的
  公式分，不再有独立的模型小问估计档案。
- 记录公式版本号与题目内容指纹；题目内容变化后标记"需重评"。

表结构见 ``migrations/question_bank/043_add_question_part_difficulty_features.sql``。
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from question_bank.database.schema import connect
from question_bank.models.tag_schema import PART_FEATURE_ORDER


STANDARD_DIFFICULTY_FORMULA_VERSION = "std-difficulty-v1"

# 公式（固定）：raw = (solo−1)×1.0 + reasoning×1.0 + computation×0.75
#   + context×0.5 + hidden×1.0 + cases×0.75 + param_dynamic×0.5
#   + trap×0.5 + knowledge×0.5；满分 13；小问难度 = 1 + 9×raw/13。
_FORMULA_WEIGHTS: dict[str, float] = {
    "solo": 1.0,      # 计入 (solo - 1)
    "reasoning": 1.0,
    "computation": 0.75,
    "context": 0.5,
    "hidden": 1.0,
    "cases": 0.75,
    "param_dynamic": 0.5,
    "trap": 0.5,
    "knowledge": 0.5,
}
_FORMULA_RAW_MAX = 13.0

_TABLE = "question_part_difficulty_features"
_FEATURE_JSON_KEYS = (
    "solo",
    "reasoning",
    "computation",
    "context",
    "context_kind",
    "hidden",
    "cases",
    "param_dynamic",
    "trap",
    "knowledge",
    "evidence",
)

_FINGERPRINT_FIELDS = (
    "question_text",
    "answer_text",
    "question_type",
    "has_images",
    "image_paths",
)


def feature_raw(features: Mapping[str, Any]) -> float:
    """单个小问特征的公式 raw 分（0—13）。"""

    raw = 0.0
    for name, weight in _FORMULA_WEIGHTS.items():
        value = int(features.get(name) or 0)
        if name == "solo":
            value -= 1
        raw += value * weight
    return raw


def formula_score(raw: float) -> float:
    """raw 分换算成 1—10 的小问公式难度。"""

    return round(1.0 + 9.0 * float(raw) / _FORMULA_RAW_MAX, 1)


def difficulty_level(value: object) -> int | None:
    """把小数难度归入最近的整数档（半上取整，存储值不改动）。

    只用于“归入哪一档”的分类语义：6.4→6、6.5→7、7.5→8、10.0→10；
    范围、上限、距离与排序一律直接比较原始小数，不走这里。
    """

    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 1 <= parsed <= 10:
        return None
    return math.floor(parsed + 0.5)


def build_part_records(
    part_features: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把归一化后的 part_features 转成可入库的逐小问记录。"""

    parts: list[dict[str, Any]] = []
    for item in part_features or ():
        if not isinstance(item, Mapping):
            continue
        part_id = str(item.get("part_id") or "").strip()
        if not part_id:
            continue
        features = {
            name: int(item.get(name) or 0) for name in PART_FEATURE_ORDER
        }
        raw = round(feature_raw(features), 4)
        parts.append(
            {
                "part_id": part_id,
                "part_label": str(item.get("part_label") or "").strip(),
                "features": features,
                "context_kind": str(item.get("context_kind") or "").strip(),
                "raw": raw,
                "formula": formula_score(raw),
                "evidence": str(item.get("evidence") or "").strip(),
            }
        )
    return parts


def summarize_parts(
    parts: Sequence[Mapping[str, Any]],
) -> float | None:
    """整题公式难度取最难小问，不取平均。"""

    if not parts:
        return None
    return round(
        max(float(part.get("formula") or 0.0) for part in parts), 4
    )


def question_content_fingerprint(question: Mapping[str, Any]) -> str:
    """题目参与难度判定的内容指纹；内容变化即触发"需重评"。"""

    payload: dict[str, Any] = {}
    for field_name in _FINGERPRINT_FIELDS:
        value = question.get(field_name)
        if field_name == "image_paths":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (ValueError, json.JSONDecodeError):
                    value = [value]
            if not isinstance(value, (list, tuple)):
                value = []
            value = sorted(str(item) for item in value if str(item).strip())
        elif field_name == "has_images":
            value = bool(value)
        else:
            value = " ".join(str(value or "").split())
        payload[field_name] = value
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        f"AND name='{_TABLE}'"
    ).fetchone()
    return row is not None


def save_assessment(
    conn: sqlite3.Connection,
    *,
    question_id: int,
    part_features: Sequence[Mapping[str, Any]],
    content_fingerprint: str,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    """在同一事务内整题替换有效逐小问特征；无特征时不动既有记录。"""

    if not table_exists(conn):
        return None
    parts = build_part_records(part_features)
    if not parts:
        return None
    question_formula = summarize_parts(parts)
    qid = int(question_id)
    conn.execute(
        f"UPDATE {_TABLE} SET is_active = 0 "
        "WHERE question_id = ? AND is_active = 1",
        (qid,),
    )
    for part in parts:
        features_payload = {
            "part_label": part["part_label"],
            **{name: part["features"][name] for name in PART_FEATURE_ORDER},
            "context_kind": part["context_kind"],
            "evidence": part["evidence"],
        }
        conn.execute(
            f"""
            INSERT INTO {_TABLE} (
                question_id, part_id, features_json,
                formula_difficulty, formula_version, source_content_hash,
                model_name, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                qid,
                part["part_id"],
                json.dumps(features_payload, ensure_ascii=False),
                part["formula"],
                STANDARD_DIFFICULTY_FORMULA_VERSION,
                str(content_fingerprint or ""),
                str(model_name or "").strip() or None,
            ),
        )
    return {
        "question_formula": question_formula,
        "parts": parts,
    }


def load_assessment(
    db_path: str | Path,
    question_id: int,
    *,
    current_fingerprint: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """读取当前有效的逐小问特征；题目内容已变化时附带 needs_reevaluation。"""

    if connection is not None:
        return _load_active(connection, int(question_id), current_fingerprint)
    database = Path(db_path)
    if not database.is_file():
        return None
    with connect(database) as conn:
        return _load_active(conn, int(question_id), current_fingerprint)


def _load_active(
    conn: sqlite3.Connection,
    question_id: int,
    current_fingerprint: str | None,
) -> dict[str, Any] | None:
    if not table_exists(conn):
        return None
    rows = conn.execute(
        f"""
        SELECT part_id, features_json, formula_difficulty,
               formula_version, source_content_hash, model_name, created_at
        FROM {_TABLE}
        WHERE question_id = ? AND is_active = 1
        ORDER BY id
        """,
        (int(question_id),),
    ).fetchall()
    if not rows:
        return None
    parts: list[dict[str, Any]] = []
    stored_fingerprint = ""
    formula_version = ""
    model_name = ""
    created_at = ""
    for row in rows:
        try:
            payload = json.loads(str(row["features_json"] or "{}"))
        except (ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        features = {
            name: int(payload.get(name) or 0)
            for name in PART_FEATURE_ORDER
        }
        parts.append(
            {
                "part_id": str(row["part_id"]),
                "part_label": str(payload.get("part_label") or ""),
                "features": features,
                "context_kind": str(payload.get("context_kind") or ""),
                "formula": row["formula_difficulty"],
                "evidence": str(payload.get("evidence") or ""),
            }
        )
        stored_fingerprint = str(row["source_content_hash"] or "")
        formula_version = str(row["formula_version"] or "")
        model_name = str(row["model_name"] or "")
        created_at = str(row["created_at"] or "")
    question_formula = summarize_parts(parts)
    result = {
        "formula_version": formula_version,
        "question_formula": question_formula,
        "parts": parts,
        "model_name": model_name,
        "created_at": created_at,
    }
    result["needs_reevaluation"] = bool(
        current_fingerprint and stored_fingerprint != current_fingerprint
    )
    return result


__all__ = [
    "STANDARD_DIFFICULTY_FORMULA_VERSION",
    "build_part_records",
    "feature_raw",
    "formula_score",
    "load_assessment",
    "question_content_fingerprint",
    "save_assessment",
    "summarize_parts",
    "table_exists",
]
