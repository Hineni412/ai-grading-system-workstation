import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import { questionBankApi, type ChapterExamProfile as ChapterExamProfileData, type QuestionBankPaper, type QuestionRepairPreview, type QuestionBankFacets, type QuestionSkillIndex, type QuestionBankListItem } from '../api/question-bank'
import { trainingApi, type TrainingOverview } from '../api/training'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import QuestionBasketDrawer from '../components/question-bank/QuestionBasketDrawer.vue'
import { useAssemblyStore } from '../stores/assembly'
import QuestionBankTodo from '../components/question-bank/QuestionBankTodo.vue'
import QuestionRepairDialog from '../components/question-bank/QuestionRepairDialog.vue'
import QuestionTagBrowser from '../components/question-bank/QuestionTagBrowser.vue'
import QuestionCard from '../components/question-bank/QuestionCard.vue'
import { normalizeChapters, toggleChapter, queryValues } from '../components/question-bank/question-tag-filters'
import ChapterExamProfile from '../components/question-bank/ChapterExamProfile.vue'
import * as studentsApi from '../api/students'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import QuestionAnnotationPanel from '../components/question-bank/QuestionAnnotationPanel.vue'
import { createAppRouter } from '../router'
import { CURRICULUM_SCOPE_STORAGE_KEY } from '../stores/curriculum-scope'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import QuestionBankView from '../views/QuestionBankView.vue'
import ConfirmDialogHost from '../components/design-system/ConfirmDialogHost.vue'

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

function prepareInitialQuestionBank() {
  const host = document.createElement('div'); document.body.append(host)
  const pinia = createPinia()
  const router = createAppRouter(createMemoryHistory())
  const bank = useQuestionBankStore(pinia)
  const scope = useCurriculumScopeStore(pinia)
  scope.selectedVolumeId = 'bnu24-math-g8-upper'
  vi.spyOn(scope, 'initialize').mockResolvedValue()
  vi.spyOn(bank, 'loadPapers').mockResolvedValue()
  vi.spyOn(useJobStore(pinia), 'initialize').mockResolvedValue()
  vi.spyOn(useAssemblyStore(pinia), 'load').mockResolvedValue()
  const index: Awaited<ReturnType<typeof questionBankApi.skillIndex>> = {
    curriculum_volume_id: scope.selectedVolumeId, graph_release_id: null, model_calls: 0,
    question_count: 1, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [],
  }
  const profile: ChapterExamProfileData = {
    curriculum_volume_id: scope.selectedVolumeId, graph_release_id: null, model_calls: 0,
    counted_question_count: 1, stages: [], merged_groups: [], chapters: [],
  }
  const preview = vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue({
    curriculum_volume_id: scope.selectedVolumeId, kind: 'all', fingerprint: 'a'.repeat(64),
    scanned_count: 1, question_count: 0, repairable_count: 0, model_calls: 0, items: [],
    skills_required: true,
    counts: { tags: 0, evidence: 0, criteria: 0, skills: 0, types: 0, knowledge_points: 0 },
  })
  return { host, pinia, router, bank, index, profile, preview }
}

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
    .find((button) => button.classList.contains('paper-card__title'))
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

function mountConfirmHost(): void {
  const el = document.createElement('div')
  document.body.append(el)
  const app = createApp(ConfirmDialogHost)
  app.mount(el)
  mounted.push(app)
}

async function confirmDialogOpen(): Promise<HTMLElement> {
  let dialog: HTMLElement | null = null
  await vi.waitFor(() => {
    dialog = document.body.querySelector<HTMLElement>('[data-testid="app-confirm-dialog"]')
    expect(dialog).not.toBeNull()
  })
  return dialog!
}

function confirmDialogButton(dialog: HTMLElement, label: string): HTMLButtonElement {
  return [...dialog.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.trim() === label)!
}

