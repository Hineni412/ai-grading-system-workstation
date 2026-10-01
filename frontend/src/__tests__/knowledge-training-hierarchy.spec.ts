import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createApp, h, nextTick } from 'vue';
import { createMemoryHistory } from 'vue-router';

import type { TrainingDiagnosis, TrainingWeakPoint } from '../api/training';

import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
import { createAppRouter } from '../router';
import { useCurriculumScopeStore } from '../stores/curriculum-scope';

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

describe('training knowledge structure hierarchy', () => {
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
  it('shows topic and skill mastery and highlights both directions with association evidence', async () => {
    const diagnosis = makeDiagnosis()
    diagnosis.knowledge_catalog!.push({ knowledge_key: 'sk_synthetic', knowledge_point: '技能·正确合并指数', parent_knowledge_key: 'kp_c1_s1', node_kind: 'skill' })
    diagnosis.group_weak_points!.push(makeWeak('sk_synthetic', '技能·正确合并指数', .61, 5, 'kp_c1_s1'))
    diagnosis.knowledge_associations = [{ topic_key: 'kp_c1_s1_p1', skill_key: 'sk_synthetic', question_count: 7, same_part_question_count: 4, basis: 'same_part' }]
    const { host } = mount(TrainingKnowledgeStructure, { diagnosis, modelValue: [], title: '关联', description: '' })
    expect(host.querySelector('.relationship-topics')?.textContent).toContain('52%')
    expect(host.querySelector('.relationship-skills')?.textContent).toContain('61%')
    host.querySelector<HTMLButtonElement>('[data-node-key="kp_c1_s1_p1"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="sk_synthetic"]')?.classList.contains('is-related')).toBe(true)
    expect(host.querySelector('.relationship-summary')?.textContent).toContain('4 道有同小问依据 / 7 道同题出现')
    host.querySelector<HTMLButtonElement>('[data-node-key="sk_synthetic"] button')!.click()
    await nextTick()
    expect(host.querySelector('[data-node-key="kp_c1_s1_p1"]')?.classList.contains('is-related')).toBe(true)
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
