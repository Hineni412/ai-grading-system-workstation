import { createApp, nextTick, type App } from 'vue';
import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TrainingDiagnosis, TrainingGroup } from '../api/training';
import TrainingGroupRecommendations from '../components/knowledge-training/TrainingGroupRecommendations.vue'

import type { ChapterGroupEditor } from '../features/training/paper-selection-session';

const diagnose = vi.hoisted(() => vi.fn())
vi.mock('../api/training', async (original) => ({
  ...await original<typeof import('../api/training')>(), trainingApi: { diagnose },
}))

const group: TrainingGroup = {
  group_id: 'group-ab', source_version: 'source-1', ready: true, issues: [], warnings: [],
  reason: '共同薄弱且水平接近', compatibility: .9, available_question_count: 8, recent_excluded_count: 0,
  members: ['A', 'B'].map((id, i) => ({ student_id: id, student_name: '同名学生', student_code: `S${id}`,
    class_id: `${i + 1}`, evidence_count: 2, targets: [{ knowledge_key: 'target', knowledge_point: '全等三角形的性质',
      mastery: .4 + i * .05, evidence_count: 2, source_question_refs: [] }] })),
  targets: [{ knowledge_key: 'target', knowledge_point: '全等三角形的性质', min_mastery: .4,
    max_mastery: .45, median_mastery: .425, evidence_count: 4, sparse_member_count: 0,
    target_difficulty: 4, available_question_count: 8, difficulty_unknown: false }],
}
const diagnosis: TrainingDiagnosis = {
  scope: { mode: 'all', student_ids: ['A', 'B'] },
  exam_scope: { mode: 'current', session_ids: [1], sessions: [] },
  students: group.members.map((member, i) => ({ ...member, score_rate: .5 + i * .3, score_rate_source: 'current_exam', weak_points: [] })),
  knowledge_catalog: [{ knowledge_key: 'chapter', knowledge_point: '三角形' },
    { knowledge_key: 'target', knowledge_point: '全等三角形的性质' }],
  coverage: { covered_items: 2, total_items: 2, missing_items: {} },
  confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [], diagnosis_identity: 'question_tag',
  grouping: { version: 'v1', scope_keys: ['chapter'], groups: [group], selection: group, unassigned: [], warnings: [] },
}
let app: App | undefined
let host: HTMLDivElement
afterEach(() => { app?.unmount(); app = undefined; host?.remove(); vi.clearAllMocks() })

function mount(overlap = false, editor: ChapterGroupEditor | null = { memberIds: ['A', 'B'], targetKeys: ['target'], scopeKeys: ['chapter'] }, pinia = createPinia(), data = diagnosis) {
  diagnose.mockResolvedValue(data)
  const adopt = vi.fn()
  host = document.createElement('div')
  document.body.append(host)
  app = createApp(TrainingGroupRecommendations, {
    diagnosis: data, scope: { mode: 'all' }, examScope: { mode: 'current', session_ids: [1] },
    settings: { scope_keys: ['chapter'], question_count: 10, expected_minutes: 40, difficulty_min: 2,
      difficulty_max: 8,
      exclude_current_exam_originals: true, curriculum_volume_id: '' },
    editor, adopted: null,
    arrangements: overlap ? [{ groupId: 'previous', memberIds: ['A'], targetKeys: ['target'], scopeKeys: ['chapter'], sourceVersion: 'old' }] : [],
    onAdopt: adopt,
  })
  app.use(pinia)
  app.mount(host)
  return adopt
}
function button(label: string): HTMLButtonElement {
  const value = [...host.querySelectorAll('button')].find(item => item.textContent?.trim() === label)
  if (!value) throw new Error(`按钮不存在：${label}`)
  return value
}

describe('章节小组的采用与失败恢复', () => {

  it.each([[4.2, 5.6, '目标难度 4–6 级'], [4.2, 4.3, '目标难度 4 级'], [null, null, '难度未知']] as const)('shows target difficulty %s / %s', async (first, second, label) => {
    const current = { ...group, targets: [first, second].map(value => ({ ...group.targets[0]!, target_difficulty: value })) }
    mount(false, null, createPinia(), { ...diagnosis, grouping: { ...diagnosis.grouping!, groups: [current], selection: current } })
    await vi.waitFor(() => expect(host.textContent).toContain(label))
  })

  it('requires an explicit overlap decision and adopts only after checking the source again', async () => {
    const adopted = mount(true)
    await vi.waitFor(() => expect(host.textContent).toContain('此前生成草稿'))
    const submit = button('采用小组并核对出卷设置')
    expect(submit.disabled).toBe(true)
    const checkbox = [...host.querySelectorAll('input')].find(input => input.parentElement?.textContent?.includes('已核对重合'))!
    checkbox.click()
    await nextTick()
    expect(submit.disabled).toBe(false)
    submit.click()
    await vi.waitFor(() => expect(adopted).toHaveBeenCalledOnce())
    expect(diagnose).toHaveBeenCalledTimes(2)
    expect(adopted.mock.calls[0]?.[0].members.map((member: { student_id: string }) => member.student_id)).toEqual(['A', 'B'])
  })

  it('keeps the edited names and goals on a failed adoption check and succeeds on retry', async () => {
    const adopted = mount()
    await vi.waitFor(() => expect(button('采用小组并核对出卷设置').disabled).toBe(false))
    diagnose.mockRejectedValueOnce(new Error('synthetic unavailable'))
    button('采用小组并核对出卷设置').click()
    await vi.waitFor(() => expect(host.textContent).toContain('当前名单和目标已保留'))
    expect(adopted).not.toHaveBeenCalled()
    expect(host.querySelectorAll('.training-groups__members details')).toHaveLength(2)
    expect(host.textContent).toContain('全等三角形的性质')
    expect(button('采用小组并核对出卷设置').disabled).toBe(true)
    button('刷新建议').click()
    await vi.waitFor(() => expect(button('采用小组并核对出卷设置').disabled).toBe(false))
    button('采用小组并核对出卷设置').click()
    await vi.waitFor(() => expect(adopted).toHaveBeenCalledOnce())
  })
})
