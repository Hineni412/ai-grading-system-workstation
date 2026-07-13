# P2-08 Sample Page Integration and Formal Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-08
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** b6ab906872653dfc06ac222c1ea3de681a0930d9
**用户自测：** formal
**自测清单：** docs/user-testing/checkpoints/P2-08-sample-page-formal.md

**Goal:** 把已经合并的复核队列、答卷证据查看器和教师评分检查器收口为可发现、可连续操作、可刷新恢复且能用固定匿名数据完成正式验收的单题复核样板页，在 Blocker/Major 清零前继续阻止复杂页面扩散。

**Architecture:** 保持 `/grading` 现有三栏和后端契约不变，在页面层增加一个同步、无持久状态的快捷键命令总线，让队列搜索、证据查看器和评分检查器各自消费自己拥有的命令。匿名验收环境使用只绑定 loopback 的独立 Node 测试服务器，从固定 JSON 数据集提供构建后的 SPA、模拟 API 和程序生成媒体；它不进入生产构建路径、不读取 `user_data/`、不调用模型。2026-07-13 用户明确确认：`R` 不代表已存在的业务需求，本包删除 `R` 及“重新标记待复核”门槛，不新增后端写接口；未来如有真实需求，另包设计可持久化的“稍后再看”状态。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, Vue Router 5.1.0, native Fetch client, Element Plus 2.14.3, Vitest 4.1.10, Playwright 1.61.1, Node 24 built-in HTTP/test modules, existing CSS Tokens, Python 3.12 repository guards.

## Global Constraints

- 业务事实以已合并 P2-05/P2-06/P2-07、Review API 和 `docs/ui/STYLE.md` 为准；不新增评分规则、后端路由、Schema、状态、模型调用或数据库迁移。
- 快捷键固定为：`J/K` 下一份/上一份，`/` 聚焦搜索，`Z` 适应宽度，`+/-` 缩放，`Enter` 确认当前记录并进入提交前确定的下一份。`Shift+Enter` 不再是受支持操作。
- `R` 不实现、不展示、不进入测试或验收。该决定来自用户对当前业务含义的明确确认，不得通过复用 confirm 接口伪造“待复核”状态。
- 全局快捷键只在 `/grading` 生效；`input`、`textarea`、`select`、按钮、可编辑区域或已经 `preventDefault()` 的局部控件中不得误触。写快捷键的按键自动重复不得产生重复 POST。
- 画布自身已有的 `Z/0/+/-/方向键` 必须继续工作且不得被全局处理两次；全局 `Z/+/-` 只把命令交给现有查看器状态，不复制缩放算法。
- 得分框聚焦时全选旧分；得分框内外的普通 `Enter` 共用 P2-07 的单条 one-shot confirm，成功后才导航。`Shift+Enter` 不提交；失败保留草稿，POST 成功后的刷新失败不得恢复草稿或误报写入失败。
- 页面增加一条克制、可换行的快捷键提示带作为唯一新增视觉签名；它表达真实工作序列，不引入卡片堆叠、营销标题、渐变、大圆角或新的颜色/字体。
- 固定匿名数据集必须覆盖长姓名/学号/班级、长评分标准、待复核/已复核、不同置信度、正反面媒体、确认失败、批注稍后刷新、空队列和加载/错误恢复。所有姓名、答卷和文本均为程序生成，不得复制真实业务内容。
- 匿名服务器只绑定 `127.0.0.1`，只服务 `frontend/dist`、固定模拟 API、程序生成媒体和受限测试控制端点；不得解析任意文件路径、代理生产 API 或读取仓库 `user_data/`。
- 刷新恢复的含义固定为：当前考试、题目、记录 URL 和服务器已确认结果恢复；未确认草稿仍按 P2-07 仅驻留内存，刷新前显示标准浏览器警告，刷新后不伪称草稿已保存。
- 只支持不低于 1024px 的 Windows 桌面浏览器；验证 1920×1080、1440×900、1366×768、1280×800、1024×768，平板和手机不进入实现或验收。
- 测试只使用 mock API、固定匿名数据和程序生成媒体；不得读取、修改、暂存、提交或 stash 真实 `user_data/`，不得调用真实模型。
- 实施使用 `frontend-design`、`test-driven-development` 和 `executing-plans`；每个新行为先确认 RED，再做最小 GREEN。完成声明前使用 `verification-before-completion`。
- 当前任务不派生子代理。独立复审采用本会话的 fresh-pass 需求审查与代码质量审查，Critical/Important 必须清零后才可进入用户正式验收。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-08
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 2026-07-13 领取证据

