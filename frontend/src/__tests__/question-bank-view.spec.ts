import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { QuestionBankPaper } from '../api/question-bank'
import QuestionBankView from '../views/QuestionBankView.vue'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import QuestionInspector from '../components/question-bank/QuestionInspector.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
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
  grade: '九年级',
  semester: '下学期',
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
const paper: QuestionBankPaper = {
  id: 4,
  title: '匿名期末试卷',
  year: '2025',
  province: '广东省',
  city: '深圳市',
  district: null,
  exam_type: '期末',
  grade: '九年级',
  semester: '下学期',
  folder_name: null,
  textbook_version: null,
  import_status: 'imported',
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
  question_count: 1,
  tagged_question_count: 0,
  tagged_any_question_count: 0,
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

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('question bank workspace', () => {
  it('groups papers by semester, honors manual folders, and lets teachers collapse a group', async () => {
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

    expect(host.textContent).toContain('2025 · 下学期')
    expect(host.textContent).toContain('按学期自动归类')
    expect(host.textContent).toContain('中考专题')
    expect(host.textContent).toContain('自定义文件夹')

    const semesterHeader = [...host.querySelectorAll<HTMLButtonElement>('.paper-folder__header')]
      .find((button) => button.textContent?.includes('2025 · 下学期'))!
    expect(semesterHeader.getAttribute('aria-expanded')).toBe('true')
    semesterHeader.click()
    await nextTick()
    expect(semesterHeader.getAttribute('aria-expanded')).toBe('false')
    expect(host.textContent).not.toContain('同学期练习卷')
    expect(host.textContent).toContain('中考函数专题')
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
    const baselineCalls = fetchSpy.mock.calls.length
    const initialQuestionRequest = fetchSpy.mock.calls
      .map(([request]) => String(request))
      .find((url) => url.startsWith('/api/question-bank/questions?'))
    expect(initialQuestionRequest).toContain('sort=difficulty_desc')
    expect(host.querySelector<HTMLButtonElement>('.question-sort button.is-active')?.textContent)
      .toContain('难度')

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
    const aiButton = document.body.querySelector<HTMLButtonElement>('.qb-button.is-ai')!
    await vi.waitFor(() => expect(aiButton.disabled).toBe(false))
    aiButton.click()
    await nextTick()
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('已选 1 道题'))
    expect(fetchSpy).toHaveBeenCalledTimes(callsAfterDifficulty + 1)

    confirmSpy.mockReturnValue(true)
    aiButton.click()
    aiButton.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(callsAfterDifficulty + 2))
    expect(String(fetchSpy.mock.calls[callsAfterDifficulty + 1]?.[0])).toBe('/api/question-bank/tagging-jobs')
    expect(JSON.parse(String(fetchSpy.mock.calls[callsAfterDifficulty + 1]?.[1]?.body))).toEqual({
      question_ids: [17],
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

  it('tells the teacher to restore an identical paper from the trash', async () => {
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

    expect(host.textContent).toContain('相同试卷已在回收站')
    expect(host.textContent).toContain('恢复原试卷')
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

    const edit = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('编辑资料'))!
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

    const retag = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '重新打标签')!
    retag.click()

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
      force_retag: true,
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
    expect(host.textContent).toContain('已提交 1 道题')
  })

  it('can fill only incomplete tags without overwriting completed analysis', async () => {
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
        questionListUrl = url
        return response({ items: [item], total: 1, page: 1, page_size: 100, total_pages: 1 })
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
      .find((button) => button.textContent?.trim() === '只补缺失标签')!
    fill.click()

    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )).toBe(true))
    expect(questionListUrl).toContain('tag_status=untagged')
    const submitCall = fetchSpy.mock.calls.find(
      ([request, options]) => String(request) === '/api/question-bank/tagging-jobs'
        && options?.method === 'POST',
    )
    expect(JSON.parse(String(submitCall?.[1]?.body))).toEqual({
      question_ids: [17],
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
    expect(host.textContent).toContain('1 道缺少核心标签的题')
  })

  it('requires explicit confirmation before trashing a paper and restores it from the drawer', async () => {
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
    const trashedPaper = {
      ...paper,
      import_status: 'deleted',
      updated_at: '2026-07-29 11:00:00.000001',
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input)
        if (url.endsWith('/trash')) {
          return response({
            id: 4,
            deleted: true,
            import_status: 'deleted',
            updated_at: trashedPaper.updated_at,
            affected_question_count: 1,
          })
        }
        if (url === '/api/question-bank/papers?deleted=true') {
          return response({ items: [trashedPaper], total: 1 })
        }
        if (url.endsWith('/restore')) {
          return response({
            id: 4,
            deleted: false,
            import_status: 'completed',
            updated_at: '2026-07-29 11:01:00.000001',
            affected_question_count: 1,
          })
        }
        throw new Error(`unexpected request: ${url}`)
      })

    const trash = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('移入回收站'))!
    trash.click()
    await nextTick()
    expect(document.body.textContent).toContain('确认移入回收站')
    expect(document.body.textContent).toContain('此前单独删除的题不会被恢复')
    expect(fetchSpy).not.toHaveBeenCalled()

    const cancel = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('取消'))!
    cancel.click()
    await nextTick()
    expect(host.textContent).toContain('匿名期末试卷')
    expect(fetchSpy).not.toHaveBeenCalled()

    trash.click()
    await nextTick()
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认移入'))!
    confirm.click()
    await vi.waitFor(() => expect(store.papers).toHaveLength(0))
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe(
      '/api/question-bank/papers/4/trash',
    )

    const openTrash = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('回收站'))!
    openTrash.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('已删除试卷'))
    await vi.waitFor(() => expect(store.trashedPapers).toHaveLength(1))
    const restore = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('恢复到试卷库'))!
    restore.click()
    await vi.waitFor(() => expect(store.papers).toHaveLength(1))
    expect(fetchSpy.mock.calls.some(([request]) => (
      String(request) === '/api/question-bank/papers/4/restore'
    ))).toBe(true)
  })

  it('reuses the permanent-delete request token when the first response is lost', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    const app = createApp(PaperLibrary)
    app.use(pinia)
    const store = useQuestionBankStore(pinia)
    store.papers = []
    store.papersState = 'ready'
    app.mount(host)
    mounted.push(app)
    const trashedPaper = {
      ...paper,
      import_status: 'deleted',
      updated_at: '2026-07-29 12:00:00.000001',
    }
    let deleteAttempts = 0
    let deletionConfirmed = false
    const deleteBodies: Array<Record<string, unknown>> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-bank/papers?deleted=true') {
        return response({
          items: deletionConfirmed ? [] : [trashedPaper],
          total: deletionConfirmed ? 0 : 1,
        })
      }
      if (url.endsWith('/permanent-deletion-impact')) {
        return response({
          paper_count: 1,
          question_count: 2,
          tag_count: 3,
          training_link_count: 0,
          knowledge_graph_link_count: 0,
          owned_file_count: 1,
          shared_file_count: 0,
          permanent_delete_phrase: '彻底删除 1 份试卷',
        })
      }
      if (url.endsWith('/permanent-delete')) {
        deleteAttempts += 1
        deleteBodies.push(JSON.parse(String(init?.body)))
        if (deleteAttempts === 1) {
          return response({ error: { message: 'response lost' } }, 503)
        }
        deletionConfirmed = true
        return response({
          deleted_paper_ids: [trashedPaper.id],
          deleted_question_count: 2,
          deleted_tag_count: 3,
          removed_training_link_count: 0,
          removed_knowledge_graph_link_count: 0,
          deleted_file_count: 1,
          skipped_shared_file_count: 0,
          storage_cleanup_pending: false,
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const openTrash = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('回收站'))!
    openTrash.click()
    await vi.waitFor(() => expect(store.trashedPapers).toHaveLength(1))
    const select = document.body.querySelector<HTMLInputElement>(
      `input[aria-label="选择 ${trashedPaper.title}"]`,
    )!
    select.checked = true
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    const reviewDelete = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('彻底删除所选'))!
    reviewDelete.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('确认彻底删除？'))
    const confirmation = document.body.querySelector<HTMLInputElement>(
      '.paper-permanent-confirmation input',
    )!
    confirmation.value = '彻底删除 1 份试卷'
    confirmation.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认彻底删除'))!
    confirm.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('结果尚不确定'))
    confirm.click()
    await vi.waitFor(() => expect(deleteAttempts).toBe(2))
    await vi.waitFor(() => expect(document.body.textContent).not.toContain('确认彻底删除？'))

    expect(deleteBodies[0]?.request_token).toBe(deleteBodies[1]?.request_token)
  })
})

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
