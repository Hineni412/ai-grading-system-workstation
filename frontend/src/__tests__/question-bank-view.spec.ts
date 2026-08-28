import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { questionBankApi, type QuestionBankPaper } from '../api/question-bank'
import QuestionBankView from '../views/QuestionBankView.vue'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import QuestionInspector from '../components/question-bank/QuestionInspector.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { createAppRouter } from '../router'

const revision = 'a'.repeat(64)
const item = {
  id: 17,
  revision,
  paper_id: 4,
  question_number: '1',
  question_type: '解答题',
  question_text: '已知 x + y = 3，求证……',
  answer_text: '证明过程',
  difficulty: '6',
  typicality: null,
  reason: null,
  needs_review: false,
  criteria_needs_review: false,
  has_images: false,
  needs_image_review: false,
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
  paper_title: '匿名期末试卷',
  year: '2025',
  province: '广东省',
  city: '深圳市',
  district: null,
  exam_type: '期末',
  grade: '七年级',
  semester: '下学期',
  textbook_version: '北师大版（2024）',
  tags: [],
  asset_urls: [],
  rich_content: {
    available: true,
    question_block_count: 0,
    answer_block_count: 0,
    question_blocks: [],
    answer_blocks: [],
  },
}
const paper: QuestionBankPaper = {
  id: 4,
  title: '匿名期末试卷',
  year: '2025',
  province: '广东省',
  city: '深圳市',
  district: null,
  exam_type: '期末',
  grade: '七年级',
  semester: '下学期',
  folder_name: null,
  textbook_version: '北师大版（2024）',
  curriculum_volume_id: 'bnu24-math-g7-lower',
  import_status: 'imported',
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
  question_count: 1,
  tagged_question_count: 0,
  tagged_any_question_count: 0,
  evidence_question_count: 0,
  criteria_question_count: 0,
  criteria_needs_review_count: 0,
  complete_analysis_count: 0,
  source_type: 'docx',
}

const mounted: Array<ReturnType<typeof createApp>> = []

async function mountView() {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionBankView)
  const pinia = createPinia()
  const router = createAppRouter(createMemoryHistory())
  await router.push('/question-bank')
  await router.isReady()
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => {
    expect(host.textContent).toContain('匿名期末试卷')
  })
  const openPaper = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.includes('查看试题'))
  openPaper?.click()
  await vi.waitFor(() => {
    expect(host.textContent).toContain('已知 x + y = 3')
  })
  return { host, pinia, router }
}

async function openCardMenu(host: HTMLElement, paperTitle: string): Promise<void> {
  const toggle = [...host.querySelectorAll<HTMLButtonElement>('.paper-card__more-toggle')]
    .find((button) => button.getAttribute('aria-label')?.includes(paperTitle))!
  toggle.click()
  await nextTick()
}

function cardMenuItem(label: string): HTMLButtonElement {
  return [...document.querySelectorAll<HTMLButtonElement>('.paper-card__more-menu button')]
    .find((button) => button.textContent?.trim() === label)!
}

