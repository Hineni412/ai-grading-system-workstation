# P1-24 LLM Gateway Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一管理模型请求超时、有限重试、节流、请求 ID、协议兼容和脱敏用量事件的 LLM Gateway，并保持现有 `LLMClient` 调用契约。

**Architecture:** 在 `backend/llm/` 新增策略、错误分类、节流、用量和 Gateway 五个单责模块。根目录 `llm_client.py` 保留一个版本的兼容门面，Chat Completions 请求内部委托 Gateway；Responses 适配器作为 P1-25 可直接复用的稳定接口，但本包不迁移四处直连调用。

**Tech Stack:** Python 3.12、OpenAI Python SDK 2.43.0、现有 `RequestPacer`/`usage_logger`、pytest、假客户端与假时钟。

**执行包：** P1-24
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** d1d143cba0c724b82fa35f698ec3b05522021720
**用户自测：** none
**自测清单：** not_required

## Global Constraints

- P1-24 是 `daytime_only`；不得调用真实模型、读取真实密钥或执行真实 API 健康检查。
- 不修改、删除、暂存、提交或 stash 任何真实 `user_data/`；真实两库只允许按文件读取大小、UTC mtime 和 SHA-256。
- 不更换供应商、模型或 API profile 存储协议，不修改 prompt、评分规则、题号契约、标签质量门槛、活动知识点语义或业务并发上限。
- 不迁移 choice、fill-blank、objective batch 或 AI tagging 四处直连 OpenAI 的调用链；这些属于 P1-25。
- 不清理 `objective_batch_recognition_service.py` 的 `timeout=None`；P1-25 才负责第一方代码零命中。
- Chat Completions 和 Responses 共用策略、错误分类、请求 ID 和用量结构；协议参数和响应提取分别适配。
- 默认超时精确为：`grading=300s`、`recognition=60s`、`config_generation=120s`、`tagging=120s`。
- API profile 覆盖键固定为 `llm_<kind>_timeout_seconds`、`llm_<kind>_max_retries`、`llm_<kind>_requests_per_minute`；范围分别为 `1..600`、`0..5`、`1..5000`。
- SDK 自动重试保持 `max_retries=0`；只有 Gateway 的超时、连接、限流和 5xx 分类可以消耗普通重试预算。
- P1-24 兼容期由保留既有外层循环的调用方拥有普通重试；`LLMClient` 默认关闭其委托 Gateway 的普通重试，直接使用 Gateway 的调用方仍可使用策略中的有界重试。P1-25 迁移并移除旧外层循环后再切换所有权。
- 参数兼容降级不消耗普通重试预算；JSON 截断重试/修复保持现有上限和业务语义。
- 同一 `(config_key, request_kind)` 的 RPM 在进程内只允许收紧；旧/新客户端交错时复用同一 pacer、保留已预约状态，进程重启前不得被更高 RPM 放宽。
- 用量事件不得记录 prompt、响应正文、API key、完整 Base URL、学生姓名、答卷内容或内部绝对路径；日志失败不得覆盖模型结果。
- 生产 `LLMClient` 默认把主/配置 Gateway 的脱敏 usage event 写入既有 `logs/llm_usage.jsonl`；测试通过 sink factory 注入临时或空 sink，不写工作区运行日志。
- `json_from_text_once` 与 `json_from_images_once` 仍只允许一次物理模型请求，不运行网络重试、参数兼容降级或 AI JSON 修复。
- 功能分支不修改 `EXECUTION_INDEX.md`；共享 Index 与最终架构状态由 integration 更新。

---

## File Structure

