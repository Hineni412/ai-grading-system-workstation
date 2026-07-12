# P1-23 Ops Protected Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为备份、恢复、数据库迁移、数据包导入和导出增加带 dry-run、单次短期确认令牌、独立 Job、离线落地和自动回退的 FastAPI Ops 写能力。

**Architecture:** 保留 `backend/ops/service.py` 作为 P1-22 只读服务，新增小型、单责的 plan store、archive guard、write service、operation journal、job handlers 和 offline applier。备份/导出在线原子发布；恢复/迁移/导入只由 Job 准备受控清单，并在 `运行.bat` 启动两个应用进程前离线重检、重备份和应用。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 JobManager/PathManager/受控文件服务、`zipfile`、`tempfile`、pytest。

**执行包：** P1-23
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** 7b11b6b0e80ae53c16c54bc7bfeb9821deeeed96
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- P1-23 是 `daytime_only`；自动验证只允许使用 `tmp_path` 临时数据根和临时 Ops 状态区。
- 不执行任何真实备份、恢复、迁移、导入或导出；不调用真实 Ops 写端点。
- 不修改、删除、暂存、提交或 stash 任何真实 `user_data/`；根目录两库只读记录大小、UTC mtime 和 SHA-256。
- 确认令牌单次使用、有效期精确为 300 秒，并绑定操作类型、规范化参数、源资源指纹和预检摘要。
- API 不接受客户端绝对/相对磁盘路径、命令、SQL 或迁移目录；公开响应不返回内部路径、密钥、业务正文或原始异常。
- 备份、恢复、迁移、导入和导出分别使用专用 Job 类型；通用 Job 提交端点必须拒绝绕过专用 Ops 接口。
- 备份和数据包导出在线完成；恢复、迁移和导入只准备 `restart_required` 操作，正式数据仅由启动前 offline applier 触及。
- 恢复和导入保持当前 overlay 兼容语义：只覆盖包内明确文件，不删除包外现有文件。
- 恢复/导入的准备时备份失败必须阻断；offline applier 还必须针对最新目标重跑预检并创建应用时备份。
- `all` 数据库迁移是一个整体操作；任一目标失败必须恢复本次涉及的全部数据库。
- 不开放任意命令执行，不默认包含 Phase 6 数据，不改变评分、题号、标签或状态语义。
- 功能分支不修改 `EXECUTION_INDEX.md`；共享 Index 和最终架构事实由 integration 整理。

---

## File Structure

- Create `backend/ops/models.py`: 固定操作枚举、预检摘要、内部计划和 Journal 数据模型。
- Create `backend/ops/plan_store.py`: 300 秒单次确认令牌存储、原子消费和稳定错误。
- Create `backend/ops/archive.py`: 流式 ZIP 暂存、成员守卫、大小/压缩比限制和受控解压。
- Create `backend/ops/journal.py`: Ops 本机状态区、pending 清单、公开状态和撤销协议。
- Create `backend/ops/write_service.py`: 五类预检、上传资源、令牌消费与 Job payload 构造。
- Create `backend/ops/jobs.py`: 在线备份/导出和离线恢复/迁移/导入准备 handlers。
- Create `backend/ops/offline.py`: 启动前固定离线应用器和 CLI 入口。
- Modify `backend/ops/__init__.py`: 导出稳定写服务接口。
- Modify `path_manager.py`: 新增可测试覆盖的 `ops_state_dir` 逻辑路径。
- Modify `data_transfer_service.py`: 增加文件流 ZIP 输出，避免在线 Job 把完整包留在内存。
- Modify `update_tools/backup_core.py`: 增加显式 PathManager/输出根/数据库快照注入；dry-run 不创建目录或日志。
- Modify `update_tools/migrate_db.py`: 增加纯候选预演接口和显式 logger/backup override，禁止 P1-23 预检打开正式库。
- Modify `backend/jobs/default_handlers.py`: 注册五类 Ops handler，并注入同一 Journal/状态根。
- Modify `backend/api/dependencies.py`: app-owned OpsPlanStore/OpsWriteService 生命周期依赖和 Ops 输出下载根。
- Modify `backend/api/app.py`: lifespan 创建并释放 app-owned Ops 服务。
- Modify `backend/api/schemas/ops.py`: 严格联合预检、上传、提交和 operation status schema。
- Modify `backend/api/routers/ops.py`: 上传、预检、提交、查询和撤销端点。
- Modify `backend/api/routers/jobs.py`: Ops payload/result 显式允许列表，通用提交拒绝专用 Ops 类型。
- Modify `backend/files/service.py`: 为 `ops_backup`/`ops_transfer_export` 增加 `.zip` 受控根规则。
- Modify `运行.bat`: 启动 Streamlit/API 前调用 `python -m backend.ops.offline --apply-pending`，非零即停止启动。
- Modify `tests/test_api_openapi_contract.py`: 固定 Ops 写操作及危险字段缺失。
- Modify `tests/test_run_bat_api_entry.py`: 固定离线门槛顺序、成功继续和失败停止。
- Create `tests/test_ops_plan_store.py`。
- Create `tests/test_ops_archive.py`。
- Create `tests/test_ops_write_service.py`。
- Create `tests/test_ops_jobs.py`。
- Create `tests/test_ops_offline.py`。
- Extend `tests/test_api_ops_routes.py`、`tests/test_api_jobs.py`、`tests/test_api_files.py`、`tests/test_data_transfer_service.py`、`tests/test_migration_tooling.py`。
- Modify `ARCHITECTURE.md`: 验证后记录 P1-23 已实现事实。