function batchBarButton(host: HTMLElement, label: string): HTMLButtonElement {
  const bar = host.querySelector('.paper-batch-bar')!
  return [...bar.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.trim() === label)!
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('question bank workspace', () => {
  it('does not download the full taxonomy catalog while opening the paper library', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/taxonomy/proposals?status=pending&summary=true') {
        return response({
          revision: 7,
          items: [],
          counts: { pending: 0, actionable: 0, historical_unavailable: 0 },
        })
      }
      if (url === '/api/question-bank/taxonomy/catalog') {
        return response({
          revision: 7,
          dimensions: {
            curriculum: [], knowledge: [], ability: [], method: [], thought: [],
            model: [], special_type: [],
          },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('还没有导入试卷'))
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(
      '/api/question-bank/taxonomy/proposals?status=pending&summary=true',
      expect.anything(),
    ))

    expect(fetchSpy.mock.calls.some(
      ([input]) => String(input) === '/api/question-bank/taxonomy/catalog',
    )).toBe(false)
  })

  it('shows that the pending taxonomy count is loading instead of flashing a false zero', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary, {
      pendingTaxonomyCount: 0,
      pendingTaxonomyState: 'loading',
    })
    app.use(pinia)
    app.mount(host)
    mounted.push(app)
    await nextTick()

    const reviewButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('待审核新词'))
    expect(reviewButton?.textContent).toContain('读取中')
    expect(reviewButton?.textContent).not.toContain('0')
  })

  it('groups papers by grade and semester, honors manual folders, and lets teachers collapse a group', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [
      paper,
      {
        ...paper,
        id: 5,
        title: '同学期练习卷',
        updated_at: '2026-07-18T08:30:00Z',
      },
      {
        ...paper,
        id: 6,
        title: '中考函数专题',
        folder_name: '中考专题',
        updated_at: '2026-07-18T08:00:00Z',
      },
    ]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)

    expect(host.textContent).toContain('七年级 · 下学期')
    expect(host.textContent).toContain('按年级学期自动归类')
    expect(host.textContent).toContain('中考专题')
    expect(host.textContent).toContain('自定义文件夹')

    const semesterHeader = [...host.querySelectorAll<HTMLButtonElement>('.paper-folder__header')]
      .find((button) => button.textContent?.includes('七年级 · 下学期'))!
    expect(semesterHeader.getAttribute('aria-expanded')).toBe('true')
    semesterHeader.click()
    await nextTick()
    expect(semesterHeader.getAttribute('aria-expanded')).toBe('false')
    expect(host.textContent).not.toContain('同学期练习卷')
    expect(host.textContent).toContain('中考函数专题')
  })

  it('orders automatic folders and their papers by newest year first', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [
      {
        ...paper,
        id: 10,
        title: '旧年七上卷',
        year: '2024',
        grade: '七年级',
        semester: '上学期',
        updated_at: '2026-07-18T08:00:00Z',
      },
      {
        ...paper,
        id: 11,
        title: '新年七上卷',
        year: '2026',
        grade: '七年级',
        semester: '上学期',
        updated_at: '2026-07-18T08:30:00Z',
      },
      {
        ...paper,
        id: 12,
        title: '八年级旧卷',
        year: '2023',
        grade: '八年级',
        semester: '上学期',
        updated_at: '2026-07-18T09:00:00Z',
      },
    ]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)

    const folders = [...host.querySelectorAll<HTMLElement>('.paper-folder')]
    expect(folders).toHaveLength(2)
    // 七年级组内有 2026 年的试卷，排在只有 2023 年试卷的八年级组前面。
    expect(folders[0]!.textContent).toContain('七年级 · 上学期')
    expect(folders[1]!.textContent).toContain('八年级 · 上学期')

    const titles = [...folders[0]!.querySelectorAll<HTMLButtonElement>('.paper-card__title')]
      .map((button) => button.textContent)
    expect(titles).toEqual(['新年七上卷', '旧年七上卷'])
  })

  it('shows incomplete questions from paper fields even without tracked jobs', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        if (!url.includes('analysis_status=incomplete')) {
          throw new Error(`expected incomplete analysis filter: ${url}`)
        }
        return response({
          items: [
            { ...item, id: 21, paper_id: 4, question_number: '1' },
            { ...item, id: 22, paper_id: 4, question_number: '3' },
          ],
          total: 2,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [{ ...paper, question_count: 5, complete_analysis_count: 3 }]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => {
      expect(host.textContent).toContain('第1、3题分析未完成')
    })
  })

  it('keeps the library visible while refreshing papers in the background', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)

    let resolveRefresh!: (value: { items: QuestionBankPaper[]; total: number }) => void
    const pending = store.loadPapers(() => new Promise((resolve) => {
      resolveRefresh = resolve
    }))
    await nextTick()

    // 刷新进行中列表保持原样，不回到“正在读取试卷库”占位。
    expect(store.papersState).toBe('ready')
    expect(host.textContent).toContain('匿名期末试卷')
    expect(host.textContent).not.toContain('正在读取试卷库')

    resolveRefresh({ items: [{ ...paper, id: 30, title: '刷新后的试卷' }], total: 1 })
    await pending
    expect(store.papersState).toBe('ready')
    expect(host.textContent).toContain('刷新后的试卷')
  })

  it('keeps papers from other teaching semesters visible by default', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    const curriculumScope = useCurriculumScopeStore(pinia)
    curriculumScope.selectedVolumeId = 'bnu24-math-g7-upper'
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    await nextTick()

    const otherSemester = [...host.querySelectorAll<HTMLButtonElement>('.paper-folder__header')]
      .find(button => button.textContent?.includes('其他学期'))
    expect(otherSemester?.getAttribute('aria-expanded')).toBe('true')
    expect(otherSemester?.textContent).toContain('仍可操作')
    expect(host.textContent).toContain('匿名期末试卷')
  })

  it('does not call incomplete tags successful when new taxonomy terms need review', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [{
      ...paper,
      question_count: 5,
      tagged_question_count: 0,
      tagged_any_question_count: 0,
      evidence_question_count: 0,
      criteria_question_count: 0,
      complete_analysis_count: 0,
    }]
    store.papersState = 'ready'
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).startsWith('/api/question-bank/questions?')) {
        return response({
          items: [5, 8, 9, 11, 12].map((id, index) => ({
            ...item,
            id,
            paper_id: 4,
            question_number: String(index + 1),
          })),
          total: 5,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (String(input) === '/api/question-bank/papers') {
        return response({ items: store.papers, total: 1 })
      }
      throw new Error(`unexpected request: ${String(input)}`)
    })
    app.mount(host)
    mounted.push(app)
    useJobStore(pinia).track({
      id: 106,
      job_type: 'tagging_sync',
      payload: { question_ids: [5, 8, 9, 11, 12] },
      result: {
        outcome: 'failed', complete_tagged_count: 0, failed_count: 5,
        review_count: 9, review_question_ids: [5, 8, 9, 11, 12],
      },
      status: 'succeeded', progress: 1, stage: 'tagging_sync', detail: '',
      error: null, cancel_requested: false,
      created_at: '2026-08-08T23:46:00Z', started_at: '2026-08-08T23:46:01Z',
      updated_at: '2026-08-08T23:47:00Z', finished_at: '2026-08-08T23:47:00Z',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('第1、2、3、4、5题产生了待审核新词'))

    expect(host.textContent).not.toContain('AI 解析进度')
    expect(host.textContent).not.toContain('标签、解题证据和训练判定点均已完成')
    expect(host.textContent).not.toContain('解题证据')
  })

  it('keeps criterion review visible on paper cards after jobs disappear', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const opened = vi.fn()
    const app = createApp(PaperLibrary, { onReviewCriteria: opened })
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [{
      ...paper,
      question_count: 12,
      tagged_question_count: 12,
      tagged_any_question_count: 12,
      evidence_question_count: 12,
      criteria_question_count: 12,
      complete_analysis_count: 12,
      criteria_needs_review_count: 2,
    }]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    await nextTick()

    expect(host.textContent).toContain('2 道题判定点待您审核')
    expect(host.textContent).toContain('待审核判定点 2')
    const reviewButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('判定点待审核'))
    expect(reviewButton?.disabled).toBe(false)
    reviewButton?.click()
    await nextTick()
    expect(opened).toHaveBeenCalledOnce()
  })

  it('opens a criterion review panel from the library header', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({
          items: [{ ...paper, criteria_needs_review_count: 1 }],
          total: 1,
        })
      }
      if (url === '/api/question-bank/taxonomy/proposals?status=pending&summary=true') {
        return response({
          revision: 7,
          items: [],
          counts: { pending: 0, actionable: 0, historical_unavailable: 0 },
        })
      }
      if (url.includes('criteria_needs_review=true')) {
        return response({
          items: [{
            ...item,
            question_number: '8',
            question_text: '选择正确选项。',
            criteria_needs_review: true,
          }],
          total: 1,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('匿名期末试卷'))
    const reviewButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('判定点待审核'))
    reviewButton?.click()
    await vi.waitFor(() => {
      expect(document.body.textContent).toContain('这些题目的判定点还需要您核对')
    })
    expect(document.body.textContent).toContain('第 8 题')
    expect(document.body.textContent).toContain('选择正确选项。')
  })

  it('shows criterion review on the paper question list without a live job', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({
          items: [{
            ...paper,
            tagged_question_count: 1,
            tagged_any_question_count: 1,
            evidence_question_count: 1,
            criteria_question_count: 1,
            complete_analysis_count: 1,
            criteria_needs_review_count: 1,
          }],
          total: 1,
        })
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        return response({
          items: [{ ...item, criteria_needs_review: true }],
          total: 1,
          page: 1,
          page_size: 20,
          total_pages: 1,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const { host } = await mountView()
    expect(host.textContent).toContain('1 道题判定点待审核')
    expect(host.querySelector('.qb-question-card__review-flag')?.textContent).toContain('判定点待审核')
  })

  it('warns inside question detail when criteria still need review', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionInspector)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)
    const bank = useQuestionBankStore(pinia)
    bank.detail = {
      ...item,
      criteria_needs_review: true,
      page_range: null,
      assets: [],
      rich_content: {
        available: true,
        question_block_count: 0,
        answer_block_count: 0,
        question_blocks: [],
        answer_blocks: [],
      },
      previews: [],
    }
    bank.detailState = 'ready'
    await nextTick()
    expect(document.body.textContent).toContain('本题判定点待审核')
  })

  it('refreshes all three saved counts when a tagging job reaches terminal state', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [{
      ...paper,
      question_count: 12,
      tagged_question_count: 7,
      tagged_any_question_count: 7,
      evidence_question_count: 7,
      criteria_question_count: 7,
      complete_analysis_count: 7,
    }]
    store.papersState = 'ready'
    const refreshed = {
      ...store.papers[0]!,
      tagged_question_count: 12,
      tagged_any_question_count: 12,
      evidence_question_count: 12,
      criteria_question_count: 12,
      complete_analysis_count: 12,
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/question-bank/papers') {
        return response({ items: [refreshed], total: 1 })
      }
      if (String(input).startsWith('/api/question-bank/questions?')) {
        return response({
          items: Array.from({ length: 12 }, (_value, index) => ({
            ...item,
            id: index + 1,
            paper_id: 4,
            question_number: String(index + 1),
          })),
          total: 12,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      throw new Error(`unexpected request: ${String(input)}`)
    })
    app.mount(host)
    mounted.push(app)
    expect(host.textContent).toContain('标签 7/12')

    useJobStore(pinia).track({
      id: 107,
      job_type: 'tagging_sync',
      payload: { question_ids: [8, 9, 10, 11, 12] },
      result: {
        outcome: 'complete',
        complete_tagged_count: 12,
        evidence_count: 12,
        criteria_count: 12,
        failed_count: 0,
      },
      status: 'succeeded',
      progress: 1,
      stage: 'tagging_sync',
      detail: '',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-09T10:00:00Z',
      started_at: '2026-08-09T10:00:01Z',
      updated_at: '2026-08-09T10:00:02Z',
      finished_at: '2026-08-09T10:00:02Z',
    })

    await vi.waitFor(() => expect(host.textContent).toContain('标签 12/12'))
    expect(host.textContent).toContain('判定点 12/12')
    expect(host.textContent).not.toContain('解题证据')
    expect(host.textContent).not.toContain('AI 解析进度')
    expect(fetchSpy.mock.calls.some((call) => String(call[0]) === '/api/question-bank/papers')).toBe(true)
  })

  it('applies difficulty on release but waits for explicit text filters and AI cost confirmation', async () => {
    const queuedJob = {
      id: 41,
      job_type: 'tagging_sync',
      payload: { question_ids: [17] },
      result: {},
      status: 'queued',
      progress: 0,
      stage: '',
      detail: '',
      error: null,
      cancel_requested: false,
      created_at: '2026-07-18T10:00:00Z',
      started_at: null,
      updated_at: '2026-07-18T10:00:00Z',
      finished_at: null,
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return new Response(JSON.stringify({ items: [paper], total: 1 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        return new Response(JSON.stringify({
          items: [item],
          total: 1,
          page: 1,
          page_size: 20,
          total_pages: 1,
        }), { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        return new Response(JSON.stringify(queuedJob), {
          status: 202,
          headers: { 'content-type': 'application/json' },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { host, pinia } = await mountView()
    await vi.waitFor(() => {
      expect(host.querySelector<HTMLButtonElement>('.question-sort button.is-active')?.textContent)
        .toContain('题号')
      expect(host.querySelector('.difficulty-range.qb-difficulty-filter')).toBeTruthy()
    })
    const baselineCalls = fetchSpy.mock.calls.length
    const initialQuestionRequest = fetchSpy.mock.calls
      .map(([request]) => String(request))
      .find((url) => url.startsWith('/api/question-bank/questions?'))
    expect(initialQuestionRequest).toContain('sort=paper_order')
    const lowerDifficulty = host.querySelector<HTMLInputElement>('input[aria-label="最低难度"]')!
    lowerDifficulty.value = '4'
    lowerDifficulty.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    lowerDifficulty.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(baselineCalls + 1))
    expect(String(fetchSpy.mock.calls[baselineCalls]?.[0])).toContain('difficulty_min=4')

    const callsAfterDifficulty = fetchSpy.mock.calls.length
    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!
    search.value = '二次函数'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    host.querySelector<HTMLElement>('.qb-more-filters summary')!.click()
    await nextTick()
    const examScope = host.querySelector<HTMLInputElement>('input[placeholder="如：函数"]')!
    examScope.value = '期中'
    examScope.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(fetchSpy).toHaveBeenCalledTimes(callsAfterDifficulty)

    const apply = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('应用筛选'))!
    apply.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(callsAfterDifficulty + 1))
    expect(String(fetchSpy.mock.calls[callsAfterDifficulty]?.[0])).toContain(
      'exam_scopes=%E6%9C%9F%E4%B8%AD',
    )

    host.querySelector<HTMLInputElement>('input[aria-label="选择第 1 题"]')!.click()
    await nextTick()
    expect(useQuestionBankStore(pinia).selectedQuestionIds).toEqual([17])
    const openImport = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('上传与 AI 标注'))!
    openImport.click()
    await nextTick()
    let aiButton: HTMLButtonElement | null = null
    await vi.waitFor(() => {
      aiButton = document.body.querySelector<HTMLButtonElement>('.qb-button.is-ai')
      expect(aiButton).not.toBeNull()
      expect(aiButton?.disabled).toBe(false)
    })
    const readyAiButton = aiButton!
    const callsBeforeAi = fetchSpy.mock.calls.length
    readyAiButton.click()
    await nextTick()
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('已选 1 道题'))
    expect(fetchSpy).toHaveBeenCalledTimes(callsBeforeAi)

    confirmSpy.mockReturnValue(true)
    readyAiButton.click()
    readyAiButton.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(callsBeforeAi + 1))
    expect(String(fetchSpy.mock.calls[callsBeforeAi]?.[0])).toBe('/api/question-bank/tagging-jobs')
    expect(JSON.parse(String(fetchSpy.mock.calls[callsBeforeAi]?.[1]?.body))).toEqual({
      question_ids: [17],
      curriculum_volume_id: 'bnu24-math-g7-lower',
    })
  })

  it('disables unchecked rows when the 500-question selection is full', async () => {
    const item501 = { ...item, id: 501, question_number: '501' }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const payload = url === '/api/question-bank/papers'
        ? { items: [paper], total: 1 }
        : {
            items: [item501],
            total: 1,
            page: 1,
            page_size: 20,
            total_pages: 1,
          }
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    const { host, pinia } = await mountView()
    const store = useQuestionBankStore(pinia)
    store.selectedQuestionIds = Array.from({ length: 500 }, (_value, index) => index + 1)
    await nextTick()

    const checkbox = host.querySelector<HTMLInputElement>('input[aria-label="选择第 501 题"]')!
    expect(checkbox.disabled).toBe(true)
    checkbox.click()
    expect(checkbox.checked).toBe(false)
    expect(store.selectedCount).toBe(500)
  })

  it('adds selected question-bank rows to the assembly basket from the ledger toolbar', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({ items: [paper], total: 1 })
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        return response({
          items: [item],
          total: 1,
          page: 1,
          page_size: 20,
          total_pages: 1,
        })
      }
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return response({
          basket_ids: [],
          order_ids: [],
          sections: [],
          title: '',
          header_text: '',
          include_answer: true,
          layout_mode: 'sequential',
          preview_mode: 'teacher',
          revision,
        })
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return response({ items: [], total: 0 })
      }
      if (url === '/api/question-assembly/draft' && init?.method === 'PUT') {
        return response({
          basket_ids: [17],
          order_ids: [17],
          sections: [],
          title: '',
          header_text: '',
          include_answer: true,
          layout_mode: 'sequential',
          preview_mode: 'teacher',
          revision: 'b'.repeat(64),
        })
      }
      if (url.startsWith('/api/question-assembly/questions?')) {
        return response({ items: [], missing_question_ids: [17] })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const { host, router } = await mountView()

    host.querySelector<HTMLInputElement>('input[aria-label="选择第 1 题"]')!.click()
    await nextTick()
    const add = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('加入试卷篮'))!
    add.click()

    await vi.waitFor(() => expect(
      (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
        ([input, init]) => String(input) === '/api/question-assembly/draft'
          && init?.method === 'PUT',
      ),
    ).toBe(true))
    expect(router.currentRoute.value.fullPath).toBe('/question-bank')
    const saveCall = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      ([input, init]) => String(input) === '/api/question-assembly/draft' && init?.method === 'PUT',
    )
    expect(JSON.parse(String(saveCall?.[1]?.body))).toMatchObject({
      expected_revision: revision,
      draft: { basket_ids: [17], order_ids: [17] },
    })
  })

  it('explains an answer-image failure and a failed job synchronization', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp({
      render: () => [h(QuestionInspector), h(QuestionImportJobs)],
    })
    app.use(pinia)
    app.mount(host)
    mounted.push(app)
    const bank = useQuestionBankStore(pinia)
    bank.detail = {
      ...item,
      page_range: null,
      assets: [],
      rich_content: {
        available: true,
        question_block_count: 1,
        answer_block_count: 1,
        question_blocks: [{
          kind: 'paragraph',
          text: '题干',
          segments: [],
          rows: [],
          asset_indexes: [],
          asset_urls: [],
        }],
        answer_blocks: [{
          kind: 'paragraph',
          text: '答案',
          segments: [],
          rows: [],
          asset_indexes: [0],
          asset_urls: ['/api/question-bank/questions/17/assets/0'],
        }],
      },
      previews: [],
    }
    bank.detailState = 'ready'
    await nextTick()
    document.body.querySelector<HTMLElement>('.qb-answer-section summary')!.click()
    await nextTick()
    const answerImage = document.body.querySelector<HTMLImageElement>('img[alt="答案配图"]')!
    answerImage.dispatchEvent(new Event('error'))
    await nextTick()
    expect(document.body.textContent).toContain('图片暂时无法读取')

    const jobs = useJobStore(pinia)
    jobs.track({
      id: 41,
      job_type: 'tagging_sync',
      payload: { question_ids: [17] },
      result: {},
      status: 'failed',
      progress: 1,
      stage: 'failed',
      detail: '',
      error: '任务失败',
      cancel_requested: false,
      created_at: '2026-07-18T10:00:00Z',
      started_at: '2026-07-18T10:00:01Z',
      updated_at: '2026-07-18T10:00:02Z',
      finished_at: '2026-07-18T10:00:02Z',
    })
    jobs.syncErrors[41] = {
      kind: 'network',
      message: '取消请求未能同步，任务可能仍在继续。',
      retryable: true,
      requestId: 'req-cancel',
    }
    await nextTick()
    expect(document.body.textContent).toContain('取消请求未能同步，任务可能仍在继续。')
  })

  it('explains that legacy duplicate records no longer require a restore flow', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionImportJobs)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)

    useJobStore(pinia).track({
      id: 42,
      job_type: 'question_import',
      payload: {},
      result: {
        outcome: 'failed',
        failure_category: 'duplicate_in_trash',
        restore_required: true,
        restore_paper_id: 7,
        retryable: false,
      },
      status: 'succeeded',
      progress: 1,
      stage: 'question_import',
      detail: '',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T10:00:00Z',
      started_at: '2026-08-03T10:00:01Z',
      updated_at: '2026-08-03T10:00:02Z',
      finished_at: '2026-08-03T10:00:02Z',
    })
    await nextTick()

    expect(host.textContent).toContain('旧版删除流程留下的任务记录')
    expect(host.textContent).toContain('无需恢复旧试卷')
    expect(host.textContent).not.toContain('恢复旧记录')
    expect(host.textContent).not.toContain('重试允许的失败项')
  })

  it('edits paper metadata from its card and shows the saved values immediately', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({
      id: 4,
      title: '0526test2',
      year: '2026',
      province: '广东省',
      city: '深圳市',
      district: null,
      exam_type: '阶段练习',
      grade: '七年级',
      semester: '下学期',
      folder_name: '中考专题',
      textbook_version: null,
      updated_at: '2026-07-29 10:30:00.123456',
    }))

    await openCardMenu(host, '匿名期末试卷')
    const edit = cardMenuItem('编辑资料')
    edit.click()
    await nextTick()
    const title = document.body.querySelector<HTMLInputElement>('input[name="paper-title"]')!
    title.value = '0526test2'
    title.dispatchEvent(new Event('input', { bubbles: true }))
    const examType = document.body.querySelector<HTMLInputElement>('input[name="paper-exam-type"]')!
    examType.value = '阶段练习'
    examType.dispatchEvent(new Event('input', { bubbles: true }))
    const folderName = document.body.querySelector<HTMLInputElement>('input[name="paper-folder-name"]')!
    folderName.value = '中考专题'
    folderName.dispatchEvent(new Event('input', { bubbles: true }))
    const save = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('保存资料'))!
    save.click()

    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(host.textContent).toContain('0526test2'))
    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toMatchObject({
      expected_updated_at: paper.updated_at,
      metadata: {
        title: '0526test2',
        exam_type: '阶段练习',
        folder_name: '中考专题',
      },
    })
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
  })

  it('confirms cost and submits a forced retag from the paper card', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        return response({
          items: [item],
          total: 1,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        return response({
          id: 44,
          job_type: 'tagging_sync',
          payload: { question_ids: [17], force_retag_question_ids: [17] },
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-01T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-01T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('重新打标签').click()

    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )).toBe(true))
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('1 道题'))
    const submitCall = fetchSpy.mock.calls.find(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )
    expect(JSON.parse(String(submitCall?.[1]?.body))).toEqual({
      question_ids: [17],
      curriculum_volume_id: 'bnu24-math-g7-lower',
      force_retag: true,
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
    expect(host.textContent).toContain('已提交 1 道题')
  })

  it('submits the whole paper so the server can check current-version gaps', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let questionListUrl = ''
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        if (url.includes('analysis_status=all')) {
          questionListUrl = url
        }
        return response({
          items: [item, { ...item, id: 18 }],
          total: 2,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        return response({
          id: 45,
          job_type: 'tagging_sync',
          payload: { question_ids: [17] },
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-01T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-01T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const fill = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '继续完成未完成题目')!
    fill.click()

    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )).toBe(true))
    expect(questionListUrl).toContain('analysis_status=all')
    const submitCall = fetchSpy.mock.calls.find(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )
    expect(JSON.parse(String(submitCall?.[1]?.body))).toEqual({
      question_ids: [17, 18],
      curriculum_volume_id: 'bnu24-math-g7-lower',
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
    expect(host.textContent).toContain('2 道题等待后端核对')
  })

  it('loads 24 papers from the same curriculum in one question-list request', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    const papers = Array.from({ length: 24 }, (_value, index) => ({
      ...paper,
      id: index + 1,
      title: `同教材试卷 ${index + 1}`,
    }))
    store.papers = papers
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const questionListUrls: string[] = []
    const taggingBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        if (url.includes('analysis_status=all')) {
          questionListUrls.push(url)
        }
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        return response({
          items: paperIds.map((paperId) => ({
            ...item,
            id: 1_000 + paperId,
            paper_id: paperId,
            question_number: String(paperId),
          })),
          total: paperIds.length,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        taggingBodies.push(JSON.parse(String(init.body)))
        return response({
          id: 100,
          job_type: 'tagging_sync',
          payload: {},
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-01T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-01T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const retag = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '全库重新打标签')!
    retag.click()

    await vi.waitFor(() => expect(taggingBodies).toHaveLength(1))
    expect(new URL(questionListUrls[0]!, 'http://local.test').searchParams.getAll('paper_ids'))
      .toEqual(papers.map(({ id }) => String(id)))
    expect(taggingBodies[0]).toEqual({
      question_ids: papers.map(({ id }) => 1_000 + id),
      curriculum_volume_id: 'bnu24-math-g7-lower',
      force_retag: true,
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
  })

  it('keeps grouped all-library fill requests inside their curriculum', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    const papers = [
      { ...paper, id: 1, title: '教材一试卷 1' },
      { ...paper, id: 2, title: '教材一试卷 2' },
      { ...paper, id: 21, title: '教材二试卷 1', curriculum_volume_id: 'bnu24-math-g8-upper' },
      { ...paper, id: 22, title: '教材二试卷 2', curriculum_volume_id: 'bnu24-math-g8-upper' },
    ]
    store.papers = papers
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const questionPaperGroups: number[][] = []
    const taggingBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        questionPaperGroups.push(paperIds)
        return response({
          items: paperIds.map((paperId) => ({
            ...item,
            id: 2_000 + paperId,
            paper_id: paperId,
            question_number: String(paperId),
          })),
          total: paperIds.length,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        taggingBodies.push(JSON.parse(String(init.body)))
        return response({
          id: 200 + taggingBodies.length,
          job_type: 'tagging_sync',
          payload: {},
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-01T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-01T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const fill = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '继续完成未完成题目')!
    fill.click()

    await vi.waitFor(() => expect(taggingBodies).toHaveLength(2))
    expect(questionPaperGroups.slice(0, 2)).toEqual([[1, 2], [21, 22]])
    expect(taggingBodies.map((body) => ({
      question_ids: body.question_ids,
      curriculum_volume_id: body.curriculum_volume_id,
      force_retag: body.force_retag,
    }))).toEqual([
      {
        question_ids: [2_001, 2_002],
        curriculum_volume_id: 'bnu24-math-g7-lower',
        force_retag: undefined,
      },
      {
        question_ids: [2_021, 2_022],
        curriculum_volume_id: 'bnu24-math-g8-upper',
        force_retag: undefined,
      },
    ])
  })

  it('requires one explicit confirmation before permanently deleting an active paper', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    let deleted = false
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async (input, init) => {
        const url = String(input)
        if (url.endsWith('/permanent-deletion-impact')) {
          return response({
            paper_count: 1,
            question_count: 2,
            tag_count: 3,
            analysis_record_count: 6,
            training_link_count: 1,
            knowledge_graph_link_count: 1,
            owned_file_count: 1,
            shared_file_count: 0,
            taxonomy_proposal_count: 2,
            permanent_delete_phrase: '彻底删除 1 份试卷',
          })
        }
        if (url.endsWith('/permanent-delete')) {
          deleted = true
          return response({
            deleted_paper_ids: [paper.id],
            deleted_question_count: 2,
            deleted_tag_count: 3,
            deleted_analysis_record_count: 6,
            removed_training_link_count: 1,
            removed_knowledge_graph_link_count: 1,
            deleted_file_count: 1,
            skipped_shared_file_count: 0,
            storage_cleanup_pending: false,
          })
        }
        if (url === '/api/question-bank/papers') {
          return response({ items: deleted ? [] : [paper], total: deleted ? 0 : 1 })
        }
        throw new Error(`unexpected request: ${url} ${String(init?.method)}`)
      })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    expect(document.body.textContent).toContain('6 条分析记录')
    expect(document.body.textContent).toContain('将清除 2 条待审新词')
    expect(document.body.textContent).toContain('已完成考试的答卷与成绩不受影响')
    expect(fetchSpy.mock.calls.some(([request]) => String(request).endsWith('/permanent-delete'))).toBe(false)

    const cancel = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('取消'))!
    cancel.click()
    await nextTick()
    expect(host.textContent).toContain('匿名期末试卷')
    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认彻底删除'))!
    confirm.click()
    await vi.waitFor(() => expect(store.papers).toHaveLength(0))
    expect(host.textContent).not.toContain('试卷回收站')
  })

  it('reuses the permanent-delete request token when the first response is lost', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    let deleteAttempts = 0
    let deletionConfirmed = false
    const deleteBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({
          items: deletionConfirmed ? [] : [paper],
          total: deletionConfirmed ? 0 : 1,
        })
      }
      if (url.endsWith('/permanent-deletion-impact')) {
        return response({
          paper_count: 1,
          question_count: 2,
          tag_count: 3,
          analysis_record_count: 0,
          training_link_count: 0,
          knowledge_graph_link_count: 0,
          owned_file_count: 1,
          shared_file_count: 0,
          taxonomy_proposal_count: 0,
          permanent_delete_phrase: '彻底删除 1 份试卷',
        })
      }
      if (url.endsWith('/permanent-delete')) {
        deleteAttempts += 1
        deleteBodies.push(JSON.parse(String(init?.body)))
        if (deleteAttempts === 1) {
          throw new TypeError('response lost')
        }
        deletionConfirmed = true
        return response({
          deleted_paper_ids: [paper.id],
          deleted_question_count: 2,
          deleted_tag_count: 3,
          deleted_analysis_record_count: 0,
          removed_training_link_count: 0,
          removed_knowledge_graph_link_count: 0,
          deleted_file_count: 1,
          skipped_shared_file_count: 0,
          storage_cleanup_pending: false,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    expect(document.body.textContent).not.toContain('待审新词')
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认彻底删除'))!
    confirm.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('结果尚不确定'))
    confirm.click()
    await vi.waitFor(() => expect(deleteAttempts).toBe(2))
    await vi.waitFor(() => expect(document.body.textContent).not.toContain('确认彻底删除？'))

    expect(deleteBodies[0]?.request_token).toBe(deleteBodies[1]?.request_token)
    expect(deleteBodies[0]?.request_token).toEqual(
      expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(deleteBodies[0]?.confirmation_phrase).toBe('彻底删除 1 份试卷')
  })

  it('shows the server reason when deletion is authoritatively rejected', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const serverMessage = '题库中存在当前版本无法安全处理的关联数据。请先更新应用，再重新删除；本次没有删除任何内容。'
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({ items: [paper], total: 1 })
      }
      if (url.endsWith('/permanent-deletion-impact')) {
        return response({
          paper_count: 1,
          question_count: 2,
          tag_count: 3,
          analysis_record_count: 6,
          training_link_count: 0,
          knowledge_graph_link_count: 0,
          owned_file_count: 1,
          shared_file_count: 0,
          taxonomy_proposal_count: 0,
          permanent_delete_phrase: '彻底删除 1 份试卷',
        })
      }
      if (url.endsWith('/permanent-delete')) {
        const requestId = new Headers(init?.headers).get('x-request-id') ?? ''
        return new Response(JSON.stringify({
          error: {
            code: 'paper_permanent_delete_dependency_conflict',
            message: serverMessage,
            details: {},
            request_id: requestId,
          },
        }), {
          status: 409,
          headers: {
            'content-type': 'application/json',
            'x-request-id': requestId,
          },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('确认彻底删除'))!
    confirm.click()

    await vi.waitFor(() => expect(document.body.textContent).toContain(serverMessage))
    expect(document.body.textContent).not.toContain('未收到服务器确认')
    expect(store.papers).toHaveLength(1)
  })

  it('shows the server reason when deletion impact cannot be prepared', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const serverMessage = '试卷文件未通过安全删除检查。请关闭可能占用文件的 Word 或 PDF 后重试；本次没有删除任何内容。'
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      const requestId = new Headers(init?.headers).get('x-request-id') ?? ''
      return new Response(JSON.stringify({
        error: {
          code: 'paper_permanent_delete_storage_incomplete',
          message: serverMessage,
          details: {},
          request_id: requestId,
        },
      }), {
        status: 409,
        headers: {
          'content-type': 'application/json',
          'x-request-id': requestId,
        },
      })
    })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()

    await vi.waitFor(() => expect(host.textContent).toContain(serverMessage))
    expect(host.textContent).not.toContain('删除影响读取失败')
    expect(store.papers).toHaveLength(1)
  })

  it('confirms a completed deletion by refreshing when the response is lost', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    let deleted = false
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({ items: deleted ? [] : [paper], total: deleted ? 0 : 1 })
      }
      if (url.endsWith('/permanent-deletion-impact')) {
        return response({
          paper_count: 1, question_count: 2, tag_count: 3,
          analysis_record_count: 0,
          training_link_count: 0, knowledge_graph_link_count: 0,
          owned_file_count: 1, shared_file_count: 0,
          taxonomy_proposal_count: 0,
          permanent_delete_phrase: '彻底删除 1 份试卷',
        })
      }
      if (url.endsWith('/permanent-delete')) {
        deleted = true
        throw new TypeError('response lost')
      }
      throw new Error(`unexpected request: ${url}`)
    })

    await openCardMenu(host, '匿名期末试卷')
    cardMenuItem('删除').click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('确认彻底删除'))!
    confirm.click()

    await vi.waitFor(() => expect(store.papers).toHaveLength(0))
    expect(document.body.textContent).not.toContain('删除结果尚不确定')
    expect(host.textContent).toContain('刷新后已确认试卷删除成功')
  })

  it('allows permanent deletion cleanup to run longer than the default request timeout', async () => {
    vi.useFakeTimers()
    try {
      let requestSignal: AbortSignal | undefined
      vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => new Promise((resolve, reject) => {
        requestSignal = init?.signal ?? undefined
        const timer = setTimeout(() => resolve(new Response(JSON.stringify({
          deleted_paper_ids: [paper.id], deleted_question_count: 2,
          deleted_tag_count: 3, deleted_analysis_record_count: 0,
          removed_training_link_count: 0,
          removed_knowledge_graph_link_count: 0, deleted_file_count: 26,
          skipped_shared_file_count: 0, storage_cleanup_pending: false,
        }), { status: 200, headers: { 'content-type': 'application/json' } })), 20_000)
        requestSignal?.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new DOMException('aborted', 'AbortError'))
        }, { once: true })
      }))

      const pending = questionBankApi.permanentlyDeletePapers(
        [{ id: paper.id, expected_updated_at: paper.updated_at }],
        '彻底删除 1 份试卷',
        'a'.repeat(32),
      )
      await vi.advanceTimersByTimeAsync(15_001)
      expect(requestSignal?.aborted).toBe(false)
      await vi.advanceTimersByTimeAsync(4_999)
      await expect(pending).resolves.toMatchObject({ deleted_file_count: 26 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('drives the batch bar from card, folder and master checkboxes', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper, { ...paper, id: 5, title: '第二份试卷' }]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    await nextTick()

    expect(host.textContent).toContain('未选中试卷')
    expect(batchBarButton(host, '继续完成未完成题目').disabled).toBe(true)

    const cardChecks = [...host.querySelectorAll<HTMLInputElement>('.paper-card__check input')]
    cardChecks[0]!.click()
    await nextTick()
    expect(host.textContent).toContain('已选 1 份试卷')
    expect(batchBarButton(host, '继续完成未完成题目').disabled).toBe(false)
    const master = host.querySelector<HTMLInputElement>('.paper-batch-bar__select-all input')!
    expect(master.checked).toBe(false)
    expect(master.indeterminate).toBe(true)

    // The folder checkbox selects the rest of the group without collapsing it.
    const folderCheck = host.querySelector<HTMLInputElement>('.paper-folder__check input')!
    folderCheck.click()
    await nextTick()
    expect(host.textContent).toContain('已选 2 份试卷')
    expect(host.textContent).toContain('第二份试卷')
    expect(master.checked).toBe(true)

    master.click()
    await nextTick()
    expect(host.textContent).toContain('未选中试卷')
    expect(batchBarButton(host, '删除').disabled).toBe(true)
  })

  it('asks once and submits a tagging job per selected paper for batch fill', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper, { ...paper, id: 5, title: '第二份试卷' }]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const taggingBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/questions?')) {
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        return response({
          items: paperIds.map((paperId) => ({ ...item, id: 3_000 + paperId, paper_id: paperId })),
          total: paperIds.length,
          page: 1,
          page_size: 100,
          total_pages: 1,
        })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        taggingBodies.push(JSON.parse(String(init.body)))
        return response({
          id: 300 + taggingBodies.length,
          job_type: 'tagging_sync',
          payload: {},
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-01T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-01T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    for (const check of host.querySelectorAll<HTMLInputElement>('.paper-card__check input')) {
      check.click()
    }
    await nextTick()
    batchBarButton(host, '继续完成未完成题目').click()

    await vi.waitFor(() => expect(taggingBodies).toHaveLength(2))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('还有 2 道题未打全标签'))
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('共 2 道题提交后端逐题核对'))
    expect(taggingBodies.map((body) => body.question_ids)).toEqual([[3_004], [3_005]])
    expect(taggingBodies.every((body) => body.force_retag === undefined)).toBe(true)
    expect(host.textContent).toContain('已提交 2 份试卷等待后端核对（其中 2 道题未打全标签）')
  })

  it('previews and deletes the whole selection with one request each', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    const papers = [paper, { ...paper, id: 5, title: '第二份试卷' }]
    store.papers = papers
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    let deleted = false
    const previewBodies: Array<Record<string, unknown>> = []
    const deleteBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers') {
        return response({ items: deleted ? [] : papers, total: deleted ? 0 : papers.length })
      }
      if (url.endsWith('/permanent-deletion-impact')) {
        previewBodies.push(JSON.parse(String(init?.body)))
        return response({
          paper_count: 2,
          question_count: 4,
          tag_count: 6,
          analysis_record_count: 8,
          training_link_count: 1,
          knowledge_graph_link_count: 1,
          owned_file_count: 2,
          shared_file_count: 0,
          taxonomy_proposal_count: 1,
          permanent_delete_phrase: '彻底删除 2 份试卷',
        })
      }
      if (url.endsWith('/permanent-delete')) {
        deleteBodies.push(JSON.parse(String(init?.body)))
        deleted = true
        return response({
          deleted_paper_ids: [4, 5],
          deleted_question_count: 4,
          deleted_tag_count: 6,
          deleted_analysis_record_count: 8,
          removed_training_link_count: 1,
          removed_knowledge_graph_link_count: 1,
          deleted_file_count: 2,
          skipped_shared_file_count: 0,
          storage_cleanup_pending: false,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    for (const check of host.querySelectorAll<HTMLInputElement>('.paper-card__check input')) {
      check.click()
    }
    await nextTick()
    batchBarButton(host, '删除').click()

    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    expect(document.body.textContent).toContain('选中的 2 份试卷')
    expect(document.body.textContent).toContain('第二份试卷')
    expect(document.body.textContent).toContain('将清除 1 条待审新词')
    expect(previewBodies).toHaveLength(1)
    expect(previewBodies[0]?.selections).toEqual([
      { id: 4, expected_updated_at: paper.updated_at },
      { id: 5, expected_updated_at: paper.updated_at },
    ])

    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认彻底删除'))!
    confirm.click()
    await vi.waitFor(() => expect(store.papers).toHaveLength(0))
    expect(deleteBodies).toHaveLength(1)
    expect((deleteBodies[0]?.selections as unknown[])).toHaveLength(2)
    expect(deleteBodies[0]?.confirmation_phrase).toBe('彻底删除 2 份试卷')
    expect(host.textContent).toContain('已删除 2 份试卷')
    expect(host.textContent).toContain('未选中试卷')
  })
})

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