- 专属工作树从最新 `origin/main` 的 `b6ab906872653dfc06ac222c1ea3de681a0930d9` 创建；创建后源码和 `user_data/` 状态均干净。
- 根工作区真实数据库仅读取文件大小、UTC 修改时间与 SHA-256：阅卷库 `2863104 / 2026-07-10T07:10:41.1221109Z / 93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`；题库 `3461120 / 2026-07-08T11:58:06.3320883Z / E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`。未用 SQLite 打开真实库。
- 起点基线：前端 20 个测试文件共 171 项通过；Review queue/evidence/scoring 三个 Chromium 套件共 16 项通过。
- 根工作区存在与本包无关的 Phase 3 文档改动和真实数据状态；本包不在根工作区实施、不暂存它们，也不将它们复制进功能提交。

## 2026-07-13 用户验收反馈重开

- 用户在正式验收过程中确认原实现符合旧清单，但要求改成更符合连续阅卷习惯的方案 A：得分框聚焦时全选旧分，普通 Enter 在得分框内外均“确认并下一份”，取消“确认并停留”和 `Shift+Enter`。
- 原 reviewed source `4684ffa2905bf917d79e41c792b57c686c9403c2` 仅保留为历史证据；本次交互代码变化后不得复用其自动验证、独立复审或用户验收结论。
- 已确认设计与增量实施计划分别为 `docs/superpowers/specs/2026-07-13-p2-08-teacher-score-entry-design.md` 和 `docs/superpowers/plans/2026-07-13-p2-08-teacher-score-entry-implementation.md`。
- 新候选自动验证：前端 22 个测试文件共 179 项、评分浏览器 5 项、P2-08 正式浏览器门禁 10 项（含五种桌面尺寸）、Review 后端 22 项、匿名服务契约 5 项均通过；lint、类型检查、生产构建和 `tools/smoke_check.py --skip-tests` 通过。
- fresh-pass 需求与代码质量复审未发现 Critical/Important；得分框全选、输入框内外 Enter 单次确认并前进、Shift+Enter 无动作、备注框换行、无效/失败/重复提交保护均有自动证据。
- 真实两库只读指纹和 Stash 基线复核不变；本次实现与验证没有读取业务内容、写入真实数据库或暂存 `user_data/`。

## 2026-07-13 步骤 9 验收失败重开

- 用户在 422 失败模式确认：安全中文提示内容正确、草稿保留，但提示位于右侧内部滚动区顶部；教师在下方最终得分框操作时通常不可见。
- 该问题按 Major 处理，正式验收暂停并退回实现；后续步骤不得沿用本轮通过结论，修复和复审后从步骤 9 重新开始。
- 用户选择非阻塞的右下角浮动通知：失败和警告保持到手动关闭，成功自动消失；通知不得抢焦点、遮挡主要操作或在 1024×768 下溢出屏幕。
- 修复候选自动验证：前端 23 个测试文件共 182 项、评分浏览器 5 项、P2-08 正式浏览器门禁 10 项（含五种桌面尺寸）、Review 后端 22 项、匿名服务契约 5 项均通过；lint、生产构建和 `tools/smoke_check.py --skip-tests` 通过。
- fresh-pass 需求与代码质量复审未发现 Critical/Important；已核对通知定时器清理、单通知替换、无障碍语义、1024×768 几何位置、不抢焦点、不重复显示、失败保留草稿、Enter 行为不变，以及后端和真实数据边界不变。
- 真实两库只读指纹和 Stash 基线再次复核不变；本次修复与验证没有读取业务内容、写入真实数据库或暂存 `user_data/`。

---

### Task 1: Claim P2-08 with the only valid first commit

**Files:**
- Create: `docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md`

