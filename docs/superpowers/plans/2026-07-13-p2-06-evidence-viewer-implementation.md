# P2-06 Answer Evidence Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-06
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 6e5ce9122673579a0a95045e9f95bd6bd3fb3a7f
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-06-evidence-viewer-quick.md

**Goal:** 在 P2-05 只读复核队列中接入受控裁剪图和原卷正反面查看，提供稳定画布、适应宽度、原比例、缩放、旋转、拖拽、错误重试、相邻裁剪预载和大图内存守卫。

**Architecture:** 复用 `ReviewItem.media` 中已经过运行时校验的同源 `/api/` URL，不修改后端契约。一个无业务依赖的 Vue composable 负责缩放、旋转、平移、适应宽度和加载世代；`ReviewEvidenceViewer` 只渲染一个活动 `<img>`，相邻项仅预载裁剪图；`ReviewQueueView` 继续拥有队列、URL 和连续导航，不保存图片字节或变换状态。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Pinia 3.0.4, Vue Router 5.1.0, native `<img>`/Pointer Events/ResizeObserver, Vitest 4.1.10, Playwright 1.61.1, existing CSS Tokens, Python 3.12 repository guards.

## Global Constraints

- 实现必须符合 `docs/superpowers/specs/2026-07-13-p2-06-evidence-viewer-design.md`、`docs/superpowers/packages/phase-2-execution-packages.md` 和 `docs/ui/STYLE.md`。
- 只消费 `ReviewItem.media.crop_url`、`original_front_url` 与 `original_back_url`；不公开批注图切换，不新增后端路由、数据库字段或文件缓存。
- 所有图像变换只在浏览器显示层完成；不得修改、上传、另存或持久化原始图片，不得调用 review confirm POST。
- 同一时间只渲染一个活动证据 `<img>`；只预载过滤结果的上一条/下一条裁剪图，不预载原卷或批注图。
- 记录或来源切换重置为裁剪图、适应宽度、0°、居中；缩放范围 10%–400%，按钮步进 25%，旋转步进 90°。
- 画布焦点内支持 `+`/`-`、`0`、`Z` 和方向键；不得抢占 P2-05 的全局 `J`/`K`，不得提前实现 P2-08 的全页快捷键整合。
- 图片失败只显示安全中文错误，不回显响应正文、内部路径、URL、堆栈或文件类型推断；重试不得追加任意查询参数。
- 页面必须覆盖加载、裁剪失败、原卷正面失败、原卷反面失败、快速切换、相邻预载失败和筛选空态；媒体失败不得清空队列或当前上下文。
- 只支持不低于 1024px 的 Windows 桌面浏览器；验证 1920×1080、1440×900、1366×768、1280×800、1024×768，不建设移动端手势或抽屉。
- 页面样式只使用 `frontend/src/styles/tokens.css` 中已有 Token；不修改全局色板、字体或 App Shell，不增加生产依赖或改动 `package-lock.json`。
- 测试只使用 mock API 和程序生成的匿名媒体；不得读取、修改、暂存、提交或 stash 真实 `user_data/`，不得调用真实模型。
- 执行模型为 T-H。每个新行为必须先有可见 RED，再有最小 GREEN；同一问题连续两次修复失败时安全停机。
- 实施前必须使用 `frontend-design`，实施过程必须使用 `test-driven-development`，完成声明前必须使用 `verification-before-completion`；本任务禁止自动派生子代理，除非用户另行明确授权。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-06
**交接状态：** verified_pending_integration
**功能提交：** 93b2a20152cb1d77799e4d23904c49b1736f10be
**自动验证：** passed
**独立复审：** passed
**用户验收：** passed
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 2026-07-13 领取证据

- 实现工作树 `HEAD` 与当时 `origin/main` 均为 `e080255bdcfbf982ac58164a3b8b48d964904448`；源码及该工作树的 `user_data/` 状态干净。
- Stash 基线为 `85726b3b9863575c9aebe4ff12916e96d4bb08ba`、`67edf9783a70b42878c44ae05eea25528b51ddf2`。
- 根工作区真实数据库只读指纹：`grading_system.db|2863104|2026-07-10T07:10:41.1221109Z|93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`；`question_bank.db|3461120|2026-07-08T11:58:06.3320883Z|E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`。仅读取名称、大小、UTC 修改时间和 SHA-256，未打开 SQLite。

---

### Task 1: Claim P2-06 without touching source or real data

**Files:**
- Modify: `docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md`

**Interfaces:**
- Consumes: package identity and plan baseline already merged into `origin/main`.
- Produces: the first first-parent claim commit and a validator-compatible `in_progress` handoff block.

- [x] **Step 1: Record immutable stash and real-database baselines**

