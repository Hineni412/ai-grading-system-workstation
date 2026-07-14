# Phase 2 Frontend Source Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the P2-03～P2-08 Vue review experience around authoritative question-level batch review, with optional non-overlapping single-record deep review, while preserving data semantics, failure recovery, and security boundaries.

**Architecture:** Keep the existing review API, session store, request client, evidence viewer engine, and draft store where their behavior is traceable. Replace the placeholder navigation and permanent three-column composition with one truthful `/grading` workspace; split that workspace into mutually exclusive batch and deep-review modes. Add batch-capable API/store primitives, perform one atomic confirmation request per visible batch, retain explicit annotation retry state, and keep all test/demo writes inside fixed anonymous process memory.

**Tech Stack:** Vue 3.5, TypeScript 6, Pinia 3, Vue Router 5, Element Plus 2, Vitest 4, Playwright 1.61, Node 22/24, existing FastAPI review contracts.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-14-phase-2-frontend-source-recalibration-ux-design.md` exactly.
- The default workflow is question-level batch review; single-record detail is optional deep review only.
- The deep-review workspace must replace the batch body temporarily; it must never overlay or squeeze the batch grid.
- Existing Streamlit behavior, services, database contracts, and tests are authoritative; existing Vue behavior is only an audited reuse candidate.
- Do not implement or expose P2-09～P2-22 capabilities.
- Do not change the database Schema, grading rules, `needs_review` semantics, media ownership checks, or atomic transaction boundary.
- Do not read, write, copy, stage, commit, or stash real `user_data/`.
- Write requests are never automatically replayed; failures retain teacher drafts and context.
- A saved score remains successful when annotation rendering returns `retry_required`; retry is a separate explicit action.
- Support 1024×768, 1280×800, 1366×768, 1440×900, and 1920×1080.
- Follow `docs/ui/STYLE.md`; no gradient, decorative hero, permanent three-column shell, card ocean, or hidden unsafe confirmation shortcut.
- The non-package gate remains blocked until the versioned checklist records the reviewed SHA, no Blocker/Major findings, and explicit user `passed`.

## File Structure

### Create

- `frontend/src/components/review/ReviewAnswerSheet.vue` — one answer crop, score draft, validation, image retry, and deep-review entry.
- `frontend/src/components/review/ReviewBatchWorkspace.vue` — question strip, filters, contact sheet, pagination, and explicit batch action bar.
- `frontend/src/components/review/ReviewDeepWorkspace.vue` — non-overlapping deep-review state with evidence and teacher scoring.
- `frontend/src/__tests__/review-batch-workspace.spec.ts` — batch rendering, unchanged-score confirmation, validation, and accessibility tests.
- `frontend/src/__tests__/review-deep-workspace.spec.ts` — deep-review state, return behavior, and annotated-media coverage.

### Modify

- `frontend/src/navigation.ts` — expose only the truthful grading destination.
- `frontend/src/router/index.ts` — redirect `/` to `/grading`; remove business placeholder routes.
- `frontend/src/layouts/AppShell.vue` — remove permanent navigation/inspector panels; retain focus and dirty-draft protection.
- `frontend/src/components/shell/AppTopbar.vue` — product/page identity plus current-exam selector, without panel toggles.
- `frontend/src/api/review.ts` — default pending-only item reads and batch confirmation function.
- `frontend/src/stores/review-queue.ts` — 24-item batches, pending default, multi-item local confirmation, pending-count reconciliation.
- `frontend/src/stores/review-drafts.ts` — batch draft lookup and confirmed-key cleanup helpers.
- `frontend/src/views/ReviewQueueView.vue` — authoritative loading, batch submit, explicit annotation retry, mode switching, URL/context restoration.
- `frontend/src/components/review/ReviewEvidenceViewer.vue` — annotated front/back sources.
- `frontend/src/composables/use-evidence-viewer.ts` — annotated source type support.
- `frontend/src/components/review/ReviewScoringInspector.vue` — allow unchanged confirmation, remove unsourced activity history, emit single-confirm completion.
- `frontend/src/composables/review-shortcuts.ts` and `frontend/src/components/review/ReviewShortcutGuide.vue` — remove Enter-to-confirm and retain only safe contextual shortcuts.
- `frontend/src/styles/app-shell.css`, `review-queue.css`, `review-scoring.css`, `review-evidence.css` — approved continuous workspace and responsive deep-review layout.
- Existing unit tests under `frontend/src/__tests__/`, `frontend/src/api/__tests__/`, and `frontend/src/components/shell/__tests__/` — replace obsolete P2-03～P2-08 expectations.
- `frontend/demo/p2-08-server.mjs`, `frontend/demo/p2-08-server.test.mjs` — pending filter, multi-item atomic confirmation, and annotation retry.
- `frontend/e2e/p2-08-formal-gate.spec.ts`, review/shell/evidence E2E specs — source-recalibration workflow and five viewport checks.
- `frontend/package.json` — add clearly named recalibration demo/test aliases while retaining historical aliases.
- `docs/superpowers/packages/EXECUTION_INDEX.md` — record the actual non-package gate stage without changing package counts.

### Remove after references are eliminated

- `frontend/src/components/shell/AppNavigation.vue`
- `frontend/src/components/shell/SessionInspector.vue`
- `frontend/src/views/RoutePlaceholderView.vue`
- `frontend/src/components/review/ReviewQueuePanel.vue`

Existing `ReviewSelectionSummary.vue` remains reusable inside deep review unless the final component no longer imports it; remove it only if `rg` proves it has no callers.

---

### Task 1: Truthful shell and navigation

**Files:**
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`
- Modify: `frontend/src/components/shell/__tests__/app-shell.spec.ts`
- Modify: `frontend/src/__tests__/App.spec.ts`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layouts/AppShell.vue`
- Modify: `frontend/src/components/shell/AppTopbar.vue`
- Modify: `frontend/src/components/ApplicationErrorBoundary.vue`
- Modify: `frontend/src/views/NotFoundView.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Delete: `frontend/src/components/shell/AppNavigation.vue`
- Delete: `frontend/src/components/shell/SessionInspector.vue`
- Delete: `frontend/src/views/RoutePlaceholderView.vue`

