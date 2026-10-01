import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

import QuestionInspector from '../QuestionInspector.vue'
import { questionBankApi, type QuestionBankDetail, type QuestionErrorPattern } from '../../../api/question-bank'
import { useQuestionBankStore } from '../../../stores/question-bank'

vi.mock('../TrainingCriterionReview.vue', () => ({
  default: { template: '<div />' },
}))

const apps: Array<{ unmount: () => void }> = []

afterEach(() => {
  apps.forEach((app) => app.unmount())
  apps.length = 0
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

function detail(patterns: QuestionBankDetail['error_patterns'], options: string[] = []): QuestionBankDetail {
  return {
    id: 1,
    revision: 'a'.repeat(64),
    paper_id: 1,
    question_number: '1',
    question_type: options.length ? '选择题' : '解答题',
    question_text: '测试题干',
    answer_text: '答案',
    paper_title: '测试试卷',
    difficulty: '3',
    typicality: null,
    reason: null,
    needs_review: false,
    criteria_needs_review: false,
    has_images: false,
    needs_image_review: false,
    created_at: '2026-09-27 10:00:00',
    updated_at: '2026-09-27 10:00:00',
    year: null,
    province: null,
    city: null,
    district: null,
    exam_type: null,
    grade: null,
    semester: null,
    textbook_version: null,
    tags: [],
    asset_urls: [],
    page_range: null,
    assets: [],
    previews: [],
    error_patterns: patterns,
    wrong_option_letters: options,
    rich_content: {
      available: false,
      question_block_count: 0,
      answer_block_count: 0,
      question_blocks: [],
      answer_blocks: [],
    },
  }
}

it('shows each wrong option, edits and rejects the shared question pattern, then handles other and empty questions', async () => {
  vi.spyOn(questionBankApi, 'getCurriculum').mockRejectedValue(new Error('not needed'))
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/authoring', component: { template: '<div />' } }],
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionInspector)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  apps.push(app)
  const bank = useQuestionBankStore(pinia)
  const items: QuestionErrorPattern[] = [
    {
      id: 11, category: '计算与化简', pattern: '误选A', explanation: 'A 项漏算',
      trigger_kind: 'option', trigger_value: 'A', status: 'candidate', source: 'ai_predicted',
      has_evidence: false,
    },
    {
      id: 12, category: '概念理解', pattern: '误选C', explanation: 'C 项混淆概念',
      trigger_kind: 'option', trigger_value: 'C', status: 'confirmed', source: 'ai_predicted',
      has_evidence: true,
    },
  ]
  bank.selectedQuestionId = 1
  bank.detail = detail(items, ['A', 'C', 'D'])
  bank.detailState = 'ready'
  await nextTick()

  const section = document.body.querySelector<HTMLElement>('.qb-patterns')!
  expect(section.textContent).toContain('C 项混淆概念')
  expect(section.textContent).toContain('已有实际作答记录')
  expect(section.textContent).toContain('尚无实际作答记录')
  expect(section.textContent).toContain('暂无该选项的错法说明')
  const cRow = [...section.querySelectorAll<HTMLElement>('.qb-patterns__option')]
    .find((row) => row.querySelector('.qb-patterns__letter')?.textContent?.trim() === 'C')!
  cRow.querySelector<HTMLButtonElement>('button')!.click()
  await nextTick()
  const changed = detail([
    items[0]!,
    { ...items[1]!, id: 13, pattern: '把概念混为一谈', category: '方法与思路', source: 'teacher_edit' },
  ], ['A', 'C', 'D'])
  const edit = vi.spyOn(questionBankApi, 'editErrorPattern').mockResolvedValueOnce(changed)
  section.querySelector<HTMLInputElement>('.qb-patterns__editor input')!.value = '把概念混为一谈'
  section.querySelector<HTMLInputElement>('.qb-patterns__editor input')!.dispatchEvent(new Event('input'))
  const category = section.querySelector<HTMLSelectElement>('.qb-patterns__editor select')!
  category.value = '方法与思路'
  category.dispatchEvent(new Event('change'))
  await nextTick()
  ;[...section.querySelectorAll<HTMLButtonElement>('.qb-patterns__editor button')]
    .find((button) => button.textContent?.includes('保存错法'))!.click()
  await vi.waitFor(() => expect(edit).toHaveBeenCalledWith(1, items[1], {
    action: 'edit', pattern: '把概念混为一谈', category: '方法与思路',
  }))
  await nextTick()
  expect(section.textContent).toContain('把概念混为一谈')
  expect(section.textContent).toContain('教师调整')

  vi.spyOn(window, 'confirm').mockReturnValue(true)
  edit.mockResolvedValueOnce(detail([changed.error_patterns![1]!], ['A', 'C', 'D']))
  const aRow = [...section.querySelectorAll<HTMLElement>('.qb-patterns__option')]
    .find((row) => row.querySelector('.qb-patterns__letter')?.textContent?.trim() === 'A')!
  ;[...aRow.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.includes('驳回'))!.click()
  await vi.waitFor(() => expect(edit).toHaveBeenLastCalledWith(1, items[0], { action: 'reject' }))

  bank.detail = detail([{
    ...items[0]!, id: 21, trigger_kind: 'step', trigger_value: 's1',
    pattern: '漏写关键一步', source: 'ai_predicted',
  }])
  await nextTick()
  expect(section.textContent).toContain('其他典型错法')
  expect(section.textContent).toContain('判定点：s1')
  bank.detail = detail([])
  await nextTick()
  expect(section.textContent).toContain('本题暂无典型错法')

  bank.detail = detail([items[0]!], ['A'])
  await nextTick()
  section.querySelector<HTMLButtonElement>('.qb-patterns__item .qb-link')!.click()
  await nextTick()
  expect(section.querySelector('.qb-patterns__editor')).not.toBeNull()
  bank.selectedQuestionId = 2
  bank.detail = { ...detail([{ ...items[0]!, id: 22, pattern: '新题的错法' }], ['A']), id: 2 }
  await nextTick()
  expect(section.textContent).toContain('新题的错法')
  expect(section.querySelector('.qb-patterns__editor')).toBeNull()
})

it('renders all linked skills with their derivation source', async () => {
  vi.spyOn(questionBankApi, 'getCurriculum').mockRejectedValue(new Error('not needed'))
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/authoring', component: { template: '<div />' } }],
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionInspector)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  apps.push(app)
  const bank = useQuestionBankStore(pinia)
  const items: QuestionErrorPattern[] = [
    {
      id: 31, category: '过程与依据', pattern: '判定点多技能错法',
      explanation: '', trigger_kind: 'step', trigger_value: 'ep-1',
      status: 'confirmed', source: 'ai_auto', has_evidence: true,
      skill_key: null, skill_label: null,
      skill_keys: ['sk_1', 'sk_2'], skill_labels: ['求平方根', '验根'],
      skill_source: 'criterion',
    },
    {
      id: 32, category: '过程与依据', pattern: '教师指定技能错法',
      explanation: '', trigger_kind: 'observation', trigger_value: '',
      status: 'confirmed', source: 'teacher_edit', has_evidence: false,
      skill_key: 'sk_1', skill_label: '求平方根',
      skill_keys: ['sk_1'], skill_labels: ['求平方根'],
      skill_source: 'teacher',
    },
    {
      id: 33, category: '概念理解', pattern: '本题唯一技能错法',
      explanation: '', trigger_kind: 'option', trigger_value: 'C',
      status: 'confirmed', source: 'ai_auto', has_evidence: false,
      skill_key: 'sk_3', skill_label: '识别图形',
      skill_keys: ['sk_3'], skill_labels: ['识别图形'],
      skill_source: 'question',
    },
  ]
  bank.selectedQuestionId = 1
  bank.detail = detail(items)
  bank.detailState = 'ready'
  await nextTick()

  const section = document.body.querySelector<HTMLElement>('.qb-patterns')!
  // 判定点触发的错法列出全部直达技能，标注由判定点得出。
  expect(section.textContent).toContain('关联技能：求平方根、验根（由判定点得出）')
  expect(section.textContent).toContain('关联技能：求平方根（教师设定）')
  expect(section.textContent).toContain('关联技能：识别图形（由本题唯一技能得出）')

  // 编辑面板保留教师改选入口；判定点来源给出提示。
  const stepRow = [...section.querySelectorAll<HTMLElement>('.qb-patterns__item')]
    .find((row) => row.textContent?.includes('判定点多技能错法'))!
  stepRow.querySelector<HTMLButtonElement>('.qb-link')!.click()
  await nextTick()
  expect(section.querySelector('.qb-patterns__editor select')).not.toBeNull()
})