From the fresh implementation worktree, run:

```powershell
git stash list --format=%H
git status --short --branch
git status --short -- user_data
git rev-parse HEAD
git rev-parse origin/main
```

From the repository root, read without opening SQLite:

```powershell
Get-Item user_data/databases/grading_system.db,user_data/databases/question_bank.db |
  ForEach-Object { "{0}|{1}|{2:o}" -f $_.Name,$_.Length,$_.LastWriteTimeUtc }
Get-FileHash user_data/databases/grading_system.db,user_data/databases/question_bank.db -Algorithm SHA256 |
  ForEach-Object { "{0}|{1}" -f (Split-Path $_.Path -Leaf),$_.Hash }
```

Expected: implementation worktree source and `user_data` are clean; `HEAD` equals then-current `origin/main` and contains plan baseline `6e5ce9122673579a0a95045e9f95bd6bd3fb3a7f`. Keep the exact stash SHA list and database outputs in this plan’s dated evidence section; do not copy paths or business content.

- [x] **Step 2: Add exactly one handoff block**

The current repository stash baseline is exactly the two SHA values below. If Step 1 returns anything different, stop before claiming the package; do not rewrite this baseline.

The actual handoff block now appears once near the top of this plan and uses `**执行包：** P2-06`.

- [x] **Step 3: Verify and commit the claim**

```powershell
git diff --check
git status --short -- user_data
git add docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P2-06 evidence viewer"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md `
  --repo .
```

Expected: staged paths contain only this plan; the commit is the first first-parent commit after the merge base; validator returns one JSON line with `ok=true`, `state="in_progress"`, and no issues.

---

### Task 2: Build deterministic evidence-viewer state with RED → GREEN

**Files:**
- Create: `frontend/src/composables/use-evidence-viewer.ts`
- Create: `frontend/src/__tests__/evidence-viewer-state.spec.ts`

**Interfaces:**
- Consumes: no Store, router, API or DOM-specific business data.
- Produces: `EvidenceSource`, `ViewerPoint`, `ViewerSize`, `useEvidenceViewer()`, source/reset/loading actions, computed transform and bounded input actions used by Task 3.

- [x] **Step 1: Write failing state tests**

Create `frontend/src/__tests__/evidence-viewer-state.spec.ts` with these exact cases:

```ts
import { describe, expect, it } from 'vitest'
import { useEvidenceViewer } from '../composables/use-evidence-viewer'

describe('P2-06 evidence viewer state', () => {
  it('starts on the crop in fit-width mode and resets between records', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 1600, height: 1200 })
    viewer.zoomBy(0.5)
    viewer.panBy({ x: 120, y: -80 })
    viewer.rotateBy(90)
    viewer.resetForRecord('detail-2')
    expect(viewer.source.value).toBe('crop')
    expect(viewer.mode.value).toBe('fit-width')
    expect(viewer.rotation.value).toBe(0)
    expect(viewer.pan.value).toEqual({ x: 0, y: 0 })
  })

  it('fits rotated image width and keeps zoom between ten and four hundred percent', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 820, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 1600, height: 1000 })
    expect(viewer.scale.value).toBeCloseTo(0.48)
    viewer.rotateBy(90)
    expect(viewer.scale.value).toBeCloseTo(0.772)
    viewer.setActualSize()
    viewer.zoomBy(20)
    expect(viewer.scale.value).toBe(4)
    viewer.zoomBy(-20)
    expect(viewer.scale.value).toBe(0.1)
  })

  it('zooms around the pointer and recenters an image smaller than the canvas', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 800, height: 600 })
    viewer.setActualSize()
    viewer.zoomBy(0.5, { x: 200, y: 100 })
    expect(viewer.pan.value.x).toBeLessThan(0)
    expect(viewer.pan.value.y).toBeLessThan(0)
    viewer.setActualSize()
    expect(viewer.pan.value).toEqual({ x: 0, y: 0 })
  })

  it('bounds drag and keyboard pan while keeping a visible edge', () => {
    const viewer = useEvidenceViewer()
    viewer.setCanvasSize({ width: 800, height: 600 })
    viewer.acceptImage(viewer.beginImageLoad(), { width: 2400, height: 1800 })
    viewer.setActualSize()
    viewer.panBy({ x: 5000, y: -5000 })
    expect(viewer.pan.value).toEqual({ x: 848, y: -648 })
  })

  it('ignores stale load events and gives retry a new image key', () => {
    const viewer = useEvidenceViewer()
    const first = viewer.beginImageLoad()
    const second = viewer.beginImageLoad()
    expect(viewer.acceptImage(first, { width: 10, height: 10 })).toBe(false)
    expect(viewer.failImage(first)).toBe(false)
    expect(viewer.acceptImage(second, { width: 100, height: 200 })).toBe(true)
    const key = viewer.imageKey.value
    viewer.retry()
    expect(viewer.imageKey.value).toBe(key + 1)
    expect(viewer.loadState.value).toBe('loading')
  })
})
```

- [x] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/evidence-viewer-state.spec.ts --maxWorkers=1
```

