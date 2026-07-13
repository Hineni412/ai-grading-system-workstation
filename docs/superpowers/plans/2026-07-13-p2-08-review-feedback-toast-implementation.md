# P2-08 Review Feedback Toast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评分确认结果从右栏内部顶部改成始终可见、非阻塞且可关闭的右下角浮动通知，使教师在最终得分区域无需滚动即可看到失败、警告和成功结果。

**Architecture:** 新增一个只负责展示和生命周期的 `ReviewFeedbackToast.vue`，使用 Vue `Teleport` 挂到 `body`，由 `ReviewScoringInspector.vue` 继续提供现有安全文案与语义类型。失败/警告保持到关闭，成功用组件内 4 秒定时器自动关闭；样式沿用现有 tokens，并把浮层放在评分底部操作区上方。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Vitest 4.1.10 with jsdom, Playwright 1.61.1, existing P2-08 anonymous runtime and CSS Tokens.

## Global Constraints

- 权威设计为 `docs/superpowers/specs/2026-07-13-p2-08-review-feedback-toast-design.md`。
- 通知固定在浏览器视口右下方，不属于评分检查器内部滚动区；原顶部提示必须删除，不能重复显示。
- 失败和警告不会自动消失；成功在 4 秒后自动消失；所有通知都可手动关闭且同一时刻最多一个。
- 通知不使用遮罩、不自动聚焦、不改变最终得分、备注、当前记录或确认事实。
- 失败/警告使用 `role="alert"` 和 assertive live region；成功使用 `role="status"` 和 polite live region；关闭按钮名称为“关闭通知”。
- 1024×768 下浮层不得溢出视口、产生页面横向滚动或遮住“确认并下一份”按钮。
- 保留现有 Enter 确认并下一份、Shift+Enter 无动作、单次提交、失败保留草稿、批注警告和刷新失败语义。
- 不接入全局通知端口，不引入 Element Plus 命令式通知，不改后端、API、Schema、数据库、模型或真实 `user_data/`。
- 当前任务不派生子代理；在本会话使用 `superpowers:executing-plans` 执行，完成前使用 `superpowers:verification-before-completion`，集成前使用 `superpowers:finishing-a-development-branch`。

---

### Task 1: Specify the teleported toast lifecycle with RED tests

**Files:**
- Create: `frontend/src/components/review/ReviewFeedbackToast.vue`
- Create: `frontend/src/__tests__/review-feedback-toast.spec.ts`

**Interfaces:**
- Consumes: `message: string`, `tone: 'success' | 'warning' | 'error'`.
- Produces: one `dismiss` event, one `[data-testid="review-feedback-toast"]` teleported element, and deterministic 4000ms success dismissal.

- [ ] **Step 1: Write the teleported rendering and close RED test**

Create `review-feedback-toast.spec.ts` with a small reactive harness:

```ts
import { createApp, h, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ReviewFeedbackToast from '../components/review/ReviewFeedbackToast.vue'

type Tone = 'success' | 'warning' | 'error'

function mountToast(message = '确认失败，教师草稿已保留。', tone: Tone = 'error') {
  const state = reactive({ message, tone })
  const dismiss = vi.fn(() => { state.message = '' })
  const host = document.createElement('div')
  const app = createApp({
    setup: () => () => h(ReviewFeedbackToast, {
      message: state.message,
      tone: state.tone,
      onDismiss: dismiss,
    }),
  })
  app.mount(host)
  return { app, dismiss, host, state }
}

afterEach(() => {
  vi.useRealTimers()
  document.querySelectorAll('[data-testid="review-feedback-toast"]').forEach((node) => node.remove())
})

it('teleports one error alert to body and closes without focusing it', async () => {
  const { app, dismiss, host } = mountToast()
  const toast = document.body.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')!
  expect(toast).not.toBeNull()
  expect(host.contains(toast)).toBe(false)
  expect(toast.getAttribute('role')).toBe('alert')
  expect(toast.getAttribute('aria-live')).toBe('assertive')
  expect(toast).not.toBe(document.activeElement)
  toast.querySelector<HTMLButtonElement>('[aria-label="关闭通知"]')!.click()
  expect(dismiss).toHaveBeenCalledTimes(1)
  app.unmount()
})
```

- [ ] **Step 2: Write RED tests for timeout and replacement**

Add tests using fake timers:

