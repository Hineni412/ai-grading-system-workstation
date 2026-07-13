# P1-28 Five-Flow API E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P1-28
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** 85d7cc664f24313408a01e75127b03fa7620bf12
**用户自测：** none
**自测清单：** not_required

**Goal:** 用完全隔离的合成数据证明 FastAPI 可以完成会话/配置、模板/区域、扫描/批改、教师复核和报表导出下载，并能从扫描失败、批改部分失败和进程重启中按既有契约恢复。

**Architecture:** 在 `tests/api_e2e/` 建立共享测试台，使用真实 FastAPI 路由、真实临时 SQLite、真实 JobManager、真实 Job 编排与真实报表/下载服务；模型、扫描识别和评分模型调用替换为确定性假实现。所有业务动作经 HTTP 触发，测试只直接读取临时数据库和文件作为持久化证据。

**Tech Stack:** Python 3.12.1、FastAPI 0.139.0、Starlette TestClient、SQLite 3.43.1、pytest、Pillow、openpyxl、现有 DBManager/JobManager/ReportGenerator。

## Global Constraints

- 本包只增加测试资产和测试辅助代码；如测试揭示生产缺口，必须先保留对应 RED，再单独做最小生产修复。
- 不新增或改变 API、Schema、评分规则、题号、Job 状态、报告格式、WAL/busy timeout 或活动 `knowledge_point` 语义。
- 只使用 pytest 临时目录、固定合成学生、程序生成 PNG、假 LLM、假扫描器和假批改服务；不得读取真实模型配置、密钥或真实 `user_data/` 内容。
- 根目录真实两库只允许读取文件大小、UTC 修改时间和 SHA-256；功能开始、验证前后和交接前必须完全一致，不得用 SQLite 打开真实库。
- 完整链路中的业务动作必须由 HTTP 端点触发；直接访问临时 SQLite/文件只用于验证已触发动作的持久化结果。
- Job 完成只通过 `GET /api/jobs/{id}` 轮询，不把 `manager.wait()` 当作 E2E 成功证据。
- 公开 Job payload/result 与错误响应不得包含临时绝对路径、密钥、合成试卷正文或内部异常文本；session/config/template 当前已公开的兼容路径字段不在本包改变。
- `completed` 只表示批改运行结束，允许同时存在失败答卷；断言必须同时读取 `graded/failed` 统计。
- 交接前运行 P1-28 聚焦测试、受影响 API/Job 回归、`git diff --check` 和 `tools/smoke_check.py --skip-tests`；integration 因跨核心 API/Job 状态运行一次完整 `tools/smoke_check.py`。
- 计划文件名、顶部包号和领取后的交接块包号必须一致；首次功能分支提交只能修改本计划并写入 `in_progress` 交接块。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-28
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

- 功能 worktree 分支：`codex/p1-28-api-e2e`
- 基线提交：`85d7cc664f24313408a01e75127b03fa7620bf12`
- 根目录 `grading_system.db`：2863104 bytes；UTC mtime `2026-07-10T07:10:41.1221109Z`；SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`
- 根目录 `question_bank.db`：3461120 bytes；UTC mtime `2026-07-08T11:58:06.3320883Z`；SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`
- 基线受影响 API 测试：64 passed in 14.01s。
- 根 worktree 的无关 Phase 3 文档改动与真实 `user_data/` 改动均未复制、未暂存。

---

### Task 1: Claim P1-28 with an isolated feature branch

**Files:**
- Create: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`

**Interfaces:**
- Consumes: approved design at `docs/superpowers/specs/2026-07-14-p1-28-five-flow-api-e2e-design.md`, latest `origin/main`, immutable stash baseline and root database fingerprints.
- Produces: validator-compatible first first-parent claim commit with `in_progress` handoff state.

- [x] **Step 1: Verify the dedicated feature channel and immutable baselines**

Run from the new P1-28 worktree:

```powershell
git status --short --branch
git status --short -- user_data
git rev-parse HEAD
git rev-parse origin/main
git stash list --format=%H
rg -n "\*\*执行包：\*\* P1-28|HANDOFF_STATUS_(START|END)" docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
```

Expected: only the plan is untracked; `user_data` is clean; HEAD and `origin/main` equal the plan baseline; stash SHAs are recorded exactly once; before claim the plan has one package declaration and no handoff markers.

- [x] **Step 2: Add the handoff block and claim evidence**

Append this exact block after Global Constraints, using the observed immutable stash baseline:

```text
&lt;!-- HANDOFF_STATUS_START --&gt;
## 昼夜交接

