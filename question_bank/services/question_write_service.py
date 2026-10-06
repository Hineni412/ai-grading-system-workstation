from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import (
    ALLOWED_TAG_TYPES,
    QuestionCreate,
    TagCreate,
)
from question_bank.models.tag_schema import MAX_TAG_LENGTH, TagAnalysis
from question_bank.parsers.type_detector import ESSAY_SUBTYPES, QUESTION_TYPES
from question_bank.services.question_revision import question_revision
from question_bank.solution_evidence.knowledge_links import (
    load_point_links,
    resolve_anchor_keys,
)
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

if TYPE_CHECKING:
    from question_bank.taxonomy.governance import TaxonomyGovernance


_REQUEST_LOCKS_GUARD = threading.Lock()
_REQUEST_LOCKS: dict[str, threading.Lock] = {}
# 八上重打方案：模型输出只剩这些整题维度。知识点、章与小节归属全部由
# 判定点关联派生；error_type、学生层次、教学阶段、canonical_knowledge_id
# 等旧维度不再写入，重打时按下方 _RETIRED_ANALYSIS_TAG_TYPES 清理。
_TAG_ANALYSIS_MAP = {
    "method_tags": "method",
    "thought_tags": "thought",
    "ability_tags": "ability",
    "math_model_tags": "model",
    "special_type_tags": "special_type",
}
# 重打时一并清除的停用维度（仅限 ai/taxonomy 等非手工来源）。
_RETIRED_ANALYSIS_TAG_TYPES = (
    "knowledge_point",
    "exam_scope",
    "curriculum_section",
    "error_type",
    "canonical_knowledge_id",
    "student_level",
    "teaching_stage",
    "sub_skill",
    "measured_skill_name",
    "supporting_skill_name",
)
# 历史自动来源：重打时与 ai/taxonomy 一起覆盖，保证全库口径统一。
_NON_MANUAL_TAG_SOURCES = (
    "ai",
    "taxonomy",
    "question_type_migration",
    "question_type_suggestion",
    "type_detector",
)
_TAG_STATUS_TYPE = "tag_status"
_DERIVED_PENDING_STATUS = "derived_pending"
_ANSWERED_AI_CONFIDENCE = 0.8
_ANSWERLESS_AI_CONFIDENCE = 0.55
_PAPER_DELETE_ASSET_SUBDIRS = (
    "question_bank/raw_papers",
    "question_bank/extracted_images",
    "question_bank/previews",
    "question_bank/rich_content",
    "question_bank/document_pages",
    "question_bank/cache/latex",
)


@dataclass(frozen=True)
class ConfirmedQuestionTag:
    tag_type: str
    tag_value: str
    confidence: float | None = None


@dataclass(frozen=True)
class QuestionWriteResult:
    question_id: int
    revision: str
    deleted: bool
    tags: tuple[ConfirmedQuestionTag, ...]


@dataclass(frozen=True)
class PaperMetadataUpdate:
    title: str
    year: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    exam_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    folder_name: str | None = None
    textbook_version: str | None = None


@dataclass(frozen=True)
class PaperMetadataWriteResult:
    id: int
    title: str
    year: str | None
    province: str | None
    city: str | None
    district: str | None
    exam_type: str | None
    grade: str | None
    semester: str | None
    folder_name: str | None
    textbook_version: str | None
    updated_at: str


@dataclass(frozen=True)
class PaperStateWriteResult:
    id: int
    deleted: bool
    import_status: str
    updated_at: str
    affected_question_count: int


@dataclass(frozen=True)
class PaperPermanentDeleteSelection:
    id: int
    expected_updated_at: str


@dataclass(frozen=True)
class PaperPermanentDeleteImpact:
    paper_count: int
    question_count: int
    tag_count: int
    analysis_record_count: int
    training_link_count: int
    knowledge_graph_link_count: int
    owned_file_count: int
    shared_file_count: int
    taxonomy_proposal_count: int
    permanent_delete_phrase: str


@dataclass(frozen=True)
class PaperPermanentDeleteResult:
    deleted_paper_ids: tuple[int, ...]
    deleted_question_count: int
    deleted_tag_count: int
    deleted_analysis_record_count: int
    removed_training_link_count: int
    removed_knowledge_graph_link_count: int
    deleted_file_count: int
    skipped_shared_file_count: int
    storage_cleanup_pending: bool


class QuestionWriteNotFound(LookupError):
    pass


class QuestionWriteConflict(RuntimeError):
    def __init__(self, current_revision: str) -> None:
        super().__init__("Question state changed")
        self.current_revision = current_revision


class PaperMetadataNotFound(LookupError):
    pass


class PaperMetadataConflict(RuntimeError):
    def __init__(self, current_updated_at: str) -> None:
        super().__init__("Paper metadata changed")
        self.current_updated_at = current_updated_at


class PaperStateNotFound(LookupError):
    pass


class PaperStateConflict(RuntimeError):
    def __init__(self, current_updated_at: str, *, deleted: bool) -> None:
        super().__init__("Paper state changed")
        self.current_updated_at = current_updated_at
        self.deleted = bool(deleted)


class PaperPermanentDeleteConflict(RuntimeError):
    pass


class PaperPermanentDeleteConfirmationMismatch(ValueError):
    pass


class PaperPermanentDeleteStorageIncomplete(RuntimeError):
    pass


class PaperPermanentDeleteDependencyConflict(RuntimeError):
    pass


class QuestionImportUploadNotFound(LookupError):
    pass


class QuestionImportTypeNotSupported(ValueError):
    pass


class QuestionImportStorageForbidden(RuntimeError):
    pass


class QuestionImportTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class StagedImportUpload:
    upload_id: str
    filename: str
    suffix: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "suffix": self.suffix,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class QuestionImportRequest:
    request_id: str
    upload_id: str
    filename: str
    size: int
    sha256: str
    status: str = "pending"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "request_id": self.request_id,
            "upload_id": self.upload_id,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "status": self.status,
        }


@dataclass(frozen=True)
class QuestionImportResource:
    request_id: str
    upload_id: str
    filename: str
    suffix: str
    size: int
    sha256: str
    source_path: Path


class _TagAnalysisWriteBatch:
    """One prepared tag-write run sharing a current-knowledge resolver."""

    def __init__(self, service: QuestionBankWriteService) -> None:
        self._service = service
        self._resolver: CurrentKnowledgeResolver | None = None
        self._prepare_error: Exception | None = None
        self._entered = False

    def __enter__(self) -> _TagAnalysisWriteBatch:
        with self._service._tag_analysis_batch_lock:
            if self._service._active_tag_analysis_batch is not None:
                raise RuntimeError("tag analysis write batch is already active")
            try:
                initialize_database(self._service.db_path)
                self._resolver = CurrentKnowledgeResolver.from_active_database(
                    self._service.db_path
                )
            except Exception as error:  # Keep the existing per-question failure seam.
                self._prepare_error = error
            self._service._active_tag_analysis_batch = self
            self._entered = True
        return self

    def __exit__(
        self,
        _exc_type: object,
        exc: BaseException | None,
        _traceback: object,
    ) -> bool:
        with self._service._tag_analysis_batch_lock:
            if self._service._active_tag_analysis_batch is self:
                self._service._active_tag_analysis_batch = None
            self._entered = False
        return False

    def save_tag_analysis(
        self,
        question_id: int,
        analysis: TagAnalysis,
        *,
        overwrite_manual: bool,
        edited_fields: set[str] | None,
        model_name: str | None,
        confidence: float | None,
        taxonomy_governance: object | None,
    ) -> bool:
        resolver = self._resolver
        if not self._entered:
            raise RuntimeError("tag analysis write batch is not active")
        if self._prepare_error is not None:
            raise self._prepare_error
        if resolver is None:
            raise RuntimeError("tag analysis write batch was not prepared")
        saved = self._service._save_tag_analysis_with_resolver(
            question_id,
            analysis,
            overwrite_manual=overwrite_manual,
            edited_fields=edited_fields,
            model_name=model_name,
            confidence=confidence,
            taxonomy_governance=taxonomy_governance,
            resolver=resolver,
        )
        return saved