**Interfaces:**
- Consumes: `useSessionStore().initialize()`, `selectSession(number | null)`, `useReviewDraftStore().hasDirtyDrafts`.
- Produces: `reviewRouteDefinition`, root redirect `/grading`, `#main-workspace`, and topbar `#current-session` used by all later tasks.

- [ ] **Step 1: Write failing shell and router tests**

Replace seven-entry and panel-toggle expectations with assertions equivalent to:

```ts
expect(navigationItems.map((item) => [item.id, item.path, item.label])).toEqual([
  ['grading', '/grading', '评分复核'],
])
expect(router.resolve('/').redirectedFrom).toBeUndefined()
await router.push('/')
expect(router.currentRoute.value.path).toBe('/grading')
expect(wrapper.find('[data-testid="app-navigation"]').exists()).toBe(false)
expect(wrapper.find('[data-testid="session-inspector"]').exists()).toBe(false)
expect(wrapper.get('[data-testid="app-topbar"]').text()).toContain('AI 阅卷系统')
expect(wrapper.get('#current-session').exists()).toBe(true)
```

Retain tests that route changes focus `#main-workspace h1`, session initialization runs once, stale session candidates remain governed by the session store, and `beforeunload` is prevented only with dirty drafts.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
npm.cmd run test -- src/__tests__/navigation-router.spec.ts src/components/shell/__tests__/app-shell.spec.ts src/__tests__/App.spec.ts
```

Expected: FAIL because the current shell exposes seven routes, redirects to `/workbench`, and renders permanent side panels.

- [ ] **Step 3: Implement the truthful route metadata**

Use this public shape in `navigation.ts`:

```ts
export interface WorkspaceRouteDefinition {
  id: 'grading'
  label: '评分复核'
  path: '/grading'
  title: '评分复核'
  description: '按题号批量比较并确认评分结果'
  breadcrumb: '评分复核'
}

export const reviewRouteDefinition: WorkspaceRouteDefinition = {
  id: 'grading',
  label: '评分复核',
  path: '/grading',
  title: '评分复核',
  description: '按题号批量比较并确认评分结果',
  breadcrumb: '评分复核',
}

export const navigationItems = [reviewRouteDefinition] as const
```

Define `/grading`, `/design-system`, and the existing 404 route explicitly. Redirect `/` to `/grading`; do not define the former placeholder business routes.

- [ ] **Step 4: Implement the single-workspace shell**

`AppShell.vue` must render exactly:

```vue
<div class="app-shell" data-testid="app-shell">
  <AppTopbar />
  <main id="main-workspace" class="main-workspace" tabindex="-1">
    <RouterView />
  </main>
