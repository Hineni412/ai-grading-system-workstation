"""AI 补答案 job：为选中试卷下还没有答案的题逐题生成答案草稿。

授权点是教师在试卷库点击「AI 补答案」；每个失败的题独立记录，
已有答案的题由条件 UPDATE 保证绝不被覆盖。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from question_bank.services.answer_draft_service import AnswerDraftService

from .execution_locks import keyed_execution_locks
from .manager import JobContext

LOGGER = logging.getLogger(__name__)

_PUBLIC_FAILURE_MESSAGES = {
    "model_not_configured": "请先在设置中配置内容生成模型。",
    "rate_limit": "模型请求过于频繁，请稍后再试。",
    "timeout": "生成答案超时，可稍后重试未完成题目。",
    "network": "暂时连不上模型服务，请稍后再试。",
    "service_config": "生成服务设置有误（密钥、权限或参数），请检查模型配置。",
    "parse": "模型返回无法解析，可稍后重试。",
    "validation": "这道题暂时无法生成答案。",
    "quality": "生成结果未达到保存标准。",
    "save": "答案草稿未能保存。",
    "unknown": "生成答案失败，可稍后重试。",
}


def run_answer_draft_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None = None,
) -> dict[str, object]:
    question_ids = _normalize_question_ids(context.payload.get("question_ids"))
    db_path = Path(question_bank_db_path)
    lock_keys = [
        f"answer-draft:{db_path.resolve(strict=False)}:{question_id}"
        for question_id in question_ids
    ]
    with keyed_execution_locks(
        lock_keys,
        cancel_check=context.raise_if_cancelled,
    ):
        return _run_answer_draft_locked(
            context=context,
            db_path=db_path,
            data_root=(None if data_root is None else Path(data_root)),
            question_ids=question_ids,
            llm_client_factory=llm_client_factory,
        )


def _run_answer_draft_locked(
    *,
    context: JobContext,
    db_path: Path,
    data_root: Path | None,
    question_ids: list[int],
    llm_client_factory: Callable[[], Any] | None,
) -> dict[str, object]:
    context.raise_if_cancelled()
    llm_client = llm_client_factory() if llm_client_factory is not None else None
    service = AnswerDraftService(
        db_path,
        data_root=data_root,
        llm_client=llm_client,
    )
    total = len(question_ids)
    context.report(0.05, "answer_draft", "loading")
    progressed = {"count": 0}

    def _check_and_report() -> None:
        context.raise_if_cancelled()
        progressed["count"] += 1
        context.report(
            0.05 + 0.9 * min(progressed["count"], total) / max(1, total),
            "answer_draft",
            f"{min(progressed['count'], total)}/{total}",
        )

    summary = service.draft_for_questions(
        question_ids,
        cancel_check=_check_and_report,
    )
    successful_set = {int(item) for item in summary.get("successful", [])}
    skipped_set = {int(item) for item in summary.get("skipped", [])}
    failure_categories = {
        int(item["question_id"]): str(item["category"])
        for item in summary.get("failed", [])
    }
    successful_ids = [qid for qid in question_ids if qid in successful_set]
    failed_ids = [qid for qid in question_ids if qid in failure_categories]
    skipped_ids = [qid for qid in question_ids if qid in skipped_set]
    failures = [
        _failure(question_id, failure_categories[question_id])
        for question_id in failed_ids
    ]
    if failed_ids:
        outcome = "partial" if successful_ids else "failed"
    else:
        outcome = "complete"
    context.report(1.0, "answer_draft", "done")
    return {
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "skipped_question_ids": skipped_ids,
        "failures": failures,
        "requested_count": total,
        "outcome": outcome,
        "retryable": bool(failed_ids),
    }


def _normalize_question_ids(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
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


def _failure(question_id: int, category: str) -> dict[str, object]:
    safe_category = (
        category if category in _PUBLIC_FAILURE_MESSAGES else "unknown"
    )
    return {
        "question_id": int(question_id),
        "category": safe_category,
        "message": _PUBLIC_FAILURE_MESSAGES[safe_category],
    }


__all__ = ["run_answer_draft_job"]