Expected: FAIL because `use-evidence-viewer.ts` does not exist.

- [x] **Step 3: Implement the minimal composable**

Create `frontend/src/composables/use-evidence-viewer.ts` with the public contract below and no Store/router imports:

```ts
import { computed, ref } from 'vue'

export type EvidenceSource = 'crop' | 'original_front' | 'original_back'
export type ViewerMode = 'fit-width' | 'manual'
export type ViewerLoadState = 'idle' | 'loading' | 'ready' | 'error'
export interface ViewerPoint { x: number; y: number }
export interface ViewerSize { width: number; height: number }

const MIN_SCALE = 0.1
const MAX_SCALE = 4
const FIT_INSET = 48
const VISIBLE_EDGE = 48
const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value))

export function useEvidenceViewer() {
  const recordKey = ref('')
  const source = ref<EvidenceSource>('crop')
  const mode = ref<ViewerMode>('fit-width')
  const loadState = ref<ViewerLoadState>('idle')
  const rotation = ref(0)
  const scale = ref(1)
  const pan = ref<ViewerPoint>({ x: 0, y: 0 })
  const canvas = ref<ViewerSize>({ width: 0, height: 0 })
  const image = ref<ViewerSize>({ width: 0, height: 0 })
  const imageKey = ref(0)
  let loadGeneration = 0

  const rotatedSize = computed<ViewerSize>(() =>
    Math.abs(rotation.value) % 180 === 90
      ? { width: image.value.height, height: image.value.width }
      : image.value,
  )
  const transform = computed(() =>
    `translate(-50%, -50%) translate3d(${pan.value.x}px, ${pan.value.y}px, 0) rotate(${rotation.value}deg) scale(${scale.value})`,
  )

  function boundPan(next = pan.value): ViewerPoint {
    const renderedWidth = rotatedSize.value.width * scale.value
    const renderedHeight = rotatedSize.value.height * scale.value
    const maxX = Math.max(0, (renderedWidth - canvas.value.width) / 2 + VISIBLE_EDGE)
    const maxY = Math.max(0, (renderedHeight - canvas.value.height) / 2 + VISIBLE_EDGE)
    return {
      x: maxX === 0 ? 0 : clamp(next.x, -maxX, maxX),
      y: maxY === 0 ? 0 : clamp(next.y, -maxY, maxY),
    }
  }

  function fitWidth(): void {
    mode.value = 'fit-width'
    const available = Math.max(1, canvas.value.width - FIT_INSET)
    scale.value = clamp(available / Math.max(1, rotatedSize.value.width), MIN_SCALE, MAX_SCALE)
    pan.value = boundPan({ x: 0, y: 0 })
  }

  function setActualSize(): void {
    mode.value = 'manual'
    scale.value = 1
    pan.value = boundPan({ x: 0, y: 0 })
  }

  function zoomBy(delta: number, anchor: ViewerPoint = { x: 0, y: 0 }): void {
    mode.value = 'manual'
    const previous = scale.value
    const next = clamp(previous + delta, MIN_SCALE, MAX_SCALE)
    const ratio = next / previous
    scale.value = next
    pan.value = boundPan({
      x: anchor.x - (anchor.x - pan.value.x) * ratio,
      y: anchor.y - (anchor.y - pan.value.y) * ratio,
    })
  }

  function rotateBy(delta: -90 | 90): void {
    rotation.value = (rotation.value + delta + 360) % 360
    if (mode.value === 'fit-width') fitWidth()
    else pan.value = boundPan()
  }

  function panBy(delta: ViewerPoint): void {
    pan.value = boundPan({ x: pan.value.x + delta.x, y: pan.value.y + delta.y })
  }

  function resetView(): void {
    rotation.value = 0
    pan.value = { x: 0, y: 0 }
    mode.value = 'fit-width'
    if (image.value.width > 0) fitWidth()
  }

  function resetForRecord(nextKey: string): void {
    recordKey.value = nextKey
    source.value = 'crop'
    image.value = { width: 0, height: 0 }
    loadState.value = 'idle'
    imageKey.value += 1
    resetView()
  }

  function selectSource(next: EvidenceSource): void {
    if (source.value === next) return
    source.value = next
    image.value = { width: 0, height: 0 }
    imageKey.value += 1
    resetView()
  }

  function beginImageLoad(): number {
    loadState.value = 'loading'
    return ++loadGeneration
  }

  function acceptImage(generation: number, size: ViewerSize): boolean {
    if (generation !== loadGeneration) return false
    image.value = size
    loadState.value = 'ready'
    fitWidth()
    return true
  }

  function failImage(generation: number): boolean {
    if (generation !== loadGeneration) return false
    loadState.value = 'error'
    return true
  }

  function retry(): void {
    imageKey.value += 1
    loadState.value = 'loading'
  }

  function setCanvasSize(size: ViewerSize): void {
    canvas.value = size
    if (mode.value === 'fit-width' && image.value.width > 0) fitWidth()
    else pan.value = boundPan()
  }

  return {
    source, mode, loadState, rotation, scale, pan, imageKey, transform,
    resetForRecord, selectSource, beginImageLoad, acceptImage, failImage,
    retry, setCanvasSize, fitWidth, setActualSize, zoomBy, rotateBy, panBy,
  }
}
```

