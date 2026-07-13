# P2-07 Review Scoring Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-07
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** 3aab528749bd88ce1df457ff6783b29f3edcf65f
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-07-review-scoring-quick.md

**Goal:** 在 P2-05/P2-06 单题复核样板页右侧接入评分标准、AI 初评、风险、教师草稿和安全确认，并在确认失败时保留输入、成功时进入正确下一份。

**Architecture:** `/grading` 路由复用 App Shell 现有 320–360px 右侧检查器位置，其他路由继续显示考试概况，避免在 1024px 桌面形成四栏。前端通过现有 `GET /config` 读取评分标准、现有 Review GET 读取 AI 初评和证据、现有单条 confirm POST 原子写入教师最终分；评分草稿只保存在 Pinia 内存，不写 localStorage/sessionStorage。POST 不自动重放，成功后的队列刷新失败只降级为“分数已确认、列表待刷新”，不得误报保存失败。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, Vue Router 5.1.0, native Fetch client, Element Plus 2.14.3, Vitest 4.1.10, Playwright 1.61.1, existing CSS Tokens, Python 3.12 repository guards.

## Global Constraints

- 实现必须符合 `docs/superpowers/packages/phase-2-execution-packages.md` 的 P2-07、`docs/ui/STYLE.md` 第 1/3/6/7/10/11/12/14/16/19 节和已合并的 P2-05/P2-06 行为。
- 教师最终分始终高于 AI 初评；AI 当前分只能标为“AI 初评/建议”，已经教师确认的记录不得重新标成 AI 最终结论。
- 只复用 `GET /api/sessions/{id}/config`、Review GET 与 `POST /api/sessions/{id}/review/questions/{question}/confirm`；不得新增后端路由、Schema、评分规则、状态、模型调用或数据库迁移。
- confirm 每次只提交当前一条 `result_id/detail_id`；写请求只发一次，不自动重试、不批量确认、不根据前端推断所有权。
- 分数必须是有限数字且满足 `0 <= score <= max_score`；前端校验只做提前反馈，服务器仍是最终判定者。
- 教师备注映射到现有 `deduction_reason`；`error_category/error_summary` 不由前端伪造，留给服务端既有默认确认语义。
- 评分标准只展示配置中当前题或当前 part 的已有字段；字段缺失时明确显示“评分标准暂不可用”，不得臆造评分细则。
- `candidate_scores`、`evidence_steps`、`missing_steps` 只作为已有 AI 建议/证据摘要展示；不展示隐藏推理、内部路径、响应正文、URL、模型日志或技术参数。
- 草稿按 `session_id/question_id/detail_id` 保存在 Pinia 内存；切换学生、题目或考试时保留，成功确认后删除，失败后原样保留；不得写 localStorage/sessionStorage、日志或 URL。
- 存在未确认草稿时显示清楚提示，并在浏览器刷新/关闭前使用标准 `beforeunload` 守卫；应用内切换不会丢草稿，因此不使用阻塞式浏览器弹窗。
- 成功导航目标在 POST 前由完整过滤队列确定；响应返回时若用户仍停留在原记录，才跳转到该目标，避免旧响应抢走后来选择的上下文。
- POST 成功、后续 GET 刷新失败时必须保留“确认已成功”的事实，局部更新当前记录并提示可安全刷新；不得恢复草稿或再次自动 POST。
- annotation outcome 为 `retry_required` 时分数仍视为已确认，只提示标注图需要稍后刷新；不得把副作用补偿失败误报为评分失败。
- 右侧唯一主要按钮为“确认并下一份”；加载中、无当前记录、分数非法、未改动或正在提交时禁用并给出原因。
- 活动记录只提供诚实占位契约，说明当前 API 未提供历史列表；不得生成虚假时间线、操作者或审计记录。
- 只支持不低于 1024px 的 Windows 桌面浏览器；验证 1920×1080、1440×900、1366×768、1280×800、1024×768，不建设移动端布局。
- 页面样式只使用既有 Token；评分检查器使用克制的教师语义色作为唯一视觉重点，不修改全局色板、字体、依赖或锁文件。
- 测试只使用 mock API、合成学生和程序生成媒体；不得读取、修改、暂存、提交或 stash 真实 `user_data/`，不得调用真实模型。
- 执行模型为 T-H。每个新行为先有可见 RED，再做最小 GREEN；同一问题连续两次修复失败时安全停机。
- 实施过程使用 `frontend-design`、`test-driven-development` 和 `executing-plans`；完成声明前使用 `verification-before-completion`。本任务禁止自动派生子代理，除非用户另行明确授权。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-07
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 2026-07-13 领取证据

