# P1-26 API/DB 性能测量基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用默认关闭的请求测量器和固定生成数据，为 P1-15/P1-19/P1-21/P1-22 的代表性只读 API 建立可重复的耗时、数据库语句数、返回记录数和样本规模基线。

**Architecture:** 在独立 `backend/performance/` 模块用 `contextvars` 隔离每个 FastAPI 请求，以 SQLite trace callback 只计数、不保存 SQL；`create_app()` 接受可选 sink 和隔离路径对象，正常生产调用保持测量关闭。`tools/performance/` 生成 `small`、`medium`、`large_5pct` 三个命名工作负载的临时双库和受控素材，真实 TestClient 请求运行 16 个场景，内存聚合两轮 p50/p95 后只输出脱敏 JSON/Markdown 摘要。

**Tech Stack:** Python 3.12、FastAPI 0.139.0、Starlette TestClient、SQLite 3.43.1、pytest、标准库 `contextvars`/`sqlite3`/`statistics`/`tempfile`。

## Global Constraints

**执行包：** P1-26
**用户自测：** none
**自测清单：** not_required
- **规划状态：** ready_for_execution
- **规划模型：** S-XH
- **允许夜间执行：** yes
- **计划基线：** ae840b05db68999a3a315c4ed3c60fcfa460941b
- 只使用固定随机种子生成的数据和系统临时目录；Git 创建 worktree 时自动检出的主线历史跟踪 `user_data/` 仅作为不可触碰基线，实施和测试不得主动读取、复制、用 SQLite 打开、修改或删除，任何阶段都不得暂存、提交或 stash。
- 根目录真实两库只允许读取文件大小、UTC 修改时间和 SHA-256；功能开始、基准运行前后、交接前必须完全一致。
- 不添加缓存、索引、连接池、请求级连接复用，不修改 SQL、分页、排序、事务、WAL、busy timeout、评分、题号、状态或活动 `knowledge_point` 语义。
- 不记录 SQL 文本、原始 URL、查询词、资源 ID、请求/响应正文、学生/题目正文、密钥、文件名、主机名、用户名、环境变量、命令行或内部绝对路径。
- 性能测量默认关闭；关闭时不安装 SQLite trace callback、不增加响应 header、不改变公开 API/OpenAPI 契约。
- 正式矩阵固定为 16 个场景、3 次预热、20 次正式样本、2 次完整重复；p95 使用 nearest-rank。
- 2026-07-13 用户覆盖原大型档：保留 `small`/`medium`，把原 `large` 七个计数组件分别按 5% 向上取整为 `(1, 25, 2, 7_500, 500, 25, 5)`，公开名为 `large_5pct`；默认三档不是各维度单调递增序列，旧 `large` CLI 选择器必须校验失败。
- 本包只报告可能的 N+1 候选，不提出或实施优化；P1-27/P3-18 另行决定是否优化。
- 计划文件名、顶部包号和领取后的交接块必须始终一致；首次功能分支提交只能修改本计划并写入 `in_progress` 交接证据。

---

## File Structure

- Create `backend/performance/__init__.py`: 只导出请求记录、sink、请求 scope 与 SQLite instrumentation 的稳定接口。
- Create `backend/performance/metrics.py`: 不可变记录、线程安全内存 sink、contextvar recorder、SQL token 分类和 trace callback。
- Modify `backend/api/app.py`: `create_app()` 注入可选性能 sink/隔离路径；请求中间件启停 recorder并提交安全路由模板记录；默认行为不变。
- Modify `db_manager.py`: 新建 grading 连接后、PRAGMA 前挂接可选计数器。
- Modify `question_bank/database/schema.py`: 题库公共连接在 PRAGMA 前挂接可选计数器。
- Modify `question_bank/services/question_read_service.py`: 稳定临时候选只读连接在任何 PRAGMA/SELECT 前挂接计数器。
- Modify `question_bank/services/training_task_service.py`: 训练任务只读连接挂接计数器。
- Modify `update_tools/migrate_db.py`: Ops 自检调用的迁移状态只读连接挂接计数器。
- Create `tools/performance/__init__.py`: 导出生成数据和基准运行接口。
- Create `tools/performance/dataset.py`: 三档规模定义、隔离路径对象、双库/素材/备份元数据生成和 manifest。
- Create `tools/performance/scenarios.py`: 16 个确定性只读场景、依赖覆盖和返回记录计数。
- Create `tools/performance/runner.py`: TestClient 运行、预热/正式采样、请求记录关联、两轮可重复性比较和摘要。
- Create `tools/performance/report.py`: nearest-rank、环境摘要、可能 N+1 观察、JSON/Markdown allowlist 渲染。
- Create `tools/benchmark_api_db.py`: 参数校验、临时环境、三档运行，以及两个版本化报告的逐文件原子替换和可捕获失败恢复。
- Create `tests/test_api_performance_metrics.py`: recorder、中间件、脱敏、异常和并发隔离。
- Create `tests/test_db_performance_instrumentation.py`: 五类目标连接路径计数且语义不变。
- Create `tests/test_performance_dataset.py`: 固定种子、规模、外键、素材、路径和文本安全。
- Create `tests/test_performance_benchmark.py`: 16 场景、预热排除、样本关联、失败即停和可重复性。
- Create `tests/test_performance_report.py`: percentile、allowlist、N+1 候选和禁止内容守卫。
- Create `docs/performance/p1-26-api-db-baseline.json`: 当前候选 SHA 上两轮三档聚合数字，不含逐次原始样本。
- Create `docs/performance/p1-26-api-db-baseline.md`: 用户可读基线、环境、规模、限制和候选观察。
- Modify `ARCHITECTURE.md`: 自动验证通过后记录 P1-26 已实现测量边界和基线事实。
- Modify this plan: checkbox、验证数字、交接状态、复审 SHA 与回退证据。