If the exact `VISIBLE_EDGE` expectation exposes a one-pixel arithmetic difference, adjust the test and implementation together only after writing the formula in the test name; do not weaken it to a broad range.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/evidence-viewer-state.spec.ts --maxWorkers=1
git add frontend/src/composables/use-evidence-viewer.ts frontend/src/__tests__/evidence-viewer-state.spec.ts
git commit -m "feat: add evidence viewer state"
```

Expected: the focused test file passes with all five behaviors.

---

### Task 3: Render one safe media image and integrate it with the review queue

**Files:**
- Create: `frontend/src/components/review/ReviewEvidenceViewer.vue`
- Create: `frontend/src/__tests__/review-evidence-viewer.spec.ts`
- Modify: `frontend/src/components/review/ReviewSelectionSummary.vue`
- Modify: `frontend/src/views/ReviewQueueView.vue`
- Modify: `frontend/src/__tests__/review-queue-view.spec.ts`

**Interfaces:**
- Consumes: `ReviewItem`, Task 2 `useEvidenceViewer()`, current/previous/next entries from the filtered P2-05 queue.
- Produces: one active evidence image, safe source switching, retry, pointer/wheel/focused-keyboard operations, and at most two adjacent crop preloads.

- [x] **Step 1: Write failing component and integration tests**

Create `frontend/src/__tests__/review-evidence-viewer.spec.ts`. Stub `ResizeObserver` and `globalThis.Image`, mount with three anonymous items, and assert these behaviors:

```ts
it('loads the crop by default and renders exactly one active image')
it('switches to original front and back while resetting transform state')
it('shows a safe retry state and remounts the same controlled URL')
it('ignores stale load events after a rapid item change')
it('preloads only previous and next crop URLs and releases them on unmount')
it('supports wheel-centered zoom, pointer drag, and focused canvas keys')
```

Extend `review-queue-view.spec.ts` so its former “never renders image elements” case becomes:

```ts
it('renders read-only evidence without score, save, or confirm controls', async () => {
  const { host } = await mountView({ initialUrl: '/grading?question=Q1&detail=11' })
  await vi.waitFor(() => expect(host.textContent).toContain('当前得分 3 / 5'))
  expect(host.querySelectorAll('.review-evidence-viewer img')).toHaveLength(1)
  expect(host.querySelector('input[type="number"]')).toBeNull()
  expect(host.querySelector('textarea, [contenteditable="true"]')).toBeNull()
  expect([...host.querySelectorAll('button')].some((button) =>
    /保存|确认/.test(button.textContent ?? ''),
  )).toBe(false)
  expect(host.textContent).toContain('评分与确认将在 P2-07 接入。')
})
```

Also add an integration case that selects the next record and expects the viewer source label `裁剪证据`, zoom label `适应宽度`, and one new `img[src]` matching the new current item.

- [x] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/review-evidence-viewer.spec.ts src/__tests__/review-queue-view.spec.ts --maxWorkers=1
```

Expected: FAIL because `ReviewEvidenceViewer.vue` does not exist and the page still renders only the P2-05 notice.

- [x] **Step 3: Implement `ReviewEvidenceViewer.vue`**

Use this exact public interface:

```ts
const props = defineProps<{
  item: ReviewItem
  previousItem: ReviewItem | null
  nextItem: ReviewItem | null
}>()
```

Map sources without constructing paths:

```ts
const sourceOptions = [
  { value: 'crop', label: '裁剪证据' },
  { value: 'original_front', label: '原卷正面' },
  { value: 'original_back', label: '原卷反面' },
] as const
const sourceUrl = computed(() => ({
  crop: props.item.media.crop_url,
  original_front: props.item.media.original_front_url,
  original_back: props.item.media.original_back_url,
})[viewer.source.value])
const recordKey = computed(() => `${props.item.session_id}:${props.item.detail_id}`)
```

