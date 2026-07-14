# P1-27 请求级只读连接复用 Implementation Plan

> **执行要求：** 使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务执行；每个行为改动必须先得到明确 RED，再做最小 GREEN。

**执行包：** P1-27
**用户自测：** none
**自测清单：** not_required
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** `0564d60697d1e6e643491679099ed3e796be32ca`

**目标：** 只在 P1-26 已证明昂贵的 Training/Graph 只读请求中复用显式连接，保持请求级稳定候选、只读拒写、异常关闭和线程隔离；保留 Streamlit 与写路径的旧构造/事务行为，并用同一生成数据证明性能改善。

**设计：** `docs/superpowers/specs/2026-07-14-p1-27-request-scoped-read-connections-design.md`

## 固定边界

- 目标路由仅为 Training diagnosis/plan preview 与 Graph profiles/rows/evidence。
- `POST /api/training/tasks` 及所有其他写路由不注入只读连接。
- P1-15 Question Bank GET 继续每请求捕获零源写入候选；本包只把它作为性能控制组。
- 不引入第三方连接池、进程级池、跨请求缓存、索引、Schema、迁移、WAL/busy-timeout 调整或 Phase 3 仓储拆分。
- 不修改评分规则、题号、活动知识点、推荐排序、响应 Schema、OpenAPI、Streamlit 或错误公开文案。
- 所有写测试只使用临时双库；真实 `user_data/` 只允许读取文件大小、UTC mtime 与 SHA-256。
- 性能报告只含生成数据聚合指标，不含路径、SQL、请求体、学生/题目正文、请求 ID 或逐样本原始数据。

## 预期文件

- Modify `db_manager.py`
- Modify `question_bank/database/schema.py`
- Modify `question_bank/services/question_read_service.py`
- Create `backend/api/read_connections.py`
- Modify `backend/api/dependencies.py`
- Modify `backend/api/routers/training.py`
- Modify `backend/api/routers/graph.py`
- Modify `integration/diagnosis_profile_service.py`
- Modify `integration/question_tag_projection_service.py`
- Modify `question_bank/recommendation/practice_plan_service.py`
- Modify `question_bank/services/question_frequency_service.py`
- Modify `question_bank/services/source_question_link_service.py`
- Create `tools/benchmark_request_connections.py`
- Create `tools/performance/request_connection_report.py`
- Modify `tools/performance/runner.py`
- Create `tests/test_request_read_connections.py`
- Create `tests/test_request_connection_benchmark.py`
- Modify existing DB/service/API tests listed per task
- Create `docs/performance/p1-27-request-connection-comparison.json`
- Create `docs/performance/p1-27-request-connection-comparison.md`
- Modify `ARCHITECTURE.md`
- Modify this plan

## Task 0: Claim P1-27 in a clean daytime feature worktree

### Step 1: Recheck authority and baseline

- [ ] Read `AGENTS.md`, `ARCHITECTURE.md`, packages README/Index/matrix/automation/worktree manuals, P1-27 phase definition, this design and plan.
- [ ] Fetch `origin` and require `origin/main` to equal the full plan SHA.
- [ ] Require Index P1-27=`ready`, matrix P1-27=`daytime_only`, local time in the supervised daytime window, and no existing P1-27 implementation branch/worktree.
- [ ] Read-only capture root real-database size/UTC-mtime/SHA-256 outside Git.

Expected: all gates pass; otherwise stop without creating the feature channel.

### Step 2: Create the feature worktree

```powershell
git worktree add -b codex/p1-27-request-read-connections .worktrees/p1-27-request-read-connections origin/main
```

- [ ] Verify source status clean and `git status --short -- user_data` empty.
- [ ] Record the immutable existing stash SHA list.

### Step 3: Create the claim commit

- [ ] Copy only this plan into the feature worktree.
- [ ] Add exactly one `HANDOFF_STATUS` block:
  - package `P1-27`
  - status `in_progress`
  - functional commit `none`
  - automated validation `pending`
  - independent review `pending`
  - user acceptance `not_required`
  - real-data fingerprint `unchanged`
  - immutable stash baseline
  - nightly action `report_only`