**Interfaces:**
- Consumes: approved package boundary, latest `origin/main`, immutable stash baseline and read-only database fingerprints.
- Produces: validator-compatible first first-parent claim commit with `in_progress` handoff state.

- [x] **Step 1: Verify plan identity, clean channel and immutable baselines**

Run from the P2-08 worktree root:

```powershell
git status --short
git status --short -- user_data
git rev-parse HEAD
git rev-parse origin/main
git stash list --format=%H
rg -n "\*\*执行包：\*\* P2-08|HANDOFF_STATUS_(START|END)" docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md
```

Expected: only this plan is untracked; `user_data` is clean; both SHAs equal the plan baseline; stash output is exactly the two recorded SHAs; the plan has one top package declaration and one matching declaration inside exactly one handoff block.

- [x] **Step 2: Commit only the claim plan and validate it**

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
git add docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md
git diff --cached --name-only
git diff --check
git commit -m "docs: claim P2-08 sample page gate"
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md `
  --repo .
```

Expected: staged list contains only this plan; validator emits one JSON line with `ok=true`, `state="in_progress"`, and no issues.

---

### Task 2: Add a bounded review-workspace shortcut bus with RED → GREEN

**Files:**
- Create: `frontend/src/composables/review-shortcuts.ts`
- Create: `frontend/src/__tests__/review-shortcuts.spec.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/components/review/ReviewQueuePanel.vue`
- Modify: `frontend/src/components/review/ReviewEvidenceViewer.vue`
- Modify: `frontend/src/__tests__/review-queue-view.spec.ts`
- Modify: `frontend/src/__tests__/review-evidence-viewer.spec.ts`

**Interfaces:**
- Consumes: existing window key listener, queue selection methods and evidence viewer state methods.
- Produces: `ReviewShortcutCommand`, `ReviewShortcutBus`, singleton `reviewShortcutBus`, synchronous `dispatch(command)` and `subscribe(handler): () => void`.

- [x] **Step 1: Write failing command-bus and shortcut-routing tests**

Add a unit test that proves subscription is synchronous, unsubscribe is final and dispatch order is stable:

```ts
const bus = new ReviewShortcutBus()
const received: ReviewShortcutCommand[] = []
const stop = bus.subscribe((command) => received.push(command))
bus.dispatch('focus-search')
bus.dispatch('fit-width')
stop()
bus.dispatch('zoom-in')
expect(received).toEqual(['focus-search', 'fit-width'])
```

Extend `review-queue-view.spec.ts` to prove:

```ts
window.dispatchEvent(new KeyboardEvent('keydown', { key: '/' }))
expect(document.activeElement).toBe(host.querySelector('#review-search'))

window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z' }))
expect(shortcutSpy).toHaveBeenCalledWith('fit-width')
```

Cover `J/K`, `/`, `Z`, `+`/`=`, `-` and unshifted `Enter`; assert `Shift+Enter` and `R` dispatch nothing. Assert all commands are suppressed from protected targets, modifier chords and already-prevented events; repeated Enter does not dispatch a write command. Extend evidence-viewer tests so global fit/zoom commands call the same existing state methods exactly once, while a canvas-local key that already prevented default is not handled again.

Run:

```powershell
cd frontend
npm test -- src/__tests__/review-shortcuts.spec.ts src/__tests__/review-queue-view.spec.ts src/__tests__/review-evidence-viewer.spec.ts
```

Expected: FAIL because the bus and cross-component routing do not exist.

- [x] **Step 2: Implement the minimal synchronous bus and ownership-safe handlers**

Implement the core as:

```ts
export type ReviewShortcutCommand =
  | 'focus-search'
  | 'fit-width'
  | 'zoom-in'
  | 'zoom-out'
  | 'confirm-next'

export class ReviewShortcutBus {
  private readonly handlers = new Set<(command: ReviewShortcutCommand) => void>()

  subscribe(handler: (command: ReviewShortcutCommand) => void): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  dispatch(command: ReviewShortcutCommand): void {
    for (const handler of [...this.handlers]) handler(command)
  }
}

export const reviewShortcutBus = new ReviewShortcutBus()
```

