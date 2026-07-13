# P1-25 四处直连迁移与缺失超时清零 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 choice、fill-blank、objective batch 与 AI tagging 四条 OpenAI SDK 直连调用逐条迁移到 P1-24 LLM Gateway，并让第一方 Python 代码不再出现 `timeout=None`。

**Architecture:** 在 `backend/llm/transport.py` 增加只负责 SDK 客户端构造、稳定配置身份与 Gateway 协议转发的薄适配器；Gateway 继续独占显式超时、普通重试、节流、请求 ID 和统一用量记录。四条业务链保留原 prompt、请求参数、响应解析、fallback、评分和线程/批次参数，只替换物理请求出口；objective batch 原有三次外层尝试由 Gateway 的 `max_retries=2` 接管，保持最多三次物理请求。

**Tech Stack:** Python 3.12、OpenAI Python SDK 2.43.0、现有 `backend.llm.LLMGateway`、pytest、假客户端与临时文件。

## Global Constraints

**执行包：** P1-25
**用户自测：** none
**自测清单：** not_required
- **规划状态：** ready_for_execution
- **规划模型：** S-XH
- **允许夜间执行：** no
- **计划基线：** 6e5ce9122673579a0a95045e9f95bd6bd3fb3a7f
- 本包是 `daytime_only`；不得调用真实模型、读取真实密钥或执行受控 API 健康检查，除非用户另行明确授权。
- 不修改、删除、暂存、提交或 stash 任何 `user_data/`；真实两库只做文件级大小、UTC mtime 与 SHA-256 比对。
- 每次只迁移一条调用链；每条迁移独立提交并在进入下一条前运行该链聚焦测试。
- 不修改 prompt、模型名选择、temperature、thinking、response format、JSON 业务结构、评分规则、fallback 触发条件、批次大小、worker 数或外层业务 RPM 参数。
- SDK 自动重试固定为 `max_retries=0`；普通重试只由 Gateway 管理。
- choice、fill-blank 和 AI tagging 旧 SDK 默认最多三次物理请求；迁移后由 Gateway 默认 `max_retries=2` 保持同一上限。objective batch 删除自己的三次循环后同样最多三次物理请求。
- choice、fill-blank、objective batch 使用 `LLMRequestKind.RECOGNITION`；AI tagging 的 Responses 请求使用 `LLMRequestKind.TAGGING`。
- 保留现有调用方结果中的 token/latency 字段和 objective batch `usage_callback`；choice/fill-blank 删除重复的手工 `log_llm_usage()` 写入，使每个物理请求只由 Gateway 写一条统一用量事件。统一事件不记录 prompt、响应正文、密钥、完整 URL、学生姓名、答卷内容或绝对路径。

---

## File Structure

- Create `backend/llm/transport.py`: 统一构造 `max_retries=0` 的 OpenAI SDK 客户端，生成与兼容 `LLMClient` 相同的稳定配置键，并通过 `LLMGateway` 转发 Chat Completions/Responses。
- Modify `backend/llm/__init__.py`: 导出 `LLMProtocolAdapter`、`create_openai_client()`、`gateway_config_key()` 与 `normalize_openai_base_url()`。
- Modify `llm_client.py`: 保留现有私有兼容函数和公开 URL 规范化函数，但内部委托共享 transport，确保已有 monkeypatch 测试与调用方不变。
- Modify `api_profiles.py`: 在 objective 配置中附带仅含 `llm_<kind>_*` 安全字段的 `policy_profile`。
- Modify `choice_recognition_chain.py`: 原样保留消息、解析、评分和结果字段，只把 SDK 直连替换为 recognition Gateway。
- Modify `fill_blank_recognition_chain.py`: 原样保留消息、解析、评分和结果字段，只把 SDK 直连替换为 recognition Gateway。
- Modify `objective_batch_recognition_service.py`: `ObjectiveBatchRecognitionClient` 使用 recognition Gateway；删除重复三次外层 retry/sleep，保留 fallback、外层 rate limiter 和并行参数。
- Modify `question_bank/services/ai_tagging_service.py`: 单题和批次 Responses 分支使用 tagging Gateway；保留 `LLMClient` 兼容分支、批次 fallback、质量重试、复核与请求事件控制器。
- Create `tests/test_llm_transport.py`: transport 构造、显式协议转发、策略、配置身份与注入假客户端测试。
- Modify `tests/test_grading_limits.py`: objective 配置安全策略投影测试。
- Modify `tests/test_choice_recognition_chain.py`: choice 请求参数、Gateway kind、显式超时、响应与评分等价测试。
- Create `tests/test_fill_blank_recognition_chain.py`: fill-blank 请求参数、Gateway kind、显式超时、响应与评分等价测试。
- Modify `tests/test_objective_batch_recognition_service.py`: Gateway 超时、最多三次物理请求、无外层重复 retry 与原 fallback 测试。
- Modify `tests/test_question_bank_ai_tagging_quality.py`: 单题与批次 Responses 通过 tagging Gateway，结构化请求和 fallback 事件保持测试。
- Modify `ARCHITECTURE.md`: 全部自动验证与独立复审通过后记录 P1-25 已实现事实，不写真实健康检查已通过。