### Task 0: Claim P1-26 in a clean feature worktree

**Files:**
- Create on feature branch: `docs/superpowers/plans/2026-07-13-p1-26-api-db-performance-baseline-implementation.md`
- Import after claim: `docs/superpowers/specs/2026-07-13-p1-26-api-db-performance-baseline-design.md`

**Interfaces:**
- Consumes: `origin/main` at `ae840b05db68999a3a315c4ed3c60fcfa460941b`, design commit `0207f1c`, current `git stash list --format=%H`.
- Produces: branch `codex/p1-26-performance-baseline`, dedicated worktree, first-parent claim commit containing only this plan, then the approved design commit.

- [x] **Step 1: Use the worktree skill and verify the launch gate**

Invoke `superpowers:using-git-worktrees`. Fetch `origin`, require `origin/main` to equal the plan baseline, require no existing P1-26 feature branch/worktree, and require root database fingerprints to equal the claim baseline recorded outside Git. Run:

```powershell
git fetch --prune origin
git rev-parse origin/main
git worktree list --porcelain
git branch --all --verbose --no-abbrev
git stash list --format='%H'
git status --short -- user_data
```

Expected: `origin/main` is the full plan SHA; no P1-26 implementation channel exists; root `user_data` may contain pre-existing real changes but is never carried into the new worktree; the new worktree's `git status --short -- user_data` is empty.

- [x] **Step 2: Create the feature branch from the exact baseline**

After the worktree skill's safety checks, create `codex/p1-26-performance-baseline` from `origin/main` in `.worktrees/p1-26-performance-baseline`. Do not base it on the design branch.

- [x] **Step 3: Copy only this plan and write the initial handoff block**

Restore this one plan from `codex/p1-26-design`, then use `apply_patch` to add the single handoff block required by the packages README with exactly these values:

- package `P1-26`
- status `in_progress`
- functional commit `none`
- automated verification `pending`
- independent review `pending`
- user acceptance `not_required`
- real-data fingerprint `not_touched`
- immutable stash baseline equal to every SHA returned in Step 1, comma-separated, or `none`
- nightly action `report_only`

Run the handoff validator before committing. It may report only the expected absence of a claim commit until the commit is made; every field and package identity check must otherwise pass.

- [ ] **Step 4: Commit the plan-only claim**

```powershell
git add docs/superpowers/plans/2026-07-13-p1-26-api-db-performance-baseline-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P1-26 performance baseline"
```

Expected: the first commit after the merge base modifies exactly one file, this plan.

- [ ] **Step 5: Import the approved design as the second commit**

```powershell
git cherry-pick 0207f1c
```

Expected: the second commit adds only `docs/superpowers/specs/2026-07-13-p1-26-api-db-performance-baseline-design.md`; source code remains unchanged.

### Task 1: Add opt-in request measurement and isolated app injection

**Files:**
- Create: `backend/performance/__init__.py`
- Create: `backend/performance/metrics.py`
- Modify: `backend/api/app.py:73-130`
- Create: `tests/test_api_performance_metrics.py`

**Interfaces:**
- Produces: `RequestPerformanceRecord(request_id, method, route_template, status_code, elapsed_ms, db_statements_total, db_select_statements)`.
- Produces: `PerformanceSink.record(record) -> None`, `InMemoryPerformanceSink.record()`, `InMemoryPerformanceSink.pop(request_id)`.
- Produces: `request_performance_scope(request_id) -> ContextManager[RequestPerformanceRecorder]` and `instrument_sqlite_connection(connection) -> connection`.
- Changes: `create_app(*, performance_sink: PerformanceSink | None = None, path_manager: PathManager | None = None) -> FastAPI`; calls without arguments remain identical.

- [x] **Step 1: Write failing recorder and SQL-classification tests**

Create tests equivalent to:

```python
def test_request_scope_counts_statements_without_retaining_sql() -> None:
    sink = InMemoryPerformanceSink()
    with request_performance_scope("req-1") as recorder:
        connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        connection.execute("CREATE TABLE sample(id INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
        connection.execute("SELECT id FROM sample").fetchall()
        record = recorder.finish(
            method="GET",
            route_template="/api/sample/{item_id}",
            status_code=200,
            elapsed_ms=1.25,
        )
    sink.record(record)
    captured = sink.pop("req-1")
    assert captured.db_statements_total >= 3
    assert captured.db_select_statements == 1
    assert "sample" not in repr(captured)
```