- Create `backend/llm/__init__.py`: 导出稳定 Gateway 公共接口。
- Create `backend/llm/policy.py`: 请求类型/协议、默认策略、严格 API profile 覆盖解析。
- Create `backend/llm/errors.py`: OpenAI/HTTP 异常集中分类和可重试判定。
- Create `backend/llm/pacing.py`: 按配置身份和请求类型共享线程安全 `RequestPacer`。
- Create `backend/llm/usage.py`: 脱敏用量事件、两协议 token 归一化和兼容 sink。
- Create `backend/llm/gateway.py`: 同步 Chat Completions/Responses 执行、显式 timeout、有限重试与请求 ID。
- Modify `llm_client.py`: `LLMSettings` 接收非敏感策略覆盖；旧公开方法通过 Gateway，保留参数降级和 JSON 修复行为。
- Modify `usage_logger.py`: 允许显式日志路径注入并提供兼容 Gateway sink，不改变旧记录默认字段。
- Modify `backend/jobs/default_handlers.py`: 从活动 profile 传入 Gateway 策略覆盖。
- Modify `web_app.py`: 从已加载 profile 传入策略覆盖，不新增 UI 控件或改变保存表单。
- Modify `question_bank/services/ai_tagging_service.py`: profile 构造的兼容客户端传入策略覆盖；环境变量构造继续使用默认策略。
- Create `tests/test_llm_gateway_policy.py`。
- Create `tests/test_llm_gateway.py`。
- Create `tests/test_llm_gateway_usage.py`。
- Extend `tests/test_request_pacer.py`、`tests/test_api_profile_store.py`、`tests/test_grading_config_generation_policy.py`。
- Modify `ARCHITECTURE.md`: 行为验证后记录 P1-24 已实现事实，不宣称 P1-25 直连迁移完成。

## Public Interfaces

```python
class LLMRequestKind(str, Enum):
    GRADING = "grading"
    RECOGNITION = "recognition"
    CONFIG_GENERATION = "config_generation"
    TAGGING = "tagging"

class LLMProtocol(str, Enum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"

@dataclass(frozen=True, slots=True)
class LLMRequestPolicy:
    timeout_seconds: float
    max_retries: int
    requests_per_minute: int
    retry_delays: tuple[float, ...]

def policy_from_profile(kind: LLMRequestKind, profile: Mapping[str, object] | None) -> LLMRequestPolicy: ...
def policy_overrides_from_profile(profile: Mapping[str, object] | None) -> dict[str, object]: ...

@dataclass(frozen=True, slots=True)
class LLMUsageEvent:
    request_id: str
    attempt: int
    request_kind: str
    protocol: str
    model: str
    latency_ms: int
    success: bool
    error_category: str
    compatibility_fallback: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    total_tokens: int

class LLMGateway:
    def chat_completions(self, *, request_kind, client, model, kwargs, request_id=None, allow_retry=True): ...
    def responses(self, *, request_kind, client, model, kwargs, request_id=None, allow_retry=True): ...
```

---

### Task 1: Freeze request policies and centralized error classification

**Files:**
- Create: `backend/llm/policy.py`
- Create: `backend/llm/errors.py`
- Create: `tests/test_llm_gateway_policy.py`

**Interfaces:**
- Produces: `LLMRequestKind`, `LLMProtocol`, `LLMRequestPolicy`, `policy_from_profile()`, `policy_overrides_from_profile()`.
- Produces: `LLMErrorCategory`, `classify_llm_error()`, `is_retryable_error()`.

- [x] **Step 1: Write RED policy and classification tests**

```python
def test_default_timeout_budgets_are_explicit():
    assert policy_from_profile(LLMRequestKind.GRADING, None).timeout_seconds == 300.0
    assert policy_from_profile(LLMRequestKind.RECOGNITION, None).timeout_seconds == 60.0
    assert policy_from_profile(LLMRequestKind.CONFIG_GENERATION, None).timeout_seconds == 120.0
    assert policy_from_profile(LLMRequestKind.TAGGING, None).timeout_seconds == 120.0

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_grading_timeout_seconds", None),
        ("llm_grading_timeout_seconds", 0),
        ("llm_grading_timeout_seconds", 601),
        ("llm_grading_max_retries", 6),
        ("llm_grading_requests_per_minute", "fast"),
    ],
)
def test_invalid_profile_override_fails_before_request(field, value):
    with pytest.raises(LLMPolicyError, match=field):
        policy_from_profile(LLMRequestKind.GRADING, {field: value})
```

Also assert timeout/connection/rate-limit/500/502/503/504 are retryable; authentication, 400/404/422 and unknown exceptions are not; parameter incompatibility is classified separately and never ordinary-retryable.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_policy.py -q`
Expected: FAIL because `backend.llm.policy` and `backend.llm.errors` do not exist.

- [x] **Step 3: Implement immutable defaults and strict profile parsing**

```python
DEFAULT_POLICIES = {
    LLMRequestKind.GRADING: LLMRequestPolicy(300.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)),
    LLMRequestKind.RECOGNITION: LLMRequestPolicy(60.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)),
    LLMRequestKind.CONFIG_GENERATION: LLMRequestPolicy(120.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)),
    LLMRequestKind.TAGGING: LLMRequestPolicy(120.0, 2, 1000, (0.5, 1.5, 3.0, 5.0, 8.0)),
}