- [ ] Commit only this plan:

```powershell
git add docs/superpowers/plans/2026-07-14-p1-27-request-scoped-read-connections-implementation.md
git commit -m "docs: claim P1-27 request read connections"
```

Expected: claim is the first package commit after the merge baseline and changes one plan file.

### Step 4: Bring in the approved design

- [ ] Cherry-pick the standalone design commit after the claim commit.
- [ ] Run `tools/handoff_status.py`; expect valid `in_progress`.

## Task 1: Add explicit non-owning connection primitives

**Files:**

- Modify `db_manager.py`
- Modify `question_bank/database/schema.py`
- Modify `question_bank/services/question_read_service.py`
- Create `tests/test_request_read_connections.py`
- Modify `tests/test_db_manager.py` if required by the existing test split

### Step 1: Write RED ownership tests

- [ ] Add `test_db_manager_borrowed_connection_reuses_identity_without_closing`.
- [ ] Add `test_db_manager_nested_context_does_not_commit_or_end_request_snapshot`.
- [ ] Add `test_question_bank_connect_borrowed_connection_does_not_own_lifecycle`.
- [ ] Add `test_snapshot_connection_allows_cross_worker_teardown_but_rejects_writes`.
- [ ] Add `test_legacy_connections_keep_existing_pragmas_and_close_behavior`.

Run:

```powershell
runtime\python\python.exe -m pytest tests\test_request_read_connections.py -q
```

Expected RED: missing external connection parameter/public request connection helper/non-owning behavior.

### Step 2: Implement the minimal primitives

- [ ] Add optional keyword-only `external_connection` to `DBManager.__init__`.
- [ ] Add a private non-owning proxy returned by `_connect()` only when external connection exists.
- [ ] Proxy `with`/`close` must not commit, roll back or close the underlying request connection; data access delegates unchanged.
- [ ] Extend question-bank `connect()` with optional keyword-only external connection; borrowed branch only yields.
- [ ] Expose a validated snapshot read-connection helper with `check_same_thread` parameter; defaults preserve P1-15 behavior.
- [ ] Request use sets `check_same_thread=False`, `mode=ro`, `query_only`, stable `BEGIN`, current foreign-key/busy-timeout validation and instrumentation.

### Step 3: Run GREEN and legacy regression

```powershell
runtime\python\python.exe -m pytest tests\test_request_read_connections.py tests\test_db_performance_instrumentation.py tests\test_api_question_bank_routes.py -q
```

Expected: new ownership tests and legacy snapshot/instrumentation tests pass.

### Step 4: Commit Task 1

```powershell
git add db_manager.py question_bank/database/schema.py question_bank/services/question_read_service.py tests/test_request_read_connections.py
git commit -m "feat: add borrowed readonly connection primitives"
```

## Task 2: Propagate the borrowed Question Bank connection through read services

**Files:**

- Modify `integration/diagnosis_profile_service.py`
- Modify `integration/question_tag_projection_service.py`
- Modify `question_bank/recommendation/practice_plan_service.py`
- Modify `question_bank/services/question_frequency_service.py`
- Modify `question_bank/services/source_question_link_service.py`
- Modify `tests/test_diagnosis_profile_service.py`
- Modify `tests/test_question_tag_projection_service.py`
- Modify `tests/test_practice_plan_service.py`
- Modify `tests/test_question_frequency_service.py`
- Modify `tests/test_source_question_link_service.py`

### Step 1: Write RED propagation tests

- [ ] Diagnosis tag path uses one injected grading connection and one injected question-bank connection.
- [ ] Projection skips `initialize_database()` when borrowed and passes the identical connection to links/questions/tags reads.
- [ ] Practice question-tag path skips initialization and reuses the identical connection for candidate, frequency and exclusion reads.
- [ ] Frequency batch read and SourceLink list/confirmed-ID read accept borrowed connections.
- [ ] Service exceptions do not close the borrowed connection.
- [ ] Existing constructors without external connections still initialize/open/close as before.