## Public Interfaces

```python
class OpsOperation(str, Enum):
    BACKUP = "backup"
    RESTORE = "restore"
    MIGRATION = "migration"
    TRANSFER_IMPORT = "transfer_import"
    TRANSFER_EXPORT = "transfer_export"

@dataclass(frozen=True, slots=True)
class OpsInternalPlan:
    operation: OpsOperation
    parameters: dict[str, object]
    resource_fingerprint: str
    summary: dict[str, object]
    created_monotonic: float

class OpsPlanStore:
    def issue(self, plan: OpsInternalPlan) -> tuple[str, datetime]: ...
    def consume(self, token: str) -> OpsInternalPlan: ...

class OpsWriteService:
    async def stage_import_upload(self, filename: str, chunks: AsyncIterable[bytes]) -> dict[str, object]: ...
    def preflight(self, request: OpsPreflightRequest) -> dict[str, object]: ...
    def submit(self, confirmation_token: str, manager: JobManager) -> JobRecord: ...
    def operation_status(self, operation_id: str) -> dict[str, object]: ...
    def cancel_operation(self, operation_id: str) -> dict[str, object]: ...

def register_ops_job_handlers(manager: JobManager, *, paths: PathManager) -> None: ...
def apply_pending_operation(paths: PathManager) -> int: ...
```

---

### Task 1: Freeze operation models, machine-local state path, and one-time plan store

**Files:**
- Create: `backend/ops/models.py`
- Create: `backend/ops/plan_store.py`
- Modify: `path_manager.py`
- Create: `tests/test_ops_plan_store.py`

**Interfaces:**
- Produces: `OpsOperation`, `OpsInternalPlan`, `OpsConfirmationExpired`, `OpsConfirmationUsed`, `OpsConfirmationInvalid`, `OpsPlanStore.issue()` and `OpsPlanStore.consume()`.
- Produces: `PathManager.ops_state_dir`, overridden only by `AI_GRADING_OPS_STATE_DIR` in tests/controlled launch.

- [x] **Step 1: Write RED tests for the exact state path and token lifecycle**

```python
def test_ops_state_dir_defaults_outside_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("AI_GRADING_OPS_STATE_DIR", raising=False)
    paths = PathManager()
    assert paths.ops_state_dir == (tmp_path / "local" / "AIGradingSystem" / "ops").resolve()
    assert paths.ops_state_dir != paths.data_root

def test_plan_token_is_single_use_and_expires_after_300_seconds():
    clock = FakeClock(monotonic_value=10.0, utc_value=datetime(2026, 7, 12, tzinfo=UTC))
    store = OpsPlanStore(clock=clock, token_factory=lambda: "a" * 64)
    token, expires_at = store.issue(_plan(created_monotonic=10.0))
    assert expires_at == clock.utcnow() + timedelta(seconds=300)
    assert store.consume(token).operation is OpsOperation.BACKUP
    with pytest.raises(OpsConfirmationUsed):
        store.consume(token)
```

