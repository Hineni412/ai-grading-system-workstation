import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeResultsCenterResponse, fetchResultsCenter, type ResultsCenterItem, type ResultsCenterResponse } from '../api/results-center'
import { fetchReviewItems, fetchReviewQuestions, fetchReviewRubric, type ResolvedReviewItem } from '../api/review'
import { useSessionStore } from '../stores/session'
import { useResultsCenterStore } from '../stores/results-center'
import ResultsCenterView from '../views/ResultsCenterView.vue'
import type { ClassAnalysisResponse } from '../api/class-analysis'
import { invalidateClassAnalysis } from '../components/results-center/class-analysis-cache'
import { walkthroughDataFor } from '../components/results-center/paper-walkthrough'
import PaperWalkthroughCard from '../components/results-center/PaperWalkthroughCard.vue'

vi.mock('../api/review', async (original) => ({
  ...await original<typeof import('../api/review')>(),
  fetchReviewItems: vi.fn(), fetchReviewQuestions: vi.fn(), fetchReviewRubric: vi.fn(),
}))

vi.mock('../api/results-center', async (original) => ({
  ...await original<typeof import('../api/results-center')>(),
  fetchResultsCenter: vi.fn(),
}))

const classAnalysisMock = vi.hoisted(() => ({
  getClassAnalysis: vi.fn(),
  updateSettings: vi.fn(),
  regenerate: vi.fn(),
  getQuestionPreview: vi.fn(),
  editCausePattern: vi.fn(),
}))

vi.mock('../api/class-analysis', async (original) => ({
  ...await original<typeof import('../api/class-analysis')>(),
  classAnalysisApi: classAnalysisMock,
}))

const exportsMock = vi.hoisted(() => ({
  getReportContext: vi.fn(async () => {
    throw new Error('合成环境不读导出登记簿')
  }),
  getAnalysisPreflight: vi.fn(),
  getAnalysisReviewNotes: vi.fn(
    async (): Promise<import('../api/exports').AnalysisReviewNotes> => ({
      session_id: 7,
      generated_at: null,
      items: [],
    }),
  ),
  submitReport: vi.fn(),
  downloadJobFile: vi.fn(),
  deleteReportFile: vi.fn(),
}))

vi.mock('../api/exports', async (original) => ({
  ...await original<typeof import('../api/exports')>(),
  exportsApi: exportsMock,
}))

function item(studentId: number, questionId: string, score: number | null, status: ResultsCenterItem['score_status'] = 'ai_ready'): ResultsCenterItem {
  return {
    review_item_id: `detail:${studentId}${questionId.slice(1)}`, question_id: questionId,
    score_awarded: score, max_score: 5, score_status: status,
    score_source: score === null ? 'none' : 'ai', confidence_score: 90,
    needs_review: status === 'ai_review', review_reason: null,
    result_id: studentId, detail_id: studentId * 10 + Number(questionId.slice(1)),
  }
}

