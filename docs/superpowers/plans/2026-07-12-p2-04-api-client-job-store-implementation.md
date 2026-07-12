# P2-04 API Client, Error Contract, and Job Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-04
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 056c8bc3737e18da0ae3851404b9d418a403c05d
**用户自测：** none
**自测清单：** not_required

**Goal:** 为非生产 Vue 前端建立唯一类型化 API Client、统一脱敏错误契约和只恢复当前浏览器已记录任务的 Pinia Job Store。

**Architecture:** 原生 `fetch` Client 集中同源相对路径、request ID、JSON 解析、超时/中止和仅 GET 的有界重试；资源适配器拥有受控手写类型和运行时校验。Pinia Job Store 只持久化 Job ID、类型和跟踪时间，以每 Job 唯一轮询控制器、服务器 `updated_at` 和本地世代号防止取消/刷新竞态覆盖新状态。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, native Fetch/AbortController/Web Storage APIs, Vitest 4.1.10, Playwright 1.61.1, Python 3.12 repository guards.

## Global Constraints

- 实现必须符合 `docs/superpowers/specs/2026-07-12-p2-04-api-client-job-store-design.md`、`docs/superpowers/packages/phase-2-execution-packages.md` 和后端当前错误/Job 契约。
- 使用原生 `fetch` 和受控手写类型；不增加 Axios、OpenAPI 生成工具、Schema 库或其他生产依赖，不修改 `package-lock.json`。
- Client 只接受以 `/api/` 开头的同源相对路径；禁止远程 base URL、绝对 URL 和页面直接拼接 URL。
- 只有 GET 的临时网络失败和 5xx 可自动有界重试；写请求、422、404、409、超时、取消和契约错误不自动重放。
- `ApiError` 只暴露标准错误体中的安全字段；原始响应文本、堆栈、内部路径和密钥不进入通知或本地存储。
- 刷新后只恢复当前浏览器索引中的 Job；不扫描、列出或认领其他历史任务。
- Job 本地索引只保存 `id`、`jobType` 和 `trackedAt`；不保存 payload、result、error/details、下载路径或 API key。
- `cancel_requested=true` 不等于 `cancelled`；取消后仍以服务器 Job 状态为准并继续轮询到终态。
- 保留 P2-03 Session Store 公共行为、App Shell、路由、样式和五个桌面视口契约；本包不新增可见 UI、样式或用户短测。
- 不修改后端、JobManager/JobStore 状态机、数据库 Schema、`run.bat`/生产入口、评分规则或真实 `user_data/`。
- 测试只使用 mock Fetch、合成 Job 和 jsdom `localStorage`；不读取真实数据库、不调用真实模型。
- 执行模型为 T-H。每个新行为必须先有可见 RED，再有最小 GREEN；同一问题连续两次修复失败时安全停机。

---

### Task 1: Establish the claimed baseline and error/validation primitives

**Files:**
- Create: `frontend/src/api/validation.ts`
- Create: `frontend/src/api/errors.ts`
- Create: `frontend/src/api/__tests__/errors.spec.ts`
- Modify: `docs/superpowers/plans/2026-07-12-p2-04-api-client-job-store-implementation.md`

**Interfaces:**
- Produces: `isRecord(value): value is Record<string, unknown>`, `isNullableString(value): value is string | null`, `ApiErrorKind`, `ApiError`, `parseErrorResponse(payload, responseRequestId, status)`, and `toNotification(error, impact)`.

- [x] **Step 1: Record the clean baseline before source edits**

Run from this P2-04 worktree:

```powershell
git status --short --branch
git merge-base --is-ancestor 056c8bc3737e18da0ae3851404b9d418a403c05d HEAD
git status --short -- user_data
npm run lint
npm run typecheck
npm run test -- --maxWorkers=1
npm run build
```

Expected: branch contains the plan baseline; source status and `user_data/` are clean; all four front-end commands exit 0. From the repository root, record but never open both real databases with `Get-Item` and `Get-FileHash -Algorithm SHA256`; retain size, UTC modification time and SHA-256 for the final comparison.

- [x] **Step 2: Write failing error-contract tests**

Create `errors.spec.ts` with direct assertions for the exact backend shape and redaction:

```ts
import { describe, expect, it } from 'vitest'
import { ApiError, parseErrorResponse, toNotification } from '../errors'

describe('API error contract', () => {
  it('keeps only the validated backend error and matching request id', () => {
    const error = parseErrorResponse({
      error: { code: 'validation_error', message: 'Invalid request', details: { errors: [] }, request_id: 'req-7' },
    }, 'req-7', 422)
    expect(error).toMatchObject({ kind: 'validation', status: 422, code: 'validation_error', requestId: 'req-7', retryable: false })
  })

  it('rejects request-id disagreement as a contract error', () => {
    const error = parseErrorResponse({
      error: { code: 'job_not_found', message: 'Job not found', details: {}, request_id: 'body-id' },
    }, 'header-id', 404)
    expect(error).toMatchObject({ kind: 'contract', code: 'invalid_error_contract', requestId: 'header-id' })
  })

  it('never places details in the user notification', () => {
    const error = new ApiError({ kind: 'server', status: 500, code: 'server_error', message: 'Request failed', details: { path: 'private' }, requestId: 'req-9', retryable: true })
    expect(toNotification(error, '考试列表保持上次内容')).toEqual({ message: 'Request failed', impact: '考试列表保持上次内容', retryable: true, requestId: 'req-9' })
  })
})
```

- [x] **Step 3: Run RED**

Run `npm run test -- src/api/__tests__/errors.spec.ts --maxWorkers=1` from `frontend/`.

Expected: FAIL because `validation.ts` and `errors.ts` do not exist.

- [x] **Step 4: Implement minimal validation and error primitives**

Implement these exact public types and constructor shape:

```ts
export type ApiErrorKind = 'validation' | 'not_found' | 'conflict' | 'server' | 'network' | 'timeout' | 'cancelled' | 'contract'

export interface ApiErrorInit {
  kind: ApiErrorKind
  status: number | null
  code: string
  message: string
  details: Record<string, unknown>
  requestId: string
  retryable: boolean
}

export class ApiError extends Error implements ApiErrorInit {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId: string
  readonly retryable: boolean
  constructor(init: ApiErrorInit) {
    super(init.message)
    this.name = 'ApiError'
    Object.assign(this, init)
  }
}
```

`parseErrorResponse` accepts only `{ error: { code: string, message: string, details: plain object, request_id: non-empty string } }`; map 400/422 to `validation`, 404 to `not_found`, 409 to `conflict`, and 500–599 to `server`. Any malformed body, unsupported status, or header/body request-ID mismatch returns `kind: 'contract'`, `code: 'invalid_error_contract'`, fixed safe text and no raw payload. `toNotification` returns only `{ message, impact, retryable, requestId }`.

- [x] **Step 5: Run GREEN and commit**

```powershell
npm run test -- src/api/__tests__/errors.spec.ts --maxWorkers=1
npm run typecheck
git add -- frontend/src/api/validation.ts frontend/src/api/errors.ts frontend/src/api/__tests__/errors.spec.ts
git commit -m "feat: define P2-04 API errors"
```

Expected: focused tests and typecheck pass; commit contains only error/validation primitives and tests.

### Task 2: Build the same-origin Fetch client with request IDs, aborts, and bounded GET retry

**Files:**
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/__tests__/client.spec.ts`

**Interfaces:**
- Consumes: `ApiError`, `parseErrorResponse`.
- Produces: `ApiMethod`, `ResponseDecoder<T>`, `ApiRequestOptions<T>`, `ApiClientDependencies`, `createApiClient(dependencies)`, and singleton `apiClient` with `request<T>(path, options): Promise<T>`.

- [x] **Step 1: Write failing client tests**

Cover the public contract with injected fetch, delay and request-ID dependencies:

```ts
const client = createApiClient({
  fetch: fetchMock,
  createRequestId: () => 'client-req-1',
  delay: vi.fn(async () => undefined),
})

await expect(client.request('/api/sessions', { decode: (value) => value })).resolves.toEqual({ ok: true })
expect(fetchMock).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ headers: expect.objectContaining({ accept: 'application/json', 'x-request-id': 'client-req-1' }) }))
```

Add separate tests proving: absolute and non-`/api/` paths reject before fetch; a successful malformed decoder throws `contract`; standard 422/404/500 bodies become `ApiError`; GET retries exactly three attempts for network and 500; POST makes one attempt; 404 makes one attempt; timeout becomes `timeout`; caller abort becomes `cancelled`; after either abort there is no delayed retry.

- [x] **Step 2: Run RED**

Run `npm run test -- src/api/__tests__/client.spec.ts --maxWorkers=1`.

Expected: FAIL because `client.ts` does not exist.

- [x] **Step 3: Implement the public client contract**

Use these exact interfaces:

```ts
export type ApiMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type ResponseDecoder<T> = (payload: unknown) => T