The component must implement these exact event rules:

1. Watch `recordKey` immediately and call `resetForRecord`.
2. Before a new keyed `<img>` mounts, call `beginImageLoad()` and store the returned generation beside the key.
3. On `load`, accept only that generation and read `naturalWidth/naturalHeight`; on `error`, accept only that generation and show `裁剪图暂时无法读取。` or `原卷页暂时无法读取。`.
4. `ResizeObserver` watches only the canvas element and calls `setCanvasSize`; disconnect on unmount.
5. Pointer down captures the pointer, records client coordinates, and adds class `is-dragging`; pointer move sends deltas to `panBy`; pointer up/cancel/lost capture clears drag state.
6. Wheel delta uses `deltaY < 0 ? 0.25 : -0.25`; anchor is pointer position minus canvas center; call `preventDefault()` only when the scale changes.
7. Canvas has `tabindex="0"`. `+`/`=` and `-` zoom; `0` calls `setActualSize`; `z` calls `fitWidth`; arrow keys pan by 32px; handled keys prevent default. Do not register a window listener.
8. The active image has `draggable="false"`, `decoding="async"`, `alt="{student_name} 的{来源标签}"`, and inline `transform: viewer.transform.value`; do not render hidden source images.
9. Retry increments `imageKey`, mounts the same `sourceUrl`, and never appends a query string.
10. Preload `previousItem?.media.crop_url` and `nextItem?.media.crop_url`, deduplicated and excluding the active crop URL. Hold at most two `new Image()` objects; set `src`, silence their `onerror`, clear handlers/src and the array before replacement and on unmount.

The template must contain, in order:

```html
<section class="review-evidence-viewer" aria-label="答卷证据查看器">
  <div class="review-evidence-source" role="group" aria-label="证据来源">...</div>
  <div class="review-evidence-toolbar" role="toolbar" aria-label="图片查看工具">...</div>
  <div class="review-evidence-canvas" tabindex="0" aria-label="答卷图片画布">...</div>
  <p class="review-evidence-help">画布聚焦后可用 Z、0、+、− 和方向键。</p>
</section>
```

Toolbar button names are exactly `适应宽度`, `原比例`, `缩小`, `放大`, `向左旋转`, `向右旋转`; the visible percentage is `Math.round(scale * 100)%`. Loading and error content remain inside the canvas; the error action is `重新加载`.

- [x] **Step 4: Integrate current and adjacent records**

In `ReviewQueueView.vue`, add:

```ts
import ReviewEvidenceViewer from '../components/review/ReviewEvidenceViewer.vue'

const previousItem = computed(() =>
  reviewStore.currentIndex > 0
    ? reviewStore.filteredItems[reviewStore.currentIndex - 1] ?? null
    : null,
)
const nextItem = computed(() =>
  reviewStore.currentIndex >= 0
    ? reviewStore.filteredItems[reviewStore.currentIndex + 1] ?? null
    : null,
)
```

Replace the P2-05 summary-only body with a `review-evidence-layout` that renders the compact `ReviewSelectionSummary` first and then:

```vue
<ReviewEvidenceViewer
  v-if="reviewStore.currentItem"
  :item="reviewStore.currentItem"
  :previous-item="previousItem"
  :next-item="nextItem"
/>
```

Keep `StatePanel` for no current item. Do not change session/question loading, URL synchronization, J/K behavior, page size or Store state.

In `ReviewSelectionSummary.vue`, keep identity, score, confidence, status and existing error text, add class `review-selection-summary--compact`, remove the old “答卷证据将在 P2-06 接入” notice, and change the notice to exactly `评分与确认将在 P2-07 接入。当前页面不会修改原图或评分数据。`.

- [x] **Step 5: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/review-evidence-viewer.spec.ts src/__tests__/review-queue-view.spec.ts --maxWorkers=1
git add frontend/src/composables frontend/src/components/review frontend/src/views/ReviewQueueView.vue frontend/src/__tests__/review-evidence-viewer.spec.ts frontend/src/__tests__/review-queue-view.spec.ts
git commit -m "feat: add read-only answer evidence viewer"
```

Expected: component and affected view tests pass; DOM contains one evidence image and no score/save/confirm control.

---

### Task 4: Apply Token-only canvas styles and verify real images in Chromium

**Files:**
- Create: `frontend/src/styles/review-evidence.css`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/__tests__/App.spec.ts`
- Create: `frontend/e2e/review-evidence.spec.ts`
- Modify: `frontend/e2e/review-queue.spec.ts`