</div>
```

Keep the existing route-heading focus watcher, `sessionStore.initialize()`, and dirty-draft `beforeunload` listener. `AppTopbar.vue` keeps the skip link, product label, route title, and API-validated exam selector; remove navigation/inspector props, events, buttons, and `aria-controls` references.

- [ ] **Step 5: Rewrite shell CSS without hidden panels**

Use a two-row grid (`var(--shell-topbar-height) minmax(0, 1fr)`), allow only `.main-workspace` to scroll, and ensure the topbar wraps safely at 1024 pixels. Remove geometry and transitions that reserve navigation or inspector widths.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: all selected tests PASS with no Vue warnings.

- [ ] **Step 7: Commit**

```powershell
git add -- frontend/src frontend/src/styles
git commit -m "refactor(frontend): expose truthful review workspace"
```

### Task 2: Batch API and queue state primitives

**Files:**
- Modify: `frontend/src/api/__tests__/review.spec.ts`
- Modify: `frontend/src/__tests__/review-queue-store.spec.ts`
- Modify: `frontend/src/__tests__/review-drafts-store.spec.ts`
- Modify: `frontend/src/api/review.ts`
- Modify: `frontend/src/stores/review-queue.ts`
- Modify: `frontend/src/stores/review-drafts.ts`

**Interfaces:**
- Consumes: existing `/api/sessions/{session}/review/questions/{question}/items` and `/confirm` contracts.
- Produces: `FetchReviewItemsOptions`, `confirmReviewItems()`, `REVIEW_PAGE_SIZE = 24`, `markItemsConfirmed()`, `adjustQuestionPendingCount()`, and `markConfirmedMany()`.

- [ ] **Step 1: Write failing API contract tests**

Add assertions equivalent to:

```ts
await fetchReviewItems(7, 'Q 1/甲')
await fetchReviewItems(7, 'Q 1/甲', { needsReviewOnly: false })
await confirmReviewItems(7, 'Q 1/甲', [
  { result_id: 11, detail_id: 21, score_awarded: 3 },
  { result_id: 12, detail_id: 22, score_awarded: 4, deduction_reason: '人工复核已确认' },
])

expect(request.mock.calls[0]?.[0]).toContain('needs_review_only=true')
expect(request.mock.calls[1]?.[0]).toContain('needs_review_only=false')
expect(request.mock.calls[2]?.[1]?.body).toEqual({ items: [
  { result_id: 11, detail_id: 21, score_awarded: 3 },
  { result_id: 12, detail_id: 22, score_awarded: 4, deduction_reason: '人工复核已确认' },
] })
```

Assert an empty confirmation array is rejected before any request.

- [ ] **Step 2: Write failing queue/draft tests**

Cover these exact state transitions:

```ts
expect(REVIEW_PAGE_SIZE).toBe(24)
expect(store.scope).toBe('needs_review')
store.markItemsConfirmed([
  { identity: items[0]!, scoreAwarded: 3, deductionReason: '人工复核已确认' },
  { identity: items[1]!, scoreAwarded: 4, deductionReason: '教师调整' },
])
expect(store.items.filter((entry) => entry.needs_review)).toHaveLength(0)
store.adjustQuestionPendingCount('Q1', -2)
expect(store.questions[0]?.needs_review_count).toBe(0)
draftStore.markConfirmedMany(['7:Q1:21', '7:Q1:22'])
expect(draftStore.drafts).toEqual({})
```

Also retain deterministic search/sort, cancellation, retained-content error, and latest-request-wins tests using the new loader options.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
npm.cmd run test -- src/api/__tests__/review.spec.ts src/__tests__/review-queue-store.spec.ts src/__tests__/review-drafts-store.spec.ts
```

Expected: FAIL because item reads currently force `false`, confirmation wraps one item, page size is 100, and multi-item helpers do not exist.

- [ ] **Step 4: Implement the API functions**

Add these signatures:

```ts
export interface FetchReviewItemsOptions {
  needsReviewOnly?: boolean
  signal?: AbortSignal
}

export async function fetchReviewItems(
  sessionId: number,
  questionId: string,
  options: FetchReviewItemsOptions = {},
): Promise<ReviewItem[]>

export async function confirmReviewItems(
  sessionId: number,
  questionId: string,
  inputs: readonly ReviewConfirmInput[],
  signal?: AbortSignal,
): Promise<ReviewConfirmResponse>
```

`fetchReviewItems` defaults `needsReviewOnly` to `true` and passes `options.signal`. `confirmReviewItems` trims and validates the question ID, rejects an empty array, sends one POST with `{ items: [...inputs] }`, and uses `isReviewConfirmResponse`. Keep `confirmReviewItem` as a one-item compatibility wrapper that calls `confirmReviewItems`.

