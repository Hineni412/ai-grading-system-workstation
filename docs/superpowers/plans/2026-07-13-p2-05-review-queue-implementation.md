# P2-05 Review Queue, Filters, and Continuous Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-05
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 532624a459a3a99d7806cde14c273f1df11257a0
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-05-review-queue-quick.md

**Goal:** 在 Vue 阅卷工作区实现只读的单题复核队列、搜索筛选、稳定排序、100 条分页、URL 恢复和上一/下一连续导航。

**Architecture:** 受控手写的 Review API 适配器通过 P2-04 `apiClient` 校验现有两个 GET 契约；页面专用 Pinia Store 负责请求世代、保留旧内容、过滤排序、分页和选择规则。`/grading` 页面只编排 URL、键盘和组件，App Shell、Session Store 与右侧 Session Inspector 保持不变，所有评分写入和媒体渲染留给后续包。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, Vue Router 5.1.0, native Fetch/AbortController, Vitest 4.1.10, Playwright 1.61.1, existing CSS Tokens, Python 3.12 repository guards.

## Global Constraints

- 实现必须符合 `docs/superpowers/specs/2026-07-13-p2-05-review-queue-design.md`、`docs/superpowers/packages/phase-2-execution-packages.md` 和 `docs/ui/STYLE.md`。
- 只调用 `GET /api/sessions/{session_id}/review/questions` 与 `GET /api/sessions/{session_id}/review/questions/{question_id}/items?needs_review_only=false`；禁止调用 confirm POST 或修改后端。
- `/grading` 使用现有 App Shell、顶部考试选择器和 Session Inspector；不新增第二套考试上下文、网络层、状态库或生产依赖，不修改 `package-lock.json`。
- 每页固定 100 条；默认风险优先；搜索只匹配姓名、学号和班级；筛选只消费后端 `needs_review`，不臆造新状态或置信度等级。
- URL 只保存已验证的 `question` 与 `detail`；搜索词、筛选和完整学生队列不进入 `localStorage`。
- `J`/`K` 只在非输入、非按钮、非可编辑焦点时切换；不实现 Enter、Shift+Enter、R、Z、缩放、评分草稿或确认。
- 页面必须覆盖未选择考试、加载、无题目、筛选空、首次失败和保留旧内容刷新失败；错误不得暴露原始响应、内部路径或堆栈。
- 只支持不低于 1024px 的 Windows 桌面浏览器；验证 1920×1080、1440×900、1366×768、1280×800、1024×768，不建设移动端抽屉。
- 页面样式只使用 `frontend/src/styles/tokens.css` 中已有 Token；不增加渐变、大圆角、头像、无意义卡片或每行操作按钮。
- 测试只使用 mock API 和生成数据；不得读取、修改、暂存、提交或 stash 真实 `user_data/`，不得调用真实模型。
- 执行模型为 T-H。每个新行为必须先有可见 RED，再有最小 GREEN；同一问题连续两次修复失败时安全停机。
- 实施前必须使用 `frontend-design`，实施过程必须使用 `test-driven-development`，完成声明前必须使用 `verification-before-completion`；本任务禁止自动派生子代理，除非用户另行明确授权。

---