Run only the named tests and confirm RED for missing parameters/extra connection identities.

### Step 2: Implement minimal optional parameters

- [ ] Add keyword-only `question_bank_connection` to `DiagnosisProfileService` and propagate only through tag projection.
- [ ] Add keyword-only borrowed connection to Projection/Practice/Frequency/SourceLink constructors.
- [ ] Guard `initialize_database()` only in borrowed read paths; legacy and write paths remain unchanged.
- [ ] Replace relevant `connect(self.db_path)` calls with `connect(..., external_connection=...)`.
- [ ] Pass the connection to nested read service constructors.
- [ ] Do not modify legacy/skill branches beyond type-safe defaults required to preserve behavior.

### Step 3: Run GREEN and affected service regression

```powershell
runtime\python\python.exe -m pytest tests\test_diagnosis_profile_service.py tests\test_question_tag_projection_service.py tests\test_practice_plan_service.py tests\test_question_frequency_service.py tests\test_source_question_link_service.py -q
```

Expected: propagation identity, exception ownership and all existing behavior pass.

### Step 4: Commit Task 2

```powershell
git add integration/diagnosis_profile_service.py integration/question_tag_projection_service.py question_bank/recommendation/practice_plan_service.py question_bank/services/question_frequency_service.py question_bank/services/source_question_link_service.py tests/test_diagnosis_profile_service.py tests/test_question_tag_projection_service.py tests/test_practice_plan_service.py tests/test_question_frequency_service.py tests/test_source_question_link_service.py
git commit -m "feat: reuse question bank reads within a request"
```

## Task 3: Add the FastAPI request read context and route it only to read endpoints

**Files:**

- Create `backend/api/read_connections.py`
- Modify `backend/api/dependencies.py`
- Modify `backend/api/routers/training.py`
- Modify `backend/api/routers/graph.py`
- Modify `tests/test_api_training_routes.py`
- Modify `tests/test_api_graph_routes.py`
- Extend `tests/test_request_read_connections.py`

### Step 1: Write RED API lifecycle tests

- [ ] One target request captures each database once and opens one working connection per database.
- [ ] Diagnosis and Practice dependencies in plan preview resolve from the same cached context.
- [ ] Normal response closes both connections and removes both candidates.
- [ ] Business exception, second-candidate failure and route validation error close/clean all already-created resources.
- [ ] Two concurrent requests have different candidate paths and connection identities.
- [ ] A connection may be closed during dependency teardown on a different worker thread.
- [ ] `/api/training/tasks` still receives legacy services and its write transaction succeeds on a temporary database.
- [ ] Target OpenAPI paths/operations and response bodies remain unchanged.

### Step 2: Implement the request context

- [ ] Add a dataclass/context object holding both candidates, both owned request connections, borrowed DBManager and the two read services.
- [ ] Use `ExitStack` so partial setup and exception teardown are deterministic.
- [ ] Map snapshot errors to the existing path-free 503 codes/messages.
- [ ] Add diagnosis/practice extractor dependencies that depend on one cached context.
- [ ] Update only diagnosis/preview and Graph routes to the new read dependencies.
- [ ] Leave Training task confirmation and all writes on legacy dependencies.

### Step 3: Run GREEN and API regressions

```powershell
runtime\python\python.exe -m pytest tests\test_request_read_connections.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_api_app.py -q
```

Expected: lifecycle/concurrency/OpenAPI tests and existing route contracts pass.

### Step 4: Commit Task 3

```powershell
git add backend/api/read_connections.py backend/api/dependencies.py backend/api/routers/training.py backend/api/routers/graph.py tests/test_request_read_connections.py tests/test_api_training_routes.py tests/test_api_graph_routes.py
git commit -m "feat: share readonly connections per API request"
```

## Task 4: Prove concurrent read/write and readonly rejection boundaries

**Files:**

- Extend `tests/test_request_read_connections.py`
- Modify API/service tests only if required

### Step 1: Write RED concurrency and rejection tests

