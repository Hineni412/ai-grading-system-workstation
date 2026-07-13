import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem, ReviewQuestionSummary } from '../api/review'
import { reviewShortcutBus } from '../composables/review-shortcuts'
import ReviewQueueView from '../views/ReviewQueueView.vue'
import { createAppRouter } from '../router'
import { useReviewQueueStore } from '../stores/review-queue'
import { useSessionStore } from '../stores/session'

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
  annotated_front_url: '/api/front?variant=annotated',
  annotated_back_url: '/api/back?variant=annotated',
}

const item = (index: number, overrides: Partial<ReviewItem> = {}): ReviewItem => ({
  session_id: 7,
  result_id: index,
  detail_id: index,
  question_id: 'Q1',
  student_code: `S${String(index).padStart(3, '0')}`,
  student_name: `学生${index}`,
  class_name: '七年级一班',
  score_awarded: 3,
  max_score: 5,
  deduction_reason: null,
  error_category: null,
  error_summary: null,
  confidence_score: 90,
  needs_review: false,
  candidate_scores: [],
  metadata: {},
  media,
  ...overrides,
})

const questions: ReviewQuestionSummary[] = [
  { question_id: 'Q1', total_count: 2, needs_review_count: 1, max_score: 5 },
  { question_id: 'Q2', total_count: 2, needs_review_count: 1, max_score: 5 },
]

const itemsByQuestion: Record<string, ReviewItem[]> = {
  Q1: [
    item(11, { student_name: '学生甲', needs_review: true, confidence_score: 65 }),
    item(12, { student_name: '学生乙', class_name: '七年级二班' }),
  ],
  Q2: [
    item(21, { question_id: 'Q2', student_name: '学生丙' }),
    item(22, {
      question_id: 'Q2',
      student_code: null,
      student_name: '学生丁',
      class_name: null,
      confidence_score: null,
      needs_review: true,
      error_summary: '步骤需要人工核对',
    }),
  ],
}

interface MountOptions {
  sessionId?: number | null
  initialUrl?: string
  reviewQuestions?: ReviewQuestionSummary[]
  reviewItems?: Record<string, ReviewItem[]>
  failItemLoad?: boolean
  itemLoader?: (
    sessionId: number,
    questionId: string,
    signal?: AbortSignal,
  ) => Promise<ReviewItem[]>
  configureRouter?: (router: Router) => void
}

const mountedApps: App[] = []

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView({
  sessionId = 7,
  initialUrl = '/grading',
  reviewQuestions = questions,
  reviewItems = itemsByQuestion,
  failItemLoad = false,
  itemLoader,
  configureRouter,
}: MountOptions = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  configureRouter?.(router)
  await router.push(initialUrl)
  await router.isReady()

  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({
    sessions: sessionId === null
      ? []
      : [{
          id: sessionId,
          name: '七年级数学期末质量监测',
          status: 'grading',
          is_deleted: false,
          deleted_at: null,
          created_at: null,
          updated_at: null,
        }],
    selectedSessionId: sessionId,
    loadState: 'ready',
  })

  const reviewStore = useReviewQueueStore(pinia)
  const loadQuestions = reviewStore.loadQuestions
  const loadItems = reviewStore.loadItems
  const loadQuestionsSpy = vi
    .spyOn(reviewStore, 'loadQuestions')
    .mockImplementation((requestedSessionId) =>
      loadQuestions(requestedSessionId, async () => reviewQuestions),
    )
  const loadItemsSpy = vi
    .spyOn(reviewStore, 'loadItems')
    .mockImplementation((requestedSessionId, questionId) =>
      loadItems(requestedSessionId, questionId, async (_sessionId, _questionId, signal) => {
        if (failItemLoad) throw new Error('private item failure')
        if (itemLoader) return itemLoader(requestedSessionId, questionId, signal)
        return reviewItems[questionId] ?? []
      }),
    )

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ReviewQueueView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mountedApps.push(app)
  await settleUi()

  return {
    host,
    pinia,
    router,
    reviewStore,
    sessionStore,
    loadItemsDirect: loadItems,
    loadQuestionsSpy,
    loadItemsSpy,
    unmount: () => {
      const index = mountedApps.indexOf(app)
      if (index >= 0) mountedApps.splice(index, 1)
      app.unmount()
    },
  }
}

function inputValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', {
    bubbles: true,
  }))
}

function dispatchKey(target: EventTarget, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(event)
  return event
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
})

describe('P2-05 review queue view', () => {
  it('shows the no-session state without requesting review data', async () => {
    const { host, loadQuestionsSpy, loadItemsSpy } = await mountView({ sessionId: null })

    expect(host.textContent).toContain('请先选择考试')
    expect(loadQuestionsSpy).not.toHaveBeenCalled()
    expect(loadItemsSpy).not.toHaveBeenCalled()
  })

  it('restores a valid question and detail from the URL', async () => {
    const { host, router, loadItemsSpy } = await mountView({
      initialUrl: '/grading?question=Q2&detail=22&unknown=discard',
    })

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query).toEqual({ question: 'Q2', detail: '22' })
    })
    expect(loadItemsSpy).toHaveBeenCalledWith(7, 'Q2')
    expect(host.textContent).toContain('题目')
    expect(host.textContent).toContain('搜索学生')
    expect(host.textContent).toContain('复核范围')
    expect(host.textContent).toContain('排序方式')
    expect(host.textContent).toContain('学生丁')
    expect(host.textContent).toContain('未提供学号')
    expect(host.textContent).toContain('置信度未提供')
    expect(host.textContent).toContain('待复核')
    expect(host.textContent).toContain('步骤需要人工核对')
  })

  it('replaces invalid question and detail query values with validated defaults', async () => {
    const { host, router } = await mountView({
      initialUrl: '/grading?question=missing&detail=not-a-number&unknown=discard',
    })

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '11' })
    })
    expect(host.textContent).toContain('学生甲')
    expect(host.textContent).toContain('当前位置 1 / 2')
  })

  it('recovers URL synchronization after one router replacement is rejected', async () => {
    let replaceCalls = 0
    const { router } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11&unknown=discard',
      configureRouter: (configuredRouter) => {
        const actualReplace = configuredRouter.replace.bind(configuredRouter)
        vi.spyOn(configuredRouter, 'replace').mockImplementation(async (target) => {
          replaceCalls += 1
          if (replaceCalls === 1) {
            throw new Error('expected route replacement rejection')
          }
          return actualReplace(target)
        })
      },
    })

    await vi.waitFor(() => {
      expect(replaceCalls).toBe(2)
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '11' })
    })
  })

  it('preserves the selected item when filters keep it and selects the first when excluded', async () => {
    const { host, router, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q1&detail=12',
    })
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(12))

    const search = host.querySelector<HTMLInputElement>('#review-search')!
    inputValue(search, '学生乙')
    await settleUi()
    expect(reviewStore.selectedDetailId).toBe(12)
    expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '12' })

    inputValue(search, '学生甲')
    await vi.waitFor(() => {
      expect(reviewStore.selectedDetailId).toBe(11)
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '11' })
    })
    expect(host.textContent).toContain('学生甲')

    inputValue(search, '不存在的学生')
    await vi.waitFor(() => {
      expect(reviewStore.selectedDetailId).toBeNull()
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1' })
    })
    expect(host.textContent).toContain('当前筛选没有记录')
    expect(
      host.querySelector('.review-selection-summary')?.hasAttribute('aria-labelledby'),
    ).toBe(false)
  })

  it('moves with J/K but ignores shortcuts from input, select, button, textarea, and contenteditable', async () => {
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11',
    })
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(11))

    const next = dispatchKey(window, 'j')
    expect(reviewStore.selectedDetailId).toBe(12)
    expect(next.defaultPrevented).toBe(true)
    const previous = dispatchKey(window, 'K')
    expect(reviewStore.selectedDetailId).toBe(11)
    expect(previous.defaultPrevented).toBe(true)

    const textarea = document.createElement('textarea')
    const editable = document.createElement('div')
    editable.setAttribute('contenteditable', 'true')
    const emptyEditable = document.createElement('div')
    emptyEditable.setAttribute('contenteditable', '')
    const emptyEditableChild = document.createElement('span')
    emptyEditable.append(emptyEditableChild)
    const plaintextEditable = document.createElement('div')
    plaintextEditable.setAttribute('contenteditable', 'plaintext-only')
    const plaintextEditableChild = document.createElement('span')
    plaintextEditable.append(plaintextEditableChild)
    host.append(textarea, editable, emptyEditable, plaintextEditable)
    const protectedTargets = [
      host.querySelector<HTMLInputElement>('input')!,
      host.querySelector<HTMLSelectElement>('select')!,
      host.querySelector<HTMLButtonElement>('button')!,
      textarea,
      editable,
      emptyEditableChild,
      plaintextEditableChild,
    ]
    for (const target of protectedTargets) {
      reviewStore.selectDetail(11)
      expect(dispatchKey(target, 'j').defaultPrevented).toBe(false)
      expect(reviewStore.selectedDetailId).toBe(11)
    }

    expect(dispatchKey(window, 'j', { ctrlKey: true }).defaultPrevented).toBe(false)
    expect(reviewStore.selectedDetailId).toBe(11)

    for (const modifiers of [
      { altKey: true },
      { metaKey: true },
      { shiftKey: true },
    ]) {
      reviewStore.selectDetail(11)
      expect(dispatchKey(window, 'j', modifiers).defaultPrevented).toBe(false)
      expect(reviewStore.selectedDetailId).toBe(11)
    }

    const alreadyPrevented = new KeyboardEvent('keydown', {
      key: 'j',
      bubbles: true,
      cancelable: true,
    })
    alreadyPrevented.preventDefault()
    window.dispatchEvent(alreadyPrevented)
    expect(alreadyPrevented.defaultPrevented).toBe(true)
    expect(reviewStore.selectedDetailId).toBe(11)
  })

  it('routes the approved workspace shortcuts, focuses search, and leaves R unassigned', async () => {
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11',
    })
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(11))
    const received: string[] = []
    const stop = reviewShortcutBus.subscribe((command) => received.push(command))

    const search = host.querySelector<HTMLInputElement>('#review-search')!
    const searchShortcut = dispatchKey(window, '/')
    expect(searchShortcut.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(search)

    search.blur()
    for (const [key, init] of [
      ['z', {}],
      ['+', {}],
      ['=', {}],
      ['-', {}],
      ['Enter', {}],
      ['Enter', { shiftKey: true }],
    ] satisfies Array<[string, KeyboardEventInit]>) {
      expect(dispatchKey(window, key, init).defaultPrevented).toBe(true)
    }

    expect(received).toEqual([
      'focus-search',
      'fit-width',
      'zoom-in',
      'zoom-in',
      'zoom-out',
      'confirm-stay',
      'confirm-next',
    ])

    expect(dispatchKey(window, 'r').defaultPrevented).toBe(false)
    expect(received).not.toContain('mark-review')

    search.focus()
    expect(dispatchKey(search, 'z').defaultPrevented).toBe(false)
    expect(dispatchKey(window, 'Enter', { repeat: true }).defaultPrevented).toBe(false)
    expect(dispatchKey(window, '/', { ctrlKey: true }).defaultPrevented).toBe(false)
    expect(received).toHaveLength(7)
    stop()
  })

  it('scrolls the selected row into view after moving across a page boundary', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const pageBoundaryItems = Array.from({ length: 101 }, (_, offset) =>
      item(offset + 1, {
        student_code: `S${String(offset + 1).padStart(3, '0')}`,
        student_name: `学生${String(offset + 1).padStart(3, '0')}`,
      }),
    )
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q1&detail=100',
      reviewItems: { Q1: pageBoundaryItems },
    })
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(100))
    scrollIntoView.mockClear()

    dispatchKey(window, 'j')
    await vi.waitFor(() => {
      expect(reviewStore.selectedDetailId).toBe(101)
      expect(reviewStore.page).toBe(2)
      expect(host.querySelector('.review-queue-row[aria-current="true"]')?.textContent)
        .toContain('学生101')
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    })
  })

  it('cancels pending row scrolling when selection is invalidated or the view unmounts', async () => {
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const { reviewStore, unmount } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11',
    })
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(11))
    scrollIntoView.mockClear()

    reviewStore.selectDetail(12)
    reviewStore.setSearch('不存在的学生')
    await settleUi()
    expect(reviewStore.selectedDetailId).toBeNull()
    expect(scrollIntoView).not.toHaveBeenCalled()

    reviewStore.setSearch('')
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(11))
    scrollIntoView.mockClear()
    reviewStore.selectDetail(12)
    unmount()
    await settleUi()
    expect(scrollIntoView).not.toHaveBeenCalled()
  })

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

  it('resets the viewer when selecting the next record', async () => {
    const firstMedia = { ...media, crop_url: '/api/crop/11' }
    const secondMedia = { ...media, crop_url: '/api/crop/12' }
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11',
      reviewItems: {
        Q1: [
          item(11, { student_name: '学生甲', media: firstMedia }),
          item(12, { student_name: '学生乙', media: secondMedia }),
        ],
      },
    })
    await vi.waitFor(() => {
      expect(reviewStore.selectedDetailId).toBe(11)
      expect(reviewStore.itemLoadState).toBe('ready')
    })
    await settleUi()
    const next = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '下一条')!
    next.click()
    await vi.waitFor(() => expect(reviewStore.selectedDetailId).toBe(12))
    await vi.waitFor(() => {
      expect(host.textContent).toContain('裁剪证据')
      expect(host.textContent).toContain('适应宽度')
      const images = host.querySelectorAll<HTMLImageElement>('.review-evidence-viewer img')
      expect(images).toHaveLength(1)
      expect(images[0]?.getAttribute('src')).toBe('/api/crop/12')
    })
  })

  it('uses a blocking state when the first item load fails', async () => {
    const { host } = await mountView({ failItemLoad: true })

    await vi.waitFor(() => expect(host.textContent).toContain('复核队列加载失败'))
    expect(host.querySelector('[data-testid="feedback-banner"]')).toBeNull()
    expect(host.querySelector('[data-testid="state-panel"][data-kind="error"]')).not.toBeNull()
  })

  it('keeps old items visible with non-blocking feedback when refresh fails', async () => {
    const { host, reviewStore, loadItemsDirect, loadItemsSpy } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('学生甲'))

    await loadItemsDirect(7, 'Q1', async () => {
      throw new Error('private refresh failure')
    })
    await settleUi()

    expect(host.querySelector('[data-testid="feedback-banner"]')).not.toBeNull()
    expect(host.textContent).toContain('复核队列暂时无法读取，已保留上次内容。')
    expect(host.textContent).toContain('学生甲')

    const retryButton = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === '重新加载',
    )!
    retryButton.click()
    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="feedback-banner"]')).toBeNull()
      expect(reviewStore.itemLoadState).toBe('ready')
      expect(reviewStore.errorMessage).toBe('')
    })
    expect(loadItemsSpy).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain('学生甲')
  })

  it('aborts a stale question request, resets detail, and keeps only the latest URL context', async () => {
    const pendingQ2 = deferred<ReviewItem[]>()
    let q2Signal: AbortSignal | undefined
    const q3Item = item(31, { question_id: 'Q3', student_name: '学生戊' })
    const { host, router, reviewStore } = await mountView({
      reviewQuestions: [
        ...questions,
        { question_id: 'Q3', total_count: 1, needs_review_count: 0, max_score: 5 },
      ],
      itemLoader: async (_sessionId, questionId, signal) => {
        if (questionId === 'Q2') {
          q2Signal = signal
          return pendingQ2.promise
        }
        if (questionId === 'Q3') return [q3Item]
        return itemsByQuestion[questionId] ?? []
      },
    })
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '11' })
    })

    const questionSelect = host.querySelector<HTMLSelectElement>('#review-question')!
    inputValue(questionSelect, 'Q2')
    await vi.waitFor(() => {
      expect(reviewStore.selectedQuestionId).toBe('Q2')
      expect(reviewStore.selectedDetailId).toBeNull()
      expect(router.currentRoute.value.query).toEqual({ question: 'Q2' })
    })
    expect(reviewStore.itemLoadState).toBe('loading')
    expect(host.contains(questionSelect)).toBe(true)
    expect(questionSelect.disabled).toBe(false)

    inputValue(questionSelect, 'Q3')
    await vi.waitFor(() => {
      expect(q2Signal?.aborted).toBe(true)
      expect(reviewStore.items.map((entry) => entry.question_id)).toEqual(['Q3'])
      expect(router.currentRoute.value.query).toEqual({ question: 'Q3', detail: '31' })
    })

    pendingQ2.resolve(itemsByQuestion.Q2 ?? [])
    await settleUi()
    expect(reviewStore.items.map((entry) => entry.question_id)).toEqual(['Q3'])
    expect(router.currentRoute.value.query).toEqual({ question: 'Q3', detail: '31' })
  })

  it('aborts a pending request on unmount and ignores its late result', async () => {
    const pendingItems = deferred<ReviewItem[]>()
    let pendingSignal: AbortSignal | undefined
    const { reviewStore, unmount } = await mountView({
      itemLoader: async (_sessionId, _questionId, signal) => {
        pendingSignal = signal
        return pendingItems.promise
      },
    })
    await vi.waitFor(() => {
      expect(reviewStore.itemLoadState).toBe('loading')
      expect(pendingSignal).toBeDefined()
    })

    unmount()
    expect(pendingSignal?.aborted).toBe(true)
    expect(reviewStore.items).toEqual([])
    expect(reviewStore.itemLoadState).toBe('idle')

    pendingItems.resolve([item(71)])
    await settleUi()
    expect(reviewStore.items).toEqual([])
    expect(reviewStore.itemLoadState).toBe('idle')
  })

  it('aborts the old session request and ignores its result after the new session loads', async () => {
    const pendingSessionSeven = deferred<ReviewItem[]>()
    let sessionSevenSignal: AbortSignal | undefined
    const sessionNineItem = item(91, { session_id: 9, student_name: '新考试学生' })
    const { router, reviewStore, sessionStore } = await mountView({
      itemLoader: async (sessionId, _questionId, signal) => {
        if (sessionId === 7) {
          sessionSevenSignal = signal
          return pendingSessionSeven.promise
        }
        return [sessionNineItem]
      },
    })
    await vi.waitFor(() => {
      expect(reviewStore.itemLoadState).toBe('loading')
      expect(sessionSevenSignal).toBeDefined()
    })

    sessionStore.$patch({
      sessions: [
        ...sessionStore.sessions,
        {
          id: 9,
          name: '九年级数学测试',
          status: 'grading',
          is_deleted: false,
          deleted_at: null,
          created_at: null,
          updated_at: null,
        },
      ],
      selectedSessionId: 9,
    })
    await vi.waitFor(() => {
      expect(sessionSevenSignal?.aborted).toBe(true)
      expect(reviewStore.items.map((entry) => entry.detail_id)).toEqual([91])
      expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '91' })
    })

    pendingSessionSeven.resolve([item(71)])
    await settleUi()
    expect(reviewStore.items.map((entry) => entry.detail_id)).toEqual([91])
    expect(router.currentRoute.value.query).toEqual({ question: 'Q1', detail: '91' })
  })
})