Also add: outside a scope `instrument_sqlite_connection()` does not install a callback; `WITH ... SELECT` counts as a select; nested scopes restore the outer recorder; two `ThreadPoolExecutor` requests keep independent totals; `elapsed_ms` rejects NaN/negative values; duplicate/missing sink IDs fail deterministically.

- [x] **Step 2: Run RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_performance_metrics.py -q
```

Expected: collection fails because `backend.performance` does not exist.

- [x] **Step 3: Implement the minimal metrics module**

Implement frozen dataclasses and a locked recorder. The trace callback must only classify the first token and discard the statement immediately:

```python
_ACTIVE_RECORDER: ContextVar[RequestPerformanceRecorder | None] = ContextVar(
    "api_performance_recorder", default=None
)

def instrument_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    if _ACTIVE_RECORDER.get() is None:
        return connection

    def trace(statement: str) -> None:
        recorder = _ACTIVE_RECORDER.get()
        if recorder is not None:
            recorder.count_statement(statement)

    connection.set_trace_callback(trace)
    return connection
```

`count_statement()` increments total for every nonblank callback and increments select only when the first uppercased token is `SELECT` or `WITH`. Implement `RequestPerformanceRecord` with `repr=False` and a custom `__repr__` that exposes method, route template, status and numeric metrics but explicitly omits `request_id` and all SQL text. `InMemoryPerformanceSink` stores by request ID under a lock and `pop()` removes the record so warmups cannot leak into formal samples.

- [x] **Step 4: Write failing middleware and app-path isolation tests**

Add a dynamic test route that opens an instrumented in-memory connection and returns one item. Assert:

```python
sink = InMemoryPerformanceSink()
paths = SimpleNamespace(version="v-test")
app = create_app(performance_sink=sink, path_manager=paths)

@app.get("/api/perf/{item_id}")
def measured(item_id: int):
    connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
    connection.execute("SELECT 1").fetchone()
    connection.close()
    return {"id": item_id}

response = TestClient(app).get(
    "/api/perf/99", headers={"x-request-id": "metric-99"}
)
record = sink.pop("metric-99")
assert record.route_template == "/api/perf/{item_id}"
assert "99" not in repr(record)
assert response.headers["x-request-id"] == "metric-99"
```

Also assert: `create_app()` without a sink emits no performance record; 404 uses `<unmatched>` instead of the raw path; handler exception records status 500 and re-raises; a sink exception leaves the original response unchanged; supplied `path_manager` drives app version, health response and lifespan factories.

- [x] **Step 5: Extend the existing request middleware minimally**

Capture `paths = path_manager or get_path_manager()` in `create_app()`, save it on `api.state`, and make `_lifespan()` pass that object to `create_job_manager(paths)` and `create_ops_write_service(paths)`. In the middleware use `nullcontext(None)` when no sink exists; otherwise start `request_performance_scope(request_id)`, keep the existing request log, derive the safe template from `request.scope.get("route")`, finish the recorder in `finally`, and catch/log sink errors without raw path or exception text.

- [x] **Step 6: Run GREEN and existing app lifecycle regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_performance_metrics.py tests\test_api_app.py tests\test_api_job_lifecycle.py -q
```

Expected: PASS; default `create_app()` and lifespan ownership remain unchanged.

- [x] **Step 7: Commit Task 1**

```powershell
git add backend/performance/__init__.py backend/performance/metrics.py backend/api/app.py tests/test_api_performance_metrics.py
git commit -m "feat: add opt-in API performance metrics"
```

### Task 2: Attach query counting to every target connection boundary

**Files:**
- Modify: `db_manager.py:64-72`
- Modify: `question_bank/database/schema.py:9-24`
- Modify: `question_bank/services/question_read_service.py:468-500`
- Modify: `question_bank/services/training_task_service.py:501-510`
- Modify: `update_tools/migrate_db.py:521-545`
- Create: `tests/test_db_performance_instrumentation.py`

**Interfaces:**
- Consumes: `instrument_sqlite_connection(connection)` from Task 1.
- Preserves: every existing connection's row factory, PRAGMA order, transaction, read-only URI, commit/rollback and close ownership.
- Produces: complete statement/select counts for grading DB, question-bank public connection, stable snapshot validation/read, Training task reads and Ops migration status reads.

- [x] **Step 1: Write failing connection-boundary tests**

Use one helper per target:

```python
def measured_counts(action: Callable[[], None]) -> tuple[int, int]:
    with request_performance_scope("db-boundary") as recorder:
        action()
        record = recorder.finish(
            method="GET", route_template="/api/test", status_code=200, elapsed_ms=1.0
        )
    return record.db_statements_total, record.db_select_statements
```

Tests must create only pytest temporary databases and assert:

- `DBManager._connect()` counts its three PRAGMAs plus an explicit SELECT and still returns `sqlite3.Row`.
- `question_bank.database.schema.connect()` counts PRAGMAs/SELECT and still commits on success/rolls back on exception.
- `captured_sqlite_read_connection()` counts `quick_check`, schema validation and service SELECT while source main/WAL bytes remain unchanged.
- `training_task_service._read_connection()` counts a task SELECT and still rejects writes through `mode=ro`.
- `get_migration_status(..., db_path_override=temp_db)` counts its `schema_migrations` read and never returns the candidate path in the performance record.