**Interfaces:**
- Consumes: existing App Shell/review queue geometry, CSS Tokens, P2-06 accessible names/test hooks and mock Review API.
- Produces: stable evidence canvas, visible focus/loading/error states, generated image routes, pixel checks, large-image and five-viewport evidence.

- [x] **Step 1: Write failing style/import and browser checks**

Extend `App.spec.ts` to require `main.ts` to import `./styles/review-evidence.css` after `review-queue.css`, and require the new stylesheet to contain no hex/rgb/hsl literals.

Create `frontend/e2e/review-evidence.spec.ts` with the existing anonymous session/review mock shape and generated media routes. Use Node `zlib.createDeflate()` plus PNG signature/IHDR/IDAT/IEND chunks and a local CRC32 helper to generate two deterministic PNGs:

- crop: 1200×800, white page with dark horizontal bands and a red rectangular border;
- original: 4096×4096 indexed or grayscale image with nonuniform bands, generated row-by-row so the test does not hold a second uncompressed copy.

Do not add binary fixtures to Git. Route the exact `ReviewItem.media` URLs and respond with `contentType: 'image/png'`, `Cache-Control: no-store`.

Cover these browser cases:

```ts
test('loads non-empty crop pixels and switches one active image across original pages')
test('fit, actual size, zoom, rotate, wheel center and pointer drag change the transform predictably')
test('failed media keeps review context and retries the same controlled URL')
test('rapid J/K switching resets the viewer and never shows stale pixels')
test('large original image keeps one active DOM image and remains interactive')
test('evidence viewer has no overflow, overlap, inaccessible tools, or console errors at five viewports')
```

For the non-empty pixel assertion, screenshot the canvas after the image reports `complete && naturalWidth > 0`, sample pixels in the screenshot buffer through a small PNG decoder already implemented in the spec (inflate IDAT and read RGB/RGBA scanlines), and assert at least two distinct luminance values plus at least one red-border pixel. Keep all decoder code test-only.

For all five viewports assert:

```ts
expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
await expect(page.locator('.review-queue-row[aria-current="true"]')).toBeInViewport()
await expect(page.getByRole('toolbar', { name: '图片查看工具' })).toBeInViewport()
await expect(page.getByLabel('答卷图片画布')).toBeInViewport()
await expect(page.locator('.review-evidence-viewer img')).toHaveCount(1)
```

Track `pageerror` and console `error` arrays and require both empty.

- [x] **Step 2: Run RED**

```powershell
npm run test -- src/__tests__/App.spec.ts --maxWorkers=1
npm run e2e -- review-evidence.spec.ts
```

Expected: import/style test fails because the stylesheet is absent; browser tests fail because canvas layout is not styled.

- [x] **Step 3: Implement Token-only evidence styles**

Create `frontend/src/styles/review-evidence.css` and import it in `main.ts` after `review-queue.css`. Use the existing Token palette; the canvas background is `var(--color-text-primary)` with `var(--color-bg-sidebar)` as its border/surround, not a new raw color.

The structural selectors must include:

```css
.review-evidence-layout {
  display: grid;
  min-width: 0;
  min-height: 0;
  height: 100%;
  grid-template-rows: auto minmax(0, 1fr);
}

.review-selection-summary--compact {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-block-end: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.review-evidence-viewer {
  display: grid;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  background: var(--color-bg-sidebar);
}

.review-evidence-source,
.review-evidence-toolbar {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  overflow-x: auto;
  border-block-end: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.review-evidence-canvas {
  position: relative;
  min-width: 0;
  min-height: 240px;
  overflow: hidden;
  background: var(--color-text-primary);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.review-evidence-canvas:focus-visible {
  z-index: 1;
  outline: var(--border-width) solid var(--color-accent);
  outline-offset: calc(-1 * var(--focus-offset));
  box-shadow: inset var(--focus-ring);
}

.review-evidence-canvas.is-dragging { cursor: grabbing; }

.review-evidence-canvas img {
  position: absolute;
  inset-block-start: 50%;
  inset-inline-start: 50%;
  max-width: none;
  max-height: none;
  transform-origin: center;
  will-change: transform;
}
```

Buttons reuse the review navigation control border/background/focus language, selected source uses `aria-pressed="true"` plus `var(--color-bg-selected)` and an accent inset border, loading/error text uses existing StatePanel/Feedback conventions without overlay shadow. Add `@media (min-width: 1024px) and (max-width: 1279px)` to reduce horizontal padding to `var(--space-2)`, allow source/toolbar wrapping only when necessary, and keep the canvas at least 240px tall.

Modify `.review-detail` in `review-queue.css` from scrolling content to an internal grid/hidden overflow host so the evidence canvas receives the remaining height; keep navigation sticky and queue scrolling unchanged. Update `review-queue.spec.ts` selectors only where the former summary-specific viewport assertion must point to `.review-evidence-viewer`.

