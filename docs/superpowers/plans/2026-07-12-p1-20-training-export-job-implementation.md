# P1-20 Training Export Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已确认的训练任务接入可查询、可取消、可重试的 `training_export` Job，复用现有 Word/Markdown 导出并返回受控下载 URL。

**Architecture:** Training API 只接受服务器端 `task_id`、可选 `variant_id`和既有导出选项，再提交小型、无路径的 Job payload。Job handler 复用 `TrainingExportService`，先在输出根内的 job 临时目录生成文件，在协作式取消安全边界后原子重命名为 job 专属目录，并在一个 SQLite 事务内改写导出记录路径。公开 Job 结果只返回任务/导出记录 ID、安全文件名和 `/api/jobs/{id}/download`；内部路径和原始异常不出现在 API 中。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 JobManager/TrainingExportService/Word/Markdown exporters、pytest。

**执行包：** P1-20
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** b0d188b9568ff0cc6925825476904648daf282b1
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-20：已确认 Training task/variant 的 Word 或 Markdown 导出 Job、进度、协作式取消、重试和受控下载；Graph API 属于 P1-21。
- 复用 `TrainingExportService`、`export_training_docx()` 和 `export_training_markdown()`；不改选题算法、任务快照、教师/学生内容口径或追踪码。
- 只支持现有 `docx` 和 `markdown` 材料；整任务仍复用现有 ZIP bundle，不引入 PDF、Excel 或新导出格式。
- Job payload 只保存服务器 ID、格式、audience 和 `retry_of_job_id`；不接受或保存客户端路径、题干、学生姓名、密钥或导出正文。
- 整任务导出：`variant_id` 为空，调用现有 `export_task_bundle()`，最终发布一个 ZIP；单 variant 导出：必须同时提供单个 `format` 和 `audience`，最终发布一个 `.docx` 或 `.md`。
- running 取消不强杀 exporter；在导出调用返回后、最终目录发布前再确认取消。已请求取消时删除 job 临时目录，不发布可下载文件。
- 导出文件只发布到临时 `PathManager.outputs_dir / "training"`；下载服务必须校验该受控根、扩展名和真实路径。
- 不修改 SQLite Schema、Streamlit 行为、Job 通用状态机或任何真实 `user_data/`。
- 所有测试只使用 `tmp_path` 临时题库、临时输出和合成题目；不打开、初始化或写入真实数据库。
- 功能分支不更新 `EXECUTION_INDEX.md`；共享 Index 和最终架构状态由 integration 整理。

---

## File Structure

- Create: `backend/jobs/training_export.py`：验证小型 payload，编排同步导出、进度、取消安全边界、目录原子发布和公开结果摘要。
- Modify: `question_bank/services/training_export_service.py`：增加输出记录路径前缀的事务性重定位与失败收敛，不改导出内容。
- Modify: `backend/jobs/default_handlers.py`：注册 `training_export` 并注入题库路径与 Training 输出根。
- Modify: `backend/api/schemas/training.py`：增加严格导出提交模型。
- Modify: `backend/api/schemas/__init__.py`：导出 Training 导出请求模型，保持 schema 包公开入口完整。
- Modify: `backend/api/routers/training.py`：增加任务/变体导出提交和失败 Job 重试端点。
- Modify: `backend/api/routers/jobs.py`：阻止通用提交端点绕过 Training 验证，并显式投影公开 payload/result。
- Modify: `backend/files/service.py`：为 `training_export` 增加受控输出根和 `.docx/.md/.zip` 扩展名规则。
- Modify: `backend/file_access.py`：在共享媒体类型表增加 DOCX、Markdown 和 ZIP；各资源仍须同时通过自己的扩展名白名单。
- Modify: `backend/api/dependencies.py`：将 `outputs_dir / "training"` 注入 Job handler 和下载服务。
- Modify: `backend/api/routers/files.py`：声明 Word、Markdown 和 ZIP 的二进制响应契约。
- Create: `tests/test_training_export_job.py`：成功、缺素材、失败、取消、原子发布和路径重定位。
- Create: `tests/test_api_training_export_jobs.py`：提交、查询、重试、脱敏、并发收敛和下载契约。
- Modify: `tests/test_training_export_service.py`：服务路径重定位和失败记录回归。
- Modify: `tests/test_api_file_downloads.py`：Training 下载、越界、过期、错误类型和 `no-store`。
- Modify: `tests/test_api_openapi_contract.py`：新端点、严格请求、稳定错误和多媒体 200 契约。
- Modify: `ARCHITECTURE.md`：实现验证后记录 P1-20 增量事实。
- Modify: `docs/superpowers/plans/2026-07-12-p1-20-training-export-job-implementation.md`：checkbox、验证、复审和交接证据。

