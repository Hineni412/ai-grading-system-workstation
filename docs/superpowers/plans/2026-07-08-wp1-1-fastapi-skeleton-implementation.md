# WP1.1 FastAPI 骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个最小 FastAPI API 层骨架，提供统一错误体、请求日志、`/healthz`，并让 `运行.bat` 默认同时启动 Streamlit 8501 与 API 8000。

**Architecture:** 本包只做增量 API 外壳，不迁移现有 Streamlit 页面和业务服务。`backend/api/app.py` 暴露 `create_app()` 与模块级 `app` 给 uvicorn 使用；后续 WP1.2 的路由都挂到这个应用上。启动脚本仍以前台 Streamlit 为主，API 在独立窗口/进程中运行，绑定 `127.0.0.1`。

**Tech Stack:** FastAPI 0.139.0、Pydantic 2.13.4、Starlette 1.3.1、Uvicorn 0.49.0、pytest。

**当前状态（2026-07-08，Codex 接续）：**

- Task 1-4 已完成：FastAPI 0.139.0 已安装到便携运行时并写入 `requirements.txt` / `constraints.txt`；`backend/api/app.py`、`运行.bat` 双入口和对应测试已新增。
- WP1.1 定向测试已通过：`runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_run_bat_api_entry.py -q`，结果 5 passed。
- 完整冒烟已通过：`runtime\python\python.exe tools\smoke_check.py`，结果为静态编译 OK、全量测试 724 passed / 2 skipped、两库副本初始化幂等 OK。
- 提交步骤暂不执行，需先决定 WP0.4/WP0.5/WP1.1 与 `user_data` 变更的提交边界。

## Global Constraints

- 绑定地址必须是 `127.0.0.1`，不引入局域网/公网访问。
- Phase 1 不迁移、不重写现有 `grading_service.py`、`session_manager.py`、`question_bank/`；API 层只做薄壳。
- 新增 API 必须可删除回退：移除 `backend/`、相关测试和 `运行.bat` 启动段即可回到现状。
- 后续每个 WP 必跑 `runtime\python\python.exe tools\smoke_check.py`。
- 默认不要提交 `user_data/` 数据库、图片、导出文件或备份。

---

## File Structure

- Create: `backend/__init__.py`：标记后端包。
- Create: `backend/api/__init__.py`：导出 `create_app` 与 `app`。
- Create: `backend/api/app.py`：FastAPI 实例、请求 ID 中间件、错误处理、`/healthz`。
- Create: `tests/test_api_app.py`：API app 契约测试。
- Create: `tests/test_run_bat_api_entry.py`：启动脚本双入口文本契约测试。
- Modify: `requirements.txt`：新增运行依赖 `fastapi>=0.139.0`。
- Modify: `constraints.txt`：新增 `annotated-doc==0.0.4` 与 `fastapi==0.139.0`。
- Modify: `运行.bat`：默认启动 API 端口 8000，同时保留 Streamlit 端口 8501。
- Modify: `ARCHITECTURE.md`、`AGENTS.md`：记录 WP1.1 增量 API 骨架和启动方式。

## Tasks

### Task 1: FastAPI 依赖声明

**Files:**
- Modify: `requirements.txt`
- Modify: `constraints.txt`
- Test: `tests/test_api_app.py`

**Interfaces:**
- Produces: `import fastapi` 可用；后续任务可以从 `fastapi`, `fastapi.testclient`, `pydantic` 导入。

- [x] **Step 1: 写失败测试**

Add to `tests/test_api_app.py`:

```python
from __future__ import annotations


def test_fastapi_runtime_dependency_is_available() -> None:
    import fastapi

    assert fastapi.__version__ == "0.139.0"
```

- [x] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py::test_fastapi_runtime_dependency_is_available -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'fastapi'`.

- [x] **Step 3: 安装并声明依赖**

Run:

```powershell
runtime\python\python.exe -m pip install "fastapi==0.139.0" -c constraints.txt
```

Edit `requirements.txt` and add:

```text
fastapi>=0.139.0
```

Edit `constraints.txt` and add these sorted entries:

```text
annotated-doc==0.0.4
fastapi==0.139.0
```

- [x] **Step 4: 验证依赖测试通过**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py::test_fastapi_runtime_dependency_is_available -q`

