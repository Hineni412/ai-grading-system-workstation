import { createApp, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeTrainingCriterionWorkspace,
  questionBankCriteriaApi,
  type TrainingCriterionVersion,
  type TrainingCriterionWorkspace,
} from '../api/question-bank-criteria'
import { ApiError } from '../api/errors'
import { questionBankApi } from '../api/question-bank'
import TrainingCriterionReview from '../components/question-bank/TrainingCriterionReview.vue'

const version: TrainingCriterionVersion = {
  version_id: 'a'.repeat(64),
  question_id: 17,
  version_number: 1,
  parent_version_id: null,
  source_content_hash: 'b'.repeat(64),
  schema_version: 'training-criteria-draft-v1',
  status: 'proposed',
  source_kind: 'backfill',
  source_reference: 'analysis:synthetic',
  criteria: {
    schema_version: 'training-criteria-draft-v1',
    question_id: 17,
    source_content_hash: 'b'.repeat(64),
    question_type: '计算题',
    points: [{
      point_id: 'p-process',
      target: '建立方程',
      observable_evidence: '列出正确等量关系',
      equivalent_rules: ['写出等价方程'],
      counterexamples: ['只有最终答案'],
    }],
    auxiliary_rules: ['书写清楚但不计入达成点数'],
    rationale: '依据标准答案拆分',
    confidence: 0.92,
    source_kind: 'combined_model',
  },
  criteria_hash: 'c'.repeat(64),
  quality_status: 'passed',
  quality_codes: [],
  created_by: 'criterion_backfill_job',
  decision_by: null,
  decision_note: null,
  decided_at: null,
  created_at: '2026-07-30 10:00:00',
  updated_at: '2026-07-30 10:00:00',
}

function workspace(
  current: TrainingCriterionVersion | null = version,
): TrainingCriterionWorkspace {
  const available = Boolean(
    current
    && current.quality_status === 'passed'
    && (current.status === 'proposed' || current.status === 'approved')
  )
  return {
    question_id: 17,
    state: available ? 'available' : (current?.status ?? 'missing'),
    available,
    revision: current ? 1 : 0,
    current_source_hash: 'b'.repeat(64),
    current_version: current,
    approved_version: null,
    versions: current ? [current] : [],
  }
}

const mounted: App[] = []

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

async function mountReview(): Promise<HTMLElement> {
  if (!vi.isMockFunction(questionBankApi.getSolutionEvidence)) {
    vi.spyOn(questionBankApi, 'getSolutionEvidence').mockResolvedValue({
      question_id: 17,
      available: false,
      evidence_version_id: null,
      status: null,
      evidence: null,
    })
  }
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(TrainingCriterionReview, { questionId: 17 })
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => {
    expect(host.textContent).not.toContain('正在读取判定点')
  })
  return host
}

