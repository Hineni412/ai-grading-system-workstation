from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from question_bank.database.schema import connect, initialize_database
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationModule,
    RecommendationDraftNotFound,
    RecommendationSourceChanged,
)
from question_bank.training_criteria import (
    CriterionVersionNotFound,
    QuestionAnalysisInput,
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
)

from .rendering import (
    DOCX_MEDIA_TYPE,
    LAYOUT_VERSION,
    PDF_MEDIA_TYPE,
    OfficePdfConverter,
    PaperRenderError,
    PdfConversionAdapter,
    decode_page_identity,
    inspect_docx,
    page_signature,
    pdf_page_count,
    render_review_docx,
    stamp_frozen_pdf,
)


BUDGET_VERSION = "whole-paper-context-budget-v1"
SUPPORTED_CONTEXT_WINDOWS = frozenset({32_768, 65_536, 128_000})
MAX_REVIEW_DOCX_BYTES = 50 * 1024 * 1024
MAX_QUESTIONS = 12
MAX_CRITERION_POINTS = 120
MAX_IMAGES = 48
MAX_PAGES = 20
ArtifactKind = Literal["review-docx", "reviewed-docx", "frozen-pdf"]

_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INSTANCE_PATTERN = _HASH_PATTERN
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


class PersonalizedPaperError(RuntimeError):
    pass


class PaperRequestConflict(PersonalizedPaperError):
    pass


class PaperInstanceNotFound(PersonalizedPaperError):
    pass