Expected: `1 passed`.

### Task 2: API App 骨架与契约

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/api/__init__.py`
- Create: `backend/api/app.py`
- Modify: `tests/test_api_app.py`

**Interfaces:**
- Consumes: FastAPI dependency from Task 1.
- Produces: `backend.api.app.create_app() -> fastapi.FastAPI`; module variable `backend.api.app.app`; `ApiError(status_code: int, code: str, message: str, details: dict | None = None)`.

- [x] **Step 1: 写失败测试**

Append to `tests/test_api_app.py`:

```python
from fastapi.testclient import TestClient


def test_healthz_returns_local_api_status() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.json() == {
        "status": "ok",
        "service": "ai-grading-api",
        "version": "v1.5.0",
    }


def test_api_healthz_alias_matches_root_healthz() -> None:
    from backend.api.app import create_app

    client = TestClient(create_app())

    assert client.get("/api/healthz").json() == client.get("/healthz").json()


def test_api_error_uses_unified_error_body() -> None:
    from backend.api.app import ApiError, create_app

    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise ApiError(409, "demo_conflict", "演示冲突", {"field": "name"})

    client = TestClient(app)

    response = client.get("/boom", headers={"x-request-id": "rid-test"})

    assert response.status_code == 409
    assert response.headers["x-request-id"] == "rid-test"
    assert response.json() == {
        "error": {
            "code": "demo_conflict",
            "message": "演示冲突",
            "details": {"field": "name"},
            "request_id": "rid-test",
        }
    }
```

- [x] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py -q`

Expected: dependency test passes, new API tests fail with `ModuleNotFoundError: No module named 'backend'`.

- [x] **Step 3: 写最小实现**

Create `backend/__init__.py`:

```python
"""Backend package for the incremental FastAPI API layer."""
```

Create `backend/api/__init__.py`:

```python
from .app import app, create_app

__all__ = ["app", "create_app"]
```

Create `backend/api/app.py`:

```python
from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from path_manager import get_path_manager


LOGGER = logging.getLogger("ai_grading.api")


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ai-grading-api"
    version: str


class ErrorPayload(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorPayload


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def create_app() -> FastAPI:
    api = FastAPI(
        title="AI 阅卷系统 API",
        version=get_path_manager().version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @api.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            LOGGER.info(
                "api_request method=%s path=%s request_id=%s elapsed_ms=%.1f",
                request.method,
                request.url.path,
                request_id,
                elapsed_ms,
            )
        response.headers["x-request-id"] = request_id
        return response

    @api.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "") or uuid4().hex
        return JSONResponse(
            status_code=exc.status_code,
            headers={"x-request-id": request_id},
            content=ErrorResponse(
                error=ErrorPayload(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                    request_id=request_id,
                )
            ).model_dump(),
        )

    @api.get("/healthz", response_model=HealthResponse)
    @api.get("/api/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(version=get_path_manager().version)

    return api


app = create_app()
```

- [x] **Step 4: 验证 API 测试通过**

Run: `runtime\python\python.exe -m pytest tests\test_api_app.py -q`

Expected: all tests in `tests/test_api_app.py` pass.

### Task 3: `运行.bat` 双入口

**Files:**
- Modify: `运行.bat`
- Create: `tests/test_run_bat_api_entry.py`

**Interfaces:**
- Consumes: `backend.api.app:app` from Task 2.
- Produces: default `API_PORT=8000`; optional `START_API=0` disables API; Streamlit remains foreground on `PORT=8501`.

- [x] **Step 1: 写失败测试**

Create `tests/test_run_bat_api_entry.py`:

```python
from __future__ import annotations

from pathlib import Path


def test_run_bat_starts_api_and_streamlit_by_default() -> None:
    content = Path("运行.bat").read_text(encoding="utf-8")

    assert 'if "%API_PORT%"=="" set "API_PORT=8000"' in content
    assert 'if "%START_API%"=="" set "START_API=1"' in content
    assert 'backend.api.app:app --host 127.0.0.1 --port %API_PORT%' in content
    assert 'if /I not "%START_API%"=="0"' in content
    assert 'streamlit run web_app.py --server.address 127.0.0.1 --server.port %PORT%' in content
```