## Public Interfaces

```python
class TrainingExportSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variant_id: int | None = Field(default=None, ge=1)
    format: Literal["docx", "markdown"] = "docx"
    audience: Literal["student", "teacher"] | None = None

run_training_export_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    output_root: Path,
    service_factory: Callable[[Path, Path], TrainingExportService] = TrainingExportService,
) -> dict[str, object]
```

```text
POST /api/training/tasks/{task_id}/exports
POST /api/training/exports/jobs/{job_id}/retry
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
GET  /api/jobs/{job_id}/download
```

Stable failures:

```text
404 training_task_not_found
404 training_export_job_not_found
409 training_export_retry_not_available
422 training_export_request_invalid
503 training_database_unavailable
```

---

### Task 1: Claim Plan And Establish RED Job Contracts

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-p1-20-training-export-job-implementation.md`
- Create: `tests/test_training_export_job.py`

**Interfaces:**
- Establishes this package's immutable handoff identity and stash baseline before source edits.
- Specifies `run_training_export_job()` behavior for task bundles and single variants.

- [x] **Step 1: Commit the plan-only claim**

```powershell
git add docs/superpowers/plans/2026-07-12-p1-20-training-export-job-implementation.md
git commit -m "docs: claim P1-20 training export job"
```

Expected: the first first-parent commit after `origin/main` changes only this plan and `tools/handoff_status.py` reports a valid `in_progress` block.

- [x] **Step 2: Write failing handler tests**

Create temporary task fixtures by reusing `TrainingTaskService.create_task()`. Assert that a whole-task payload publishes one ZIP, a variant payload publishes exactly one requested Word/Markdown file, progress is monotonic, the final result contains IDs plus one internal file path, and no client path is consumed.

```python
def test_training_export_job_publishes_variant_after_cancel_boundary(tmp_path):
    result = run_training_export_job(
        context=context_for({
            "task_id": task.id,
            "variant_id": task.variants[0].id,
            "format": "markdown",
            "audience": "teacher",
        }),
        question_bank_db_path=db_path,
        output_root=tmp_path / "outputs" / "training",
    )
    assert result["task_id"] == task.id
    assert Path(result["file_path"]).suffix == ".md"
    assert Path(result["file_path"]).is_file()
```

- [x] **Step 3: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_training_export_job.py -q
```

Expected: FAIL because `backend.jobs.training_export` and its runner do not exist.

---

### Task 2: Atomic Export Runner, Progress And Cancellation

