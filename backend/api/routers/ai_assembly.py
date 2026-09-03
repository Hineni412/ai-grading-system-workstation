"""AI 组卷路由：细目表生成（job）、确定性选题、模板结构与费用预检。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends

from analysis_report_exporter import (
    content_generation_public_info,
    estimate_prompt_tokens,
    resolve_content_generation_settings,
)
from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import (
    get_assembly_workspace_service,
    get_job_manager,
    get_question_bank_read_service,
)
from backend.api.schemas.ai_assembly import (
    AiAssemblyPreflightResponse,
    AiAssemblySelectRequest,
    AiAssemblySelectResponse,
    AiAssemblySpecJobSubmitRequest,
    AiAssemblyTemplateStructureResponse,
)
from backend.api.schemas.jobs import JobResponse
from backend.api.routers.jobs import _job_response
from backend.jobs.manager import JobManager, UnsupportedJobTypeError
from question_bank.services.ai_assembly_prompts import (
    ASSEMBLY_SYSTEM_PROMPT,
    SPEC_MAX_TOKENS,
)
from question_bank.services.ai_assembly_service import (
    AssemblySpec,
    AssemblySpecError,
    build_bank_profile,
    extract_template_structure,
    select_questions,
)
from question_bank.services.assembly_workspace_service import (
    AssemblyWorkspaceService,
)
from question_bank.services.question_frequency_service import (
    QuestionFrequencyService,
)
from question_bank.services.question_read_service import QuestionBankReadService


router = APIRouter(prefix="/api/question-assembly/ai", tags=["question-assembly"])


@router.get("/preflight", response_model=AiAssemblyPreflightResponse)
def get_ai_assembly_preflight(
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> AiAssemblyPreflightResponse:
    """生成前的费用预估：固定 1 次调用，token = 概况/system 字符数粗估 + 输出上限。"""

    profile = build_bank_profile(service)
    prompt_head = ASSEMBLY_SYSTEM_PROMPT + json.dumps(
        {"bank_profile": profile.to_payload()},
        ensure_ascii=False,
    )
    configured = resolve_content_generation_settings() is not None
    service_name, model_name = content_generation_public_info()
    return AiAssemblyPreflightResponse(
        configured=configured,
        service_name=service_name if configured else None,
        model_name=model_name if configured else None,
        call_count=1,
        estimated_total_tokens=estimate_prompt_tokens(prompt_head) + SPEC_MAX_TOKENS,
    )


@router.post(
    "/spec-jobs",
    response_model=JobResponse,
    status_code=202,
    responses={
        422: {"model": ErrorResponse, "description": "Spec request is invalid"},
    },
)
def submit_ai_assembly_spec_job(
    body: AiAssemblySpecJobSubmitRequest,
    manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """提交细目表生成 job；前端须先走 preflight 确认模型与费用。"""

    try:
        job = manager.submit(
            "ai_assembly_spec",
            {"request": body.request.model_dump()},
        )
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            404,
            "job_type_not_supported",
            "Job type is not supported",
            {"job_type": "ai_assembly_spec"},
        ) from exc
    return _job_response(job)


@router.post(
    "/select",
    response_model=AiAssemblySelectResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Assembly spec is invalid"},
    },
)
def select_ai_assembly_questions(
    body: AiAssemblySelectRequest,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
    workspace: AssemblyWorkspaceService = Depends(get_assembly_workspace_service),
) -> AiAssemblySelectResponse:
    """同步确定性选题：快且不调用模型，不走 job。"""

    spec = AssemblySpec.from_payload(body.spec.model_dump())
    try:
        result = select_questions(
            spec,
            service,
            frequency_service=QuestionFrequencyService(Path(service.db_path)),
            workspace=workspace,
            dedupe_enabled=body.dedupe_enabled,
            exclude_ids=body.exclude_ids,
        )
    except AssemblySpecError as exc:
        raise ApiError(
            422,
            "ai_assembly_spec_invalid",
            "Assembly spec is invalid",
            {"reason": str(exc)},
        ) from exc
    return AiAssemblySelectResponse(**result.to_payload())


@router.get(
    "/template-structure/{paper_id}",
    response_model=AiAssemblyTemplateStructureResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Template paper not found"},
    },
)
def get_ai_assembly_template_structure(
    paper_id: int,
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
) -> AiAssemblyTemplateStructureResponse:
    structure = extract_template_structure(service, paper_id)
    if not structure.entries:
        raise ApiError(
            404,
            "ai_assembly_template_not_found",
            "Template paper not found",
            {"paper_id": int(paper_id)},
        )
    return AiAssemblyTemplateStructureResponse(**structure.to_payload())
