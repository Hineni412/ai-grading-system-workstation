import { createApp, h } from 'vue';
import { afterEach, describe, expect, it } from 'vitest';

import type { QuestionBankRichBlock } from '../api/question-bank';
import QuestionContentRenderer from '../components/question-bank/QuestionContentRenderer.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

function imageBlock(text: string, index: number): QuestionBankRichBlock {
  return {
    kind: 'paragraph',
    text,
    segments: [],
    rows: [],
    asset_indexes: [index],
    asset_urls: [`/api/question-bank/questions/17/assets/${index}`],
  }
}

function textBlock(text: string): QuestionBankRichBlock {
  return {
    kind: 'paragraph',
    text,
    segments: [],
    rows: [],
    asset_indexes: [],
    asset_urls: [],
  }
}

function mountCompactRenderer(
  compactMediaWithText: boolean | undefined,
  blocks: QuestionBankRichBlock[],
): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    render: () => h(QuestionContentRenderer, {
      blocks,
      paperMediaFlow: true,
      compactMediaWithText,
    }),
  })
  app.mount(host)
  mounted.push(app)
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('question content renderer option grid', () => {
  function mountBlocks(
    blocks: QuestionBankRichBlock[],
    paperMediaFlow = false,
  ): HTMLElement {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      render: () => h(QuestionContentRenderer, { blocks, paperMediaFlow }),
    })
    app.mount(host)
    mounted.push(app)
    return host
  }

  it('keeps four option images in one equal grid instead of wrapping', () => {
    const host = mountBlocks([
      textBlock('A. B. C. D.'),
      imageBlock('A', 0),
      imageBlock('B', 1),
      imageBlock('C', 2),
      imageBlock('D', 3),
    ])
    const grid = host.querySelector<HTMLElement>('.question-content__option-grid')

    expect(grid).not.toBeNull()
    expect(grid!.getAttribute('data-option-count')).toBe('4')
    expect(grid!.querySelectorAll('img')).toHaveLength(4)
    expect(host.querySelector('.question-content__media-strip')).toBeNull()
  })

})

describe('question content renderer compact media with text', () => {

  it('pairs images that are inline inside a long text block when compacting', () => {
    const host = mountCompactRenderer(true, [
      {
        kind: 'paragraph',
        text: '（2）猜想y与x的数量关系，并说明理由，这一段文字足够长，超过三十二个字符的限制。',
        segments: [],
        rows: [],
        asset_indexes: [0, 1],
        asset_urls: [
          '/api/question-bank/questions/23/assets/0',
          '/api/question-bank/questions/23/assets/1',
        ],
      },
    ])

    const block = host.querySelector<HTMLElement>('.question-content__block--inline-media-paired')
    expect(block).not.toBeNull()
    expect(block!.querySelectorAll('img')).toHaveLength(2)
  })

})
