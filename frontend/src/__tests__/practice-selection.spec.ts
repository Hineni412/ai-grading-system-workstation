import { createApp, defineComponent, h, nextTick, ref, shallowRef, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CurriculumVolume } from '../api/question-bank'
import { decodeTrainingDisplayDiagnosis, type TrainingDiagnosis, type TrainingReadDiagnosis, type TrainingWeakPoint } from '../api/training'
import StudentPicker from '../components/knowledge-training/StudentPicker.vue'
import StudentQuickView from '../components/knowledge-training/StudentQuickView.vue'
import KnowledgeRangeList from '../components/knowledge-training/KnowledgeRangeList.vue'
import TrainingScopeBar from '../components/knowledge-training/TrainingScopeBar.vue'
import WrongQuestionBookExport from '../components/knowledge-training/WrongQuestionBookExport.vue'
import { ApiError } from '../api/errors'
import { DEFAULT_HANDOUT_RULES, DEFAULT_TRAINING_RULES, loadPaperSelectionSession, presetFocusedTraining, resolvePaperScope } from '../features/training/paper-selection-session'

const apps: App[] = []
const wrongApi = vi.hoisted(() => ({ preview: vi.fn(), submit: vi.fn(), find: vi.fn(), download: vi.fn() }))
vi.mock('../api/students', async original => ({ ...await original<typeof import('../api/students')>(),
  previewWrongQuestionBooks: wrongApi.preview, submitWrongQuestionBooks: wrongApi.submit, findWrongQuestionBookRequest: wrongApi.find }))
vi.mock('../api/exports', () => ({ exportsApi: { downloadJobFile: wrongApi.download } }))
const volume: CurriculumVolume = {
  id: 'TEST-volume', order: 1, label: '合成册', grade: '八年级', semester: '上学期', textbook_version: '北师大版', source: {},
  statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
  chapters: [1, 2].map(index => ({ id: `TEST-c${index}`, knowledge_id: `kp_c${index}`, order: index,
    number: String(index), title: `合成章${index}`, label: `第${index}章`, kind: 'chapter', display_name: `第${index}章`,
    source_ref: { node_id: '', relative_url: '' }, exam_scope_values: [],
    sections: [1, 2].map(section => ({ id: `TEST-c${index}-s${section}`, knowledge_id: `kp_c${index}_s${section}`,
      order: section, number: String(section), title: `节${index}.${section}`, label: `节${index}.${section}`, kind: 'lesson',
      display_name: `节${index}.${section}`, source_ref: { node_id: '', relative_url: '' }, knowledge_points: [], exam_scope_values: [] })),
  })),
}
function point(key: string, tier: TrainingWeakPoint['tier'], observed = 1): TrainingWeakPoint {
  return { knowledge_key: key, knowledge_point: key, mastery: .4, tier, observation_count: observed,
    score_sum: 0, full_score_sum: 0, deduction_count: 0, evidence_count: observed, exam_count: 1,
    source_question_refs: [], actionable_reasons: [], tag_context: {}, error_counts: {} }
}
const diagnosis: TrainingDiagnosis = {
  scope: { mode: 'all', student_ids: [] }, exam_scope: { mode: 'semester', session_ids: [7], sessions: [] },
  students: [
    { student_id: '12', student_name: '合成甲', student_code: 'S012', class_id: '1', score_rate: .8, score_rate_source: 'current_exam',
      weak_points: [point('sk_a', 'weak'), point('sk_a', 'weak'), point('tp_b', 'unsteady'), point('kp_c1', 'weak')] },
    { student_id: '13', student_name: '合成乙', student_code: 'S013', class_id: '1', score_rate: .3, score_rate_source: 'historical_fallback',
      weak_points: [point('sk_a', 'stable'), point('tp_b', 'unsteady'), point('sk_none', 'insufficient', 0)] },
    { student_id: '22', student_name: '合成丙', student_code: 'S022', class_id: '2', weak_points: [point('tp_b', 'weak')] },
  ],
  knowledge_catalog: [
    { knowledge_key: 'kp_c1', knowledge_point: '章', node_kind: 'chapter' },
    { knowledge_key: 'kp_c1_s1', knowledge_point: '节', node_kind: 'section', parent_knowledge_key: 'kp_c1' },
    { knowledge_key: 'sk_a', knowledge_point: '技能甲', node_kind: 'skill', parent_knowledge_key: 'kp_c1_s1' },
    { knowledge_key: 'tp_b', knowledge_point: '题型乙', node_kind: 'topic', parent_knowledge_key: 'kp_c1_s1' },
    { knowledge_key: 'sk_none', knowledge_point: '未观测', node_kind: 'skill', parent_knowledge_key: 'kp_c1_s1' },
  ],
  coverage: { covered_items: 0, total_items: 0, missing_items: {} }, confirmed_concept_ids: [], suggested_terms: [],
  unmapped_terms: [], warnings: [], diagnosis_identity: 'question_tag',
}
function mount(component: Parameters<typeof createApp>[0], router?: Router) {
  const host = document.createElement('div'); document.body.append(host)
  const app = createApp(component); app.use(createPinia()); if (router) app.use(router); app.mount(host); apps.push(app)
  return host
}
function button(host: HTMLElement, label: string) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === label)! }
beforeEach(() => {
  localStorage.clear()
  wrongApi.preview.mockImplementation(async body => ({ students: body.student_ids.map((id: number) => ({ id, name: `合成${id}`, student_code: `S${id}`, class_name: '1', created_at: null })),
    sessions: (body.student_ids.includes(22) ? [7, 8, 9] : [7, 8]).map(id => ({ session_id: id, session_name: `合成考试${id}`, exam_created_at: null })),
    session_ids: body.session_ids ?? (body.student_ids.includes(22) ? [7, 8, 9] : [7, 8]), semester_label: '合成册', question_count: 3,
    session_wrong_counts: { 7: 2, 8: 1, 9: 1 }, out_of_scope_count: 1, empty_students: [], missing_items: [] }))
})
afterEach(() => { apps.splice(0).forEach(app => app.unmount()); document.body.innerHTML = ''; vi.clearAllMocks() })

