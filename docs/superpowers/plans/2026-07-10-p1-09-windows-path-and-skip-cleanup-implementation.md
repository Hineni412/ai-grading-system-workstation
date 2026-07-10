# P1-09 Windows Path And Skip Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复不可访问绝对路径导致的题库素材解析异常，并让 Windows 全量测试中的两个 fork-only skip 由真实跨平台进程测试替代。

**Architecture:** 题库路径解析增加一个只负责“安全判定并解析现存文件”的内部函数，所有候选统一复用，`OSError` 表示候选不可访问并继续既有回退规则。会话锁不改生产实现；测试把跨进程互斥改用各平台都支持的 `spawn`，PID/fork 继承恢复继续由确定性单测和 POSIX 条件收集的原始 fork 集成测试覆盖。

**Tech Stack:** Python 3.12、pathlib、multiprocessing、pytest、Windows `msvcrt` / POSIX `fcntl`。

## Global Constraints

- 不改变题库素材搜索顺序、唯一文件回退、歧义时拒绝猜测和无匹配时返回原路径的规则。
- 不改变 `AnswerRegionSessionLock` 的锁协议或超时时间。
- 只使用 `tmp_path`、spawn 子进程和测试替身；不读写真实 `user_data/`。
- 当前工作区已有未提交 API/JobManager 变更，本计划不得整理、覆盖或提交它们。
- 未经用户明确要求，不执行 commit、push 或创建 PR。

---

### Task 1: 不可访问题库素材路径

**Files:**
- Modify: `tests/test_question_bank_asset_path_service.py`
- Modify: `question_bank/services/asset_path_service.py`

**Interfaces:**
- Consumes: `resolve_question_bank_asset_path(path_value, data_root, search_subdirs)` 现有契约。
- Produces: `_resolve_existing_file(value: Path) -> Path | None`，仅供本模块内部安全探测候选。

- [x] **Step 1: 写确定性失败测试**

在测试中创建唯一 fallback 文件，并让 `Path.is_file()` 只对一个绝对候选抛 `OSError`：

```python
def test_inaccessible_absolute_path_uses_unique_filename_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "user_data"
    fallback = data_root / "question_bank" / "extracted_images" / "set" / "unique.png"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"image")
    inaccessible = (tmp_path / "offline" / "unique.png").resolve(strict=False)
    original_is_file = Path.is_file

    def raise_for_inaccessible(path: Path, *args: object, **kwargs: object) -> bool:
        if path == inaccessible:
            raise OSError("network path unavailable")
        return original_is_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", raise_for_inaccessible)

    assert resolve_question_bank_asset_path(inaccessible, data_root=data_root) == fallback
```

- [x] **Step 2: 验证 RED**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_question_bank_asset_path_service.py::test_inaccessible_absolute_path_uses_unique_filename_fallback -q
```

Expected: FAIL，`OSError: network path unavailable` 从 `stored.is_file()` 传播。

- [x] **Step 3: 实现最小安全探测**

```python
def _resolve_existing_file(value: Path) -> Path | None:
    try:
        if not value.is_file():
            return None
        return value.resolve()
    except OSError:
        return None
```

绝对 stored path 和 `_unique_existing_files()` 均调用该函数；不得吞掉歧义异常或改变 fallback 顺序。

- [x] **Step 4: 让原歧义测试不依赖真实 `Z:` 超时**

把原测试改成临时绝对路径，并用相同的定向 `Path.is_file()` 异常替身；仍断言两个同名 fallback 抛 `AmbiguousQuestionBankAssetPathError`。

- [x] **Step 5: 验证 GREEN 与服务回归**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_question_bank_asset_path_service.py -q
```

Expected: 全部通过，无真实网络盘等待。

### Task 2: 跨平台会话锁测试

**Files:**
- Modify: `tests/test_answer_region_session_lock.py`
- Test: `tests/test_answer_region_draft_service.py`
- Test: `tests/test_answer_region_commit_service.py`

**Interfaces:**
- Consumes: `get_answer_region_session_lock(session_dir)` 与现有 PID reset 行为。
- Produces: Windows/POSIX 均执行的 spawn registry identity 和 parent/child contention 测试；不产生 pytest skip。

- [x] **Step 1: 增加 spawn 子进程 helper**

```python
def _report_fresh_registry_lock(session_dir: str, events: object) -> None:
    lock = get_answer_region_session_lock(Path(session_dir))
    events.put((os.getpid(), lock_module._REGISTRY_PID, lock._pid))


def _acquire_fresh_session_lock(session_dir: str, events: object) -> None:
    events.put("started")
    with get_answer_region_session_lock(Path(session_dir)):
        events.put("acquired")
```

