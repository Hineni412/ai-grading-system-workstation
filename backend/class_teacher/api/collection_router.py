from __future__ import annotations

from fastapi import APIRouter, Request, Response

from .collection_schemas import (
    CollectionBoardCreateRequest,
    CollectionBoardListResponse,
    CollectionBoardResponse,
    CollectionItemUpdateRequest,
    IndividualReminder,
    MeetingConfirmResponse,
    MeetingImportRequest,
    MeetingInboxListResponse,
    MeetingInboxResponse,
    MeetingInboxUpdateRequest,
    RevisionRequest,
)
from .router import (
    _call,
    _no_store,
    _require_trusted_mutation,
    _service,
)


def create_collection_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/meeting-inboxes",
        response_model=MeetingInboxResponse,
    )
    def import_meeting_notes(
        request: Request,
        body: MeetingImportRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.import_meeting_notes(
                token="",
                operation_id=body.operation_id,
                raw_text=body.raw_text,
                reference_at=body.reference_at,
            )
        )

    @router.get(
        "/meeting-inboxes",
        response_model=MeetingInboxListResponse,
    )
    def list_meeting_inboxes(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).collections.list_meeting_inboxes(
                token="",
            )
        )

    @router.put(
        "/meeting-inboxes/{inbox_id}",
        response_model=MeetingInboxResponse,
    )
    def update_meeting_inbox(
        inbox_id: str,
        request: Request,
        body: MeetingInboxUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.update_meeting_inbox(
                token="",
                inbox_id=inbox_id,
                operation_id=body.operation_id,
                revision=body.revision,
                drafts=[item.model_dump() for item in body.drafts],
                delete_source_after_confirm=body.delete_source_after_confirm,
            )
        )

    @router.post(
        "/meeting-inboxes/{inbox_id}/cancel",
        response_model=MeetingInboxResponse,
    )
    def cancel_meeting_inbox(
        inbox_id: str,
        request: Request,
        body: RevisionRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.cancel_meeting_inbox(
                token="",
                inbox_id=inbox_id,
                operation_id=body.operation_id,
                revision=body.revision,
            )
        )

    @router.post(
        "/meeting-inboxes/{inbox_id}/confirm",
        response_model=MeetingConfirmResponse,
    )
    def confirm_meeting_inbox(
        inbox_id: str,
        request: Request,
        body: RevisionRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.confirm_meeting_inbox(
                token="",
                inbox_id=inbox_id,
                operation_id=body.operation_id,
                revision=body.revision,
            )
        )

    @router.post(
        "/collection-boards",
        response_model=CollectionBoardResponse,
    )
    def create_board(
        request: Request,
        body: CollectionBoardCreateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.create_board(
                token="",
                operation_id=body.operation_id,
                action_id=body.action_id,
                title=body.title,
                participant_refs=body.participant_refs,
            )
        )

    @router.get(
        "/collection-boards",
        response_model=CollectionBoardListResponse,
    )
    def list_boards(
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).collections.list_boards(
                token="",
            )
        )

    @router.patch(
        "/collection-boards/{board_id}/items/{item_id}",
        response_model=CollectionBoardResponse,
    )
    def update_collection_item(
        board_id: str,
        item_id: str,
        request: Request,
        body: CollectionItemUpdateRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).collections.update_collection_item(
                token="",
                board_id=board_id,
                item_id=item_id,
                operation_id=body.operation_id,
                revision=body.revision,
                status=body.status,
            )
        )

    @router.get(
        "/collection-boards/{board_id}/items/{item_id}/reminder",
        response_model=IndividualReminder,
    )
    def individual_reminder(
        board_id: str,
        item_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).collections.individual_reminder(
                token="",
                board_id=board_id,
                item_id=item_id,
            )
        )

    return router


__all__ = ["create_collection_router"]
