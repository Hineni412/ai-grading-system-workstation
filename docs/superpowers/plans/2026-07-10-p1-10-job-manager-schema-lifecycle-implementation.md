# P1-10 JobManager Schema And Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `003_add_jobs.sql` 成为 jobs 表与索引的唯一完整 DDL 权威，并让每个 FastAPI 应用在 lifespan 内创建、恢复和关闭自己唯一的 JobManager。

**Architecture:** `JobStore` 继续支持 Phase 3 前的空库/旧库兼容初始化，但完整建表 SQL 直接读取 `migrations/grading/003_add_jobs.sql`；`000` 基线不再吸收 003 之后的 jobs 增量。移除进程级 manager 缓存，新增纯 `create_job_manager()` 工厂；FastAPI lifespan 把 manager 放到 `app.state`，依赖只读取当前 app 所有的实例，退出时同步关闭线程池。

**Tech Stack:** Python 3.12、SQLite、FastAPI 0.139、Starlette lifespan、ThreadPoolExecutor、pytest。

## Global Constraints

- 不新增 job 类型，不改变任务状态、进度、重启恢复或取消语义；协作式取消属于 P1-11。
- `migrations/grading/003_add_jobs.sql` 是 jobs 表、CHECK、默认值和两个索引的唯一完整 DDL 文本。
- 保留 `JobStore` 独立初始化空库、自动创建父目录，以及旧 jobs 表缺少 `result_json` 时的兼容补列。
- `000_baseline_schema.sql` 不得包含 jobs；已发布 migration 文件不删除、不改名。
- 测试 dependency override 的 manager 由测试拥有，FastAPI lifespan 不创建或关闭它。
- 所有数据库验证只使用 `tmp_path` 或副本；不读写真实 `user_data/`。
- 当前工作区有 P1-09 和 WP1.2/WP1.3 未提交变更；不得覆盖、回退或提交无关文件。
- 未经用户明确要求，不执行 commit、push 或创建 PR。

---

### Task 1: jobs Schema 唯一权威

**Files:**
- Modify: `tests/test_schema_baseline.py`
- Modify: `tests/test_job_store.py`
- Modify: `backend/jobs/store.py`
- Modify: `tools/generate_schema_baseline.py`
- Modify: `migrations/grading/000_baseline_schema.sql`
- Preserve: `migrations/grading/003_add_jobs.sql`

**Interfaces:**
- Consumes: `JobStore(db_path)` 与 `run_migrations("grading", ...)` 现有入口。
- Produces: `_JOBS_SCHEMA_MIGRATION_PATH: Path` 和 `_load_jobs_schema_sql() -> str`，完整 DDL 只来自 003。

- [x] **Step 1: 写迁移顺序失败测试**

```python
def test_jobs_schema_is_introduced_only_by_003(tmp_path: Path) -> None:
    import shutil

    through_002 = tmp_path / "through_002"
    through_002.mkdir()
    grading_migrations = _PROJECT_ROOT / "migrations" / "grading"
    for name in (
        "000_baseline_schema.sql",
        "001_init_migration_tracking.sql",
        "002_add_grading_run_ledger.sql",
    ):
        shutil.copy2(grading_migrations / name, through_002 / name)

    db_path = tmp_path / "grading.db"
    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=through_002,
    )
    assert report.error is None, report.error
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone() is None

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=grading_migrations,
    )
    assert report.error is None, report.error
    with sqlite3.connect(db_path) as conn:
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name = 'jobs' OR name LIKE 'idx_jobs_%'"
            )
        }
    assert objects == {
        ("table", "jobs"),
        ("index", "idx_jobs_status_created"),
        ("index", "idx_jobs_type_created"),
    }
```

- [x] **Step 2: 验证迁移顺序 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_schema_baseline.py::test_jobs_schema_is_introduced_only_by_003 -q
```

Expected: FAIL；执行到 002 后已经从当前 000 发现 jobs 表。

- [x] **Step 3: 写 JobStore 读取迁移文件失败测试**

```python
def test_job_store_executes_configured_jobs_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs import store as store_module

    canonical = Path.cwd() / "migrations" / "grading" / "003_add_jobs.sql"
    custom = tmp_path / "custom_jobs.sql"
    custom.write_text(
        canonical.read_text(encoding="utf-8")
        + "\nCREATE TABLE runtime_schema_probe (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(store_module, "_JOBS_SCHEMA_MIGRATION_PATH", custom)

    store_module.JobStore(tmp_path / "jobs.db")

    with sqlite3.connect(tmp_path / "jobs.db") as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'runtime_schema_probe'"
        ).fetchone() is not None