### Task 1: Add the shared Gateway protocol adapter

**Files:**
- Create: `backend/llm/transport.py`
- Modify: `backend/llm/__init__.py`
- Modify: `llm_client.py`
- Modify: `api_profiles.py`
- Create: `tests/test_llm_transport.py`
- Modify: `tests/test_grading_limits.py`

**Interfaces:**
- Produces: `normalize_openai_base_url(base_url: str) -> str`.
- Produces: `gateway_config_key(api_key: str, base_url: str) -> str` without exposing either input.
- Produces: `create_openai_client(api_key: str, base_url: str, *, client_factory=OpenAI) -> object`, with SDK `timeout=120.0` and `max_retries=0`; Gateway always overwrites request timeout.
- Produces: `LLMProtocolAdapter(api_key, base_url, policy_profile=None, client=None, gateway_factory=LLMGateway, usage_sink_factory=...)`.
- Produces: `chat_completions(*, request_kind, model, kwargs, request_id=None, allow_retry=True)` and matching `responses(...)` methods.
- Consumes: existing `LLMGateway`, `JsonlUsageSink`, `policy_overrides_from_profile()` and `usage_logger.LOG_FILE`.

- [ ] **Step 1: Write failing transport and profile tests**

Add tests that inject a fake SDK factory, fake Gateway and null usage sink and assert:

```python
adapter = LLMProtocolAdapter(
    api_key="secret",
    base_url="https://example.test/v1",
    policy_profile={"llm_recognition_timeout_seconds": 45},
    client=fake_client,
    gateway_factory=RecordingGateway,
    usage_sink_factory=lambda: NullUsageSink(),
)
adapter.chat_completions(
    request_kind=LLMRequestKind.RECOGNITION,
    model="objective-model",
    kwargs={"messages": [], "response_format": {"type": "json_object"}},
)
assert recording_gateway.calls[0]["request_kind"] is LLMRequestKind.RECOGNITION
assert recording_gateway.calls[0]["client"] is fake_client
```

Also assert the SDK factory receives `max_retries=0`, equivalent normalized URLs generate the same opaque config key, different credentials generate different keys, raw credentials/URL are absent from the key, `llm_client._create_openai_client` still exists, and `get_objective_api_config()` returns only the safe policy projection alongside existing objective fields.

- [ ] **Step 2: Run RED**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_llm_transport.py tests\test_grading_limits.py -q
```

Expected: FAIL because `backend.llm.transport` and the objective `policy_profile` field do not exist.

- [ ] **Step 3: Implement the minimal shared adapter**

Implement an immutable ownership boundary: the adapter creates or accepts one SDK client and one Gateway, never stores prompt/response data, and forwards a fresh copy of `kwargs`. Keep these compatibility wrappers in `llm_client.py`:

```python
def _create_openai_client(api_key: str, base_url: str) -> OpenAI:
    return create_openai_client(api_key, base_url)

def _gateway_config_key(api_key: str, base_url: str) -> str:
    return gateway_config_key(api_key, base_url)
```

In `get_objective_api_config()`, use the already loaded active profile:

```python
"policy_profile": policy_overrides_from_profile(profile),
```

- [ ] **Step 4: Run GREEN and affected Gateway compatibility tests**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_llm_transport.py tests\test_llm_gateway.py tests\test_llm_gateway_policy.py tests\test_grading_limits.py tests\test_grading_config_generation_policy.py -q
```

