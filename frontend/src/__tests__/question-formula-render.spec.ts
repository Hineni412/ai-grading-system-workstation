import { createApp, h, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { QuestionBankRichBlock } from '../api/question-bank'
import QuestionContentRenderer from '../components/question-bank/QuestionContentRenderer.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

function htmlBlock(html: string): QuestionBankRichBlock {
  return {
    kind: 'paragraph',
    text: '计算：(1)/(3)',
    segments: [],
    rows: [],
    html,
    asset_indexes: [],
    asset_urls: [],
  }
}

function legacySegmentBlock(): QuestionBankRichBlock {
  return {
    kind: 'paragraph',
    text: '化简：x<sup>2</sup>',
    segments: [
      {
        text: '化简：x',
        superscript: false,
        subscript: false,
        underline: false,
        line_break: false,
      },
      {
        text: '2',
        superscript: true,
        subscript: false,
        underline: false,
        line_break: false,
      },
    ],
    rows: [],
    html: '',
    asset_indexes: [],
    asset_urls: [],
  }
}

function mountBlocks(blocks: QuestionBankRichBlock[], typesetText = false): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    render: () => h(QuestionContentRenderer, { blocks, typesetText }),
  })
  app.mount(host)
  mounted.push(app)
  return host
}

async function flushAsync(): Promise<void> {
  await nextTick()
  for (let index = 0; index < 5; index += 1) {
    // Dynamic import('katex') resolves over several microtask rounds.
    await new Promise((resolve) => {
      setTimeout(resolve, 0)
    })
    await nextTick()
  }
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('question html block rendering', () => {
  it('typesets ordinary Word superscripts for the assistant without changing prose or option labels', async () => {
    const host = mountBlocks([htmlBlock('2024-2025 学年。A. x<sup>2</sup>+6<sup>2</sup>=10<sup>2</sup>  B. 10<sup>2</sup>+6<sup>2</sup>=x<sup>2</sup>')], true)
    await vi.waitFor(() => expect(host.querySelectorAll('.qm .katex')).toHaveLength(2))
    expect(host.textContent).toContain('2024-2025 学年。A.')
    expect(host.textContent).toContain('B.')
    expect([...host.querySelectorAll<HTMLElement>('.qm')].map(item => item.dataset.latex?.trim())).toEqual(['x^{2}+6^{2}=10^{2}', '10^{2}+6^{2}=x^{2}'])
  })

  it('typesets legacy segments when enabled and keeps malformed formulas readable', async () => {
    const host = mountBlocks([legacySegmentBlock(), htmlBlock('公式 x^{ + 2 = 5')], true)
    await vi.waitFor(() => expect(host.querySelector('.qm .katex')).not.toBeNull())
    expect(host.textContent).toContain('公式 x^{ + 2 = 5')
  })
  it('hydrates formula spans with KaTeX', async () => {
    const host = mountBlocks([
      htmlBlock(
        '计算：<span class="qm" data-latex="\\frac{1}{3}">(1)/(3)</span>',
      ),
    ])

    await flushAsync()

    expect(host.querySelector('.qm .katex')).not.toBeNull()
  })

  it('keeps the linear fallback text when KaTeX cannot render', async () => {
    const host = mountBlocks([
      htmlBlock('计算：<span class="qm" data-latex="\\frac{1}">(1)/(3)</span>'),
    ])

    await flushAsync()

    expect(host.querySelector('.qm .katex')).toBeNull()
    expect(host.querySelector('.qm')?.textContent).toBe('(1)/(3)')
  })

  it('renders inline images and tables from projected html', () => {
    const host = mountBlocks([
      htmlBlock(
        '如图<img src="/api/question-bank/questions/7/assets/0" alt="题目图片">'
        + '<table><tbody><tr><td>方法一</td></tr></tbody></table>',
      ),
    ])

    expect(host.querySelector('.question-html img')).not.toBeNull()
    expect(host.querySelector('.question-html td')?.textContent).toBe('方法一')
    // HTML blocks never re-render their assets through the legacy media area.
    expect(host.querySelector('.question-content__media')).toBeNull()
  })

  it('does not apply option-grid heuristics to html blocks', () => {
    const optionImages = [
      'A.<img src="/api/question-bank/questions/7/assets/0" alt="题目图片">',
      'B.<img src="/api/question-bank/questions/7/assets/1" alt="题目图片">',
      'C.<img src="/api/question-bank/questions/7/assets/2" alt="题目图片">',
      'D.<img src="/api/question-bank/questions/7/assets/3" alt="题目图片">',
    ]
    const host = mountBlocks([
      htmlBlock('下列标志是轴对称图形的是（ ）'),
      ...optionImages.map((html) => htmlBlock(html)),
    ])

    expect(host.querySelector('.question-content__option-grid')).toBeNull()
    expect(host.querySelectorAll('.question-html img')).toHaveLength(4)
  })

  it('falls back to flat segments when html is empty', () => {
    const host = mountBlocks([legacySegmentBlock()])

    expect(host.querySelector('.question-html')).toBeNull()
    expect(host.querySelector('sup')?.textContent).toBe('2')
    expect(host.textContent).toContain('化简：x')
  })
})
