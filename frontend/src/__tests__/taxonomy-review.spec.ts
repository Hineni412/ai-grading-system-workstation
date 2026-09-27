import { createApp, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { decodeTaxonomySuggestionRun, questionBankTaxonomyApi, type TaxonomyProposal } from '../api/question-bank-taxonomy';
import { ApiError } from '../api/errors';

import TaxonomyCandidateReview from '../components/question-bank/TaxonomyCandidateReview.vue'
import { useJobStore } from '../stores/jobs';
import { useTaxonomyReviewStore } from '../stores/taxonomy-review';

const proposal: TaxonomyProposal = {
  id: 'proposal-1',
  dimension: 'curriculum',
  proposed_name: '相交线与平行线，三角形',
  edited_name: null,
  reason: '一道题同时涉及两个章节',
  nearest_id: 'term-1',
  why_not_reuse: null,
  question_refs: [482],
  active_question_refs: [482],
  unavailable_question_ref_count: 0,
  actionable: true,
  resolved_term_ids: [],
  status: 'pending',
  created_at: '2026-07-29T10:21:00Z',
  updated_at: '2026-07-29T10:21:00Z',
}

const suggestionRun = {
  run_id: 'run-1',
  status: 'completed',
  taxonomy_revision: 7,
  stale: false,
  retryable: false,
  created_at: '2026-07-29T10:22:00Z',
  updated_at: '2026-07-29T10:22:05Z',
  progress: {
    total: 1,
    completed: 1,
    failed: 0,
    pending: 0,
    cancelled: 0,
  },
  items: [{
    proposal_id: 'proposal-1',
    dimension: 'curriculum',
    proposed_name: '相交线与平行线，三角形',
    question_refs: [482],
    status: 'suggested',
    attempts: 1,
    suggestion: {
      relation_kind: 'related',
      target_term_ids: ['term-1', 'term-2'],
      reason: '题目确实同时考查两个章节。',
      confidence: 0.91,
      source: 'ai',
      legacy_format: false,
      evidence_question_ids: [482],
      taxonomy_revision: 7,
      graph_release_id: 'current-standard',
    },
    error: null,
  }],
}

beforeEach(() => {
  document.body.innerHTML = ''
  setActivePinia(createPinia())
})

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('taxonomy review', () => {

  it('shows a comparison table, checks merge-category rows by default, and saves checked merges once', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useTaxonomyReviewStore()
    store.revision = 7
    store.loadState = 'ready'
    store.suggestionRun = decodeTaxonomySuggestionRun(suggestionRun)
    store.batchPreview = {
      run_id: 'run-1',
      base_revision: 7,
      evidence_revision: 3,
      graph_release_id: 'current-standard',
      policy_version: 'taxonomy-batch-v1',
      policy_fingerprint: 'fingerprint-1',
      counts: { automatic: 1, manual: 1, total: 2 },
      items: [
        {
          proposal_id: 'proposal-manual',
          dimension: 'curriculum',
          proposed_name: '需要人工判断的词',
          question_ids: [482],
          automatic: false,
          reasons: ['relation_requires_teacher'],
          suggestion: {
            relation_kind: 'related',
            target_term_ids: ['term-1'],
            reason: '相关但并非严格同义。',
            confidence: 0.72,
            source: 'ai',
            legacy_format: false,
            evidence_question_ids: [482],
            taxonomy_revision: 7,
            graph_release_id: 'current-standard',
          },
        },
        {
          proposal_id: 'proposal-auto',
          dimension: 'curriculum',
          proposed_name: '可严格归并的词',
          question_ids: [483],
          automatic: true,
          reasons: [],
          suggestion: {
            relation_kind: 'exact',
            target_term_ids: ['term-1'],
            reason: '定义和适用边界相同。',
            confidence: 0.91,
            source: 'ai',
            legacy_format: false,
            evidence_question_ids: [483],
            taxonomy_revision: 7,
            graph_release_id: 'current-standard',
          },
        },
      ],
    }
    const saveSpy = vi.spyOn(store, 'applySuggestionBatch').mockResolvedValue(true)

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(TaxonomyCandidateReview, { open: true })
    app.use(pinia)
    app.mount(host)
    await nextTick()

    const results = document.body.querySelector('.taxonomy-ai-results')
    const table = results?.querySelector<HTMLTableElement>('.taxonomy-ai-results__table')
    const resultText = results?.textContent ?? ''
    expect(results?.textContent).toContain('归并类已按 AI 建议默认勾选')
    expect(table).not.toBeNull()
    expect(results?.querySelector('.taxonomy-ai-results__automatic')).toBeNull()
    expect(resultText).toContain('可严格归并的词')
    expect(resultText).toContain('需要人工判断的词')
    expect(resultText.indexOf('可严格归并的词')).toBeLessThan(
      resultText.indexOf('需要人工判断的词'),
    )
    const checkboxes = [...(table?.querySelectorAll<HTMLInputElement>('input[type="checkbox"]') ?? [])]
    expect(checkboxes).toHaveLength(2)
    expect(checkboxes[0]?.checked).toBe(true)
    expect(checkboxes[1]?.checked).toBe(true)
    expect(document.body.textContent).toContain('当前知识标准 · 词表修订 7')
    expect(document.body.textContent).not.toContain('启用知识图谱')

    const saveButton = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认已勾选'))
    saveButton?.click()
    await nextTick()
    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(saveSpy).toHaveBeenCalledWith([
      {
        proposal_id: 'proposal-manual',
        decision: 'merge',
        target_term_ids: ['term-1'],
      },
      {
        proposal_id: 'proposal-auto',
        decision: 'merge',
        target_term_ids: ['term-1'],
      },
    ])

    app.unmount()
  })

  it('shows a storage failure only on the submitted proposal and keeps its draft', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useTaxonomyReviewStore()
    const otherProposal: TaxonomyProposal = {
      ...proposal,
      id: 'proposal-2',
      proposed_name: '另一个待审词',
      nearest_id: 'term-2',
      question_refs: [483],
    }
    store.revision = 7
    store.proposals = [proposal, otherProposal]
    store.pendingCount = 2
    store.loadState = 'loading'
    store.dimensions.curriculum = [
      {
        id: 'term-1',
        dimension: 'curriculum',
        name: '七年级下册 第二章 相交线与平行线',
        aliases: [],
        status: 'active',
      },
      {
        id: 'term-2',
        dimension: 'curriculum',
        name: '七年级下册 第四章 三角形',
        aliases: [],
        status: 'active',
      },
    ]
    vi.spyOn(questionBankTaxonomyApi, 'reviewProposal').mockRejectedValue(
      new ApiError({
        kind: 'server',
        status: 503,
        code: 'taxonomy_storage_read_only',
        message: 'Taxonomy storage is read only',
        details: {},
        requestId: 'request-storage-read-only',
        retryable: true,
      }),
    )

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(TaxonomyCandidateReview, { open: true })
    app.use(pinia)
    app.mount(host)
    await nextTick()

    const cards = [...document.body.querySelectorAll<HTMLElement>('.taxonomy-candidate')]
    expect(cards).toHaveLength(2)
    cards[0]?.querySelector<HTMLButtonElement>(
      '.taxonomy-candidate__merge-actions .qb-button',
    )?.click()

    await vi.waitFor(() => {
      expect(cards[0]?.textContent).toContain('标签状态当前不能写入')
    })
    expect(cards[0]?.textContent).toContain('request-storage-read-only')
    expect(cards[1]?.textContent).not.toContain('标签状态当前不能写入')
    expect(
      cards[0]?.querySelector<HTMLInputElement>(
        '.taxonomy-candidate__term-options input[value="term-1"]',
      )?.checked,
    ).toBe(true)

    app.unmount()
  })

  it('reuses the same paid-request token after an ambiguous start response', async () => {
    const runId = 'a'.repeat(32)
    const restored = decodeTaxonomySuggestionRun({
      ...suggestionRun,
      run_id: runId,
    })
    const inputs: Array<{ request_token: string }> = []
    const startSuggestions = vi.fn(async (input) => {
      inputs.push(input)
      if (inputs.length === 1) {
        throw new ApiError({
          kind: 'network',
          status: null,
          code: 'network_error',
          message: '连接中断',
          details: {},
          requestId: '',
          retryable: true,
        })
      }
      return {
        job: {
          id: 92,
          job_type: 'taxonomy_suggestion',
          payload: { run_id: runId },
          result: {},
          status: 'queued',
          progress: 0,
          stage: '',
          detail: '',
          error: null,
          cancel_requested: false,
          created_at: '2026-07-29T10:22:00Z',
          started_at: null,
          updated_at: '2026-07-29T10:22:00Z',
          finished_at: null,
        },
        run: restored,
      }
    })
    const store = useTaxonomyReviewStore()
    store.revision = 7

    expect(await store.startSuggestions(['proposal-1'], {
      startSuggestions,
    } as never)).toBe(false)
    expect(store.suggestionMessage).toContain('同一请求')
    expect(await store.startSuggestions(['proposal-1'], {
      startSuggestions,
    } as never)).toBe(true)

    expect(inputs).toHaveLength(2)
    expect(inputs[0]?.request_token).toBe(inputs[1]?.request_token)
    expect(useJobStore().jobs[92]?.job_type).toBe('taxonomy_suggestion')
  })
})