Expected: PASS; existing `LLMClient` monkeypatch tests remain green.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/llm/transport.py backend/llm/__init__.py llm_client.py api_profiles.py tests/test_llm_transport.py tests/test_grading_limits.py
git commit -m "feat: add shared LLM gateway transport"
```

### Task 2: Migrate the choice recognition chain

**Files:**
- Modify: `choice_recognition_chain.py`
- Modify: `tests/test_choice_recognition_chain.py`

**Interfaces:**
- Consumes: `LLMProtocolAdapter.chat_completions()` with `LLMRequestKind.RECOGNITION`.
- Preserves: `recognize_choice_answer(...) -> dict`, prompt text, messages, temperature, max token key/value, response format, thinking body, parsing tolerance, scoring and review fields；旧手工用量写入由 Gateway 单条事件替代。

- [ ] **Step 1: Write a failing end-to-end fake-provider test**

Patch `get_objective_api_config()` with a complete fake config, inject a fake adapter/provider response, create a temporary JPEG, call `recognize_choice_answer()`, and assert the provider receives exactly the existing `messages`, `temperature`, `max_tokens`, `response_format` and `extra_body`, plus Gateway-injected `model` and finite recognition timeout. Assert the returned selected answer, score, confidence, review fields and token fields remain unchanged.

- [ ] **Step 2: Run RED**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_choice_recognition_chain.py -q
```

Expected: FAIL because the chain still constructs `OpenAI` and calls Chat Completions directly.

- [ ] **Step 3: Replace only the physical request boundary**

Construct `LLMProtocolAdapter` from the objective API key, base URL and `policy_profile`, then call:

```python
completion = adapter.chat_completions(
    request_kind=LLMRequestKind.RECOGNITION,
    model=config["model"],
    kwargs={
        "messages": messages,
        "temperature": config["temperature"],
        "max_tokens": config["max_tokens"],
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": config["thinking_type"]}}
        if config["thinking_type"] != "disabled"
        else None,
    },
)
```

Do not alter the prompt, image resizing, parsing, score calculation or exception-to-review behavior. Remove only the duplicate final `log_llm_usage()` block; keep response usage extraction for the returned result fields and keep the separate `log_choice_recognition()` helper.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_choice_recognition_chain.py tests\test_llm_transport.py -q
```

Expected: PASS with no real network or workspace log write.

- [ ] **Step 5: Commit Task 2**

```powershell
git add choice_recognition_chain.py tests/test_choice_recognition_chain.py
git commit -m "refactor: route choice recognition through gateway"
```

### Task 3: Migrate the fill-blank recognition chain

**Files:**
- Modify: `fill_blank_recognition_chain.py`
- Create: `tests/test_fill_blank_recognition_chain.py`

**Interfaces:**
- Consumes: `LLMProtocolAdapter.chat_completions()` with `LLMRequestKind.RECOGNITION`.
- Preserves: `recognize_fill_blank_answer(...) -> dict`, optional model override, prompt, messages, temperature, max token key/value, forced disabled thinking, parsing, normalization, scoring and review fields；旧手工用量写入由 Gateway 单条事件替代。

- [ ] **Step 1: Write a failing fake-provider regression test**

Create a temporary JPEG, return `{"raw_answer":"1/2","confidence":0.96,"need_review":false,"review_reason":""}`, and assert request kwargs are unchanged except for Gateway-injected model and finite timeout. Assert `normalized_student_answer`, score, auto-score and token fields match the current behavior. Add a separate assertion that the function-level `model` argument still overrides only the configured model.

- [ ] **Step 2: Run RED**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_fill_blank_recognition_chain.py -q
```

Expected: FAIL because the chain still constructs `OpenAI` directly and uses its local timeout default.

- [ ] **Step 3: Replace only the physical request boundary**

Use the same objective adapter and recognition kind as Task 2. Preserve `extra_body={"thinking":{"type":"disabled"}}`, `json.loads(content)` strictness and every result field. Remove only the duplicate final `log_llm_usage()` block; keep response usage extraction and `log_fill_blank_recognition()`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_fill_blank_recognition_chain.py tests\test_answer_normalizer.py tests\test_prompt_injection_guard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add fill_blank_recognition_chain.py tests/test_fill_blank_recognition_chain.py
git commit -m "refactor: route fill blank recognition through gateway"
```

### Task 4: Migrate objective batch and transfer retry ownership

**Files:**
- Modify: `objective_batch_recognition_service.py`
- Modify: `tests/test_objective_batch_recognition_service.py`

**Interfaces:**
- Consumes: `LLMProtocolAdapter.chat_completions()` with `LLMRequestKind.RECOGNITION`.
- Preserves: `ObjectiveBatchRecognitionClient.json_from_images(...)`, prompt/atlas creation, no token-limit parameter, response format, optional thinking body, usage callback, external `rate_limiter`, `batch_workers`, fallback model/client and review merging.
- Changes only retry owner: three-attempt local loop becomes Gateway `max_retries=2`, so total physical attempts remain three and each attempt receives a finite timeout.

- [ ] **Step 1: Replace the old timeout assertion with failing Gateway contract tests**

Replace `test_objective_batch_client_omits_timeout_and_token_limit` with tests that assert:

```python
assert captured["client_kwargs"]["max_retries"] == 0
assert captured["completion_kwargs"]["timeout"] == 60.0
assert "max_tokens" not in captured["completion_kwargs"]
```

Add a retryable 503 sequence asserting exactly three provider calls, and a run-level test proving the outer batch layer invokes `json_from_images()` once rather than multiplying Gateway retries. Existing fallback and parallel rate-limiter tests remain unchanged.

- [ ] **Step 2: Run RED**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_objective_batch_recognition_service.py -q
```