- [x] **Step 2: Run RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_db_performance_instrumentation.py -q
```

Expected: counts remain zero because target factories do not call the instrumentation hook.

- [x] **Step 3: Add the hook immediately after each `sqlite3.connect()`**

Use the same pattern at every target and do not reorder any existing statement:

```python
conn = instrument_sqlite_connection(sqlite3.connect(self.db_path))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
```

For URI connections retain every existing keyword argument. In `_open_snapshot_connection()` attach before `PRAGMA query_only`; in `get_migration_status()` attach before the first SELECT. Do not add hooks to unrelated write/offline paths.

- [x] **Step 4: Run GREEN and affected database behavior tests**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_db_performance_instrumentation.py tests\test_api_question_bank_routes.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_ops_self_check_service.py tests\test_migration_tooling.py -q
```

Expected: PASS; snapshot zero-source-write, read-only rejection, migration status and transaction tests remain green.

- [x] **Step 5: Commit Task 2**

```powershell
git add backend/performance db_manager.py question_bank/database/schema.py question_bank/services/question_read_service.py question_bank/services/training_task_service.py update_tools/migrate_db.py tests/test_db_performance_instrumentation.py
git commit -m "feat: count target SQLite statements"
```

### Task 3: Build deterministic small, medium and large_5pct temporary datasets

**Files:**
- Create: `tools/performance/__init__.py`
- Create: `tools/performance/dataset.py`
- Create: `tests/test_performance_dataset.py`

**Interfaces:**
- Produces: `ScaleDefinition`, `SMALL`, `MEDIUM`, `LARGE`, `SCALES` with the exact user-overridden sizes and public names; `LARGE` remains the Python constant for compatibility, while `LARGE.name` is `large_5pct`.
- Produces: `BenchmarkPaths` exposing the PathManager properties used by API dependencies without reading repository config.
- Produces: `DatasetManifest` and `BenchmarkDataset(paths, manifest, representative_question_id, representative_task_id, knowledge_key)`.
- Produces: `build_benchmark_dataset(root: Path, scale: ScaleDefinition, *, seed: int = 126) -> BenchmarkDataset`.

- [x] **Step 1: Write failing scale and deterministic-manifest tests**

Assert the exact defaults:

```python
assert SMALL.counts == (1, 30, 10, 300, 200, 10, 5)
assert MEDIUM.counts == (5, 200, 20, 20_000, 2_000, 100, 50)
assert LARGE.name == "large_5pct"
assert LARGE.counts == (1, 25, 2, 7_500, 500, 25, 5)
```

Use a custom micro scale `(1, 2, 2, 4, 8, 2, 2)` to build twice under different pytest temp roots with seed 126. Assert equal manifests/table counts/representative IDs; `PRAGMA foreign_key_check` is empty; both `integrity_check` values are `ok`; every generated file resolves beneath its supplied root; generated text contains only `GEN-`, `CLASS-`, `generated-`, `knowledge-` markers; no repository `user_data` path appears in values or repr.

- [x] **Step 2: Run RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_performance_dataset.py -q
```

Expected: collection fails because `tools.performance.dataset` does not exist.

- [x] **Step 3: Implement paths, scales and schema creation**

Define:

```python
@dataclass(frozen=True)
class ScaleDefinition:
    name: str
    sessions: int
    students: int
    questions_per_session: int
    grading_details: int
    question_bank_questions: int
    training_tasks: int
    backups: int

    @property
    def counts(self) -> tuple[int, int, int, int, int, int, int]:
        return (
            self.sessions,
            self.students,
            self.questions_per_session,
            self.grading_details,
            self.question_bank_questions,
            self.training_tasks,
            self.backups,
        )

