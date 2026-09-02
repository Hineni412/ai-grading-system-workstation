"""题库自动选题流水线。

只读访问题库数据库，按课时知识范围、难度、题干长度、考频和方法多样性
筛选候选题组，供课件改编发送使用。本模块不写题库，不调用模型。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

_VOLUME_ID_BY_NAME: dict[str, str] = {
    "七年级上册": "bnu24-math-g7-upper",
    "七年级下册": "bnu24-math-g7-lower",
    "八年级上册": "bnu24-math-g8-upper",
    "八年级下册": "bnu24-math-g8-lower",
    "九年级上册": "bnu24-math-g9-upper",
}

_FREQUENCY_FIELDS = ("score_midterm", "score_final", "score_zhongkao")

_MAX_SECTION_IDS = 12
_MAX_LIMIT = 30
_MAX_DIFFICULTY = 9
_MAX_STEM_CHARS = 400


class QuestionSelectionError(ValueError):
    """选题请求或数据状态不满足要求。"""


def volume_id_for_name(name: object) -> str | None:
    text = str(name or "").strip()
    return _VOLUME_ID_BY_NAME.get(text)


@dataclass(frozen=True)
class SelectionRequest:
    volume_id: str
    section_ids: tuple[str, ...]
    difficulty_max: int = 5
    stem_max_chars: int = 220
    limit: int = 12
    max_per_method: int = 2
    exclude_question_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.volume_id:
            raise QuestionSelectionError("必须提供教材册别范围。")
        if not self.section_ids:
            raise QuestionSelectionError("必须至少选择一个小节范围。")
        if len(self.section_ids) > _MAX_SECTION_IDS:
            raise QuestionSelectionError("小节范围数量过多。")
        if not 1 <= self.difficulty_max <= _MAX_DIFFICULTY:
            raise QuestionSelectionError("难度上限无效。")
        if not 1 <= self.stem_max_chars <= _MAX_STEM_CHARS:
            raise QuestionSelectionError("题干长度上限无效。")
        if not 1 <= self.limit <= _MAX_LIMIT:
            raise QuestionSelectionError("选题数量无效。")
        if not 1 <= self.max_per_method <= 6:
            raise QuestionSelectionError("同一方法的保留题数无效。")


def _connect_read_only(database_path: str | Path) -> sqlite3.Connection:
    uri = Path(database_path).resolve().as_posix()
    connection = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def list_volume_sections(database_path: str | Path, volume_id: object) -> list[dict[str, Any]]:
    """列出某册在题库中实际有题的小节（含人名与题量），供页面选择范围。"""

    volume_prefix = str(volume_id or "").strip()
    if not volume_prefix:
        raise QuestionSelectionError("必须提供教材册别。")
    catalog = load_curriculum_catalog()
    section_names: dict[str, dict[str, Any]] = {}
    for volume in catalog["volumes"]:
        if volume["id"] != volume_prefix:
            continue
        for chapter in volume["chapters"]:
            for section in chapter["sections"]:
                section_names[section["id"]] = {
                    "section_id": section["id"],
                    "section_name": str(
                        section.get("display_name") or section.get("title") or ""
                    ),
                    "chapter_id": chapter["id"],
                    "chapter_name": str(
                        chapter.get("display_name") or chapter.get("title") or ""
                    ),
                }
    if not section_names:
        return []
    counts: dict[str, int] = {}
    with _connect_read_only(database_path) as connection:
        rows = connection.execute(
            """
            SELECT t.tag_value AS section_id, COUNT(DISTINCT t.question_id) AS n
            FROM question_tags t
            JOIN questions q ON q.id = t.question_id
            WHERE t.tag_type = 'curriculum_section'
              AND t.tag_value LIKE ?
              AND q.is_deleted = 0
            GROUP BY t.tag_value
            """,
            (volume_prefix + "-c%",),
        ).fetchall()
    for section_id, count in rows:
        if section_id in section_names:
            counts[section_id] = int(count)
    result = []
    for section_id, info in section_names.items():
        result.append(
            {
                **info,
                "question_count": counts.get(section_id, 0),
            }
        )
    result.sort(key=lambda item: (item["chapter_id"], item["section_id"]))
    return result


def fetch_question_content(
    database_path: str | Path, question_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """按题号取当前题干、答案与配图路径，供本机执行题页插入。"""

    clean_ids = sorted({int(q) for q in question_ids if int(q) > 0})
    if not clean_ids:
        return {}
    placeholders = ",".join("?" * len(clean_ids))
    with _connect_read_only(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, question_text, answer_text, question_type,
                   has_images, image_paths
            FROM questions
            WHERE id IN ({placeholders})
              AND COALESCE(is_deleted, 0) = 0
            """,
            clean_ids,
        ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        image_paths: list[str] = []
        if row["has_images"] and row["image_paths"]:
            try:
                parsed = json.loads(row["image_paths"])
                if isinstance(parsed, list):
                    image_paths = [str(item) for item in parsed if str(item)]
            except (TypeError, ValueError):
                image_paths = []
        result[int(row["id"])] = {
            "stem_html": str(row["question_text"] or ""),
            "answer_html": str(row["answer_text"] or ""),
            "question_type": str(row["question_type"] or ""),
            "image_paths": image_paths,
        }
    return result


def _frequency_score(row: sqlite3.Row) -> float:
    total = 0.0
    for field_name in _FREQUENCY_FIELDS:
        total += float(row[field_name] or 0.0)
    return round(total, 6)


