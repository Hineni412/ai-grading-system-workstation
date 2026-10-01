import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, ref, type App } from 'vue';
import { createMemoryHistory, type Router } from 'vue-router';

import { fetchResultsCenter, type ResultsCenterResponse } from '../api/results-center';
import { confirmReviewItem, confirmReviewItems, fetchReviewItems, fetchReviewRubric, type FetchReviewItemsOptions, type ReviewItem, type ReviewQuestionSummary } from '../api/review';
import { createAppRouter } from '../router';

import { useReviewDraftStore } from '../stores/review-drafts';
import { useReviewQueueStore } from '../stores/review-queue';
import { useResultsCenterStore } from '../stores/results-center';
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

vi.mock('../api/class-analysis', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/class-analysis')>()
  return {
    ...original,
    classAnalysisApi: {
      ...original.classAnalysisApi,
      getQuestionPreview: vi.fn().mockRejectedValue(new Error('no preview in tests')),
    },
  }
})

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
const routeDisposers: (() => void)[] = []

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
  for (const dispose of routeDisposers.splice(0)) dispose()
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
    const buttons = [...strip.querySelectorAll<HTMLButtonElement>('.review-student-strip__chip')]
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
      expect(current!.querySelectorAll('.review-student-strip__chip')[1]!.getAttribute('aria-current')).toBe('true')
    })

    // ← 在同一学生的题条中回到上一题。
    dispatchKey(document.body, 'ArrowLeft')
    await vi.waitFor(() => {
      expect(reviewStore.selectedQuestionId).toBe('Q1')
      expect(reviewStore.selectedReviewItemId).toBe('7:Q1:11')
    })
  })

  it('keeps the deep workspace mounted while a cross-question switch is loading', async () => {
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
    const pending = deferred<ReviewItem[]>()
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: {
        Q1: [item(11), item(12)],
        Q2: [item(11, { question_id: 'Q2', score_awarded: 4, needs_review: false })],
      },
      itemLoader: async (_sessionId, questionId) =>
        questionId === 'Q2' ? pending.promise : (itemsByQuestion[questionId] ?? []),
    })

    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="review-deep-workspace"]'),
    ).not.toBeNull())
    const chip = [...host.querySelectorAll<HTMLButtonElement>('.review-student-strip__chip')]
      .find((button) => button.querySelector('strong')?.textContent === 'Q2')!
    chip.click()

    // 目标题还在加载：深查工作区继续显示旧答卷，批量工作区绝不闪现。
    await nextTick()
    await nextTick()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).toBeNull()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(host.querySelector('.review-student-strip__loading')).not.toBeNull()
    expect(host.textContent).toContain('学生甲')

    pending.resolve([item(11, { question_id: 'Q2', score_awarded: 4, needs_review: false })])
    await vi.waitFor(() => {
      expect(reviewStore.selectedQuestionId).toBe('Q2')
      expect(reviewStore.selectedReviewItemId).toBe('7:Q2:11')
    })
    await settleUi()
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="review-batch-workspace"]')).toBeNull()
    expect(host.querySelector('.review-student-strip__loading')).toBeNull()
  })

  it('stays on the deep page and refocuses the quick bar after confirming a results-entry item', async () => {
    const { host, reviewStore, router } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: { Q1: [item(11), item(12)] },
    })
    const confirmButton = await vi.waitFor(() => {
      const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')
      expect(button?.disabled).toBe(false)
      return button!
    })
    // 成绩入口：按钮不再提示“并返回”
    expect(confirmButton.textContent).toBe('确认此份')
    confirmButton.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
    await settleUi()
    // 不返回成绩明细：仍停在 /grading 的同一答卷上
    expect(router.currentRoute.value.path).toBe('/grading')
    expect(router.currentRoute.value.query).toMatchObject({ entry: 'results', item: '7:Q1:11' })
    expect(reviewStore.selectedReviewItemId).toBe('7:Q1:11')
    expect(host.querySelector('[data-testid="review-deep-workspace"]')).not.toBeNull()
    expect(document.activeElement).toBe(
      host.querySelector<HTMLInputElement>('.review-quick-score input'),
    )
  })

  it('resizes the answer panel with divider keys, resets on double click, and keeps page arrows', async () => {
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: { Q1: [item(11), item(12)] },
    })
    host.querySelector<HTMLButtonElement>('[title="显示或隐藏原题与参考答案"]')!.click()
    const divider = await vi.waitFor(() => {
      const element = host.querySelector<HTMLElement>('.review-deep-workspace__divider')
      expect(element).not.toBeNull()
      return element!
    })
    expect(divider.getAttribute('role')).toBe('separator')
    expect(divider.getAttribute('aria-orientation')).toBe('vertical')
    expect(divider.getAttribute('aria-valuenow')).toBe('420')
    dispatchKey(divider, 'ArrowRight')
    await nextTick()
    expect(divider.getAttribute('aria-valuenow')).toBe('444')
    // 分隔条上的方向键用于调宽度，不触发页面级题间切换
    expect(reviewStore.selectedQuestionId).toBe('Q1')
    expect(localStorage.getItem('ai-grading:review-answer-panel-width:v1')).toBe('444')
    divider.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await nextTick()
    expect(divider.getAttribute('aria-valuenow')).toBe('420')
  })

  it('lets arrow keys inside quick score inputs switch questions', async () => {
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
    const { host, reviewStore } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: {
        Q1: [item(11), item(12)],
        Q2: [item(11, { question_id: 'Q2', score_awarded: 4, needs_review: false })],
      },
    })
    const quick = await vi.waitFor(() => {
      const input = host.querySelector<HTMLInputElement>('.review-quick-score input')
      expect(input).not.toBeNull()
      return input!
    })
    dispatchKey(quick, 'ArrowRight')
    await vi.waitFor(() => expect(reviewStore.selectedQuestionId).toBe('Q2'))
    dispatchKey(document.activeElement ?? document.body, 'ArrowLeft')
    await vi.waitFor(() => expect(reviewStore.selectedQuestionId).toBe('Q1'))
  })

  it('moves between students in the stored matrix order, skipping students without the item', async () => {
    const navItem = (questionId: string, id: number, score = 3) => ({
      review_item_id: `7:${questionId}:${id}`,
      question_id: questionId,
      score_awarded: score,
      max_score: 5,
      score_status: 'ai_review' as const,
      score_source: 'ai' as const,
      confidence_score: 65,
      needs_review: true,
      review_reason: null,
      result_id: id,
      detail_id: id,
    })
    const navStudent = (id: number, name: string, items: ReturnType<typeof navItem>[]) => ({
      student_id: id,
      student_code: `S${String(id).padStart(3, '0')}`,
      student_name: name,
      class_name: '七年级一班',
      pinyin_initials: 'xs',
      pinyin_full: 'xuesheng',
      current_score: 8,
      max_score: 10,
      ungraded_count: 0,
      failed_count: 0,
      needs_review_count: 1,
      status: 'needs_review' as const,
      items,
    })
    const navResults: ResultsCenterResponse = {
      session_id: 7,
      session_name: '顺序验证',
      summary: {
        student_count: 3, complete_student_count: 3, average_sample_count: 3,
        average_score: 8, highest_score: 8, lowest_score: 8, max_score: 10,
        ungraded_item_count: 0, failed_item_count: 0, needs_review_item_count: 3,
        ai_ready_item_count: 0, teacher_final_item_count: 0,
      },
      questions: [
        { question_id: 'Q1', max_score: 5, total_count: 2, ungraded_count: 0, failed_count: 0, needs_review_count: 2, ai_ready_count: 0, teacher_final_count: 0, average_score: 3 },
        { question_id: 'Q2', max_score: 5, total_count: 3, ungraded_count: 0, failed_count: 0, needs_review_count: 0, ai_ready_count: 3, teacher_final_count: 0, average_score: 4 },
      ],
      students: [
        navStudent(11, '学生甲', [navItem('Q1', 11), navItem('Q2', 11)]),
        navStudent(12, '学生乙', [navItem('Q2', 12)]),
        navStudent(13, '学生丙', [navItem('Q1', 13), navItem('Q2', 13)]),
      ],
    }
    vi.mocked(fetchResultsCenter).mockResolvedValue(navResults)
    const { host, pinia, router, reviewStore } = await mountView({
      initialUrl: '/grading?session=7&question=Q1&item=7:Q1:11&entry=results&student=11',
      reviewItems: {
        Q1: [item(11, { student_name: '学生甲' }), item(13, { student_name: '学生丙' })],
        Q2: [item(11, { question_id: 'Q2' }), item(12, { question_id: 'Q2' }), item(13, { question_id: 'Q2' })],
      },
    })
    useResultsCenterStore(pinia).setReviewNavigation({
      sessionId: 7,
      studentIds: [11, 12, 13],
    })
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="student-question-strip"]'),
    ).not.toBeNull())
    expect(host.textContent).toContain('第 1 / 3 人')

    // 学生乙没有 Q1 记录，↓ 应直接跳到学生丙。
    dispatchKey(document.body, 'ArrowDown')
    await vi.waitFor(() => expect(reviewStore.selectedReviewItemId).toBe('7:Q1:13'))
    await vi.waitFor(() => expect(router.currentRoute.value.query.student).toBe('13'))
    expect(host.textContent).toContain('第 3 / 3 人')
    const nextButton = [
      ...host.querySelectorAll<HTMLButtonElement>('.review-deep-workspace__student-nav > button'),
    ].slice(-1)[0]!
    expect(nextButton.disabled).toBe(true)

    dispatchKey(document.body, 'ArrowUp')
    await vi.waitFor(() => expect(reviewStore.selectedReviewItemId).toBe('7:Q1:11'))
    expect(host.textContent).toContain('学生甲')
  })
})

