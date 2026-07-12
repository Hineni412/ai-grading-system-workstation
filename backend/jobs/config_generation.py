from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from db_manager import DBManager
from session_manager import (
    failed_grading_config_question_ids,
    generate_grading_config_from_confirmed_blocks,
    retry_failed_grading_config_questions,
    save_generated_config,
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
    confirmed_blocks: list[dict[str, Any]],
    document_text: str,
    question_images: dict[str, Any] | None,
) -> str:
    input_id = uuid.uuid4().hex
    _write_json_atomic(
        _input_path(upload_config_dir, input_id),
        {
            "session_id": int(session_id),
            "confirmed_blocks": confirmed_blocks,
            "document_text": str(document_text or ""),
            "question_images": dict(question_images or {}),
        },
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
        ):
            raise ValueError("config generation source job is not retryable")
        input_id = str(source_job.payload.get("input_id") or "")
        existing_payload = _read_json_object(_draft_path(upload_config_dir, source_job_id))

    inputs = load_config_generation_input(upload_config_dir, input_id)
    if _required_int(inputs, "session_id") != session_id:
        raise ValueError("config generation input does not belong to session")
    confirmed_blocks = inputs.get("confirmed_blocks")
    question_images = inputs.get("question_images")
    if not isinstance(confirmed_blocks, list) or not confirmed_blocks:
        raise ValueError("confirmed_blocks must be a non-empty list")
    if question_images is not None and not isinstance(question_images, dict):
        raise ValueError("question_images must be an object")

    context.raise_if_cancelled()
    context.report(0.05, "config_generation", "starting")
    client = llm_client_factory()

    def report(progress: float, stage: str, detail: str = "") -> None:
        context.report(progress, stage, detail)
        context.raise_if_cancelled()

    if existing_payload is None:
        payload = generate_grading_config_from_confirmed_blocks(
            confirmed_blocks,
            str(inputs.get("document_text") or ""),
            llm_client=client,
            model_name=_config_model(client),
            report=report,
            q_images=question_images or None,
        )
    else:
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
    context.raise_if_cancelled()
    failed_ids = failed_grading_config_question_ids(payload)
    total_questions = _question_count(payload, confirmed_blocks)
    summary = _summary(session_id, total_questions, failed_ids)
    if failed_ids:
        draft_path = _draft_path(upload_config_dir, context.job_id)
        _write_json_atomic(draft_path, payload)
        return summary

    rubric_path = Path(upload_config_dir) / f"rubric_job-{context.job_id}.json"
    answer_path = Path(upload_config_dir) / f"answer_key_job-{context.job_id}.json"
    try:
        rubric_path, answer_path = save_generated_config(
            Path(upload_config_dir),
            payload,
            f"job-{context.job_id}",
        )
        context.raise_if_cancelled()
        db.update_grading_session_config(
            session_id,
            rubric_path=str(rubric_path),
            answer_key_path=str(answer_path),
        )
    except Exception:
        _remove_unbound_files(rubric_path, answer_path)
        raise
    context.report(0.98, "config_generation", "complete")
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
) -> dict[str, object]:
    failed_count = len(failed_ids)
    return {
        "session_id": session_id,
        "outcome": "partial" if failed_count else "complete",
        "total_questions": total_questions,
        "generated_questions": max(0, total_questions - failed_count),
        "failed_count": failed_count,
        "failed_question_ids": failed_ids,
        "retryable": bool(failed_count),
    }


def _remove_unbound_files(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
