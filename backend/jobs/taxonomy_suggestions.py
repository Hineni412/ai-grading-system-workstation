from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from question_bank.services.taxonomy_review_suggestions import (
    TaxonomySuggestionService,
)

from .manager import JobContext


_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "partial", "failed", "cancelled", "stale"}
)
_JOB_STAGE = "生成归并建议"
# Bounded worker pool for suggestion batches; only the model call overlaps,
# state writes stay serialized inside the service.
_SUGGESTION_BATCH_CONCURRENCY = 3


def run_taxonomy_suggestion_job(
    *,
    context: JobContext,
    suggestion_state_path: Path,
    taxonomy_governance: Any,
    question_loader: Callable[
        [Sequence[int]],
        Sequence[Mapping[str, Any]],
    ],
    ai_service_factory: Callable[[], Any],
    batch_size: int = 8,
    concurrency: int = _SUGGESTION_BATCH_CONCURRENCY,
) -> dict[str, object]:
    run_id = str(context.payload.get("run_id") or "").strip().casefold()
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run_id must contain 32 hexadecimal characters")
    operation = str(context.payload.get("operation") or "").strip()
    if operation not in {"process", "retry"}:
        raise ValueError("taxonomy suggestion operation is invalid")

    context.raise_if_cancelled()
    context.report(0.02, _JOB_STAGE, "正在准备待审词和相关题目。")
    service = TaxonomySuggestionService(
        state_path=Path(suggestion_state_path),
        governance=taxonomy_governance,
        question_loader=question_loader,
    )
    gateway = _LazySuggestionGateway(ai_service_factory)
    execute = (
        service.process_run
        if operation == "process"
        else service.retry_failed
    )
    result = execute(
        run_id,
        gateway,
        batch_size=batch_size,
        concurrency=concurrency,
        progress_callback=lambda snapshot: _report_progress(
            context,
            snapshot,
        ),
        cancel_requested=context.is_cancel_requested,
    )
    summary = _safe_summary(result, operation=operation)
    if context.is_cancel_requested():
        context.raise_if_cancelled()
    context.report(
        1.0,
        _JOB_STAGE,
        _progress_detail(summary),
    )
    return summary


class _LazySuggestionGateway:
    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._service: Any | None = None
        self._lock = threading.Lock()

    def suggest_taxonomy_reviews(
        self,
        batch: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        service = self._service
        if service is None:
            with self._lock:
                if self._service is None:
                    self._service = self._factory()
                service = self._service
        suggest = getattr(service, "suggest_taxonomy_reviews", None)
        if not callable(suggest):
            raise RuntimeError(
                "taxonomy suggestion model service is unavailable"
            )
        return suggest(batch)


def _report_progress(
    context: JobContext,
    snapshot: Mapping[str, Any],
) -> None:
    counts = _progress_counts(snapshot)
    total = counts["total_count"]
    terminal = counts["processed_count"]
    progress = 0.05 + (0.9 * terminal / max(1, total))
    context.report(
        min(0.95, progress),
        _JOB_STAGE,
        _progress_detail(counts),
    )


def _safe_summary(
    result: Mapping[str, Any],
    *,
    operation: str,
) -> dict[str, object]:
    run_id = str(result.get("run_id") or "").strip().casefold()
    status = str(result.get("status") or "").strip()
    if _RUN_ID.fullmatch(run_id) is None:
        raise RuntimeError("taxonomy suggestion result has an invalid run id")
    if status not in _TERMINAL_RUN_STATUSES:
        raise RuntimeError(
            "taxonomy suggestion run did not reach a terminal state"
        )
    counts = _progress_counts(result)
    try:
        taxonomy_revision = max(
            0,
            int(result.get("taxonomy_revision") or 0),
        )
    except (TypeError, ValueError):
        taxonomy_revision = 0
    return {
        "run_id": run_id,
        "operation": operation,
        "outcome": status,
        "status": status,
        "taxonomy_revision": taxonomy_revision,
        "progress": {
            "total": counts["total_count"],
            "processed": counts["processed_count"],
            "completed": counts["completed_count"],
            "failed": counts["failed_count"],
            "pending": counts["pending_count"],
            "cancelled": counts["cancelled_count"],
        },
        "stale": bool(result.get("stale")),
        **counts,
        "retryable": bool(result.get("retryable")),
    }


def _progress_counts(value: Mapping[str, Any]) -> dict[str, int]:
    raw = value.get("progress")
    progress = raw if isinstance(raw, Mapping) else {}

    def count(key: str) -> int:
        try:
            return max(0, int(progress.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    total = count("total")
    pending = count("pending")
    processed = (
        count("processed")
        if "processed" in progress
        else max(0, total - pending)
    )
    return {
        "total_count": total,
        "processed_count": processed,
        "completed_count": count("completed"),
        "failed_count": count("failed"),
        "pending_count": pending,
        "cancelled_count": count("cancelled"),
    }


def _progress_detail(counts: Mapping[str, object]) -> str:
    return (
        f"已处理 {int(counts.get('processed_count') or 0)}，"
        f"已生成建议 {int(counts.get('completed_count') or 0)}，"
        f"失败 {int(counts.get('failed_count') or 0)}，"
        f"待处理 {int(counts.get('pending_count') or 0)}，"
        f"已取消 {int(counts.get('cancelled_count') or 0)}。"
    )
