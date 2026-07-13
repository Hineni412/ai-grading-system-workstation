# P2-08 Teacher Score Entry Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让教师单击最终得分后直接覆盖旧分，并把唯一 Enter 提交动作统一为“确认并下一份”，取消“确认并停留”和 Shift+Enter。

**Architecture:** 保持现有确认 API、草稿仓库和队列前进逻辑不变。最终得分输入框只新增聚焦全选与本地 Enter 处理；页面级快捷键只派发一个 `confirm-next` 命令，评分检查器继续复用现有 one-shot `submitCurrent(true)`，从而让输入框内外得到相同结果而不影响备注框换行。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, Vitest 4.1.10, Playwright 1.61.1, existing P2-08 anonymous runtime.

## Global Constraints

- 已确认设计为 `docs/superpowers/specs/2026-07-13-p2-08-teacher-score-entry-design.md`；最终行为只采用方案 A。
- 单击最终得分框取得焦点时选中完整旧值；直接输入新数字必须覆盖旧值。
- 最终得分框内及页面非输入区域的无修饰 Enter 均执行一次“确认并下一份”。
- 删除“确认并停留”命令；Shift+Enter 在最终得分框及页面非输入区域均不提交。
- 教师备注框内 Enter 保持正常换行；其他受保护控件不响应页面级快捷键。
- 无改动、无效分数、提交中、重复按键与提交失败继续由现有安全门禁处理；失败保留当前记录和草稿。
- 不改后端、API、Schema、数据库、模型或真实 `user_data/`；测试只使用现有匿名运行环境与 mock API。
- 本次改动发生在正式用户验收完成前；代码变化后必须使旧的 reviewed SHA 与验证证据失效，重新完成自动验证、fresh-pass 复审和正式验收。
- 当前任务不派生子代理；在本会话内使用 `superpowers:executing-plans` 逐项执行。

---

### Task 1: Reopen the P2-08 handoff and prove the requested workflow is RED

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md`
- Modify: `frontend/src/__tests__/review-queue-view.spec.ts`
- Modify: `frontend/src/__tests__/review-shortcut-guide.spec.ts`
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/e2e/review-scoring.spec.ts`
- Modify: `frontend/e2e/p2-08-formal-gate.spec.ts`

**Interfaces:**
- Consumes: current `ReviewShortcutCommand`, `reviewShortcutBus`, `ReviewQueueView.onKeydown` and the versioned P2-08 handoff block.
- Produces: failing unit and browser expectations for click-to-replace, input Enter confirm-next, workspace Enter confirm-next, inactive Shift+Enter, and a five-group shortcut guide.

- [x] **Step 1: Return the canonical handoff to implementation**

Change the handoff block in `2026-07-13-p2-08-sample-page-gate-implementation.md` to:

```markdown
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**夜间动作：** report_only
```

Add a dated note that user feedback during acceptance replaced the old Enter/Shift+Enter design, and that previous source SHA `4684ffa2905bf917d79e41c792b57c686c9403c2` remains historical evidence only.

- [x] **Step 2: Validate and commit the plan-only handoff transition**