- [x] **Step 4: Run GREEN and commit**

```powershell
npm run test -- src/__tests__/App.spec.ts --maxWorkers=1
npm run e2e -- review-evidence.spec.ts review-queue.spec.ts
git add frontend/src/styles/review-evidence.css frontend/src/styles/review-queue.css frontend/src/main.ts frontend/src/__tests__/App.spec.ts frontend/e2e/review-evidence.spec.ts frontend/e2e/review-queue.spec.ts
git commit -m "test: verify evidence viewer desktop workflow"
```

Expected: import/style guard and both P2-05/P2-06 Chromium suites pass, including five viewports and generated large PNG.

---

### Task 5: Run package gates, prepare the quick test, and freeze for review

**Files:**
- Create: `docs/user-testing/checkpoints/P2-06-evidence-viewer-quick.md`
- Modify: `docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md`

**Interfaces:**
- Consumes: implemented viewer, `docs/user-testing/USER_TEST_TEMPLATE.md`, generated P2-06 media environment and repository handoff validator.
- Produces: complete automated evidence, unchanged real database fingerprints, a versioned quick-test checklist and `waiting_review` feature commit.

- [x] **Step 1: Run focused and affected frontend gates**

From `frontend/`:

```powershell
npm run lint
npm run typecheck
npm run test -- --maxWorkers=1
npm run build
npm run e2e -- review-evidence.spec.ts review-queue.spec.ts app-shell.spec.ts app.spec.ts
```

Expected: all commands exit 0. Record exact Vitest file/test counts, Playwright counts and build result in a dated evidence section in this plan.

- [x] **Step 2: Run repository and scope guards**

From the P2-06 worktree root:

```powershell
git diff --check
git status --short -- user_data
git diff --name-only origin/main...HEAD
rg -n "confirm|score.*input|fetch\(|Blob|createObjectURL|localStorage|canvas" `
  frontend/src/composables/use-evidence-viewer.ts `
  frontend/src/components/review/ReviewEvidenceViewer.vue `
  frontend/src/views/ReviewQueueView.vue
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/smoke_check.py --skip-tests
```

Expected: no `user_data` changes; changed paths are limited to the P2-06 spec/plan, frontend viewer sources/tests/styles and quick checklist; production viewer has no confirm/fetch/Blob/object URL/localStorage/Canvas path; smoke exits 0. Do not run full pytest unless a documented risk trigger occurs.

- [x] **Step 3: Compare real database fingerprints without opening SQLite**

From the repository root, repeat Task 1’s `Get-Item` and `Get-FileHash` commands. Compare exact name/size/UTC/SHA-256 tuples to the recorded Task 1 baseline.

Expected: both tuples are byte-for-byte identical. Any mismatch is a blocker; do not update the baseline to the new values.

- [x] **Step 4: Create the implemented quick-test checklist**

After the actual browser environment has been run and verified, create `docs/user-testing/checkpoints/P2-06-evidence-viewer-quick.md` from `USER_TEST_TEMPLATE.md`. Fill the actual validated start command, address, visible marker and close method. The checklist must use only anonymous generated records and include these user actions:

1. Open “阅卷” and confirm the current record shows `答卷证据查看器` and `裁剪证据`.
2. Switch `原卷正面` and `原卷反面`; confirm only one page is visible and the current student does not change.
3. Use `适应宽度`, `原比例`, `缩小`, `放大`, `向左旋转`, `向右旋转`.
4. Drag a zoomed image and focus the canvas to try `Z`, `0`, `+`, `-` and arrow keys.
5. Use J/K or previous/next to change records; confirm the viewer returns to裁剪证据、适应宽度和 0°.
6. Open the generated failed-media record, confirm the queue remains usable, use `重新加载`, then switch to an available source.
7. Confirm there is no score input, save, confirm, image editing or download action, and no horizontal page scrollbar.

Do not mark the machine result or user result `passed`; user feedback remains pending.

- [x] **Step 5: Commit the verified feature handoff**

Update completed checkboxes and add the dated evidence. Change the actual handoff block to:

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
git add docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md docs/user-testing/checkpoints/P2-06-evidence-viewer-quick.md
git diff --cached --check
git commit -m "docs: prepare P2-06 review handoff"
& 'D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe' tools/handoff_status.py `
  --plan docs/superpowers/plans/2026-07-13-p2-06-evidence-viewer-implementation.md `
  --repo .
```

Expected: validator returns `ok=true`, `state="waiting_review"`, and no issues. Stop the implementation phase for independent review; do not self-mark review or user acceptance passed.

---

