import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'

import QuestionAnnotationPanel from '../QuestionAnnotationPanel.vue'
import ConfirmDialogHost from '../../design-system/ConfirmDialogHost.vue'
import { questionBankApi, type QuestionBankDetail, type QuestionErrorPattern, type QuestionSolutionEvidenceResponse } from '../../../api/question-bank'
import { questionBankCriteriaApi, type TrainingCriterionWorkspace } from '../../../api/question-bank-criteria'
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
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionAnnotationPanel)
  app.use(pinia)
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

  const confirmEl = document.createElement('div')
  document.body.append(confirmEl)
  const confirmApp = createApp(ConfirmDialogHost)
  confirmApp.mount(confirmEl)
  apps.push(confirmApp)
  edit.mockResolvedValueOnce(detail([changed.error_patterns![1]!], ['A', 'C', 'D']))
  const aRow = [...section.querySelectorAll<HTMLElement>('.qb-patterns__option')]
    .find((row) => row.querySelector('.qb-patterns__letter')?.textContent?.trim() === 'A')!
  ;[...aRow.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.includes('驳回'))!.click()
  await vi.waitFor(() => {
    expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).not.toBeNull()
  })
  ;[...document.body.querySelector('[data-testid="app-confirm-dialog"]')!.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.trim() === '驳回')!.click()
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
  ;[...section.querySelectorAll<HTMLButtonElement>('.qb-patterns__item button')]
    .find((button) => button.textContent === '调整')!.click()
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
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionAnnotationPanel)
  app.use(pinia)
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
  ;[...stepRow.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent === '调整')!.click()
  await nextTick()
  expect(section.querySelector('.qb-patterns__editor select')).not.toBeNull()
})

it('keeps knowledge and prerequisites visible and permits quality success without direct skill links', async () => {
  vi.spyOn(questionBankApi, 'getCurriculum').mockRejectedValue(new Error('not needed'))
  const evidence: QuestionSolutionEvidenceResponse = {
    question_id: 1, available: true, evidence_version_id: 'c'.repeat(64), status: 'approved',
    evidence: {
      schema_version: 'question-solution-evidence-v1', question_id: 1,
      source_content_hash: 'a'.repeat(64), content_hash: 'b'.repeat(64), version_id: 'c'.repeat(64),
      auxiliary_rules: [], rationale: '测试判定资料', confidence: 1,
      parts: [{
        part_id: 'part-1', label: '第一问', response_mode: 'process_required',
        canonical_answer: '2', accepted_forms: ['2'], full_answer: '测试答案',
        proof_obligations: [], visual_requirements: [], deduction_policy: [], allow_alternative_methods: true,
        evidence_points: [{
          evidence_point_id: 'point-knowledge', target: '写出平方根', observable_evidence: '写出平方根',
          fine_term_links: [{
            fine_term_id: 'knowledge-1', fine_term_name: '平方根', role: 'direct',
            core_resolution: { status: 'resolved', stable_keys: ['kp_TEST_sqrt'], reason: '' },
          }, {
            fine_term_id: 'prerequisite-1', fine_term_name: '乘方', role: 'supporting_prerequisite',
            core_resolution: { status: 'resolved', stable_keys: ['kp_TEST_power'], reason: '' },
          }], equivalent_rules: [], counterexamples: [],
        }, {
          evidence_point_id: 'point-skill', target: '检查答案', observable_evidence: '检查答案',
          fine_term_links: [{
            fine_term_id: 'skill-1', fine_term_name: '测试直接技能', role: 'direct',
            core_resolution: { status: 'resolved', stable_keys: ['sk_TEST_check'], reason: '' },
          }], equivalent_rules: [], counterexamples: [],
        }],
      }],
      whole_question_classification: {
        direct_fine_terms: [], supporting_prerequisite_fine_terms: [],
        direct_resolved_core_node_ids: ['kp_TEST_sqrt', 'sk_TEST_check'],
        supporting_resolved_core_node_ids: ['kp_TEST_power'],
        direct_ambiguous_core_node_ids: [], supporting_ambiguous_core_node_ids: [],
        direct_unmapped_fine_term_ids: [], supporting_unmapped_fine_term_ids: [],
        resolved_core_node_ids: ['kp_TEST_sqrt', 'sk_TEST_check', 'kp_TEST_power'],
        ambiguous_core_node_ids: [], unmapped_fine_term_ids: [],
      },
    },
  }
  const criterion: TrainingCriterionWorkspace = {
    question_id: 1, state: 'available', available: true, revision: 1,
    current_source_hash: 'a'.repeat(64), approved_version: null, versions: [],
    current_version: {
      version_id: 'c'.repeat(64), question_id: 1, version_number: 1, parent_version_id: null,
      source_content_hash: 'a'.repeat(64), schema_version: 'judgment-points-v1', status: 'approved',
      source_kind: 'teacher_manual', source_reference: '', criteria_hash: 'b'.repeat(64),
      quality_status: 'passed', quality_codes: [], created_by: 'TEST', decision_by: 'TEST',
      decision_note: '', decided_at: null, created_at: '2026-10-09', updated_at: '2026-10-09',
      criteria: {
        schema_version: 'judgment-points-v1', question_id: 1, source_content_hash: 'a'.repeat(64),
        question_type: '解答题', points: [], auxiliary_rules: [], rationale: '', confidence: 1,
        source_kind: 'confirmed_rubric_adapter',
      },
    },
  }
  vi.spyOn(questionBankApi, 'getSolutionEvidence').mockResolvedValue(evidence)
  const workspaceRead = vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(criterion)
  const pinia = createPinia()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(QuestionAnnotationPanel)
  app.use(pinia)
  app.mount(host)
  apps.push(app)
  const bank = useQuestionBankStore(pinia)
  bank.selectedQuestionId = 1
  bank.detail = {
    ...detail([]), labels: {
      primary_type: null, secondary_types: [], knowledge_points: [{ key: 'kp_TEST_sqrt', label: '平方根' }],
    },
  }
  bank.detailState = 'ready'
  await vi.waitFor(() => expect(host.querySelector('.qb-evidence-summary h3')?.textContent).toContain('全部质检通过'))
  const summary = host.querySelector<HTMLElement>('.qb-evidence-summary')
  expect(summary?.textContent).toContain('知识点 · 平方根')
  expect(summary?.textContent).toContain('前置知识 · 乘方')
  expect(summary?.textContent).not.toContain('测试直接技能')
  expect(summary?.textContent).not.toContain('未挂技能')
  expect(summary?.querySelector('.qb-skill-capsule')).toBeNull()

  bank.detail.criteria_needs_review = true
  await nextTick()
  expect(summary?.querySelector('h3')?.textContent).not.toContain('全部质检通过')
  bank.detail.criteria_needs_review = false
  await nextTick()
  expect(summary?.querySelector('h3')?.textContent).toContain('全部质检通过')

  const blockedStates: TrainingCriterionWorkspace[] = [
    { ...criterion, available: false },
    { ...criterion, current_version: criterion.current_version && { ...criterion.current_version, quality_status: 'failed' } },
  ]
  for (const blocked of blockedStates) {
    workspaceRead.mockResolvedValue(blocked)
    bank.selectedQuestionId = (bank.selectedQuestionId ?? 1) + 1
    await vi.waitFor(() => expect(summary?.querySelector('h3')?.textContent).toContain('2 个判定点'))
    expect(summary?.querySelector('h3')?.textContent).not.toContain('全部质检通过')
  }
})