```ts
it('dismisses success after 4000ms but keeps warning and error until closed', async () => {
  vi.useFakeTimers()
  const success = mountToast('教师最终分已确认。', 'success')
  await vi.advanceTimersByTimeAsync(3999)
  expect(success.dismiss).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(success.dismiss).toHaveBeenCalledTimes(1)
  success.app.unmount()

  for (const tone of ['warning', 'error'] as const) {
    const sticky = mountToast('需要教师处理。', tone)
    await vi.advanceTimersByTimeAsync(10000)
    expect(sticky.dismiss).not.toHaveBeenCalled()
    sticky.app.unmount()
  }
})

it('restarts the success timer when a new message replaces the old one', async () => {
  vi.useFakeTimers()
  const mounted = mountToast('第一次成功', 'success')
  await vi.advanceTimersByTimeAsync(3000)
  mounted.state.message = '第二次成功'
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(3999)
  expect(mounted.dismiss).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(mounted.dismiss).toHaveBeenCalledTimes(1)
  mounted.app.unmount()
})
```

- [ ] **Step 3: Run the new component test and verify RED**

Run from `frontend/`:

```powershell
npm test -- src/__tests__/review-feedback-toast.spec.ts
```

Expected: FAIL because `ReviewFeedbackToast.vue` does not exist. A module-not-found failure is the intended first RED result.

- [ ] **Step 4: Implement the minimal toast component**

Create `ReviewFeedbackToast.vue`:

```vue
<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'

const props = defineProps<{
  message: string
  tone: 'success' | 'warning' | 'error'
}>()
const emit = defineEmits<{ dismiss: [] }>()

let dismissTimer: ReturnType<typeof setTimeout> | null = null

function clearDismissTimer(): void {
  if (dismissTimer === null) return
  clearTimeout(dismissTimer)
  dismissTimer = null
}

function dismiss(): void {
  clearDismissTimer()
  emit('dismiss')
}

watch(
  () => [props.message, props.tone] as const,
  ([message, tone]) => {
    clearDismissTimer()
    if (message && tone === 'success') dismissTimer = setTimeout(dismiss, 4000)
  },
  { immediate: true },
)

onBeforeUnmount(clearDismissTimer)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="message"
      class="review-feedback-toast"
      :class="`review-feedback-toast--${tone}`"
      data-testid="review-feedback-toast"
      :role="tone === 'success' ? 'status' : 'alert'"
      :aria-live="tone === 'success' ? 'polite' : 'assertive'"
      aria-atomic="true"
    >
      <p>{{ message }}</p>
      <button type="button" aria-label="关闭通知" @click="dismiss">×</button>
    </div>
  </Teleport>
</template>
```

- [ ] **Step 5: Run the component test and verify GREEN**

Run:

```powershell
npm test -- src/__tests__/review-feedback-toast.spec.ts
```

Expected: all toast lifecycle tests PASS without timer leaks or Vue warnings.

- [ ] **Step 6: Commit the isolated component**

```powershell
git add frontend/src/components/review/ReviewFeedbackToast.vue frontend/src/__tests__/review-feedback-toast.spec.ts
git diff --cached --check
git commit -m "feat: add review feedback toast"
```

---

### Task 2: Integrate the toast into scoring with RED → GREEN

