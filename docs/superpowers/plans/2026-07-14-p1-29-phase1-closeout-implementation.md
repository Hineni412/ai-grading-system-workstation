# P1-29 Phase 1 Full-Chain Closeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P1-29
**规划状态：** in_progress
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** f5d5a06e8d804d285d34372a1c17cc233697727d
**用户自测：** formal
**自测清单：** docs/user-testing/checkpoints/P1-29-v1.5.0-phase1-formal.md

**Goal:** 在不改业务规则、API、Schema、性能策略或真实数据的前提下，组合验证 P1-26、P1-27、P1-28，完成 Phase 1 自动门槛、实际双入口匿名黑盒验收、独立复审和标准 integration/PR/main 收口。

**Architecture:** 把已合并的三个前置包当作一个候选基线验证：P1-26 的优化前正式报告只做结构、完整性和安全复核并运行小型当前代码冒烟，不重生成或覆盖；P1-27 复核已提交快速诊断的有效门槛及限制，并运行小型当前代码对比；P1-28 重跑真实 FastAPI/服务/Job/数据库编排的临时双库 E2E。正式用户验收由专用测试工具从已复审 Git SHA 导出临时代码副本、生成匿名双库并启动真实 FastAPI 与 Streamlit，避免 `config/app_config.yaml` 的生产 `user_data` 路径优先级误触真实数据。

**Tech Stack:** Python 3.12、pytest、FastAPI/TestClient/Uvicorn、Streamlit、SQLite 临时双库、既有 P1-26/P1-27 benchmark 工具、P1-28 API E2E harness、Git archive、Windows loopback 进程管理。

## Global Constraints

- 只验证和收口；不优化性能、不改变评分/题号/标签/状态语义、不扩展 API、不迁移 Schema、不调用真实模型。
- 不读取真实业务正文；根工作区两库只允许读取文件大小、UTC mtime 和 SHA-256，不得用 SQLite 打开。
- 不修改、删除、暂存、提交或 stash 任何 `user_data/`；测试写入只发生在 pytest 临时目录或系统临时验收副本。
- P1-26 正式报告是优化前基线，禁止在含 P1-27 的当前代码上覆盖生成；当前健康只用临时输出的 small micro run 验证。
- P1-27 报告保持 `quick_diagnostic`：六项可支持门槛通过，`nonzero_response_records` 为 `not_evaluated`；不得把两样本 10% 数据诊断写成容量结论或 SLA。
- P1-28 必须继续使用临时双库、合成图片和假外部模型，同时保持真实 FastAPI/服务/Job/数据库边界在链路内。
- 双入口黑盒必须从已复审 SHA 导出到系统临时目录；验收副本配置只指向副本内匿名数据，监听仅限 `127.0.0.1`，不得启动根工作区 `运行.bat`。
- 正式用户验收只有用户明确结论可以设为 `passed`；Blocker/Major 立即停止并退回本分支定位根因、最小修复、复审和重跑。
- 若自动测试失败，先按 `superpowers:systematic-debugging` 定位根因；生产或工具修复先按 `superpowers:test-driven-development` 建立 RED，再做最小 GREEN。
- 功能分支完成聚焦回归、完整 smoke、真实两库指纹守卫和独立复审后进入 `waiting_user`；用户通过后严格按 checklist-only evidence → plan-only final handoff 提交顺序。
- integration 从届时最新 `origin/main` 创建，只接收本包完整提交链；更新共享 `ARCHITECTURE.md`/Index 后重跑受影响验证与一次完整 smoke，再通过 PR 合并 GitHub `main`，禁止直接 push `main`。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-29
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## Claim and baseline evidence

- 最新本地 `main` 与 `origin/main` 均为计划基线；P1-26、P1-27、P1-28 在 Index 中为 `merged`，P1-29 为 `ready`。
- 根工作区已有与本包无关的 Phase 3 文档改动和真实 `user_data` 状态；本包在隔离 worktree 实施，不暂存、不 stash、不覆盖这些现场。
- 领取时两库只读三元组：阅卷库 `2863104 / 2026-07-10T07:10:41.1221109Z / 93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`；题库 `3461120 / 2026-07-08T11:58:06.3320883Z / E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`。
- 起点组合选择覆盖 P1-26 核心、P1-27 连接、P1-28 E2E、OpenAPI、双入口脚本和 Schema/迁移，结果为 `145 passed, 0 failed, 0 skipped, 1 dependency warning`；前后真实两库三元组完全一致。
- 当前 OpenAPI 为 56 paths / 66 operations / 0 duplicate operation IDs；第一方生产 Python `timeout=None` 搜索为零。

## Automatic closeout evidence

