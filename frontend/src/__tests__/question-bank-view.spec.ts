import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import { questionBankApi, type ChapterExamProfile as ChapterExamProfileData, type QuestionBankPaper } from '../api/question-bank'
import { trainingApi } from '../api/training'
import QuestionBasketDrawer from '../components/question-bank/QuestionBasketDrawer.vue'
import { useAssemblyStore } from '../stores/assembly'
import QuestionBankTodo from '../components/question-bank/QuestionBankTodo.vue'
import QuestionRepairDialog from '../components/question-bank/QuestionRepairDialog.vue'
import QuestionSkillBrowser from '../components/question-bank/QuestionSkillBrowser.vue'
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

describe('question bank workspace', () => {
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

  it.each(['resolve', 'reject'] as const)('keeps the reopened repair preview when a cancelled request later %s', async outcome => {
    const host = document.createElement('div'); document.body.append(host)
    const preview = { curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'skills' as const,
      fingerprint: 'a'.repeat(64), scanned_count: 1, question_count: 1, repairable_count: 1, model_calls: 0,
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
    vi.spyOn(questionBankApi, 'listFacets').mockResolvedValue({ exam_scopes: [], curriculum_sections: [], knowledge_points: [], curriculum_chapters: [], abilities: [], methods: [], models: [], thoughts: [], special_types: [], error_types: [], error_pattern_categories: [], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] })
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
    await router.replace({ query: { tab: 'skill', skill: 'unlinked' } })
    await vi.waitFor(() => expect(host.querySelector('.qb-skill-layout')?.classList.contains('is-unlinked')).toBe(true))
    expect(host.querySelector('.qb-skill-pane')).not.toBeNull()
    expect(host.querySelector('.qb-unlinked')).not.toBeNull()
    expect(host.textContent).toContain('AI 补挂技能')
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ skillUnlinked: true, skillKeys: [] }))
  })

  it('switches sections from the column header and passes band and source picks to the shared list', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank?tab=skill')
    const scope = useCurriculumScopeStore(pinia)
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    scope.volumes = [{ id: scope.selectedVolumeId, label: '八年级上册', order: 1, grade: '八年级', semester: '上学期', textbook_version: '北师大版（2024）', source: {}, statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 }, chapters: [{ id: 'kp_TEST_chapter', knowledge_id: 'kp_TEST_chapter', label: '第一章', title: '第一章', display_name: '第一章', kind: 'chapter', number: '1', source_ref: { node_id: 'TEST', relative_url: '' }, exam_scope_values: [], order: 1, sections: [] }] }]
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    vi.spyOn(questionBankApi, 'listFacets').mockResolvedValue({ exam_scopes: [], curriculum_sections: [], knowledge_points: [], curriculum_chapters: [], abilities: [], methods: [], models: [], thoughts: [], special_types: [], error_types: [], error_pattern_categories: [], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] })
    const overview = vi.spyOn(trainingApi, 'overview').mockResolvedValue({ nodes: [] } as unknown as Awaited<ReturnType<typeof trainingApi.overview>>)
    bank.papers = [
      { ...paper, id: 4, curriculum_volume_id: 'bnu24-math-g8-upper', title: 'TEST-函数单元卷' },
      { ...paper, id: 9, curriculum_volume_id: 'bnu24-math-g8-upper', title: 'TEST-几何期末卷' },
    ]
    bank.papersState = 'ready'
    const stats = { question_count: 1, type_counts: { '选择题': 0, '多选题': 0, '填空题': 0, '解答题': 1 }, difficulty: { min: 4, median: 4, max: 4 }, criteria_needs_review_count: 0 }
    const skill = (key: string, name: string) => ({ ...stats, stable_key: key, display_name: name, full_name: `八年级上册/${name}`, cross_section: false, definition: { observable_evidence: '判断', include_scope: '三边', exclude_scope: '作图' } })
    const section = (id: string, label: string, skills: ReturnType<typeof skill>[]) => ({ id, label, question_count: 1, skills, topics: [] })
    const index = { graph_release_id: 'kgr_TEST', curriculum_volume_id: scope.selectedVolumeId, model_calls: 0, question_count: 2, unlinked: { no_usable_evidence: 1, no_skill_link: 2 }, chapters: [{ id: 'kp_TEST_chapter', label: '第一章', question_count: 2, cross_section_skills: [], sections: [section('kp_TEST_s1', '第一节', [skill('sk_TEST_one', '判断直角三角形')]), section('kp_TEST_s2', '第二节', [skill('sk_TEST_two', '应用勾股定理')])] }] }
    const app = createApp({ render: () => h(QuestionSkillBrowser, { index, loading: false, error: '' }) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ skillKeys: ['sk_TEST_one'] })))

    const switcher = host.querySelector<HTMLDetailsElement>('.qb-section-switcher')!
    switcher.open = true
    await nextTick()
    const row = [...switcher.querySelectorAll<HTMLButtonElement>('.qb-tree-row')].find(button => button.textContent?.includes('第二节'))!
    row.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('kp_TEST_s2'))
    expect(switcher.open).toBe(false)
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ skillKeys: ['sk_TEST_two'] })))

    const difficulty = host.querySelector<HTMLDetailsElement>('.qb-difficulty-filter')!
    difficulty.open = true
    await nextTick()
    const band = [...difficulty.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.includes('中档提升'))!
    band.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ difficultyMin: 4.5, difficultyMax: 6.4 })))

    const source = host.querySelector<HTMLDetailsElement>('.qb-source-filter')!
    source.open = true
    source.dispatchEvent(new Event('toggle'))
    await nextTick()
    const search = source.querySelector<HTMLInputElement>('input[aria-label="筛选来源"]')!
    search.value = '几何'
    search.dispatchEvent(new Event('input'))
    await nextTick()
    const rows = [...source.querySelectorAll<HTMLButtonElement>('.qb-source-list .qb-source-row')]
    expect(rows).toHaveLength(1)
    expect(rows[0]!.textContent).toContain('TEST-几何期末卷')
    rows[0]!.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ paperIds: [9] })))
    expect(source.open).toBe(false)

    const unlinkedButton = host.querySelector<HTMLButtonElement>('.qb-unlinked')!
    expect(unlinkedButton.textContent).toContain('未挂技能')
    unlinkedButton.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.skill).toBe('unlinked'))
    expect(host.querySelector('.qb-skill-pane')).not.toBeNull()
    expect(host.querySelector('.qb-section-switcher')).not.toBeNull()

    // The mastery read needs only group-level nodes, not per-student detail.
    await vi.waitFor(() => expect(overview).toHaveBeenCalledWith(expect.objectContaining({ include_student_detail: false }), expect.anything()), { timeout: 4000 })
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
            midterm: { choice_basic: [1, 2], choice_advanced: [3], fill: [], written: [] },
            final: { choice_basic: [], choice_advanced: [], fill: [], written: [4] },
          },
          skills: [{
            key: 'sk_TEST_a', name: '区分有理数无理数', unlinked: false,
            home_section_label: '', definition: '无限不循环小数是无理数', total: 4,
            cells: {
              midterm: { choice_basic: [1, 2], choice_advanced: [3], fill: [], written: [] },
              final: { choice_basic: [], choice_advanced: [], fill: [], written: [4] },
            },
            positions: { midterm: { '1': [1], '2': [2] }, final: { '13': [4] } },
            typical: [{ tier: 'basic', question_id: 1, same_tier_count: 2, group_count: 1, role: '基础入口', reason: '本节第1常考技能' }],
          }],
        }, {
          id: 'sec_TEST_22', label: '2.2 平方根与立方根', synthesis: false, main_count: 2,
          coverage: {
            midterm: { groups: 1, of: 1, percent: 100, questions: 2 },
            final: { groups: 0, of: 1, percent: 0, questions: 0 },
          },
          overview: {
            midterm: { choice_basic: [9], choice_advanced: [8], fill: [], written: [] },
            final: { choice_basic: [], choice_advanced: [], fill: [], written: [] },
          },
          skills: [{
            key: 'sk_TEST_b', name: '求平方根', unlinked: false,
            home_section_label: '', definition: '', total: 2,
            cells: {
              midterm: { choice_basic: [9], choice_advanced: [8], fill: [], written: [] },
              final: { choice_basic: [], choice_advanced: [], fill: [], written: [] },
            },
            positions: { midterm: {}, final: {} },
            typical: [{ tier: 'basic', question_id: 9, same_tier_count: 3, group_count: 2, role: '基础入口', reason: '本节第1常考技能' }],
          }],
        }, {
          id: 'sec_TEST_23', label: '☆ 问题解决策略', synthesis: false, main_count: 0,
          coverage: {
            midterm: { groups: 0, of: 1, percent: 0, questions: 0 },
            final: { groups: 0, of: 1, percent: 0, questions: 0 },
          },
          overview: {
            midterm: { choice_basic: [], choice_advanced: [], fill: [], written: [] },
            final: { choice_basic: [], choice_advanced: [], fill: [], written: [] },
          },
          skills: [],
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
      nodes: [{ kind: 'skill', knowledge_key: 'sk_TEST_a', display_name: '区分有理数无理数', chapter_key: 'ch', section_key: 'sec', group_mastery: 0.5, evidence_student_count: 4, distribution: { weak: 2, unsteady: 1, stable: 1, insufficient: 0 }, students: [] }],
      exam_scope: { sessions: [] },
    } as unknown as Awaited<ReturnType<typeof trainingApi.overview>>)
    vi.spyOn(questionBankApi, 'chapterExamProfile').mockResolvedValue(examProfile())
    return { host, pinia, router, query }
  }

  it('renders the chapter exam profile as one scroll and loads count cells into the shared ledger', async () => {
    const { host, pinia, router, query } = mountExamProfile('?tab=exam&section=sec_TEST_21')
    await router.push(`/question-bank${query}`)
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    // 右栏列出当前小节典型题（保持技能顺序、档位顺序）。
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ questionIds: [1], includeSkills: true, tagStatus: 'all', collapseDuplicates: false }),
      expect.anything(),
    ))
    await vi.waitFor(() => expect(host.textContent).toContain('考法热力'))
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
    // 本班明显薄弱列：弱占比 2/4 = 50%，且主考 4 题 ≥3 → 常考且薄弱。
    await vi.waitFor(() => expect(block.textContent).toContain('50%（2/4 人）'))
    expect(block.textContent).toContain('常考且薄弱')
    // 合计档点击单元格 → 题单只含该格题目（期中 2 题 + 期末 0 题），出现「回到典型题」。
    const basicCell = [...block.querySelectorAll<HTMLButtonElement>('.cep-cell')].find(button => button.textContent?.trim() === '2')!
    basicCell.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(
      expect.objectContaining({ questionIds: [1, 2], sort: 'difficulty_asc' }),
      expect.anything(),
    ))
    expect(host.textContent).toContain('2.1 认识实数 · 区分有理数无理数 · 选择·基础 · 合计 2 题')
    expect(host.textContent).toContain('回到典型题')
    // 回到典型题恢复该节典型题题单。
    const back = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '回到典型题')!
    back.click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ questionIds: [1] }), expect.anything()))
    // 阶段切换驱动热力表计数：期末档基础格为空、解答格剩 1 题。
    const finalStage = [...host.querySelectorAll<HTMLButtonElement>('.app-segmented button')].find(button => button.textContent === '期末')!
    finalStage.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.stage).toBe('final'))
    expect(block.textContent).toContain('考法热力 · 期末')
    const finalCells = [...block.querySelectorAll<HTMLButtonElement>('.cep-cell')].map(button => button.textContent?.trim())
    expect(finalCells).toContain('1')
    expect(finalCells).not.toContain('2')
  })

  it('scrolls to a section block from the index chip and updates the query', async () => {
    MockIntersectionObserver.reset()
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    const bank = useQuestionBankStore(pinia)
    vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
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

  it('restores the saved chapter, section and stage when the query has none', async () => {
    localStorage.setItem(
      'chapter-exam:last:bnu24-math-g8-upper',
      JSON.stringify({ chapterId: 'ch_TEST_2', sectionId: 'sec_TEST_22', stage: 'final' }),
    )
    const { host, pinia, router } = mountExamProfile('?tab=exam')
    await router.push('/question-bank?tab=exam')
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('sec_TEST_22'))
    expect(router.currentRoute.value.query.chapter).toBe('ch_TEST_2')
    expect(router.currentRoute.value.query.stage).toBe('final')
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ questionIds: [9] }),
      expect.anything(),
    ))
    expect(host.querySelector('.cep-index-chip.is-active')?.textContent?.trim()).toBe('2.2')
  })

  it('falls back to the chapter overview when the saved position is stale', async () => {
    localStorage.setItem(
      'chapter-exam:last:bnu24-math-g8-upper',
      JSON.stringify({ chapterId: 'ch_GONE', sectionId: 'sec_GONE', stage: 'final' }),
    )
    const { host, pinia, router } = mountExamProfile('?tab=exam')
    await router.push('/question-bank?tab=exam')
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    const app = createApp({ render: () => h(ChapterExamProfile) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    // 失效的章节号不写入地址，落在第一章总览并加载全章典型题。
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ questionIds: [1, 9] }),
      expect.anything(),
    ))
    expect(router.currentRoute.value.query.chapter).toBeUndefined()
    expect(router.currentRoute.value.query.section).toBeUndefined()
    expect(host.querySelector('.cep-index-chip.is-active')?.textContent?.trim()).toBe('本章总览')
  })

  it('switches the ledger between blocks from the cached picks without a new list request', async () => {
    MockIntersectionObserver.reset()
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const { host, pinia, router } = mountExamProfile('?tab=exam&chapter=ch_TEST_2')
    await router.push('/question-bank?tab=exam&chapter=ch_TEST_2')
    const bank = useQuestionBankStore(pinia)
    const itemsById = new Map([[1, { ...item, id: 1 }], [9, { ...item, id: 9 }]])
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
    // 全章典型题一次性按本章题号拉取，右栏显示合并题单。
    await vi.waitFor(() => expect(
      list.mock.calls.some(call => JSON.stringify(call[0]?.questionIds) === '[1,9]'),
    ).toBe(true))
    await vi.waitFor(() => expect(bank.questions.map(row => row.id)).toEqual([1, 9]))
    expect(host.textContent).toContain('第二章 实数 · 全章典型题')
    const calls = list.mock.calls.length
    // 滚动定位到 2.2 块：台账换成该节缓存子集，不再请求列表接口。
    const io = MockIntersectionObserver.instances[MockIntersectionObserver.instances.length - 1]!
    io.fire(['sec_TEST_22'])
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('sec_TEST_22'), { timeout: 2000 })
    await vi.waitFor(() => expect(bank.questions.map(row => row.id)).toEqual([9]))
    expect(list.mock.calls.length).toBe(calls)
    expect(host.textContent).toContain('2.2 平方根与立方根 · 典型题 · 共 1 题')
    io.fire(['overview'])
    await vi.waitFor(() => expect(bank.questions.map(row => row.id)).toEqual([1, 9]))
    expect(list.mock.calls.length).toBe(calls)
  })

  it('browses questions across skills by tag dimension, scope and value', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank?tab=skill&section=kp_TEST_section')
    const scope = useCurriculumScopeStore(pinia)
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    scope.volumes = [{ id: scope.selectedVolumeId, label: '八年级上册', order: 1, grade: '八年级', semester: '上学期', textbook_version: '北师大版（2024）', source: {}, statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 }, chapters: [{ id: 'kp_TEST_chapter', knowledge_id: 'kp_TEST_chapter', label: '第一章', title: '第一章', display_name: '第一章', kind: 'chapter', number: '1', source_ref: { node_id: 'TEST', relative_url: '' }, exam_scope_values: [], order: 1, sections: [] }] }]
    const bank = useQuestionBankStore(pinia)
    const list = vi.spyOn(bank, 'loadQuestions').mockResolvedValue()
    const facetList = vi.spyOn(questionBankApi, 'listFacets').mockResolvedValue({ exam_scopes: [], curriculum_sections: [], knowledge_points: [{ value: '八年级上册｜第一章｜勾股定理', count: 1 }], curriculum_chapters: [], abilities: [], methods: [], models: [], thoughts: [], special_types: [{ value: '新定义题', count: 2 }], error_types: [], error_pattern_categories: [{ value: '方法与思路', count: 3 }], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] })
    const stats = { question_count: 1, type_counts: { '选择题': 0, '多选题': 0, '填空题': 0, '解答题': 1 }, difficulty: { min: 4, median: 4, max: 4 }, criteria_needs_review_count: 0 }
    const index = { graph_release_id: 'kgr_TEST', curriculum_volume_id: scope.selectedVolumeId, model_calls: 0, question_count: 1, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [{ id: 'kp_TEST_chapter', label: '第一章', question_count: 1, cross_section_skills: [], sections: [{ id: 'kp_TEST_section', label: '第一节', question_count: 1, skills: [{ ...stats, stable_key: 'sk_TEST_one', display_name: '判断直角三角形', full_name: '八年级上册/判断直角三角形', cross_section: false, definition: { observable_evidence: '判断', include_scope: '三边', exclude_scope: '作图' } }], topics: [{ ...stats, stable_key: 'kp_TEST_topic', display_name: '勾股定理', filter_value: '八年级上册/第一章/勾股定理' }] }] }] }
    const app = createApp({ render: () => h(QuestionSkillBrowser, { index, loading: false, error: '' }) })
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === label)!
    const tagRow = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('.qb-skill-row')].find(item => item.textContent?.includes(label))!
    await vi.waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ skillKeys: ['sk_TEST_one'] })))
    const callsBeforeTagMode = list.mock.calls.length
    button('按标签').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tagDim).toBe('typeKeys'))
    button('特殊考法').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tagDim).toBe('specialTypes'))
    await vi.waitFor(() => expect(tagRow('新定义题')).toBeTruthy())
    expect(list.mock.calls.length).toBe(callsBeforeTagMode)
    expect(host.textContent).toContain('请选择标签')
    expect(facetList).toHaveBeenLastCalledWith(expect.objectContaining({ skillKeys: [], curriculumVolumeIds: [scope.selectedVolumeId], curriculumSections: [] }), expect.anything())
    tagRow('新定义题').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ specialTypes: ['新定义题'], skillKeys: [], curriculumSections: [], curriculumVolumeIds: [scope.selectedVolumeId] })))
    expect(list.mock.lastCall?.[0]).not.toHaveProperty('scopeMode')
    expect(router.currentRoute.value.query.tag).toBe('新定义题')
    button('本章').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ curriculumSections: ['kp_TEST_chapter'] })))
    button('本小节').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ curriculumSections: ['kp_TEST_section'] })))
    expect(router.currentRoute.value.query.scope).toBe('section')
    button('知识点').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tagDim).toBe('knowledgePoints'))
    expect(router.currentRoute.value.query.tag).toBeUndefined()
    tagRow('勾股定理').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ knowledgePoints: ['八年级上册｜第一章｜勾股定理'], specialTypes: [], scopeMode: 'any', curriculumSections: [] })))
    expect(button('本章').disabled).toBe(true)
    expect(button('本小节').disabled).toBe(true)
    expect(button('整个学期').getAttribute('aria-pressed')).toBe('true')
    button('错因').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tagDim).toBe('errorPatternCategories'))
    tagRow('方法与思路').click()
    await vi.waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ errorPatternCategories: ['方法与思路'], specialTypes: [] })))
    button('按技能').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.tagDim).toBeUndefined())
    expect(router.currentRoute.value.query.tag).toBeUndefined()
    expect(router.currentRoute.value.query.scope).toBeUndefined()
  })


  it('shows actionable todo groups from existing read filters', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    const load = vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [item], total: 1, page: 1, page_size: 20, total_pages: 1 })
    const open = vi.fn()
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 2, onQuestion: open }) })
    app.use(pinia).mount(host); mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.qb-todo-group article')?.textContent).toContain('第1题匿名期末试卷'))
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ criteriaNeedsReview: true }), expect.anything())
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ missingType: true }), expect.anything())
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ missingKnowledge: true }), expect.anything())
    expect(load).not.toHaveBeenCalledWith(expect.objectContaining({ analysisStatus: 'incomplete' }), expect.anything())
    const action = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '去处理 →')!
    action.click(); expect(open).toHaveBeenCalledWith(item)
  })

  it('drives the analysis todo card from the same cached preview as the AI 补齐 dialog', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    const preview = { curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'all' as const,
      fingerprint: 'a'.repeat(64), scanned_count: 3, question_count: 3, repairable_count: 3, model_calls: 0,
      counts: { types: 0, knowledge_points: 0, tags: 1, evidence: 1, criteria: 1, skills: 2 }, items: [
        { id: 21, question_number: '3', paper_title: 'TEST-缺标签卷', missing: ['tags' as const, 'evidence' as const], blocked_reason: '', revision: 'b'.repeat(64) },
        { id: 22, question_number: '4', paper_title: 'TEST-缺判定点卷', missing: ['criteria' as const, 'skills' as const], blocked_reason: '', revision: 'c'.repeat(64) },
        { id: 23, question_number: '5', paper_title: 'TEST-只缺技能卷', missing: ['skills' as const], blocked_reason: '', revision: 'd'.repeat(64) },
      ] }
    vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue(preview)
    const openQuestion = vi.fn()
    const repair = vi.fn()
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 0, onOpenQuestion: openQuestion, onRepair: repair }) })
    app.use(pinia).mount(host); mounted.push(app)
    const analysisCard = () => [...host.querySelectorAll<HTMLElement>('.qb-todo-group')].find(el => el.querySelector('h2')?.textContent === '分析未完成')!
    await vi.waitFor(() => expect(analysisCard().querySelector('.qb-todo-count')?.textContent).toBe('2'))
    const articles = [...analysisCard().querySelectorAll('article')].map(el => el.textContent)
    expect(articles).toHaveLength(2)
    expect(articles[0]).toContain('第3题')
    expect(articles[0]).toContain('TEST-缺标签卷')
    expect(articles[0]).toContain('缺：题目标签、解题证据')
    expect(articles[1]).toContain('缺：判定点')
    expect(articles[1]).not.toContain('技能关联')
    const action = [...analysisCard().querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '去处理 →')!
    action.click(); expect(openQuestion).toHaveBeenCalledWith({ questionId: 21, paperId: null })
    const repairButton = [...analysisCard().querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'AI 补齐分析')!
    repairButton.click(); expect(repair).toHaveBeenCalledWith('analysis')
  })

  it('shows the analysis todo card empty state when no question misses analysis parts', async () => {
    const host = document.createElement('div'); document.body.append(host)
    const pinia = createPinia(); useCurriculumScopeStore(pinia).selectedVolumeId = 'bnu24-math-g8-upper'
    vi.spyOn(questionBankApi, 'listQuestions').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
    vi.spyOn(questionBankApi, 'repairPreview').mockResolvedValue({ curriculum_volume_id: 'bnu24-math-g8-upper', kind: 'all' as const,
      fingerprint: 'a'.repeat(64), scanned_count: 1, question_count: 1, repairable_count: 1, model_calls: 0,
      counts: { types: 0, knowledge_points: 0, tags: 0, evidence: 0, criteria: 0, skills: 1 }, items: [
        { id: 23, question_number: '5', paper_title: 'TEST-只缺技能卷', missing: ['skills' as const], blocked_reason: '', revision: 'd'.repeat(64) },
      ] })
    const app = createApp({ render: () => h(QuestionBankTodo, { index: null, pendingCount: 0 }) })
    app.use(pinia).mount(host); mounted.push(app)
    const analysisCard = () => [...host.querySelectorAll<HTMLElement>('.qb-todo-group')].find(el => el.querySelector('h2')?.textContent === '分析未完成')!
    await vi.waitFor(() => expect(analysisCard().querySelector('.qb-todo-count')?.textContent).toBe('0'))
    expect(analysisCard().textContent).toContain('当前学期没有这类待处理题目。')
    expect(analysisCard().querySelectorAll('article')).toHaveLength(0)
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
