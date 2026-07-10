# P1-11 Cooperative Job Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 queued job 可立即取消，让 running job 仅在 handler 到达已核实的安全边界后进入 `cancelled`，并阻止报告、扫描分析和批改在确认取消后继续发布业务结果。

**Architecture:** `JobStore` 负责原子状态迁移：queued/paused 可直接终止，running 只置 `cancel_requested`；`JobContext.raise_if_cancelled()` 是 handler 明确确认安全停止的唯一异常协议，普通异常即使撞上取消请求也仍记为 failed。报告先写 job staging 目录再原子发布，扫描分析先写临时 JSON 再替换，批改通过 `should_cancel` 回调在派发与结果持久化边界停止并复用现有 paused 账本保存可恢复状态。

**Tech Stack:** Python 3.12、SQLite、ThreadPoolExecutor、FastAPI、pytest、现有 `GradingRunStore` 暂停/恢复账本。

## Global Constraints

- 不强杀 Python 线程，不尝试中断已经发出的单次 OCR/LLM/Excel 阻塞调用；阻塞调用返回后必须再次检查取消。
- 不改变评分规则、题号契约、批改 prompt、并发上限或活动知识点语义。
- 不新增/修改数据库列或 migration；沿用 `jobs.cancel_requested`、`finished_at` 和现有状态 CHECK。
- running 取消请求不得提前写 `cancelled` 或 `finished_at`；只有 handler 抛出专用取消异常或 paused 已安全停止时才确认终态。
- 普通 handler 异常不得因同时存在 `cancel_requested=1` 而伪装成 cancelled。
- 报告取消不得在正式 reports 目录留下新 xlsx；扫描取消不得覆盖既有 `scan_analysis_latest.json`；批改取消不得保存取消检查之后返回的在途评分结果。
- 所有测试只用 `tmp_path`、假服务和临时数据库；不得读写真实 `user_data/`。
- 当前工作区包含 P1-09/P1-10 与 WP1.2/WP1.3 未提交改动；只增量编辑 P1-11 文件，不覆盖或回退既有修改。
- 按用户要求，本计划不 commit、不 push、不创建 PR，也不暂存任何文件。

---

### Task 1: JobStore/JobManager 取消状态机

**Files:**
- Modify: `tests/test_job_store.py`
- Modify: `tests/test_job_manager.py`
- Modify: `tests/test_api_jobs.py`
- Modify: `backend/jobs/store.py`
- Modify: `backend/jobs/manager.py`

**Interfaces:**
- Produces: `JobCancellationRequested(RuntimeError)`。
- Produces: `JobContext.raise_if_cancelled() -> None` 与 `JobContext.is_cancel_requested() -> bool`。
- Produces: `JobStore.confirm_cancelled(job_id: int) -> bool`。
- Changes: `JobStore.request_cancel()` 对 queued/paused 立即终止，对 running 只记录请求。

- [x] **Step 1: 写 queued/running/timestamp 状态机失败测试**

```python
def test_running_cancel_stays_running_until_confirmed(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("grading_run", {})
    assert store.mark_running(job.id) is True

    assert store.request_cancel(job.id) is True
    requested = store.get_job(job.id)
    assert requested.status == "running"
    assert requested.cancel_requested is True
    assert requested.finished_at is None

    assert store.confirm_cancelled(job.id) is True
    cancelled = store.get_job(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


def test_queued_cancel_is_immediate_and_never_starts(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("report_export", {})
    assert store.request_cancel(job.id) is True
    cancelled = store.get_job(job.id)
    assert cancelled.status == "cancelled"
    assert cancelled.started_at is None
    assert cancelled.finished_at is not None
    assert store.mark_running(job.id) is False
```

- [x] **Step 2: 写 manager 确认、失败与竞态失败测试**

