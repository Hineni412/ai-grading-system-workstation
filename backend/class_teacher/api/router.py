from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Response

from ..errors import VaultError
from ..vault_service import VaultService
from .schemas import (
    ChangePasswordRequest,
    InitializeRequest,
    InitializeResponse,
    OperationResponse,
    PinInitializeRequest,
    PinChangeRequest,
    PinRecoverRequest,
    PinUpgradeRequest,
    PinUnlockRequest,
    RecoverRequest,
    SessionResponse,
    TouchResponse,
    UnlockRequest,
    VaultStatusResponse,
)


_CLIENT_HEADER = "class-teacher-browser-v1"
def _api_error(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
):
    from backend.api.app import ApiError

    return ApiError(status_code, code, message, details)


def _service(request: Request) -> VaultService:
    services = getattr(request.app.state, "workspace_services", {})
    service = services.get("class-teacher") if isinstance(services, dict) else None
    if not isinstance(service, VaultService):
        raise _api_error(
            503,
            "class_teacher_unavailable",
            "班主任工作台暂时不可用",
        )
    return service


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _require_trusted_mutation(request: Request) -> None:
    host = request.headers.get("host", "").split(":", 1)[0].casefold()
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        raise _api_error(
            403,
            "class_teacher_origin_rejected",
            "请求来源未获允许",
        )
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost"}
        ):
            raise _api_error(
                403,
                "class_teacher_origin_rejected",
                "请求来源未获允许",
            )
    if request.headers.get("x-class-teacher-client") != _CLIENT_HEADER:
        raise _api_error(
            403,
            "class_teacher_client_required",
            "请从班主任工作台页面执行此操作",
        )


def _token(value: str | None) -> str:
    return str(value or "")


def _call(function):
    try:
        return function()
    except VaultError as exc:
        raise _api_error(
            exc.status_code,
            exc.code,
            exc.message,
            exc.details,
        ) from None


async def _serialized_operation(request: Request):
    async with _service(request).operation_scope():
        yield


def create_router() -> APIRouter:
    router = APIRouter(tags=["class-teacher"])
    protected_router = APIRouter(
        dependencies=[Depends(_serialized_operation)],
    )

    @protected_router.get("/vault/status", response_model=VaultStatusResponse)
    def status(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(lambda: _service(request).status(session_token))

    @protected_router.post("/vault/initialize", response_model=InitializeResponse)
    def initialize(request: Request, body: InitializeRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).initialize(
                password=body.password.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/pin/initialize", response_model=InitializeResponse)
    def initialize_pin(
        request: Request,
        body: PinInitializeRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).initialize_pin(
                pin=body.pin.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/unlock", response_model=SessionResponse)
    def unlock(request: Request, body: UnlockRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).unlock(
                password=body.password.get_secret_value(),
            )
        )

    @protected_router.post("/vault/pin/unlock", response_model=SessionResponse)
    def unlock_pin(
        request: Request,
        body: PinUnlockRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).unlock_pin(pin=body.pin.get_secret_value())
        )

    @protected_router.post("/vault/recover", response_model=SessionResponse)
    def recover(request: Request, body: RecoverRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).recover(
                recovery_key=body.recovery_key.get_secret_value(),
                new_password=body.new_password.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/pin/recover", response_model=SessionResponse)
    def recover_pin(
        request: Request,
        body: PinRecoverRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).recover_pin(
                recovery_key=body.recovery_key.get_secret_value(),
                new_pin=body.new_pin.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/pin/upgrade", response_model=OperationResponse)
    def upgrade_legacy_to_pin(
        request: Request,
        body: PinUpgradeRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).upgrade_legacy_to_pin(
                token=_token(session_token),
                current_password=body.current_password.get_secret_value(),
                new_pin=body.new_pin.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/pin/change", response_model=OperationResponse)
    def change_pin(
        request: Request,
        body: PinChangeRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).change_pin(
                token=_token(session_token),
                current_pin=body.current_pin.get_secret_value(),
                new_pin=body.new_pin.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @protected_router.post("/vault/lock", response_model=OperationResponse)
    def lock(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        _service(request).lock(session_token)
        return OperationResponse(completed=True, locked=True)

    @protected_router.post("/vault/touch", response_model=TouchResponse)
    def touch(
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
            lambda: _service(request).touch(token=_token(session_token))
        )

    @protected_router.post("/vault/recovery-key/acknowledge")
    def acknowledge_recovery_key(
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
            lambda: _service(request).acknowledge_recovery_key(
                token=_token(session_token),
            )
        )

    @protected_router.get("/protected-work/{projection_id}")
    def resolve_protected_work(
        projection_id: str,
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).projections.resolve(
                token=_token(session_token),
                projection_id=projection_id,
            )
        )

    @protected_router.post("/vault/change-password", response_model=OperationResponse)
    def change_password(
        request: Request,
        body: ChangePasswordRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        _call(
            lambda: _service(request).change_password(
                token=_token(session_token),
                current_password=body.current_password.get_secret_value(),
                new_password=body.new_password.get_secret_value(),
                operation_id=body.operation_id,
            )
        )
        return OperationResponse(completed=True, locked=True)

    from .action_router import create_action_router
    from .planning_router import create_planning_router
    from .sop_router import create_sop_router
    from .collection_router import create_collection_router
    from .support_router import create_support_router
    from .work_router import create_work_router
    from .intake_router import create_intake_router

    protected_router.include_router(create_action_router())
    protected_router.include_router(create_planning_router())
    protected_router.include_router(create_sop_router())
    protected_router.include_router(create_collection_router())
    protected_router.include_router(create_support_router())
    protected_router.include_router(create_intake_router())
    router.include_router(protected_router)
    router.include_router(create_work_router())
    return router


__all__ = ["create_router"]
