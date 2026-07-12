# P1-22 Ops Read-only Self-check API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本机运维页面提供脱敏、无危险写操作的系统自检快照与备份清单 FastAPI 契约。

**Architecture:** 新增独立 `backend/ops/` 服务，把旧 `pages/系统自检.py` 中版本、目录、数据库、迁移、工具和 API 配置状态的非 UI 检查收口到可注入、可限时测试的只读服务。数据库与迁移状态只检查 `captured_sqlite_snapshot_path()` 生成的系统临时候选；`/api/ops` 路由只做 schema 投影和稳定错误映射，备份清单复用现有 `backup_core.list_backups()` 但移除路径并限制返回数量。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 PathManager/稳定 SQLite 捕获/迁移与备份工具、pytest。

**执行包：** P1-22
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 5c65ed0447699fae9ba2ac19811090afe3f83ed9
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-22 的只读自检快照与备份清单；不得增加备份、恢复、数据库迁移、数据包导入/导出或任意命令执行端点。
- 不返回任何绝对/相对内部路径、API key、token、secret、password、学生姓名、答卷正文、题目正文、日志正文或原始异常。
- 阅卷库和题库不得由 SQLite 直接打开；存在的数据库只通过 `captured_sqlite_snapshot_path()` 捕获到系统临时目录，再在候选上执行 `quick_check` 与迁移状态读取。
- 目录可写性使用创建后立即关闭并删除的唯一临时探针；不创建缺失目录，不修改业务文件。生产端点只定义此能力，本包验证仅在 `tmp_path` 合成数据根运行，不调用真实自检端点。
- 备份 API 只返回 `kind/filename/created_at/reason/size_bytes`，按新到旧排序，默认 50 条、最大 100 条；ZIP 与旧 `.db` 备份均只扫描 PathManager 的备份根。
- 工具可用性只做静态定位，不启动 Word、LibreOffice、pdflatex 或任何子进程；响应只返回稳定工具 ID、可用布尔值与 `ok/warning`，不返回可执行文件路径。
- API 配置只返回 `configured: bool`；解析失败只产生稳定 warning，不返回 profile 名称、文件位置或异常文本。
- 每个检查项与整体状态只使用 `ok/warning/error`；数据库损坏或捕获失败作为该项 `error` 返回，路由自身仍返回结构化 200 快照；服务无法建立安全 PathManager 边界时映射为脱敏 `ops_self_check_unavailable` 503。
- Pydantic 响应模型使用显式字段并 `extra="forbid"`；OpenAPI 中不得出现写操作或 path/file/destination/command 等客户端输入。
- 不修改 Streamlit 页面行为、现有备份/恢复/迁移行为、数据库 Schema、评分规则、标签语义、运行入口或任何真实 `user_data/`。
- 功能分支不更新 `EXECUTION_INDEX.md`；共享 Index 与最终架构状态由 integration 统一整理。

---

## File Structure

- Create: `backend/ops/__init__.py`：导出自检服务公开入口。
- Create: `backend/ops/service.py`：目录探针、数据库候选检查、迁移摘要、工具探测、API 配置布尔状态和安全备份投影。
- Create: `backend/api/schemas/ops.py`：严格自检与备份响应模型。
- Create: `backend/api/routers/ops.py`：两个 GET 端点与稳定 503 映射。
- Modify: `update_tools/migrate_db.py`：`get_migration_status()` 增加仅供临时候选使用的显式 `db_path_override`，保持 CLI 默认行为不变。
- Modify: `backend/api/dependencies.py`：注入 `OpsSelfCheckService`，只传 PathManager 与可替换探测器。
- Modify: `backend/api/routers/__init__.py`：导出 `ops_router`。
- Modify: `backend/api/schemas/__init__.py`：导出 Ops 契约模型。
- Modify: `backend/api/app.py`：注册独立 Ops router。
- Create: `tests/test_ops_self_check_service.py`：合成目录、数据库候选、迁移、坏库、工具、配置与备份投影测试。
- Create: `tests/test_api_ops_routes.py`：路由快照、limit、脱敏、错误状态和禁止写操作测试。
- Modify: `tests/test_migration_tooling.py`：候选路径 override 不碰源库且默认调用兼容。
- Modify: `tests/test_api_openapi_contract.py`：新增两个 GET 操作并证明无 Ops 写端点/危险参数。
- Modify: `ARCHITECTURE.md`：验证后记录 P1-22 增量边界和只读 Ops API 事实。
- Modify: `docs/superpowers/plans/2026-07-12-p1-22-ops-readonly-self-check-api-implementation.md`：维护 checkbox、RED/GREEN、验证、复审与交接证据。

