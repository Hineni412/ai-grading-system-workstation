# P1-18 Question Import And Tagging Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P1-16 已暂存的题库导入请求接入 `question_import` 长任务，并把题目 AI 打标接入独立的 `tagging_sync` 长任务，使两类任务都可查询、取消和重试，同时保持“只有 complete 才自动保存”的现有规则。

**Architecture:** 新增两个职责单一的 Job runner：导入 runner 只根据服务器生成的 `request_id` 重新校验受控上传资源并调用现有批量导入器；打标 runner 只读取显式题目 ID，按有界批次调用现有 `AITaggingService`，每批之间检查协作式取消，并通过现有 `QuestionService.save_tag_analysis(resolve_skills=False)` 保存 complete 结果。Question Bank router 提供专用提交/重试入口；通用 Job 查询与取消继续复用现有 API，公开 payload/result 使用显式允许列表，不公开文件路径、题干、模型配置或原始异常。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 JobManager/QuestionBankWriteService/BatchImporter/AITaggingService/QuestionService、pytest。

**执行包：** P1-18
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** a93d40b0e60bfba614e3732529188b8c9c40f415
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-18 的 `question_import` 与 `tagging_sync` Job、专用提交/重试 API、进度、部分失败和脱敏摘要；不提前实现 P1-24 LLM Gateway。
- 不修改 SQLite Schema，不改变题库标签质量门槛、题号契约、活动知识点语义、评分规则或阅卷主流程。
- `question_import` payload 只允许服务器生成的 `request_id` 和重试来源 ID；`tagging_sync` payload 只允许题目 ID、重试来源 ID及有界批次控制，不保存文件路径、题干、密钥或模型设置。
- AI 打标仍只在 `is_auto_saveable_result(result)` 为真时保存，并且必须调用 `save_tag_analysis(..., resolve_skills=False)`；不得恢复旧技能 AI 消歧或写 `question_skill_links`。
- 协作式取消不强杀线程或在途模型请求；取消请求到达后，当前在途批次返回时不保存该批结果，并且不再启动后续批次。已经在更早安全边界保存的 complete 标签可以保留，重新提交会跳过它们。
- 导入和打标必须幂等：相同导入请求重复执行不得重复建试卷/题目；已拥有完整核心标签的题目不得再次调用 AI；重复重试只处理仍失败或仍缺完整标签的题目。
- 所有导入、数据库、归档、富文本、上传和 AI 测试只使用 `tmp_path`、生成 DOCX/PDF 或假 AI；禁止打开真实题库 SQLite，禁止调用真实模型或读取真实密钥。
- 不修改、删除、暂存、提交或 stash 任何 `user_data/`；结束时根目录两库大小、UTC 修改时间与 SHA-256 必须和领取基线一致。
- 功能分支不更新 `EXECUTION_INDEX.md`，不 push、不创建 PR、不合入 integration/main、不同步或清理任何 worktree/branch。

---

## File Structure

- Modify: `question_bank/services/question_write_service.py`：公开只读加载并复核服务器导入请求资源的内部接口。
- Create: `backend/jobs/question_import.py`：受控请求解析、现有批量导入调用、题目 ID 汇总、进度与安全结果摘要。
- Create: `backend/jobs/tagging_sync.py`：待打标题目加载、有界批次、complete-only 保存、失败分类、取消和重试摘要。
- Modify: `backend/jobs/default_handlers.py`：注册两个 runner 并注入题库路径、数据根和 AI 工厂。
- Modify: `backend/api/dependencies.py`：把题库数据库路径和数据根传入默认 handler 注册。
- Modify: `backend/api/schemas/question_bank.py`：新增严格的任务提交/重试请求模型。
- Modify: `backend/api/schemas/__init__.py`：导出新增 schema。
- Modify: `backend/api/routers/question_bank.py`：新增专用提交/重试端点和稳定错误映射。
- Modify: `backend/api/routers/jobs.py`：拒绝通用提交这两类任务，并显式投影安全 payload/result。
- Create: `tests/test_question_import_job.py`：导入 runner 的资源、幂等、失败、取消和重试行为。
- Create: `tests/test_tagging_sync_job.py`：打标 runner 的批次、complete-only、部分失败、取消、幂等和脱敏行为。
- Create: `tests/test_api_question_bank_jobs.py`：专用 API、重试校验、公开摘要与通用入口守卫。
- Modify: `tests/test_api_openapi_contract.py`：新端点及敏感字段守卫。
- Modify: `ARCHITECTURE.md`：只记录已实现的 P1-18 增量边界与运行事实。
- Modify: `docs/superpowers/plans/2026-07-12-p1-18-question-import-tagging-implementation.md`：checkbox、RED/GREEN、验证、复审与交接证据。

