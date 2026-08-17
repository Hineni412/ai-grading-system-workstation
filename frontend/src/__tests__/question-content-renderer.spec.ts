import { createApp, h } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import type { QuestionBankRichBlock } from '../api/question-bank'
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

function mountRenderer(paperMediaFlow: boolean): HTMLElement {
  const blocks: QuestionBankRichBlock[] = [
    imageBlock('A', 0),
    imageBlock('B', 1),
    imageBlock(
      '这是一段需要保持整行宽度的长题干，它带有说明图片，但不能和前面的选项缩略图挤在同一行。',
      2,
    ),
    {
      kind: 'table',
      text: '表格内容',
      segments: [],
      rows: [{
        cells: [{
          segments: [{
            text: '数据',
            superscript: false,
            subscript: false,
            underline: false,
            line_break: false,
          }],
        }],
      }],
      asset_indexes: [3],
      asset_urls: ['/api/question-bank/questions/17/assets/3'],
    },
  ]
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    render: () => h(QuestionContentRenderer, {
      blocks,
      paperMediaFlow,
    }),
  })
  app.mount(host)
  mounted.push(app)
  return host
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

describe('question content renderer paper media flow', () => {
  it('groups only consecutive short label and image blocks into a wrapping strip', () => {
    const host = mountRenderer(true)
    const strips = host.querySelectorAll<HTMLElement>('.question-content__media-strip')

    expect(strips).toHaveLength(1)
    expect(getComputedStyle(strips[0]!).display).toBe('flex')
    expect(strips[0]!.querySelectorAll('.question-content__block')).toHaveLength(2)
    expect(strips[0]!.textContent).toContain('A')
    expect(strips[0]!.textContent).toContain('B')
    expect(strips[0]!.textContent).not.toContain('这是一段需要保持整行宽度')
    expect(host.querySelectorAll('.question-content__full-block')).toHaveLength(2)
  })

  it('does not apply the paper-only grouping to ordinary previews', () => {
    const host = mountRenderer(false)

    expect(host.querySelector('.question-content__media-strip')).toBeNull()
    expect(host.querySelector('.question-content__option-grid')).toBeNull()
    expect(host.querySelectorAll('.question-content__block')).toHaveLength(4)
  })
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

  it('groups four unlabeled option images even in ordinary previews', () => {
    const host = mountBlocks([
      imageBlock('A', 0),
      imageBlock('B', 1),
      imageBlock('C', 2),
      imageBlock('D', 3),
    ], false)
    const grid = host.querySelector<HTMLElement>('.question-content__option-grid')

    expect(grid).not.toBeNull()
    expect(grid!.getAttribute('data-option-count')).toBe('4')
    expect(host.querySelector('.question-content__media-strip')).toBeNull()
  })

  it('does not treat two option images as a four-up grid', () => {
    const host = mountBlocks([
      imageBlock('A', 0),
      imageBlock('B', 1),
    ], true)

    expect(host.querySelector('.question-content__option-grid')).toBeNull()
    expect(host.querySelectorAll('.question-content__media-strip')).toHaveLength(1)
  })
})

describe('question content renderer compact media with text', () => {
  it('pairs a standalone media group with the preceding text block', () => {
    const host = mountCompactRenderer(true, [
      textBlock('如图，在△ABC中，∠ACB＝90°，点D在斜边AB上。'),
      imageBlock('图1', 0),
      textBlock('（1）求∠A的度数。'),
    ])

    const pairs = host.querySelectorAll<HTMLElement>('.question-content__pair')
    expect(pairs).toHaveLength(1)
    expect(pairs[0]!.querySelector('.question-content__pair-text')?.textContent).toContain('如图')
    expect(pairs[0]!.querySelector('.question-content__pair-media')?.textContent).toContain('图1')
    expect(host.querySelector('.question-content__media-strip')).toBeNull()
  })

  it('does not pair media with a block that already carries images', () => {
    const host = mountCompactRenderer(true, [
      imageBlock('题干带图', 0),
      imageBlock('图1', 1),
    ])

    expect(host.querySelector('.question-content__pair')).toBeNull()
    expect(host.querySelectorAll('.question-content__media-strip')).toHaveLength(1)
  })

  it('right-aligns standalone media when the question reserves answer space', () => {
    const host = mountCompactRenderer(false, [
      textBlock('（1）猜想y与x的数量关系，并说明理由。'),
      imageBlock('图1', 0),
    ])

    const strip = host.querySelector<HTMLElement>('.question-content__media-strip--right')
    expect(strip).not.toBeNull()
    expect(strip!.textContent).toContain('图1')
    expect(host.querySelector('.question-content__pair')).toBeNull()
  })

  it('keeps legacy strips when compactMediaWithText is not set', () => {
    const host = mountCompactRenderer(undefined, [
      textBlock('题干'),
      imageBlock('图1', 0),
    ])

    expect(host.querySelector('.question-content__pair')).toBeNull()
    expect(host.querySelector('.question-content__media-strip--right')).toBeNull()
    expect(host.querySelectorAll('.question-content__media-strip')).toHaveLength(1)
  })

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

  it('right-aligns inline images when the question reserves answer space', () => {
    const host = mountCompactRenderer(false, [
      {
        kind: 'paragraph',
        text: '（2）猜想y与x的数量关系，并说明理由，这一段文字足够长，超过三十二个字符的限制。',
        segments: [],
        rows: [],
        asset_indexes: [0],
        asset_urls: ['/api/question-bank/questions/23/assets/0'],
      },
    ])

    expect(host.querySelector('.question-content__block--inline-media-right')).not.toBeNull()
    expect(host.querySelector('.question-content__block--inline-media-paired')).toBeNull()
  })
})
