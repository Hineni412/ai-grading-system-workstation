import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { saveEvidenceScope } from '../features/evidence-scope/session'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import StudentEvidenceView from '../views/StudentEvidenceView.vue'
import { ApiError } from '../api/errors'
import { useJobStore } from '../stores/jobs'

const fetchStudentExamResultsMock = vi.hoisted(() => vi.fn())
const fetchGraphEvidenceMock = vi.hoisted(() => vi.fn())
const getQuestionMock = vi.hoisted(() => vi.fn())
const previewWrongBookMock = vi.hoisted(() => vi.fn())
const submitWrongBookMock = vi.hoisted(() => vi.fn())
const findWrongBookMock = vi.hoisted(() => vi.fn())
const downloadWrongBookMock = vi.hoisted(() => vi.fn())
const getJobMock = vi.hoisted(() => vi.fn())

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudentExamResults: fetchStudentExamResultsMock,
  previewWrongQuestionBook: previewWrongBookMock,
  submitWrongQuestionBooks: submitWrongBookMock,
  findWrongQuestionBookRequest: findWrongBookMock,
}))

vi.mock('../api/exports', () => ({ exportsApi: { downloadJobFile: downloadWrongBookMock } }))
vi.mock('../api/jobs', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/jobs')>()
  return { ...original, jobApi: { ...original.jobApi, getJob: getJobMock } }
})

vi.mock('../api/graph', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph')>(),
  fetchGraphEvidence: fetchGraphEvidenceMock,
}))

vi.mock('../api/question-bank', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/question-bank')>()
  return {
    ...original,
    questionBankApi: { ...original.questionBankApi, getQuestion: getQuestionMock },
  }
})

const questionDetail = {
  id: 101,
  question_number: '12',
  paper_title: '匿名试卷',
  question_type: '解答题',
  difficulty: '5',
  page_range: null,
  has_images: false,
  question_text: '利用边角关系证明两个三角形全等',
  answer_text: '证明略',
  tags: [{ tag_type: 'knowledge_point', tag_value: '初中数学｜三角形｜三角形全等', confidence: null }],
  previews: [],
  asset_urls: [],
  assets: [],
  rich_content: {
    available: false,
    question_block_count: 0,
    answer_block_count: 0,
    question_blocks: [],
    answer_blocks: [],
  },
}

const student = {
  id: 12,
  student_code: 'S012',
  name: '匿名学生甲',
  class_name: '七年级一班',
  created_at: null,
}

function sessionItem(sessionId: number, overrides: Record<string, unknown> = {}) {
  return {
    session_id: sessionId,
    session_name: `匿名考试${sessionId}`,
    graded_at: '2026-07-18T09:00:00Z',
    exam_created_at: '2026-07-15T09:00:00Z',
    result_id: 31,
    student_score: 78,
    total_score: 100,
    items: [{
      detail_id: 501,
      question_id: 'Q1',
      bank_question_id: 101,
      score_awarded: 6,
      max_score: 10,
      deduction_amount: 4,
      deduction_reason: '证明步骤缺少依据',
      error_category: '逻辑断裂',
      error_summary: null,
      evidence_url: `/api/sessions/${sessionId}/results/31/details/501/crop`,
    }],
    ...overrides,
  }
}

function responseFor(sessions: unknown[], page = 1, totalSessions = 21, totalPages = 3) {
  return {
    student,
    sessions,
    total_sessions: totalSessions,
    page,
    page_size: 10,
    total_pages: totalPages,
  }
}

function graphEvidenceItem(sessionId: number, overrides: Record<string, unknown> = {}) {
  return {
    student_id: 12,
    student_code: 'S012',
    student_name: '匿名学生甲',
    class_id: '七年级一班',
    knowledge_key: 'kp_triangle_congruence',
    stable_key: 'kp_triangle_congruence',
    knowledge_label: '三角形全等',
    session_id: sessionId,
    session_name: `匿名考试${sessionId}`,
    question_id: 'Q1',
    bank_question_id: 101,
    score_awarded: 4,
    full_score: 10,
    score_rate: 0.4,
    detail_id: 501,
    deduction_reason: '证明步骤缺少依据',
    evidence_url: `/api/sessions/${sessionId}/results/31/details/501/crop`,
    tag_context: {},
    actionable_reasons: [],
    error_counts: {},
    ...overrides,
  }
}