Also assert blank/unknown token, expiry at `created + 300`, concurrent two-thread consumption has exactly one winner, and no raw token appears in `repr(store)`.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_plan_store.py -q`
Expected: FAIL because `backend.ops.plan_store`, models, and `ops_state_dir` do not exist.

- [x] **Step 3: Implement minimal immutable models and locked token store**

```python
class OpsPlanStore:
    def __init__(self, *, ttl_seconds: int = 300, clock=time, token_factory=None):
        if int(ttl_seconds) != 300:
            raise ValueError("Ops confirmation TTL must be 300 seconds")
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._plans: dict[str, _StoredPlan] = {}
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, token: str) -> OpsInternalPlan:
        digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        with self._lock:
            if digest in self._used:
                raise OpsConfirmationUsed
            stored = self._plans.pop(digest, None)
            if stored is None:
                raise OpsConfirmationInvalid
            if self._clock.monotonic() >= stored.expires_monotonic:
                raise OpsConfirmationExpired
            self._used.add(digest)
            return stored.plan
```

Path resolution:

```python
ops_override = os.getenv("AI_GRADING_OPS_STATE_DIR")
if ops_override:
    self._ops_state_dir = Path(ops_override).expanduser().resolve()
else:
    local_appdata = os.getenv("LOCALAPPDATA")
    root = Path(local_appdata) if local_appdata else Path.home() / ".ai_grading_system"
    self._ops_state_dir = (root / "AIGradingSystem" / "ops").resolve() if local_appdata else (root / "ops").resolve()
```

- [x] **Step 4: Run GREEN and adjacent PathManager tests**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_plan_store.py tests\test_api_profile_store.py -q`
Expected: `20 passed`; no directory is created merely by reading `ops_state_dir`.

- [x] **Step 5: Commit Task 1**

```powershell
git add backend/ops/models.py backend/ops/plan_store.py path_manager.py tests/test_ops_plan_store.py
git commit -m "feat: add protected ops confirmation plans"
```

### Task 2: Add bounded ZIP upload, validation, and streaming export primitives

**Files:**
- Create: `backend/ops/archive.py`
- Modify: `data_transfer_service.py`
- Create: `tests/test_ops_archive.py`
- Modify: `tests/test_data_transfer_service.py`

**Interfaces:**
- Produces: `OpsArchivePolicy(max_upload_bytes=209715200, max_members=10000, max_expanded_bytes=1073741824, max_member_bytes=268435456, max_compression_ratio=100.0)`.
- Produces: `stage_zip_upload()`, `inspect_zip()`, `extract_validated_zip()`, `write_export_zip(entries, destination)`.

- [x] **Step 1: Write RED archive attack and stream tests**

```python
@pytest.mark.parametrize("name", ["../escape.txt", "/rooted.txt", "C:/secret.txt"])
def test_inspect_zip_rejects_path_escape(tmp_path, name):
    archive = _zip_with_member(tmp_path / "bad.zip", name, b"x")
    with pytest.raises(OpsArchiveInvalid):
        inspect_zip(archive, policy=OpsArchivePolicy(), allowed_roots={"user_data", "config"})

def test_write_export_zip_streams_to_destination(tmp_path):
    source = _write(tmp_path / "source.bin", b"x" * 1024)
    destination = tmp_path / "out.zip"
    write_export_zip([ExportEntry(source, "user_data/source.bin", 1024)], destination)
    assert zipfile.is_zipfile(destination)
```

