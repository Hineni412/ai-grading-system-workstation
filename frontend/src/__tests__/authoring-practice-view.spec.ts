import { createApp, nextTick } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

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

})