function graphEvidenceResponse(items: unknown[], sessionIds: number[]) {
  return {
    response_schema_version: 'knowledge-graph-evidence-current',
    response_version: 'e'.repeat(64),
    scope: { mode: 'selected', student_ids: ['12'], class_id: null },
    exam_scope: {
      mode: 'manual',
      session_ids: sessionIds,
      sessions: sessionIds.map((id) => ({ session_id: id, session_name: `匿名考试${id}` })),
    },
    coverage: { covered_items: 1, total_items: 1, missing_items: {} },
    current_standard: { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 2 },
    stable_key: 'kp_triangle_congruence',
    display_name: '三角形全等',
    items,
    total: items.length,
    page: 1,
    page_size: 20,
    total_pages: 1,
  }
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(path = '/training/evidence/12?from=student') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useCurriculumScopeStore(pinia).$patch({ loadState: 'ready', selectedVolumeId: 'bnu24-math-g8-upper' })
  useSessionStore(pinia).$patch({
    sessions: [
      {
        id: 7,
        name: '匿名考试7',
        status: 'completed',
        is_deleted: false,
        deleted_at: null,
        created_at: '2026-07-10T09:00:00Z',
        updated_at: null,
      },
      {
        id: 8,
        name: '匿名考试8',
        status: 'completed',
        is_deleted: false,
        deleted_at: null,
        created_at: '2026-07-18T09:00:00Z',
        updated_at: null,
      },
    ],
    selectedSessionId: 7,
    loadState: 'ready',
  })
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(StudentEvidenceView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  localStorage.clear()
  fetchStudentExamResultsMock.mockResolvedValue(responseFor([sessionItem(7)]))
  getQuestionMock.mockResolvedValue(questionDetail)
  previewWrongBookMock.mockImplementation(async (_id, body) => ({
    students: body.include_class ? [student, { ...student, id: 13, name: '全对学生' }] : [student],
    sessions: [7, 8].map(id => ({ session_id: id, session_name: `匿名考试${id}`, exam_created_at: null })),
    session_ids: body.session_ids ?? [7, 8],
    semester_label: '八年级上学期', question_count: 3,
    missing_items: [{ student_id: 12, student_name: student.name, session_id: 7, session_name: '匿名考试7', question_id: 'Q9' }],
  }))
  downloadWrongBookMock.mockResolvedValue({ blob: new Blob(['test Word']), filename: '测试错题本.zip' })
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:wrong-book') })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

function wrongBookJob(status: 'running' | 'succeeded' = 'succeeded') {
  return {
    id: 71, job_type: 'wrong_question_export', payload: {},
    result: status === 'succeeded' ? {
      question_count: 3, download_url: '/api/jobs/71/download', filename: '测试错题本.zip',
      generated_students: [{ student_id: 12, student_name: student.name }],
      empty_students: [{ student_id: 13, student_name: '全对学生', reason: '没有错题' }],
      missing_items: [{ student_id: 12, student_name: student.name, session_id: 7, session_name: '匿名考试7', question_id: 'Q9' }],
      failed_students: [],
    } : {},
    status, progress: status === 'running' ? 0.5 : 1, stage: 'wrong_question_export',
    detail: '已处理 1/2 名学生', error: null, cancel_requested: false,
    created_at: '2026-09-30T10:00:00Z', started_at: '2026-09-30T10:00:00Z',
    updated_at: status === 'running' ? '2026-09-30T10:00:01Z' : '2026-09-30T10:00:02Z',
    finished_at: status === 'succeeded' ? '2026-09-30T10:00:02Z' : null,
  }
}

describe('student evidence wrong question book', () => {
  it('selects the class and exams, shows progress and omitted students, and downloads', async () => {
    submitWrongBookMock.mockResolvedValue(wrongBookJob('running'))
    getJobMock.mockResolvedValue(wrongBookJob())
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名学生甲'))
    const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '导出错题本')!
    button.click()
    await vi.waitFor(() => expect(host.textContent).toContain('可导出 3 道错题'))
    expect([...host.querySelectorAll<HTMLInputElement>('.wrong-book-dialog input[type="checkbox"]')].every(item => item.checked)).toBe(true)
    const select = host.querySelector<HTMLSelectElement>('#wrong-book-students')!
    select.selectedIndex = 1
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(host.textContent).toContain('预计 2 名学生'))
    const checkbox = host.querySelector<HTMLInputElement>('.wrong-book-dialog input[type="checkbox"]')!
    checkbox.click()
    await vi.waitFor(() => expect(previewWrongBookMock).toHaveBeenLastCalledWith(12, {
      curriculum_volume_id: 'bnu24-math-g8-upper', include_class: true, session_ids: [8],
    }, expect.any(AbortSignal)))
    await vi.waitFor(() => expect(host.querySelector<HTMLFieldSetElement>('fieldset')?.disabled).toBe(false))
    ;[...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '导出')!.click()
    await vi.waitFor(() => expect(host.querySelector('progress')).not.toBeNull())
    expect(submitWrongBookMock).toHaveBeenCalledWith({
      curriculum_volume_id: 'bnu24-math-g8-upper', student_ids: [12, 13], session_ids: [8], client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    })
    await useJobStore().refresh(71)
    await vi.waitFor(() => expect(host.textContent).toContain('全对学生（没有错题）'))
    expect(host.textContent).toContain('匿名考试7 · Q9')
    const anchor = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    ;[...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '下载全部错题本')!.click()
    await vi.waitFor(() => expect(downloadWrongBookMock).toHaveBeenCalledWith(71))
    await vi.waitFor(() => expect(host.textContent).toContain('本机临时导出文件已清除'))
    anchor.mockRestore()
  })

  it('queries an ambiguous submission and restores its token when reopened without resubmitting', async () => {
    submitWrongBookMock.mockRejectedValue(new ApiError({ kind: 'timeout', status: null, code: 'request_timeout', message: '超时', details: {}, requestId: 'test', retryable: false }))
    findWrongBookMock.mockRejectedValue(new Error('not found yet'))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名学生甲'))
    const open = () => [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '导出错题本')!.click()
    open()
    await vi.waitFor(() => expect(host.textContent).toContain('可导出 3 道错题'))
    ;[...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === '导出')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('不会自动重复导出'))
    const token = submitWrongBookMock.mock.calls[0]![0].client_request_token
    host.querySelector<HTMLButtonElement>('.wrong-book-dialog__heading button')!.click()
    await settle()
    findWrongBookMock.mockResolvedValue(wrongBookJob())
    open()
    await vi.waitFor(() => expect(host.textContent).toContain('已完成'))
    expect(findWrongBookMock).toHaveBeenLastCalledWith(token)
    expect(submitWrongBookMock).toHaveBeenCalledTimes(1)
  })
})

