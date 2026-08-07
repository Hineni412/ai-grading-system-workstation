import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QuestionAssemblyView from '../views/QuestionAssemblyView.vue'
import { useAssemblyStore } from '../stores/assembly'
import type { QuestionBankListItem } from '../api/question-bank'

const revisionA = 'a'.repeat(64)
const revisionB = 'b'.repeat(64)

function draft(ids: number[] = [], revision = revisionA) {
  return {
    basket_ids: ids,
    order_ids: ids,
    sections: [],
    title: '函数专项练习',
    header_text: '限时 45 分钟',
    include_answer: true,
    layout_mode: 'sequential',
    preview_mode: 'teacher',
    revision,
  }
}

function job() {
  return {
    id: 51,
    job_type: 'assembly_export',
    payload: { draft_revision: revisionB, format: 'docx', question_count: 2 },
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
}

const mounted: Array<ReturnType<typeof createApp>> = []

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('question assembly view', () => {
  it('shows the first question page without waiting for slow catalog and facet requests', async () => {
    const slowCatalog = deferred<Response>()
    const slowFacets = deferred<Response>()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') return slowCatalog.promise
      if (url.startsWith('/api/question-bank/facets?')) return slowFacets.promise
      if (url.startsWith('/api/question-bank/questions?')) {
        return json(questionPage(bankQuestion(17, '无需等待筛选统计的题目')))
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    try {
      await vi.waitFor(() => expect(host.textContent).toContain('无需等待筛选统计的题目'))
      expect(host.textContent).toContain('正在读取教材目录')
    } finally {
      slowCatalog.resolve(await json(curriculumCatalog()))
      slowFacets.resolve(await json(questionFacets()))
      await settle()
    }
  })

  it('loads the initial question facets once while opening the workspace', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') {
        return json(curriculumCatalog())
      }
      if (url.startsWith('/api/question-bank/facets?')) {
        return json(questionFacets())
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        return json(questionPage(bankQuestion(17, '初始题目')))
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('初始题目'))
    await vi.waitFor(() => expect(host.querySelector('.assembly-curriculum-tree')).toBeTruthy())

    const difficultyRow = host.querySelector('.assembly-filter-row.is-difficulty')
    expect(difficultyRow?.querySelector('.difficulty-range')).toBeTruthy()
    expect(difficultyRow?.querySelector('.assembly-filter-label')).toBeNull()

    const facetCalls = fetchSpy.mock.calls.filter(
      ([input]) => String(input).startsWith('/api/question-bank/facets?'),
    )
    expect(facetCalls).toHaveLength(1)
  })

  it('shows only the most specific knowledge point on question cards', async () => {
    const taggedQuestion: QuestionBankListItem = {
      ...bankQuestion(17, '带层级知识点的题目'),
      tags: [{
        tag_type: 'knowledge_point',
        tag_value: '七年级上册｜第一章 丰富的图形世界｜1 生活中的立体图形｜常见的几何体',
        confidence: 0.96,
      }],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') return json(draft())
      if (url === '/api/question-assembly/records?limit=100') return json({ items: [], total: 0 })
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') return json(curriculumCatalog())
      if (url.startsWith('/api/question-bank/facets?')) return json(questionFacets())
      if (url.startsWith('/api/question-bank/questions?')) return json(questionPage(taggedQuestion))
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('常见的几何体'))
    const card = host.querySelector('.assembly-result-card')
    expect(card?.textContent).not.toContain('七年级上册｜第一章')
  })

  it('shows only the leaf knowledge label in assembly filter chips', async () => {
    const facets = questionFacets()
    facets.knowledge_points = [{
      value: '八年级下册｜第五章 图形的轴对称｜1 轴对称及其性质｜折叠问题',
      count: 3,
    }]
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') return json(draft())
      if (url === '/api/question-assembly/records?limit=100') return json({ items: [], total: 0 })
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') return json(curriculumCatalog())
      if (url.startsWith('/api/question-bank/facets?')) return json(facets)
      if (url.startsWith('/api/question-bank/questions?')) return json(questionPage(bankQuestion(17, '折叠题')))
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('折叠问题'))
    const chip = [...host.querySelectorAll<HTMLButtonElement>('.assembly-filter-chips button')]
      .find(button => button.textContent?.includes('折叠问题'))
    expect(chip?.textContent).not.toContain('八年级下册')
    expect(chip?.querySelector('span')?.title).toContain('八年级下册｜第五章')
  })

  it('shows selected chapter questions before a slow facet refresh and keeps the chapter tree visible', async () => {
    const slowFacets = deferred<Response>()
    let holdFacetRefresh = false
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') {
        return json(curriculumCatalog())
      }
      if (url.startsWith('/api/question-bank/facets?')) {
        if (holdFacetRefresh) return slowFacets.promise
        return json(questionFacets())
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        const selectedChapter = url.includes('exam_scopes=')
        return json(questionPage(bankQuestion(
          selectedChapter ? 18 : 17,
          selectedChapter ? '章节筛选后的题目' : '初始题目',
        )))
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('初始题目'))
    await vi.waitFor(() => expect(
      host.querySelector<HTMLButtonElement>('.assembly-curriculum-node__chapter'),
    ).toBeTruthy())
    const chapter = host.querySelector<HTMLButtonElement>('.assembly-curriculum-node__chapter')

    holdFacetRefresh = true
    chapter!.click()
    await vi.waitFor(() => expect(fetchSpy.mock.calls.filter(
      ([input]) => String(input).startsWith('/api/question-bank/questions?'),
    )).toHaveLength(2))

    try {
      await vi.waitFor(() => expect(host.textContent).toContain('章节筛选后的题目'))
      expect(host.querySelector('.assembly-curriculum-tree')).toBeTruthy()
      expect(host.textContent).not.toContain('正在读取教材目录')
    } finally {
      slowFacets.resolve(new Response(JSON.stringify(questionFacets()), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))
      await settle()
    }
  })

  it('refreshes facets for filter changes but not for pagination or sorting', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') {
        return json(curriculumCatalog())
      }
      if (url.startsWith('/api/question-bank/facets?')) {
        return json(questionFacets())
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        const page = url.includes('page=2') ? 2 : 1
        const text = page === 2 ? '第二页题目' : '第一页题目'
        return json(questionPage(bankQuestion(page === 2 ? 18 : 17, text), page))
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('第一页题目'))
    const facetCallCount = () => fetchSpy.mock.calls.filter(
      ([input]) => String(input).startsWith('/api/question-bank/facets?'),
    ).length
    await vi.waitFor(() => expect(facetCallCount()).toBeGreaterThan(0))
    const initialFacetCalls = facetCallCount()

    const nextPage = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '下一页')
    expect(nextPage).toBeTruthy()
    nextPage!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('第二页题目'))
    expect(facetCallCount()).toBe(initialFacetCalls)

    const sort = host.querySelector<HTMLButtonElement>('button[aria-label^="难度"]')
    expect(sort).toBeTruthy()
    sort!.click()
    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(
      ([input]) => (
        String(input).startsWith('/api/question-bank/questions?')
        && String(input).includes('sort=difficulty_asc')
      ),
    )).toBe(true))
    expect(facetCallCount()).toBe(initialFacetCalls)

    await vi.waitFor(() => expect(
      [...host.querySelectorAll<HTMLButtonElement>('.assembly-filter-panel button')]
        .find((button) => button.textContent?.includes('选择题')),
    ).toBeTruthy())
    const questionType = [...host.querySelectorAll<HTMLButtonElement>(
      '.assembly-filter-panel button',
    )].find((button) => button.textContent?.includes('选择题'))
    questionType!.click()
    await vi.waitFor(() => expect(facetCallCount()).toBe(initialFacetCalls + 1))
    expect(fetchSpy.mock.calls.some(
      ([input]) => (
        String(input).startsWith('/api/question-bank/questions?')
        && String(input).includes('question_types=')
      ),
    )).toBe(true)
  })

  it('adds selected question-bank rows to the paper basket and submits an export job', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/draft' && init?.method === 'PUT') {
        return json(draft([17], revisionB))
      }
      if (url.startsWith('/api/question-bank/questions?')) {
        return json({
          items: [bankQuestion(17, '一次函数图像题')],
          total: 1,
          page: 1,
          page_size: 12,
          total_pages: 1,
        })
      }
      if (
        url === '/api/question-bank/curriculum?include_knowledge_points=false'
        || url.startsWith('/api/question-bank/facets?')
      ) {
        return json({}, 400)
      }
      if (url.startsWith('/api/question-assembly/questions?')) {
        return json({
          items: [
            question(17, '一次函数图像题', 5),
            question(18, '二次函数应用题', 10),
          ],
          missing_question_ids: [],
        })
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({
          items: [{
            id: 'record-1',
            title: '函数专项练习',
            question_ids: [17],
            order_ids: [17],
            sections: [],
            export_format: 'markdown',
            filename: 'paper.md',
            include_answer: true,
            created_at: '2026-07-18T10:00:00Z',
            question_count: 1,
            question_type_summary: { 选择题: 1 },
            download_url: '/api/question-assembly/records/record-1/download',
          }],
          total: 1,
        })
      }
      if (url === '/api/question-assembly/export' && init?.method === 'POST') {
        return json(job(), 202)
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    const pinia = createPinia()
    app.use(pinia)
    app.mount(host)
    mounted.push(app)
    await settle()

    expect(host.querySelector('.assembly')?.classList.contains('is-workspace-wide')).toBe(true)
    await vi.waitFor(() => expect(host.textContent).toContain('一次函数图像题'))
    await vi.waitFor(() => expect(useAssemblyStore(pinia).loadState).not.toBe('loading'))
    const add = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '加入试卷篮')!
    await vi.waitFor(() => expect(add.disabled).toBe(false))
    add.click()
    await vi.waitFor(() => expect(useAssemblyStore(pinia).selectedQuestionCount).toBe(1))
    await vi.waitFor(() => expect(host.textContent).toContain('一次函数图像题'))
    const saveCall = fetchSpy.mock.calls.find(
      ([input, init]) => String(input) === '/api/question-assembly/draft' && init?.method === 'PUT',
    )
    expect(JSON.parse(String(saveCall?.[1]?.body))).toMatchObject({
      expected_revision: revisionA,
      draft: { basket_ids: [17], order_ids: [17] },
    })
    const openEditor = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('进入编辑与导出'))!
    openEditor.click()
    await vi.waitFor(() => expect(host.textContent).toContain('一次函数图像题'))
    expect(host.textContent).toContain('A')
    expect(host.textContent).toContain('paper.md')

    const exportWord = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('导出 Word'))!
    exportWord.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(
      '/api/question-assembly/export',
      expect.objectContaining({ method: 'POST' }),
    ))
    const exportCall = fetchSpy.mock.calls.find(
      ([input, init]) => String(input) === '/api/question-assembly/export' && init?.method === 'POST',
    )
    expect(JSON.parse(String(exportCall?.[1]?.body))).toEqual({
      draft_revision: revisionB,
      format: 'docx',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('任务 #51'))
  })

  it('manages section names from the basket without a separate preview editor', async () => {
    const saveDraft = vi.fn(async (_revision: string, nextDraft: ReturnType<typeof draft>) => ({
      ...nextDraft,
      revision: revisionB,
      sections: [
        ...nextDraft.sections,
        { id: 'unassigned', title: '未分节', question_ids: [17, 18] },
      ],
    }))
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft([17, 18]))
      }
      if (url === '/api/question-assembly/draft' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { expected_revision: string; draft: ReturnType<typeof draft> }
        return json(await saveDraft(body.expected_revision, body.draft))
      }
      if (url.startsWith('/api/question-assembly/questions?')) {
        return json({
          items: [
            question(17, 'question 17', 5),
            question(18, 'question 18', 10),
          ],
          missing_question_ids: [],
        })
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)
    await settle()

    await vi.waitFor(() => expect(host.textContent).toContain('第 17 题'))
    const openEditor = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('进入编辑与导出'))!
    openEditor.click()
    await vi.waitFor(() => expect(host.textContent).toContain('question 17'))
    const addSection = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('添加分节'))!
    expect(addSection).toBeTruthy()
    await vi.waitFor(() => expect(addSection.disabled).toBe(false))
    addSection.click()

    await vi.waitFor(() => expect(host.textContent).toContain('分节 1'))
    expect(host.textContent).toContain('未分节')
    expect(host.textContent).toContain('question 17')
    expect(host.textContent).not.toContain('新增分节标题')
    expect(host.querySelectorAll<HTMLInputElement>('input[aria-label="分节名称"]')).toHaveLength(1)
    const sectionSave = fetchSpy.mock.calls
      .filter(([input, init]) => String(input) === '/api/question-assembly/draft' && init?.method === 'PUT')
      .map(([, init]) => JSON.parse(String(init?.body)))
      .find((body) => body.draft.layout_mode === 'sections')
    expect(sectionSave).toMatchObject({
      expected_revision: revisionA,
      draft: {
        layout_mode: 'sections',
        sections: [expect.objectContaining({ title: '分节 1', question_ids: [] })],
      },
    })

    const sectionName = host.querySelector<HTMLInputElement>('input[aria-label="分节名称"]')
    expect(sectionName).toBeTruthy()
    sectionName!.value = '第一部分'
    sectionName!.dispatchEvent(new Event('change', { bubbles: true }))

    await vi.waitFor(() => expect(saveDraft).toHaveBeenCalledWith(
      revisionB,
      expect.objectContaining({
        sections: [expect.objectContaining({ title: '第一部分', question_ids: [] })],
      }),
    ))
  })
})

