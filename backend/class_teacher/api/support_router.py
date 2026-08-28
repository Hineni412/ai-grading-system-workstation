from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Query, Request, Response

from ..errors import VaultError
from ..vault_service import VaultService
from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
    _student_subject_id,
)
from .support_schemas import (
    AffairProjectionRequest,
    AiDraftConfirmRequest,
    AttentionCreateRequest,
    AttentionDecisionRequest,
    AttentionResolveRequest,
    EvidenceBatchRequest,
    EvidenceGlobalMaxScoresRequest,
    EvidenceLinkRequest,
    EvidenceSessionDeleteRequest,
    EvidenceSessionMaxScoresRequest,
    EvidenceSessionUpdateRequest,
    EvidenceSupersedeRequest,
    FollowUpPostponeRequest,
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
    SupportPlanAiDraftRequest,
    SupportPlanCompleteRequest,
    SupportPlanCreateRequest,
)


def _roster_preview_identity(
    service: VaultService,
    student_ref: str,
) -> dict[str, object]:
    identity = service.class_roster.roster_identity_for_ref(
        roster_ref=student_ref,
    )
    if identity is None:
        raise VaultError(
            "support_subject_not_found", "学生档案不存在", status_code=404
        ) from None
    return identity


def _workspace_header(service: VaultService, student_ref: str) -> dict[str, object]:
    try:
        return service.student_directory.open(token="", subject_id=student_ref)
    except VaultError as exc:
        if exc.code != "support_subject_not_found":
            raise
    identity = _roster_preview_identity(service, student_ref)
    linked_subject_id = identity.get("subject_id")
    if linked_subject_id:
        return service.student_directory.open(
            token="", subject_id=str(linked_subject_id)
        )
    return {
        "subject_id": student_ref,
        "student_ref": student_ref,
        "source_student_id": str(identity["source_student_id"]),
        "display_name": str(identity["display_name"]),
        "class_label": str(identity["class_label"]),
        "support_record_count": 0,
        "support_plan_count": 0,
        "attention_pending_count": 0,
        "confirmed_entry_count": 0,
        "affair_count": 0,
        "related_affairs": [],
        "projection_state": "none",
        "last_confirmed_at": None,
        "profile_state": "not_created",
    }