**Files:**
- Modify: `frontend/src/components/review/ReviewScoringInspector.vue`
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/src/styles/review-scoring.css`
- Modify: `frontend/e2e/review-scoring.spec.ts`
- Modify: `frontend/e2e/p2-08-formal-gate.spec.ts`

**Interfaces:**
- Consumes: `ReviewFeedbackToast` props and `dismiss` event; existing `feedback` and `feedbackTone` refs.
- Produces: one viewport-fixed toast outside `[data-testid="scoring-scroll-region"]`, preserving all current confirmation state transitions.

- [ ] **Step 1: Write the integration RED test before changing the inspector**

Update the failure test in `review-scoring-inspector.spec.ts` to assert the requested placement and dismiss behavior:

```ts
const toast = await vi.waitFor(() => {
  const found = document.body.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')
  expect(found).not.toBeNull()
  return found!
})
expect(toast.textContent).toContain('确认失败，教师草稿已保留')
expect(host.querySelector('[data-testid="scoring-scroll-region"]')?.contains(toast)).toBe(false)
toast.querySelector<HTMLButtonElement>('[aria-label="关闭通知"]')!.click()
await nextTick()
expect(document.body.querySelector('[data-testid="review-feedback-toast"]')).toBeNull()
expect(useReviewDraftStore(pinia).drafts['7:Q1:21']?.scoreText).toBe('4')
```

Replace all existing `host.textContent` assertions for confirmation feedback with queries against the teleported toast, while keeping score, queue and draft assertions unchanged. Add an assertion that `.review-scoring-feedback` no longer exists in the internal scroll region.

In the 422/500 Playwright flow, scroll the scoring panel to its bottom before submitting and assert:

```ts
const scoringScroll = page.getByTestId('scoring-scroll-region')
await scoringScroll.evaluate((element) => { element.scrollTop = element.scrollHeight })
await score.press('Enter')
const toast = page.getByTestId('review-feedback-toast')
await expect(toast).toBeVisible()
await expect(toast).toContainText('确认失败，教师草稿已保留')
expect(await toast.evaluate((element) => element.parentElement === document.body)).toBe(true)
await page.waitForTimeout(4100)
await expect(toast).toBeVisible()
```

Add one 1024×768 formal-gate geometry assertion:

```ts
const geometry = await page.evaluate(() => {
  const toast = document.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')!
  const footer = document.querySelector<HTMLElement>('[data-testid="scoring-footer"]')!
  const toastRect = toast.getBoundingClientRect()
  const footerRect = footer.getBoundingClientRect()
  return {
    insideViewport: toastRect.left >= 0 && toastRect.right <= innerWidth &&
      toastRect.top >= 0 && toastRect.bottom <= innerHeight,
    overlapsFooter: toastRect.left < footerRect.right && toastRect.right > footerRect.left &&
      toastRect.top < footerRect.bottom && toastRect.bottom > footerRect.top,
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }
})
expect(geometry).toEqual({ insideViewport: true, overlapsFooter: false, documentOverflow: 0 })
```

- [ ] **Step 2: Run scoring tests and verify RED**

Run:

```powershell
npm test -- src/__tests__/review-scoring-inspector.spec.ts src/__tests__/review-feedback-toast.spec.ts
npx playwright test e2e/review-scoring.spec.ts --grep "422|500" --workers=1
npm run e2e:p2-08 -- --grep "failure retains" --workers=1
```

Expected: tests FAIL because feedback is still rendered as `.review-scoring-feedback` inside the scroll region and is outside the viewport after scrolling down.

- [ ] **Step 3: Replace the internal feedback paragraph with the toast**

Import the component:

```ts
import ReviewFeedbackToast from './ReviewFeedbackToast.vue'
```

Inside the inspector template but outside `.review-scoring-inspector__scroll`, render:

```vue
<ReviewFeedbackToast
  :message="feedback"
  :tone="feedbackTone"
  @dismiss="feedback = ''"
/>
```

Delete the old `<p v-if="feedback" class="review-scoring-feedback">` block. Do not change the existing feedback strings, submit guards or `feedbackTone` assignments.

- [ ] **Step 4: Replace old feedback CSS with the viewport-fixed styles**

Remove `.review-scoring-feedback` from the shared padding selector and delete its four old rules. Add:

```css
.review-feedback-toast {
  position: fixed;
  z-index: calc(var(--shell-skip-link-z-index) + 1);
  inset-inline-end: var(--space-6);
  inset-block-end: calc(
    var(--control-height-large) + var(--space-9) + var(--space-6)
  );
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-3);
  width: min(360px, calc(100vw - var(--space-8)));
  padding: var(--space-4);
  border: var(--border-width) solid currentColor;
  border-radius: var(--radius-overlay);
  box-shadow: var(--shadow-overlay);
  font-size: var(--font-size-dense);
}

.review-feedback-toast p { margin: 0; overflow-wrap: anywhere; }
.review-feedback-toast button {
  min-width: var(--control-height-small);
  min-height: var(--control-height-small);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
}
.review-feedback-toast button:focus-visible {
  outline: var(--border-width) solid currentColor;
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-ring);
}
.review-feedback-toast--success { background: var(--color-success-subtle); color: var(--color-success); }
.review-feedback-toast--warning { background: var(--color-warning-subtle); color: var(--color-warning); }
.review-feedback-toast--error { background: var(--color-danger-subtle); color: var(--color-danger); }

