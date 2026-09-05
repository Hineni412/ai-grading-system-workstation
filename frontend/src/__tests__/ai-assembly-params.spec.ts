import { createApp, nextTick } from 'vue'
import { createPinia, type Pinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AiAssemblyPanel from '../components/question-assembly/AiAssemblyPanel.vue'
import { createAppRouter } from '../router'
import type { CurriculumCatalog, QuestionBankFacets } from '../api/question-bank'
import { useAiAssemblyStore } from '../stores/ai-assembly'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'

const revision = 'a'.repeat(64)

const mounted: Array<ReturnType<typeof createApp>> = []

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

function knowledgePoint(
  id: string,
  order: number,
  fullPath: string,
  parentKnowledgeId: string,
) {
  const parts = fullPath.split('｜')
  return {
    id,
    order,
    label: parts[parts.length - 1] ?? fullPath,
    display_name: fullPath,
    parent_knowledge_id: parentKnowledgeId,
    source_ref: { node_id: `${id}-node`, relative_url: `/czsx/${id}` },
  }
}

function section(
  id: string,
  order: number,
  number: string,
  title: string,
  chapterPath: string,
  points: Array<[string, string]>,
) {
  const path = `${chapterPath}｜${number} ${title}`
  return {
    id,
    knowledge_id: id,
    order,
    number,
    title,
    label: `${number} ${title}`,
    kind: 'lesson' as const,
    display_name: path,
    source_ref: { node_id: `${id}-node`, relative_url: `/czsx/${id}` },
    knowledge_points: points.map(([pointId, pointTitle], index) => (
      knowledgePoint(pointId, index + 1, `${path}｜${pointTitle}`, id)
    )),
  }
}

function minimalVolume(id: string, order: number, label: string) {
  return {
    id,
    order,
    label,
    grade: '九年级',
    semester: '上学期',
    textbook_version: '北师大版2024',
    source: { provider: '组卷网' },
    statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
    chapters: [{
      id: `${id}-chapter-1`,
      knowledge_id: `${id}-chapter-1`,
      order: 1,
      number: '第一章',
      title: '占位章节',
      label: '第一章 占位章节',
      kind: 'chapter' as const,
      display_name: `${label}｜第一章 占位章节`,
      source_ref: { node_id: `${id}-node`, relative_url: `/czsx/${id}` },
      exam_scope_values: [`${label} 占位章节`],
      sections: [],
    }],
  }
}

function curriculumCatalog(): CurriculumCatalog {
  const volumePath = '八年级上册'
  const chapter1Path = `${volumePath}｜第一章 勾股定理`
  const chapter2Path = `${volumePath}｜第二章 实数`
  return {
    schema_version: 2,
    catalog_id: 'bnu-math-2024',
    knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
    publisher: '北京师范大学出版社',
    subject: '初中数学',
    edition: '2024',
    statistics: {
      raw_nodes: 8,
      excluded_nodes: 0,
      retained_nodes: 8,
      chapters: 2,
      sections: 3,
      knowledge_points: 5,
    },
    volumes: [{
      id: 'volume-1',
      order: 1,
      label: '八年级上册',
      grade: '八年级',
      semester: '上学期',
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 8, excluded_nodes: 0, retained_nodes: 8 },
      chapters: [
        {
          id: 'chapter-1',
          knowledge_id: 'chapter-1',
          order: 1,
          number: '第一章',
          title: '勾股定理',
          label: '第一章 勾股定理',
          kind: 'chapter',
          display_name: chapter1Path,
          source_ref: { node_id: 'chapter-1-node', relative_url: '/czsx/chapter-1' },
          exam_scope_values: ['八年级上册 第一章 勾股定理'],
          sections: [
            section('section-1', 1, '1', '探索勾股定理', chapter1Path, [
              ['kp-1', '勾股定理'],
              ['kp-2', '勾股定理的逆定理'],
            ]),
            section('section-2', 2, '2', '一定是直角三角形吗', chapter1Path, [
              ['kp-3', '直角三角形的判别'],
            ]),
          ],
        },
        {
          id: 'chapter-2',
          knowledge_id: 'chapter-2',
          order: 2,
          number: '第二章',
          title: '实数',
          label: '第二章 实数',
          kind: 'chapter',
          display_name: chapter2Path,
          source_ref: { node_id: 'chapter-2-node', relative_url: '/czsx/chapter-2' },
          exam_scope_values: ['八年级上册 第二章 实数'],
          sections: [
            section('section-3', 1, '2', '平方根', chapter2Path, [
              ['kp-4', '算术平方根'],
              ['kp-5', '平方根'],
            ]),
          ],
        },
      ],
    },
    minimalVolume('volume-2', 2, '八年级下册'),
    minimalVolume('volume-3', 3, '九年级上册'),
    minimalVolume('volume-4', 4, '九年级下册'),
    minimalVolume('volume-5', 5, '七年级上册'),
    ],
  }
}

