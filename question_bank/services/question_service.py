from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import ALLOWED_TAG_TYPES, QuestionCreate, QuestionUpdate, TagCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.taxonomy.registry import canonicalize_knowledge_values
from question_bank.parsers.type_detector import detect_question_type


TAG_ANALYSIS_MAP = {
    "knowledge_points": "knowledge_point",
    "method_tags": "method",
    "ability_tags": "ability",
    "math_model_tags": "model",
    "error_prone_points": "error_type",
    "prerequisite_points": "prerequisite",
    "textbook_chapter": "exam_scope",
    "teaching_stage": "teaching_stage",
    "suitable_student_level": "student_level",
}
ANSWERED_AI_CONFIDENCE = 0.8
ANSWERLESS_AI_CONFIDENCE = 0.55
CORE_ANALYSIS_TAG_TYPES = ("knowledge_point", "ability", "exam_scope", "student_level")


class QuestionService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize_database(self) -> None:
        initialize_database(self.db_path)

    def backfill_question_types(self) -> int:
        self.initialize_database()
        updated_count = 0
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, question_text, question_type
                FROM questions
                WHERE is_deleted = 0
                  AND (question_type IS NULL OR question_type IN ('', '未知', '解答题'))
                """
            ).fetchall()
            
            updates = []
            for row in rows:
                qid = row["id"]
                text = row["question_text"]
                curr_type = row["question_type"]
                detected = detect_question_type(text, curr_type)
                if detected != curr_type:
                    updates.append((detected, qid))
                    
            if updates:
                conn.executemany(
                    """
                    UPDATE questions
                    SET question_type = ?, updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    updates
                )
                conn.commit()
                updated_count = len(updates)
        return updated_count

    def add_question(self, question: QuestionCreate) -> int:
        self.initialize_database()
        _validate_question_text(question.question_number, question.question_text)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_type, question_text, answer_text,
                    source_file, page_range, image_paths, difficulty,
                    needs_review, has_images, needs_image_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question.paper_id,
                    question.question_number.strip(),
                    _clean_optional(question.question_type),
                    question.question_text.strip(),
                    _clean_optional(question.answer_text),
                    _clean_optional(question.source_file),
                    _clean_optional(question.page_range),
                    json.dumps(question.image_paths, ensure_ascii=False),
                    _clean_optional(question.difficulty),
                    int(question.needs_review),
                    int(question.has_images),
                    int(question.needs_image_review),
                ),
            )
            question_id = int(cursor.lastrowid)
            self._insert_tags(conn, question_id, question.tags)
            conn.commit()
        return question_id

    def get_question(self, question_id: int) -> dict[str, Any] | None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT q.*, p.title AS paper_title, p.year, p.province, p.city, p.grade, p.semester, p.exam_type, p.district
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id = ? AND q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """,
                (int(question_id),),
            ).fetchone()
            if row is None:
                return None
            return self._question_from_row(conn, row)

    def find_exact_duplicate_tag_analysis(self, question_id: int) -> tuple[TagAnalysis, str | None] | None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            target = conn.execute(
                """
                SELECT q.*, p.title AS paper_title, p.year, p.province, p.city, p.grade, p.semester, p.exam_type, p.district
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id = ? AND q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """,
                (int(question_id),),
            ).fetchone()
            if target is None:
                return None
            target_key = _duplicate_key(target)
            if not target_key:
                return None
            rows = conn.execute(
                """
                SELECT q.*, p.title AS paper_title, p.year, p.province, p.city, p.grade, p.semester, p.exam_type, p.district
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id <> ? AND q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.updated_at DESC, q.id DESC
                """,
                (int(question_id),),
            ).fetchall()
            for row in rows:
                if _duplicate_key(row) != target_key:
                    continue
                item = self._question_from_row(conn, row)
                if has_complete_analysis_tags(item):
                    return _analysis_from_tagged_question(item)
        return None

    def _build_filter_query(
        self,
        *,
        question_number: str | None = None,
        keyword: str | None = None,
        knowledge_point: str | None = None,
        difficulty: str | None = None,
        difficulty_range: tuple[int, int] | None = None,
        question_types: list[str] | None = None,
        paper_ids: list[int] | None = None,
        years: list[str] | None = None,
        exam_types: list[str] | None = None,
        grades: list[str] | None = None,
        tag_filters: Mapping[str, list[str]] | None = None,
        is_deleted: bool = False,
        tag_status: str | None = None,
    ) -> tuple[list[str], list[str], list[Any]]:
        joins = []
        where = []
        params = []
        
        joins.append("LEFT JOIN papers p ON p.id = q.paper_id")
        if _clean_optional(knowledge_point):
            joins.append(
                "JOIN question_tags kt ON kt.question_id = q.id AND kt.tag_type = 'knowledge_point' AND kt.tag_value LIKE ?"
            )
            params.append(f"%{knowledge_point.strip()}%")
            
        where.append("q.is_deleted = ?")
        params.append(1 if is_deleted else 0)
        
        where.append("COALESCE(p.import_status, '') <> 'deleted'")
        
        if _clean_optional(question_number):
            where.append("q.question_number = ?")
            params.append(question_number.strip())
        if _clean_optional(keyword):
            where.append("(q.question_text LIKE ? OR COALESCE(q.answer_text, '') LIKE ?)")
            params.extend([f"%{keyword.strip()}%", f"%{keyword.strip()}%"])
        if _clean_optional(difficulty):
            where.append("q.difficulty = ?")
            params.append(difficulty.strip())
        if difficulty_range is not None:
            low, high = _normalized_range(difficulty_range)
            where.append("CAST(q.difficulty AS REAL) BETWEEN ? AND ?")
            params.extend([low, high])
        cleaned_question_types = [_clean_optional(value) for value in (question_types or [])]
        cleaned_question_types = [value for value in cleaned_question_types if value]
        if cleaned_question_types:
            placeholders = ", ".join("?" for _ in cleaned_question_types)
            where.append(f"q.question_type IN ({placeholders})")
            params.extend(cleaned_question_types)
            
        cleaned_paper_ids = [int(value) for value in (paper_ids or []) if value]
        if cleaned_paper_ids:
            placeholders = ", ".join("?" for _ in cleaned_paper_ids)
            where.append(f"q.paper_id IN ({placeholders})")
            params.extend(cleaned_paper_ids)
            
        cleaned_years = [_clean_optional(value) for value in (years or [])]
        cleaned_years = [value for value in cleaned_years if value]
        if cleaned_years:
            placeholders = ", ".join("?" for _ in cleaned_years)
            where.append(f"CAST(p.year AS TEXT) IN ({placeholders})")
            params.extend(cleaned_years)
            
        cleaned_exam_types = [_clean_optional(value) for value in (exam_types or [])]
        cleaned_exam_types = [value for value in cleaned_exam_types if value]
        if cleaned_exam_types:
            placeholders = ", ".join("?" for _ in cleaned_exam_types)
            where.append(f"p.exam_type IN ({placeholders})")
            params.extend(cleaned_exam_types)
            
        cleaned_grades = [_clean_optional(value) for value in (grades or [])]
        cleaned_grades = [value for value in cleaned_grades if value]
        if cleaned_grades:
            placeholders = ", ".join("?" for _ in cleaned_grades)
            where.append(f"p.grade IN ({placeholders})")
            params.extend(cleaned_grades)
            
        for tag_type, tag_values in (tag_filters or {}).items():
            cleaned_values = [_clean_optional(value) for value in tag_values]
            cleaned_values = [value for value in cleaned_values if value]
            if not cleaned_values:
                continue
            placeholders = ", ".join("?" for _ in cleaned_values)
            where.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM question_tags tf
                    WHERE tf.question_id = q.id
                      AND tf.tag_type = ?
                      AND tf.tag_value IN ({placeholders})
                )
                """
            )
            params.extend([tag_type, *cleaned_values])
            
        if _clean_optional(tag_status) and tag_status != "全部":
            if tag_status == "已打标签":
                where.append("""
                    EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'knowledge_point' AND COALESCE(kt2.tag_value, '') <> '')
                    AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'ability' AND COALESCE(kt2.tag_value, '') <> '')
                    AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'exam_scope' AND COALESCE(kt2.tag_value, '') <> '')
                    AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'student_level' AND COALESCE(kt2.tag_value, '') <> '')
                """)
            elif tag_status == "未打标签":
                where.append("""
                    NOT (
                        EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'knowledge_point' AND COALESCE(kt2.tag_value, '') <> '')
                        AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'ability' AND COALESCE(kt2.tag_value, '') <> '')
                        AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'exam_scope' AND COALESCE(kt2.tag_value, '') <> '')
                        AND EXISTS (SELECT 1 FROM question_tags kt2 WHERE kt2.question_id = q.id AND kt2.tag_type = 'student_level' AND COALESCE(kt2.tag_value, '') <> '')
                    )
                """)
            
        return joins, where, params

    def count_questions(
        self,
        *,
        question_number: str | None = None,
        keyword: str | None = None,
        knowledge_point: str | None = None,
        difficulty: str | None = None,
        difficulty_range: tuple[int, int] | None = None,
        question_types: list[str] | None = None,
        paper_ids: list[int] | None = None,
        years: list[str] | None = None,
        exam_types: list[str] | None = None,
        grades: list[str] | None = None,
        tag_filters: Mapping[str, list[str]] | None = None,
        is_deleted: bool = False,
        tag_status: str | None = None,
    ) -> int:
        self.initialize_database()
        joins, where, params = self._build_filter_query(
            question_number=question_number,
            keyword=keyword,
            knowledge_point=knowledge_point,
            difficulty=difficulty,
            difficulty_range=difficulty_range,
            question_types=question_types,
            paper_ids=paper_ids,
            years=years,
            exam_types=exam_types,
            grades=grades,
            tag_filters=tag_filters,
            is_deleted=is_deleted,
            tag_status=tag_status,
        )
        query = ["SELECT COUNT(DISTINCT q.id) FROM questions q"]
        query.extend(joins)
        if where:
            query.append("WHERE " + " AND ".join(where))
            
        with connect(self.db_path) as conn:
            row = conn.execute(" ".join(query), params).fetchone()
            return int(row[0] or 0)

    def query_questions(
        self,
        *,
        question_number: str | None = None,
        keyword: str | None = None,
        knowledge_point: str | None = None,
        difficulty: str | None = None,
        difficulty_range: tuple[int, int] | None = None,
        question_types: list[str] | None = None,
        paper_ids: list[int] | None = None,
        years: list[str] | None = None,
        exam_types: list[str] | None = None,
        grades: list[str] | None = None,
        tag_filters: Mapping[str, list[str]] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        is_deleted: bool = False,
        tag_status: str | None = None,
        sort_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize_database()
        joins, where, params = self._build_filter_query(
            question_number=question_number,
            keyword=keyword,
            knowledge_point=knowledge_point,
            difficulty=difficulty,
            difficulty_range=difficulty_range,
            question_types=question_types,
            paper_ids=paper_ids,
            years=years,
            exam_types=exam_types,
            grades=grades,
            tag_filters=tag_filters,
            is_deleted=is_deleted,
            tag_status=tag_status,
        )
        
        query = ["SELECT DISTINCT q.*, p.title AS paper_title, p.year, p.province, p.city, p.grade, p.semester, p.exam_type, p.district FROM questions q"]
        query.extend(joins)
        if where:
            query.append("WHERE " + " AND ".join(where))
        query.append(_question_order_clause(sort_mode))
        
        if limit is not None:
            query.append("LIMIT ?")
            params.append(limit)
        if offset is not None:
            query.append("OFFSET ?")
            params.append(offset)
            
        with connect(self.db_path) as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
            if not rows:
                return []
            
            question_ids = [row["id"] for row in rows]
            placeholders = ", ".join("?" for _ in question_ids)
            tags_rows = conn.execute(
                f"""
                SELECT id, question_id, tag_type, tag_value, confidence, source, model_name, created_at
                FROM question_tags
                WHERE question_id IN ({placeholders})
                ORDER BY id ASC
                """,
                question_ids
            ).fetchall()
            
            tags_by_q: dict[int, list[dict[str, Any]]] = {}
            for tag in tags_rows:
                tags_by_q.setdefault(tag["question_id"], []).append(dict(tag))
                
            questions = []
            for row in rows:
                item = dict(row)
                try:
                    parsed_paths = json.loads(str(item.get("image_paths") or "[]"))
                except json.JSONDecodeError:
                    parsed_paths = []
                item["image_paths"] = parsed_paths if isinstance(parsed_paths, list) else []
                item["tags"] = tags_by_q.get(item["id"], [])
                questions.append(item)
            return questions

    def overview_stats(self) -> dict[str, int]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            total_questions = conn.execute(
                """
                SELECT COUNT(*)
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """
            ).fetchone()[0]
            tagged_questions = conn.execute(
                """
                SELECT COUNT(DISTINCT q.id)
                FROM questions q
                JOIN question_tags t ON t.question_id = q.id
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                  AND EXISTS (SELECT 1 FROM question_tags kt WHERE kt.question_id = q.id AND kt.tag_type = 'knowledge_point' AND COALESCE(kt.tag_value, '') <> '')
                  AND EXISTS (SELECT 1 FROM question_tags kt WHERE kt.question_id = q.id AND kt.tag_type = 'ability' AND COALESCE(kt.tag_value, '') <> '')
                  AND EXISTS (SELECT 1 FROM question_tags kt WHERE kt.question_id = q.id AND kt.tag_type = 'exam_scope' AND COALESCE(kt.tag_value, '') <> '')
                  AND EXISTS (SELECT 1 FROM question_tags kt WHERE kt.question_id = q.id AND kt.tag_type = 'student_level' AND COALESCE(kt.tag_value, '') <> '')
                """
            ).fetchone()[0]
            paper_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM papers
                WHERE COALESCE(import_status, '') <> 'deleted'
                """
            ).fetchone()[0]
        total = int(total_questions or 0)
        tagged = int(tagged_questions or 0)
        return {
            "total_questions": total,
            "tagged_questions": tagged,
            "untagged_questions": max(total - tagged, 0),
            "paper_count": int(paper_count or 0),
        }

    def list_tag_values(self, tag_types: list[str] | tuple[str, ...] | None = None) -> dict[str, list[str]]:
        self.initialize_database()
        filters = [tag_type for tag_type in (tag_types or tuple(ALLOWED_TAG_TYPES)) if tag_type in ALLOWED_TAG_TYPES]
        if not filters:
            return {}
        placeholders = ", ".join("?" for _ in filters)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.tag_type, t.tag_value, COUNT(DISTINCT q.id) AS question_count
                FROM question_tags t
                JOIN questions q ON q.id = t.question_id AND q.is_deleted = 0
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE t.tag_type IN ({placeholders})
                  AND COALESCE(t.tag_value, '') <> ''
                  AND COALESCE(p.import_status, '') <> 'deleted'
                GROUP BY t.tag_type, t.tag_value
                ORDER BY t.tag_type ASC, question_count DESC, t.tag_value ASC
                """,
                filters,
            ).fetchall()
        values: dict[str, list[str]] = {tag_type: [] for tag_type in filters}
        for row in rows:
            values.setdefault(row["tag_type"], []).append(row["tag_value"])
        return values

    def save_question_preview(
        self,
        question_id: int,
        *,
        preview_type: str,
        source_file: str | None = None,
        page_number: int | None = None,
        image_path: str | None = None,
        bbox: Mapping[str, Any] | None = None,
        status: str = "ready",
        message: str | None = None,
    ) -> None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM question_previews
                WHERE question_id = ? AND preview_type = ?
                """,
                (int(question_id), preview_type),
            )
            conn.execute(
                """
                INSERT INTO question_previews (
                    question_id, preview_type, source_file, page_number,
                    image_path, bbox_json, status, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(question_id),
                    preview_type,
                    _clean_optional(source_file),
                    page_number,
                    _clean_optional(image_path),
                    json.dumps(dict(bbox or {}), ensure_ascii=False),
                    status,
                    _clean_optional(message),
                ),
            )
            conn.commit()

    def list_question_previews(self, question_ids: list[int] | tuple[int, ...]) -> dict[int, dict[str, dict[str, Any]]]:
        self.initialize_database()
        ids = [int(question_id) for question_id in question_ids if question_id]
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT question_id, preview_type, source_file, page_number,
                       image_path, bbox_json, status, message, updated_at
                FROM question_previews
                WHERE question_id IN ({placeholders})
                ORDER BY id ASC
                """,
                ids,
            ).fetchall()
        previews: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            try:
                item["bbox"] = json.loads(str(item.pop("bbox_json") or "{}"))
            except json.JSONDecodeError:
                item["bbox"] = {}
            previews.setdefault(int(row["question_id"]), {})[str(row["preview_type"])] = item
        return previews

    def list_papers(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        self.initialize_database()
        query = """
            SELECT
                p.*,
                COUNT(q.id) AS question_count,
                SUM(CASE WHEN COALESCE(q.is_deleted, 0) = 0 THEN 1 ELSE 0 END) AS active_question_count
            FROM papers p
            LEFT JOIN questions q ON q.paper_id = p.id
        """
        params: list[Any] = []
        if not include_deleted:
            query += " WHERE COALESCE(p.import_status, '') <> 'deleted'"
        query += " GROUP BY p.id ORDER BY p.created_at DESC, p.id DESC"
        with connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def delete_paper(self, paper_id: int) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            paper = conn.execute(
                "SELECT id FROM papers WHERE id = ? AND COALESCE(import_status, '') <> 'deleted'",
                (int(paper_id),),
            ).fetchone()
            if paper is None:
                return False
            conn.execute(
                """
                UPDATE questions
                SET is_deleted = 1,
                    deleted_at = COALESCE(deleted_at, datetime('now','localtime')),
                    updated_at = datetime('now','localtime')
                WHERE paper_id = ? AND is_deleted = 0
                """,
                (int(paper_id),),
            )
            conn.execute(
                """
                UPDATE papers
                SET import_status = 'deleted',
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (int(paper_id),),
            )
            conn.commit()
        return True

    def restore_paper(self, paper_id: int) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            paper = conn.execute(
                "SELECT id FROM papers WHERE id = ? AND COALESCE(import_status, '') = 'deleted'",
                (int(paper_id),),
            ).fetchone()
            if paper is None:
                return False
            conn.execute(
                """
                UPDATE questions
                SET is_deleted = 0,
                    deleted_at = NULL,
                    updated_at = datetime('now','localtime')
                WHERE paper_id = ? AND is_deleted = 1
                """,
                (int(paper_id),),
            )
            conn.execute(
                """
                UPDATE papers
                SET import_status = 'needs_review',
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (int(paper_id),),
            )
            conn.commit()
        return True

    def update_question(self, question_id: int, question: QuestionUpdate) -> bool:
        self.initialize_database()
        _validate_question_text(question.question_number, question.question_text)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE questions
                SET paper_id = ?, question_number = ?, question_type = ?, question_text = ?,
                    answer_text = ?, source_file = ?, page_range = ?, image_paths = ?,
                    difficulty = ?, needs_review = ?, has_images = ?,
                    needs_image_review = ?, updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 0
                """,
                (
                    question.paper_id,
                    question.question_number.strip(),
                    _clean_optional(question.question_type),
                    question.question_text.strip(),
                    _clean_optional(question.answer_text),
                    _clean_optional(question.source_file),
                    _clean_optional(question.page_range),
                    json.dumps(question.image_paths, ensure_ascii=False),
                    _clean_optional(question.difficulty),
                    int(question.needs_review),
                    int(question.has_images),
                    int(question.needs_image_review),
                    int(question_id),
                ),
            )
            conn.commit()
        return cursor.rowcount > 0

    def update_question_type(self, question_id: int, question_type: str) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE questions
                SET question_type = ?, updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 0
                """,
                (_clean_optional(question_type), int(question_id)),
            )
            conn.commit()
        return cursor.rowcount > 0

    def delete_question(self, question_id: int) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE questions
                SET is_deleted = 1,
                    deleted_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 0
                """,
                (int(question_id),),
            )
            conn.commit()
        return cursor.rowcount > 0

    def restore_question(self, question_id: int) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE questions
                SET is_deleted = 0,
                    deleted_at = NULL,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 1
                """,
                (int(question_id),),
            )
            conn.commit()
        return cursor.rowcount > 0

    def batch_delete_questions(self, question_ids: list[int]) -> int:
        self.initialize_database()
        ids = [int(qid) for qid in question_ids if qid]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                f"""
                UPDATE questions
                SET is_deleted = 1,
                    deleted_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE id IN ({placeholders}) AND is_deleted = 0
                """,
                ids,
            )
            conn.commit()
        return cursor.rowcount

    def batch_restore_questions(self, question_ids: list[int]) -> int:
        self.initialize_database()
        ids = [int(qid) for qid in question_ids if qid]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                f"""
                UPDATE questions
                SET is_deleted = 0,
                    deleted_at = NULL,
                    updated_at = datetime('now','localtime')
                WHERE id IN ({placeholders}) AND is_deleted = 1
                """,
                ids,
            )
            conn.commit()
        return cursor.rowcount

    def tag_frequency_stats(self, analysis: TagAnalysis, *, exclude_question_id: int | None = None) -> dict[str, Any]:
        self.initialize_database()
        tag_values = _analysis_frequency_tags(analysis)
        with connect(self.db_path) as conn:
            total_questions = conn.execute(
                """
                SELECT COUNT(*)
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                """
            ).fetchone()[0]
            if not tag_values:
                return {
                    "total_questions": int(total_questions or 0),
                    "matched_tag_count": 0,
                    "matched_question_count": 0,
                    "matched_tags": [],
                    "frequency_score": 1,
                }
            placeholders = ", ".join("?" for _ in tag_values)
            params: list[Any] = list(tag_values)
            exclude_clause = ""
            if exclude_question_id is not None:
                exclude_clause = "AND q.id <> ?"
                params.append(int(exclude_question_id))
            matched_question_count = conn.execute(
                f"""
                SELECT COUNT(DISTINCT q.id)
                FROM questions q
                JOIN question_tags t ON t.question_id = q.id
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                  AND t.tag_type IN ('knowledge_point', 'method', 'model')
                  AND t.tag_value IN ({placeholders})
                  {exclude_clause}
                """,
                params,
            ).fetchone()[0]
            matched_tag_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM question_tags t
                JOIN questions q ON q.id = t.question_id
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.is_deleted = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                  AND t.tag_type IN ('knowledge_point', 'method', 'model')
                  AND t.tag_value IN ({placeholders})
                  {exclude_clause}
                """,
                params,
            ).fetchone()[0]
        total = max(int(total_questions or 0), 1)
        ratio = min(1.0, float(matched_question_count or 0) / total)
        frequency_score = min(10, max(1, round(1 + ratio * 9)))
        return {
            "total_questions": int(total_questions or 0),
            "matched_tag_count": int(matched_tag_count or 0),
            "matched_question_count": int(matched_question_count or 0),
            "matched_tags": list(tag_values),
            "frequency_score": frequency_score,
        }

    def save_tag_analysis(
        self,
        question_id: int,
        analysis: TagAnalysis,
        *,
        overwrite_manual: bool = False,
        edited_fields: set[str] | None = None,
        model_name: str | None = None,
        confidence: float | None = None,
    ) -> bool:
        self.initialize_database()
        with connect(self.db_path) as conn:
            question = conn.execute(
                "SELECT id, answer_text FROM questions WHERE id = ? AND is_deleted = 0",
                (int(question_id),),
            ).fetchone()
            if question is None:
                return False
            covered_tag_types = tuple(TAG_ANALYSIS_MAP.values())
            placeholders = ", ".join("?" for _ in covered_tag_types)
            conn.execute(
                f"""
                DELETE FROM question_tags
                WHERE question_id = ?
                  AND tag_type IN ({placeholders})
                  AND (source = 'ai' OR ? = 1)
                """,
                (int(question_id), *covered_tag_types, int(overwrite_manual)),
            )
            fallback_confidence = ANSWERED_AI_CONFIDENCE if _clean_optional(question["answer_text"]) else ANSWERLESS_AI_CONFIDENCE
            resolved_confidence = _normalize_confidence(confidence if confidence is not None else getattr(analysis, "confidence", fallback_confidence))
            rows = _tag_analysis_rows(
                question_id=int(question_id),
                analysis=analysis,
                confidence=resolved_confidence,
                edited_fields=edited_fields or set(),
                model_name=_clean_optional(model_name),
            )
            conn.executemany(
                """
                INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source, model_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.execute(
                """
                UPDATE questions
                SET difficulty = ?,
                    reason = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 0
                """,
                (
                    str(analysis.difficulty),
                    _clean_optional(analysis.reason),
                    int(question_id),
                ),
            )
            conn.commit()
        return True

    def _insert_tags(self, conn, question_id: int, tags: list[TagCreate]) -> None:
        rows: list[tuple[Any, ...]] = []
        for tag in tags:
            tag_type = tag.tag_type.strip()
            tag_value = tag.tag_value.strip()
            if tag_type not in ALLOWED_TAG_TYPES:
                raise ValueError(f"未知题目标签类型: {tag_type}")
            if not tag_value:
                continue
            rows.append((question_id, tag_type, tag_value, tag.confidence, _clean_optional(tag.source)))
        conn.executemany(
            """
            INSERT INTO question_tags (question_id, tag_type, tag_value, confidence, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _question_from_row(self, conn, row) -> dict[str, Any]:
        item = dict(row)
        try:
            parsed_paths = json.loads(str(item.get("image_paths") or "[]"))
        except json.JSONDecodeError:
            parsed_paths = []
        item["image_paths"] = parsed_paths if isinstance(parsed_paths, list) else []
        tags = conn.execute(
            """
            SELECT id, question_id, tag_type, tag_value, confidence, source, model_name, created_at
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id ASC
            """,
            (item["id"],),
        ).fetchall()
        item["tags"] = [dict(tag) for tag in tags]
        return item


def _validate_question_text(question_number: str, question_text: str) -> None:
    if not str(question_number or "").strip():
        raise ValueError("题号不能为空")
    if not str(question_text or "").strip():
        raise ValueError("题干不能为空")


def _clean_optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalized_range(value: tuple[int, int]) -> tuple[int, int]:
    low, high = int(value[0]), int(value[1])
    return (min(low, high), max(low, high))


def _question_order_clause(sort_mode: str | None) -> str:
    if sort_mode == "试题难度":
        return "ORDER BY CAST(q.difficulty AS REAL) DESC, q.created_at DESC, q.id DESC"
    return "ORDER BY q.created_at DESC, q.id DESC"


def _tag_analysis_rows(
    *,
    question_id: int,
    analysis: TagAnalysis,
    confidence: float,
    edited_fields: set[str],
    model_name: str | None,
) -> list[tuple[int, str, str, float, str, str | None]]:
    rows: list[tuple[int, str, str, float, str, str | None]] = []
    payload = analysis.to_dict()
    for field_name, tag_type in TAG_ANALYSIS_MAP.items():
        source = "manual" if field_name in edited_fields else "ai"
        values = payload.get(field_name)
        if isinstance(values, str):
            values = [values] if values.strip() else []
        rows.extend((question_id, tag_type, tag_value, confidence, source, model_name if source == "ai" else None) for tag_value in values or [])
    canonical = canonicalize_knowledge_values(payload.get("knowledge_points", []))
    if canonical is not None:
        rows.append((question_id, "canonical_knowledge_id", canonical.canonical_id, confidence, "taxonomy", model_name))
    return rows


def _analysis_from_tagged_question(question: Mapping[str, Any]) -> tuple[TagAnalysis, str | None]:
    grouped: dict[str, list[str]] = {}
    model_name = None
    confidences: list[float] = []
    for tag in question.get("tags", []):
        if not isinstance(tag, Mapping):
            continue
        tag_type = _clean_optional(tag.get("tag_type"))
        tag_value = _clean_optional(tag.get("tag_value"))
        if tag_type and tag_value:
            grouped.setdefault(tag_type, [])
            if tag_value not in grouped[tag_type]:
                grouped[tag_type].append(tag_value)
        if model_name is None:
            model_name = _clean_optional(tag.get("model_name"))
        try:
            confidences.append(float(tag.get("confidence")))
        except (TypeError, ValueError):
            pass
    confidence = min(max(confidences), 0.9) if confidences else 0.9
    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": grouped.get("knowledge_point", []),
            "method_tags": grouped.get("method", []),
            "ability_tags": grouped.get("ability", []),
            "math_model_tags": grouped.get("model", []),
            "difficulty": question.get("difficulty") or 1,
            "error_prone_points": grouped.get("error_type", []),
            "prerequisite_points": grouped.get("prerequisite", []),
            "textbook_chapter": _first_value(grouped.get("exam_scope", [])),
            "teaching_stage": _first_value(grouped.get("teaching_stage", [])),
            "suitable_student_level": _first_value(grouped.get("student_level", [])),
            "reason": question.get("reason") or "",
            "confidence": confidence,
        }
    )
    return analysis, model_name