def policy_from_profile(kind, profile):
    base = DEFAULT_POLICIES[LLMRequestKind(kind)]
    values = dict(profile or {})
    prefix = f"llm_{LLMRequestKind(kind).value}"
    timeout = _bounded_float(values, f"{prefix}_timeout_seconds", base.timeout_seconds, 1.0, 600.0)
    retries = _bounded_int(values, f"{prefix}_max_retries", base.max_retries, 0, 5)
    rpm = _bounded_int(values, f"{prefix}_requests_per_minute", base.requests_per_minute, 1, 5000)
    return LLMRequestPolicy(timeout, retries, rpm, base.retry_delays[:retries])
```

`classify_llm_error()` first uses OpenAI exception types/status codes, then narrow parameter markers. Do not classify every 400/422 as parameter incompatibility; a marker such as `max_tokens`, `max_completion_tokens`, `response_format`, `json_object`, `unsupported parameter`, `unknown parameter` or `extra_forbidden` is required.

- [x] **Step 4: Run GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_policy.py -q`
Expected: all policy/classification tests pass.

- [x] **Step 5: Commit Task 1**

```powershell
git add backend/llm/policy.py backend/llm/errors.py tests/test_llm_gateway_policy.py
git commit -m "feat: define LLM gateway request policies"
```

### Task 2: Add keyed pacing without changing RequestPacer semantics

**Files:**
- Create: `backend/llm/pacing.py`
- Modify: `tests/test_request_pacer.py`

**Interfaces:**
- Produces: `LLMPacerRegistry.acquire(config_key: str, kind: LLMRequestKind, requests_per_minute: int) -> None`.
- Consumes: existing `RequestPacer.acquire()` first-request-immediate and evenly spaced slot behavior.

- [x] **Step 1: Write RED keyed/concurrent pacing tests**

```python
def test_registry_reuses_pacer_for_same_config_and_kind():
    created = []
    registry = LLMPacerRegistry(factory=lambda rpm: created.append(rpm) or FakePacer())
    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)
    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)
    assert created == [60]

def test_policy_change_replaces_keyed_pacer():
    registry.acquire("profile-a", LLMRequestKind.GRADING, 60)
    registry.acquire("profile-a", LLMRequestKind.GRADING, 120)
    assert created == [60, 120]
```

Retain and rerun the existing concurrent distinct-slot test; add a registry concurrency test proving one pacer is created for simultaneous first access.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_request_pacer.py -q`
Expected: FAIL because `LLMPacerRegistry` does not exist.

- [x] **Step 3: Implement locked registry around existing pacer**

```python
class LLMPacerRegistry:
    def __init__(self, *, factory=RequestPacer):
        self._factory = factory
        self._entries = {}
        self._lock = threading.Lock()

    def acquire(self, config_key, kind, requests_per_minute):
        key = (str(config_key), LLMRequestKind(kind))
        rpm = int(requests_per_minute)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] != rpm:
                entry = (rpm, self._factory(rpm))
                self._entries[key] = entry
            pacer = entry[1]
        pacer.acquire()
```

- [x] **Step 4: Run GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_request_pacer.py -q`
Expected: existing and new pacing tests pass.

- [x] **Step 5: Commit Task 2**

```powershell
git add backend/llm/pacing.py tests/test_request_pacer.py
git commit -m "feat: share LLM request pacing policies"
```

### Task 3: Normalize and safely persist Gateway usage events

**Files:**
- Create: `backend/llm/usage.py`
- Modify: `usage_logger.py`
- Create: `tests/test_llm_gateway_usage.py`

**Interfaces:**
- Produces: `LLMUsageEvent`, `usage_fields()`, `JsonlUsageSink`, `NullUsageSink`.
- Preserves: `usage_logger.extract_usage_fields()` and `usage_logger.log_llm_usage(record)`.

- [x] **Step 1: Write RED usage normalization and redaction tests**

