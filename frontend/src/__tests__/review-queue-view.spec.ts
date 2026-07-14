import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  confirmReviewItem,
  confirmReviewItems,
  fetchReviewItems,
  fetchReviewRubric,
  type FetchReviewItemsOptions,
  type ReviewItem,
  type ReviewQuestionSummary,
} from '../api/review'
import { createAppRouter } from '../router'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useReviewQueueStore } from '../stores/review-queue'
import { useSessionStore } from '../stores/session'
import ReviewQueueView from '../views/ReviewQueueView.vue'

vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  confirmReviewItem: vi.fn(),
  confirmReviewItems: vi.fn(),
  fetchReviewItems: vi.fn(),
  fetchReviewRubric: vi.fn(),
}))

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
  needs_review: true,
  candidate_scores: [],
  metadata: {},
  media,
  ...overrides,
})

const questions: ReviewQuestionSummary[] = [
  { question_id: 'Q1', total_count: 2, needs_review_count: 2, max_score: 5 },
  { question_id: 'Q2', total_count: 2, needs_review_count: 1, max_score: 5 },
]

const itemsByQuestion: Record<string, ReviewItem[]> = {
  Q1: [
    item(11, { student_name: '学生甲', confidence_score: 65 }),
    item(12, { student_name: '学生乙', class_name: '七年级二班' }),
  ],
  Q2: [
    item(21, { question_id: 'Q2', student_name: '学生丙', needs_review: false }),
    item(22, {
      question_id: 'Q2',
      student_code: null,
      student_name: '学生丁',
      class_name: null,
      confidence_score: null,
      error_summary: '步骤需要人工核对',
    }),
  ],
}

interface MountOptions {
  sessionId?: number | null
  sessionLoadState?: 'idle' | 'loading' | 'ready' | 'error'
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
  sessionLoadState = 'ready',
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
    loadState: sessionLoadState,
  })

  const reviewStore = useReviewQueueStore(pinia)
  const loadQuestionsDirect = reviewStore.loadQuestions
  const loadItemsDirect = reviewStore.loadItems
  const itemRequests: FetchReviewItemsOptions[] = []
  const loadQuestionsSpy = vi
    .spyOn(reviewStore, 'loadQuestions')
    .mockImplementation((requestedSessionId) =>
      loadQuestionsDirect(requestedSessionId, async () => reviewQuestions),
    )
  const loadItemsSpy = vi
    .spyOn(reviewStore, 'loadItems')
    .mockImplementation((requestedSessionId, questionId, _loader, needsReviewOnly) =>
      loadItemsDirect(requestedSessionId, questionId, async (_sessionId, _questionId, options) => {
        itemRequests.push(options)
        if (failItemLoad) throw new Error('private item failure')
        if (itemLoader) return itemLoader(requestedSessionId, questionId, options.signal)
        const available = reviewItems[questionId] ?? []
        return options.needsReviewOnly
          ? available.filter((entry) => entry.needs_review)
          : available
      }, needsReviewOnly),
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
    itemRequests,
    loadItemsDirect,
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
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  vi.mocked(confirmReviewItems).mockResolvedValue({
    updated_details: 2,
    updated_results: 2,
    annotation_outcomes: [],
  })
  vi.mocked(confirmReviewItem).mockResolvedValue({
    updated_details: 1,
    updated_results: 1,
    annotation_outcomes: [],
  })
  vi.mocked(fetchReviewItems).mockResolvedValue([])
  vi.mocked(fetchReviewRubric).mockResolvedValue(null)
  document.body.innerHTML = ''
  localStorage.clear()
})

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
})

