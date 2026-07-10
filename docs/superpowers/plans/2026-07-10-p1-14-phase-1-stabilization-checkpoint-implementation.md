# P1-14 Phase 1 Stabilization Checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前未提交的 WP1.2、最小 WP1.3 与 P1-09 至 P1-13 增量整理成经过完整审查、OpenAPI 契约一致、范围清晰且可由用户逐项审阅的本地检查点。

**Architecture:** P1-14 不增加业务能力，只修复集成审计确认的检查点缺陷：忽略本地 `.superpowers/` 运行产物；让 OpenAPI 的 422 模型与运行时统一错误体一致；阻止通用 Job payload 写入敏感键，并在公开 JobResponse 中递归隐藏路径/敏感字段。其余工作是只读审查、分层验证、独立复审和状态文档收口。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite、pytest、PowerShell、Git、现有 `tools/smoke_check.py`。

## Global Constraints

- 不新增领域路由、Job 类型、数据库表/列、评分规则、题号口径、知识点语义或状态含义。
- 不改变 Streamlit 工作流；只稳定当前 FastAPI/JobManager 增量及其文档、测试和仓库范围。
- 除下述已接受例外外，不读取、写入、删除、恢复、暂存或提交真实 `user_data/`；数据库验证只使用工具创建的临时副本。
- 开工基线固定为分支 `codex/wp1-2-api-routes`、HEAD/origin-main `d80d512b3230e9e91b0bec7bdaf66c4efa2dba2a`、暂存区 0 文件。
- `user_data` 状态清单继续使用与 P1-13 相同的默认 `git status --short -- user_data | Sort-Object` 口径：205 行，SHA-256 `b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`；结束时必须完全一致。
- 已接受例外：2026-07-10 一次未完整 override lifespan 的只读诊断意外让 `JobStore.initialize()` 在真实 `user_data/databases/grading_system.db` 创建了空 `jobs` 表及 `idx_jobs_status_created`、`idx_jobs_type_created` 两个索引，`jobs` 行数为 0。用户明确要求保留并继续；接受后的内容基线为 2,863,104 bytes、SHA-256 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`。从此点起不得再发生任何 `user_data` 写入。
- 只读文件守卫同时固定 `question_bank.db` 为 3,461,120 bytes、SHA-256 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`；最终必须与 grading DB 一起逐字节指纹一致。读取文件用于哈希或复制临时验证是允许的，禁止的是对源文件或其 sidecar 的写连接与替换。
- 当前普通 checkout 上的未提交改动就是 P1-14 审查对象，不创建新 worktree，不覆盖或回退用户/前序包改动。
- `.superpowers/` 是本地工具运行状态和生成内容，不删除，只通过 `.gitignore` 排除；`user_data/` 仍按用户既有决定保持跟踪，不能新增忽略规则掩盖真实数据变化。
- 任何 API 错误响应、公开 Job payload/result/error 都不得回显 API key、密码、授权头、内部绝对路径或底层异常文本。
- P1-14 可新增/强化契约测试和修复审计缺陷，但不得借机实现 P1-15 之后的功能。
- 未经用户另行确认，不 stage、不 commit、不 push、不创建 PR；计划中的 Git 工作只生成范围清单、建议提交信息和 PR 摘要。

## Investigation Baseline

- OpenAPI 当前为 25 paths / 33 operations，无重复 `operationId`；现有 422 自动文档仍引用 FastAPI `HTTPValidationError`，与运行时 `ErrorResponse` 不一致。
- `POST /api/jobs/{job_type}` 当前接受任意 `payload`；注册 handler 前没有递归敏感键检查，公开 `JobResponse.payload` 也未经路径/敏感字段脱敏。
- 非 `user_data` 状态共 140 行，其中 `.superpowers/` 41 个未跟踪运行产物；`.gitignore` 当前未覆盖该目录。
- `migrations/grading/000_baseline_schema.sql` 的工作树 hash 与索引 blob hash 都是 `d2237dda5e11ae67b1da176658c1f1a720fe5612`，属于 Git stat/换行状态噪声，不是内容差异；不得把它描述成 Schema 修改。
- 文档漂移：`AGENTS.md`/`EXECUTION_INDEX.md` 把下一包写成配置生成；权威 Phase 1 地图定义 P1-15 为 Question Bank 只读路由，配置生成 Job 是 P1-17。
- P1-14 整包复审没有 Critical，确认 7 个 Important：Job 公开 result/键名变体脱敏、历史复核元数据脱敏、config/template 错误路径脱敏、二进制 OpenAPI 媒体类型、Job future 回收、并发报告文件名冲突，以及状态文档漂移；另有 1 个 Minor 是符号链接测试在无特权环境静默返回。文档漂移在 Task 6 收口，其余项在 Task 4 以 TDD 修复。

