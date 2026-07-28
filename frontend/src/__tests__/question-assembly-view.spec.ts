import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QuestionAssemblyView from '../views/QuestionAssemblyView.vue'
import { useAssemblyStore } from '../stores/assembly'

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
        url === '/api/question-bank/curriculum'
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

function bankQuestion(id: number, text: string) {
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

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