Run from the worktree root:

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/handoff_status.py --plan docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md --repo .
git add docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: reopen P2-08 score workflow"
```

Expected: validator reports `ok=true` and `state="in_progress"`; the staged list contains only the canonical handoff plan.

- [x] **Step 3: Write the page shortcut failing test**

Replace the Enter expectations in `review-queue-view.spec.ts` with:

```ts
expect(dispatchKey(window, 'Enter').defaultPrevented).toBe(true)
expect(dispatchKey(window, 'Enter', { shiftKey: true }).defaultPrevented).toBe(false)
expect(received).toEqual([
  'focus-search',
  'fit-width',
  'zoom-in',
  'zoom-in',
  'zoom-out',
  'confirm-next',
])
```

Keep the existing assertions for `R`, protected targets, modifier chords and repeated Enter.

- [x] **Step 4: Write the shortcut-guide failing test**

Change `review-shortcut-guide.spec.ts` to require five `<dt>` entries, `Enter` plus `确认并下一份`, and absence of `Shift + Enter` and `确认并停留`:

```ts
expect(guide?.querySelectorAll('dt')).toHaveLength(5)
expect(guide?.textContent).toContain('Enter')
expect(guide?.textContent).toContain('确认并下一份')
expect(guide?.textContent).not.toContain('Shift + Enter')
expect(guide?.textContent).not.toContain('确认并停留')
```

- [x] **Step 5: Write the score focus-selection failing test**

In `review-scoring-inspector.spec.ts`, spy on the real input method and dispatch focus:

```ts
const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
const select = vi.spyOn(input, 'select')
input.dispatchEvent(new FocusEvent('focus'))
expect(select).toHaveBeenCalledTimes(1)
```

- [x] **Step 6: Write the input Enter and Shift+Enter failing tests**

Set a dirty valid score, dispatch an unshifted Enter from the score input, and assert one POST plus navigation:

```ts
input.value = '4'
input.dispatchEvent(new Event('input', { bubbles: true }))
await nextTick()
const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
input.dispatchEvent(enter)
expect(enter.defaultPrevented).toBe(true)
await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
await vi.waitFor(() => expect(queue.selectedDetailId).toBe(22))
```

On a fresh dirty mount, dispatch `Shift+Enter` and assert `confirmReviewItem` remains uncalled. Dispatch Enter in the teacher-note textarea and assert it also remains uncalled, proving normal note entry stays protected.

- [x] **Step 7: Write browser tests for the real teacher sequence**

In both Playwright suites, add the sequence below. Keep an exact request counter and use a fresh record for the Shift+Enter assertion:

```ts
const score = page.getByTestId('teacher-score')
await score.click()
await page.keyboard.type('4.25')
await expect(score).toHaveValue('4.25')
await score.press('Enter')
await expect.poll(() => confirmRequests).toBe(1)
await expect(page).toHaveURL(/detail=2$/)
```

Also retain one canvas-focused ordinary Enter case and require it to confirm once and advance. Require `Shift+Enter` from both the score field and canvas to leave URL and request count unchanged.

- [x] **Step 8: Run focused unit and browser tests and verify RED**

Run:

```powershell
npm test -- --run src/__tests__/review-queue-view.spec.ts src/__tests__/review-shortcut-guide.spec.ts
npm test -- --run src/__tests__/review-scoring-inspector.spec.ts
npx playwright test e2e/review-scoring.spec.ts --grep "score|Enter" --workers=1
npm run e2e:p2-08 -- --grep "shortcut|score|confirmed" --workers=1
```

from `frontend/`; the last command uses the existing loopback demo server.

Expected: FAIL for the requested missing behavior: focus does not select the value, score-input Enter is protected, ordinary workspace Enter stays on the record, Shift+Enter still submits, and the guide still has six groups. Failures must be assertions, not setup errors.

---

### Task 2: Implement the minimal score-entry interaction

**Files:**
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/src/composables/review-shortcuts.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/components/review/ReviewScoringInspector.vue`
- Modify: `frontend/src/components/review/ReviewShortcutGuide.vue`

**Interfaces:**
- Consumes: `submitCurrent(advance = true): Promise<void>` and the existing submit-disabled/one-shot guards.
- Produces: `selectScore(event: FocusEvent): void`, `onScoreKeydown(event: KeyboardEvent): void`, and a `ReviewShortcutCommand` union containing only `confirm-next` for confirmation.

- [x] **Step 1: Implement input-owned focus and Enter behavior**

Add to `ReviewScoringInspector.vue`:

```ts
function selectScore(event: FocusEvent): void {
  ;(event.currentTarget as HTMLInputElement).select()
}

function onScoreKeydown(event: KeyboardEvent): void {
  if (
    event.key !== 'Enter' ||
    event.shiftKey ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.repeat
  ) return
  event.preventDefault()
  event.stopPropagation()
  void submitCurrent(true)
}
```

Bind the handlers only to the final-score input:

```vue
@focus="selectScore"
@keydown="onScoreKeydown"
```

Do not bind them to the teacher-note textarea.

- [x] **Step 2: Collapse the page confirmation shortcut to one command**

Remove `'confirm-stay'` from `ReviewShortcutCommand`. In `ReviewQueueView.vue`, reject shifted Enter and map only unshifted Enter:

```ts
if (event.shiftKey && key === 'enter') return
// existing modifier handling remains
else if (key === 'enter') command = 'confirm-next'
```

In `ReviewScoringInspector.vue`, subscribe only to:

```ts
if (command === 'confirm-next') void submitCurrent(true)
```

In `ReviewShortcutGuide.vue`, remove the Shift+Enter row and change the Enter description to `确认并下一份`.

- [x] **Step 3: Run focused unit and browser tests and verify GREEN**

Run from `frontend/`:

```powershell
npm test -- --run src/__tests__/review-queue-view.spec.ts src/__tests__/review-shortcut-guide.spec.ts src/__tests__/review-scoring-inspector.spec.ts
npx playwright test e2e/review-scoring.spec.ts --grep "score|Enter" --workers=1
npm run e2e:p2-08 -- --grep "shortcut|score|confirmed" --workers=1
```

Expected: all focused tests PASS with no unhandled errors or warnings; click-and-type replaces the old score in Chromium.

- [x] **Step 4: Commit the tested interaction**

```powershell
git add frontend/src/composables/review-shortcuts.ts frontend/src/views/ReviewQueueView.vue frontend/src/components/review/ReviewScoringInspector.vue frontend/src/components/review/ReviewShortcutGuide.vue frontend/src/__tests__/review-queue-view.spec.ts frontend/src/__tests__/review-shortcut-guide.spec.ts frontend/src/__tests__/review-scoring-inspector.spec.ts frontend/e2e/review-scoring.spec.ts frontend/e2e/p2-08-formal-gate.spec.ts
git diff --cached --check
git commit -m "feat: streamline teacher score entry"
```

---