class PaperRevisionConflict(PersonalizedPaperError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = int(expected_revision)
        self.current_revision = int(current_revision)
        super().__init__(
            f"paper revision conflict: expected {expected_revision}, "
            f"current {current_revision}"
        )


class PaperSourceChanged(PersonalizedPaperError):
    pass


class PaperInvalid(PersonalizedPaperError):
    pass


class PaperBudgetExceeded(PersonalizedPaperError):
    def __init__(self, budget: Mapping[str, Any]) -> None:
        self.budget = dict(budget)
        super().__init__("whole-paper assessment budget was exceeded")


class PaperRenderUnavailable(PersonalizedPaperError):
    pass


class PaperArtifactNotFound(PersonalizedPaperError):
    pass


@dataclass(frozen=True, slots=True)
class CreatePaperCommand:
    operation_token: str
    expected_draft_revision: int
    student_id: str
    actor_ref: str
    context_window_tokens: int = 32_768

    def __post_init__(self) -> None:
        token = _token(self.operation_token)
        student_id = _required_text(self.student_id, "student_id", 100)
        actor = _required_text(self.actor_ref, "actor_ref", 100)
        revision = int(self.expected_draft_revision)
        context = int(self.context_window_tokens)
        if revision < 1:
            raise ValueError("expected_draft_revision must be positive")
        if context not in SUPPORTED_CONTEXT_WINDOWS:
            raise ValueError("context_window_tokens is not supported")
        object.__setattr__(self, "operation_token", token)
        object.__setattr__(self, "student_id", student_id)
        object.__setattr__(self, "actor_ref", actor)
        object.__setattr__(self, "expected_draft_revision", revision)
        object.__setattr__(self, "context_window_tokens", context)


@dataclass(frozen=True, slots=True)
class FreezePaperCommand:
    operation_token: str
    expected_revision: int
    content_sha256: str
    filename: str
    actor_ref: str

    def __post_init__(self) -> None:
        token = _token(self.operation_token)
        digest = str(self.content_sha256 or "").strip().casefold()
        filename = Path(str(self.filename or "")).name
        actor = _required_text(self.actor_ref, "actor_ref", 100)
        revision = int(self.expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        if not _HASH_PATTERN.fullmatch(digest):
            raise ValueError("content_sha256 is invalid")
        if (
            not filename
            or len(filename) > 180
            or Path(filename).suffix.casefold() != ".docx"
        ):
            raise ValueError("filename must identify a DOCX file")
        object.__setattr__(self, "operation_token", token)
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "actor_ref", actor)
        object.__setattr__(self, "expected_revision", revision)


class PersonalizedPaperModule:
    """Freeze one reviewed student draft behind a small, recovery-safe interface."""

    def __init__(
        self,
        *,
        db_path: Path,
        data_root: Path,
        pdf_converter: PdfConversionAdapter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.artifact_root = (
            self.data_root / "question_bank" / "personalized_papers"
        )
        self.pdf_converter = pdf_converter or OfficePdfConverter()
        self.clock = clock or (lambda: datetime.now(UTC))

    def create_review_instance(
        self,
        draft_id: str,
        command: CreatePaperCommand,
    ) -> dict[str, Any]:
        clean_draft_id = _identifier(draft_id, "draft_id")
        initialize_database(self.db_path)
        fingerprint = _hash_payload(
            {
                "draft_id": clean_draft_id,
                **asdict(command),
            }
        )
        repeated = self._event_result(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        paper_instance_id = _hash_payload(
            {
                "kind": "personalized-paper-instance",
                "draft_id": clean_draft_id,
                "student_id": command.student_id,
                "operation_token": command.operation_token,
            }
        )
        with _instance_lock(paper_instance_id):
            repeated = self._event_result(
                command.operation_token,
                fingerprint,
            )
            if repeated is not None:
                return repeated
            existing = self._instance_by_operation(command.operation_token)
            if existing is not None:
                if str(existing["operation_fingerprint"]) != fingerprint:
                    raise PaperRequestConflict(
                        "paper operation token was reused"
                    )
                if str(existing["status"]) in {"review_pending", "frozen"}:
                    return self._finish_missing_create_event(
                        existing,
                        command=command,
                        fingerprint=fingerprint,
                    )
                snapshot = self._snapshot_for_resume(
                    existing,
                    command=command,
                )
                return self._resume_review_document(
                    existing,
                    snapshot=snapshot,
                    command=command,
                    fingerprint=fingerprint,
                )

            recommendation = PersonalizedRecommendationModule(
                db_path=self.db_path,
                data_root=self.data_root,
                clock=self.clock,
            )
            try:
                draft = recommendation.ensure_current(clean_draft_id)
            except RecommendationDraftNotFound as exc:
                raise PaperInstanceNotFound(clean_draft_id) from exc
            except RecommendationSourceChanged as exc:
                raise PaperSourceChanged(
                    "recommendation sources changed before paper creation"
                ) from exc
            if int(draft["revision"]) != command.expected_draft_revision:
                raise PaperRevisionConflict(
                    command.expected_draft_revision,
                    int(draft["revision"]),
                )
            student = _student_from_draft(draft, command.student_id)
            if not student.get("items"):
                raise PaperInvalid(
                    "a paper cannot be created without recommended items"
                )
            prepared_items = self._prepare_items(
                student,
                paper_instance_id=paper_instance_id,
            )
            initial_budget = _paper_budget(
                prepared_items,
                context_window_tokens=command.context_window_tokens,
                page_count=max(1, math.ceil(len(prepared_items) / 2)),
                page_count_is_estimate=True,
            )
            if initial_budget["status"] != "ready":
                raise PaperBudgetExceeded(initial_budget)
            paper_batch_id = _hash_payload(
                {
                    "draft_id": clean_draft_id,
                    "draft_result_version": draft["result_version"],
                }
            )
            signing_secret = secrets.token_hex(32)
            series_version = self._reserve_instance(
                paper_instance_id=paper_instance_id,
                operation_token=command.operation_token,
                operation_fingerprint=fingerprint,
                draft=draft,
                student=student,
                paper_batch_id=paper_batch_id,
                command=command,
                budget=initial_budget,
                signing_secret=signing_secret,
            )
            snapshot = self._build_snapshot(
                paper_instance_id=paper_instance_id,
                paper_batch_id=paper_batch_id,
                series_version=series_version,
                draft=draft,
                student=student,
                command=command,
                budget=initial_budget,
                prepared_items=prepared_items,
            )
            self._store_snapshot_and_items(
                paper_instance_id,
                snapshot=snapshot,
                prepared_items=prepared_items,
            )
            row = self._instance_row(paper_instance_id)
            return self._resume_review_document(
                row,
                snapshot=snapshot,
                command=command,
                fingerprint=fingerprint,
            )

    def freeze(
        self,
        paper_instance_id: str,
        command: FreezePaperCommand,
        reviewed_docx: BinaryIO,
    ) -> dict[str, Any]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
        initialize_database(self.db_path)
        fingerprint = _hash_payload(
            {
                "paper_instance_id": clean_id,
                **asdict(command),
            }
        )
        repeated = self._event_result(command.operation_token, fingerprint)
        if repeated is not None:
            return repeated
        with _instance_lock(clean_id):
            repeated = self._event_result(
                command.operation_token,
                fingerprint,
            )
            if repeated is not None:
                return repeated
            row = self._instance_row(clean_id)
            current_revision = int(row["revision"])
            if command.expected_revision != current_revision:
                raise PaperRevisionConflict(
                    command.expected_revision,
                    current_revision,
                )
            if str(row["status"]) != "review_pending":
                raise PaperInvalid(
                    "only a review-pending paper can be frozen"
                )
            snapshot = json.loads(str(row["snapshot_json"]))
            items = _snapshot_items(snapshot)
            self.artifact_root.mkdir(parents=True, exist_ok=True)
            temp_root = self.artifact_root / ".tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=f"{clean_id[:12]}-",
                dir=temp_root,
                ignore_cleanup_errors=True,
            ) as temporary:
                temporary_path = Path(temporary)
                uploaded = temporary_path / "reviewed.docx"
                received_hash = _copy_upload(
                    reviewed_docx,
                    uploaded,
                    limit=MAX_REVIEW_DOCX_BYTES,
                )
                if not hmac.compare_digest(
                    received_hash,
                    command.content_sha256,
                ):
                    raise PaperInvalid("reviewed DOCX hash does not match")
                try:
                    inspect_docx(
                        uploaded,
                        paper_instance_id=clean_id,
                        task_item_codes=tuple(
                            str(item["task_item_code"]) for item in items
                        ),
                        question_texts=tuple(
                            str(
                                _mapping(item["question_snapshot"])[
                                    "tagging_context"
                                ]["question_text"]
                            )
                            for item in items
                        ),
                    )
                except PaperRenderError as exc:
                    raise PaperInvalid(
                        "reviewed DOCX no longer matches the paper"
                    ) from exc
                converted = temporary_path / "converted.pdf"
                try:
                    self.pdf_converter.convert(uploaded, converted)
                except (OSError, subprocess.SubprocessError, PaperRenderError) as exc:
                    raise PaperRenderUnavailable(
                        "reviewed DOCX could not be converted to PDF"
                    ) from exc
                pages = pdf_page_count(converted)
                final_budget = _paper_budget(
                    items,
                    context_window_tokens=int(
                        _mapping(snapshot["budget"])[
                            "context_window_tokens"
                        ]
                    ),
                    page_count=pages,
                    page_count_is_estimate=False,
                )
                if final_budget["status"] != "ready":
                    raise PaperBudgetExceeded(final_budget)
                stamped = temporary_path / "frozen.pdf"
                page_rows = stamp_frozen_pdf(
                    converted,
                    stamped,
                    paper_instance_id=clean_id,
                    paper_batch_id=str(row["paper_batch_id"]),
                    series_version=int(row["series_version"]),
                    signing_secret=str(row["signing_secret"]),
                    reviewed_docx_sha256=received_hash,
                    layout_version=str(row["layout_version"]),
                )
                reviewed_relative = self._relative_artifact(
                    clean_id,
                    f"reviewed-v{int(row['series_version'])}-{received_hash[:12]}.docx",
                )
                pdf_hash = _file_sha256(stamped)
                pdf_relative = self._relative_artifact(
                    clean_id,
                    f"frozen-v{int(row['series_version'])}-{pdf_hash[:12]}.pdf",
                )
                reviewed_final = self._absolute_artifact(reviewed_relative)
                pdf_final = self._absolute_artifact(pdf_relative)
                _atomic_publish(uploaded, reviewed_final)
                _atomic_publish(stamped, pdf_final)
                try:
                    return self._commit_frozen(
                        row=row,
                        command=command,
                        fingerprint=fingerprint,
                        budget=final_budget,
                        reviewed_relative=reviewed_relative,
                        reviewed_hash=received_hash,
                        pdf_relative=pdf_relative,
                        pdf_hash=pdf_hash,
                        pages=page_rows,
                    )
                except Exception:
                    self._remove_unreferenced(
                        clean_id,
                        (reviewed_relative, pdf_relative),
                    )
                    raise

    def get(self, paper_instance_id: str) -> dict[str, Any]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
        initialize_database(self.db_path)
        return self._public_instance(self._instance_row(clean_id))

    def list_for_draft(self, draft_id: str) -> tuple[dict[str, Any], ...]:
        clean_id = _identifier(draft_id, "draft_id")
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM personalized_paper_instances
                WHERE draft_id = ?
                  AND status IN ('review_pending', 'frozen')
                ORDER BY student_id, series_version DESC
                """,
                (clean_id,),
            ).fetchall()
        return tuple(self._public_instance(row) for row in rows)

    def artifact_path(
        self,
        paper_instance_id: str,
        kind: ArtifactKind,
    ) -> tuple[Path, str]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
        initialize_database(self.db_path)
        row = self._instance_row(clean_id)
        columns = {
            "review-docx": ("review_docx_path", DOCX_MEDIA_TYPE),
            "reviewed-docx": ("reviewed_docx_path", DOCX_MEDIA_TYPE),
            "frozen-pdf": ("frozen_pdf_path", PDF_MEDIA_TYPE),
        }
        if kind not in columns:
            raise PaperArtifactNotFound(kind)
        column, media_type = columns[kind]
        relative = str(row[column] or "")
        if not relative:
            raise PaperArtifactNotFound(kind)
        path = self._absolute_artifact(relative)
        if not path.is_file():
            raise PaperArtifactNotFound(kind)
        return path, media_type

    def verify_page_identity(self, value: str) -> dict[str, Any]:
        decoded = decode_page_identity(value)
        initialize_database(self.db_path)
        row = self._instance_row(decoded["paper_instance_id"])
        if (
            str(row["status"]) != "frozen"
            or int(row["series_version"]) != decoded["series_version"]
            or int(row["page_count"] or 0) != decoded["total_pages"]
        ):
            raise PaperInvalid("page identity does not match a frozen paper")
        expected = page_signature(
            signing_secret=str(row["signing_secret"]),
            paper_batch_id=str(row["paper_batch_id"]),
            paper_instance_id=str(row["paper_instance_id"]),
            series_version=int(row["series_version"]),
            page_number=int(decoded["page_number"]),
            total_pages=int(decoded["total_pages"]),
            reviewed_docx_sha256=str(row["reviewed_docx_sha256"]),
            layout_version=str(row["layout_version"]),
            identity_version=str(decoded["identity_version"]),
        )
        if not hmac.compare_digest(expected, str(decoded["page_signature"])):
            raise PaperInvalid("page identity signature is invalid")
        with connect(self.db_path) as connection:
            page = connection.execute(
                """
                SELECT page_identity, page_content_hash, pdf_sha256
                FROM personalized_paper_pages
                WHERE paper_instance_id = ? AND page_number = ?
                """,
                (
                    decoded["paper_instance_id"],
                    decoded["page_number"],
                ),
            ).fetchone()
        if page is None or not hmac.compare_digest(
            str(page["page_identity"]),
            str(value),
        ):
            raise PaperInvalid("page identity is not registered")
        return {
            **decoded,
            "page_content_hash": str(page["page_content_hash"]),
            "pdf_sha256": str(page["pdf_sha256"]),
        }

    def _prepare_items(
        self,
        student: Mapping[str, Any],
        *,
        paper_instance_id: str,
    ) -> tuple[dict[str, Any], ...]:
        recommendations = _mappings(student.get("items"))
        if len(recommendations) > MAX_QUESTIONS:
            raise PaperInvalid("paper exceeds the question limit")
        question_ids = tuple(
            int(item["question_id"]) for item in recommendations
        )
        loader = QuestionAnalysisInputLoader(
            db_path=self.db_path,
            data_root=self.data_root,
        )
        try:
            loaded = loader.load(question_ids)
        except (KeyError, OSError, ValueError) as exc:
            raise PaperSourceChanged(
                "a recommended question is no longer available"
            ) from exc
        by_id = {item.question_id: item for item in loaded}
        criteria = TrainingCriterionModule(self.db_path)
        prepared: list[dict[str, Any]] = []
        for order, recommendation in enumerate(recommendations, start=1):
            question_id = int(recommendation["question_id"])
            question = by_id[question_id]
            expected_version = str(
                recommendation["criterion_version_id"]
            )
            try:
                version = criteria.get_version(expected_version)
                workspace = criteria.read(question)
            except (CriterionVersionNotFound, KeyError, ValueError) as exc:
                raise PaperSourceChanged(
                    "a recommended criterion version is unavailable"
                ) from exc
            approved = workspace.get("approved_version")
            if (
                not workspace.get("available")
                or not isinstance(approved, Mapping)
                or str(approved["version_id"]) != expected_version
                or str(version["status"]) != "approved"
                or str(version["source_content_hash"])
                != question.criterion_source_content_hash
            ):
                raise PaperSourceChanged(
                    "a recommended criterion version changed"
                )
            question_snapshot = self._question_snapshot(
                question,
                paper_instance_id=paper_instance_id,
            )
            prepared.append(
                {
                    "item_order": order,
                    "question_id": question_id,
                    "question_content_hash": (
                        question.criterion_source_content_hash
                    ),
                    "question_snapshot": question_snapshot,
                    "criterion_version_id": expected_version,
                    "criterion_hash": str(version["criteria_hash"]),
                    "criterion_snapshot": dict(version),
                    "recommendation_snapshot": dict(recommendation),
                }
            )
        return tuple(prepared)

    def _question_snapshot(
        self,
        question: QuestionAnalysisInput,
        *,
        paper_instance_id: str,
    ) -> dict[str, Any]:
        images: list[dict[str, Any]] = []
        for image in question.images:
            extension = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }[image.mime_type]
            relative = self._relative_artifact(
                paper_instance_id,
                f"assets/{image.sha256}{extension}",
            )
            destination = self._absolute_artifact(relative)
            _atomic_write_bytes(destination, image.content)
            images.append(
                {
                    "role": image.role,
                    "mime_type": image.mime_type,
                    "sha256": image.sha256,
                    "asset_path": relative,
                }
            )
        return {
            "question_id": question.question_id,
            "tagging_context": question.tagging_context.to_dict(),
            "rich_question_blocks": [
                dict(item) for item in question.rich_question_blocks
            ],
            "rich_answer_blocks": [
                dict(item) for item in question.rich_answer_blocks
            ],
            "images": images,
            "source_content_hash": question.criterion_source_content_hash,
        }

    def _reserve_instance(
        self,
        *,
        paper_instance_id: str,
        operation_token: str,
        operation_fingerprint: str,
        draft: Mapping[str, Any],
        student: Mapping[str, Any],
        paper_batch_id: str,
        command: CreatePaperCommand,
        budget: Mapping[str, Any],
        signing_secret: str,
    ) -> int:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT *
                FROM personalized_paper_instances
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_fingerprint"])
                    != operation_fingerprint
                ):
                    raise PaperRequestConflict(
                        "paper operation token was reused"
                    )
                return int(existing["series_version"])
            version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(series_version), 0) + 1
                    FROM personalized_paper_instances
                    WHERE draft_id = ? AND student_id = ?
                    """,
                    (draft["draft_id"], command.student_id),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO personalized_paper_instances (
                    paper_instance_id, operation_token,
                    operation_fingerprint, draft_id, draft_revision,
                    draft_result_version, paper_batch_id, series_version,
                    student_id, student_code_snapshot,
                    student_name_snapshot, class_id_snapshot,
                    status, revision, layout_version, budget_version,
                    budget_json, snapshot_json, signing_secret,
                    created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'creating', 1, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    paper_instance_id,
                    operation_token,
                    operation_fingerprint,
                    draft["draft_id"],
                    draft["revision"],
                    draft["result_version"],
                    paper_batch_id,
                    version,
                    command.student_id,
                    _optional_text(student.get("student_code")),
                    _optional_text(student.get("student_name")),
                    _optional_text(student.get("class_id")),
                    LAYOUT_VERSION,
                    BUDGET_VERSION,
                    _json(budget),
                    signing_secret,
                    command.actor_ref,
                ),
            )
        return version

    def _build_snapshot(
        self,
        *,
        paper_instance_id: str,
        paper_batch_id: str,
        series_version: int,
        draft: Mapping[str, Any],
        student: Mapping[str, Any],
        command: CreatePaperCommand,
        budget: Mapping[str, Any],
        prepared_items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        items = []
        for prepared in prepared_items:
            order = int(prepared["item_order"])
            items.append(
                {
                    **dict(prepared),
                    "task_item_code": (
                        f"P4-{paper_instance_id[:20].upper()}-"
                        f"V{series_version:02d}-Q{order:02d}"
                    ),
                }
            )
        return {
            "schema_version": "personalized-paper-snapshot-v1",
            "paper_instance_id": paper_instance_id,
            "paper_batch_id": paper_batch_id,
            "series_version": series_version,
            "layout_version": LAYOUT_VERSION,
            "budget_version": BUDGET_VERSION,
            "student": {
                "student_id": command.student_id,
                "student_code": _optional_text(student.get("student_code")),
                "student_name": _optional_text(student.get("student_name")),
                "class_id": _optional_text(student.get("class_id")),
            },
            "draft": {
                "draft_id": draft["draft_id"],
                "revision": draft["revision"],
                "result_version": draft["result_version"],
                "source_version": draft["source_version"],
                "engine_version": draft["engine_version"],
                "config": dict(draft.get("config") or {}),
            },
            "selection_mode": student.get("selection_mode"),
            "estimated_minutes": int(
                student.get("estimated_minutes") or 0
            ),
            "warnings": list(student.get("warnings") or []),
            "shortages": list(student.get("shortages") or []),
            "budget": dict(budget),
            "items": items,
        }

    def _snapshot_for_resume(
        self,
        row,
        *,
        command: CreatePaperCommand,
    ) -> dict[str, Any]:
        snapshot = json.loads(str(row["snapshot_json"]))
        if _mappings(snapshot.get("items")):
            return snapshot
        recommendation = PersonalizedRecommendationModule(
            db_path=self.db_path,
            data_root=self.data_root,
            clock=self.clock,
        )
        try:
            draft = recommendation.ensure_current(str(row["draft_id"]))
        except RecommendationDraftNotFound as exc:
            raise PaperInstanceNotFound(str(row["draft_id"])) from exc
        except RecommendationSourceChanged as exc:
            raise PaperSourceChanged(
                "recommendation sources changed before paper creation"
            ) from exc
        if int(draft["revision"]) != command.expected_draft_revision:
            raise PaperRevisionConflict(
                command.expected_draft_revision,
                int(draft["revision"]),
            )
        student = _student_from_draft(draft, command.student_id)
        prepared_items = self._prepare_items(
            student,
            paper_instance_id=str(row["paper_instance_id"]),
        )
        budget = _paper_budget(
            prepared_items,
            context_window_tokens=command.context_window_tokens,
            page_count=max(1, math.ceil(len(prepared_items) / 2)),
            page_count_is_estimate=True,
        )
        if budget["status"] != "ready":
            raise PaperBudgetExceeded(budget)
        snapshot = self._build_snapshot(
            paper_instance_id=str(row["paper_instance_id"]),
            paper_batch_id=str(row["paper_batch_id"]),
            series_version=int(row["series_version"]),
            draft=draft,
            student=student,
            command=command,
            budget=budget,
            prepared_items=prepared_items,
        )
        self._store_snapshot_and_items(
            str(row["paper_instance_id"]),
            snapshot=snapshot,
            prepared_items=prepared_items,
        )
        return snapshot

    def _store_snapshot_and_items(
        self,
        paper_instance_id: str,
        *,
        snapshot: Mapping[str, Any],
        prepared_items: Sequence[Mapping[str, Any]],
    ) -> None:
        snapshot_items = _snapshot_items(snapshot)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE personalized_paper_instances
                SET snapshot_json = ?, budget_json = ?,
                    error_code = NULL,
                    updated_at = datetime('now','localtime')
                WHERE paper_instance_id = ?
                  AND status IN ('creating', 'failed')
                """,
                (
                    _json(snapshot),
                    _json(snapshot["budget"]),
                    paper_instance_id,
                ),
            )
            connection.execute(
                """
                DELETE FROM personalized_paper_items
                WHERE paper_instance_id = ?
                """,
                (paper_instance_id,),
            )
            for item, prepared in zip(
                snapshot_items,
                prepared_items,
                strict=True,
            ):
                connection.execute(
                    """
                    INSERT INTO personalized_paper_items (
                        paper_instance_id, task_item_code, item_order,
                        bank_question_id, question_content_hash,
                        question_snapshot_json, criterion_version_id,
                        criterion_hash, criterion_snapshot_json,
                        recommendation_snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_instance_id,
                        item["task_item_code"],
                        item["item_order"],
                        prepared["question_id"],
                        prepared["question_content_hash"],
                        _json(prepared["question_snapshot"]),
                        prepared["criterion_version_id"],
                        prepared["criterion_hash"],
                        _json(prepared["criterion_snapshot"]),
                        _json(prepared["recommendation_snapshot"]),
                    ),
                )

    def _resume_review_document(
        self,
        row,
        *,
        snapshot: Mapping[str, Any],
        command: CreatePaperCommand,
        fingerprint: str,
    ) -> dict[str, Any]:
        paper_instance_id = str(row["paper_instance_id"])
        relative = self._relative_artifact(
            paper_instance_id,
            f"review-v{int(row['series_version'])}.docx",
        )
        destination = self._absolute_artifact(relative)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        temp_root = self.artifact_root / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"{paper_instance_id[:12]}-",
                dir=temp_root,
                ignore_cleanup_errors=True,
            ) as temporary:
                staged = Path(temporary) / "review.docx"
                render_review_docx(
                    snapshot,
                    data_root=self.data_root,
                    output_path=staged,
                )
                review_hash = _file_sha256(staged)
                _atomic_publish(staged, destination)
            with connect(self.db_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    """
                    UPDATE personalized_paper_instances
                    SET status = 'review_pending',
                        review_docx_path = ?,
                        review_docx_sha256 = ?,
                        error_code = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE paper_instance_id = ?
                      AND status IN ('creating', 'failed', 'review_pending')
                    """,
                    (
                        relative,
                        review_hash,
                        paper_instance_id,
                    ),
                )
                if updated.rowcount != 1:
                    current = connection.execute(
                        """
                        SELECT revision
                        FROM personalized_paper_instances
                        WHERE paper_instance_id = ?
                        """,
                        (paper_instance_id,),
                    ).fetchone()
                    raise PaperRevisionConflict(
                        1,
                        int(current["revision"]) if current else 0,
                    )
                result = self._public_instance_from_connection(
                    connection,
                    paper_instance_id,
                )
                self._insert_event(
                    connection,
                    paper_instance_id=paper_instance_id,
                    operation_token=command.operation_token,
                    operation_fingerprint=fingerprint,
                    event_type="created",
                    actor_ref=command.actor_ref,
                    expected_revision=0,
                    resulting_revision=int(result["revision"]),
                    details={
                        "review_docx_sha256": review_hash,
                        "student_id": command.student_id,
                    },
                    result=result,
                )
            return result
        except Exception as exc:
            with connect(self.db_path) as connection:
                connection.execute(
                    """
                    UPDATE personalized_paper_instances
                    SET status = 'failed', error_code = ?,
                        updated_at = datetime('now','localtime')
                    WHERE paper_instance_id = ?
                      AND status IN ('creating', 'failed')
                    """,
                    (_safe_error_code(exc), paper_instance_id),
                )
            if isinstance(exc, PersonalizedPaperError):
                raise
            if isinstance(exc, PaperRenderError):
                raise PaperRenderUnavailable(
                    "review DOCX could not be generated"
                ) from exc
            raise

    def _finish_missing_create_event(
        self,
        row,
        *,
        command: CreatePaperCommand,
        fingerprint: str,
    ) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = self._public_instance_from_connection(
                connection,
                str(row["paper_instance_id"]),
            )
            self._insert_event(
                connection,
                paper_instance_id=str(row["paper_instance_id"]),
                operation_token=command.operation_token,
                operation_fingerprint=fingerprint,
                event_type="created",
                actor_ref=command.actor_ref,
                expected_revision=0,
                resulting_revision=int(result["revision"]),
                details={
                    "review_docx_sha256": row["review_docx_sha256"],
                    "student_id": command.student_id,
                },
                result=result,
            )
        return result

    def _commit_frozen(
        self,
        *,
        row,
        command: FreezePaperCommand,
        fingerprint: str,
        budget: Mapping[str, Any],
        reviewed_relative: str,
        reviewed_hash: str,
        pdf_relative: str,
        pdf_hash: str,
        pages: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        paper_instance_id = str(row["paper_instance_id"])
        next_revision = int(row["revision"]) + 1
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repeated = connection.execute(
                """
                SELECT operation_fingerprint, resulting_instance_json
                FROM personalized_paper_events
                WHERE operation_token = ?
                """,
                (command.operation_token,),
            ).fetchone()
            if repeated is not None:
                if str(repeated["operation_fingerprint"]) != fingerprint:
                    raise PaperRequestConflict(
                        "paper operation token was reused"
                    )
                return json.loads(str(repeated["resulting_instance_json"]))
            current_snapshot = json.loads(str(row["snapshot_json"]))
            current_snapshot["budget"] = dict(budget)
            updated = connection.execute(
                """
                UPDATE personalized_paper_instances
                SET status = 'frozen', revision = ?,
                    budget_json = ?, snapshot_json = ?,
                    reviewed_docx_path = ?,
                    reviewed_docx_sha256 = ?, frozen_pdf_path = ?,
                    frozen_pdf_sha256 = ?, page_count = ?,
                    frozen_by = ?, frozen_at = datetime('now','localtime'),
                    error_code = NULL,
                    updated_at = datetime('now','localtime')
                WHERE paper_instance_id = ?
                  AND status = 'review_pending'
                  AND revision = ?
                """,
                (
                    next_revision,
                    _json(budget),
                    _json(current_snapshot),
                    reviewed_relative,
                    reviewed_hash,
                    pdf_relative,
                    pdf_hash,
                    len(pages),
                    command.actor_ref,
                    paper_instance_id,
                    command.expected_revision,
                ),
            )
            if updated.rowcount != 1:
                current = connection.execute(
                    """
                    SELECT revision
                    FROM personalized_paper_instances
                    WHERE paper_instance_id = ?
                    """,
                    (paper_instance_id,),
                ).fetchone()
                raise PaperRevisionConflict(
                    command.expected_revision,
                    int(current["revision"]) if current else 0,
                )
            connection.execute(
                """
                DELETE FROM personalized_paper_pages
                WHERE paper_instance_id = ?
                """,
                (paper_instance_id,),
            )
            for page in pages:
                connection.execute(
                    """
                    INSERT INTO personalized_paper_pages (
                        paper_instance_id, page_number, total_pages,
                        page_identity, page_signature, page_content_hash,
                        layout_version, pdf_sha256,
                        width_points, height_points
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_instance_id,
                        page["page_number"],
                        page["total_pages"],
                        page["page_identity"],
                        page["page_signature"],
                        page["page_content_hash"],
                        page["layout_version"],
                        page["pdf_sha256"],
                        page["width_points"],
                        page["height_points"],
                    ),
                )
            result = self._public_instance_from_connection(
                connection,
                paper_instance_id,
            )
            self._insert_event(
                connection,
                paper_instance_id=paper_instance_id,
                operation_token=command.operation_token,
                operation_fingerprint=fingerprint,
                event_type="frozen",
                actor_ref=command.actor_ref,
                expected_revision=command.expected_revision,
                resulting_revision=next_revision,
                details={
                    "reviewed_docx_sha256": reviewed_hash,
                    "frozen_pdf_sha256": pdf_hash,
                    "page_count": len(pages),
                },
                result=result,
            )
        return result

    def _event_result(
        self,
        operation_token: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT operation_fingerprint, resulting_instance_json
                FROM personalized_paper_events
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_fingerprint"]) != fingerprint:
            raise PaperRequestConflict("paper operation token was reused")
        return json.loads(str(row["resulting_instance_json"]))

    def _instance_by_operation(self, operation_token: str):
        with connect(self.db_path) as connection:
            return connection.execute(
                """
                SELECT *
                FROM personalized_paper_instances
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()

    def _instance_row(self, paper_instance_id: str):
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM personalized_paper_instances
                WHERE paper_instance_id = ?
                """,
                (paper_instance_id,),
            ).fetchone()
        if row is None:
            raise PaperInstanceNotFound(paper_instance_id)
        return row

    def _public_instance(self, row) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            return self._public_instance_from_connection(
                connection,
                str(row["paper_instance_id"]),
            )

    def _public_instance_from_connection(
        self,
        connection,
        paper_instance_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT *
            FROM personalized_paper_instances
            WHERE paper_instance_id = ?
            """,
            (paper_instance_id,),
        ).fetchone()
        if row is None:
            raise PaperInstanceNotFound(paper_instance_id)
        items = connection.execute(
            """
            SELECT item_order, task_item_code, bank_question_id,
                   criterion_version_id, criterion_hash,
                   recommendation_snapshot_json
            FROM personalized_paper_items
            WHERE paper_instance_id = ?
            ORDER BY item_order
            """,
            (paper_instance_id,),
        ).fetchall()
        pages = connection.execute(
            """
            SELECT page_number, total_pages, page_signature,
                   page_content_hash
            FROM personalized_paper_pages
            WHERE paper_instance_id = ?
            ORDER BY page_number
            """,
            (paper_instance_id,),
        ).fetchall()
        return {
            "paper_instance_id": str(row["paper_instance_id"]),
            "paper_batch_id": str(row["paper_batch_id"]),
            "draft_id": str(row["draft_id"]),
            "draft_revision": int(row["draft_revision"]),
            "student_id": str(row["student_id"]),
            "student_code": row["student_code_snapshot"],
            "student_name": row["student_name_snapshot"],
            "class_id": row["class_id_snapshot"],
            "series_version": int(row["series_version"]),
            "status": str(row["status"]),
            "revision": int(row["revision"]),
            "layout_version": str(row["layout_version"]),
            "budget": json.loads(str(row["budget_json"])),
            "question_count": len(items),
            "criterion_point_count": sum(
                int(
                    _mapping(
                        json.loads(
                            str(item["recommendation_snapshot_json"])
                        )
                    ).get("criterion_point_count")
                    or 0
                )
                for item in items
            ),
            "items": [
                {
                    "item_order": int(item["item_order"]),
                    "task_item_code": str(item["task_item_code"]),
                    "question_id": int(item["bank_question_id"]),
                    "criterion_version_id": str(
                        item["criterion_version_id"]
                    ),
                    "criterion_hash": str(item["criterion_hash"]),
                }
                for item in items
            ],
            "pages": [
                {
                    "page_number": int(page["page_number"]),
                    "total_pages": int(page["total_pages"]),
                    "identity_short": str(page["page_signature"])[:16],
                    "page_content_hash": str(page["page_content_hash"]),
                }
                for page in pages
            ],
            "review_docx_sha256": row["review_docx_sha256"],
            "reviewed_docx_sha256": row["reviewed_docx_sha256"],
            "frozen_pdf_sha256": row["frozen_pdf_sha256"],
            "downloads": {
                "review_docx": (
                    f"/api/training/paper-instances/{paper_instance_id}"
                    "/files/review-docx"
                    if row["review_docx_path"]
                    else None
                ),
                "reviewed_docx": (
                    f"/api/training/paper-instances/{paper_instance_id}"
                    "/files/reviewed-docx"
                    if row["reviewed_docx_path"]
                    else None
                ),
                "frozen_pdf": (
                    f"/api/training/paper-instances/{paper_instance_id}"
                    "/files/frozen-pdf"
                    if row["frozen_pdf_path"]
                    else None
                ),
            },
            "error_code": row["error_code"],
            "created_at": str(row["created_at"]),
            "frozen_at": row["frozen_at"],
        }

    @staticmethod
    def _insert_event(
        connection,
        *,
        paper_instance_id: str,
        operation_token: str,
        operation_fingerprint: str,
        event_type: str,
        actor_ref: str,
        expected_revision: int,
        resulting_revision: int,
        details: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        existing = connection.execute(
            """
            SELECT operation_fingerprint
            FROM personalized_paper_events
            WHERE operation_token = ?
            """,
            (operation_token,),
        ).fetchone()
        if existing is not None:
            if str(existing["operation_fingerprint"]) != operation_fingerprint:
                raise PaperRequestConflict(
                    "paper operation token was reused"
                )
            return
        connection.execute(
            """
            INSERT INTO personalized_paper_events (
                paper_instance_id, operation_token,
                operation_fingerprint, event_type, actor_ref,
                expected_revision, resulting_revision,
                details_json, resulting_instance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_instance_id,
                operation_token,
                operation_fingerprint,
                event_type,
                actor_ref,
                expected_revision,
                resulting_revision,
                _json(details),
                _json(result),
            ),
        )

    def _relative_artifact(
        self,
        paper_instance_id: str,
        filename: str,
    ) -> str:
        relative = Path("question_bank") / "personalized_papers"
        relative = relative / paper_instance_id / filename
        return relative.as_posix()

    def _absolute_artifact(self, relative: str) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise PaperArtifactNotFound("artifact path is invalid")
        root = self.data_root.resolve()
        resolved = (root / value).resolve()
        if root not in resolved.parents:
            raise PaperArtifactNotFound("artifact is outside data root")
        return resolved

    def _remove_unreferenced(
        self,
        paper_instance_id: str,
        relatives: Sequence[str],
    ) -> None:
        try:
            row = self._instance_row(paper_instance_id)
        except Exception:
            return
        referenced = {
            str(row["reviewed_docx_path"] or ""),
            str(row["frozen_pdf_path"] or ""),
        }
        for relative in relatives:
            if relative in referenced:
                continue
            try:
                self._absolute_artifact(relative).unlink(missing_ok=True)
            except OSError:
                pass


def _paper_budget(
    items: Sequence[Mapping[str, Any]],
    *,
    context_window_tokens: int,
    page_count: int,
    page_count_is_estimate: bool,
) -> dict[str, Any]:
    question_count = len(items)
    criterion_points = 0
    image_count = 0
    serialized_chars = 0
    for item in items:
        question = _mapping(item.get("question_snapshot"))
        criterion = _mapping(item.get("criterion_snapshot"))
        criteria_payload = _mapping(criterion.get("criteria"))
        points = criteria_payload.get("points")
        criterion_points += len(points) if isinstance(points, list) else 0
        images = question.get("images")
        image_count += len(images) if isinstance(images, list) else 0
        serialized_chars += len(
            json.dumps(
                {
                    "question": question,
                    "criterion": criterion,
                    "recommendation": item.get("recommendation_snapshot"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    text_tokens = math.ceil(serialized_chars / 1.5)
    image_tokens = image_count * 1_200
    page_tokens = int(page_count) * 1_600
    fixed_input_tokens = 1_200
    estimated_input_tokens = (
        text_tokens + image_tokens + page_tokens + fixed_input_tokens
    )
    estimated_output_tokens = 800 + criterion_points * 180
    estimated_total_tokens = (
        estimated_input_tokens + estimated_output_tokens
    )
    blockers = []
    if question_count > MAX_QUESTIONS:
        blockers.append("question_limit")
    if criterion_points > MAX_CRITERION_POINTS:
        blockers.append("criterion_point_limit")
    if image_count > MAX_IMAGES:
        blockers.append("image_limit")
    if int(page_count) > MAX_PAGES:
        blockers.append("page_limit")
    if estimated_total_tokens > int(context_window_tokens):
        blockers.append("context_window_limit")
    return {
        "version": BUDGET_VERSION,
        "status": "blocked" if blockers else "ready",
        "context_window_tokens": int(context_window_tokens),
        "question_count": question_count,
        "criterion_point_count": criterion_points,
        "image_count": image_count,
        "page_count": int(page_count),
        "page_count_is_estimate": bool(page_count_is_estimate),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_total_tokens": estimated_total_tokens,
        "limits": {
            "questions": MAX_QUESTIONS,
            "criterion_points": MAX_CRITERION_POINTS,
            "images": MAX_IMAGES,
            "pages": MAX_PAGES,
        },
        "blockers": blockers,
    }


def _student_from_draft(
    draft: Mapping[str, Any],
    student_id: str,
) -> dict[str, Any]:
    for student in _mappings(draft.get("students")):
        if str(student.get("student_id")) == student_id:
            return student
    raise PaperInvalid("student is not part of the recommendation draft")


def _snapshot_items(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = _mappings(snapshot.get("items"))
    if not items:
        raise PaperInvalid("paper snapshot has no items")
    return items


def _copy_upload(
    source: BinaryIO,
    destination: Path,
    *,
    limit: int,
) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0
    with destination.open("wb") as target:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise PaperInvalid("reviewed DOCX exceeds the size limit")
            digest.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if received <= 0:
        raise PaperInvalid("reviewed DOCX is empty")
    return digest.hexdigest()


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if _file_sha256(destination) != hashlib.sha256(payload).hexdigest():
            raise PaperInvalid("frozen asset hash collision")
        return
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(8)}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _instance_lock(identifier: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(identifier, threading.Lock())


def _identifier(value: object, name: str) -> str:
    result = str(value or "").strip().casefold()
    if not _INSTANCE_PATTERN.fullmatch(result):
        raise ValueError(f"{name} is invalid")
    return result


def _token(value: object) -> str:
    result = str(value or "").strip().casefold()
    if not _TOKEN_PATTERN.fullmatch(result):
        raise ValueError("operation_token is invalid")
    return result


def _required_text(value: object, name: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{name} is invalid")
    return result


def _optional_text(value: object) -> str | None:
    result = str(value or "").strip()
    return result or None


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, PaperRenderError):
        return "review_document_render_failed"
    if isinstance(exc, OSError):
        return "paper_storage_unavailable"
    return "paper_creation_failed"


__all__ = [
    "BUDGET_VERSION",
    "CreatePaperCommand",
    "FreezePaperCommand",
    "PaperArtifactNotFound",
    "PaperBudgetExceeded",
    "PaperInstanceNotFound",
    "PaperInvalid",
    "PersonalizedPaperError",
    "PaperRenderUnavailable",
    "PaperRequestConflict",
    "PaperRevisionConflict",
    "PaperSourceChanged",
    "PersonalizedPaperModule",
    "SUPPORTED_CONTEXT_WINDOWS",
]