### Task 1: Claim the package and add the strict Review read adapter

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-p2-05-review-queue-implementation.md`
- Create: `frontend/src/api/review.ts`
- Create: `frontend/src/api/__tests__/review.spec.ts`

**Interfaces:**
- Consumes: `apiClient.request<T>(path, { decode, signal })` from `frontend/src/api/client.ts`, and `isRecord`/`isNullableString` from `frontend/src/api/validation.ts`.
- Produces: `ReviewQuestionSummary`, `ReviewItem`, `ReviewMediaLinks`, `fetchReviewQuestions(sessionId, signal?)`, and `fetchReviewItems(sessionId, questionId, signal?)`.

- [ ] **Step 1: Record the immutable claim and baseline**

Before any source edit, read `git stash list --format=%H`, then add exactly one handoff block to this plan:

```markdown
<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-05
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
```

Run from the fresh implementation worktree:

```powershell
git status --short --branch
git merge-base --is-ancestor 532624a459a3a99d7806cde14c273f1df11257a0 HEAD
git status --short -- user_data
git diff --check
git add docs/superpowers/plans/2026-07-13-p2-05-review-queue-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P2-05 review queue"
```

Expected: the branch starts from the then-current `origin/main` containing this plan; this is the first first-parent commit after the merge base; the staged path list contains only this plan; `user_data` is clean. If the stash list differs from the two recorded baseline SHAs, stop instead of editing the block.

- [ ] **Step 2: Write failing adapter tests**

Create `frontend/src/api/__tests__/review.spec.ts` with injected `apiClient.request` spies and the exact public shapes:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../client'
import {
  fetchReviewItems,
  fetchReviewQuestions,
  isReviewItemListResponse,
  isReviewQuestionListResponse,
} from '../review'

const media = {
  crop_url: '/api/sessions/7/results/11/details/21/crop',
  original_front_url: '/api/sessions/7/results/11/pages/front',
  original_back_url: '/api/sessions/7/results/11/pages/back',
  annotated_front_url: '/api/sessions/7/results/11/pages/front?variant=annotated',
  annotated_back_url: '/api/sessions/7/results/11/pages/back?variant=annotated',
}

afterEach(() => vi.restoreAllMocks())

describe('review API contract', () => {
  it('accepts the current question and item contracts', () => {
    expect(isReviewQuestionListResponse({ items: [{ question_id: 'Q1', total_count: 2, needs_review_count: 1, max_score: 5 }], total: 1 })).toBe(true)
    expect(isReviewItemListResponse({ items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_code: 'S001', student_name: '测试学生', class_name: '七年级一班', score_awarded: 3, max_score: 5, deduction_reason: '步骤不完整', error_category: '需复核', error_summary: null, confidence_score: 72, needs_review: true, candidate_scores: [], metadata: {}, media }], total: 1 })).toBe(true)
  })

  it.each([
    { items: [], total: -1 },
    { items: [{ question_id: 'Q1', total_count: 1, needs_review_count: 2, max_score: 5 }], total: 1 },
    { items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_name: '学生', score_awarded: 3, max_score: 5, confidence_score: Number.NaN, needs_review: true, candidate_scores: [], metadata: {}, media }], total: 1 },
    { items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_name: '学生', score_awarded: 3, max_score: 5, confidence_score: null, needs_review: true, candidate_scores: [], metadata: {}, media: { ...media, crop_url: 'https://outside.invalid/private' } }], total: 1 },
  ])('rejects malformed or unsafe payload %#', (payload) => {
    expect(isReviewQuestionListResponse(payload) || isReviewItemListResponse(payload)).toBe(false)
  })

  it('uses the shared client, encodes the question, and requests all items', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [], total: 0 })
    await fetchReviewQuestions(7)
    await fetchReviewItems(7, 'Q 1/甲')
    expect(request.mock.calls[0]?.[0]).toBe('/api/sessions/7/review/questions')
    expect(request.mock.calls[1]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/items?needs_review_only=false')
  })
})
```

- [ ] **Step 3: Run RED**

Run from `frontend/`:

```powershell
npm run test -- src/api/__tests__/review.spec.ts --maxWorkers=1
```

Expected: FAIL because `../review` does not exist.

- [ ] **Step 4: Implement the strict adapter**

Create `frontend/src/api/review.ts` with these exact public types and validators:

```ts
import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

export interface ReviewQuestionSummary {
  question_id: string
  total_count: number
  needs_review_count: number
  max_score: number
}

export interface ReviewMediaLinks {
  crop_url: string
  original_front_url: string
  original_back_url: string
  annotated_front_url: string
  annotated_back_url: string
}

export interface ReviewItem {
  session_id: number
  result_id: number
  detail_id: number
  question_id: string
  student_code: string | null
  student_name: string
  class_name: string | null
  score_awarded: number
  max_score: number
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
  confidence_score: number | null
  needs_review: boolean
  candidate_scores: Record<string, unknown>[]
  metadata: Record<string, unknown>
  media: ReviewMediaLinks
}

export interface ReviewQuestionListResponse { items: ReviewQuestionSummary[]; total: number }
export interface ReviewItemListResponse { items: ReviewItem[]; total: number }

const isNonnegativeInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isApiUrl = (value: unknown): value is string => typeof value === 'string' && /^\/api\//.test(value)

function isReviewQuestion(value: unknown): value is ReviewQuestionSummary {
  return isRecord(value) && typeof value.question_id === 'string' && value.question_id.trim().length > 0 && isNonnegativeInteger(value.total_count) && isNonnegativeInteger(value.needs_review_count) && Number(value.needs_review_count) <= Number(value.total_count) && isFiniteNumber(value.max_score) && value.max_score >= 0
}

function isReviewMedia(value: unknown): value is ReviewMediaLinks {
  return isRecord(value) && isApiUrl(value.crop_url) && isApiUrl(value.original_front_url) && isApiUrl(value.original_back_url) && isApiUrl(value.annotated_front_url) && isApiUrl(value.annotated_back_url)
}

function isReviewItem(value: unknown): value is ReviewItem {
  return isRecord(value) && isNonnegativeInteger(value.session_id) && value.session_id > 0 && isNonnegativeInteger(value.result_id) && value.result_id > 0 && isNonnegativeInteger(value.detail_id) && value.detail_id > 0 && typeof value.question_id === 'string' && typeof value.student_name === 'string' && isNullableString(value.student_code) && isNullableString(value.class_name) && isFiniteNumber(value.score_awarded) && isFiniteNumber(value.max_score) && isNullableString(value.deduction_reason) && isNullableString(value.error_category) && isNullableString(value.error_summary) && (value.confidence_score === null || isFiniteNumber(value.confidence_score)) && typeof value.needs_review === 'boolean' && Array.isArray(value.candidate_scores) && value.candidate_scores.every(isRecord) && isRecord(value.metadata) && isReviewMedia(value.media)
}

export function isReviewQuestionListResponse(value: unknown): value is ReviewQuestionListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewQuestion) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

export function isReviewItemListResponse(value: unknown): value is ReviewItemListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewItem) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

export async function fetchReviewQuestions(sessionId: number, signal?: AbortSignal): Promise<ReviewQuestionSummary[]> {
  const payload = await apiClient.request(`/api/sessions/${sessionId}/review/questions`, { signal, decode: (value) => { if (!isReviewQuestionListResponse(value)) throw new Error('invalid review questions'); return value } })
  return payload.items
}

export async function fetchReviewItems(sessionId: number, questionId: string, signal?: AbortSignal): Promise<ReviewItem[]> {
  const encodedQuestion = encodeURIComponent(questionId)
  const payload = await apiClient.request(`/api/sessions/${sessionId}/review/questions/${encodedQuestion}/items?needs_review_only=false`, { signal, decode: (value) => { if (!isReviewItemListResponse(value)) throw new Error('invalid review items'); return value } })
  return payload.items
}
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
npm run test -- src/api/__tests__/review.spec.ts --maxWorkers=1
git add frontend/src/api/review.ts frontend/src/api/__tests__/review.spec.ts
git commit -m "feat: add typed review queue client"
```

Expected: adapter tests PASS; commit contains only the adapter and its tests.

---

### Task 2: Build deterministic queue state, filtering, pagination, and navigation

**Files:**
- Create: `frontend/src/stores/review-queue.ts`
- Create: `frontend/src/__tests__/review-queue-store.spec.ts`

**Interfaces:**
- Consumes: `ReviewQuestionSummary`, `ReviewItem`, `fetchReviewQuestions`, and `fetchReviewItems` from Task 1.
- Produces: `ReviewScope = 'all' | 'needs_review'`, `ReviewSort = 'risk' | 'student_code' | 'student_name'`, `REVIEW_PAGE_SIZE = 100`, and `useReviewQueueStore()` with load, filter, selection, pagination, navigation, and reset actions.

- [ ] **Step 1: Write failing store tests**

Create `frontend/src/__tests__/review-queue-store.spec.ts` with a generated fixture and direct action assertions:

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ReviewItem } from '../api/review'
import { REVIEW_PAGE_SIZE, useReviewQueueStore } from '../stores/review-queue'