### Task 3: Revise formal acceptance and run browser regression

**Files:**
- Modify: `docs/user-testing/checkpoints/P2-08-sample-page-formal.md`

**Interfaces:**
- Consumes: built SPA, P2-08 loopback demo server at `127.0.0.1:4188`, and existing exact-one-request counters.
- Produces: a versioned user checklist matching the browser-proven behavior and full affected browser regression evidence.

- [x] **Step 1: Rewrite affected acceptance steps**

Update `P2-08-sample-page-formal.md` so its visible shortcut list contains `J/K`, `/`, `Z`, `+/-`, `Enter` and no Shift+Enter or `R`. Replace the old confirmation steps with:

```text
单击“最终得分”框，直接键入一个新分数。旧分数被完整覆盖，不会追加成两位数。
光标仍在最终得分框内时按 Enter。只提交一次，并进入下一位学生。
在另一条有效草稿上把焦点移到答卷后按 Enter。只提交一次，并进入下一位学生。
在新草稿上按 Shift+Enter。不得提交、不得跳转；随后可用普通 Enter 完成提交。
```

Update invalid-score and failure-mode steps to use ordinary Enter only. Preserve the existing anonymous-data, five-viewport, loading/empty/error and no-`R` gates.

- [x] **Step 2: Run browser regression and verify GREEN**

Run from `frontend/`:

```powershell
npx playwright test e2e/review-scoring.spec.ts --workers=1
npm run e2e:p2-08 -- --workers=1
```

Expected: both suites PASS; the formal suite still covers all five desktop viewports.

- [x] **Step 3: Commit acceptance wording**

```powershell
git add docs/user-testing/checkpoints/P2-08-sample-page-formal.md
git diff --cached --check
git commit -m "test: align P2-08 acceptance with teacher workflow"
```

---

### Task 4: Complete validation, fresh-pass review and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-teacher-score-entry-implementation.md`

**Interfaces:**
- Consumes: final source commit, unchanged dependency/config baseline, focused RED/GREEN evidence, and immutable real-data/stash baselines.
- Produces: a new reviewed source SHA, validator-compatible `waiting_user` state, and a revised formal acceptance handoff.

- [x] **Step 1: Run affected automated validation**

Run from `frontend/`:

```powershell
npm test -- --run src/__tests__/review-queue-view.spec.ts src/__tests__/review-shortcut-guide.spec.ts src/__tests__/review-scoring-inspector.spec.ts
npm test
npm run lint
npm run typecheck
npm run build
npx playwright test e2e/review-scoring.spec.ts --workers=1
npm run e2e:p2-08 -- --workers=1
```

Run from the worktree root:

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
& (Join-Path $repoRoot 'runtime/python/python.exe') -m pytest tests/test_review_api_routes.py tests/test_review_service.py -q
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: all commands PASS; `user_data` remains clean in the feature worktree.

- [x] **Step 2: Recheck immutable root data and stash evidence read-only**

Recompute file length, UTC modification time and SHA-256 for the two real databases without opening them. Expected values remain:

```text
grading_system.db 2863104 2026-07-10T07:10:41.1221109Z 93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD
question_bank.db   3461120 2026-07-08T11:58:06.3320883Z E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8
```

Expected stash SHAs remain `85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2`.

- [x] **Step 3: Perform fresh-pass requirements and quality review**

Review the complete diff from `b6ab906872653dfc06ac222c1ea3de681a0930d9` through the new candidate, checking the confirmed design, protected textarea behavior, duplicate-submit guards, failure retention, no `R`, no backend change and no real-data access. Fix every Critical/Important issue with a new failing test and rerun affected validation.

- [ ] **Step 4: Anchor the new reviewed source**

Commit any final source/test/checklist changes, record the resulting source SHA, then make a plan-only commit that sets the canonical handoff to:

```markdown
**交接状态：** waiting_user
**功能提交：** 该 plan-only 提交之前最近一个源码/测试/验收清单提交的完整 `git rev-parse HEAD` 输出
**自动验证：** passed
**独立复审：** passed
**用户验收：** pending
**真实数据指纹：** unchanged
**夜间动作：** report_only
```

Run `tools/handoff_status.py` and require `ok=true` before opening the revised anonymous page to the user.

- [ ] **Step 5: Execute revised formal acceptance**

Use `docs/user-testing/checkpoints/P2-08-sample-page-formal.md`. Only an explicit user conclusion may set acceptance to passed. If any Blocker/Major is found, return the handoff to `in_progress`, reproduce with a failing test and fix before resuming acceptance.

- [ ] **Step 6: Continue the authorized integration workflow after acceptance**

After a checklist-only acceptance evidence commit and a plan-only `verified_pending_integration` anchor, use `superpowers:finishing-a-development-branch`, integrate one package on a fresh integration branch, run affected combination regression plus one complete `tools/smoke_check.py`, verify both real database fingerprints unchanged, then push the integration branch and merge through a GitHub PR. Never push `main` directly, force-push, stage `user_data/`, or delete an unmerged worktree/branch.
