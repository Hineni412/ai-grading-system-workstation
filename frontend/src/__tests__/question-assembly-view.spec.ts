import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import { createMemoryHistory } from 'vue-router'
import { createAppRouter } from '../router'
import { useAssemblyStore } from '../stores/assembly';

import QuestionAssemblyView from '../views/QuestionAssemblyView.vue'

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

  it('opens the basket by default and submits an export job', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft([17], revisionB))
      }
      if (url === '/api/question-assembly/draft' && init?.method === 'PUT') {
        return json(draft([17], revisionB))
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
            source: null,
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
    expect(host.querySelector('.page-tabs .is-active')?.textContent).toBe('试卷篮与导出')
    await vi.waitFor(() => expect(host.textContent).toContain('paper.md'))
    expect(host.textContent).toContain('A')

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

  it.each(['', '?mode=browse', '?mode=edit', '?mode=ai', '?mode=assistant'])('keeps old route %s compatible with the two workspaces', async query => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => String(input).includes('/records?') ? json({ items: [], total: 0 }) : String(input).endsWith('/draft') ? json(draft()) : json({}, 400))
    const router = createAppRouter(createMemoryHistory())
    await router.push(`/question-assembly${query}`)
    const host = document.createElement('div'); document.body.append(host)
    const app = createApp(QuestionAssemblyView); app.use(createPinia()).use(router).mount(host); mounted.push(app)
    expect(host.querySelectorAll('.page-tabs button')).toHaveLength(2)
    expect(host.querySelector('.page-tabs .is-active')?.textContent).toBe(query.includes('ai') || query.includes('assistant') ? '学情组卷助手' : '试卷篮与导出')
    const bank = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '去题库选题')!
    bank.click()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/question-bank?tab=skill'))
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

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