function legacyFacets(): QuestionBankFacets {
  return {
    exam_scopes: [],
    curriculum_sections: [],
    curriculum_chapters: [],
    knowledge_points: [],
    abilities: [],
    methods: [],
    thoughts: [],
    models: [],
    special_types: [{ value: '动态几何题', count: 3 }],
    error_types: [],
    student_levels: [],
    teaching_stages: [],
    sub_skills: [],
    question_types: [
      { value: '选择题', count: 583 },
      { value: '填空题', count: 357 },
      { value: '解答题（画图）', count: 142 },
      { value: '解答题（计算）', count: 139 },
      { value: '解答题', count: 129 },
      { value: '解答题（证明）', count: 56 },
    ],
    years: [{ value: '2024', count: 40 }],
    exam_types: [{ value: '期末', count: 60 }],
    grades: [],
  }
}

function migratedFacets(): QuestionBankFacets {
  return {
    ...legacyFacets(),
    special_types: [
      { value: '画图', count: 142 },
      { value: '计算', count: 139 },
      { value: '证明', count: 56 },
      { value: '动态几何题', count: 3 },
    ],
    question_types: [
      { value: '选择题', count: 583 },
      { value: '填空题', count: 357 },
      { value: '解答题', count: 466 },
    ],
  }
}

function templatePaper() {
  return {
    id: 7,
    title: '2024 年南山区期末卷',
    year: '2024',
    province: '广东省',
    city: '深圳市',
    district: '南山区',
    exam_type: '期末',
    grade: '八年级',
    semester: '上学期',
    folder_name: null,
    textbook_version: '北师大版2024',
    curriculum_volume_id: 'volume-1',
    import_status: 'imported',
    created_at: '2026-07-18T08:00:00Z',
    updated_at: '2026-07-18T09:00:00Z',
    question_count: 16,
    tagged_question_count: 16,
    tagged_any_question_count: 16,
    evidence_question_count: 0,
    criteria_question_count: 16,
    criteria_needs_review_count: 0,
    complete_analysis_count: 16,
    source_type: 'docx',
  }
}

function templateStructure() {
  return {
    paper_id: 7,
    paper_title: '2024 年南山区期末卷',
    entries: [
      // 连续同类同难度已合并行：题数以 count 为准（10 + 6 = 16 题，而非 2 行）。
      { question_number: '1', question_type: '选择题', difficulty: 3, score: 2, count: 10 },
      { question_number: '11', question_type: '填空题', difficulty: 4, score: 3, count: 6 },
    ],
  }
}

function emptySession() {
  return {
    params: {
      template_paper_id: null,
      scope_keys: [],
      difficulty_ratio: { easy: null, medium: null, hard: null },
      type_counts: {},
      exam_types: [],
      years: [],
      free_text: '',
      essay_subtype: null,
    },
    spec: null,
    spec_model_name: '',
    selections: {},
    locked_question_ids: [],
    locked_row_by_id: {},
    gaps: [],
    dedupe_enabled: true,
    title: '',
    spec_job_id: null,
    updated_at: '',
    revision,
  }
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}

function mockPanelFetch(facets: QuestionBankFacets) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/api/question-assembly/ai/session') return json(emptySession())
    if (url === '/api/question-assembly/ai/preflight') {
      return json({
        configured: true,
        service_name: '校内服务',
        model_name: 'qwen-plus',
        call_count: 1,
        estimated_total_tokens: 12000,
      })
    }
    if (url === '/api/question-bank/papers') return json({ items: [templatePaper()], total: 1 })
    if (url.startsWith('/api/question-bank/facets')) return json(facets)
    if (url === '/api/question-bank/curriculum') return json(curriculumCatalog())
    if (url === '/api/question-assembly/ai/template-structure/7') return json(templateStructure())
    throw new Error(`unexpected request: ${url}`)
  })
}

interface MountedPanel {
  host: HTMLElement
  pinia: Pinia
  router: ReturnType<typeof createAppRouter>
  store: ReturnType<typeof useAiAssemblyStore>
}

async function mountPanel(
  facets: QuestionBankFacets,
  route = '/question-assembly?mode=ai',
): Promise<MountedPanel> {
  mockPanelFetch(facets)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AiAssemblyPanel)
  const pinia = createPinia()
  const router = createAppRouter(createMemoryHistory())
  await router.push(route)
  await router.isReady()
  app.use(pinia)
  app.use(router)
  const scope = useCurriculumScopeStore(pinia)
  scope.volumes = curriculumCatalog().volumes
  scope.selectedVolumeId = 'volume-1'
  app.mount(host)
  mounted.push(app)
  const store = useAiAssemblyStore(pinia)
  await vi.waitFor(() => expect(host.textContent).toContain('第一章 勾股定理'))
  return { host, pinia, router, store }
}

