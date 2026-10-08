from __future__ import annotations

import argparse
import copy
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable
from typing import Any

from backend.api.frontend import FrontendDistributionError, validate_frontend_dist
from backend.schema_migrations import (
    SchemaMigrationRequired,
    SchemaVersionError,
    ensure_application_schema,
)
from path_manager import get_path_manager

LOOPBACK_HOST = "127.0.0.1"


def _port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def open_browser_when_started(
    server: Any,
    url: str,
    *,
    browser_open: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    timeout_seconds: float = 30.0,
) -> bool:
    deadline = monotonic() + max(0.0, float(timeout_seconds))
    while not bool(getattr(server, "started", False)):
        if bool(getattr(server, "should_exit", False)) or monotonic() >= deadline:
            print(
                f"浏览器未自动打开，请在服务启动后手动访问 {url}",
                file=sys.stderr,
            )
            return False
        sleep(0.05)
    try:
        opened = bool(browser_open(url))
    except OSError:
        opened = False
    if not opened:
        print(f"浏览器未自动打开，请手动访问 {url}", file=sys.stderr)
    return opened


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 AI 阅卷系统 Vue/FastAPI 入口")
    parser.add_argument("--host", choices=(LOOPBACK_HOST,), default=LOOPBACK_HOST)
    parser.add_argument("--port", type=_port, default=8000)
    parser.add_argument("--check-frontend", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    paths = get_path_manager()
    try:
        validate_frontend_dist(paths.project_root / "frontend" / "dist")
    except FrontendDistributionError:
        print(
            "前端文件缺失：请重新解压完整工作机包，"
            "确认 frontend/dist 目录完整后重试。",
            file=sys.stderr,
        )
        return 2
    if args.check_frontend:
        print("前端文件检查通过。")
        return 0
    try:
        ensure_application_schema(paths)
    except SchemaMigrationRequired as exc:
        print(
            "检测到已有数据库需要升级；普通启动没有修改数据。"
            "请由维护人员通过受保护维护入口确认数据库迁移后再重试。"
            f"维护目标：{exc.target}",
            file=sys.stderr,
        )
        return 3
    except SchemaVersionError:
        print(
            "数据库版本与当前程序不兼容，系统为保护数据已停止启动。"
            "请保留当前数据和备份，交由维护人员处理后再重试。",
            file=sys.stderr,
        )
        return 3

    import uvicorn

    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["ai_grading.api"] = {
        "handlers": ["default"], "level": "INFO", "propagate": False,
    }
    url = f"http://{args.host}:{args.port}/"
    server = uvicorn.Server(
        uvicorn.Config(
            "backend.api.app:app",
            host=args.host,
            port=args.port,
            log_config=log_config,
        )
    )
    browser_thread = None
    if not args.no_browser:
        browser_thread = threading.Thread(
            target=open_browser_when_started,
            args=(server, url),
            daemon=True,
            name="open-ai-grading-browser",
        )
        browser_thread.start()
    server.run()
    if browser_thread is not None:
        browser_thread.join(timeout=0.2)
    if not bool(getattr(server, "started", False)):
        print(
            "FastAPI 未能成功监听，请确认端口未被占用。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
