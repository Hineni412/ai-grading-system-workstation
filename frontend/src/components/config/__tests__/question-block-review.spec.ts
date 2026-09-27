import { createApp, nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const questionBankApiMock = vi.hoisted(() => ({
  getQuestion: vi.fn(),
}))
vi.mock('../../../api/question-bank', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../api/question-bank')>()
  return { ...original, questionBankApi: questionBankApiMock }
})

import type {
  ConfigAmbiguousAssetDecision,
  ConfigQuestionGenerationState,
  ConfigSource,
  ConfigSourceDuplicateItem,
  QuestionDecision,
} from '../../../api/config-workspace'
import type { QuestionBankDetail } from '../../../api/question-bank'
import QuestionBlockReview from '../QuestionBlockReview.vue'

function source(overrides: Partial<ConfigSource> = {}): ConfigSource {
  const longText = '这是一段用于核对拆题结果的很长题目内容。'.repeat(16)
  return {
    session_id: 7,
    source_id: 'a'.repeat(32),
    source_revision: 'b'.repeat(64),
    safe_filename: '七年级数学.pdf', suffix: '.pdf', size_bytes: 4096,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready',
    questions: [
      { question_id: 'Q1', question_type: 'calculation', question_preview: longText,
        answer_preview: '答案一', answer_present: true, needs_review: true,
        local_answer_trusted: true, has_question_asset: true, has_answer_asset: true },
      { question_id: 'Q2', question_type: 'calculation', question_preview: '第二题',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
      { question_id: 'Q3', question_type: 'comprehensive', question_preview: '第三题',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
    ],
    ...overrides,
  }
}

async function mountReview(options: {
  value?: ConfigSource
  decisions?: QuestionDecision[]
  assetDecisions?: ConfigAmbiguousAssetDecision[]
  onUpdate?: (value: QuestionDecision[]) => void
  onAssetUpdate?: (value: ConfigAmbiguousAssetDecision[]) => void
  questionStates?: ConfigQuestionGenerationState[]
  duplicates?: ConfigSourceDuplicateItem[]
  duplicatesUnavailable?: boolean
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const state = reactive({ value: options.value ?? source() })
  const onUpdate = options.onUpdate ?? vi.fn()
  const onAssetUpdate = options.onAssetUpdate ?? vi.fn()
  const app = createApp({
    components: { QuestionBlockReview },
    setup: () => ({
      state, onUpdate, onAssetUpdate,
      decisions: options.decisions ?? [],
      assetDecisions: options.assetDecisions ?? [],
      questionStates: options.questionStates ?? [],
      duplicates: options.duplicates ?? [],
      duplicatesUnavailable: options.duplicatesUnavailable ?? false,
    }),
    template: '<QuestionBlockReview :source="state.value" :decisions="decisions" :asset-decisions="assetDecisions" :question-states="questionStates" :duplicates="duplicates" :duplicates-unavailable="duplicatesUnavailable" @update:decisions="onUpdate" @update:asset-decisions="onAssetUpdate" />',
  })
  app.mount(host)
  await nextTick()
  return { host, state, onUpdate, onAssetUpdate, unmount: () => app.unmount() }
}

beforeEach(() => {
  document.body.innerHTML = ''
  questionBankApiMock.getQuestion.mockReset()
  questionBankApiMock.getQuestion.mockResolvedValue({
    id: 42,
    question_text: '题库题干全文',
    answer_text: 'C',
    rich_content: {
      available: true, question_block_count: 0, answer_block_count: 0,
      question_blocks: [], answer_blocks: [],
    },
    assets: [], previews: [], tags: [],
  } as unknown as QuestionBankDetail)
})

describe('QuestionBlockReview', () => {

  it('removes resolved ambiguous images from the reminder and shows bound ones only under the target question', async () => {
    const assetBase = `/api/sessions/7/config/sources/${'a'.repeat(32)}/ambiguous-assets`
    const reviewSource = source({
      suffix: '.docx',
      safe_filename: '七年级数学.docx',
      ambiguous_assets: [
        { candidate_id: 'A1', previous_question_id: 'Q1', next_question_id: 'Q2',
          source_section: 'question', asset_url: `${assetBase}/A1` },
        { candidate_id: 'A2', previous_question_id: 'Q1', next_question_id: 'Q2',
          source_section: 'question', asset_url: `${assetBase}/A2` },
        { candidate_id: 'A3', previous_question_id: 'Q2', next_question_id: 'Q3',
          source_section: 'question', asset_url: `${assetBase}/A3` },
      ],
    })
    const mounted = await mountReview({
      value: reviewSource,
      assetDecisions: [
        { candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question' },
        { candidate_id: 'A2', action: 'ignore' },
      ],
    })

    const reminders = [...mounted.host.querySelectorAll<HTMLElement>('.question-review__list .question-review__asset-between')]
    expect(reminders).toHaveLength(1)
    expect(reminders[0]!.textContent).toContain('疑难图片 · Q2 / Q3')
    expect(reminders[0]!.textContent).toContain('待归属')

    mounted.host.querySelector<HTMLButtonElement>('[data-question-row="Q2"] .question-review__row-button')!
      .click()
    await nextTick()
    const bound = mounted.host.querySelectorAll('[data-asset-id="A1"]')
    expect(bound).toHaveLength(1)
    expect(mounted.host.querySelector('[data-question-panel="Q2"] .question-review__image-well [data-asset-id="A1"]'))
      .not.toBeNull()
    expect(mounted.host.querySelectorAll('[data-asset-id="A2"]')).toHaveLength(0)
  })

  it('lets a teacher drag an automatically assigned image into an answer panel', async () => {
    const onAssetUpdate = vi.fn()
    const base = source({ suffix: '.docx', safe_filename: '七年级数学.docx' })
    const assetUrl = `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/question`
    const mounted = await mountReview({
      value: {
        ...base,
        assets: [{
          asset_id: 'P1',
          asset_url: assetUrl,
          assignment_state: 'automatic',
          question_id: 'Q1',
          asset_kind: 'question',
          candidate_question_ids: [],
        }],
      },
      onAssetUpdate,
    })
    const values = new Map<string, string>()
    const dataTransfer = {
      effectAllowed: 'none',
      setData: (type: string, value: string) => values.set(type, value),
      getData: (type: string) => values.get(type) ?? '',
    }
    const dragged = mounted.host.querySelector<HTMLImageElement>('[data-asset-id="P1"]')!
    const dragStart = new Event('dragstart', { bubbles: true })
    Object.defineProperty(dragStart, 'dataTransfer', { value: dataTransfer })
    dragged.dispatchEvent(dragStart)
    mounted.host.querySelector<HTMLButtonElement>('[data-question-row="Q2"] .question-review__row-button')!
      .click()
    await nextTick()
    const answerPanel = mounted.host.querySelector<HTMLElement>('[data-answer-panel="Q2"]')!
    const drop = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(drop, 'dataTransfer', { value: dataTransfer })
    answerPanel.dispatchEvent(drop)
    await nextTick()

    expect(onAssetUpdate).toHaveBeenLastCalledWith([{
      candidate_id: 'P1', action: 'bind', question_id: 'Q2', asset_kind: 'answer',
    }])
  })

  it('emits bank decisions from the dialog, relabels the tag, drops 需核对, and undoes', async () => {
    const decisionsRef = reactive<QuestionDecision[]>([])
    const onUpdate = vi.fn((next: QuestionDecision[]) => {
      decisionsRef.splice(0, decisionsRef.length, ...next)
    })
    const mounted = await mountReview({
      decisions: decisionsRef,
      onUpdate,
      duplicates: [
        { question_id: 'Q2', kind: 'suspected', matched_question_id: 42,
          matched_paper_title: '八上期中卷', matched_question_number: '7',
          similarity: 0.92, matched_question_excerpt: '相似题干节选',
          reason: '题干高度相似' },
      ],
    })

    mounted.host.querySelector<HTMLElement>('[data-question-card="Q2"]')!
      .querySelector<HTMLButtonElement>('.question-review__dup-compare')!.click()
    await nextTick()
    const dialog = document.body.querySelector<HTMLElement>('[data-testid="dup-compare-dialog"]')!
    expect(dialog.textContent).toContain('相似')
    expect(dialog.textContent).toContain('1/1')
    const sameButton = [...dialog.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '是同一题')!
    sameButton.click()
    await nextTick()

    expect(onUpdate).toHaveBeenCalledWith([
      { question_id: 'Q2', excluded: false, bank_match: 'same', bank_question_id: 42 },
    ])
    expect(dialog.querySelector('[data-testid="dup-compare-decided"]')!.textContent)
      .toContain('已确认同一题')
    expect(mounted.host.textContent).toContain('已确认同一题')

    // 撤销 removes the decision and restores the original tag.
    dialog.querySelector<HTMLButtonElement>('.dup-compare__undo')!.click()
    await nextTick()
    expect(onUpdate).toHaveBeenLastCalledWith([])
    expect(mounted.host.textContent).toContain('相似')
    expect(mounted.host.textContent).not.toContain('已确认同一题')
  })

})