Expected: FAIL because the client still sets `timeout=None`, bypasses Gateway and the run layer still owns a three-attempt loop.

- [ ] **Step 3: Route the client through Gateway and delete only the duplicate retry loop**

Keep the external `rate_limiter.acquire()` once per logical batch request, then call `batch_client.json_from_images(...)` exactly once. In `ObjectiveBatchRecognitionClient`, pass the same messages, temperature, response format and optional thinking body to the shared adapter. Do not add a token-limit parameter.

- [ ] **Step 4: Run GREEN and hybrid affected regression**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_objective_batch_recognition_service.py tests\test_grading_limits.py tests\test_hybrid_grading_regressions.py tests\test_objective_escalation.py -q
```

Expected: PASS; fallback, batch size and worker behavior remain unchanged.

- [ ] **Step 5: Commit Task 4**

```powershell
git add objective_batch_recognition_service.py tests/test_objective_batch_recognition_service.py
git commit -m "refactor: route objective batches through gateway"
```

### Task 5: Migrate AI tagging Responses calls

**Files:**
- Modify: `question_bank/services/ai_tagging_service.py`
- Modify: `tests/test_question_bank_ai_tagging_quality.py`
- Modify: `tests/test_tagging_batch_attempts.py`
- Modify: `tests/test_tagging_sync_job.py`

**Interfaces:**
- Consumes: `LLMProtocolAdapter.responses()` with `LLMRequestKind.TAGGING`.
- Preserves: constructor injection of a fake/provider client, `LLMClient` compatibility branch, single and batch structured-output payloads, `_TaggingRequestController`, adaptive batching, single fallback, quality retry, review model and `complete`-only persistence rule.
- Produces: optional `protocol_adapter` constructor injection for isolated tests; production lazily constructs exactly one adapter per service instance.

- [ ] **Step 1: Write failing single and batch Responses tests**

Instantiate `AITaggingService` with a fake Responses client and environment containing only the tagging API key/model/base URL. Assert single and batch paths call Gateway with `request_kind=TAGGING`, preserve `text={"format": ...}` and `input=...`, receive a finite 120-second timeout, and still return the same `AITaggingResult`/batch mapping. Add a retryable fake provider failure followed by success to prove Gateway, not the request-event controller, owns ordinary retry.

- [ ] **Step 2: Run RED**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_question_bank_ai_tagging_quality.py tests\test_tagging_batch_attempts.py tests\test_tagging_sync_job.py -q
```

Expected: FAIL because both Responses branches still call `service._client().responses.create(...)` directly and the SDK client retains default retries.

- [ ] **Step 3: Add one lazily created tagging adapter and replace both direct calls**

Keep an injected raw fake client usable by passing it to `LLMProtocolAdapter`. For saved profiles use `policy_overrides_from_profile(profile)`; for explicit environment-only configuration use validated defaults. Replace both calls with:

```python
response = service._protocol_adapter().responses(
    request_kind=LLMRequestKind.TAGGING,
    model=service.model,
    kwargs={
        "text": {"format": response_format},
        "input": prompt_input,
    },
)
```

Do not change batch request numbering, fallback fan-out, quality thresholds or persistence decisions.

- [ ] **Step 4: Run GREEN and tagging affected regression**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_question_bank_ai_tagging_quality.py tests\test_tagging_batch_attempts.py tests\test_tagging_sync_job.py tests\test_api_profile_store.py tests\test_grading_paper_archive_intake.py tests\test_source_question_link_service.py -q
```

Expected: PASS with existing complete-only save and fallback event counts unchanged.

- [ ] **Step 5: Commit Task 5**

```powershell
git add question_bank/services/ai_tagging_service.py tests/test_question_bank_ai_tagging_quality.py tests/test_tagging_batch_attempts.py tests/test_tagging_sync_job.py
git commit -m "refactor: route AI tagging through gateway"
```

### Task 6: Enforce the boundary, update architecture, verify and hand off

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-13-p1-25-direct-call-migration-implementation.md`