In `ReviewQueueView.vue`, keep `J/K` local because the view owns navigation; map the other accepted keys to the bus. Permit Shift only when it produces `+`; shifted Enter returns without dispatch. In `ReviewQueuePanel.vue`, hold a search input ref and subscribe/unsubscribe on mount/unmount; `focus-search` calls `focus()` and `select()`. In `ReviewEvidenceViewer.vue`, subscribe to `fit-width/zoom-in/zoom-out` and delegate to `viewer.fitWidth()` or `viewer.zoomBy(±0.25)`.

Run the same test command and expect PASS.

- [x] **Step 3: Commit the shortcut coordination slice**

```powershell
git add frontend/src/composables/review-shortcuts.ts frontend/src/__tests__/review-shortcuts.spec.ts frontend/src/views/ReviewQueueView.vue frontend/src/components/review/ReviewQueuePanel.vue frontend/src/components/review/ReviewEvidenceViewer.vue frontend/src/__tests__/review-queue-view.spec.ts frontend/src/__tests__/review-evidence-viewer.spec.ts
git diff --check
git commit -m "feat: coordinate review workspace shortcuts"
```

---

### Task 3: Unify Enter as confirm-and-next with RED → GREEN

**Files:**
- Modify: `frontend/src/components/review/ReviewScoringInspector.vue`
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/e2e/review-scoring.spec.ts`

**Interfaces:**
- Consumes: the `confirm-next` bus command, P2-07 one-shot confirm adapter and pre-submit filtered queue.
- Produces: focus-select score entry, score-field/page Enter-confirm-and-next, inactive Shift+Enter and unchanged click behavior for the existing primary button.

- [x] **Step 1: Write failing confirmation-mode tests**

Add component tests proving:

```ts
reviewShortcutBus.dispatch('confirm-next')
expect(confirmReviewItem).toHaveBeenCalledTimes(1)
expect(store.selectedDetailId).toBe(nextDetailId)

scoreInput.dispatchEvent(new FocusEvent('focus'))
expect(scoreInput.select).toHaveBeenCalledTimes(1)

scoreInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
expect(confirmReviewItem).toHaveBeenCalledTimes(1)
```

Also prove disabled/invalid/clean/submitting states do not POST, repeated Enter does not duplicate POST, selection changed during POST is never overwritten, failure retains the same draft, and POST success plus refresh failure remains a success. Extend Playwright to prove click-and-type replaces the old score, Enter works in the score field and evidence workspace, and Shift+Enter stays inactive.

Run:

```powershell
cd frontend
npm test -- src/__tests__/review-scoring-inspector.spec.ts src/__tests__/review-queue-view.spec.ts
npm run e2e -- review-scoring.spec.ts
```

Expected: FAIL because the score field does not select its old value or consume Enter, page Enter still stays in place, and Shift+Enter still submits.

- [x] **Step 2: Implement one submit path with an explicit advance flag**

Change the submit signature to:

```ts
async function submitCurrent(advance = true): Promise<void> {
  // existing one-shot validation and POST
  const nextDetailId = advance
    ? reviewStore.filteredItems[reviewStore.currentIndex + 1]?.detail_id ?? null
    : null
  // after confirmed local patch, navigate only when advance is true
}
```

Subscribe to the shortcut bus on mount and unsubscribe on unmount. `confirm-next` calls `submitCurrent(true)`. Bind score focus to `input.select()` and unmodified, non-repeated score-field Enter to the same `submitCurrent(true)` path; leave the note textarea protected. Keep the button bound to `submitCurrent(true)`. Preserve the existing context equality guard, local authoritative patch, annotation warning and separate refresh-failure message.

Run the same tests and expect PASS.

- [x] **Step 3: Commit the confirmation behavior**

```powershell
git add frontend/src/components/review/ReviewScoringInspector.vue frontend/src/__tests__/review-scoring-inspector.spec.ts frontend/e2e/review-scoring.spec.ts
git diff --check
git commit -m "feat: add explicit review confirmation shortcuts"
```

---

### Task 4: Make the integrated shortcuts discoverable without redesigning the page

**Files:**
- Create: `frontend/src/components/review/ReviewShortcutGuide.vue`
- Create: `frontend/src/__tests__/review-shortcut-guide.spec.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/styles/review-queue.css`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Consumes: the approved shortcut set and existing CSS Tokens.
- Produces: a semantic, wrapping shortcut ribbon that lists only real actions and remains readable at all five desktop viewports.

- [x] **Step 1: Write the failing rendering and accessibility test**

Mount the guide and assert that it contains exactly the approved actions:

```ts
expect(wrapper.text()).toContain('J / K')
expect(wrapper.text()).toContain('Enter')
expect(wrapper.text()).toContain('确认并下一份')
expect(wrapper.text()).not.toContain('Shift + Enter')
expect(wrapper.text()).toContain('Z')
expect(wrapper.text()).toContain('+ / −')
expect(wrapper.text()).toContain('/')
expect(wrapper.text()).not.toContain('R')
expect(wrapper.get('[aria-label="单题复核快捷键"]').exists()).toBe(true)
```

Run:

```powershell
cd frontend
npm test -- src/__tests__/review-shortcut-guide.spec.ts src/__tests__/review-queue-view.spec.ts
```

Expected: FAIL because the guide does not exist.

- [x] **Step 2: Implement the restrained shortcut ribbon**

Use semantic `<dl>`/`<kbd>` markup, plain Chinese action labels and existing `--color-*`, spacing, radius and type tokens. Place it below the page title and above feedback/workspace states. Allow wrapping at compact widths; do not add horizontal scrolling, fixed overlays, shadows or a second primary action.

Run the same tests and expect PASS.

- [x] **Step 3: Commit the discoverability slice**

```powershell
git add frontend/src/components/review/ReviewShortcutGuide.vue frontend/src/__tests__/review-shortcut-guide.spec.ts frontend/src/views/ReviewQueueView.vue frontend/src/styles/review-queue.css frontend/src/main.ts
git diff --check
git commit -m "feat: expose review workspace shortcuts"
```

---

### Task 5: Build a fixed anonymous formal-acceptance runtime with RED → GREEN

**Files:**
- Create: `frontend/demo/p2-08-dataset.json`
- Create: `frontend/demo/p2-08-server.mjs`
- Create: `frontend/demo/p2-08-server.test.mjs`
- Modify: `frontend/package.json`
- Create: `frontend/e2e/p2-08-formal-gate.spec.ts`
- Create: `frontend/playwright.p2-08.config.ts`

**Interfaces:**
- Consumes: built `frontend/dist`, existing same-origin API paths and fixed anonymous JSON.
- Produces: `npm run demo:p2-08`, `npm run demo:test`, loopback static/API server, reset/mode controls and repeatable formal browser evidence.

- [x] **Step 1: Write failing server contract tests**

Using `node:test`, start the server on an ephemeral loopback port with a temporary static root and assert:

```js
assert.equal((await fetch(`${base}/healthz`)).status, 200)
assert.equal((await fetch(`${base}/api/sessions`)).status, 200)
assert.equal((await fetch(`${base}/grading?question=Q1&detail=1`)).status, 200)
assert.equal((await fetch(`${base}/api/media/crop/1`)).headers.get('cache-control'), 'no-store')
```

Prove confirm updates only in-memory anonymous state, reset restores the fixed dataset, 422/500/annotation-retry modes are explicit, arbitrary paths cannot escape the static root, and the server never resolves or references `user_data`.

Run:

```powershell
cd frontend
node --test demo/p2-08-server.test.mjs
```

Expected: FAIL because the demo runtime does not exist.

- [x] **Step 2: Implement the loopback-only static/API server**

Use only `node:http`, `node:fs`, `node:path`, `node:url` and `node:crypto`. Bind `127.0.0.1`; normalize and contain static paths under `dist`; serve SPA fallback only for GET/HEAD non-API routes; return JSON for the exact sessions/config/review endpoints and deterministic SVG/PNG media with `Cache-Control: no-store`. Accept only these control operations:

```text
POST /__p2_08__/reset
POST /__p2_08__/mode   {"confirm":"success|422|500|retry","items":"ready|empty|error|slow"}
```

Reject unknown control values with 422 and unknown API routes with 404. Store all confirmation changes in process memory and never open a database.

Add package scripts without changing dependencies or the lock file:

```json
"demo:p2-08": "node demo/p2-08-server.mjs",
"demo:test": "node --test demo/p2-08-server.test.mjs"
```

Run the same server tests and expect PASS.

- [x] **Step 3: Write and pass the integrated formal browser gate**

Create a Playwright suite using the same dataset and verify:

1. fixed anonymous marker, queue, evidence and scoring are simultaneously discoverable;
2. `/`, J/K, unshifted Enter, Z and +/- perform exactly one action; Shift+Enter and `R` perform none;
3. protected focus suppresses global shortcuts;
4. draft survives record changes, warns before refresh, and does not claim persistence after refresh;
5. confirmed server state and URL/session/question/detail restore after refresh;
6. 422/500 retain input; annotation retry remains non-blocking;
7. loading, empty, retained-content error, first-load error, disabled and long-content states are actionable;
8. each required viewport has no document overflow, queue/evidence/scoring overlap or unreachable primary action;
9. non-empty image pixels, console errors and page errors are checked;
10. no production API, real model or real data path is contacted.

Run:

```powershell
cd frontend
npm run e2e:p2-08
```

Expected before implementation: FAIL; after implementation: PASS.

- [x] **Step 4: Commit the anonymous acceptance runtime**

```powershell
git add frontend/demo frontend/e2e/p2-08-formal-gate.spec.ts frontend/playwright.p2-08.config.ts frontend/package.json
git diff --check
git diff -- frontend/package-lock.json
git commit -m "test: add P2-08 formal acceptance runtime"
```

Expected: lockfile diff is empty; the commit contains no generated `dist`, screenshots, reports or `user_data`.

---

### Task 6: Complete visual critique, package verification and formal user gate

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md`
- Create only after the verified runtime exists: `docs/user-testing/checkpoints/P2-08-sample-page-formal.md`
- Integration-only update after user acceptance: `docs/superpowers/packages/phase-2-execution-packages.md`
- Integration-only update after user acceptance: `docs/superpowers/packages/EXECUTION_INDEX.md`