export interface ApiRequestOptions<T> {
  method?: ApiMethod
  body?: unknown
  decode: ResponseDecoder<T>
  signal?: AbortSignal
  timeoutMs?: number
}

export interface ApiClientDependencies {
  fetch: typeof globalThis.fetch
  createRequestId: () => string
  delay: (milliseconds: number, signal: AbortSignal) => Promise<void>
}
```

`createApiClient` defaults to `globalThis.fetch`, `crypto.randomUUID()` and an abort-aware `setTimeout` delay. `request` rejects paths not matching `^/api/`; sets `accept`, `x-request-id`, and JSON `content-type` only when a body exists; defaults timeout to 15,000 ms; merges caller and timeout cancellation through one internal controller; and clears timers/listeners in `finally`.

For successful responses, parse JSON and call `decode`; wrap JSON/decode failures as `ApiError(kind='contract', code='invalid_success_contract')`. For non-success responses, parse JSON only as unknown and call `parseErrorResponse`; do not preserve raw text. Retry only GET network errors and `kind='server'` responses with delays 250 ms then 500 ms, for at most three total attempts. Timeout, caller abort, contract, 4xx and every non-GET use one attempt.

- [x] **Step 4: Run GREEN, lint, and commit**

```powershell
npm run test -- src/api/__tests__/client.spec.ts src/api/__tests__/errors.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/api/client.ts frontend/src/api/__tests__/client.spec.ts
git commit -m "feat: add typed P2-04 API client"
```

Expected: focused tests, typecheck and lint pass.

### Task 3: Migrate the sessions adapter without changing Session Store behavior

**Files:**
- Modify: `frontend/src/api/sessions.ts`
- Modify: `frontend/src/__tests__/session-store.spec.ts`

**Interfaces:**
- Consumes: singleton `apiClient.request<T>()` and `ResponseDecoder<T>`.
- Preserves: `SessionSummary`, `SessionListResponse`, `SessionLoader`, `SessionReadError`, `isSessionSummary`, and `fetchSessions(): Promise<SessionSummary[]>`.

- [x] **Step 1: Change adapter tests first and verify RED**

Update the sessions adapter test to expect the shared Client behavior: the fetch call carries `accept` plus `x-request-id`, and a standard 500 error, malformed success payload, and network rejection still surface only `SessionReadError('无法读取考试列表')`. Add an assertion that `Session Store` preserves its saved candidate after a Client failure and restores it after a successful injected loader.

Run `npm run test -- src/__tests__/session-store.spec.ts --maxWorkers=1`.

Expected: FAIL because the current adapter calls `fetch` directly and does not use shared request headers/error parsing.

- [x] **Step 2: Replace only the adapter transport**

Keep the existing public types and validators. Add a decoder:

```ts
function decodeSessionListResponse(value: unknown): SessionListResponse {
  if (!isSessionListResponse(value)) throw new Error('invalid session response')
  return value
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  try {
    const payload = await apiClient.request('/api/sessions', { decode: decodeSessionListResponse })
    return payload.items.filter((session) => session.is_deleted === false)
  } catch {
    throw new SessionReadError()
  }
}
```

Do not change `stores/session.ts`, its storage key, selection semantics, messages or exported actions.

- [x] **Step 3: Run Session/App GREEN and commit**

```powershell
npm run test -- src/__tests__/session-store.spec.ts src/__tests__/App.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/api/sessions.ts frontend/src/__tests__/session-store.spec.ts
git commit -m "refactor: route sessions through API client"
```

Expected: Session Store and root App behavior remain green.

### Task 4: Define and validate the public Job API adapter

**Files:**
- Create: `frontend/src/api/jobs.ts`
- Create: `frontend/src/api/__tests__/jobs.spec.ts`

**Interfaces:**
- Produces: `JOB_STATUSES`, `TERMINAL_JOB_STATUSES`, `JobStatus`, `JobResponse`, `JobApi`, `decodeJobResponse`, singleton `jobApi.getJob(id, signal?)`, and `jobApi.cancelJob(id, signal?)`.

- [x] **Step 1: Write failing Job contract tests**

Use one complete synthetic response and reject every invalid status/field type:

```ts
const job = {
  id: 41, job_type: 'report_export', payload: {}, result: {}, status: 'running',
  progress: 0.5, stage: 'rendering', detail: '2/4', error: null,
  cancel_requested: false, created_at: '2026-07-12T10:00:00Z', started_at: null,
  updated_at: '2026-07-12T10:00:01Z', finished_at: null,
}