- [ ] Hold one read request open, update the source temporary database through the legacy writer, and prove the current request retains its captured view.
- [ ] Start a later request and prove it sees the committed source change.
- [ ] Execute INSERT/UPDATE/DDL through each request connection and require SQLite readonly failure.
- [ ] Prove the source main/WAL/SHM and candidate bytes/mtime do not change after rejected writes.
- [ ] Run multiple request threads and require no cross-request cursor/result contamination.
- [ ] Force teardown close failure; preserve the original route error and sanitize the public response.

### Step 2: Implement only lifecycle fixes exposed by RED

- [ ] Add no lock/pool/cache unless a RED demonstrates request-internal concurrency; prefer per-request isolation.
- [ ] Ensure teardown cleanup uses best effort without hiding the primary exception.
- [ ] Keep source-write and read-snapshot ownership separate.

### Step 3: Run GREEN and affected regression

```powershell
runtime\python\python.exe -m pytest tests\test_request_read_connections.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_api_question_bank_routes.py -q
```

### Step 4: Commit Task 4

```powershell
git add tests/test_request_read_connections.py backend/api/read_connections.py
git commit -m "test: verify request readonly concurrency boundaries"
```

If no production fix is needed, commit only the new tests with that message.

## Task 5: Generate a reproducible before/after performance report

**Files:**

- Create `tools/performance/request_connection_report.py`
- Create `tools/benchmark_request_connections.py`
- Create `tests/test_request_connection_benchmark.py`
- Create `docs/performance/p1-27-request-connection-comparison.json`
- Create `docs/performance/p1-27-request-connection-comparison.md`
- Modify this plan

### User-approved workload amendment (2026-07-14)

The original formal process exceeded one hour and was stopped without publishing reports. The user approved this exact replacement workload:

- Keep the original tier names `small`, `medium`, and `large_5pct`, the fixed seed, and all six allowlisted scenarios.
- Generate every tier at `data_scale_factor=0.1`; multiply every original logical row count by `0.1`, truncate to an integer, and clamp every positive result to at least `1`.
- Use exactly `warmups=3`, `samples=2`, and `repetitions=2` for the formal comparison.
- Run benchmark-only `legacy_per_call` and production `request_scoped` dependency/service behavior against the same generated 10% dataset for each tier. The legacy wiring must not change production routing or expose a runtime switch.
- Treat the committed P1-26 report only as allowlisted provenance and seed/scenario context. Its 100% measurements must not be used as the numeric before side or as performance evidence for the amended 10% run.
- Require exact legacy/request-scoped status and response-record equality in both repetitions, deterministic completion of both modes, target total-statement reduction, and at least 20% p50 improvement for amended medium plan preview and all amended medium Graph scenarios. The Question Bank default control is measured without an improvement requirement.
- Preserve the existing JSON/Markdown privacy, per-file atomic publication/catchable recovery, mixed-pair limitation, and root plus feature real-database fingerprint guards.
- Explicitly report that two samples and 10% generated data reduce statistical confidence and capacity coverage. The amended report is a quick performance diagnostic that provides focused comparative latency evidence only; functional correctness and non-empty response behavior are established by the API/service suites, not by this artifact, and it is not full-capacity or service-level evidence.

### Step 1: Write RED report-contract tests

- [x] Load the committed P1-26 report by allowlisted fields only as provenance/context, never as the numeric before side.
- [x] Run the same seed/scales for six allowlisted scenarios: five targets plus Question Bank default control.
- [x] Require two repetitions, status 200, deterministic status/query/record summaries and no raw samples.
- [x] Compare same-dataset `legacy_per_call`/`request_scoped` p50, total statements, SELECTs and response records.
- [x] Require exact response record/status equality.
- [x] Preserve before/after response-record minimum, median and maximum values in future reports; require both minima to be positive for every comparison, so an identical `(minimum=0, median=1, maximum=1)` pair blocks publication through the explicit non-zero gate.
- [x] Require target total-statement reduction and at least 20% p50 improvement for medium plan preview and all medium Graph scenarios.
- [x] Control scenario is measured but has no improvement requirement.
- [x] JSON/Markdown allowlists reject paths, SQL, request IDs, bodies and generated content.
- [x] Publication uses the P1-26 per-file atomic/catchable-recovery helper and documents the same sudden-termination mixed-pair limit.