SMALL = ScaleDefinition("small", 1, 30, 10, 300, 200, 10, 5)
MEDIUM = ScaleDefinition("medium", 5, 200, 20, 20_000, 2_000, 100, 50)
LARGE = ScaleDefinition("large_5pct", 1, 25, 2, 7_500, 500, 25, 5)
SCALES = (SMALL, MEDIUM, LARGE)
```

These are three named default workloads, not a monotonic size ladder. `large_5pct` identifies the 5% replacement for the original large workload and may be smaller than `small` or `medium` on individual dimensions. CLI validation accepts only the names present in `SCALES`; it must not retain `large` as an alias.

`BenchmarkPaths` must explicitly expose `project_root`, `data_root`, `databases_dir`, `db_path`, `qb_db_path`, `config_dir`, `upload_config_dir`, `api_profiles_path`, `ops_state_dir`, `templates_dir`, `annotated_dir`, `reports_dir`, `backups_dir`, `outputs_dir`, `exams_dir`, `logs_dir`, `qb_data_dir`, `snapshots_dir`, and `version="v1.5.0-p1-26-generated"`. `build_benchmark_dataset()` creates these directories, calls `DBManager(db_path).initialize()` and `initialize_database(qb_db_path, seed_skills=False)`, then bulk-inserts generated rows.

- [x] **Step 4: Implement bounded bulk generation**

Use batches of at most 1,000 tuples and deterministic formulas:

```python
student_code = f"GEN-{student_id:05d}"
student_name = student_code
class_name = "CLASS-001"
question_id = f"Q{question_index:03d}"
knowledge_label = f"knowledge-{question_index % 50:02d}"
score = float((student_id + session_id + question_index) % 11)
```

Create one result for every session/student pair and exactly `scale.grading_details` detail rows by cycling the available results/questions without violating unique relations. Group question-bank questions deterministically into papers of at most 100 questions, so the derived paper count is `max(1, ceil(question_bank_questions / 100))` (`small=2`、`medium=20`、`large_5pct=5`、`micro=1`) without adding or changing a scale dimension, and associate every question with exactly one valid paper. Create at least `questions_per_session` confirmed `grading_question_links` per session, `question_bank_questions` active questions with the current `tagged` contract's four non-empty core tags (`knowledge_point`、`ability`、`exam_scope`、`student_level`) plus `method`, preserving the existing active `knowledge_point` value and its matching `knowledge-01` representative filter, and `training_tasks` tasks each with one ready variant, one variant student and one task item. Store only `{}`/`[]` or fixed generated snapshots.

Write one fixed 1×1 PNG from a standard-library base64 constant beneath the temporary question-bank root. Give the representative question one `image_paths` entry and ready `question`/`answer` preview rows pointing to that temporary file. Create `scale.backups` small `.db` metadata files beneath the temporary backups directory; no backup file is copied from any real database.

- [x] **Step 5: Return an allowlisted manifest and verify actual counts**

Before returning, query exact table counts and build a frozen manifest containing only scale name, seed, logical counts (including derived `papers`), database byte sizes and generated asset byte size. Assert counts equal the requested definition; run `foreign_key_check` and `integrity_check`; fail without returning a partial dataset if any invariant differs.

- [x] **Step 6: Run GREEN**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_performance_dataset.py tests\test_schema_baseline.py -q
```

Expected: PASS; repeat builds have identical logical manifests and never touch worktree `user_data`.

- [x] **Step 7: Commit Task 3**

```powershell
git add tools/performance/__init__.py tools/performance/dataset.py tests/test_performance_dataset.py
git commit -m "test: add generated performance datasets"
```

### Task 4: Run 16 real API scenarios and render safe repeatable reports

**Files:**
- Create: `tools/performance/scenarios.py`
- Create: `tools/performance/runner.py`
- Create: `tools/performance/report.py`
- Create: `tools/benchmark_api_db.py`
- Create: `tests/test_performance_benchmark.py`
- Create: `tests/test_performance_report.py`

**Interfaces:**
- Produces: frozen `ScenarioRequest(path: str, params: tuple[tuple[str, str], ...] = (), json_body: dict[str, object] | None = None)`.
- Produces: frozen `BenchmarkScenario(name, method, route_template, build_request: Callable[[BenchmarkDataset], ScenarioRequest], count_records: Callable[[Response], int], scale_driver: str)`.
- Produces: `build_scenarios(dataset) -> tuple[BenchmarkScenario, ...]` of length 16.
- Produces: `run_scale(dataset, *, warmups=3, samples=20, repetitions=2) -> ScaleBenchmarkResult`.
- Produces: `nearest_rank(values, percentile)`, `deterministic_projection(result)`, `render_json(report)`, `render_markdown(report)`.
- Produces CLI defaults: seed 126, `small`, `medium`, `large_5pct`, 3 warmups, 20 samples, 2 repetitions, JSON/Markdown destinations under `docs/performance/`; `--scales large` fails validation.

- [x] **Step 1: Write failing 16-scenario contract tests**

Assert exact names and routes:

```python
expected = {
    "health",
    "question_bank.papers",
    "question_bank.questions.default",
    "question_bank.questions.filtered",
    "question_bank.question.detail",
    "question_bank.question.asset",
    "question_bank.question.preview",
    "training.diagnosis",
    "training.plan.preview",
    "training.tasks",
    "training.task.detail",
    "graph.profiles",
    "graph.rows",
    "graph.evidence",
    "ops.self_check",
    "ops.backups",
}
assert {scenario.name for scenario in build_scenarios(dataset)} == expected
```

Every dynamic route must retain `{question_id}`, `{asset_index}`, `{preview_type}` or `{task_id}` in `route_template`. Training/Graph bodies use `scope={"mode":"class","class_id":"CLASS-001"}` and `exam_scope={"mode":"cross_exam"}`. The filtered question list uses deterministic `knowledge_point=knowledge-01`, `tag_status=tagged`, `sort=difficulty`, `page_size=100`; the papers scenario uses `scale_driver="papers"`.

The generated fixture must make both `question_bank.papers` and `question_bank.questions.filtered` return at least one record, and their response-record summaries must be identical across the two repetitions.

- [x] **Step 2: Write failing runner tests**

With the micro dataset, one warmup, two formal samples and two repetitions, assert each scenario produces two summaries of exactly two samples. Supply request IDs `p1-26-{scale}-{scenario}-{repetition}-{sample}` only in memory; assert they are absent from the result model. A fake non-200 scenario, missing sink record or duplicate request ID must raise `BenchmarkRunError` and return no report.

