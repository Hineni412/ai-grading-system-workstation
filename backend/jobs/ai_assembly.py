"""AI 组卷细目表生成 job：单次模型调用，失败不自动重发。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.jobs.manager import JobContext
from question_bank.services.ai_assembly_service import (
    SpecGenerationRequest,
    generate_spec,
)
from question_bank.services.question_read_service import QuestionBankReadService


def run_ai_assembly_spec_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    llm_client_factory: Callable[[], Any] | None = None,
) -> dict[str, object]:
    raw_request = context.payload.get("request")
    if not isinstance(raw_request, dict):
        raise ValueError("ai assembly request is required")
    request = SpecGenerationRequest.from_payload(raw_request)
    read_service = QuestionBankReadService(
        Path(question_bank_db_path),
        data_root=data_root,
    )
    llm_client = llm_client_factory() if llm_client_factory is not None else None
    generated = generate_spec(
        request,
        read_service,
        llm_client=llm_client,
        report=context.report,
        raise_if_cancelled=context.raise_if_cancelled,
    )
    return {
        "spec": generated.spec.to_payload(),
        "model_name": generated.model_name,
    }


__all__ = ["run_ai_assembly_spec_job"]
