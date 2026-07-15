import { createApp, nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConfigSource, QuestionDecision } from '../../../api/config-workspace'
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
  onUpdate?: (value: QuestionDecision[]) => void
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const state = reactive({ value: options.value ?? source() })
  const onUpdate = options.onUpdate ?? vi.fn()
  const app = createApp({
    components: { QuestionBlockReview },
    setup: () => ({ state, onUpdate, decisions: options.decisions ?? [] }),
    template: '<QuestionBlockReview :source="state.value" :decisions="decisions" @update:decisions="onUpdate" />',
  })
  app.mount(host)
  await nextTick()
  return { host, state, onUpdate, unmount: () => app.unmount() }
}

function change(element: HTMLInputElement | HTMLSelectElement, value: string | boolean): void {
  if (element instanceof HTMLInputElement) element.checked = Boolean(value)
  else element.value = String(value)
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(() => { document.body.innerHTML = '' })

describe('QuestionBlockReview', () => {
  it('emits only type and exclusion decisions for known questions', async () => {
    const onUpdate = vi.fn()
    const mounted = await mountReview({
      decisions: [
        { question_id: 'missing', question_type: 'proof', excluded: true },
        { question_id: 'Q1', question_type: 'calculation', excluded: false },
      ],
      onUpdate,
    })
    change(mounted.host.querySelector<HTMLSelectElement>('[aria-label="Q2 题型"]')!, 'proof')
    await nextTick()
    change(mounted.host.querySelector<HTMLInputElement>('[aria-label="排除 Q3"]')!, true)
    await nextTick()

    expect(onUpdate).toHaveBeenLastCalledWith([
      { question_id: 'Q2', question_type: 'proof', excluded: false },
      { question_id: 'Q3', question_type: 'comprehensive', excluded: true },
    ])
  })

  it('shows answer facts and builds assets only from semantic identifiers', async () => {
    const mounted = await mountReview()
    expect(mounted.host.textContent).toContain('已识别答案')
    expect(mounted.host.textContent).toContain('未识别答案')
    const images = [...mounted.host.querySelectorAll<HTMLImageElement>('img')]
    expect(images.map((image) => [image.alt, image.getAttribute('src')])).toEqual([
      ['Q1 题目图', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/question`],
      ['Q1 答案图', `/api/sessions/7/config/sources/${'a'.repeat(32)}/questions/Q1/assets/answer`],
    ])
    expect(images.every((image) => !image.src.includes('path='))).toBe(true)
  })

  it('expands long content with keyboard-labelled controls and resets on source revision', async () => {
    const onUpdate = vi.fn()
    const mounted = await mountReview({ onUpdate })
    const expand = mounted.host.querySelector<HTMLButtonElement>('[aria-label="展开 Q1 题目"]')!
    expect(expand.getAttribute('aria-expanded')).toBe('false')
    expect(mounted.host.querySelector('[data-question-content="Q1"]')?.classList).toContain('question-review__preview--clamped')
    expand.click()
    await nextTick()
    expect(expand.getAttribute('aria-expanded')).toBe('true')
    expect(expand.textContent).toContain('收起题目')

    change(mounted.host.querySelector<HTMLSelectElement>('[aria-label="Q2 题型"]')!, 'proof')
    await nextTick()
    mounted.state.value = source({ source_revision: 'd'.repeat(64) })
    await nextTick()

    expect(onUpdate).toHaveBeenLastCalledWith([])
    expect(mounted.host.querySelector<HTMLSelectElement>('[aria-label="Q2 题型"]')?.value).toBe('calculation')
    expect(mounted.host.querySelector<HTMLButtonElement>('[aria-label="展开 Q1 题目"]')?.getAttribute('aria-expanded')).toBe('false')
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
