import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  decodeTaxonomySuggestionRun,
  questionBankTaxonomyApi,
  type TaxonomyProposal,
} from '../api/question-bank-taxonomy'
import { ApiError } from '../api/errors'
import { questionBankApi, type QuestionBankDetail } from '../api/question-bank'
import TaxonomyCandidateReview from '../components/question-bank/TaxonomyCandidateReview.vue'
import {
  TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
  TAXONOMY_SUGGESTION_RUN_STORAGE_KEY,
  useTaxonomyReviewStore,
} from '../stores/taxonomy-review'

const proposal: TaxonomyProposal = {
  id: 'proposal-1',
  dimension: 'curriculum',
  proposed_name: '相交线与平行线，三角形',
  edited_name: null,
  reason: '一道题同时涉及两个章节',
  nearest_id: 'term-1',
  why_not_reuse: null,
  question_refs: [482],
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
      decision: 'map_many',
      target_term_ids: ['term-1', 'term-2'],
      reason: '题目确实同时考查两个章节。',
      confidence: 0.91,
      source: 'ai',
    },
    error: null,
  }],
}

const detail: QuestionBankDetail = {
  id: 482,
  revision: 'a'.repeat(64),
  paper_id: 10,
  question_number: '12',
  question_type: '解答题',
  question_text: '证明两组三角形全等，并说明平行线关系。',
  answer_text: '由 ASA 可得全等。',
  difficulty: '6',
  typicality: null,
  reason: null,
  needs_review: false,
  has_images: false,
  needs_image_review: false,
  created_at: '2026-07-29T10:00:00Z',
  updated_at: '2026-07-29T10:00:00Z',
  paper_title: '0526test2',
  year: '2026',
  province: null,
  city: null,
  district: null,
  exam_type: '阶段练习',
  grade: '七年级',
  semester: '下学期',
  textbook_version: '北师大版',
  tags: [],
  asset_urls: [],
  page_range: null,
  assets: [],
  rich_content: {
    available: true,
    question_block_count: 1,
    answer_block_count: 1,
    question_blocks: [{
      text: '证明两组三角形全等，并说明平行线关系。',
      asset_indexes: [],
      asset_urls: [],
    }],
    answer_blocks: [{
      text: '由 ASA 可得全等。',
      asset_indexes: [],
      asset_urls: [],
    }],
  },
  previews: [],
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
  it('decodes multi-target suggestions without treating them as approvals', () => {
    const decoded = decodeTaxonomySuggestionRun(suggestionRun)

    expect(decoded.items[0]?.suggestion?.decision).toBe('map_many')
    expect(decoded.items[0]?.suggestion?.target_term_ids).toEqual([
      'term-1',
      'term-2',
    ])
    expect(decoded.stale).toBe(false)
  })

  it('sends an idempotency token when retrying unfinished suggestion items', async () => {
    const runId = 'f'.repeat(32)
    const requestToken = 'e'.repeat(32)
    const job = {
      id: 91,
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
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        job,
        run: { ...suggestionRun, run_id: runId },
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await questionBankTaxonomyApi.retrySuggestionRun(runId, {
      request_token: requestToken,
    })

    expect(String(fetchSpy.mock.calls[0]?.[0])).toContain(
      `/api/question-bank/taxonomy/suggestions/${runId}/retry`,
    )
    const init = fetchSpy.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual({
      request_token: requestToken,
    })
  })

  it('keeps merge open, approval collapsed, and previews a clicked question', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useTaxonomyReviewStore()
    store.revision = 7
    store.proposals = [proposal]
    store.pendingCount = 1
    store.loadState = 'ready'
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
    store.suggestionRun = decodeTaxonomySuggestionRun(suggestionRun)
    vi.spyOn(questionBankApi, 'getQuestion').mockResolvedValue(detail)

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(TaxonomyCandidateReview, { open: true })
    app.use(pinia)
    app.mount(host)
    await nextTick()

    const mergeDetails = document.body.querySelector<HTMLDetailsElement>(
      '.taxonomy-candidate__merge',
    )
    const approveDetails = document.body.querySelector<HTMLDetailsElement>(
      '.taxonomy-candidate__approve',
    )
    expect(mergeDetails?.open).toBe(true)
    expect(approveDetails?.open).toBe(false)

    const questionButton = document.body.querySelector<HTMLButtonElement>(
      '.taxonomy-question-link',
    )
    expect(questionButton?.textContent).toContain('#482')
    questionButton?.focus()
    questionButton?.click()
    await vi.waitFor(() => {
      expect(document.body.textContent).toContain(
        '证明两组三角形全等，并说明平行线关系。',
      )
    })
    expect(document.body.querySelector('.taxonomy-question-preview')).not.toBeNull()

    document.body.querySelector<HTMLButtonElement>(
      '.taxonomy-question-preview .qb-drawer-close',
    )?.click()
    await nextTick()
    expect(document.activeElement).toBe(questionButton)

    store.suggestionRun = decodeTaxonomySuggestionRun({
      ...suggestionRun,
      status: 'failed',
      retryable: true,
      progress: {
        total: 1,
        completed: 0,
        failed: 1,
        pending: 0,
        cancelled: 0,
      },
      items: [{
        ...suggestionRun.items[0],
        status: 'failed',
        suggestion: null,
        error: {
          category: 'model_response',
          message: 'AI 返回中缺少这个待审词，可单独重试。',
        },
      }],
    })
    await nextTick()
    expect(document.body.textContent).toContain(
      'AI 返回中缺少这个待审词，可单独重试。',
    )

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

  it.each([
    ['taxonomy_storage_busy', '另一个操作正在保存标签'],
    ['taxonomy_storage_invalid', '标签状态文件内容异常'],
    ['taxonomy_storage_unavailable', '标签状态暂时无法保存'],
  ])('maps %s to an actionable proposal error', async (code, expectedMessage) => {
    const store = useTaxonomyReviewStore()
    store.revision = 7

    const reviewed = await store.review('proposal-1', { decision: 'reject' }, {
      reviewProposal: vi.fn().mockRejectedValue(new ApiError({
        kind: 'server',
        status: 503,
        code,
        message: 'Backend detail',
        details: {},
        requestId: `request-${code}`,
        retryable: true,
      })),
    } as never)

    expect(reviewed).toBe(false)
    expect(store.proposalErrors['proposal-1']).toMatchObject({
      code,
      requestId: `request-${code}`,
    })
    expect(store.proposalErrors['proposal-1']?.message).toContain(expectedMessage)
  })

  it('restores a persisted suggestion run after the page store is recreated', async () => {
    const runId = 'f'.repeat(32)
    localStorage.setItem(TAXONOMY_SUGGESTION_RUN_STORAGE_KEY, runId)
    localStorage.setItem(
      TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY,
      JSON.stringify({
        fingerprint: 'pending-start',
        requestToken: 'd'.repeat(32),
      }),
    )
    const restored = decodeTaxonomySuggestionRun({
      ...suggestionRun,
      run_id: runId,
    })
    const getSuggestionRun = vi.fn().mockResolvedValue(restored)
    const store = useTaxonomyReviewStore()
    const emptyDimensions = {
      curriculum: [],
      knowledge: [],
      ability: [],
      method: [],
      model: [],
      special_type: [],
    }

    await store.load({
      getCatalog: vi.fn().mockResolvedValue({
        revision: 7,
        dimensions: emptyDimensions,
      }),
      listProposals: vi.fn().mockResolvedValue({
        revision: 7,
        items: [proposal],
        counts: { pending: 1 },
      }),
      getSuggestionRun,
    } as never)

    expect(getSuggestionRun).toHaveBeenCalledWith(runId)
    expect(store.suggestionRun?.run_id).toBe(runId)
    expect(store.suggestionMessage).toContain('已恢复')
    expect(
      localStorage.getItem(TAXONOMY_SUGGESTION_COMMAND_STORAGE_KEY),
    ).toBeNull()
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
  })
})