- 实现工作树创建于最新 `origin/main`：`3aab528749bd88ce1df457ff6783b29f3edcf65f`；创建后源码和 `user_data/` 状态干净。
- Stash 基线为 `85726b3b9863575c9aebe4ff12916e96d4bb08ba`、`67edf9783a70b42878c44ae05eea25528b51ddf2`。
- 根工作区真实数据库只读指纹：`grading_system.db|2863104|2026-07-10T07:10:41.1221109Z|93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`；`question_bank.db|3461120|2026-07-08T11:58:06.3320883Z|E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`。仅读取名称、大小、UTC 修改时间和 SHA-256，未打开 SQLite。
- 起点回归：Review 前端 5 个聚焦文件 45 tests passed；后端 Review API/Application 22 tests passed。

---

### Task 1: Claim P2-07 without touching source or real data

**Files:**
- Create: `docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md`

**Interfaces:**
- Consumes: package identity `P2-07`, latest `origin/main`, immutable stash and database fingerprints.
- Produces: first first-parent claim commit and validator-compatible `in_progress` handoff block.

- [x] **Step 1: Verify the clean implementation channel**

Run:

```powershell
git status --short
git status --short -- user_data
git rev-parse HEAD
git rev-parse origin/main
git stash list --format=%H
```

Expected: before staging, only this plan is untracked; `user_data` is clean; both SHAs equal `3aab528749bd88ce1df457ff6783b29f3edcf65f`; stash output is exactly the two recorded SHAs.

- [x] **Step 2: Commit only the claim plan**

Run:

```powershell
git add docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md
git diff --cached --name-only
git diff --check
git commit -m "docs: claim P2-07 review scoring"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md `
  --repo .
```

Expected: staged list contains only this plan; validator emits one JSON line with `ok=true`, `state="in_progress"`, and no issues.

---

### Task 2: Add strict rubric and confirmation adapters with RED → GREEN

**Files:**
- Modify: `frontend/src/api/review.ts`
- Modify: `frontend/src/api/__tests__/review.spec.ts`

**Interfaces:**
- Consumes: `apiClient`, Review GET response, `GET /api/sessions/{id}/config`, existing confirm request/response schema.
- Produces: `ReviewRubricSection`, `ReviewConfirmInput`, `ReviewConfirmResponse`, `fetchReviewRubric(sessionId, signal?)`, and `confirmReviewItem(sessionId, questionId, input, signal?)`.

- [ ] **Step 1: Write failing adapter tests**

Add tests proving:

```ts
expect(await fetchReviewRubric(7)).toEqual(expect.objectContaining({ questions: expect.any(Array) }))
expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/7/config')
expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'POST' })
expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
  items: [{ result_id: 10, detail_id: 20, score_awarded: 8.5, deduction_reason: '步骤二符号错误' }],
})
```

Also reject malformed rubric arrays, non-finite scores, invalid annotation outcomes, totals that disagree with outcome arrays, and unsafe question IDs. Confirm that no `error_category/error_summary` is sent when the teacher only enters a score and note.

Run:

```powershell
npm test -- src/api/__tests__/review.spec.ts
```

Expected: FAIL because the new exports do not exist.

- [ ] **Step 2: Implement minimal strict decoders**

Implement pure bounded extraction helpers that accept only records/arrays/finite numbers/strings. Flatten a matching question or part into display-safe fields such as `title`, `maxScore`, `questionType`, `knowledgeLabels`, and rubric lines from existing `core_goal`, `required_elements`, `step_score`, `analysis`, or equivalent known text fields. Unknown fields are ignored.

Implement confirm with:

```ts
return apiClient.request(path, {
  method: 'POST',
  body: { items: [input] },
  signal,
  decode: decodeReviewConfirmResponse,
})
```

Run the same test and expect PASS.

- [ ] **Step 3: Commit the adapter slice**

```powershell
git add frontend/src/api/review.ts frontend/src/api/__tests__/review.spec.ts
git diff --check
git commit -m "feat: add review scoring API adapters"
```

---

### Task 3: Build memory-only teacher drafts and score validation with RED → GREEN

**Files:**
- Create: `frontend/src/stores/review-drafts.ts`
- Create: `frontend/src/__tests__/review-drafts-store.spec.ts`

**Interfaces:**
- Consumes: `ReviewItem` identity/current score/max score.
- Produces: `useReviewDraftStore()`, `ensureDraft(item)`, `updateScore(key, text)`, `updateNote(key, text)`, `scoreIssue(draft, maxScore)`, `markConfirmed(key)`, `hasDirtyDrafts`, `dirtyCount`, and `reset()`.

- [ ] **Step 1: Write failing draft lifecycle tests**

Cover one behavior per test:

```ts
const first = store.ensureDraft(item)
store.updateScore(first.key, '9')
store.ensureDraft(otherItem)
expect(store.drafts[first.key]?.scoreText).toBe('9')
expect(store.hasDirtyDrafts).toBe(true)
```

Also cover blank/NaN/infinite/negative/above-maximum rejection, decimal acceptance, notes, per-session/question/detail isolation, server refresh not overwriting dirty drafts, clean draft refresh following authoritative score, and successful confirmation removing only the submitted draft.

Run:

```powershell
npm test -- src/__tests__/review-drafts-store.spec.ts
```

Expected: FAIL because the store does not exist.

- [ ] **Step 2: Implement the minimal Pinia store**

Use a plain reactive record keyed by `${sessionId}:${questionId}:${detailId}`. Store only score text, note, base score/note and dirty timestamp; never store media URLs or student names. `scoreIssue` returns stable Chinese field errors and `null` when valid.

Run the same test and expect PASS.

- [ ] **Step 3: Commit the draft slice**

```powershell
git add frontend/src/stores/review-drafts.ts frontend/src/__tests__/review-drafts-store.spec.ts
git diff --check
git commit -m "feat: preserve teacher review drafts"
```

---

### Task 4: Build the route-aware scoring inspector with RED → GREEN

**Files:**
- Create: `frontend/src/components/review/ReviewScoringInspector.vue`
- Create: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Create: `frontend/src/styles/review-scoring.css`
- Modify: `frontend/src/layouts/AppShell.vue`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/__tests__/app-shell.spec.ts`