```

- [x] **Step 4: 验证 JobStore authority RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py::test_job_store_executes_configured_jobs_migration -q
```

Expected: FAIL；当前模块没有 `_JOBS_SCHEMA_MIGRATION_PATH`，且运行时只执行内嵌 `_SCHEMA`。

- [x] **Step 5: 实现单一 DDL 读取**

删除 `backend/jobs/store.py` 的 `_SCHEMA`，加入：

```python
_JOBS_SCHEMA_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "grading"
    / "003_add_jobs.sql"
)


def _load_jobs_schema_sql() -> str:
    return _JOBS_SCHEMA_MIGRATION_PATH.read_text(encoding="utf-8")
```

`JobStore.initialize()` 改为 `conn.executescript(_load_jobs_schema_sql())`，随后保留现有 `PRAGMA table_info(jobs)` 与 `ALTER TABLE ... result_json` 兼容分支。

- [x] **Step 6: 固定 000 边界**

从 `tools/generate_schema_baseline.py::_build_fresh_grading()` 删除 `JobStore` import/call，并在模块说明中写明 000 只重建 Phase 0 基线，后续增量 store 不得加入。只从 `migrations/grading/000_baseline_schema.sql` 删除 `CREATE TABLE jobs` 和两个 `idx_jobs_*` 块；不重写题库基线，不修改 003。

- [x] **Step 7: 保留旧表兼容测试**

从 003 文本删除 `result_json` 那一行来构造旧表，插入一条 queued 记录，再构造 `JobStore`；断言记录保留、`result_json` 已补且 `JobRecord.result == {}`。

- [x] **Step 8: 验证 Schema GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_schema_baseline.py tests\test_migration_tooling.py -q
```

Expected: 全部通过；000→002 无 jobs，003 后完整；运行时与迁移 schema 等价。

### Task 2: JobManager shutdown 契约

**Files:**
- Modify: `tests/test_job_manager.py`
- Modify: `backend/jobs/manager.py`

**Interfaces:**
- Produces: `JobManager.is_shutdown: bool`；`shutdown()` 幂等；shutdown 后 `submit()` 在写入 jobs 行前明确拒绝。

- [x] **Step 1: 写 shutdown 失败测试**

```python
def test_job_manager_shutdown_is_idempotent_and_rejects_new_jobs(tmp_path) -> None:
    import sqlite3

    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    db_path = tmp_path / "jobs.db"
    manager = JobManager(JobStore(db_path), max_workers=1)
    manager.register("report_export", lambda _context: {})

    manager.shutdown()
    manager.shutdown()

    assert manager.is_shutdown is True
    with pytest.raises(RuntimeError, match="shut down"):
        manager.submit("report_export", {})
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
```

- [x] **Step 2: 验证 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_job_manager.py::test_job_manager_shutdown_is_idempotent_and_rejects_new_jobs -q
```

Expected: FAIL；当前没有 `is_shutdown`，且 executor 关闭后的 submit 会先留下 queued 记录。

- [x] **Step 3: 实现原子 shutdown 状态**

在构造器增加 `self._shutdown = False`。`submit()` 在现有锁内先检查状态，再创建记录、提交 future 并登记；关闭后抛 `RuntimeError("job manager is shut down")`。实现：

```python
@property
def is_shutdown(self) -> bool:
    with self._lock:
        return self._shutdown


def shutdown(self) -> None:
    with self._lock:
        if self._shutdown:
            return
        self._shutdown = True
    self._executor.shutdown(wait=True)
```

- [x] **Step 4: 验证 Manager GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_job_manager.py -q
```

### Task 3: FastAPI app-owned JobManager lifespan

**Files:**
- Create: `tests/test_api_job_lifecycle.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/app.py`

**Interfaces:**
- Produces: `create_job_manager(path_manager: PathManager | None = None) -> JobManager`。
- Changes: `get_job_manager(request: Request) -> JobManager` 从 `request.app.state.job_manager` 读取，不再维护模块级缓存。

- [x] **Step 1: 写 lifespan 恢复与关闭失败测试**

测试用 `SimpleNamespace` 提供临时 `db_path/reports_dir/exams_dir/templates_dir/data_root`。预置 queued、running、succeeded 三条记录；`with TestClient(create_app())` 启动时断言前两条变 failed、后一条不变，且 `app.state.job_manager` 存在；退出后断言 manager `is_shutdown` 且 state 已清除。

- [x] **Step 2: 写 dependency override 所有权失败测试**

创建外部 manager，设置 `app.dependency_overrides[get_job_manager]`；把 `create_job_manager` monkeypatch 为一旦调用就失败。进入/退出 TestClient context 后断言默认工厂从未调用、外部 manager 未关闭，最后由测试 `finally` 主动关闭。

- [x] **Step 3: 验证 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_api_job_lifecycle.py -q
```

