from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response

from ..errors import VaultError
from ..vault_service import VaultService


_CLIENT_HEADER = "class-teacher-browser-v1"
# 内部档案编号（uuid hex）只作兼容输入识别；对外学生编号统一为稳定学籍标识。
_INTERNAL_SUBJECT_ID = re.compile(r"[0-9a-f]{32}")


def _student_subject_id(service: VaultService, student_ref: str) -> str:
    """对外学生标识统一为稳定学籍标识「班级|学号」（student_stable_ref）。
    入口统一解析为内部 subject_id 后走既有逻辑；旧 uuid 档案编号按兼容输入接受。
    解析失败沿用「学生引用已失效」风格。"""
    text = str(student_ref or "").strip()
    if _INTERNAL_SUBJECT_ID.fullmatch(text):
        return text
    linked = service.support.subject_id_for_ref(student_ref=text)
    if linked is not None:
        return linked
    identity = service.class_roster.roster_identity_for_ref(roster_ref=text)
    if identity is None:
        raise VaultError(
            "class_teacher_subject_ref_invalid",
            "学生引用已失效，请重新选择",
            status_code=409,
        )
    raise VaultError(
        "support_subject_not_found", "学生档案不存在", status_code=404
    )


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


def _require_runtime_compatible(request: Request) -> None:
    _call(lambda: _service(request).require_runtime_compatible())


def create_router() -> APIRouter:
    router = APIRouter(
        tags=["class-teacher"],
        dependencies=[Depends(_require_runtime_compatible)],
    )

    @router.get("/protected-work/{projection_id}")
    def resolve_protected_work(
        projection_id: str,
        request: Request,
        response: Response,
    ):
        _no_store(response)
        return _call(
            lambda: _service(request).projections.resolve(
                token="",
                projection_id=projection_id,
            )
        )

    from .sop_router import create_sop_router
    from .support_router import create_support_router
    from .work_router import create_work_router
    from .intake_router import create_intake_router
    from .daily_router import create_daily_router
    from .daily_table_router import create_daily_table_router

    router.include_router(create_sop_router())
    router.include_router(create_support_router())
    router.include_router(create_intake_router())
    router.include_router(create_work_router())
    router.include_router(create_daily_router())
    router.include_router(create_daily_table_router())
    return router


__all__ = ["create_router"]
