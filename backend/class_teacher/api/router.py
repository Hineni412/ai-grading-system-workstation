from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Response

from ..errors import VaultError
from ..vault_service import VaultService
from .schemas import (
    BackupCreateRequest,
    BackupCreateResponse,
    BackupListResponse,
    BackupSecretRequest,
    BackupSummaryResponse,
    ChangePasswordRequest,
    InitializeRequest,
    InitializeResponse,
    OperationResponse,
    RecoverRequest,
    RestoreConfirmRequest,
    RestoreConfirmResponse,
    RestorePreviewResponse,
    SessionResponse,
    TouchResponse,
    UnlockRequest,
    VaultStatusResponse,
)


_CLIENT_HEADER = "class-teacher-browser-v1"
_RESTORE_PHRASE = "确认恢复班主任工作台"


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
    router = APIRouter(
        tags=["class-teacher"],
        dependencies=[Depends(_serialized_operation)],
    )

    @router.get("/vault/status", response_model=VaultStatusResponse)
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

    @router.post("/vault/initialize", response_model=InitializeResponse)
    def initialize(request: Request, body: InitializeRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).initialize(
                password=body.password.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @router.post("/vault/unlock", response_model=SessionResponse)
    def unlock(request: Request, body: UnlockRequest, response: Response):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).unlock(
                password=body.password.get_secret_value(),
            )
        )

    @router.post("/vault/recover", response_model=SessionResponse)
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

    @router.post("/vault/lock", response_model=OperationResponse)
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

    @router.post("/vault/touch", response_model=TouchResponse)
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

    @router.post("/vault/recovery-key/acknowledge")
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

    @router.post("/vault/change-password", response_model=OperationResponse)
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

    @router.post("/vault/backups", response_model=BackupCreateResponse)
    def create_backup(
        request: Request,
        body: BackupCreateRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).create_backup(
                token=_token(session_token),
                backup_password=body.backup_password.get_secret_value(),
                operation_id=body.operation_id,
            )
        )

    @router.get("/vault/backups", response_model=BackupListResponse)
    def list_backups(
        request: Request,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).list_backups(
                token=_token(session_token),
            )
        )

    @router.post("/vault/backups/verify", response_model=BackupSummaryResponse)
    def verify_backup(
        request: Request,
        body: BackupSecretRequest,
        response: Response,
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).verify_backup(
                file_name=body.file_name,
                secret=body.secret.get_secret_value(),
                secret_kind=body.secret_kind,
            )
        )

    @router.post("/vault/restore/preview", response_model=RestorePreviewResponse)
    def preview_restore(
        request: Request,
        body: BackupSecretRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        return _call(
            lambda: _service(request).preview_restore(
                token=_token(session_token),
                file_name=body.file_name,
                secret=body.secret.get_secret_value(),
                secret_kind=body.secret_kind,
            )
        )

    @router.post("/vault/restore/confirm", response_model=RestoreConfirmResponse)
    def confirm_restore(
        request: Request,
        body: RestoreConfirmRequest,
        response: Response,
        session_token: str | None = Header(
            default=None,
            alias="x-class-teacher-session",
        ),
    ):
        _require_trusted_mutation(request)
        _no_store(response)
        if body.confirmation_phrase != _RESTORE_PHRASE:
            raise _api_error(
                422,
                "vault_restore_confirmation_required",
                f"请输入“{_RESTORE_PHRASE}”后再恢复",
            )
        return _call(
            lambda: _service(request).confirm_restore(
                token=_token(session_token),
                preview_token=body.preview_token,
                operation_id=body.operation_id,
            )
        )

    from .action_router import create_action_router
    from .planning_router import create_planning_router
    from .sop_router import create_sop_router
    from .collection_router import create_collection_router
    from .support_router import create_support_router

    router.include_router(create_action_router())
    router.include_router(create_planning_router())
    router.include_router(create_sop_router())
    router.include_router(create_collection_router())
    router.include_router(create_support_router())
    return router


__all__ = ["create_router"]