---

### Task 1: 仓库范围卫生与基线守卫

**Files:**
- Modify: `.gitignore`
- Inspect only: `.superpowers/`
- Inspect only: `user_data/`

**Interfaces:**
- Consumes: 当前 Git status、P1-13 `user_data` 指纹。
- Produces: `.superpowers/` 被忽略；不删除本地运行产物；最终状态清单不再把它们列入候选提交。

- [x] **Step 1: 确认 `.superpowers/` 未被忽略（RED）**

```powershell
git check-ignore -v .superpowers/brainstorm/.last-port
```

Expected: exit 1，输出为空。

- [x] **Step 2: 在 `.gitignore` 增加本地工具状态规则**

在 “OS/editor noise” 前新增：

```gitignore
# Local agent/tool session state
.superpowers/
```

- [x] **Step 3: 验证规则生效且没有删除任何文件（GREEN）**

```powershell
git check-ignore -v .superpowers/brainstorm/.last-port
Test-Path .superpowers/brainstorm/.last-port
git status --short -- .superpowers .gitignore
```

Expected: `git check-ignore` 指向 `.gitignore` 新规则；文件仍存在；status 只显示 `.gitignore` 修改，不再展开 `.superpowers/`。

- [x] **Step 4: 记录准确范围，不刷新或暂存真实数据**

```powershell
git diff --name-status
git ls-files --others --exclude-standard
git diff --cached --name-only
```

Expected: 暂存区为空；候选范围只含源码、测试、迁移、工具和文档；`user_data/` 仍显示既有真实变化但明确排除。

---

### Task 2: OpenAPI 检查点契约与统一 422 文档

**Files:**
- Create: `tests/test_api_openapi_contract.py`
- Modify: `backend/api/app.py`

**Interfaces:**
- Consumes: `create_app() -> FastAPI`、运行时 `ErrorResponse`、当前 33 个 Phase 1 operations。
- Produces: 所有 OpenAPI 422 响应引用 `#/components/schemas/ErrorResponse`；当前路由存在性、operationId 唯一性和二进制路由无路径参数由测试守卫。

- [x] **Step 1: 写 OpenAPI RED 契约测试**

创建 `tests/test_api_openapi_contract.py`：

```python
from __future__ import annotations

from collections import Counter


EXPECTED_OPERATIONS = {
    ("GET", "/healthz"),
    ("GET", "/api/healthz"),
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/{session_id}"),
    ("PATCH", "/api/sessions/{session_id}"),
    ("DELETE", "/api/sessions/{session_id}"),
    ("POST", "/api/sessions/{session_id}/restore"),
    ("GET", "/api/students"),
    ("POST", "/api/students"),
    ("PATCH", "/api/students/{student_id}"),
    ("DELETE", "/api/students/{student_id}"),
    ("GET", "/api/sessions/{session_id}/config"),
    ("PUT", "/api/sessions/{session_id}/config"),
    ("GET", "/api/sessions/{session_id}/template"),
    ("PUT", "/api/sessions/{session_id}/template"),
    ("GET", "/api/sessions/{session_id}/regions"),
    ("GET", "/api/sessions/{session_id}/regions/draft"),
    ("PUT", "/api/sessions/{session_id}/regions/draft"),
    ("POST", "/api/sessions/{session_id}/regions/commit"),
    ("GET", "/api/sessions/{session_id}/progress"),
    ("POST", "/api/jobs/{job_type}"),
    ("GET", "/api/jobs/{job_id}"),
    ("POST", "/api/jobs/{job_id}/cancel"),
    ("POST", "/api/sessions/{session_id}/reports/export"),
    ("POST", "/api/sessions/{session_id}/scan/analyze"),
    ("POST", "/api/sessions/{session_id}/grading/run"),
    ("GET", "/api/sessions/{session_id}/review/questions"),
    ("GET", "/api/sessions/{session_id}/review/questions/{question_id}/items"),
    ("POST", "/api/sessions/{session_id}/review/questions/{question_id}/confirm"),
    ("GET", "/api/sessions/{session_id}/results/{result_id}/pages/{page}"),
    ("GET", "/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop"),
    ("GET", "/api/jobs/{job_id}/download"),
}


def _operations(schema: dict) -> list[tuple[str, str, dict]]:
    operations = []
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                operations.append((method.upper(), path, operation))
    return operations


def test_openapi_covers_current_phase1_routes_with_unique_operation_ids() -> None:
    from backend.api.app import create_app

    operations = _operations(create_app().openapi())
    assert EXPECTED_OPERATIONS <= {(method, path) for method, path, _ in operations}
    operation_ids = [operation["operationId"] for _, _, operation in operations]
    assert not [key for key, count in Counter(operation_ids).items() if count > 1]


def test_openapi_422_responses_match_unified_runtime_error_shape() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    operations = _operations(schema)
    assert "ErrorResponse" in schema["components"]["schemas"]
    for _, _, operation in operations:
        response = operation.get("responses", {}).get("422")
        if response is None:
            continue
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_binary_routes_do_not_accept_client_file_paths() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    for path in (
        "/api/jobs/{job_id}/download",
        "/api/sessions/{session_id}/results/{result_id}/pages/{page}",
        "/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop",
    ):
        parameters = schema["paths"][path]["get"].get("parameters", [])
        assert not {
            parameter["name"]
            for parameter in parameters
            if parameter["name"] in {"path", "file", "file_path", "filename"}
        }
```

