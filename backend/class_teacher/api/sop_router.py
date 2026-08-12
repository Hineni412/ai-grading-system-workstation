from __future__ import annotations

from fastapi import APIRouter, Request, Response

from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
)
from .sop_schemas import (
    AffairCloseRequest,
    AffairCommandRequest,
    AffairCreateRequest,
    AffairDraftRequest,
    AffairDraftResponse,
    AffairFlowRevisionDecideRequest,
    AffairListResponse,
    AffairReopenRequest,
    AffairResponse,
    AffairSyncUpdateRequest,
    AffairSyncUpdateResponse,
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.publish_template(
                token="",
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
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).sop.list_templates(
                token="",
            )
        )

    @router.post("/sop/baselines/ensure", response_model=SopBaselineResponse)
    def ensure_baselines(
        request: Request,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop_baselines.ensure_baselines(
                token="",
            )
        )

    @router.post("/sop/affairs", response_model=AffairWorkspaceResponse)
    def create_affair(
        request: Request,
        body: AffairCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.create(
                token="",
                operation_id=body.operation_id,
                template_version_id=body.template_version_id,
                title=body.title,
                summary=body.summary,
                participant_refs=body.participant_refs,
                subject_ids=body.subject_ids,
            )
        )

    @router.get("/sop/affairs", response_model=AffairWorkspaceListResponse)
    def list_affairs(
        request: Request,
        response: Response,
        state: str | None = None,
        template: str | None = None,
        cursor: str | None = None,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.list(
                token="",
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
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.read(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.save_draft(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).affairs.advance(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.complete_step(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.record_decision(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.close_affair(
                token="",
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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.reopen_affair(
                token="",
                affair_id=affair_id,
                operation_id=body.operation_id,
                revision=body.revision,
                reason=body.reason,
            )
        )

    @router.post(
        "/sop/affairs/{affair_id}/sync-updates",
        response_model=AffairSyncUpdateResponse,
    )
    def sync_affair_update(
        affair_id: str,
        request: Request,
        body: AffairSyncUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)

        def sync():
            services = _service(request)
            created = services.sop.create_sync_request(
                token="",
                affair_id=affair_id,
                expected_revision=body.expected_revision,
                text=body.text,
                operation_id=body.operation_id,
            )
            return services.intake.start_affair_flow_revision(
                affair_id=affair_id,
                affair_revision=int(created["affair_revision"]),
                sync_id=str(created["sync_id"]),
                operation_id=body.operation_id,
            )

        return _call(sync)

    @router.post(
        "/sop/affairs/{affair_id}/flow-revisions/{revision_id}/decide",
        response_model=AffairResponse,
    )
    def decide_flow_revision(
        affair_id: str,
        revision_id: str,
        request: Request,
        body: AffairFlowRevisionDecideRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).sop.decide_flow_revision(
                token="",
                affair_id=affair_id,
                revision_id=revision_id,
                accepted_item_ids=body.accepted_item_ids,
                expected_revision=body.expected_revision,
                operation_id=body.operation_id,
            )
        )

    return router


__all__ = ["create_sop_router"]