describe('学生和章节选择', () => {
  it('keeps roster, all-section statistics and learned progress equal with display references counted', async () => {
    const referenceOnly: TrainingWeakPoint = { ...point('sk_reference', 'unsteady', 0),
      parent_knowledge_key: 'kp_c2_s1', source_question_refs: [{ session_id: 7, session_name: 'TEST考试',
        question_id: 'Q1', bank_question_id: 1, score_awarded: 0, full_score: 1 }] }
    const extra = Array.from({ length: 13 }, (_, index) => ({ ...point(`sk_extra_${index}`, 'weak'), mastery: index / 20 }))
    const full: TrainingDiagnosis = { ...diagnosis,
      students: diagnosis.students.map((student, index) => ({ ...student,
        weak_points: [...student.weak_points, ...(index === 0 ? [referenceOnly, ...extra] : [])] })),
      knowledge_catalog: [...(diagnosis.knowledge_catalog ?? []),
        { knowledge_key: 'kp_c2', knowledge_point: '第二章', node_kind: 'chapter' },
        { knowledge_key: 'kp_c2_s1', knowledge_point: '第二章节1', node_kind: 'section', parent_knowledge_key: 'kp_c2' },
        { knowledge_key: 'sk_reference', knowledge_point: '引用证据项', node_kind: 'skill', parent_knowledge_key: 'kp_c2_s1' },
        ...extra.map((weak): NonNullable<TrainingDiagnosis['knowledge_catalog']>[number] => ({
          knowledge_key: weak.knowledge_key, knowledge_point: weak.knowledge_point,
          node_kind: 'skill', parent_knowledge_key: 'kp_c1_s1' }))],
    }
    const display = decodeTrainingDisplayDiagnosis({ ...full, response_mode: 'display', group_weak_points: [],
      students: full.students.map(student => ({ ...student, weak_points: student.weak_points.map(weak => ({
        knowledge_key: weak.knowledge_key, knowledge_point: weak.knowledge_point, mastery: weak.mastery,
        tier: weak.tier ?? 'insufficient', observation_count: weak.observation_count ?? 0,
        evidence_count: weak.evidence_count, parent_knowledge_key: weak.parent_knowledge_key,
        source_reference_count: weak.source_question_refs.length,
      })) })),
    })
    expect(display.students.map(student => student.weak_points.length)).toEqual(full.students.map(student => student.weak_points.length))
    expect(resolvePaperScope(volume, display, [], '', 'comprehensive')).toEqual(resolvePaperScope(volume, full, [], '', 'comprehensive'))
    expect(resolvePaperScope(volume, display, [], '', 'comprehensive').progressId).toBe('TEST-c2')
    const fullHost = mount(defineComponent({ setup: () => () => h('div', [h(StudentPicker, { diagnosis: full, modelValue: [] }),
      h(KnowledgeRangeList, { volume, diagnosis: full, mode: 'range' })]) }))
    const displayHost = mount(defineComponent({ setup: () => () => h('div', [h(StudentPicker, { diagnosis: display, modelValue: [] }),
      h(KnowledgeRangeList, { volume, diagnosis: display, mode: 'range' })]) }))
    expect(displayHost.textContent).toBe(fullHost.textContent)
    expect([...displayHost.querySelectorAll('.range-tier-bar')].map(node => node.getAttribute('aria-label')))
      .toEqual([...fullHost.querySelectorAll('.range-tier-bar')].map(node => node.getAttribute('aria-label')))
    const snapshot = shallowRef<TrainingReadDiagnosis>(full)
    const blank = defineComponent({ render: () => null })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: blank }, { name: 'student-evidence', path: '/student/:studentId', component: blank },
    ] })
    mount(defineComponent({ setup: () => () => h(StudentQuickView, { diagnosis: snapshot.value,
      student: snapshot.value.students[0] ?? null, open: true }) }), router)
    await vi.waitFor(() => expect(document.querySelectorAll('.practice-student-drawer li')).toHaveLength(10))
    const before = [...document.querySelectorAll('.practice-student-drawer li')].map(node => node.textContent)
    snapshot.value = display
    await nextTick()
    expect([...document.querySelectorAll('.practice-student-drawer li')].map(node => node.textContent)).toEqual(before)
  })

  it('starts with no students, deduplicates weak leaves, and selects a whole class independently of search', async () => {
    const selected = ref<string[]>([]), show = vi.fn()
    const host = mount(defineComponent({ setup: () => () => h(StudentPicker, { diagnosis, modelValue: selected.value,
      'onUpdate:modelValue': value => { selected.value = value }, onShowStudent: show }) }))
    expect(host.textContent).toContain('已选 0 人')
    expect(host.querySelector('.student-picker-row')?.textContent).toContain('薄弱 1')
    expect(host.textContent).toContain('80%')
    expect(host.querySelectorAll('.student-picker-score.none')).toHaveLength(2)
    const search = host.querySelector<HTMLInputElement>('[aria-label="检索姓名或学号"]')!
    search.value = 'S012'; search.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    button(host, '全选本班').click(); await nextTick()
    expect(selected.value).toEqual(['12', '13'])
    button(host, '合成甲').click(); expect(show).toHaveBeenCalledWith(diagnosis.students[0])
    button(host, '清空').click(); await nextTick(); expect(selected.value).toEqual([])
    search.value = ''; search.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    host.querySelector<HTMLInputElement>('.student-picker-tools input[type=checkbox]')!.click(); await nextTick()
    expect(host.querySelectorAll('.student-picker-row')).toHaveLength(2)
    expect(host.textContent).not.toContain('合成乙')
  })

  it('uses each observed student’s worst tier and expands leaf counts without counting chapter summaries', async () => {
    const host = mount(defineComponent({ setup: () => () => h(KnowledgeRangeList, { volume, diagnosis, mode: 'range', studentIds: ['12', '13'] }) }))
    expect(host.querySelector('.range-tier-bar')?.getAttribute('aria-label')).toBe('明显薄弱 1 人，还不稳 1 人，较稳定 0 人，证据不足 0 人')
    host.querySelector<HTMLButtonElement>('[aria-label="展开节1.1"]')!.click(); await nextTick()
    const text = host.querySelector('.range-points')?.textContent
    expect(text).toContain('sk_a明显薄弱 1 人 · 还不稳 0 人')
    expect(text).toContain('tp_b明显薄弱 0 人 · 还不稳 2 人')
    expect(text).not.toContain('未观测')
    expect(text).not.toContain('kp_c1')
  })

  it('expands a stored whole chapter before unchecking a section and supports comprehensive progress', async () => {
    const keys = ref(['kp_c1']), mode = ref<'comprehensive' | 'focused'>('focused'), progress = ref('TEST-c1')
    const host = mount(defineComponent({ setup: () => () => h(KnowledgeRangeList, { volume, diagnosis, mode: 'range', studentIds: [],
      rangeKeys: keys.value, 'onUpdate:rangeKeys': value => { keys.value = value }, scopeMode: mode.value,
      'onUpdate:scopeMode': value => { mode.value = value }, teachingProgressChapterId: progress.value }) }))
    expect(host.textContent).toContain('勾选学生后显示')
    host.querySelector<HTMLInputElement>('[aria-label="选择节1.1"]')!.click(); await nextTick()
    expect(keys.value).toEqual(['kp_c1_s2'])
    host.querySelector<HTMLInputElement>('[aria-label="选择第1章"]')!.click(); await nextTick()
    expect(new Set(keys.value)).toEqual(new Set(['kp_c1_s1', 'kp_c1_s2']))
    button(host, '综合').click(); await nextTick()
    expect(host.querySelectorAll('.is-unlearned')).toHaveLength(1)
    expect(host.querySelector('input[type=checkbox]')).toBeNull()
  })

  it('commits score floor only on blur or Enter and selects one class', async () => {
    const floor = ref<number | null>(null), apply = vi.fn(), select = vi.fn()
    const host = mount(defineComponent({ setup: () => () => h(TrainingScopeBar, { volumeLabel: '合成册', sessionCount: 2,
      classes: ['1', '2'], selectedClass: '', scoreFloor: floor.value, 'onSelect-class': select,
      'onUpdate-score-floor': (value: number | null) => { floor.value = value; apply(value) } }) }))
    const input = host.querySelector<HTMLInputElement>('input')!
    input.value = '20'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    expect(apply).not.toHaveBeenCalled()
    input.dispatchEvent(new Event('blur')); await nextTick(); expect(apply).toHaveBeenCalledExactlyOnceWith(.2)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })); expect(apply).toHaveBeenCalledExactlyOnceWith(.2)
    input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })); await nextTick(); expect(apply).toHaveBeenLastCalledWith(null)
    button(host, '2 班').click(); expect(select).toHaveBeenCalledWith('2')
  })

  it.each(['training', 'handout'] as const)('migrates version 7 flat rules only into its %s purpose', purpose => {
    localStorage.setItem('ai-grading:personalized-paper-selection:v1', JSON.stringify({ rulesVersion: 7, purpose,
      questionCount: 42, difficultyMax: 7, maxQuestionsPerSkill: 2, maxWrittenQuestions: 9, recentActivityCount: 4,
      targetKeys: [], rangeKeys: [], excludeCurrentOriginals: true, paperMode: 'individual' }))
    const restored = loadPaperSelectionSession()!
    expect(restored.rulesVersion).toBe(8); expect(restored.selectedStudentIds).toEqual([])
    expect(restored[purpose === 'training' ? 'trainingRules' : 'handoutRules']).toMatchObject({ questionCount: 42, difficultyMax: 7, maxQuestionsPerSkill: 2 })
    expect(restored[purpose === 'training' ? 'handoutRules' : 'trainingRules']).toEqual(purpose === 'training' ? DEFAULT_HANDOUT_RULES : DEFAULT_TRAINING_RULES)
    expect(restored.wrongBook).toEqual({ sessionIds: null, includeSourceLabel: true, includeAnswerSpace: true })
    presetFocusedTraining({ targetKeys: ['sk_a'], rangeKeys: ['kp_c1'] })
    expect(loadPaperSelectionSession()).toMatchObject({ purpose: 'training', scopeMode: 'focused', targetKeys: ['sk_a'], rangeKeys: ['kp_c1'] })
  })
})

