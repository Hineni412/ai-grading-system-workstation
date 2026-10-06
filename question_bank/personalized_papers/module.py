from __future__ import annotations

from question_bank.atomic_files import replace_with_retry
from question_bank.canonical_hash import canonical_hash, canonical_json

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from question_bank.database.schema import connect, initialize_database
from question_bank.document_pipeline import build_math_expression
from question_bank.personalized_papers.latex_render import (
    LatexRenderError,
    TectonicCompiler,
    render_training_tex,
)
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
    usable_training_criterion,
)

from .rendering import (
    DOCX_MEDIA_TYPE,
    LAYOUT_VERSION,
    PDF_MEDIA_TYPE,
    OfficePdfConverter,
    PaperRenderError,
    PdfConversionAdapter,
    append_scratch_page,
    decode_page_identity,
    inspect_docx,
    page_signature,
    pdf_page_count,
    prepare_identity_font,
    render_review_docx,
    stamp_frozen_pdf,
)
from .rendering import _file_sha256

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
# WPS/Word COM conversion can share an application process. LaTeX runs in
# separate temporary directories; only the Office fallback must stay serial.
_PDF_CONVERSION_LOCK = threading.Lock()
_PDF_FINALIZE_LOCK = threading.Lock()
_MATH_RUN = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)
_GOVERNED_IMAGE_REFERENCE = re.compile(r"^sha256:([0-9a-f]{64})$")


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
    direct_freeze: bool = False

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
        object.__setattr__(self, "direct_freeze", self.direct_freeze is True)


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
        latex_compiler: TectonicCompiler | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.artifact_root = (
            self.data_root / "question_bank" / "personalized_papers"
        )
        self.pdf_converter = pdf_converter or OfficePdfConverter()
        self.latex_compiler = (
            latex_compiler if latex_compiler is not None else TectonicCompiler()
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    def create_review_instance(
        self,
        draft_id: str,
        command: CreatePaperCommand,
    ) -> dict[str, Any]:
        clean_draft_id = _identifier(draft_id, "draft_id")
        initialize_database(self.db_path)
        result = self._create_review_instance_ready(clean_draft_id, command)
        if command.direct_freeze and str(result["status"]) == "review_pending":
            return self._freeze_rendered(
                str(result["paper_instance_id"]),
                command=command,
            )
        return result

    def _create_review_instance_ready(
        self,
        draft_id: str,
        command: CreatePaperCommand,
    ) -> dict[str, Any]:
        """Create one review instance after the caller prepared the database."""

        clean_draft_id = _identifier(draft_id, "draft_id")
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
            if draft.get("config", {}).get("purpose", "training") != "training":
                raise PaperInvalid("讲义不能创建可回收训练卷，请重新生成训练卷草稿。")
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
                        question_snapshots=tuple(
                            _mapping(item["question_snapshot"])
                            for item in items
                        ),
                    )
                except PaperRenderError as exc:
                    raise PaperInvalid(
                        "reviewed DOCX no longer matches the paper"
                    ) from exc
                reviewed_relative = self._relative_artifact(
                    clean_id,
                    f"reviewed-v{int(row['series_version'])}-{received_hash[:12]}.docx",
                )
                return self._convert_and_commit_frozen(
                    row,
                    source_docx=uploaded,
                    reviewed_relative=reviewed_relative,
                    reviewed_hash=received_hash,
                    publish_source=True,
                    command=command,
                    fingerprint=fingerprint,
                )

    def _freeze_rendered(
        self,
        paper_instance_id: str,
        *,
        command: CreatePaperCommand,
        pdf_converter: PdfConversionAdapter | None = None,
        identity_font_buffer: bytes | None = None,
    ) -> dict[str, Any]:
        """Freeze the freshly rendered review DOCX without a teacher upload."""
        row = self._instance_row(paper_instance_id)
        if str(row["status"]) == "frozen":
            return self._public_instance(row)
        freeze_command = FreezePaperCommand(
            operation_token=_direct_freeze_token(command.operation_token),
            expected_revision=int(row["revision"]),
            content_sha256=str(row["review_docx_sha256"]),
            filename="review.docx",
            actor_ref=command.actor_ref,
        )
        fingerprint = _hash_payload(
            {
                "paper_instance_id": paper_instance_id,
                **asdict(freeze_command),
            }
        )
        repeated = self._event_result(
            freeze_command.operation_token,
            fingerprint,
        )
        if repeated is not None:
            return repeated
        with _instance_lock(paper_instance_id):
            row = self._instance_row(paper_instance_id)
            if str(row["status"]) == "frozen":
                return self._public_instance(row)
            if str(row["status"]) != "review_pending":
                raise PaperInvalid(
                    "only a review-pending paper can be frozen"
                )
            return self._convert_and_commit_frozen(
                row,
                source_docx=self._absolute_artifact(
                    str(row["review_docx_path"])
                ),
                reviewed_relative=str(row["review_docx_path"]),
                reviewed_hash=str(row["review_docx_sha256"]),
                publish_source=False,
                command=freeze_command,
                fingerprint=fingerprint,
                pdf_converter=pdf_converter,
                identity_font_buffer=identity_font_buffer,
            )

    def _convert_and_commit_frozen(
        self,
        row,
        *,
        source_docx: Path,
        reviewed_relative: str,
        reviewed_hash: str,
        publish_source: bool,
        command: FreezePaperCommand,
        fingerprint: str,
        pdf_converter: PdfConversionAdapter | None = None,
        identity_font_buffer: bytes | None = None,
    ) -> dict[str, Any]:
        converter = pdf_converter or self.pdf_converter
        clean_id = str(row["paper_instance_id"])
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
            converted = temporary_path / "converted.pdf"
            render_info: dict[str, Any] = {
                "renderer": "docx",
                "fallback_reason": None,
                "scratch_page_added": False,
            }
            if not publish_source:
                # 直接出卷：优先本机 LaTeX 排版正文；任何失败回退 DOCX 转换，
                # 出卷永不被 LaTeX 问题阻断，回退原因如实记录。
                try:
                    if not self.latex_compiler.available:
                        raise LatexRenderError("no tectonic engine is available")
                    tex_source = render_training_tex(
                        snapshot,
                        data_root=self.data_root,
                    )
                    self.latex_compiler.compile(tex_source, converted)
                    render_info["renderer"] = "latex"
                except (
                    LatexRenderError,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    render_info["fallback_reason"] = (
                        f"{type(exc).__name__}: {exc}"[:200]
                    )
                    try:
                        with _PDF_CONVERSION_LOCK:
                            converter.convert(source_docx, converted)
                    except (
                        OSError,
                        subprocess.SubprocessError,
                        PaperRenderError,
                    ) as convert_exc:
                        raise PaperRenderUnavailable(
                            "reviewed DOCX could not be converted to PDF"
                        ) from convert_exc
            else:
                try:
                    with _PDF_CONVERSION_LOCK:
                        converter.convert(source_docx, converted)
                except (OSError, subprocess.SubprocessError, PaperRenderError) as exc:
                    raise PaperRenderUnavailable(
                        "reviewed DOCX could not be converted to PDF"
                    ) from exc
            with _PDF_FINALIZE_LOCK:
                pages = pdf_page_count(converted)
                if not publish_source and pages % 2 == 1:
                    # 直接出卷按一张 A4 双面印制：奇数页时追加演算草稿区。
                    # 教师上传的审阅稿保持原来的版面。
                    padded = temporary_path / "padded.pdf"
                    append_scratch_page(converted, padded, identity_font_buffer=identity_font_buffer)
                    converted = padded
                    pages = pdf_page_count(converted)
                    render_info["scratch_page_added"] = True
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
            with _PDF_FINALIZE_LOCK:
                page_rows = stamp_frozen_pdf(
                    converted,
                    stamped,
                    paper_instance_id=clean_id,
                    paper_batch_id=str(row["paper_batch_id"]),
                    series_version=int(row["series_version"]),
                    student_name=str(row["student_name_snapshot"] or ""),
                    student_code=str(row["student_code_snapshot"] or ""),
                    class_id=str(row["class_id_snapshot"] or ""),
                    signing_secret=str(row["signing_secret"]),
                    reviewed_docx_sha256=reviewed_hash,
                    layout_version=str(row["layout_version"]),
                    identity_font_buffer=identity_font_buffer,
                )
            pdf_hash = _file_sha256(stamped)
            pdf_relative = self._relative_artifact(
                clean_id,
                f"frozen-v{int(row['series_version'])}-{pdf_hash[:12]}.pdf",
            )
            pdf_final = self._absolute_artifact(pdf_relative)
            if publish_source:
                _atomic_publish(
                    source_docx,
                    self._absolute_artifact(reviewed_relative),
                )
            _atomic_publish(stamped, pdf_final)
            try:
                return self._commit_frozen(
                    row=row,
                    command=command,
                    fingerprint=fingerprint,
                    budget=final_budget,
                    reviewed_relative=reviewed_relative,
                    reviewed_hash=reviewed_hash,
                    pdf_relative=pdf_relative,
                    pdf_hash=pdf_hash,
                    pages=page_rows,
                    render_info=render_info,
                )
            except Exception:
                relatives = (
                    (reviewed_relative, pdf_relative)
                    if publish_source
                    else (pdf_relative,)
                )
                self._remove_unreferenced(clean_id, relatives)
                raise

    def get(self, paper_instance_id: str) -> dict[str, Any]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
        initialize_database(self.db_path)
        return self._get_ready(clean_id)

    def _get_ready(self, paper_instance_id: str) -> dict[str, Any]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
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

    def create_review_batch(
        self,
        draft_id: str,
        *,
        operation_token: str,
        expected_draft_revision: int,
        student_ids: Sequence[str] = (),
        actor_ref: str,
        context_window_tokens: int = 32_768,
        direct_freeze: bool = False,
    ) -> dict[str, Any]:
        clean_draft_id = _identifier(draft_id, "draft_id")
        clean_token = _token(operation_token)
        initialize_database(self.db_path)
        return self._create_review_batch_ready(
            clean_draft_id,
            operation_token=clean_token,
            expected_draft_revision=expected_draft_revision,
            student_ids=student_ids,
            actor_ref=actor_ref,
            context_window_tokens=context_window_tokens,
            direct_freeze=direct_freeze,
        )

    def _create_review_batch_ready(
        self,
        draft_id: str,
        *,
        operation_token: str,
        expected_draft_revision: int,
        student_ids: Sequence[str] = (),
        actor_ref: str,
        context_window_tokens: int = 32_768,
        direct_freeze: bool = False,
    ) -> dict[str, Any]:
        """Create a batch after the caller prepared the database."""

        clean_draft_id = _identifier(draft_id, "draft_id")
        clean_token = _token(operation_token)
        requested = tuple(dict.fromkeys(
            str(value or "").strip() for value in student_ids if str(value or "").strip()
        ))
        fingerprint = _hash_payload({
            "kind": "personalized-paper-review-batch",
            "draft_id": clean_draft_id,
            "draft_revision": int(expected_draft_revision),
            "requested_student_ids": list(requested),
            "actor_ref": _required_text(actor_ref, "actor_ref", 100),
            "context_window_tokens": int(context_window_tokens),
            "direct_freeze": bool(direct_freeze),
        })
        existing = self._batch_by_operation(clean_token)
        if existing is not None:
            if str(existing["operation_fingerprint"]) != fingerprint:
                raise PaperRequestConflict("paper batch operation token was reused")
            if str(existing["status"]) != "creating":
                return self._public_batch(str(existing["batch_run_id"]))
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
                "recommendation sources changed before paper batch creation"
            ) from exc
        if int(draft["revision"]) != int(expected_draft_revision):
            raise PaperRevisionConflict(
                int(expected_draft_revision), int(draft["revision"])
            )
        if draft.get("config", {}).get("purpose", "training") != "training":
            raise PaperInvalid("讲义不能创建可回收训练卷，请重新生成训练卷草稿。")
        available = {
            str(item.get("student_id") or ""): item
            for item in _mappings(draft.get("students"))
        }
        targets = requested or tuple(available)
        batch_run_id = _hash_payload({
            "kind": "personalized-paper-review-batch",
            "draft_id": clean_draft_id,
            "draft_revision": int(expected_draft_revision),
            "operation_token": clean_token,
            "student_ids": list(targets),
        })
        paper_batch_id = _hash_payload({
            "draft_id": clean_draft_id,
            "draft_result_version": draft["result_version"],
        })
        self._reserve_batch(
            batch_run_id=batch_run_id,
            operation_token=clean_token,
            operation_fingerprint=fingerprint,
            draft_id=clean_draft_id,
            draft_revision=int(expected_draft_revision),
            paper_batch_id=paper_batch_id,
            targets=targets,
            actor_ref=actor_ref,
            request_json=_json({
                "requested_student_ids": list(requested),
                "context_window_tokens": int(context_window_tokens),
                "actor_ref": actor_ref,
            }),
        )
        with connect(self.db_path) as connection:
            saved_items = connection.execute(
                """
                SELECT * FROM personalized_paper_batch_items
                WHERE batch_run_id = ? ORDER BY item_order
                """,
                (batch_run_id,),
            ).fetchall()
        identity_font_buffer = None
        if direct_freeze:
            with _PDF_FINALIZE_LOCK:
                identity_font_buffer = prepare_identity_font("".join(
                    str(available.get(sid, {}).get(field) or "")
                    for sid in targets for field in ("student_name", "student_code", "class_id")
                ))
        def create_item(saved: Mapping[str, Any]) -> None:
            if self._batch_is_cancelled(batch_run_id):
                return
            student_id = str(saved["student_id"])
            if str(saved["status"]) == "succeeded":
                return
            if str(saved["status"]) == "failed":
                return
            self._start_batch_item(batch_run_id, student_id)
            if student_id not in available:
                failure = {"student_id": student_id, "error_code": "student_not_in_draft"}
                self._finish_batch_item(batch_run_id, **failure)
                return
            per_student_token = hashlib.sha256(
                f"{clean_token}:{student_id}".encode("utf-8")
            ).hexdigest()[:32]
            try:
                lock_id = hashlib.sha256(
                    f"batch:{clean_draft_id}:{expected_draft_revision}:{student_id}".encode("utf-8")
                ).hexdigest()
                with _instance_lock(lock_id):
                    per_student_command = CreatePaperCommand(
                        operation_token=per_student_token,
                        expected_draft_revision=int(expected_draft_revision),
                        student_id=student_id,
                        actor_ref=actor_ref,
                        context_window_tokens=context_window_tokens,
                        direct_freeze=direct_freeze,
                    )
                    instance = self._current_instance_for_student(
                        clean_draft_id,
                        student_id=student_id,
                        draft_revision=int(expected_draft_revision),
                    )
                    if instance is not None:
                        try:
                            self._artifact_path_ready(
                                str(instance["paper_instance_id"]),
                                "review-docx",
                            )
                        except PaperArtifactNotFound:
                            row = self._instance_row(str(instance["paper_instance_id"]))
                            if str(row["status"]) == "frozen":
                                raise
                            instance = self._resume_review_document(
                                row,
                                snapshot=json.loads(str(row["snapshot_json"])),
                                command=per_student_command,
                                fingerprint=str(row["operation_fingerprint"]),
                                record_event=False,
                            )
                    else:
                        instance = self._create_review_instance_ready(
                            clean_draft_id,
                            per_student_command,
                        )
                    if direct_freeze and str(instance["status"]) == "review_pending":
                        instance = self._freeze_rendered(
                            str(instance["paper_instance_id"]),
                            command=per_student_command,
                            pdf_converter=batch_converter,
                            identity_font_buffer=identity_font_buffer,
                        )
                self._finish_batch_item(
                    batch_run_id,
                    student_id=student_id,
                    paper_instance_id=str(instance["paper_instance_id"]),
                )
            except (
                PersonalizedPaperError,
                OSError,
                sqlite3.Error,
                TypeError,
                ValueError,
            ) as exc:
                failure = {
                    "student_id": student_id,
                    "error_code": type(exc).__name__,
                }
                self._finish_batch_item(batch_run_id, **failure)
        # Submit one bounded wave at a time so cancellation leaves later
        # students pending. Every worker retains its own source/revision check,
        # identity, recovery event and per-student transaction.
        converter_session = (
            self.pdf_converter.batch_session()
            if direct_freeze and isinstance(self.pdf_converter, OfficePdfConverter)
            else nullcontext(self.pdf_converter)
        )
        with converter_session as batch_converter, ThreadPoolExecutor(max_workers=4, thread_name_prefix="training-paper") as pool:
            for start in range(0, len(saved_items), 4):
                if self._batch_is_cancelled(batch_run_id):
                    break
                list(pool.map(create_item, [dict(row) for row in saved_items[start:start + 4]]))
        self._fail_missing_batch_artifacts(batch_run_id)
        created, failed = self._batch_results(batch_run_id)
        downloads = self._publish_review_batch(
            batch_run_id,
            draft=draft,
            instances=created,
            failures=failed,
        )
        status = (
            "cancelled" if self._batch_is_cancelled(batch_run_id)
            else "complete" if created and not failed
            else "partial" if created else "failed"
        )
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE personalized_paper_batches
                SET status = ?, succeeded_count = ?, failed_count = ?,
                    manifest_path = ?, bundle_path = ?,
                    updated_at = datetime('now','localtime')
                WHERE batch_run_id = ?
                """,
                (
                    status,
                    len(created),
                    len(failed),
                    str(downloads.get("manifest_path") or "") or None,
                    str(downloads.get("bundle_path") or "") or None,
                    batch_run_id,
                ),
            )
        return self._public_batch(batch_run_id)

    def cancel_batch(self, batch_run_id: str) -> dict[str, Any]:
        clean_id = _identifier(batch_run_id, "batch_run_id")
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                """
                SELECT * FROM personalized_paper_batches
                WHERE batch_run_id = ?
                """,
                (clean_id,),
            ).fetchone()
            if batch is None:
                raise PaperInstanceNotFound(clean_id)
            if str(batch["status"]) == "creating":
                connection.execute(
                    """
                    UPDATE personalized_paper_batch_items
                    SET status = 'failed', error_code = 'batch_cancelled',
                        updated_at = datetime('now','localtime')
                    WHERE batch_run_id = ? AND status IN ('pending', 'running')
                    """,
                    (clean_id,),
                )
                counts = connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END),
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                    FROM personalized_paper_batch_items WHERE batch_run_id = ?
                    """,
                    (clean_id,),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE personalized_paper_batches
                    SET status = 'cancelled', succeeded_count = ?, failed_count = ?,
                        updated_at = datetime('now','localtime')
                    WHERE batch_run_id = ?
                    """,
                    (int(counts[0] or 0), int(counts[1] or 0), clean_id),
                )
        return self._public_batch(clean_id)

    def get_batch(self, batch_run_id: str) -> dict[str, Any]:
        clean_id = _identifier(batch_run_id, "batch_run_id")
        initialize_database(self.db_path)
        return self._public_batch(clean_id)

    def retry_batch(
        self,
        batch_run_id: str,
        *,
        student_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Resume interrupted items or explicitly retry failed students only."""

        clean_id = _identifier(batch_run_id, "batch_run_id")
        requested_retry = set(
            str(value or "").strip() for value in student_ids if str(value or "").strip()
        )
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM personalized_paper_batches WHERE batch_run_id = ?",
                (clean_id,),
            ).fetchone()
            if batch is None:
                raise PaperInstanceNotFound(clean_id)
            available_failures = {
                str(row["student_id"])
                for row in connection.execute(
                    """
                    SELECT student_id FROM personalized_paper_batch_items
                    WHERE batch_run_id = ? AND status IN ('failed', 'running', 'pending')
                    """,
                    (clean_id,),
                ).fetchall()
            }
            targets = requested_retry or available_failures
            invalid = targets - available_failures
            if invalid:
                raise PaperInvalid("retry scope contains students without a failed item")
            if not targets:
                operation_token = ""
                draft_id = ""
                draft_revision = 0
                original_requested = ()
                context_window_tokens = 32768
                actor_ref = "local_teacher"
            else:
                connection.execute(
                    f"""
                    UPDATE personalized_paper_batch_items
                    SET status = 'pending', paper_instance_id = NULL, error_code = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE batch_run_id = ? AND student_id IN ({','.join('?' for _ in targets)})
                      AND status IN ('failed', 'running', 'pending')
                    """,
                    (clean_id, *sorted(targets)),
                )
                connection.execute(
                    """
                    UPDATE personalized_paper_batches
                    SET status = 'creating', manifest_path = NULL, bundle_path = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE batch_run_id = ?
                    """,
                    (clean_id,),
                )
                request = json.loads(str(batch["request_json"]))
                original_requested = tuple(request.get("requested_student_ids") or ())
                context_window_tokens = int(request.get("context_window_tokens") or 32768)
                actor_ref = str(request.get("actor_ref") or "local_teacher")
                operation_token = str(batch["operation_token"])
                draft_id = str(batch["draft_id"])
                draft_revision = int(batch["draft_revision"])
        if not targets:
            return self._public_batch(clean_id)
        return self._create_review_batch_ready(
            draft_id,
            operation_token=operation_token,
            expected_draft_revision=draft_revision,
            student_ids=original_requested,
            actor_ref=actor_ref,
            context_window_tokens=context_window_tokens,
        )

    def _fail_missing_batch_artifacts(self, batch_run_id: str) -> None:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT student_id, paper_instance_id
                FROM personalized_paper_batch_items
                WHERE batch_run_id = ? AND status = 'succeeded'
                """,
                (batch_run_id,),
            ).fetchall()
        missing: list[str] = []
        for row in rows:
            try:
                self._artifact_path_ready(
                    str(row["paper_instance_id"]),
                    "review-docx",
                )
            except PaperArtifactNotFound:
                missing.append(str(row["student_id"]))
        if not missing:
            return
        with connect(self.db_path) as connection:
            connection.executemany(
                """
                UPDATE personalized_paper_batch_items
                SET status = 'failed', paper_instance_id = NULL,
                    error_code = 'review_artifact_missing',
                    updated_at = datetime('now','localtime')
                WHERE batch_run_id = ? AND student_id = ? AND status = 'succeeded'
                """,
                ((batch_run_id, student_id) for student_id in missing),
            )

    def list_batches_for_draft(self, draft_id: str) -> tuple[dict[str, Any], ...]:
        clean_id = _identifier(draft_id, "draft_id")
        initialize_database(self.db_path)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT batch_run_id
                FROM personalized_paper_batches
                WHERE draft_id = ?
                ORDER BY created_at DESC, batch_run_id DESC
                """,
                (clean_id,),
            ).fetchall()
        return tuple(self._public_batch(str(row["batch_run_id"])) for row in rows)

    def _batch_by_operation(self, operation_token: str) -> Mapping[str, Any] | None:
        with connect(self.db_path) as connection:
            return connection.execute(
                """
                SELECT * FROM personalized_paper_batches
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()

    def _batch_is_cancelled(self, batch_run_id: str) -> bool:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT status FROM personalized_paper_batches
                WHERE batch_run_id = ?
                """,
                (batch_run_id,),
            ).fetchone()
        return row is not None and str(row["status"]) == "cancelled"

    def _batch_results(
        self,
        batch_run_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM personalized_paper_batch_items
                WHERE batch_run_id = ? ORDER BY item_order
                """,
                (batch_run_id,),
            ).fetchall()
        created = [
            self._get_ready(str(row["paper_instance_id"]))
            for row in rows if str(row["status"]) == "succeeded"
        ]
        failed = [
            {
                "student_id": str(row["student_id"]),
                "error_code": str(row["error_code"]),
            }
            for row in rows if str(row["status"]) == "failed"
        ]
        return created, failed

    def _current_instance_for_student(
        self,
        draft_id: str,
        *,
        student_id: str,
        draft_revision: int,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM personalized_paper_instances
                WHERE draft_id = ? AND student_id = ? AND draft_revision = ?
                  AND status IN ('review_pending', 'frozen')
                ORDER BY series_version DESC LIMIT 1
                """,
                (draft_id, student_id, draft_revision),
            ).fetchone()
        return self._public_instance(row) if row is not None else None

    def _reserve_batch(
        self,
        *,
        batch_run_id: str,
        operation_token: str,
        operation_fingerprint: str,
        draft_id: str,
        draft_revision: int,
        paper_batch_id: str,
        targets: Sequence[str],
        actor_ref: str,
        request_json: str,
    ) -> None:
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM personalized_paper_batches
                WHERE operation_token = ?
                """,
                (operation_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["operation_fingerprint"]) != operation_fingerprint:
                    raise PaperRequestConflict("paper batch operation token was reused")
                return
            connection.execute(
                """
                INSERT INTO personalized_paper_batches (
                    batch_run_id, operation_token, operation_fingerprint,
                    draft_id, draft_revision, paper_batch_id, status, request_json,
                    requested_count, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)
                """,
                (
                    batch_run_id,
                    operation_token,
                    operation_fingerprint,
                    draft_id,
                    draft_revision,
                    paper_batch_id,
                    request_json,
                    len(targets),
                    actor_ref,
                ),
            )
            connection.executemany(
                """
                INSERT INTO personalized_paper_batch_items (
                    batch_run_id, student_id, item_order, status
                ) VALUES (?, ?, ?, 'pending')
                """,
                (
                    (batch_run_id, student_id, index)
                    for index, student_id in enumerate(targets, start=1)
                ),
            )

    def _finish_batch_item(
        self,
        batch_run_id: str,
        *,
        student_id: str,
        paper_instance_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        status = "succeeded" if paper_instance_id else "failed"
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE personalized_paper_batch_items
                SET status = ?, paper_instance_id = ?, error_code = ?,
                    updated_at = datetime('now','localtime')
                WHERE batch_run_id = ? AND student_id = ?
                  AND status IN ('pending', 'running')
                """,
                (status, paper_instance_id, error_code, batch_run_id, student_id),
            )

    def _start_batch_item(self, batch_run_id: str, student_id: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE personalized_paper_batch_items
                SET status = 'running', updated_at = datetime('now','localtime')
                WHERE batch_run_id = ? AND student_id = ? AND status = 'pending'
                """,
                (batch_run_id, student_id),
            )

    def _public_batch(self, batch_run_id: str) -> dict[str, Any]:
        with connect(self.db_path) as connection:
            batch = connection.execute(
                """
                SELECT * FROM personalized_paper_batches
                WHERE batch_run_id = ?
                """,
                (batch_run_id,),
            ).fetchone()
            if batch is None:
                raise PaperInstanceNotFound(batch_run_id)
            rows = connection.execute(
                """
                SELECT * FROM personalized_paper_batch_items
                WHERE batch_run_id = ? ORDER BY item_order
                """,
                (batch_run_id,),
            ).fetchall()
            instances = [
                connection.execute(
                    """
                    SELECT * FROM personalized_paper_instances
                    WHERE paper_instance_id = ?
                    """,
                    (row["paper_instance_id"],),
                ).fetchone()
                for row in rows if str(row["status"]) == "succeeded"
            ]
        items = [self._public_instance(row) for row in instances if row is not None]
        failures = [
            {
                "student_id": str(row["student_id"]),
                "error_code": str(row["error_code"]),
            }
            for row in rows if str(row["status"]) == "failed"
        ]
        return {
            "batch_run_id": str(batch["batch_run_id"]),
            "paper_batch_id": str(batch["paper_batch_id"]),
            "status": str(batch["status"]),
            "requested_count": int(batch["requested_count"]),
            "succeeded_count": int(batch["succeeded_count"]),
            "failed_count": int(batch["failed_count"]),
            "items": items,
            "failures": failures,
            "downloads": {
                "bundle": (
                    f"/api/training/paper-batches/{batch_run_id}/files/bundle"
                    if batch["bundle_path"] else None
                ),
                "manifest": (
                    f"/api/training/paper-batches/{batch_run_id}/files/manifest"
                    if batch["manifest_path"] else None
                ),
                "frozen_bundle": (
                    f"/api/training/paper-batches/{batch_run_id}/files/frozen-bundle"
                    if any(item["status"] == "frozen" for item in items) else None
                ),
            },
        }

    def batch_artifact_path(self, batch_run_id: str, kind: str) -> tuple[Path, str]:
        clean_id = _identifier(batch_run_id, "batch_run_id")
        initialize_database(self.db_path)
        columns = {
            "bundle": ("bundle_path", "application/zip"),
            "manifest": ("manifest_path", "application/json"),
            "frozen-bundle": ("frozen_bundle_path", "application/zip"),
        }
        if kind not in columns:
            raise PaperArtifactNotFound(kind)
        column, media_type = columns[kind]
        if kind == "frozen-bundle":
            self._publish_frozen_batch(clean_id)
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT bundle_path, manifest_path, frozen_bundle_path
                FROM personalized_paper_batches WHERE batch_run_id = ?
                """,
                (clean_id,),
            ).fetchone()
        if row is None or not row[column]:
            raise PaperArtifactNotFound(kind)
        relative = str(row[column])
        path = self._absolute_artifact(relative)
        if not path.is_file():
            raise PaperArtifactNotFound(kind)
        return path, media_type

    def _publish_frozen_batch(self, batch_run_id: str) -> None:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT paper_instance_id, student_id
                FROM personalized_paper_batch_items
                WHERE batch_run_id = ? AND status = 'succeeded'
                ORDER BY item_order
                """,
                (batch_run_id,),
            ).fetchall()
        instances: list[dict[str, Any]] = []
        for row in rows:
            instance = self._get_ready(str(row["paper_instance_id"]))
            if instance["status"] == "frozen" and instance["downloads"]["frozen_pdf"]:
                instances.append(instance)
        if not instances:
            raise PaperArtifactNotFound("frozen-bundle")
        relative = (
            Path("question_bank") / "personalized_papers" / "batches"
            / batch_run_id / "frozen-papers.zip"
        ).as_posix()
        destination = self._absolute_artifact(relative)
        temporary_root = self.artifact_root / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"frozen-batch-{batch_run_id[:12]}-",
            dir=temporary_root,
            ignore_cleanup_errors=True,
        ) as temporary:
            staged = Path(temporary) / "frozen-papers.zip"
            used_names: set[str] = set()
            frozen_manifest: list[dict[str, Any]] = []
            with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in instances:
                    source, _ = self._artifact_path_ready(
                        str(item["paper_instance_id"]),
                        "frozen-pdf",
                    )
                    stem = _safe_filename(
                        str(item.get("student_name") or item.get("student_code") or item["student_id"])
                    )
                    stable = _safe_filename(str(item.get("student_code") or item["student_id"]))
                    candidate = f"{stem}-{stable}-个性化训练卷-V{item['series_version']}.pdf"
                    suffix = 2
                    while candidate.casefold() in used_names:
                        candidate = f"{stem}-{stable}-个性化训练卷-V{item['series_version']}-{suffix}.pdf"
                        suffix += 1
                    used_names.add(candidate.casefold())
                    archive.write(source, candidate)
                    frozen_manifest.append({
                        "paper_instance_id": item["paper_instance_id"],
                        "student_id": item["student_id"],
                        "student_code": item.get("student_code"),
                        "student_name": item.get("student_name"),
                        "class_id": item.get("class_id"),
                        "series_version": item["series_version"],
                        "page_count": len(item["pages"]),
                        "frozen_pdf_sha256": item["frozen_pdf_sha256"],
                    })
                archive.writestr("frozen-manifest.json", _json({
                    "schema_version": "personalized-paper-frozen-batch-v1",
                    "batch_run_id": batch_run_id,
                    "created_at": self.clock().isoformat(),
                    "items": frozen_manifest,
                }))
            _atomic_publish(staged, destination)
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE personalized_paper_batches
                SET frozen_bundle_path = ?, updated_at = datetime('now','localtime')
                WHERE batch_run_id = ?
                """,
                (relative, batch_run_id),
            )

    def _publish_review_batch(
        self,
        batch_run_id: str,
        *,
        draft: Mapping[str, Any],
        instances: Sequence[Mapping[str, Any]],
        failures: Sequence[Mapping[str, str]],
    ) -> dict[str, str | None]:
        relative_root = (
            Path("question_bank") / "personalized_papers" / "batches" / batch_run_id
        )
        destination_root = self._absolute_artifact(relative_root.as_posix())
        destination_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "personalized-paper-review-batch-v1",
            "batch_run_id": batch_run_id,
            "draft_id": str(draft.get("draft_id") or ""),
            "draft_revision": int(draft.get("revision") or 0),
            "created_at": self.clock().isoformat(),
            "status": (
                "complete" if instances and not failures
                else "partial" if instances else "failed"
            ),
            "requested_count": len(instances) + len(failures),
            "succeeded_count": len(instances),
            "failed_count": len(failures),
            "items": [
                {
                    "paper_instance_id": str(item.get("paper_instance_id") or ""),
                    "student_id": str(item.get("student_id") or ""),
                    "student_code": str(item.get("student_code") or ""),
                    "student_name": str(item.get("student_name") or ""),
                    "class_id": str(item.get("class_id") or ""),
                    "series_version": int(item.get("series_version") or 0),
                    "status": str(item.get("status") or ""),
                    "question_count": int(item.get("question_count") or 0),
                    "page_count": int(_mapping(item.get("budget")).get("page_count") or 0),
                    "formula_fallback_count": len(item.get("formula_fallbacks") or []),
                    "formula_fallbacks": list(item.get("formula_fallbacks") or []),
                    "review_docx_sha256": str(item.get("review_docx_sha256") or ""),
                }
                for item in instances
            ],
            "failures": [dict(item) for item in failures],
        }
        manifest_path = destination_root / "manifest.json"
        bundle_path = destination_root / "papers.zip"
        temporary_root = self.artifact_root / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"batch-{batch_run_id[:12]}-",
            dir=temporary_root,
            ignore_cleanup_errors=True,
        ) as temporary:
            staged_manifest = Path(temporary) / "manifest.json"
            staged_manifest.write_text(_json(manifest), encoding="utf-8")
            staged_bundle = Path(temporary) / "papers.zip"
            used_names: set[str] = set()
            name_counts: dict[str, int] = {}
            for item in instances:
                stem = _safe_filename(
                    str(item.get("student_name") or item.get("student_code") or item.get("student_id") or "学生")
                )
                name_counts[stem.casefold()] = name_counts.get(stem.casefold(), 0) + 1
            with zipfile.ZipFile(staged_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(staged_manifest, "manifest.json")
                for item in instances:
                    paper_id = str(item.get("paper_instance_id") or "")
                    try:
                        source, _ = self._artifact_path_ready(
                            paper_id,
                            "review-docx",
                        )
                    except PaperArtifactNotFound:
                        continue
                    stem = _safe_filename(
                        str(item.get("student_name") or item.get("student_code") or item.get("student_id") or "学生")
                    )
                    if name_counts.get(stem.casefold(), 0) > 1:
                        stable = _safe_filename(
                            str(item.get("student_code") or item.get("student_id") or "编号")
                        )
                        stem = f"{stem}-{stable}"
                    version = int(item.get("series_version") or 1)
                    candidate = f"{stem}-个性化训练卷-V{version}.docx"
                    suffix = 2
                    while candidate.casefold() in used_names:
                        candidate = f"{stem}-个性化训练卷-V{version}-{suffix}.docx"
                        suffix += 1
                    used_names.add(candidate.casefold())
                    archive.write(source, candidate)
            _atomic_publish(staged_manifest, manifest_path)
            _atomic_publish(staged_bundle, bundle_path)
        return {
            "bundle": f"/api/training/paper-batches/{batch_run_id}/files/bundle" if instances else None,
            "manifest": f"/api/training/paper-batches/{batch_run_id}/files/manifest",
            "bundle_path": (relative_root / "papers.zip").as_posix() if instances else None,
            "manifest_path": (relative_root / "manifest.json").as_posix(),
        }

    def artifact_path(
        self,
        paper_instance_id: str,
        kind: ArtifactKind,
    ) -> tuple[Path, str]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
        initialize_database(self.db_path)
        return self._artifact_path_ready(clean_id, kind)

    def _artifact_path_ready(
        self,
        paper_instance_id: str,
        kind: ArtifactKind,
    ) -> tuple[Path, str]:
        clean_id = _identifier(paper_instance_id, "paper_instance_id")
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
            approved = usable_training_criterion(workspace)
            if (
                approved is None
                or str(approved["version_id"]) != expected_version
                or str(version["source_content_hash"]) not in {
                    question.criterion_source_content_hash,
                    *workspace.get("compatible_source_hashes", ()),
                }
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
        frozen_assets_by_sha256: dict[str, str] = {}
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
            frozen_assets_by_sha256[image.sha256] = relative
        fallback_asset = images[0]["asset_path"] if images else None
        fallback_sha256 = images[0]["sha256"] if images else None
        math_expressions = []
        for index, match in enumerate(
            _MATH_RUN.finditer(question.tagging_context.question_text)
        ):
            source = match.group(1) if match.group(1) is not None else match.group(2)
            math_expressions.append(asdict(build_math_expression(
                expression_id=f"p4-{question.question_id}-math-{index + 1}",
                source=source,
                fallback_asset=fallback_asset,
                fallback_sha256=fallback_sha256,
            )))
        return {
            "question_id": question.question_id,
            "tagging_context": question.tagging_context.to_dict(),
            "rich_question_blocks": [
                *_frozen_word_blocks(
                    question.word_question_blocks,
                    frozen_assets_by_sha256,
                )
            ],
            "rich_answer_blocks": [
                *_frozen_word_blocks(
                    question.word_answer_blocks,
                    frozen_assets_by_sha256,
                )
            ],
            "images": images,
            "math_expressions": math_expressions,
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
        record_event: bool = True,
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
                formula_fallbacks = render_review_docx(
                    snapshot,
                    data_root=self.data_root,
                    output_path=staged,
                )
                review_hash = _file_sha256(staged)
                _atomic_publish(staged, destination)
            rendered_snapshot = dict(snapshot)
            rendered_snapshot["formula_fallbacks"] = list(formula_fallbacks)
            with connect(self.db_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    """
                    UPDATE personalized_paper_instances
                    SET status = 'review_pending',
                        review_docx_path = ?,
                        review_docx_sha256 = ?,
                        snapshot_json = ?,
                        error_code = NULL,
                        updated_at = datetime('now','localtime')
                    WHERE paper_instance_id = ?
                      AND status IN ('creating', 'failed', 'review_pending')
                    """,
                    (
                        relative,
                        review_hash,
                        _json(rendered_snapshot),
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
                if record_event:
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
                            "formula_fallback_count": len(formula_fallbacks),
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
        render_info: Mapping[str, Any] | None = None,
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
            if render_info is not None:
                current_snapshot["render_info"] = dict(render_info)
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
        snapshot = json.loads(str(row["snapshot_json"]))
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
            "formula_fallbacks": list(snapshot.get("formula_fallbacks") or []),
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
    # 出卷全程本机排版，不调用模型：token 估算只作诊断信息，不再拦截。
    # 真实保护由上面的题量、判定点、图片、页数硬上限承担。
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


def _frozen_word_blocks(
    blocks: Sequence[Mapping[str, Any]],
    frozen_assets_by_sha256: Mapping[str, str],
) -> list[dict[str, Any]]:
    frozen: list[dict[str, Any]] = []
    for source_block in blocks:
        block = dict(source_block)
        relationships = block.get("image_relationships")
        if isinstance(relationships, Mapping):
            governed: dict[str, str] = {}
            for relationship_id, reference in relationships.items():
                match = _GOVERNED_IMAGE_REFERENCE.fullmatch(
                    str(reference or "").strip()
                )
                if match is None:
                    raise PaperSourceChanged(
                        "rich question image reference is not governed"
                    )
                asset_path = frozen_assets_by_sha256.get(match.group(1))
                if not asset_path:
                    raise PaperSourceChanged(
                        "rich question image is absent from the frozen snapshot"
                    )
                governed[str(relationship_id)] = asset_path
            block["image_relationships"] = governed
        frozen.append(block)
    return frozen


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
        replace_with_retry(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)



def _atomic_publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    replace_with_retry(source, destination)


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


def _direct_freeze_token(operation_token: str) -> str:
    """Derive a separate idempotency token for the direct-freeze step."""
    return hashlib.sha256(
        f"{operation_token}:direct-freeze".encode("utf-8")
    ).hexdigest()[:32]


def _required_text(value: object, name: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{name} is invalid")
    return result


def _optional_text(value: object) -> str | None:
    result = str(value or "").strip()
    return result or None


def _safe_filename(value: object) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or "").strip())
    cleaned = cleaned.strip(" .-")
    return (cleaned or "学生")[:80]


_hash_payload = canonical_hash
_json = canonical_json


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
