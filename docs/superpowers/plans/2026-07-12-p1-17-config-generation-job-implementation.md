# P1-17 Config Generation Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有评分依据生成能力接入可查询、可协作取消、可按失败题重试并可在进程重启后给出确定终态的 `config_generation` Job，同时只在完整成功后原子发布文件并绑定会话。

**Architecture:** FastAPI 配置路由只校验请求、把无密钥生成输入原子暂存到受控配置目录并提交 Job；新的 `backend/jobs/config_generation.py` 负责读取暂存输入、调用现有 `session_manager` 生成/重试函数、保存部分结果草稿，并在最终安全边界发布 rubric/answer_key 后更新会话。Job 数据库只保存服务器生成的输入 ID、会话 ID、模式和重试题号，公开响应只返回摘要，不返回模型密钥、提示词正文或内部路径。

**Tech Stack:** Python 3.12、FastAPI 0.139、Pydantic v2、SQLite 3.43、现有 JobManager/session_manager/PathManager、pytest。

**执行包：** P1-17
**规划状态：** ready_for_execution
**计划基线：** d1582855ec2273fae1d4b00dfb7b4d54824009e8
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- 只实现 P1-17 的 `config_generation` Job、生成摘要、部分失败草稿与单题/多题重试；不实现 P1-18 导入/打标 Job，也不提前迁移 P1-24 LLM Gateway。
- 复用 `generate_grading_config_from_confirmed_blocks()`、`retry_failed_grading_config_questions()` 和现有 API profile 读取；不改 prompt、评分规则、题号契约、总分口径或模型供应商。
- 请求模型禁止出现 API key、token、secret、password 或客户端存储路径；Job payload 只含服务器生成引用和标量控制字段。
- 初次生成的部分失败结果只写受控草稿，不绑定会话；只有失败题清零且最终结构可验证时才发布 rubric/answer_key 并更新会话路径。
- 取消为协作式：不强杀线程或正在执行的单次模型请求；模型调用返回后、草稿发布前、最终文件发布前和数据库绑定前必须检查取消信号。
- 生成输入、部分结果和最终 JSON 使用同目录临时文件加 `os.replace()`；异常或取消不得让数据库指向半成品。
- 所有模型调用使用假 LLM；所有数据库、上传和配置文件测试只使用 `tmp_path`，不得打开或写入真实 `user_data/`。
- 不修改 SQLite Schema；进程重启继续使用 JobStore 的既有规则把 queued/running 标为 failed，并保留已成功/部分成功 Job 的可查询摘要。
- 不修改、删除、暂存、提交或 stash 真实 `user_data/`；结束时真实两库 SHA-256 必须与领取时一致。
- 功能分支可记录 P1-17 专属架构事实，但 `EXECUTION_INDEX.md` 的 merged 状态与最终共享入口整理只在 integration 阶段更新。

---

## File Structure

- Create: `backend/jobs/config_generation.py`：受控输入暂存、初次生成/失败题重试编排、部分草稿、原子发布和会话绑定。
- Modify: `session_manager.py`：最终 JSON 改为逐文件原子发布；失败题重试支持明确题号子集并保留未选择失败项。
- Modify: `backend/jobs/default_handlers.py`：注册 `config_generation` handler，复用活动 API profile 创建 LLM client。
- Modify: `backend/api/dependencies.py`：把受控 upload config 目录传给默认 handler 注册。
- Modify: `backend/api/schemas/config.py`：增加初次生成、重试和 Job 接受响应模型，禁止额外字段。
- Modify: `backend/api/schemas/__init__.py`：导出新增模型。
- Modify: `backend/api/routers/config.py`：增加生成与重试端点、会话/源 Job 校验、稳定错误映射。
- Modify: `backend/api/routers/jobs.py`：对配置生成 Job 公开最小 payload/result 摘要。
- Create: `tests/test_config_generation_job.py`：服务/handler 的成功、部分失败、取消、重试、重启和原子性测试。
- Create: `tests/test_api_config_generation_jobs.py`：专用 API 契约、无密钥 payload、公开摘要和错误映射测试。
- Modify: `tests/test_grading_config_generation_policy.py`：指定失败题重试并保留其他失败项的算法适配测试。
- Modify: `tests/test_api_openapi_contract.py`：新增端点、模型和敏感字段守卫。
- Modify: `ARCHITECTURE.md`：记录已实现的 P1-17 Job 边界、取消语义和部分结果规则。