Also cover ZIP symlink mode bits, duplicate normalized names, Windows case-fold collisions, unknown roots, member count, single/total expanded size, compression ratio, CRC failure, upload cap and partial-upload cleanup.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_archive.py tests\test_data_transfer_service.py -q`
Expected: FAIL because archive primitives and `write_export_zip` do not exist.

- [x] **Step 3: Implement member normalization before any extraction**

```python
def normalized_member(name: str, allowed_roots: set[str]) -> tuple[str, ...]:
    text = str(name).replace("\\", "/")
    pure = PurePosixPath(text)
    parts = pure.parts
    if pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise OpsArchiveInvalid("unsafe_member")
    if parts[0] not in allowed_roots:
        raise OpsArchiveInvalid("unknown_root")
    if PureWindowsPath(text).drive:
        raise OpsArchiveInvalid("unsafe_member")
    return parts
```

Never call `ZipFile.extract()` or `extractall()`. Open each member and copy into a newly created regular file under a verified staging root; reject pre-existing destination entries and any parent reparse point.

- [x] **Step 4: Run archive GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_archive.py tests\test_data_transfer_service.py -q`
Expected: `16 passed` with no writes outside `tmp_path`.

- [x] **Step 5: Commit Task 2**

```powershell
git add backend/ops/archive.py data_transfer_service.py tests/test_ops_archive.py tests/test_data_transfer_service.py
git commit -m "feat: validate ops data archives"
```

### Task 3: Build five dry-run preflights and strict write-service boundary

**Files:**
- Create: `backend/ops/write_service.py`
- Modify: `update_tools/backup_core.py`
- Modify: `update_tools/migrate_db.py`
- Create: `tests/test_ops_write_service.py`
- Modify: `tests/test_migration_tooling.py`

**Interfaces:**
- Produces: `OpsWriteService.stage_import_upload()`, `.preflight()`, `.consume_plan()` and `.build_job_payload()`.
- Produces: `preview_backup(..., paths: PathManager)`, `preview_migrations(target, *, db_path, migrations_dir)` with no source-side write.

- [x] **Step 1: Write RED tests proving dry-run has zero target side effects**

```python
def test_backup_preflight_does_not_create_backup_or_log_directories(tmp_path, paths):
    assert not paths.backups_dir.exists()
    response = service(paths).preflight(OpsBackupPreflightRequest(operation="backup", reason="manual"))
    assert response["operation"] == "backup"
    assert not paths.backups_dir.exists()
    assert not paths.logs_dir.exists()

def test_migration_preflight_only_executes_on_candidate(tmp_path, monkeypatch, paths):
    before = _file_state(paths.db_path)
    response = service(paths).preflight(OpsMigrationPreflightRequest(operation="migration", target="grading"))
    assert response["summary"]["integrity"] == "ok"
    assert _file_state(paths.db_path) == before
```

Add restore/import/export preflight tests, source fingerprint changes, backup-list containment, no API profiles, no Phase 6 root, and stable path-free summaries.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_write_service.py tests\test_migration_tooling.py -q`
Expected: FAIL because `OpsWriteService` and pure preview adapters do not exist.

- [x] **Step 3: Implement operation-specific preflight dispatch**

```python
def preflight(self, request: OpsPreflightRequest) -> dict[str, object]:
    if request.operation is OpsOperation.BACKUP:
        plan = self._preflight_backup(request)
    elif request.operation is OpsOperation.RESTORE:
        plan = self._preflight_restore(request)
    elif request.operation is OpsOperation.MIGRATION:
        plan = self._preflight_migration(request)
    elif request.operation is OpsOperation.TRANSFER_IMPORT:
        plan = self._preflight_transfer_import(request)
    else:
        plan = self._preflight_transfer_export(request)
    token, expires_at = self.plan_store.issue(plan)
    return {
        "operation": plan.operation.value,
        "confirmation_token": token,
        "expires_at": expires_at,
        "requires_restart": plan.operation in OFFLINE_OPERATIONS,
        "summary": plan.summary,
    }
```

`consume_plan()` must recompute the bound source fingerprint before returning the plan; mismatch raises `OpsPreflightStale` and the consumed token remains unusable.

- [x] **Step 4: Run write-service GREEN and adjacent backup/migration tests**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_write_service.py tests\test_migration_tooling.py tests\test_schema_baseline.py -q`
Expected: `36 passed`; tests assert source files and directories remain unchanged during preflight.

