from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from backend.repositories.access import GradingRepositoryAccess
from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.editor import (
    ManualPartInput,
    ReplaceScoringUnitsCommand,
    SplitScoringUnitCommand,
    apply_config_editor_changes,
    editor_identity_signature,
    editor_part_ids,
)
from backend.config_workspace.publish import (
    PublishedConfig,
    load_editor_config,
    publish_generated_config,
    refresh_mapping_after_config_save,
    refresh_template_mapping_from_session,
    remove_published_config,
)
from backend.config_workspace.sources import (
    AmbiguousAssetDecision,
    ConfigSourceRecord,
    ConfigSourceService,
    QuestionDecision,
)
from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from question_bank.services.source_paper_archive_service import (
    ArchivedSourcePaper,
    archive_source_bytes,
    source_archive_sha_lock,
)
from backend.config_generation.compat import (
    allocate_grading_config_scores,
    failed_grading_config_batches,
    failed_grading_config_question_ids,
    generate_grading_config_in_batches,
    regenerate_grading_config_questions,
    retry_failed_grading_config_batches,
    refine_grading_config_from_manual_structure,
)
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_volume,
    infer_curriculum_volume_from_text,
)
from question_bank.training_criteria import (
    ConfigQuestionAnalysisSource,
    DeferredCombinedAnalysisBundle,
    InMemoryCombinedQuestionAnalysisModule,
    OpenAICombinedAnalysisGateway,
    QuestionAnalysisImage,
    question_analysis_input_from_config_source,
)
from backend.config_workspace.deferred_analysis import (
    DeferredAnalysisArtifact,
    DeferredAnalysisArtifactStore,
)
from backend.config_generation.normalization import (
    normalize_generated_config_schema,
    normalize_new_generated_config_payload,
    strip_generated_config_knowledge_fields,
)
from backend.config_generation.quality import (
    blocking_quality_question_ids,
    collect_generated_config_quality_issues,
    refresh_generated_config_quality_warnings,
)
from session_manager import (
    generate_grading_config_from_images,
    generate_grading_config_from_text,
)

from .manager import JobCancellationRequested, JobContext
from .question_bank_sync import run_deferred_question_bank_intake


# Compatibility names for older callers/tests. Both execute the new batched
# implementation; no per-question request behavior remains.
generate_grading_config_from_confirmed_blocks = generate_grading_config_in_batches
retry_failed_grading_config_questions = retry_failed_grading_config_batches

if TYPE_CHECKING:
    from .store import JobStore


_INPUT_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_CONFIG_GENERATION_INPUT_BYTES = 96 * 1024 * 1024


def _input_path(upload_config_dir: Path, input_id: str) -> Path:
    clean_id = str(input_id or "").strip().casefold()
    if not _INPUT_ID.fullmatch(clean_id):
        raise ValueError("invalid config generation input id")
    return Path(upload_config_dir) / f"config_generation_input_{clean_id}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_CONFIG_GENERATION_INPUT_BYTES:
        raise ValueError("config generation input exceeds size limit")
    filesystem = SecureRootFilesystem(path.parent)
    filesystem.write_json_atomic(path, payload)


def stage_config_generation_input(
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_rubric_path: str,
    expected_answer_key_path: str,
    confirmed_blocks: list[dict[str, Any]],
    document_text: str,
    question_images: dict[str, Any] | None,
    generation_mode: str | None = None,
    source_id: str | None = None,
    source_revision: str | None = None,
    source_suffix: str | None = None,
    source_safe_filename: str | None = None,
    whole_page_images: list[bytes] | None = None,
    sync_to_question_bank: bool = False,
) -> str:
    input_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "session_id": int(session_id),
        "expected_rubric_path": str(expected_rubric_path),
        "expected_answer_key_path": str(expected_answer_key_path),
        "confirmed_blocks": confirmed_blocks,
        "document_text": str(document_text or ""),
        "question_images": dict(question_images or {}),
        "sync_to_question_bank": bool(sync_to_question_bank),
    }
    if generation_mode is not None:
        payload.update(
            {
                "generation_mode": str(generation_mode),
                "source_id": str(source_id or ""),
                "source_revision": str(source_revision or ""),
                "source_suffix": str(source_suffix or ""),
                "source_safe_filename": str(source_safe_filename or ""),
                "whole_page_images": [
                    base64.b64encode(bytes(image)).decode("ascii")
                    for image in (whole_page_images or [])
                ],
            }
        )
    _write_json_atomic(
        _input_path(upload_config_dir, input_id),
        payload,
    )
    return input_id


def stage_config_refine_input(
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_rubric_path: str,
    expected_answer_key_path: str,
    expected_revision: str,
    existing_payload: dict[str, Any],
    commands: list[dict[str, Any]],
) -> str:
    input_id = uuid.uuid4().hex
    _write_json_atomic(
        _input_path(upload_config_dir, input_id),
        {
            "session_id": int(session_id),
            "expected_rubric_path": str(expected_rubric_path),
            "expected_answer_key_path": str(expected_answer_key_path),
            "expected_revision": str(expected_revision),
            "existing_payload": existing_payload,
            "commands": commands,
        },
    )
    return input_id


def stage_config_source_generation_input(
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_rubric_path: str,
    expected_answer_key_path: str,
    generation_mode: str,
    source_id: str,
    source_revision: str,
    decisions: list[dict[str, Any]],
    asset_decisions: list[dict[str, Any]] | None = None,
    sync_to_question_bank: bool = False,
    curriculum_volume_id: str | None = None,
    existing_payload: dict[str, Any] | None = None,
    regenerate_question_ids: list[str] | None = None,
    expected_revision: str | None = None,
) -> str:
    input_id = uuid.uuid4().hex
    analysis_artifact_id = (
        DeferredAnalysisArtifactStore.new_artifact_id()
        if sync_to_question_bank
        else ""
    )
    payload: dict[str, Any] = {
            "session_id": int(session_id),
            "expected_rubric_path": str(expected_rubric_path),
            "expected_answer_key_path": str(expected_answer_key_path),
            "generation_mode": str(generation_mode),
            "source_id": str(source_id),
            "source_revision": str(source_revision),
            "decisions": list(decisions),
            "asset_decisions": list(asset_decisions or []),
            "sync_to_question_bank": bool(sync_to_question_bank),
            "curriculum_volume_id": str(curriculum_volume_id or "").strip(),
            "analysis_artifact_id": analysis_artifact_id,
        }
    if regenerate_question_ids is not None:
        if existing_payload is None or expected_revision is None:
            raise ValueError(
                "targeted regeneration input requires the current configuration"
            )
        payload.update(
            {
                "existing_payload": existing_payload,
                "regenerate_question_ids": list(regenerate_question_ids),
                "expected_revision": str(expected_revision),
            }
        )
    _write_json_atomic(_input_path(upload_config_dir, input_id), payload)
    return input_id


def load_config_generation_input(
    upload_config_dir: Path,
    input_id: str,
) -> dict[str, Any]:
    path = _input_path(upload_config_dir, input_id)
    payload = json.loads(
        SecureRootFilesystem(Path(upload_config_dir)).read_text(
            path,
            encoding="utf-8",
            max_bytes=MAX_CONFIG_GENERATION_INPUT_BYTES,
        )
    )
    if not isinstance(payload, dict):
        raise ValueError("config generation input must contain a JSON object")
    return payload


def discard_config_generation_input(upload_config_dir: Path, input_id: str) -> None:
    path = _input_path(upload_config_dir, input_id)
    try:
        SecureRootFilesystem(Path(upload_config_dir)).unlink_many((path,))
    except SecureFilesystemError:
        pass


def cleanup_consumed_config_retry_artifacts(
    upload_config_dir: Path,
    store: "JobStore",
    *,
    session_id: int | None = None,
) -> None:
    root = Path(upload_config_dir)
    artifacts = store.consumed_config_retry_artifacts(session_id)
    if not artifacts:
        return
    filesystem = SecureRootFilesystem(root)
    active_input_ids = store.active_config_input_ids()
    draft_paths = tuple(
        _draft_path(root, source_job_id)
        for _artifact_session_id, source_job_id, _input_id in artifacts
    )
    try:
        filesystem.unlink_many(draft_paths)
    except SecureFilesystemError:
        pass
    for input_id in {
        input_id
        for _artifact_session_id, _source_job_id, input_id in artifacts
        if input_id not in active_input_ids
    }:
        discard_config_generation_input(root, input_id)
    service = ConfigSourceService(root)
    for artifact_session_id in sorted({item[0] for item in artifacts}):
        service.cleanup_inactive(
            session_id=artifact_session_id,
            referenced_source_ids=store.referenced_config_source_ids(
                artifact_session_id
            ),
        )