## Public Interfaces

```python
stage_config_generation_input(
    upload_config_dir: Path,
    *,
    session_id: int,
    confirmed_blocks: list[dict[str, Any]],
    document_text: str,
    question_images: dict[str, Any] | None,
) -> str

run_config_generation_job(
    *,
    context: JobContext,
    db: DBManager,
    upload_config_dir: Path,
    llm_client_factory: Callable[[], Any],
) -> dict[str, object]

retry_failed_grading_config_questions(
    existing_payload: dict[str, Any],
    question_blocks: list[dict[str, Any]],
    doc_text: str,
    llm_client: LLMClient,
    model_name: str | None = None,
    report: Any = None,
    q_images: dict[str, Any] | None = None,
    *,
    retry_question_ids: Sequence[str] | None = None,
) -> dict[str, Any]

POST /api/sessions/{session_id}/config/generate
POST /api/sessions/{session_id}/config/generate/retry
```

Stable failures:

```text
404 session_not_found
404 config_generation_job_not_found
409 config_generation_retry_not_available
422 invalid_config_generation_request
503 job_type_not_supported
```

---

### Task 1: Atomic Input And Final Config Publication

**Files:**
- Create: `backend/jobs/config_generation.py`
- Modify: `session_manager.py`
- Test: `tests/test_config_generation_job.py`

**Interfaces:**
- Produces an opaque 32-character input ID and fixed server-derived input path.
- Produces atomic JSON helpers that never accept a client destination.
- Keeps `save_generated_config(upload_dir, payload, ts) -> tuple[Path, Path]` compatible.

- [x] **Step 1: Write failing filesystem tests**

Add tests asserting input JSON is published by `os.replace`, IDs reject traversal, failure before replace leaves no visible input, final rubric/answer files contain valid JSON, and an injected second-file publish failure leaves the database untouched when used through the Job runner.

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py -k "stage or atomic" -q
```

Expected: collection/import FAIL because `backend.jobs.config_generation` does not exist and existing config saves write directly.

- [x] **Step 3: Implement minimal atomic helpers**

Use UUID hex IDs, strict `[0-9a-f]{32}` validation, `json.dump(..., ensure_ascii=False)`, a same-directory dot-prefixed temporary file, flush/close, then `os.replace`. Update `save_generated_config()` to write each final JSON through the same atomic-file pattern while preserving filenames and return type. Clean only server-created temporary files on exceptions.

- [x] **Step 4: Run atomic tests and existing config API regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py -k "stage or atomic" tests/test_api_config_routes.py -q
```

Expected: all selected tests PASS.

---

### Task 2: Config Generation Handler Success, Partial Result And Cancellation

**Files:**
- Modify: `backend/jobs/config_generation.py`
- Modify: `backend/jobs/default_handlers.py`
- Modify: `backend/api/dependencies.py`
- Modify: `tests/test_config_generation_job.py`

**Interfaces:**
- Consumes Job payload `{session_id, mode: "generate", input_id}`.
- Produces result `{session_id, outcome, total_questions, generated_questions, failed_count, failed_question_ids, retryable}`.
- Registers exactly one `config_generation` handler using the active config model without persisting credentials.

- [x] **Step 1: Write failing handler tests**

