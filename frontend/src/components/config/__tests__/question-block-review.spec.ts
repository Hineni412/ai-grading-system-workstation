import { createApp, nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConfigAmbiguousAssetDecision,
  ConfigQuestionGenerationState,
  ConfigSource,
  QuestionDecision,
} from '../../../api/config-workspace'
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
    }),
    template: '<QuestionBlockReview :source="state.value" :decisions="decisions" :asset-decisions="assetDecisions" :question-states="questionStates" @update:decisions="onUpdate" @update:asset-decisions="onAssetUpdate" />',
  })
  app.mount(host)
  await nextTick()
  return { host, state, onUpdate, onAssetUpdate, unmount: () => app.unmount() }
}

beforeEach(() => { document.body.innerHTML = '' })

describe('QuestionBlockReview', () => {
  it('keeps passed questions and exceptions fully expanded', async () => {
    const mounted = await mountReview({
      questionStates: [
        { question_id: 'Q1', state: 'passed', reason: '', retryable: false },
        { question_id: 'Q2', state: 'blocked', reason: 'local_validation', retryable: true },
      ],
    })
    const q1 = mounted.host.querySelector<HTMLElement>('[data-question-row="Q1"]')!
    const q2 = mounted.host.querySelector<HTMLElement>('[data-question-row="Q2"]')!
    expect(q1.classList.contains('is-collapsed')).toBe(false)
    expect(q1.querySelector('.question-review__pair')).not.toBeNull()
    expect(q2.classList.contains('is-exception')).toBe(true)
    expect(q2.querySelector('.question-review__pair')).not.toBeNull()
  })
  it('shows preview only without question exclusion controls', async () => {
    const onUpdate = vi.fn()
    const mounted = await mountReview({
      decisions: [
        { question_id: 'missing', excluded: true },
        { question_id: 'Q1', excluded: false },
      ],
      onUpdate,
    })
    expect(mounted.host.querySelector<HTMLInputElement>('[aria-label="排除 Q3"]')).toBeNull()
    expect(onUpdate).toHaveBeenLastCalledWith([])
    expect(mounted.host.querySelector('[aria-label="Q2 题型"]')).toBeNull()
    expect(mounted.host.textContent).not.toContain('确认用于生成')
    expect(mounted.host.textContent).not.toContain('排除此题')
  })

  it('does not expose or persist a local type guess when the parser is uncertain', async () => {
    const unknown = source({
      questions: [{
        ...source().questions[1]!,
        question_id: 'Q-unknown',
        question_type: 'essay-from-parser',
      }],
    })
    const mounted = await mountReview({ value: unknown })

    expect(mounted.host.querySelector('[aria-label="Q-unknown 题型"]')).toBeNull()
    expect(mounted.host.textContent).toContain('题型、小问和作答方式由 AI')
  })

  it('shows answer facts and builds assets only from semantic identifiers', async () => {
    const mounted = await mountReview({ value: source({
      safe_filename: '七年级数学.docx',
      suffix: '.docx',
    }) })
    expect(mounted.host.textContent).toContain('答案已匹配')
    expect(mounted.host.textContent).toContain('未识别到答案')
    const images = [...mounted.host.querySelectorAll<HTMLImageElement>('img')]
    expect(images.map((image) => [image.alt, image.getAttribute('src')])).toEqual([
      ['Q1 题目图 1', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/question`],
      ['Q1 答案图 1', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/answer`],
    ])
    expect(images.every((image) => !image.src.includes('path='))).toBe(true)
  })

  it('renders one adjacent-image candidate with only a reversible ignore control', async () => {
    const onAssetUpdate = vi.fn()
    const baseSource = source()
    const reviewSource = source({
      suffix: '.docx',
      safe_filename: '七年级数学.docx',
      questions: baseSource.questions.map((question) => question.question_id === 'Q2'
        ? { ...question, has_question_asset: true }
        : question),
      ambiguous_assets: [{
        candidate_id: 'A1',
        previous_question_id: 'Q1',
        next_question_id: 'Q2',
        source_section: 'question',
        asset_url: `/api/sessions/7/config/sources/${'a'.repeat(32)}/ambiguous-assets/A1`,
      }],
    })
    const mounted = await mountReview({ value: reviewSource, onAssetUpdate })
    const candidate = mounted.host.querySelector<HTMLElement>('.question-review__asset-between')!

    expect(mounted.host.querySelectorAll('.question-review__asset-between')).toHaveLength(1)
    expect(candidate.textContent).toContain('Q1 / Q2')
    const buttons = candidate.querySelectorAll<HTMLButtonElement>('button')
    expect(buttons).toHaveLength(1)
    expect(buttons[0]!.getAttribute('aria-label')).toContain('忽略这张图片')
    buttons[0]!.click()
    await nextTick()

    expect(onAssetUpdate).toHaveBeenLastCalledWith([{
      candidate_id: 'A1', action: 'ignore',
    }])
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
    const answerPanel = mounted.host.querySelector<HTMLElement>('[data-answer-panel="Q2"]')!
    const drop = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(drop, 'dataTransfer', { value: dataTransfer })
    answerPanel.dispatchEvent(drop)
    await nextTick()

    expect(onAssetUpdate).toHaveBeenLastCalledWith([{
      candidate_id: 'P1', action: 'bind', question_id: 'Q2', asset_kind: 'answer',
    }])
  })

  it('lets a keyboard user ignore an automatically assigned image', async () => {
    const onAssetUpdate = vi.fn()
    const base = source({ suffix: '.docx', safe_filename: '七年级数学.docx' })
    const mounted = await mountReview({
      value: {
        ...base,
        assets: [{
          asset_id: 'P1',
          asset_url: `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/question`,
          assignment_state: 'automatic',
          question_id: 'Q1',
          asset_kind: 'question',
          candidate_question_ids: [],
        }],
      },
      onAssetUpdate,
    })
    const ignore = mounted.host.querySelector<HTMLButtonElement>(
      '.question-review__placed-asset .question-review__ignore-button',
    )!
    expect(ignore.getAttribute('aria-label')).toContain('忽略这张图片')
    ignore.click()
    await nextTick()

    expect(onAssetUpdate).toHaveBeenLastCalledWith([{
      candidate_id: 'P1', action: 'ignore',
    }])
  })

  it('shows the complete question and answer side by side without an expand step', async () => {
    const onUpdate = vi.fn()
    const longQuestion = '完整题干不能折叠。'.repeat(30)
    const completeSolution = '完整解答第一步：由已知条件得到中间结论。'
    const reviewSource = source({
      safe_filename: '七年级数学.docx',
      suffix: '.docx',
      questions: [{
        ...source().questions[0]!,
        question_preview: longQuestion,
        answer_preview: '结论成立',
        rich_content: {
          available: true,
          question_block_count: 1,
          answer_block_count: 2,
          question_blocks: [{
            kind: 'paragraph', text: longQuestion,
            segments: [], rows: [], asset_indexes: [], asset_urls: [],
          }],
          answer_blocks: [
            {
              kind: 'paragraph', text: '结论成立',
              segments: [], rows: [], asset_indexes: [], asset_urls: [],
            },
            {
              kind: 'paragraph', text: completeSolution,
              segments: [], rows: [], asset_indexes: [], asset_urls: [],
            },
          ],
        },
      }],
    })
    const mounted = await mountReview({ value: reviewSource, onUpdate })
    expect(mounted.host.querySelector('[data-question-content="Q1"]')?.classList)
      .not.toContain('question-review__preview--clamped')
    expect(mounted.host.textContent).toContain(longQuestion)
    expect(mounted.host.textContent).toContain('结论成立')
    expect(mounted.host.textContent).toContain(completeSolution)
    expect(mounted.host.querySelector('[aria-label="展开 Q1 答案"]')).toBeNull()
    expect(mounted.host.querySelector('[data-question-panel="Q1"]')).not.toBeNull()
    expect(mounted.host.querySelector('[data-answer-panel="Q1"]')).not.toBeNull()
    expect(
      [...mounted.host.querySelectorAll('.question-content')]
        .every((element) => element.classList.contains('is-dense')),
    ).toBe(true)

    mounted.state.value = source({
      ...reviewSource,
      source_revision: 'd'.repeat(64),
    })
    await nextTick()

    expect(onUpdate).toHaveBeenLastCalledWith([])
    expect(mounted.host.querySelector('[aria-label="展开 Q1 答案"]')).toBeNull()
  })

  it('renders a continuous list and an explicit zero-question state', async () => {
    const mounted = await mountReview()
    expect(mounted.host.querySelectorAll('.question-review__list > .question-review__row')).toHaveLength(3)
    expect(mounted.host.querySelectorAll('.question-review__row .question-review__row')).toHaveLength(0)
    mounted.state.value = source({ questions: [] })
    await nextTick()
    expect(mounted.host.querySelector('[role="status"]')?.textContent).toContain('没有可核对的题目')
  })
})