## Public Interfaces

```python
QuestionBankWriteService.load_import_resource(request_id: str) -> QuestionImportResource

run_question_import_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    write_service: QuestionBankWriteService,
    importer: Callable[..., BatchImportResult] = import_scanned_papers,
) -> dict[str, object]

run_tagging_sync_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: Callable[[], AITaggingService],
    batch_size: int = 20,
) -> dict[str, object]

POST /api/question-bank/import-requests/{request_id}/jobs
POST /api/question-bank/question-import-jobs/{job_id}/retry
POST /api/question-bank/tagging-jobs
POST /api/question-bank/tagging-jobs/{job_id}/retry
```

Stable failures:

```text
404 question_import_request_not_found
404 question_import_job_not_found
404 tagging_sync_job_not_found
409 question_import_retry_not_available
409 tagging_sync_retry_not_available
422 dedicated_job_endpoint_required
422 question_tagging_request_invalid
503 job_type_not_supported
```

---

### Task 1: Controlled Import Resource And `question_import` Runner

**Files:**
- Modify: `question_bank/services/question_write_service.py`
- Create: `backend/jobs/question_import.py`
- Create: `tests/test_question_import_job.py`

**Interfaces:**
- Produces `QuestionImportResource(request_id, upload_id, filename, suffix, size, sha256, source_path)` only for server-internal code.
- Produces a safe Job result with `outcome`, counts, successful question IDs, failed count/category and `retryable`; no source path or parser exception text.

- [x] **Step 1: Write failing resource and runner tests**

Add tests that create a staged upload/request under `tmp_path`, reject missing/traversal/tampered request resources, run a fake importer, map imported source rows to stable numeric question IDs, report progress, return sanitized failures, and prove cancellation before import or before result publication produces no new DB rows.

```python
def test_question_import_job_uses_server_request_and_returns_safe_ids(tmp_path):
    service, request = stage_import_request(tmp_path)
    context = recording_context("question_import", {"request_id": request.request_id})
    result = run_question_import_job(
        context=context,
        question_bank_db_path=service.db_path,
        data_root=service.data_root,
        write_service=service,
        importer=fake_importer_creating_questions("1", "2"),
    )
    assert result["outcome"] == "complete"
    assert len(result["successful_question_ids"]) == 2
    assert str(tmp_path) not in json.dumps(result)
```

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_question_import_job.py -q
```

Expected: collection/import fails because `QuestionImportResource`, `load_import_resource` and the runner do not exist.

- [x] **Step 3: Implement minimal controlled loading and import orchestration**

Refactor the existing upload/request verification into a private shared loader. `load_import_resource()` must require a lowercase 32-hex request ID, require the request manifest to match its deterministic upload request, re-hash the staged source, constrain the resolved source below `<data_root>/question_bank/import_staging/uploads`, and return the path only to backend code. The runner checks cancellation, reports `loading/importing/indexing/complete`, calls the existing importer for exactly one `ScannedPaper`, checks cancellation before and after the import call, finds active questions by the returned stored source, and returns only safe IDs/counts/status. Importer exceptions are classified to a bounded category and raise a generic error so JobStore never persists the raw path/error.

- [x] **Step 4: Verify GREEN and existing P1-16/importer regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_question_import_job.py tests/test_api_question_bank_write_routes.py tests/test_question_bank_importer.py -q
```

Expected: all selected tests pass; repeated execution of the same request returns the same question rows without duplicates.

---

### Task 2: Batched `tagging_sync` Runner

**Files:**
- Create: `backend/jobs/tagging_sync.py`
- Create: `tests/test_tagging_sync_job.py`

**Interfaces:**
- Consumes payload `{question_ids: list[int], source_job_id?: int, retry_of_job_id?: int}`.
- Produces `{outcome, requested_count, skipped_complete_count, tagged_count, failed_count, successful_question_ids, failed_question_ids, failures, retryable}`.

- [x] **Step 1: Write failing tagging tests**

Cover: complete result saves raw tags without skill resolution; partial/invalid result is not saved; missing/deleted IDs are classified; fake rate-limit/timeout/parse/save errors are sanitized; progress is monotonic; an already complete question is skipped without calling fake AI; cancellation before a batch starts and cancellation arriving during fake AI prevent the current and later batch from saving; retrying all original IDs only calls AI for still-incomplete IDs.