**Interfaces:**
- Consumes: final source commit, fixed anonymous runtime, five-viewpoint browser evidence and explicit user result.
- Produces: `waiting_review` feature commit, reviewed-SHA anchor, formal checklist-only result commit, `verified_pending_integration` handoff and standard integration PR.

- [x] **Step 1: Run package and affected regressions**

From `frontend/`:

```powershell
npm test -- src/__tests__/review-shortcuts.spec.ts src/__tests__/review-shortcut-guide.spec.ts src/__tests__/review-queue-store.spec.ts src/__tests__/review-queue-view.spec.ts src/__tests__/evidence-viewer-state.spec.ts src/__tests__/review-evidence-viewer.spec.ts src/__tests__/review-drafts-store.spec.ts src/__tests__/review-scoring-inspector.spec.ts src/api/__tests__/review.spec.ts src/components/shell/__tests__/app-shell.spec.ts
npm run e2e -- review-queue.spec.ts review-evidence.spec.ts review-scoring.spec.ts p2-08-formal-gate.spec.ts
npm run demo:test
npm run lint
npm run typecheck
npm test
npm run build
```

From the worktree root:

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
& (Join-Path $repoRoot 'runtime/python/python.exe') -m pytest tests/test_api_review_routes.py tests/test_review_application_service.py -q
git diff --check
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/smoke_check.py --skip-tests
git status --short -- user_data
```

Expected: every command passes, `user_data` is clean and no risk trigger requires full pytest. If a trigger appears, record it and run the required wider gate before proceeding.

- [x] **Step 2: Perform a fresh visual critique at all five viewports**

Build and start the fixed anonymous runtime, then inspect the real browser at every required viewport. Confirm the evidence canvas remains the central visual focus; teacher score is more prominent than AI; the shortcut ribbon is useful but quiet; long rubric and queue content remain independently scrollable; footer and primary action are reachable; focus is visible; states are not color-only; there is no card stack, decorative gradient, overlap, overflow, console error or empty image.

Remove any decoration that does not support continuous review, rerun the affected tests, and capture only repository-excluded temporary screenshots for review. Do not commit screenshots.

- [x] **Step 3: Create the verified formal checklist**

Only after the runtime and browser evidence pass, create `docs/user-testing/checkpoints/P2-08-sample-page-formal.md` using `docs/user-testing/USER_TEST_TEMPLATE.md`. It must state exact anonymous data source, build/start command, loopback address, visible marker, reset/mode preparation owned by Codex, 30–45 minute numbered steps, expected results, shutdown, Blocker/Major/Minor handling and machine result block. It must explicitly describe refresh semantics and state that `R` is not a supported action.

- [x] **Step 4: Recheck root real-data fingerprints and create the functional handoff commit**

Read only the two root database files' size, UTC mtime and SHA-256; they must exactly equal the claim evidence. Any difference stops the package for investigation.

Update `ARCHITECTURE.md` with the P2-08 implemented boundary, check completed plan boxes and set the handoff block to:

```markdown
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**夜间动作：** report_only
```

Commit source, tests, package evidence and the unexecuted formal checklist, never `user_data`:

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
git diff --check
git status --short -- user_data
git add frontend ARCHITECTURE.md docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md docs/user-testing/checkpoints/P2-08-sample-page-formal.md
git diff --cached --name-only
git commit -m "feat: complete P2-08 sample page gate"
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md `
  --repo .