### Step 2: Implement the focused comparison runner

- [x] Reuse P1-26 dataset/scenario/runner primitives; add an allowlisted scenario filter without changing default P1-26 behavior.
- [x] Default seed and tier names match P1-26; generate at exactly 10% and use 3 warmups, 2 samples and 2 repetitions under the approved amendment above.
- [x] Add legacy behavior only in benchmark dependency wiring and reuse the exact same generated dataset object for the optimized run.
- [x] Record the code SHA, environment, scenario set and sample counts.
- [x] Render only aggregates and explicit pass/fail gates.

### Step 3: Run micro smoke and focused tests

```powershell
runtime\python\python.exe -m pytest tests\test_request_connection_benchmark.py tests\test_performance_benchmark.py tests\test_performance_report.py -q
runtime\python\python.exe tools\benchmark_request_connections.py --scales small --warmups 1 --samples 2 --repetitions 2 --output-json "$env:TEMP\p1-27-smoke.json" --output-markdown "$env:TEMP\p1-27-smoke.md"
```

Expected: report structure/safety passes and micro run shows target statement reduction.

### Step 4: Commit tooling before the formal run

```powershell
git add tools/benchmark_request_connections.py tools/performance/request_connection_report.py tools/performance/runner.py tests/test_request_connection_benchmark.py tests/test_performance_benchmark.py tests/test_performance_report.py
git commit -m "perf: reduce P1-27 comparison workload"
```

### Step 5: Run the formal comparison

- [x] Capture root and feature grading/question-bank database size, UTC mtime and SHA-256 read-only before the run.
- [x] Preserve the completed exact-workload diagnostic as formal evidence without rerunning scenarios; validate its code SHA/configuration/coverage/metrics/gates and canonical JSON/Markdown pair before publication.
- [x] Require all gates pass before reports publish.
- [x] Validate safety, exact scenario/sample counts and two-round repeatability. The retained artifact validates positive response-record medians only because its source did not preserve minima/maxima; it therefore makes no per-sample non-zero claim.
- [x] Compare all root and feature fingerprints again; any difference blocks publication, commit and integration.

### Step 6: Commit the versioned comparison as `waiting_review`

- [x] Update the plan with exact before/after outcomes, runtime, limitations and current functional SHA.
- [x] Set handoff to `waiting_review`, implementation `branch_head`, automated `passed`, independent review `pending`, user acceptance `not_required`, real-data `unchanged`, nightly `report_only`.
- [x] Run focused suite, affected regressions, `smoke_check.py --skip-tests`, `git diff --check`, empty feature `user_data`, and handoff validator.
- [x] Commit reports and plan with no source changes:

```powershell
git add docs/performance/p1-27-request-connection-comparison.json docs/performance/p1-27-request-connection-comparison.md docs/superpowers/plans/2026-07-14-p1-27-request-scoped-read-connections-implementation.md
git commit -m "docs: record P1-27 connection comparison"
```

### Task 5 formal evidence