```python
def test_running_job_is_cancelled_only_after_handler_checkpoint(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def handler(context: JobContext) -> None:
        started.set()
        assert release.wait(3)
        context.raise_if_cancelled()

    manager.register("grading_run", handler)
    job = manager.submit("grading_run", {})
    assert started.wait(3)
    assert manager.cancel(job.id) is True
    requested = store.get_job(job.id)
    assert requested.status == "running"
    assert requested.finished_at is None
    release.set()
    manager.wait(job.id, timeout=5)
    assert store.get_job(job.id).status == "cancelled"


def test_failure_after_cancel_request_is_failed_not_cancelled(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def handler(_context: JobContext) -> None:
        started.set()
        assert release.wait(3)
        raise RuntimeError("boom after request")

    manager.register("scan_analysis", handler)
    try:
        job = manager.submit("scan_analysis", {})
        assert started.wait(3)
        assert manager.cancel(job.id) is True
        release.set()
        manager.wait(job.id, timeout=5)
        loaded = store.get_job(job.id)
        assert loaded.status == "failed"
        assert loaded.cancel_requested is True
        assert loaded.error == "boom after request"
    finally:
        release.set()
        manager.shutdown()


def test_normal_completion_can_win_cancel_race(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    manager = JobManager(store, max_workers=1)
    committed = threading.Event()
    release = threading.Event()

    def handler(_context: JobContext) -> dict[str, bool]:
        committed.set()
        assert release.wait(3)
        return {"published": True}

    manager.register("report_export", handler)
    try:
        job = manager.submit("report_export", {})
        assert committed.wait(3)
        assert manager.cancel(job.id) is True
        release.set()
        manager.wait(job.id, timeout=5)
        loaded = store.get_job(job.id)
        assert loaded.status == "succeeded"
        assert loaded.cancel_requested is True
        assert loaded.result == {"published": True}
    finally:
        release.set()
        manager.shutdown()
```

- [x] **Step 3: 验证状态机 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_jobs.py -q
```

Expected: FAIL；当前 running 会立即写 `cancelled/finished_at`，且普通异常在取消请求存在时也会被记为 cancelled。

- [x] **Step 4: 实现原子请求与确认迁移**

`JobStore.request_cancel()` 使用单条 UPDATE：

```sql
UPDATE jobs
SET cancel_requested = CASE
        WHEN status IN ('queued','running','paused') THEN 1
        ELSE cancel_requested
    END,
    status = CASE
        WHEN status IN ('queued','paused') THEN 'cancelled'
        ELSE status
    END,
    updated_at = CASE
        WHEN status IN ('queued','running','paused') THEN datetime('now','localtime')
        ELSE updated_at
    END,
    finished_at = CASE
        WHEN status IN ('queued','paused')
            THEN COALESCE(finished_at, datetime('now','localtime'))
        ELSE finished_at
    END