function fixture(): ResultsCenterResponse {
  return {
    session_id: 7, session_name: '合成成绩验证',
    summary: {
      student_count: 4, complete_student_count: 3, average_sample_count: 3,
      average_score: 8, highest_score: 10, lowest_score: 6, max_score: 10,
      ungraded_item_count: 1, failed_item_count: 0, needs_review_item_count: 1,
      ai_ready_item_count: 6, teacher_final_item_count: 0,
    },
    questions: ['Q1', 'Q2'].map((questionId) => ({
      question_id: questionId, max_score: 5, total_count: 4,
      ungraded_count: questionId === 'Q2' ? 1 : 0, failed_count: 0,
      needs_review_count: questionId === 'Q1' ? 1 : 0,
      ai_ready_count: 3, teacher_final_count: 0, average_score: 3,
    })),
    students: [
      { student_id: 1, student_code: 'A01', student_name: '合成甲', class_name: '一班', pinyin_initials: 'hcj', pinyin_full: 'hechengjia', current_score: 8, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete', items: [item(1, 'Q1', 3), item(1, 'Q2', 5)] },
      { student_id: 2, student_code: 'A02', student_name: '合成乙', class_name: '一班', pinyin_initials: 'hcy', pinyin_full: 'hechengyi', current_score: 6, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 1, status: 'needs_review', items: [item(2, 'Q1', 2, 'ai_review'), item(2, 'Q2', 4)] },
      { student_id: 3, student_code: 'A03', student_name: '合成丙', class_name: '一班', pinyin_initials: 'hcb', pinyin_full: 'hechengbing', current_score: 2, max_score: 10, ungraded_count: 1, failed_count: 0, needs_review_count: 0, status: 'incomplete', items: [item(3, 'Q1', 2), item(3, 'Q2', null, 'ungraded')] },
      { student_id: 4, student_code: 'B01', student_name: '合成丁', class_name: '二班', pinyin_initials: 'hcd', pinyin_full: 'hechengding', current_score: 10, max_score: 10, ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete', items: [item(4, 'Q1', 5), item(4, 'Q2', 5)] },
    ],
  }
}

let app: App | null = null

function reviewItems(qid: string): ResolvedReviewItem[] {
  return fixture().students.flatMap((s) => s.items.filter((it) => it.question_id === qid)
    .map((it) => ({
      ...it, session_id: 7, student_id: s.student_id, revision: 0,
      student_name: s.student_name, student_code: s.student_code, class_name: s.class_name,
      teacher_locked: false, candidate_scores: [], metadata: {},
      deduction_reason: null, error_category: null, error_summary: null,
      media: {
        crop_url: `/api/test/${s.student_id}/${qid}/crop`,
        original_front_url: `/api/test/${s.student_id}/front`, original_back_url: null,
        annotated_front_url: null, annotated_back_url: null,
      },
    })))
}

async function mountView(tab: string | null = 'details', analysis?: ClassAnalysisResponse) {
  localStorage.clear()
  vi.mocked(fetchResultsCenter).mockReset().mockResolvedValue(fixture())
  classAnalysisMock.getClassAnalysis.mockReset().mockRejectedValue(new Error('合成环境不读班级分析'))
  invalidateClassAnalysis(7)
  vi.mocked(fetchReviewQuestions).mockReset().mockResolvedValue([])
  vi.mocked(fetchReviewItems).mockReset().mockImplementation(async (_sid, qid) => reviewItems(qid))
  vi.mocked(fetchReviewRubric).mockReset().mockResolvedValue(null)
  if (analysis) classAnalysisMock.getClassAnalysis.mockResolvedValue(analysis)
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().$patch({ selectedSessionId: 7, loadState: 'ready', sessions: [{ id: 7, name: '合成成绩验证', status: 'grading', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }] })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/results', component: ResultsCenterView },
    { path: '/grading', component: { render: () => h('div', '合成作答页面') } },
  ] })
  await router.push(tab === null ? '/results?session=7' : `/results?tab=${tab}&session=7`)
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  app = createApp({ render: () => h('main', { id: 'main-workspace' }, [h(RouterView)]) })
  app.use(pinia).use(router).mount(host)
  const ready = tab === null || tab === 'overview' || tab === 'exports'
    ? '[data-testid="results-overview"]'
    : '.results-matrix-wrap'
  await vi.waitFor(() => expect(host.querySelector(ready)).not.toBeNull())
  return { host, router }
}

function input(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}

function conclusionChip(host: HTMLElement, label: string): HTMLButtonElement {
  return [...host.querySelectorAll<HTMLButtonElement>('.results-conclusion__line button')]
    .find((button) => button.textContent?.includes(label))!
}

afterEach(() => {
  app?.unmount()
  app = null
  document.body.innerHTML = ''
})

