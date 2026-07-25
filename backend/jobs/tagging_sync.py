from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    classify_tagging_error,
    is_auto_saveable_result,
)
from question_bank.services.question_service import (
    CORE_ANALYSIS_TAG_TYPES,
    QuestionService,
)

from .execution_locks import keyed_execution_locks
from .manager import JobContext


TaggingFactory = Callable[[], AITaggingService]
_RETRYABLE_CATEGORIES = {
    "rate_limit",
    "timeout",
    "network",
    "parse",
    "quality",
    "save",
    "unknown",
}
_PUBLIC_FAILURE_MESSAGES = {
    "rate_limit": "AI service rate limited the request.",
    "timeout": "AI tagging request timed out.",
    "network": "AI tagging service was unavailable.",
    "parse": "AI tagging response could not be parsed.",
    "validation": "Question is unavailable for tagging.",
    "quality": "AI tagging result did not meet the save quality gate.",
    "save": "Complete AI tags could not be saved.",
    "unknown": "AI tagging failed.",
}


def run_tagging_sync_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: TaggingFactory,
    batch_size: int = 20,
) -> dict[str, object]:
    question_ids = _normalize_question_ids(context.payload.get("question_ids"))
    db_path = Path(question_bank_db_path)
    lock_keys = [
        f"tagging-sync:{db_path.resolve(strict=False)}:{question_id}"
        for question_id in question_ids
    ]
    with keyed_execution_locks(
        lock_keys,
        cancel_check=context.raise_if_cancelled,
    ):
        return _run_tagging_sync_job_locked(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=ai_service_factory,
            batch_size=batch_size,
            question_ids=question_ids,
        )


