import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { QuestionBankFacets, QuestionBankListItem } from '../api/question-bank';
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
        url === '/api/question-bank/curriculum?include_knowledge_points=false'
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

  it('fetches the full answer blocks when expanding an answer from the compact list', async () => {
    const listItem = bankQuestion(17, '需要展开解析的题目')
    const detail = {
      ...listItem,
      page_range: null,
      assets: [],
      previews: [],
      rich_content: {
        available: true,
        question_block_count: 0,
        answer_block_count: 1,
        question_blocks: [],
        answer_blocks: [{
          kind: 'paragraph',
          text: '解析：完整富文本答案',
          segments: [],
          rows: [],
          html: '',
          asset_indexes: [],
          asset_urls: [],
        }],
      },
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/question-assembly/draft' && init?.method !== 'PUT') {
        return json(draft())
      }
      if (url === '/api/question-assembly/records?limit=100') {
        return json({ items: [], total: 0 })
      }
      if (url === '/api/question-bank/curriculum?include_knowledge_points=false') {
        return json(curriculumCatalog())
      }
      if (url.startsWith('/api/question-bank/facets?')) return json(questionFacets())
      if (url === '/api/question-bank/questions/17') return json(detail)
      if (url.startsWith('/api/question-bank/questions?')) {
        return json(questionPage(listItem))
      }
      throw new Error(`unexpected request: ${url}`)
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(createPinia())
    app.mount(host)
    mounted.push(app)

    await vi.waitFor(() => expect(host.textContent).toContain('需要展开解析的题目'))
    expect(host.textContent).not.toContain('解析：完整富文本答案')

    const toggle = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === '查看解析')
    expect(toggle).toBeTruthy()
    toggle!.click()

    await vi.waitFor(() => expect(host.textContent).toContain('解析：完整富文本答案'))
    expect(fetchSpy.mock.calls.some(
      ([input]) => String(input) === '/api/question-bank/questions/17',
    )).toBe(true)
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

function bankQuestion(id: number, text: string): QuestionBankListItem {
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
    criteria_needs_review: false,
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

function questionPage(item: ReturnType<typeof bankQuestion>, page = 1) {
  return {
    items: [item],
    total: 2,
    page,
    page_size: 12,
    total_pages: 2,
  }
}

function questionFacets(): QuestionBankFacets {
  return {
    exam_scopes: [],
    curriculum_sections: [],
    curriculum_chapters: [{ value: 'bnu24-math-g7-upper-c01', count: 2 }],
    knowledge_points: [{ value: '有理数', count: 2 }],
    abilities: [],
    methods: [],
    thoughts: [],
    models: [],
    special_types: [{ value: '动态几何题', count: 1 }],
    error_types: [],
    student_levels: [],
    teaching_stages: [],
    sub_skills: [],
    question_types: [{ value: '选择题', count: 2 }],
    years: [],
    exam_types: [],
    grades: [],
  }
}

function curriculumCatalog() {
  const volumes = [
    ['七年级', '上学期'],
    ['七年级', '下学期'],
    ['八年级', '上学期'],
    ['八年级', '下学期'],
    ['九年级', '上学期'],
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
    volumes: volumes.map(([grade, semester], index) => ({
      id: `volume-${index + 1}`,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: index === 0 ? 'bnu24-math-g7-upper-c01' : `chapter-${index + 1}`,
        knowledge_id: index === 0 ? 'bnu24-math-g7-upper-c01' : `chapter-${index + 1}`,
        order: 1,
        number: '第一章',
        title: index === 0 ? '有理数' : `测试章节${index + 1}`,
        label: index === 0 ? '第一章 有理数' : `第一章 测试章节${index + 1}`,
        kind: 'chapter',
        display_name: `${grade}${semester === '上学期' ? '上册' : '下册'}｜${index === 0 ? '第一章 有理数' : `第一章 测试章节${index + 1}`}`,
        source_ref: {
          node_id: `node-${index + 1}`,
          relative_url: `/czsx/zj${index + 1}`,
        },
        exam_scope_values: [
          index === 0 ? '七年级上册 第一章 有理数' : `${grade}${semester === '上学期' ? '上册' : '下册'} 测试范围${index + 1}`,
        ],
        sections: [],
      }],
    })),
  }
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}
