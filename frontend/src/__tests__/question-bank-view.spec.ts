import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QuestionBankView from '../views/QuestionBankView.vue'

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
}
const paper = {
  id: 4,
  title: '匿名期末试卷',
  year: '2025',
  province: '广东省',
  city: '深圳市',
  district: null,
  exam_type: '期末',
  grade: '九年级',
  semester: '下学期',
  textbook_version: null,
  import_status: 'imported',
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
  question_count: 1,
  tagged_question_count: 0,
}

const mounted: Array<ReturnType<typeof createApp>> = []

async function mountView() {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionBankView)
  app.use(createPinia())
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => {
    expect(host.textContent).toContain('已知 x + y = 3')
  })
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('question bank workspace', () => {
  it('waits for applied filters and explicit AI cost confirmation', async () => {
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
    const host = await mountView()
    expect(fetchSpy).toHaveBeenCalledTimes(2)

    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!
    search.value = '二次函数'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(fetchSpy).toHaveBeenCalledTimes(2)

    const apply = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('应用筛选'))!
    apply.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(3))

    host.querySelector<HTMLInputElement>('input[aria-label="选择第 1 题"]')!.click()
    await nextTick()
    const aiButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('AI 标注'))!
    aiButton.click()
    await nextTick()
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('已选 1 道题'))
    expect(fetchSpy).toHaveBeenCalledTimes(3)

    confirmSpy.mockReturnValue(true)
    aiButton.click()
    aiButton.click()
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(4))
    expect(String(fetchSpy.mock.calls[3]?.[0])).toBe('/api/question-bank/tagging-jobs')
    expect(JSON.parse(String(fetchSpy.mock.calls[3]?.[1]?.body))).toEqual({
      question_ids: [17],
    })
  })
})
