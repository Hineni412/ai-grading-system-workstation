import { createApp, nextTick } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/assembly'
import type { AssemblyAssistantResult, AssemblyDraft, AssemblyQuestion } from '../api/assembly'
import * as students from '../api/students'
import { useAssemblyAssistantStore } from '../stores/assembly-assistant'
import { useAssemblyStore } from '../stores/assembly'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import AssemblyAssistantPanel from '../components/question-assembly/AssemblyAssistantPanel.vue'
import QuestionAssemblyView from '../views/QuestionAssemblyView.vue'

const revision = 'a'.repeat(64)
const blank: AssemblyDraft = { basket_ids: [], order_ids: [], sections: [], title: '已有的班级卷', header_text: '', include_answer: true, layout_mode: 'sequential', preview_mode: 'teacher', revision }
const question: AssemblyQuestion = { id: 11, revision, question_number: '1', question_type: '选择题', question_text: '合成题：直角边为 3 和 4，斜边长为多少？', answer_text: '合成解析：5', difficulty: '4', paper_title: '合成题库', tags: [], asset_urls: [], score_value: 3 }
function result(): AssemblyAssistantResult {
  return { student_count: 30, evidence_student_count: 25, exam_student_count: 25, exam_count: 2, exam_score_rate: .6,
    weaknesses: [
      { knowledge_key: 'kp_one', knowledge_point: '八年级｜勾股定理应用', mastery: .55, weak_student_count: 19, evidence_student_count: 25, exam_score_rate: .61, evidence_count: 50, candidate_count: 35, target_difficulty: 5.2 },
      { knowledge_key: 'kp_two', knowledge_point: '八年级｜平方根', mastery: .7, weak_student_count: 12, evidence_student_count: 25, exam_score_rate: .68, evidence_count: 30, candidate_count: null, target_difficulty: null },
    ],
    selected_target_keys: ['kp_one'], candidate_total: 35, candidates: [{ question_id: 11, target_keys: ['kp_one'], difficulty: 5, difficulty_band: 'suitable' }],
  }
}
let pinia: ReturnType<typeof createPinia>
const mounted: ReturnType<typeof createApp>[] = []
beforeEach(() => {
  localStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(api, 'fetchAssemblyCandidates').mockResolvedValue(result())
  vi.spyOn(api.assemblyApi, 'resolveQuestions').mockResolvedValue({ items: [question], missing_question_ids: [] })
  vi.spyOn(api.assemblyApi, 'getDraft').mockResolvedValue({ ...blank })
  vi.spyOn(api.assemblyApi, 'saveDraft').mockImplementation(async (_, draft) => ({ ...draft }))
  vi.spyOn(api.assemblyApi, 'listRecords').mockResolvedValue({ items: [], total: 0 })
  vi.spyOn(students, 'fetchStudents').mockResolvedValue([{ id: 1, student_code: 'synthetic', name: '合成学生', class_name: '9班', created_at: null }])
  const curriculum = useCurriculumScopeStore()
  curriculum.selectedVolumeId = 'volume'
  curriculum.loadState = 'ready'
  curriculum.volumes = [{ id: 'volume', label: '八年级上册', grade: '八年级', semester: '上学期', textbook_version: '北师大版', order: 3, source: {}, statistics: { raw_nodes: 3, excluded_nodes: 0, retained_nodes: 3 }, chapters: [] }]
})
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  disposePinia(pinia)
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
async function mountPanel() {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AssemblyAssistantPanel)
  app.use(pinia).mount(host)
  mounted.push(app)
  await vi.waitFor(() => expect(useAssemblyAssistantStore().filters.class_id).toBe('9班'))
  await vi.waitFor(() => expect(useAssemblyStore().loadState).toBe('empty'))
  return { app, host }
}