describe('training criterion review', () => {
  it('accepts the frozen workspace contract and rejects internal paths', () => {
    const payload = workspace()
    expect(decodeTrainingCriterionWorkspace(payload)).toEqual(payload)
    expect(() => decodeTrainingCriterionWorkspace({
      ...payload,
      internal_file_path: 'D:\\private\\question.json',
    })).toThrow()
  })

  it('accepts judgment points written by exam and question-bank paper ingest', () => {
    const ingested = {
      ...version,
      schema_version: 'judgment-points-v1',
      source_kind: 'combined_model' as const,
      criteria: {
        ...version.criteria,
        schema_version: 'judgment-points-v1' as const,
        points: [{
          ...version.criteria.points[0]!,
          depends_on: [],
        }],
        solution_evidence: {
          schema_version: 'question-solution-evidence-v2',
          question_id: 17,
          parts: [{
            part_id: 'part-1',
            evidence_points: [{
              evidence_point_id: 'p-process',
              target: '建立方程',
              observable_evidence: '列出正确等量关系',
            }],
          }],
        },
      },
    }
    const payload = workspace(ingested)

    expect(decodeTrainingCriterionWorkspace(payload).current_version?.criteria.schema_version)
      .toBe('judgment-points-v1')
    expect(decodeTrainingCriterionWorkspace(payload).current_version?.criteria.points[0]?.target)
      .toBe('建立方程')
  })

  it('accepts teacher manual versions whose criteria carry a null solution_evidence', () => {
    const manual = {
      ...version,
      source_kind: 'teacher_manual',
      criteria: {
        ...version.criteria,
        solution_evidence: null,
      },
    }
    const payload = {
      ...workspace(),
      current_version: manual,
      versions: [manual],
    }

    const decoded = decodeTrainingCriterionWorkspace(payload)
    expect(decoded.current_version?.source_kind).toBe('teacher_manual')
    expect(decoded.current_version?.criteria.solution_evidence).toBeUndefined()
  })

  it('saves edits and confirms them in one step', async () => {
    const blocked = {
      ...version,
      quality_status: 'failed' as const,
      quality_codes: [
        'calculation_process_missing',
        'unknown_dependency',
      ],
    }
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(
      workspace(blocked),
    )
    const save = vi.spyOn(questionBankCriteriaApi, 'saveDraft').mockResolvedValue(
      workspace(version),
    )
    const confirm = vi.spyOn(questionBankCriteriaApi, 'review').mockResolvedValue(
      workspace({ ...version, status: 'approved' as const }),
    )
    const host = await mountReview()

    expect(host.querySelector('#criterion-review-title')?.textContent).toBe('判定点')
    expect(host.textContent).toContain('计算题不能只保留最终答案')
    expect(host.textContent).toContain('判定点之间的依赖关系不完整')
    expect(host.textContent).not.toContain('TRAINING EVIDENCE')
    expect(host.textContent).not.toContain('解题证据')
    expect(
      [...host.querySelectorAll('button')]
        .some((button) => button.textContent?.includes('批准')),
    ).toBe(false)

    const target = host.querySelector<HTMLInputElement>(
      'input[aria-label="第 1 个判定点目标"]',
    )
    expect(target).not.toBeNull()
    target!.value = '建立并求解方程'
    target!.dispatchEvent(new Event('input'))
    await nextTick()
    const saveButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '保存')
    saveButton?.click()

    await vi.waitFor(() => {
      expect(save).toHaveBeenCalledOnce()
      expect(confirm).toHaveBeenCalledOnce()
      expect(host.textContent).toContain('已保存并确认')
    })
    expect(save.mock.calls[0]?.[1]).toMatchObject({
      expected_revision: 1,
      parent_version_id: version.version_id,
      points: [{
        point_id: 'p-process',
        target: '建立并求解方程',
        observable_evidence: '列出正确等量关系',
      }],
    })
    expect(confirm.mock.calls[0]?.[1]).toMatchObject({
      version_id: version.version_id,
      action: 'approve',
    })
  })

  it('keeps the saved version visible when quality checks block confirmation', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(workspace())
    const stillBlocked = {
      ...version,
      quality_status: 'failed' as const,
      quality_codes: ['unknown_dependency'],
    }
    vi.spyOn(questionBankCriteriaApi, 'saveDraft').mockResolvedValue(
      workspace(stillBlocked),
    )
    vi.spyOn(questionBankCriteriaApi, 'review').mockRejectedValue(new ApiError({
      kind: 'validation',
      status: 422,
      code: 'criterion_quality_failed',
      message: 'criterion quality checks failed',
      details: {},
      requestId: 'req-quality-1',
      retryable: false,
    }))
    const host = await mountReview()

    const saveButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '保存')
    saveButton?.click()

    await vi.waitFor(() => {
      expect(host.textContent).toContain('已保存，但质量检查未通过')
    })
    expect(host.textContent).toContain('判定点之间的依赖关系不完整')
  })

  it('shows duplicate obligations as advisory notes instead of blocking', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(
      workspace({
        ...version,
        quality_status: 'failed' as const,
        quality_codes: ['duplicate_obligation'],
      }),
    )
    const host = await mountReview()

    expect(host.textContent).toContain('不同判定点重复要求了同一件事')
    expect(host.textContent).toContain('等待教师确认')
    expect(host.textContent).not.toContain('需要先修正')
  })

  it('lets quality-passed drafts enter training without an extra approval click', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(
      workspace({
        ...version,
        quality_status: 'passed',
        quality_codes: ['missing_actual_image'],
      }),
    )
    const host = await mountReview()

    expect(host.textContent).toContain('题目引用了图片，但当前图片内容不可用')
    expect(host.textContent).toContain('已可进入训练')
    expect(host.textContent).not.toContain('当前版本不能批准')
    const approve = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('批准用于以后训练'))
    expect(approve).toBeUndefined()
  })

  it('requires confirmation before regenerating only the selected question', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(
      workspace(null),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const start = vi.spyOn(
      questionBankCriteriaApi,
      'startBackfill',
    ).mockResolvedValue({
      run: {
        run_id: 'd'.repeat(64),
        mode: 'regenerate',
        status: 'pending',
        question_ids: [17],
        created_at: '2026-07-30 10:00:00',
        updated_at: '2026-07-30 10:00:00',
        finished_at: null,
        items: [{
          question_id: 17,
          status: 'pending',
          version_id: null,
          error_category: '',
          attempt_count: 0,
        }],
      },
      job: null,
    })
    const host = await mountReview()
    const regenerate = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('重新生成'))
    regenerate?.click()

    await vi.waitFor(() => {
      expect(start).toHaveBeenCalledOnce()
      expect(host.textContent).toContain('完成后刷新这里')
    })
    expect(window.confirm).toHaveBeenCalledOnce()
    expect(start.mock.calls[0]?.[0]).toEqual([17])
    expect(start.mock.calls[0]?.[2]).toBe('regenerate')
  })

  it('keeps knowledge mapping inside the same 判定点 block', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(workspace())
    vi.spyOn(questionBankApi, 'getSolutionEvidence').mockResolvedValue({
      question_id: 17,
      available: true,
      evidence_version_id: 'c'.repeat(64),
      status: 'proposed',
      evidence: {
        schema_version: 'question-solution-evidence-v1',
        question_id: 17,
        source_content_hash: 'a'.repeat(64),
        parts: [{
          part_id: 'part-1',
          label: '（1）',
          response_mode: 'process_required',
          canonical_answer: 'x = 2',
          accepted_forms: ['x=2'],
          full_answer: '配方后得到 x = 2。',
          proof_obligations: [],
          visual_requirements: [],
          deduction_policy: [],
          allow_alternative_methods: true,
          evidence_points: [{
            evidence_point_id: 'point-1',
            target: '完成方程求解',
            observable_evidence: '写出配方过程并得到正确解。',
            fine_term_links: [{
              fine_term_id: 'term-direct',
              fine_term_name: '一元二次方程求根',
              role: 'direct',
              core_resolution: {
                status: 'resolved',
                stable_keys: ['kp_equation'],
                reason: '明确细化到方程核心节点',
              },
            }],
            equivalent_rules: [],
            counterexamples: [],
          }],
        }],
        auxiliary_rules: [],
        rationale: '按小问拆解。',
        confidence: 0.91,
        content_hash: 'b'.repeat(64),
        version_id: 'c'.repeat(64),
        whole_question_classification: {
          direct_fine_terms: [
            { fine_term_id: 'term-direct', fine_term_name: '一元二次方程求根' },
          ],
          supporting_prerequisite_fine_terms: [],
          direct_resolved_core_node_ids: ['kp_equation'],
          supporting_resolved_core_node_ids: [],
          direct_ambiguous_core_node_ids: [],
          supporting_ambiguous_core_node_ids: [],
          direct_unmapped_fine_term_ids: [],
          supporting_unmapped_fine_term_ids: [],
          resolved_core_node_ids: ['kp_equation'],
          ambiguous_core_node_ids: [],
          unmapped_fine_term_ids: [],
        },
      },
    })
    const host = await mountReview()

    await vi.waitFor(() => {
      expect(host.querySelector('.solution-evidence.is-embedded')).not.toBeNull()
    })
    expect(host.querySelectorAll('.criterion-review').length).toBe(1)
    expect(host.querySelector('#criterion-review-title')?.textContent).toBe('判定点')
    expect(host.textContent).toContain('知识细项与图谱映射')
    expect(host.textContent).toContain('完成方程求解')
    expect(host.textContent).not.toContain('拆分点、精细词条')
    expect(host.textContent).not.toContain('解题证据')
  })
})