- [ ] **Step 5: Implement batch queue and draft helpers**

Set `REVIEW_PAGE_SIZE = 24` and initial/reset scope to `needs_review`. Add:

```ts
export interface ConfirmedReviewPatch {
  identity: Pick<ReviewItem, 'session_id' | 'question_id' | 'detail_id'>
  scoreAwarded: number
  deductionReason: string
}

function markItemsConfirmed(patches: readonly ConfirmedReviewPatch[]): void
function adjustQuestionPendingCount(questionId: string, delta: number): void
```

Match all three identity fields before patching teacher-owned status. Clamp pending counts to `0..total_count`. Update `loadItems` to call the new API options and keep generation/abort behavior. Add `markConfirmedMany(keys: readonly string[])` to delete confirmed drafts in one immutable assignment.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the Step 3 command.

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add -- frontend/src/api frontend/src/stores frontend/src/__tests__
git commit -m "feat(frontend): add batch review contract primitives"
```

### Task 3: Question-level contact sheet and atomic batch confirmation

**Files:**
- Create: `frontend/src/components/review/ReviewAnswerSheet.vue`
- Create: `frontend/src/components/review/ReviewBatchWorkspace.vue`
- Create: `frontend/src/__tests__/review-batch-workspace.spec.ts`
- Modify: `frontend/src/__tests__/review-queue-view.spec.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/styles/review-queue.css`
- Delete: `frontend/src/components/review/ReviewQueuePanel.vue`

**Interfaces:**
- Consumes: `ReviewItem`, `ReviewQuestionSummary`, queue store filters/page, draft store, `confirmReviewItems()`.
- Produces: `confirm-batch(inputs, draftKeys)`, `open-detail(detailId)`, `retry-annotations`, and stable selectors `question-strip`, `review-answer-sheet`, `confirm-batch`.

- [ ] **Step 1: Write failing component tests**

Mount `ReviewBatchWorkspace` with two pending items and one confirmed item. Assert:

```ts
expect(wrapper.get('[data-testid="question-strip"]').text()).toContain('Q1')
expect(wrapper.findAll('[data-testid="review-answer-sheet"]')).toHaveLength(2)
expect(wrapper.get('[data-testid="confirm-batch"]').attributes('disabled')).toBeUndefined()
await wrapper.get('[data-testid="confirm-batch"]').trigger('click')
expect(wrapper.emitted('confirm-batch')?.[0]?.[0]).toEqual([
  { result_id: 101, detail_id: 1, score_awarded: 3 },
  { result_id: 103, detail_id: 3, score_awarded: 2.5 },
])
```

This explicitly proves unchanged valid scores can be confirmed. Add invalid, image-error/retry, search-label, scope-label, pagination, and deep-review button assertions. Pressing Enter inside a score input must move focus to the next score input and must not emit `confirm-batch`.

- [ ] **Step 2: Write failing view tests for one-request batch semantics**

Mock `confirmReviewItems` and assert one click sends one POST-equivalent call containing all visible pending entries, double click while pending still calls once, success clears matching drafts and decrements the question pending count, and a 422/500 rejection retains every score and the current batch.

Add a success response with two `retry_required` result IDs; assert the page says scores were saved and exposes an explicit annotation retry action rather than reporting submission failure.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
npm.cmd run test -- src/__tests__/review-batch-workspace.spec.ts src/__tests__/review-queue-view.spec.ts
```

Expected: FAIL because the contact sheet and batch confirmation path do not exist.

- [ ] **Step 4: Implement `ReviewAnswerSheet.vue`**

The component accepts `item: ReviewItem` and `position: number`, calls `draftStore.ensureDraft(item)`, shows student identity/class, risk reason/confidence, one controlled crop image, score input with `/ max_score`, inline `scoreIssue`, and a labeled “深查” button. Emit `open-detail` with `item.detail_id`. Keep an image load key and error state so “重新加载答卷图片” remounts the same service-provided URL.

The score input uses `data-score-position` and this safe keyboard rule:

```ts
function onScoreKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return
  event.preventDefault()
  emit('focus-next-score', props.position + 1)
}
```

It must never call a network function.

- [ ] **Step 5: Implement `ReviewBatchWorkspace.vue`**

Render, in order:

1. `question-strip`: all server summaries with question ID, pending/total count, max score, and current `aria-current`.
2. Search, scope, sort, and pagination controls with visible labels.
3. A continuous `review-contact-sheet` containing the current 24-item page.
4. A sticky action bar with visible batch count, validation summary, and one `confirm-batch` button.