- [x] **Step 2: 用真实 spawn 测试替代两个 Windows skip**

```python
def test_spawned_process_uses_fresh_lock_registry(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    process = context.Process(
        target=_report_fresh_registry_lock,
        args=(str(tmp_path / "session"), events),
    )

    try:
        process.start()
        child_pid, registry_pid, lock_pid = events.get(timeout=5)
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert child_pid != os.getpid()
    assert registry_pid == child_pid
    assert lock_pid == child_pid


def test_parent_held_lock_blocks_spawned_child_until_release(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    lock = AnswerRegionSessionLock(tmp_path / "session")
    process = context.Process(
        target=_acquire_fresh_session_lock,
        args=(str(tmp_path / "session"), events),
    )

    try:
        with lock:
            process.start()
            assert events.get(timeout=5) == "started"
            with pytest.raises(Empty):
                events.get(timeout=0.3)

        assert events.get(timeout=5) == "acquired"
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
```

- [x] **Step 3: 保留 POSIX 原始 fork 回归但不在 Windows 收集**

```python
if os.name != "nt" and hasattr(os, "fork"):
    def test_fresh_registry_lock_after_fork_does_not_keep_its_inherited_lock(
        tmp_path: Path,
    ) -> None:
        inherited_lock = get_answer_region_session_lock(tmp_path / "session")
        status_read, status_write = os.pipe()
        child_pid: int | None = None

        try:
            with inherited_lock:
                child_pid = os.fork()
                if child_pid == 0:
                    os.close(status_read)
                    exit_code = 0
                    try:
                        fresh_lock = get_answer_region_session_lock(tmp_path / "session")
                        assert fresh_lock is not inherited_lock
                        assert inherited_lock._depth == 0
                        assert inherited_lock._lock_file is None
                        os.write(status_write, b"S")
                        with fresh_lock:
                            os.write(status_write, b"A")
                        with inherited_lock:
                            os.write(status_write, b"R")
                    except BaseException:
                        exit_code = 1
                    finally:
                        os.close(status_write)
                        os._exit(exit_code)

                os.close(status_write)
                status_write = -1
                assert _read_pipe_byte(status_read, 5.0) == b"S"
                with pytest.raises(TimeoutError):
                    _read_pipe_byte(status_read, 0.3)

            assert _read_pipe_byte(status_read, 5.0) == b"A"
            assert _read_pipe_byte(status_read, 5.0) == b"R"
            status = _wait_for_child(child_pid, 5.0)
            assert status is not None
            child_pid = None
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            if status_write >= 0:
                os.close(status_write)
            os.close(status_read)
            if child_pid is not None:
                _terminate_child(child_pid)
```

条件定义只保护 POSIX 独有的实际 fork 集成；跨平台行为必须由 Step 2 的两条测试真实执行，不得用条件定义替代。

- [x] **Step 4: 验证锁测试无 skip**

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_answer_region_session_lock.py -q -rs
runtime\python\python.exe -m pytest tests\test_answer_region_draft_service.py tests\test_answer_region_commit_service.py -q
```

Expected: Windows 上 `test_answer_region_session_lock.py` 全部通过且 short summary 无 SKIPPED；draft/commit 跨进程回归通过。

### Task 3: 完整验证与状态同步

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/PLAN_AUDIT_2026-07-10.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

**Interfaces:**
- Consumes: 定向测试、全量 pytest、快速冒烟的实际输出。
- Produces: P1-09 `verified` 状态、零失败/零 skip 的新基线和 P1-10 下一动作。

- [x] **Step 1: 运行相关回归**

```powershell
runtime\python\python.exe -m pytest tests\test_question_bank_asset_path_service.py tests\test_answer_region_session_lock.py tests\test_answer_region_draft_service.py tests\test_answer_region_commit_service.py -q -rs
```

- [x] **Step 2: 运行全量测试与快速冒烟**

```powershell
runtime\python\python.exe -m pytest -q -rs
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

只有实际输出为零失败、零 skip 时才更新数字；否则保留真实结果并继续根因调查。

- [x] **Step 3: 同步计划状态**

将 P1-09 标为 `verified`，Phase 1 待执行包由 21 改为 20，总待执行包由 87 改为 86；正式包定义总数仍为 87。下一动作改为 P1-10。文档明确两个原 skip 是 fork-only 测试设计问题，不是生产依赖缺失。

- [x] **Step 4: 检查范围，不提交**

```powershell
git diff --check
git status --short -- question_bank/services/asset_path_service.py tests/test_question_bank_asset_path_service.py tests/test_answer_region_session_lock.py docs/superpowers AGENTS.md
```

确认 `user_data/` 未纳入本任务变更；等待用户明确要求后再提交。