def select_questions(
    database_path: str | Path, request: SelectionRequest
) -> dict[str, Any]:
    """按请求筛选候选题组。返回题目条目、筛选统计与淘汰原因计数。"""

    section_ids = list(dict.fromkeys(request.section_ids))
    placeholders = ",".join("?" * len(section_ids))
    excluded = set(request.exclude_question_ids)

    candidates_sql = f"""
        SELECT q.id, q.question_type, q.question_text,
               q.difficulty, q.has_images, q.image_paths,
               q.needs_review,
               f.score_midterm, f.score_final, f.score_zhongkao
        FROM questions q
        JOIN question_tags scope
          ON scope.question_id = q.id
         AND scope.tag_type = 'curriculum_section'
         AND scope.tag_value IN ({placeholders})
        LEFT JOIN question_frequency_cache f ON f.question_id = q.id
        WHERE q.is_deleted = 0
          AND CAST(q.difficulty AS INTEGER) <= ?
        GROUP BY q.id
    """
    duplicate_sql = """
        SELECT question_id, duplicate_of_question_id
        FROM question_duplicate_links
    """
    method_sql = """
        SELECT question_id, tag_value
        FROM question_tags
        WHERE tag_type = 'method'
    """
    knowledge_sql = f"""
        SELECT question_id, tag_value
        FROM question_tags
        WHERE tag_type = 'knowledge_point'
          AND question_id IN (
              SELECT question_id FROM question_tags
              WHERE tag_type = 'curriculum_section'
                AND tag_value IN ({placeholders})
          )
    """

    with _connect_read_only(database_path) as connection:
        rows = connection.execute(
            candidates_sql,
            (*section_ids, request.difficulty_max),
        ).fetchall()
        duplicates = connection.execute(duplicate_sql).fetchall()
        methods = connection.execute(method_sql).fetchall()
        knowledge = connection.execute(knowledge_sql, section_ids).fetchall()

    method_by_question: dict[int, str] = {}
    for question_id, method in methods:
        method_by_question.setdefault(int(question_id), str(method or ""))

    knowledge_by_question: dict[int, list[str]] = {}
    for question_id, point in knowledge:
        text = str(point or "").strip()
        if text:
            knowledge_by_question.setdefault(int(question_id), []).append(text)

    duplicate_partners: dict[int, set[int]] = {}
    for left, right in duplicates:
        duplicate_partners.setdefault(int(left), set()).add(int(right))
        duplicate_partners.setdefault(int(right), set()).add(int(left))

    stats = {
        "candidate_total": len(rows),
        "dropped_needs_review": 0,
        "dropped_stem_length": 0,
        "dropped_excluded": 0,
        "dropped_duplicate": 0,
        "dropped_method_balance": 0,
        "selected": 0,
    }
    items: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    method_counts: dict[str, int] = {}

    scored: list[tuple[float, int, sqlite3.Row]] = []
    for row in rows:
        scored.append((_frequency_score(row), int(row["difficulty"] or 0), row))
    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]["id"]))

    for score, _difficulty, row in scored:
        if len(items) >= request.limit:
            break
        question_id = int(row["id"])
        stem = str(row["question_text"] or "")
        if row["needs_review"]:
            # 答案待复核的题不进候选组
            stats["dropped_needs_review"] += 1
            continue
        if len(stem) > request.stem_max_chars:
            stats["dropped_stem_length"] += 1
            continue
        if question_id in excluded:
            stats["dropped_excluded"] += 1
            continue
        if question_id in selected_ids or any(
            partner in selected_ids
            for partner in duplicate_partners.get(question_id, ())
        ):
            stats["dropped_duplicate"] += 1
            continue
        method = method_by_question.get(question_id, "")
        if method and method_counts.get(method, 0) >= request.max_per_method:
            stats["dropped_method_balance"] += 1
            continue

        image_paths: list[str] = []
        if row["has_images"] and row["image_paths"]:
            try:
                parsed = json.loads(row["image_paths"])
                if isinstance(parsed, list):
                    image_paths = [str(item) for item in parsed if str(item)]
            except (TypeError, ValueError):
                image_paths = []

        items.append(
            {
                "question_id": question_id,
                "question_type": str(row["question_type"] or ""),
                "stem": stem,
                "difficulty": int(row["difficulty"] or 0),
                "frequency_score": score,
                "frequency": {
                    "midterm": float(row["score_midterm"] or 0.0),
                    "final": float(row["score_final"] or 0.0),
                    "zhongkao": float(row["score_zhongkao"] or 0.0),
                },
                "method": method,
                "knowledge_points": knowledge_by_question.get(question_id, [])[:3],
                "has_images": bool(image_paths),
                "selection_reason": {
                    "frequency": f"综合考频 {score:.3f}",
                    "difficulty": f"难度 {int(row['difficulty'] or 0)}",
                    "method": method or "未标注方法",
                },
            }
        )
        selected_ids.add(question_id)
        if method:
            method_counts[method] = method_counts.get(method, 0) + 1

    stats["selected"] = len(items)
    return {
        "request": {
            "volume_id": request.volume_id,
            "section_ids": section_ids,
            "difficulty_max": request.difficulty_max,
            "stem_max_chars": request.stem_max_chars,
            "limit": request.limit,
            "max_per_method": request.max_per_method,
            "exclude_question_ids": sorted(excluded),
        },
        "stats": stats,
        "items": items,
        "method_distribution": dict(
            sorted(method_counts.items(), key=lambda entry: -entry[1])
        ),
    }