- [x] **Step 2: 运行测试确认失败**

Run: `runtime\python\python.exe -m pytest tests\test_run_bat_api_entry.py -q`

Expected: FAIL because `API_PORT` and uvicorn command are not present.

- [x] **Step 3: 修改 `运行.bat`**

Insert after the existing `if "%PORT%"=="" set "PORT=8501"`:

```bat
if "%API_PORT%"=="" set "API_PORT=8000"
if "%START_API%"=="" set "START_API=1"
```

Replace the startup echo/browser block with:

```bat
echo AI阅卷系统 工作机版 v1.5.0
echo 数据目录: %AI_GRADING_DATA_DIR%
echo Streamlit 地址: http://127.0.0.1:%PORT%
if /I not "%START_API%"=="0" (
  echo API 地址: http://127.0.0.1:%API_PORT%/healthz
  start "AI阅卷系统 API" "%PYTHON_EXE%" -m uvicorn backend.api.app:app --host 127.0.0.1 --port %API_PORT%
) else (
  echo API 启动: 已跳过 START_API=0
)
start "" "http://127.0.0.1:%PORT%"
```

Keep the Streamlit foreground command unchanged.

- [x] **Step 4: 验证启动脚本契约测试通过**

Run: `runtime\python\python.exe -m pytest tests\test_run_bat_api_entry.py -q`

Expected: `1 passed`.

### Task 4: 文档与回归验收

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/plans/2026-07-08-wp1-1-fastapi-skeleton-implementation.md`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: handoff docs that identify WP1.1 status and the new smoke command remains the regression gate.

- [x] **Step 1: 更新文档**

Update `AGENTS.md` current progress with:

```markdown
- WP1.1 已开始/完成（按实际执行状态更新）：新增 FastAPI API 骨架 `backend/api/app.py`，`运行.bat` 默认同时启动 Streamlit 8501 与 API 8000；API 当前只有 `/healthz` 与统一错误体，业务路由留给 WP1.2。
```

Update `ARCHITECTURE.md`:

- In the development view, add `backend/api/app.py` as the incremental API entry.
- In the startup section, note `运行.bat` starts API on `127.0.0.1:8000` when `START_API` is not `0`.
- In the interface table, add FastAPI local API as an incremental interface with only `/healthz` in WP1.1.

- [x] **Step 2: 跑 WP1.1 定向测试**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_run_bat_api_entry.py -q
```

Expected: all tests pass.

- [x] **Step 3: 跑完整冒烟**

Run:

```powershell
runtime\python\python.exe tools\smoke_check.py
```

Expected: static compile OK, full pytest OK, database copy idempotency OK.

- [x] **Step 4: 更新本计划状态**

Record the exact smoke result in the top status block and check completed boxes. Leave commit unchecked unless the user explicitly asks to commit.

### Task 5: 提交（用户确认后执行）

**Files:**
- Stage only WP1.1 code, tests, docs, dependency files, and plan updates.
- Do not stage `user_data/` unless the user explicitly requests it.

**Interfaces:**
- Produces: one reviewable commit, for example `feat: add FastAPI API skeleton`.

- [ ] **Step 1: 检查提交范围**

Run: `git status --short`

Expected: WP0.4/WP0.5/WP1.1 code/docs are visible; `user_data/` remains excluded from staging by default.

- [ ] **Step 2: 提交**

Run:

```powershell
git add AGENTS.md ARCHITECTURE.md requirements.txt constraints.txt backend tests/test_api_app.py tests/test_run_bat_api_entry.py "运行.bat" docs/superpowers/plans/2026-07-08-wp1-1-fastapi-skeleton-implementation.md
git commit -m "feat: add FastAPI API skeleton"
```

Expected: commit succeeds. If WP0.4/WP0.5 are still uncommitted, split commits intentionally instead of bundling unrelated phases.

## Self-Review

- Spec coverage: covers WP1.1 FastAPI skeleton, unified error body, request logging, `/healthz`, and dual startup entry.
- Known deferral: no business API routes; those belong to WP1.2.
- Dependency risk: FastAPI is not currently installed; Task 1 resolves it and records exact versions.
- Rollback: remove `backend/`, two new tests, dependency lines, and the `运行.bat` API block.