const media = { crop_url: '/api/crop', original_front_url: '/api/front', original_back_url: '/api/back', annotated_front_url: '/api/front?variant=annotated', annotated_back_url: '/api/back?variant=annotated' }
const item = (index: number, overrides: Partial<ReviewItem> = {}): ReviewItem => ({ session_id: 7, result_id: index, detail_id: index, question_id: 'Q1', student_code: `S${String(index).padStart(4, '0')}`, student_name: `学生${index}`, class_name: index % 2 ? '七年级一班' : '七年级二班', score_awarded: 3, max_score: 5, deduction_reason: null, error_category: null, error_summary: null, confidence_score: 90, needs_review: false, candidate_scores: [], metadata: {}, media, ...overrides })

describe('review queue store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('sorts risk deterministically and searches only identity fields', () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(3), item(2, { needs_review: true, confidence_score: null }), item(1, { needs_review: true, confidence_score: 65, deduction_reason: '搜索不应命中这段原因' })])
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([1, 2, 3])
    store.setSearch('二班')
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([2])
    store.setSearch('搜索不应命中')
    expect(store.filteredItems).toEqual([])
  })

  it('filters by server needs_review and reconciles selection', () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(1), item(2, { needs_review: true })], 1)
    store.setScope('needs_review')
    expect(store.selectedDetailId).toBe(2)
    store.setScope('all')
    expect(store.selectedDetailId).toBe(2)
  })

  it('paginates 1000 rows and moves across the 100/101 boundary', () => {
    const store = useReviewQueueStore()
    store.setSort('student_code')
    store.replaceItems(Array.from({ length: 1000 }, (_, offset) => item(offset + 1)), 100)
    expect(REVIEW_PAGE_SIZE).toBe(100)
    expect(store.totalPages).toBe(10)
    store.moveSelection(1)
    expect(store.selectedDetailId).toBe(101)
    expect(store.page).toBe(2)
    store.moveSelection(-1)
    expect(store.selectedDetailId).toBe(100)
    expect(store.page).toBe(1)
  })

  it('keeps the last successful queue when refresh fails', async () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(1)])
    await store.loadItems(7, 'Q1', async () => { throw new Error('private failure') })
    expect(store.items).toHaveLength(1)
    expect(store.itemLoadState).toBe('error')
    expect(store.errorMessage).toBe('复核队列暂时无法读取，已保留上次内容。')
  })
})
```

- [ ] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/review-queue-store.spec.ts --maxWorkers=1
```

Expected: FAIL because `stores/review-queue.ts` does not exist.

- [ ] **Step 3: Implement the Store**

Create `frontend/src/stores/review-queue.ts`. Use `defineStore`, refs and computed values with these exact public members:

```ts
export const REVIEW_PAGE_SIZE = 100
export type ReviewScope = 'all' | 'needs_review'
export type ReviewSort = 'risk' | 'student_code' | 'student_name'
export type ReviewLoadState = 'idle' | 'loading' | 'ready' | 'error'

// State returned by the store:
questions, items, questionLoadState, itemLoadState, errorMessage,
selectedQuestionId, selectedDetailId, search, scope, sort, page,
filteredItems, pageItems, totalPages, currentItem, currentIndex,
canMovePrevious, canMoveNext,
loadQuestions(sessionId, loader = fetchReviewQuestions),
loadItems(sessionId, questionId, loader = fetchReviewItems),
replaceItems(nextItems, preferredDetailId?), selectQuestion(questionId),
selectDetail(detailId), setSearch(value), setScope(value), setSort(value),
setPage(value), moveSelection(delta), reset()
```

Implement deterministic helpers as plain functions in the same file:

```ts
const text = (value: string | null) => (value ?? '').trim().toLocaleLowerCase('zh-CN')
const identityKey = (entry: ReviewItem) => [entry.class_name ?? '', entry.student_code ?? '', entry.student_name, entry.detail_id]
const compareText = (left: string | number, right: string | number) => String(left).localeCompare(String(right), 'zh-CN', { numeric: true })

function compareTuple(left: (string | number)[], right: (string | number)[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const compared = compareText(left[index] ?? '', right[index] ?? '')
    if (compared !== 0) return compared
  }
  return 0
}

function riskKey(entry: ReviewItem): (string | number)[] {
  return [entry.needs_review ? 0 : 1, entry.confidence_score === null ? 1 : 0, entry.confidence_score ?? 0, ...identityKey(entry)]
}
```

The computed pipeline must copy before sorting, filter search against only `student_name`, `student_code`, and `class_name`, filter scope with `entry.needs_review`, and use `riskKey`, `identityKey`, or `[student_name, class_name, student_code, detail_id]` according to the selected sort. Reconciliation rules are exact: retain a visible selected ID; otherwise use the preferred visible ID; otherwise select the first filtered item; if empty use `null`; calculate `page = floor(index / 100) + 1`. Clamp manual page changes to `1..totalPages`.

Each load action owns an `AbortController` and monotonic generation. Starting a new load aborts the previous controller; only the latest generation may assign results or error state. An `AbortError`/P2-04 cancelled `ApiError` does not set the visible error. A failed first load uses `复核题目暂时无法读取。` or `复核队列暂时无法读取。`; a failed refresh with existing content uses `，已保留上次内容。`.

- [ ] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/review-queue-store.spec.ts --maxWorkers=1
git add frontend/src/stores/review-queue.ts frontend/src/__tests__/review-queue-store.spec.ts
git commit -m "feat: add deterministic review queue store"
```

Expected: store tests PASS, including the 100/101 boundary and retained-content failure.

---

### Task 3: Render the read-only page and synchronize validated URL context

**Files:**
- Create: `frontend/src/components/review/ReviewQueuePanel.vue`
- Create: `frontend/src/components/review/ReviewSelectionSummary.vue`
- Create: `frontend/src/views/ReviewQueueView.vue`
- Create: `frontend/src/__tests__/review-queue-view.spec.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`

**Interfaces:**
- Consumes: `useSessionStore()`, `useReviewQueueStore()`, existing `StatusBadge`, `StatePanel`, router query APIs, and Task 1/2 public types/actions.
- Produces: `/grading` route with the P2-05 read-only queue, validated `question`/`detail` query synchronization, and guarded `J`/`K` navigation.

- [ ] **Step 1: Write failing route and view tests**

Extend `navigation-router.spec.ts` to require `/grading` to load a component named `ReviewQueueView`. Create `review-queue-view.spec.ts` with a memory router, active Pinia, seeded Session Store and mocked Review Store actions. Cover these exact assertions:

```ts
it('shows the no-session state without requesting review data')
it('restores a valid question and detail from the URL')
it('replaces invalid question and detail query values with validated defaults')
it('preserves the selected item when filters keep it and selects the first when excluded')
it('moves with J/K but ignores shortcuts from input, select, button, textarea, and contenteditable')
it('never renders score inputs, save buttons, confirm buttons, or image elements')
```

Use generated public `ReviewItem` fixtures; assert visible Chinese labels and `router.currentRoute.value.query`, not component implementation details.

- [ ] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/navigation-router.spec.ts src/__tests__/review-queue-view.spec.ts --maxWorkers=1
```

Expected: FAIL because `/grading` still resolves `RoutePlaceholderView` and Review Queue components do not exist.

- [ ] **Step 3: Implement the two presentation components**

`ReviewQueuePanel.vue` must expose explicit props and emits:

```ts
const props = defineProps<{
  questions: ReviewQuestionSummary[]
  selectedQuestionId: string | null
  pageItems: ReviewItem[]
  selectedDetailId: number | null
  search: string
  scope: ReviewScope
  sort: ReviewSort
  page: number
  totalPages: number
  filteredTotal: number
  loading: boolean
}>()
const emit = defineEmits<{
  selectQuestion: [questionId: string]
  selectDetail: [detailId: number]
  updateSearch: [value: string]
  updateScope: [value: ReviewScope]
  updateSort: [value: ReviewSort]
  updatePage: [page: number]
}>()
```