describe('student evidence view knowledge mode', () => {
  it('groups wrong answers by exam, newest first, across multiple manual sessions', async () => {
    saveEvidenceScope({
      scope: { mode: 'selected', student_ids: ['12'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    })
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([
      graphEvidenceItem(7),
      graphEvidenceItem(8, { question_id: 'Q3', score_awarded: 2 }),
      graphEvidenceItem(8, { question_id: 'Q4', score_awarded: 10, deduction_reason: '' }),
    ], [7, 8]))

    const { host } = await mountView(
      `/training/evidence/12?from=chapter&knowledge=kp_triangle_congruence&klabel=${encodeURIComponent('三角形全等')}`,
    )
    await vi.waitFor(() => expect(host.textContent).toContain('证明步骤缺少依据'))

    expect(fetchStudentExamResultsMock).not.toHaveBeenCalled()
    expect(fetchGraphEvidenceMock).toHaveBeenCalledWith(
      {
        scope: { mode: 'selected', student_ids: ['12'], use_historical_fallback: false },
        exam_scope: { mode: 'semester', curriculum_volume_id: 'bnu24-math-g8-upper' },
      },
      'kp_triangle_congruence',
      expect.any(AbortSignal),
      1,
    )
    expect(host.querySelector('h1')?.textContent).toBe('匿名学生甲')
    expect(host.textContent).toContain('三角形全等 · 只看答错记录')
    const cards = [...host.querySelectorAll('.student-evidence__session')]
    expect(cards).toHaveLength(2)
    // 较新的考试（created_at 更晚的匿名考试8）排在前面；满分作答被过滤。
    expect(cards[0]!.textContent).toContain('匿名考试8')
    expect(cards[0]!.textContent).toContain('Q3')
    expect(cards[0]!.textContent).not.toContain('Q4')
    expect(cards[1]!.textContent).toContain('匿名考试7')
    expect(host.textContent).not.toContain('全部作答')
  })

})

describe('student evidence view original question panel', () => {

  it('offers the original question button on group-mode question headings and survives load failures', async () => {
    saveEvidenceScope({
      scope: { mode: 'class', class_ids: ['七年级一班'] },
      exam_scope: { mode: 'manual', session_ids: [7] },
    })
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([graphEvidenceItem(7)], [7]))
    getQuestionMock.mockRejectedValueOnce(new Error('network down'))

    const { host } = await mountView(
      `/training/evidence/group?mode=questions&knowledge=kp_triangle_congruence&klabel=${encodeURIComponent('三角形全等')}&from=student`,
    )
    await vi.waitFor(() => expect(host.textContent).toContain('1 人答错'))

    host.querySelector<HTMLButtonElement>('button[aria-label="查看 Q1 的题库原题"]')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('题库原题暂时无法读取'))
    // 加载失败只影响面板，不崩页面。
    expect(host.textContent).toContain('1 人答错')

    ;[...document.querySelectorAll<HTMLButtonElement>('.question-panel button')]
      .find((button) => button.textContent?.trim() === '重试')!
      .click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('利用边角关系证明两个三角形全等'))

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(document.querySelector('.question-panel')).toBeNull()
  })

})