Assert dependency wiring uses:

```python
app = create_app(performance_sink=sink, path_manager=dataset.paths)
app.dependency_overrides[get_path_manager] = lambda: dataset.paths
app.dependency_overrides[get_question_bank_read_service] = lambda: QuestionBankReadService(
    dataset.paths.qb_db_path, data_root=dataset.paths.data_root
)
app.dependency_overrides[get_diagnosis_profile_service] = lambda: DiagnosisProfileService(
    dataset.paths.db_path, dataset.paths.qb_db_path
)
app.dependency_overrides[get_practice_plan_service] = lambda: PracticePlanService(
    dataset.paths.qb_db_path
)
app.dependency_overrides[get_training_task_service] = lambda: TrainingTaskService(
    dataset.paths.qb_db_path
)
```

Leave Graph and Ops service dependencies unoverridden so they consume the overridden `get_path_manager` and exercise their real stable-snapshot/self-check paths. Use a lifespan TestClient because Task 1 routes manager/Ops state to the generated paths.

- [x] **Step 3: Run RED for scenarios and runner**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_performance_benchmark.py -q
```

Expected: collection fails because scenarios/runner do not exist.

- [x] **Step 4: Implement scenario requests and response-record counters**

Each scenario owns an allowlisted counter:

- list responses: `len(payload["items"])`;
- Training diagnosis and Graph profiles: `len(payload["students"])`;
- Training plan: `len(payload["plan"]["variants"])`;
- Graph rows: `len(payload["rows"])`;
- Graph evidence: `len(payload["items"])`;
- single object/binary/health/self-check: 1;
- empty successful collections: 0.

The runner parses JSON only in memory to calculate the count and then discards it. It keeps only status, response bytes, response record count and the matched `RequestPerformanceRecord` numeric fields.

- [x] **Step 5: Implement warmup, two formal repetitions and deterministic comparison**

For each scenario/repetition: pop and discard all three warmup records; collect exactly 20 formal records; summarize in memory; never serialize individual samples. `deterministic_projection()` retains only scale manifest, scenario names, status, sample count, query-count min/median/max and response-record counts. Require repetition 1 and 2 projections to be identical before returning `repeatability="passed"`; latency is deliberately excluded from this equality.

- [x] **Step 6: Write failing report allowlist and percentile tests**

Assert:

```python
assert nearest_rank(list(range(1, 21)), 0.50) == 10
assert nearest_rank(list(range(1, 21)), 0.95) == 19
```

Build a synthetic report and assert both renderers contain code SHA, Windows/Python/SQLite versions, logical CPU count, manifests, p50/p95/min/max, statement/select summaries, response counts/bytes, repeatability and limitations. Recursively reject keys matching `path`, `url`, `sql`, `body`, `content`, `student_name`, `question_text`, `request_id`, `hostname`, `username`, `environment`, `command`; reject Windows drive prefixes and worktree/user-data markers in rendered text.

- [x] **Step 7: Run RED for report**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_performance_report.py -q
```

Expected: collection fails because report functions do not exist.

- [x] **Step 8: Implement summaries, nearest-rank and possible N+1 observations**

Sort finite nonnegative timings and use `ceil(percentile * len(values)) - 1`. Report two independent p50/p95 pairs per scale/scenario. Compare only the explicitly named `small` and `large_5pct` workloads; do not infer comparison endpoints from tuple position or treat all three defaults as monotonically increasing. A possible N+1 candidate requires the scenario's actual driver to increase and all of:

```python
select_delta = large_5pct.select_median - small.select_median
driver_delta = large_5pct.scale_driver_count - small.scale_driver_count
candidate = select_delta >= 5 and driver_delta > 0 and select_delta / driver_delta >= 0.10
```

Label it only `possible_n_plus_one`; retain `small_select_median`/`small_driver_count` for the base endpoint and publish the comparison endpoint as `comparison_scale="large_5pct"`, `comparison_select_median`, and `comparison_driver_count`. JSON and Markdown must not expose retired `large_select_median`/`large_driver_count` keys or headers. Include the select/driver counts, never SQL or an optimization recommendation.

- [x] **Step 9: Implement the CLI, per-file atomic replacement and catchable recovery**

`tools/benchmark_api_db.py` validates scale names and positive warmup/sample/repetition values, creates one `TemporaryDirectory(prefix="p1-26-benchmark-")`, builds/runs scales sequentially, obtains `git rev-parse HEAD`, and writes UTF-8 JSON/Markdown through sibling temporary files followed by `os.replace()`. Default destinations are:

```text
docs/performance/p1-26-api-db-baseline.json
docs/performance/p1-26-api-db-baseline.md
```

Each destination is written to a sibling temporary file and atomically replaced with `os.replace()`. If a `BaseException` is caught, attempt to restore the exact previous pair and clean temporary/restore files before re-raising. Sudden process termination or power loss between the two replacements can leave a mixed old/new pair; regenerate or inspect both files before the next use. This package does not implement a cross-file transaction. Error messages contain only scale/scenario and stable error code.

