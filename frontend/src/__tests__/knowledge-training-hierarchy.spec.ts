import { createPinia, setActivePinia } from 'pinia'
import { createApp, h, nextTick, reactive } from 'vue'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { TrainingDiagnosis, TrainingWeakPoint } from '../api/training'
import ChapterTrainingMatrix from '../components/knowledge-training/ChapterTrainingMatrix.vue'
import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
import { createAppRouter } from '../router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'

const apps: Array<ReturnType<typeof createApp>> = []

function makeWeak(
  key: string,
  point: string,
  mastery: number,
  evidenceCount: number,
  parentKey?: string,
): TrainingWeakPoint {
  return {
    knowledge_key: key,
    knowledge_point: point,
    mastery,
    score_sum: 0,
    full_score_sum: 0,
    deduction_count: 0,
    evidence_count: evidenceCount,
    effective_weight: evidenceCount,
    exam_count: 1,
    source_question_refs: [],
    actionable_reasons: [],
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
    hierarchy_kind: parentKey ? 'child' : 'root',
    ...(parentKey ? { parent_knowledge_key: parentKey } : {}),
  } as TrainingWeakPoint
}

function makeDiagnosis(): TrainingDiagnosis {
  return {
    scope: { mode: 'all', student_ids: [] },
    exam_scope: { mode: 'current', session_ids: [7], sessions: [{ session_id: 7, session_name: '阶段测验' }] },
    students: [],
    group_weak_points: [
      makeWeak('kp_c1', '七年级下册｜第一章 整式的乘除', 0.52, 8),
      makeWeak('kp_c1_s1', '七年级下册｜第一章 整式的乘除｜1 幂的乘除', 0.52, 8, 'kp_c1'),
      makeWeak('kp_c1_s1_p1', '七年级下册｜第一章 整式的乘除｜1 幂的乘除｜同底数幂相乘', 0.52, 8, 'kp_c1_s1'),
    ],
    knowledge_catalog: [{
      knowledge_key: 'kp_c1',
      knowledge_point: '七年级下册｜第一章 整式的乘除',
    }, {
      knowledge_key: 'kp_c1_s1',
      knowledge_point: '七年级下册｜第一章 整式的乘除｜1 幂的乘除',
      parent_knowledge_key: 'kp_c1',
      parent_knowledge_point: '七年级下册｜第一章 整式的乘除',
    }, {
      knowledge_key: 'kp_c1_s1_p1',
      knowledge_point: '七年级下册｜第一章 整式的乘除｜1 幂的乘除｜同底数幂相乘',
      parent_knowledge_key: 'kp_c1_s1',
      parent_knowledge_point: '七年级下册｜第一章 整式的乘除｜1 幂的乘除',
    }, {
      knowledge_key: 'kp_c1_s1_p2',
      knowledge_point: '七年级下册｜第一章 整式的乘除｜1 幂的乘除｜幂的乘方运算',
      parent_knowledge_key: 'kp_c1_s1',
      parent_knowledge_point: '七年级下册｜第一章 整式的乘除｜1 幂的乘除',
    }, {
      knowledge_key: 'kp_c2',
      knowledge_point: '七年级上册｜第一章 丰富的图形世界',
    }, {
      knowledge_key: 'kp_c2_s1',
      knowledge_point: '七年级上册｜第一章 丰富的图形世界｜1 生活中的立体图形',
      parent_knowledge_key: 'kp_c2',
      parent_knowledge_point: '七年级上册｜第一章 丰富的图形世界',
    }],
    coverage: { covered_items: 0, total_items: 0, missing_items: {} },
    confirmed_concept_ids: [],
    suggested_terms: [],
    unmapped_terms: [],
    warnings: [],
    diagnosis_identity: 'question_tag',
  }
}

function selectVolume(label: string | null): void {
  const store = useCurriculumScopeStore()
  store.volumes = [
    { id: 'v-lower', label: '七年级下册' },
    { id: 'v-upper', label: '七年级上册' },
  ] as never
  store.selectedVolumeId = label === null ? null : (label === '七年级下册' ? 'v-lower' : 'v-upper')
  store.loadState = 'ready'
}

function mount(component: unknown, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  const router = createAppRouter(createMemoryHistory())
  const app = createApp({
    setup() {
      return () => h(component as never, props)
    },
  })
  app.use(router)
  app.mount(host)
  apps.push(app)
  return { host }
}