WHERE id = ?
```

新增确认方法：

```python
def confirm_cancelled(self, job_id: int) -> bool:
    with self._connect() as conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                updated_at = datetime('now','localtime'),
                finished_at = COALESCE(finished_at, datetime('now','localtime'))
            WHERE id = ?
              AND status = 'running'
              AND cancel_requested = 1
            """,
            (int(job_id),),
        )
        return cursor.rowcount == 1
```

`finish()` 增加 `status NOT IN ('succeeded','failed','cancelled')` 条件，禁止迟到线程覆盖既有终态。

- [x] **Step 5: 实现 handler 确认协议**

```python
class JobCancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: int
    job_type: str
    payload: dict[str, Any]
    store: JobStore

    def is_cancel_requested(self) -> bool:
        return self.store.is_cancel_requested(self.job_id)

    def raise_if_cancelled(self) -> None:
        if self.is_cancel_requested():
            raise JobCancellationRequested(f"job {self.job_id} cancellation requested")
```

`JobManager._run_job()` 只在捕获专用异常时调用 `confirm_cancelled()`；普通异常始终 `failed`；handler 正常返回始终尝试 `succeeded`，使已经跨过最终发布边界的正常完成在竞态中胜出。

- [x] **Step 6: 更新 API 取消契约测试并验证 GREEN**

running 取消 API 首次响应断言 `status == "running"`、`cancel_requested is True`、`finished_at is None`；释放 handler 并等待后再断言 `cancelled`。运行：

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_jobs.py -q
```

---

### Task 2: 报告与扫描结果的安全发布

**Files:**
- Modify: `tests/test_report_export_job.py`
- Modify: `tests/test_scan_analysis_job.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/jobs/scan_analysis.py`

**Interfaces:**
- Changes: `run_scan_analysis` 新增关键字参数 `raise_if_cancelled: Callable[[], None] | None = None`。
- Behavior: 报告 generator 只接触 job staging 目录；扫描 latest JSON 只在最终检查后用 `os.replace()` 发布。

- [x] **Step 1: 写报告阻塞返回后的取消失败测试**

```python
def test_report_cancel_during_export_does_not_publish_xlsx(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingReportGenerator:
        def __init__(self, _db_path: Path, reports_dir: Path) -> None:
            self.reports_dir = reports_dir

        def export_session(self, session_id: int) -> Path:
            staged = self.reports_dir / f"session-{session_id}.xlsx"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(b"staged")
            started.set()
            assert release.wait(3)
            return staged

    reports_dir = tmp_path / "reports"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=reports_dir,
            report_generator_factory=BlockingReportGenerator,
        )
        job = manager.submit("report_export", {"session_id": 42})
        assert started.wait(3)
        assert manager.cancel(job.id) is True
        assert manager.get(job.id).status == "running"
        release.set()
        manager.wait(job.id, timeout=5)

        assert manager.get(job.id).status == "cancelled"
        assert list(reports_dir.glob("*.xlsx")) == []
        assert list(reports_dir.glob(".job-*")) == []
    finally:
        release.set()
        manager.shutdown()
```

- [x] **Step 2: 写扫描 latest 文件保护失败测试**

```python
def test_scan_cancel_after_analyze_preserves_previous_latest_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.jobs.manager import JobCancellationRequested
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    class FakeDb:
        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    latest = work_dir / "scan_analysis_latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text('{"version":"previous"}', encoding="utf-8")
    cancelled = False

    class CancellingScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            nonlocal cancelled
            cancelled = True
            return ScanAnalysis(total_pages=2)

    def raise_if_cancelled() -> None:
        if cancelled:
            raise JobCancellationRequested("cancelled")

    with pytest.raises(JobCancellationRequested):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=CancellingScanner,
            raise_if_cancelled=raise_if_cancelled,
        )
    assert latest.read_text(encoding="utf-8") == '{"version":"previous"}'
    assert list(latest.parent.glob(".scan_analysis_latest.*.tmp")) == []
```

- [x] **Step 3: 验证真实 handler 契约 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_report_export_job.py tests\test_scan_analysis_job.py -q
```

Expected: FAIL；当前报告直接写正式目录，扫描直接覆盖 latest JSON，runner 也没有取消回调。

- [x] **Step 4: 实现报告 staging 发布**

`_build_report_export_handler()` 创建 `reports_dir/.job-<id>-*` 临时目录，把该目录传入 `report_generator_factory`。阻塞导出返回后依次校验输出文件位于 staging 内、报告进度、调用 `context.raise_if_cancelled()`，最后以 `os.replace(staged_output, reports_dir / staged_output.name)` 原子发布；`TemporaryDirectory` 在成功或取消后都清理残留。

- [x] **Step 5: 实现扫描原子发布与取消检查**

`run_scan_analysis()` 在前置校验前、`scanner.analyze()` 返回后、临时 JSON 写完后各调用一次可选 `raise_if_cancelled`。临时文件与目标同目录，用 UTF-8 写入、flush/close 后调用最终检查与 `os.replace()`；异常路径在 `finally` 删除临时文件，既有 latest 文件保持不变。默认 scan handler 把 `context.raise_if_cancelled` 传给 runner。

- [x] **Step 6: 验证报告与扫描 GREEN**

```powershell
runtime\python\python.exe -m pytest tests\test_report_export_job.py tests\test_scan_analysis_job.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py -q
```

---

### Task 3: 批改安全停止与结果持久化边界

**Files:**
- Modify: `tests/test_grading_run_job.py`
- Modify: `tests/test_grading_pause_resume.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/jobs/grading_run.py`
- Modify: `grading_service.py`

**Interfaces:**
- Changes: `run_grading_job` 新增关键字参数 `raise_if_cancelled: Callable[[], None] | None = None` 与 `should_cancel: Callable[[], bool] | None = None`。
- Changes: `GradingService.run_session_grading` 新增关键字参数 `should_cancel: Callable[[], bool] | None = None`。
- Produces internal event with exact keys: `event="session_cancelled"`、`run_id: int | None`、`progress: dict[str, int | float]`。

- [x] **Step 1: 写 grading handler 回调透传失败测试**

在既有成功测试中加入：

```python
should_cancel = lambda: False
result = run_grading_job(
    db=db,
    session_id=session_id,
    exams_dir=tmp_path / "exams",
    session_work_dir=work_dir,
    data_root=tmp_path,
    question_bank_db_path=tmp_path / "databases" / "question_bank.db",
    llm_client_factory=lambda: "llm",
    service_factory=FakeService,
    should_cancel=should_cancel,
)
assert captured["should_cancel"] is should_cancel
```

另加专用事件测试：

```python
def test_run_grading_job_confirms_cancel_when_service_stops(tmp_path) -> None:
    from backend.jobs.grading_run import run_grading_job
    from backend.jobs.manager import JobCancellationRequested

    db, session_id = _seed_session(tmp_path)
    checks = 0
    captured: dict[str, Any] = {}

    class FakeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run_session_grading(self, **kwargs: Any):
            captured.update(kwargs)
            yield {"event": "session_cancelled", "run_id": 3, "progress": {}}

    def raise_if_cancelled() -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise JobCancellationRequested("cancelled")

    should_cancel = lambda: True
    with pytest.raises(JobCancellationRequested):
        run_grading_job(
            db=db,
            session_id=session_id,
            exams_dir=tmp_path / "exams",
            session_work_dir=tmp_path / "templates" / f"session_{session_id}",
            data_root=tmp_path,
            question_bank_db_path=tmp_path / "databases" / "question_bank.db",
            llm_client_factory=lambda: object(),
            service_factory=FakeService,
            failed_only=True,
            raise_if_cancelled=raise_if_cancelled,
            should_cancel=should_cancel,
        )
    assert captured["should_cancel"] is should_cancel
    assert checks == 2
```

- [x] **Step 2: 写整卷在途请求返回后不落成绩失败测试**

```python
def test_cancel_discards_inflight_full_paper_result(patched, tmp_path, monkeypatch) -> None:
    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_grade(_grader, paper_group, *_args, **_kwargs):
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return Result()

    monkeypatch.setattr(grading_service, "_grade_one_paper_with_retries", blocking_grade)
    service = _service(db)
    events: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: events.extend(
            service.run_session_grading(
                session_id=session_id,
                exams_dir=tmp_path,
                rubric_path=tmp_path / "rubric.json",
                answer_key_path=tmp_path / "answer.json",
                scan_analysis={"groups": [], "issues": []},
                max_workers=1,
                grading_mode="full_paper",
                enhance_images=False,
                should_cancel=cancelled.is_set,
            )
        )
    )
    worker.start()
    assert started.wait(3)
    cancelled.set()
    release.set()
    worker.join(5)

    assert any(event.get("event") == "session_cancelled" for event in events)
    assert not worker.is_alive()
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT processing_status FROM exam_papers WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == "pending"
    assert GradingRunStore(db.db_path).latest(session_id).state == "paused"
```

- [x] **Step 3: 写混合批改发布前取消失败测试**

```python
def test_cancel_discards_unpublished_hybrid_results(patched, tmp_path, monkeypatch) -> None:
    from hybrid_batch_grading_service import HybridBatchRunResult, PaperEntry

    db, session_id = _seed(tmp_path, [(1, "stu1")])
    group = _make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_hybrid(**kwargs: Any) -> HybridBatchRunResult:
        paper_group = kwargs["paper_groups"][0]
        started.set()
        assert release.wait(3)

        class Result:
            student_name = paper_group.student_name
            student_score = 1.0
            total_score = 1.0
            needs_human_review = False
            raw_json = {"questions": []}
            grading_details: list[object] = []

        return HybridBatchRunResult(
            paper_entries=[PaperEntry("paper-1", 1, "stu1", paper_group)],
            results_by_paper_key={"paper-1": Result()},
            fallback_items=[],
            usage_records=[],
            usage_summary={},
            paused=True,
        )

    monkeypatch.setattr(grading_service, "run_hybrid_batch_grading", blocking_hybrid)
    events: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: events.extend(
            _service(db).run_session_grading(
                session_id=session_id,
                exams_dir=tmp_path,
                rubric_path=tmp_path / "rubric.json",
                answer_key_path=tmp_path / "answer.json",
                scan_analysis={"groups": [], "issues": []},
                grading_mode="hybrid_batch",
                enhance_images=False,
                should_cancel=cancelled.is_set,
            )
        )
    )
    worker.start()
    assert started.wait(3)
    cancelled.set()
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert any(event.get("event") == "session_cancelled" for event in events)
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT processing_status FROM exam_papers WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == "pending"
assert GradingRunStore(db.db_path).latest(session_id).state == "paused"
```

- [x] **Step 3a: 锁定 failed-only 取消恢复语义**

新增真实临时库测试：把一份 paper 预置为 `processing_status='failed'` 且保留原错误，在 failed-only 重试的在途调用中请求取消；安全停止后断言 paper 恢复为 `failed` 且错误文本不变。实现用 `paper_cancel_restore_state` 保存每份重试 paper 的取消前状态；普通新运行仍恢复为 pending，带不完整结果的 graded paper 恢复为 graded。

- [x] **Step 3b: 防止无请求的取消信号留下 running 孤儿**

新增 manager 协议守卫测试：handler 若直接抛 `JobCancellationRequested`，但 job 没有 `cancel_requested`，`confirm_cancelled()` 会拒绝确认，manager 必须把该协议错误记为 failed，而不是返回后留下永久 running。

- [x] **Step 4: 验证批改取消 RED**

```powershell
runtime\python\python.exe -m pytest tests\test_grading_run_job.py tests\test_grading_pause_resume.py -q
```

Expected: FAIL；当前 service 没有 `should_cancel`，在途 future 返回后会直接写 `session_results`。

- [x] **Step 5: 实现 runner 与 service 取消协议**

默认 handler 把 `context.raise_if_cancelled` 和 `context.is_cancel_requested` 传给 `run_grading_job()`。runner 在构造服务前检查一次；把 `should_cancel` 传入 `run_session_grading()`；收到 `session_cancelled` 时调用 `raise_if_cancelled()` 结束 handler。

`GradingService` 新增安全布尔 helper，并在以下边界检查：启动运行守卫后、每份 paper 建立/派发前、等待 in-flight future 时、每次 `save_session_result()`/`replace_result_details_atomic()` 前、混合批改返回后及逐卷发布前。取消后停止派发，等待已发出的单次请求返回但丢弃未发布结果；把 `grading` paper/ledger item 恢复为 pending，把 grading run 记为 paused，释放 session 运行守卫并只发 `session_cancelled`。现有 ledger pause 仍保存 in-flight 结果并发 `session_paused`，两种语义不得混淆。

- [x] **Step 6: 验证批改 GREEN 与既有暂停回归**

```powershell
runtime\python\python.exe -m pytest tests\test_grading_run_job.py tests\test_grading_pause_resume.py tests\test_grading_run_store.py tests\test_hybrid_grading_regressions.py tests\test_retry_failed_grading.py -q
```

---

### Task 4: 状态同步与 P1-11 验收

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/plans/2026-07-10-p1-11-cooperative-job-cancellation-implementation.md`

- [x] **Step 1: 运行 P1-11 聚焦回归**

```powershell
runtime\python\python.exe -m pytest tests\test_job_store.py tests\test_job_manager.py tests\test_api_jobs.py tests\test_report_export_job.py tests\test_scan_analysis_job.py tests\test_grading_run_job.py tests\test_grading_pause_resume.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_job_lifecycle.py -q
```

- [x] **Step 2: 运行 Job/API 相关回归**

```powershell
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_read_routes.py tests\test_api_write_routes.py tests\test_api_config_routes.py tests\test_api_template_region_routes.py tests\test_api_jobs.py tests\test_api_report_jobs.py tests\test_api_scan_jobs.py tests\test_api_grading_jobs.py tests\test_api_review_routes.py -q
```

- [x] **Step 3: 运行快速冒烟**

```powershell
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

只允许工具创建临时副本；不得操作或暂存真实 `user_data/`。

- [x] **Step 4: 运行全量测试**

```powershell
runtime\python\python.exe -m pytest -q -rs
```

- [x] **Step 5: 更新架构与执行证据**

记录 request-vs-confirmed cancellation 状态机、三类 handler 的安全边界和“不强杀单次阻塞调用”。把 P1-11 标成 `verified`，P1-12 保持下一项；测试数字只写 Step 1-4 的实际输出。

- [x] **Step 6: 最终范围与格式检查，不提交**

```powershell
git diff --check
git status --short -- backend/jobs grading_service.py tests docs/superpowers AGENTS.md ARCHITECTURE.md
git status --short -- user_data
```

最后一条只确认既有真实数据脏状态未被本包新增修改；不 stage、不 commit、不 push。
