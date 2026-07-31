import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  compareMasteryVersions,
  fetchMasteryRollout,
  reviewMasteryDifference,
  updateMasteryRollout,
  type MasteryComparisonResponse,
  type MasteryRolloutState,
} from '../api/graph-v2'
import MasteryV2ComparisonPanel from '../components/knowledge-graph/MasteryV2ComparisonPanel.vue'

vi.mock('../api/graph-v2', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph-v2')>(),
  compareMasteryVersions: vi.fn(),
  fetchMasteryRollout: vi.fn(),
  reviewMasteryDifference: vi.fn(),
  updateMasteryRollout: vi.fn(),
}))

const query = {
  scope: { mode: 'student' as const, student_ids: ['12'] },
  exam_scope: { mode: 'current' as const, session_ids: [7] },
}
const rolloutV1: MasteryRolloutState = {
  enabled: false,
  active_mode: 'v1',
  active_parameter_version: null,
  approved_evaluation_id: null,
  revision: 1,
  updated_by: null,
  reason: null,
  updated_at: '2026-01-01 00:00:00',
}
const comparison: MasteryComparisonResponse = {
  schema_version: 'mastery-v1-v2-comparison-v1',
  evaluation_id: 'e'.repeat(64),
  as_of: '2026-01-31T00:00:00+00:00',
  parameter_version: 'p'.repeat(64),
  review_delta: 0.1,
  required_review_count: 1,
  maximum_absolute_delta: 0.233333,
  performance: { duration_ms: 2.5, items_per_second: 400 },
  gate: {
    evaluation_id: 'e'.repeat(64),
    parameter_version: 'p'.repeat(64),
    revision: 1,
    required_review_count: 1,
    accepted_count: 0,
    rejected_count: 0,
    pending_count: 1,
    passed: false,
  },
  items: [{
    item_hash: 'i'.repeat(64),
    student_id: '12',
    student_code: 'S12',
    student_name: '合成学生',
    class_id: '合成班',
    stable_key: 'kp_alg_linear_equation',
    display_name: '一元一次方程',
    mastery_v1: 1,
    mastery_v2: {
      schema_version: 'mastery-v2-result-v1',
      stable_key: 'kp_alg_linear_equation',
      status: 'available',
      value: 0.766667,
      as_of: '2026-01-31T00:00:00+00:00',
      parameter_version: 'p'.repeat(64),
      direct_evidence_count: 1,
      effective_sample_weight: 1,
      prior_mean: 0.65,
      prior_strength: 2,
      contributions: [],
      layers: [],
      prerequisites: [],
      explanations: ['小样本按先验收缩。'],
    },
    signed_delta: -0.233333,
    absolute_delta: 0.233333,
    reason_codes: ['small_sample_shrinkage'],
    reasons: ['v2 对小样本采用先验收缩，避免少量证据直接形成极端值。'],
    requires_review: true,
  }],
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountPanel() {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(MasteryV2ComparisonPanel, { query })
  app.mount(host)
  mounted.push(app)
  await settle()
  return host
}

function input(element: HTMLInputElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchMasteryRollout).mockResolvedValue(rolloutV1)
  vi.mocked(compareMasteryVersions).mockResolvedValue(comparison)
  vi.mocked(reviewMasteryDifference).mockResolvedValue({
    ...comparison.gate,
    revision: 2,
    accepted_count: 1,
    pending_count: 0,
    passed: true,
  })
  vi.mocked(updateMasteryRollout).mockResolvedValue({
    enabled: true,
    active_mode: 'v2',
    active_parameter_version: comparison.parameter_version,
    approved_evaluation_id: comparison.evaluation_id,
    revision: 2,
    updated_by: '数学组-张老师',
    reason: '已核对合成差异',
    updated_at: '2026-01-31 08:00:00',
  })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('mastery v2 comparison panel', () => {
  it('keeps v1 active until every required difference is accepted', async () => {
    const host = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('当前正式口径：v1'))

    host.querySelector<HTMLButtonElement>('[data-testid="compare-mastery-versions"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('-23.3 个百分点'))
    expect(host.textContent).toContain('必须抽检')

    const inputs = host.querySelectorAll<HTMLInputElement>(
      '.knowledge-graph-mastery-rollout__identity input',
    )
    input(inputs[0]!, '数学组-张老师')
    input(inputs[1]!, '已核对合成差异')
    await nextTick()

    const enable = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('抽检通过后启用 v2'))!
    expect(enable.disabled).toBe(true)
    const accept = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '确认可接受')!
    accept.click()

    await vi.waitFor(() => expect(host.textContent).toContain('已确认可接受'))
    expect(enable.disabled).toBe(false)
    enable.click()

    await vi.waitFor(() => expect(updateMasteryRollout).toHaveBeenCalledWith({
      enabled: true,
      expected_revision: 1,
      teacher_ref: '数学组-张老师',
      reason: '已核对合成差异',
      evaluation_id: comparison.evaluation_id,
    }))
    await vi.waitFor(() => expect(host.textContent).toContain('当前正式口径：v2'))
  })

  it('requires an explicit identity and reason before complete rollback', async () => {
    vi.mocked(fetchMasteryRollout).mockResolvedValue({
      ...rolloutV1,
      enabled: true,
      active_mode: 'v2',
      active_parameter_version: comparison.parameter_version,
      approved_evaluation_id: comparison.evaluation_id,
    })
    vi.mocked(updateMasteryRollout).mockResolvedValue({
      ...rolloutV1,
      revision: 2,
      updated_by: '数学组-张老师',
      reason: '发现解释不合适，回退',
    })
    const host = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('完整回退到 v1'))
    const rollback = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('完整回退到 v1'))!
    expect(rollback.disabled).toBe(true)

    const inputs = host.querySelectorAll<HTMLInputElement>(
      '.knowledge-graph-mastery-rollout__identity input',
    )
    input(inputs[0]!, '数学组-张老师')
    input(inputs[1]!, '发现解释不合适，回退')
    await nextTick()
    expect(rollback.disabled).toBe(false)
    rollback.click()

    await vi.waitFor(() => expect(updateMasteryRollout).toHaveBeenCalledWith({
      enabled: false,
      expected_revision: 1,
      teacher_ref: '数学组-张老师',
      reason: '发现解释不合适，回退',
    }))
    await vi.waitFor(() => expect(host.textContent).toContain('当前正式口径：v1'))
  })
})