Render persistent labels for the question selector, search, scope, and sort. Each queue row is a `<button type="button">` with `aria-current="true"` only when selected; it shows student name, code fallback `未提供学号`, optional class, one `StatusBadge` for `待复核`/`已复核`, confidence `置信度 72%` or `置信度未提供`, and one truncated reason from `error_summary ?? error_category ?? deduction_reason`. The only row action is selecting the row. Pagination buttons use `上一页`/`下一页` and are disabled at bounds.

`ReviewSelectionSummary.vue` accepts `item: ReviewItem | null` and `questionId: string | null`. With no item, show a `StatePanel` titled `当前筛选没有记录`. With an item, render identity, `当前得分 {score_awarded} / {max_score}`, confidence, status, and existing error text. Include the static notice `答卷证据将在 P2-06 接入；评分与确认将在 P2-07 接入。` Do not render `img`, input, textarea, editable content, save, or confirm controls.

- [ ] **Step 4: Implement the route view and safe URL synchronization**

Create `ReviewQueueView.vue` with `onMounted`, `onBeforeUnmount`, and watchers:

1. Watch `sessionStore.selectedSessionId`. On `null`, call `reviewStore.reset()`. On a positive ID, call `loadQuestions`; select `route.query.question` only if it exists in returned questions, otherwise the first question; then call `loadItems`.
2. After items load, select numeric `route.query.detail` only if it exists in current items, otherwise reconcile to the first visible item.
3. Watch `selectedQuestionId` and `selectedDetailId`, and call `router.replace({ query: validatedQuery })` only when the normalized query differs from the current query. Preserve no unknown query keys.
4. Changing the question aborts the previous item request, resets the detail preference, loads the new question, and writes validated query state.
5. Attach one window `keydown` listener. Return early when `event.defaultPrevented`, modifier keys are active, or the target matches `input, textarea, select, button, [contenteditable="true"]`. For `j` call `moveSelection(1)`; for `k` call `moveSelection(-1)`; then prevent default only when a move occurred.
6. Remove the listener and reset/abort page-owned requests on unmount.

The template must show:

- `StatePanel` `请先选择考试` when no session is selected;
- loading state for the first question/item load;
- `当前考试没有复核题目` when the question list is empty;
- a non-blocking `FeedbackBanner` with retry when old content exists and refresh failed;
- the two-column queue/summary layout when a validated question exists;
- previous/next record buttons above the summary, with `当前位置 X / Y` and exact disabled states.

Modify `frontend/src/router/index.ts` so only the `grading` definition uses lazy `ReviewQueueView`; all other workspace definitions still use `RoutePlaceholderView`:

```ts
const placeholderRoutes = workspaceRouteDefinitions.map((definition) => ({
  path: definition.path,
  name: definition.id,
  component: definition.id === 'grading'
    ? () => import('../views/ReviewQueueView.vue')
    : () => import('../views/RoutePlaceholderView.vue'),
  meta: { title: definition.title, description: definition.description, breadcrumb: definition.breadcrumb },
}))
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/navigation-router.spec.ts src/__tests__/review-queue-view.spec.ts --maxWorkers=1
git add frontend/src/components/review frontend/src/views/ReviewQueueView.vue frontend/src/router/index.ts frontend/src/__tests__/navigation-router.spec.ts frontend/src/__tests__/review-queue-view.spec.ts
git commit -m "feat: add read-only review queue page"
```

Expected: route/view tests PASS; source grep finds no confirm request and no editable score control in P2-05 files.

---

### Task 4: Apply the approved visual system and verify 1000-row desktop behavior

**Files:**
- Create: `frontend/src/styles/review-queue.css`
- Modify: `frontend/src/main.ts`
- Create: `frontend/e2e/review-queue.spec.ts`
- Modify: `frontend/src/__tests__/App.spec.ts`

**Interfaces:**
- Consumes: existing Token variables, App Shell geometry, Review Queue page test IDs/accessible labels, and mock API routes.
- Produces: stable two-column desktop layout, long-content truncation, keyboard focus visibility, five-viewport and 1000-row browser evidence.

- [ ] **Step 1: Write failing browser checks**