expect(decodeJobResponse(job)).toEqual(job)
expect(() => decodeJobResponse({ ...job, status: 'cancelling' })).toThrow()
```

Mock the shared client and assert `getJob(41)` requests `/api/jobs/41` with GET, while `cancelJob(41)` requests `/api/jobs/41/cancel` with POST. Invalid, non-positive or unsafe integer IDs must reject before a request.

- [x] **Step 2: Run RED**

Run `npm run test -- src/api/__tests__/jobs.spec.ts --maxWorkers=1`.

Expected: FAIL because `jobs.ts` does not exist.

- [x] **Step 3: Implement the exact Job type and adapter**

Define `JobStatus` from:

```ts
export const JOB_STATUSES = ['queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled'] as const
export const TERMINAL_JOB_STATUSES = new Set<JobStatus>(['succeeded', 'failed', 'cancelled'])
```

`JobResponse` uses the backend field names and types from `backend/api/schemas/jobs.py`; `payload` and `result` must be plain objects, progress must be finite, IDs must be positive safe integers, and nullable timestamps/error must be string or null. `JobApi` is:

```ts
export interface JobApi {
  getJob(id: number, signal?: AbortSignal): Promise<JobResponse>
  cancelJob(id: number, signal?: AbortSignal): Promise<JobResponse>
}
```

Use the singleton Client and decoder; do not add generic submit endpoints or domain-specific payload types in P2-04.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/api/__tests__/jobs.spec.ts src/api/__tests__/client.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/api/jobs.ts frontend/src/api/__tests__/jobs.spec.ts
git commit -m "feat: add validated Job API adapter"
```

Expected: focused tests and checks pass.

### Task 5: Implement minimal Job persistence and refresh recovery

**Files:**
- Create: `frontend/src/stores/jobs.ts`
- Create: `frontend/src/__tests__/job-store.spec.ts`

**Interfaces:**
- Consumes: `JobApi`, `JobResponse`, `TERMINAL_JOB_STATUSES`, `ApiError`.
- Produces: `JOB_STORAGE_KEY`, `PersistedJobReference`, `JobSyncError`, `JobStoreDependencies`, and `useJobStore` actions `initialize`, `track`, `refresh`, `stopPolling`, `remove`, `clearCompleted`.

- [x] **Step 1: Write persistence and recovery RED tests**

Set active Pinia and clear storage before each test. Assert exact minimal persistence:

```ts
const store = useJobStore()
store.track(runningJob, dependencies)
expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
  { id: 41, jobType: 'report_export', trackedAt: '2026-07-12T10:00:00.000Z' },
])
expect(localStorage.getItem(JOB_STORAGE_KEY)).not.toContain('payload')
expect(localStorage.getItem(JOB_STORAGE_KEY)).not.toContain('result')
```

Add tests that `initialize` requests only valid persisted IDs; active jobs remain tracked and schedule polling; terminal jobs remain visible without polling; malformed entries are removed individually; an entirely malformed storage value is cleared; 404 removes the persisted reference and leaves a runtime `not_found` sync error; network/500 preserves the last successful snapshot.

- [x] **Step 2: Run RED**

Run `npm run test -- src/__tests__/job-store.spec.ts --maxWorkers=1`.

Expected: FAIL because `stores/jobs.ts` does not exist.

- [x] **Step 3: Implement minimal state and storage helpers**

Use this dependency boundary so tests control time without production globals leaking into state:

```ts
export interface JobStoreDependencies {
  api: JobApi
  now: () => Date
  schedule: (callback: () => void, milliseconds: number) => ReturnType<typeof setTimeout>
  cancelScheduled: (handle: ReturnType<typeof setTimeout>) => void
  pollIntervalMs: number
  maxBackoffMs: number
}
```