Build submission payload only from rendered entries where `needs_review === true`; read their drafts, validate every score, and emit the full input array plus draft keys. Do not require `draft.dirty`. If any rendered item is invalid, focus the first invalid score field and emit nothing.

- [ ] **Step 6: Refactor `ReviewQueueView.vue` for batch orchestration**

Keep current session/question request generations, abort handling, retained-content feedback, and validated URL query. Replace row selection/detail-first rendering with `ReviewBatchWorkspace` as the default body.

Implement one guarded batch submit:

```ts
if (batchSubmitting.value || inputs.length === 0) return
batchSubmitting.value = true
const response = await confirmReviewItems(sessionId, questionId, inputs)
reviewStore.markItemsConfirmed(confirmedPatches)
reviewStore.adjustQuestionPendingCount(questionId, -inputs.length)
draftStore.markConfirmedMany(draftKeys)
annotationRetryInputs.value = inputs.filter((input) =>
  retryResultIds.has(resultIdByDetail.get(input.detail_id) ?? -1),
)
```

On failure, set a sanitized error notification and do not mutate queue or drafts. On success, reconcile the current page locally before any refresh. If the current question reaches zero pending, choose the next server-ordered question with pending work; otherwise stay on the current question. Never confirm non-rendered pages.

Explicit annotation retry resubmits only the stored inputs whose result IDs returned `retry_required`; keep the retry action visible until all outcomes succeed.

- [ ] **Step 7: Implement contact-sheet CSS**

Use thin ruled dividers, no per-item shadow, 2 columns at 1024/1280, 3 at 1366/1440, and 4 at 1920. Keep score controls and deep-review buttons visible without horizontal scrolling. The batch action bar may be sticky inside the workspace but must not cover the final answer row; reserve matching block padding.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run the Step 3 command.

Expected: all selected tests PASS with no unhandled promise or accessibility warnings.

- [ ] **Step 9: Commit**

```powershell
git add -- frontend/src/components/review frontend/src/views/ReviewQueueView.vue frontend/src/styles/review-queue.css frontend/src/__tests__
git commit -m "feat(frontend): make question batch review the default"
```

### Task 4: Non-overlapping deep review and complete evidence

**Files:**
- Create: `frontend/src/components/review/ReviewDeepWorkspace.vue`
- Create: `frontend/src/__tests__/review-deep-workspace.spec.ts`
- Modify: `frontend/src/components/review/ReviewEvidenceViewer.vue`
- Modify: `frontend/src/composables/use-evidence-viewer.ts`
- Modify: `frontend/src/components/review/ReviewScoringInspector.vue`
- Modify: `frontend/src/__tests__/review-evidence-viewer.spec.ts`
- Modify: `frontend/src/__tests__/evidence-viewer-state.spec.ts`
- Modify: `frontend/src/__tests__/review-scoring-inspector.spec.ts`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/styles/review-evidence.css`
- Modify: `frontend/src/styles/review-scoring.css`

**Interfaces:**
- Consumes: current queue selection, all five `ReviewMediaLinks`, rubric fetch, one-item compatibility confirmation.
- Produces: `ReviewDeepWorkspace` events `back` and `confirmed`; evidence sources `crop | original_front | original_back | annotated_front | annotated_back`.

- [ ] **Step 1: Write failing evidence and scoring tests**

Assert five source controls exist and each controlled URL becomes the one active image. Retain stale-load, retry-same-URL, pan/zoom/rotate, neighbor crop preload, and one-active-image tests.

Change scoring assertions to:

```ts
expect(host.textContent).not.toContain('活动记录')
expect(host.textContent).not.toContain('当前接口暂未提供历史活动列表')
expect(confirmButton.disabled).toBe(false) // valid unchanged score
scoreInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
expect(confirmReviewItem).not.toHaveBeenCalled()
```

Keep tests for invalid boundaries, failure-retained draft, stale-context isolation, successful single confirmation, and refresh failure not reversing saved state.

- [ ] **Step 2: Write failing deep-workspace tests**

Assert deep review renders a clear “返回 Q1 批量复核” button, evidence and scoring regions side by side in markup, emits `back`, and contains no dialog/drawer/overlay role. In the view test, opening deep review must hide the contact sheet; returning must restore the same question, search, scope, sort, page, selected detail, and draft values.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
npm.cmd run test -- src/__tests__/review-deep-workspace.spec.ts src/__tests__/review-evidence-viewer.spec.ts src/__tests__/evidence-viewer-state.spec.ts src/__tests__/review-scoring-inspector.spec.ts src/__tests__/review-queue-view.spec.ts
```

