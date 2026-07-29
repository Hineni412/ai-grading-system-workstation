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
    expect(host.querySelectorAll('.question-content__block')).toHaveLength(4)
  })
})