function question(id: number, text: string, score: number) {
  return {
    id,
    revision: revisionA,
    question_number: String(id),
    question_type: '选择题',
    question_text: text,
    answer_text: 'A',
    difficulty: '5',
    paper_title: '匿名试卷',
    tags: [],
    asset_urls: [],
    rich_content: {
      available: true,
      question_block_count: 0,
      answer_block_count: 0,
      question_blocks: [],
      answer_blocks: [],
    },
    score_value: score,
  }
}

function bankQuestion(id: number, text: string): QuestionBankListItem {
  return {
    id,
    revision: revisionA,
    paper_id: 4,
    question_number: String(id),
    question_type: '选择题',
    question_text: text,
    answer_text: 'A',
    difficulty: '5',
    typicality: null,
    reason: null,
    needs_review: false,
    has_images: false,
    needs_image_review: false,
    created_at: '2026-07-18T08:00:00Z',
    updated_at: '2026-07-18T09:00:00Z',
    paper_title: '匿名试卷',
    year: '2025',
    province: null,
    city: null,
    district: null,
    exam_type: '期末',
    grade: '七年级',
    semester: '上学期',
    textbook_version: null,
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
}

function questionPage(item: ReturnType<typeof bankQuestion>, page = 1) {
  return {
    items: [item],
    total: 2,
    page,
    page_size: 12,
    total_pages: 2,
  }
}

function questionFacets() {
  return {
    exam_scopes: [],
    curriculum_sections: [],
    curriculum_chapters: [{ value: 'bnu24-math-g7-upper-c01', count: 2 }],
    knowledge_points: [{ value: '有理数', count: 2 }],
    abilities: [],
    methods: [],
    thoughts: [],
    models: [],
    special_types: [{ value: '动态几何题', count: 1 }],
    student_levels: [],
    teaching_stages: [],
    sub_skills: [],
    question_types: [{ value: '选择题', count: 2 }],
    years: [],
    exam_types: [],
    grades: [],
  }
}

function curriculumCatalog() {
  const volumes = [
    ['七年级', '上学期'],
    ['七年级', '下学期'],
    ['八年级', '上学期'],
    ['八年级', '下学期'],
    ['九年级', '上学期'],
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
    volumes: volumes.map(([grade, semester], index) => ({
      id: `volume-${index + 1}`,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: index === 0 ? 'bnu24-math-g7-upper-c01' : `chapter-${index + 1}`,
        knowledge_id: index === 0 ? 'bnu24-math-g7-upper-c01' : `chapter-${index + 1}`,
        order: 1,
        number: '第一章',
        title: index === 0 ? '有理数' : `测试章节${index + 1}`,
        label: index === 0 ? '第一章 有理数' : `第一章 测试章节${index + 1}`,
        kind: 'chapter',
        display_name: `${grade}${semester === '上学期' ? '上册' : '下册'}｜${index === 0 ? '第一章 有理数' : `第一章 测试章节${index + 1}`}`,
        source_ref: {
          node_id: `node-${index + 1}`,
          relative_url: `/czsx/zj${index + 1}`,
        },
        exam_scope_values: [
          index === 0 ? '七年级上册 第一章 有理数' : `${grade}${semester === '上学期' ? '上册' : '下册'} 测试范围${index + 1}`,
        ],
        sections: [],
      }],
    })),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve
  })
  return { promise, resolve }
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