def has_complete_analysis_tags(question: Mapping[str, Any]) -> bool:
    seen: set[str] = set()
    for tag in question.get("tags", []):
        tag_type = _clean_optional(tag.get("tag_type")) if isinstance(tag, Mapping) else None
        tag_value = _clean_optional(tag.get("tag_value")) if isinstance(tag, Mapping) else None
        if tag_type in CORE_ANALYSIS_TAG_TYPES and tag_value:
            seen.add(tag_type)
    return all(tag_type in seen for tag_type in CORE_ANALYSIS_TAG_TYPES)


def _duplicate_key(question: Mapping[str, Any]) -> str:
    question_text = _normalize_duplicate_text(_mapping_value(question, "question_text"))
    answer_text = _normalize_duplicate_text(_mapping_value(question, "answer_text"))
    return f"{question_text}\n{answer_text}" if question_text else ""


def _normalize_duplicate_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _mapping_value(value: Mapping[str, Any], key: str) -> Any:
    if hasattr(value, "get"):
        return value.get(key)
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return None


def _first_value(values: list[str] | tuple[str, ...] | None) -> str:
    return str((values or [""])[0] or "").strip()


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = ANSWERED_AI_CONFIDENCE
    return round(min(1.0, max(0.0, confidence)), 4)


def _analysis_frequency_tags(analysis: TagAnalysis) -> tuple[str, ...]:
    values: list[str] = []
    for field_name in ("knowledge_points", "method_tags", "math_model_tags"):
        for tag_value in analysis.to_dict().get(field_name, []):
            cleaned = _clean_optional(tag_value)
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return tuple(values)
