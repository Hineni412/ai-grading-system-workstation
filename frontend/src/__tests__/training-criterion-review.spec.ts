import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, type App } from 'vue';

import { ApiError } from '../api/errors';
import { questionBankApi } from '../api/question-bank';
import { questionBankCriteriaApi, type TrainingCriterionVersion, type TrainingCriterionWorkspace } from '../api/question-bank-criteria';
import TrainingCriterionReview from '../components/question-bank/TrainingCriterionReview.vue'
import ConfirmDialogHost from '../components/design-system/ConfirmDialogHost.vue'

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

function mountConfirmHost(): void {
  const el = document.createElement('div')
  document.body.append(el)
  const app = createApp(ConfirmDialogHost)
  app.mount(el)
  mounted.push(app)
}

function confirmDialog(): HTMLElement {
  const found = document.body.querySelector<HTMLElement>('[data-testid="app-confirm-dialog"]')
  if (!found) throw new Error('confirm dialog not open')
  return found
}

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

  it('requires confirmation before regenerating only the selected question', async () => {
    vi.spyOn(questionBankCriteriaApi, 'getWorkspace').mockResolvedValue(
      workspace(null),
    )
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
    mountConfirmHost()
    const regenerate = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('重新生成'))
    regenerate?.click()

    await vi.waitFor(() => {
      const dialog = confirmDialog()
      expect(dialog.textContent).toContain('为这道题重新生成判定点？')
      expect(dialog.textContent).toContain('可能调用已配置的 AI 服务并产生费用')
    })
    ;[...confirmDialog().querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '重新生成')!.click()

    await vi.waitFor(() => {
      expect(start).toHaveBeenCalledOnce()
      expect(host.textContent).toContain('完成后刷新这里')
    })
    expect(start.mock.calls[0]?.[0]).toEqual([17])
    expect(start.mock.calls[0]?.[2]).toBe('regenerate')
  })
})
