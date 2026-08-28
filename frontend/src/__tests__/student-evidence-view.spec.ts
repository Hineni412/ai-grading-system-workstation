import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { saveEvidenceScope } from '../features/evidence-scope/session'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import StudentEvidenceView from '../views/StudentEvidenceView.vue'

const fetchStudentExamResultsMock = vi.hoisted(() => vi.fn())
const fetchGraphEvidenceMock = vi.hoisted(() => vi.fn())
const getQuestionMock = vi.hoisted(() => vi.fn())

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudentExamResults: fetchStudentExamResultsMock,
}))

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
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('student evidence view', () => {
  it('shows the student header, session cards and a back link to the source page', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    expect(host.querySelector('h1')?.textContent).toBe('匿名学生甲')
    expect(host.textContent).toContain('S012 · 七年级一班')
    expect(host.textContent).toContain('78 / 100')
    expect(host.textContent).toContain('证明步骤缺少依据')
    expect(host.textContent).toContain('共 21 场考试')
    const back = host.querySelector<HTMLAnchorElement>('.student-evidence__back')!
    expect(back.textContent).toContain('返回按学生训练')
    expect(back.getAttribute('href')).toContain('/training?mode=student')
    expect(fetchStudentExamResultsMock).toHaveBeenCalledWith(
      '12',
      { onlyDeducted: true, page: 1, pageSize: 10 },
      expect.any(AbortSignal),
    )
  })

  it('appends the next page when loading more', async () => {
    fetchStudentExamResultsMock
      .mockResolvedValueOnce(responseFor([sessionItem(7)], 1, 12, 2))
      .mockResolvedValueOnce(responseFor([sessionItem(8)], 2, 12, 2))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    const more = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('加载更多'))!
    more.click()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试8'))

    expect(fetchStudentExamResultsMock).toHaveBeenLastCalledWith(
      '12',
      { onlyDeducted: true, page: 2, pageSize: 10 },
      expect.any(AbortSignal),
    )
    expect(host.textContent).not.toContain('加载更多')
  })

  it('reloads from the first page when switching to all answers', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    ;[...host.querySelectorAll<HTMLButtonElement>('.student-evidence__modes button')]
      .find((button) => button.textContent?.trim() === '全部作答')!
      .click()
    await vi.waitFor(() => expect(fetchStudentExamResultsMock).toHaveBeenCalledTimes(2))
    expect(fetchStudentExamResultsMock).toHaveBeenLastCalledWith(
      '12',
      { onlyDeducted: false, page: 1, pageSize: 10 },
      expect.any(AbortSignal),
    )
  })

  it('shows a retryable error and an empty state', async () => {
    fetchStudentExamResultsMock.mockRejectedValueOnce(new Error('network down'))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('作答证据暂时无法读取'))

    fetchStudentExamResultsMock.mockResolvedValueOnce(responseFor([], 1, 0, 0))
    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '重新加载')!
      .click()
    await vi.waitFor(() => expect(host.textContent).toContain('该学生暂无考试作答记录'))
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
        scope: { mode: 'selected', student_ids: ['12'] },
        exam_scope: { mode: 'manual', session_ids: [7, 8] },
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

  it('falls back to the current session when no scope was saved', async () => {
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([graphEvidenceItem(7)], [7]))
    const { host } = await mountView('/training/evidence/12?knowledge=kp_triangle_congruence')
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    expect(fetchGraphEvidenceMock).toHaveBeenCalledWith(
      {
        scope: { mode: 'selected', student_ids: ['12'] },
        exam_scope: { mode: 'current', session_ids: [7] },
      },
      'kp_triangle_congruence',
      expect.any(AbortSignal),
      1,
    )
  })
})

