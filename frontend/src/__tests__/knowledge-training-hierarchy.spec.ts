import { createPinia, setActivePinia } from 'pinia'
import { createApp, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { TrainingDiagnosis, TrainingWeakPoint } from '../api/training'
import ChapterTrainingMatrix from '../components/knowledge-training/ChapterTrainingMatrix.vue'
import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
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
  const app = createApp({
    setup() {
      return () => h(component as never, props)
    },
  })
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
    expect(sectionButtons.map((node) => node.textContent?.trim())).toEqual(['1 幂的乘除'])
    const headers = [...host.querySelectorAll('.chapter-training thead th label span')]
    expect(headers.map((node) => node.textContent)).toEqual(['同底数幂相乘'])
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

  it('hides no-evidence points by default and reveals them via the toggle', async () => {
    const { host } = mountStructure()
    expect(host.textContent).not.toContain('幂的乘方运算')
    await toggleShowEmpty(host, '.structure-toggle')
    const names = [...host.querySelectorAll('.structure-point-name')]
    expect(names.map((node) => node.textContent)).toContain('幂的乘方运算')
  })
})