class QuestionBankWriteService:
    def __init__(
        self,
        db_path: Path,
        *,
        data_root: Path,
        max_upload_bytes: int = 200 * 1024 * 1024,
        taxonomy_governance: TaxonomyGovernance | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.max_upload_bytes = int(max_upload_bytes)
        self._taxonomy_governance = taxonomy_governance
        self._tag_analysis_batch_lock = threading.Lock()
        self._active_tag_analysis_batch: _TagAnalysisWriteBatch | None = None

    def _live_question_exists(self, question_id: int) -> bool:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM questions "
                "WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                (int(question_id),),
            ).fetchone()
        return row is not None

    def tag_analysis_batch(self) -> _TagAnalysisWriteBatch:
        return _TagAnalysisWriteBatch(self)

    def add_question(self, question: QuestionCreate) -> int:
        """Insert an imported question through the canonical write boundary."""

        _validate_question_create(question)
        initialize_database(self.db_path)
        from question_bank.services.duplicate_analysis_copy_service import (
            copy_duplicate_analysis,
            link_new_question_duplicate,
        )
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_type, question_text,
                    answer_text, source_file, page_range, image_paths,
                    difficulty, needs_review, has_images, needs_image_review
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
            # Explicit input takes precedence over labels inherited from an
            # identical older question. The duplicate helper fills empty types.
            _insert_imported_tags(conn, question_id, question.tags)
            source_id = link_new_question_duplicate(conn, question_id=question_id, data_root=self.data_root)
        if source_id is not None:
            copy_duplicate_analysis(self.db_path, source_question_id=source_id,
                                    target_question_id=question_id, data_root=self.data_root)
        return question_id

    def save_tag_analysis(
        self,
        question_id: int,
        analysis: TagAnalysis,
        *,
        overwrite_manual: bool = False,
        edited_fields: set[str] | None = None,
        model_name: str | None = None,
        confidence: float | None = None,
        taxonomy_governance: object | None = None,
    ) -> bool:
        """Persist one governed analysis without rewriting unrelated tags."""

        with self._tag_analysis_batch_lock:
            active_batch = self._active_tag_analysis_batch
        if active_batch is not None:
            return active_batch.save_tag_analysis(
                question_id,
                analysis,
                overwrite_manual=overwrite_manual,
                edited_fields=edited_fields,
                model_name=model_name,
                confidence=confidence,
                taxonomy_governance=taxonomy_governance,
            )
        initialize_database(self.db_path)
        resolver = CurrentKnowledgeResolver.from_active_database(self.db_path)
        saved = self._save_tag_analysis_with_resolver(
            question_id,
            analysis,
            overwrite_manual=overwrite_manual,
            edited_fields=edited_fields,
            model_name=model_name,
            confidence=confidence,
            taxonomy_governance=taxonomy_governance,
            resolver=resolver,
        )
        if not saved:
            return False
        return True

    def _save_tag_analysis_with_resolver(
        self,
        question_id: int,
        analysis: TagAnalysis,
        *,
        overwrite_manual: bool,
        edited_fields: set[str] | None,
        model_name: str | None,
        confidence: float | None,
        taxonomy_governance: object | None,
        resolver: CurrentKnowledgeResolver,
    ) -> bool:
        with connect(self.db_path) as conn:
            question = conn.execute(
                """
                SELECT id, answer_text, question_text, question_type,
                       has_images, image_paths
                FROM questions WHERE id = ? AND is_deleted = 0
                """,
                (int(question_id),),
            ).fetchone()
            if question is None:
                return False
            derived = _derived_ownership(
                conn,
                self.db_path,
                int(question_id),
            )
            # prerequisite 也属派生维度（来自 supporting_prerequisite 关联），
            # 重打时同样清理非手工旧值后由 _derived_ownership 重写。
            covered = tuple(_TAG_ANALYSIS_MAP.values()) + (
                *_RETIRED_ANALYSIS_TAG_TYPES,
                "prerequisite",
                _TAG_STATUS_TYPE,
            )
            placeholders = ", ".join("?" for _ in covered)
            source_placeholders = ", ".join("?" for _ in _NON_MANUAL_TAG_SOURCES)
            conn.execute(
                f"""
                DELETE FROM question_tags
                WHERE question_id = ?
                  AND tag_type IN ({placeholders})
                  AND (source IN ({source_placeholders}) OR ? = 1)
                """,
                (
                    int(question_id),
                    *covered,
                    *_NON_MANUAL_TAG_SOURCES,
                    int(overwrite_manual),
                ),
            )
            fallback = (
                _ANSWERED_AI_CONFIDENCE
                if _clean_optional(question["answer_text"])
                else _ANSWERLESS_AI_CONFIDENCE
            )
            resolved_confidence = _normalize_confidence(
                confidence
                if confidence is not None
                else getattr(analysis, "confidence", fallback)
            )
            rows = _tag_analysis_rows(
                question_id=int(question_id),
                analysis=analysis,
                confidence=resolved_confidence,
                edited_fields=edited_fields or set(),
                model_name=_clean_optional(model_name),
            )
            if derived is not None:
                rows.extend(
                    (
                        int(question_id),
                        tag_type,
                        value,
                        resolved_confidence,
                        "taxonomy",
                        _clean_optional(model_name),
                    )
                    for tag_type, values in (
                        ("exam_scope", derived["exam_scope"]),
                        ("curriculum_section", derived["curriculum_section"]),
                        ("knowledge_point", derived["direct_keys"]),
                        ("prerequisite", derived["prerequisite_keys"]),
                    )
                    for value in values
                )
            if derived is None or not (
                derived["exam_scope"] or derived["curriculum_section"]
            ):
                rows.append(
                    (
                        int(question_id),
                        _TAG_STATUS_TYPE,
                        _DERIVED_PENDING_STATUS,
                        resolved_confidence,
                        "taxonomy",
                        _clean_optional(model_name),
                    )
                )
            rows = _dedupe_tag_rows(rows)
            conn.executemany(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, confidence, source,
                    model_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            # 整题难度：有逐小问特征时按公式取最难小问的一位小数分数
            # （如 8.8），限幅到 1.0—10.0；无特征时回退到模型给的整题难度。
            difficulty_value = analysis.difficulty
            part_records: list[dict[str, Any]] = []
            if analysis.part_features:
                from question_bank.services import standard_difficulty

                part_records = standard_difficulty.build_part_records(
                    analysis.part_features
                )
                if part_records:
                    max_formula = max(
                        float(part["formula"]) for part in part_records
                    )
                    difficulty_value = (
                        f"{max(1.0, min(10.0, max_formula)):.1f}"
                    )
            conn.execute(
                """
                UPDATE questions
                SET difficulty = ?, reason = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND is_deleted = 0
                """,
                (
                    (
                        str(difficulty_value)
                        if difficulty_value is not None
                        else None
                    ),
                    _clean_optional(analysis.reason),
                    int(question_id),
                ),
            )
            # 预测典型错法：写入典型错法表（仅预测候选项），不再是 error_type 标签。
            from question_bank.services.predicted_error_patterns import (
                record_predicted_patterns,
            )

            record_predicted_patterns(
                conn,
                int(question_id),
                analysis.predicted_error_patterns,
            )
            # 标准难度：独立表存储特征与公式版本。
            if part_records:
                from question_bank.services import standard_difficulty

                standard_difficulty.save_assessment(
                    conn,
                    question_id=int(question_id),
                    part_features=analysis.part_features,
                    content_fingerprint=standard_difficulty.question_content_fingerprint(
                        dict(question)
                    ),
                    model_name=_clean_optional(model_name),
                )
        return True

    def apply_question_type_suggestion(
        self,
        question_id: int,
        *,
        suggested_type: str,
        question_type_confirmed: bool,
        reason: str,
        model_name: str | None,
        operation_id: str,
        suggested_subtype: str | None = None,
    ) -> dict[str, Any]:
        """应用联合分析的题型建议；教师确认的题型只登记冲突不改数据。

        子类建议（画图/计算/证明）只写 special_type 标签，题型枚举保持四类；
        已存在任一子类标签时不覆盖既有标注。
        """

        question_id = int(question_id)
        suggested = str(suggested_type or "").strip()
        if suggested not in QUESTION_TYPES:
            raise ValueError("suggested question type is not a supported type")
        subtype = str(suggested_subtype or "").strip() or None
        if subtype is not None and (
            subtype not in ESSAY_SUBTYPES or suggested != "解答题"
        ):
            raise ValueError("suggested essay subtype is not a supported subtype")
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT question_type FROM questions "
                "WHERE id = ? AND is_deleted = 0",
                (question_id,),
            ).fetchone()
            if row is None:
                raise QuestionWriteNotFound("Question not found")
            local_type = str(row["question_type"] or "").strip()
            audit: dict[str, Any] = {
                "local_type": local_type,
                "suggested_type": suggested,
                "suggested_subtype": subtype,
                "reason": str(reason or "").strip(),
                "model_name": str(model_name or "").strip(),
                "operation_id": str(operation_id or "").strip(),
            }
            if question_type_confirmed:
                audit["action"] = "conflict_only"
            else:
                if local_type == suggested:
                    audit["action"] = "unchanged"
                else:
                    conn.execute(
                        """
                        UPDATE questions
                        SET question_type = ?,
                            updated_at = datetime('now','localtime')
                        WHERE id = ? AND is_deleted = 0
                        """,
                        (suggested, question_id),
                    )
                    audit["action"] = "applied"
                if subtype is not None:
                    manual_subtype = conn.execute(
                        """
                        SELECT 1 FROM question_tags
                        WHERE question_id = ?
                          AND tag_type = 'special_type'
                          AND tag_value IN ('画图', '计算', '证明')
                          AND source = 'manual'
                        """,
                        (question_id,),
                    ).fetchone()
                    if manual_subtype is not None:
                        audit["subtype_action"] = "existing_kept"
                    else:
                        # 重打后模型重判的子类替代早期迁移/建议来源，仅保留手工标注。
                        conn.execute(
                            """
                            DELETE FROM question_tags
                            WHERE question_id = ?
                              AND tag_type = 'special_type'
                              AND tag_value IN ('画图', '计算', '证明')
                            """,
                            (question_id,),
                        )
                        conn.execute(
                            """
                            INSERT INTO question_tags (
                                question_id, tag_type, tag_value,
                                confidence, source, model_name
                            ) VALUES (?, 'special_type', ?, 0.8,
                                      'question_type_suggestion', ?)
                            """,
                            (
                                question_id,
                                subtype,
                                str(model_name or "").strip() or None,
                            ),
                        )
                        audit["subtype_action"] = "applied"
        return audit

    def save_question_preview(
        self,
        question_id: int,
        *,
        preview_type: str,
        source_file: str | None = None,
        page_number: int | None = None,
        image_path: str | None = None,
        bbox: dict[str, object] | None = None,
        status: str = "ready",
        message: str | None = None,
    ) -> None:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM question_previews
                WHERE question_id = ? AND preview_type = ?
                """,
                (int(question_id), str(preview_type)),
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
                    str(preview_type),
                    _clean_optional(source_file),
                    page_number,
                    _clean_optional(image_path),
                    json.dumps(dict(bbox or {}), ensure_ascii=False),
                    str(status),
                    _clean_optional(message),
                ),
            )

    def get_revision(self, question_id: int) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            revision = question_revision(conn, int(question_id))
        finally:
            conn.close()
        if revision is None:
            raise QuestionWriteNotFound("Question not found")
        return revision

    def update_paper_metadata(
        self,
        paper_id: int,
        *,
        expected_updated_at: str,
        metadata: PaperMetadataUpdate,
    ) -> PaperMetadataWriteResult:
        target = _normalize_paper_metadata(metadata)
        expected = str(expected_updated_at or "").strip()
        if not expected:
            raise ValueError("Paper metadata version is required")
        paper_id = int(paper_id)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_row = _load_paper_metadata(conn, paper_id)
            if current_row is None:
                raise PaperMetadataNotFound("Paper not found")
            current = _paper_metadata_result(current_row)
            if _same_paper_metadata(current, target):
                return current
            if current.updated_at != expected:
                raise PaperMetadataConflict(current.updated_at)
            next_updated_at = _next_paper_updated_at(current.updated_at)
            cursor = conn.execute(
                """
                UPDATE papers
                SET title = ?,
                    year = ?,
                    province = ?,
                    city = ?,
                    district = ?,
                    exam_type = ?,
                    grade = ?,
                    semester = ?,
                    folder_name = ?,
                    textbook_version = ?,
                    updated_at = ?
                WHERE id = ?
                  AND updated_at = ?
                  AND COALESCE(import_status, '') <> 'deleted'
                """,
                (
                    target.title,
                    target.year,
                    target.province,
                    target.city,
                    target.district,
                    target.exam_type,
                    target.grade,
                    target.semester,
                    target.folder_name,
                    target.textbook_version,
                    next_updated_at,
                    paper_id,
                    expected,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = _load_paper_metadata(conn, paper_id)
                if latest_row is None:
                    raise PaperMetadataNotFound("Paper not found")
                raise PaperMetadataConflict(
                    str(latest_row["updated_at"] or "")
                )
            updated_row = _load_paper_metadata(conn, paper_id)
            if updated_row is None:
                raise PaperMetadataNotFound("Paper not found")
            return _paper_metadata_result(updated_row)

    def set_paper_deleted(
        self,
        paper_id: int,
        *,
        expected_updated_at: str,
        deleted: bool,
    ) -> PaperStateWriteResult:
        expected = str(expected_updated_at or "").strip()
        if not expected:
            raise ValueError("Paper state version is required")
        paper_id = int(paper_id)
        desired = bool(deleted)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _load_paper_state(conn, paper_id)
            if current is None:
                raise PaperStateNotFound("Paper not found")
            current_deleted = _paper_row_is_deleted(current)
            if current_deleted == desired:
                return _paper_state_result(conn, current, desired=desired)
            current_updated_at = str(current["updated_at"] or "")
            if current_updated_at != expected:
                raise PaperStateConflict(
                    current_updated_at,
                    deleted=current_deleted,
                )
            next_updated_at = _next_paper_updated_at(current_updated_at)
            if desired:
                operation_id = uuid.uuid4().hex
                # A canonical question still referenced by a live paper keeps
                # its bank identity: the oldest surviving occurrence becomes
                # its new host instead of being trashed with this paper.
                _rehome_surviving_questions(conn, (paper_id,), None)
                question_cursor = conn.execute(
                    """
                    UPDATE questions
                    SET is_deleted = 1,
                        deleted_at = COALESCE(deleted_at, ?),
                        paper_delete_operation_id = ?,
                        updated_at = ?
                    WHERE paper_id = ?
                      AND COALESCE(is_deleted, 0) = 0
                    """,
                    (
                        next_updated_at,
                        operation_id,
                        next_updated_at,
                        paper_id,
                    ),
                )
                affected_question_count = int(question_cursor.rowcount)
                paper_cursor = conn.execute(
                    """
                    UPDATE papers
                    SET import_status = 'deleted',
                        deleted_at = ?,
                        deleted_from_status = import_status,
                        delete_operation_id = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND updated_at = ?
                      AND COALESCE(import_status, '') <> 'deleted'
                    """,
                    (
                        next_updated_at,
                        operation_id,
                        next_updated_at,
                        paper_id,
                        expected,
                    ),
                )
            else:
                operation_id = str(current["delete_operation_id"] or "")
                affected_question_count = 0
                if operation_id:
                    count_row = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM questions
                        WHERE paper_id = ?
                          AND paper_delete_operation_id = ?
                        """,
                        (paper_id, operation_id),
                    ).fetchone()
                    affected_question_count = int(count_row[0] or 0)
                    conn.execute(
                        """
                        UPDATE questions
                        SET is_deleted = 0,
                            deleted_at = NULL,
                            paper_delete_operation_id = NULL,
                            updated_at = ?
                        WHERE paper_id = ?
                          AND paper_delete_operation_id = ?
                        """,
                        (next_updated_at, paper_id, operation_id),
                    )
                restored_status = (
                    str(current["deleted_from_status"] or "").strip()
                    or "needs_review"
                )
                paper_cursor = conn.execute(
                    """
                    UPDATE papers
                    SET import_status = ?,
                        deleted_at = NULL,
                        deleted_from_status = NULL,
                        delete_operation_id = NULL,
                        updated_at = ?
                    WHERE id = ?
                      AND updated_at = ?
                      AND COALESCE(import_status, '') = 'deleted'
                    """,
                    (
                        restored_status,
                        next_updated_at,
                        paper_id,
                        expected,
                    ),
                )
            if paper_cursor.rowcount != 1:
                latest = _load_paper_state(conn, paper_id)
                if latest is None:
                    raise PaperStateNotFound("Paper not found")
                raise PaperStateConflict(
                    str(latest["updated_at"] or ""),
                    deleted=_paper_row_is_deleted(latest),
                )
            updated = _load_paper_state(conn, paper_id)
            if updated is None:
                raise PaperStateNotFound("Paper not found")
            return PaperStateWriteResult(
                id=paper_id,
                deleted=desired,
                import_status=str(updated["import_status"] or ""),
                updated_at=str(updated["updated_at"] or ""),
                affected_question_count=affected_question_count,
            )

    def preview_paper_permanent_delete(
        self,
        selections: Iterable[PaperPermanentDeleteSelection],
    ) -> PaperPermanentDeleteImpact:
        _recover_pending_paper_deletes(self.db_path, self.data_root)
        normalized = _normalize_paper_delete_selections(selections)
        with connect(self.db_path) as conn:
            paper_rows, question_ids = _validate_permanent_paper_selection(
                conn, normalized
            )
            owned, shared = _paper_delete_file_candidates(
                conn, self.data_root, paper_rows, question_ids
            )
            counts = _preview_paper_database_delete(
                conn,
                tuple(selection.id for selection in normalized),
            )
        taxonomy_proposal_count = 0
        governance = self._taxonomy_governance
        if governance is not None and question_ids:
            taxonomy_proposal_count = int(
                governance.prune_proposals_for_deleted_questions(
                    question_ids,
                    question_exists=self._live_question_exists,
                    dry_run=True,
                )["removed_pending_proposals"]
            )
        return PaperPermanentDeleteImpact(
            paper_count=len(paper_rows),
            question_count=len(question_ids),
            tag_count=counts["tags"],
            analysis_record_count=counts["analysis"],
            training_link_count=counts["training"],
            knowledge_graph_link_count=counts["graph"],
            owned_file_count=len(owned),
            shared_file_count=len(shared),
            taxonomy_proposal_count=taxonomy_proposal_count,
            permanent_delete_phrase=f"彻底删除 {len(paper_rows)} 份试卷",
        )

    def permanently_delete_papers(
        self,
        selections: Iterable[PaperPermanentDeleteSelection],
        *,
        confirmation_phrase: str,
        request_token: str,
    ) -> PaperPermanentDeleteResult:
        _recover_pending_paper_deletes(self.db_path, self.data_root)
        normalized = _normalize_paper_delete_selections(selections)
        expected_phrase = f"彻底删除 {len(normalized)} 份试卷"
        if str(confirmation_phrase or "") != expected_phrase:
            raise PaperPermanentDeleteConfirmationMismatch(
                "Permanent paper deletion confirmation does not match"
            )
        token = str(request_token or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Permanent paper deletion request token is invalid")
        request_fingerprint = _paper_delete_request_fingerprint(
            normalized,
            confirmation_phrase=confirmation_phrase,
        )
        staging_root = (
            self.data_root / "question_bank" / ".paper-delete-staging" / token
        )
        with _shared_request_lock(staging_root):
            with connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                receipt = _load_paper_delete_receipt(
                    conn,
                    token,
                    request_fingerprint=request_fingerprint,
                )
                if receipt is not None:
                    if receipt.storage_cleanup_pending and not staging_root.exists():
                        receipt = _paper_delete_result_with_cleanup(
                            receipt,
                            pending=False,
                        )
                        _store_paper_delete_receipt_result(
                            conn,
                            token,
                            receipt,
                        )
                    conn.commit()
                    return receipt
                paper_rows, question_ids = _validate_permanent_paper_selection(
                    conn, normalized
                )
                owned, shared = _paper_delete_file_candidates(
                    conn, self.data_root, paper_rows, question_ids
                )
                staged = _stage_paper_delete_files(
                    staging_root,
                    owned,
                    [selection.id for selection in normalized],
                    data_root=self.data_root,
                )
                try:
                    paper_ids = tuple(selection.id for selection in normalized)
                    counts = _execute_paper_database_delete(conn, paper_ids)
                    result = PaperPermanentDeleteResult(
                        deleted_paper_ids=paper_ids,
                        deleted_question_count=len(question_ids),
                        deleted_tag_count=counts["tags"],
                        deleted_analysis_record_count=counts["analysis"],
                        removed_training_link_count=counts["training"],
                        removed_knowledge_graph_link_count=counts["graph"],
                        deleted_file_count=len(owned),
                        skipped_shared_file_count=len(shared),
                        storage_cleanup_pending=True,
                    )
                    _insert_paper_delete_receipt(
                        conn,
                        token,
                        request_fingerprint=request_fingerprint,
                        result=result,
                    )
                    conn.commit()
                except sqlite3.IntegrityError as exc:
                    _restore_staged_paper_files(staged)
                    shutil.rmtree(staging_root, ignore_errors=True)
                    raise PaperPermanentDeleteDependencyConflict(
                        "Paper deletion is blocked by dependent question-bank data"
                    ) from exc
                except Exception:
                    _restore_staged_paper_files(staged)
                    shutil.rmtree(staging_root, ignore_errors=True)
                    raise
        governance = self._taxonomy_governance
        if governance is not None and question_ids:
            # The paper deletion is committed and replay-protected by the
            # receipt above; the governance prune carries the same request
            # token so a replayed call cannot trim or count twice.
            governance.prune_proposals_for_deleted_questions(
                question_ids,
                question_exists=self._live_question_exists,
                request_token=token,
            )
        cleanup_pending = False
        try:
            shutil.rmtree(staging_root)
        except FileNotFoundError:
            pass
        except OSError:
            cleanup_pending = True
        result = _paper_delete_result_with_cleanup(
            result,
            pending=cleanup_pending,
        )
        if not cleanup_pending:
            try:
                with connect(self.db_path) as conn:
                    _store_paper_delete_receipt_result(conn, token, result)
            except sqlite3.Error:
                # The deletion is already committed. Keep the response
                # conservative; a same-token retry reconciles the receipt.
                result = _paper_delete_result_with_cleanup(
                    result,
                    pending=True,
                )
        return result

    def replace_tags(
        self,
        question_id: int,
        *,
        expected_revision: str,
        tags: Iterable[ConfirmedQuestionTag],
    ) -> QuestionWriteResult:
        normalized = _normalize_tags(tags)
        question_id = int(question_id)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = _load_question_state(conn, question_id)
            if state is None or bool(state["is_deleted"]):
                raise QuestionWriteNotFound("Question not found")
            current_revision = question_revision(conn, question_id)
            assert current_revision is not None
            if _is_exact_manual_tag_state(conn, question_id, normalized):
                return QuestionWriteResult(
                    question_id=question_id,
                    revision=current_revision,
                    deleted=False,
                    tags=normalized,
                )
            if str(expected_revision) != current_revision:
                raise QuestionWriteConflict(current_revision)

            conn.execute(
                "DELETE FROM question_tags WHERE question_id = ?",
                (question_id,),
            )
            conn.executemany(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, confidence, source, model_name
                ) VALUES (?, ?, ?, ?, 'manual', NULL)
                """,
                [
                    (question_id, tag.tag_type, tag.tag_value, tag.confidence)
                    for tag in normalized
                ],
            )
            _touch_question(conn, question_id)
            updated_revision = question_revision(conn, question_id)
            assert updated_revision is not None

        return QuestionWriteResult(
            question_id=question_id,
            revision=updated_revision,
            deleted=False,
            tags=normalized,
        )

    def add_tags(
        self,
        question_id: int,
        *,
        tags: Iterable[ConfirmedQuestionTag],
    ) -> QuestionWriteResult:
        """Add teacher-confirmed tags without replacing unrelated current tags."""

        additions = _normalize_tags(tags)
        question_id = int(question_id)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = _load_question_state(conn, question_id)
            if state is None or bool(state["is_deleted"]):
                raise QuestionWriteNotFound("Question not found")
            current = _load_current_tags(conn, question_id)
            existing = {(tag.tag_type, tag.tag_value) for tag in current}
            missing = tuple(
                tag
                for tag in additions
                if (tag.tag_type, tag.tag_value) not in existing
            )
            current_revision = question_revision(conn, question_id)
            assert current_revision is not None
            if not missing:
                return QuestionWriteResult(
                    question_id=question_id,
                    revision=current_revision,
                    deleted=False,
                    tags=current,
                )
            conn.executemany(
                """
                INSERT INTO question_tags (
                    question_id, tag_type, tag_value, confidence, source, model_name
                ) VALUES (?, ?, ?, ?, 'manual', NULL)
                """,
                [
                    (
                        question_id,
                        tag.tag_type,
                        tag.tag_value,
                        tag.confidence,
                    )
                    for tag in missing
                ],
            )
            _touch_question(conn, question_id)
            updated_revision = question_revision(conn, question_id)
            assert updated_revision is not None
            updated_tags = _load_current_tags(conn, question_id)

        return QuestionWriteResult(
            question_id=question_id,
            revision=updated_revision,
            deleted=False,
            tags=updated_tags,
        )

    def set_deleted(
        self,
        question_id: int,
        *,
        expected_revision: str,
        deleted: bool,
    ) -> QuestionWriteResult:
        question_id = int(question_id)
        desired = bool(deleted)
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = _load_question_state(conn, question_id)
            if state is None:
                raise QuestionWriteNotFound("Question not found")
            current_revision = question_revision(conn, question_id)
            assert current_revision is not None
            tags = _load_current_tags(conn, question_id)
            if bool(state["is_deleted"]) == desired:
                return QuestionWriteResult(
                    question_id=question_id,
                    revision=current_revision,
                    deleted=desired,
                    tags=tags,
                )
            if str(expected_revision) != current_revision:
                raise QuestionWriteConflict(current_revision)
            conn.execute(
                """
                UPDATE questions
                SET is_deleted = ?,
                    deleted_at = CASE
                        WHEN ? = 1 THEN strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                        ELSE NULL
                    END,
                    updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                WHERE id = ?
                """,
                (int(desired), int(desired), question_id),
            )
            updated_revision = question_revision(conn, question_id)
            assert updated_revision is not None
        return QuestionWriteResult(
            question_id=question_id,
            revision=updated_revision,
            deleted=desired,
            tags=tags,
        )

    def stage_upload(self, *, filename: str, content: bytes) -> StagedImportUpload:
        if not content:
            raise ValueError("Question import upload is empty")
        if len(content) > self.max_upload_bytes:
            raise QuestionImportTooLarge("Question import upload is too large")
        safe_filename = _safe_client_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        if suffix not in {".docx", ".pdf"}:
            raise QuestionImportTypeNotSupported(
                "Question import file type is not supported"
            )
        upload_id = uuid.uuid4().hex
        uploads_root = self._controlled_staging_path("uploads")
        uploads_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=uploads_root))
        destination = uploads_root / upload_id
        digest = hashlib.sha256(content).hexdigest()
        upload = StagedImportUpload(
            upload_id=upload_id,
            filename=safe_filename,
            suffix=suffix,
            size=len(content),
            sha256=digest,
        )
        try:
            (temporary / f"source{suffix}").write_bytes(content)
            (temporary / "upload.json").write_text(
                json.dumps(upload.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return upload

    async def stage_upload_stream(
        self,
        *,
        filename: str,
        chunks: AsyncIterable[bytes],
    ) -> StagedImportUpload:
        safe_filename = _safe_client_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        if suffix not in {".docx", ".pdf"}:
            raise QuestionImportTypeNotSupported(
                "Question import file type is not supported"
            )
        upload_id = uuid.uuid4().hex
        uploads_root = self._controlled_staging_path("uploads")
        uploads_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=uploads_root))
        destination = uploads_root / upload_id
        digest = hashlib.sha256()
        size = 0

        def write_chunk(handle: Any, chunk: bytes) -> None:
            # Disk writes and hashing run in the worker thread pool so a long
            # upload cannot stall the API event loop for other requests.
            digest.update(chunk)
            handle.write(chunk)

        try:
            with (temporary / f"source{suffix}").open("wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise QuestionImportTooLarge(
                            "Question import upload is too large"
                        )
                    await anyio.to_thread.run_sync(
                        write_chunk, handle, chunk
                    )
            if size == 0:
                raise ValueError("Question import upload is empty")
            upload = StagedImportUpload(
                upload_id=upload_id,
                filename=safe_filename,
                suffix=suffix,
                size=size,
                sha256=digest.hexdigest(),
            )
            (temporary / "upload.json").write_text(
                json.dumps(upload.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return upload

    def create_import_request(self, *, upload_id: str) -> QuestionImportRequest:
        upload, _source_path = self._load_staged_upload(upload_id)

        request = QuestionImportRequest(
            request_id=hashlib.sha256(
                f"question-import:{upload.upload_id}".encode("ascii")
            ).hexdigest()[:32],
            upload_id=upload.upload_id,
            filename=upload.filename,
            size=upload.size,
            sha256=upload.sha256,
        )
        requests_root = self._controlled_staging_path("requests")
        requests_root.mkdir(parents=True, exist_ok=True)
        destination = requests_root / f"{request.request_id}.json"
        with _shared_request_lock(destination):
            if destination.exists():
                return _load_existing_import_request(destination, request)
            temporary = requests_root / f".{request.request_id}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_text(
                    json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(temporary, destination)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        return request

    def load_import_resource(self, request_id: str) -> QuestionImportResource:
        normalized_request_id = str(request_id).strip().casefold()
        if re.fullmatch(r"[0-9a-f]{32}", normalized_request_id) is None:
            raise QuestionImportUploadNotFound("Question import request not found")
        request_path = self._controlled_staging_path(
            "requests", f"{normalized_request_id}.json"
        )
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            upload, source_path = self._load_staged_upload(str(payload["upload_id"]))
            expected_request_id = hashlib.sha256(
                f"question-import:{upload.upload_id}".encode("ascii")
            ).hexdigest()[:32]
            if normalized_request_id != expected_request_id:
                raise QuestionImportUploadNotFound(
                    "Question import request not found"
                )
            expected = QuestionImportRequest(
                request_id=expected_request_id,
                upload_id=upload.upload_id,
                filename=upload.filename,
                size=upload.size,
                sha256=upload.sha256,
            )
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            QuestionImportUploadNotFound,
        ) as exc:
            raise QuestionImportUploadNotFound(
                "Question import request not found"
            ) from exc
        if payload != expected.to_dict():
            raise QuestionImportUploadNotFound("Question import request not found")
        return QuestionImportResource(
            request_id=expected.request_id,
            upload_id=upload.upload_id,
            filename=upload.filename,
            suffix=upload.suffix,
            size=upload.size,
            sha256=upload.sha256,
            source_path=source_path,
        )

    def _load_staged_upload(
        self,
        upload_id: str,
    ) -> tuple[StagedImportUpload, Path]:
        normalized_upload_id = str(upload_id).strip().casefold()
        if re.fullmatch(r"[0-9a-f]{32}", normalized_upload_id) is None:
            raise QuestionImportUploadNotFound("Question import upload not found")
        upload_root = self._controlled_staging_path("uploads", normalized_upload_id)
        manifest_path = upload_root / "upload.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_filename = str(manifest["filename"])
            safe_filename = _safe_client_filename(raw_filename)
            upload = StagedImportUpload(
                upload_id=str(manifest["upload_id"]),
                filename=safe_filename,
                suffix=str(manifest["suffix"]),
                size=int(manifest["size"]),
                sha256=str(manifest["sha256"]),
            )
            source_path = upload_root / f"source{upload.suffix}"
            actual_size, actual_sha256 = _file_size_and_sha256(source_path)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QuestionImportUploadNotFound(
                "Question import upload not found"
            ) from exc
        if (
            upload.upload_id != normalized_upload_id
            or upload.filename != raw_filename
            or upload.suffix not in {".docx", ".pdf"}
            or Path(upload.filename).suffix.casefold() != upload.suffix
            or actual_size != upload.size
            or actual_sha256 != upload.sha256
        ):
            raise QuestionImportUploadNotFound("Question import upload not found")
        return upload, source_path

    def _controlled_staging_path(self, *parts: str) -> Path:
        self.data_root.mkdir(parents=True, exist_ok=True)
        canonical_root = self.data_root.resolve()
        target = self.data_root.joinpath(
            "question_bank",
            "import_staging",
            *parts,
        )
        try:
            target.resolve(strict=False).relative_to(canonical_root)
        except ValueError as exc:
            raise QuestionImportStorageForbidden(
                "Question import storage is outside the data root"
            ) from exc
        return target


def _normalize_tags(
    tags: Iterable[ConfirmedQuestionTag],
) -> tuple[ConfirmedQuestionTag, ...]:
    normalized: list[ConfirmedQuestionTag] = []
    seen: set[tuple[str, str]] = set()
    for item in tags:
        tag_type = str(item.tag_type).strip()
        # skill 是读取层的展示类型；写回时仍按 knowledge_point 存储。
        if tag_type == "skill":
            tag_type = "knowledge_point"
        tag_value = str(item.tag_value).strip()
        if tag_type not in ALLOWED_TAG_TYPES:
            raise ValueError("Unsupported question tag type")
        if not tag_value:
            raise ValueError("Question tag value is required")
        if len(tag_value) > MAX_TAG_LENGTH:
            raise ValueError("Question tag value is too long")
        key = (tag_type, tag_value)
        if key in seen:
            continue
        seen.add(key)
        confidence = item.confidence
        if confidence is not None:
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("Question tag confidence is out of range")
        normalized.append(ConfirmedQuestionTag(tag_type, tag_value, confidence))
    return tuple(normalized)


def _normalize_paper_metadata(
    metadata: PaperMetadataUpdate,
) -> PaperMetadataUpdate:
    title = str(metadata.title or "").strip()
    if not title:
        raise ValueError("Paper title is required")
    if len(title) > 255:
        raise ValueError("Paper title is too long")

    def optional(value: object, *, max_length: int) -> str | None:
        clean = str(value or "").strip()
        if len(clean) > max_length:
            raise ValueError("Paper metadata value is too long")
        return clean or None

    return PaperMetadataUpdate(
        title=title,
        year=optional(metadata.year, max_length=24),
        province=optional(metadata.province, max_length=48),
        city=optional(metadata.city, max_length=48),
        district=optional(metadata.district, max_length=48),
        exam_type=optional(metadata.exam_type, max_length=48),
        grade=optional(metadata.grade, max_length=48),
        semester=optional(metadata.semester, max_length=48),
        folder_name=optional(metadata.folder_name, max_length=80),
        textbook_version=optional(
            metadata.textbook_version,
            max_length=100,
        ),
    )


def _load_paper_metadata(
    conn: sqlite3.Connection,
    paper_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            id, title, year, province, city, district, exam_type, grade,
            semester, folder_name, textbook_version, updated_at
        FROM papers
        WHERE id = ?
          AND COALESCE(import_status, '') <> 'deleted'
        """,
        (int(paper_id),),
    ).fetchone()