- Tooling SHA: `65fde607a2e4fef3874a970b03e4fcb2bcd43d69` (`perf: reduce P1-27 comparison workload`).
- Formal evidence source: the preserved successful exact-workload diagnostic generated at `2026-07-14T04:21:59.897665Z`; source JSON SHA-256 `2c24dd68bfebe0064f5f65a163dae99b28d876fff66a106a977b685f1796257e`, source Markdown SHA-256 `63e98824c8bd96ea1d80eb162aca76bc33724f3cb1af03a977209262b22783ec`.
- Workload: original tier names `small`, `medium`, `large_5pct`; seed `126`; `data_scale_factor=0.1`; six allowlisted scenarios; `warmups=3`, `samples=2`, `repetitions=2`; same generated dataset per tier for `legacy_per_call` and `request_scoped`.
- Coverage: 36 scale/repetition/scenario comparisons; status equality `36/36`; response-record equality `36/36`; all retained legacy and request-scoped response-record medians are positive `36/36`; target statement reduction `30/30`; medium p50 improvement `8/8`; legacy and request-scoped repeatability `3/3` each. The source artifact retained medians but not minima/maxima, so this evidence does not claim that every underlying sample was positive.
- Medium latency evidence: plan preview improved `1323239.7073→959.4182 ms` and `2779.3096→878.7385 ms`; Graph profiles `203.2853→116.0825 ms` and `122.5878→89.3832 ms`; Graph rows `263.9323→110.7921 ms` and `137.5047→97.0612 ms`; Graph evidence `269.9335→103.4438 ms` and `133.4016→101.7445 ms`.
- Statement evidence: medium plan preview `41663→164`; every medium Graph comparison `1043→24`. The Question Bank default control was measured without an improvement requirement.
- Runtime evidence: the first fail-closed formal attempt ended after `72.977s` without publication because its medium p50 group failed, but that CLI did not preserve per-comparison failure values. The successful preserved diagnostic's controlling process ran for approximately 70 minutes and exposed a first-repetition legacy plan-preview p50 of about 22.05 minutes.
- Limitations: the `0.1/3/2/2` artifact is a quick performance diagnostic; only two measured samples per repetition make p50 sensitive to timing variance; the 10% dataset reduces statistical confidence and capacity coverage; the observed 22-minute legacy timing outlier demonstrates that these results are focused comparison evidence, not full-capacity or service-level evidence. Retained evidence proves all 36 before/after medians are positive but cannot prove every underlying sample/minimum was positive. Functional correctness and non-empty response behavior are covered by the API and service test suites, not by this latency artifact.
- Publication: the preserved canonical pair was reconstructed and atomically republished without rerunning scenarios. Existing latency, statement, SELECT, status and response-record median metrics remain intact; unavailable response-record minima/maxima are recorded as unknown. The six supportable performance/equality/repeatability gates retain their accurate counts, while the seventh per-sample non-zero gate is explicitly `not_evaluated` with an unavailable passed count; the overall result is labeled `quick_diagnostic` and never represented as seven gates passed. Future reports preserve before/after minimum/median/maximum and fail the non-zero gate when either minimum is missing or not positive. Final JSON SHA-256 `bca82443913577ecabab404312716fb9bdb4b22c2805d47f1b781a1e121079e1`; Markdown SHA-256 `8f93da6c118ae6e9336ca64f6a45d9f986b51678d3bb4b1551b687cd15aa5861`.
- Safety: root and feature grading/question-bank size, UTC mtime and SHA-256 were identical before and after the attempt, diagnostic preservation and atomic publication; feature `user_data/` stayed clean and no real database was opened through SQLite.

## Task 6: Architecture, independent review and integration handoff

**Files:**

- Modify `ARCHITECTURE.md`
- Modify this plan

### Step 1: Record the implemented boundary

- [x] Add one P1-27 architecture fact: target routes, request candidate/connection ownership, readonly mode, legacy/write compatibility, no cross-request pool/cache, performance report location and real-data safety.
- [x] Do not copy machine paths or raw fingerprints.

### Step 2: Run final feature verification