function exportJob() { return { id: 71, job_type: 'wrong_question_export', payload: {}, result: {
  question_count: 3, download_url: '/api/jobs/71/download', filename: 'TEST-books.zip', missing_items: [], failed_students: [],
  empty_students: [{ student_id: 13, student_name: '全对学生', reason: '没有错题' }],
}, status: 'succeeded', progress: 1, stage: 'wrong_question_export', detail: '', error: null, cancel_requested: false,
  created_at: '2026-10-03T00:00:00Z', started_at: null, updated_at: '2026-10-03T00:00:00Z', finished_at: '2026-10-03T00:00:00Z' } }
function mountExport(studentIds = ref(['12', '13']), sessions = ref<number[] | null>(null), source = ref(true), space = ref(true)) {
  const keys = ref<string[]>([])
  const host = mount(defineComponent({ setup: () => () => h(WrongQuestionBookExport, { studentIds: studentIds.value,
    volumeId: 'TEST-volume', scopeKeys: keys.value, valid: true, sessionIds: sessions.value,
    includeSourceLabel: source.value, includeAnswerSpace: space.value,
    'onUpdate:sessionIds': value => { sessions.value = value }, 'onUpdate:includeSourceLabel': value => { source.value = value },
    'onUpdate:includeAnswerSpace': value => { space.value = value } }) }))
  return { host, studentIds, sessions, keys, source, space }
}
describe('错题本批量导出', () => {
  it('preserves unchecked exams on student changes, selects new exams, and aborts the superseded preview', async () => {
    const view = mountExport()
    await vi.waitFor(() => expect(view.host.textContent).toContain('可导出 3 道错题'))
    expect(view.sessions.value).toEqual([7, 8])
    expect([...view.host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')].every(input => input.checked)).toBe(true)
    view.host.querySelector<HTMLInputElement>('.wrong-book-exams input')!.click()
    await vi.waitFor(() => expect(wrongApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ session_ids: [8] }), expect.any(AbortSignal)))
    view.studentIds.value = ['12', '22']
    await vi.waitFor(() => expect(view.sessions.value).toEqual([8, 9]))
    await vi.waitFor(() => expect(wrongApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ student_ids: [12, 22], session_ids: [8, 9] }), expect.any(AbortSignal)))
    let pendingSignal: AbortSignal | undefined
    wrongApi.preview.mockImplementationOnce((_body, signal) => { pendingSignal = signal; return new Promise(() => {}) })
    view.keys.value = ['kp_c1']
    await vi.waitFor(() => expect(pendingSignal).toBeDefined())
    view.keys.value = ['kp_c2']; await nextTick(); expect(pendingSignal!.aborted).toBe(true)
    await vi.waitFor(() => expect(wrongApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ scope_keys: ['kp_c2'] }), expect.any(AbortSignal)))
  })

  it('submits exact options, lists omitted students, downloads once, and retains the downloaded receipt', async () => {
    wrongApi.submit.mockResolvedValue(exportJob())
    wrongApi.download.mockResolvedValue({ blob: new Blob(['TEST']), filename: 'TEST-books.zip' })
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:TEST') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    const anchor = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const view = mountExport()
    await vi.waitFor(() => expect(view.host.textContent).toContain('可导出 3 道错题'))
    view.source.value = false; view.space.value = false; view.keys.value = ['kp_c1']
    await nextTick()
    await vi.waitFor(() => expect(view.host.querySelector<HTMLButtonElement>('footer button')?.disabled).toBe(false))
    view.host.querySelector<HTMLButtonElement>('footer button')!.click()
    await vi.waitFor(() => expect(view.host.textContent).toContain('已完成'))
    expect(wrongApi.submit).toHaveBeenCalledWith(expect.objectContaining({ student_ids: [12, 13], scope_keys: ['kp_c1'],
      session_ids: [7, 8], include_source_label: false, include_answer_space: false, client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/) }))
    expect(view.host.textContent).toContain('全对学生 · 没有错题')
    button(view.host, '下载全部错题本').click()
    await vi.waitFor(() => expect(view.host.textContent).toContain('本机临时导出文件已清除'))
    expect(wrongApi.download).toHaveBeenCalledOnce()
    expect(JSON.parse(localStorage.getItem('ai-grading:wrong-question-book:v2')!)).toMatchObject({ downloaded: true })
    anchor.mockRestore()
  })

  it('recovers an uncertain submission only for the identical fingerprint and never resubmits automatically', async () => {
    wrongApi.submit.mockRejectedValue(new ApiError({ kind: 'timeout', status: null, code: 'request_timeout', message: 'TEST timeout', details: {}, requestId: 'TEST', retryable: false }))
    wrongApi.find.mockRejectedValue(new Error('TEST pending'))
    const view = mountExport()
    await vi.waitFor(() => expect(view.host.textContent).toContain('可导出 3 道错题'))
    view.host.querySelector<HTMLButtonElement>('footer button')!.click()
    await vi.waitFor(() => expect(view.host.textContent).toContain('不会自动重复导出'))
    const token = wrongApi.submit.mock.calls[0]![0].client_request_token
    apps.splice(0).forEach(app => app.unmount()); document.body.innerHTML = ''
    wrongApi.find.mockResolvedValue(exportJob())
    const restored = mountExport(ref(['12', '13']), ref([7, 8]))
    await vi.waitFor(() => expect(restored.host.textContent).toContain('已完成'))
    expect(wrongApi.find).toHaveBeenLastCalledWith(token); expect(wrongApi.submit).toHaveBeenCalledOnce()
    apps.splice(0).forEach(app => app.unmount()); document.body.innerHTML = ''
    wrongApi.find.mockClear()
    mountExport(ref(['12', '13']), ref([7, 8]), ref(false))
    await new Promise(resolve => setTimeout(resolve, 350))
    expect(wrongApi.find).not.toHaveBeenCalled(); expect(wrongApi.submit).toHaveBeenCalledOnce()
  })
})