```python
def test_tagging_sync_saves_only_complete_results(tmp_path):
    ids = seed_questions(tmp_path / "qb.db", 2)
    fake_ai = FakeAI({ids[0]: complete_result(), ids[1]: partial_result()})
    result = run_tagging_sync_job(
        context=recording_context("tagging_sync", {"question_ids": ids}),
        question_bank_db_path=tmp_path / "qb.db",
        ai_service_factory=lambda: fake_ai,
        batch_size=1,
    )
    assert result["successful_question_ids"] == [ids[0]]
    assert result["failed_question_ids"] == [ids[1]]
    assert load_tags(tmp_path / "qb.db", ids[1]) == []
```

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_tagging_sync_job.py -q
```

Expected: collection/import fails because the tagging runner does not exist.

- [x] **Step 3: Implement minimal sequential batch orchestration**

Normalize unique positive IDs with a hard maximum of 500. Query active questions and current core tag types from the temporary question-bank DB. Skip questions that already contain every `CORE_ANALYSIS_TAG_TYPES` value. For each batch, check cancellation, call one injected `AITaggingService.analyze_questions()` with `allow_batch_fallback=False`, `quality_retry_limit=1`, `enable_review=False`, then check cancellation again before any save. Save only `is_auto_saveable_result()` results through `QuestionService.save_tag_analysis(..., resolve_skills=False)`. Store only bounded `category` and `message` from `classify_tagging_error`/`sanitize_tagging_error`, never exception repr, prompt, question text or model credentials.

- [x] **Step 4: Verify GREEN and existing tagging/intake regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_tagging_sync_job.py tests/test_grading_paper_archive_intake.py tests/test_question_bank_ai_tagging_quality.py tests/test_tagging_batch_attempts.py tests/test_question_bank_service.py -q
```

Expected: all selected tests pass and existing complete-only behavior remains unchanged.

---

### Task 3: Handler Registration, Dedicated APIs And Safe Job Projection

**Files:**
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/api/dependencies.py`
- Modify: `backend/api/schemas/question_bank.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/routers/question_bank.py`
- Modify: `backend/api/routers/jobs.py`
- Create: `tests/test_api_question_bank_jobs.py`

**Interfaces:**
- Initial import submission accepts only path parameter `request_id`.
- Initial tagging submission accepts `{question_ids: list[int], source_job_id?: int}` with `extra="forbid"`.
- Retry accepts optional bounded `question_ids`; omitted means all source failures/original IDs.
- All four endpoints return HTTP 202 standard `JobResponse`.

- [x] **Step 1: Write failing registration and API contract tests**

Use a temporary `JobManager` and dependency overrides. Cover registration of exactly two new types; 202 submit/query/cancel; import source request validation; tagging source import validation; failed/partial/succeeded retry eligibility; wrong type/missing source; selected retry must be a subset of source failures; duplicate retries remain idempotent; generic `/api/jobs/question_import` and `/api/jobs/tagging_sync` are rejected; public payload/result contains no path, filename, SHA, question text, key/token/password/secret, or raw internal error.

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_question_bank_jobs.py -q
```

Expected: new routes return 404/405 and handlers are not registered.

- [x] **Step 3: Add strict schemas, thin routes and explicit projections**

Register both handlers with the existing question-bank DB path and data root; use a dedicated tagging AI factory so tests never touch active credentials. Routes validate source Job identity and safe retry sets before `manager.submit()`. Public `question_import` payload exposes only `request_id/retry_of_job_id`; public `tagging_sync` payload exposes only numeric IDs/source/retry IDs. Public result explicitly permits only outcome/counts/ID lists/failure categories and bounded sanitized messages. Add both types to the dedicated-endpoint guard in the generic jobs route.

- [x] **Step 4: Verify GREEN and affected Job/API regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_question_bank_jobs.py tests/test_api_question_bank_write_routes.py tests/test_api_jobs.py tests/test_api_job_lifecycle.py tests/test_job_manager.py tests/test_job_store.py -q
```

Expected: all selected tests pass; existing Job types remain queryable/cancellable.

---

### Task 4: OpenAPI, Architecture And Package Verification

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-18-question-import-tagging-implementation.md`

**Interfaces:**
- Produces four OpenAPI operations with strict bodies and stable 202/404/409/422/503 responses.
- Produces a `waiting_review` functional commit, independent-review evidence and final `verified_pending_integration` handoff commit.