```python
def test_usage_fields_supports_chat_and_responses_shapes():
    chat = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    responses = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}
    assert usage_fields(chat)["prompt_tokens"] == 10
    assert usage_fields(responses)["completion_tokens"] == 3

def test_jsonl_sink_writes_only_allowlisted_metadata(tmp_path):
    sink = JsonlUsageSink(tmp_path / "usage.jsonl")
    sink.write(_event(model="model", request_id="req-1"))
    text = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "secret-key" not in text
    assert "student answer" not in text
    assert "C:\\" not in text
```

Also cover cached/reasoning detail objects, failed request with zero usage, sink write failure not raising, and compatibility logger retaining its current default record shape.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_usage.py -q`
Expected: FAIL because Gateway usage module does not exist.

- [x] **Step 3: Implement frozen allowlisted event and injectable JSONL path**

```python
@dataclass(frozen=True, slots=True)
class LLMUsageEvent:
    request_id: str
    attempt: int
    request_kind: str
    protocol: str
    model: str
    latency_ms: int
    success: bool
    error_category: str = ""
    compatibility_fallback: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

class JsonlUsageSink:
    def write(self, event):
        try:
            log_llm_usage(asdict(event), log_file=self.path)
        except Exception:
            logger.warning("Failed to record LLM usage metadata", exc_info=True)
```

Change `log_llm_usage(record, *, log_file=LOG_FILE)` compatibly; old positional calls remain valid.

- [x] **Step 4: Run GREEN and usage-report compatibility**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_usage.py -q; ..\..\runtime\python\python.exe -m py_compile usage_report.py`
Expected: Gateway usage tests pass and `usage_report.py` compiles successfully.

- [x] **Step 5: Commit Task 3**

```powershell
git add backend/llm/usage.py usage_logger.py tests/test_llm_gateway_usage.py
git commit -m "feat: record unified LLM usage events"
```

### Task 4: Implement bounded Chat Completions and Responses Gateway execution

**Files:**
- Create: `backend/llm/gateway.py`
- Create: `backend/llm/__init__.py`
- Create: `tests/test_llm_gateway.py`

**Interfaces:**
- Produces: `LLMGateway.chat_completions()` and `.responses()` with explicit timeout and optional caller request ID.
- Consumes: policy, classifier, pacer registry and usage sink from Tasks 1-3.

- [x] **Step 1: Write RED protocol/retry/request-ID tests**

```python
def test_chat_and_responses_receive_explicit_timeout(fake_clients):
    gateway = _gateway(profile={})
    gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=fake_clients.chat,
        model="g",
        kwargs={"messages": []},
    )
    gateway.responses(
        request_kind=LLMRequestKind.TAGGING,
        client=fake_clients.responses,
        model="t",
        kwargs={"input": "x"},
    )
    assert fake_clients.chat.calls[0]["timeout"] == 300.0
    assert fake_clients.responses.calls[0]["timeout"] == 120.0

def test_rate_limit_retries_are_bounded_and_share_request_id():
    operation = FakeOperation([FakeRateLimitError(), FakeRateLimitError(), _response()])
    result = gateway.chat_completions(
        request_kind=LLMRequestKind.GRADING,
        client=operation.client,
        model="g",
        kwargs={"messages": []},
        request_id="req-fixed",
    )
    assert result is operation.result
    assert operation.call_count == 3
    assert {event.request_id for event in sink.events} == {"req-fixed"}
    assert [event.attempt for event in sink.events] == [1, 2, 3]
```

Also cover timeout/connection/500 success after retry, authentication/400/unknown no retry, retry budget exhaustion, deterministic sleeper delays, pacing before every physical request, generated UUID request ID, and log sink failure not changing returned response.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway.py -q`
Expected: FAIL because `LLMGateway` does not exist.

- [x] **Step 3: Implement a single private execution loop**

```python
def _execute(self, *, protocol, request_kind, client, model, kwargs, request_id, allow_retry):
    request_id = str(request_id or uuid.uuid4())
    policy = policy_from_profile(request_kind, self.profile)
    limit = policy.max_retries if allow_retry else 0
    for attempt in range(1, limit + 2):
        self.pacers.acquire(self.config_key, request_kind, policy.requests_per_minute)
        started = self.clock()
        try:
            payload = dict(kwargs)
            payload["model"] = model
            payload["timeout"] = policy.timeout_seconds
            response = _invoke(protocol, client, payload)
        except Exception as exc:
            category = classify_llm_error(exc)
            self._record_failure(...)
            if attempt > limit or not is_retryable_error(category):
                raise
            self.sleeper(policy.retry_delays[attempt - 1])
            continue
        self._record_success(...)
        return response