**Files:**
- Create: `backend/jobs/training_export.py`
- Modify: `question_bank/services/training_export_service.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `tests/test_training_export_job.py`
- Modify: `tests/test_training_export_service.py`

**Interfaces:**
- Consumes only `task_id`, optional `variant_id`, one existing format, optional variant audience and optional `retry_of_job_id`.
- Produces a job-owned file tree and a result containing `task_id`, optional `variant_id`, export record IDs, `file_path`, and `filename`.

- [x] **Step 1: Add failing atomicity, cancellation and degradation cases**

Use injected fake exporters to prove: failure leaves no final `job-{id}` directory; cancellation after the blocking exporter returns leaves no published output; a successful publish renames the directory before the job result is returned; missing optional media still uses existing exporter fallback; traversal-like filenames cannot escape the job directory.

```python
def test_cancel_after_export_does_not_publish_file(tmp_path):
    context = cancelling_context(payload)
    with pytest.raises(JobCancelled):
        run_training_export_job(
            context=context,
            question_bank_db_path=db_path,
            output_root=tmp_path / "training",
            service_factory=fake_service_factory,
        )
    assert not (tmp_path / "training" / f"job-{context.job_id}").exists()
```

- [x] **Step 2: Implement minimal runner and service relocation transaction**

In `run_training_export_job()`, validate the mode combination, create `output_root/.job-{id}-<random>` on the same filesystem, report `starting/exporting/publishing`, call `export_task_bundle()` or `export_variant()`, identify the single primary record, call `context.raise_if_cancelled()`, rename the staging directory to `job-{id}`, then transactionally replace every succeeded `training_exports.output_path` whose resolved path is under the old root with the matching path under the final root. Reject any exporter path outside staging and remove the final directory if the database relocation fails.

```python
def relocate_export_outputs(
    self,
    export_ids: Iterable[int],
    old_root: Path,
    new_root: Path,
) -> list[dict[str, Any]]:
    ids = sorted({int(value) for value in export_ids})
    if not ids:
        return []
    old_resolved = old_root.resolve()
    with connect(self.db_path) as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, output_path FROM training_exports WHERE id IN ({placeholders}) "
            "AND status = 'succeeded' AND output_path IS NOT NULL",
            ids,
        ).fetchall()
        updates = []
        for row in rows:
            path = Path(row["output_path"]).resolve()
            relative = path.relative_to(old_resolved)
            updates.append((str(new_root / relative), int(row["id"])))
        conn.executemany(
            "UPDATE training_exports SET output_path = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            updates,
        )
    return [self._export_record(export_id) for _, export_id in updates]
```

The explicit export-ID filter ensures unrelated historical rows are never rewritten.

- [x] **Step 3: Register the default handler**

Add `training_export_runner` and `training_export_service_factory` injection points to `register_default_job_handlers()`. Register `training_export` with `question_bank_db_path` and `training_output_root`; no exporter, database connection or path is constructed at module import time.

- [x] **Step 4: Run GREEN and existing exporter regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_training_export_job.py tests/test_training_export_service.py tests/test_question_bank_exporter.py -q
```

Expected: all pass; cancellation publishes nothing and existing Word/Markdown content tests remain unchanged.

---

### Task 3: Dedicated Submit, Retry And Public Job Projection

**Files:**
- Modify: `backend/api/schemas/training.py`
- Modify: `backend/api/routers/training.py`
- Modify: `backend/api/routers/jobs.py`
- Create: `tests/test_api_training_export_jobs.py`
- Modify: `tests/test_api_jobs.py`

**Interfaces:**
- Submits a task bundle when `variant_id` is absent and requires a single audience when it is present.
- Retries only failed/cancelled `training_export` jobs by copying their allowlisted payload and adding `retry_of_job_id`.
- Public Job payload/result never contains `file_path`, task snapshots, student names or raw exporter errors.

- [x] **Step 1: Write failing endpoint and sanitization tests**

Cover task 404, variant ownership, invalid bundle audience, valid task/variant submissions, generic endpoint rejection, failed-job retry, succeeded/running/wrong-type retry rejection, and public query/cancel responses.

```python
def test_training_export_submit_persists_only_server_identifiers(training_client):
    response = training_client.post(
        f"/api/training/tasks/{task_id}/exports",
        json={"variant_id": variant_id, "format": "markdown", "audience": "teacher"},
    )
    assert response.status_code == 202
    assert response.json()["payload"] == {
        "task_id": task_id,
        "variant_id": variant_id,
        "format": "markdown",
        "audience": "teacher",
    }
```

