from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
    _token,
)
from .sop_schemas import (
    AffairCloseRequest,
    AffairCommandRequest,
    AffairCreateRequest,
    AffairDraftRequest,
    AffairDraftResponse,
    AffairListResponse,
    AffairReopenRequest,
    AffairResponse,
    AffairWorkspaceListResponse,
    AffairWorkspaceResponse,
    DecisionRecordRequest,
    SopTemplateListResponse,
    SopBaselineResponse,
    SopTemplatePublishRequest,
    SopTemplateResponse,
    StepCompleteRequest,
)


def create_sop_router() -> APIRouter:
    router = APIRouter()

    @router.post("/sop/templates", response_model=SopTemplateResponse)
    def publish_template(
        request: Request,
        body: SopTemplatePublishRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.publish_template(
                token=_token(session_token),
                operation_id=body.operation_id,
                template_key=body.template_key,
                version=body.version,
                title=body.title,
                steps=[item.model_dump() for item in body.steps],
                workflow_scope=body.workflow_scope,
                risk_level=body.risk_level,
                emergency_prompt=body.emergency_prompt,
                school_config_gaps=body.school_config_gaps,
            )
        )

    @router.get("/sop/templates", response_model=SopTemplateListResponse)
    def list_templates(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).sop.list_templates(
                token=_token(session_token),
            )
        )

    @router.post("/sop/baselines/ensure", response_model=SopBaselineResponse)
    def ensure_baselines(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop_baselines.ensure_baselines(
                token=_token(session_token),
            )
        )

    @router.post("/sop/affairs", response_model=AffairWorkspaceResponse)
    def create_affair(
        request: Request,
        body: AffairCreateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.create(
                token=_token(session_token),
                operation_id=body.operation_id,
                template_version_id=body.template_version_id,
                title=body.title,
                summary=body.summary,
                participant_refs=body.participant_refs,
            )
        )

    @router.get("/sop/affairs", response_model=AffairWorkspaceListResponse)
    def list_affairs(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
        state: str | None = None,
        template: str | None = None,
        cursor: str | None = None,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.list(
                token=_token(session_token),
                state=state,
                template=template,
                cursor=cursor,
            )
        )

    @router.get("/sop/affairs/{affair_id}", response_model=AffairWorkspaceResponse)
    def get_affair(
        affair_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.read(
                token=_token(session_token),
                affair_id=affair_id,
            )
        )

    @router.put(
        "/sop/affairs/{affair_id}/steps/{step_instance_id}/draft",
        response_model=AffairDraftResponse,
    )
    def save_step_draft(
        affair_id: str,
        step_instance_id: str,
        request: Request,
        body: AffairDraftRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.save_draft(
                token=_token(session_token),
                affair_id=affair_id,
                step_instance_id=step_instance_id,
                **body.model_dump(),
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/commands",
        response_model=AffairWorkspaceResponse,
    )
    def affair_command(
        affair_id: str,
        request: Request,
        body: AffairCommandRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.advance(
                token=_token(session_token),
                affair_id=affair_id,
                **body.model_dump(),
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/steps/{step_instance_id}/complete",
        response_model=AffairResponse,
    )
    def complete_step(
        affair_id: str,
        step_instance_id: str,
        request: Request,
        body: StepCompleteRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.complete_step(
                token=_token(session_token),
                affair_id=affair_id,
                step_instance_id=step_instance_id,
                operation_id=body.operation_id,
                revision=body.revision,
                outcome=body.outcome,
                result=body.result,
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/decisions",
        response_model=AffairResponse,
    )
    def record_decision(
        affair_id: str,
        request: Request,
        body: DecisionRecordRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.record_decision(
                token=_token(session_token),
                affair_id=affair_id,
                operation_id=body.operation_id,
                decision_kind=body.decision_kind,
                summary=body.summary,
                step_instance_id=body.step_instance_id,
                decision_key=body.decision_key,
                selected_option=body.selected_option,
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/close",
        response_model=AffairResponse,
    )
    def close_affair(
        affair_id: str,
        request: Request,
        body: AffairCloseRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.close_affair(
                token=_token(session_token),
                affair_id=affair_id,
                operation_id=body.operation_id,
                revision=body.revision,
                closure_summary=body.closure_summary,
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/reopen",
        response_model=AffairResponse,
    )
    def reopen_affair(
        affair_id: str,
        request: Request,
        body: AffairReopenRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.reopen_affair(
                token=_token(session_token),
                affair_id=affair_id,
                operation_id=body.operation_id,
                revision=body.revision,
                reason=body.reason,
            )
        )

    return router


__all__ = ["create_sop_router"]
