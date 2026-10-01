import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'

import { questionBankApi, type QuestionBankPaper } from '../api/question-bank'
import QuestionSkillBrowser from '../components/question-bank/QuestionSkillBrowser.vue'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import QuestionAnnotationPanel from '../components/question-bank/QuestionAnnotationPanel.vue'
import { createAppRouter } from '../router'
import { CURRICULUM_SCOPE_STORAGE_KEY } from '../stores/curriculum-scope'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import QuestionBankView from '../views/QuestionBankView.vue'

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
  await router.push('/question-bank?tab=paper')
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
  it('pages large paper folders and preserves selections while searching across all pages', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = Array.from({ length: 67 }, (_, i) => ({ ...paper, id: i + 1, title: `合成试卷 ${i + 1}` }))
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    expect(host.querySelectorAll('.paper-card')).toHaveLength(24)
    const check = host.querySelector<HTMLInputElement>('.paper-card input[type="checkbox"]')!
    check.click()
    await nextTick()
    const next = [...host.querySelectorAll<HTMLButtonElement>('nav[aria-label="试卷分页"] button')].find(b => b.textContent === '下一页')!
    next.click()
    await nextTick()
    expect(host.textContent).toContain('第 2 / 3 页')
    expect(host.querySelectorAll('.paper-card')).toHaveLength(24)
    const previous = [...host.querySelectorAll<HTMLButtonElement>('nav[aria-label="试卷分页"] button')].find(b => b.textContent === '上一页')!
    previous.click()
    await nextTick()
    expect(host.querySelector<HTMLInputElement>('.paper-card input[type="checkbox"]')!.checked).toBe(true)
    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!
    search.value = '合成试卷 67'
    search.dispatchEvent(new Event('input'))
    await vi.waitFor(() => expect(host.querySelectorAll('.paper-card')).toHaveLength(1))
    expect(host.querySelector('.paper-card')!.textContent).toContain('合成试卷 67')
    expect(host.querySelector('nav[aria-label="试卷分页"]')).toBeNull()
  })

  it('keeps maintenance collapsed and reads standard gaps on demand', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [paper]
    store.papersState = 'ready'
    const load = vi.spyOn(questionBankApi, 'standardSummary').mockResolvedValue({
      active_release_id: 'kgr_test', taxonomy_revision: 9, versions: [], question_count: 10,
      usable_question_count: 9, skill_question_count: 8, section_only_question_count: 2,
      missing_link_question_count: 1, older_link_question_count: 0, model_calls: 0,
    })
    app.mount(host)
    mounted.push(app)
    expect(load).not.toHaveBeenCalled()
    expect(host.textContent).not.toContain('判定点需核对')
    const menu = host.querySelector<HTMLDetailsElement>('.paper-library__maintenance')!
    expect(menu.open).toBe(false)
    expect(menu.textContent).toContain('全库重新打标签')
    menu.querySelector('summary')!.click()
    const button = [...menu.querySelectorAll('button')].find((item) => item.textContent === '标准版本与缺口')!
    button.click()
    await vi.waitFor(() => expect(host.textContent).toContain('kgr_test'))
    expect(host.textContent).toContain('2 道题仍有只关联到小节')
    expect(host.textContent).toContain('查看不调用 AI、不收费')
  })

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
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank?tab=paper')
    app.use(createPinia()).use(router)
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

  it('does not treat untagged papers as complete even when a finished job is still tracked', async () => {
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
      if (String(input).startsWith('/api/question-bank/question-refs')) {
        return questionRefs([5, 8, 9, 11, 12].map((id, index) => ({
          id,
          paper_id: 4,
          question_number: String(index + 1),
        })))
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
    await vi.waitFor(() => expect(host.textContent).toContain('5 道题标签未打全'))

    expect(host.textContent).not.toContain('产生了待审核新词')
    expect(host.textContent).not.toContain('AI 解析进度')
    expect(host.textContent).not.toContain('标签、解题证据和训练判定点均已完成')
    expect(host.textContent).not.toContain('解题证据')
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

  it('restores a skill bookmark and passes duplicate, progress and topic filters to the shared list', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank?tab=skill&section=kp_TEST_section&skill=sk_TEST_one')
    const scope = useCurriculumScopeStore(pinia)
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    scope.volumes = [{ id: scope.selectedVolumeId, label: '八年级上册', order: 1, grade: '八年级', semester: '上学期', textbook_version: '北师大版（2024）', source: {}, statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 }, chapters: [{ id: 'kp_TEST_chapter', knowledge_id: 'kp_TEST_chapter', label: '第一章', title: '第一章', display_name: '第一章', kind: 'chapter', number: '1', source_ref: { node_id: 'TEST', relative_url: '' }, exam_scope_values: [], order: 1, sections: [] }] }]
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    vi.spyOn(questionBankApi, 'listFacets').mockResolvedValue({ exam_scopes: [], curriculum_sections: [], knowledge_points: [], curriculum_chapters: [], abilities: [], methods: [], models: [], thoughts: [], special_types: [], error_types: [], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] })
    const stats = { question_count: 1, type_counts: { '选择题': 0, '多选题': 0, '填空题': 0, '解答题': 1 }, difficulty: { min: 4, median: 4, max: 4 }, criteria_needs_review_count: 0 }
    const index = { graph_release_id: 'kgr_TEST', curriculum_volume_id: scope.selectedVolumeId, model_calls: 0, question_count: 1, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [{ id: 'kp_TEST_chapter', label: '第一章', question_count: 1, cross_section_skills: [], sections: [{ id: 'kp_TEST_section', label: '第一节', question_count: 1, skills: [{ ...stats, stable_key: 'sk_TEST_one', display_name: '判断直角三角形', full_name: '八年级上册/判断直角三角形', cross_section: false, definition: { observable_evidence: '判断', include_scope: '三边', exclude_scope: '作图' } }], topics: [{ ...stats, stable_key: 'kp_TEST_topic', display_name: '勾股定理', filter_value: '八年级上册/第一章/勾股定理' }] }] }] }
    const app = createApp({ render: () => h(QuestionSkillBrowser, { index, loading: false, error: '' }) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ skillKeys: ['sk_TEST_one'], collapseDuplicates: true, includeSkills: true })))
    const progress = [...host.querySelectorAll<HTMLSelectElement>('select')].find(select => select.textContent?.includes('已学到第一章'))!
    progress.value = 'kp_TEST_chapter'
    progress.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ teachingProgressChapter: 'kp_TEST_chapter' })))
    const topic = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '按知识主题')!
    topic.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ skillKeys: [], knowledgePoints: ['八年级上册/第一章/勾股定理'] })))
    expect(router.currentRoute.value.query.topic).toBe('kp_TEST_topic')
    expect(router.currentRoute.value.fullPath).not.toContain('勾股')
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
            page_size: 100,
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
          page_size: 100,
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
    expect(router.currentRoute.value.query).toMatchObject({ tab: 'paper', paper: '4' })
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
      render: () => [h(QuestionAnnotationPanel), h(QuestionImportJobs)],
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

  it('surfaces grouped import match detail with preview links on completion', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionImportJobs)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)

    useJobStore(pinia).track({
      id: 43,
      job_type: 'question_import',
      payload: {},
      result: {
        outcome: 'complete',
        exact_duplicate_count: 2,
        analysis_reused_count: 1,
        exact_duplicates: [
          {
            question_number: '1',
            matched_question_id: 80,
            matched_paper_title: '既有试卷',
            matched_question_number: '3',
          },
          {
            question_number: '2',
            matched_question_id: 81,
            matched_paper_title: '既有试卷',
            matched_question_number: '4',
          },
        ],
        near_duplicate_hints: [
          {
            question_number: '5',
            matched_question_id: 88,
            matched_paper_title: '既有试卷',
            matched_question_number: '7',
            question_id: 201,
            similarity: 0.83,
            high: false,
            match_kind: 'suspected',
            reason: '题面相似，需核对条件、选项及图片',
          },
          {
            question_number: '6',
            matched_question_id: 90,
            matched_paper_title: '既有试卷',
            matched_question_number: '9',
            question_id: 202,
            similarity: 1.0,
            high: true,
            match_kind: 'answer_conflict',
            reason: '题面相同但答案文本不同，保留两个来源并等待核对',
          },
        ],
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

    const detail = host.querySelector<HTMLDetailsElement>('.qb-import-detail')!
    expect(detail).not.toBeNull()
    expect(detail.querySelector('summary')!.textContent).toContain('完全相同已关联 2')
    expect(detail.querySelector('summary')!.textContent).toContain('相似或变式 1')
    expect(detail.querySelector('summary')!.textContent).toContain('答案不同 1')
    expect(detail.textContent).toContain('完全相同，已关联')
    expect(detail.textContent).toContain('相似或变式，供核对')
    expect(detail.textContent).toContain('答案不同，需核对')
    expect(detail.textContent).toContain('本卷第 1 题 ↔ 《既有试卷》第 3 题')
    expect(detail.textContent).toContain('本卷第 6 题 ↔ 《既有试卷》第 9 题')
    expect(detail.textContent).toContain('题面相似，需核对条件、选项及图片')
    expect(detail.textContent).toContain('待复核')

    const previewButtons = [...detail.querySelectorAll<HTMLButtonElement>('button')]
      .filter((button) => button.textContent?.trim().startsWith('查看'))
    expect(previewButtons.length).toBeGreaterThanOrEqual(5)
    previewButtons[0]!.click()
    await nextTick()
    expect(document.body.querySelector('.question-preview')).not.toBeNull()
  })

  it('notes when a whole file was already in the bank', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionImportJobs)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)

    useJobStore(pinia).track({
      id: 44,
      job_type: 'question_import',
      payload: {},
      result: {
        outcome: 'complete',
        duplicate_papers: [{ paper_id: 9, title: '历年真题汇编' }],
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

    expect(host.textContent).toContain('这份试卷已在题库中（《历年真题汇编》），本次未重复入库。')
  })

  it('lets the teacher pick a curriculum volume for import and submits it with the job', async () => {
    localStorage.setItem(CURRICULUM_SCOPE_STORAGE_KEY, 'volume-3')
    const uploadId = 'a'.repeat(32)
    const requestId = 'b'.repeat(32)
    const submittedBodies: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') {
        return response(importVolumeCatalog())
      }
      if (url.startsWith('/api/question-bank/import-uploads?')) {
        return response({
          upload_id: uploadId,
          filename: '八年级（上）期末数学试卷.docx',
          suffix: '.docx',
          size: 4,
          sha256: '1'.repeat(64),
        }, 201)
      }
      if (url === '/api/question-bank/import-requests') {
        return response({
          request_id: requestId,
          upload_id: uploadId,
          filename: '八年级（上）期末数学试卷.docx',
          size: 4,
          sha256: '1'.repeat(64),
          status: 'pending',
        }, 201)
      }
      if (url === `/api/question-bank/import-requests/${requestId}/jobs`) {
        submittedBodies.push(JSON.parse(String(init?.body)))
        return response({
          id: 44,
          job_type: 'question_import',
          payload: { request_id: requestId },
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-08-03T10:00:00Z',
          started_at: null,
          updated_at: '2026-08-03T10:00:00Z',
          finished_at: null,
        }, 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionImportJobs)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)

    const picker = await vi.waitFor(() => {
      const select = host.querySelector<HTMLSelectElement>('.qb-volume-picker select')
      expect(select).toBeTruthy()
      return select!
    })
    // 记住的教学册别会作为默认选择，避免每次导入重新挑选。
    expect(picker.value).toBe('volume-3')
    expect(picker.textContent).toContain('八年级上册')

    const input = host.querySelector<HTMLInputElement>('.qb-file-picker input')!
    const file = new File(
      ['test'],
      '八年级（上）期末数学试卷.docx',
      { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
    )
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change'))

    await vi.waitFor(() => expect(submittedBodies).toHaveLength(1))
    expect(submittedBodies[0]).toEqual({ curriculum_volume_id: 'volume-3' })
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
      if (url.startsWith('/api/question-bank/question-refs')) {
        return questionRefs([
          { id: 17, paper_id: 4, question_number: '1' },
        ])
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
      if (url.startsWith('/api/question-bank/question-refs')) {
        if (url.includes('analysis_status=all')) {
          questionListUrl = url
        }
        return questionRefs([
          { id: 17, paper_id: 4, question_number: '1' },
          { id: 18, paper_id: 4, question_number: '2' },
        ])
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

  it('loads 24 papers from the same curriculum in one question-refs request', async () => {
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
      if (url.startsWith('/api/question-bank/question-refs')) {
        if (url.includes('analysis_status=all')) {
          questionListUrls.push(url)
        }
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        return questionRefs(paperIds.map((paperId) => ({
          id: 1_000 + paperId,
          paper_id: paperId,
          question_number: String(paperId),
        })))
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
      if (url.startsWith('/api/question-bank/question-refs')) {
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        questionPaperGroups.push(paperIds)
        return questionRefs(paperIds.map((paperId) => ({
          id: 2_000 + paperId,
          paper_id: paperId,
          question_number: String(paperId),
        })))
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
      if (url.startsWith('/api/question-bank/question-refs')) {
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        return questionRefs(paperIds.map((paperId) => ({
          id: 3_000 + paperId,
          paper_id: paperId,
          question_number: String(paperId),
        })))
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

function questionRefs(
  items: Array<{ id: number; paper_id: number; question_number: string }>,
): Promise<Response> {
  return response({
    items,
    total: items.length,
    page: 1,
    page_size: 500,
    total_pages: 1,
  })
}

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}

function importVolumeCatalog() {
  const volumes = [
    ['volume-1', '七年级', '上学期'],
    ['volume-2', '七年级', '下学期'],
    ['volume-3', '八年级', '上学期'],
    ['volume-4', '八年级', '下学期'],
    ['volume-5', '九年级', '上学期'],
  ] as const
  return {
    schema_version: 2,
    catalog_id: 'bnu-math-2024',
    knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
    publisher: '北京师范大学出版社',
    subject: '初中数学',
    edition: '2024',
    statistics: {
      raw_nodes: 5,
      excluded_nodes: 0,
      retained_nodes: 5,
      chapters: 5,
      sections: 0,
      knowledge_points: 0,
    },
    volumes: volumes.map(([id, grade, semester], index) => ({
      id,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版（2024）',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: `${id}-c01`,
        knowledge_id: `${id}-c01`,
        order: 1,
        number: '第一章',
        title: '测试章节',
        label: '第一章 测试章节',
        kind: 'chapter',
        display_name: `${grade}｜第一章 测试章节`,
        source_ref: { node_id: `node-${index + 1}`, relative_url: `/czsx/zj${index + 1}` },
        exam_scope_values: [`${grade} 测试范围`],
        sections: [],
      }],
    })),
  }
}
