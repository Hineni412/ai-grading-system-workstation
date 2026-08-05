from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Header, Query, Request, Response

from ..errors import VaultError
from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
    _token,
)
from .support_schemas import (
    AffairProjectionRequest,
    AiDraftConfirmRequest,
    AttentionCreateRequest,
    AttentionDecisionRequest,
    AttentionResolveRequest,
    EvidenceBatchRequest,
    EvidenceLinkRequest,
    EvidenceSupersedeRequest,
    SpreadsheetPreviewRequest,
    OperationRequest,
    QuickConfirmRequest,
    QuickTextRequest,
    QuickUpdateRequest,
    RecordCreateRequest,
    RecordReviseRequest,
    RecordStateRequest,
    SubjectCreateRequest,
    SubjectDeleteRequest,
    SubjectUpdateRequest,
    SupportPlanCompleteRequest,
    SupportPlanCreateRequest,
)


def create_support_router() -> APIRouter:
    router = APIRouter()

    @router.get("/support/roster-source")
    def roster_source(
        request: Request,
        response: Response,
        q: str | None = None,
        class_label: str | None = None,
        cursor: str | None = None,
        page_size: int = 50,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_roster.browse(
                token=session(session_token),
                q=q,
                class_label=class_label,
                cursor=cursor,
                page_size=page_size,
            )
        )

    @router.get("/support/directory")
    def directory(
        request: Request,
        response: Response,
        q: str | None = None,
        class_label: str | None = None,
        state: str | None = None,
        roster_state: str | None = None,
        sort: str = "last_confirmed_desc",
        cursor: str | None = None,
        page_size: int = 20,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).student_directory.search(
                token=session(session_token),
                q=q,
                class_label=class_label,
                state=state,
                roster_state=roster_state,
                sort=sort,
                cursor=cursor,
                page_size=page_size,
            )
        )

    @router.get("/support/subjects/{subject_id}/workspace-header")
    def workspace_header(
        subject_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).student_directory.open(
                token=session(session_token),
                subject_id=subject_id,
            )
        )

    @router.get("/support/subjects/{subject_id}/academic-analysis")
    def academic_analysis(
        subject_id: str,
        request: Request,
        response: Response,
        time_range: str = "all",
        comparison_series: str | None = None,
        subject_name: str | None = None,
        comparable_only: bool = False,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).academic.read(
            token=session(session_token),
            subject_id=subject_id,
            time_range=time_range,
            comparison_series=comparison_series,
            subject_name=subject_name,
            comparable_only=comparable_only,
        ))

    @router.get("/evidence/{evidence_version_id}/snapshot")
    def evidence_snapshot(
        evidence_version_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).academic.snapshot(
            token=session(session_token), evidence_version_id=evidence_version_id
        ))

    def session(value: str | None) -> str:
        return _token(value)

    @router.post("/support/subjects")
    def create_subject(
        request: Request,
        body: SubjectCreateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.create_subject(
            token=session(session_token), **body.model_dump()
        ))

    @router.get("/support/subjects")
    def list_subjects(
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).support.list_subjects(
            token=session(session_token)
        ))

    @router.put("/support/subjects/{subject_id}")
    def update_subject(
        subject_id: str,
        request: Request,
        body: SubjectUpdateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.update_subject(
            token=session(session_token),
            subject_id=subject_id,
            **body.model_dump(),
        ))

    @router.delete("/support/subjects/{subject_id}")
    def delete_subject(
        subject_id: str,
        request: Request,
        body: SubjectDeleteRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.delete_subject(
            token=session(session_token),
            subject_id=subject_id,
            **body.model_dump(),
        ))

    @router.get("/support/subjects/{subject_id}/deletion-preview")
    def preview_subject_deletion(
        subject_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).support.preview_subject_deletion(
                token=session(session_token),
                subject_id=subject_id,
            )
        )

    @router.post("/support/subjects/{subject_id}/records")
    def create_record(
        subject_id: str,
        request: Request,
        body: RecordCreateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.create_record(
            token=session(session_token),
            subject_id=subject_id,
            **body.model_dump(),
        ))

    @router.get("/support/subjects/{subject_id}/records")
    def list_records(
        subject_id: str,
        request: Request,
        response: Response,
        include_inactive: bool = Query(True),
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).support.list_records(
            token=session(session_token),
            subject_id=subject_id,
            include_inactive=include_inactive,
        ))

    @router.put("/support/records/{record_id}")
    def revise_record(
        record_id: str,
        request: Request,
        body: RecordReviseRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.revise_record(
            token=session(session_token),
            record_id=record_id,
            **body.model_dump(),
        ))

    @router.post("/support/records/{record_id}/state")
    def change_record_state(
        record_id: str,
        request: Request,
        body: RecordStateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.set_record_state(
            token=session(session_token),
            record_id=record_id,
            **body.model_dump(),
        ))

    @router.post("/support/records/{record_id}/confirm-ai")
    def confirm_ai_draft(
        record_id: str,
        request: Request,
        body: AiDraftConfirmRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.confirm_ai_draft(
            token=session(session_token),
            draft_record_id=record_id,
            **body.model_dump(),
        ))

    @router.get("/support/subjects/{subject_id}/summary")
    def get_summary(
        subject_id: str,
        request: Request,
        response: Response,
        as_of: str | None = Query(None),
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).support.get_summary(
            token=session(session_token),
            subject_id=subject_id,
            as_of=as_of,
        ))

    @router.post("/support/observation-evidence")
    def link_evidence(
        request: Request,
        body: EvidenceLinkRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).support.link_observation_evidence(
                token=session(session_token), **body.model_dump()
            )
        )

    @router.post("/support/subjects/{subject_id}/plans")
    def create_support_plan(
        subject_id: str,
        request: Request,
        body: SupportPlanCreateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.create_support_plan(
            token=session(session_token),
            subject_id=subject_id,
            **body.model_dump(),
        ))

    @router.get("/support/subjects/{subject_id}/plans")
    def list_support_plans(
        subject_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).support.list_support_plans(
                token=session(session_token),
                subject_id=subject_id,
            )
        )

    @router.post("/support/plans/{support_plan_id}/complete")
    def complete_support_plan(
        support_plan_id: str,
        request: Request,
        body: SupportPlanCompleteRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.complete_support_plan(
            token=session(session_token),
            support_plan_id=support_plan_id,
            **body.model_dump(),
        ))

    @router.post("/support/subjects/{subject_id}/project-affair")
    def project_affair(
        subject_id: str,
        request: Request,
        body: AffairProjectionRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.project_closed_affair(
            token=session(session_token),
            subject_id=subject_id,
            **body.model_dump(),
        ))

    @router.get("/quick-inbox/capabilities")
    def quick_capabilities(request: Request, response: Response):
        _no_store(response)
        return _service(request).quick_inbox.capabilities()

    @router.post("/quick-inbox")
    def create_quick_text(
        request: Request,
        body: QuickTextRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.create_text(
            token=session(session_token), **body.model_dump()
        ))

    @router.get("/quick-inbox")
    def list_quick_text(
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.list_drafts(
            token=session(session_token)
        ))

    @router.put("/quick-inbox/{inbox_item_id}")
    def update_quick_text(
        inbox_item_id: str,
        request: Request,
        body: QuickUpdateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        payload = body.model_dump()
        payload["fragments"] = [
            item.model_dump() for item in body.fragments
        ]
        return _call(lambda: _service(request).quick_inbox.update_fragments(
            token=session(session_token),
            inbox_item_id=inbox_item_id,
            **payload,
        ))

    @router.post("/quick-inbox/{inbox_item_id}/confirm")
    def confirm_quick_text(
        inbox_item_id: str,
        request: Request,
        body: QuickConfirmRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.confirm(
            token=session(session_token),
            inbox_item_id=inbox_item_id,
            **body.model_dump(),
        ))

    @router.post("/quick-inbox/{inbox_item_id}/cancel")
    def cancel_quick_text(
        inbox_item_id: str,
        request: Request,
        body: OperationRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.cancel(
            token=session(session_token),
            inbox_item_id=inbox_item_id,
            operation_id=body.operation_id,
        ))

    @router.post("/evidence/batches")
    def confirm_evidence_batch(
        request: Request,
        body: EvidenceBatchRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        if body.batch.get("source_kind") != "confirmed_spreadsheet":
            return _call(lambda: (_ for _ in ()).throw(VaultError(
                "assessment_source_read_only",
                "现有数学证据只能通过服务器只读适配器进入",
                status_code=422,
            )))
        return _call(lambda: _service(request).evidence.confirm_batch(
            token=session(session_token), **body.model_dump()
        ))

    @router.post("/evidence/spreadsheet-preview")
    def preview_spreadsheet(
        request: Request,
        body: SpreadsheetPreviewRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        _call(
            lambda: _service(request).session_key(session(session_token))
        )
        try:
            content = base64.b64decode(
                body.content_base64,
                validate=True,
            )
        except (ValueError, binascii.Error):
            return _call(lambda: (_ for _ in ()).throw(VaultError(
                "assessment_file_encoding_invalid",
                "成绩文件内容无效",
                status_code=422,
            )))
        from ..assessment_evidence_service import ConfirmedSpreadsheetAdapter

        return _call(lambda: ConfirmedSpreadsheetAdapter.preview(
            file_name=body.file_name,
            content=content,
            sheet_name=body.sheet_name,
        ))

    @router.get("/evidence/subjects/{subject_id}")
    def list_evidence(
        subject_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).evidence.list_subject_evidence(
                token=session(session_token), subject_id=subject_id
            )
        )

    @router.get("/evidence/compare")
    def compare_evidence(
        request: Request,
        response: Response,
        older: str = Query(...),
        newer: str = Query(...),
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).evidence.compare(
            token=session(session_token),
            older_evidence_version_id=older,
            newer_evidence_version_id=newer,
        ))

    @router.get("/evidence/subjects/{subject_id}/trend")
    def evidence_trend(
        subject_id: str,
        request: Request,
        response: Response,
        subject_name: str = Query(...),
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).evidence.trend(
            token=session(session_token),
            subject_id=subject_id,
            subject_name=subject_name,
        ))

    @router.post("/evidence/{evidence_version_id}/supersede")
    def supersede_evidence(
        evidence_version_id: str,
        request: Request,
        body: EvidenceSupersedeRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).evidence.supersede_evidence(
            token=session(session_token),
            evidence_version_id=evidence_version_id,
            **body.model_dump(),
        ))

    @router.post("/attention-cards")
    def create_attention(
        request: Request,
        body: AttentionCreateRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).attention.create_from_evidence(
            token=session(session_token), **body.model_dump()
        ))

    @router.get("/attention-cards/subjects/{subject_id}")
    def list_attention(
        subject_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _no_store(response)
        return _call(lambda: _service(request).attention.list_for_subject(
            token=session(session_token), subject_id=subject_id
        ))

    @router.post("/attention-cards/{attention_card_id}/resolve")
    def resolve_attention(
        attention_card_id: str,
        request: Request,
        body: AttentionResolveRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).attention.resolve(
            token=session(session_token),
            attention_card_id=attention_card_id,
            **body.model_dump(),
        ))

    @router.post("/attention-cards/{attention_card_id}/decide")
    def decide_attention(
        attention_card_id: str,
        request: Request,
        body: AttentionDecisionRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).academic.decide(
            token=session(session_token), attention_card_id=attention_card_id,
            **body.model_dump()
        ))

    return router


__all__ = ["create_support_router"]