The Store exposes reactive `jobs: Record<number, JobResponse>` and `syncErrors: Record<number, JobSyncError>`. Timers, abort controllers, in-flight promises, generation counters and retry counts remain private non-reactive maps. Persist references sorted by ID. `initialize(overrides?)` installs dependencies once for the Store lifetime, validates storage, then refreshes only those IDs. `track(job, overrides?)` records the snapshot/reference and starts polling only for non-terminal status.

On 404, stop the controller, remove only that persisted reference, and set a fixed safe `JobSyncError`. On network/server failure, preserve `jobs[id]`, set a fixed safe sync error, and schedule bounded Store-level backoff. Do not serialize `ApiError` or its details.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/job-store.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/stores/jobs.ts frontend/src/__tests__/job-store.spec.ts
git commit -m "feat: recover tracked browser jobs"
```

Expected: persistence/recovery tests and checks pass.

### Task 6: Add single-loop polling, cancellation, and stale-response protection

**Files:**
- Modify: `frontend/src/stores/jobs.ts`
- Modify: `frontend/src/__tests__/job-store.spec.ts`

**Interfaces:**
- Adds: `cancel(id): Promise<void>`, `stopAllPolling()`, and internal `shouldReplaceJob(current, next)` semantics.

- [x] **Step 1: Add failing polling and race tests**

Add tests proving:

1. two `track`/poll-start calls for ID 41 create one timer and one in-flight GET;
2. `succeeded`, `failed` and `cancelled` each stop the timer and abort controller;
3. `stopPolling(41)` increments generation so a later-resolving old GET cannot change the snapshot;
4. a cancel response `running + cancel_requested=true` replaces the old snapshot and continues polling;
5. a later `updated_at` always wins, while an older poll response cannot overwrite a newer cancel/terminal snapshot;
6. cancel failure preserves the old Job and sets a retryable sync error;
7. `remove`, `clearCompleted`, `stopAllPolling`, and Store disposal clear timers/controllers and prevent later state writes.

Use deferred Promises rather than real sleeps; use injected timer spies rather than fake production time.

- [x] **Step 2: Run RED**

Run `npm run test -- src/__tests__/job-store.spec.ts --maxWorkers=1`.

Expected: FAIL on duplicate-loop, cancel and stale-response assertions.

- [x] **Step 3: Implement concurrency rules**

`refresh(id)` returns the existing in-flight Promise when one exists. Each request captures the current generation; `stopPolling/remove/stopAllPolling` increment it and abort the controller. Before applying a response, require the captured generation to remain current and the Job to remain tracked.

`shouldReplaceJob(current, next)` compares parsed `updated_at`: newer wins, older loses. For equal timestamps, a terminal status wins over a non-terminal status and `cancel_requested=true` wins over false; otherwise the newly completed server response wins. Invalid timestamps are rejected earlier by `decodeJobResponse` only if they are not strings; comparison treats unparseable strings as equal and applies the tie rules.

`cancel(id)` rejects unknown/terminal IDs without a network call, invokes `api.cancelJob`, applies the same freshness rule, and polls until a real terminal status. A failed cancel sets a safe sync error without changing the Job snapshot. Register `onScopeDispose(stopAllPolling)`.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/job-store.spec.ts src/api/__tests__/jobs.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/stores/jobs.ts frontend/src/__tests__/job-store.spec.ts
git commit -m "feat: coordinate Job polling and cancellation"
```

Expected: all Job tests and checks pass with no real timers left open.

### Task 7: Add the notification port and repository boundary guards

**Files:**
- Create: `frontend/src/notifications.ts`
- Create: `frontend/src/__tests__/notifications.spec.ts`
- Create: `tests/test_frontend_api_client.py`
- Modify: `tests/test_frontend_app_shell.py`

**Interfaces:**
- Consumes: user-safe notification returned by `toNotification`.
- Produces: `AppNotification`, `NotificationSink`, `createMemoryNotificationSink()`, and static repository guards for the P2-04 boundary.

- [x] **Step 1: Write failing notification and repository tests**

The TypeScript test proves the memory sink accepts only `{ message, impact, retryable, requestId }`, publishes once per explicit call, supports unsubscribe, and never retains `details`, `payload` or `result`.