```

Never mutate caller `kwargs`. `Retry-After` parsing is capped to the current policy delay; invalid/negative/non-finite values use the deterministic delay.

- [x] **Step 4: Run GREEN**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway.py tests\test_llm_gateway_policy.py tests\test_llm_gateway_usage.py tests\test_request_pacer.py -q`
Expected: all Gateway core tests pass.

- [x] **Step 5: Commit Task 4**

```powershell
git add backend/llm/__init__.py backend/llm/gateway.py tests/test_llm_gateway.py
git commit -m "feat: add bounded LLM gateway execution"
```

### Task 5: Route legacy LLMClient through the Gateway without changing JSON behavior

**Files:**
- Modify: `llm_client.py`
- Modify: `tests/test_grading_config_generation_policy.py`
- Modify: `tests/test_llm_gateway.py`

**Interfaces:**
- Extends `LLMSettings` with `policy_profile: Mapping[str, object] | None = None`.
- Preserves every existing public `LLMClient` method and root-module import.

- [x] **Step 1: Write RED compatibility tests**

```python
def test_llm_client_chat_uses_gateway_timeout_and_request_id(monkeypatch):
    completion = client.json_from_text("prompt")
    assert completion == {"ok": True}
    assert fake_completions.calls[0]["timeout"] == 120.0
    assert sink.events[0].request_kind == "config_generation"
    assert sink.events[0].request_id

def test_parameter_fallback_is_bounded_and_not_counted_as_network_retry():
    fake = UnsupportedThenSuccess(["max_tokens", "max_completion_tokens"])
    assert client.json_from_text("prompt") == {"ok": True}
    assert fake.call_count == 3
    assert [event.compatibility_fallback for event in sink.events] == ["", "max_completion_tokens", "response_format"]
```

Retain the existing tests proving `json_from_text_once`/`json_from_images_once` make exactly one call. Add truncated JSON and repair tests proving all physical calls share one logical request ID and no more repair calls occur than before.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_grading_config_generation_policy.py -k "llm_single_request or llm_client_gateway or parameter_fallback or json_repair" -q`
Expected: new tests fail because `LLMClient` still calls the SDK directly.

- [x] **Step 3: Inject one Gateway per distinct client configuration**

```python
@dataclass
class LLMSettings:
    # existing fields unchanged
    policy_profile: Mapping[str, object] | None = None

class LLMClient:
    def __init__(self, settings, *, gateway_factory=LLMGateway):
        self.settings = settings
        self.gateway = gateway_factory(
            profile=dict(settings.policy_profile or {}),
            config_key=_gateway_config_key(settings.api_key, settings.base_url),
        )
```

Map calls explicitly: OCR/text-from-images → `recognition`; grading image JSON → `grading`; config client text/image JSON → `config_generation`. Do not send keys or full URLs into the config key: hash normalized base URL plus a constant local salt and include only the digest.

- [x] **Step 4: Preserve finite parameter fallback and single-request bypass**

`_create_chat_completion()` calls Gateway with `allow_retry=not single_request`. Each compatibility attempt calls Gateway with `allow_retry=False` so nested retries cannot multiply; the outer operation may retry only before compatibility handling. Pass an internal logical request ID through JSON truncation and repair calls.

- [x] **Step 5: Run GREEN and affected LLM/config regressions**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway.py tests\test_grading_config_generation_policy.py tests\test_config_generation_job.py tests\test_grading_completeness.py tests\test_standard_pdf_pages.py -q
```

Expected: selected files that exist pass; no existing single-request or JSON policy assertion changes.

- [x] **Step 6: Commit Task 5**

```powershell
git add llm_client.py tests/test_llm_gateway.py tests/test_grading_config_generation_policy.py
git commit -m "feat: route LLM client through gateway"
```

### Task 6: Propagate safe API profile overrides to current compatibility clients

**Files:**
- Modify: `backend/jobs/default_handlers.py`
- Modify: `web_app.py`
- Modify: `question_bank/services/ai_tagging_service.py`
- Modify: `tests/test_api_profile_store.py`
- Modify: `tests/test_llm_gateway_policy.py`