Cover fake-LLM complete success and DB binding; partial failure draft without DB binding; missing session/input; input/session mismatch; cancellation before generation, after fake model return and before final binding; fake LLM exception; result without absolute paths or credentials; and final file cleanup when DB binding fails.

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py -k "generate or partial or cancel" -q
```

Expected: FAIL because the handler and registration do not exist.

- [x] **Step 3: Implement thin orchestration**

Load only the fixed input resource, require a live non-deleted session, create the LLM client inside the worker, pass a report wrapper that reports then calls `raise_if_cancelled`, and use the client's `config_model`. On partial failure publish `config_generation_draft_job_<id>.json` and return a retryable summary without calling `update_grading_session_config`. On full success validate/publish both final files, perform one last cancellation check, bind both paths, and return a complete summary. Never place input content, prompt text, API settings or file paths in the public result.

- [x] **Step 4: Run handler and lifecycle regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py tests/test_api_job_lifecycle.py tests/test_job_manager.py -q
```

Expected: all tests PASS, including restart cleanup of queued/running Job records.

---

### Task 3: Selected Failed-Question Retry

**Files:**
- Modify: `session_manager.py`
- Modify: `backend/jobs/config_generation.py`
- Modify: `tests/test_grading_config_generation_policy.py`
- Modify: `tests/test_config_generation_job.py`

**Interfaces:**
- Consumes retry Job payload `{session_id, mode: "retry", source_job_id, retry_question_ids}`.
- A retry may target one or more currently failed IDs; omitted IDs remain failed and retryable.

- [x] **Step 1: Write failing subset-retry tests**

Create a payload with Q1 and Q2 failed, retry only Q1, and assert Q1 is replaced while Q2 remains in `failed_question_ids/failed_questions`; reject unknown, duplicate-only-empty, already successful or wrong-session IDs; then retry Q2 and assert final score allocation and DB binding happen once.

- [x] **Step 2: Run tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_grading_config_generation_policy.py tests/test_config_generation_job.py -k "selected or subset or retry" -q
```

Expected: FAIL because retry currently always consumes every failed question and the Job has no retry mode.

- [x] **Step 3: Extend retry without changing generation semantics**

Normalize requested IDs against the existing failure set, run the existing per-question generation only for selected blocks, merge successes, and combine new selected failures with untouched prior failures in original question order. Overall score allocation remains paused while any failure remains. The Job retry loads the source Job's server-side input and draft by verified IDs; it never trusts a client path or resends credentials.

- [x] **Step 4: Run retry and generation-policy regression**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_grading_config_generation_policy.py tests/test_config_generation_job.py -q
```

Expected: all tests PASS and legacy all-failed retry behavior remains unchanged when no subset is provided.

---

### Task 4: Dedicated FastAPI Generate And Retry Routes

**Files:**
- Modify: `backend/api/schemas/config.py`
- Modify: `backend/api/schemas/__init__.py`
- Modify: `backend/api/routers/config.py`
- Modify: `backend/api/routers/jobs.py`
- Create: `tests/test_api_config_generation_jobs.py`

**Interfaces:**
- Initial request contains confirmed blocks, document text and optional question-image mapping; no path or API settings.
- Retry request contains source Job ID and optional bounded failed-question ID list.
- Both endpoints return HTTP 202 with the standard Job representation reduced to safe config-generation metadata.

- [x] **Step 1: Write failing API contract tests**

Cover 202 submit/query; nonexistent/deleted session; source Job missing/wrong type/wrong session/not partial; extra fields and sensitive keys; no internal path/document text/image base64 in public Job payload; cancelled/failed/public error behavior; repeated retry after completion; and generic `/api/jobs/config_generation` validation by the handler.