- P1-26 聚焦回归 `76 passed`，版本化报告保持三个命名工作负载、每档 16 场景/2 次重复并通过重复性门槛；当前 small micro 只写系统临时输出并通过，未覆盖正式基线。
- P1-27 聚焦回归 `123 passed`，版本化 quick diagnostic 的 36 个比较、六项可支持门槛保持通过，`nonzero_response_records` 仍为 `not_evaluated`；当前 small micro 的 12 个比较只作健康检查。
- P1-28 与受影响 API/Job 回归 `70 passed`；其余 Phase 1 契约、启动、迁移、Schema 和 smoke 工具门槛 `299 passed`。
- P1-29 验收入口最终聚焦测试 `35 passed`；精确 SHA 临时副本的 API/Streamlit 双入口实际就绪，关闭后监听端口为零，页面三类 API Key 为空、异常数为零，只显示两名合成学生与 90/70 结果；第二个服务启动失败会清理第一个进程并脱敏已有日志。
- 最终功能分支完整 smoke：`1616 passed, 2 skipped, 1 dependency warning`；文档治理、433 个第一方 Python 文件静态编译、全量测试及两库副本初始化幂等/完整性全部通过。
- 四轮实现复审依次发现并修复：便携 Python 从根仓库加载模块、dotted module 来源检查先执行父包、机器级 API profile/敏感环境变量泄露；每项都先建立 RED，再最小修复、重跑并复审。最终 Task 2 findings 为 none、verdict 为 PASS。
- 整包复审发现并修正三项 Important：正式清单第 6 步页面区域、验收日志绝对路径、ARCHITECTURE 动态复审状态；日志修复使用 RED→GREEN 并补第二服务启动失败清理回归，修复复审 verdict 为 PASS。
- 内置浏览器控制组件发生初始化兼容冲突；产品双入口和 Streamlit 健康正常。可见控件预检改用 Streamlit 官方页面测试运行器，并保留实际浏览器正式用户验收作为最终黑盒门槛。
- 开工至最终 smoke 后，根工作区两库三元组、功能 worktree `user_data` 空状态和两个 stash SHA 均保持不变。

---

### Task 1: Claim P1-29 with the only valid first commit

**Files:**
- Create: `docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md`

**Interfaces:**
- Consumes: latest `origin/main`, P1-29 package boundary, immutable stash baseline and read-only root database fingerprints.
- Produces: validator-compatible first first-parent claim commit with `in_progress` handoff state.

- [x] **Step 1: Verify package identity and clean isolated channel**

Run `git status --short`, `git status --short -- user_data`, compare `HEAD` with `origin/main`, list stash SHAs, and require exactly one top package declaration plus one matching declaration inside one handoff block.

- [x] **Step 2: Record the current composite baseline**

Run the 145-test selection named in the claim evidence, query current OpenAPI counts, search first-party production Python for `timeout=None`, and compare root database fingerprints. Expected: 145 pass, 56/66/0 OpenAPI summary, zero timeout matches, no worktree `user_data` status and unchanged root fingerprints.

- [x] **Step 3: Commit only the claim plan and validate it**

```powershell
git add docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md
git diff --cached --name-only
git diff --check
git commit -m "docs: claim P1-29 phase closeout"
..\..\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-14-p1-29-phase1-closeout-implementation.md --repo .
```

Expected: staged/committed path is only this plan; validator returns `ok=true`, `state="in_progress"`, no issues.

---

### Task 2: Add a fail-closed anonymous dual-entry acceptance launcher with RED → GREEN

**Files:**
- Create: `tools/p1_29_acceptance.py`
- Create: `tests/test_p1_29_acceptance.py`

**Interfaces:**
- Consumes: an exact local Git SHA, the repository runtime, P1-28 `tests.api_e2e.harness`, an empty target below the system temporary directory, and unused loopback ports.
- Produces: `prepare`, `smoke`, and `serve` CLI commands; a staged source tree with `DATA_DIR: synthetic_data`, seeded anonymous databases/assets/report, readiness JSON, and deterministic child-process shutdown.

- [x] **Step 1: Write RED tests for target, archive and data safety**

Cover these exact failures before implementation: target outside `tempfile.gettempdir()`; target equal to or containing a `user_data` segment; existing non-empty target; invalid/full SHA not resolvable by `git rev-parse`; archive member absolute path, `..`, symlink or hardlink; staged config missing exactly one `DATA_DIR`/`LOGS_DIR`; any source checkout `user_data` entry; requested port outside 1024–65535 or duplicate ports.