describe('source-recalibrated review view', () => {
  it('confirms the visible pending batch once and accepts unchanged scores', async () => {
    const pending = [
      item(11, { score_awarded: 3 }),
      item(12, { score_awarded: 2.5 }),
    ]
    const { host, pinia } = await mountView({
      reviewQuestions: [
        { question_id: 'Q1', total_count: 2, needs_review_count: 2, max_score: 5 },
      ],
      reviewItems: { Q1: pending },
    })

    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!
    button.click()
    button.click()

    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledTimes(1))
    expect(confirmReviewItems).toHaveBeenCalledWith(7, 'Q1', [
      { result_id: 11, detail_id: 11, score_awarded: 3 },
      { result_id: 12, detail_id: 12, score_awarded: 2.5 },
    ])
    await vi.waitFor(() => expect(useReviewQueueStore(pinia).questions[0]?.needs_review_count).toBe(0))
    expect(useReviewQueueStore(pinia).items.every((entry) => !entry.needs_review)).toBe(true)
    expect(useReviewDraftStore(pinia).drafts).toEqual({})
  })

  it('keeps every batch draft when confirmation fails', async () => {
    vi.mocked(confirmReviewItems).mockRejectedValue(new Error('private server failure'))
    const { host, pinia } = await mountView()
    const firstScore = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-11"]')!
    inputValue(firstScore, '4')
    await nextTick()

    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')?.disabled,
    ).toBe(false))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()

    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('确认失败，整批草稿已保留'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:11']?.scoreText).toBe('4')
    expect(useReviewQueueStore(pinia).items.every((entry) => entry.needs_review)).toBe(true)
    expect(document.body.textContent).not.toContain('private server failure')
  })

  it('keeps saved scores and retries annotations only after an explicit action', async () => {
    vi.mocked(confirmReviewItems)
      .mockResolvedValueOnce({
        updated_details: 2,
        updated_results: 2,
        annotation_outcomes: [
          { result_id: 11, status: 'retry_required' },
          { result_id: 12, status: 'retry_required' },
        ],
      })
      .mockResolvedValueOnce({
        updated_details: 2,
        updated_results: 2,
        annotation_outcomes: [
          { result_id: 11, status: 'succeeded' },
          { result_id: 12, status: 'succeeded' },
        ],
      })
    const { host } = await mountView()

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('分数已保存，2 份标注图需要重试'))
    expect(confirmReviewItems).toHaveBeenCalledTimes(1)

    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '重试标注图')!
    retry.click()
    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(host.textContent).not.toContain('标注图需要重试'))
  })

  it('does not let a completed batch from a previous session mutate the new session view', async () => {
    const pendingConfirmation = deferred<Awaited<ReturnType<typeof confirmReviewItems>>>()
    vi.mocked(confirmReviewItems).mockReturnValue(pendingConfirmation.promise)
    const { host, reviewStore, sessionStore } = await mountView({
      itemLoader: async (sessionId, questionId) => (itemsByQuestion[questionId] ?? []).map(
        (entry) => ({ ...entry, session_id: sessionId, result_id: sessionId * 100 + entry.detail_id }),
      ),
    })

    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')?.disabled,
    ).toBe(false))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledTimes(1))
    sessionStore.$patch({
      sessions: [{
        id: 8,
        name: '另一场考试',
        status: 'grading',
        is_deleted: false,
        deleted_at: null,
        created_at: null,
        updated_at: null,
      }],
      selectedSessionId: 8,
      loadState: 'ready',
    })
    await vi.waitFor(() => expect(reviewStore.items[0]?.session_id).toBe(8))

    pendingConfirmation.resolve({
      updated_details: 2,
      updated_results: 2,
      annotation_outcomes: [],
    })
    await settleUi()

    expect(reviewStore.items.every((entry) => entry.session_id === 8 && entry.needs_review)).toBe(true)
    expect(reviewStore.questions[0]?.needs_review_count).toBe(2)
    expect(reviewStore.selectedQuestionId).toBe('Q1')
  })

  it('keeps single-review annotation retry available after returning to the batch', async () => {
    vi.mocked(confirmReviewItem).mockResolvedValue({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [{ result_id: 11, status: 'retry_required' }],
    })
    vi.mocked(fetchReviewItems).mockResolvedValue([itemsByQuestion.Q1![1]!])
    vi.mocked(confirmReviewItems).mockResolvedValue({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [{ result_id: 11, status: 'succeeded' }],
    })
    const { host, reviewStore } = await mountView()
    const firstCard = host.querySelector<HTMLElement>('[data-detail-id="11"]')!
    firstCard.querySelector<HTMLButtonElement>('.review-answer-sheet__deep')!.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull())
    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')?.disabled,
    ).toBe(false))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()

    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(reviewStore.questions[0]?.needs_review_count).toBe(1))
    await vi.waitFor(() => expect(host.textContent).toContain('分数已保存，1 份标注图需要重试'))
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).not.toBeNull()
    expect(reviewStore.questions[0]?.needs_review_count).toBe(1)

    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '重试标注图')!
    retry.click()
    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledWith(7, 'Q1', [{
      result_id: 11,
      detail_id: 11,
      score_awarded: 3,
    }]))
    await vi.waitFor(() => expect(host.textContent).not.toContain('标注图需要重试'))
  })

  it('keeps single-review annotation retry when selection changes during submission', async () => {
    const pendingSingle = deferred<Awaited<ReturnType<typeof confirmReviewItem>>>()
    vi.mocked(confirmReviewItem).mockReturnValue(pendingSingle.promise)
    const { host, reviewStore } = await mountView()
    host.querySelector<HTMLElement>('[data-detail-id="11"]')
      ?.querySelector<HTMLButtonElement>('.review-answer-sheet__deep')
      ?.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull())
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    dispatchKey(window, 'j')
    expect(reviewStore.selectedDetailId).toBe(12)
    pendingSingle.resolve({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [{ result_id: 11, status: 'retry_required' }],
    })

    await vi.waitFor(() => expect(host.textContent).toContain('分数已保存，1 份标注图需要重试'))
    expect(reviewStore.selectedDetailId).toBe(12)
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '重试标注图')!
    retry.click()
    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledWith(7, 'Q1', [{
      result_id: 11,
      detail_id: 11,
      score_awarded: 3,
    }]))
  })

  it('merges retry entries across questions and retries each ownership group explicitly', async () => {
    vi.mocked(confirmReviewItems)
      .mockResolvedValueOnce({
        updated_details: 2,
        updated_results: 2,
        annotation_outcomes: [
          { result_id: 11, status: 'retry_required' },
          { result_id: 12, status: 'retry_required' },
        ],
      })
      .mockResolvedValueOnce({
        updated_details: 1,
        updated_results: 1,
        annotation_outcomes: [{ result_id: 22, status: 'retry_required' }],
      })
      .mockResolvedValueOnce({
        updated_details: 2,
        updated_results: 2,
        annotation_outcomes: [
          { result_id: 11, status: 'succeeded' },
          { result_id: 12, status: 'succeeded' },
        ],
      })
      .mockResolvedValueOnce({
        updated_details: 1,
        updated_results: 1,
        annotation_outcomes: [{ result_id: 22, status: 'succeeded' }],
      })
    const { host } = await mountView()

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('分数已保存，2 份标注图需要重试'))
    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('[data-question-id="Q2"]')?.getAttribute('aria-current'),
    ).toBe('true'))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('分数已保存，3 份标注图需要重试'))

    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '重试标注图')!
    retry.click()
    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledTimes(4))
    expect(confirmReviewItems).toHaveBeenNthCalledWith(3, 7, 'Q1', [
      { result_id: 11, detail_id: 11, score_awarded: 3 },
      { result_id: 12, detail_id: 12, score_awarded: 3 },
    ])
    expect(confirmReviewItems).toHaveBeenNthCalledWith(4, 7, 'Q2', [
      { result_id: 22, detail_id: 22, score_awarded: 3 },
    ])
    await vi.waitFor(() => expect(host.textContent).not.toContain('标注图需要重试'))
  })

  it('shows the no-session state without requesting review data', async () => {
    const { host, loadQuestionsSpy, loadItemsSpy } = await mountView({ sessionId: null })

    expect(host.textContent).toContain('请先选择考试')
    expect(loadQuestionsSpy).not.toHaveBeenCalled()
    expect(loadItemsSpy).not.toHaveBeenCalled()
  })

  it('restores a valid question and pending detail from the URL', async () => {
    const { host, router, reviewStore } = await mountView({
      initialUrl: '/grading?question=Q2&detail=22',
    })

    await vi.waitFor(() => expect(reviewStore.selectedQuestionId).toBe('Q2'))
    expect(reviewStore.selectedDetailId).toBe(22)
    expect(router.currentRoute.value.query).toEqual({ question: 'Q2', detail: '22' })
    expect(host.textContent).toContain('学生丁')
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).toBeNull()
  })

  it('replaces invalid URL context with the first pending question item', async () => {
    const { router, reviewStore } = await mountView({
      initialUrl: '/grading?question=missing&detail=not-a-number',
    })

    await vi.waitFor(() => expect(reviewStore.selectedQuestionId).toBe('Q1'))
    expect(reviewStore.selectedDetailId).toBe(11)
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({
      question: 'Q1',
    }))
  })

  it('replaces the batch workspace during deep review and restores filters and drafts on return', async () => {
    const { host, router, reviewStore } = await mountView()
    const search = host.querySelector<HTMLInputElement>('#review-search')!
    inputValue(search, '学生甲')
    const score = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-11"]')!
    inputValue(score, '4')

    const deepButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '深查此份答卷')!
    deepButton.click()
    await nextTick()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).toBeNull()
    expect(reviewStore.selectedQuestionId).toBe('Q1')
    expect(reviewStore.selectedDetailId).toBe(11)
    expect(reviewStore.items.some((entry) => entry.detail_id === 11)).toBe(true)
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({
      question: 'Q1',
      detail: '11',
    }))

    host.querySelector<HTMLButtonElement>('[data-testid="back-to-batch"]')!.click()
    await nextTick()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).toBeNull()
    expect(host.querySelector<HTMLInputElement>('#review-search')?.value).toBe('学生甲')
    expect(host.querySelector<HTMLInputElement>('[data-testid="teacher-score-11"]')?.value).toBe('4')
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ question: 'Q1' }))
  })

  it('defaults to pending reads and reloads all items only after explicit scope change', async () => {
    const { host, itemRequests } = await mountView({
      reviewItems: {
        ...itemsByQuestion,
        Q1: [itemsByQuestion.Q1![0]!, { ...itemsByQuestion.Q1![1]!, needs_review: false }],
      },
    })
    expect(itemRequests[0]?.needsReviewOnly).toBe(true)
    expect(host.textContent).not.toContain('学生乙')

    inputValue(host.querySelector<HTMLSelectElement>('#review-scope')!, 'all')

    await vi.waitFor(() => expect(itemRequests[itemRequests.length - 1]?.needsReviewOnly).toBe(false))
    await vi.waitFor(() => expect(host.textContent).toContain('学生乙'))
  })

  it('rolls the scope label back when loading the requested scope fails', async () => {
    let failAll = false
    const { host, reviewStore } = await mountView({
      itemLoader: async (_sessionId, questionId) => {
        if (failAll) throw new Error('scope load failed')
        return itemsByQuestion[questionId] ?? []
      },
    })
    await vi.waitFor(() => expect(reviewStore.items).toHaveLength(2))
    failAll = true
    inputValue(host.querySelector<HTMLSelectElement>('#review-scope')!, 'all')

    await vi.waitFor(() => expect(host.textContent).toContain('复核内容刷新失败'))
    expect(reviewStore.scope).toBe('needs_review')
    expect(host.querySelector<HTMLSelectElement>('#review-scope')?.value).toBe('needs_review')
    expect(reviewStore.items.map((entry) => entry.detail_id)).toEqual([11, 12])
  })

  it('searches identity fields and slash focuses search outside protected controls', async () => {
    const { host } = await mountView()
    const search = host.querySelector<HTMLInputElement>('#review-search')!

    const slash = dispatchKey(window, '/')
    expect(slash.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(search)

    search.blur()
    expect(dispatchKey(search, '/').defaultPrevented).toBe(false)
    inputValue(search, '学生甲')
    await nextTick()
    expect(host.querySelectorAll('[data-testid="review-answer-sheet"]')).toHaveLength(1)
  })

  it('uses a blocking first-load error and a non-blocking retained-content error', async () => {
    const first = await mountView({ failItemLoad: true })
    await vi.waitFor(() => expect(first.host.textContent).toContain('复核内容加载失败'))
    expect(first.host.querySelector('[data-testid="state-panel"][data-kind="error"]')).not.toBeNull()
    first.unmount()

    const retained = await mountView()
    await retained.loadItemsDirect(7, 'Q1', async () => {
      throw new Error('private refresh detail')
    })
    await nextTick()
    expect(retained.host.querySelector('[data-testid="feedback-banner"]')).not.toBeNull()
    expect(retained.host.textContent).toContain('学生甲')
    expect(retained.host.textContent).not.toContain('private refresh detail')
  })

  it('aborts a stale question request and keeps only the latest context', async () => {
    const q2 = deferred<ReviewItem[]>()
    let q2Signal: AbortSignal | undefined
    const { host, router, reviewStore } = await mountView({
      itemLoader: async (_sessionId, questionId, signal) => {
        if (questionId === 'Q2') {
          q2Signal = signal
          return q2.promise
        }
        return itemsByQuestion.Q1!
      },
    })

    const q2Button = await vi.waitFor(() => {
      const button = host.querySelector<HTMLButtonElement>('[data-question-id="Q2"]')
      expect(button).not.toBeNull()
      return button!
    })
    q2Button.click()
    await vi.waitFor(() => expect(q2Signal).toBeDefined())
    host.querySelector<HTMLButtonElement>('[data-question-id="Q1"]')!.click()
    await vi.waitFor(() => expect(q2Signal?.aborted).toBe(true))
    q2.resolve(itemsByQuestion.Q2!)

    await vi.waitFor(() => {
      expect(reviewStore.selectedQuestionId).toBe('Q1')
      expect(reviewStore.items.map((entry) => entry.detail_id)).toEqual([11, 12])
    })
    expect(router.currentRoute.value.query.question).toBe('Q1')
  })

  it('aborts a pending item request on unmount and ignores its late result', async () => {
    const pending = deferred<ReviewItem[]>()
    let signal: AbortSignal | undefined
    const mounted = await mountView({
      itemLoader: async (_sessionId, _questionId, nextSignal) => {
        signal = nextSignal
        return pending.promise
      },
    })
    mounted.unmount()

    expect(signal?.aborted).toBe(true)
    pending.resolve([item(99)])
    await settleUi()
    expect(mounted.reviewStore.items).toEqual([])
  })
})