- [x] **Step 2: Run API tests and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_config_generation_jobs.py -q
```

Expected: 404/405 because the dedicated routes do not exist.

- [x] **Step 3: Add strict schemas and thin routes**

Use `ConfigDict(extra="forbid")`, bounded question IDs and list sizes, stage the input before submission, remove a newly staged input if submission fails, and map unsupported Job registration to 503. Before retry submission, verify the source Job belongs to the same session, is `config_generation`, succeeded with `outcome="partial"`, and exposes requested IDs. Special-case public config Job payload/result projection so stored input references and internal paths are never returned.

- [x] **Step 4: Run API and existing Job/config regressions**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_config_generation_jobs.py tests/test_api_config_routes.py tests/test_api_jobs.py tests/test_api_job_lifecycle.py -q
```

Expected: all tests PASS.

---

### Task 5: OpenAPI, Architecture And Package Verification

**Files:**
- Modify: `tests/test_api_openapi_contract.py`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p1-17-config-generation-job-implementation.md`

**Interfaces:**
- Produces OpenAPI operations with no key/token/secret/password/path/destination fields.
- Produces a `waiting_review` functional commit and machine-verifiable handoff.

- [x] **Step 1: Write failing OpenAPI assertions**

Assert both endpoints and their 202/404/409/422/503 responses exist, operation IDs are unique, schemas forbid additional properties, and request/response properties do not introduce sensitive-key or destination-path fields.

- [x] **Step 2: Run OpenAPI test and verify RED**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_openapi_contract.py -q
```

Expected: FAIL until the new operations are declared.

- [x] **Step 3: Complete documentation and focused regression**

Record only implemented P1-17 facts in `ARCHITECTURE.md`, then run:

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_config_generation_job.py tests/test_api_config_generation_jobs.py tests/test_api_config_routes.py tests/test_api_jobs.py tests/test_api_job_lifecycle.py tests/test_grading_config_generation_policy.py tests/test_api_openapi_contract.py -q
```

Expected: all selected tests PASS.

- [x] **Step 4: Run affected API regression, diff and quick smoke**

```powershell
..\..\runtime\python\python.exe -m pytest tests/test_api_app.py tests/test_api_read_routes.py tests/test_api_write_routes.py tests/test_api_config_routes.py tests/test_api_config_generation_jobs.py tests/test_api_jobs.py -q
git diff --check
..\..\runtime\python\python.exe tools/smoke_check.py --skip-tests
```

Expected: all tests PASS; diff check and quick smoke exit 0.

- [x] **Step 5: Recheck scope, stash and real-data fingerprints**

Confirm no commit or stash added after领取 contains `user_data/`, and the real grading/question-bank database SHA-256 values still match the recorded baseline.

- [x] **Step 6: Create functional commit and update handoff**

Update Implementation Evidence and the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `真实数据指纹: unchanged`; commit only P1-17 code, tests, architecture fact and this plan. Do not integrate before independent review.

---

## Rollback

- Revert the P1-17 functional commit chain; no database migration or Schema rollback is required.
- A partial Job never changes the session binding. A failed final publish may leave only unreferenced server-generated files; the handler removes files it created when binding fails.
- Existing Streamlit synchronous generation remains available because P1-17 does not replace or delete its entry points.
- If integration regression, full smoke, OpenAPI, handoff validation or real-data fingerprint checks fail, do not push/merge the integration branch.

## Implementation Evidence

- Baseline: config API, Job lifecycle/manager and generation-policy suite `81 passed` before source changes.
- RED/GREEN: 输入暂存、原子发布、Job 注册、成功/部分失败/取消、指定失败题重试、专用 API、客户端路径拒绝和 OpenAPI 均先由聚焦测试复现失败，再以最小实现转绿。
- Focused regression: 最终 P1-17 聚焦回归 `93 passed`；受影响 API 回归 `45 passed`。
- Independent review: pending.
- Quick smoke: 文档治理、352 个第一方 Python 文件静态编译、两库副本初始化幂等和 `integrity_check=ok` 全部通过。
- Real data: 工作树没有 `user_data/` 变更，stash 仍为领取时两条；真实阅卷库与题库 SHA-256 和领取基线完全一致。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-17
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