Create the Python guard with exact source rules:

```python
def test_p2_04_has_one_fetch_boundary_and_no_absolute_api_urls() -> None:
    ts_files = list(SRC.rglob("*.ts")) + list(SRC.rglob("*.vue"))
    direct_fetch = [path for path in ts_files if "fetch(" in path.read_text(encoding="utf-8")]
    assert direct_fetch == [SRC / "api" / "client.ts"]
    for path in ts_files:
        source = path.read_text(encoding="utf-8")
        assert "http://127.0.0.1:8000/api" not in source
        assert "https://" not in source

def test_p2_04_job_storage_is_minimal_and_no_visible_ui_was_added() -> None:
    store = (SRC / "stores" / "jobs.ts").read_text(encoding="utf-8")
    assert "id: job.id" in store
    assert "jobType: job.job_type" in store
    assert "trackedAt:" in store
    assert "payload:" not in store.split("function persistReferences", 1)[1]
    assert "result:" not in store.split("function persistReferences", 1)[1]
    assert not (SRC / "components" / "JobNotifications.vue").exists()
```

Update the P2-03 guard so it now requires `api/client.ts` and `stores/jobs.ts`, while retaining the one-navigation-source assertion and every desktop/view/style guard.

- [x] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/notifications.spec.ts --maxWorkers=1
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_api_client.py tests\test_frontend_app_shell.py -q
```

Expected: notification test fails because the port is absent; the old P2-03 guard fails because it still forbids P2-04 files.

- [x] **Step 3: Implement the non-visual notification port and guards**

Use exact interfaces:

```ts
export interface AppNotification { message: string; impact: string; retryable: boolean; requestId: string }
export interface NotificationSink {
  publish(notification: AppNotification): void
  subscribe(listener: (notification: AppNotification) => void): () => void
}
```

`createMemoryNotificationSink` keeps only listener functions, does not persist or render notifications, and publishes a fresh shallow copy to each listener. Do not add Vue components, CSS, routes, App Shell controls, Element imports or a global toast implementation.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/notifications.spec.ts --maxWorkers=1
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_api_client.py tests\test_frontend_app_shell.py -q
npm run typecheck
npm run lint
git add -- frontend/src/notifications.ts frontend/src/__tests__/notifications.spec.ts tests/test_frontend_api_client.py tests/test_frontend_app_shell.py
git commit -m "test: guard P2-04 frontend boundaries"
```

Expected: notification and repository guard tests pass; no visible UI file or style diff exists.

### Task 8: Document the implemented boundary and prepare independent review

**Files:**
- Modify: `frontend/README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p2-04-api-client-job-store-implementation.md`

**Interfaces:**
- Produces: contributor guidance, implemented architecture facts, complete verification evidence, feature commit with `waiting_review`, and later a plan-only verified handoff.

- [x] **Step 1: Document usage, safety, and non-goals**

Update `frontend/README.md` with Client usage, decoder ownership, safe GET retry, `ApiError` fields, Job storage key/contents, refresh recovery, polling/cancel semantics, notification-port non-UI boundary and explicit business-page/backend exclusions. Replace the statement that a general Client does not exist.

Update `ARCHITECTURE.md` only with implemented P2-04 facts: native Fetch Client and unified error contract exist; Session reads use it; current-browser Job references recover through GET; cancellation remains cooperative/server-authoritative; Vue remains non-production and no business page was migrated.

- [x] **Step 2: Run focused and affected regression gates**

```powershell
npm run test -- src/api/__tests__/errors.spec.ts src/api/__tests__/client.spec.ts src/api/__tests__/jobs.spec.ts src/__tests__/session-store.spec.ts src/__tests__/job-store.spec.ts src/__tests__/notifications.spec.ts src/__tests__/App.spec.ts --maxWorkers=1
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_foundation.py tests\test_frontend_portable_packaging.py tests\test_frontend_design_system.py tests\test_frontend_app_shell.py tests\test_frontend_api_client.py -q
npm run e2e
npm run lint
npm run typecheck
npm run test
npm run build
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\smoke_check.py --skip-tests
git diff --check
```

Expected: every command exits 0; App Shell browser tests remain unchanged and use only synthetic sessions. If any test is unstable or public front-end infrastructure outside P2-04 changes, investigate and run the additional regression required by repository rules before claiming success.