Expected: FAIL because annotated sources and the separate deep workspace do not exist, and unchanged scoring is disabled.

- [ ] **Step 4: Add annotated evidence sources**

Extend `EvidenceSource` and the viewer mappings with:

```ts
{ value: 'annotated_front', label: '正面标注图' },
{ value: 'annotated_back', label: '背面标注图' },
```

Map only to `item.media.annotated_front_url` and `item.media.annotated_back_url`. Source changes reset transform and load generation exactly like existing original pages.

- [ ] **Step 5: Correct single-record scoring semantics**

Remove `!currentDraft.value.dirty` from `submitDisabled` and remove the “请先修改” disabled reason. Delete the activity-history section. Enter in the score input prevents accidental form submission but does not call confirmation. Add:

```ts
const emit = defineEmits<{ confirmed: [detailId: number] }>()
```

After a successful confirmation of the still-active submitted context, emit `confirmed` so the deep workspace can return. Keep the no-auto-retry and sanitized notification behavior.

- [ ] **Step 6: Implement `ReviewDeepWorkspace.vue` and view mode switching**

The component renders a normal in-flow header with the back button, then a `review-deep-workspace` grid containing `ReviewEvidenceViewer` and `ReviewScoringInspector`. It is not an `aside` overlay, dialog, drawer, or fixed-position element.

In `ReviewQueueView.vue`, use `mode: Ref<'batch' | 'deep'>`. `openDeepReview(detailId)` records the batch scroll offset, selects the detail, sets `mode = 'deep'`, and syncs `detail` into the validated URL. `closeDeepReview()` removes only the detail query, sets batch mode, waits for render, and restores the recorded scroll offset. Search/scope/sort/page/drafts stay in Pinia and are never reset by the mode change.

- [ ] **Step 7: Implement responsive deep-review CSS**

At widths with enough room, use two in-flow columns: evidence `minmax(0, 1.25fr)` and scoring `minmax(320px, .75fr)`. At the approved compact threshold, stack evidence then scoring. Do not use `position: absolute`, negative margins, overlay masks, or a grid column containing the batch contact sheet.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run the Step 3 command.

Expected: all selected tests PASS.

- [ ] **Step 9: Commit**

```powershell
git add -- frontend/src
git commit -m "feat(frontend): add optional non-overlapping deep review"
```

### Task 5: Safe shortcuts, controlled demo, and browser gate

