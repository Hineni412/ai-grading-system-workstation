import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, type App } from 'vue';
import { createMemoryHistory, type Router } from 'vue-router';

import { fetchResultsCenter, type ResultsCenterResponse } from '../api/results-center';
import { confirmReviewItem, confirmReviewItems, fetchReviewItems, fetchReviewRubric, type FetchReviewItemsOptions, type ReviewItem, type ReviewQuestionSummary } from '../api/review';
import { createAppRouter } from '../router';

import { useReviewDraftStore } from '../stores/review-drafts';
import { useReviewQueueStore } from '../stores/review-queue';
import { useSessionStore } from '../stores/session';
import ReviewQueueView from '../views/ReviewQueueView.vue'

vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  confirmReviewItem: vi.fn(),
  confirmReviewItems: vi.fn(),
  fetchReviewItems: vi.fn(),
  fetchReviewRubric: vi.fn(),
}))

vi.mock('../api/results-center', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/results-center')>(),
  fetchResultsCenter: vi.fn(),
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
  {
    question_id: 'Q1',
    question_type: 'choice',
    total_count: 2,
    needs_review_count: 2,
    ungraded_count: 0,
    teacher_confirmed_count: 0,
    max_score: 5,
  },
  {
    question_id: 'Q2',
    question_type: 'proof',
    total_count: 2,
    needs_review_count: 1,
    ungraded_count: 0,
    teacher_confirmed_count: 1,
    max_score: 5,
  },
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
  waitForItems?: boolean
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
  waitForItems = true,
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
      loadQuestionsDirect(requestedSessionId, async (_sessionId, _signal, options = {}) => {
        const updated = reviewQuestions.map((question) => {
          return {
            ...question,
            teacher_confirmed_count: question.teacher_confirmed_count ?? 0,
            ungraded_count: question.ungraded_count ?? 0,
          }
        })
        if (options.scope === 'teacher_pending') {
          return updated.filter(
            (question) => question.needs_review_count + question.ungraded_count > 0,
          )
        }
        if (options.scope === 'ungraded') {
          return updated.filter((question) => question.ungraded_count > 0)
        }
        if (options.scope === 'ai_review') {
          return updated.filter((question) => question.needs_review_count > 0)
        }
        if (options.scope === 'teacher_final') {
          return updated.filter((question) => question.teacher_confirmed_count > 0)
        }
        return updated
      }),
    )
  const loadItemsSpy = vi
    .spyOn(reviewStore, 'loadItems')
    .mockImplementation((requestedSessionId, questionId, _loader, needsReviewOnly) =>
      loadItemsDirect(requestedSessionId, questionId, async (_sessionId, _questionId, options) => {
        itemRequests.push(options)
        if (failItemLoad) throw new Error('private item failure')
        const available = itemLoader
          ? await itemLoader(requestedSessionId, questionId, options.signal)
          : (reviewItems[questionId] ?? [])
        const updated = available
        if (options.needsReviewOnly) return updated.filter((entry) => entry.needs_review)
        if (options.scope === 'teacher_pending') {
          return updated.filter((entry) => entry.needs_review || entry.score_status === 'ungraded')
        }
        if (options.scope === 'ungraded') {
          return updated.filter((entry) => entry.score_status === 'ungraded')
        }
        if (options.scope === 'ai_review') {
          return updated.filter((entry) => entry.needs_review)
        }
        if (options.scope === 'teacher_final') {
          return updated.filter((entry) => entry.score_status === 'teacher_final')
        }
        return updated
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
  if (sessionId !== null && sessionLoadState === 'ready' && waitForItems) {
    await vi.waitFor(() => expect(reviewStore.questionLoadState).not.toBe('loading'))
    await vi.waitFor(() => expect(reviewStore.itemLoadState).not.toBe('loading'))
  }

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
  vi.mocked(fetchReviewItems).mockImplementation(async (sessionId, questionId) =>
    (itemsByQuestion[questionId] ?? []).map((entry) => ({
      ...entry,
      session_id: sessionId,
      score_status: entry.needs_review ? 'ai_review' : 'teacher_final',
      teacher_locked: !entry.needs_review,
    })),
  )
  vi.mocked(fetchReviewRubric).mockResolvedValue(null)
  document.body.innerHTML = ''
  localStorage.clear()
})

afterEach(async () => {
  for (const app of mountedApps.splice(0)) app.unmount()
  await settleUi()
})

describe('source-recalibrated review view', () => {

  it('keeps every batch draft when confirmation fails', async () => {
    vi.mocked(confirmReviewItems).mockRejectedValue(new Error('private server failure'))
    const { host, pinia } = await mountView()
    const firstScore = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
    inputValue(firstScore, '4')
    await nextTick()

    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')?.disabled,
    ).toBe(false))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!.click()

    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('确认失败，整批草稿已保留'))
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:11']?.scoreText).toBe('4')
    expect(useReviewQueueStore(pinia).items.every((entry) => entry.needs_review)).toBe(true)
    expect(document.body.textContent).not.toContain('private server failure')
  })

  it('moves across the 24/25 boundary and submits every page once from the final score', async () => {
    const wholeClass = Array.from({ length: 25 }, (_, offset) => item(offset + 1))
    const { host, reviewStore } = await mountView({
      reviewQuestions: [{
        question_id: 'Q1',
        question_type: 'choice',
        total_count: 25,
        needs_review_count: 25,
        ungraded_count: 0,
        teacher_confirmed_count: 0,
        max_score: 5,
      }],
      reviewItems: { Q1: wholeClass },
    })
    const pageOneLast = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-23"]')!

    pageOneLast.focus()
    const tab = dispatchKey(pageOneLast, 'Tab')
    await vi.waitFor(() => expect(reviewStore.page).toBe(2))
    await vi.waitFor(() => expect(
      document.activeElement,
    ).toBe(host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')))
    expect(tab.defaultPrevented).toBe(true)

    const finalScore = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
    dispatchKey(finalScore, 'Enter')

    await vi.waitFor(() => expect(confirmReviewItems).toHaveBeenCalledTimes(1))
    expect(vi.mocked(confirmReviewItems).mock.calls[0]?.[2]).toHaveLength(25)
    expect(vi.mocked(confirmReviewItems).mock.calls[0]?.[2].map((entry) => entry.detail_id))
      .toEqual(Array.from({ length: 25 }, (_, offset) => offset + 1))
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

  it('replaces the batch workspace during deep review and restores filters and drafts on return', async () => {
    const { host, router, reviewStore } = await mountView()
    const search = host.querySelector<HTMLInputElement>('#review-search')!
    inputValue(search, '学生甲')
    const score = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
    inputValue(score, '4')

    const deepButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.getAttribute('aria-label') === '深查答卷')!
    deepButton.click()
    await nextTick()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).toBeNull()
    expect(reviewStore.selectedQuestionId).toBe('Q1')
    expect(reviewStore.selectedDetailId).toBe(11)
    expect(reviewStore.items.some((entry) => entry.detail_id === 11)).toBe(true)
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({
      scope: 'all',
      session: '7',
      question: 'Q1',
      item: '7:Q1:11',
    }))

    host.querySelector<HTMLButtonElement>('[data-testid="back-to-batch"]')!.click()
    await nextTick()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).toBeNull()
    expect(host.querySelector<HTMLInputElement>('#review-search')?.value).toBe('学生甲')
    expect(host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')?.value).toBe('4')
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({
      scope: 'all',
      session: '7',
      question: 'Q1',
    }))
  })

  it('shows the per-student question strip from results and switches questions within the student', async () => {
    const stripResults: ResultsCenterResponse = {
      session_id: 7,
      session_name: '合成成绩验证',
      summary: {
        student_count: 1, complete_student_count: 1, average_sample_count: 1,
        average_score: 7, highest_score: 7, lowest_score: 7, max_score: 10,
        ungraded_item_count: 0, failed_item_count: 0, needs_review_item_count: 1,
        ai_ready_item_count: 1, teacher_final_item_count: 0,
      },
      questions: [
        { question_id: 'Q1', max_score: 5, total_count: 1, ungraded_count: 0, failed_count: 0, needs_review_count: 1, ai_ready_count: 0, teacher_final_count: 0, average_score: 3 },
        { question_id: 'Q2', max_score: 5, total_count: 1, ungraded_count: 0, failed_count: 0, needs_review_count: 0, ai_ready_count: 1, teacher_final_count: 0, average_score: 4 },
      ],
      students: [{
        student_id: 11, student_code: 'S011', student_name: '学生甲', class_name: '七年级一班',
        pinyin_initials: 'xsj', pinyin_full: 'xueshengjia',
        current_score: 7, max_score: 10,
        ungraded_count: 0, failed_count: 0, needs_review_count: 1, status: 'needs_review',
        items: [
          {
            review_item_id: '7:Q1:11', question_id: 'Q1', score_awarded: 3, max_score: 5,
            score_status: 'ai_review', score_source: 'ai', confidence_score: 65,
            needs_review: true, review_reason: null, result_id: 11, detail_id: 11,
          },
          {
            review_item_id: '7:Q2:11', question_id: 'Q2', score_awarded: 4, max_score: 5,
            score_status: 'ai_ready', score_source: 'ai', confidence_score: 90,
            needs_review: false, review_reason: null, result_id: 12, detail_id: 12,
          },
        ],
      }],
    }
    vi.mocked(fetchResultsCenter).mockResolvedValue(stripResults)
    const { host, reviewStore, router } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: {
        Q1: [item(11), item(12)],
        Q2: [item(11, { question_id: 'Q2', score_awarded: 4, needs_review: false }), item(21, { question_id: 'Q2' })],
      },
    })

    const strip = await vi.waitFor(() => {
      const element = host.querySelector('[data-testid="student-question-strip"]')
      expect(element).not.toBeNull()
      return element!
    })
    const buttons = [...strip.querySelectorAll<HTMLButtonElement>('button')]
    expect(buttons.map((button) => button.querySelector('strong')?.textContent)).toEqual(['Q1', 'Q2'])
    expect(buttons[0]!.getAttribute('aria-current')).toBe('true')
    expect(buttons[0]!.textContent).toContain('3 / 5')
    expect(buttons[0]!.textContent).toContain('待复核')
    expect(buttons[1]!.textContent).toContain('4 / 5')

    buttons[1]!.click()
    await vi.waitFor(() => {
      expect(reviewStore.selectedQuestionId).toBe('Q2')
      expect(reviewStore.selectedReviewItemId).toBe('7:Q2:11')
    })
    expect(router.currentRoute.value.query).toMatchObject({
      entry: 'results',
      student: '11',
      question: 'Q2',
      item: '7:Q2:11',
    })
    await vi.waitFor(() => {
      const current = host.querySelector('[data-testid="student-question-strip"]')
      expect(current).not.toBeNull()
      expect(current!.querySelectorAll('button')[1]!.getAttribute('aria-current')).toBe('true')
    })
  })
})