- [x] **Step 3: Verify real data, update handoff, and commit the feature state**

From the repository root, repeat both database size/UTC/SHA-256 reads and require exact equality with Task 1. Confirm `git status --short -- user_data` is empty in the P2-04 worktree and the commit diff contains no `user_data`, caches, build output, report artifacts or secrets.

Set the handoff block to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: not_required`, `真实数据指纹: unchanged`, preserve the immutable stash baseline, and keep `夜间动作: report_only`. Commit all remaining P2-04 source/test/docs changes without `user_data/`.

- [x] **Step 4: Run handoff validation and independent review**

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p2-04-api-client-job-store-implementation.md --repo .
```

Expected before review: exit 0, single-line JSON, `ok=true`, status `waiting_review`. Perform a fresh whole-branch review against the approved design and current backend error/Job contracts. Critical/Important findings must be zero; each behavior fix starts with a failing regression and reruns its affected commands.

- [x] **Step 5: Create the plan-only final handoff commit**

After independent review passes, set `交接状态: verified_pending_integration`, record the direct parent's full reviewed SHA in `功能提交`, set automatic verification and independent review to `passed`, keep user acceptance `not_required`, real-data fingerprint `unchanged`, and nightly action `independent_candidate_allowed`. Commit only this plan, rerun `handoff_status.py`, and require `ok=true` with no issues.

**2026-07-12 initial independent-review correction evidence:**

- Review of `056c8bc3737e18da0ae3851404b9d418a403c05d..910372f81b2c269018fef51afd5d2128957fbc10` found Critical 0, Important 5, Minor 0.
- Added RED regressions for retry-delay cancellation normalization, dot-segment API path traversal, cancellation disposal races, non-retryable Store errors, and sanitizing legacy persisted references.
- All five regressions are GREEN after minimal fixes; the affected full gate passes 99 Vitest tests, 17 Python guards, lint, typecheck and build. A fresh whole-branch re-review was required before Step 4 could be checked.
- Fresh re-review of `056c8bc3737e18da0ae3851404b9d418a403c05d..21cc0887c3bbefa93828505caad479a5decd26c8` closed all five prior findings and reported Critical 0, Important 0, Minor 0. Final Chromium 13/13, quick smoke, diff and two-database fingerprint guards also passed.

### Task 9: Integrate P2-04 through GitHub main and synchronize the baseline

**Files:**
- Modify on integration branch: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify on integration branch only if needed: shared architecture/evidence files

**Interfaces:**
- Consumes: complete `verified_pending_integration` P2-04 commit chain.
- Produces: P2-04 merged into GitHub `main`, updated authoritative Index, and synchronized local baseline.

- [ ] **Step 1: Merge the verified feature chain into a fresh integration branch**

Create a P2-04 integration worktree/branch from latest `origin/main`, verify the feature handoff from the repository root, merge only P2-04, and confirm no `user_data/` path enters the diff.

- [ ] **Step 2: Run integration gates**

Run all P2-04 focused tests, affected Python guards, unmodified lint/typecheck/unit/build/e2e, full `tools/smoke_check.py`, `git diff --check`, and before/after root database fingerprint comparison. P2-04 changes the shared front-end request boundary and adds the core Job state layer, so integration must not reuse an older full-smoke result.

- [ ] **Step 3: Update the authoritative package state**

Set P2-04 to `merged`, update Phase 2 counts/current queue/next action in `EXECUTION_INDEX.md`, and do not copy dynamic state into Phase maps or the nightly matrix. Preserve the sample-page gate: P2-05 may become the next candidate, but no later business-page migration is declared complete.

- [ ] **Step 4: Push, create and merge the integration PR**

Push the integration branch, create a PR targeting GitHub `main`, verify its exact head SHA and checks, then merge without directly pushing `main`.

- [ ] **Step 5: Synchronize and safely clean up**

Fetch/prune, fast-forward root `main` and active worktrees to `origin/main`, delete only integration/feature branches proven merged by `git branch --merged origin/main`, and remove linked worktrees only after source status, `user_data/` and Windows reparse-point checks pass. Never delete or move a worktree directory recursively by hand.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-04
**交接状态：** verified_pending_integration
**功能提交：** 21cc0887c3bbefa93828505caad479a5decd26c8
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