- [x] **Step 2: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_export_jobs.py tests/test_api_jobs.py -q
```

Expected: new endpoint tests fail with 404 and generic submission still accepts the dedicated type.

- [x] **Step 3: Implement strict request, submit and retry routes**

Use a Pydantic `model_validator(mode="after")` to reject `audience` without `variant_id` and require it with `variant_id`. Load the task through `TrainingTaskService` before submit; verify the variant belongs to that task. Retry only source jobs with `job_type == "training_export"` and terminal retryable state, then submit a new job with allowlisted fields plus `retry_of_job_id`.

- [x] **Step 4: Add explicit public projections and block generic submission**

Add `training_export` to the dedicated-type set. Allowlist payload fields and, only for a succeeded job with a primary file, return `task_id`, optional `variant_id`, export IDs, safe filename and `download_url`. Keep `public_job_error()` generic.

- [x] **Step 5: Run GREEN and P1-19/API regression**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_training_export_jobs.py tests/test_api_training_routes.py tests/test_api_jobs.py tests/test_job_manager.py -q
```

Expected: all pass; P1-19 diagnosis/plan/task behavior is unchanged.

---

### Task 4: Controlled Download, OpenAPI And Architecture Facts

**Files:**
- Modify: `backend/files/service.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/routers/files.py`
- Modify: `tests/test_api_file_downloads.py`
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`

**Interfaces:**
- Resolves `training_export.file_path` only under `outputs_dir / "training"` with `.docx`, `.md` or `.zip`.
- Declares the existing report XLSX plus DOCX, Markdown and ZIP 200 content types without changing the download URL.

- [x] **Step 1: Write failing download and OpenAPI cases**

Add succeeded Word/Markdown/ZIP downloads, nonterminal 409, unsupported job 404, expired 410, outside-root 403, wrong suffix 415, filename/no-store assertions, and OpenAPI content-type assertions. Add both Training export operations to the expected operation set and assert their request schemas forbid extra properties.

- [x] **Step 2: Run RED**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_file_downloads.py tests/test_api_openapi_contract.py -q
```

Expected: Training download cases fail because only report XLSX is allowlisted and the new operations are absent from OpenAPI.

- [x] **Step 3: Implement root-specific file rules and media declarations**

Extend `JobFileRule` with a logical root selector. Construct `JobFileService(reports_dir, training_outputs_dir)` from one `PathManager` snapshot; keep report behavior unchanged. Add the exact media types for DOCX, Markdown and ZIP to the route `responses[200].content` map.

- [x] **Step 4: Record only implemented architecture facts and run GREEN**

Add a P1-20 increment line and update the run/job/interface tables only where behavior is now verified. Do not update package counts or Index on the feature branch.

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_file_downloads.py tests/test_api_openapi_contract.py tests/test_api_training_export_jobs.py -q
```

Expected: all pass with no internal path in JSON and `Cache-Control: no-store` on downloads/errors.

---

### Task 5: Verification, Review And Handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-12-p1-20-training-export-job-implementation.md`

**Interfaces:**
- Produces a reviewed functional SHA and a final handoff-only commit validated as `verified_pending_integration`.