Create `frontend/e2e/review-queue.spec.ts` with mock handlers for `/api/sessions`, `/api/sessions/7/review/questions`, and `/api/sessions/7/review/questions/Q1/items?needs_review_only=false`. Generate 1000 anonymous items in memory and cover:

```ts
test('restores URL context and crosses the 100/101 boundary with keyboard navigation')
test('search, needs-review filter, and risk sort keep a valid current item')
test('input focus suppresses J/K navigation')
test('first-load, retained-content error, no-questions, and filtered-empty states are actionable')
test('1000-row queue has no horizontal overflow or console errors at all five desktop viewports')
```

For each viewport, assert `document.documentElement.scrollWidth <= clientWidth`, queue rows rendered are at most 100, the selected row has `aria-current=true`, the summary remains visible, long names do not cross their row bounds, and browser `pageerror`/console error arrays are empty.

- [ ] **Step 2: Run RED**

```powershell
npm run e2e -- review-queue.spec.ts
```

Expected: FAIL because review-specific layout styles and imported stylesheet are missing.

- [ ] **Step 3: Implement Token-only styles**

Create `review-queue.css`, import it from `main.ts` after `app-shell.css`, and use these stable structural rules:

```css
.review-workspace {
  display: grid;
  min-width: 0;
  min-height: 100%;
  grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
  background: var(--color-bg-app);
}
.review-queue-panel {
  min-width: 0;
  border-inline-end: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
}
.review-queue-panel__controls,
.review-selection-summary { padding: var(--space-4); }
.review-queue-list { display: grid; margin: 0; padding: 0; list-style: none; }
.review-queue-row {
  display: grid;
  width: 100%;
  min-width: 0;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  border: 0;
  border-block-end: var(--border-width) solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  text-align: start;
}
.review-queue-row[aria-current="true"] {
  box-shadow: inset var(--border-width) 0 0 var(--color-accent);
  background: var(--color-bg-selected);
}
.review-queue-row__name,
.review-queue-row__reason { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (min-width: 1024px) and (max-width: 1279px) {
  .review-workspace { grid-template-columns: minmax(240px, 280px) minmax(0, 1fr); }
}
```

Complete controls, focus, pagination, summary and feedback selectors using only existing `--color-*`, `--space-*`, `--radius-*`, `--font-*`, `--control-*`, `--duration-*`, `--border-*`, and `--opacity-*` variables. Ordinary content uses borders/background hierarchy, not card shadows. `:focus-visible` must remain obvious. Do not add raw hex/rgb/hsl values or arbitrary pixel spacing outside the approved structural widths.

Update `App.spec.ts` to assert `main.ts` imports `./styles/review-queue.css` and P2-05 styles contain no color literals.