- [x] **Step 5: Commit Task 3**

```powershell
git add backend/ops/write_service.py update_tools/backup_core.py update_tools/migrate_db.py tests/test_ops_write_service.py tests/test_migration_tooling.py
git commit -m "feat: add ops write preflights"
```

### Task 4: Expose strict Ops upload, preflight, submit, status, and cancel APIs

**Files:**
- Modify: `backend/api/schemas/ops.py`
- Modify: `backend/api/routers/ops.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/app.py`
- Modify: `backend/api/routers/jobs.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `tests/test_api_ops_routes.py`
- Modify: `tests/test_api_jobs.py`

**Interfaces:**
- Produces the endpoints defined in the approved design.
- App lifespan owns exactly one `OpsPlanStore` and `OpsWriteService`; dependency overrides remain externally owned in tests.

- [ ] **Step 1: Write RED strict schema and endpoint tests**

```python
def test_ops_submit_accepts_only_confirmation_token(ops_client):
    response = ops_client.post("/api/ops/jobs", json={"confirmation_token": "token"})
    assert response.status_code == 202
    assert set(response.json()["payload"]) <= {"operation_id", "operation"}

@pytest.mark.parametrize("field", ["path", "destination", "command", "sql", "migrations_dir"])
def test_ops_preflight_rejects_dangerous_extra_fields(ops_client, field):
    response = ops_client.post("/api/ops/preflights", json={"operation": "backup", "reason": "manual", field: "x"})
    assert response.status_code == 422
```

Add upload streaming/413/415 tests, all five discriminated requests, error mappings, operation query/cancel, and generic `/api/jobs/{type}` rejection.

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py tests\test_api_jobs.py -q`
Expected: new endpoints return 404 and schemas are missing.

- [ ] **Step 3: Implement app-owned dependencies and thin routes**

```python
@router.post("/preflights", response_model=OpsPreflightResponse)
def create_preflight(body: OpsPreflightRequest, service=Depends(get_ops_write_service)):
    try:
        return service.preflight(body)
    except OpsWriteError as exc:
        raise _ops_api_error(exc) from exc

@router.post("/jobs", response_model=JobResponse, status_code=202)
def submit_ops_job(body: OpsJobSubmitRequest, service=Depends(get_ops_write_service), manager=Depends(get_job_manager)):
    try:
        return _job_response(service.submit(body.confirmation_token, manager))
    except OpsWriteError as exc:
        raise _ops_api_error(exc) from exc
```

Initialize service in lifespan beside JobManager and delete both state attributes during shutdown. Tests overriding either dependency must not be shut down by the app.

- [ ] **Step 4: Run API GREEN and public-data guards**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_ops_routes.py tests\test_api_jobs.py tests\test_public_data_sanitization.py -q`
Expected: PASS; responses contain no filesystem references.

- [ ] **Step 5: Commit Task 4**

```powershell
git add backend/api/schemas/ops.py backend/api/routers/ops.py backend/api/dependencies.py backend/api/app.py backend/api/routers/jobs.py backend/api/schemas/__init__.py tests/test_api_ops_routes.py tests/test_api_jobs.py
git commit -m "feat: expose protected ops write API"
```

### Task 5: Implement atomic online backup/export Jobs and controlled ZIP download

**Files:**
- Create: `backend/ops/jobs.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/files/service.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/jobs.py`
- Create: `tests/test_ops_jobs.py`
- Modify: `tests/test_api_files.py`

**Interfaces:**
- Produces: `run_ops_backup_job()`, `run_ops_transfer_export_job()`, `register_ops_job_handlers()`.
- Extends `JobFileService` with distinct `backups_dir` and `ops_outputs_dir` roots plus ZIP-only rules: `ops_backup` resolves only under `backups_dir`; `ops_transfer_export` resolves only under `ops_outputs_dir`.

- [ ] **Step 1: Write RED online Job publication/cancel tests**

```python
def test_ops_backup_publishes_only_after_zip_validation(tmp_path, context, paths):
    result = run_ops_backup_job(context=context, paths=paths)
    published = paths.backups_dir / result["filename"]
    assert published.is_file()
    assert zipfile.is_zipfile(published)
    assert not list(paths.backups_dir.glob(".job-*"))