def _load_paper_state(
    conn: sqlite3.Connection,
    paper_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            id, import_status, updated_at, deleted_at,
            deleted_from_status, delete_operation_id
        FROM papers
        WHERE id = ?
        """,
        (int(paper_id),),
    ).fetchone()


def _paper_row_is_deleted(row: sqlite3.Row) -> bool:
    return str(row["import_status"] or "") == "deleted"


def _paper_state_result(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    desired: bool,
) -> PaperStateWriteResult:
    affected_question_count = 0
    operation_id = str(row["delete_operation_id"] or "")
    if desired and operation_id:
        count_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM questions
            WHERE paper_id = ?
              AND paper_delete_operation_id = ?
            """,
            (int(row["id"]), operation_id),
        ).fetchone()
        affected_question_count = int(count_row[0] or 0)
    return PaperStateWriteResult(
        id=int(row["id"]),
        deleted=desired,
        import_status=str(row["import_status"] or ""),
        updated_at=str(row["updated_at"] or ""),
        affected_question_count=affected_question_count,
    )


def _paper_metadata_result(row: sqlite3.Row) -> PaperMetadataWriteResult:
    return PaperMetadataWriteResult(
        id=int(row["id"]),
        title=str(row["title"] or ""),
        year=_optional_row_text(row["year"]),
        province=_optional_row_text(row["province"]),
        city=_optional_row_text(row["city"]),
        district=_optional_row_text(row["district"]),
        exam_type=_optional_row_text(row["exam_type"]),
        grade=_optional_row_text(row["grade"]),
        semester=_optional_row_text(row["semester"]),
        folder_name=_optional_row_text(row["folder_name"]),
        textbook_version=_optional_row_text(row["textbook_version"]),
        updated_at=str(row["updated_at"] or ""),
    )


