from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Literal

from db_manager import DBManager
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
from session_manager import (
    failed_grading_config_question_ids,
    generate_grading_config_from_confirmed_blocks,
    generate_grading_config_from_images,
    generate_grading_config_from_text,
    retry_failed_grading_config_questions,
    refine_grading_config_from_manual_structure,
)

from .manager import JobCancellationRequested, JobContext


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


def run_config_generation_job(
    *,
    context: JobContext,
    db: DBManager,
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
    except BaseException as exc:
        if input_id and mode != "retry":
            discard_config_generation_input(upload_config_dir, input_id)
        raise
    if input_id and result.get("outcome") != "partial":
        discard_config_generation_input(upload_config_dir, input_id)
    return result


def _run_config_generation_job_impl(
    *,
    context: JobContext,
    db: DBManager,
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
        if (
            source_job is None
            or source_job.job_type != "config_generation"
            or source_job.status != "succeeded"
            or source_job.result.get("outcome") != "partial"
            or _required_int(source_job.payload, "session_id") != session_id
            or str(source_job.payload.get("generation_mode") or "per_question")
            != "per_question"
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
        or "per_question"
    ).strip()
    if generation_mode not in {"per_question", "whole_document"}:
        raise ValueError("unsupported config generation mode")
    if mode == "retry" and generation_mode != "per_question":
        raise ValueError("whole-document config generation is not retryable")
    staged_generation_mode = inputs.get("generation_mode")
    if staged_generation_mode is not None and str(staged_generation_mode) != generation_mode:
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
        generation_mode == "per_question" and not confirmed_blocks
    ):
        raise ValueError("confirmed_blocks must be a non-empty list")
    if question_images is not None and not isinstance(question_images, dict):
        raise ValueError("question_images must be an object")

    context.raise_if_cancelled()
    context.report(0.05, "config_generation", "starting")
    client = llm_client_factory()

    def report(progress: float, stage: str, detail: str = "") -> None:
        context.report(progress, stage, detail)
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
        )
    elif generation_mode == "per_question":
        payload = generate_grading_config_from_confirmed_blocks(
            confirmed_blocks,
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
        )
    elif source_suffix == ".docx":
        payload = generate_grading_config_from_text(
            document_text,
            llm_client=client,
            model_name=_config_model(client),
            report=report,
        )
    else:
        payload = generate_grading_config_from_images(
            whole_page_images,
            "",
            llm_client=client,
            model_name=_config_model(client),
            report=report,
        )
    context.raise_if_cancelled()
    failed_ids = failed_grading_config_question_ids(payload)
    total_questions = _question_count(payload, confirmed_blocks)
    summary = _summary(
        session_id,
        total_questions,
        failed_ids,
        retryable_mode=generation_mode == "per_question",
    )
    if failed_ids:
        if generation_mode != "per_question":
            raise ValueError("whole-document generation returned an incomplete result")
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
    db: DBManager,
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
    db: DBManager,
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
    rubric = payload.get("rubric")
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    return len(questions) if isinstance(questions, list) else len(confirmed_blocks)


def _summary(
    session_id: int,
    total_questions: int,
    failed_ids: list[str],
    *,
    retryable_mode: bool = True,
) -> dict[str, object]:
    failed_count = len(failed_ids)
    return {
        "session_id": session_id,
        "outcome": "partial" if failed_count else "complete",
        "total_questions": total_questions,
        "generated_questions": max(0, total_questions - failed_count),
        "failed_count": failed_count,
        "failed_question_ids": failed_ids,
        "retryable": bool(failed_count and retryable_mode),
    }


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