- [x] **Step 2: 运行测试确认 422 OpenAPI 契约为 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py -q
```

Expected: 路由/operationId/二进制参数断言通过；422 `$ref` 因仍为 `HTTPValidationError` 而失败。

- [x] **Step 3: 用 FastAPI 全局响应模型对齐运行时错误体**

在 `create_app()` 的 `FastAPI(...)` 参数中加入：

```python
responses={
    422: {
        "model": ErrorResponse,
        "description": "Invalid request",
    }
},
```

不改变 `RequestValidationError` handler 的运行时状态码、错误 code、details 或 `x-request-id`。

- [x] **Step 4: 运行 OpenAPI 与错误体回归确认 GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py tests\test_api_app.py tests\test_api_write_routes.py tests\test_api_media_routes.py -q
```

Expected: 全部通过；OpenAPI 422 与实际 `ErrorResponse` 一致。

---

### Task 3: Job payload 敏感键拒绝与公开脱敏

**Files:**
- Modify: `backend/api/routers/jobs.py`
- Modify: `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `JobSubmitRequest.payload`、`JobRecord.payload`、已有 `_without_internal_paths()`。
- Produces: `public_job_payload(job) -> dict[str, Any]`；递归敏感键检测；API 在创建 Job 前返回 422 `unsafe_job_payload`。

- [x] **Step 1: 写敏感 payload 不落库 RED 测试**

在 `tests/test_api_jobs.py` 增加：

```python
def test_jobs_api_rejects_nested_sensitive_payload_before_persistence(
    client_with_manager,
) -> None:
    import sqlite3

    client, manager = client_with_manager
    manager.register("report_export", lambda _context: {})

    response = client.post(
        "/api/jobs/report_export",
        json={"payload": {"session_id": 8, "config": {"api_key": "secret-value"}}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_job_payload"
    assert "secret-value" not in response.text
    with sqlite3.connect(manager.store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
```

- [x] **Step 2: 写公开 payload 路径/旧敏感字段脱敏 RED 测试**

```python
def test_jobs_api_redacts_internal_paths_and_sensitive_keys_from_public_payload(
    client_with_manager,
) -> None:
    client, manager = client_with_manager
    manager.register("report_export", lambda _context: {})

    created = client.post(
        "/api/jobs/report_export",
        json={
            "payload": {
                "session_id": 8,
                "artifact_path": "C:/private/report.xlsx",
                "nested": {"summary": {"count": 1}, "paths": ["C:/private"]},
            }
        },
    ).json()

    assert created["payload"] == {
        "session_id": 8,
        "nested": {"summary": {"count": 1}},
    }
    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["payload"] == created["payload"]
    assert "C:/private" not in str(loaded)
```

再用 `manager.store.create_job()` 插入一条模拟旧记录，保证旧数据只是不回显，不对真实库做清理或迁移：

```python
def test_job_response_redacts_sensitive_keys_from_legacy_stored_payload(
    client_with_manager,
) -> None:
    from backend.api.routers.jobs import _job_response

    _client, manager = client_with_manager
    legacy = manager.store.create_job(
        "report_export",
        {
            "session_id": 8,
            "config_api_key": "old-secret",
            "nested": {"summary": {"count": 1}},
        },
    )

    payload = _job_response(legacy).model_dump()["payload"]
    assert payload == {
        "session_id": 8,
        "nested": {"summary": {"count": 1}},
    }
    assert "old-secret" not in str(payload)
```

- [x] **Step 3: 运行 Job API 测试确认 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_api_jobs.py -q
```

Expected: 新敏感键请求当前会创建 Job，公开 payload 当前保留路径，因此新增测试失败。

- [x] **Step 4: 实现递归敏感键检测与公开 payload 脱敏**

在 `backend/api/routers/jobs.py` 定义：

```python
SENSITIVE_JOB_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)


def _normalized_payload_key(value: object) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _is_sensitive_payload_key(value: object) -> bool:
    key = _normalized_payload_key(value)
    return (
        key in SENSITIVE_JOB_PAYLOAD_KEYS
        or key.endswith("_api_key")
        or key.endswith("_secret")
    )


def _contains_sensitive_payload_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _is_sensitive_payload_key(key)
            or _contains_sensitive_payload_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_payload_key(item) for item in value)
    return False


def _without_internal_payload_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_internal_payload_fields(item)
            for key, item in value.items()
            if not _is_internal_path_key(key)
            and not _is_sensitive_payload_key(key)
        }
    if isinstance(value, list):
        return [_without_internal_payload_fields(item) for item in value]
    return value


def public_job_payload(job: JobRecord) -> dict[str, Any]:
    return _without_internal_payload_fields(job.payload)
```

`_job_response()` 改用 `payload=public_job_payload(job)`；`submit_job()` 在 `manager.submit()` 前检查 `_contains_sensitive_payload_key(request.payload)`，命中时抛出：

```python
raise ApiError(
    422,
    "unsafe_job_payload",
    "Job payload contains fields that must not be persisted",
    {"job_type": str(job_type)},
)
```

错误 details 只含 job type，不含敏感键名、值或 payload。

- [x] **Step 5: 运行 Job/handler/API 回归确认 GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_api_jobs.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_job_manager.py tests\test_job_store.py -q
```

Expected: 全部通过；handler 内部仍读取未改写的 JobStore payload，公开响应只返回脱敏副本。

---

### Task 4: 全范围代码审查与独立复审

**Files:**
- Inspect: 当前所有 `backend/`、`db_manager.py`、`grading_service.py`、`manual_review_service.py`
- Inspect: `migrations/grading/003_add_jobs.sql`、Schema/迁移/冒烟工具
- Inspect: P1-02 至 P1-13 相关测试和即时计划
- Modify only if review proves a defect: exact affected production/test files

**Interfaces:**
- Consumes: HEAD `d80d512...` 加工作树/未跟踪改动、P1-14 计划和 Phase 1 包验收。
- Produces: Critical/Important 为零的独立复审结论；任何有效发现都有失败测试、修复和回归证据。

- [x] **Step 1: 做本地静态与范围审计**

```powershell
git diff --check
rg -n "TODO|FIXME|NotImplemented|placeholder" backend tests tools migrations
rg -n -g "*.py" "timeout\s*=\s*None" .
rg -n -g "*.py" "api[_-]?key|authorization|access[_-]?token|password|secret" backend/api backend/jobs
rg -n -g "*.py" "FileResponse|file_path|scan_analysis_path|download_url" backend/api backend/files
```

逐个判断命中是既有显式兼容、测试种子、内部存储还是公开泄露；不得仅凭 grep 自动改业务代码。

- [x] **Step 2: 核对迁移、运行时 Schema 与生命周期**

```powershell
runtime\python\python.exe -m pytest tests\test_schema_baseline.py tests\test_migration_rehearsal.py tests\test_migration_tooling.py tests\test_api_job_lifecycle.py -q
```

Expected: `003_add_jobs.sql` 仍是 jobs 唯一完整 DDL；临时副本迁移、lifespan 与连接关闭契约通过。

- [x] **Step 3: 按 requesting-code-review 模板派发只读独立复审**

Reviewer 必须阅读本计划、`git diff HEAD`、所有非忽略未跟踪源码/测试/迁移，重点检查：

1. Session/student/config/template API 是否保持旧服务事实来源。
2. Job schema、生命周期、并发关闭和协作式取消是否有竞态或副作用越界。
3. Review 单 JOIN、跨 result 事务、批注补偿是否保持原子性。
4. Media/download 的根/扩展名/所属关系/缓存与错误脱敏是否安全。
5. OpenAPI 与运行时契约、Job payload 敏感字段、提交范围和文档是否一致。

复审只读，不得修改 checkout、index、HEAD 或 branch。

- [x] **Step 4: 处理复审结果**

- Critical：立即停止阶段收口，使用 `superpowers:systematic-debugging` 和 TDD 修复。
- Important：P1-14 结束前修复并复跑相关回归。
- Minor：只在不扩散到 P1-15+ 且不改变业务语义时处理；否则记录到对应未来包。
- 对不成立的建议用源码/测试证据说明，不做迎合性修改。

修复附录（先 RED、后最小 GREEN）：

1. 提供无 FastAPI 依赖的共享公开数据净化器；键名以大小写折叠并移除非字母数字字符后识别 `api key`、`configApiKey`、`accessToken` 等变体，递归删除敏感键、路径键和任何键下的绝对路径值。
2. Job 提交前拒绝敏感键；公开 payload/result 复用净化器；报告成功结果继续使用精确允许列表。
3. 历史 review `detail_metadata` 只公开现有领域生产者实际写入的题号、证据步骤、缺失步骤和候选分数安全字段，不回显任意历史键。
4. config/template 错误 details 不返回存储路径或底层异常文本。
5. 文件下载、页面图和裁剪图的 OpenAPI 200 响应声明真实二进制媒体类型与 binary schema。
6. `JobManager` 在 future 完成时按 identity 从 `_futures` 回收；callback 必须在 manager 锁外注册，避免同步完成死锁。
7. 报告最终文件名包含 `job_id`，保证同会话并发任务各自发布独立文件。
8. 符号链接越界测试改为确定性的 `Path.resolve` 注入，不再因 Windows 创建符号链接失败而静默通过。

对应聚焦测试至少覆盖 `tests/test_api_jobs.py`、`tests/test_review_application_service.py`、`tests/test_api_review_routes.py`、`tests/test_api_config_routes.py`、`tests/test_api_template_region_routes.py`、`tests/test_api_openapi_contract.py`、`tests/test_job_manager.py`、`tests/test_report_export_job.py`、`tests/test_controlled_file_access.py` 和 `tests/test_api_file_downloads.py`。

- [x] **Step 5: 修复后请求一次聚焦复审**

若首轮存在 Critical/Important，向同一 reviewer 发送修复摘要和测试证据，要求确认是否仍有同级遗留。

---

### Task 5: 分层验证与完整冒烟门

**Files:**
- Test: all `tests/`
- Verify: all first-party Python、两库临时副本、API 启动入口

**Interfaces:**
- Consumes: Tasks 1-4 的最终工作树。
- Produces: P1-14 实际测试数字、完整 smoke 输出、OpenAPI 摘要、差异洁净度和数据指纹证据。

- [x] **Step 1: 运行 P1-14 聚焦回归**

```powershell
runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py tests\test_api_app.py tests\test_api_jobs.py tests\test_api_job_lifecycle.py tests\test_job_manager.py tests\test_job_store.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_review_routes.py tests\test_api_media_routes.py tests\test_api_file_downloads.py -q
```

- [x] **Step 2: 运行全部 API 回归**

```powershell
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_openapi_contract.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py tests\test_api_jobs.py tests\test_api_job_lifecycle.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_review_routes.py tests\test_api_media_routes.py tests\test_api_file_downloads.py tests\test_run_bat_api_entry.py -q
```

- [x] **Step 3: 运行完整冒烟（包含全量 pytest）**

```powershell
runtime\python\python.exe tools\smoke_check.py
```

Expected: 全量 pytest 0 failed/0 skipped；全部第一方 Python 编译；两个数据库只在临时副本初始化两次且 Schema/业务行数幂等、`integrity_check=ok`。

- [x] **Step 4: 输出 OpenAPI 检查点摘要**

```powershell
@'
from collections import Counter
from backend.api.app import create_app
schema = create_app().openapi()
operations = [
    operation
    for item in schema["paths"].values()
    for method, operation in item.items()
    if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
]
ids = [operation["operationId"] for operation in operations]
duplicates = [key for key, count in Counter(ids).items() if count > 1]
print(f"paths={len(schema['paths'])}")
print(f"operations={len(operations)}")
print(f"duplicate_operation_ids={duplicates}")
print(f"has_error_response={'ErrorResponse' in schema['components']['schemas']}")
'@ | runtime\python\python.exe -
```

- [x] **Step 5: 最终差异、安全和 `user_data` 指纹检查**

```powershell
git diff --check
git diff --cached --name-only
git status --short -- backend db_manager.py grading_service.py manual_review_service.py migrations tests tools docs AGENTS.md ARCHITECTURE.md .gitignore
git status --short -- user_data
```

使用 Global Constraints 的同一 PowerShell hash 算法复算：状态清单必须仍为 205 行和 `b81a376...f6e343cce8`；真实 grading DB 必须仍为接受后的 2,863,104 bytes 和 `93fee56e...fb841cd`；真实 question-bank DB 必须仍为 3,461,120 bytes 和 `e1e5123a...7a88b8`。若任一变化，停止并调查，不得自动还原用户数据。

---

### Task 6: 文档证据、状态和可审阅交接

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/PLAN_AUDIT_2026-07-10.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
- Modify: this plan

**Interfaces:**
- Consumes: 实际 review 结论、OpenAPI 数字、pytest/smoke 数字、Git 范围和 `user_data` 指纹。
- Produces: P1-14 `verified`、P1-15 Question Bank 只读路由 `ready`、准确待执行包数量和无需提交即可审阅的集成摘要。

- [x] **Step 1: 修正文档漂移并更新状态**

只按实际证据更新：

- P1-14 从 `ready` -> `verified`，仍注明“尚未提交”。
- P1-15 的名称统一为 “Question Bank 只读路由”，从 `planned` -> `ready`；P1-17 继续是配置生成 Job。
- 正式待执行从 82 -> 81；Phase 1 待执行从 16 -> 15；Phase 1 已验证从 `1 merged + 12 verified` -> `1 merged + 13 verified`。
- `PLAN_AUDIT` 把已解决的 P1-12/P1-13 项和当前 P1-14 审阅就绪状态更新清楚，但保留“未形成 Git 检查点”事实。
- `ARCHITECTURE.md` 只记录 OpenAPI 422 对齐、Job payload 安全边界和 P1-14 验证证据，不臆造新领域能力。

- [x] **Step 2: 在计划 Completion Evidence 记录准确数字**

记录：聚焦/API/完整 smoke 数字、OpenAPI paths/operations/duplicate IDs、独立复审结论、`git diff --check`、暂存区 0、`.superpowers/` 忽略、user_data 指纹不变。

- [x] **Step 3: 记录候选提交范围**

按以下类别列出：

1. Phase 1 API/schema/dependency。
2. JobManager、三类 handler、协作式取消。
3. Review application/media/files 服务。
4. Migration 003 与 Schema/smoke 工具。
5. P1-09 路径/跨平台锁修复。
6. 测试、架构、执行包与即时计划。
7. `.gitignore` 本地工具状态规则。

明确排除 `user_data/`、`.superpowers/`、运行时、缓存、日志和其他生成物。

- [x] **Step 4: 准备但不执行 Git/PR 文案**

建议提交标题：

```text
feat(api): stabilize phase 1 routes and job workflows
```

PR 摘要必须覆盖 sessions/students/config/template、JobManager/handlers/cancel、review/media/download、迁移/跨平台修复、测试证据和数据边界；不得声称 Phase 1 已完成或已合并。

- [x] **Step 5: 最终保持原分支未提交状态**

```powershell
git branch --show-current
git diff --cached --name-only
git log -1 --oneline
```

Expected: 仍在 `codex/wp1-2-api-routes`；暂存区为空；HEAD 仍为 `d80d512...`；无 commit、push 或 PR。

## Completion Evidence

- **TDD 审查修复：** 首轮整包复审无 Critical，确认 7 个代码 Important 与 1 个测试 Minor；逐项 RED 为 Job 5 failed、review 3 failed、config/template 3 failed、OpenAPI 1 failed、future 1 failed、并发报告 2 failed，确定性路径解析测试在生产守卫不变时先行 2 passed。修复后代理聚焦 93 passed、相关回归 18 passed。
- **独立复审：** 同一整包 reviewer 对修复后的共享净化、review 允许列表、错误脱敏、二进制 OpenAPI、future callback、并发报告和确定性路径测试逐项复核，结论为 0 Critical / 0 Important / 0 Minor。
- **主代理分层回归：** P1-14 聚焦集合 87 passed；完整 API 集合 85 passed。
- **完整冒烟：** `runtime\python\python.exe tools\smoke_check.py` exit 0；889 passed / 0 skipped / 0 failed，编译 338 个第一方 Python 文件；阅卷库/题库临时副本重复初始化幂等且 `integrity_check=ok`。
- **OpenAPI：** 25 paths / 33 operations / 0 duplicate operation IDs；存在 `ErrorResponse`；XLSX download 只声明 XLSX media type，page 只声明 JPEG/PNG/WebP/BMP，crop 只声明 JPEG，全部为 binary schema。
- **仓库守卫：** `git diff --check` exit 0；暂存区 0；`.superpowers/` 由 `.gitignore` 排除但本地内容保留。
- **Git 状态：** 分支仍为 `codex/wp1-2-api-routes`，HEAD 与 `origin/main` 均为 `d80d512b3230e9e91b0bec7bdaf66c4efa2dba2a`；候选源码/测试/迁移/工具/文档状态共 66 行，`runtime/` 与 `.superpowers/` 状态均为 0；未 stage、commit、push 或创建 PR。
- **数据守卫：** `user_data` 状态仍为 205 行、SHA-256 `b81a376b3e19205d2c0ae29ac07d440ccdf31ebd891639d221162ef6e343cce8`。用户接受的 grading DB 例外基线仍为 2,863,104 bytes / `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`；question-bank DB 仍为 3,461,120 bytes / `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`，两者修改时间亦未变化。

### Candidate commit scope (not staged)

1. Phase 1 FastAPI app、dependency、router 与 schema。
2. JobManager/JobStore、三类现有 handler、协作式取消、完成 future 回收和 job-owned 报告发布。
3. Review application/media/files 服务、受控文件守卫和共享公开数据净化。
4. `003_add_jobs.sql`、Schema baseline/migration rehearsal/smoke 工具。
5. P1-09 Windows 不可访问路径与跨平台锁测试修复。
6. 全部相应测试、`ARCHITECTURE.md`、执行包、审计和即时计划。
7. `.gitignore` 的 `.superpowers/` 本地工具状态规则。

显式排除：全部 `user_data/`、`.superpowers/` 内容、`runtime/`、缓存、日志和其他运行生成物。

### Prepared Git/PR text (not executed)

建议提交标题：`feat(api): stabilize phase 1 routes and job workflows`

PR 摘要：在保持 Streamlit 事实来源与既有数据库契约的前提下，汇总 sessions/students/config/template API、最小 JobManager 与 report/scan/grading handlers、协作式取消、review 原子应用服务、受控 media/files 下载、迁移/跨平台修复；P1-14 进一步统一 OpenAPI 错误/二进制契约、公开数据脱敏、future 生命周期和并发报告发布。验证为聚焦 87 passed、API 85 passed、全量 889 passed，整包复审无遗留。`user_data/` 和本地工具状态不进入提交；Phase 1 仍有 P1-15 至 P1-29，不能描述为完成或已合并。

## Plan Self-Review

- Spec coverage: 覆盖全量 review、OpenAPI、完整 smoke、Git/未跟踪范围、`user_data` 排除、可审阅清单和 PR 摘要。
- Scope: 生产代码修改限于审计确认的 OpenAPI/公开数据/错误脱敏、future 生命周期和报告发布冲突修复；不新增领域路由、Job 类型、Schema 或业务规则。
- TDD: `.superpowers` ignore、OpenAPI 422、Job 敏感键/公开脱敏均有明确 RED/GREEN 步骤；review 新发现必须先补失败测试。
- Type consistency: `public_job_payload(JobRecord) -> dict[str, Any]`、`ErrorResponse` 和当前 `JobResponse.payload` 类型一致。
- Data safety: 除用户明确接受的空 jobs DDL 初始化例外外，所有数据库测试与 smoke 使用临时库/副本；最终同时复核 205 行状态 hash 与两库内容 hash。
- Git safety: 没有 add/commit/push/PR 步骤；当前普通 checkout 保持原地，以便审查完整未提交范围。
- Placeholder scan: 无 TBD、TODO、“类似前项”、未定义接口或未给命令的实现步骤。
