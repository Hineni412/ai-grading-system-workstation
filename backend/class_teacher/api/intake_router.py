from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response
from starlette.concurrency import run_in_threadpool

from .intake_schemas import (
    ConversationStartRequest,
    DraftAIRevisionRequest,
    DraftUpdateRequest,
    HandoffAdoptRequest,
    HomeroomPreferenceUpdate,
    ManualRouteRequest,
    TurnAppendRequest,
)
from .router import _api_error, _call, _no_store, _require_trusted_mutation, _service


def create_intake_router() -> APIRouter:
    router = APIRouter(prefix="/intake")

    @router.get("/speech/capabilities")
    def speech_capabilities(request: Request, response: Response):
        _no_store(response)
        def capabilities():
            service = _service(request)
            result = dict(service.local_speech.capabilities())
            result["cloud_audio"] = service.intake.cloud_audio_capabilities()
            return result

        return _call(capabilities)

    @router.post("/speech/transcriptions")
    async def transcribe_speech(request: Request, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
            raise _api_error(415, "speech_content_type_invalid", "只接受本机生成的 WAV 录音")
        service = _service(request).local_speech
        try:
            declared_size = int(request.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            raise _api_error(400, "speech_content_length_invalid", "录音大小信息无效")
        if declared_size < 0:
            raise _api_error(400, "speech_content_length_invalid", "录音大小信息无效")
        if declared_size > service.max_audio_bytes:
            raise _api_error(413, "speech_audio_too_large", "录音超过本机语音输入允许的大小")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > service.max_audio_bytes:
                raise _api_error(413, "speech_audio_too_large", "录音超过本机语音输入允许的大小")
        return await run_in_threadpool(
            lambda: _call(lambda: service.transcribe_wav(bytes(content)))
        )

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
    def start_conversation(
        request: Request,
        response: Response,
        body: ConversationStartRequest | None = None,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).intake.start_conversation(
                token="",
                subject_id=None if body is None else body.subject_id,
            )
        )

    @router.get("/conversations")
    def list_conversations(request: Request, response: Response, limit: int = 5):
        _no_store(response)
        return _call(lambda: _service(request).intake.list_conversations(limit=limit))

    @router.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request, response: Response):
        _no_store(response)
        return _call(lambda: _service(request).intake.get_conversation(conversation_id))

    @router.delete("/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, request: Request, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.delete_conversation(conversation_id))

    @router.post("/conversations/{conversation_id}/turns")
    def append_turn(conversation_id: str, request: Request, body: TurnAppendRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.append_turn(
            conversation_id=conversation_id, **body.model_dump()
        ))

    @router.post("/conversations/{conversation_id}/audio-turns")
    async def append_audio_turn(
        conversation_id: str,
        request: Request,
        response: Response,
        expected_revision: str | None = Header(
            None,
            alias="x-class-teacher-conversation-revision",
        ),
        operation_id: str | None = Header(
            None,
            alias="x-class-teacher-operation-id",
        ),
        model_fingerprint: str | None = Header(
            None,
            alias="x-class-teacher-model-fingerprint",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
            raise _api_error(415, "speech_content_type_invalid", "只接受本机生成的 WAV 录音")
        try:
            revision = int(expected_revision or "")
        except (TypeError, ValueError):
            raise _api_error(400, "class_teacher_revision_invalid", "会话版本信息无效")
        if revision < 0:
            raise _api_error(400, "class_teacher_revision_invalid", "会话版本信息无效")
        if not str(operation_id or "").strip():
            raise _api_error(400, "class_teacher_operation_id_invalid", "录音操作编号无效")
        if not str(model_fingerprint or "").strip():
            raise _api_error(400, "class_teacher_model_fingerprint_invalid", "模型配置标识无效")

        services = _service(request)
        local_speech = services.local_speech
        try:
            declared_size = int(request.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            raise _api_error(400, "speech_content_length_invalid", "录音大小信息无效")
        if declared_size < 0:
            raise _api_error(400, "speech_content_length_invalid", "录音大小信息无效")
        if declared_size > local_speech.max_audio_bytes:
            raise _api_error(413, "speech_audio_too_large", "录音超过语音输入允许的大小")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > local_speech.max_audio_bytes:
                raise _api_error(413, "speech_audio_too_large", "录音超过语音输入允许的大小")
        wav_content = bytes(content)
        await run_in_threadpool(
            lambda: _call(lambda: local_speech.validate_wav(wav_content))
        )
        return await run_in_threadpool(
            lambda: _call(lambda: services.intake.append_cloud_audio_turn(
                conversation_id=conversation_id,
                expected_revision=revision,
                operation_id=str(operation_id).strip(),
                expected_destination_fingerprint=str(model_fingerprint).strip(),
                wav_content=wav_content,
            ))
        )

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
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(lambda: _service(request).intake.adopt_handoff(
            token="", handoff_id=handoff_id, **body.model_dump()
        ))

    return router


__all__ = ["create_intake_router"]