**Interfaces:**
- Consumes: `policy_overrides_from_profile(profile)`; returns only the twelve allowlisted non-secret policy keys.
- Produces no new API endpoint, UI control or profile migration.

- [x] **Step 1: Write RED profile propagation tests**

```python
def test_policy_override_copy_excludes_secrets_and_unrelated_fields():
    copied = policy_overrides_from_profile({
        "api_key": "secret",
        "base_url": "https://private.example/v1",
        "llm_grading_timeout_seconds": 240,
        "grading_model": "model",
    })
    assert copied == {"llm_grading_timeout_seconds": 240}

def test_active_backend_settings_receive_policy_overrides(monkeypatch):
    monkeypatch.setattr(default_handlers, "get_api_profile_store", lambda: FakeStore(PROFILE))
    settings = default_handlers._active_llm_settings()
    assert settings.policy_profile["llm_config_generation_timeout_seconds"] == 90
    assert "api_key" not in settings.policy_profile
```

Add equivalent profile-path tests for web settings and `_llm_settings_from_profile()` in tagging service. Environment-only constructors must yield `policy_profile=None` and use safe defaults.

- [x] **Step 2: Run RED**

Run: `..\..\runtime\python\python.exe -m pytest tests\test_api_profile_store.py tests\test_llm_gateway_policy.py -q`
Expected: new propagation assertions fail.

- [x] **Step 3: Pass only sanitized policy mappings**

```python
policy_profile=policy_overrides_from_profile(profile)
```

In `web_app.py`, use the already loaded `saved_profile`; do not add widgets or place policy values in environment variables. In tagging service, apply profile overrides only when the canonical profile store supplied the client; dedicated environment clients retain defaults.

- [x] **Step 4: Run GREEN and profile/tagging regressions**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_profile_store.py tests\test_grading_limits.py tests\test_question_bank_ai_tagging_quality.py tests\test_question_bank_tagging_config_state.py tests\test_tagging_sync_job.py tests\test_config_generation_job.py -q
```

Expected: selected files that exist pass; deprecated `objective_timeout` remains ignored and absent from `web_app.py`.

- [x] **Step 5: Commit Task 6**

```powershell
git add backend/jobs/default_handlers.py web_app.py question_bank/services/ai_tagging_service.py tests/test_api_profile_store.py tests/test_llm_gateway_policy.py
git commit -m "feat: load LLM gateway policy overrides"
```

### Task 7: Freeze P1-24 evidence, architecture facts, and handoff

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-13-p1-24-llm-gateway-core-implementation.md`

**Interfaces:**
- Produces exact P1-24 verification evidence and valid handoff state.