Expected: FAIL；当前没有 `create_job_manager`、app state 或 lifespan shutdown。

- [x] **Step 4: 实现纯工厂与 state 依赖**

```python
def create_job_manager(path_manager: PathManager | None = None) -> JobManager:
    paths = path_manager or get_path_manager()
    manager = JobManager(JobStore(paths.db_path))
    try:
        register_default_job_handlers(
            manager,
            db_path=paths.db_path,
            reports_dir=paths.reports_dir,
            exams_dir=paths.exams_dir,
            templates_dir=paths.templates_dir,
            data_root=paths.data_root,
        )
    except Exception:
        manager.shutdown()
        raise
    return manager


def get_job_manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise RuntimeError("job manager is unavailable outside the application lifespan")
    return manager
```

删除 `_job_manager`、`_job_manager_db_path` 与旧懒加载逻辑。

- [x] **Step 5: 实现 lifespan**

```python
@asynccontextmanager
async def _lifespan(api: FastAPI) -> AsyncIterator[None]:
    from backend.api.dependencies import create_job_manager, get_job_manager

    if get_job_manager in api.dependency_overrides:
        yield
        return

    manager = create_job_manager()
    api.state.job_manager = manager
    try:
        yield
    finally:
        try:
            manager.shutdown()
        finally:
            del api.state.job_manager
```

把 `_lifespan` 传给 `FastAPI(..., lifespan=_lifespan)`。不加入强杀、超时或取消逻辑。

- [x] **Step 6: 验证 lifespan GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_api_job_lifecycle.py tests\test_api_app.py tests\test_api_jobs.py -q
```

### Task 4: API job 测试资源所有权

**Files:**
- Modify: `tests/test_api_jobs.py`
- Modify: `tests/test_api_report_jobs.py`
- Modify: `tests/test_api_scan_jobs.py`
- Modify: `tests/test_api_grading_jobs.py`

**Interfaces:**
- Test-only: 每个文件把现有 `_client_with_*` helper 改为 pytest yield fixture；fixture owns manager。

- [x] **Step 1: 将四个 helper 改为 yield fixture**

每个文件加入 `import pytest`，在原 helper 上增加 `@pytest.fixture` 并改名为 `client_with_*`。设置 dependency overrides 后使用：

```python
with TestClient(app) as client:
    try:
        yield client, db, manager
    finally:
        manager.shutdown()
```

`test_api_jobs.py` 的 tuple 不含 db，保持 `yield client, manager`。每个测试改为接收对应 fixture 并在首行解包，不再直接调用 helper。

- [x] **Step 2: 验证 override 不创建默认 manager 且 executor 均关闭**

```powershell
runtime\python\python.exe -m pytest tests\test_api_jobs.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py -q
```

### Task 5: 回归、迁移预演与文档状态

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/PLAN_AUDIT_2026-07-10.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

- [x] **Step 1: 运行 P1-10 聚焦回归**

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_job_lifecycle.py tests\test_api_jobs.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_schema_baseline.py tests\test_migration_tooling.py -q
```

- [x] **Step 2: 运行 Schema/迁移预演与快速冒烟**

```powershell
runtime\python\python.exe tools\migration_rehearsal.py --target grading
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

全部只操作工具创建的副本；不得把生成备份或副本加入 Git。

- [x] **Step 3: 运行全量测试**

```powershell
runtime\python\python.exe -m pytest -q -rs
```

只记录实际 passed/skipped/failed 数字。

- [x] **Step 4: 同步架构与执行状态**

记录 jobs DDL 权威为 003、manager 为 app state/lifespan 所有、override 所有权和 shutdown 语义。P1-10 验证后标为 `verified`，P1-11 改为 `ready`；Phase 1 剩余 19 包，总待执行 85 包。测试数字以 Step 3 实际输出为准。

- [x] **Step 5: 最终范围与格式检查，不提交**

```powershell
git diff --check
git status --short -- backend/jobs backend/api migrations/grading tools/generate_schema_baseline.py tests docs/superpowers AGENTS.md ARCHITECTURE.md
```

确认无真实 `user_data/`，等待用户明确要求后再提交。
