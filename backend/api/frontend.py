from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles


ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
INDEX_CACHE_CONTROL = "no-store"


class FrontendDistributionError(RuntimeError):
    pass


def validate_frontend_dist(dist_dir: Path | str) -> Path:
    dist = Path(dist_dir)
    assets = dist / "assets"
    if (
        not (dist / "index.html").is_file()
        or not assets.is_dir()
        or not any(path.is_file() for path in assets.rglob("*"))
    ):
        raise FrontendDistributionError(
            "frontend/dist is incomplete; rebuild or restore the packaged frontend"
        )
    return dist


class ImmutableAssetFiles(StaticFiles):
    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        if response.status_code == 200:
            response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        return response


def _missing_frontend_response() -> HTMLResponse:
    return HTMLResponse(
        status_code=503,
        headers={"Cache-Control": INDEX_CACHE_CONTROL},
        content=(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>AI阅卷系统暂时无法启动</title>"
            "<main><h1>前端文件缺失</h1>"
            "<p>请重新解压完整工作机包，确认 frontend/dist 目录完整后重试。</p>"
            "</main></html>"
        ),
    )


def mount_frontend(app: FastAPI, dist_dir: Path | str) -> None:
    try:
        dist = validate_frontend_dist(dist_dir)
    except FrontendDistributionError:
        dist = None

    if dist is not None:
        app.mount(
            "/assets",
            ImmutableAssetFiles(directory=dist / "assets"),
            name="frontend-assets",
        )

    @app.middleware("http")
    async def serve_frontend_fallback(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if request.method not in {"GET", "HEAD"} or response.status_code != 404:
            return response
        path = request.url.path
        if path == "/api" or path.startswith(("/api/", "/assets/")):
            return response
        route = request.scope.get("route")
        if isinstance(getattr(route, "path", None), str):
            return response
        if dist is None:
            return _missing_frontend_response()
        return FileResponse(
            dist / "index.html",
            media_type="text/html",
            headers={"Cache-Control": INDEX_CACHE_CONTROL},
        )


__all__ = [
    "ASSET_CACHE_CONTROL",
    "FrontendDistributionError",
    "INDEX_CACHE_CONTROL",
    "mount_frontend",
    "validate_frontend_dist",
]