Run:

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_p1_29_acceptance.py -q
```

Expected: RED because `tools.p1_29_acceptance` does not exist.

- [x] **Step 2: Implement the minimal safe extraction and exact-SHA preparation**

`prepare --source-ref <40-sha> --workspace <empty-temp-dir>` must resolve the SHA, stream `git archive --format=tar`, reject unsafe members before extraction, require no `user_data` member, rewrite only the staged `config/app_config.yaml` to `DATA_DIR: synthetic_data` and `LOGS_DIR: acceptance_logs`, then invoke an internal seed command from the staged tree using `sys.executable`.

The internal seed command must reuse `build_paths`, `E2EControls`, `build_job_manager`, `install_dependency_overrides` and `ApiE2EHarness`; initialize both temporary databases, add only `SYN-001/SYN-002`, execute config/template/region, scan, partial grading, failed-only recovery, Q1 teacher confirmation and real XLSX export/download, assert final synthetic scores `90/70`, and write an allowlisted metadata JSON containing only package, source SHA, session ID, synthetic record counts and logical filenames.

- [x] **Step 3: Implement loopback-only smoke/serve and deterministic cleanup**

`smoke` and `serve` must start `uvicorn backend.api.app:app` and `streamlit run web_app.py` from the staged source via `sys.executable`, force both addresses to `127.0.0.1`, poll `/healthz` and `/_stcore/health`, fail if either process exits, and always terminate both process trees. `smoke` exits after readiness; `serve` waits until interrupted. Logs stay in the temporary workspace and errors exposed to the caller contain no API key or real path content beyond the caller-owned temporary workspace.

- [x] **Step 4: Prove GREEN including a real dual-entry smoke**

Run the new test file, then prepare a unique temp workspace from current `HEAD` and run `smoke` on two free high ports. Assert both health endpoints, the staged source SHA, synthetic session/result/report contents, absence of staged `user_data`, empty feature `user_data` status and unchanged root database fingerprints.

- [x] **Step 5: Commit only launcher and tests**

```powershell
git add tools/p1_29_acceptance.py tests/test_p1_29_acceptance.py
git diff --cached --name-only
git commit -m "test: add P1-29 anonymous acceptance launcher"
```

---

### Task 3: Run the P1-26/P1-27/P1-28 composite gate

**Files:**
- Create: `docs/performance/p1-29-phase1-closeout.md`
- Modify: `docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md`

**Interfaces:**
- Consumes: committed P1-26/P1-27 reports, current benchmark tools, P1-28 E2E suite and Phase 1 package definition.
- Produces: a versioned stage report that separates deterministic/current evidence from machine-specific historical evidence and lists unresolved Phase 1 risks without changing policy.

- [x] **Step 1: Validate P1-26 report structure and run current micro smoke**

Run the five P1-26 focused test files. Parse the committed JSON and require package `P1-26`, seed 126, `repeatability=passed`, exactly `small/medium/large_5pct`, two repetitions per scale, 16 scenarios per repetition, status 200 and 20 samples per summary. Run `benchmark_api_db.py` only with `--scales small --warmups 1 --samples 2 --repetitions 2` and outputs under `$env:TEMP`; do not touch versioned reports.

- [x] **Step 2: Validate P1-27 report truthfulness and run current micro comparison**

Run the P1-27 focused connection/service/API/performance suite. Require the committed report to remain `quick_diagnostic`, 36 comparisons, six named scenarios, status/record equality 36/36, statement reduction 30/30, medium p50 8/8, both repeatability gates 3/3, and `nonzero_response_records=not_evaluated`. Run `benchmark_request_connections.py --scales small --data-scale-factor 0.01 --warmups 1 --samples 2 --repetitions 2` to temp outputs only; report its actual gates without weakening publication rules.

- [x] **Step 3: Re-run P1-28 real-boundary E2E and affected API/Job regressions**

Run `tests/api_e2e` plus config/template/scan/grading/review/report/file/job route tests. Expected: success, scan failure recovery, partial grading recovery, review score persistence, real report download and restart recovery all pass with no skipped tests.

- [x] **Step 4: Run Phase 1 contract, startup, timeout and database gates**

Run OpenAPI contract, `运行.bat` dual-entry contract, LLM Gateway/caller migration tests, migration tooling, migration rehearsal, schema baseline and smoke-tool tests. Re-query OpenAPI and production `timeout=None`. Require 56 paths, 66 operations, zero duplicate IDs, zero production timeout matches, database rehearsal/integrity green, and no P1 package-level Critical/Important review debt in the committed handoff evidence.

- [x] **Step 5: Write the stage report without overstating performance**

Record exact commands/totals and: P1-26 remains the pre-optimization machine-specific baseline; P1-27 shows request-level statement/latency improvement only under the recorded quick diagnostic and not an SLA; P1-28 covers generated data and fake external models, not production model health. List still-open Architecture P0/P1 risks as known debt, distinguish them from unresolved defects introduced by Phase 1, and do not declare the stage passed before full smoke, review and user acceptance.

---

### Task 4: Complete automatic closeout, architecture evidence and pre-review formal checklist

**Files:**
- Modify: `ARCHITECTURE.md`
- Create: `docs/user-testing/checkpoints/P1-29-v1.5.0-phase1-formal.md`
- Modify: `docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md`

**Interfaces:**
- Consumes: Tasks 2–3, full repository smoke, actual loopback dual-entry browser preflight and the user-testing template.
- Produces: current Phase 1 closeout fact, exact pending formal checklist and `waiting_review` functional commit.

- [x] **Step 1: Run all affected regressions and one complete feature smoke**

Run the union of P1-26/P1-27/P1-28 affected selections, `git diff --check`, empty feature `user_data`, then:

```powershell
..\..\runtime\python\python.exe tools\smoke_check.py
```

Expected: documentation governance, static compilation, full pytest, copied-database idempotency and integrity all pass. Capture exact totals and warning/skips; any failure enters root-cause workflow and invalidates prior completion claims.

- [x] **Step 2: Run an actual candidate dual-entry preflight and inspect it in a browser**

Prepare a fresh temporary workspace from current candidate SHA, run `serve` on unused loopback ports, verify API health and Streamlit health, then use the in-app browser to inspect the actual Streamlit pages and synthetic session. Confirm no real names/data appear, both channels remain responsive together, expected reviewed score/report state is visible where the current UI exposes it, refresh is safe, and shutdown removes both listeners. Record only logical evidence, never temporary absolute paths.

- [x] **Step 3: Generate the pending 20–30 minute formal checklist from observed behavior**

The checklist must name the exact anonymous data source, reviewed-SHA staging method, loopback URLs, visible markers, 20–30 minute numbered actions, reset/restart owned by Codex, expected results, stop conditions, shutdown and machine result block. It must not ask the user to run commands, inspect logs, input a key, invoke a model or use real `user_data`.

- [x] **Step 4: Update architecture evidence and recheck safety**

Add one P1-29 fact: composite P1-26/27/28 gate, preserved performance interpretation, exact-SHA anonymous dual-entry acceptance boundary, no Schema/API/business-rule/real-data change, and remaining known risks. Recompute both root database three-tuples and require exact equality with claim; scan the complete branch history/diff/stashes for `user_data`, secrets, temp paths and generated artifacts.

- [x] **Step 5: Commit the complete functional candidate as `waiting_review`**

Set handoff to `waiting_review`, `branch_head`, automated `passed`, independent review `pending`, user acceptance `pending`, real-data `unchanged`, nightly `report_only`. Commit only intended source/test/report/checklist/architecture/plan files, then validate the clean worktree.

---

### Task 5: Complete independent review and anchor the reviewed SHA

**Files:**
- Modify after review only: `docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md`

**Interfaces:**
- Consumes: exact package base/head range, package/map/plan, all code, reports and test evidence.
- Produces: Critical=0, Important=0 review and a plan-only `waiting_user` anchor whose parent is the reviewed functional SHA.

- [x] **Step 1: Dispatch an independent reviewer over the exact Git range**

Use `superpowers:requesting-code-review`. Review must inspect package alignment, report arithmetic/claims, no P1-26 overwrite, P1-27 limitations, P1-28 real/fake boundaries, archive extraction/path traversal, temp-root and no-`user_data` guards, process teardown, loopback binding, log/privacy behavior, formal checklist accuracy and handoff history.

- [x] **Step 2: Resolve all Critical/Important findings**

For code/tool failures reproduce RED, apply one minimal root-cause fix, rerun affected tests and request re-review. For evidence errors correct the source report/checklist/architecture claim and rerun its validating commands. Critical/Important must be zero before continuing.

- [x] **Step 3: Re-run verification required by review changes**

Rerun the changed slice plus affected regression, dual-entry smoke, quick/full smoke according to actual code impact, `git diff --check`, handoff validator and root fingerprint comparison. If production/shared code changed, rerun full smoke; pure plan-only anchor does not.

- [ ] **Step 4: Create the plan-only reviewed `waiting_user` anchor**

Set `waiting_user`; record the direct parent full reviewed SHA; automated/independent review `passed`; user acceptance `pending`; fingerprint `unchanged`; nightly `report_only`. Commit only this plan and validate.

---

### Task 6: Run formal user acceptance and create validator-compatible evidence

**Files:**
- Modify: `docs/user-testing/checkpoints/P1-29-v1.5.0-phase1-formal.md`
- Modify afterward only: `docs/superpowers/plans/2026-07-14-p1-29-phase1-closeout-implementation.md`

**Interfaces:**
- Consumes: reviewed waiting-user anchor, exact reviewed source SHA and pending formal checklist.
- Produces: explicit user conclusion, checklist-only evidence commit and plan-only `verified_pending_integration` commit.

- [ ] **Step 1: Start exactly the reviewed anonymous runtime**

Prepare a fresh temporary workspace from the anchored reviewed SHA, run the launcher in `serve`, open the declared Streamlit URL in the app and confirm all checklist markers before handing control to the user. Record root fingerprints immediately before acceptance.

- [ ] **Step 2: Guide the user through the formal checklist**

Keep service reset/restart under Codex control. Blocker/Major stops acceptance and returns to Task 5 after root-cause fix/review; Minor is recorded for user decision. Only an explicit user `passed` conclusion may continue.

- [ ] **Step 3: Stop services, verify fingerprints and commit only checklist evidence**

Stop both child processes, prove both ports closed, recompute unchanged root fingerprints, update only the checklist result/machine block with the reviewed functional SHA and `passed`, and commit exactly that one matching checkpoint.

- [ ] **Step 4: Create the final plan-only handoff commit**

Set `verified_pending_integration`; `功能提交` equals the direct parent checklist evidence SHA; automated/review/user acceptance `passed`; fingerprint `unchanged`; nightly `independent_candidate_allowed`. Commit only this plan and require `tools/handoff_status.py` `ok=true`.

---

### Task 7: Integrate, publish through PR and synchronize the common baseline

**Files:**
- Modify in integration: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify in integration only if evidence needs conflict reconciliation: `ARCHITECTURE.md`

**Interfaces:**
- Consumes: complete validator-approved P1-29 feature chain and latest `origin/main`.
- Produces: tested integration commit, GitHub PR merged to `main`, synchronized local baseline and Phase 1 marked complete.

- [ ] **Step 1: Create a fresh integration branch/worktree from latest `origin/main`**

Require no unknown changes or data; merge the complete P1-29 chain once. Resolve shared docs by keeping all newer main facts plus P1-29 evidence; do not fix business code in integration.

- [ ] **Step 2: Run integration gates**

Run P1-29 affected selections, real dual-entry smoke from the final candidate, `git diff --check`, handoff validator and one complete `tools/smoke_check.py`. Compare root database fingerprints before/after. Any failure returns to the feature branch.

- [ ] **Step 3: Update dynamic status and final architecture evidence**

Set Phase 1 to `merged`/21 completed/0 pending, set P1-29 `merged`, record concise evidence and choose the next dependency-valid action without bypassing the independent Phase 2 frontend source-recalibration gate. Do not copy transient test totals into long-lived architecture sections unless they materially explain the stage gate.

- [ ] **Step 4: Push integration, create and merge a GitHub PR**

Push only the integration branch, create a ready PR targeting GitHub `main`, verify the intended diff/checks, merge without force and never directly push `main`.

- [ ] **Step 5: Synchronize the common baseline safely**

Fetch/prune; fast-forward local `main` if its tracked source can be advanced without touching the user's existing Phase 3 document or real data. Update active clean channels only when their branch/worktree rules permit. Do not delete worktrees/branches unless `origin/main` contains them and source/data/reparse checks pass; preservation is acceptable.

## Plan Self-Review

- Spec coverage: Tasks 2–7 cover P1-26 performance evidence, P1-27 request-scoped readonly connections, P1-28 five-flow E2E, affected regressions, full smoke, OpenAPI/timeout/database gates, dual-entry formal user test, review, Index/architecture and standard PR delivery.
- Scope: no performance optimization, business rule, API, Schema, migration, real model or real-data operation is authorized; only test tooling and evidence are added.
- Performance truthfulness: P1-26 remains the pre-optimization baseline; P1-27 remains a limited quick diagnostic with one explicitly unevaluated gate.
- User protocol: P1-29 is `formal`; pending checklist path is fixed at claim; reviewed anchor, checklist-only evidence and final plan-only commit order matches `tools/handoff_status.py`.
- Safety: real databases are hash-only; acceptance uses exact-SHA archive plus anonymous temporary data; archive/path/loopback/process guards are testable and fail closed.
- Rollback: revert the P1-29 PR to remove test launcher/evidence/status; no Schema or business-data rollback exists because none is changed. Temporary acceptance workspaces can be closed and removed after verifying they are below the system temp root and contain no external reparse point.
