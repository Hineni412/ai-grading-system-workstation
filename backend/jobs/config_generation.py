from __future__ import annotations

import base64
import binascii
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
    failed_grading_config_batches,
    failed_grading_config_question_ids,
    generate_grading_config_in_batches,
    retry_failed_grading_config_batches,
    refine_grading_config_from_manual_structure,
)
from backend.config_generation.normalization import (
    normalize_new_generated_config_payload,
)
from session_manager import (
    generate_grading_config_from_images,
    generate_grading_config_from_text,
)

from .manager import JobCancellationRequested, JobContext


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
) -> str:
    input_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "session_id": int(session_id),
        "expected_rubric_path": str(expected_rubric_path),
        "expected_answer_key_path": str(expected_answer_key_path),
        "confirmed_blocks": confirmed_blocks,
        "document_text": str(document_text or ""),
        "question_images": dict(question_images or {}),
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
) -> str:
    input_id = uuid.uuid4().hex
    _write_json_atomic(
        _input_path(upload_config_dir, input_id),
        {
            "session_id": int(session_id),
            "expected_rubric_path": str(expected_rubric_path),
            "expected_answer_key_path": str(expected_answer_key_path),
            "generation_mode": str(generation_mode),
            "source_id": str(source_id),
            "source_revision": str(source_revision),
            "decisions": list(decisions),
        },
    )
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
) -> dict[str, object]:
    session_id = _required_int(context.payload, "session_id")
    session = db.get_grading_session(session_id)
    if session is None or bool(int(session.get("is_deleted") or 0)):
        raise ValueError("grading session is unavailable")
    mode = str(context.payload.get("mode") or "").strip()
    if mode not in {"generate", "retry", "refine"}:
        raise ValueError("unsupported config generation mode")

    existing_payload: dict[str, Any] | None = None
    if mode in {"generate", "refine"}:
        input_id = str(context.payload.get("input_id") or "")
    else:
        source_job_id = _required_int(context.payload, "source_job_id")
        source_job = context.store.get_job(source_job_id)
        source_outcome = source_job.result.get("outcome") if source_job is not None else None
        retry_failed_batches = source_outcome == "partial"
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
    expected_rubric_path = str(inputs.get("expected_rubric_path") or "")
    expected_answer_key_path = str(inputs.get("expected_answer_key_path") or "")
    if (
        str(session.get("rubric_path") or "") != expected_rubric_path
        or str(session.get("answer_key_path") or "") != expected_answer_key_path
    ):
        raise ValueError("session config changed before generation started")
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
                    question_type=str(item.get("question_type") or ""),
                    excluded=bool(item.get("excluded")),
                )
            )
        prepared = source_service.prepare_generation_input(
            source_record, decisions, generation_mode
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
    client = llm_client_factory()

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

    if existing_payload is not None:
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
        payload = generate_grading_config_from_images(
            whole_page_images,
            "",
            llm_client=client,
            model_name=_config_model(client),
            report=report,
        )
    normalize_new_generated_config_payload(payload)
    context.raise_if_cancelled()
    failed_ids = failed_grading_config_question_ids(payload)
    score_allocation = _score_allocation_summary(payload)
    total_questions = _question_count(payload, confirmed_blocks)
    summary = _summary(
        session_id,
        total_questions,
        failed_ids,
        total_batch_count=_batch_count(payload),
        failed_batches=failed_grading_config_batches(payload),
        local_json_repairs=_local_json_repairs(payload),
        **score_allocation,
        retryable_mode=generation_mode == "batched",
    )
    if generation_mode == "whole_document" and (
        failed_ids or bool(score_allocation["score_allocation_pending"])
    ):
        raise ValueError(
            "whole-document generation returned an incomplete result; nothing was published"
        )
    if failed_ids or bool(score_allocation["score_allocation_pending"]):
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
    normalize_new_generated_config_payload(payload)
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


def _summary(
    session_id: int,
    total_questions: int,
    failed_ids: list[str],
    *,
    total_batch_count: int = 0,
    failed_batches: list[dict[str, Any]] | None = None,
    local_json_repairs: list[dict[str, Any]] | None = None,
    score_allocation_pending: bool = False,
    score_allocation_failed: bool = False,
    score_allocation_error: str = "",
    retryable_mode: bool = True,
) -> dict[str, object]:
    failed_count = len(failed_ids)
    clean_batches = list(failed_batches or [])
    is_partial = bool(failed_count or score_allocation_pending)
    return {
        "session_id": session_id,
        "outcome": "partial" if is_partial else "complete",
        "total_questions": total_questions,
        "generated_questions": max(0, total_questions - failed_count),
        "total_batch_count": max(0, int(total_batch_count)),
        "failed_count": failed_count,
        "failed_question_ids": failed_ids,
        "failed_batch_count": len(clean_batches),
        "failed_batches": clean_batches,
        "local_json_repairs": list(local_json_repairs or []),
        "score_allocation_pending": bool(score_allocation_pending),
        "score_allocation_failed": bool(score_allocation_failed),
        "score_allocation_error": str(score_allocation_error or "")[:300],
        "retryable": bool(is_partial and retryable_mode),
    }


def _score_allocation_summary(payload: dict[str, Any]) -> dict[str, object]:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return {
            "score_allocation_pending": False,
            "score_allocation_failed": False,
            "score_allocation_error": "",
        }
    return {
        "score_allocation_pending": bool(meta.get("score_allocation_pending")),
        "score_allocation_failed": bool(meta.get("score_allocation_failed")),
        "score_allocation_error": str(meta.get("score_allocation_error") or ""),
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


def _summary_from_batch_draft(
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, object]:
    failed_ids = failed_grading_config_question_ids(payload)
    return _summary(
        session_id,
        _question_count(payload, []),
        failed_ids,
        total_batch_count=_batch_count(payload),
        failed_batches=failed_grading_config_batches(payload),
        local_json_repairs=_local_json_repairs(payload),
        **_score_allocation_summary(payload),
        retryable_mode=True,
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