describe('class assembly assistant', () => {
  it('only adds teacher-picked questions to the existing basket and keeps results when returning', async () => {
    const { host, app } = await mountPanel()
    const assistant = useAssemblyAssistantStore()
    assistant.filters.chapter_id = 'remembered-chapter'
    await assistant.search()
    await nextTick()
    expect(host.textContent).toContain('19 人需巩固 / 25 人有证据')
    expect(host.textContent).toContain(question.question_text)
    expect(api.assemblyApi.saveDraft).not.toHaveBeenCalled()
    const answer = [...host.querySelectorAll('button')].find(item => item.textContent === '查看解析')!
    answer.click()
    await nextTick()
    expect(host.textContent).toContain('合成解析：5')
    ;([...host.querySelectorAll('button')].find(item => item.textContent === '加入试卷篮')!).click()
    await vi.waitFor(() => expect(api.assemblyApi.saveDraft).toHaveBeenCalledTimes(1))
    expect(useAssemblyStore().draft.order_ids).toEqual([11])
    expect(useAssemblyStore().draft.title).toBe('已有的班级卷')
    app.unmount()
    mounted.splice(mounted.indexOf(app), 1)
    const nextHost = document.createElement('div')
    document.body.append(nextHost)
    const nextApp = createApp(AssemblyAssistantPanel)
    nextApp.use(pinia).mount(nextHost)
    mounted.push(nextApp)
    await nextTick()
    expect(assistant.filters.chapter_id).toBe('remembered-chapter')
    expect(nextHost.textContent).toContain('已在试卷篮')
    expect(api.fetchAssemblyCandidates).toHaveBeenCalledTimes(1)
    assistant.filters.question_type = '填空题'
    await nextTick()
    expect(nextHost.textContent).toContain('正在按新的选择更新候选题')
    expect((nextHost.querySelector('.assistant-question footer .assembly-button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('discards a late result from another class and preserves the previous shortlist on failure', async () => {
    const assistant = useAssemblyAssistantStore()
    assistant.changeScope({ class_id: '9班', curriculum_volume_id: 'volume' })
    await assistant.search()
    vi.mocked(api.fetchAssemblyCandidates).mockRejectedValueOnce(new Error('offline'))
    await assistant.search()
    expect(assistant.state).toBe('error')
    expect(assistant.questions).toEqual([question])
    let resolve!: (value: AssemblyAssistantResult) => void
    vi.mocked(api.fetchAssemblyCandidates).mockReturnValueOnce(new Promise(done => { resolve = done }))
    const pending = assistant.search()
    assistant.changeScope({ class_id: '10班' })
    resolve(result())
    await pending
    expect(assistant.result).toBeNull()
    expect(assistant.questions).toEqual([])
    expect(assistant.state).toBe('idle')
    expect(api.assemblyApi.saveDraft).not.toHaveBeenCalled()
  })

  it('distinguishes no evidence from no matching questions and marks a pending single selection stale', async () => {
    const { host } = await mountPanel()
    const assistant = useAssemblyAssistantStore()
    vi.mocked(api.fetchAssemblyCandidates).mockResolvedValueOnce({ ...result(), evidence_student_count: 0, weaknesses: [], selected_target_keys: [], candidate_total: 0, candidates: [] })
    await assistant.search()
    await nextTick()
    expect(host.textContent).toContain('本学期尚无可用掌握证据')
    expect(api.assemblyApi.resolveQuestions).not.toHaveBeenCalled()
    await assistant.search()
    assistant.selectTarget('kp_two')
    expect(assistant.filters.target_keys).toEqual(['kp_two'])
    expect(assistant.isStale).toBe(true)
    expect(api.assemblyApi.saveDraft).not.toHaveBeenCalled()
    expect(() => api.decodeAssemblyAssistant({ ...result(), exam_score_rate: 2 })).toThrow()
  })

  it('opens old AI entry links in the local assistant without starting model requests', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workbench', component: QuestionAssemblyView }] })
    await router.push('/workbench?mode=ai')
    await router.isReady()
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionAssemblyView)
    app.use(pinia).use(router).mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.textContent).toContain('从班级薄弱处，找到值得练的题'))
    expect(host.textContent).toContain('学情组卷助手')
    expect(host.textContent).not.toContain('细目表')
    expect(api.fetchAssemblyCandidates).not.toHaveBeenCalled()
    expect(api.assemblyApi.saveDraft).not.toHaveBeenCalled()
  })

  it('updates on card selection, slider changes and W/S keys, reusing cached results', async () => {
    const { host } = await mountPanel()
    const assistant = useAssemblyAssistantStore()
    vi.mocked(api.fetchAssemblyCandidates).mockImplementation(async body => ({ ...result(),
      selected_target_keys: body.target_keys ?? ['kp_one'],
      candidates: body.target_keys?.[0] === 'kp_two'
        ? [{ question_id: 12, target_keys: ['kp_two'], difficulty: 3, difficulty_band: 'lower' }]
        : result().candidates,
      candidate_total: body.target_keys?.[0] === 'kp_two' ? 1 : 35,
    }))
    vi.mocked(api.assemblyApi.resolveQuestions).mockImplementation(async ids => ({
      items: ids.map(id => ({ ...question, id })), missing_question_ids: [],
    }))
    await assistant.search()
    await nextTick()
    expect(host.querySelectorAll('.assistant-question')).toHaveLength(1)
    const secondCard = host.querySelectorAll<HTMLElement>('.assistant-weakness')[1]!
    secondCard.click()
    await vi.waitFor(() => expect(assistant.selectedKey).toBe('kp_two'))
    await vi.waitFor(() => expect(host.textContent).toContain('难度较低'))
    expect(api.fetchAssemblyCandidates).toHaveBeenLastCalledWith(expect.objectContaining({ target_keys: ['kp_two'] }), expect.any(AbortSignal))
    expect(api.fetchAssemblyCandidates).toHaveBeenCalledTimes(2)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'w' }))
    await vi.waitFor(() => expect(assistant.selectedKey).toBe('kp_one'))
    await vi.waitFor(() => expect(host.querySelectorAll('.assistant-question')).toHaveLength(1))
    // Revisiting the first card served the cached result without a new request.
    expect(api.fetchAssemblyCandidates).toHaveBeenCalledTimes(2)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's' }))
    await vi.waitFor(() => expect(assistant.selectedKey).toBe('kp_two'))
    expect(api.fetchAssemblyCandidates).toHaveBeenCalledTimes(2)
    const min = host.querySelector<HTMLInputElement>('[aria-label="最低难度"]')!
    min.value = '3'
    min.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const max = host.querySelector<HTMLInputElement>('[aria-label="最高难度"]')!
    max.value = '6'
    max.dispatchEvent(new Event('input', { bubbles: true }))
    max.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(api.fetchAssemblyCandidates).toHaveBeenLastCalledWith(expect.objectContaining({ difficulty_min: 3, difficulty_max: 6 }), expect.any(AbortSignal)))
    expect(api.assemblyApi.saveDraft).not.toHaveBeenCalled()
  })

  it('keeps the entire candidate pool and only loads twelve previews at a time', async () => {
    const assistant = useAssemblyAssistantStore()
    assistant.changeScope({ class_id: '9班', curriculum_volume_id: 'volume' })
    const candidates = Array.from({ length: 35 }, (_, i) => ({ question_id: i + 1, target_keys: ['kp_one'] }))
    vi.mocked(api.fetchAssemblyCandidates).mockResolvedValue({ ...result(), candidates })
    vi.mocked(api.assemblyApi.resolveQuestions).mockImplementation(async ids => ({ items: ids.map(id => ({ ...question, id })), missing_question_ids: [] }))
    await assistant.search()
    expect(assistant.result?.candidates).toHaveLength(35)
    expect(assistant.questions).toHaveLength(12)
    await assistant.loadMore()
    expect(assistant.questions).toHaveLength(24)
    await assistant.loadMore()
    expect(assistant.questions).toHaveLength(35)
    expect(assistant.hasMore).toBe(false)
    expect(api.fetchAssemblyCandidates).toHaveBeenCalledTimes(1)
    expect(api.assemblyApi.resolveQuestions).toHaveBeenCalledTimes(3)
  })
})