function mountMatrix() {
  return mount(ChapterTrainingMatrix, {
    diagnosis: makeDiagnosis(),
    modelValue: [],
    groupScopeLabel: '全部班级 · 0 人',
  })
}

function mountStructure() {
  return mount(TrainingKnowledgeStructure, {
    diagnosis: makeDiagnosis(),
    modelValue: [],
    title: '所选学生的加权知识结构',
    description: '',
  })
}

async function toggleShowEmpty(host: HTMLElement, selector: string): Promise<void> {
  host.querySelector<HTMLInputElement>(`${selector} input[type="checkbox"]`)!.click()
  await nextTick()
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('chapter training matrix hierarchy', () => {
  it('shows only chapters of the selected volume with short labels', () => {
    selectVolume('七年级下册')
    const { host } = mountMatrix()
    const chapterButtons = [...host.querySelectorAll('.chapter-training__scope > button')]
    expect(chapterButtons.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['第一章 整式的乘除'])
    expect(host.textContent).not.toContain('丰富的图形世界')
  })

  it('shows every volume chapter when no volume is selected', () => {
    selectVolume(null)
    const { host } = mountMatrix()
    const chapterButtons = [...host.querySelectorAll('.chapter-training__scope > button')]
    expect(chapterButtons.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['第一章 整式的乘除', '第一章 丰富的图形世界'])
  })

  it('nests sections under the active chapter and uses leaf labels in the matrix header', () => {
    selectVolume('七年级下册')
    const { host } = mountMatrix()
    const sectionButtons = [...host.querySelectorAll('.chapter-training__sections button')]
    expect(sectionButtons.map((node) => node.textContent?.trim())).toEqual(['整章汇总', '1 幂的乘除'])
    const headers = [...host.querySelectorAll('.chapter-training thead th label span')]
    expect(headers.map((node) => node.textContent)).toEqual(['同底数幂相乘'])
  })

  it('defaults to the whole-chapter view and narrows to a single section on click', async () => {
    selectVolume('七年级下册')
    const { host } = mountMatrix()
    const sectionButtons = [...host.querySelectorAll<HTMLButtonElement>('.chapter-training__sections button')]
    expect(sectionButtons[0]?.classList.contains('is-active')).toBe(true)
    expect(host.querySelector('.chapter-training__main header span')?.textContent)
      .toBe('七年级下册｜第一章 整式的乘除')

    sectionButtons[1]!.click()
    await nextTick()
    expect(host.querySelector('.chapter-training__main header span')?.textContent)
      .toBe('七年级下册｜第一章 整式的乘除｜1 幂的乘除')
    expect(sectionButtons[0]?.classList.contains('is-active')).toBe(false)

    host.querySelector<HTMLButtonElement>('.chapter-training thead input[type="checkbox"]')!.click()
    await nextTick()
    sectionButtons[0]!.click()
    await nextTick()
    expect(host.querySelector('.chapter-training__main header span')?.textContent)
      .toBe('七年级下册｜第一章 整式的乘除')
    expect(host.querySelector('.chapter-training__detail h2')?.textContent)
      .toBe('选择一个掌握度格子')
  })

  it('shows the whole-chapter evidence link with the cell knowledge key', async () => {
    selectVolume('七年级下册')
    const diagnosis = makeDiagnosis()
    diagnosis.students = [{
      student_id: '12',
      student_code: 'S012',
      student_name: '匿名学生甲',
      class_id: '七年级一班',
      weak_points: [makeWeak('kp_c1_s1_p1', '七年级下册｜第一章 整式的乘除｜1 幂的乘除｜同底数幂相乘', 0.52, 8, 'kp_c1_s1')],
    }]
    const { host } = mount(ChapterTrainingMatrix, {
      diagnosis,
      modelValue: [],
      groupScopeLabel: '全部班级 · 1 人',
    })
    host.querySelector<HTMLButtonElement>('.chapter-training tbody td button')!.click()
    await nextTick()
    const link = host.querySelector<HTMLAnchorElement>('.chapter-training__evidence-link')!
    expect(link.getAttribute('href')).toContain('/training/evidence/12')
    expect(link.getAttribute('href')).toContain('knowledge=kp_c1_s1_p1')
    expect(link.getAttribute('href')).toContain(encodeURIComponent('同底数幂相乘'))

    ;[...host.querySelectorAll<HTMLButtonElement>('.chapter-training__sections button')]
      .find((button) => button.textContent?.trim() === '1 幂的乘除')!
      .click()
    await nextTick()
    expect(host.querySelector('.chapter-training__detail h2')?.textContent)
      .toBe('选择一个掌握度格子')
  })

  it('hides no-evidence points by default and reveals them via the toggle', async () => {
    selectVolume('七年级下册')
    const { host } = mountMatrix()
    expect(host.textContent).not.toContain('幂的乘方运算')
    await toggleShowEmpty(host, '.chapter-training__toggle')
    const headers = [...host.querySelectorAll('.chapter-training thead th label span')]
    expect(headers.map((node) => node.textContent)).toEqual(['同底数幂相乘', '幂的乘方运算'])
  })
})

describe('training knowledge structure hierarchy', () => {
  it('refreshes indexed relationships when diagnosis data changes', async () => {
    const diagnosis = makeDiagnosis()
    diagnosis.knowledge_catalog!.push({ knowledge_key: 'sk_synthetic', knowledge_point: '技能·正确合并指数', parent_knowledge_key: 'kp_c1_s1', node_kind: 'skill' })
    diagnosis.knowledge_associations = [{ topic_key: 'kp_c1_s1_p1', skill_key: 'sk_synthetic', question_count: 7, same_part_question_count: 4, basis: 'same_part' }]
    const props = reactive({ diagnosis, modelValue: [], title: '关联', description: '' })
    const { host } = mount(TrainingKnowledgeStructure, props)
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p1"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_synthetic"]')?.classList.contains('is-related')).toBe(true)

    props.diagnosis = { ...diagnosis, knowledge_associations: [{ topic_key: 'kp_c1_s1_p2', skill_key: 'sk_synthetic', question_count: 9, same_part_question_count: 6, basis: 'same_part' }] }
    await nextTick()
    // A new diagnosis resets focus and restores the default section skills.
    expect(host.querySelector('[data-node-key="sk_synthetic"]')?.classList.contains('is-related')).toBe(false)
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p1"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_synthetic"]')).toBeNull()
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p2"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_synthetic"]')?.classList.contains('is-related')).toBe(true)
    expect(host.querySelector('.relationship-summary')?.textContent).toContain('6 道有同小问依据 / 9 道同题出现')
  })
  it('keeps an earlier-volume skill and its topic visible when hiding empty nodes', async () => {
    selectVolume('七年级下册')
    const diagnosis = makeDiagnosis()
    diagnosis.knowledge_catalog!.push({ knowledge_key: 'sk_earlier', knowledge_point: '七年级上册｜第一章 丰富的图形世界｜1 生活中的立体图形｜技能·辨认对应关系', parent_knowledge_key: 'kp_c2_s1', node_kind: 'skill' })
    diagnosis.group_weak_points!.push(makeWeak('sk_earlier', '技能·辨认对应关系', .6, 3, 'kp_c2_s1'))
    diagnosis.knowledge_associations = [{ topic_key: 'kp_c1_s1_p2', skill_key: 'sk_earlier', question_count: 3, same_part_question_count: 3, basis: 'same_part' }]
    const { host } = mount(TrainingKnowledgeStructure, { diagnosis, modelValue: [], title: '关联', description: '' })
    await toggleShowEmpty(host, '.structure-toggle')
    expect(host.querySelector('[data-node-key="sk_earlier"]')).toBeNull()
    expect(host.querySelector('[data-node-key="kp_c1_s1_p2"]')?.textContent).toContain('幂的乘方运算')
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p2"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_earlier"]')?.textContent).toContain('60%')
    host.querySelector<HTMLButtonElement>('[data-node-key="sk_earlier"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_earlier"]')?.classList.contains('is-focused')).toBe(true)
    expect(host.querySelector('[data-node-key="kp_c1_s1_p2"]')?.classList.contains('is-related')).toBe(true)
    host.querySelector<HTMLButtonElement>('[data-node-key="sk_earlier"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_earlier"]')).toBeNull()
    expect(host.querySelector('.structure-layout > nav')?.textContent).not.toContain('七年级上册')
  })
  it('separates topics from mastery and highlights both directions with association evidence', async () => {
    const diagnosis = makeDiagnosis()
    diagnosis.knowledge_catalog!.push({ knowledge_key: 'sk_synthetic', knowledge_point: '技能·正确合并指数', parent_knowledge_key: 'kp_c1_s1', node_kind: 'skill' })
    diagnosis.group_weak_points!.push(makeWeak('sk_synthetic', '技能·正确合并指数', .61, 5, 'kp_c1_s1'))
    diagnosis.knowledge_associations = [{ topic_key: 'kp_c1_s1_p1', skill_key: 'sk_synthetic', question_count: 7, same_part_question_count: 4, basis: 'same_part' }]
    const { host } = mount(TrainingKnowledgeStructure, { diagnosis, modelValue: [], title: '关联', description: '' })
    expect(host.querySelector('.relationship-topics')?.textContent).not.toContain('52%')
    expect(host.querySelector('.relationship-skills')?.textContent).toContain('61%')
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p1"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_synthetic"]')?.classList.contains('is-related')).toBe(true)
    expect(host.querySelector('.relationship-summary')?.textContent).toContain('4 道有同小问依据 / 7 道同题出现')
    host.querySelector<HTMLButtonElement>('[data-node-key="sk_synthetic"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="kp_c1_s1_p1"]')?.classList.contains('is-related')).toBe(true)
  })
  it('renders sections and points with leaf labels instead of full paths', () => {
    const { host } = mountStructure()
    const sectionHeading = host.querySelector('.structure-section-heading strong')
    expect(sectionHeading?.textContent).toBe('1 幂的乘除')
    const pointName = host.querySelector('.structure-point-name')
    expect(pointName?.textContent).toBe('同底数幂相乘')
  })

  it('filters chapter roots by the selected volume', () => {
    selectVolume('七年级下册')
    const { host } = mountStructure()
    const roots = [...host.querySelectorAll('.structure-layout > nav button span')]
    expect(roots.map((node) => node.textContent)).toEqual(['七年级下册｜第一章 整式的乘除'])
  })

  it('shows the complete structure by default and can hide no-evidence points', async () => {
    const { host } = mountStructure()
    expect(host.textContent).toContain('幂的乘方运算')
    await toggleShowEmpty(host, '.structure-toggle')
    expect(host.textContent).not.toContain('幂的乘方运算')
    expect(host.querySelector('.structure-point-name')?.textContent).toBe('同底数幂相乘')
  })

  it('links evidence leaves to the grouped question view and skips parent summaries', async () => {
    const diagnosis = makeDiagnosis()
    const summary = makeWeak('kp_c1_s2', '七年级下册｜第一章 整式的乘除｜2 整式的乘法', 0.61, 5, 'kp_c1')
    summary.hierarchy_kind = 'parent_summary'
    diagnosis.group_weak_points = [...(diagnosis.group_weak_points ?? []), summary]
    diagnosis.knowledge_catalog = [...(diagnosis.knowledge_catalog ?? []), {
      knowledge_key: 'kp_c1_s2',
      knowledge_point: '七年级下册｜第一章 整式的乘除｜2 整式的乘法',
      parent_knowledge_key: 'kp_c1',
      parent_knowledge_point: '七年级下册｜第一章 整式的乘除',
    }]
    const { host } = mount(TrainingKnowledgeStructure, {
      diagnosis,
      modelValue: [],
      title: '所选学生的加权知识结构',
      description: '',
    })
    await nextTick()

    const links = [...host.querySelectorAll<HTMLAnchorElement>('.structure-point-evidence')]
    expect(links).toHaveLength(1)
    const href = links[0]!.getAttribute('href')!
    expect(href).toContain('/training/evidence/group')
    expect(href).toContain('mode=questions')
    expect(href).toContain('knowledge=kp_c1_s1_p1')
    expect(href).toContain(encodeURIComponent('同底数幂相乘'))
    expect(href).toContain('from=student')
    // 父级汇总行（2 整式的乘法）有证据但不显示“证据”入口。
    expect(host.textContent).toContain('5 条证据')
  })

  it('lets teachers select chapter and section ranges without checking leaf points', async () => {
    const { host } = mount(TrainingKnowledgeStructure, {
      diagnosis: makeDiagnosis(),
      modelValue: [],
      title: '所选学生的加权知识结构',
      description: '',
      selectionKind: 'range',
    })
    await nextTick()
    expect(host.textContent).toContain('先圈定章或小节')
    const leafChecks = [...host.querySelectorAll<HTMLInputElement>('.structure-point input[type="checkbox"]')]
    expect(leafChecks).toHaveLength(0)
    const rangeChecks = [...host.querySelectorAll<HTMLInputElement>('.structure-range-check input')]
    expect(rangeChecks.length).toBeGreaterThan(0)
  })
})