- [ ] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/App.spec.ts --maxWorkers=1
npm run e2e -- review-queue.spec.ts
git add frontend/src/styles/review-queue.css frontend/src/main.ts frontend/e2e/review-queue.spec.ts frontend/src/__tests__/App.spec.ts
git commit -m "test: verify review queue desktop workflow"
```

Expected: focused unit checks and Chromium P2-05 e2e PASS at all five viewports.

---

### Task 5: Run package gates, prepare the quick test, and freeze for review

**Files:**
- Create: `docs/user-testing/checkpoints/P2-05-review-queue-quick.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-05-review-queue-implementation.md`

**Interfaces:**
- Consumes: `docs/user-testing/USER_TEST_TEMPLATE.md`, completed P2-05 route, generated mock API behavior, and repository handoff validator.
- Produces: verified quick-test instructions, final automated evidence, unchanged real database fingerprints, and a `waiting_review` feature commit.

- [ ] **Step 1: Run focused and affected regression gates**

Run from `frontend/`:

```powershell
npm run lint
npm run typecheck
npm run test -- --maxWorkers=1
npm run build
npm run e2e -- review-queue.spec.ts app-shell.spec.ts app.spec.ts
```

Expected: all commands exit 0; record test file/test counts and Playwright counts in this plan below the handoff block or in a dated evidence note within the plan.

- [ ] **Step 2: Run repository gates and scope checks**

Run from the P2-05 worktree root, using the repository-root portable Python for smoke:

```powershell
git diff --check
git status --short -- user_data
git diff --name-only origin/main...HEAD
rg -n "confirm|score.*input|fetch\(" frontend/src/api/review.ts frontend/src/stores/review-queue.ts frontend/src/views/ReviewQueueView.vue frontend/src/components/review
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
```

Expected: no `user_data` changes; changed paths are limited to this plan/spec, P2-05 frontend sources/tests/styles, and the quick checklist; `confirm` appears only in explanatory text if at all, there is no score input or direct page `fetch`; smoke exits 0. Do not run full pytest unless a risk trigger from `AGENTS.md` occurs.

- [ ] **Step 3: Compare real database fingerprints without opening SQLite**

From the repository root, run:

```powershell
Get-Item user_data/databases/grading_system.db,user_data/databases/question_bank.db | Select-Object Name,Length,LastWriteTimeUtc
Get-FileHash user_data/databases/grading_system.db,user_data/databases/question_bank.db -Algorithm SHA256
```

Expected exact baseline:

```text
grading_system.db|2863104|2026-07-10T07:10:41.1221109Z|93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD
question_bank.db|3461120|2026-07-08T11:58:06.3320883Z|E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8
```

Any mismatch is a blocker; do not update the expected values.

- [ ] **Step 4: Create the implemented quick-test checklist**

After the browser flow is verified, create `docs/user-testing/checkpoints/P2-05-review-queue-quick.md` from `USER_TEST_TEMPLATE.md`. It must state:

```markdown
# P2-05 复核队列短测

- 数据来源：仅本次浏览器测试提供的匿名合成考试和 1000 条合成复核记录。
- 启动方式、访问地址、可见标识和关闭方式：使用实际已验证的 P2-05 本地前端测试启动命令、地址和页面标题填写，不得预设未实现端口或横幅。
- 步骤：选择合成考试；打开“阅卷”；切换题目；搜索姓名/学号/班级；切换仅待复核；验证风险优先；用上一条/下一条和 J/K 跨越分页边界；在搜索框输入 J/K 验证不误切；检查无调分/确认入口。
- 预期：当前学生、位置、URL 与队列选中行一致；页面无重叠和横向滚动；任何步骤都不写业务数据。
```

Do not mark the result `passed`; quick user feedback remains pending until the user performs the checklist.

- [ ] **Step 5: Commit the verified feature state**

Update every completed checkbox, then set the handoff block to:

```markdown
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
```

Then run:

```powershell
git add docs/superpowers/plans/2026-07-13-p2-05-review-queue-implementation.md docs/user-testing/checkpoints/P2-05-review-queue-quick.md
git diff --cached --check
git commit -m "docs: prepare P2-05 review handoff"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py --plan docs/superpowers/plans/2026-07-13-p2-05-review-queue-implementation.md --repo .
```

Expected: commit succeeds; validator prints one JSON line with `ok=true`, `state="waiting_review"`, and no issues. Stop here for independent review; do not self-mark `independent review: passed`.

---

## Post-Implementation Review and Integration Gates

1. Independent review must inspect the feature commit for Critical/Important issues and verify package scope, URL/store race handling, accessibility, 1000-row behavior, and absence of write operations.
2. Review fixes use RED→GREEN and rerun only the affected tests plus required package gates; the same issue failing twice triggers safe stop.
3. After independent review passes, create the required plan-only `waiting_user` anchor commit recording the full reviewed feature SHA, then run the implemented P2-05 quick test against that exact SHA.
4. If the user reports `passed`, create the single checklist-only evidence commit with the same reviewed SHA, then a plan-only `verified_pending_integration` handoff commit. If quick testing is treated as non-blocking feedback by current repository policy, record the actual result without converting an unperformed test to `passed`.
5. Integration receives the complete P2-05 chain from latest `origin/main`, runs focused frontend regression and the wave-end complete smoke/frontend gates, compares real database fingerprints, updates `ARCHITECTURE.md` and `EXECUTION_INDEX.md`, then pushes an integration branch and merges by PR. Never push `main` directly.