```powershell
runtime\python\python.exe -m pytest tests\test_request_read_connections.py tests\test_diagnosis_profile_service.py tests\test_question_tag_projection_service.py tests\test_practice_plan_service.py tests\test_question_frequency_service.py tests\test_source_question_link_service.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_request_connection_benchmark.py -q
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_question_bank_routes.py tests\test_api_training_routes.py tests\test_api_graph_routes.py tests\test_api_job_lifecycle.py tests\test_migration_tooling.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: zero failures; root real-database fingerprints unchanged.

### Step 3: Request task and whole-package independent review

- [x] Use review packages bound to exact base/head SHAs.
- [x] Review connection ownership, readonly enforcement, partial setup/teardown, concurrency isolation, source-sidecar safety, write-path exclusion, Streamlit compatibility, performance math and report privacy.
- [x] Reproduce every Critical/Important with RED, make one minimal fix pass, rerun affected tests and request re-review.
- [x] Require Critical=0 and Important=0. Minor findings are recorded.

### Step 4: Create the final plan-only handoff commit

- [x] Modify only this plan.
- [x] Set status `verified_pending_integration`.
- [x] Record the direct parent full reviewed functional SHA.
- [x] Set automated/independent review `passed`, user acceptance `not_required`, real-data `unchanged`, nightly action `independent_candidate_allowed` as required by the generic handoff protocol. `NIGHTLY_ELIGIBILITY_MATRIX.md` remains authoritative for execution eligibility and continues to classify P1-27 as `daytime_only`, so this protocol field does not make the package night-eligible.
- [x] Run handoff validator on the clean committed worktree.

```powershell
git add docs/superpowers/plans/2026-07-14-p1-27-request-scoped-read-connections-implementation.md
git commit -m "docs: verify P1-27 integration handoff"
```

### Task 6 final evidence

- Reviewed functional SHA: `576d84988a5d5a41b5e4e3061adf28e121d4c6a8`, which is the direct parent of the final plan-only handoff commit. Independent whole-branch review covered `0564d60697d1e6e643491679099ed3e796be32ca..576d84988a5d5a41b5e4e3061adf28e121d4c6a8` and concluded `0 Critical / 0 Important / 0 Minor`, merge-ready for independent-review purposes subject to normal integration gates.
- Automated feature verification: the Task 6 focused suite passed `121` tests; the affected API/database regression suite passed `111` tests; `tools/smoke_check.py --skip-tests` passed document governance, static compilation of `431` first-party Python files, and idempotency/integrity checks on temporary copies of both databases. The review-fix focused pure/temp suite on the reviewed SHA passed `5` tests with `16` deselected.
- Performance evidence boundary: the versioned artifact remains a `quick_diagnostic` based on the preserved `0.1/3/2/2` workload. Its six supportable gates retain their recorded passing counts, while `nonzero_response_records` is explicitly `not_evaluated` because historical sample minima/maxima were not retained. Functional correctness and non-empty response behavior are established by the API/service suites, not by this latency artifact; no long benchmark rerun was performed for the review fix or final handoff.
- Safety: root and feature grading/question-bank size, UTC mtime and SHA-256 stayed unchanged across feature verification and focused review; the feature `user_data/` status stayed empty, and no real business database was opened through SQLite.
- Handoff protocol: the final commit changes only this plan and records its direct parent reviewed SHA. `independent_candidate_allowed` satisfies the generic final-state field combination; P1-27 remains `daytime_only` in the eligibility matrix and therefore cannot be selected for night implementation.

## Integration and release gate

- Merge the complete verified feature chain into a fresh integration branch created from latest `origin/main`.
- Resolve only shared `ARCHITECTURE.md`/Index conflicts in integration.
- Rerun the P1-27 focused suite, affected API/database regressions and the focused performance smoke.
- Run one complete `tools/smoke_check.py` for the combined wave because shared API/DB connection infrastructure changed.
- Compare normalized real-database fingerprints before/after.
- Update `EXECUTION_INDEX.md` only in integration so P1-27 becomes `merged` after the PR lands; P1-29 becomes `ready` only if all of P1-26/P1-27/P1-28 are in latest `origin/main`.
- Push integration, create a labeled PR (`codex`, `codex-automation` when available), merge through GitHub, then synchronize `origin/main`, local `main` and active worktrees.
- No direct push to `main`, force push, real-data write, worktree recursive deletion or loss of unmerged history.

## Recovery

- Before integration, revert Task 5→Task 1 commits in reverse order; no data migration is required.
- If performance gates fail, keep the P1-26 baseline and local diagnostic evidence but do not merge P1-27 source changes or mark the package complete.
- If readonly or concurrency tests fail, restore old route dependencies first; legacy Streamlit/write services remain available throughout.
- If any root database fingerprint changes, stop and investigate without push/PR/merge.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-27
**交接状态：** verified_pending_integration
**功能提交：** 576d84988a5d5a41b5e4e3061adf28e121d4c6a8
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