**Files:**
- Modify: `frontend/src/composables/review-shortcuts.ts`
- Modify: `frontend/src/components/review/ReviewShortcutGuide.vue`
- Modify: `frontend/src/__tests__/review-shortcuts.spec.ts`
- Modify: `frontend/src/__tests__/review-shortcut-guide.spec.ts`
- Modify: `frontend/demo/p2-08-server.mjs`
- Modify: `frontend/demo/p2-08-server.test.mjs`
- Modify: `frontend/e2e/p2-08-formal-gate.spec.ts`
- Modify: `frontend/e2e/review-queue.spec.ts`
- Modify: `frontend/e2e/review-evidence.spec.ts`
- Modify: `frontend/e2e/review-scoring.spec.ts`
- Modify: `frontend/e2e/app-shell.spec.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: fixed `frontend/demo/p2-08-dataset.json`; no production data or secrets.
- Produces: anonymous loopback demo supporting pending/all reads, atomic multi-item confirms, explicit failure modes, reset, and five-viewport evidence.

- [ ] **Step 1: Write failing shortcut tests**

Assert `/` focuses search only in batch mode, J/K changes the active answer or deep-review record only outside protected controls, viewer commands remain scoped to the focused deep viewer, and Enter is absent from the global confirmation command union and guide. No shortcut may emit batch confirmation.

- [ ] **Step 2: Write failing demo-server tests**

Add an item-filter assertion for both `needs_review_only=true` and `false`. Post two valid items and assert both mutate together. Post one valid plus one invalid item and assert status 422 and neither item changes. Assert duplicate detail IDs are rejected. In retry mode, return one annotation outcome per affected result without reverting score changes.

Use only fixed dataset identities and runtime clones:

```js
const before = structuredClone(state.items)
const validated = validateAllSubmittedItems(body.items, sessionId, questionId, state.items)
if (!validated.ok) return sendJson(response, 422, { message: '匿名验收校验失败' })
for (const entry of validated.items) applyRuntimeConfirmation(entry)
```

- [ ] **Step 3: Run unit and demo tests to verify RED**

```powershell
npm.cmd run test -- src/__tests__/review-shortcuts.spec.ts src/__tests__/review-shortcut-guide.spec.ts
npm.cmd run demo:test
```

Expected: FAIL because Enter confirmation and one-item-only demo confirmation still exist.

- [ ] **Step 4: Implement safe shortcuts and atomic anonymous demo**

Remove `confirm-next` from the shortcut bus and guide. Keep the existing protected-target checks. Parse `needs_review_only` as `true` only when the query value is `true`; false returns all question items.

Demo confirmation must validate the non-empty array, unique detail IDs, exact session/result/detail/question ownership, finite score, `0 <= score <= max_score`, and all entries before mutating any runtime item. It must never open a database or production path.

Add package aliases:

```json
"demo:phase2-recalibration": "node demo/p2-08-server.mjs",
"e2e:phase2-recalibration": "npm run build && playwright test --config playwright.p2-08.config.ts"
```

Keep `demo:p2-08` and `e2e:p2-08` as compatibility aliases.

- [ ] **Step 5: Update browser tests around the approved workflow**

The formal gate must prove:

- `/grading` is the only active business destination.
- Q1 batch contact sheet is the initial authenticated-by-context view.
- One explicit click confirms at least two unchanged/changed valid scores in exactly one request.
- An invalid score blocks the whole request and preserves all drafts.
- Confirm failure preserves batch state and writes are not auto-replayed.
- Annotation retry reports “score saved” and succeeds only after explicit retry.
- Deep review replaces the batch body, shows crop/original/annotated evidence, and returns to the exact batch context.
- Refresh before confirmation warns about dirty drafts; successful demo state persists in process memory and reset restores the dataset.
- Loading, retained-content error, first-load error, empty, image error, and no-pending states remain actionable.
- Each approved viewport has no horizontal document overflow, clipped primary action, overlap, or browser console error.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
npm.cmd run test -- src/__tests__/review-shortcuts.spec.ts src/__tests__/review-shortcut-guide.spec.ts
npm.cmd run demo:test
npm.cmd run e2e:phase2-recalibration
```

Expected: unit/demo tests PASS; Playwright builds successfully and all recalibration scenarios PASS in Chromium.

- [ ] **Step 7: Commit**

```powershell
git add -- frontend
git commit -m "test(frontend): verify recalibrated review workflow"
```

### Task 6: Full verification, independent review, and versioned acceptance handoff

**Files:**
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Create after review: `docs/user-testing/checkpoints/phase-2-frontend-source-recalibration-2026-07-14.md`
- Source template: `docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md`

**Interfaces:**
- Consumes: final reviewed feature SHA, verified demo start/stop method, root real-database read-only fingerprints.
- Produces: verified feature branch, independent-review findings/fixes, and a `pending` versioned user checklist bound to the exact SHA.

- [ ] **Step 1: Run static and complete frontend verification**

```powershell
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test
npm.cmd run build
npm.cmd run demo:test
npm.cmd run e2e:phase2-recalibration
```

Expected: every command exits 0; Vitest reports no failed files/tests; production build completes; demo and Playwright gates pass.

- [ ] **Step 2: Run repository-level affected regression and quick smoke**