def test_ops_export_cancel_before_publish_leaves_no_output(tmp_path, cancelling_context, paths):
    with pytest.raises(JobCancellationRequested):
        run_ops_transfer_export_job(context=cancelling_context, paths=paths)
    assert not list((paths.outputs_dir / "ops").glob("*.zip"))
```

Assert consistent database snapshots, no main/WAL/SHM raw copy, filename/result allow-list, download success, wrong type/status/root/suffix rejection.

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_jobs.py tests\test_api_files.py -q`
Expected: FAIL because Ops handlers and file rules do not exist.

- [ ] **Step 3: Implement staging + atomic publication**

```python
def _publish_zip(context, *, staging: Path, destination: Path) -> None:
    with zipfile.ZipFile(staging, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise OpsArchiveInvalid("crc_failed")
    context.raise_if_cancelled()
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, destination)
```

Handlers persist only `operation_id/operation/outcome/filename/file_path/counts`; public projection strips `file_path` and adds download URL only on success.

- [ ] **Step 4: Run online Job GREEN and affected downloads**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_jobs.py tests\test_api_files.py tests\test_api_jobs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```powershell
git add backend/ops/jobs.py backend/jobs/default_handlers.py backend/files/service.py backend/api/dependencies.py backend/api/routers/jobs.py tests/test_ops_jobs.py tests/test_api_files.py
git commit -m "feat: run protected ops backup and export jobs"
```

### Task 6: Add immutable operation Journal and offline preparation Jobs

**Files:**
- Create: `backend/ops/journal.py`
- Modify: `backend/ops/jobs.py`
- Modify: `backend/ops/write_service.py`
- Modify: `tests/test_ops_jobs.py`
- Create: `tests/test_ops_journal.py`

**Interfaces:**
- Produces: `OpsOperationJournal.prepare()`, `.load_public()`, `.claim_pending()`, `.mark_applied()`, `.mark_rolled_back()`, `.mark_failed()`, `.cancel_pending()`.
- Produces prepare handlers for restore, migration, and transfer import.

- [ ] **Step 1: Write RED Journal atomicity and prepare tests**

```python
def test_journal_allows_exactly_one_pending_operation(tmp_path):
    journal = OpsOperationJournal(tmp_path / "ops")
    first = journal.prepare(_manifest(operation_id=uuid4()))
    assert first.status == "restart_required"
    with pytest.raises(OpsOperationBusy):
        journal.prepare(_manifest(operation_id=uuid4()))

def test_restore_prepare_stops_when_safety_backup_fails(context, paths, monkeypatch):
    monkeypatch.setattr(jobs, "create_safety_backup", _raise(OpsPreBackupFailed()))
    with pytest.raises(OpsPreBackupFailed):
        run_ops_restore_prepare_job(context=context, paths=paths)
    assert not OpsOperationJournal(paths.ops_state_dir).pending_exists()
```

Cover atomic temp+replace JSON writes, checksum mismatch, corrupted/multiple pending, cancel only before applying, retained safety backup, prepared result path-free, and migration/import candidate verification.

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_journal.py tests\test_ops_jobs.py -q`
Expected: FAIL because Journal and prepare handlers do not exist.

- [ ] **Step 3: Implement immutable manifest and public projection**

```python
@dataclass(frozen=True, slots=True)
class OpsOperationManifest:
    operation_id: str
    operation: str
    status: str
    parameters: dict[str, object]
    resource_fingerprint: str
    staging_root: str
    preparation_backup: str
    manifest_sha256: str

