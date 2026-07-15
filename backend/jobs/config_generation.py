from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

from db_manager import DBManager
from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.publish import (
    PublishedConfig,
    publish_generated_config,
    remove_published_config,
)
from backend.config_workspace.sources import ConfigSourceRecord, ConfigSourceService
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
)

from .manager import JobContext


_INPUT_ID = re.compile(r"^[0-9a-f]{32}$")


def _input_path(upload_config_dir: Path, input_id: str) -> Path:
    clean_id = str(input_id or "").strip().casefold()
    if not _INPUT_ID.fullmatch(clean_id):
        raise ValueError("invalid config generation input id")
    return Path(upload_config_dir) / f"config_generation_input_{clean_id}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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


def load_config_generation_input(
    upload_config_dir: Path,
    input_id: str,
) -> dict[str, Any]:
    path = _input_path(upload_config_dir, input_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("config generation input must contain a JSON object")
    return payload


def discard_config_generation_input(upload_config_dir: Path, input_id: str) -> None:
    path = _input_path(upload_config_dir, input_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def run_config_generation_job(
    *,
    context: JobContext,
    db: DBManager,
    upload_config_dir: Path,
    llm_client_factory: Callable[[], Any],
    data_root: Path | None = None,
) -> dict[str, object]:
    session_id = _required_int(context.payload, "session_id")
    session = db.get_grading_session(session_id)
    if session is None or bool(int(session.get("is_deleted") or 0)):
        raise ValueError("grading session is unavailable")
    mode = str(context.payload.get("mode") or "").strip()
    if mode not in {"generate", "retry"}:
        raise ValueError("unsupported config generation mode")

    existing_payload: dict[str, Any] | None = None
    if mode == "generate":
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
        if (
            source_record.suffix != str(inputs.get("source_suffix") or "")
            or source_record.safe_filename
            != str(inputs.get("source_safe_filename") or "")
        ):
            raise ValueError("config source metadata changed")

    confirmed_blocks = inputs.get("confirmed_blocks")
    question_images = inputs.get("question_images")
    if not isinstance(confirmed_blocks, list) or (
        generation_mode == "per_question" and not confirmed_blocks
    ):
        raise ValueError("confirmed_blocks must be a non-empty list")
    if question_images is not None and not isinstance(question_images, dict):
        raise ValueError("question_images must be an object")
    whole_page_images = _decode_whole_page_images(inputs.get("whole_page_images"))

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
            str(inputs.get("document_text") or ""),
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
            retry_question_ids=retry_ids,
        )
    elif generation_mode == "per_question":
        payload = generate_grading_config_from_confirmed_blocks(
            confirmed_blocks,
            str(inputs.get("document_text") or ""),
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
        )
    elif str(inputs.get("source_suffix") or "") == ".docx":
        payload = generate_grading_config_from_text(
            str(inputs.get("document_text") or ""),
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
    payload = json.loads(path.read_text(encoding="utf-8"))
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