def _optional_row_text(value: object) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _same_paper_metadata(
    current: PaperMetadataWriteResult,
    target: PaperMetadataUpdate,
) -> bool:
    return all(
        getattr(current, field) == getattr(target, field)
        for field in (
            "title",
            "year",
            "province",
            "city",
            "district",
            "exam_type",
            "grade",
            "semester",
            "folder_name",
            "textbook_version",
        )
    )


def _next_paper_updated_at(current: str) -> str:
    now = datetime.now()
    try:
        current_time = datetime.fromisoformat(str(current))
    except ValueError:
        current_time = None
    if current_time is not None and now <= current_time:
        now = current_time + timedelta(microseconds=1)
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")


def _load_question_state(
    conn: sqlite3.Connection,
    question_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, is_deleted, updated_at FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()


def _is_exact_manual_tag_state(
    conn: sqlite3.Connection,
    question_id: int,
    target: tuple[ConfirmedQuestionTag, ...],
) -> bool:
    rows = conn.execute(
        """
        SELECT tag_type, tag_value, confidence, source, model_name
        FROM question_tags
        WHERE question_id = ?
        ORDER BY id
        """,
        (question_id,),
    ).fetchall()
    current = tuple(
        ConfirmedQuestionTag(
            str(row["tag_type"]),
            str(row["tag_value"]),
            float(row["confidence"]) if row["confidence"] is not None else None,
        )
        for row in rows
    )
    order_key = lambda tag: (
        tag.tag_type,
        tag.tag_value,
        -1.0 if tag.confidence is None else tag.confidence,
    )
    return sorted(current, key=order_key) == sorted(target, key=order_key) and all(
        row["source"] == "manual" and row["model_name"] is None for row in rows
    )


def _load_current_tags(
    conn: sqlite3.Connection,
    question_id: int,
) -> tuple[ConfirmedQuestionTag, ...]:
    rows = conn.execute(
        """
        SELECT tag_type, tag_value, confidence
        FROM question_tags
        WHERE question_id = ?
        ORDER BY id
        """,
        (question_id,),
    ).fetchall()
    return tuple(
        ConfirmedQuestionTag(
            str(row["tag_type"]),
            str(row["tag_value"]),
            float(row["confidence"]) if row["confidence"] is not None else None,
        )
        for row in rows
    )


def _touch_question(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute(
        """
        UPDATE questions
        SET updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
        WHERE id = ?
        """,
        (question_id,),
    )


def _safe_client_filename(filename: str) -> str:
    normalized = str(filename).replace("\\", "/")
    safe = normalized.rsplit("/", 1)[-1].strip()
    if not safe or safe in {".", ".."}:
        raise ValueError("Question import filename is required")
    return safe


def _load_existing_import_request(
    path: Path,
    expected: QuestionImportRequest,
) -> QuestionImportRequest:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuestionImportUploadNotFound(
            "Question import request is invalid"
        ) from exc
    if existing != expected.to_dict():
        raise QuestionImportUploadNotFound("Question import request is invalid")
    return expected


def _shared_request_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _REQUEST_LOCKS_GUARD:
        return _REQUEST_LOCKS.setdefault(key, threading.Lock())


def _file_size_and_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _normalize_paper_delete_selections(
    selections: Iterable[PaperPermanentDeleteSelection],
) -> tuple[PaperPermanentDeleteSelection, ...]:
    normalized = tuple(
        PaperPermanentDeleteSelection(
            id=int(selection.id),
            expected_updated_at=str(selection.expected_updated_at or "").strip(),
        )
        for selection in selections
    )
    if not normalized or any(
        selection.id <= 0 or not selection.expected_updated_at
        for selection in normalized
    ):
        raise ValueError("Permanent paper deletion selection is invalid")
    if len({selection.id for selection in normalized}) != len(normalized):
        raise ValueError("Permanent paper deletion selection contains duplicates")
    return normalized


def _paper_delete_request_fingerprint(
    selections: tuple[PaperPermanentDeleteSelection, ...],
    *,
    confirmation_phrase: str,
) -> str:
    payload = {
        "confirmation_phrase": str(confirmation_phrase or ""),
        "selections": [
            {
                "id": selection.id,
                "expected_updated_at": selection.expected_updated_at,
            }
            for selection in sorted(selections, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _paper_delete_result_payload(
    result: PaperPermanentDeleteResult,
) -> dict[str, object]:
    return {
        "deleted_paper_ids": list(result.deleted_paper_ids),
        "deleted_question_count": result.deleted_question_count,
        "deleted_tag_count": result.deleted_tag_count,
        "deleted_analysis_record_count": result.deleted_analysis_record_count,
        "removed_training_link_count": result.removed_training_link_count,
        "removed_knowledge_graph_link_count": (
            result.removed_knowledge_graph_link_count
        ),
        "deleted_file_count": result.deleted_file_count,
        "skipped_shared_file_count": result.skipped_shared_file_count,
        "storage_cleanup_pending": result.storage_cleanup_pending,
    }


def _paper_delete_result_from_payload(
    payload: object,
) -> PaperPermanentDeleteResult:
    if not isinstance(payload, dict):
        raise PaperPermanentDeleteConflict(
            "Permanent deletion receipt is invalid"
        )
    try:
        return PaperPermanentDeleteResult(
            deleted_paper_ids=tuple(
                int(value) for value in payload["deleted_paper_ids"]
            ),
            deleted_question_count=int(payload["deleted_question_count"]),
            deleted_tag_count=int(payload["deleted_tag_count"]),
            deleted_analysis_record_count=int(
                payload.get("deleted_analysis_record_count", 0)
            ),
            removed_training_link_count=int(
                payload["removed_training_link_count"]
            ),
            removed_knowledge_graph_link_count=int(
                payload["removed_knowledge_graph_link_count"]
            ),
            deleted_file_count=int(payload["deleted_file_count"]),
            skipped_shared_file_count=int(
                payload["skipped_shared_file_count"]
            ),
            storage_cleanup_pending=bool(
                payload["storage_cleanup_pending"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperPermanentDeleteConflict(
            "Permanent deletion receipt is invalid"
        ) from exc


def _paper_delete_result_with_cleanup(
    result: PaperPermanentDeleteResult,
    *,
    pending: bool,
) -> PaperPermanentDeleteResult:
    payload = _paper_delete_result_payload(result)
    payload["storage_cleanup_pending"] = bool(pending)
    return _paper_delete_result_from_payload(payload)


def _load_paper_delete_receipt(
    conn: sqlite3.Connection,
    request_token: str,
    *,
    request_fingerprint: str,
) -> PaperPermanentDeleteResult | None:
    row = conn.execute(
        """
        SELECT request_fingerprint, result_json
        FROM paper_permanent_delete_receipts
        WHERE request_token = ?
        """,
        (request_token,),
    ).fetchone()
    if row is None:
        return None
    if str(row["request_fingerprint"]) != request_fingerprint:
        raise PaperPermanentDeleteConflict(
            "Permanent deletion request token was already used"
        )
    try:
        payload = json.loads(str(row["result_json"]))
    except json.JSONDecodeError as exc:
        raise PaperPermanentDeleteConflict(
            "Permanent deletion receipt is invalid"
        ) from exc
    return _paper_delete_result_from_payload(payload)


def _insert_paper_delete_receipt(
    conn: sqlite3.Connection,
    request_token: str,
    *,
    request_fingerprint: str,
    result: PaperPermanentDeleteResult,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_permanent_delete_receipts (
            request_token, request_fingerprint, result_json
        ) VALUES (?, ?, ?)
        """,
        (
            request_token,
            request_fingerprint,
            json.dumps(
                _paper_delete_result_payload(result),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def _store_paper_delete_receipt_result(
    conn: sqlite3.Connection,
    request_token: str,
    result: PaperPermanentDeleteResult,
) -> None:
    updated = conn.execute(
        """
        UPDATE paper_permanent_delete_receipts
        SET result_json = ?
        WHERE request_token = ?
        """,
        (
            json.dumps(
                _paper_delete_result_payload(result),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            request_token,
        ),
    )
    if updated.rowcount != 1:
        raise PaperPermanentDeleteConflict(
            "Permanent deletion receipt is missing"
        )


def _validate_permanent_paper_selection(
    conn: sqlite3.Connection,
    selections: tuple[PaperPermanentDeleteSelection, ...],
) -> tuple[list[sqlite3.Row], list[int]]:
    paper_ids = tuple(selection.id for selection in selections)
    placeholders = ",".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"SELECT id, source_file, import_status, updated_at FROM papers "
        f"WHERE id IN ({placeholders}) ORDER BY id",
        paper_ids,
    ).fetchall()
    if len(rows) != len(selections):
        raise PaperStateNotFound("Paper not found")
    expected = {selection.id: selection.expected_updated_at for selection in selections}
    for row in rows:
        if str(row["updated_at"] or "") != expected[int(row["id"])]:
            raise PaperPermanentDeleteConflict(
                "Paper changed before permanent deletion"
            )
    question_ids = [
        int(row[0])
        for row in conn.execute(
            f"SELECT id FROM questions WHERE paper_id IN ({placeholders}) ORDER BY id",
            paper_ids,
        ).fetchall()
    ]
    return list(rows), question_ids


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


@dataclass
class _PaperDatabaseDeleteCounts:
    questions: int = 0
    tags: int = 0
    analysis: int = 0
    training: int = 0
    graph: int = 0

    def record(self, table: str, affected: int) -> None:
        count = max(0, int(affected))
        if table == "questions":
            self.questions += count
        elif table == "question_tags":
            self.tags += count
        elif (
            table == "question_analysis_items"
            or table == "question_solution_evidence_versions"
            or table.startswith("training_criterion_")
        ):
            self.analysis += count
        elif table in {"training_set_items", "training_task_items"}:
            self.training += count
        elif table == "grading_question_links":
            self.graph += count

    def as_dict(self) -> dict[str, int]:
        return {
            "questions": self.questions,
            "tags": self.tags,
            "analysis": self.analysis,
            "training": self.training,
            "graph": self.graph,
        }


@dataclass(frozen=True)
class _IncomingForeignKey:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    on_delete: str
    nullable: bool


def _quote_sqlite_identifier(value: str) -> str:
    text = str(value or "")
    if not text or "\x00" in text:
        raise PaperPermanentDeleteDependencyConflict(
            "Question-bank dependency metadata is invalid"
        )
    return '"' + text.replace('"', '""') + '"'


def _incoming_foreign_keys(
    conn: sqlite3.Connection,
    parent_table: str,
) -> tuple[_IncomingForeignKey, ...]:
    dependencies: list[_IncomingForeignKey] = []
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    for child_table in tables:
        table_info = {
            str(row[1]): (bool(row[3]), bool(row[5]))
            for row in conn.execute(
                f"PRAGMA table_info({_quote_sqlite_identifier(child_table)})"
            ).fetchall()
        }
        foreign_keys = conn.execute(
            f"PRAGMA foreign_key_list({_quote_sqlite_identifier(child_table)})"
        ).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = {}
        for foreign_key in foreign_keys:
            grouped.setdefault(int(foreign_key[0]), []).append(foreign_key)
        for parts in grouped.values():
            if str(parts[0][2]) != parent_table:
                continue
            if len(parts) != 1 or int(parts[0][1]) != 0:
                raise PaperPermanentDeleteDependencyConflict(
                    "Composite question-bank dependencies cannot be deleted safely"
                )
            foreign_key = parts[0]
            child_column = str(foreign_key[3])
            parent_column = str(foreign_key[4] or "id")
            column_state = table_info.get(child_column)
            if column_state is None:
                raise PaperPermanentDeleteDependencyConflict(
                    "Question-bank dependency metadata is incomplete"
                )
            not_null, primary_key = column_state
            dependencies.append(_IncomingForeignKey(
                child_table=child_table,
                child_column=child_column,
                parent_table=parent_table,
                parent_column=parent_column,
                on_delete=str(foreign_key[6] or "NO ACTION").upper(),
                nullable=not not_null and not primary_key,
            ))
    return tuple(dependencies)


def _dependency_action(dependency: _IncomingForeignKey) -> str:
    if dependency.on_delete == "CASCADE":
        return "delete"
    if dependency.on_delete == "SET NULL":
        return "set_null"
    if dependency.on_delete not in {"NO ACTION", "RESTRICT"}:
        raise PaperPermanentDeleteDependencyConflict(
            "Question-bank dependency uses an unsupported delete rule"
        )
    if (
        dependency.parent_table == "papers"
        and dependency.child_table == "questions"
        and dependency.child_column == "paper_id"
    ):
        return "delete"
    return "set_null" if dependency.nullable else "delete"


def _selected_column_values(
    conn: sqlite3.Connection,
    *,
    table: str,
    selected_column: str,
    selected_values: tuple[object, ...],
    projected_column: str,
) -> tuple[object, ...]:
    if not selected_values:
        return ()
    placeholders = ",".join("?" for _ in selected_values)
    table_sql = _quote_sqlite_identifier(table)
    selected_sql = _quote_sqlite_identifier(selected_column)
    projected_sql = _quote_sqlite_identifier(projected_column)
    return tuple(
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT {projected_sql} FROM {table_sql} "
            f"WHERE {selected_sql} IN ({placeholders}) "
            f"AND {projected_sql} IS NOT NULL",
            selected_values,
        ).fetchall()
    )


def _delete_selected_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    selected_column: str,
    selected_values: tuple[object, ...],
    counts: _PaperDatabaseDeleteCounts,
    active: set[tuple[str, str, tuple[object, ...]]],
) -> None:
    if not selected_values:
        return
    selection = (table, selected_column, selected_values)
    if selection in active:
        raise PaperPermanentDeleteDependencyConflict(
            "Question-bank dependencies contain an unsafe delete cycle"
        )
    active.add(selection)
    try:
        for dependency in _incoming_foreign_keys(conn, table):
            parent_values = _selected_column_values(
                conn,
                table=table,
                selected_column=selected_column,
                selected_values=selected_values,
                projected_column=dependency.parent_column,
            )
            if not parent_values:
                continue
            child_table_sql = _quote_sqlite_identifier(
                dependency.child_table
            )
            child_column_sql = _quote_sqlite_identifier(
                dependency.child_column
            )
            placeholders = ",".join("?" for _ in parent_values)
            action = _dependency_action(dependency)
            if action == "set_null":
                cursor = conn.execute(
                    f"UPDATE {child_table_sql} SET {child_column_sql} = NULL "
                    f"WHERE {child_column_sql} IN ({placeholders})",
                    parent_values,
                )
                counts.record(dependency.child_table, cursor.rowcount)
                continue
            _delete_selected_rows(
                conn,
                table=dependency.child_table,
                selected_column=dependency.child_column,
                selected_values=parent_values,
                counts=counts,
                active=active,
            )
        table_sql = _quote_sqlite_identifier(table)
        selected_sql = _quote_sqlite_identifier(selected_column)
        placeholders = ",".join("?" for _ in selected_values)
        cursor = conn.execute(
            f"DELETE FROM {table_sql} "
            f"WHERE {selected_sql} IN ({placeholders})",
            selected_values,
        )
        counts.record(table, cursor.rowcount)
    finally:
        active.remove(selection)


def _rehome_surviving_questions(
    conn: sqlite3.Connection,
    paper_ids: tuple[int, ...],
    counts: _PaperDatabaseDeleteCounts | None = None,
) -> None:
    """Keep canonical questions that still appear in surviving papers.

    A deleted paper may host a canonical question that other papers reference
    through ``paper_question_occurrences``. The oldest surviving occurrence
    becomes the question's new host (its own paper number moves onto the
    canonical row); every other occurrence stays as a normal appearance.
    """
    if not paper_ids:
        return
    placeholders = ",".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"""
        SELECT occ.id AS occurrence_id,
               occ.question_id AS question_id,
               occ.paper_id AS paper_id,
               occ.question_number AS question_number,
               q.paper_id AS original_paper_id,
               q.question_number AS original_question_number
        FROM paper_question_occurrences occ
        JOIN questions q ON q.id = occ.question_id
        WHERE q.paper_id IN ({placeholders})
          AND occ.paper_id NOT IN ({placeholders})
        ORDER BY occ.question_id, occ.paper_id, occ.id
        """,
        [*paper_ids, *paper_ids],
    ).fetchall()
    rehomed: set[int] = set()
    for row in rows:
        question_id = int(row["question_id"])
        if question_id in rehomed:
            continue
        rehomed.add(question_id)
        cursor = conn.execute(
            f"""
            UPDATE questions
            SET paper_id = ?, question_number = ?
            WHERE id = ? AND paper_id IN ({placeholders})
            """,
            [
                int(row["paper_id"]),
                str(row["question_number"]),
                question_id,
                *paper_ids,
            ],
        )
        if cursor.rowcount != 1:
            continue
        # 迁走的是规范题的托管位置，原卷中的出现关系必须保留供恢复。
        conn.execute(
            """INSERT OR IGNORE INTO paper_question_occurrences
               (paper_id, question_id, question_number) VALUES (?, ?, ?)""",
            (int(row["original_paper_id"]), question_id, str(row["original_question_number"])),
        )
        removed = conn.execute(
            """
            DELETE FROM paper_question_occurrences
            WHERE id = ?
            """,
            (int(row["occurrence_id"]),),
        )
        if counts is not None:
            counts.record("paper_question_occurrences", removed.rowcount)


def _execute_paper_database_delete(
    conn: sqlite3.Connection,
    paper_ids: tuple[int, ...],
) -> dict[str, int]:
    counts = _PaperDatabaseDeleteCounts()
    _rehome_surviving_questions(conn, tuple(paper_ids), counts)
    _delete_selected_rows(
        conn,
        table="papers",
        selected_column="id",
        selected_values=tuple(paper_ids),
        counts=counts,
        active=set(),
    )
    return counts.as_dict()


def _preview_paper_database_delete(
    conn: sqlite3.Connection,
    paper_ids: tuple[int, ...],
) -> dict[str, int]:
    conn.execute("SAVEPOINT paper_permanent_delete_preview")
    try:
        return _execute_paper_database_delete(conn, paper_ids)
    except sqlite3.IntegrityError as exc:
        raise PaperPermanentDeleteDependencyConflict(
            "Paper deletion is blocked by dependent question-bank data"
        ) from exc
    finally:
        conn.execute("ROLLBACK TO paper_permanent_delete_preview")
        conn.execute("RELEASE paper_permanent_delete_preview")


def _controlled_paper_asset(data_root: Path, value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    root = data_root.resolve()
    stored = Path(text)
    candidates = [stored] if stored.is_absolute() else [root / stored]
    lowered = [part.lower() for part in stored.parts]
    for marker in ("user_data", "data"):
        if marker in lowered:
            index = lowered.index(marker)
            candidates.append(root.joinpath(*stored.parts[index + 1 :]))
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if (
            _is_paper_delete_asset_path(data_root, resolved)
            and resolved.is_file()
        ):
            return resolved
    return None


def _is_paper_delete_asset_path(data_root: Path, path: Path) -> bool:
    resolved = path.resolve(strict=False)
    for subdir in _PAPER_DELETE_ASSET_SUBDIRS:
        controlled_root = (data_root / subdir).resolve(strict=False)
        try:
            relative = resolved.relative_to(controlled_root)
        except ValueError:
            continue
        if relative.parts:
            return True
    return False


def _paper_delete_file_candidates(
    conn: sqlite3.Connection,
    data_root: Path,
    paper_rows: list[sqlite3.Row],
    question_ids: list[int],
) -> tuple[list[Path], list[Path]]:
    raw_selected: list[object] = [row["source_file"] for row in paper_rows]
    if question_ids:
        placeholders = ",".join("?" for _ in question_ids)
        for row in conn.execute(
            f"SELECT source_file, image_paths FROM questions "
            f"WHERE id IN ({placeholders})",
            tuple(question_ids),
        ).fetchall():
            raw_selected.append(row["source_file"])
            try:
                raw_selected.extend(json.loads(str(row["image_paths"] or "[]")))
            except (json.JSONDecodeError, TypeError):
                pass
        if _table_exists(conn, "question_previews"):
            for row in conn.execute(
                f"SELECT source_file, image_path FROM question_previews "
                f"WHERE question_id IN ({placeholders})",
                tuple(question_ids),
            ).fetchall():
                raw_selected.extend((row["source_file"], row["image_path"]))
        if _table_exists(conn, "question_content_revisions"):
            raw_selected.extend(
                row[0]
                for row in conn.execute(
                    f"SELECT rich_content_path FROM question_content_revisions "
                    f"WHERE question_id IN ({placeholders})",
                    tuple(question_ids),
                ).fetchall()
            )
    selected = {
        path
        for value in raw_selected
        if (path := _controlled_paper_asset(data_root, value)) is not None
    }
    for question_id in question_ids:
        sidecar = (
            data_root / "question_bank" / "rich_content" / f"question_{question_id}.json"
        ).resolve(strict=False)
        if sidecar.is_file():
            selected.add(sidecar)

    selected_paper_ids = tuple(int(row["id"]) for row in paper_rows)
    paper_placeholders = ",".join("?" for _ in selected_paper_ids)
    raw_survivors: list[object] = [
        row[0]
        for row in conn.execute(
            f"SELECT source_file FROM papers WHERE id NOT IN ({paper_placeholders})",
            selected_paper_ids,
        ).fetchall()
    ]
    if question_ids:
        placeholders = ",".join("?" for _ in question_ids)
        for row in conn.execute(
            f"SELECT source_file, image_paths FROM questions "
            f"WHERE id NOT IN ({placeholders})",
            tuple(question_ids),
        ).fetchall():
            raw_survivors.append(row["source_file"])
            try:
                raw_survivors.extend(json.loads(str(row["image_paths"] or "[]")))
            except (json.JSONDecodeError, TypeError):
                pass
        if _table_exists(conn, "question_previews"):
            for row in conn.execute(
                f"SELECT source_file, image_path FROM question_previews "
                f"WHERE question_id NOT IN ({placeholders})",
                tuple(question_ids),
            ).fetchall():
                raw_survivors.extend((row["source_file"], row["image_path"]))
    # Published document items survive paper deletion: their nullable paper/question
    # links are cleared by the same schema-driven plan. Keep every file that those
    # surviving publication records still reference.
    if _table_exists(conn, "question_document_items"):
        for row in conn.execute(
            "SELECT rich_content_path, asset_paths_json "
            "FROM question_document_items"
        ).fetchall():
            raw_survivors.append(row["rich_content_path"])
            try:
                raw_survivors.extend(
                    json.loads(str(row["asset_paths_json"] or "[]"))
                )
            except (json.JSONDecodeError, TypeError):
                pass
    survivors = {
        path
        for value in raw_survivors
        if (path := _controlled_paper_asset(data_root, value)) is not None
    }
    return (
        sorted(selected - survivors, key=lambda path: str(path).lower()),
        sorted(selected & survivors, key=lambda path: str(path).lower()),
    )


def _stage_paper_delete_files(
    staging_root: Path,
    paths: list[Path],
    paper_ids: list[int],
    *,
    data_root: Path,
) -> list[tuple[Path, Path]]:
    if staging_root.exists():
        raise PaperPermanentDeleteConflict(
            "Permanent paper deletion request is already in progress"
        )
    staging_root.mkdir(parents=True, exist_ok=False)
    staged = [
        (source, staging_root / f"{index:05d}" / source.name)
        for index, source in enumerate(paths)
    ]
    try:
        (staging_root / "manifest.json").write_text(
            json.dumps({
                "manifest_version": 1,
                "paper_ids": paper_ids,
                "entries": [
                    {
                        "source_relative": _relative_manifest_path(
                            data_root,
                            source,
                        ),
                        "staged_relative": _relative_manifest_path(
                            staging_root,
                            target,
                        ),
                    }
                    for source, target in staged
                ],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        moved: list[tuple[Path, Path]] = []
        for source, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            moved.append((source, target))
        return staged
    except Exception as exc:
        _restore_staged_paper_files(locals().get("moved", []))
        shutil.rmtree(staging_root, ignore_errors=True)
        raise PaperPermanentDeleteStorageIncomplete(
            "Unable to stage all paper files"
        ) from exc


def _restore_staged_paper_files(
    entries: Iterable[tuple[Path, Path]],
) -> None:
    for source, staged in reversed(list(entries)):
        if not staged.exists():
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, source)


def _recover_pending_paper_deletes(db_path: Path, data_root: Path) -> None:
    root = data_root / "question_bank" / ".paper-delete-staging"
    if not root.is_dir():
        return
    for operation in root.iterdir():
        if (
            not operation.is_dir()
            or operation.is_symlink()
            or re.fullmatch(r"[0-9a-f]{32}", operation.name) is None
        ):
            continue
        lock = _shared_request_lock(operation)
        if not lock.acquire(blocking=False):
            continue
        try:
            manifest_path = operation / "manifest.json"
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Paper deletion recovery manifest is invalid")
            raw_paper_ids = manifest.get("paper_ids")
            if not isinstance(raw_paper_ids, list):
                raise ValueError("Paper deletion recovery paper ids are invalid")
            paper_ids = [int(value) for value in raw_paper_ids]
            if (
                not paper_ids
                or any(value <= 0 for value in paper_ids)
                or len(paper_ids) != len(set(paper_ids))
            ):
                raise ValueError("Paper deletion recovery has no paper ids")
            entries = _paper_delete_manifest_entries(
                manifest,
                data_root=data_root,
                staging_root=root,
                operation=operation,
            )
            placeholders = ",".join("?" for _ in paper_ids)
            with connect(db_path) as conn:
                remaining = int(conn.execute(
                    f"SELECT COUNT(*) FROM papers WHERE id IN ({placeholders})",
                    tuple(paper_ids),
                ).fetchone()[0])
            if remaining == len(paper_ids):
                if any(
                    source.exists() and staged.exists()
                    for source, staged in entries
                ):
                    raise PaperPermanentDeleteStorageIncomplete(
                        "Interrupted paper deletion would overwrite an existing file"
                    )
                _restore_staged_paper_files(entries)
            elif remaining != 0:
                raise PaperPermanentDeleteStorageIncomplete(
                    "Interrupted paper deletion has inconsistent database state"
                )
            shutil.rmtree(operation)
        except PaperPermanentDeleteStorageIncomplete:
            raise
        except Exception as exc:
            raise PaperPermanentDeleteStorageIncomplete(
                "Unable to recover interrupted paper deletion"
            ) from exc
        finally:
            lock.release()


def _relative_manifest_path(root: Path, path: Path) -> str:
    controlled_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        relative = resolved.relative_to(controlled_root)
    except ValueError as exc:
        raise PaperPermanentDeleteStorageIncomplete(
            "Paper deletion file is outside its controlled root"
        ) from exc
    if not relative.parts:
        raise PaperPermanentDeleteStorageIncomplete(
            "Paper deletion file path is invalid"
        )
    return relative.as_posix()


def _resolve_manifest_path(
    root: Path,
    value: object,
    *,
    require_relative: bool,
) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Paper deletion recovery path is empty")
    stored = Path(text)
    if require_relative and stored.is_absolute():
        raise ValueError("Paper deletion recovery path must be relative")
    candidate = stored if stored.is_absolute() else root / stored
    if candidate.is_symlink():
        raise ValueError("Paper deletion recovery path cannot be a symlink")
    controlled_root = root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(controlled_root)
    except ValueError as exc:
        raise ValueError(
            "Paper deletion recovery path leaves its controlled root"
        ) from exc
    if not relative.parts:
        raise ValueError("Paper deletion recovery path targets its root")
    return resolved


def _paper_delete_manifest_entries(
    manifest: dict[str, object],
    *,
    data_root: Path,
    staging_root: Path,
    operation: Path,
) -> list[tuple[Path, Path]]:
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > 100_000:
        raise ValueError("Paper deletion recovery entries are invalid")
    version = manifest.get("manifest_version")
    if version == 1:
        source_key = "source_relative"
        staged_key = "staged_relative"
        require_relative = True
    elif version is None:
        # Legacy manifests used absolute paths. They remain readable only after
        # both sides are proven to be inside their original controlled roots.
        source_key = "source"
        staged_key = "staged"
        require_relative = False
    else:
        raise ValueError("Paper deletion recovery manifest version is unsupported")
    entries: list[tuple[Path, Path]] = []
    seen_sources: set[Path] = set()
    seen_staged: set[Path] = set()
    operation_root = operation.resolve(strict=False)
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise ValueError("Paper deletion recovery entry is invalid")
        source = _resolve_manifest_path(
            data_root,
            item.get(source_key),
            require_relative=require_relative,
        )
        staged = _resolve_manifest_path(
            operation,
            item.get(staged_key),
            require_relative=require_relative,
        )
        if not _is_paper_delete_asset_path(data_root, source):
            raise ValueError(
                "Paper deletion recovery source is not a question-bank asset"
            )
        staged_relative = staged.relative_to(operation_root)
        if (
            len(staged_relative.parts) != 2
            or staged_relative.parts[0] != f"{index:05d}"
            or staged_relative.parts[1] != source.name
        ):
            raise ValueError(
                "Paper deletion recovery staged path is invalid"
            )
        try:
            source.relative_to(staging_root.resolve(strict=False))
        except ValueError:
            pass
        else:
            raise ValueError("Paper deletion recovery source is inside staging")
        if source in seen_sources or staged in seen_staged:
            raise ValueError("Paper deletion recovery entries contain duplicates")
        seen_sources.add(source)
        seen_staged.add(staged)
        entries.append((source, staged))
    return entries


def _validate_question_create(question: QuestionCreate) -> None:
    if not str(question.question_number or "").strip():
        raise ValueError("题号不能为空")
    if not str(question.question_text or "").strip():
        raise ValueError("题干不能为空")


def _clean_optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _insert_imported_tags(
    conn: sqlite3.Connection,
    question_id: int,
    tags: list[TagCreate],
) -> None:
    rows: list[tuple[object, ...]] = []
    for tag in tags:
        tag_type = tag.tag_type.strip()
        tag_value = tag.tag_value.strip()
        if tag_type not in ALLOWED_TAG_TYPES:
            raise ValueError(f"未知题目标签类型: {tag_type}")
        if tag_value:
            rows.append(
                (
                    question_id,
                    tag_type,
                    tag_value,
                    tag.confidence,
                    _clean_optional(tag.source),
                )
            )
    conn.executemany(
        """
        INSERT INTO question_tags (
            question_id, tag_type, tag_value, confidence, source
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = _ANSWERED_AI_CONFIDENCE
    return round(min(1.0, max(0.0, confidence)), 4)


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
    for field_name, tag_type in _TAG_ANALYSIS_MAP.items():
        source = "manual" if field_name in edited_fields else "ai"
        values = payload.get(field_name)
        if isinstance(values, str):
            values = [values] if values.strip() else []
        rows.extend(
            (
                question_id,
                tag_type,
                str(tag_value),
                confidence,
                source,
                model_name if source == "ai" else None,
            )
            for tag_value in values or []
            if str(tag_value or "").strip()
        )
    return rows


def _dedupe_tag_rows(
    rows: list[tuple[int, str, str, float, str, str | None]],
) -> list[tuple[int, str, str, float, str, str | None]]:
    seen: set[tuple[int, str, str]] = set()
    result: list[tuple[int, str, str, float, str, str | None]] = []
    for row in rows:
        key = (row[0], row[1], row[2])
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def refresh_derived_ownership_tags(conn: sqlite3.Connection, question_id: int) -> bool:
    """Refresh only the ownership projection; retain contextual/manual tags."""
    qid = int(question_id)
    derived = _derived_ownership(conn, Path('.'), qid)
    if derived is None:
        return False
    conn.execute("DELETE FROM question_tags WHERE question_id=? AND (tag_type IN ('exam_scope','curriculum_section','canonical_knowledge_id') OR (tag_type='tag_status' AND tag_value='derived_pending') OR (tag_type='knowledge_point' AND (tag_value LIKE 'sk_%' OR source='taxonomy')) OR (tag_type='prerequisite' AND COALESCE(source,'') <> 'manual'))", (qid,))
    for kind, values in [('exam_scope', derived['exam_scope']),
                         ('curriculum_section', derived['curriculum_section']),
                         ('knowledge_point', derived['direct_keys']),
                         ('prerequisite', derived['prerequisite_keys'])]:
        for value in values:
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,confidence,source) SELECT ?,?,?,1.0,'taxonomy' WHERE NOT EXISTS(SELECT 1 FROM question_tags WHERE question_id=? AND tag_type=? AND tag_value=?)",
                         (qid, kind, value, qid, kind, value))
    conn.execute("DELETE FROM question_tags WHERE id IN (SELECT id FROM (SELECT id,ROW_NUMBER() OVER(PARTITION BY tag_type,tag_value ORDER BY CASE WHEN source='manual' THEN 0 ELSE 1 END,id) AS n FROM question_tags WHERE question_id=?) WHERE n>1)", (qid,))
    conn.execute("UPDATE questions SET updated_at=datetime('now','localtime') WHERE id=?", (qid,))
    return True


def _derived_ownership(
    conn: sqlite3.Connection,
    db_path: Path,
    question_id: int,
) -> dict[str, Any] | None:
    """Derive chapter/section ownership from the current usable evidence
    version's resolved ``direct`` links. Returns ``None`` when no usable link
    exists so the caller can keep model-written values."""
    row = conn.execute(
        """
        SELECT v.evidence_version_id, v.graph_release_id
        FROM question_solution_evidence_versions v
        JOIN (
            SELECT question_id, MAX(created_at) AS max_created
            FROM question_solution_evidence_versions
            WHERE status IN ('proposed', 'approved')
            GROUP BY question_id
        ) m
          ON m.question_id = v.question_id
         AND m.max_created = v.created_at
        WHERE v.question_id = ? AND v.status IN ('proposed', 'approved')
        LIMIT 1
        """,
        (int(question_id),),
    ).fetchone()
    if row is None:
        return None
    version_id = str(row[0])
    grouped = load_point_links(
        Path(db_path),
        [version_id],
        None,
        connection=conn,
    )
    release_id = next((link.graph_release_id for links in grouped.get(version_id, {}).values()
                       for link in links), None)
    direct_keys = [
        str(link.stable_key or link.term_id)
        for links in grouped.get(version_id, {}).values()
        for link in links
        if link.role == "direct" and link.resolution_status == "resolved"
    ]
    direct_keys = [
        key for key in dict.fromkeys(direct_keys) if key.strip()
    ]
    # 前置知识标签同口径派生：resolved supporting_prerequisite 关联的稳定键。
    prerequisite_keys = [
        key
        for key in dict.fromkeys(
            str(link.stable_key or link.term_id)
            for links in grouped.get(version_id, {}).values()
            for link in links
            if link.role == "supporting_prerequisite"
            and link.resolution_status == "resolved"
        )
        if key.strip()
    ]
    if not direct_keys and not prerequisite_keys:
        return None
    section_ids, scope_values = _catalog_ownership_indexes()
    key_anchors = {
        key: resolve_anchor_keys(
            conn, [key], preferred_release_id=release_id
        )
        for key in direct_keys
    }
    anchor_sections = list(
        dict.fromkeys(
            section
            for key in direct_keys
            for section in key_anchors[key]["sections"]
        )
    )
    # 章归属只统计能落到小节的键：只挂章级锚点的跨章节技能词（如挂在
    # 综合与实践章的新定义技能）不产生考试范围归属，但仍保留为
    # knowledge_point 直接键。
    anchor_chapters = list(
        dict.fromkeys(
            chapter
            for key in direct_keys
            if key_anchors[key]["sections"]
            for chapter in key_anchors[key]["chapters"]
        )
    )
    exam_scope = [
        scope_values[key]
        for key in anchor_chapters
        if key in scope_values
    ]
    curriculum_sections = [
        section_ids[key]
        for key in anchor_sections
        if key in section_ids
    ]
    if not exam_scope and not curriculum_sections and not prerequisite_keys:
        return None
    return {
        "exam_scope": exam_scope,
        "curriculum_section": curriculum_sections,
        "direct_keys": direct_keys,
        "prerequisite_keys": prerequisite_keys,
    }


def _catalog_ownership_indexes() -> tuple[dict[str, str], dict[str, str]]:
    """Build ``knowledge_id`` → catalog section id / exam_scope value maps."""
    section_ids: dict[str, str] = {}
    scope_values: dict[str, str] = {}
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            chapter_scopes = chapter.get("exam_scope_values") or ()
            if chapter_scopes:
                scope_values[str(chapter["knowledge_id"])] = str(
                    chapter_scopes[0]
                )
            for section in chapter["sections"]:
                section_ids[str(section["knowledge_id"])] = str(section["id"])
    return section_ids, scope_values