describe('results center class filtering and return position', () => {

  it('opens cards before detail requests finish and reuses reads until results refresh', async () => {
    const { host } = await mountView('overview')
    const open = async () => {
      const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
        .find((el) => el.textContent === '看卷 10 分钟')!
      await vi.waitFor(() => expect(button.disabled).toBe(false))
      button.click()
      await nextTick()
    }
    let finish!: (items: ResolvedReviewItem[]) => void
    vi.mocked(fetchReviewItems).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    await open()
    const tabs = document.querySelector<HTMLElement>('.wt .page-tabs')
    expect(tabs).not.toBeNull()
    // 当前类别高亮 + 元信息含「第 1 类」；返回按钮带「返回考情总览」
    expect(tabs?.querySelector('[aria-current="page"]')?.textContent).toContain('典型错法')
    expect(document.querySelector('.wt .page-header')?.textContent).toContain('第 1 类')
    expect(document.querySelector('.wt .page-header .app-back-button')?.textContent).toContain('返回考情总览')
    expect(document.querySelector('.wtc__loading')?.textContent).toBe('答卷加载中…')
    expect(fetchReviewQuestions).not.toHaveBeenCalled()
    expect(fetchReviewItems).toHaveBeenCalledTimes(1)
    const qid = vi.mocked(fetchReviewItems).mock.calls[0]![1]
    finish(reviewItems(qid))
    await vi.waitFor(() => expect(document.querySelector('.wtc__img')).not.toBeNull())
    // s：组内下一名同学（卡 1 的「部分得分」组有 2 人）
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's' }))
    await vi.waitFor(() => expect(document.querySelector('.wtc__pos')?.textContent).toContain('第 2 / 2 人'))
    // d：下一张卡
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'd' }))
    await vi.waitFor(() => expect(document.querySelector('.wt .page-header')?.textContent).toContain('第 2 / 2 张'))
    // 没有计时器文案
    expect(document.querySelector('.wt')?.textContent ?? '').not.toMatch(/\d\d:\d\d/)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }))
    // 嵌入的作答面板自身也读评分依据（模块级缓存）：Q1+Q2 面板各 1 次 + 翻分时卡内 1 次
    await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledTimes(3))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    await open()
    await vi.waitFor(() => expect(document.querySelector('.wtc__img')).not.toBeNull())
    window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }))
    await nextTick()
    expect(fetchReviewItems).toHaveBeenCalledTimes(2) // Q1 + 上面 d 到卡 2 时读的 Q2
    expect(fetchReviewRubric).toHaveBeenCalledTimes(4) // 本次翻分读 Q1 卡内依据
    vi.mocked(fetchResultsCenter).mockResolvedValue(fixture())
    await useResultsCenterStore().load(7)
    await vi.waitFor(() => expect(fetchReviewItems).toHaveBeenCalledTimes(3))
    expect(document.querySelector('.wtc__score')).toBeNull()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }))
    await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledTimes(5))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    await open()
    await vi.waitFor(() => expect(document.querySelector('.wtc__img')).not.toBeNull())
    expect(fetchReviewItems).toHaveBeenCalledTimes(3)
  })

  it('keeps the selected student when an earlier slow detail read finishes later', async () => {
    const students = fixture().students
    const pending = new Map<number, (item: ResolvedReviewItem | null) => void>()
    const host = document.createElement('div')
    document.body.append(host)
    app = createApp({ render: () => h(PaperWalkthroughCard, {
      card: { id: 'test:race', category: 1, questionId: 'Q1', title: '合成卡题',
        groups: [{ label: '合成错法', members: [
          { studentId: 1, questionId: 'Q1', reason: '合成测试' },
          { studentId: 2, questionId: 'Q1', reason: '合成测试' },
        ] }] },
      students, sessionId: 7, revealed: false,
      loadItem: (sid: number) => new Promise<ResolvedReviewItem | null>((resolve) => { pending.set(sid, resolve) }),
      loadRubric: async () => null,
    }) })
    app.mount(host)
    host.querySelector<HTMLButtonElement>('[aria-label="下一名同学"]')!.click()
    await nextTick()
    pending.get(2)!(reviewItems('Q1')[1]!)
    await vi.waitFor(() => expect(host.querySelector<HTMLImageElement>('img')?.getAttribute('src'))
      .toBe('/api/test/2/Q1/crop'))
    pending.get(1)!(reviewItems('Q1')[0]!)
    await nextTick()
    await nextTick()
    expect(host.querySelector<HTMLImageElement>('img')?.getAttribute('src')).toBe('/api/test/2/Q1/crop')
  })

  it('retries failed cached reads, bounds the cache, and expires old detail reads', async () => {
    vi.mocked(fetchReviewItems).mockReset().mockResolvedValue(reviewItems('Q1'))
    const cache = walkthroughDataFor(fixture())
    vi.mocked(fetchReviewItems).mockRejectedValueOnce(new Error('temporary unavailable'))
    const first = cache.items('Q1')
    expect(cache.items('Q1')).toBe(first)
    await expect(first).rejects.toThrow('temporary unavailable')
    await expect(cache.items('Q1')).resolves.toHaveLength(4)
    await cache.items('Q1')
    expect(fetchReviewItems).toHaveBeenCalledTimes(2)
    const now = Date.now()
    const time = vi.spyOn(Date, 'now').mockReturnValue(now + 61_000)
    await cache.items('Q1')
    expect(fetchReviewItems).toHaveBeenCalledTimes(3)
    time.mockRestore()
    for (let q = 2; q <= 17; q += 1) await cache.items(`Q${q}`)
    await cache.items('Q1')
    expect(fetchReviewItems).toHaveBeenCalledTimes(20)
    const refreshed = walkthroughDataFor(fixture())
    await refreshed.items('Q1')
    expect(fetchReviewItems).toHaveBeenCalledTimes(21)
  })

  it('accepts the boolean solution flag while rejecting extra content fields', () => {
    const results = fixture()
    expect(decodeResultsCenterResponse(results)).toBe(results)
    results.students[0]!.items[0]!.alternative_solution_detected = true
    expect(decodeResultsCenterResponse(results)).toBe(results)
    const invalid = JSON.parse(JSON.stringify(results))
    invalid.students[0].items[0].alternative_solution_detected = 'true'
    expect(() => decodeResultsCenterResponse(invalid)).toThrow()
    invalid.students[0].items[0].alternative_solution_detected = true
    invalid.students[0].items[0].metadata = { evidence_steps: ['private'] }
    expect(() => decodeResultsCenterResponse(invalid)).toThrow()
  })

  it('loads overview statistics and narrative through one existing full response', async () => {
    const { host } = await mountView('overview', {
      status: 'ready', auto_generate: true, small_sample: true, data: null,
      cause_analysis: null, generated_at: null, stale: false, active_job_id: null,
      narrative_failed: false, class_names: ['一班', '二班'], selected_class: null,
      narrative: { key_findings: [{ title: '合成观察', detail: '同一响应的分析文字', severity: 'info' }],
        common_issues: [], student_notes: [], grouping_advice: '' },
    })
    await vi.waitFor(() => expect(host.textContent).toContain('合成观察'))
    expect(classAnalysisMock.getClassAnalysis).toHaveBeenCalledTimes(1)
    expect(classAnalysisMock.getClassAnalysis).toHaveBeenCalledWith(7, expect.any(AbortSignal), '', 'full')
  })

  it('uses the visible class, status and search scope without averaging incomplete scores as zero', async () => {
    const { host } = await mountView()
    input(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!, '一班')
    await nextTick()
    const line = () => host.querySelector('.results-conclusion__line')!
    expect(line().textContent?.replace(/\s+/g, '')).toContain('成绩完整2/3人')
    expect(line().textContent).toContain('平均 7')
    expect(line().textContent).toContain('最高 8')
    expect(line().textContent).toContain('最低 6')
    expect(host.querySelectorAll('.results-matrix tbody tr')).toHaveLength(3)
    expect(host.querySelectorAll('.results-matrix thead th')[3]!.textContent?.replace(/\s+/g, ' ')).toContain('4.5 / 5')
    conclusionChip(host, '未评分').click()
    await vi.waitFor(() => expect(line().textContent?.replace(/\s+/g, '')).toContain('成绩完整0/1人'))
    expect(line().textContent).toContain('平均 —')
    input(host.querySelector<HTMLInputElement>('input[type="search"]')!, '没有这个学生')
    await nextTick()
    expect(line().textContent?.replace(/\s+/g, '')).toContain('成绩完整0/0人')
    expect(host.textContent).toContain('没有符合当前筛选条件的学生')
  })

  it('restores filters, sorting and both scroll containers after viewing an answer', async () => {
    const { host, router } = await mountView()
    input(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!, '一班')
    input(host.querySelector<HTMLInputElement>('input[type="search"]')!, '合成')
    conclusionChip(host, '成绩完整').click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.filter).toBe('complete'))
    host.querySelector<HTMLButtonElement>('.results-matrix__total .results-matrix__sort')!.click()
    await nextTick()
    const main = host.querySelector('main')!
    const matrix = host.querySelector('.results-matrix-wrap')!
    main.scrollTop = 170
    matrix.scrollTop = 230
    matrix.scrollLeft = 390
    host.querySelector<HTMLButtonElement>('.results-matrix__score button')!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/grading'))
    expect(useResultsCenterStore().viewState).toMatchObject({ selectedClass: '一班', scrollTop: 170, matrixScrollTop: 230, matrixScrollLeft: 390 })
    router.back()
    await vi.waitFor(() => expect(host.querySelector('.results-matrix-wrap')?.scrollLeft).toBe(390))
    expect(host.querySelector('main')!.scrollTop).toBe(170)
    expect(host.querySelector('.results-matrix-wrap')!.scrollTop).toBe(230)
    expect(host.querySelector<HTMLSelectElement>('[aria-label="成绩明细班级"]')!.value).toBe('一班')
    expect(host.querySelector<HTMLInputElement>('input[type="search"]')!.value).toBe('合成')
    expect(router.currentRoute.value.query.filter).toBe('complete')
    expect(host.querySelector('th.results-matrix__total')!.getAttribute('aria-sort')).toBe('ascending')
    expect(host.querySelector('.results-matrix tbody tr')!.textContent).toContain('合成乙')
  })

  it('opens the export popover for the legacy tab=exports entry instead of a tab', async () => {
    const { host } = await mountView('exports')
    const rail = host.querySelector('.results-rail')!
    expect(rail.textContent).not.toContain('导出文件')
    await vi.waitFor(() => expect(
      document.body.querySelector('.results-export-popover'),
    ).not.toBeNull())
    expect(document.body.querySelector('.results-export-popover')!.textContent)
      .toContain('成绩表')
    // 手动关闭再打开由页面头部按钮驱动。
    host.querySelector<HTMLButtonElement>('.results-export-toggle')!.click()
    await vi.waitFor(() => expect(
      document.body.querySelector('.results-export-popover'),
    ).toBeNull())
    host.querySelector<HTMLButtonElement>('.results-export-toggle')!.click()
    await vi.waitFor(() => expect(
      document.body.querySelector('.results-export-popover'),
    ).not.toBeNull())
  })

  it('lists report review notes from the overview tile and routes 去核对 to the review item', async () => {
    exportsMock.getAnalysisReviewNotes.mockResolvedValueOnce({
      session_id: 7,
      generated_at: '2026-09-30T00:00:00Z',
      items: [
        {
          student_id: 2, student_code: 'A02', student_name: '合成乙',
          class_name: '一班', question_id: 'Q1', display_label: '第1题',
          note: '建议核对第 1 题给分', lock_revision: 0,
          status: 'pending', review_item_id: 'batch-1:2:Q1',
        },
        {
          student_id: 1, student_code: 'A01', student_name: '合成甲',
          class_name: '一班', question_id: 'Q2', display_label: '第2题',
          note: '已核对过的旧提示', lock_revision: 1,
          status: 'confirmed', review_item_id: 'batch-1:1:Q2',
        },
      ],
    })
    const { host, router } = await mountView('overview')
    await vi.waitFor(() => expect(host.textContent).toContain('1 条待核对'))
    const tile = [...host.querySelectorAll<HTMLElement>('.overview__tile')]
      .find((element) => element.textContent?.includes('报告提示'))!
    tile.querySelector<HTMLButtonElement>('button')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="report-review-notes"]'),
    ).not.toBeNull())
    const rows = host.querySelectorAll('.overview__notes-item')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.textContent).toContain('合成乙')
    expect(rows[0]!.textContent).toContain('第1题')
    expect(rows[1]!.textContent).toContain('已核对')
    expect(rows[1]!.classList.contains('is-confirmed')).toBe(true)
    const action = rows[0]!.querySelector<HTMLButtonElement>('button')!
    expect(action.textContent).toContain('去核对')
    action.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/grading'))
    expect(router.currentRoute.value.query).toMatchObject({
      session: '7',
      scope: 'all',
      question: 'Q1',
      item: 'batch-1:2:Q1',
      student: '2',
      entry: 'results',
    })
  })

  it('surfaces the server error message when the score load fails', async () => {
    const { ApiError } = await import('../api/errors')
    vi.mocked(fetchResultsCenter).mockReset().mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'rubric_unreadable',
      message: '评分细则文件 rubric.json 无法读取，请先恢复该文件。',
      details: {}, requestId: 'req-1', retryable: false,
    }))
    const host = document.createElement('div')
    document.body.append(host)
    const pinia = createPinia()
    setActivePinia(pinia)
    useSessionStore().$patch({ selectedSessionId: 7, loadState: 'ready', sessions: [{ id: 7, name: '合成成绩验证', status: 'grading', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }] })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/results', component: ResultsCenterView },
    ] })
    await router.push('/results?tab=details&session=7')
    await router.isReady()
    app = createApp({ render: () => h('main', { id: 'main-workspace' }, [h(RouterView)]) })
    app.use(pinia).use(router).mount(host)
    await vi.waitFor(() => expect(host.querySelector('.results-state-panel--error')).not.toBeNull())
    expect(host.textContent).toContain('评分细则文件 rubric.json 无法读取，请先恢复该文件。')
    expect(host.textContent).not.toContain('当前考试的成绩暂时无法读取')
  })
})