```powershell
runtime\python\python.exe -m pytest tests\test_review_application_service.py tests\test_manual_review_atomic.py tests\test_api_review_routes.py tests\test_review_media_service.py tests\api_e2e\test_five_flow.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

Expected: all focused backend tests PASS and quick smoke exits 0. These checks must operate on test fixtures or isolated temporary databases only.

- [ ] **Step 3: Perform manual in-app browser verification**

Start the fixed anonymous demo on loopback, inspect all five required viewports, exercise batch confirmation, invalid input, network/service failure, annotation retry, deep review, back-context restoration, media failure/retry, keyboard focus, and reduced-motion behavior. Capture only anonymous evidence. Stop the demo and verify its port is released.

- [ ] **Step 4: Request independent review**

Provide the reviewer with the approved design path, implementation-plan path, base SHA `f5d5a06`, final feature SHA, and diff. Require separate checks for:

1. business capability and result equivalence;
2. batch atomicity and unchanged-score confirmation;
3. failure recovery and no automatic write replay;
4. media ownership/source safety and no real data access;
5. no P2-09～P2-22 scope expansion;
6. responsive layout, no overlap, keyboard and accessible labels;
7. test omissions or assertions that could pass falsely.

Verify each reported finding against the code before changing it. Fix valid findings with a failing test first, rerun affected tests, and commit fixes separately.

- [ ] **Step 5: Re-run fresh verification after review fixes**

Repeat Steps 1 and 2, plus every browser scenario affected by a fix. Do not reuse earlier results if the code SHA changed.

- [ ] **Step 6: Verify real data stayed untouched**

From the root working copy, recompute size, UTC modification time, and SHA-256 for both real databases and compare with the recorded pre-work values:

```text
grading_system.db  2863104 bytes  2026-07-10T07:10:41.1221109Z  93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD
question_bank.db   3461120 bytes  2026-07-08T11:58:06.3320883Z  E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8
```

Expected: all three fields match for both files and `git status --short -- user_data` is empty in the feature worktree.

- [ ] **Step 7: Generate the versioned user checklist only now**

Copy the dedicated template to `docs/user-testing/checkpoints/phase-2-frontend-source-recalibration-2026-07-14.md`. Fill it with the exact 40-character reviewed SHA from `git rev-parse HEAD`, the verified loopback start command/address, visible anonymous marker, stop command, browser/window sizes, reset action, database pre/post fingerprints, and numbered actions matching the implemented UI.

Keep:

```text
状态：pending
结果：pending
业务等价结论：pending
UX 改善结论：pending
用户明确结论：pending
```

Do not mark any acceptance field passed before the user performs the list and explicitly confirms both business equivalence and UX improvement.

- [ ] **Step 8: Update the execution index and commit the reviewed handoff**

While user acceptance is pending, set the non-package gate wording to “业务溯源、前端修正、自动验证和独立复审已完成；等待版本化用户验收”. Keep all P2-09～P2-22 blockers and package counts unchanged.

```powershell
git add -- docs/superpowers/packages/EXECUTION_INDEX.md docs/user-testing/checkpoints/phase-2-frontend-source-recalibration-2026-07-14.md
git commit -m "docs: prepare Phase 2 recalibration acceptance"
```

- [ ] **Step 9: Stop for user acceptance**

Present the numbered checklist in plain language. Do not merge, push the final gate result, or unblock later packages until the user explicitly returns `passed`. If the user reports Blocker/Major, preserve the worktree and demo evidence, diagnose systematically, fix with TDD, repeat independent review and fresh verification, and issue an updated checklist SHA.

### Task 7: Integration and PR after explicit user `passed`

**Files:**
- Modify: `docs/user-testing/checkpoints/phase-2-frontend-source-recalibration-2026-07-14.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`

**Interfaces:**
- Consumes: explicit user `passed`, reviewed feature SHA, current `origin/main`.
- Produces: accepted checklist result, integration verification, GitHub PR merge, and synchronized `origin/main`/local main/worktrees.

- [ ] **Step 1: Record only the user’s actual acceptance result**

Update the checklist with user feedback, business equivalence, UX improvement, final `passed`, post-test database fingerprints, and the non-package acceptance block. Update the index gate result to passed and remove only the source-recalibration blocker; preserve P2-09’s separate Phase 1 dependency.

- [ ] **Step 2: Commit the acceptance record on the feature branch**

```powershell
git add -- docs/user-testing/checkpoints/phase-2-frontend-source-recalibration-2026-07-14.md docs/superpowers/packages/EXECUTION_INDEX.md
git commit -m "docs: record Phase 2 recalibration acceptance"
```

- [ ] **Step 3: Integrate from the latest remote baseline**

Fetch `origin`, create or update the authorized integration branch from current `origin/main`, merge the complete feature branch one package/gate at a time, resolve shared documentation conflicts by preserving newer mainline facts, and verify no real `user_data/` path is staged.

- [ ] **Step 4: Run integration verification**

Run the affected backend regression, complete frontend verification, recalibration Playwright gate, and full `runtime\python\python.exe tools\smoke_check.py` because the final integrated SHA differs from the feature verification SHA. Recompute both real database fingerprints after the run.

- [ ] **Step 5: Push integration, open PR, merge, and synchronize**

Push the integration branch, open a ready PR to `main`, wait for required checks, merge through GitHub, then fetch and fast-forward local `main` to `origin/main`. Synchronize remaining active worktrees to the new baseline without deleting branches or worktrees that retain unique commits or local data.

- [ ] **Step 6: Final safety evidence**

Confirm the merged commit contains the accepted checklist and index state, all required checks are green, root `main` equals `origin/main`, the feature/integration branches are merged according to `git branch --merged origin/main`, and both real database fingerprints still match. Only then report the gate complete.