## Public Interfaces

```python
CheckStatus = Literal["ok", "warning", "error"]

class OpsSelfCheckResponse(_OpsModel):
    version: str
    status: CheckStatus
    api_configured: bool
    directories: list[OpsDirectoryCheck]
    databases: list[OpsDatabaseCheck]
    tools: list[OpsToolCheck]
    warnings: list[str]

class OpsBackupListResponse(_OpsModel):
    items: list[OpsBackupItem]
    returned: int
    limit: int

GET /api/ops/self-check -> OpsSelfCheckResponse
GET /api/ops/backups?limit=50 -> OpsBackupListResponse
```

`OpsDirectoryCheck` 只含 `key/exists/writable/status`；`OpsDatabaseCheck` 只含 `key/exists/size_bytes/integrity/migration_version/pending_migrations/status`；`OpsToolCheck` 只含 `key/available/status`；`OpsBackupItem` 只含 `kind/filename/created_at/reason/size_bytes`。

---

### Task 1: Freeze strict Ops schemas and read-only OpenAPI boundary

**Files:**
- Create: `tests/test_api_ops_routes.py`
- Create: `backend/api/schemas/ops.py`
- Modify: `backend/api/schemas/__init__.py`

**Interfaces:**
- Produces: `OpsSelfCheckResponse`, `OpsBackupListResponse` and their nested explicit projection models.

- [ ] **Step 1: Write failing schema and missing-route tests**

```python
def test_ops_self_check_contract_is_path_free(ops_client):
    response = ops_client.get("/api/ops/self-check")
    assert response.status_code == 200
    text = response.text.lower()
    assert "api_key" not in text
    assert "c:\\\\" not in text
    assert "/private/" not in text

def test_ops_exposes_no_write_operations():
    schema = create_app().openapi()
    ops_paths = {path: item for path, item in schema["paths"].items() if path.startswith("/api/ops")}
    assert set(ops_paths) == {"/api/ops/self-check", "/api/ops/backups"}
    assert all(set(item) <= {"get", "parameters"} for item in ops_paths.values())
```

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py -q`
Expected: collection/import fails because `backend.api.schemas.ops` and Ops routes do not exist.

- [ ] **Step 3: Implement strict response models**

```python
class _OpsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class OpsDirectoryCheck(_OpsModel):
    key: str
    exists: bool
    writable: bool
    status: Literal["ok", "warning", "error"]

class OpsBackupListResponse(_OpsModel):
    items: list[OpsBackupItem]
    returned: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
```

Implement every nested model exactly as declared in Public Interfaces; do not add generic mapping fields.

- [ ] **Step 4: Run schema tests**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py -q`
Expected: direct schema assertions pass; endpoint assertions remain 404.

### Task 2: Make migration status safely target a temporary candidate

**Files:**
- Modify: `update_tools/migrate_db.py`
- Modify: `tests/test_migration_tooling.py`