&#42;&#42;执行包：&#42;&#42; P1-28
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
&lt;!-- HANDOFF_STATUS_END --&gt;
```

Below the block record the feature worktree branch, full baseline SHA, root database size/UTC mtime/SHA-256, baseline focused test result, and the fact that the root worktree's unrelated Phase 3 and `user_data` changes were not copied or staged.

- [x] **Step 3: Commit only the claim plan and validate it**

```powershell
git add -- docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: claim P1-28 API E2E"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md `
  --repo .
```

Expected: staged list contains only the plan; validator emits one JSON line with `ok=true`, `state="in_progress"`, and no issues.

---

### Task 2: Build the isolated E2E harness and prove configuration/template persistence

**Files:**
- Create: `tests/api_e2e/__init__.py`
- Create: `tests/api_e2e/harness.py`
- Create: `tests/api_e2e/conftest.py`
- Create: `tests/api_e2e/test_five_flow.py`
- Modify: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`

**Interfaces:**
- Consumes: `create_app()`, FastAPI dependency overrides, `DBManager`, `JobManager`, `register_default_job_handlers()`, `run_config_generation_job()`, `run_scan_analysis()`, `run_grading_job()` and `JobFileService`.
- Produces: `ApiE2EHarness(client, db, manager, paths, controls)`; `poll_job(job_id, expected_status)`; deterministic config/PNG/student fixtures; injectable scan/grading failure controls.

- [x] **Step 1: Write the first failing five-flow test through template commit**

Create `tests/api_e2e/test_five_flow.py` with this initial test:

```python
from __future__ import annotations

from pathlib import Path


def test_api_five_flow_persists_config_template_and_regions(api_e2e) -> None:
    created = api_e2e.client.post(
        "/api/sessions",
        json={
            "name": "Synthetic E2E Exam",
            "rubric_path": str(api_e2e.paths.bootstrap_rubric),
            "answer_key_path": str(api_e2e.paths.bootstrap_answer),
        },
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    submitted = api_e2e.client.post(
        f"/api/sessions/{session_id}/config/generate",
        json=api_e2e.config_request(),
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["outcome"] == "complete"

    config = api_e2e.client.get(f"/api/sessions/{session_id}/config").json()
    assert config["rubric"]["questions"][0]["question_id"] == "Q1"
    assert Path(config["rubric_path"]).is_file()
    assert Path(config["answer_key_path"]).is_file()

    committed = api_e2e.bind_and_commit_template(session_id)
    snapshot_path = Path(committed.pop("snapshot_path"))
    assert snapshot_path.is_file()
    snapshot_path.resolve().relative_to(
        (api_e2e.paths.templates_dir / f"session_{session_id}").resolve()
    )
    assert committed == {
        "committed": True,
        "snapshot_pending": False,
        "issues": [],
        "region_count": 1,
        "error": None,
    }
    assert api_e2e.db.is_template_ready(session_id) is True
    regions = api_e2e.client.get(f"/api/sessions/{session_id}/regions").json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"
```

- [x] **Step 2: Run the test and verify RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_five_flow.py::test_api_five_flow_persists_config_template_and_regions -q
```

Expected: collection fails because fixture `api_e2e` and `tests.api_e2e.harness` do not exist.

- [x] **Step 3: Implement the minimal reusable harness**

Create `tests/api_e2e/harness.py` with these public types and exact behavior:

```python
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient


@dataclass
class E2EControls:
    scan_failures_remaining: int = 0
    grading_runs: int = 0


@dataclass(frozen=True)
class E2EPaths:
    data_root: Path
    db_path: Path
    qb_db_path: Path
    reports_dir: Path
    exams_dir: Path
    templates_dir: Path
    annotated_dir: Path
    outputs_dir: Path
    backups_dir: Path
    upload_config_dir: Path
    bootstrap_rubric: Path
    bootstrap_answer: Path
    template_front: Path
    template_back: Path


@dataclass
class FakeLLM:
    calls: list[str]

    def record(self, request_type: str) -> None:
        self.calls.append(request_type)


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _normalized_public_text(value: str) -> str:
    return value.replace("\\", "/").casefold()