def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
```

Internal path fields never enter `.load_public()`.

- [ ] **Step 4: Run Journal/prepare GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_journal.py tests\test_ops_jobs.py tests\test_ops_write_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```powershell
git add backend/ops/journal.py backend/ops/jobs.py backend/ops/write_service.py tests/test_ops_journal.py tests/test_ops_jobs.py
git commit -m "feat: prepare restart-bound ops changes"
```

### Task 7: Implement startup-time offline apply, rollback, and launch gate

**Files:**
- Create: `backend/ops/offline.py`
- Modify: `运行.bat`
- Create: `tests/test_ops_offline.py`
- Modify: `tests/test_run_bat_api_entry.py`

**Interfaces:**
- Produces: `apply_pending_operation(paths: PathManager) -> int` and `python -m backend.ops.offline --apply-pending`.
- Exit `0`: no pending, applied, or apply failed but rolled back safely. Exit `2`: state invalid or rollback failed; launcher must stop.

- [ ] **Step 1: Write RED apply/rollback and launcher ordering tests**

```python
def test_restore_apply_rechecks_and_uses_latest_apply_backup(tmp_path, paths, prepared_restore):
    _write(paths.data_root / "config" / "changed_after_prepare.json", b"latest")
    assert apply_pending_operation(paths) == 0
    status = OpsOperationJournal(paths.ops_state_dir).load_public(prepared_restore.operation_id)
    assert status["status"] == "applied"
    assert status["recovery"]["backup_filename"]

def test_launcher_stops_before_api_and_streamlit_when_offline_gate_fails():
    text = Path("运行.bat").read_text(encoding="utf-8")
    gate = text.index("-m backend.ops.offline --apply-pending")
    api = text.index("-m uvicorn backend.api.app:app")
    streamlit = text.index("-m streamlit run web_app.py")
    assert gate < api < streamlit
    assert "if errorlevel 1" in text[gate:api]
```

Fault-inject each replacement index, cross-database migration second-target failure, apply-time backup failure, rollback failure, checksum change, multiple pending, overlay preservation and no-pending no-side-effect path.

- [ ] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_offline.py tests\test_run_bat_api_entry.py -q`
Expected: FAIL because offline module and launch gate do not exist.

- [ ] **Step 3: Implement fixed dispatch and rollback journal**

```python
def apply_pending_operation(paths: PathManager) -> int:
    journal = OpsOperationJournal(paths.ops_state_dir)
    manifest = journal.claim_pending()
    if manifest is None:
        return 0
    try:
        revalidate_manifest(manifest, paths)
        apply_backup = create_apply_time_backup(manifest, paths)
        _dispatch_apply(manifest, paths, apply_backup)
    except Exception as exc:
        if rollback_from_apply_backup(manifest, paths):
            journal.mark_rolled_back(manifest.operation_id, result_code="apply_failed")
            return 0
        journal.mark_failed(manifest.operation_id, result_code="rollback_failed")
        return 2
    journal.mark_applied(manifest.operation_id)
    return 0
```

The CLI prints only operation ID/status/result code and safe backup filename. It never prints internal paths or exception text.

- [ ] **Step 4: Insert the launch gate before any `start` command**

```bat
"%PYTHON_EXE%" -m backend.ops.offline --apply-pending
if errorlevel 1 (
  echo 启动前数据操作未能安全完成，系统已停止启动。
  echo 请把本窗口中的操作编号和结果代码发给 Codex 排查。
  pause
  exit /b 1
)
```