## 2026-07-13 实施与自动验证证据

- 用户在同地址媒体重试连续两次修复失败触发安全停机后明确回复“继续”，授权仅针对该问题继续根因调查。最终确认画布的指针捕获抢走了错误按钮点击；修复为交互按钮不启动拖动，并新增单元与 Chromium 回归，未扩大执行包范围。
- 前端质量门禁：`lint` 通过；`typecheck` 通过；Vitest `18` 个文件、`144` 项测试全部通过；生产构建通过，Vite 转换 `1644` 个模块。
- 受影响浏览器门禁：`review-evidence.spec.ts`、`review-queue.spec.ts`、`app-shell.spec.ts`、`app.spec.ts` 共 `24` 项 Chromium 测试全部通过；其中 P2-05/P2-06 两套专项共 `11` 项通过，覆盖五种桌面分辨率、程序生成 1200×800 裁剪图、4096×4096 原卷图、像素抽样、快速切换、重试和单活动图片边界。
- 仓库快速冒烟 `tools/smoke_check.py --skip-tests` 通过：文档治理、404 个第一方 Python 文件静态编译、两库隔离副本初始化幂等均通过；按执行包规则未运行全量 pytest。
- 生产查看器未新增后端路由、分数写入、确认请求、Blob/Object URL、localStorage、生产 Canvas 或图片持久化；`canvas` 命中仅为查看器画布 DOM/CSS 命名。
- 根工作区真实数据库只读复核与领取基线完全一致：`grading_system.db|2863104|2026-07-10T07:10:41.1221109Z|93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`；`question_bank.db|3461120|2026-07-08T11:58:06.3320883Z|E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`。未打开 SQLite，源码工作树的 `user_data/` 状态为空。
- 快速用户验收清单已生成：`docs/user-testing/checkpoints/P2-06-evidence-viewer-quick.md`；机器结果和用户结论保持 `pending`，等待独立复审后的精确 SHA。
- 独立复审：首轮完整范围复审为 `0 Critical / 2 Important / 2 Minor`；两项重要问题分别为小图轴向居中边界和拖动时指针生命周期。修复采用聚焦 RED→GREEN，并补齐错误按钮键盘隔离和真实浏览器延迟旧响应覆盖。复审者随后对修复提交及最终测试增强逐次只读复核，最终候选 `887d374345f8d7dc300b6ff2049fd5f238d7faa5` 为 `0 Critical / 0 Important / 0 Minor`，结论 `Ready to merge: Yes`。
- 复审修复后门禁：lint 无警告、typecheck、build 均通过；Vitest `18` 个文件、`146` 项测试全部通过；P2-05/P2-06 Chromium 专项 `12` 项全部通过；快速冒烟再次通过文档治理、404 个第一方 Python 文件静态编译和两库隔离副本幂等检查。
- 用户短测：用户在应用内浏览器对已复审功能 SHA `887d374345f8d7dc300b6ff2049fd5f238d7faa5` 完成匿名短测并明确回复“通过”。验收只使用 4 条匿名记录和程序生成图片；临时服务已停止。清单证据提交为 `93b2a20152cb1d77799e4d23904c49b1736f10be`。
- 用户测试后真实两库指纹仍为阅卷库 `2863104 / 93FEE56E23EA072AC48351B1E6616D7AF7F4B35CEB2B4779890E8D059FB841CD`、题库 `3461120 / E1E5123AD54C9E8AF5984BDCC5182A8F7A3038A1707F98AB26F168F4577A88B8`，与领取基线一致。

---

## Post-Implementation Review and Integration Gates

1. Independent review inspects the complete P2-06 feature range for Critical/Important findings, package scope, image ownership assumptions, transform math, stale events, accessible controls, one-image memory boundary, generated PNG test validity and absence of write operations.
2. Review fixes follow RED → GREEN and rerun only the failing/affected tests plus required package gates. The same issue failing twice triggers safe stop.
3. After review passes, create a plan-only `waiting_user` anchor commit recording the full reviewed feature SHA, then run the P2-06 quick test against exactly that SHA.
4. If the user explicitly reports `passed`, create the single checklist-only evidence commit recording the same reviewed SHA, followed by a plan-only `verified_pending_integration` handoff commit. Never infer user acceptance from automated browser tests.
5. Integration starts from latest `origin/main`, merges the complete P2-06 chain once, runs P2-06/P2-05 affected frontend regression, then the wave-end complete smoke/frontend gates and real-database fingerprint guard. Integration updates `ARCHITECTURE.md` and `EXECUTION_INDEX.md`, pushes an integration branch, creates a PR to GitHub `main`, merges after checks, and synchronizes local `main`/active worktrees. Never push `main` directly.
