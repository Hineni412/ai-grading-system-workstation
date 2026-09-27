import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import AuthoringPracticeView from '../views/AuthoringPracticeView.vue'

const authoringApiMock = vi.hoisted(() => ({
  listWorks: vi.fn(),
  listTaskCards: vi.fn(),
  createWork: vi.fn(),
  getWork: vi.fn(),
  saveVersion: vi.fn(),
  getVersion: vi.fn(),
  deleteWork: vi.fn(),
}))

const questionBankApiMock = vi.hoisted(() => ({
  getQuestion: vi.fn(),
}))

vi.mock('../api/authoring', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/authoring')>()
  return { ...original, authoringApi: authoringApiMock }
})

vi.mock('../api/question-bank', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/question-bank')>()
  return { ...original, questionBankApi: questionBankApiMock }
})

let routeQuery: Record<string, unknown> = {}

vi.mock('vue-router', async (importOriginal) => {
  const original = await importOriginal<typeof import('vue-router')>()
  return {
    ...original,
    useRoute: () => ({ query: routeQuery }),
    onBeforeRouteLeave: vi.fn(),
  }
})

const WORK_ID = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4'

function questionDetail() {
  return {
    id: 7,
    question_number: '3',
    question_text: '已知两直角边为3和4，求斜边',
    question_type: '解答题',
    answer_text: '5',
    difficulty: '3',
    paper_title: '单元卷',
    rich_content: {
      available: false,
      question_block_count: 0,
      answer_block_count: 0,
      question_blocks: [],
      answer_blocks: [],
    },
    tags: [
      { tag_type: 'knowledge_point', tag_value: '几何｜勾股定理', confidence: null },
    ],
  }
}

function snapshot() {
  return {
    question_id: 7,
    question: questionDetail(),
    judgment_points: {
      points: [{ target: '用勾股定理求斜边', observable_evidence: '算出5' }],
      auxiliary_rules: [],
      rationale: '考查勾股定理直接应用',
    },
    part_assessments: [],
    error_patterns: [{ category: '运算', pattern: '平方相加后忘记开方' }],
    captured_at: '2026-09-24 10:00:00',
  }
}

function workDetail(overrides: Record<string, unknown> = {}) {
  return {
    work_id: WORK_ID,
    kind: 'decompose',
    title: '',
    source_question_id: 7,
    source_question_number: '3',
    source_question_snippet: '已知两直角边为3和4',
    current_version: 0,
    created_at: '2026-09-24 10:00:00',
    updated_at: '2026-09-24 10:00:00',
    source_snapshot: snapshot(),
    task_card: null,
    versions: [],
    latest_content: null,
    ...overrides,
  }
}

const mounted: Array<ReturnType<typeof createApp>> = []

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AuthoringPracticeView)
  app.mount(host)
  mounted.push(app)
  await settle()
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  routeQuery = {}
  vi.clearAllMocks()
})