- [x] **Step 1: Write failing OpenAPI assertions**

Assert all four paths/methods exist, operation IDs are unique, request schemas forbid additional properties, and no request/response property introduces path/destination/api-key/token/password/secret or raw question content.

- [x] **Step 2: Run OpenAPI test and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_openapi_contract.py -q
```

Expected: fails until the new operations and response declarations exist.

- [x] **Step 3: Record implemented architecture facts and run focused regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_question_import_job.py tests/test_tagging_sync_job.py tests/test_api_question_bank_jobs.py tests/test_api_question_bank_write_routes.py tests/test_question_bank_importer.py tests/test_grading_paper_archive_intake.py tests/test_question_bank_ai_tagging_quality.py tests/test_tagging_batch_attempts.py tests/test_api_openapi_contract.py -q
```

Expected: all selected tests pass.

- [x] **Step 4: Run affected API/Job regression, diff and quick smoke**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_app.py tests/test_api_read_routes.py tests/test_api_write_routes.py tests/test_api_question_bank_routes.py tests/test_api_question_bank_write_routes.py tests/test_api_question_bank_jobs.py tests/test_api_jobs.py tests/test_api_job_lifecycle.py tests/test_job_manager.py tests/test_job_store.py -q
git diff --check
..\..\runtime\python\python.exe tools/smoke_check.py --skip-tests
```

Expected: all tests pass; diff check and quick smoke exit 0.

- [x] **Step 5: Recheck scope, stash and real-data fingerprints**

Confirm `git diff --name-only origin/main...HEAD` and every package commit exclude `user_data/`; stash list remains exactly the recorded baseline; root real grading/question-bank database size, UTC mtime and SHA-256 match the pre-work values.

- [x] **Step 6: Create functional commit and request independent review**

Update evidence and the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `真实数据指纹: unchanged`; commit only P1-18 code/tests/architecture/plan. Dispatch an independent reviewer against `origin/main..HEAD`; fix all Critical/Important findings with TDD and rerun the affected verification.

- [ ] **Step 7: Create final handoff-only commit**

After independent review passes, update only this plan: record the full reviewed functional SHA, change to `verified_pending_integration`, `功能提交: <reviewed SHA>`, `自动验证: passed`, `独立复审: passed`, `用户验收: not_required`, `真实数据指纹: unchanged`, `夜间动作: independent_candidate_allowed`; commit only this plan and run `tools/handoff_status.py` from the project root. Do not push, create a PR, integrate, sync or clean any branch/worktree.

---

## Rollback

- Revert the P1-18 functional commit chain; no database migration or Schema rollback is required.
- Existing P1-16 upload/request resources and Streamlit synchronous import/tagging entry points remain available because this package does not replace or delete them.
- A cancelled/partial tagging job may have saved earlier complete batches at explicit safe boundaries; rerunning the same original ID set skips those complete questions and only fills missing tags.
- A repeated import request relies on the existing content fingerprint/source duplicate checks; it must not create duplicate papers or questions.
- If focused regression, quick smoke, handoff validation, independent review or real-data fingerprint checks fail, leave the feature worktree intact and do not integrate.

## Implementation Evidence

- Baseline: P1-16/P1-17 Job, question-bank write/import/tagging and lifecycle regression `129 passed` before source changes.
- RED/GREEN: `question_import`、`tagging_sync`、专用 API/重试与 OpenAPI 均先由聚焦测试复现缺失或契约偏差，再以最小实现转绿；打标分类和 Job 注册回归都通过失败用例定位根因后修复。
- Focused regression: 初始 P1-18 聚焦回归 `82 passed`、受影响 API/Job `154 passed`、完整 pytest `1126 passed`；独立复审修复后扩展聚焦回归 `91 passed`、受影响 API/Job `170 passed`、完整 pytest `1135 passed`。
- Independent review: 首轮复审功能提交 `987c451cb3b8588a3bf710b7b6115c82bebc9bdf` 得到 1 Critical / 4 Important / 1 Minor；并发幂等、请求 ID 校验、导入中取消、打标初始化错误持久化安全和覆盖缺口均已按 TDD 修复，最终候选复审待执行。
- Quick smoke: 复审修复后文档治理、360 个第一方 Python 文件静态编译、两库临时副本初始化幂等与 `integrity_check=ok` 全部通过。
- Real data: worktree 无 `user_data/` 变化，stash 仍为领取时两条；根目录真实阅卷库与题库 SHA-256 和领取基线完全一致。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-18
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