- [x] **Step 1: Run focused and affected regressions**

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_training_export_job.py tests/test_training_export_service.py tests/test_question_bank_exporter.py tests/test_api_training_export_jobs.py tests/test_api_training_routes.py tests/test_api_file_downloads.py tests/test_api_jobs.py tests/test_job_manager.py tests/test_api_openapi_contract.py -q
git diff --check
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
```

Expected: all focused/affected tests pass; diff check and quick smoke exit 0. Integration runs the wave-end complete smoke because the package changes shared handler/API/download registration.

- [x] **Step 2: Recheck package scope and real-data guards**

Confirm `git diff --name-only origin/main...HEAD`, every package commit, staged files and new stashes exclude `user_data/`. Compare the root real database sizes, UTC mtimes and SHA-256 with the claim baseline; any difference stops integration.

- [x] **Step 3: Create the functional commit**

Update this plan evidence and set the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `真实数据指纹: unchanged`. Commit only P1-20 code, tests, architecture and this plan.

- [x] **Step 4: Perform independent review and fix findings with RED/GREEN**

Review `origin/main..HEAD` against this plan and the Phase map. Fix every Critical/Important finding by first adding a failing test, then rerun the affected regression until Critical/Important are zero.

- [x] **Step 5: Create the final handoff-only commit**

After review passes, update only this plan with the full reviewed functional SHA and `verified_pending_integration`; keep user acceptance `not_required`, automated verification/review `passed`, real-data fingerprint `unchanged` and the immutable stash baseline. Commit only this plan and run:

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py --plan docs/superpowers/plans/2026-07-12-p1-20-training-export-job-implementation.md --repo .
```

---

## Rollback

- Revert the P1-20 functional commit chain; no database migration or Schema rollback is required.
- Existing Streamlit Training page and synchronous export service remain usable because the package adds a Job adapter and compatible service methods.
- All implementation tests write only temporary databases and outputs. No real training task or export is created during verification.
- A failed/cancelled Job may leave failed `training_exports` audit rows in its temporary test database, but it must not leave a published job directory or downloadable file.
- If focused regression, quick smoke, handoff validation, independent review or real-data fingerprint checks fail, keep the feature worktree intact and do not integrate.

## Implementation Evidence

- Baseline: existing Training export/Training API/Job/download suite `60 passed` before source changes.
- RED/GREEN: default-handler/variant Job tests first failed `2` cases on the missing `training_output_root`; dedicated API tests first failed `6` cases with missing routes and generic endpoint bypass; controlled-download service first failed on the missing Training root, then exposed and closed root-selector/media-type gaps; the exact OpenAPI media contract first failed on the three new types. Each cycle subsequently passed.
- Focused regression: final Training export/Training API/download/Job/OpenAPI set `107 passed`; adjacent report/config/import/tagging/App handler regression `45 passed`.
- Quick smoke: document governance, static compile of `367` first-party Python files, two temporary database copies' idempotent initialization and `integrity_check=ok` passed. Full pytest is reserved for the integration wave-end gate under repository policy.
- Independent review: first review of `3e81689158bb62c2ff116d89b207650dca8960f8` found `0 Critical / 2 Important / 0 Minor`: abort could clobber an earlier successful task/variant state, and a late final state-write exception could leave succeeded rows pointing to deleted staging. Three focused tests reproduced and closed both defects in `23e11d3`. Fresh re-review found `0 Critical / 1 Important / 0 Minor`: a later audience record-creation failure could occur after an earlier audience succeeded but before the service returned IDs to the runner; a job-level test reproduced and closed it in `67d6b0c`. The next full re-review found `0 Critical / 1 Important / 0 Minor`: bundle audit-record finalization itself was outside cleanup protection; a job-level test reproduced and closed it in `5a24514`. Release re-review then found `0 Critical / 1 Important / 0 Minor`: record insert/update committed before separate record readback, so a readback exception could hide the new ID. Variant and bundle RED tests reproduced it; create/retry/finish now read the record in the same SQLite transaction, so readback failure rolls back instead of leaving hidden rows. Final fresh review of `5c7d44ed81ce276cf6161c6a28f36cefc04aa4f2` completed at `0 Critical / 0 Important / 0 Minor`; verdict `Ready to merge: Yes`.
- Real data: root grading DB remained `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`; root question-bank DB remained `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`. Worktree `user_data/` status is empty and the stash baseline is unchanged.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-20
**交接状态：** verified_pending_integration
**功能提交：** 5c7d44ed81ce276cf6161c6a28f36cefc04aa4f2
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