**Interfaces:**
- Consumes: current route, Session Store, Review Queue Store, Review Draft Store, Task 2 API adapters.
- Produces: route-aware right inspector with rubric, evidence summary, AI suggestion, risk, teacher final score, note, activity placeholder and one primary confirm action.

- [ ] **Step 1: Write failing rendering and hierarchy tests**

Mount `/grading` with a current item and assert:

```ts
expect(wrapper.get('[data-testid="review-scoring-inspector"]').exists()).toBe(true)
expect(wrapper.text()).toContain('教师最终分')
expect(wrapper.text()).toContain('AI 初评')
expect(wrapper.get('[data-testid="teacher-score"]').attributes('aria-describedby')).toContain('score')
expect(wrapper.get('[data-testid="confirm-next"]').text()).toBe('确认并下一份')
```

Also assert other routes still render `SessionInspector`; long rubric is inside the scroll region; the primary action stays in a fixed footer; no candidate is labeled final; activity placeholder contains no invented operator/time; invalid score and unchanged draft disable submit with a visible reason.

Run:

```powershell
npm test -- src/__tests__/review-scoring-inspector.spec.ts src/__tests__/app-shell.spec.ts
```

Expected: FAIL because the scoring inspector is absent.

- [ ] **Step 2: Implement the minimal component and route switch**

Use `route.name === 'grading'` in `AppShell.vue` to choose `ReviewScoringInspector` or `SessionInspector` in the same grid cell and with the same open/close control. The scoring inspector must use semantic sections and a sticky footer; styles derive only from existing tokens and use `--color-teacher*` for the teacher decision area.

Display rules:

- no session/item: actionable empty state;
- rubric loading/error/missing: isolated state that does not hide score input;
- AI block: current unconfirmed score, confidence, candidates, evidence/missing steps;
- risk block: existing `needs_review`, reason/category/summary only;
- teacher block: score input + max, note, dirty count and confirmation status;
- activity block: stable “当前接口暂未提供历史活动列表” placeholder.

Run the same tests and expect PASS.