@media (max-width: 1100px) {
  .review-feedback-toast { inset-inline-end: var(--space-4); }
}
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
npm test -- src/__tests__/review-feedback-toast.spec.ts src/__tests__/review-scoring-inspector.spec.ts
npx playwright test e2e/review-scoring.spec.ts --grep "422|500" --workers=1
npm run e2e:p2-08 -- --grep "failure retains" --workers=1
npm run lint
npm run typecheck
```

Expected: component and inspector tests PASS; lint and typecheck exit 0.

- [ ] **Step 6: Commit the scoring integration**

```powershell
git add frontend/src/components/review/ReviewScoringInspector.vue frontend/src/__tests__/review-scoring-inspector.spec.ts frontend/src/styles/review-scoring.css frontend/e2e/review-scoring.spec.ts frontend/e2e/p2-08-formal-gate.spec.ts
git diff --cached --check
git commit -m "fix: keep review feedback visible"
```

---

### Task 3: Prove viewport visibility and update formal acceptance

**Files:**
- Modify: `docs/user-testing/checkpoints/P2-08-sample-page-formal.md`

**Interfaces:**
- Consumes: fixed anonymous 422/500/retry modes and the browser-proven teleported `[data-testid="review-feedback-toast"]`.
- Produces: a formal user checklist matching the reviewed popup behavior and full affected browser regression evidence.

- [ ] **Step 1: Update the formal checklist wording**

Revise steps 9–11 in `P2-08-sample-page-formal.md`:

```text
步骤 9：右栏保持滚动到底部，422 失败后页面右下角立即显示可关闭的浮动失败通知；不滚动也能看到；草稿、备注和当前学生保留。
步骤 10：500 失败仍在右下角显示一条浮动通知，新通知替换旧通知，不堆叠，也不自动重放请求。
步骤 11：确认成功且批注稍后刷新时，右下角显示保持到手动关闭的警告通知；不得把评分成功说成失败。
```

在步骤 13 增加：1024×768 下通知不超出屏幕、不产生横向滚动、不遮住“确认并下一份”。

- [ ] **Step 2: Run full affected browser suites and verify GREEN**

Run:

```powershell
npx playwright test e2e/review-scoring.spec.ts --workers=1
npm run e2e:p2-08 -- --workers=1
```

Expected: scoring suite and ten-test formal gate PASS; five required desktop viewports remain covered.

- [ ] **Step 3: Commit the checklist alignment**

```powershell
git add docs/user-testing/checkpoints/P2-08-sample-page-formal.md
git diff --cached --check
git commit -m "test: cover visible review feedback"
```

---

### Task 4: Validate, review and resume formal acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-sample-page-gate-implementation.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-08-review-feedback-toast-implementation.md`

**Interfaces:**
- Consumes: final source candidate, unchanged dependency/config baseline, RED/GREEN evidence and immutable real-data/stash baselines.
- Produces: a new full reviewed source SHA and validator-compatible `waiting_user` anchor for restarting at formal step 9.

- [ ] **Step 1: Run complete affected automated validation**

From `frontend/` run:

```powershell
npm test
npm run lint
npm run build
npx playwright test e2e/review-scoring.spec.ts --workers=1
npm run e2e:p2-08 -- --workers=1
npm run demo:test
```

From the worktree root run:

```powershell
$repoRoot = Split-Path -Parent (git rev-parse --path-format=absolute --git-common-dir)
& (Join-Path $repoRoot 'runtime/python/python.exe') -m pytest tests/test_api_review_routes.py tests/test_review_application_service.py -q
& (Join-Path $repoRoot 'runtime/python/python.exe') tools/smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: all commands PASS; `user_data` is clean in the feature worktree.

- [ ] **Step 2: Recheck immutable data and stash evidence read-only**

Recompute file length, UTC modification time and SHA-256 without opening the databases. Require the unchanged values:

```text
grading_system.db 2863104 2026-07-10T07:10:41.1221109Z 93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD
question_bank.db   3461120 2026-07-08T11:58:06.3320883Z E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8
```

Require stash SHAs `85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2`.

- [ ] **Step 3: Perform fresh-pass review**

Review the complete diff from reviewed source `e446e09cfb00e3ac06bd5af30b8bbbd0cf817714` through the new candidate. Check component timer cleanup, one-toast replacement, accessibility roles, 1024×768 geometry, no focus stealing, no duplicate internal feedback, error draft retention, unchanged Enter behavior, no backend changes and no real-data access. Fix every Critical/Important issue with a failing test and rerun affected validation.

- [ ] **Step 4: Anchor the reviewed source and reopen step 9**

After all source/test/checklist changes are committed, set the canonical handoff in a plan-only commit to `waiting_user`, record the direct parent full 40-character SHA, set automated validation and independent review to `passed`, user acceptance to `pending`, real-data fingerprint to `unchanged`, and nightly action to `report_only`. Validate with `tools/handoff_status.py` and require `ok=true`.

Restart the anonymous loopback service from the reviewed build, reset it, prepare 422 mode and open the page at `http://127.0.0.1:4188/grading?question=Q1&detail=1`. Ask the user to repeat formal step 9; only explicit user results may advance the checklist.

- [ ] **Step 5: Continue the authorized integration workflow after acceptance**

After all remaining formal steps pass, create a checklist-only acceptance evidence commit and a plan-only `verified_pending_integration` anchor. Then use `superpowers:finishing-a-development-branch`, integrate P2-08 on a fresh integration branch, run affected combination regression plus one complete `tools/smoke_check.py`, verify both real database fingerprints unchanged, push the integration branch and merge through a GitHub PR. Never push `main` directly, force-push, stage `user_data/`, or delete an unmerged worktree or branch.