class ApiE2EHarness:
    TERMINAL = {"succeeded", "failed", "cancelled"}

    def __init__(self, client: TestClient, db: Any, manager: Any, paths: E2EPaths, controls: E2EControls) -> None:
        self.client = client
        self.db = db
        self.manager = manager
        self.paths = paths
        self.controls = controls

    def poll_job(self, job_id: int, expected_status: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            last = response.json()
            normalized_data_root = _normalized_public_text(str(self.paths.data_root)).rstrip("/")
            for value in _iter_strings(last):
                assert normalized_data_root not in _normalized_public_text(value), "job response exposed the temporary data root"
                assert "api_key" not in value.casefold(), "job response exposed an API key field or value"
            if last["status"] in self.TERMINAL:
                assert last["status"] == expected_status
                return last
            time.sleep(0.01)
        raise AssertionError(f"job {job_id} did not reach a terminal state: {last}")

    @staticmethod
    def config_request() -> dict[str, Any]:
        return {
            "confirmed_blocks": [{"question_id": "Q1", "question_type": "comprehensive", "text": "synthetic prompt", "canonical_answer": "synthetic answer"}],
            "document_text": "synthetic exam text",
            "question_images": {},
        }

    def bind_and_commit_template(self, session_id: int) -> dict[str, Any]:
        bound = self.client.put(
            f"/api/sessions/{session_id}/template",
            json={"front_template_path": str(self.paths.template_front), "back_template_path": str(self.paths.template_back)},
        )
        assert bound.status_code == 200
        region = {"region_uuid": "q1-region", "page": "front", "region_order": 1, "x": 10, "y": 10, "w": 80, "h": 60, "mapped_question_id": "Q1", "mapping_status": "manual", "is_confirmed": True}
        draft = self.client.put(f"/api/sessions/{session_id}/regions/draft", json={"revision": 1, "regions": [region]})
        assert draft.status_code == 200
        committed = self.client.post(
            f"/api/sessions/{session_id}/regions/commit",
            json={"regions": [region], "image_sizes": {"front": [120, 160], "back": [120, 160]}, "template_matches": True, "expected_template_fingerprint": draft.json()["template_fingerprint"]},
        )
        assert committed.status_code == 200
        return committed.json()
```

In the same file implement `valid_config_payload()` as six comprehensive questions Q1-Q6 using scores `[17, 17, 17, 17, 17, 15]`; each question has one same-score part/step and a matching answer part, and the payload has `meta={"warnings": []}`. This preserves the production `MAX_QUESTION_SCORE=18` contract while keeping Q1 first for the synthetic review flow. Implement `build_paths(tmp_path)` to create the listed directories, write `{}` bootstrap JSON files, and generate 120×160 PNG template/scan assets with Pillow.

Create `tests/api_e2e/conftest.py` with fixture construction that:

```python
@pytest.fixture
def api_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = build_paths(tmp_path)
    db = DBManager(paths.db_path)
    db.initialize()
    db.upsert_students([
        StudentRecord("SYN-001", "Synthetic Student A", "Synthetic Class"),
        StudentRecord("SYN-002", "Synthetic Student B", "Synthetic Class"),
    ])
    monkeypatch.setattr(
        "backend.jobs.config_generation.generate_grading_config_from_confirmed_blocks",
        lambda *_args, **_kwargs: valid_config_payload(),
    )
    controls = E2EControls()
    manager = build_job_manager(paths, controls=controls)
    try:
        app = create_app()
        install_dependency_overrides(app, db=db, manager=manager, paths=paths)
        with TestClient(app) as client:
            yield ApiE2EHarness(client, db, manager, paths, controls)
    finally:
        manager.shutdown()
```

`build_job_manager()` must call `register_default_job_handlers()` with the real config runner and ReportGenerator plus `llm_client_factory=lambda: FakeLLM([])`. During Task 2, do not pass scan/grading runner overrides because those handlers are not invoked; Task 3 injects deterministic wrappers. `install_dependency_overrides()` must override grading DB, manager, upload/templates/data/exams/reports/annotated/outputs/backups directories and `get_job_file_service` with `JobFileService(paths.reports_dir, training_outputs_dir=paths.outputs_dir / "training", backups_dir=paths.backups_dir, ops_outputs_dir=paths.outputs_dir / "ops")`. It must also override `get_ops_write_service` with an inert object so application lifespan startup cannot create a root-bound service.

- [x] **Step 4: Run the focused test and verify GREEN**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_five_flow.py::test_api_five_flow_persists_config_template_and_regions -q
```

Expected: `1 passed` with no writes outside pytest temporary storage. The portable runtime currently emits one dependency-level `StarletteDeprecationWarning` from `fastapi.testclient`; record it as baseline noise rather than hiding it.

Independent-review regressions additionally prove that nested Windows paths remain detectable after JSON decoding/normalization, and that the external JobManager is shut down if `create_app()`, dependency override installation, or `TestClient` startup fails.

- [x] **Step 5: Commit the harness foundation**

```powershell
git add -- tests/api_e2e docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --check
git commit -m "test: add P1-28 API E2E harness"
```

---

### Task 3: Prove scan failure recovery and partial grading recovery

**Files:**
- Modify: `tests/api_e2e/harness.py`
- Modify: `tests/api_e2e/conftest.py`
- Create: `tests/api_e2e/test_failure_recovery.py`
- Modify: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`

**Interfaces:**
- Consumes: `run_scan_analysis(..., scanner_factory=...)`, `run_grading_job(..., service_factory=...)`, `DBManager.create_exam_paper()`, `save_session_result()` and `update_exam_paper_status()`.
- Produces: `scan_runner`, `grading_runner`, deterministic two-student database persistence, `failed_only` recovery and progress assertions.

- [x] **Step 1: Write failing recovery tests**

Create `tests/api_e2e/test_failure_recovery.py`:

```python
def test_scan_failure_can_be_retried_without_half_published_state(api_e2e, prepared_session_id) -> None:
    api_e2e.controls.scan_failures_remaining = 1
    failed = api_e2e.client.post(f"/api/sessions/{prepared_session_id}/scan/analyze", json={"enhance_images": False})
    first_job = api_e2e.poll_job(failed.json()["id"], "failed")
    assert first_job["error"] == "Job failed; see local logs for details."
    assert not (api_e2e.paths.templates_dir / f"session_{prepared_session_id}" / "scan_analysis_latest.json").exists()
    assert api_e2e.paper_statuses(prepared_session_id) == []

    retried = api_e2e.client.post(f"/api/sessions/{prepared_session_id}/scan/analyze", json={"enhance_images": False})
    second_job = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert second_job["result"]["summary"] == {"auto_matched": 2, "issues": 0, "absent_candidates": 0, "total_pages": 4}


def test_partial_grading_uses_completed_semantics_and_failed_only_recovery(api_e2e, scanned_session_id) -> None:
    submitted = api_e2e.client.post(f"/api/sessions/{scanned_session_id}/grading/run", json={"grading_mode": "full_paper", "enhance_images": False})
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["state"] == "completed"
    assert job["result"]["summary"]["graded"] == 1
    assert job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(scanned_session_id) == ["failed", "graded"]
    before = api_e2e.result_scores(scanned_session_id)
    assert before == {"SYN-001": 85.0}

    retried = api_e2e.client.post(f"/api/sessions/{scanned_session_id}/grading/run", json={"failed_only": True, "enhance_images": False})
    recovered = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert recovered["result"]["summary"]["graded"] == 1
    assert recovered["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(scanned_session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {"SYN-001": 85.0, "SYN-002": 70.0}
```

Add fixtures `prepared_session_id` and `scanned_session_id` to conftest; they must call only HTTP helpers for session/config/template/scan setup.

- [x] **Step 2: Run both tests and verify RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_failure_recovery.py -q
```

Expected: FAIL because scan/grading runner wrappers and persistence query helpers are not implemented.

- [x] **Step 3: Implement deterministic scan and grading boundaries**

In `harness.py`, implement a `SyntheticScanner` whose `analyze(students)` returns two `ExamPaperGroup` values pointing at generated front/back PNGs and preserving the two synthetic student IDs. `make_scan_runner(paths, controls)` must decrement `scan_failures_remaining` and raise `RuntimeError("synthetic scan failure")` before calling the real runner; otherwise it calls:

```python
return run_scan_analysis(
    **kwargs,
    scanner_factory=lambda **scanner_kwargs: SyntheticScanner(paths, scanner_kwargs),
)
```

Implement `SyntheticGradingService`. On the first full run it creates a graded paper/result for `SYN-001` with six valid details: Q1 scores 12/17 with `needs_human_review=True`, Q2-Q5 score 17 each, and Q6 scores 5/15, for an initial total of 85; it then creates a failed paper for `SYN-002`. On `failed_only=True` it finds only the failed paper, saves six valid detail scores totaling 70 and marks that paper graded. It yields the production event shapes `graded`, `grading_failed` and `session_completed`; it never rewrites the already successful result. Use `GradingResult` and `QuestionGradingDetail` rather than raw result/detail INSERTs. The current production runner lives in `backend/jobs/grading_run.py`; the earlier `backend/jobs/grading.py` reference was a filename drift only.

`make_grading_runner(controls)` must call the real runner with `service_factory=SyntheticGradingService`. Add these read-only temporary DB helpers to `ApiE2EHarness`:

```python
def paper_statuses(self, session_id: int) -> list[str]:
    with self.db._connect() as connection:
        rows = connection.execute("SELECT processing_status FROM exam_papers WHERE session_id = ? ORDER BY processing_status, id", (session_id,)).fetchall()
    return [str(row["processing_status"]) for row in rows]

def result_scores(self, session_id: int) -> dict[str, float]:
    with self.db._connect() as connection:
        rows = connection.execute(
            "SELECT s.student_code, sr.student_score FROM session_results sr JOIN students s ON s.id = sr.student_id WHERE sr.session_id = ? ORDER BY s.student_code",
            (session_id,),
        ).fetchall()
    return {str(row["student_code"]): float(row["student_score"]) for row in rows}
```

- [x] **Step 4: Verify recovery tests GREEN and run the whole P1-28 slice**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_failure_recovery.py tests/api_e2e/test_five_flow.py -q
```

Expected: all current P1-28 tests pass; the successful result remains 85 after failed-only recovery.

- [x] **Step 5: Commit recovery coverage**

```powershell
git add -- tests/api_e2e docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --check
git commit -m "test: cover P1-28 failure recovery"
```

Task 3 evidence: the required RED run produced `1 failed, 1 error, 1 warning` because the deterministic scan boundary and persistence query helper were absent. After implementation, the recovery/five-flow slice produced `3 passed, 1 warning`; the focused scan/grading/API E2E regressions produced `18 passed, 1 warning`; and the plan-specified affected API/Job regression set produced `69 passed, 1 warning`. The warning is the existing dependency-level `StarletteDeprecationWarning` from `fastapi.testclient`. No production file or real `user_data/` path was read or modified.

---

### Task 4: Complete review, report and controlled download E2E

**Files:**
- Modify: `tests/api_e2e/harness.py`
- Modify: `tests/api_e2e/test_five_flow.py`
- Modify: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`

**Interfaces:**
- Consumes: real ReviewApplicationService, ReportGenerator, report Job handler, JobFileService and XLSX download route.
- Produces: HTTP proof that teacher score 90 is persisted and exported in the downloaded workbook.

- [x] **Step 1: Extend the five-flow test with failing review/export assertions**

After successful failed-only recovery, add:

```python
questions = api_e2e.client.get(f"/api/sessions/{session_id}/review/questions")
assert questions.status_code == 200
assert questions.json()["items"][0]["question_id"] == "Q1"
items = api_e2e.client.get(f"/api/sessions/{session_id}/review/questions/Q1/items")
assert items.status_code == 200
first = next(item for item in items.json()["items"] if item["student_code"] == "SYN-001")
confirmed = api_e2e.client.post(
    f"/api/sessions/{session_id}/review/questions/Q1/confirm",
    json={"items": [{"result_id": first["result_id"], "detail_id": first["detail_id"], "score_awarded": 17, "deduction_reason": "synthetic teacher confirmation"}]},
)
assert confirmed.status_code == 200
assert confirmed.json()["updated_details"] == 1
assert api_e2e.result_scores(session_id)["SYN-001"] == 90.0

exported = api_e2e.client.post(f"/api/sessions/{session_id}/reports/export")
report_job = api_e2e.poll_job(exported.json()["id"], "succeeded")
assert report_job["result"]["download_url"] == f"/api/jobs/{report_job['id']}/download"
download = api_e2e.client.get(report_job["result"]["download_url"])
assert download.status_code == 200
assert download.headers["cache-control"] == "no-store"
assert download.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
assert api_e2e.xlsx_score(download.content, "SYN-001") == 90.0
```

- [x] **Step 2: Run the test and verify RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_five_flow.py -q
```

Expected: FAIL at missing full-flow setup or `xlsx_score()`.

- [x] **Step 3: Implement the XLSX verifier and finish the single success workflow**

Implement `xlsx_score(content: bytes, student_code: str) -> float` using `openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)`, select the current session-report sheet `成绩与小题明细`, map header cells to indexes, find the row whose `学号` equals the supplied code, and return the `总分` cell as float. Always close the workbook in `finally`. The earlier `成绩总表` / `学生得分` wording belongs to the legacy non-session `ReportGenerator.export()` path; the real report Job calls `ReportGenerator.export_session()`, so Task 4 follows the existing production session-report contract and does not change report formats.

Refactor repeated setup into `ApiE2EHarness.create_configured_session()`, `scan(session_id)` and `grade(session_id, failed_only=False)` helpers; each helper submits through HTTP and calls `poll_job()`. The success test must call these helpers in business order and retain explicit assertions at each boundary.

- [x] **Step 4: Verify the complete success path GREEN**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_five_flow.py -q
```

Expected: success path passes; downloaded session workbook contains 90 for `SYN-001`; no public response contains the temporary root.

- [x] **Step 5: Commit the complete five-flow path**

```powershell
git add -- tests/api_e2e docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --check
git commit -m "test: verify review and report API flow"
```

Task 4 evidence: the required RED run produced `1 failed, 1 warning` because `ApiE2EHarness.create_configured_session()` did not yet exist. After the HTTP helpers and XLSX verifier were implemented, the complete five-flow test produced `1 passed, 1 warning`. An intermediate focused run correctly exposed that the synthetic confidence values used the 0–1 candidate-score scale while production `confidence_score` uses 0–100; correcting the synthetic boundary to 50/100 made Q1 the only initial review item and preserved production behavior. The plan-specified affected API/Job regression set produced `69 passed, 1 warning`. The warning is the existing dependency-level `StarletteDeprecationWarning` from `fastapi.testclient`. The downloaded workbook was closed in `finally`; fixture teardown shut down JobManager; all writes stayed under pytest temporary storage; no production file or real `user_data/` path was read or modified.

---

### Task 5: Prove restart recovery through the jobs API

**Files:**
- Create: `tests/api_e2e/test_restart_recovery.py`
- Modify: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`

**Interfaces:**
- Consumes: `JobStore.create_job()`, `mark_running()`, `finish()`, `JobManager(cleanup_interrupted=True)` and jobs GET route.
- Produces: restart contract for queued/running/succeeded records through a new TestClient.

- [x] **Step 1: Write the failing restart test**

```python
def test_restart_marks_inflight_jobs_failed_and_preserves_terminal(tmp_path) -> None:
    store = JobStore(tmp_path / "restart.db")
    queued = store.create_job("scan_analysis", {"session_id": 1})
    running = store.create_job("grading_run", {"session_id": 1})
    succeeded = store.create_job("report_export", {"session_id": 1})
    assert store.mark_running(running.id) is True
    store.finish(succeeded.id, "succeeded", result={"session_id": 1})

    restarted = JobManager(JobStore(store.db_path), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: restarted
    with TestClient(app) as client:
        assert client.get(f"/api/jobs/{queued.id}").json()["status"] == "failed"
        assert client.get(f"/api/jobs/{running.id}").json()["status"] == "failed"
        assert client.get(f"/api/jobs/{succeeded.id}").json()["status"] == "succeeded"
        assert client.get(f"/api/jobs/{running.id}").json()["error"] == "Job failed; see local logs for details."
    restarted.shutdown()
```

- [x] **Step 2: Run the test and confirm it exercises the restart boundary**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e/test_restart_recovery.py -q
```

Expected: if it passes immediately, temporarily construct the restarted manager with `cleanup_interrupted=False` and confirm queued/running assertions fail, then restore `cleanup_interrupted=True`; this is the required RED proof for existing behavior.

- [x] **Step 3: Keep the minimal regression test and verify GREEN**

No production change is expected. Keep the test using `cleanup_interrupted=True`, add `try/finally` around manager shutdown, and assert the failed public error contains neither `tmp_path` nor stored internal error text.

- [x] **Step 4: Run all P1-28 tests**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/api_e2e -q
```

Expected: all P1-28 tests pass with no skipped tests.

- [x] **Step 5: Commit restart coverage**

```powershell
git add -- tests/api_e2e/test_restart_recovery.py docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --check
git commit -m "test: cover API job restart recovery"
```

---

### Task 6: Run package gates, independently review, and hand off

**Files:**
- Modify: `docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md`
- Modify only if a genuine structural fact changed: `ARCHITECTURE.md`

**Interfaces:**
- Consumes: complete P1-28 commit range, package definition, design, handoff validator and repository test gates.
- Produces: reviewed functional SHA and a validator-compatible `verified_pending_integration` handoff commit.

- [x] **Step 1: Run P1-28 and affected regressions**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest `
  tests/api_e2e `
  tests/test_api_write_routes.py `
  tests/test_api_config_generation_jobs.py `
  tests/test_api_template_region_routes.py `
  tests/test_api_scan_jobs.py `
  tests/test_api_grading_jobs.py `
  tests/test_api_review_routes.py `
  tests/test_api_report_jobs.py `
  tests/test_api_file_downloads.py `
  tests/test_api_job_lifecycle.py -q
```

Expected: all selected tests pass, 0 failed and 0 skipped.

- [x] **Step 2: Run repository guards and compare real database fingerprints**

```powershell
git diff --check origin/main...HEAD
git status --short -- user_data
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
```

From the root checkout, repeat `Get-Item` and `Get-FileHash -Algorithm SHA256` for both real databases. Expected: source diff has no whitespace errors; feature worktree `user_data` is clean; quick smoke passes; size/UTC mtime/SHA-256 equal the claim record.

- [x] **Step 3: Record `waiting_review` in the functional commit**

Check every plan box through this step, add a concise verification evidence section, and set the handoff block to:

```markdown
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**夜间动作：** report_only
```

Keep the original Stash baseline unchanged, then commit all remaining test/plan changes as the final functional commit.

Task 6 Steps 1–3 evidence: the complete P1-28 and affected API/Job selection produced `70 passed, 0 failed, 0 skipped, 1 warning` in 12.11s. `git diff --check origin/main...HEAD` passed and the feature worktree had no `user_data/` status. Quick smoke passed in 4.82s, including documentation governance, compilation of 413 first-party Python files and idempotent initialization/integrity checks against isolated copies of both databases; the full pytest stage was intentionally skipped by `--skip-tests`. Before and after smoke, the root grading database remained 2863104 bytes with UTC mtime `2026-07-10T07:10:41.1221109Z` and SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`; the root question-bank database remained 3461120 bytes with UTC mtime `2026-07-08T11:58:06.3320883Z` and SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`. The warning is the existing dependency-level `StarletteDeprecationWarning` from `fastapi.testclient`. No production file changed, so `ARCHITECTURE.md` was not modified. Independent review remains pending and Tasks 4–6 were not started.

- [ ] **Step 4: Request independent code review and fix all Critical/Important findings**

Set `$headSha = git rev-parse HEAD`, then use `superpowers:requesting-code-review` with `BASE_SHA=85d7cc664f24313408a01e75127b03fa7620bf12` and `HEAD_SHA=$headSha`. Reviewer must compare package map, design, plan and code; explicitly inspect isolation, real-vs-fake boundaries, Job polling, completed-with-failures semantics, final workbook score and resource cleanup. Any fix follows RED→GREEN and reruns its affected tests.

- [ ] **Step 5: Create the final handoff-only commit**

After review passes, record the full reviewed functional SHA and set `verified_pending_integration`, `自动验证: passed`, `独立复审: passed`, `用户验收: not_required`, `真实数据指纹: unchanged`, `夜间动作: independent_candidate_allowed`. This commit may modify only this plan.

```powershell
git add -- docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md
git diff --cached --name-only
git commit -m "docs: hand off P1-28 for integration"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-14-p1-28-five-flow-api-e2e-implementation.md `
  --repo .
```

Expected: staged list contains only the plan; validator returns `ok=true`, `state="verified_pending_integration"`, and no issues.

- [ ] **Step 6: Integrate through the standard channel**

Create `codex/p1-28-integration` from current `origin/main`, merge the complete P1-28 chain, run the affected regression command, then run one complete:

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py
```

Recheck real database fingerprints, update `ARCHITECTURE.md` only if production structure changed, and update `EXECUTION_INDEX.md` to mark P1-28 merged and choose the next dependency-valid action. Push the integration branch, open and merge a PR to GitHub `main`, then fetch/prune and fast-forward local `main` and active channels. Do not delete any worktree or branch unless `git branch --merged origin/main`, source status, `user_data` and reparse-point guards all pass.