describe('student evidence view group questions mode', () => {
  it('groups wrong answers by exam and question with expandable student rows', async () => {
    saveEvidenceScope({
      scope: { mode: 'class', class_ids: ['七年级一班'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    })
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([
      graphEvidenceItem(7),
      graphEvidenceItem(7, {
        student_id: 22,
        student_code: 'S022',
        student_name: '匿名学生乙',
        score_awarded: 5,
        deduction_reason: '对应边判断错误',
      }),
      graphEvidenceItem(8, { question_id: 'Q2', score_awarded: 3 }),
      graphEvidenceItem(8, { question_id: 'Q2', score_awarded: 10, deduction_reason: '' }),
    ], [7, 8]))

    const { host } = await mountView(
      `/training/evidence/group?mode=questions&knowledge=kp_triangle_congruence&klabel=${encodeURIComponent('三角形全等')}&from=student`,
    )
    await vi.waitFor(() => expect(host.textContent).toContain('2 人答错'))

    expect(fetchStudentExamResultsMock).not.toHaveBeenCalled()
    expect(fetchGraphEvidenceMock).toHaveBeenCalledWith(
      {
        scope: { mode: 'class', class_id: '七年级一班', class_ids: ['七年级一班'] },
        exam_scope: { mode: 'manual', session_ids: [7, 8] },
      },
      'kp_triangle_congruence',
      expect.any(AbortSignal),
      1,
    )
    expect(host.querySelector('h1')?.textContent).toBe('三角形全等')
    expect(host.textContent).toContain('群体 2 人答错')

    const sessions = [...host.querySelectorAll('.student-evidence__session')]
    expect(sessions).toHaveLength(2)
    expect(sessions[0]!.textContent).toContain('匿名考试8')
    expect(sessions[1]!.textContent).toContain('匿名考试7')

    const heading = [...sessions[1]!.querySelectorAll<HTMLButtonElement>('.student-evidence__question-heading')]
      .find((button) => button.textContent?.includes('2 人答错'))!
    expect(host.textContent).not.toContain('对应边判断错误')
    heading.click()
    await nextTick()
    expect(sessions[1]!.textContent).toContain('匿名学生甲')
    expect(sessions[1]!.textContent).toContain('匿名学生乙')
    expect(sessions[1]!.textContent).toContain('对应边判断错误')
    expect(sessions[1]!.textContent).toContain('4 / 10')

    const back = host.querySelector<HTMLAnchorElement>('.student-evidence__back')!
    expect(back.getAttribute('href')).toContain('/training?mode=student')
  })
})

describe('student evidence view original question panel', () => {
  it('opens the bank question preview from a knowledge-mode row and closes it', async () => {
    saveEvidenceScope({
      scope: { mode: 'selected', student_ids: ['12'] },
      exam_scope: { mode: 'manual', session_ids: [7] },
    })
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([graphEvidenceItem(7)], [7]))

    const { host } = await mountView(
      `/training/evidence/12?from=chapter&knowledge=kp_triangle_congruence&klabel=${encodeURIComponent('三角形全等')}`,
    )
    await vi.waitFor(() => expect(host.textContent).toContain('证明步骤缺少依据'))

    host.querySelector<HTMLButtonElement>('button[aria-label="查看 Q1 的题库原题"]')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('利用边角关系证明两个三角形全等'))

    expect(getQuestionMock).toHaveBeenCalledWith(101, expect.any(AbortSignal))
    expect(document.body.textContent).toContain('第 12 题')
    expect(document.body.textContent).toContain('匿名试卷')
    expect(document.body.textContent).toContain('三角形全等')

    document.querySelector<HTMLButtonElement>('button[aria-label="关闭原题预览"]')!.click()
    await nextTick()
    expect(document.querySelector('.question-panel')).toBeNull()
    // 页面证据内容不受影响。
    expect(host.textContent).toContain('证明步骤缺少依据')
  })

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

  it('offers the original question button in the plain session list when a bank link exists', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    host.querySelector<HTMLButtonElement>('button[aria-label="查看 Q1 的题库原题"]')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('利用边角关系证明两个三角形全等'))

    expect(getQuestionMock).toHaveBeenCalledWith(101, expect.any(AbortSignal))
    expect(document.body.textContent).toContain('第 12 题')
  })

  it('hides the original question button in the plain session list when the bank link is null', async () => {
    const item = { ...(sessionItem(7).items[0] as Record<string, unknown>), bank_question_id: null }
    fetchStudentExamResultsMock.mockResolvedValueOnce(responseFor([sessionItem(7, { items: [item] })]))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名考试7'))

    expect(host.querySelector('.student-evidence__original')).toBeNull()
  })

  it('groups panel tags by dimension with chinese labels and hides raw or english-coded values', async () => {
    saveEvidenceScope({
      scope: { mode: 'selected', student_ids: ['12'] },
      exam_scope: { mode: 'manual', session_ids: [7] },
    })
    fetchGraphEvidenceMock.mockResolvedValue(graphEvidenceResponse([graphEvidenceItem(7)], [7]))
    getQuestionMock.mockResolvedValueOnce({
      ...questionDetail,
      page_range: 'document',
      tags: [
        { tag_type: 'knowledge_point', tag_value: '初中数学｜三角形｜三角形全等', confidence: null },
        { tag_type: 'method', tag_value: '综合法', confidence: null },
        { tag_type: 'thought', tag_value: '转化思想', confidence: null },
        { tag_type: 'error_type', tag_value: 'logic_break', confidence: null },
        { tag_type: 'curriculum_section', tag_value: 'bnu24-math-g7-lower-c04-s03', confidence: null },
        { tag_type: 'canonical_knowledge_id', tag_value: 'kp_123', confidence: null },
      ],
    })

    const { host } = await mountView(
      `/training/evidence/12?from=chapter&knowledge=kp_triangle_congruence&klabel=${encodeURIComponent('三角形全等')}`,
    )
    await vi.waitFor(() => expect(host.textContent).toContain('证明步骤缺少依据'))

    host.querySelector<HTMLButtonElement>('button[aria-label="查看 Q1 的题库原题"]')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('利用边角关系证明两个三角形全等'))

    const groups = [...document.querySelectorAll<HTMLElement>('.question-panel__tag-group')]
    expect(groups.map((group) => group.querySelector('h4')?.textContent))
      .toEqual(['知识点', '解题方法', '数学思想'])
    expect(groups[0]!.dataset.tone).toBe('accent')
    expect(groups[1]!.dataset.tone).toBe('info')
    expect(groups[2]!.dataset.tone).toBe('success')
    expect(groups[0]!.querySelector('li')?.textContent).toBe('三角形全等')
    expect(groups[0]!.querySelector('li')?.getAttribute('title')).toBe('初中数学｜三角形｜三角形全等')

    const panelText = document.querySelector('.question-panel')!.textContent!
    expect(panelText).not.toContain('bnu24-math-g7-lower-c04-s03')
    expect(panelText).not.toContain('kp_123')
    expect(panelText).not.toContain('logic_break')
    expect(panelText).not.toContain('curriculum_section')
    // 页码取值 document 视为未记录，不直接展示英文原值。
    expect(panelText).toContain('未记录')
    expect(panelText).not.toContain('document')
  })
})