def run_config_generation_job(
    *,
    context: JobContext,
    db: GradingRepositoryAccess,
    upload_config_dir: Path,
    llm_client_factory: Callable[[], Any],
    data_root: Path | None = None,
    mapping_output_dir: Path | None = None,
    question_bank_db_path: Path | None = None,
    tagging_ai_service_factory: Callable[[], Any] | None = None,
    taxonomy_governance: Any | None = None,
    question_bank_intake_runner: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    input_id = str(context.payload.get("input_id") or "")
    mode = str(context.payload.get("mode") or "").strip()
    try:
        result = _run_config_generation_job_impl(
            context=context,
            db=db,
            upload_config_dir=upload_config_dir,
            llm_client_factory=llm_client_factory,
            data_root=data_root,
            mapping_output_dir=mapping_output_dir,
            question_bank_db_path=question_bank_db_path,
            tagging_ai_service_factory=tagging_ai_service_factory,
            taxonomy_governance=taxonomy_governance,
            question_bank_intake_runner=question_bank_intake_runner,
        )
    except JobCancellationRequested:
        draft = _draft_path(upload_config_dir, context.job_id)
        if draft.is_file():
            try:
                payload = _read_json_object(draft)
                context.store.finish(
                    context.job_id,
                    "cancelled",
                    result=_summary_from_batch_draft(
                        int(context.payload.get("session_id") or 0), payload
                    ),
                )
            except Exception:
                pass
        elif input_id and mode != "retry":
            discard_config_generation_input(upload_config_dir, input_id)
        raise
    except BaseException as exc:
        if input_id and mode != "retry":
            discard_config_generation_input(upload_config_dir, input_id)
        if mode == "retry":
            completed = context.store.get_job(context.job_id)
            if completed is not None and completed.status == "succeeded":
                cleanup_consumed_config_retry_artifacts(
                    upload_config_dir,
                    context.store,
                    session_id=int(context.payload.get("session_id") or 0),
                )
        raise
    if input_id and result.get("outcome") != "partial":
        discard_config_generation_input(upload_config_dir, input_id)
    if mode == "retry" and result.get("outcome") == "complete":
        cleanup_consumed_config_retry_artifacts(
            upload_config_dir,
            context.store,
            session_id=int(context.payload.get("session_id") or 0),
        )
    return result


def preserve_interrupted_config_generation_checkpoints(
    upload_config_dir: Path,
    store: "JobStore",
) -> set[str]:
    """Mark interrupted checkpointed jobs retryable before generic restart cleanup."""
    jobs, _total = store.list_jobs(
        job_types=("config_generation",),
        statuses=("queued", "running"),
        limit=10_000,
        offset=0,
    )
    protected_inputs: set[str] = set()
    for job in jobs:
        draft = _draft_path(upload_config_dir, job.id)
        if not draft.is_file():
            continue
        try:
            payload = _read_json_object(draft)
            summary = _summary_from_batch_draft(
                int(job.payload.get("session_id") or 0), payload
            )
        except Exception:
            continue
        store.finish(
            job.id,
            "failed",
            error="interrupted by process restart; completed batches were preserved",
            result=summary,
        )
        input_id = str(job.payload.get("input_id") or "").strip()
        if input_id:
            protected_inputs.add(input_id)
    return protected_inputs


def _run_config_generation_job_impl(
    *,
    context: JobContext,
    db: GradingRepositoryAccess,
    upload_config_dir: Path,
    llm_client_factory: Callable[[], Any],
    data_root: Path | None = None,
    mapping_output_dir: Path | None = None,
    question_bank_db_path: Path | None = None,
    tagging_ai_service_factory: Callable[[], Any] | None = None,
    taxonomy_governance: Any | None = None,
    question_bank_intake_runner: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    session_id = _required_int(context.payload, "session_id")
    session = db.get_grading_session(session_id)
    if session is None or bool(int(session.get("is_deleted") or 0)):
        raise ValueError("grading session is unavailable")
    mode = str(context.payload.get("mode") or "").strip()
    if mode not in {"generate", "retry", "refine", "regenerate_questions"}:
        raise ValueError("unsupported config generation mode")

    existing_payload: dict[str, Any] | None = None
    if mode in {"generate", "refine", "regenerate_questions"}:
        input_id = str(context.payload.get("input_id") or "")
    else:
        source_job_id = _required_int(context.payload, "source_job_id")
        source_job = context.store.get_job(source_job_id)
        source_outcome = source_job.result.get("outcome") if source_job is not None else None
        # Keep accepting legacy partial checkpoints that predate structured
        # failed_batches. The public route still validates the exact selected
        # failure scope before creating a retry job.
        retry_failed_batches = source_outcome == "partial"
        raw_requested_retry_ids = context.payload.get("retry_question_ids")
        requested_retry_ids = {
            str(item).strip()
            for item in (
                raw_requested_retry_ids
                if isinstance(raw_requested_retry_ids, list)
                else []
            )
            if str(item).strip()
        }
        raw_source_uncertain_ids = (
            source_job.result.get("uncertain_question_ids")
            if source_job is not None
            else None
        )
        source_uncertain_ids = {
            str(item).strip()
            for item in (
                raw_source_uncertain_ids
                if isinstance(raw_source_uncertain_ids, list)
                else []
            )
            if str(item).strip()
        }
        retry_uncertain_results = (
            source_outcome == "partial"
            and source_job is not None
            and bool(context.payload.get("confirm_uncertain_retry"))
            and bool(source_uncertain_ids)
            and requested_retry_ids == source_uncertain_ids
        )
        retry_score_allocation = (
            source_outcome == "partial"
            and source_job is not None
            and bool(source_job.result.get("score_allocation_pending"))
            and not bool(source_job.result.get("failed_batches"))
        )
        resume_complete_draft = (
            source_outcome == "complete"
            and source_job is not None
            and source_job.status in {"failed", "cancelled"}
        )
        if (
            source_job is None
            or source_job.job_type != "config_generation"
            or source_job.status not in {"succeeded", "failed", "cancelled"}
            or not (
                retry_failed_batches
                or retry_uncertain_results
                or retry_score_allocation
                or resume_complete_draft
            )
            or _required_int(source_job.payload, "session_id") != session_id
            or str(source_job.payload.get("generation_mode") or "batched")
            not in {"batched", "per_question"}
        ):
            raise ValueError("config generation source job is not retryable")
        input_id = str(source_job.payload.get("input_id") or "")
        existing_payload = _read_json_object(_draft_path(upload_config_dir, source_job_id))

    inputs = load_config_generation_input(upload_config_dir, input_id)
    if _required_int(inputs, "session_id") != session_id:
        raise ValueError("config generation input does not belong to session")
    if mode == "regenerate_questions":
        raw_existing_payload = inputs.get("existing_payload")
        if not isinstance(raw_existing_payload, dict):
            raise ValueError("targeted regeneration is missing the current configuration")
        existing_payload = raw_existing_payload
    sync_to_question_bank = bool(
        context.payload.get("sync_to_question_bank")
        or inputs.get("sync_to_question_bank")
    )
    curriculum_volume_id = str(
        inputs.get("curriculum_volume_id") or ""
    ).strip()
    if sync_to_question_bank:
        volume = curriculum_volume(volume_id=curriculum_volume_id)
        if volume is None:
            raise ValueError(
                "curriculum volume must be confirmed before question analysis"
            )
        curriculum_volume_id = str(volume["id"])
    expected_rubric_path = str(inputs.get("expected_rubric_path") or "")
    expected_answer_key_path = str(inputs.get("expected_answer_key_path") or "")
    if (
        str(session.get("rubric_path") or "") != expected_rubric_path
        or str(session.get("answer_key_path") or "") != expected_answer_key_path
    ):
        raise ValueError("session config changed before generation started")
    if mode == "regenerate_questions":
        loaded = load_editor_config(db, session_id)
        if loaded.revision != str(inputs.get("expected_revision") or ""):
            raise ValueError("session config changed before regeneration started")
    if mode == "refine":
        return _run_refine_config_job(
            context=context,
            db=db,
            upload_config_dir=Path(upload_config_dir),
            llm_client_factory=llm_client_factory,
            inputs=inputs,
            session_id=session_id,
            expected_rubric_path=expected_rubric_path,
            expected_answer_key_path=expected_answer_key_path,
            mapping_output_dir=mapping_output_dir,
        )
    generation_mode = str(
        context.payload.get("generation_mode")
        or inputs.get("generation_mode")
        or "batched"
    ).strip()
    if generation_mode == "per_question":
        generation_mode = "batched"
    if generation_mode not in {"batched", "whole_document"}:
        raise ValueError("unsupported config generation mode")
    if mode == "retry" and generation_mode != "batched":
        raise ValueError("whole-document config generation is not retryable")
    staged_generation_mode = inputs.get("generation_mode")
    staged_mode = str(staged_generation_mode or "").strip()
    if staged_mode == "per_question":
        staged_mode = "batched"
    if staged_generation_mode is not None and staged_mode != generation_mode:
        raise ValueError("config generation mode changed")

    source_record: ConfigSourceRecord | None = None
    source_id = str(context.payload.get("source_id") or inputs.get("source_id") or "").strip()
    source_revision = str(
        context.payload.get("source_revision") or inputs.get("source_revision") or ""
    ).strip()
    if bool(source_id) != bool(source_revision):
        raise ValueError("config source identity is incomplete")
    source_service: ConfigSourceService | None = None
    if source_id:
        if (
            source_id != str(inputs.get("source_id") or "").strip()
            or source_revision != str(inputs.get("source_revision") or "").strip()
        ):
            raise ValueError("config source identity changed")
        source_service = ConfigSourceService(Path(upload_config_dir))
        source_record = source_service.load_for_generation(
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
        )
        raw_decisions = inputs.get("decisions")
        if not isinstance(raw_decisions, list):
            raise ValueError("config source decisions are invalid")
        decisions: list[QuestionDecision] = []
        for item in raw_decisions:
            if not isinstance(item, dict):
                raise ValueError("config source decisions are invalid")
            decisions.append(
                QuestionDecision(
                    question_id=str(item.get("question_id") or ""),
                    excluded=bool(item.get("excluded")),
                    question_type=(
                        str(item.get("question_type"))
                        if item.get("question_type") is not None
                        else None
                    ),
                    answer_confirmed=item.get("answer_confirmed") is True,
                    answer_override=(
                        str(item.get("answer_override"))
                        if item.get("answer_override") is not None
                        else None
                    ),
                )
            )
        raw_asset_decisions = inputs.get("asset_decisions", [])
        if not isinstance(raw_asset_decisions, list):
            raise ValueError("config ambiguous asset decisions are invalid")
        asset_decisions: list[AmbiguousAssetDecision] = []
        for item in raw_asset_decisions:
            if not isinstance(item, dict):
                raise ValueError("config ambiguous asset decisions are invalid")
            asset_decisions.append(
                AmbiguousAssetDecision(
                    candidate_id=str(item.get("candidate_id") or ""),
                    action=str(item.get("action") or ""),  # type: ignore[arg-type]
                    question_id=(
                        str(item.get("question_id"))
                        if item.get("question_id") is not None
                        else None
                    ),
                    asset_kind=(
                        str(item.get("asset_kind"))
                        if item.get("asset_kind") is not None
                        else None
                    ),  # type: ignore[arg-type]
                )
            )
        prepared = source_service.prepare_generation_input(
            source_record, decisions, generation_mode, asset_decisions
        )
        confirmed_blocks = list(prepared.confirmed_blocks)
        question_images = prepared.question_images
        document_text = prepared.document_text
        whole_page_images = list(prepared.whole_page_images)
        source_suffix = source_record.suffix
    else:
        confirmed_blocks = inputs.get("confirmed_blocks")
        question_images = inputs.get("question_images")
        document_text = str(inputs.get("document_text") or "")
        whole_page_images = _decode_whole_page_images(inputs.get("whole_page_images"))
        source_suffix = str(inputs.get("source_suffix") or "")
    if not isinstance(confirmed_blocks, list) or (
        generation_mode == "batched" and not confirmed_blocks
    ):
        raise ValueError("confirmed_blocks must be a non-empty list")
    if question_images is not None and not isinstance(question_images, dict):
        raise ValueError("question_images must be an object")

    context.raise_if_cancelled()
    context.report(0.05, "config_generation", "starting")

    def report(progress: float, stage: str, detail: str = "") -> None:
        context.report(progress, stage, detail)

    def checkpoint(value: dict[str, Any]) -> None:
        with session_config_lock(Path(upload_config_dir), session_id):
            current_session = db.get_grading_session(session_id)
            if (
                current_session is None
                or bool(int(current_session.get("is_deleted") or 0))
                or str(current_session.get("rubric_path") or "") != expected_rubric_path
                or str(current_session.get("answer_key_path") or "")
                != expected_answer_key_path
            ):
                raise ValueError("session config changed while generation was running")
            if source_service is not None:
                source_service.load_for_generation(
                    session_id=session_id,
                    source_id=source_id,
                    source_revision=source_revision,
                )
            _write_json_atomic(_draft_path(upload_config_dir, context.job_id), value)
        context.raise_if_cancelled()

    evidence_artifact_hash = ""
    analysis_artifact: DeferredAnalysisArtifact | None = None
    local_quality_retry_refs: tuple[str, ...] = ()
    evidence_flow = (
        sync_to_question_bank
        and source_record is not None
        and generation_mode == "batched"
        and mode in {"generate", "retry"}
        and tagging_ai_service_factory is not None
        and taxonomy_governance is not None
    )
    if evidence_flow:
        evidence_reported_count = 0
        analysis_artifact_id = str(
            inputs.get("analysis_artifact_id") or ""
        ).strip().casefold()
        if not _INPUT_ID.fullmatch(analysis_artifact_id):
            raise ValueError("deferred question analysis identity is invalid")
        artifact_store = DeferredAnalysisArtifactStore(Path(upload_config_dir))
        previous_artifact = (
            artifact_store.load(
                analysis_artifact_id,
                session_id=session_id,
                source_id=source_id,
                source_revision=source_revision,
                curriculum_volume_id=curriculum_volume_id,
            )
            if artifact_store.exists(analysis_artifact_id)
            else None
        )
        retry_source_refs = (
            _retry_source_refs(context.payload)
            if mode == "retry"
            else None
        )
        local_quality_retry_refs = _local_quality_retry_source_refs(
            existing_payload,
            retry_source_refs,
        )

        def evidence_checkpoint(bundle: DeferredCombinedAnalysisBundle) -> None:
            nonlocal evidence_artifact_hash, evidence_reported_count
            artifact = artifact_store.save(
                artifact_id=analysis_artifact_id,
                session_id=session_id,
                source_id=source_id,
                source_revision=source_revision,
                curriculum_volume_id=curriculum_volume_id,
                bundle=bundle,
            )
            evidence_artifact_hash = artifact.content_hash
            checkpoint(
                _deferred_analysis_draft(
                    bundle,
                    exam_title=str(session.get("name") or "待命名试卷"),
                )
            )
            running_refs = set(bundle.running_source_refs)
            completed_refs = (
                {item.source_question_ref for item in bundle.items}
                | {
                    item.source_question_ref
                    for item in bundle.failures
                }
                | set(bundle.uncertain_source_refs)
            ) - running_refs
            completed_count = len(completed_refs)
            total_count = len(bundle.source_fingerprints)
            if completed_count > evidence_reported_count and total_count > 0:
                evidence_reported_count = completed_count
                succeeded_count = len(
                    {
                        item.source_question_ref
                        for item in bundle.items
                        if item.source_question_ref in completed_refs
                    }
                )
                pending_count = max(0, completed_count - succeeded_count)
                context.report(
                    0.08 + 0.76 * (completed_count / total_count),
                    "question_analysis",
                    (
                        f"题目分析已完成 {completed_count}/{total_count} 题；"
                        f"成功 {succeeded_count} 题，待处理 {pending_count} 题。"
                    ),
                )

        if (
            previous_artifact is not None
            and previous_artifact.bundle.status == "succeeded"
            and not local_quality_retry_refs
        ):
            analysis_bundle = previous_artifact.bundle
            evidence_artifact_hash = previous_artifact.content_hash
            analysis_artifact = previous_artifact
        else:
            tagging_service = tagging_ai_service_factory()
            sources = _config_analysis_sources(
                confirmed_blocks,
                question_images or {},
                curriculum_volume_id=curriculum_volume_id,
                tagging_service=tagging_service,
                volume=volume,
            )
            gateway = OpenAICombinedAnalysisGateway(
                protocol_adapter=tagging_service._protocol_adapter(),
                model_name=str(tagging_service.model),
            )
            analysis_module = InMemoryCombinedQuestionAnalysisModule(
                gateway=gateway,
                taxonomy_governance=taxonomy_governance,
            )
        if previous_artifact is None:
            if mode == "retry" and existing_payload is not None:
                raise ValueError(
                    "题目分析断点已丢失；为避免重复调用模型，"
                    "本次未自动重新分析全部题目。"
                )
            context.report(
                0.08,
                "question_analysis",
                f"正在分析题目：0/{len(sources)}。",
            )
            analysis_bundle = analysis_module.analyze(
                operation_id=f"config:{session_id}:{analysis_artifact_id}",
                curriculum_volume_id=curriculum_volume_id,
                sources=sources,
                checkpoint=evidence_checkpoint,
            )
        elif previous_artifact.bundle.running_source_refs:
            context.report(
                0.08,
                "question_analysis",
                "正在核对上次中断的题目分析结果。",
            )
            analysis_bundle = analysis_module.resume_interrupted(
                previous_artifact.bundle,
                sources=sources,
                curriculum_volume_id=curriculum_volume_id,
                checkpoint=evidence_checkpoint,
            )
        elif local_quality_retry_refs:
            context.report(
                0.08,
                "question_analysis",
                "正在重新分析本地结构检查未通过的题目。",
            )
            analysis_bundle = analysis_module.reanalyze_selected(
                previous_artifact.bundle,
                sources=sources,
                curriculum_volume_id=curriculum_volume_id,
                source_refs=local_quality_retry_refs,
                validation_issues_by_ref=_quality_issues_by_ref(
                    existing_payload,
                    local_quality_retry_refs,
                ),
                repair_attempts_by_ref=_quality_repair_attempts_by_ref(
                    existing_payload,
                    local_quality_retry_refs,
                ),
                checkpoint=evidence_checkpoint,
            )
        elif previous_artifact.bundle.status != "succeeded":
            context.report(
                0.08,
                "question_analysis",
                "正在重试教师选定的未完成题目。",
            )
            analysis_bundle = analysis_module.retry_failed(
                previous_artifact.bundle,
                sources=sources,
                curriculum_volume_id=curriculum_volume_id,
                retry_source_refs=retry_source_refs,
                retry_uncertain=bool(
                    context.payload.get("confirm_uncertain_retry")
                ),
                checkpoint=evidence_checkpoint,
            )
        artifact = artifact_store.save(
            artifact_id=analysis_artifact_id,
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
            curriculum_volume_id=curriculum_volume_id,
            bundle=analysis_bundle,
        )
        evidence_artifact_hash = artifact.content_hash
        analysis_artifact = artifact
        if analysis_bundle.status != "succeeded":
            payload = _deferred_analysis_draft(
                analysis_bundle,
                exam_title=str(session.get("name") or "待命名试卷"),
            )
            checkpoint(payload)
        else:
            structure = analysis_bundle.compose_generated_config(
                exam_title=str(session.get("name") or "待命名试卷"),
            )
            normalize_new_generated_config_payload(structure)
            refresh_generated_config_quality_warnings(structure)
            if blocking_quality_question_ids(structure):
                # Do not spend a second model request allocating scores for a
                # structure that is already known to be invalid.  The exact
                # evidence issues are persisted below and become the repair
                # contract for the next targeted request.
                payload = structure
            else:
                context.raise_if_cancelled()
                client = llm_client_factory()
                payload = allocate_grading_config_scores(
                    structure,
                    confirmed_blocks,
                    document_text,
                    llm_client=client,
                    model_name=_config_model(client),
                    report=report,
                    q_images=question_images or None,
                    checkpoint=checkpoint,
                )
    elif mode == "regenerate_questions":
        client = llm_client_factory()
        raw_regenerate_ids = inputs.get("regenerate_question_ids")
        if not isinstance(raw_regenerate_ids, list):
            raise ValueError("targeted regeneration question ids are invalid")
        regenerate_ids = [
            str(question_id).strip()
            for question_id in raw_regenerate_ids
            if str(question_id).strip()
        ]
        payload = regenerate_grading_config_questions(
            existing_payload or {},
            confirmed_blocks,
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
            regenerate_question_ids=regenerate_ids,
            checkpoint=checkpoint,
        )
    elif existing_payload is not None:
        client = llm_client_factory()
        raw_retry_ids = context.payload.get("retry_question_ids")
        retry_ids = (
            [str(qid).strip() for qid in raw_retry_ids if str(qid).strip()]
            if isinstance(raw_retry_ids, list)
            else None
        )
        payload = retry_failed_grading_config_questions(
            existing_payload,
            confirmed_blocks,
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
            retry_question_ids=retry_ids,
            checkpoint=checkpoint,
        )
    elif generation_mode == "batched":
        client = llm_client_factory()
        payload = generate_grading_config_from_confirmed_blocks(
            confirmed_blocks,
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
            checkpoint=checkpoint,
        )
    elif source_suffix == ".docx":
        client = llm_client_factory()
        payload = generate_grading_config_from_text(
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            question_blocks=(
                list(source_record.private_blocks)
                if source_record is not None
                else confirmed_blocks
            ),
        )
    else:
        client = llm_client_factory()
        payload = generate_grading_config_from_images(
            whole_page_images,
            "",
            llm_client=client,
            model_name=_config_model(client),
            report=report,
        )
    normalize_new_generated_config_payload(payload)
    refresh_generated_config_quality_warnings(payload)
    _refresh_quality_repair_state(
        payload,
        previous_payload=existing_payload,
        retried_refs=local_quality_retry_refs,
    )
    quality_blocked_ids = blocking_quality_question_ids(payload)
    if quality_blocked_ids and not failed_grading_config_question_ids(payload):
        meta = payload.setdefault("meta", {})
        quality_warnings = [
            str(item)
            for item in meta.get("warnings") or []
            if str(item).startswith("[质量检查-阻断]")
        ]
        failure = {
            "batch_id": (
                "整卷生成"
                if generation_mode == "whole_document"
                else "本地校验"
            ),
            "question_ids": quality_blocked_ids,
            "status": "failed",
            "category": "local_validation",
            "error": (
                "；".join(quality_warnings[:3])
                or "评分标准未通过本地业务校验"
            )[:600],
        }
        meta["failed_question_ids"] = quality_blocked_ids
        meta["failed_batches"] = [failure]
        if generation_mode == "whole_document":
            meta["batches"] = [failure]
    context.raise_if_cancelled()
    failed_ids = failed_grading_config_question_ids(payload)
    uncertain_ids = _deferred_uncertain_question_ids(payload)
    score_allocation = _score_allocation_summary(payload)
    total_questions = _question_count(payload, confirmed_blocks)
    summary = _summary(
        session_id,
        total_questions,
        failed_ids,
        uncertain_ids=uncertain_ids,
        total_batch_count=_batch_count(payload),
        failed_batches=failed_grading_config_batches(payload),
        local_json_repairs=_local_json_repairs(payload),
        local_structure_repairs=_local_structure_repairs(payload),
        **score_allocation,
        retryable_mode=generation_mode == "batched",
    )
    taxonomy_review_ids = _taxonomy_review_question_ids(payload)
    summary["taxonomy_review_count"] = len(taxonomy_review_ids)
    summary["taxonomy_review_question_ids"] = taxonomy_review_ids
    summary["question_bank_sync_requested"] = sync_to_question_bank
    summary["question_bank_sync_state"] = (
        "waiting_for_config" if sync_to_question_bank else "not_requested"
    )
    repair_meta = payload.get("meta") if isinstance(payload, dict) else None
    stagnated_ids = (
        list(repair_meta.get("quality_repair_stagnated_question_ids") or [])
        if isinstance(repair_meta, dict)
        else []
    )
    if stagnated_ids:
        summary["quality_repair_stagnated_question_ids"] = stagnated_ids
        summary["retryable"] = False
    if (
        failed_ids
        or uncertain_ids
        or bool(score_allocation["score_allocation_pending"])
    ):
        if (
            sync_to_question_bank
            and analysis_artifact is not None
            and source_record is not None
            and question_bank_db_path is not None
            and tagging_ai_service_factory is not None
            and taxonomy_governance is not None
        ):
            resolved_data_root = (
                Path(data_root)
                if data_root is not None
                else _infer_data_root(Path(db.db_path))
            )
            intake_runner = (
                question_bank_intake_runner
                or run_deferred_question_bank_intake
            )
            try:
                intake_result = intake_runner(
                    context=context,
                    session_id=session_id,
                    artifact=analysis_artifact,
                    source_filename=source_record.safe_filename,
                    source_content=source_record.private_source_bytes,
                    question_bank_db_path=Path(question_bank_db_path),
                    data_root=resolved_data_root,
                    ai_service_factory=tagging_ai_service_factory,
                    taxonomy_governance=taxonomy_governance,
                )
            except JobCancellationRequested:
                raise
            except Exception:
                summary["question_bank_sync_state"] = "intake_failed"
                summary["question_bank_sync_error"] = (
                    "题目分析结果已保留，但试卷暂时没有写入题库；"
                    "评分依据草稿已保留，重试时会按原来源继续收敛。"
                )
            else:
                imported_count = max(
                    0,
                    int(intake_result.get("imported_count") or 0),
                )
                tagged_count = max(
                    0,
                    int(intake_result.get("tagged_count") or 0),
                )
                evidence_count = max(
                    0,
                    int(intake_result.get("evidence_count") or 0),
                )
                summary.update(
                    {
                        "question_bank_sync_state": (
                            "ready_for_config_link"
                            if str(intake_result.get("outcome") or "")
                            == "complete"
                            else "partial"
                        ),
                        "question_bank_imported_count": imported_count,
                        "question_bank_tagged_count": tagged_count,
                        "question_bank_evidence_count": evidence_count,
                        "question_bank_failed_count": max(
                            0,
                            int(intake_result.get("failed_count") or 0),
                        ),
                        "question_bank_config_link_pending": True,
                    }
                )
        with session_config_lock(Path(upload_config_dir), session_id):
            current_session = db.get_grading_session(session_id)
            if (
                current_session is None
                or bool(int(current_session.get("is_deleted") or 0))
                or str(current_session.get("rubric_path") or "") != expected_rubric_path
                or str(current_session.get("answer_key_path") or "")
                != expected_answer_key_path
            ):
                raise ValueError("session config changed while generation was running")
            if source_service is not None:
                source_service.load_for_generation(
                    session_id=session_id,
                    source_id=source_id,
                    source_revision=source_revision,
                )
            context.raise_if_cancelled()
            draft_path = _draft_path(upload_config_dir, context.job_id)
            _write_json_atomic(draft_path, payload)
        return summary

    publication: PublishedConfig | None = None
    archived: ArchivedSourcePaper | None = None
    resolved_data_root = (
        Path(data_root)
        if data_root is not None
        else _infer_data_root(Path(db.db_path))
    )
    resolved_mapping_output_dir = (
        Path(mapping_output_dir)
        if mapping_output_dir is not None
        else resolved_data_root / "templates"
    )
    _set_mapping_result(summary, "reconfirm_required")
    with session_config_lock(Path(upload_config_dir), session_id):
        current_session = db.get_grading_session(session_id)
        if (
            current_session is None
            or bool(int(current_session.get("is_deleted") or 0))
            or str(current_session.get("rubric_path") or "")
            != expected_rubric_path
            or str(current_session.get("answer_key_path") or "")
            != expected_answer_key_path
        ):
            raise ValueError("session config changed while generation was running")
        final_source: ConfigSourceRecord | None = None
        if source_service is not None:
            final_source = source_service.load_for_generation(
                session_id=session_id,
                source_id=source_id,
                source_revision=source_revision,
            )
        context.raise_if_cancelled()
        archive_guard = (
            source_archive_sha_lock(
                content=final_source.private_source_bytes,
                data_root=resolved_data_root,
            )
            if final_source is not None
            else nullcontext()
        )
        with archive_guard:
            try:
                if final_source is not None:
                    archived = archive_source_bytes(
                        filename=final_source.safe_filename,
                        content=final_source.private_source_bytes,
                        data_root=resolved_data_root,
                    )
                context.raise_if_cancelled()
                publication = publish_generated_config(
                    Path(upload_config_dir),
                    payload,
                    job_id=context.job_id,
                )
                context.raise_if_cancelled()
                context.report(0.98, "config_generation", "binding")
                context.raise_if_cancelled()
                bound = context.store.finish_config_generation_and_bind(
                    context.job_id,
                    session_id=session_id,
                    expected_rubric_path=expected_rubric_path,
                    expected_answer_key_path=expected_answer_key_path,
                    rubric_path=str(publication.rubric_path),
                    answer_key_path=str(publication.answer_key_path),
                    source_paper_path=(archived.stored_path if archived else None),
                    source_paper_sha256=(archived.sha256 if archived else None),
                    result=summary,
                )
                if not bound:
                    context.raise_if_cancelled()
                    raise ValueError(
                        "session config changed while generation was running"
                    )
                if sync_to_question_bank:
                    _submit_automatic_question_bank_sync(
                        context=context,
                        db=db,
                        session_id=session_id,
                        source_paper_sha256=(
                            archived.sha256 if archived is not None else ""
                        ),
                        source_safe_filename=(
                            final_source.safe_filename
                            if final_source is not None
                            else ""
                        ),
                        curriculum_volume_id=curriculum_volume_id,
                        analysis_artifact_id=(
                            str(inputs.get("analysis_artifact_id") or "")
                            if evidence_flow
                            else ""
                        ),
                        analysis_artifact_hash=evidence_artifact_hash,
                        analysis_source_id=source_id if evidence_flow else "",
                        analysis_source_revision=(
                            source_revision if evidence_flow else ""
                        ),
                        summary=summary,
                    )
                _refresh_mapping_and_finalize_job(
                    context=context,
                    db=db,
                    session_id=session_id,
                    mapping_output_dir=resolved_mapping_output_dir,
                    summary=summary,
                )
            except BaseException:
                if publication is not None:
                    created = {str(path): path for path in publication.created_paths}
                    referenced = context.store.referenced_config_paths(set(created))
                    remove_published_config(
                        Path(upload_config_dir),
                        tuple(
                            path
                            for value, path in created.items()
                            if value not in referenced
                        ),
                    )
                if archived is not None and not archived.reused:
                    referenced_archives = context.store.referenced_source_paper_paths(
                        {archived.stored_path}
                    )
                    if archived.stored_path not in referenced_archives:
                        try:
                            archived.physical_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                raise
    return summary


def _run_refine_config_job(
    *,
    context: JobContext,
    db: GradingRepositoryAccess,
    upload_config_dir: Path,
    llm_client_factory: Callable[[], Any],
    inputs: dict[str, Any],
    session_id: int,
    expected_rubric_path: str,
    expected_answer_key_path: str,
    mapping_output_dir: Path | None,
) -> dict[str, object]:
    expected_revision = str(inputs.get("expected_revision") or "")
    current = load_editor_config(db, session_id)
    if current.revision != expected_revision:
        raise ValueError("session config changed before refinement started")
    existing_payload = inputs.get("existing_payload")
    commands = inputs.get("commands")
    if not isinstance(existing_payload, dict) or not isinstance(commands, list):
        raise ValueError("refine input is invalid")
    candidate = apply_config_editor_changes(
        existing_payload,
        edits=(),
        commands=_decode_refine_commands(commands),
    )
    expected_ids = editor_part_ids(candidate)
    expected_identity = editor_identity_signature(candidate)
    context.raise_if_cancelled()
    context.report(0.25, "config_generation", "refining")
    client = llm_client_factory()
    payload = refine_grading_config_from_manual_structure(
        candidate,
        llm_client=client,
        model_name=_config_model(client),
    )
    # Teacher-created scoring-unit identities are stable references used by
    # the editor and answer-region mapping.  Refine repairs the schema and
    # removes forbidden knowledge metadata, but must not canonicalise part IDs
    # as if this were a newly generated rubric.
    strip_generated_config_knowledge_fields(payload)
    normalize_generated_config_schema(payload)
    strip_generated_config_knowledge_fields(payload)
    context.raise_if_cancelled()
    if editor_identity_signature(payload) != expected_identity:
        raise ValueError("refined config changed teacher scoring-unit identities")
    summary: dict[str, object] = {
        "session_id": session_id,
        "outcome": "complete",
        "total_questions": len(expected_ids),
        "generated_questions": len(expected_ids),
        "failed_count": 0,
        "failed_question_ids": [],
        "retryable": False,
    }
    publication: PublishedConfig | None = None
    resolved_mapping_output_dir = (
        Path(mapping_output_dir)
        if mapping_output_dir is not None
        else _infer_data_root(Path(db.db_path)) / "templates"
    )
    _set_mapping_result(summary, "reconfirm_required")
    with session_config_lock(upload_config_dir, session_id):
        latest = load_editor_config(db, session_id)
        if latest.revision != expected_revision:
            raise ValueError("session config changed while refinement was running")
        try:
            context.raise_if_cancelled()
            publication = publish_generated_config(
                upload_config_dir, payload, job_id=context.job_id
            )
            bound = context.store.finish_config_generation_and_bind(
                context.job_id,
                session_id=session_id,
                expected_rubric_path=expected_rubric_path,
                expected_answer_key_path=expected_answer_key_path,
                rubric_path=str(publication.rubric_path),
                answer_key_path=str(publication.answer_key_path),
                result=summary,
            )
            if not bound:
                context.raise_if_cancelled()
                raise ValueError("session config changed while refinement was running")
            _refresh_mapping_and_finalize_job(
                context=context,
                db=db,
                session_id=session_id,
                mapping_output_dir=resolved_mapping_output_dir,
                summary=summary,
            )
        except BaseException:
            if publication is not None:
                referenced = context.store.referenced_config_paths(
                    {str(path) for path in publication.created_paths}
                )
                remove_published_config(
                    upload_config_dir,
                    tuple(path for path in publication.created_paths if str(path) not in referenced),
                )
            raise
    return summary


def _set_mapping_result(
    summary: dict[str, object],
    status: Literal["not_present", "refreshed", "reconfirm_required"],
) -> None:
    result = refresh_mapping_after_config_save(lambda: status)
    summary["mapping_status"] = result.mapping_status
    summary["mapping_message"] = result.mapping_message


def _submit_automatic_question_bank_sync(
    *,
    context: JobContext,
    db: GradingRepositoryAccess,
    session_id: int,
    source_paper_sha256: str,
    summary: dict[str, object],
    source_safe_filename: str = "",
    curriculum_volume_id: str = "",
    analysis_artifact_id: str = "",
    analysis_artifact_hash: str = "",
    analysis_source_id: str = "",
    analysis_source_revision: str = "",
) -> None:
    source_sha = str(source_paper_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        summary["question_bank_sync_state"] = "blocked"
        summary["question_bank_sync_error"] = (
            "评分依据已发布，但来源试卷尚未形成可核对指纹；"
            "题库任务没有启动，可在评分编辑页重新提交。"
        )
        return
    try:
        loaded = load_editor_config(db, session_id)
        if not loaded.configured:
            raise ValueError("published grading config is unavailable")
        volume = curriculum_volume(volume_id=curriculum_volume_id)
        if volume is None and not str(curriculum_volume_id or "").strip():
            volume = infer_curriculum_volume_from_text(
                " ".join(
                    item
                    for item in (
                        str(loaded.session.get("name") or ""),
                        str(source_safe_filename or ""),
                    )
                    if item
                )
            )
        if volume is None:
            summary["question_bank_sync_state"] = "awaiting_metadata"
            summary["question_bank_sync_error"] = (
                "评分依据已发布；入库前需要老师确认年级和上下册，"
                "确认前不会调用标签模型。"
            )
            return
        identity = {
            "session_id": int(session_id),
            "mode": "sync",
            "config_revision": str(loaded.revision),
            "source_paper_sha256": source_sha,
            "curriculum_volume_id": str(volume["id"]),
        }
        deferred_identity = (
            str(analysis_artifact_id or "").strip().casefold(),
            str(analysis_artifact_hash or "").strip().casefold(),
            str(analysis_source_id or "").strip().casefold(),
            str(analysis_source_revision or "").strip().casefold(),
        )
        if any(deferred_identity):
            if not (
                _INPUT_ID.fullmatch(deferred_identity[0])
                and re.fullmatch(r"[0-9a-f]{64}", deferred_identity[1])
                and _INPUT_ID.fullmatch(deferred_identity[2])
                and re.fullmatch(r"[0-9a-f]{64}", deferred_identity[3])
            ):
                raise ValueError("deferred question analysis hand-off is invalid")
            identity.update(
                {
                    "analysis_artifact_id": deferred_identity[0],
                    "analysis_artifact_hash": deferred_identity[1],
                    "analysis_source_id": deferred_identity[2],
                    "analysis_source_revision": deferred_identity[3],
                }
            )
        clean_filename = Path(str(source_safe_filename or "")).name
        if clean_filename:
            identity["source_safe_filename"] = clean_filename
        fingerprint = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        token = hashlib.sha256(
            (
                f"config-generation:{context.job_id}:"
                f"{loaded.revision}:{source_sha}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        job, _created = context.submit_question_bank_sync(
            {
                **identity,
                "client_request_token": token,
                "client_request_fingerprint": fingerprint,
            }
        )
    except Exception:
        summary["question_bank_sync_state"] = "submission_failed"
        summary["question_bank_sync_error"] = (
            "评分依据已发布，但题库任务没有成功进入队列；"
            "评分依据不受影响，可在评分编辑页重新提交题库任务。"
        )
        return
    summary["question_bank_sync_state"] = (
        "queued" if job.status == "queued" else str(job.status)
    )
    summary["question_bank_sync_job_id"] = int(job.id)
    summary["config_revision"] = str(loaded.revision)
    summary["source_paper_sha256"] = source_sha


def _config_analysis_sources(
    confirmed_blocks: list[dict[str, Any]],
    question_images: dict[str, Any],
    *,
    curriculum_volume_id: str,
    tagging_service: Any,
    volume: dict[str, Any],
) -> tuple[ConfigQuestionAnalysisSource, ...]:
    provisional: list[ConfigQuestionAnalysisSource] = []
    normalized_blocks: list[dict[str, Any]] = []
    for index, raw_block in enumerate(confirmed_blocks, start=1):
        if not isinstance(raw_block, dict):
            raise ValueError("confirmed question block is invalid")
        source_ref = str(raw_block.get("question_id") or "").strip()
        if not source_ref:
            raise ValueError("confirmed question id is missing")
        block = dict(raw_block)
        block.setdefault("grade", str(volume.get("grade") or ""))
        block.setdefault("semester", str(volume.get("semester") or ""))
        block.setdefault(
            "textbook_version",
            str(volume.get("textbook_version") or ""),
        )
        images = _config_analysis_images(question_images.get(source_ref))
        if not any(
            str(block.get(key) or "").strip()
            for key in ("question_text", "text", "content", "stem")
        ) and any(image.role == "question" for image in images):
            block["question_text"] = "题目内容见随附图像"
        question = question_analysis_input_from_config_source(
            block,
            question_id=index,
            curriculum_volume_id=curriculum_volume_id,
            images=images,
        )
        normalized_blocks.append(block)
        provisional.append(ConfigQuestionAnalysisSource(source_ref, question))
    if len({item.source_question_ref for item in provisional}) != len(provisional):
        raise ValueError("confirmed question ids are duplicated")
    contracts = tagging_service.taxonomy_contracts(
        {item.question.question_id: item.question.tagging_context for item in provisional}
    )
    resolved: list[ConfigQuestionAnalysisSource] = []
    for item, block in zip(provisional, normalized_blocks, strict=True):
        contract = contracts.get(item.question.question_id)
        if not isinstance(contract, dict):
            raise ValueError("question taxonomy shortlist is unavailable")
        question = question_analysis_input_from_config_source(
            block,
            question_id=item.question.question_id,
            curriculum_volume_id=curriculum_volume_id,
            taxonomy_contract=contract,
            images=item.question.images,
        )
        resolved.append(
            ConfigQuestionAnalysisSource(item.source_question_ref, question)
        )
    return tuple(resolved)


def _config_analysis_images(value: Any) -> tuple[QuestionAnalysisImage, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError("question image bundle is invalid")
    result: list[QuestionAnalysisImage] = []
    for role in ("question", "answer"):
        raw_encoded = value.get(role)
        if raw_encoded is None:
            continue
        encoded_values = (
            [raw_encoded]
            if isinstance(raw_encoded, str)
            else raw_encoded
            if isinstance(raw_encoded, list)
            else None
        )
        if (
            not isinstance(encoded_values, list)
            or not encoded_values
            or len(encoded_values) > 32
            or any(not isinstance(item, str) or not item for item in encoded_values)
        ):
            raise ValueError("question image body is invalid")
        for encoded in encoded_values:
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("question image body is invalid") from None
            result.append(
                QuestionAnalysisImage(
                    role=role,  # type: ignore[arg-type]
                    mime_type=_image_mime_type(content),
                    content=content,
                )
            )
    return tuple(result)


def _image_mime_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("question image type is unsupported")


def _deferred_analysis_draft(
    bundle: DeferredCombinedAnalysisBundle,
    *,
    exam_title: str,
) -> dict[str, Any]:
    failed_refs = list(bundle.failed_source_refs)
    uncertain_refs = list(bundle.uncertain_source_refs)
    taxonomy_review_refs = list(bundle.taxonomy_review_source_refs)
    internal_failure_by_ref: dict[str, Any] = {}
    for failure in bundle.failures:
        internal_failure_by_ref.setdefault(
            failure.source_question_ref,
            failure,
        )
    for reference in failed_refs:
        internal_failure_by_ref.setdefault(reference, None)
    grouped_refs: dict[tuple[str, str], list[str]] = {}
    for reference in failed_refs:
        failure = internal_failure_by_ref[reference]
        category = (
            str(failure.category)
            if failure is not None
            else "analysis_incomplete"
        )
        detail = (
            str(failure.validation_error or "")
            if failure is not None
            else ""
        )
        grouped_refs.setdefault((category, detail), []).append(reference)
    category_copy = {
        "combined_response_contract": (
            "model_response_parse",
            "模型已返回，但题号或结果列表不完整。",
        ),
        "combined_item_contract": (
            "model_output_contract",
            "模型已返回，但题目分析对象缺少必要字段。",
        ),
        "tag_contract": (
            "model_output_contract",
            "模型已返回，但标签字段不符合约定。",
        ),
        "tag_normalization": (
            "model_output_contract",
            "模型已返回，但标签内容无法安全规范化。",
        ),
        "solution_evidence_contract": (
            "model_output_contract",
            "模型已返回，但拆分点字段或标识不符合约定。",
        ),
        "evidence_granularity_insufficient": (
            "evidence_granularity_insufficient",
            "模型已返回，但把多个可独立给分的推导步骤合并成了一个评分点；重试会重新分析本题。",
        ),
        "solution_evidence_terms": (
            "model_output_contract",
            "模型已返回，但拆分点引用了本题候选范围外的知识词。",
        ),
        "solution_evidence_candidates": (
            "model_output_contract",
            "模型已返回，但拆分点引用的知识候选无法建立安全快照。",
        ),
        "taxonomy_retrieval_insufficient": (
            "local_validation",
            "本题没有召回到可用知识候选，已在调用模型前停止，未产生本题模型费用。",
        ),
        "evidence_validation": (
            "model_output_contract",
            "拆分点未通过本地校验；旧记录未保留具体失败阶段。",
        ),
        "parse": (
            "model_response_parse",
            "模型已返回，但结果 JSON 无法解析。",
        ),
        "model": (
            "model_request",
            "模型请求或返回处理失败，未自动重试。",
        ),
        "cancelled": (
            "cancelled",
            "题目尚未发送或任务已取消，可以安全重试。",
        ),
        "analysis_incomplete": (
            "local_validation",
            "题目分析未完成且没有出站请求记录，可以安全重试。",
        ),
    }
    failed_batches = []
    for index, ((internal_category, detail), question_ids) in enumerate(
        grouped_refs.items(),
        start=1,
    ):
        public_category, error = category_copy.get(
            internal_category,
            (
                "model_output_contract",
                "模型已返回，但拆分点未通过本地安全校验。",
            ),
        )
        failed_batches.append(
            {
                "batch_id": f"解题证据分析-{index}",
                "question_ids": question_ids,
                "status": "failed",
                "category": public_category,
                "error": (
                    f"{error} 具体位置：{detail}"
                    if detail
                    else error
                )[:600],
            }
        )
    warnings: list[str] = []
    if failed_refs:
        warnings.append("部分题目的拆分点分析失败")
    if uncertain_refs:
        warnings.append("部分模型请求结果未知，需要教师决定是否重新发起新分析")
    return {
        "rubric": {
            "exam_title": str(exam_title or "待命名试卷"),
            "total_score": 0,
            "questions": [],
        },
        "answer_key": {"questions": []},
        "meta": {
            "warnings": warnings,
            "generation_mode": "solution_evidence_structure",
            "structure_source": "solution_evidence",
            "structure_generation_model_requests": len(
                {item.request_id for item in bundle.requests}
            ),
            "failed_question_ids": failed_refs,
            "failed_batches": failed_batches,
            "analysis_total_questions": len(bundle.source_fingerprints),
            "uncertain_question_ids": uncertain_refs,
            "needs_teacher_resolution": bool(uncertain_refs),
            "taxonomy_review_question_ids": taxonomy_review_refs,
            "taxonomy_review_count": len(taxonomy_review_refs),
            "score_allocation_pending": False,
        },
    }


def _retry_source_refs(payload: dict[str, Any]) -> list[str] | None:
    raw = payload.get("retry_question_ids")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("retry question ids are invalid")
    values = [str(item or "").strip() for item in raw]
    if not values or any(not item for item in values):
        raise ValueError("retry question ids are invalid")
    return values


def _local_quality_retry_source_refs(
    payload: dict[str, Any] | None,
    requested_refs: list[str] | None,
) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    failed_refs = failed_grading_config_question_ids(payload)
    if not failed_refs:
        return ()
    selected = list(failed_refs if requested_refs is None else requested_refs)
    meta = payload.get("meta")
    failed_batches = meta.get("failed_batches") if isinstance(meta, dict) else None
    if not isinstance(failed_batches, list):
        return ()
    local_refs = {
        str(question_id).strip()
        for batch in failed_batches
        if isinstance(batch, dict)
        and str(batch.get("category") or "") == "local_validation"
        for question_id in batch.get("question_ids") or []
        if str(question_id).strip()
    }
    if not selected or not set(selected).issubset(local_refs):
        return ()
    repair_state = meta.get("quality_repair_state")
    repair_state = repair_state if isinstance(repair_state, dict) else {}
    blocked_refs = []
    for reference in selected:
        state = repair_state.get(reference)
        if not isinstance(state, dict):
            continue
        attempts = int(state.get("attempts") or 0)
        if bool(state.get("stagnated")) or attempts >= 2:
            blocked_refs.append(reference)
    if blocked_refs:
        raise ValueError(
            "以下题目的定向修复没有取得进展，系统已停止继续调用模型以避免重复费用："
            + "、".join(blocked_refs)
            + "。请先人工调整评分标准，再继续。"
        )
    return tuple(dict.fromkeys(selected))


def _quality_issues_by_ref(
    payload: dict[str, Any] | None,
    source_refs: tuple[str, ...],
) -> dict[str, tuple[dict[str, str], ...]]:
    if not isinstance(payload, dict):
        raise ValueError("本地质量问题记录已丢失，未继续调用模型。")
    meta = payload.get("meta")
    raw_issues = meta.get("quality_issues") if isinstance(meta, dict) else None
    if not isinstance(raw_issues, list):
        raise ValueError("本地质量问题记录已丢失，未继续调用模型。")
    selected = set(source_refs)
    grouped: dict[str, list[dict[str, str]]] = {
        reference: [] for reference in source_refs
    }
    allowed_fields = ("question_id", "code", "path", "expected", "actual", "message")
    for raw_issue in raw_issues:
        if not isinstance(raw_issue, dict):
            continue
        question_id = str(raw_issue.get("question_id") or "").strip()
        if question_id not in selected:
            continue
        grouped[question_id].append(
            {
                field: str(raw_issue.get(field) or "")[:600]
                for field in allowed_fields
            }
        )
    missing = [reference for reference, issues in grouped.items() if not issues]
    if missing:
        raise ValueError(
            "以下题目的具体质量问题记录已丢失，未继续调用模型："
            + "、".join(missing)
        )
    return {
        reference: tuple(issues)
        for reference, issues in grouped.items()
    }


def _quality_repair_attempts_by_ref(
    payload: dict[str, Any] | None,
    source_refs: tuple[str, ...],
) -> dict[str, int]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    raw_state = meta.get("quality_repair_state") if isinstance(meta, dict) else None
    state = raw_state if isinstance(raw_state, dict) else {}
    result: dict[str, int] = {}
    for reference in source_refs:
        item = state.get(reference)
        previous_attempts = (
            int(item.get("attempts") or 0)
            if isinstance(item, dict)
            else 0
        )
        result[reference] = previous_attempts + 1
    return result


def _quality_issue_signature(issues: list[dict[str, str]]) -> str:
    stable = [
        {
            key: str(issue.get(key) or "")
            for key in ("code", "path", "expected", "actual")
        }
        for issue in issues
    ]
    stable.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _refresh_quality_repair_state(
    payload: dict[str, Any],
    *,
    previous_payload: dict[str, Any] | None,
    retried_refs: tuple[str, ...],
) -> None:
    meta = payload.setdefault("meta", {})
    if not isinstance(meta, dict):
        return
    previous_meta = (
        previous_payload.get("meta")
        if isinstance(previous_payload, dict)
        else None
    )
    previous_state_raw = (
        previous_meta.get("quality_repair_state")
        if isinstance(previous_meta, dict)
        else None
    )
    previous_state = (
        previous_state_raw if isinstance(previous_state_raw, dict) else {}
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    for issue in collect_generated_config_quality_issues(payload):
        question_id = str(issue.get("question_id") or "").strip()
        if question_id:
            grouped.setdefault(question_id, []).append(issue)
    retried = set(retried_refs)
    next_state: dict[str, dict[str, Any]] = {}
    for question_id, issues in grouped.items():
        signature = _quality_issue_signature(issues)
        old = previous_state.get(question_id)
        old = old if isinstance(old, dict) else {}
        previous_attempts = int(old.get("attempts") or 0)
        was_retried = question_id in retried
        attempts = previous_attempts + 1 if was_retried else previous_attempts
        stagnated = bool(
            was_retried
            and old.get("issue_signature")
            and str(old.get("issue_signature")) == signature
        )
        next_state[question_id] = {
            "attempts": attempts,
            "issue_signature": signature,
            "stagnated": stagnated,
        }
    meta["quality_repair_state"] = next_state
    meta["quality_repair_stagnated_question_ids"] = [
        question_id
        for question_id, state in next_state.items()
        if bool(state.get("stagnated"))
    ]


def _refresh_mapping_and_finalize_job(
    *,
    context: JobContext,
    db: GradingRepositoryAccess,
    session_id: int,
    mapping_output_dir: Path,
    summary: dict[str, object],
) -> None:
    escaped: BaseException | None = None
    try:
        result = refresh_mapping_after_config_save(
            lambda: refresh_template_mapping_from_session(
                db,
                session_id,
                output_root=mapping_output_dir,
            )
        )
        summary["mapping_status"] = result.mapping_status
        summary["mapping_message"] = result.mapping_message
    except BaseException as exc:
        _set_mapping_result(summary, "reconfirm_required")
        escaped = exc
    try:
        finalized = context.store.finalize_bound_config_generation(
            context.job_id,
            summary,
        )
    except BaseException:
        # The generic JobManager success path is the fallback.  The provisional
        # committed result remains recoverable after a process restart.
        return
    if not finalized:
        return
    if escaped is not None:
        raise escaped


def _decode_refine_commands(values: list[Any]) -> tuple[Any, ...]:
    result: list[Any] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("refine command is invalid")
        kind = str(value.get("kind") or "")
        if kind == "split":
            result.append(
                SplitScoringUnitCommand(
                    kind="split",
                    question_id=str(value.get("question_id") or ""),
                    count=int(value.get("count") or 0),
                    style=str(value.get("style") or ""),
                )
            )
        elif kind == "replace_parts":
            parts = value.get("parts")
            if not isinstance(parts, list):
                raise ValueError("refine parts are invalid")
            result.append(
                ReplaceScoringUnitsCommand(
                    kind="replace_parts",
                    question_id=str(value.get("question_id") or ""),
                    parts=tuple(
                        ManualPartInput(
                            part_id=str(part.get("part_id") or ""),
                            score=float(part.get("score") or 0),
                            core_goal=str(part.get("core_goal") or ""),
                        )
                        for part in parts
                        if isinstance(part, dict)
                    ),
                )
            )
        else:
            raise ValueError("refine command is unsupported")
    return tuple(result)


def _required_int(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _config_model(client: Any) -> str | None:
    settings = getattr(client, "settings", None)
    value = getattr(settings, "config_model", None)
    return str(value) if value else None


def _draft_path(upload_config_dir: Path, job_id: int) -> Path:
    return Path(upload_config_dir) / f"config_generation_draft_job_{int(job_id)}.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(
        SecureRootFilesystem(path.parent).read_text(
            path,
            encoding="utf-8",
            max_bytes=MAX_CONFIG_GENERATION_INPUT_BYTES,
        )
    )
    if not isinstance(payload, dict):
        raise ValueError("config generation draft must contain a JSON object")
    return payload


def _question_count(
    payload: dict[str, Any],
    confirmed_blocks: list[dict[str, Any]],
) -> int:
    confirmed_ids = {
        str(block.get("question_id") or "").strip()
        for block in confirmed_blocks
        if isinstance(block, dict) and str(block.get("question_id") or "").strip()
    }
    if confirmed_ids:
        return len(confirmed_ids)
    batch_ids = _batch_question_ids(payload)
    if batch_ids:
        return len(batch_ids)
    rubric = payload.get("rubric")
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    return len(questions) if isinstance(questions, list) else len(confirmed_blocks)


def _batch_question_ids(payload: dict[str, Any]) -> set[str]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    batches = meta.get("batches") if isinstance(meta, dict) else None
    return {
        str(qid).strip()
        for batch in batches or []
        if isinstance(batch, dict)
        for qid in batch.get("question_ids") or []
        if str(qid).strip()
    }


def _batch_count(payload: dict[str, Any]) -> int:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    batches = meta.get("batches") if isinstance(meta, dict) else None
    if not isinstance(batches, list):
        return 0
    return sum(
        1
        for batch in batches
        if isinstance(batch, dict)
        and any(str(qid).strip() for qid in batch.get("question_ids") or [])
    )


def _taxonomy_review_question_ids(payload: dict[str, Any]) -> list[str]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    raw = meta.get("taxonomy_review_question_ids") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return list(
        dict.fromkeys(
            str(item or "").strip()
            for item in raw
            if str(item or "").strip()
        )
    )


def _summary(
    session_id: int,
    total_questions: int,
    failed_ids: list[str],
    *,
    uncertain_ids: list[str] | None = None,
    total_batch_count: int = 0,
    failed_batches: list[dict[str, Any]] | None = None,
    local_json_repairs: list[dict[str, Any]] | None = None,
    local_structure_repairs: list[dict[str, Any]] | None = None,
    score_allocation_pending: bool = False,
    score_allocation_failed: bool = False,
    score_allocation_error: str = "",
    score_allocation_failure_category: str = "",
    retryable_mode: bool = True,
) -> dict[str, object]:
    failed_count = len(failed_ids)
    clean_uncertain = list(uncertain_ids or [])
    clean_batches = list(failed_batches or [])
    is_partial = bool(failed_count or clean_uncertain or score_allocation_pending)
    result: dict[str, object] = {
        "session_id": session_id,
        "outcome": "partial" if is_partial else "complete",
        "total_questions": total_questions,
        "generated_questions": max(
            0,
            total_questions - failed_count - len(clean_uncertain),
        ),
        "total_batch_count": max(0, int(total_batch_count)),
        "failed_count": failed_count,
        "failed_question_ids": failed_ids,
        "failed_batch_count": len(clean_batches),
        "failed_batches": clean_batches,
        "local_json_repairs": list(local_json_repairs or []),
        "local_structure_repairs": list(local_structure_repairs or []),
        "score_allocation_pending": bool(score_allocation_pending),
        "score_allocation_failed": bool(score_allocation_failed),
        "score_allocation_error": str(score_allocation_error or "")[:300],
        "score_allocation_failure_category": str(
            score_allocation_failure_category or ""
        )[:80],
        "retryable": bool(
            retryable_mode and (failed_count or score_allocation_pending)
        ),
        "uncertain_retry_available": bool(retryable_mode and clean_uncertain),
    }
    if clean_uncertain:
        result.update(
            {
                "uncertain_count": len(clean_uncertain),
                "uncertain_question_ids": clean_uncertain,
                "needs_teacher_resolution": True,
            }
        )
    return result


def _score_allocation_summary(payload: dict[str, Any]) -> dict[str, object]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return {
            "score_allocation_pending": False,
            "score_allocation_failed": False,
            "score_allocation_error": "",
            "score_allocation_failure_category": "",
        }
    return {
        "score_allocation_pending": bool(meta.get("score_allocation_pending")),
        "score_allocation_failed": bool(meta.get("score_allocation_failed")),
        "score_allocation_error": str(meta.get("score_allocation_error") or ""),
        "score_allocation_failure_category": str(
            meta.get("score_allocation_failure_category") or ""
        ),
    }


def _local_json_repairs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    batches = meta.get("batches") if isinstance(meta, dict) else None
    reports: list[dict[str, Any]] = []
    for batch in batches or []:
        if not isinstance(batch, dict):
            continue
        repair = batch.get("local_json_repair")
        if not isinstance(repair, dict) or not bool(repair.get("repaired")):
            continue
        reports.append(
            {
                "batch_id": str(batch.get("batch_id") or ""),
                "question_ids": [str(qid) for qid in batch.get("question_ids") or []],
                "operations": [str(item) for item in repair.get("operations") or []],
            }
        )
    return reports


def _local_structure_repairs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    batches = meta.get("batches") if isinstance(meta, dict) else None
    reports: list[dict[str, Any]] = []
    for batch in batches or []:
        if not isinstance(batch, dict):
            continue
        operations = batch.get("local_structure_repairs")
        if not isinstance(operations, list) or not operations:
            continue
        reports.append(
            {
                "batch_id": str(batch.get("batch_id") or ""),
                "question_ids": [
                    str(qid) for qid in batch.get("question_ids") or []
                ],
                "operations": [str(item)[:200] for item in operations[:100]],
            }
        )
    score_operations = (
        meta.get("score_allocation_local_structure_repairs")
        if isinstance(meta, dict)
        else None
    )
    if isinstance(score_operations, list) and score_operations:
        rubric = payload.get("rubric") if isinstance(payload, dict) else None
        questions = rubric.get("questions") if isinstance(rubric, dict) else None
        reports.append(
            {
                "batch_id": "统一配分",
                "question_ids": [
                    str(item.get("question_id") or "")
                    for item in questions or []
                    if isinstance(item, dict)
                    and str(item.get("question_id") or "")
                ],
                "operations": [
                    str(item)[:200] for item in score_operations[:100]
                ],
            }
        )
    return reports


def _summary_from_batch_draft(
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    failed_ids = failed_grading_config_question_ids(payload)
    uncertain_ids = _deferred_uncertain_question_ids(payload)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    total_questions = (
        int(meta.get("analysis_total_questions") or 0)
        if isinstance(meta, dict)
        else 0
    )
    return _summary(
        session_id,
        total_questions or _question_count(payload, []),
        failed_ids,
        uncertain_ids=uncertain_ids,
        total_batch_count=_batch_count(payload),
        failed_batches=failed_grading_config_batches(payload),
        local_json_repairs=_local_json_repairs(payload),
        local_structure_repairs=_local_structure_repairs(payload),
        **_score_allocation_summary(payload),
        retryable_mode=True,
    )


def _deferred_uncertain_question_ids(payload: dict[str, Any]) -> list[str]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    raw = meta.get("uncertain_question_ids") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return list(
        dict.fromkeys(
            str(item or "").strip()
            for item in raw
            if str(item or "").strip()
        )
    )


def _decode_whole_page_images(value: Any) -> list[bytes]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("whole_page_images must be a list")
    decoded: list[bytes] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("whole_page_images contains an invalid item")
        try:
            content = base64.b64decode(item, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("whole_page_images contains invalid base64") from None
        if not content:
            raise ValueError("whole_page_images contains an empty image")
        decoded.append(content)
    return decoded


def _infer_data_root(db_path: Path) -> Path:
    parent = Path(db_path).parent
    return parent.parent if parent.name == "databases" else parent
