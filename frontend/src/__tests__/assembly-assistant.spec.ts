import { createApp, nextTick } from 'vue';
import { createPinia, disposePinia, setActivePinia } from 'pinia';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/assembly'
import type { AssemblyAssistantResult, AssemblyDraft, AssemblyQuestion } from '../api/assembly';
import * as students from '../api/students'
import { useAssemblyAssistantStore } from '../stores/assembly-assistant';
import { useAssemblyStore } from '../stores/assembly';
import { useCurriculumScopeStore } from '../stores/curriculum-scope';
import AssemblyAssistantPanel from '../components/question-assembly/AssemblyAssistantPanel.vue'

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
async function mountPanel(initialSkill?: string) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AssemblyAssistantPanel, { initialSkill })
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
    expect(host.textContent).toContain('19 人需关注 / 25 人有证据')
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
    expect(useAssemblyStore().draft.practice_rules).toBe(true)
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

  it('preselects a bookmarked target within current results without changing scope', async () => {
    await mountPanel('kp_two')
    const assistant = useAssemblyAssistantStore()
    assistant.filters.chapter_id = 'remembered-chapter'
    await assistant.search()
    await vi.waitFor(() => expect(assistant.filters.target_keys).toEqual(['kp_two']))
    expect(assistant.filters.class_id).toBe('9班')
    expect(assistant.filters.chapter_id).toBe('remembered-chapter')
  })
  it('explains an unavailable bookmarked target and preserves current scope', async () => {
    const { host } = await mountPanel('sk_unavailable')
    const assistant = useAssemblyAssistantStore()
    assistant.filters.chapter_id = 'remembered-chapter'
    await assistant.search()
    await vi.waitFor(() => expect(host.textContent).toContain('技能不在当前班级与章节结果中'))
    expect(assistant.filters.target_keys).not.toContain('sk_unavailable')
    expect(assistant.filters.chapter_id).toBe('remembered-chapter')
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

})