describe('authoring practice view', () => {
  it('shows the storage-upgrade message when authoring tables are missing', async () => {
    authoringApiMock.listWorks.mockRejectedValue(new ApiError({
      kind: 'server',
      status: 503,
      code: 'authoring_storage_unavailable',
      message: 'Authoring practice storage is not available yet',
      details: {},
      requestId: 'req-1',
      retryable: true,
    }))

    const host = await mountView()

    expect(host.textContent).toContain('命题练习的数据表还没有建立，需要先完成数据库升级')
    expect(host.textContent).not.toContain('保存为新版本')
  })

  it('renders the works list', async () => {
    authoringApiMock.listWorks.mockResolvedValue({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '拆解勾股定理',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边为3和4',
        current_version: 2,
        created_at: '2026-09-24 10:00:00',
        updated_at: '2026-09-24 11:00:00',
      }],
      total: 1,
    })

    const host = await mountView()

    expect(host.textContent).toContain('拆解勾股定理')
    expect(host.textContent).toContain('拆解练习')
    expect(host.textContent).toContain('已存 2 版')
  })

  it('shows the picker hint when no source question is given', async () => {
    authoringApiMock.listWorks.mockResolvedValue({ items: [], total: 0 })

    const host = await mountView()

    expect(host.textContent).toContain('在题库中打开一道题，点“用这道题练习”开始')
  })

  it('creates a decompose work from ?source= and opens the editor', async () => {
    routeQuery = { source: '7' }
    authoringApiMock.listWorks.mockResolvedValue({ items: [], total: 0 })
    authoringApiMock.listTaskCards.mockResolvedValue({
      items: [{ id: 'change_data', label: '换数据', method: 'change_data', description: '替换数值' }],
    })
    questionBankApiMock.getQuestion.mockResolvedValue(questionDetail())
    authoringApiMock.createWork.mockResolvedValue(workDetail())

    const host = await mountView()

    expect(questionBankApiMock.getQuestion).toHaveBeenCalledWith(7)
    expect(host.textContent).toContain('新建练习')
    expect(host.textContent).toContain('已知两直角边为3和4，求斜边')

    const createButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('创建练习'))
    createButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(authoringApiMock.createWork).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'decompose',
        source_question_id: 7,
        operation_token: expect.stringMatching(/^[0-9a-f]{32}$/),
      }),
    )
    expect(host.textContent).toContain('保存为新版本')
  })

  it('saves a version with the current base_version and token', async () => {
    const summary = (version: number, updatedAt: string) => ({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '已有练习',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边',
        current_version: version,
        created_at: '2026-09-24 10:00:00',
        updated_at: updatedAt,
      }],
      total: 1,
    })
    authoringApiMock.listWorks
      .mockResolvedValueOnce(summary(0, '2026-09-24 10:00:00'))
      .mockResolvedValue(summary(1, '2026-09-24 12:00:00'))
    authoringApiMock.getWork.mockResolvedValue(workDetail({ current_version: 0 }))
    authoringApiMock.saveVersion.mockResolvedValue({
      work_id: WORK_ID,
      version_no: 1,
      content: { intent: '练习拆解' },
      created_at: '2026-09-24 12:00:00',
      current_version: 1,
    })

    const host = await mountView()
    const workButton = host.querySelector('.authoring__work') as HTMLElement
    workButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(host.textContent).toContain('命题意图')
    const textarea = host.querySelector('textarea') as HTMLTextAreaElement
    textarea.value = '练习拆解'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()

    const saveButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('保存为新版本')) as HTMLButtonElement
    saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(authoringApiMock.saveVersion).toHaveBeenCalledWith(WORK_ID, expect.objectContaining({
      base_version: 0,
      content: expect.objectContaining({ intent: '练习拆解' }),
      operation_token: expect.stringMatching(/^[0-9a-f]{32}$/),
    }))
    // 保存成功后列表卡片同步到最新版本
    expect(authoringApiMock.listWorks).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain('已存 1 版')
  })

  it('reuses the save token for an identical retry but rotates it after edits', async () => {
    authoringApiMock.listWorks.mockResolvedValue({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '已有练习',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边',
        current_version: 0,
        created_at: '2026-09-24 10:00:00',
        updated_at: '2026-09-24 10:00:00',
      }],
      total: 1,
    })
    authoringApiMock.getWork.mockResolvedValue(workDetail({ current_version: 0 }))
    authoringApiMock.saveVersion.mockRejectedValue(new Error('网络异常'))

    const host = await mountView()
    const workButton = host.querySelector('.authoring__work') as HTMLElement
    workButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    const textarea = host.querySelector('textarea') as HTMLTextAreaElement
    textarea.value = '练习拆解'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()

    const saveButton = () => [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('保存为新版本')) as HTMLButtonElement

    saveButton().dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(host.textContent).toContain('网络异常')
    const firstToken = authoringApiMock.saveVersion.mock.calls[0]?.[1]?.operation_token

    // 内容没有变化的重试沿用同一个幂等令牌
    saveButton().dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const secondToken = authoringApiMock.saveVersion.mock.calls[1]?.[1]?.operation_token
    expect(secondToken).toBe(firstToken)

    // 修改内容后再保存是一次新的提交，需要换令牌
    textarea.value = '练习拆解（修改后）'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    saveButton().dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const thirdToken = authoringApiMock.saveVersion.mock.calls[2]?.[1]?.operation_token
    expect(thirdToken).not.toBe(firstToken)
    expect(thirdToken).toMatch(/^[0-9a-f]{32}$/)
  })

  it('shows the conflict message on authoring_version_conflict', async () => {
    authoringApiMock.listWorks.mockResolvedValue({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '已有练习',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边',
        current_version: 0,
        created_at: '2026-09-24 10:00:00',
        updated_at: '2026-09-24 10:00:00',
      }],
      total: 1,
    })
    authoringApiMock.getWork.mockResolvedValue(workDetail())
    authoringApiMock.saveVersion.mockRejectedValue(new ApiError({
      kind: 'conflict',
      status: 409,
      code: 'authoring_version_conflict',
      message: 'This work has newer saved versions; refresh before saving',
      details: { current_version: 2 },
      requestId: 'req-2',
      retryable: false,
    }))

    const host = await mountView()
    const workButton = host.querySelector('.authoring__work') as HTMLElement
    workButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    const textarea = host.querySelector('textarea') as HTMLTextAreaElement
    textarea.value = '新的拆解'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()

    const saveButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('保存为新版本')) as HTMLButtonElement
    saveButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(host.textContent).toContain('这份练习在其他地方已经保存了新版本')
    expect(host.textContent).toContain('重新加载最新内容')
  })

  it('compares two versions and marks changed fields', async () => {
    authoringApiMock.listWorks.mockResolvedValue({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '已有练习',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边',
        current_version: 2,
        created_at: '2026-09-24 10:00:00',
        updated_at: '2026-09-24 12:00:00',
      }],
      total: 1,
    })
    authoringApiMock.getWork.mockResolvedValue(workDetail({
      current_version: 2,
      versions: [
        { version_no: 1, created_at: '2026-09-24 11:00:00' },
        { version_no: 2, created_at: '2026-09-24 12:00:00' },
      ],
      latest_content: { intent: '第二版意图', knowledge_points: ['勾股定理'] },
    }))
    authoringApiMock.getVersion.mockImplementation(async (_id: string, versionNo: number) => ({
      work_id: WORK_ID,
      version_no: versionNo,
      content: versionNo === 1
        ? { intent: '第一版意图', knowledge_points: ['勾股定理'] }
        : { intent: '第二版意图', knowledge_points: ['勾股定理'] },
      created_at: '2026-09-24 11:00:00',
      current_version: 2,
    }))

    const host = await mountView()
    const workButton = host.querySelector('.authoring__work') as HTMLElement
    workButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(host.textContent).toContain('已保存版本')

    const selects = [...host.querySelectorAll('.authoring__compare-controls select')]
    const selectA = selects[0] as HTMLSelectElement
    const selectB = selects[1] as HTMLSelectElement
    selectA.value = '1'
    selectA.dispatchEvent(new Event('change', { bubbles: true }))
    selectB.value = '2'
    selectB.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    await settle()

    expect(authoringApiMock.getVersion).toHaveBeenCalledWith(WORK_ID, 1)
    expect(authoringApiMock.getVersion).toHaveBeenCalledWith(WORK_ID, 2)
    expect(host.textContent).toContain('第一版意图')
    expect(host.textContent).toContain('第二版意图')
    const changedRows = host.querySelectorAll('.authoring__compare-table tr.is-changed')
    expect(changedRows.length).toBe(1)
    expect(changedRows[0]?.textContent).toContain('命题意图')
  })

  it('shows the bank comparison panel for a saved decompose work', async () => {
    authoringApiMock.listWorks.mockResolvedValue({
      items: [{
        work_id: WORK_ID,
        kind: 'decompose',
        title: '已有练习',
        source_question_id: 7,
        source_question_number: '3',
        source_question_snippet: '已知两直角边',
        current_version: 1,
        created_at: '2026-09-24 10:00:00',
        updated_at: '2026-09-24 11:00:00',
      }],
      total: 1,
    })
    authoringApiMock.getWork.mockResolvedValue(workDetail({
      current_version: 1,
      versions: [{ version_no: 1, created_at: '2026-09-24 11:00:00' }],
      latest_content: {
        intent: '我的意图',
        knowledge_points: ['勾股定理'],
        key_steps: ['找直角边'],
        expected_errors: ['忘记开方'],
        predicted_difficulty: 4,
        predicted_solo: 'unistructural',
        parts: [],
      },
    }))

    const host = await mountView()
    const workButton = host.querySelector('.authoring__work') as HTMLElement
    workButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(host.textContent).toContain('与题库资料对照')
    expect(host.textContent).toContain('我的意图')
    expect(host.textContent).toContain('考查勾股定理直接应用')
    expect(host.textContent).toContain('平方相加后忘记开方')
  })
})