**Interfaces:**
- Consumes: existing target metadata and migration files.
- Produces: `get_migration_status(target_name: str, *, db_path_override: Path | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing source-isolation test**

```python
def test_migration_status_uses_explicit_candidate_without_opening_source(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    create_tracking_db(candidate)
    monkeypatch.setattr(migrate_db, "_get_targets", lambda: {
        "grading": {"db_path": source, "migrations_dir": grading_migrations_dir()}
    })
    status = migrate_db.get_migration_status("grading", db_path_override=candidate)
    assert status["db_exists"] is True
    assert not source.exists()
```

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_migration_tooling.py -q`
Expected: `TypeError` because `db_path_override` is not accepted.

- [ ] **Step 3: Add the minimal keyword-only override**

```python
def get_migration_status(
    target_name: str,
    *,
    db_path_override: Path | None = None,
) -> dict[str, Any]:
    targets = _get_targets()
    if target_name not in targets:
        return {"error": f"未知目标: {target_name}"}
    config = targets[target_name]
    db_path = Path(db_path_override) if db_path_override is not None else config["db_path"]
```

Keep every existing return key and CLI call unchanged.

- [ ] **Step 4: Run migration tests GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_schema_baseline.py -q`
Expected: all tests pass; default status behavior remains compatible.

### Task 3: Build the side-effect-bounded self-check service

**Files:**
- Create: `backend/ops/__init__.py`
- Create: `backend/ops/service.py`
- Create: `tests/test_ops_self_check_service.py`

**Interfaces:**
- Consumes: `PathManager`, `captured_sqlite_snapshot_path`, migration status override, `backup_core.list_backups`, filesystem/tool detector callables.
- Produces: `OpsSelfCheckService.build_snapshot()` and `OpsSelfCheckService.list_backups(limit)` using only schema-compatible dictionaries.

- [ ] **Step 1: Write failing service tests**

```python
def test_snapshot_checks_candidates_without_opening_sources(ops_service, guarded_sources):
    snapshot = ops_service.build_snapshot()
    assert snapshot["status"] == "ok"
    assert {item["key"] for item in snapshot["databases"]} == {"grading", "question_bank"}
    assert guarded_sources.opened_by_sqlite == []

def test_bad_database_is_an_error_without_raw_exception(bad_db_service):
    snapshot = bad_db_service.build_snapshot()
    grading = next(item for item in snapshot["databases"] if item["key"] == "grading")
    assert grading["status"] == "error"
    assert grading["integrity"] == "unavailable"
    assert "not a database" not in json.dumps(snapshot).lower()

def test_backup_projection_drops_paths_and_limits_items(ops_service):
    payload = ops_service.list_backups(limit=2)
    assert payload["returned"] == 2
    assert all(set(item) == {"kind", "filename", "created_at", "reason", "size_bytes"} for item in payload["items"])
```

Also cover missing directories, unwritable probe, absent DB, pending migrations, missing tools, malformed API config, `.db` stat races, stable sorting, and ZIP metadata containing an injected path/key.

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_self_check_service.py -q`
Expected: import fails because `backend.ops.service` does not exist.

- [ ] **Step 3: Implement bounded helpers**

```python
def _probe_writable(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    probe = directory / f".ops-write-probe-{uuid.uuid4().hex}"
    try:
        with probe.open("xb") as handle:
            handle.write(b"")
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass

def _database_check(key, source, target):
    if not source.is_file():
        return missing_database_item(key)
    try:
        with captured_sqlite_snapshot_path(source) as candidate:
            status = get_migration_status(target, db_path_override=candidate)
        return safe_database_item(key, source.stat().st_size, status)
    except (OSError, sqlite3.Error, SnapshotReadError):
        return unavailable_database_item(key, safe_size(source))
```

Use a fixed directory allow-list (`data`, `databases`, `backups`, `logs`, `reports`, `outputs`), fixed tool IDs (`microsoft_word`, `libreoffice`, `pdflatex`), and fixed warning codes rather than exception text. Read API profiles only to compute whether any dict entry has a non-empty `api_key`; never retain profile data.

- [ ] **Step 4: Run service tests GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_self_check_service.py tests\test_migration_tooling.py -q`
Expected: all service and migration tests pass with only `tmp_path` data.

### Task 4: Add thin Ops routes and registration

**Files:**
- Create: `backend/api/routers/ops.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/__init__.py`
- Modify: `backend/api/app.py`
- Modify: `tests/test_api_ops_routes.py`

**Interfaces:**
- Consumes: `get_ops_self_check_service`, strict Ops response schemas.
- Produces: `GET /api/ops/self-check` and `GET /api/ops/backups?limit=50`.

- [ ] **Step 1: Extend failing route behavior tests**

```python
def test_ops_routes_return_explicit_safe_projection(ops_client, fake_ops_service):
    response = ops_client.get("/api/ops/self-check")
    assert response.status_code == 200
    assert response.json()["version"] == "v-test"
    assert fake_ops_service.snapshot_calls == 1

def test_ops_backups_enforces_bounded_limit(ops_client):
    assert ops_client.get("/api/ops/backups?limit=0").status_code == 422
    assert ops_client.get("/api/ops/backups?limit=101").status_code == 422
```

Cover injected extra response keys being rejected during projection, stable 503 on service-boundary failure, and POST/PUT/DELETE returning 405/404 with no registered operation.

- [ ] **Step 2: Run route RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py -q`
Expected: Ops requests return 404.

- [ ] **Step 3: Implement router and dependency**

```python
router = APIRouter(prefix="/api/ops", tags=["ops"])

@router.get("/self-check", response_model=OpsSelfCheckResponse, responses=ERROR_503)
def get_self_check(service: OpsSelfCheckService = Depends(get_ops_self_check_service)):
    try:
        return service.build_snapshot()
    except Exception as exc:
        raise ApiError(503, "ops_self_check_unavailable", "System self-check is temporarily unavailable") from exc

@router.get("/backups", response_model=OpsBackupListResponse, responses=ERROR_503)
def get_backups(limit: int = Query(50, ge=1, le=100), service=Depends(get_ops_self_check_service)):
    try:
        return service.list_backups(limit)
    except Exception as exc:
        raise ApiError(503, "ops_backup_list_unavailable", "Backup list is temporarily unavailable") from exc
```

Register `ops_router` exactly once in `create_app()`.

- [ ] **Step 4: Run route GREEN and adjacent API regression**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py tests\test_api_app.py tests\test_api_read_routes.py -q`
Expected: all selected API tests pass.

### Task 5: Freeze OpenAPI, architecture and package handoff evidence

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-22-ops-readonly-self-check-api-implementation.md`

**Interfaces:**
- Consumes: registered Ops operations and all prior evidence.
- Produces: stable read-only OpenAPI contract, current architecture fact and valid P1-22 handoff.

- [ ] **Step 1: Add exact OpenAPI assertions**

```python
def test_ops_openapi_is_read_only_and_has_no_dangerous_inputs():
    schema = create_app().openapi()
    assert ("GET", "/api/ops/self-check") in operation_pairs(schema)
    assert ("GET", "/api/ops/backups") in operation_pairs(schema)
    ops = json.dumps({p: v for p, v in schema["paths"].items() if p.startswith("/api/ops")}, sort_keys=True)
    for forbidden in ("restore", "migrate", "import", "destination", "command", "api_key"):
        assert forbidden not in ops.lower()
```

- [ ] **Step 2: Run OpenAPI test RED, update exact operation set, then GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py -q`
Expected before update: new Ops operations are absent from the expected set; after update: pass with unique operation IDs and unified 422/503 schemas.

- [ ] **Step 3: Update architecture fact after code verification**

Add one P1-22 increment bullet and update FastAPI route listings: Ops returns path-free version/directory/database/migration/tool/config state plus bounded backup metadata; database inspection uses temporary candidates; no backup/restore/migration/import operation is exposed.

- [ ] **Step 4: Run final focused and affected regression**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_self_check_service.py tests\test_api_ops_routes.py tests\test_migration_tooling.py tests\test_schema_baseline.py tests\test_api_openapi_contract.py tests\test_api_app.py tests\test_api_read_routes.py -q`
Expected: all selected tests pass with 0 failures.

- [ ] **Step 5: Run package completion gates**

```powershell
git diff --check
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p1-22-ops-readonly-self-check-api-implementation.md --repo .
git status --short -- user_data
```

Expected: diff clean; quick smoke passes; handoff validator emits one JSON line with `ok=true`; worktree `user_data/` status empty.

- [ ] **Step 6: Re-read root real database fingerprints without opening SQLite**

Compare size, UTC mtime and SHA-256 to the recorded baseline:

- grading DB: `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`
- question-bank DB: `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`

Any difference is a blocking failure; do not integrate.

- [ ] **Step 7: Record waiting_review, commit feature, complete independent review, then create plan-only final handoff commit**

The functional commit must contain `交接状态: waiting_review`, `功能提交: branch_head`, automatic validation passed, independent review pending, user acceptance not required, unchanged fingerprint, and unchanged stash baseline. After fresh review has zero Critical/Important findings, create a plan-only final handoff commit whose block records its direct parent full SHA as `verified_pending_integration`.

## Verification and Recovery

- Baseline before claim: `tests/test_api_app.py tests/test_api_openapi_contract.py tests/test_migration_tooling.py` = `20 passed` on `5c65ed0447699fae9ba2ac19811090afe3f83ed9`.
- Rollback is the isolated P1-22 commit chain; no database migration, persistent file format change or production write endpoint exists.
- If a focused test, quick smoke, handoff validation, independent review or real-data fingerprint check fails, preserve the feature worktree and do not integrate.
- Full pytest is not the default feature-branch gate; integration runs the wave-end full smoke once unless a repository risk trigger requires additional full testing.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-22
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
