import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem, ReviewQuestionSummary } from '../api/review'
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
}: MountOptions = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
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
      loadItems(requestedSessionId, questionId, async () => {
        if (failItemLoad) throw new Error('private item failure')
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
    router,
    reviewStore,
    loadItemsDirect: loadItems,
    loadQuestionsSpy,
    loadItemsSpy,
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

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
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
    host.append(textarea, editable)
    const protectedTargets = [
      host.querySelector<HTMLInputElement>('input')!,
      host.querySelector<HTMLSelectElement>('select')!,
      host.querySelector<HTMLButtonElement>('button')!,
      textarea,
      editable,
    ]
    for (const target of protectedTargets) {
      expect(dispatchKey(target, 'j').defaultPrevented).toBe(false)
      expect(reviewStore.selectedDetailId).toBe(11)
    }

    expect(dispatchKey(window, 'j', { ctrlKey: true }).defaultPrevented).toBe(false)
    expect(reviewStore.selectedDetailId).toBe(11)
  })

  it('never renders score inputs, save buttons, confirm buttons, or image elements', async () => {
    const { host } = await mountView({
      initialUrl: '/grading?question=Q1&detail=11',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('当前得分 3 / 5'))

    expect(host.textContent).toContain('答卷证据将在 P2-06 接入；评分与确认将在 P2-07 接入。')
    expect(host.querySelector('input[type="number"]')).toBeNull()
    expect(host.querySelector('textarea, [contenteditable="true"], img')).toBeNull()
    expect(
      [...host.querySelectorAll('button')].some((button) => /保存|确认/.test(button.textContent ?? '')),
    ).toBe(false)
  })

  it('uses a blocking state when the first item load fails', async () => {
    const { host } = await mountView({ failItemLoad: true })

    await vi.waitFor(() => expect(host.textContent).toContain('复核队列加载失败'))
    expect(host.querySelector('[data-testid="feedback-banner"]')).toBeNull()
    expect(host.querySelector('[data-testid="state-panel"][data-kind="error"]')).not.toBeNull()
  })

  it('keeps old items visible with non-blocking feedback when refresh fails', async () => {
    const { host, loadItemsDirect } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('学生甲'))

    await loadItemsDirect(7, 'Q1', async () => {
      throw new Error('private refresh failure')
    })
    await settleUi()

    expect(host.querySelector('[data-testid="feedback-banner"]')).not.toBeNull()
    expect(host.textContent).toContain('复核队列暂时无法读取，已保留上次内容。')
    expect(host.textContent).toContain('学生甲')
  })
})