- [x] **Step 1: Run the complete focused and affected regression**

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_llm_gateway_policy.py tests\test_llm_gateway.py tests\test_llm_gateway_usage.py tests\test_request_pacer.py tests\test_api_profile_store.py tests\test_grading_config_generation_policy.py tests\test_config_generation_job.py tests\test_grading_completeness.py tests\test_standard_pdf_pages.py tests\test_question_bank_ai_tagging_quality.py tests\test_question_bank_tagging_config_state.py tests\test_tagging_sync_job.py tests\test_objective_batch_recognition_service.py tests\test_choice_recognition_chain.py -q
```

Expected: the exact selected set passes with 0 failed.

- [x] **Step 2: Assert package boundary and no infinite retry patterns**

Run:

```powershell
rg -n "OpenAI\(|chat\.completions\.create|responses\.create" backend/llm llm_client.py
rg -n "while\s+True|timeout\s*=\s*None" backend/llm llm_client.py
git diff --check
git status --short -- user_data
```

Expected: SDK calls exist only in protocol adapter execution points; no `while True` or `timeout=None` in Gateway/compatibility client; diff check passes; feature worktree has no `user_data/` entries. Existing P1-25-owned direct calls outside these paths are allowed and must not be edited.

- [x] **Step 3: Update architecture only after verified behavior**

Record that P1-24 adds the policy core, safe profile overrides, Chat/Responses adapters, finite retry classification, keyed pacing, request IDs and unified usage events. State explicitly that four direct callers and the objective batch `timeout=None` remain for P1-25, and no real model call was executed.

- [x] **Step 4: Run completion gates**

```powershell
git diff --check
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-13-p1-24-llm-gateway-core-implementation.md --repo .
```

Expected: diff clean; quick smoke passes; handoff validator emits one JSON line with `ok=true` after the handoff block is updated.

- [x] **Step 5: Compare root real-database fingerprints without SQLite**

From the main project root, record for each real database: length, `LastWriteTimeUtc`, and SHA-256 before the first feature edit and after all tests. The controller captured the pre-feature values before claim commit `52d3136` and before Task 1; the same exact triplets are preserved in the immutable tracked baseline at `docs/superpowers/plans/2026-07-12-p1-23-ops-protected-writes-implementation.md` Step 6, inherited from P1-22. Expected: all three values remain byte-for-byte identical. Any difference blocks commit and integration.

- grading: `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`
- question bank: `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`

- [x] **Step 6: Record `waiting_review` and create the feature commit**

Before the first feature edit, the claim commit must already have added the handoff block with immutable Stash baseline and `in_progress`. After tests pass, update it to `waiting_review/branch_head/passed/pending/not_required/unchanged/report_only`, stage only P1-24 files, and commit:

```powershell
git commit -m "feat: add unified LLM gateway core"
```

- [ ] **Step 7: Complete independent review and final plan-only handoff**

Review for retry multiplication, request-ID loss, unsafe logging, changed JSON behavior and P1-25 scope leakage. Critical/Important must be zero. Fix findings with focused RED/GREEN tests, rerun affected regression, then create a plan-only handoff commit whose block records its direct parent full reviewed SHA as `verified_pending_integration`.

## Verification and Recovery

- Baseline gate: run Task 1's policy test target after claim and before implementation; existing `tests/test_request_pacer.py`, two single-request tests, `tests/test_api_profile_store.py` and config/tagging focused tests must be green on the claimed baseline.
- Feature branch gate: task-level RED/GREEN, complete focused/affected regression, `git diff --check`, quick smoke, handoff validator and root real-database fingerprint guard.
- Integration gate: merge only the reviewed P1-24 chain, rerun the focused Gateway/LLM/config/tagging regression, then run full `tools/smoke_check.py`. Full smoke is mandatory because P1-24 changes shared model-request infrastructure.
- No user/browser acceptance is required because P1-24 has no visible production UI and uses no real API call.
- Code rollback: revert the isolated P1-24 commit chain. The old root `llm_client.py` import path remains; no data or profile migration needs rollback.
- Operational rollback: profile policy keys are optional. Removing them returns to safe defaults; invalid values fail before a request is sent.

## Implementation Evidence

- Claim and task chain: the immutable claim handoff preceded implementation; Tasks 1-6 are complete through `85285619ded13f1d3420318c7c511a68dd892f98`, with their focused RED/GREEN cycles and requested task reviews recorded in `.superpowers/sdd/` reports and progress ledger.
- Final focused and affected regression: the exact fourteen-file Task 7 command completed with `219 passed / 0 failed`.
- Boundary guard: `backend/llm/` and `llm_client.py` contain only the compatibility client constructor and the two Chat/Responses protocol execution points; neither `while True` nor `timeout=None` occurs in those paths. The four P1-25-owned direct caller chains and objective-batch `timeout=None` remain unchanged outside the P1-24 paths.
- Completion gates: `git diff --check` passed. Quick smoke passed document governance, static compilation of `403` first-party Python files, and idempotent initialization plus `integrity_check=ok` on temporary copies of both databases; full pytest remains the integration wave-end gate. The handoff validator parsed the required `waiting_review/branch_head/passed/pending/not_required/unchanged/report_only` record and is rerun against the clean feature commit because committed handoff validation intentionally rejects a dirty pre-commit worktree.
- Real-data guard: before claim commit `52d3136` and any feature edit, the controller captured the root grading database as `2863104` bytes / `2026-07-10T07:10:41.1221109Z` / SHA-256 `93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD` and the root question-bank database as `3461120` bytes / `2026-07-08T11:58:06.3320883Z` / SHA-256 `E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`. The immutable tracked P1-23 plan Step 6, inherited from P1-22, independently preserves those same triplets. The post-feature file-only read matched every value exactly. No SQLite connection or real model call was made, and worktree `user_data/` status is empty.
- Review boundary: Step 7 remains unchecked. Independent whole-branch review, Critical/Important disposition, and the plan-only `verified_pending_integration` handoff belong to the controller's next task.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-24
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