- [ ] **Step 3: Commit the inspector slice**

```powershell
git add frontend/src/components/review/ReviewScoringInspector.vue frontend/src/__tests__/review-scoring-inspector.spec.ts frontend/src/styles/review-scoring.css frontend/src/layouts/AppShell.vue frontend/src/main.ts frontend/src/__tests__/app-shell.spec.ts
git diff --check
git commit -m "feat: add teacher scoring inspector"
```

---

### Task 5: Integrate safe confirmation, failure retention and correct navigation with RED → GREEN

**Files:**
- Modify: `frontend/src/components/review/ReviewScoringInspector.vue`
- Modify: `frontend/src/stores/review-queue.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/src/__tests__/review-queue-store.spec.ts`
- Modify: `frontend/src/__tests__/review-queue-view.spec.ts`

**Interfaces:**
- Consumes: single-item confirm response, pre-submit filtered queue, current route/session/item generation.
- Produces: one-shot confirmation, local authoritative patch, refresh reconciliation, next-item selection, dirty `beforeunload` guard and safe status feedback.

- [ ] **Step 1: Write failing confirmation behavior tests**

Cover:

1. POST failure leaves score/note and current selection unchanged and shows retryable/non-retryable safe impact text without response details.
2. Rapid double click produces one POST.
3. Success clears only submitted draft, marks teacher-confirmed locally, then selects the pre-submit next detail.
4. Selection changed while POST is pending is not overwritten by the old success.
5. POST success plus GET refresh failure remains success and warns that the list may be stale.
6. `retry_required` annotation outcome shows a non-blocking warning but does not restore the draft.
7. `beforeunload` is prevented only while any dirty draft exists; input focus continues to protect J/K.

Run:

```powershell
npm test -- src/__tests__/review-scoring-inspector.spec.ts src/__tests__/review-queue-store.spec.ts src/__tests__/review-queue-view.spec.ts
```

Expected: FAIL on missing confirmation/reconciliation behavior.

- [ ] **Step 2: Implement one-shot submit and queue reconciliation**

Add a queue-store method that patches a confirmed detail using a new immutable item object with the teacher score, note/default confirmation reason, `needs_review=false`, and confirmed category/summary. Do not mutate other items or redefine sorting.

Capture before POST:

```ts
const submittedDetailId = item.detail_id
const nextDetailId = reviewStore.filteredItems[reviewStore.currentIndex + 1]?.detail_id ?? null
const context = `${item.session_id}:${item.question_id}:${item.detail_id}`
```

After success, patch and clear the draft. Only select `nextDetailId` when the current context still equals `context`; then refresh Review GET. Handle refresh failure separately from POST failure.

Run the same tests and expect PASS.

- [ ] **Step 3: Commit the integrated behavior**

```powershell
git add frontend/src/components/review/ReviewScoringInspector.vue frontend/src/stores/review-queue.ts frontend/src/views/ReviewQueueView.vue frontend/src/__tests__/review-scoring-inspector.spec.ts frontend/src/__tests__/review-queue-store.spec.ts frontend/src/__tests__/review-queue-view.spec.ts
git diff --check
git commit -m "feat: confirm teacher scores safely"
```

---

### Task 6: Prove the browser workflow, five viewports and package handoff

**Files:**
- Create: `frontend/e2e/review-scoring.spec.ts`
- Modify: `frontend/e2e/review-queue.spec.ts`
- Modify: `frontend/e2e/review-evidence.spec.ts`
- Create after verified runtime exists: `docs/user-testing/checkpoints/P2-07-review-scoring-quick.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md`

**Interfaces:**
- Consumes: mock API routes, generated media, real built Vue app and browser.
- Produces: browser evidence, quick-test instructions, architecture boundary and validator-compatible review handoff.

- [ ] **Step 1: Write the failing Playwright workflow**

Use generated anonymous data and intercept config/Review GET/confirm POST. Assert the complete path:

1. select a pending record;
2. rubric, AI initial score, risk and teacher score are simultaneously discoverable;
3. edit score/note, switch record and return, draft remains;
4. invalid score blocks POST;
5. server 422/500 preserves input;
6. success submits one item and moves to the pre-submit next record;
7. annotation retry warning is non-blocking;
8. scoring input focus prevents J/K navigation;
9. long rubric scrolls while footer remains visible;
10. at 1920×1080, 1440×900, 1366×768, 1280×800 and 1024×768 there is no horizontal overflow, overlap or unreachable primary action;
11. no `pageerror` or console error occurs.

