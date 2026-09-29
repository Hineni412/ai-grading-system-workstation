import { createApp, h, ref } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { QuestionBankRichBlock } from '../api/question-bank';
import QuestionContentRenderer from '../components/question-bank/QuestionContentRenderer.vue'
import QuestionHtmlBlock from '../components/question-bank/QuestionHtmlBlock.vue'

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

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('question html block rendering', () => {
  it('renders inline score text, nested radicals and delimited LaTeX, then refreshes edited content', async () => {
    const text = ref('答案 ±8；计算 √(1+√(4))；满足 $x\\geq 5$。')
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({ render: () => h(QuestionHtmlBlock, { text: text.value, inline: true }) })
    app.mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.katex')).toHaveLength(3))
    expect(host.querySelector('.question-html')?.tagName).toBe('SPAN')
    expect([...host.querySelectorAll<HTMLElement>('.qm')].map(node => node.dataset.latex?.trim()))
      .toEqual(['\\pm 8', '\\sqrt{1+\\sqrt{4}}', 'x\\geq 5'])
    text.value = '保留原文 <img src=x>；答案 \\frac{1}{2}。'
    await vi.waitFor(() => expect(host.querySelectorAll('.katex')).toHaveLength(1))
    expect(host.querySelector('img')).toBeNull()
    expect(host.textContent).toContain('<img src=x>')
  })

  it('keeps rich source formulas in a single-line summary without images or paragraph wrappers', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({ render: () => h(QuestionHtmlBlock, {
      inline: true, html: '<p>计算 <span class="qm" data-latex="\\frac{1}{3}">(1)/(3)</span><img src="test.png"></p>',
    }) })
    app.mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelector('.katex')).not.toBeNull())
    expect(host.querySelector('p,img')).toBeNull()
    expect(host.textContent).toContain('计算')
  })

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

  it('typesets radicals in segment-only blocks that lack backend qm markup', async () => {
    const host = mountBlocks([{
      kind: 'paragraph',
      text: '(8) √((2)/(45))÷(3)/(2)√(1(3)/(5))；',
      segments: [{
        text: '(8) √((2)/(45))÷(3)/(2)√(1(3)/(5))；',
        superscript: false,
        subscript: false,
        underline: false,
        line_break: false,
      }],
      rows: [],
      html: '',
      asset_indexes: [],
      asset_urls: [],
    }], true)
    await vi.waitFor(() => expect(host.querySelector('.qm .katex')).not.toBeNull())
    const latex = host.querySelector<HTMLElement>('.qm')?.dataset.latex ?? ''
    expect(latex).toContain('\\sqrt{(2)/(45)}')
    expect(latex).toContain('\\sqrt{1(3)/(5)}')
  })

  it('typesets superscript characters and parenthesised radicals in rubric text', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      render: () => h(QuestionHtmlBlock, {
        text: '展开 (√(2x))²；化简 2x−2√(2xy)+y；面积 x²',
        typesetText: true,
      }),
    })
    app.mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.katex')).toHaveLength(3))
    const latex = [...host.querySelectorAll<HTMLElement>('.qm')]
      .map(node => node.dataset.latex?.trim())
    expect(latex).toContain('(\\sqrt{2x})^{2}')
    expect(latex).toContain('2x-2\\sqrt{2xy}+y')
    expect(latex).toContain('x^{2}')
    expect(host.textContent).toContain('展开')
    expect(host.textContent).toContain('化简')
  })

  it('typesets stems whose formulas sit next to blank-fill underscores', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      render: () => h(QuestionHtmlBlock, {
        text: '比较大小：2√(3) ____ ____3√(2)',
        typesetText: true,
      }),
    })
    app.mount(host)
    mounted.push(app)
    await vi.waitFor(() => expect(host.querySelectorAll('.katex').length).toBeGreaterThan(0))
    const latex = host.querySelector<HTMLElement>('.qm')?.dataset.latex ?? ''
    expect(latex).toContain('2\\sqrt{3}')
    expect(latex).toContain('3\\sqrt{2}')
    expect(latex).toContain('\\underline{\\hspace{')
    expect(host.textContent).toContain('比较大小')
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

})
