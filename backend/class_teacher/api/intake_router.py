from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from .intake_schemas import (
    DraftAIRevisionRequest,
    DraftUpdateRequest,
    HandoffAdoptRequest,
    HomeroomPreferenceUpdate,
    ManualRouteRequest,
    TurnAppendRequest,
)
from .router import _call, _no_store, _require_trusted_mutation, _service, _token


def create_intake_router() -> APIRouter:
    router = APIRouter(prefix="/intake")

    @router.get("/preferences/homeroom-class")
    def get_homeroom(request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).intake.preferences.get())

    @router.put("/preferences/homeroom-class")
    def set_homeroom(request: Request, body: HomeroomPreferenceUpdate, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.preferences.set(**body.model_dump()))

    @router.post("/conversations")
    def start_conversation(request: Request, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.start_conversation())

    @router.get("/conversations")
    def list_conversations(request: Request, response: Response, limit: int = 12):
        _no_store(response)
        return _call(lambda: _service(request).intake.list_conversations(limit=limit))

    @router.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).intake.get_conversation(conversation_id))

    @router.post("/conversations/{conversation_id}/turns")
    def append_turn(conversation_id: str, request: Request, body: TurnAppendRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.append_turn(
            conversation_id=conversation_id, **body.model_dump()
        ))

    @router.post("/turns/{turn_id}/manual-route")
    def manual_route(turn_id: str, request: Request, body: ManualRouteRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.manual_route(turn_id=turn_id, mode=body.mode))

    @router.get("/handoffs/{handoff_id}")
    def open_handoff(handoff_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).intake.open_handoff(handoff_id))

    @router.put("/handoffs/{handoff_id}/draft")
    def update_draft(handoff_id: str, request: Request, body: DraftUpdateRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.update_draft(
            handoff_id=handoff_id, **body.model_dump()
        ))

    @router.post("/handoffs/{handoff_id}/ai-revisions")
    def request_draft_revision(
        handoff_id: str,
        request: Request,
        body: DraftAIRevisionRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.request_draft_revision(
            handoff_id=handoff_id, **body.model_dump()
        ))

    @router.get("/draft-revisions/{request_id}")
    def get_draft_revision(request_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).intake.get_draft_revision(request_id))

    @router.post("/handoffs/{handoff_id}/discard")
    def discard_handoff(handoff_id: str, request: Request, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.discard_handoff(handoff_id))

    @router.post("/handoffs/{handoff_id}/adopt")
    def adopt_handoff(
        handoff_id: str,
        request: Request,
        body: HandoffAdoptRequest,
        response: Response,
        session_token: str | None = Header(None, alias="x-class-teacher-session"),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.adopt_handoff(
            token=_token(session_token), handoff_id=handoff_id, **body.model_dump()
        ))

    return router


__all__ = ["create_intake_router"]