Run:

```powershell
npm run e2e -- review-scoring.spec.ts
```

Expected before final implementation: FAIL on missing or incomplete workflow; after implementation: PASS.

- [ ] **Step 2: Run package and affected regressions**

Run from `frontend/`:

```powershell
npm test -- src/api/__tests__/review.spec.ts src/__tests__/review-drafts-store.spec.ts src/__tests__/review-scoring-inspector.spec.ts src/__tests__/review-queue-store.spec.ts src/__tests__/review-queue-view.spec.ts src/__tests__/evidence-viewer-state.spec.ts src/__tests__/review-evidence-viewer.spec.ts src/__tests__/app-shell.spec.ts
npm run e2e -- review-queue.spec.ts review-evidence.spec.ts review-scoring.spec.ts
npm run lint
npm run typecheck
npm test
npm run build
```

Run from worktree root:

```powershell
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' -m pytest tests/test_api_review_routes.py tests/test_review_application_service.py -q
git diff --check
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
git status --short -- user_data
```

Expected: all commands pass; no `user_data` paths are modified or untracked. Do not run full pytest unless a repository risk trigger appears.

- [ ] **Step 3: Perform visual critique and create the verified quick checklist**

Use the real browser at all five required viewports. Confirm the evidence canvas remains the central visual focus, teacher final score is more prominent than AI, the footer is stable, status is not color-only, long Chinese content is readable, and the page does not become a card stack. Remove any decoration that does not support scoring.

Only after the synthetic runtime is proven, create `docs/user-testing/checkpoints/P2-07-review-scoring-quick.md` with exact data source, startup, URL, visible marker, five-minute steps, shutdown and machine result block. It must not mention an environment that has not been implemented.

- [ ] **Step 4: Recheck root real-data fingerprints**

From the repository root, read size, UTC mtime and SHA-256 without opening SQLite. Expected values must exactly equal the two values recorded in the claim evidence. Any difference stops the package.

- [ ] **Step 5: Commit the functional handoff**

Update `ARCHITECTURE.md` with the P2-07 implemented boundary, check every completed plan box, and set the handoff block to:

```markdown
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**夜间动作：** report_only
```

Commit only source/tests/package evidence (never `user_data`). Run:

```powershell
git diff --check
git status --short -- user_data
git add frontend ARCHITECTURE.md docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md docs/user-testing/checkpoints/P2-07-review-scoring-quick.md
git diff --cached --name-only
git commit -m "feat: complete P2-07 review scoring"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-07-review-scoring-implementation.md `
  --repo .
```

Expected: validator returns `ok=true`, `state="waiting_review"` and no issues.

- [ ] **Step 6: Complete independent review, quick user check and standard integration**

Use `superpowers:requesting-code-review` without subagents: perform a fresh requirement review and code-quality review, fix every Critical/Important finding with a new RED → GREEN cycle, and rerun only affected tests plus required package gates.

After review passes, create the required plan-only `waiting_user` anchor commit that records the reviewed 40-character SHA. Start the verified synthetic runtime and ask the user to follow the quick checklist. If the user passes it, create the checklist-only evidence commit, then the plan-only `verified_pending_integration` handoff commit. Validate after each state transition.

Integrate the complete commit chain into a fresh P2-07 integration branch based on latest `origin/main`, run affected frontend gates plus one full integration smoke, recheck real database fingerprints, push integration, open and merge a GitHub PR into `main`, then fetch/prune and fast-forward local `main` and active worktrees. Do not directly push `main`, force push, delete unmerged history or remove any worktree containing data.

## Plan Self-Review

- Spec coverage: every P2-07 goal maps to Tasks 2–6; no P2-08 global shortcut set, new backend contract or production switch is included.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type consistency: `ReviewConfirmInput/Response`, `ReviewRubricSection`, draft key and queue patch identities are defined once and consumed by later tasks with the same names.
- Safety: only temporary/mock data is used; real databases are hashed read-only before and after; confirm POST is single-attempt and existing server validation remains authoritative.
- Rollback: reverting the P2-07 commits restores the P2-06 evidence viewer and generic Session Inspector; Streamlit, FastAPI, databases and production entry remain unchanged.
