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
  it('keeps passed questions and exceptions fully expanded', async () => {
    const mounted = await mountReview({
      questionStates: [
        { question_id: 'Q1', state: 'passed', reason: '', retryable: false },
        { question_id: 'Q2', state: 'blocked', reason: 'local_validation', retryable: true },
      ],
    })
    const q1 = mounted.host.querySelector<HTMLElement>('[data-question-row="Q1"]')!
    const q2 = mounted.host.querySelector<HTMLElement>('[data-question-row="Q2"]')!
    expect(q1.querySelector('.question-review__dot')?.className).toContain('is-passed')
    expect(q1.querySelector('.question-review__row-button')?.getAttribute('aria-selected')).toBe('true')
    expect(mounted.host.querySelector('[data-question-panel="Q1"]'))
      .not.toBeNull()
    expect(q2.classList.contains('is-exception')).toBe(true)
    q2.querySelector<HTMLButtonElement>('.question-review__row-button')!.click()
    await nextTick()
    expect(mounted.host.querySelector('[data-question-panel="Q2"]'))
      .not.toBeNull()
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
    // Decisions survive a remount while the source revision is unchanged —
    // the store owns clearing on source replacement; only a live revision
    // change still clears.
    expect(onUpdate).not.toHaveBeenCalled()
    mounted.state.value = source({ source_revision: 'f'.repeat(64) })
    await nextTick()
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
    expect(mounted.host.textContent).not.toContain('本地判为')
    expect(mounted.host.querySelector('.question-review__type-check select')).toBeNull()
    expect(
      mounted.host.querySelector('.question-review__detail-head .question-review__type-chip')
        ?.textContent,
    ).toContain('essay-from-parser')
  })

  it('asks for type confirmation only when the source reports a conflict', async () => {
    const onUpdate = vi.fn()
    const value = source({
      questions: [{
        ...source().questions[1]!,
        question_id: 'Q9',
        question_type: 'fill_blank',
        question_type_review_required: true,
        question_type_review_reason: '检测到下一部分标题可能粘在本题末尾，请确认题型。',
      }],
    })
    const mounted = await mountReview({ value, onUpdate })
    const select = mounted.host.querySelector<HTMLSelectElement>('.question-review__type-check select')!

    expect(mounted.host.textContent).toContain('请确认题型')
    expect(mounted.host.textContent).toContain('下一部分标题')
    select.value = 'fill_blank'
    select.dispatchEvent(new Event('change'))

    expect(onUpdate).toHaveBeenLastCalledWith([
      { question_id: 'Q9', excluded: false, question_type: 'fill_blank' },
    ])
  })

  it('shows the local type guess and keeps mixed blank-and-subpart questions for confirmation', async () => {
    const value = source({
      questions: [{
        ...source().questions[2]!,
        question_id: 'Q14',
        question_type: 'comprehensive',
        question_type_basis: '题面有填空位置 · 题面有多个小问',
        question_type_review_required: true,
        question_type_review_reason: '题面既有多个小问，也有填空位置。本地按综合解答题处理，请确认题型。',
      }],
    })
    const mounted = await mountReview({
      value,
      decisions: [{ question_id: 'Q14', excluded: false, question_type: 'comprehensive' }],
    })

    expect(mounted.host.textContent).toContain('您已确认为综合解答题')
    expect(mounted.host.textContent).toContain('请确认题型')
    expect(mounted.host.querySelector('.question-review__type-check')).not.toBeNull()
  })

  it('tells the teacher the unconfirmed local type on ordinary questions', async () => {
    const mounted = await mountReview({
      value: source({
        questions: [{
          ...source().questions[1]!,
          question_type: 'fill_blank',
          question_type_basis: '题面有填空位置',
        }],
      }),
    })

    expect(
      mounted.host.querySelector('.question-review__detail-head .question-review__type-chip')
        ?.getAttribute('title'),
    ).toContain('本地判为填空题（题面有填空位置 · 未经您确认）')
  })

  it('shows answer facts and builds assets only from semantic identifiers', async () => {
    const mounted = await mountReview({ value: source({
      safe_filename: '七年级数学.docx',
      suffix: '.docx',
    }) })
    expect(mounted.host.textContent).toContain('答案已匹配')
    const images = [...mounted.host.querySelectorAll<HTMLImageElement>('img')]
    expect(images.map((image) => [image.alt, image.getAttribute('src')])).toEqual([
      ['Q1 题目图 1', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/question`],
      ['Q1 答案图 1', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/answer`],
    ])
    expect(images.every((image) => !image.src.includes('path='))).toBe(true)
    mounted.host.querySelector<HTMLButtonElement>('[data-question-row="Q2"] .question-review__row-button')!
      .click()
    await nextTick()
    expect(
      mounted.host.querySelector('[data-answer-panel="Q2"] .question-review__answer-status')
        ?.textContent,
    ).toContain('未识别到答案')
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

    expect(mounted.host.querySelectorAll('.question-review__list .question-review__asset-between')).toHaveLength(1)
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

  it('offers a restore entry for ignored ambiguous images and returns them to the reminder', async () => {
    const assetBase = `/api/sessions/7/config/sources/${'a'.repeat(32)}/ambiguous-assets`
    const reviewSource = source({
      suffix: '.docx',
      safe_filename: '七年级数学.docx',
      ambiguous_assets: [
        { candidate_id: 'A1', previous_question_id: 'Q1', next_question_id: 'Q2',
          source_section: 'question', asset_url: `${assetBase}/A1` },
        { candidate_id: 'A2', previous_question_id: 'Q2', next_question_id: 'Q3',
          source_section: 'question', asset_url: `${assetBase}/A2` },
      ],
    })
    const host = document.createElement('div')
    document.body.append(host)
    const state = reactive({
      value: reviewSource,
      assetDecisions: [
        { candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question' },
        { candidate_id: 'A2', action: 'ignore' },
      ] as ConfigAmbiguousAssetDecision[],
    })
    const app = createApp({
      components: { QuestionBlockReview },
      setup: () => ({ state }),
      template: '<QuestionBlockReview :source="state.value" :asset-decisions="state.assetDecisions" @update:asset-decisions="state.assetDecisions = $event" />',
    })
    app.mount(host)
    await nextTick()

    expect(host.querySelectorAll('.question-review__asset-between')).toHaveLength(0)
    const toggle = host.querySelector<HTMLButtonElement>('.question-review__ignored-toggle')!
    expect(toggle.textContent).toContain('已忽略 1 张')
    expect(host.querySelector('.question-review__ignored-list')).toBeNull()

    toggle.click()
    await nextTick()
    const ignoredItems = [...host.querySelectorAll<HTMLElement>('.question-review__ignored-item')]
    expect(ignoredItems).toHaveLength(1)
    expect(ignoredItems[0]!.textContent).toContain('疑难图片 · Q2 / Q3')
    expect(ignoredItems[0]!.textContent).toContain('已忽略')

    const restore = ignoredItems[0]!.querySelector<HTMLButtonElement>('.question-review__restore-button')!
    restore.click()
    await nextTick()

    expect(state.assetDecisions).toEqual([
      { candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question' },
    ])
    expect(host.querySelector('.question-review__ignored-tray')).toBeNull()
    const reminders = [...host.querySelectorAll<HTMLElement>('.question-review__list .question-review__asset-between')]
    expect(reminders).toHaveLength(1)
    expect(reminders[0]!.textContent).toContain('疑难图片 · Q2 / Q3')
    expect(reminders[0]!.textContent).toContain('待归属')

    app.unmount()
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

  it('keeps the full question beside a clamped full answer that expands in place', async () => {
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
    const answerContent = mounted.host.querySelector<HTMLElement>('[data-answer-content="Q1"]')!
    expect(answerContent.textContent).toContain('结论成立')
    expect(answerContent.textContent).toContain(completeSolution)
    expect(answerContent.classList).toContain('is-clamped')
    expect(mounted.host.querySelector('[data-question-panel="Q1"]')).not.toBeNull()
    expect(mounted.host.querySelector('[data-answer-panel="Q1"]')).not.toBeNull()
    const expand = [...mounted.host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '展开')!
    expand.click()
    await nextTick()
    expect(answerContent.classList).not.toContain('is-clamped')
    const open = [...mounted.host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('查看完整答案'))!
    open.click()
    await nextTick()
    expect(document.body.textContent).toContain(completeSolution)

    mounted.state.value = source({
      ...reviewSource,
      source_revision: 'd'.repeat(64),
    })
    await nextTick()

    expect(onUpdate).toHaveBeenLastCalledWith([])
    expect(mounted.host.textContent).toContain('查看完整答案')
  })

  it('tags bank duplicates, hides variant, filters them, and opens the compare dialog', async () => {
    const mounted = await mountReview({
      duplicates: [
        { question_id: 'Q1', kind: 'exact_reusable', matched_question_id: 41,
          matched_paper_title: '八上月考卷', matched_question_number: '3',
          similarity: 1, matched_question_excerpt: '题库中的相同题干节选',
          reason: '题库已有相同题目，可复用分析' },
        { question_id: 'Q2', kind: 'answer_conflict', matched_question_id: 42,
          matched_paper_title: '八上期中卷', matched_question_number: '7',
          similarity: 1, matched_question_excerpt: '相同题干但答案不同',
          bank_answer_text: 'C', suggested_answer_override: 'C',
          reason: '题库答案与本卷不一致' },
        { question_id: 'Q3', kind: 'same_session', matched_question_id: 43,
          matched_paper_title: '本场上次导入', matched_question_number: '1',
          similarity: 1, matched_question_excerpt: '', reason: '本卷此前已入库' },
        { question_id: 'Q1', kind: 'variant', matched_question_id: 44,
          matched_paper_title: '变式卷', matched_question_number: '9',
          similarity: 0.8, matched_question_excerpt: '变式节选', reason: '疑似变式' },
      ],
    })

    // same_session and variant show nothing; Q1 keeps the informational bank tag.
    const q1Row = mounted.host.querySelector<HTMLElement>('[data-question-row="Q1"]')!
    expect(q1Row.querySelector('.question-review__flag')?.textContent).toBe('题库已有')
    expect(mounted.host.textContent).not.toContain('变式')
    const q3Row = mounted.host.querySelector<HTMLElement>('[data-question-row="Q3"]')!
    expect(q3Row.querySelector('.question-review__flag')).toBeNull()

    // answer_conflict counts toward 需核对；exact_reusable does not.
    const chips = [...mounted.host.querySelectorAll<HTMLButtonElement>(
      '.question-review__filters button',
    )].map((button) => button.textContent?.trim())
    expect(chips).toContain('需核对 1')
    expect(chips).toContain('题库已有 1')

    // 对照 opens the modal with header, counter and answer side-by-side.
    const card = mounted.host.querySelector<HTMLElement>('[data-question-card="Q2"]')!
    const dupLine = card.querySelector<HTMLElement>('.question-review__dup-line')!
    expect(dupLine.textContent).toContain('《八上期中卷》第7题')
    expect(dupLine.textContent).toContain('答案与题库不同')
    card.querySelector<HTMLButtonElement>('.question-review__dup-compare')!.click()
    await nextTick()
    const dialog = document.body.querySelector<HTMLElement>('[data-testid="dup-compare-dialog"]')!
    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toContain('《八上期中卷》第7题')
    expect(dialog.textContent).toContain('2/2')
    expect(dialog.textContent).toContain('本卷答案')
    expect(dialog.textContent).toContain('题库答案')
    expect(dialog.textContent).toContain('本卷答案正确')
    expect(dialog.textContent).toContain('题库答案正确')

    // Arrow keys navigate; ← stays on the first item boundary.
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    await nextTick()
    expect(document.body.querySelector('[data-testid="dup-compare-dialog"]')!.textContent)
      .toContain('1/2')
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await nextTick()
    expect(document.body.querySelector('[data-testid="dup-compare-dialog"]')!.textContent)
      .toContain('2/2')

    // Esc closes (the node lingers for the exit animation in jsdom).
    document.body.querySelector<HTMLElement>('[data-testid="dup-compare-dialog"]')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    await nextTick()
    const closedDialog = document.body.querySelector('[data-testid="dup-compare-dialog"]')
    expect(closedDialog === null
      || closedDialog.getAttribute('data-state') === 'closed').toBe(true)

    // 题库已有 filter keeps only exact matches.
    const bankChip = [...mounted.host.querySelectorAll<HTMLButtonElement>(
      '.question-review__filters button',
    )].find((button) => button.textContent?.includes('题库已有'))!
    bankChip.click()
    await nextTick()
    const rows = mounted.host.querySelectorAll('.question-review__list > .question-review__row')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.getAttribute('data-question-row')).toBe('Q1')
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

  it('counts undecided conflict questions toward 需核对 and un-counts them after a decision', async () => {
    const decisionsRef = reactive<QuestionDecision[]>([])
    const onUpdate = vi.fn((next: QuestionDecision[]) => {
      decisionsRef.splice(0, decisionsRef.length, ...next)
    })
    const mounted = await mountReview({
      decisions: decisionsRef,
      onUpdate,
      duplicates: [
        { question_id: 'Q2', kind: 'answer_conflict', matched_question_id: 42,
          matched_paper_title: '八上期中卷', matched_question_number: '7',
          similarity: 1, matched_question_excerpt: '相同题干',
          bank_answer_text: 'C', suggested_answer_override: 'C',
          reason: '题库答案与本卷不一致' },
      ],
    })
    const chipText = () => [...mounted.host.querySelectorAll<HTMLButtonElement>(
      '.question-review__filters button',
    )].map((button) => button.textContent?.trim())
    expect(chipText()).toContain('需核对 1')

    mounted.host.querySelector<HTMLElement>('[data-question-card="Q2"]')!
      .querySelector<HTMLButtonElement>('.question-review__dup-compare')!.click()
    await nextTick()
    const dialog = document.body.querySelector<HTMLElement>('[data-testid="dup-compare-dialog"]')!
    const bankButton = [...dialog.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '题库答案正确')!
    bankButton.click()
    await nextTick()

    expect(onUpdate).toHaveBeenCalledWith([
      { question_id: 'Q2', excluded: false, answer_confirmed: true, answer_override: 'C' },
    ])
    expect(mounted.host.textContent).toContain('已改用题库答案')
    expect(chipText()).not.toContain('需核对 1')
  })

  it('shows a non-blocking notice when the duplicate check is unavailable', async () => {
    const mounted = await mountReview({ duplicatesUnavailable: true })
    expect(mounted.host.textContent).toContain('查重暂不可用')
    expect(mounted.host.querySelectorAll('.question-review__list > .question-review__row'))
      .toHaveLength(3)
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