beforeEach(() => {
  vi.spyOn(questionBankApi, 'previewTagging').mockImplementation(async (_ids, volumeId) => ({ base_release_id: 'kgr_TEST', input_fingerprint: 'e'.repeat(64), volume_id: volumeId, chapters: [], planned_requests: 0, model_calls: 0 }))
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const knowledgeA = '八年级上册｜第一章 实数｜1 认识实数｜无理数'
const knowledgeB = '八年级上册｜第一章 实数｜2 平方根｜平方根'
function tagIndex(): QuestionSkillIndex {
  const stats = { question_count: 2, type_counts: { '选择题': 1, '多选题': 0, '填空题': 0, '解答题': 1 }, difficulty: null, criteria_needs_review_count: 0 }
  return {
    graph_release_id: 'kgr_TEST', curriculum_volume_id: 'bnu24-math-g8-upper', model_calls: 0, question_count: 6,
    unlinked: { no_usable_evidence: 1, no_skill_link: 2 },
    types: [
      { value: 'TEST-type-a', label: '题型·实数计算', count: 3, section_id: 'TEST-s1', definition: '', common_knowledge_points: [] },
      { value: 'TEST-type-b', label: '题型·平方根运算', count: 2, section_id: 'TEST-s2', definition: '', common_knowledge_points: [] },
      { value: 'TEST-type-c', label: '题型·证明全等', count: 1, section_id: 'TEST-s3', definition: '', common_knowledge_points: [] },
    ],
    chapters: [
      { id: 'TEST-c1', label: '第一章 实数', question_count: 5, cross_section_skills: [], sections: [
        { id: 'TEST-s1', label: '1 认识实数', question_count: 2, skills: [{ ...stats, stable_key: 'sk_TEST_one', display_name: '判断实数', full_name: '八年级上册/判断实数', cross_section: false, definition: { observable_evidence: '', include_scope: '', exclude_scope: '' } }], topics: [{ ...stats, stable_key: 'TEST-topic', display_name: '无理数', filter_value: knowledgeA }] },
        { id: 'TEST-s2', label: '2 平方根', question_count: 3, skills: [], topics: [] },
      ] },
      { id: 'TEST-c2', label: '第二章 三角形', question_count: 1, cross_section_skills: [], sections: [{ id: 'TEST-s3', label: '1 全等三角形', question_count: 1, skills: [], topics: [] }] },
    ],
  }
}
function tagFacets(): QuestionBankFacets {
  return { exam_scopes: [], curriculum_sections: [], curriculum_chapters: [], primary_sections: [{ value: 'TEST-s1', count: 2 }, { value: 'TEST-s2', count: 3 }, { value: 'TEST-s3', count: 1 }, { value: '', count: 2 }],
    task_types: [{ value: 'TEST-type-a', count: 3 }, { value: 'TEST-type-b', count: 2 }, { value: 'TEST-type-c', count: 1 }],
    knowledge_points: [{ value: knowledgeA, count: 2 }, { value: knowledgeB, count: 1 }],
    abilities: [{ value: '运算能力', count: 3 }], methods: [{ value: '公式法', count: 2 }, { value: '坐标法', count: 3 }], models: [{ value: '方程模型', count: 2 }], thoughts: [{ value: '数形结合', count: 2 }],
    special_types: [{ value: '新定义题', count: 2 }], error_pattern_categories: [{ value: '方法与思路', count: 3 }],
    error_types: [], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] }
}
function tagOverview(): TrainingOverview {
  return {
    scope: { mode: 'all', student_ids: ['1', '2', '3', '4'] },
    exam_scope: { mode: 'semester', session_ids: [7, 8], sessions: [{ session_id: 7, session_name: 'TEST-单元考试' }, { session_id: 8, session_name: 'TEST-阶段考试' }] },
    warnings: [],
    nodes: [
      { knowledge_key: 'TEST-c1', display_name: '第一章 实数', kind: 'chapter', chapter_key: 'TEST-c1', section_key: '', group_mastery: .73, evidence_student_count: 4, distribution: { weak: 1, unsteady: 2, stable: 1, insufficient: 0 }, students: [] },
      { knowledge_key: 'TEST-s1', display_name: '1 认识实数', kind: 'section', chapter_key: 'TEST-c1', section_key: 'TEST-s1', group_mastery: .61, evidence_student_count: 3, distribution: { weak: 1, unsteady: 0, stable: 1, insufficient: 1 }, students: [] },
      { knowledge_key: 'TEST-s2', display_name: '2 平方根', kind: 'section', chapter_key: 'TEST-c1', section_key: 'TEST-s2', group_mastery: null, evidence_student_count: 0, distribution: { weak: 0, unsteady: 0, stable: 0, insufficient: 0 }, students: [] },
      { knowledge_key: 'TEST-c2', display_name: '第二章 三角形', kind: 'chapter', chapter_key: 'TEST-c2', section_key: '', group_mastery: null, evidence_student_count: 0, distribution: { weak: 0, unsteady: 0, stable: 0, insufficient: 0 }, students: [] },
    ],
    students: [],
    summary: { student_count: 4, evidence_student_count: 4, exam_student_count: 4, exam_score_rate: .7, topic_count: 0, skill_count: 0, weak_topic_count: 0, weak_skill_count: 0 },
  }
}
async function mountTagBrowser(query = '?tab=tag') {
  const host = document.createElement('div'); document.body.append(host)
  const pinia = createPinia()
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/question-bank${query}`); await router.isReady()
  const scope = useCurriculumScopeStore(pinia)
  scope.selectedVolumeId = 'bnu24-math-g8-upper'
  const index = tagIndex()
  scope.loadState = 'ready'
  const mastery = useMasteryOverviewStore(pinia)
  const overview = vi.spyOn(trainingApi, 'overview').mockResolvedValue(tagOverview())
  vi.spyOn(studentsApi, 'fetchStudents').mockResolvedValue(Array.from({ length: 4 }, (_, index) => ({ id: index + 1, student_code: `TEST-${index}`, name: '合成学生', class_name: index < 2 ? '9班' : '10班', created_at: null })))
  scope.volumes = [{ id: scope.selectedVolumeId, label: '八年级上册', order: 1, grade: '八年级', semester: '上学期', textbook_version: '北师大版（2024）', source: {}, statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 }, chapters: index.chapters.map((chapter, order) => ({ id: chapter.id, knowledge_id: chapter.id, label: chapter.label, title: chapter.label, display_name: chapter.label, kind: 'chapter', number: String(order + 1), source_ref: { node_id: chapter.id, relative_url: '' }, exam_scope_values: [], order, sections: [] })) }]
  const bank = useQuestionBankStore(pinia)
  bank.papers = [{ ...paper, curriculum_volume_id: scope.selectedVolumeId, title: 'TEST-函数单元卷' }, { ...paper, id: 9, curriculum_volume_id: scope.selectedVolumeId, title: 'TEST-几何期末卷' }]; bank.papersState = 'ready'
  const list = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [item], total: 6, page: 1, page_size: 20, total_pages: 1 })
  const facets = vi.spyOn(questionBankApi, 'listFacets').mockResolvedValue(tagFacets())
  const app = createApp({ render: () => h(QuestionTagBrowser, { index, loading: false, error: '' }) })
  app.use(pinia).use(router).mount(host); mounted.push(app)
  await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe(query.includes('unlinked') ? 'todo' : 'tag'))
  if (!query.includes('unlinked')) await vi.waitFor(() => expect(bank.listState).toBe('ready'))
  await vi.waitFor(() => expect(mastery.loadState).toBe('ready'))
  const dimension = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('.qb-dimension-tabs button')].find(button => button.textContent?.startsWith(label))!
  const tag = (value: string) => [...host.querySelectorAll<HTMLButtonElement>('[data-tag-value]')].find(button => button.dataset.tagValue === value)!
  const chapter = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('.qb-chapter-name')].find(button => button.textContent === label)!
  return { host, bank, router, index, list, facets, dimension, tag, chapter, mastery, overview }
}

describe('question bank workspace', () => {
  it.each([
    [true, '完整'], [false, '缺知识点'], [undefined, '缺知识点'],
  ] as const)('uses knowledge coverage %s for the paper status without adding direct tags', async (coverage, expected) => {
    const { host, pinia, router, bank, index } = prepareInitialQuestionBank()
    bank.papers = [paper]; bank.papersState = 'ready'
    vi.spyOn(questionBankApi, 'skillIndex').mockResolvedValue(index)
    vi.spyOn(questionBankApi, 'listQuestionRefs').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 500, total_pages: 0 })
    const question = { ...item, labels: {
      primary_type: { key: 'TEST-type', label: 'TEST-题型' }, secondary_types: [], knowledge_points: [],
      ...(coverage === undefined ? {} : { knowledge_coverage: coverage }),
    } }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => new Response(JSON.stringify(
      String(input).startsWith('/api/question-bank/questions?')
        ? { items: [question], total: 1, page: 1, page_size: 100, total_pages: 1 } : {},
    ), { status: 200, headers: { 'content-type': 'application/json' } }))
    await router.push(`/question-bank?tab=paper&paper=${paper.id}`)
    const app = createApp(QuestionBankView)
    app.use(pinia).use(router).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.qb-number-nav button')?.getAttribute('aria-label')).toBe(`第 1 题 · ${expected}`))
    expect(bank.questions[0]?.labels?.knowledge_points).toEqual([])
  })

  it('rejects a nonboolean knowledge coverage field from the question API', async () => {
    const question = { ...item, labels: {
      primary_type: null, secondary_types: [], knowledge_points: [], knowledge_coverage: 'yes',
    } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      items: [question], total: 1, page: 1, page_size: 20, total_pages: 1,
    }), { status: 200, headers: { 'content-type': 'application/json' } }))
    await expect(questionBankApi.listQuestions()).rejects.toThrow()
  })

  it('loads the paper catalog only when entering its workspace from skill browsing', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia()
    const bank = useQuestionBankStore(pinia)
    const load = vi.spyOn(bank, 'loadPapers').mockResolvedValue()
    vi.spyOn(useCurriculumScopeStore(pinia), 'initialize').mockResolvedValue()
    vi.spyOn(useJobStore(pinia), 'initialize').mockResolvedValue()
    vi.spyOn(useAssemblyStore(pinia), 'load').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank?tab=skill'); await router.isReady()
    const app = createApp(QuestionBankView); app.use(pinia).use(router).mount(host); mounted.push(app)
    await nextTick()
    expect(load).not.toHaveBeenCalled()
    await router.push('/question-bank?tab=paper'); await nextTick()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it.each(['exam', 'tag'])('waits for the %s first-screen reads before background repair prefetch', async tab => {
    const { host, pinia, router, bank, index, profile, preview } = prepareInitialQuestionBank()
    let resolveIndex: (value: typeof index) => void = () => {}
    let resolveProfile: (value: typeof profile) => void = () => {}
    const indexRead = vi.spyOn(questionBankApi, 'skillIndex').mockImplementation(() =>
      new Promise(resolve => { resolveIndex = resolve }))
    const profileRead = vi.spyOn(questionBankApi, 'chapterExamProfile').mockImplementation(() =>
      new Promise(resolve => { resolveProfile = resolve }))
    await router.push(`/question-bank?tab=${tab}`)
    const app = createApp(QuestionBankView)
    app.use(pinia).use(router).mount(host); mounted.push(app)
    await nextTick()
    expect(indexRead).toHaveBeenCalledTimes(1)
    expect(profileRead).toHaveBeenCalledTimes(tab === 'exam' ? 1 : 0)
    expect(preview).not.toHaveBeenCalled()
    resolveIndex(index)
    await vi.waitFor(() => expect(bank.skillIndexes.has(index.curriculum_volume_id)).toBe(true))
    await nextTick()
    expect(preview).toHaveBeenCalledTimes(tab === 'exam' ? 0 : 1)
    resolveProfile(profile)
    await vi.waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    indexRead.mockResolvedValueOnce({ ...index, question_count: 2 })
    await bank.loadSkillIndex(index.curriculum_volume_id)
    await nextTick()
    expect(preview).toHaveBeenCalledTimes(1)
  })

  it.each(['todo', 'dialog'])('allows an explicit %s read when first-screen statistics fail', async entry => {
    const { host, pinia, router, preview } = prepareInitialQuestionBank()
    vi.spyOn(questionBankApi, 'skillIndex').mockRejectedValue(new Error('TEST-索引读取失败'))
    vi.spyOn(questionBankApi, 'chapterExamProfile').mockRejectedValue(new Error('TEST-考情读取失败'))
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    await router.push(`/question-bank?tab=${entry === 'todo' ? 'todo' : 'exam'}`)
    const app = createApp(QuestionBankView)
    app.use(pinia).use(router).mount(host); mounted.push(app)
    await nextTick()
    if (entry === 'dialog') {
      const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.includes('AI 补齐'))
      button?.click()
    }
    await vi.waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
    expect(document.body.textContent).not.toContain('缺失清单暂时无法读取')
  })

  it('defaults to the chapter exam tab and maps legacy query params to their views', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia()
    vi.spyOn(useCurriculumScopeStore(pinia), 'initialize').mockResolvedValue()
    vi.spyOn(useJobStore(pinia), 'initialize').mockResolvedValue()
    vi.spyOn(useAssemblyStore(pinia), 'load').mockResolvedValue()
    vi.spyOn(useQuestionBankStore(pinia), 'loadPapers').mockResolvedValue()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank'); await router.isReady()
    const app = createApp(QuestionBankView); app.use(pinia).use(router).mount(host); mounted.push(app)
    await nextTick()

    const nav = host.querySelector<HTMLElement>('nav.page-tabs[aria-label="题库视图"]')!
    expect(nav).not.toBeNull()
    expect(host.querySelector('.page-header__navigation')).not.toBeNull()
    expect(host.querySelector('nav.qb-viewnav')).toBeNull()
    expect([...nav.querySelectorAll('button')].map(b => b.textContent?.trim()))
      .toEqual(['章节考情', '按标签', '按试卷', '待处理'])
    const current = () => nav.querySelector('button[aria-current="page"]')?.textContent?.trim()
    expect(current()).toBe('章节考情')

    for (const [query, label] of [
      ['?skill=sk_1', '按标签'], ['?topic=t_1', '按标签'], ['?tagDim=idea', '按标签'],
      ['?paper=7', '按试卷'], ['?tab=todo&skill=sk_1', '待处理'], ['?tab=paper', '按试卷'],
    ] as const) {
      await router.push(`/question-bank${query}`); await nextTick()
      expect(current()).toBe(label)
    }

    ;[...nav.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === '章节考情')!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe('exam'))
  })

  it('previews individual missing parts and recovers an ambiguous repair submission with the same token', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia()
    const preview = { curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'skills' as const,
      fingerprint: 'a'.repeat(64), scanned_count: 100, question_count: 2, repairable_count: 1, model_calls: 0,
      skills_required: true,
      counts: { types: 0, knowledge_points: 0, tags: 0, evidence: 1, criteria: 1, skills: 2 }, items: [
        { id: 17, question_number: '1', paper_title: 'TEST-试卷', missing: ['skills' as const], blocked_reason: '', revision: 'b'.repeat(64) },
        { id: 18, question_number: '2', paper_title: 'TEST-试卷', missing: ['evidence' as const, 'criteria' as const, 'skills' as const], blocked_reason: '题目内容或图片无法读取', revision: 'c'.repeat(64) },
      ] }
    vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue(preview)
    const submit = vi.spyOn(questionBankApi, 'submitRepair').mockRejectedValueOnce(new ApiError({ kind: 'network', status: null,
      code: 'network', message: '', details: {}, requestId: 'TEST', retryable: false })).mockResolvedValue({
      id: 901, job_type: 'question_bank_repair', payload: { curriculum_volume_id: preview.curriculum_volume_id, kind: 'skills' },
      result: {}, status: 'running', stage: '', detail: '', progress: 0, error: null, cancel_requested: false,
      created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', started_at: null, finished_at: null,
    })
    const track = vi.spyOn(useJobStore(pinia), 'track').mockImplementation(() => {})
    const app = createApp(QuestionRepairDialog, { open: true, volumeId: preview.curriculum_volume_id, volumeLabel: '八年级上册', kind: 'skills' })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认补齐 1 题'))
    expect(document.body.textContent).toContain('题目内容或图片无法读取')
    expect(submit).not.toHaveBeenCalled()
    const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '确认补齐 1 题')!
    button.click()
    await vi.waitFor(() => expect(button.textContent).toBe('找回本次任务'))
    const firstToken = submit.mock.calls[0]![2]
    button.click()
    await vi.waitFor(() => expect(track).toHaveBeenCalled())
    expect(submit).toHaveBeenLastCalledWith(preview, [17], firstToken)
    expect(submit).toHaveBeenCalledTimes(2)
  })

  it('shows the analysis subset immediately and keeps confirmation disabled when revalidation fails', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia()
    const bank = useQuestionBankStore(pinia)
    const all: QuestionRepairPreview = {
      curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'all', fingerprint: 'a'.repeat(64),
      scanned_count: 20, question_count: 4, repairable_count: 3, model_calls: 0,
      skills_required: true,
      counts: { tags: 1, evidence: 1, criteria: 0, skills: 2, types: 1, knowledge_points: 0 },
      items: [
        { id: 17, question_number: '1', paper_title: 'TEST-缺分析', missing: ['tags', 'skills'], blocked_reason: '', revision: 'b'.repeat(64) },
        { id: 18, question_number: '2', paper_title: 'TEST-原题不可读', missing: ['evidence'], blocked_reason: '题目内容或图片无法读取', revision: 'c'.repeat(64) },
        { id: 19, question_number: '3', paper_title: 'TEST-只缺题型', missing: ['types'], blocked_reason: '', revision: 'd'.repeat(64) },
        { id: 20, question_number: '4', paper_title: 'TEST-只缺技能', missing: ['skills'], blocked_reason: '', revision: 'e'.repeat(64) },
      ],
    }
    bank.repairPreviews.set(`${all.curriculum_volume_id}:all`, all)
    let rejectRead: (error: Error) => void = () => {}
    const load = vi.spyOn(questionBankApi, 'repairPreview').mockImplementation(() =>
      new Promise<QuestionRepairPreview>((_resolve, reject) => { rejectRead = reject }))
    const submit = vi.spyOn(questionBankApi, 'submitRepair')
    const app = createApp(QuestionRepairDialog, { open: true, volumeId: all.curriculum_volume_id, volumeLabel: '八年级上册', kind: 'analysis' })
    app.use(pinia).mount(host); mounted.push(app)
    await nextTick()
    expect(document.body.textContent).toContain('发现 2 道缺失题；本次可处理 1 道。')
    expect(document.body.textContent).toContain('题型 · 0 题缺失')
    expect(document.body.textContent).toContain('关键步骤关联 · 1 题缺失')
    expect(document.body.textContent).not.toContain('TEST-只缺题型')
    expect(document.body.textContent).not.toContain('TEST-只缺技能')
    const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '确认补齐 1 题')
    expect(button?.disabled).toBe(true)
    expect(load).toHaveBeenCalledWith(all.curriculum_volume_id, 'analysis', expect.anything(), expect.anything())
    rejectRead(new Error('TEST-复核失败'))
    await vi.waitFor(() => expect(document.body.textContent).toContain('无法核对最新状态，请重试。'))
    expect(button?.disabled).toBe(true)
    button?.click()
    expect(submit).not.toHaveBeenCalled()
    expect(bank.repairPreviews.has(`${all.curriculum_volume_id}:analysis`)).toBe(false)
  })

  it.each(['resolve', 'reject'] as const)('keeps the reopened repair preview when a cancelled request later %s', async outcome => {
    const host = document.createElement('div'); document.body.append(host)
    const preview = { curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'skills' as const,
      fingerprint: 'a'.repeat(64), scanned_count: 1, question_count: 1, repairable_count: 1, model_calls: 0,
      skills_required: true,
      counts: { types: 0, knowledge_points: 0, tags: 0, evidence: 0, criteria: 0, skills: 1 }, items: [
        { id: 17, question_number: '1', paper_title: 'TEST-当前清单', missing: ['skills' as const], blocked_reason: '', revision: 'b'.repeat(64) },
      ] }
    let settlePrevious!: () => void
    const previous = new Promise<typeof preview>((resolve, reject) => {
      settlePrevious = () => outcome === 'resolve'
        ? resolve({ ...preview, items: [{ ...preview.items[0]!, paper_title: 'TEST-已取消清单' }] })
        : reject(new Error('TEST-cancelled request'))
    })
    const load = vi.spyOn(questionBankApi, 'repairPreview').mockReturnValueOnce(previous).mockResolvedValue(preview)
    const submit = vi.spyOn(questionBankApi, 'submitRepair')
    const open = ref(true)
    const app = createApp({ render: () => h(QuestionRepairDialog, {
      open: open.value, volumeId: preview.curriculum_volume_id, volumeLabel: '八年级上册', kind: 'skills',
    }) })
    app.use(createPinia()).mount(host); mounted.push(app)
    await nextTick()
    open.value = false; await nextTick()
    open.value = true; await nextTick()
    await vi.waitFor(() => expect(document.body.textContent).toContain('TEST-当前清单'))
    expect(load.mock.calls[0]![2]?.aborted).toBe(true)
    settlePrevious(); await previous.catch(() => {}); await nextTick()
    expect(document.body.textContent).toContain('确认补齐 1 题')
    expect(document.body.textContent).toContain('TEST-当前清单')
    expect(document.body.textContent).not.toContain('TEST-已取消清单')
    expect(document.body.textContent).not.toContain('缺失清单暂时无法读取')
    expect(submit).not.toHaveBeenCalled()
  })

  it('removes daily maintenance, whole-library retagging and trash entries', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); const bank = useQuestionBankStore(pinia)
    bank.papers = [paper]; bank.papersState = 'ready'
    const app = createApp(PaperLibrary); app.use(pinia).mount(host); mounted.push(app)
    expect(host.textContent).not.toContain('维护')
    expect(host.textContent).not.toContain('全库重新打标签')
    expect(host.textContent).not.toContain('回收站')
  })

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
    await openCardMenu(host, '匿名期末试卷')
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

  it('shows all semester questions and keeps every chosen dimension when switching labels', async () => {
    const { host, router, list, facets, dimension, tag, overview } = await mountTagBrowser()
    expect(host.textContent).toContain('本学期全部题目 · 共 6 题')
    expect(host.textContent).toContain('另有 2 题的主考小节不在本册')
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ scopeMode: 'primary', includeSkills: true, curriculumVolumeIds: ['bnu24-math-g8-upper'], curriculumSections: [], typeKeys: [], knowledgePoints: [], skillKeys: [], difficultyMin: 1, difficultyMax: 10, collapseDuplicates: true, sort: 'newest' }), expect.anything())
    tag('TEST-type-a').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'type')).toEqual(['TEST-type-a']))
    const reads = list.mock.calls.length
    const counts = facets.mock.calls.length
    dimension('方法').click(); await nextTick()
    expect(list).toHaveBeenCalledTimes(reads)
    expect(facets).toHaveBeenCalledTimes(counts)
    tag('公式法').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: ['TEST-type-a'], methods: ['公式法'] }), expect.anything()))
    tag('坐标法').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: ['TEST-type-a'], methods: ['公式法', '坐标法'] }), expect.anything()))
    dimension('知识点').click(); await nextTick(); tag(knowledgeA).click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: ['TEST-type-a'], knowledgePoints: [knowledgeA], methods: ['公式法', '坐标法'], scopeMode: 'primary' }), expect.anything()))
    expect(dimension('题型').querySelector('sup')?.textContent).toBe('1')
    expect(dimension('方法').querySelector('sup')?.textContent).toBe('2')
    expect(host.querySelector('.qb-selected-conditions')?.textContent).toContain('题型：实数计算')
    expect(host.querySelector('.qb-selected-conditions')?.textContent).toContain('知识点：无理数')
    host.querySelector<HTMLButtonElement>('[aria-label="删除方法：公式法"]')!.click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'method')).toEqual(['坐标法']))
    host.querySelector<HTMLButtonElement>('[aria-label="不限知识点"]')!.click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'kp')).toEqual([]))
    expect(queryValues(router.currentRoute.value.query, 'type')).toEqual(['TEST-type-a'])
    expect(overview).toHaveBeenCalledTimes(1)
    for (const retired of ['按技能', '知识主题', '未挂技能', 'AI 补挂技能', '标签筛选']) expect(host.textContent).not.toContain(retired)
    ;[...host.querySelectorAll<HTMLButtonElement>('.qb-selected-conditions button')].find(button => button.textContent?.trim() === '清空条件')!.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: [], methods: [], knowledgePoints: [], curriculumSections: [] }), expect.anything()))
  })

  it('separates exclusive chapter names from additive checkboxes and merges full chapter selections', async () => {
    const { host, router, list, chapter, index } = await mountTagBrowser()
    const boxes = () => [...host.querySelectorAll<HTMLInputElement>('.qb-chapter-options input')]
    const box = (label: string) => boxes().find(input => input.getAttribute('aria-label') === label)!
    chapter('第一章 实数').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ curriculumSections: ['TEST-c1'] }), expect.anything()))
    expect(box('同时看第一章 实数').checked).toBe(true)
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-c1"]')?.textContent).toContain('掌握度 73%')
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-c1"]')?.textContent).toContain('需关注 3 人 / 有证据 4 人')
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-s1"]')?.textContent).toContain('掌握度 61%')
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-s1"]')?.textContent).toContain('需关注 1 人 / 有证据 3 人')
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-s2"]')?.textContent).toBe('暂无作答证据')
    expect(host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-c2"]')?.textContent).toBe('暂无作答证据')
    expect(host.querySelector('.qb-chapter-heading')?.textContent).toContain('题库题数 · 随筛选更新')
    box('同时看第一章 实数 · 1 认识实数').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual(['TEST-s2']))
    expect(box('同时看第一章 实数').indeterminate).toBe(true)
    box('同时看第一章 实数 · 1 认识实数').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual(['TEST-c1']))
    box('同时看第二章 三角形').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual(['TEST-c1', 'TEST-c2']))
    chapter('1 认识实数').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual(['TEST-s1']))
    chapter('本学期全部章节').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual([]))
    expect(normalizeChapters(index.chapters, ['TEST-s1', 'TEST-s2', 'TEST-s2'])).toEqual(['TEST-c1'])
    expect(toggleChapter(index.chapters, ['TEST-c1'], 'TEST-s1')).toEqual(['TEST-s2'])
  })

  it('uses the chosen class and hides old chapter values during loading or failed scope changes', async () => {
    const { host, mastery, overview } = await mountTagBrowser()
    const summary = () => host.querySelector('.qb-chapter-mastery[data-knowledge="TEST-c1"]')?.textContent
    const classButton = (name: string) => [...host.querySelectorAll<HTMLButtonElement>('.knowledge-overview-scope-bar button')].find(button => button.textContent === name)!
    expect(host.querySelector('.qb-chapter-overview-scope')?.textContent).toContain('本学期 2 场考试 + 已发布训练')
    expect(summary()).toContain('掌握度 73%')
    let finish: ((result: TrainingOverview) => void) | undefined
    overview.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    classButton('10班').click()
    await vi.waitFor(() => expect(mastery.loadState).toBe('loading'))
    expect(summary()).toBe('学情读取中')
    expect(summary()).not.toContain('73%')
    expect(overview).toHaveBeenLastCalledWith(expect.objectContaining({ scope: expect.objectContaining({ mode: 'class', class_id: '10班', class_ids: ['10班'] }) }), expect.anything(), expect.anything())
    const selected = tagOverview()
    selected.nodes = selected.nodes.map(node => node.knowledge_key === 'TEST-c1' ? { ...node, group_mastery: .83, evidence_student_count: 2, distribution: { weak: 0, unsteady: 1, stable: 1, insufficient: 0 } } : node)
    selected.scope = { mode: 'class', class_id: '10班', class_ids: ['10班'], student_ids: ['3', '4'] }
    if (!finish) throw new Error('TEST overview request did not start')
    finish(selected)
    await vi.waitFor(() => expect(summary()).toContain('掌握度 83%'))
    expect(summary()).toContain('需关注 1 人 / 有证据 2 人')

    overview.mockRejectedValueOnce(new Error('TEST-refresh-failed'))
    await mastery.load('bnu24-math-g8-upper', true)
    await nextTick()
    expect(summary()).toContain('83%')
    expect(host.querySelector('.qb-chapter-overview-scope')?.textContent).toContain('显示上次学情，本次刷新失败。')
    overview.mockResolvedValueOnce(selected)
    ;[...host.querySelectorAll<HTMLButtonElement>('.qb-chapter-overview-scope button')].find(button => button.textContent === '重试学情')!.click()
    await vi.waitFor(() => expect(mastery.loadState).toBe('ready'))

    overview.mockRejectedValueOnce(new Error('TEST-class-read-failed'))
    classButton('9班').click()
    await vi.waitFor(() => expect(mastery.loadState).toBe('error'))
    expect(summary()).toBe('学情暂不可用')
    expect(summary()).not.toMatch(/\d+%|需关注/)
    expect(host.textContent).toContain('已知 x + y = 3')
    overview.mockResolvedValueOnce(tagOverview())
    ;[...host.querySelectorAll<HTMLButtonElement>('.qb-chapter-overview-scope button')].find(button => button.textContent === '重试学情')!.click()
    await vi.waitFor(() => expect(summary()).toContain('掌握度 73%'))
  })

  it('removes only zero-count type and knowledge conditions after changing chapter', async () => {
    const { host, router, list, facets, chapter } = await mountTagBrowser(`?tab=tag&type=TEST-type-a&kp=${encodeURIComponent(knowledgeA)}&method=公式法`)
    facets.mockImplementation(async filters => {
      const current = tagFacets()
      if (filters?.curriculumSections?.includes('TEST-c2')) {
        current.task_types = [{ value: 'TEST-type-a', count: 0 }, { value: 'TEST-type-c', count: 1 }]
        current.knowledge_points = [{ value: knowledgeA, count: 0 }]
        current.methods = [{ value: '公式法', count: 0 }]
      }
      return current
    })
    chapter('第一章 实数').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ curriculumSections: ['TEST-c1'], typeKeys: ['TEST-type-a'], knowledgePoints: [knowledgeA] }), expect.anything()))
    chapter('第二章 三角形').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'type')).toEqual([]))
    expect(queryValues(router.currentRoute.value.query, 'kp')).toEqual([])
    expect(queryValues(router.currentRoute.value.query, 'method')).toEqual(['公式法'])
    expect(host.textContent).toContain('已去掉在所选章节里没有题的条件：实数计算、无理数')
  })

  it('keeps chapter-valid type and knowledge conditions when another dimension makes the intersection empty', async () => {
    const { host, router, facets, chapter } = await mountTagBrowser(`?tab=tag&type=TEST-type-a&kp=${encodeURIComponent(knowledgeA)}&method=坐标法`)
    facets.mockImplementation(async filters => {
      const result = tagFacets()
      if (filters?.methods?.length) { result.task_types = []; result.knowledge_points = [] }
      return result
    })
    chapter('第一章 实数').click()
    await vi.waitFor(() => expect(facets).toHaveBeenLastCalledWith(expect.objectContaining({ curriculumSections: ['TEST-c1'], scopeMode: 'primary' }), expect.anything()))
    expect(facets.mock.lastCall?.[0]?.methods).toBeUndefined()
    expect(queryValues(router.currentRoute.value.query, 'type')).toEqual(['TEST-type-a'])
    expect(queryValues(router.currentRoute.value.query, 'kp')).toEqual([knowledgeA])
    expect(queryValues(router.currentRoute.value.query, 'method')).toEqual(['坐标法'])
    expect(host.textContent).not.toContain('已去掉')
  })

  it('keeps answer form, difficulty, source and progress rows in the same request as labels', async () => {
    const { host, list, tag } = await mountTagBrowser()
    tag('TEST-type-a').click(); await nextTick()
    ;[...host.querySelectorAll<HTMLButtonElement>('.qb-type-chips button')].find(button => button.textContent === '选择')!.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: ['TEST-type-a'], questionTypes: ['选择题', '多选题'] }), expect.anything()))
    const difficulty = host.querySelector<HTMLDetailsElement>('.qb-difficulty-filter')!
    difficulty.open = true; await nextTick()
    ;[...difficulty.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.includes('中档提升'))!.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ difficultyMin: 4.5, difficultyMax: 6.4 }), expect.anything()))
    const source = host.querySelector<HTMLDetailsElement>('.qb-source-filter')!
    source.open = true; source.dispatchEvent(new Event('toggle')); await nextTick()
    const search = source.querySelector<HTMLInputElement>('input')!
    search.value = '几何'; search.dispatchEvent(new Event('input')); await nextTick()
    const rows = source.querySelectorAll<HTMLButtonElement>('.qb-source-list button')
    expect(rows).toHaveLength(1); rows[0]!.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ paperIds: [9] }), expect.anything()))
    expect(source.open).toBe(false)
    const progress = host.querySelector<HTMLSelectElement>('[aria-label="教学进度"]')!
    progress.value = 'TEST-c1'; progress.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: ['TEST-type-a'], teachingProgressChapter: 'TEST-c1' }), expect.anything()))
  })

  it.each([
    ['?tab=skill', 'sec', []],
    ['?tagDim=typeKeys&tag=TEST-type-a', 'type', ['TEST-type-a']],
    ['?tagDim=knowledgePoints&tag=无理数', 'kp', ['无理数']],
    ['?tagDim=abilities&tag=运算能力', 'ability', ['运算能力']],
    ['?tagDim=thoughts&tag=数形结合', 'thought', ['数形结合']],
    ['?tagDim=methods&tag=公式法', 'method', ['公式法']],
    ['?tagDim=models&tag=方程模型', 'model', ['方程模型']],
    ['?tagDim=specialTypes&tag=新定义题', 'special', ['新定义题']],
    ['?tagDim=errorPatternCategories&tag=方法与思路', 'error', ['方法与思路']],
    ['?tab=skill&section=TEST-s1', 'sec', []],
    ['?tab=skill&section=TEST-s1&scope=volume', 'sec', []],
    ['?tab=skill&section=TEST-s1&scope=section', 'sec', ['TEST-s1']],
    ['?tab=skill&section=TEST-s1&scope=chapter', 'sec', ['TEST-c1']],
    ['?topic=TEST-topic', 'kp', [knowledgeA]],
  ])('converts the legacy address %s without retaining old navigation fields', async (query, key, expected) => {
    const { router } = await mountTagBrowser(query)
    expect(queryValues(router.currentRoute.value.query, key)).toEqual(expected)
    for (const obsolete of ['topic', 'tagDim', 'tag', 'section', 'scope']) expect(router.currentRoute.value.query[obsolete]).toBeUndefined()
  })

  it('keeps and removes a legacy skill condition without exposing a skill dimension', async () => {
    const { host, router, list } = await mountTagBrowser('?skill=sk_TEST_one')
    expect(host.querySelector('.qb-selected-chip.is-legacy')?.textContent).toContain('技能：判断实数（旧链接）')
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ skillKeys: ['sk_TEST_one'] }), expect.anything())
    expect(host.querySelector('.qb-dimension-tabs')?.textContent).not.toContain('技能')
    host.querySelector<HTMLButtonElement>('[aria-label="删除技能：判断实数（旧链接）"]')!.click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'skill')).toEqual([]))
  })

  it('ignores an unavailable legacy topic and sends unlinked bookmarks to pending maintenance', async () => {
    const { host, router } = await mountTagBrowser('?topic=TEST-missing')
    expect(host.textContent).toContain('该知识点不在当前学期')
    expect(router.currentRoute.value.query.topic).toBeUndefined()
    const pending = await mountTagBrowser('?tab=skill&skill=unlinked')
    expect(pending.router.currentRoute.value.query.tab).toBe('todo')
    expect(pending.router.currentRoute.value.query.skill).toBeUndefined()
  })

  it('keeps questions readable when facet counts fail and retries both count surfaces', async () => {
    const { host, facets, router, bank } = await mountTagBrowser()
    facets.mockRejectedValue(new Error('TEST-facet-unavailable'))
    await router.replace({ query: { tab: 'tag', ability: '运算能力' } })
    await vi.waitFor(() => expect(host.querySelectorAll('.qb-count-error')).toHaveLength(2))
    expect(host.textContent).toContain('题数暂时无法读取')
    expect(host.textContent).toContain('已知 x + y = 3')
    expect(bank.listState).toBe('ready')
    facets.mockResolvedValue(tagFacets())
    host.querySelector<HTMLButtonElement>('.qb-count-error button')!.click()
    await vi.waitFor(() => expect(host.querySelector('.qb-count-error')).toBeNull())
  })

  it('offers clear conditions after an empty intersection and resets answer filters', async () => {
    const { host, list, tag, bank } = await mountTagBrowser()
    list.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    tag('TEST-type-a').click()
    await vi.waitFor(() => expect(bank.listState).toBe('empty'))
    expect(host.textContent).toContain('没有同时满足全部条件的题')
    list.mockResolvedValue({ items: [item], total: 6, page: 1, page_size: 20, total_pages: 1 })
    host.querySelector<HTMLButtonElement>('[data-kind="empty"] button')!.click()
    await vi.waitFor(() => expect(bank.listState).toBe('ready'))
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ typeKeys: [], knowledgePoints: [], questionTypes: [], difficultyMin: 1, difficultyMax: 10 }), expect.anything())
  })

  it('uses type section metadata for grouping and keeps search out of address filters', async () => {
    const { host, chapter, tag, dimension, list, router } = await mountTagBrowser()
    chapter('第一章 实数').click()
    await vi.waitFor(() => expect(chapter('1 认识实数')).toBeTruthy())
    chapter('1 认识实数').click()
    await vi.waitFor(() => expect(queryValues(router.currentRoute.value.query, 'sec')).toEqual(['TEST-s1']))
    expect(tag('TEST-type-b').closest('.qb-tag-row')?.textContent).toContain('其他小节')
    expect(tag('TEST-type-a').textContent).toContain('实数计算')
    const reads = list.mock.calls.length
    dimension('知识点').click(); await nextTick()
    const search = host.querySelector<HTMLInputElement>('[aria-label="搜知识点"]')!
    search.value = '平方根'; search.dispatchEvent(new Event('input')); await nextTick()
    expect(host.querySelector('.qb-tag-lines')?.classList.contains('is-clamped')).toBe(false)
    expect(tag(knowledgeB).closest('.qb-tag-row')?.textContent).not.toContain('其他小节')
    expect(list).toHaveBeenCalledTimes(reads)
    expect(router.currentRoute.value.query.kp).toBeUndefined()
  })

  it('shows primary and secondary types, two knowledge points and no direct skill badges', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const question = { ...item, evidence_point_count: 3, skills: [{ stable_key: 'sk_TEST_one', display_name: '判断实数' }], labels: { primary_type: { key: 'TEST-a', label: '题型·实数计算' }, secondary_types: [{ key: 'TEST-b', label: '题型·分类讨论' }], knowledge_points: [{ key: 'kp1', label: '无理数' }, { key: 'kp2', label: '平方根' }, { key: 'kp3', label: '倒数' }] } }
    const app = createApp({ render: () => h(QuestionCard, { question }) })
    app.use(createPinia()).mount(host); mounted.push(app)
    expect(host.querySelector('.qb-type-capsule:not(.is-secondary)')?.textContent).toBe('实数计算')
    expect(host.querySelector('.qb-type-capsule.is-secondary')?.textContent).toBe('分类讨论')
    expect([...host.querySelectorAll('.qb-topic-capsule')].map(point => point.textContent)).toEqual(['无理数', '平方根'])
    expect(host.textContent).toContain('+1')
    expect(host.textContent).toContain('判定点 3')
    expect(host.textContent).not.toContain('技能')
  })

  it('keeps legacy knowledge labels but excludes raw type and skill identifiers from cards', () => {
    const host = document.createElement('div'); document.body.append(host)
    const question: QuestionBankListItem = { ...item, tags: [{ tag_type: 'knowledge_point', tag_value: knowledgeA, confidence: 1 }, { tag_type: 'knowledge_point', tag_value: 'sk_TEST_one', confidence: 1 }, { tag_type: 'knowledge_point', tag_value: 'kp_TEST_t01', confidence: 1 }] }
    const app = createApp({ render: () => h(QuestionCard, { question }) })
    app.use(createPinia()).mount(host); mounted.push(app)
    expect(host.textContent).toContain('无理数')
    expect(host.textContent).not.toContain('sk_TEST_one')
    expect(host.textContent).not.toContain('kp_TEST_t01')
  })

  function examProfile(): ChapterExamProfileData {
    return {
      curriculum_volume_id: 'bnu24-math-g8-upper',
      graph_release_id: 'kgr_TEST',
      model_calls: 0,
      counted_question_count: 6,
      stages: [
        { stage: 'midterm', label: '期中', paper_count: 2, group_count: 1, unit: '组' },
        { stage: 'final', label: '期末', paper_count: 1, group_count: 1, unit: '份' },
      ],
      merged_groups: [{ stage: 'midterm', papers: [{ id: 1, title: '期中A' }, { id: 2, title: '期中B' }] }],
      chapters: [{
        id: 'ch_TEST_2', label: '第二章 实数',
        totals: { midterm: { main: 5, cross: 0 }, final: { main: 2, cross: 0 } },
        difficulty: {
          midterm: { total: 5, basic: 3, mid: 2, hard: 0, choice: 4, fill: 0, written: 1 },
          final: { total: 2, basic: 0, mid: 2, hard: 0, choice: 0, fill: 0, written: 2 },
        },
        cross_question_ids: { midterm: [], final: [] },
        sections: [{
          id: 'sec_TEST_21', label: '2.1 认识实数', synthesis: false, main_count: 4,
          coverage: {
            midterm: { groups: 1, of: 1, percent: 100, questions: 3 },
            final: { groups: 1, of: 1, percent: 100, questions: 1 },
          },
          overview: {
            midterm: { basic: [1, 2], mid: [3], hard: [] },
            final: { basic: [6], mid: [], hard: [4] },
          },
          skills: [{
            key: 'sk_TEST_a', name: '区分有理数无理数', unlinked: false,
            home_section_label: '', definition: '无限不循环小数是无理数', total: 4, group_count: 2,
            cells: {
              midterm: { basic: [1, 2], mid: [3], hard: [] },
              final: { basic: [6], mid: [], hard: [4] },
            },
            positions: { midterm: { '1': [1], '2': [2] }, final: { '13': [4] } },
            typical: [
              { tier: 'basic', question_id: 1, same_tier_count: 2, group_count: 1, role: '基础入口', reason: '本节第1常考技能' },
              { tier: 'mid', question_id: 3, same_tier_count: 3, group_count: 2, role: '常见变式', reason: '同技能中档变式，同类3题' },
            ],
          }],
        }, {
          id: 'sec_TEST_22', label: '2.2 平方根与立方根', synthesis: false, main_count: 4,
          coverage: {
            midterm: { groups: 1, of: 1, percent: 100, questions: 2 },
            final: { groups: 0, of: 1, percent: 0, questions: 0 },
          },
          overview: {
            midterm: { basic: [9], mid: [8], hard: [] },
            final: { basic: [], mid: [], hard: [] },
          },
          skills: [{
            key: 'sk_TEST_b', name: '求平方根', unlinked: false,
            home_section_label: '', definition: '', total: 2, group_count: 1,
            cells: {
              midterm: { basic: [9], mid: [8], hard: [] },
              final: { basic: [], mid: [], hard: [] },
            },
            positions: { midterm: {}, final: {} },
            typical: [{ tier: 'basic', question_id: 9, same_tier_count: 3, group_count: 2, role: '基础入口', reason: '本节第1常考技能' }],
          }, {
            key: 'sk_TEST_c', name: '估算无理数范围', unlinked: false,
            home_section_label: '', definition: '', total: 1, group_count: 1,
            cells: {
              midterm: { basic: [7], mid: [], hard: [] },
              final: { basic: [], mid: [], hard: [] },
            },
            positions: { midterm: {}, final: {} },
            typical: [],
          }, {
            key: 'sk_TEST_d', name: '实数混合运算', unlinked: false,
            home_section_label: '', definition: '', total: 1, group_count: 1,
            cells: {
              midterm: { basic: [], mid: [10], hard: [] },
              final: { basic: [], mid: [], hard: [] },
            },
            positions: { midterm: {}, final: {} },
            typical: [],
          }],
        }, {
          id: 'sec_TEST_23', label: '☆ 问题解决策略', synthesis: false, main_count: 0,
          coverage: {
            midterm: { groups: 0, of: 1, percent: 0, questions: 0 },
            final: { groups: 0, of: 1, percent: 0, questions: 0 },
          },
          overview: {
            midterm: { basic: [], mid: [], hard: [] },
            final: { basic: [], mid: [], hard: [] },
          },
          skills: [],
        }],
      }, {
        id: 'ch_TEST_3', label: '第三章 三角形',
        totals: { midterm: { main: 4, cross: 1 }, final: { main: 1, cross: 0 } },
        difficulty: {
          midterm: { total: 4, basic: 1, mid: 1, hard: 1, choice: 3, fill: 0, written: 1 },
          final: { total: 1, basic: 0, mid: 0, hard: 1, choice: 0, fill: 0, written: 1 },
        },
        cross_question_ids: { midterm: [20], final: [] },
        sections: [{
          id: 'sec_TEST_31', label: '3.1 认识三角形', synthesis: false, main_count: 5,
          coverage: {
            midterm: { groups: 1, of: 1, percent: 100, questions: 4 },
            final: { groups: 1, of: 1, percent: 100, questions: 1 },
          },
          overview: {
            midterm: { basic: [11], mid: [12], hard: [13] },
            final: { basic: [], mid: [], hard: [14] },
          },
          skills: [{
            key: 'sk_TEST_e', name: '判断三边关系', unlinked: false,
            home_section_label: '', definition: '', total: 5, group_count: 2,
            cells: {
              midterm: { basic: [11], mid: [12], hard: [13] },
              final: { basic: [], mid: [], hard: [14] },
            },
            positions: { midterm: { '3': [11] }, final: { '3': [14] } },
            typical: [],
          }],
        }],
      }],
    }
  }

  class MockIntersectionObserver {
    static instances: MockIntersectionObserver[] = []
    readonly callback: IntersectionObserverCallback
    elements = new Set<Element>()
    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback
      MockIntersectionObserver.instances.push(this)
    }
    static reset(): void { MockIntersectionObserver.instances = [] }
    observe(el: Element): void { this.elements.add(el) }
    unobserve(el: Element): void { this.elements.delete(el) }
    disconnect(): void { this.elements.clear() }
    fire(ids: string[]): void {
      const entries = [...this.elements].map(el => ({
        target: el,
        isIntersecting: ids.includes((el as HTMLElement).dataset.cepBlock ?? ''),
      })) as unknown as IntersectionObserverEntry[]
      this.callback(entries, this as unknown as IntersectionObserver)
    }
  }

  function mountExamProfile(query: string) {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    const scope = useCurriculumScopeStore(pinia)
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    scope.loadState = 'ready'
    scope.volumes = [{ id: scope.selectedVolumeId, label: '八年级上册', order: 1, grade: '八年级', semester: '上学期', textbook_version: '北师大版（2024）', source: {}, statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 }, chapters: [] }]
    vi.spyOn(studentsApi, 'fetchStudents').mockResolvedValue([])
    vi.spyOn(trainingApi, 'overview').mockResolvedValue({
      nodes: [
        // 现行题型标准返回 kind: 'type' 的行，不能按 'skill' 过滤掉。
        // 明显薄弱占比分母剔除证据不足：2/(2+1+1) = 50%，3 名证据不足不计。
        { kind: 'type', knowledge_key: 'sk_TEST_a', display_name: '区分有理数无理数', chapter_key: 'ch', section_key: 'sec', group_mastery: 0.5, evidence_student_count: 7, distribution: { weak: 2, unsteady: 1, stable: 1, insufficient: 3 }, students: [] },
        { kind: 'skill', knowledge_key: 'sk_TEST_b', display_name: '求平方根', chapter_key: 'ch', section_key: 'sec2', group_mastery: 0.6, evidence_student_count: 10, distribution: { weak: 1, unsteady: 4, stable: 5, insufficient: 0 }, students: [] },
        // 有学情人数但三档之和为 0 → 证据不足。
        { kind: 'type', knowledge_key: 'sk_TEST_c', display_name: '估算无理数范围', chapter_key: 'ch', section_key: 'sec2', group_mastery: null, evidence_student_count: 3, distribution: { weak: 0, unsteady: 0, stable: 0, insufficient: 3 }, students: [] },
      ],
      exam_scope: { sessions: [] },
    } as unknown as Awaited<ReturnType<typeof trainingApi.overview>>)
    vi.spyOn(questionBankApi, 'chapterExamProfile').mockResolvedValue(examProfile())
    return { host, pinia, router, query }
  }

  it('restores the chapter profile before a refresh finishes and keeps it after failure', async () => {
    const { host, pinia, router, query } = mountExamProfile('?tab=exam&section=sec_TEST_21')
    await router.push(`/question-bank${query}`)
    const bank = useQuestionBankStore(pinia)
    bank.chapterExamProfiles.set('bnu24-math-g8-upper', examProfile())
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    let rejectRead: (error: Error) => void = () => {}
    vi.mocked(questionBankApi.chapterExamProfile).mockImplementation(() =>
      new Promise<ChapterExamProfileData>((_resolve, reject) => { rejectRead = reject }))
    const app = createApp(ChapterExamProfile)
    app.use(pinia).use(router).mount(host); mounted.push(app)
    await nextTick()
    expect(host.textContent).toContain('第二章 实数')
    expect(host.textContent).not.toContain('正在统计本册期中与期末卷')
    rejectRead(new Error('TEST-刷新失败'))
    await vi.waitFor(() => expect(host.querySelector('.chapter-exam')).not.toBeNull())
    expect(host.textContent).toContain('第二章 实数')
  })

  it('does not replace the new workspace list when an old chapter prefetch finishes', async () => {
    const { host, pinia, router, query } = mountExamProfile('?tab=exam&section=sec_TEST_21')
    await router.push(`/question-bank${query}`)
    const bank = useQuestionBankStore(pinia)
    let resolvePrefetch: (value: Awaited<ReturnType<typeof questionBankApi.listQuestions>>) => void = () => {}
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockImplementation(() =>
      new Promise(resolve => { resolvePrefetch = resolve }))
    const app = createApp(ChapterExamProfile)
    app.use(pinia).use(router).mount(host)
    await vi.waitFor(() => expect(list).toHaveBeenCalled())
    app.unmount()
    const current = { ...item, id: 501, question_text: 'TEST-当前筛选结果' }
    await bank.loadQuestions({ keyword: '当前筛选' }, async () => ({
      items: [current], total: 1, page: 1, page_size: 20, total_pages: 1,
    }))
    resolvePrefetch({ items: [{ ...item, id: 1 }], total: 1, page: 1, page_size: 100, total_pages: 1 })
    await nextTick(); await nextTick()
    expect(bank.appliedFilters.keyword).toBe('当前筛选')
    expect(bank.questions.map(question => question.id)).toEqual([501])
  })

  it('renders the chapter exam profile as one scroll and opens count cells in a drawer', async () => {
    const { host, pinia, router, query } = mountExamProfile('?tab=exam&section=sec_TEST_21')
    await router.push(`/question-bank${query}`)
    const bank = useQuestionBankStore(pinia)
    const itemsById = new Map([[1, { ...item, id: 1 }], [2, { ...item, id: 2 }], [3, { ...item, id: 3 }], [4, { ...item, id: 4 }], [6, { ...item, id: 6 }], [9, { ...item, id: 9 }]])
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockImplementation(async (filters = {}) => {
      const ids = filters.questionIds ?? []
      return {
        items: ids.map(id => itemsById.get(id)!).filter(Boolean),
        total: ids.length,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 100,
        total_pages: 1,
      }
    })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    // 期中与期末始终并列：没有阶段切换器。
    await vi.waitFor(() => expect(host.textContent).toContain('出卷率'))
    expect(host.querySelector('.app-segmented')).toBeNull()
    // 出卷率哑铃图：变化列与平均题数按夹具计算。
    const overviewBlock = host.querySelector<HTMLElement>('[data-cep-block="overview"]')!
    expect(overviewBlock.textContent).toContain('第二章 实数 · 出卷率')
    expect(overviewBlock.textContent).toContain('▼ −100 ppt')
    expect(overviewBlock.textContent).toContain('持平')
    expect(overviewBlock.textContent).toContain('期中平均 5.00 题/组')
    expect(overviewBlock.textContent).toContain('期末平均 2.00 题/份')
    // 哑铃图圆点可点开抽屉：2.1 与 2.2 各期中期末两点。
    const dots = [...overviewBlock.querySelectorAll<HTMLButtonElement>('.cep-dumb-row:not(.is-empty) button.cep-dumb-dot')]
    expect(dots).toHaveLength(4)
    // 期中→期末漂移箭头：Δ≠0 的 2.2 行有箭头，2.1 持平行与空行不画。
    const dumbRows = [...overviewBlock.querySelectorAll<HTMLElement>('.cep-dumb-row')]
    const row21 = dumbRows.find(row => row.textContent?.includes('2.1'))!
    const row22 = dumbRows.find(row => row.textContent?.includes('2.2'))!
    expect(row22.querySelector('.cep-dumb-arrow')).not.toBeNull()
    expect(row21.querySelector('.cep-dumb-arrow')).toBeNull()
    // 命题结构蝴蝶图：每个小节一行，期中向左、期末向右，按难度档分段。
    const bflyRows = [...overviewBlock.querySelectorAll<HTMLElement>('.cep-bfly-row')]
    expect(bflyRows).toHaveLength(3)
    const midSegments = bflyRows[0]!.querySelectorAll('.cep-bfly-half.is-left .cep-seg')
    expect([...midSegments].map(b => b.textContent?.trim())).toEqual(['2', '1'])
    const finSegments = bflyRows[0]!.querySelectorAll('.cep-bfly-half.is-right .cep-seg')
    expect([...finSegments].map(b => b.textContent?.trim())).toEqual(['1', '1'])
    // 难度与题型表已移除；蝴蝶图下方给出两阶段难度构成摘要与跨章链接。
    expect(host.textContent).not.toContain('难度与题型')
    expect(overviewBlock.textContent).toContain('全章 期中 5 题：基础 3（60%）· 中档 2（40%）· 提高 0（0%）')
    expect(overviewBlock.textContent).toContain('全章 期末 2 题：基础 0（0%）· 中档 2（100%）· 提高 0（0%）')
    expect(overviewBlock.textContent).toContain('另有')
    expect(overviewBlock.textContent).toContain('主要考其他章、同时涉及本章')
    // 章节页签：每章一枚，当前章高亮；页签只写章名与题数，主考数收进 title/aria。
    const chapterTabs = [...host.querySelectorAll<HTMLButtonElement>('.cep-chapter-tab')]
    expect(chapterTabs).toHaveLength(2)
    expect(chapterTabs[0]!.textContent).toContain('第二章 实数')
    expect(chapterTabs[0]!.textContent).not.toContain('主考')
    expect(chapterTabs[0]!.textContent).toContain('7')
    expect(chapterTabs[0]!.getAttribute('title')).toBe('本章主考 7 题')
    expect(chapterTabs[0]!.getAttribute('aria-current')).toBe('true')
    expect(chapterTabs[1]!.textContent).toContain('第三章 三角形')
    expect(chapterTabs[1]!.getAttribute('aria-current')).toBeNull()
    // 点击蝴蝶分段打开抽屉：2.1 · 期中 · 基础 → [1,2]。
    ;(midSegments[0] as HTMLButtonElement).click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('2.1 认识实数 · 期中 · 基础 · 2 题'))
    await vi.waitFor(() => expect(bank.questions.map(question => question.id)).toEqual([1, 2]))
    const closeB = [...document.body.querySelectorAll<HTMLButtonElement>('[data-slot="sheet-content"] button')].find(button => button.getAttribute('aria-label') === '关闭')!
    closeB.click()
    await vi.waitFor(() => expect(document.body.querySelector('[data-slot="sheet-content"]')).toBeNull())
    // 本章优先讲题型：有证据的行入图，证据不足与无证据列在下方。
    await vi.waitFor(() => expect(overviewBlock.querySelectorAll('.cep-quad-dot')).not.toHaveLength(0))
    expect(overviewBlock.querySelectorAll('.cep-quad-dot')).toHaveLength(2)
    expect(overviewBlock.textContent).toContain('常考且薄弱 · 优先讲')
    expect(overviewBlock.textContent).toContain('证据不足：估算无理数范围')
    expect(overviewBlock.textContent).toContain('暂无学情证据：实数混合运算')
    ;(overviewBlock.querySelector<HTMLButtonElement>('.cep-quad-dot.is-priority'))!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('区分有理数无理数 · 期中＋期末 · 5 题'))
    await vi.waitFor(() => expect(bank.questions.map(question => question.id).sort()).toEqual([1, 2, 3, 4, 6]))
    const closeC = [...document.body.querySelectorAll<HTMLButtonElement>('[data-slot="sheet-content"] button')].find(button => button.getAttribute('aria-label') === '关闭')!
    closeC.click()
    await vi.waitFor(() => expect(document.body.querySelector('[data-slot="sheet-content"]')).toBeNull())
    // 小节索引条：本章总览 + 各小节，无主考题的小节禁用。
    const chips = [...host.querySelectorAll<HTMLButtonElement>('.cep-index-chip')]
    expect(chips.map(chip => chip.textContent?.trim())).toEqual(['本章总览', '2.1', '2.2', '☆'])
    expect(chips[3]!.disabled).toBe(true)
    expect(chips[1]!.classList.contains('is-active')).toBe(true)
    // 小节块标题行含小节名、主考数与出卷率。
    const block = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_21"]')!
    expect(block.textContent).toContain('2.1 认识实数')
    expect(block.textContent).toContain('本节主考 4 题')
    expect(block.textContent).toContain('出卷率')
    expect(block.textContent).toContain('区分有理数无理数')
    expect(host.textContent).toContain('同源卷合并')
    // 本班明显薄弱列：弱占比分母剔除证据不足 2/(2+1+1) = 50%，主考 4 题 ≥3 → 常考且薄弱。
    // 证据节点 kind: 'type'（题型标准）与 'skill' 都要命中对应行键。
    await vi.waitFor(() => expect(block.textContent).toContain('50%（2/4 人）'))
    expect(block.querySelector('.cep-badge-warn')?.textContent).toBe('常考且薄弱')
    const block22 = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_22"]')!
    expect(block22.textContent).toContain('10%（1/10 人）')
    expect(block22.textContent).toContain('证据不足')
    expect(block22.textContent).toContain('无证据')
    expect(block22.querySelector('.cep-badge-warn')).toBeNull()
    // 怎么考：难度档分段两阶段合计（基础 期中 2 题 + 期末 1 题 = 3）。
    expect(block.textContent).toContain('怎么考')
    const combinedCell = [...block.querySelectorAll<HTMLButtonElement>('.cep-seg')].find(button => button.textContent?.trim() === '3')!
    combinedCell.click()
    await vi.waitFor(() => expect(document.body.querySelector('[data-slot="sheet-content"]')).not.toBeNull())
    expect(document.body.textContent).toContain('2.1 认识实数 · 区分有理数无理数 · 基础 · 期中＋期末 · 3 题')
    await vi.waitFor(() => expect(bank.questions.map(question => question.id)).toEqual([1, 2, 6]))
    await vi.waitFor(() => expect(document.body.querySelectorAll('[data-slot="sheet-content"] .qb-question-card')).toHaveLength(3))
    // 关闭抽屉回到整页，展开的题卡收起。
    const close = [...document.body.querySelectorAll<HTMLButtonElement>('[data-slot="sheet-content"] button')].find(button => button.getAttribute('aria-label') === '关闭')!
    close.click()
    await vi.waitFor(() => expect(document.body.querySelector('[data-slot="sheet-content"]')).toBeNull())
    expect(bank.selectedQuestionId).toBeNull()
    expect(list.mock.calls.some(call => JSON.stringify(call[0]?.questionIds) === '[1,2,6]')).toBe(true)
  })

  it('renders the position bubble strip for both stages and opens a drawer from a bubble', async () => {
    const { host, pinia, router } = mountExamProfile('?tab=exam&section=sec_TEST_21')
    await router.push('/question-bank?tab=exam&section=sec_TEST_21')
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.textContent).toContain('落在第几题'))
    const block = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_21"]')!
    expect(block.querySelectorAll('.cep-pos-row')).toHaveLength(1)
    expect(block.querySelectorAll('.cep-pos-dot.is-midterm')).toHaveLength(2)
    expect(block.querySelectorAll('.cep-pos-dot.is-final')).toHaveLength(1)
    expect(block.textContent).toContain('第1—8题')
    expect(block.textContent).toContain('第9—13题')
    expect(block.textContent).toContain('第14题起')
    // 平均题号漂移：期中 (1+2)/2=1.5，期末 13.0 → 后移 11.5 题；细灰道竖线＋线＋三角箭头，无菱形。
    expect(block.textContent).toContain('平均题号 1.5 → 13.0（后移 11.5 题）')
    expect(block.querySelectorAll('.cep-pos-mark')).toHaveLength(0)
    expect(block.querySelectorAll('.cep-pos-drift-lane')).toHaveLength(1)
    expect(block.querySelectorAll('.cep-pos-drift-tick')).toHaveLength(1)
    expect(block.querySelectorAll('.cep-pos-drift-line')).toHaveLength(1)
    expect(block.querySelectorAll('.cep-pos-drift-head')).toHaveLength(1)
    expect(block.querySelector('.cep-pos-drift-hit')?.getAttribute('aria-label'))
      .toBe('平均题号 1.5 → 13.0（后移 11.5 题）')
    // 点击气泡打开抽屉：类型 × 阶段 × 题号。
    block.querySelector<HTMLButtonElement>('.cep-pos-dot.is-midterm')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('2.1 认识实数 · 区分有理数无理数 · 第1题 · 期中 · 1 题'))
  })

  it('switches the chapter from a chapter tab and updates the query', async () => {
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.cep-chapter-tab')).toHaveLength(2))
    expect(host.textContent).toContain('2.1 认识实数')
    const next = [...host.querySelectorAll<HTMLButtonElement>('.cep-chapter-tab')]
      .find(tab => tab.textContent?.includes('第三章 三角形'))!
    next.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.chapter).toBe('ch_TEST_3'))
    expect(router.currentRoute.value.query.section).toBeUndefined()
    await vi.waitFor(() => expect(host.textContent).toContain('3.1 认识三角形'))
    expect(host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_31"]')).not.toBeNull()
    expect(host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_21"]')).toBeNull()
    expect(next.getAttribute('aria-current')).toBe('true')
    // 切换后的章节摘要与难度摘要随章变化；3.1 行平均题号持平 → 只画菱形不画箭头。
    const overviewBlock = host.querySelector<HTMLElement>('[data-cep-block="overview"]')!
    await vi.waitFor(() => expect(overviewBlock.textContent).toContain('全章 期中 4 题：基础 1（25%）· 中档 1（25%）· 提高 1（25%）· 未定难度 1'))
    const block31 = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_31"]')!
    expect(block31.textContent).toContain('平均题号 3.0 → 3.0（基本持平）')
    expect(block31.querySelectorAll('.cep-pos-drift-tick')).toHaveLength(1)
    expect(block31.querySelectorAll('.cep-pos-drift-line')).toHaveLength(0)
    expect(block31.querySelectorAll('.cep-pos-drift-head')).toHaveLength(0)
  })

  it('renders a legacy ?stage=final address without the stage toggle', async () => {
    const { host, pinia, router } = mountExamProfile('?tab=exam&stage=final')
    await router.push('/question-bank?tab=exam&stage=final')
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.textContent).toContain('出卷率'))
    expect(host.querySelector('.app-segmented')).toBeNull()
    expect(host.querySelector('.cep-index-chip.is-active')).not.toBeNull()
  })

  it('scrolls to a section block from the index chip and updates the query', async () => {
    MockIntersectionObserver.reset()
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.cep-index-chip')).toHaveLength(4))
    const chip = [...host.querySelectorAll<HTMLButtonElement>('.cep-index-chip')]
      .find(item => item.textContent?.trim() === '2.2')!
    chip.click()
    await nextTick()
    expect(scrollIntoView).toHaveBeenCalled()
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('sec_TEST_22'), { timeout: 2000 })
    expect(router.currentRoute.value.query.chapter).toBe('ch_TEST_2')
    const active = host.querySelector<HTMLButtonElement>('.cep-index-chip.is-active')!
    expect(active.textContent?.trim()).toBe('2.2')
  })

  it('restores the saved chapter and section when the query has none', async () => {
    localStorage.setItem(
      'chapter-exam:last:bnu24-math-g8-upper',
      JSON.stringify({ chapterId: 'ch_TEST_2', sectionId: 'sec_TEST_22', stage: 'final' }),
    )
    const { host, pinia, router } = mountExamProfile('?tab=exam')
    await router.push('/question-bank?tab=exam')
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('sec_TEST_22'))
    expect(router.currentRoute.value.query.chapter).toBe('ch_TEST_2')
    // 旧记录中的阶段字段不再恢复、不再写回地址。
    expect(router.currentRoute.value.query.stage).toBeUndefined()
    await vi.waitFor(() => expect(
      list.mock.calls.some(call => JSON.stringify(call[0]?.questionIds) === '[1,3,9]'),
    ).toBe(true))
    expect(host.querySelector('.cep-index-chip.is-active')?.textContent?.trim()).toBe('2.2')
  })

  it('falls back to the chapter overview when the saved position is stale', async () => {
    localStorage.setItem(
      'chapter-exam:last:bnu24-math-g8-upper',
      JSON.stringify({ chapterId: 'ch_GONE', sectionId: 'sec_GONE', stage: 'final' }),
    )
    const { host, pinia, router } = mountExamProfile('?tab=exam')
    await router.push('/question-bank?tab=exam')
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, total_pages: 0 })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    // 失效的章节号不写入地址，落在第一章总览并预取全章典型题。
    await vi.waitFor(() => expect(
      list.mock.calls.some(call => JSON.stringify(call[0]?.questionIds) === '[1,3,9]'),
    ).toBe(true))
    expect(router.currentRoute.value.query.chapter).toBeUndefined()
    expect(router.currentRoute.value.query.section).toBeUndefined()
    expect(host.querySelector('.cep-index-chip.is-active')?.textContent?.trim()).toBe('本章总览')
  })

  it('shows typical questions inline under each section from the cached picks', async () => {
    MockIntersectionObserver.reset()
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    const itemsById = new Map([[1, { ...item, id: 1 }], [3, { ...item, id: 3 }], [9, { ...item, id: 9 }]])
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockImplementation(async (filters = {}) => {
      const ids = filters.questionIds ?? []
      return {
        items: ids.map(id => itemsById.get(id)!).filter(Boolean),
        total: ids.length,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 100,
        total_pages: 1,
      }
    })
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    // 全章典型题一次性按本章题号拉取，一型一卡横向轮播。
    await vi.waitFor(() => expect(
      list.mock.calls.some(call => JSON.stringify(call[0]?.questionIds) === '[1,3,9]'),
    ).toBe(true))
    const first = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_21"]')!
    const second = host.querySelector<HTMLElement>('[data-cep-block="sec_TEST_22"]')!
    await vi.waitFor(() => expect(first.textContent).toContain('典型题 · 1 个常考技能'))
    expect(first.querySelectorAll('.cep-typical .cep-qcard')).toHaveLength(1)
    // 卡片头部：类型名 + 常考位次/题数/卷组数。
    const card = first.querySelector<HTMLElement>('.cep-qcard')!
    expect(card.textContent).toContain('区分有理数无理数')
    expect(card.textContent).toContain('本节第1常考 · 共 4 题 · 出现在 2 套卷')
    // 默认显示第一个 pick（基础），切到中档换题。
    await vi.waitFor(() => expect(card.querySelector('#qb-question-1')).not.toBeNull())
    expect(card.querySelector('.qb-badge-label')?.textContent).toBe('本节第1常考技能')
    const tabs = [...card.querySelectorAll<HTMLButtonElement>('.cep-tier-tab')]
    expect(tabs.map(tab => tab.textContent?.trim())).toEqual(['基础', '中档'])
    tabs[1]!.click()
    await vi.waitFor(() => expect(card.querySelector('#qb-question-3')).not.toBeNull())
    expect(card.querySelector('.qb-badge-label')?.textContent).toBe('同技能中档变式，同类3题')
    await vi.waitFor(() => expect(second.textContent).toContain('典型题 · 1 个常考技能'))
    expect(second.querySelectorAll('.cep-typical .cep-qcard')).toHaveLength(1)
    const calls = list.mock.calls.length
    // 滚动定位到其他块：典型题来自章级缓存，不再请求列表接口。
    const io = MockIntersectionObserver.instances[MockIntersectionObserver.instances.length - 1]!
    io.fire(['sec_TEST_22'])
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('sec_TEST_22'), { timeout: 2000 })
    expect(list.mock.calls.length).toBe(calls)
    io.fire(['overview'])
    expect(list.mock.calls.length).toBe(calls)
  })

  it('opens the typical-question viewer from the focused track and skims cards with keyboard', async () => {
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    const itemsById = new Map([[1, { ...item, id: 1 }], [3, { ...item, id: 3 }], [9, { ...item, id: 9 }]])
    vi.spyOn(questionBankApi, 'listQuestions').mockImplementation(async (filters = {}) => {
      const ids = filters.questionIds ?? []
      return {
        items: ids.map(id => itemsById.get(id)!).filter(Boolean),
        total: ids.length,
        page: filters.page ?? 1,
        page_size: filters.pageSize ?? 100,
        total_pages: 1,
      }
    })
    const detail = vi.spyOn(questionBankApi, 'getQuestion').mockImplementation(async id => ({
      ...itemsById.get(id)!,
      page_range: null,
      assets: [],
      previews: [],
      rich_content: {
        available: true,
        question_block_count: 1,
        answer_block_count: 1,
        question_blocks: [{ kind: 'paragraph', text: `题干 ${id}`, segments: [], rows: [], asset_indexes: [], asset_urls: ['test-fig.png'] }],
        answer_blocks: [{ kind: 'paragraph', text: `解析 ${id}`, segments: [], rows: [], asset_indexes: [], asset_urls: [] }],
      },
    }))
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.cep-car-track')).not.toBeNull())
    // 轮播轨道聚焦后按 Enter → 打开第一张卡的放大浏览。
    host.querySelector<HTMLElement>('.cep-car-track')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await vi.waitFor(() => expect(document.querySelector('.cep-tv-dialog')).not.toBeNull())
    const dialog = () => document.querySelector<HTMLElement>('.cep-tv-dialog')!
    expect(document.body.textContent).toContain('区分有理数无理数')
    expect(document.body.textContent).toContain('第 1 / 2 个技能')
    expect(document.body.textContent).toContain('2.1 认识实数')
    // 数字键 2 切到中档档位的 pick（题号 1 → 3）。
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: '2', bubbles: true }))
    await vi.waitFor(() => expect(document.body.textContent).toContain('题干 3'))
    expect(document.body.textContent).not.toContain('解析 3')
    // A 展开答案与解析。
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }))
    await vi.waitFor(() => expect(document.body.textContent).toContain('解析 3'))
    // → 跨小节进入下一卡（2.2 求平方根）。
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await vi.waitFor(() => expect(document.body.textContent).toContain('求平方根'))
    expect(document.body.textContent).toContain('2.2 平方根与立方根')
    expect(document.body.textContent).toContain('第 2 / 2 个技能')
    // 已到末尾，再按 → 不越界。
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await nextTick()
    expect(document.body.textContent).toContain('第 2 / 2 个技能')
    // F 切换第一张配图的整宽缩放状态。
    const figure = () => dialog().querySelector('.cep-tv-body .question-content__media img')
    expect(figure()).not.toBeNull()
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }))
    await nextTick()
    expect(figure()!.classList.contains('is-zoom')).toBe(true)
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }))
    await nextTick()
    expect(figure()!.classList.contains('is-zoom')).toBe(false)
    // Escape 关闭。
    dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await vi.waitFor(() => expect(document.querySelector('.cep-tv-dialog')).toBeNull())
    expect(detail).toHaveBeenCalled()
  })

  const todoPreview = {
    curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'all' as const,
    fingerprint: 'a'.repeat(64), scanned_count: 4, question_count: 4, repairable_count: 4, model_calls: 0,
    skills_required: true,
    counts: { types: 1, knowledge_points: 1, tags: 1, evidence: 1, criteria: 1, skills: 2 },
    items: [
      { id: 21, question_number: '3', paper_title: 'TEST-缺标签卷', missing: ['tags' as const, 'evidence' as const], blocked_reason: '', revision: 'b'.repeat(64) },
      { id: 22, question_number: '4', paper_title: 'TEST-缺判定点卷', missing: ['criteria' as const, 'skills' as const], blocked_reason: '', revision: 'c'.repeat(64) },
      { id: 23, question_number: '5', paper_title: 'TEST-只缺技能卷', missing: ['skills' as const], blocked_reason: '', revision: 'd'.repeat(64) },
      { id: 24, question_number: '6', paper_title: 'TEST-缺题型与知识点卷', missing: ['types' as const, 'knowledge_points' as const], blocked_reason: '', revision: 'e'.repeat(64) },
    ],
  }

  it('shows each repair question once and filters locally without reloading full question lists', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    const list = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [item], total: 1, page: 1, page_size: 20, total_pages: 1 })
    const preview = vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue(todoPreview)
    const standard = vi.spyOn(questionBankApi, 'standardSummary')
    const candidates = vi.spyOn(questionBankApi, 'skillCandidateSummary')
    const openQuestion = vi.fn(), repair = vi.fn()
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 2, onOpenQuestion: openQuestion, onRepair: repair }) })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.qb-todo-questions article')).toHaveLength(4))
    expect(host.querySelector('.qb-todo-overview h2')?.textContent).toBe('待补齐 4 题')
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ criteriaNeedsReview: true }), expect.anything())
    expect(standard).not.toHaveBeenCalled(); expect(candidates).not.toHaveBeenCalled()
    const filterButton = [...host.querySelectorAll<HTMLButtonElement>('.qb-todo-filters button')].find(button => button.textContent?.includes('分析未完成'))!
    expect(filterButton.textContent).toContain('2 题')
    filterButton.click(); await nextTick()
    const articles = host.querySelectorAll('.qb-todo-questions article')
    expect(articles).toHaveLength(2)
    expect(articles[0]?.textContent).toContain('缺题目标签')
    expect(articles[1]?.textContent).toContain('缺判定点')
    expect(articles[1]?.textContent).toContain('缺关键步骤')
    articles[0]?.querySelector<HTMLButtonElement>('button')?.click()
    expect(openQuestion).toHaveBeenCalledWith({ questionId: 21, paperId: null })
    host.querySelector<HTMLButtonElement>('.qb-todo-filter-action button')?.click()
    expect(repair).toHaveBeenCalledWith('analysis')
    expect(preview).toHaveBeenCalledTimes(1); expect(list).toHaveBeenCalledTimes(1)
  })

  it('hides the key-step filter and gap maintenance when the volume has no key-step vocabulary', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-lower'
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue({ ...todoPreview,
      curriculum_volume_id: 'bnu24-math-g8-lower', skills_required: false,
      question_count: 1, repairable_count: 1, items: [todoPreview.items[0]!] })
    const candidates = vi.spyOn(questionBankApi, 'skillCandidateSummary')
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 0 }) })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.qb-todo-overview h2')?.textContent).toBe('待补齐 1 题'))
    expect([...host.querySelectorAll<HTMLButtonElement>('.qb-todo-filters button')].map(button => button.textContent).join())
      .not.toContain('缺关键步骤')
    const maintenance = [...host.querySelectorAll<HTMLDetailsElement>('.qb-todo-fold')]
      .find(details => details.textContent?.includes('辅助维护'))!
    expect(maintenance.querySelector('summary')?.textContent).toContain('标准与新词')
    maintenance.open = true
    maintenance.dispatchEvent(new Event('toggle'))
    await nextTick()
    await vi.waitFor(() => expect(candidates).not.toHaveBeenCalled())
    expect(maintenance.textContent).not.toContain('关键步骤缺口')
    expect(maintenance.textContent).not.toContain('题量少的关键步骤')
  })

  it('keeps the repair list usable when the independent review list fails', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    vi.spyOn(questionBankApi, 'listQuestions').mockRejectedValue(new Error('review timeout'))
    vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue(todoPreview)
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 0 }) })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.qb-todo-questions article')).toHaveLength(4))
    expect(host.querySelector('.qb-todo-overview h2')?.textContent).toBe('待补齐 4 题')
    expect(host.querySelector('.qb-todo-questions')?.textContent).not.toContain('读取失败')
    expect(host.querySelector('.qb-todo-fold')?.textContent).toContain('人工审核清单读取失败')
    const button = [...host.querySelectorAll<HTMLButtonElement>('.qb-todo-filters button')].find(entry => entry.textContent?.includes('缺知识点'))!
    button.click(); await nextTick()
    expect(host.querySelectorAll('.qb-todo-questions article')).toHaveLength(1)
    expect(host.querySelector('.qb-todo-questions article')?.textContent).toContain('TEST-缺题型与知识点卷')
  })

  it('does not present a failed repair preview as zero pending questions and allows retry', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    const preview = vi.spyOn(questionBankApi, 'repairPreview').mockRejectedValueOnce(new Error('timeout')).mockResolvedValue({ ...todoPreview, question_count: 0, repairable_count: 0, items: [], counts: { types: 0, knowledge_points: 0, tags: 0, evidence: 0, criteria: 0, skills: 0 } })
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 0 }) })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.qb-todo-questions')?.textContent).toContain('待补齐题目暂时无法读取'))
    expect(host.querySelector('.qb-todo-overview h2')?.textContent).toBe('待补齐 — 题')
    const retry = [...host.querySelectorAll<HTMLButtonElement>('.qb-todo-questions button')].find(button => button.textContent === '重试')!
    retry.click()
    await vi.waitFor(() => expect(host.querySelector('.qb-todo-overview h2')?.textContent).toBe('待补齐 0 题'))
    expect(host.querySelector('.qb-todo-questions')?.textContent).toContain('当前学期没有需要补齐的题目。')
    expect(preview).toHaveBeenCalledTimes(2)
  })

  it('uses the shared basket draft and preserves it when a removal is rejected', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); const basket = useAssemblyStore(pinia)
    basket.loadState = 'ready'; basket.draft.basket_ids = [17]; basket.draft.order_ids = [17]; basket.draft.practice_rules = true
    basket.questions = [{ ...item, score_value: 0 }]
    const remove = vi.spyOn(basket, 'removeQuestion').mockResolvedValue(false)
    const router = createAppRouter(createMemoryHistory()); await router.push('/question-bank')
    const app = createApp(QuestionBasketDrawer, { open: true }); app.use(pinia).use(router).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(document.querySelector('.qb-basket-drawer')?.textContent).toContain('同技能最多 1 道'))
    basket.draft.practice_rules = {purpose:'handout',question_count:10,difficulty_max:10,max_questions_per_skill:3,max_written_questions:4,recent_activity_count:0}
    await nextTick()
    expect(document.querySelector('.qb-basket-drawer')?.textContent).toContain('同技能最多 3 道 · 解答题最多 4 道 · 难度 ≤ 10 · 排除最近 0 次原题')
    const button = [...document.querySelectorAll<HTMLButtonElement>('.qb-basket-drawer button')].find(button => button.textContent === '移出')!
    button.click(); await vi.waitFor(() => expect(remove).toHaveBeenCalledWith(17))
    expect(basket.draft.basket_ids).toEqual([17])
    expect(document.querySelector('.qb-basket-drawer a')?.getAttribute('href')).toBe('/question-assembly?mode=edit')
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

  it.each(['published', 'failed_status', 'failed_result', 'outcome_unknown', 'skipped', 'paused'])('shows chapter child %s in the existing import result', async (outcome) => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(QuestionImportJobs)
    app.use(pinia)
    app.mount(host)
    mounted.push(app)
    const base: JobResponse = { id: 43, job_type: 'tagging_sync', payload: {}, result: { chapter_type_job_id: 44, chapter_type_state: 'queued' },
      status: 'succeeded', progress: 1, stage: '', detail: '', error: null, cancel_requested: false,
      created_at: '2026-10-08T10:00:00Z', started_at: null, updated_at: '2026-10-08T10:00:01Z', finished_at: '2026-10-08T10:00:01Z' }
    let resolveChild!: (job: JobResponse) => void
    const pending = new Promise<JobResponse>(resolve => { resolveChild = resolve })
    const getJob = vi.fn(() => pending)
    const jobs = useJobStore(pinia)
    await jobs.initialize({ api: { getJob, cancelJob: vi.fn(async () => base), getJobStatusBatch: vi.fn(async () => []) },
      now: () => new Date(0), schedule: setTimeout, cancelScheduled: clearTimeout, pollIntervalMs: 2_000, maxBackoffMs: 30_000 })
    jobs.track(base)
    await nextTick()
    expect(host.textContent).toContain('等待题型整理完成')
    expect(getJob).toHaveBeenCalledExactlyOnceWith(44, expect.any(AbortSignal))
    resolveChild({ ...base, id: 44, job_type: 'chapter_type_organize', payload: { private_body: 'TEST-full-model-body' },
      status: outcome === 'failed_status' ? 'failed' : outcome === 'paused' ? 'paused' : 'succeeded',
      result: ['failed_status', 'paused'].includes(outcome) ? {} : { outcome: outcome === 'failed_result' ? 'failed' : outcome, chapter_summaries: [{ label: '第1章', new_type_count: 2,
        question_count: 30, unclassified_count: 1, types: [{ name: 'TEST整式化简', question_count: 29 }] }] } })
    await vi.waitFor(() => expect(jobs.jobs[44]).toBeDefined())
    const noteByOutcome: Record<string, string> = { failed_result: '题型整理未完成，原标准已保留', skipped: '本次可用题目尚未达到章节整理条件', paused: '题型整理已暂停。' }
    const expectedCopy = outcome === 'published'
      ? ['第1章已整理题型', '新增 2 类', '本次处理 30 题', '待归类 1 题', 'TEST整式化简 · 29 题']
      : [noteByOutcome[outcome] ?? '题型整理任务状态需要核对；不会自动追加请求']
    for (const copy of expectedCopy) expect(host.textContent).toContain(copy)
    expect(host.textContent?.includes('第1章已整理题型')).toBe(outcome === 'published')
    expect(host.textContent?.includes('原标准已保留')).toBe(outcome === 'failed_result')
    expect(host.textContent).not.toContain('TEST-full-model-body')
    expect(host.querySelectorAll('.qb-job')).toHaveLength(1)
    jobs.$dispose()
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
    expect(document.body.querySelector('.question-preview-sheet')).not.toBeNull()
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
    mountConfirmHost()
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

    const dialog = await confirmDialogOpen()
    expect(dialog.textContent).toContain('1 道题')
    confirmDialogButton(dialog, '继续').click()

    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )).toBe(true))
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
    expect(document.querySelector('[role=dialog][aria-label=试卷回收站]')).toBeNull()
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
    expect([...host.querySelectorAll('.paper-batch-bar button')]).toHaveLength(0)

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
    expect([...host.querySelectorAll('.paper-batch-bar button')]).toHaveLength(0)
  })

  it.each([1, 501])('confirms one chapter scope and keeps per-paper batches for %i questions each', async (questionCount) => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = [{ ...paper, question_count: questionCount }, { ...paper, id: 5, title: '第二份试卷', question_count: questionCount }]
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    mountConfirmHost()
    const taggingBodies: Array<Record<string, unknown>> = []
    const idsFor = (paperId: number) => Array.from({ length: questionCount }, (_, index) => 3_000 + paperId + index * 10)
    const scopeIds = [...idsFor(4), ...idsFor(5)]
    const finalIds = idsFor(5).slice(Math.floor((questionCount - 1) / 500) * 500)
    const plan = { base_release_id: 'kgr_TEST', input_fingerprint: 'e'.repeat(64), volume_id: 'bnu24-math-g7-lower',
      chapters: [{ chapter_id: 'kp_TEST', label: '第1章', question_count: scopeIds.length, paper_count: 2, unclassified_count: scopeIds.length, planned_requests: 1 }],
      pending_question_ids: scopeIds, planned_requests: 1, model_calls: 0 as const }
    vi.mocked(questionBankApi.previewTagging).mockResolvedValueOnce(plan)
    const child: JobResponse = { id: 990, job_type: 'chapter_type_organize', payload: {}, result: {}, status: 'queued', progress: 0, stage: '', detail: '', error: null,
      cancel_requested: false, created_at: '2026-10-08T10:00:00Z', started_at: null, updated_at: '2026-10-08T10:00:00Z', finished_at: null }
    const getJob = vi.fn(async () => child)
    const jobs = useJobStore(pinia)
    await jobs.initialize({ api: { getJob, cancelJob: vi.fn(async () => child), getJobStatusBatch: vi.fn(async () => []) }, now: () => new Date(0),
      schedule: setTimeout, cancelScheduled: clearTimeout, pollIntervalMs: 2_000, maxBackoffMs: 30_000 })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.startsWith('/api/question-bank/question-refs')) {
        const paperIds = new URL(url, 'http://local.test').searchParams
          .getAll('paper_ids')
          .map(Number)
        const page = Number(new URL(url, 'http://local.test').searchParams.get('page') || 1)
        const refs = paperIds.flatMap(paperId => idsFor(paperId).map(id => ({ id, paper_id: paperId, question_number: String(id) })))
        return response({ items: refs.slice((page - 1) * 500, page * 500), total: refs.length, page, page_size: 500, total_pages: Math.ceil(refs.length / 500) })
      }
      if (url === '/api/question-bank/tagging-jobs' && init?.method === 'POST') {
        taggingBodies.push(JSON.parse(String(init.body)))
        return response({
          id: 300 + taggingBodies.length,
          job_type: 'tagging_sync',
          payload: {},
          result: taggingBodies.length === Math.ceil(questionCount / 500) * 2 ? { chapter_type_job_id: 990, chapter_type_state: 'queued' } : {},
          status: taggingBodies.length === Math.ceil(questionCount / 500) * 2 ? 'succeeded' : 'queued',
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

    const batchDialog = await confirmDialogOpen()
    expect(batchDialog.textContent).toContain(`还有 ${scopeIds.length} 道题未打全标签`)
    expect(batchDialog.textContent).toContain(`共 ${scopeIds.length} 道题提交后端逐题核对`)
    expect(batchDialog.textContent?.split('预计增加 1 次模型请求')).toHaveLength(2)
    expect(questionBankApi.previewTagging).toHaveBeenCalledExactlyOnceWith(finalIds, paper.curriculum_volume_id, undefined, scopeIds)
    confirmDialogButton(batchDialog, '继续').click()

    await vi.waitFor(() => expect(taggingBodies).toHaveLength(Math.ceil(questionCount / 500) * 2))
    expect(taggingBodies.map((body) => body.question_ids)).toEqual([4, 5].flatMap(id => Array.from({ length: Math.ceil(questionCount / 500) }, (_, index) => idsFor(id).slice(index * 500, (index + 1) * 500))))
    expect(taggingBodies.slice(0, -1).every(body => body.chapter_type_authorization === undefined)).toBe(true)
    expect(taggingBodies[taggingBodies.length - 1]?.chapter_type_authorization).toEqual({ ...plan, confirmed: true, request_limit: 1 })
    await vi.waitFor(() => expect(jobs.jobs[990]).toBeDefined())
    expect(getJob).toHaveBeenCalledExactlyOnceWith(990, expect.any(AbortSignal))
    expect(taggingBodies.every((body) => body.force_retag === undefined)).toBe(true)
    expect(host.textContent).toContain(`已提交 2 份试卷等待后端核对（其中 ${scopeIds.length} 道题未打全标签）`)
    jobs.$dispose()
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