- [x] **Step 10: Run GREEN and CLI micro smoke**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_performance_benchmark.py tests\test_performance_report.py -q
..\..\runtime\python\python.exe tools\benchmark_api_db.py --scales small --warmups 1 --samples 2 --repetitions 2 --output-json "$env:TEMP\p1-26-smoke.json" --output-markdown "$env:TEMP\p1-26-smoke.md"
```

Expected: tests pass; CLI reports 16/16 successful scenarios and repeatability passed; smoke outputs contain no forbidden markers.

- [x] **Step 11: Commit Task 4**

```powershell
git add tools/performance tools/benchmark_api_db.py tests/test_performance_benchmark.py tests/test_performance_report.py
git commit -m "feat: add repeatable API database benchmark"
```

### Task 5: Generate the baseline, verify, review and hand off

**Files:**
- Create: `docs/performance/p1-26-api-db-baseline.json`
- Create: `docs/performance/p1-26-api-db-baseline.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-13-p1-26-api-db-performance-baseline-implementation.md`

**Interfaces:**
- Consumes: Tasks 1-4 at one functional SHA and the exact `small`/`medium`/`large_5pct` formal CLI defaults.
- Produces: committed reproducible baseline, architecture fact, `waiting_review` then independently reviewed `verified_pending_integration` handoff.

- [x] **Step 1: Run the full P1-26 focused suite**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_performance_metrics.py tests\test_db_performance_instrumentation.py tests\test_performance_dataset.py tests\test_performance_benchmark.py tests\test_performance_report.py -q
```

Expected: PASS with zero failures/skips.

- [x] **Step 2: Run affected API/database regressions**

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_job_lifecycle.py tests\test_api_question_bank_routes.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_api_ops_routes.py tests\test_ops_self_check_service.py tests\test_migration_tooling.py -q
```

Expected: PASS; no source snapshot writes, API contract drift or lifecycle regression.

- [x] **Step 3: Recheck the Task 0 real-database fingerprints before the formal benchmark**

Read only file length, UTC mtime and SHA-256 from the root checkout and compare both tuples byte-for-byte with the exact Task 0 claim values. Do not call SQLite against either file. Expected: unchanged.

- [x] **Step 4: Run the formal three-workload/two-repetition baseline**

```powershell
..\..\runtime\python\python.exe tools\benchmark_api_db.py
```

Expected: `small`、`medium`、`large_5pct` 3 个命名工作负载 × 16 scenarios × 2 repetitions complete; every repetition has 20 samples after 3 discarded warmups; deterministic projection matches; both versioned reports are complete and each destination was atomically replaced. The reports use manifest counts rather than implying that the three names form a monotonic size sequence. A sudden termination or power loss between replacements remains a documented mixed-pair risk.

- [x] **Step 5: Validate report safety and scope**

Run the report tests again, inspect the JSON keys and Markdown tables, and use repository searches to prove neither report contains `user_data`, `.worktrees`, drive-letter absolute paths, SQL text, request IDs, generated student names or question text. Confirm Git status contains no temporary DB/image/log/cache/output outside the two intended reports.

- [x] **Step 6: Update architecture and plan evidence**

Add one P1-26 increment paragraph to `ARCHITECTURE.md`: opt-in/default-off measurement; generated three-workload (`small`/`medium`/`large_5pct`) 16-scenario baseline; statement/select and response-count semantics; report location; no optimization, Schema, real data or model calls. In this plan record exact test totals, formal runtime, baseline functional SHA, possible N+1 observations and coverage limitations without copying machine paths.

- [x] **Step 7: Run static, quick-smoke and handoff guards**

```powershell
git diff --check
git status --short -- user_data
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-13-p1-26-api-db-performance-baseline-implementation.md --repo .
```

Expected: diff check and quick smoke pass; feature `user_data` status empty; handoff fields are valid for the current pre-review state.

- [x] **Step 8: Compare root real database fingerprints again**

Expected: both real database size/UTC mtime/SHA-256 values exactly match Task 0 and Step 3. Any difference blocks commit and integration.

- [x] **Step 9: Commit verified functional work as `waiting_review`**

Change the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: not_required`, `真实数据指纹: unchanged`, preserve the immutable stash baseline, and keep `夜间动作: report_only`. Commit source/tests/tools/reports/architecture/design/plan with no `user_data`.

### Task 5 functional evidence