- [ ] **Step 5: Run offline GREEN and startup regression**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_ops_offline.py tests\test_run_bat_api_entry.py tests\test_api_app.py -q`
Expected: PASS; no real process is started by tests.

- [ ] **Step 6: Commit Task 7**

```powershell
git add backend/ops/offline.py 运行.bat tests/test_ops_offline.py tests/test_run_bat_api_entry.py
git commit -m "feat: apply protected ops changes before startup"
```

### Task 8: Freeze OpenAPI, compatibility, architecture, and package evidence

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `tests/test_api_ops_routes.py`
- Modify: `tests/test_api_files.py`
- Modify: `backend/ops/__init__.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-23-ops-protected-writes-implementation.md`

**Interfaces:**
- Produces exact P1-23 OpenAPI and completed handoff evidence.

- [ ] **Step 1: Add exact OpenAPI operation and forbidden-input assertions**

```python
def test_ops_write_openapi_uses_only_protected_inputs():
    schema = create_app().openapi()
    expected = {
        ("post", "/api/ops/transfer-import/uploads"),
        ("post", "/api/ops/preflights"),
        ("post", "/api/ops/jobs"),
        ("get", "/api/ops/operations/{operation_id}"),
        ("post", "/api/ops/operations/{operation_id}/cancel"),
    }
    assert expected <= operation_pairs(schema)
    serialized = json.dumps({k: v for k, v in schema["paths"].items() if k.startswith("/api/ops")}, sort_keys=True).lower()
    for forbidden in ("destination", "command", "migrations_dir", "api_key", "password", "secret"):
        assert forbidden not in serialized
```

- [ ] **Step 2: Run OpenAPI RED then GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_openapi_contract.py -q`
Expected before expectation update: exact operation set mismatch; after update: PASS with no duplicate operation IDs.

- [ ] **Step 3: Update architecture only after behavior verification**

Record that P1-23 provides protected preflight/token/Job operations, online backup/export, restart-bound restore/migrate/import, machine-local Journal, startup rollback gate, path-free public results and temporary-root-only verification. Preserve P1-22 read-only facts and do not claim real-data execution.

- [ ] **Step 4: Run focused and affected regression**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_ops_plan_store.py tests\test_ops_archive.py tests\test_ops_write_service.py tests\test_ops_journal.py tests\test_ops_jobs.py tests\test_ops_offline.py tests\test_api_ops_routes.py tests\test_api_jobs.py tests\test_api_files.py tests\test_migration_tooling.py tests\test_schema_baseline.py tests\test_data_transfer_service.py tests\test_run_bat_api_entry.py tests\test_api_openapi_contract.py tests\test_api_app.py -q
```

Expected: all selected tests pass with 0 failed; only known dependency deprecation warnings are acceptable.

- [ ] **Step 5: Run completion gates**

```powershell
git diff --check
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p1-23-ops-protected-writes-implementation.md --repo .
git status --short -- user_data
```

Expected: diff clean; quick smoke passes; handoff validator emits one JSON line with `ok=true`; worktree `user_data/` status empty.

- [ ] **Step 6: Re-read root real database fingerprints without SQLite**

Expected baseline inherited from P1-22:

- grading: `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`
- question bank: `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`

Any difference is blocking; do not commit or integrate.

- [ ] **Step 7: Record waiting_review and create the feature commit**

Update the handoff block to `waiting_review/branch_head/passed/pending/not_required/unchanged/report_only`, preserve the stash baseline exactly, stage only package files, and commit:

```powershell
git commit -m "feat: add protected ops write jobs"
```

- [ ] **Step 8: Complete independent review and final plan-only handoff**

After review reports 0 Critical and 0 Important, fix findings with focused RED/GREEN tests. Then create a plan-only final handoff commit whose block records its direct parent full reviewed SHA as `verified_pending_integration`.

## Verification and Recovery

- Baseline before source changes: Ops/API/Job/migration/schema/data-transfer/run-bat set = `47 passed / 0 failed` on the pre-P2-03 baseline; the same set must be rerun on plan baseline `7b11b6b0e80ae53c16c54bc7bfeb9821deeeed96` before Task 1.
- Feature branch gate: Task-specific RED/GREEN, full focused set, `git diff --check`, quick smoke, handoff validator and real two-database fingerprint guard.
- Integration gate: affected regression after merge, then full `tools/smoke_check.py`; P1-23 is highest data risk and changes startup ordering, so full smoke evidence cannot be skipped.
- No browser/user acceptance is required because P1-23 exposes no production UI.
- Rollback before offline apply: cancel pending operation; retain preparation backup and Journal.
- Rollback during offline apply: restore all touched files/databases from the apply-time backup; rollback failure stops startup.
- Code rollback: revert the isolated P1-23 commit chain. Do not delete pending manifests or backups during code rollback without separately verifying their state.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-23
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