```

Expected: validator returns `ok=true`, `state="waiting_review"` and no issues.

- [x] **Step 5: Perform independent review and anchor the reviewed SHA**

Use `superpowers:requesting-code-review` without subagents. Re-read the package, user decision and plan; perform a fresh requirements review followed by code-quality/security/accessibility review. Fix every Critical/Important finding with a new RED → GREEN cycle and rerun the affected tests plus package gates.

After review passes, create a plan-only `waiting_user` anchor commit whose handoff block records the full 40-character reviewed source SHA, with automatic verification and independent review `passed`, user acceptance `pending`, real-data fingerprint `unchanged` and `report_only`. Validate the anchor.

- [ ] **Step 6: Run the 30–45 minute formal user acceptance**

Start exactly the reviewed anonymous runtime, open the declared URL and ask the user to follow the versioned checklist. Keep reset and failure-mode preparation under Codex control. Blocker or Major stops the gate and returns to this feature branch; Minor is recorded for the user's decision. Only the user's explicit conclusion can set the result to `passed`.

If passed, create the required checklist-only evidence commit. Its machine result block must record the anchored reviewed SHA and `passed`; no other file may change.

- [ ] **Step 7: Create final handoff and complete standard integration**

Create the plan-only `verified_pending_integration` commit immediately after the checklist evidence commit; its handoff block records the reviewed SHA, verification/review/user acceptance `passed`, real-data fingerprint `unchanged` and `independent_candidate_allowed`. Validate it.

Create a fresh P2-08 integration branch from latest `origin/main`, merge the full feature chain, update the Phase 2 package definition and Index to remove the obsolete `R` requirement and record the merged package facts, then run affected frontend gates plus one complete integration smoke and real-data fingerprint guard. Push the integration branch, open and merge a GitHub PR into `main`, fetch/prune and fast-forward local `main` and active worktrees. Do not directly push `main`, force push, delete unmerged history or remove any worktree containing data.

## Plan Self-Review

- Spec coverage: Tasks 2–6 cover every approved P2-08 behavior—integrated shortcuts, focus safety, confirm modes, fixed anonymous data, loading/empty/error/disabled states, refresh semantics, five viewports, browser evidence and formal user gate.
- User decision: `R` is explicitly removed from implementation, UI, tests and acceptance; no backend write or invented status semantics remain.
- Placeholder scan: every implementation step names concrete files, interfaces, commands and expected results; no unfinished marker or undefined future interface remains.
- Type consistency: `ReviewShortcutCommand`, `ReviewShortcutBus`, `submitCurrent(advance)` and demo control modes are defined once and consumed with the same names.
- Safety: the demo server is loopback-only and in-memory; it cannot read arbitrary paths or `user_data`; real databases are hashed without SQLite before and after.
- Rollback: reverting the P2-08 source commits restores the merged P2-07 page; deleting the demo directory and package scripts removes the acceptance runtime; Streamlit, FastAPI, databases and production entry remain unchanged.