def _run_tagging_sync_job_locked(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: TaggingFactory,
    batch_size: int,
    question_ids: list[int],
) -> dict[str, object]:
    size = max(1, min(int(batch_size), 50))
    db_path = Path(question_bank_db_path)
    context.raise_if_cancelled()
    try:
        contexts, complete_ids, unavailable_ids = _load_tagging_candidates(
            db_path, question_ids
        )
    except Exception:
        raise RuntimeError("tagging sync setup failed") from None
    failures = [
        _failure(question_id, "validation") for question_id in unavailable_ids
    ]
    successful_ids = list(complete_ids)
    tagged_count = 0
    pending_ids = [item for item in question_ids if item in contexts]
    total_batches = max(1, (len(pending_ids) + size - 1) // size)
    try:
        ai_service = ai_service_factory() if pending_ids else None
    except Exception:
        raise RuntimeError("tagging sync setup failed") from None
    service = QuestionService(db_path)

    context.report(0.05, "tagging_sync", "loading")
    for batch_index, start in enumerate(range(0, len(pending_ids), size)):
        context.raise_if_cancelled()
        batch_ids = pending_ids[start : start + size]
        context.report(
            0.1 + (0.75 * batch_index / total_batches),
            "tagging_sync",
            f"batch {batch_index + 1}/{total_batches}",
        )
        try:
            assert ai_service is not None
            results = ai_service.analyze_questions(
                {question_id: contexts[question_id] for question_id in batch_ids},
                progress_callback=None,
                request_callback=None,
                allow_batch_fallback=False,
                quality_retry_limit=1,
                enable_review=False,
            )
        except Exception as exc:  # noqa: BLE001
            context.raise_if_cancelled()
            category = classify_tagging_error(exc)
            failures.extend(_failure(question_id, category) for question_id in batch_ids)
            continue
        context.raise_if_cancelled()
        for question_id in batch_ids:
            result = results.get(question_id)
            if result is None:
                failures.append(_failure(question_id, "validation"))
                continue
            if not is_auto_saveable_result(result):
                failures.append(_result_failure(question_id, result))
                continue
            try:
                assert result.analysis is not None
                saved = service.save_tag_analysis(
                    question_id,
                    result.analysis,
                    model_name=result.model_name,
                    confidence=result.analysis.confidence,
                )
            except Exception:  # noqa: BLE001
                saved = False
            if not saved:
                failures.append(_failure(question_id, "save"))
                continue
            tagged_count += 1
            successful_ids.append(question_id)

    failed_ids = [int(item["question_id"]) for item in failures]
    successful_ids = [item for item in question_ids if item in set(successful_ids)]
    if failures:
        outcome = "partial" if successful_ids else "failed"
    else:
        outcome = "complete"
    context.report(1.0, "tagging_sync", outcome)
    return {
        "outcome": outcome,
        "requested_count": len(question_ids),
        "skipped_complete_count": len(complete_ids),
        "tagged_count": tagged_count,
        "failed_count": len(failed_ids),
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "failures": failures,
        "retryable": any(
            str(item["category"]) in _RETRYABLE_CATEGORIES for item in failures
        ),
    }


def _normalize_question_ids(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("question_ids must be a list")
    result: list[int] = []
    seen: set[int] = set()
    for raw in value:
        try:
            question_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("question_ids must contain integers") from exc
        if question_id <= 0:
            raise ValueError("question_ids must contain positive integers")
        if question_id not in seen:
            seen.add(question_id)
            result.append(question_id)
    if not result:
        raise ValueError("question_ids must not be empty")
    if len(result) > 500:
        raise ValueError("question_ids exceeds the batch limit")
    return result


def _load_tagging_candidates(
    db_path: Path,
    question_ids: list[int],
) -> tuple[dict[int, TaggingContext], list[int], list[int]]:
    placeholders = ",".join("?" for _ in question_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT q.id, q.question_text, q.answer_text, q.question_number,
                   q.question_type, q.has_images, q.is_deleted,
                   p.grade, p.semester, p.exam_type, p.district
            FROM questions q
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE q.id IN ({placeholders})
            """,
            question_ids,
        ).fetchall()
        tag_rows = conn.execute(
            f"""
            SELECT question_id, tag_type, tag_value FROM question_tags
            WHERE question_id IN ({placeholders})
              AND COALESCE(tag_value, '') <> ''
            ORDER BY question_id ASC, id ASC
            """,
            question_ids,
        ).fetchall()
    rows_by_id = {int(row["id"]): row for row in rows}
    tag_types: dict[int, set[str]] = {}
    tag_values: dict[int, list[str]] = {}
    for row in tag_rows:
        question_id = int(row["question_id"])
        tag_types.setdefault(question_id, set()).add(str(row["tag_type"]))
        value = str(row["tag_value"] or "").strip()
        values = tag_values.setdefault(question_id, [])
        if value and value not in values:
            values.append(value)
    required = set(CORE_ANALYSIS_TAG_TYPES)
    contexts: dict[int, TaggingContext] = {}
    complete: list[int] = []
    unavailable: list[int] = []
    for question_id in question_ids:
        row = rows_by_id.get(question_id)
        if row is None or bool(row["is_deleted"]):
            unavailable.append(question_id)
        elif required.issubset(tag_types.get(question_id, set())):
            complete.append(question_id)
        else:
            contexts[question_id] = TaggingContext(
                question_text=str(row["question_text"] or ""),
                answer_text=str(row["answer_text"] or ""),
                question_number=str(row["question_number"] or ""),
                question_type=str(row["question_type"] or ""),
                grade=str(row["grade"] or ""),
                semester=str(row["semester"] or ""),
                exam_type=str(row["exam_type"] or ""),
                district=str(row["district"] or ""),
                has_images=bool(row["has_images"]),
                existing_tags=tag_values.get(question_id, []),
            )
    return contexts, complete, unavailable


def _result_failure(question_id: int, result: AITaggingResult) -> dict[str, object]:
    if not result.ok and result.error:
        return _failure(
            question_id,
            classify_tagging_error(RuntimeError(str(result.error))),
        )
    if result.quality_status != "complete":
        return _failure(question_id, "quality")
    error = RuntimeError(str(result.error or "AI tagging failed"))
    return _failure(question_id, classify_tagging_error(error))


def _failure(question_id: int, category: str) -> dict[str, object]:
    safe_category = category if category in _PUBLIC_FAILURE_MESSAGES else "unknown"
    return {
        "question_id": int(question_id),
        "category": safe_category,
        "message": _PUBLIC_FAILURE_MESSAGES[safe_category],
    }