def _student_card(service: VaultService, student_ref: str) -> dict[str, object]:
    try:
        return service.student_cards.get_card(token="", subject_id=student_ref)
    except VaultError as exc:
        if exc.code != "support_subject_not_found":
            raise
    identity = _roster_preview_identity(service, student_ref)
    linked_subject_id = identity.get("subject_id")
    if linked_subject_id:
        return service.student_cards.get_card(
            token="", subject_id=str(linked_subject_id)
        )
    return {
        "subject": {
            "subject_id": student_ref,
            "student_ref": student_ref,
            "revision": 0,
            "state": "not_created",
            "source_student_id": str(identity["source_student_id"]),
            "display_name": str(identity["display_name"]),
            "class_label": str(identity["class_label"]),
        },
        "entries": [],
        "current_profile": None,
        "existing_records": [],
        "support_plans": [],
        "profile_state": "not_created",
    }


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
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_roster.browse(
                token="",
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
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).student_directory.search(
                token="",
                q=q,
                class_label=class_label,
                state=state,
                roster_state=roster_state,
                sort=sort,
                cursor=cursor,
                page_size=page_size,
            )
        )

    @router.get("/support/overview")
    def support_overview(
        request: Request,
        response: Response,
        limit: int = 50,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_overview.support_overview(
                token="",
                limit=limit,
            )
        )

    @router.get("/evidence/overview")
    def evidence_overview(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_overview.academic_overview(
                token=""
            )
        )

    @router.get("/support/subjects/{student_ref}/workspace-header")
    def workspace_header(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _workspace_header(_service(request), student_ref)
        )

    @router.get("/support/subjects/{student_ref}/student-card")
    def student_card(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _student_card(_service(request), student_ref)
        )

    @router.get("/support/subjects/{student_ref}/academic-analysis")
    def academic_analysis(
        student_ref: str,
        request: Request,
        response: Response,
        time_range: str = "all",
        comparison_series: str | None = None,
        subject_name: str | None = None,
        comparable_only: bool = False,
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.academic.read(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                time_range=time_range,
                comparison_series=comparison_series,
                subject_name=subject_name,
                comparable_only=comparable_only,
            )
        return _call(read)

    @router.get("/evidence/{evidence_version_id}/snapshot")
    def evidence_snapshot(
        evidence_version_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(lambda: _service(request).academic.snapshot(
            token="", evidence_version_id=evidence_version_id
        ))

    @router.post("/support/subjects")
    def create_subject(
        request: Request,
        body: SubjectCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.create_subject(
            token="", **body.model_dump()
        ))

    @router.get("/support/subjects")
    def list_subjects(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(lambda: _service(request).support.list_subjects(
            token=""
        ))

    @router.put("/support/subjects/{student_ref}")
    def update_subject(
        student_ref: str,
        request: Request,
        body: SubjectUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def update():
            service = _service(request)
            return service.support.update_subject(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                **body.model_dump(),
            )
        return _call(update)

    @router.delete("/support/subjects/{student_ref}")
    def delete_subject(
        student_ref: str,
        request: Request,
        body: SubjectDeleteRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def delete():
            service = _service(request)
            return service.support.delete_subject(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                **body.model_dump(),
            )
        return _call(delete)

    @router.get("/support/subjects/{student_ref}/deletion-preview")
    def preview_subject_deletion(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        def preview():
            service = _service(request)
            return service.support.preview_subject_deletion(
                token="",
                subject_id=_student_subject_id(service, student_ref),
            )
        return _call(preview)

    @router.post("/support/subjects/{student_ref}/records")
    def create_record(
        student_ref: str,
        request: Request,
        body: RecordCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def create():
            service = _service(request)
            return service.support.create_record(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                **body.model_dump(),
            )
        return _call(create)

    @router.get("/support/subjects/{student_ref}/records")
    def list_records(
        student_ref: str,
        request: Request,
        response: Response,
        include_inactive: bool = Query(True),
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.support.list_records(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                include_inactive=include_inactive,
            )
        return _call(read)

    @router.put("/support/records/{record_id}")
    def revise_record(
        record_id: str,
        request: Request,
        body: RecordReviseRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.revise_record(
            token="",
            record_id=record_id,
            **body.model_dump(),
        ))

    @router.post("/support/records/{record_id}/state")
    def change_record_state(
        record_id: str,
        request: Request,
        body: RecordStateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.set_record_state(
            token="",
            record_id=record_id,
            **body.model_dump(),
        ))

    @router.post("/support/records/{record_id}/confirm-ai")
    def confirm_ai_draft(
        record_id: str,
        request: Request,
        body: AiDraftConfirmRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.confirm_ai_draft(
            token="",
            draft_record_id=record_id,
            **body.model_dump(),
        ))

    @router.get("/support/subjects/{student_ref}/summary")
    def get_summary(
        student_ref: str,
        request: Request,
        response: Response,
        as_of: str | None = Query(None),
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.support.get_summary(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                as_of=as_of,
            )
        return _call(read)

    @router.post("/support/observation-evidence")
    def link_evidence(
        request: Request,
        body: EvidenceLinkRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).support.link_observation_evidence(
                token="", **body.model_dump()
            )
        )

    @router.post("/support/subjects/{student_ref}/plans")
    def create_support_plan(
        student_ref: str,
        request: Request,
        body: SupportPlanCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def create():
            service = _service(request)
            return service.support.create_support_plan(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                **body.model_dump(),
            )
        return _call(create)

    @router.post("/support/subjects/{student_ref}/plans/ai-draft")
    def draft_support_plan_with_ai(
        student_ref: str,
        request: Request,
        body: SupportPlanAiDraftRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def draft():
            service = _service(request)
            return service.support_plan_drafts.draft_plan(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                operation_id=body.operation_id,
            )
        return _call(draft)

    @router.get("/support/subjects/{student_ref}/plans")
    def list_support_plans(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.support.list_support_plans(
                token="",
                subject_id=_student_subject_id(service, student_ref),
            )
        return _call(read)

    @router.post("/support/plans/{support_plan_id}/complete")
    def complete_support_plan(
        support_plan_id: str,
        request: Request,
        body: SupportPlanCompleteRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).support.complete_support_plan(
            token="",
            support_plan_id=support_plan_id,
            **body.model_dump(),
        ))

    @router.post("/support/follow-ups/{projection_id}/postpone")
    def postpone_follow_up(
        projection_id: str,
        request: Request,
        body: FollowUpPostponeRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).projections.postpone(
            token="",
            projection_id=projection_id,
            due_date=body.due_date,
        ))

    @router.post("/support/follow-ups/{projection_id}/dismiss")
    def dismiss_follow_up(
        projection_id: str,
        request: Request,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).projections.dismiss(
            token="",
            projection_id=projection_id,
        ))

    @router.post("/support/subjects/{student_ref}/project-affair")
    def project_affair(
        student_ref: str,
        request: Request,
        body: AffairProjectionRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def project():
            service = _service(request)
            return service.support.project_closed_affair(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                **body.model_dump(),
            )
        return _call(project)

    @router.get("/quick-inbox/capabilities")
    def quick_capabilities(request: Request, response: Response):
        _no_store(response)
        return _service(request).quick_inbox.capabilities()

    @router.post("/quick-inbox")
    def create_quick_text(
        request: Request,
        body: QuickTextRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        def create():
            service = _service(request)
            payload = body.model_dump()
            if payload.get("subject_id"):
                # 对外学生编号统一为稳定学籍标识；兼容旧 uuid，入口归一为内部编号。
                payload["subject_id"] = _student_subject_id(
                    service, str(payload["subject_id"])
                )
            return service.quick_inbox.create_text(token="", **payload)
        return _call(create)

    @router.get("/quick-inbox")
    def list_quick_text(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.list_drafts(
            token=""
        ))

    @router.put("/quick-inbox/{inbox_item_id}")
    def update_quick_text(
        inbox_item_id: str,
        request: Request,
        body: QuickUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        payload = body.model_dump()
        payload["fragments"] = [
            item.model_dump() for item in body.fragments
        ]
        return _call(lambda: _service(request).quick_inbox.update_fragments(
            token="",
            inbox_item_id=inbox_item_id,
            **payload,
        ))

    @router.post("/quick-inbox/{inbox_item_id}/confirm")
    def confirm_quick_text(
        inbox_item_id: str,
        request: Request,
        body: QuickConfirmRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.confirm(
            token="",
            inbox_item_id=inbox_item_id,
            **body.model_dump(),
        ))

    @router.post("/quick-inbox/{inbox_item_id}/cancel")
    def cancel_quick_text(
        inbox_item_id: str,
        request: Request,
        body: OperationRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).quick_inbox.cancel(
            token="",
            inbox_item_id=inbox_item_id,
            operation_id=body.operation_id,
        ))

    @router.post("/evidence/batches")
    def confirm_evidence_batch(
        request: Request,
        body: EvidenceBatchRequest,
        response: Response,
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
            token="", **body.model_dump()
        ))

    @router.post("/evidence/spreadsheet-preview")
    def preview_spreadsheet(
        request: Request,
        body: SpreadsheetPreviewRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        _call(
            lambda: _service(request).ensure_plaintext_ready()
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

    @router.get("/evidence/subjects/{student_ref}")
    def list_evidence(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.evidence.list_subject_evidence(
                token="",
                subject_id=_student_subject_id(service, student_ref),
            )
        return _call(read)

    @router.get("/evidence/compare")
    def compare_evidence(
        request: Request,
        response: Response,
        older: str = Query(...),
        newer: str = Query(...),
    ):
        _no_store(response)
        return _call(lambda: _service(request).evidence.compare(
            token="",
            older_evidence_version_id=older,
            newer_evidence_version_id=newer,
        ))

    @router.get("/evidence/subjects/{student_ref}/trend")
    def evidence_trend(
        student_ref: str,
        request: Request,
        response: Response,
        subject_name: str = Query(...),
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.evidence.trend(
                token="",
                subject_id=_student_subject_id(service, student_ref),
                subject_name=subject_name,
            )
        return _call(read)

    @router.post("/evidence/{evidence_version_id}/supersede")
    def supersede_evidence(
        evidence_version_id: str,
        request: Request,
        body: EvidenceSupersedeRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).evidence.supersede_evidence(
            token="",
            evidence_version_id=evidence_version_id,
            **body.model_dump(),
        ))

    @router.patch("/evidence/sessions/{session_id}")
    def update_session_metadata(
        session_id: str,
        request: Request,
        body: EvidenceSessionUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).evidence.update_session_metadata(
            token="",
            session_id=session_id,
            operation_id=body.operation_id,
            fields=body.model_dump(
                exclude={"operation_id"},
                exclude_none=True,
            ),
        ))

    @router.get("/evidence/sessions/{session_id}/delete-preview")
    def preview_session_deletion(
        session_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).evidence.preview_delete_session(
                token="",
                session_id=session_id,
            )
        )

    @router.get("/evidence/sessions/{session_id}/class-results")
    def session_class_results(
        session_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_overview.session_class_results(
                token="",
                session_id=session_id,
            )
        )

    @router.get("/evidence/class-trend")
    def evidence_class_trend(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).class_overview.class_trend(token="")
        )

    @router.patch("/evidence/sessions/{session_id}/max-scores")
    def update_session_max_scores(
        session_id: str,
        request: Request,
        body: EvidenceSessionMaxScoresRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).evidence.update_session_max_scores(
                token="",
                session_id=session_id,
                operation_id=body.operation_id,
                max_scores=body.max_scores,
                participant_count=body.participant_count,
            )
        )

    @router.patch("/evidence/max-scores/global")
    def update_global_max_scores(
        request: Request,
        body: EvidenceGlobalMaxScoresRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).evidence.update_global_max_scores(
                token="",
                operation_id=body.operation_id,
                max_scores=body.max_scores,
                participant_count=body.participant_count,
            )
        )

    @router.delete("/evidence/sessions/{session_id}")
    def delete_session(
        session_id: str,
        request: Request,
        body: EvidenceSessionDeleteRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).evidence.delete_session(
            token="",
            session_id=session_id,
            **body.model_dump(),
        ))

    @router.post("/attention-cards")
    def create_attention(
        request: Request,
        body: AttentionCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).attention.create_from_evidence(
            token="", **body.model_dump()
        ))

    @router.get("/attention-cards/subjects/{student_ref}")
    def list_attention(
        student_ref: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        def read():
            service = _service(request)
            return service.attention.list_for_subject(
                token="",
                subject_id=_student_subject_id(service, student_ref),
            )
        return _call(read)

    @router.post("/attention-cards/{attention_card_id}/resolve")
    def resolve_attention(
        attention_card_id: str,
        request: Request,
        body: AttentionResolveRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).attention.resolve(
            token="",
            attention_card_id=attention_card_id,
            **body.model_dump(),
        ))

    @router.post("/attention-cards/{attention_card_id}/decide")
    def decide_attention(
        attention_card_id: str,
        request: Request,
        body: AttentionDecisionRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).academic.decide(
            token="", attention_card_id=attention_card_id,
            **body.model_dump()
        ))

    return router


__all__ = ["create_support_router"]