**Interfaces:**
- Consumes: all four migrated chains and P1-24 Gateway contracts.
- Produces: static guard evidence that SDK calls remain only in the shared transport/Gateway protocol execution points and first-party Python has no `timeout=None`.

- [ ] **Step 1: Run the full P1-25 focused and affected regression**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_policy.py tests\test_llm_gateway.py tests\test_llm_gateway_usage.py tests\test_llm_transport.py tests\test_grading_config_generation_policy.py tests\test_api_profile_store.py tests\test_choice_recognition_chain.py tests\test_fill_blank_recognition_chain.py tests\test_answer_normalizer.py tests\test_prompt_injection_guard.py tests\test_objective_batch_recognition_service.py tests\test_grading_limits.py tests\test_hybrid_grading_regressions.py tests\test_objective_escalation.py tests\test_question_bank_ai_tagging_quality.py tests\test_tagging_batch_attempts.py tests\test_tagging_sync_job.py tests\test_grading_paper_archive_intake.py tests\test_source_question_link_service.py -q
```

Expected: PASS with zero failures/skips caused by P1-25.

- [ ] **Step 2: Run static and quick-smoke guards**

Run:

```powershell
rg -n --glob '*.py' --glob '!tests/**' --glob '!runtime/**' --glob '!user_data/**' 'timeout\s*=\s*None'
rg -n --glob '*.py' --glob '!tests/**' --glob '!runtime/**' --glob '!user_data/**' 'chat\.completions\.create|responses\.create|OpenAI\('
git diff --check
git status --short -- user_data
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
```

Expected: first command has zero matches; SDK construction/invocation appears only in `backend/llm/transport.py` and `backend/llm/gateway.py`; diff check and quick smoke pass; worktree `user_data/` status is empty.

- [ ] **Step 3: Compare the root real-data file fingerprints**

Read only size, UTC mtime and SHA-256 of the two root databases and compare with the recorded claim baseline. Do not open either database with SQLite. Expected: every value is unchanged.

- [ ] **Step 4: Update architecture after verified behavior**

Record that four direct chains now use Gateway with explicit finite timeouts and bounded retry ownership; state that prompts, algorithms, fallback, scoring and concurrency remain unchanged, all automated verification used fakes/temporary data, and no real API health check was executed.

- [ ] **Step 5: Commit verified functional work as `waiting_review`**

Update this plan's checkboxes/evidence and handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: not_required`, `真实数据指纹: unchanged`; preserve the Stash baseline. Then commit only P1-25 source/tests/architecture/plan changes.

- [ ] **Step 6: Request independent review and fix Critical/Important findings with RED/GREEN tests**

Review the complete package range for retry multiplication, lost request IDs, unsafe usage logging, changed provider kwargs, prompt/response drift, fallback drift, changed batch concurrency and remaining direct SDK calls. Critical/Important must be zero before integration.

- [ ] **Step 7: Create the final plan-only handoff commit**

After review passes, change only this plan: record the direct parent full reviewed functional SHA, set `verified_pending_integration`, `自动验证: passed`, `独立复审: passed`, `用户验收: not_required`, `真实数据指纹: unchanged`, and `夜间动作: independent_candidate_allowed`. Run `tools/handoff_status.py` against the clean worktree and commit the plan-only handoff.

## Verification and Recovery

- Baseline evidence: on `6e5ce9122673579a0a95045e9f95bd6bd3fb3a7f`, the selected Gateway/choice/objective/tagging suite passed `80 passed` before any plan or source edit.
- Functional feedback: every task starts with a focused test that fails for the missing Gateway route, then the minimum code change makes it green before the task commit.
- Feature-branch gate: focused/affected regression, static zero-match guards, `git diff --check`, empty worktree `user_data/` status, quick smoke and real-data file fingerprint equality.
- Integration gate: merge the full verified commit chain into a fresh integration branch based on current `origin/main`; rerun P1-25 focused/affected regression, then complete one wave-end full `tools/smoke_check.py` because P1-25 changes shared model request infrastructure. Recompare root database fingerprints before and after.
- Real API health check is deliberately excluded. If later authorized, use a separately approved non-sensitive prompt/image and never store a real key, prompt, response or student data in Git.
- Rollback is per-chain: revert AI tagging, objective batch, fill-blank or choice migration commits in reverse dependency order; the shared transport can be reverted only after all four caller commits are reverted. No Schema or data rollback is required.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-25
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