- Baseline functional SHA: `a7e6de8476ece27ed87a67072756ba87ec5eb0dd`.
- Fresh focused suite: 70 passed, 0 failed, 0 skipped. Fresh affected API/database regressions: 131 passed, 0 failed, 0 skipped. The Step 5 report-only rerun added 18 passed with the existing Starlette TestClient/httpx2 deprecation warning and no test failure.
- The exact default formal run used `small`、`medium`、`large_5pct`, 16 scenarios, 3 discarded warmups, 20 formal samples and 2 repetitions. It completed 48/48 scale-scenario combinations in `7432.680` seconds; every status was 200 and every deterministic repetition comparison passed.
- The user-approved `large_5pct` workload keeps the original `small`/`medium` definitions and replaces the retired large workload with `(1, 25, 2, 7_500, 500, 25, 5)`. The complete default matrix still took about 123.878 minutes on the recorded environment; the report therefore treats latency as machine-specific and does not imply that the three workload names are a monotonic scale sequence.
- Possible N+1 observations: none met the fixed threshold when comparing the explicitly named `small` and `large_5pct` endpoints. The report retains neutral `comparison_*` fields and exports no retired `large_*` observation fields.
- Coverage limitations: only allowlisted SQLite connection boundaries are counted; latency is not a service-level objective; generated data may not reproduce production distributions; the run performs no optimization. Reports contain only aggregate allowlisted fields and no raw samples.
- Safety scan: zero forbidden JSON keys and zero matches for real-data/worktree/absolute-path markers, raw URLs, SQL text, request IDs, generated student/question identifiers or retired observation fields. Git status contained only the two intended versioned reports before documentation evidence was added.
- Root real-database fingerprints: the read-only length, UTC mtime and SHA-256 tuples matched the exact Task 0 claim values and the Step 3 recheck after the formal run; no real database was opened through SQLite or copied.
- Functional handoff guards: diff whitespace validation, empty feature `user_data` status, documentation governance, static compilation of 419 first-party Python files, both temporary database idempotency/integrity checks and the pre-review handoff validator passed.
- Real-data evidence: Task 0, Step 3 and Step 8 recorded exact matching file-size/UTC-mtime/SHA-256 tuples. `origin/main..HEAD` history, branch diff and feature status contain no `user_data` path.
- Process evidence: the formal launcher exited with code 0; both complete reports, top-level and per-scale `repeatability=passed`, and all 48 scale-scenario summaries with 20 samples and status 200 independently confirm completion. Catchable publication failures are covered by recovery tests; sudden termination or power loss between replacements remains a documented mixed-pair risk.
- Independent-review repair: review found that the original generator left `papers` empty, did not assign `questions.paper_id`, and wrote only two tag types, so the representative papers/tagged-filter scenarios were empty despite HTTP 200. A focused RED now covers expected papers, valid paper links, all four current core tags, and non-empty deterministic response counts. Review also found that one restore failure skipped the other report restore and masked the original publish exception; a fault-injection RED now requires independent best-effort restoration/cleanup and chains recovery failures behind the original publication error.
- Baseline regeneration requirement: these generator changes alter manifest counts and representative response counts. The committed formal JSON/Markdown reports describe the pre-repair dataset and must be regenerated on the final reviewed SHA. The formal three-workload report was intentionally not rerun during this review-fix pass.
- Review-fix verification: the dataset/report/benchmark suite passed 56 tests; the five-file P1-26 focused suite passed 76 tests; quick smoke passed documentation governance, static compilation of 419 first-party Python files, and both temporary database idempotency/integrity checks. The new fault-injection and representative-data cases failed for the intended reasons before implementation and passed after the minimal fixes.

- [ ] **Step 10: Request independent code review**

Invoke `superpowers:requesting-code-review` over the full package range. Review must specifically inspect:

- default-off instrumentation and any production overhead;
- contextvar/thread isolation and trace callback lifetime;
- route-template/path/body/SQL privacy;
- query-count completeness and accidental double instrumentation;
- generated dataset counts, scale realism and absence of real data;
- percentile/repeatability math and raw-sample exclusion;
- per-file atomic replacement, catchable-failure recovery and the documented crash/power-loss mixed-pair limitation;
- no optimization, Schema or API-contract scope creep.

Reproduce every Critical/Important finding with a RED test, make the minimal fix, rerun the affected focused/regression tests and request re-review. Critical/Important must be zero.

- [ ] **Step 11: Create the final plan-only handoff commit**

After independent review passes, modify only this plan: set `verified_pending_integration`; record the direct parent full reviewed functional SHA; set automated verification/independent review `passed`, user acceptance `not_required`, real-data fingerprint `unchanged`, nightly action `independent_candidate_allowed`. Run `tools/handoff_status.py` against the clean worktree and commit only the plan.

## Verification and Recovery

- Baseline verification: run Task 1/2 affected tests on `origin/main` before source edits and record totals in this plan; no failure is accepted as “already broken” without reproducing it on the exact baseline.
- RED/GREEN evidence: every task begins with a named failing test and records the expected missing module/count/contract failure before implementation.
- Functional branch gate: focused suite, affected regressions, formal benchmark, report safety scan, `git diff --check`, quick smoke, empty worktree `user_data`, and root real database fingerprint equality.
- Integration gate: merge the complete verified chain into a fresh integration branch from latest `origin/main`; rerun focused and affected tests; run the wave-end full `tools/smoke_check.py` once because instrumentation touches shared API/DB connection infrastructure; compare root fingerprints before and after.
- Rollback: revert reports/architecture, runner/report, dataset, connection hooks and metrics/app commits in reverse order. There is no Schema or data rollback. Default-off behavior remains available until the metrics/app commit itself is reverted.
- Performance interpretation: latency values are specific to the recorded environment and candidate SHA; query counts and returned records are deterministic invariants. No optimization is authorized by this plan.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-26
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