function chipButtons(host: HTMLElement, selector: string): HTMLButtonElement[] {
  return [...host.querySelectorAll<HTMLButtonElement>(selector)]
}

describe('ai assembly params page', () => {
  it('checks chapters tri-state and summarizes selection by chapter/section/points', async () => {
    const { host, store } = await mountPanel(legacyFacets())

    // 摘要条恒定渲染：无选择时显示占位，不塌掉。
    const bar = () => host.querySelector('.scope-columns__bar')
    expect(bar()).toBeTruthy()
    expect(bar()?.textContent).toContain('未选择（默认全库范围）')

    // 章勾选框整章连选：章 + 全部小节 + 全部知识点。
    const chapterCheck = host.querySelector<HTMLInputElement>(
      'input[aria-label="全选 第一章 勾股定理"]',
    )!
    expect(chapterCheck).toBeTruthy()
    chapterCheck.click()
    await nextTick()
    expect(store.params.scopeKeys).toEqual([
      'chapter-1', 'section-1', 'kp-1', 'kp-2', 'section-2', 'kp-3',
    ])
    expect(bar()?.textContent).not.toContain('未选择（默认全库范围）')
    expect(bar()?.textContent).toContain('已选 1 章')
    expect(bar()?.textContent).toContain('第一章 勾股定理（全章）')

    // 取消一个知识点 → 章变半选，摘要按节/知识点粒度重算。
    const point = chipButtons(host, '.scope-columns__point')
      .find((button) => button.textContent?.includes('直角三角形的判别'))!
    point.click()
    await nextTick()
    expect(chapterCheck.indeterminate).toBe(true)
    expect(bar()?.textContent).toContain('第一章 勾股定理 · 1 探索勾股定理（本节）')
    expect(bar()?.textContent).not.toContain('（全章）')

    // 再取消本节的一个知识点 → 散知识点计数行。
    const point2 = chipButtons(host, '.scope-columns__point')
      .find((button) => button.textContent?.includes('勾股定理的逆定理'))!
    point2.click()
    await nextTick()
    expect(bar()?.textContent).toContain('第一章 勾股定理 · 1 个知识点')

    // 摘要行 × 删除：清空本章已选。
    const remove = bar()?.querySelector<HTMLButtonElement>(
      'button[aria-label^="取消 第一章 勾股定理"]',
    )
    expect(remove).toBeTruthy()
    remove!.click()
    await nextTick()
    expect(store.params.scopeKeys).toEqual([])
    expect(bar()?.textContent).toContain('未选择（默认全库范围）')
  })

  it('removes single knowledge points from the manager overlay', async () => {
    const { host, store } = await mountPanel(legacyFacets())

    host.querySelector<HTMLInputElement>('input[aria-label="全选 第一章 勾股定理"]')!.click()
    await nextTick()
    const manage = chipButtons(host, '.scope-columns__manage')
      .find((button) => button.textContent?.trim() === '管理')!
    manage.click()
    await nextTick()

    const panel = host.querySelector('.scope-columns__panel')!
    expect(panel.textContent).toContain('已选范围明细')
    const chipRemove = panel.querySelector<HTMLButtonElement>('button[aria-label="取消 勾股定理"]')!
    chipRemove.click()
    await nextTick()
    expect(store.params.scopeKeys).not.toContain('kp-1')
    expect(store.params.scopeKeys).toContain('kp-2')
    expect(host.querySelector('.scope-columns__bar')?.textContent)
      .toContain('第一章 勾股定理 · 2 一定是直角三角形吗（本节）')
  })

  it('merges legacy six-value question type facets into three main chips with subtype row', async () => {
    const { host, store } = await mountPanel(legacyFacets())

    const mainChips = chipButtons(host, '.ai-assembly__type-list .ai-assembly__chip')
    expect(mainChips.map((button) => button.textContent?.replace(/\s+/g, ''))).toEqual([
      '选择题583',
      '填空题357',
      '解答题466',
    ])

    // 选中解答题：数量默认 1，出现子类行（全部/画图/计算/证明 + 未标注静态数字）。
    mainChips[2]!.click()
    await nextTick()
    expect(store.params.typeCounts).toEqual({ 解答题: 1 })
    const countInput = host.querySelector<HTMLInputElement>('input[aria-label="解答题 数量"]')!
    expect(countInput.value).toBe('1')

    const subtypes = host.querySelector('.ai-assembly__subtypes')!
    const subtypeText = subtypes.textContent?.replace(/\s+/g, '') ?? ''
    expect(subtypeText).toContain('全部')
    expect(subtypeText).toContain('画图142')
    expect(subtypeText).toContain('计算139')
    expect(subtypeText).toContain('证明56')
    expect(subtypeText).toContain('未标注129')

    const proof = chipButtons(host, '.ai-assembly__subtypes .ai-assembly__chip--sub')
      .find((button) => button.textContent?.includes('证明'))!
    proof.click()
    await nextTick()
    expect(store.params.essaySubtype).toBe('证明')

    const all = chipButtons(host, '.ai-assembly__subtypes .ai-assembly__chip--sub')
      .find((button) => button.textContent?.trim() === '全部')!
    all.click()
    await nextTick()
    expect(store.params.essaySubtype).toBeNull()

    // 修改数量；再点主 chip 取消，子类行随之消失。
    countInput.value = '3'
    countInput.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(store.params.typeCounts).toEqual({ 解答题: 3 })

    chipButtons(host, '.ai-assembly__type-list .ai-assembly__chip')[2]!.click()
    await nextTick()
    expect(store.params.typeCounts).toEqual({})
    expect(host.querySelector('.ai-assembly__subtypes')).toBeNull()
  })

  it('reads subtype counts from special_type facets after the type migration', async () => {
    const { host } = await mountPanel(migratedFacets())

    const mainChips = chipButtons(host, '.ai-assembly__type-list .ai-assembly__chip')
    expect(mainChips.map((button) => button.textContent?.replace(/\s+/g, ''))).toEqual([
      '选择题583',
      '填空题357',
      '解答题466',
    ])

    mainChips[2]!.click()
    await nextTick()
    const subtypeText = host.querySelector('.ai-assembly__subtypes')?.textContent?.replace(/\s+/g, '') ?? ''
    expect(subtypeText).toContain('画图142')
    expect(subtypeText).toContain('计算139')
    expect(subtypeText).toContain('证明56')
    // 未标注 = 解答题总数 466 − 三个子类之和 337。
    expect(subtypeText).toContain('未标注129')
    expect(subtypeText).not.toContain('动态几何题')
  })

  it('shows the empty template card and navigates to the paper library', async () => {
    const { host, router } = await mountPanel(legacyFacets())

    const card = host.querySelector('[data-testid="ai-template-card"]')!
    expect(card.textContent).toContain('未使用模板')
    const pick = [...card.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('去题库选真卷'))!
    pick.click()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('question-bank'))
  })

  it('applies the template query, summarizes structure by count and clears the query', async () => {
    const { host, router, store } = await mountPanel(
      legacyFacets(),
      '/question-assembly?mode=ai&template=7',
    )

    await vi.waitFor(() => expect(store.params.templatePaperId).toBe(7))
    // query 中的 template 随即清除，mode 保留，避免刷新重复套用。
    await vi.waitFor(() => expect(router.currentRoute.value.query.template).toBeUndefined())
    expect(router.currentRoute.value.query.mode).toBe('ai')

    const card = host.querySelector('[data-testid="ai-template-card"]')!
    await vi.waitFor(() => expect(card.textContent).toContain('题型结构：选择题 × 10，填空题 × 6'))
    expect(card.textContent).toContain('2024 年南山区期末卷')
    expect(card.textContent).toContain('2024 · 期末 · 16 题')

    // 模板模式：题型区只读展示模板结构，比例留空沿用模板难度。
    expect(host.textContent).toContain('已选模板，题型与数量以模板为准。')
    expect(store.params.difficultyRatio).toEqual({ easy: null, medium: null, hard: null })

    // 移除模板：回到自由组卷，难度比例恢复默认预填。
    const remove = [...card.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '移除')!
    remove.click()
    await nextTick()
    expect(store.params.templatePaperId).toBeNull()
    expect(card.textContent).toContain('未使用模板')
    expect(store.params.difficultyRatio).toEqual({ easy: 30, medium: 50, hard: 20 })
  })

  it('clears a previously chosen essay subtype when a template is applied', async () => {
    const { host, router, store } = await mountPanel(legacyFacets())

    // 自由组卷下先选解答题 + 证明子类。
    chipButtons(host, '.ai-assembly__type-list .ai-assembly__chip')[2]!.click()
    await nextTick()
    chipButtons(host, '.ai-assembly__subtypes .ai-assembly__chip--sub')
      .find((button) => button.textContent?.includes('证明'))!
      .click()
    await nextTick()
    expect(store.params.essaySubtype).toBe('证明')

    // 再从题库跳入模板：子类选择被清掉，不变成隐藏约束。
    await router.push({ query: { mode: 'ai', template: '7' } })
    await vi.waitFor(() => expect(store.params.templatePaperId).toBe(7))
    expect(store.params.essaySubtype).toBeNull()
    await vi.waitFor(() => expect(router.currentRoute.value.query.template).toBeUndefined())
  })
})