describe('review queue route state', () => {
  async function connect(initialUrl: string) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push(initialUrl)
    await router.isReady()
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '合成复核路由考试', status: 'grading', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7, loadState: 'ready',
    })
    const queue = useReviewQueueStore(pinia)
    const loadQuestions = queue.loadQuestions
    vi.spyOn(queue, 'loadQuestions').mockImplementation((sessionId) =>
      loadQuestions(sessionId, async () => questions),
    )
    routeDisposers.push(queue.connectRoute(router, sessions, ref(null)))
    await vi.waitFor(() => expect(queue.itemLoadState).toBe('ready'))
    await queue.waitForRouteSync()
    return { queue, router }
  }

  it('restores a legacy detail link and writes the validated current identity', async () => {
    const { queue, router } = await connect('/grading?session=7&question=Q2&detail=22&scope=all')
    expect(queue.mode).toBe('deep')
    expect(queue.selectedQuestionId).toBe('Q2')
    expect(queue.selectedDetailId).toBe(22)
    expect(router.currentRoute.value.query).toEqual({
      session: '7', question: 'Q2', item: queue.selectedReviewItemId, scope: 'all',
    })
  })

  it('falls back from invalid question, item and scope without keeping stale parameters', async () => {
    const { queue, router } = await connect('/grading?session=7&question=missing&item=missing&scope=invalid')
    expect(queue.mode).toBe('batch')
    expect(queue.selectedQuestionId).toBe('Q1')
    expect(router.currentRoute.value.query).toEqual({ session: '7', question: 'Q1', scope: 'all' })
  })

  it('does not let queued selection writes change the destination after leaving review', async () => {
    const { queue, router } = await connect('/grading?session=7&question=Q1')
    await router.push('/results?session=7&tab=details')
    queue.selectItem('7:Q1:12')
    queue.syncValidatedQuery()
    await nextTick()
    await queue.waitForRouteSync()
    expect(router.currentRoute.value.fullPath).toBe('/results?session=7&tab=details')
  })

  it('keeps the latest question when an earlier load finishes late', async () => {
    const { queue, router } = await connect('/grading?session=7&question=Q1')
    const older = deferred<ReviewItem[]>()
    vi.mocked(fetchReviewItems).mockImplementation(async (_sessionId, questionId) =>
      questionId === 'Q2' ? older.promise : itemsByQuestion.Q1!,
    )
    const first = queue.selectQuestionFromRoute('Q2')
    await queue.selectQuestionFromRoute('Q1')
    older.resolve(itemsByQuestion.Q2!)
    await first
    await queue.waitForRouteSync()
    expect(queue.selectedQuestionId).toBe('Q1')
    expect(queue.items.every((entry) => entry.question_id === 'Q1')).toBe(true)
    expect(router.currentRoute.value.query.question).toBe('Q1')
  })
})
