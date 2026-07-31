import { describe, expect, it } from 'vitest'

import type { QuickFragment } from '../api/support'
import { mergeQuickFragmentWithNeighbor } from '../quickFragments'

function fragments(): QuickFragment[] {
  return [
    { fragment_id: 'first', text: '第一段', suggested_kind: 'fact' },
    { fragment_id: 'second', text: '第二段', suggested_kind: 'unclassified' },
    { fragment_id: 'third', text: '第三段', suggested_kind: 'reported_statement' },
  ]
}

describe('mergeQuickFragmentWithNeighbor', () => {
  it('merges the first fragment into the next fragment without losing order', () => {
    const source = fragments()

    const merged = mergeQuickFragmentWithNeighbor(source, 0)

    expect(merged).toEqual([
      {
        fragment_id: 'second',
        text: '第一段\n第二段',
        suggested_kind: 'unclassified',
      },
      source[2],
    ])
    expect(source.map((fragment) => fragment.text)).toEqual([
      '第一段',
      '第二段',
      '第三段',
    ])
  })

  it('merges a middle or final fragment into the previous fragment', () => {
    expect(mergeQuickFragmentWithNeighbor(fragments(), 1)).toEqual([
      {
        fragment_id: 'first',
        text: '第一段\n第二段',
        suggested_kind: 'fact',
      },
      fragments()[2],
    ])
    expect(mergeQuickFragmentWithNeighbor(fragments(), 2)).toEqual([
      fragments()[0],
      {
        fragment_id: 'second',
        text: '第二段\n第三段',
        suggested_kind: 'unclassified',
      },
    ])
  })

  it('keeps all characters from both fragments', () => {
    const source = [
      { fragment_id: 'first', text: '  前段  ', suggested_kind: 'fact' },
      { fragment_id: 'second', text: '\t后段\n', suggested_kind: 'fact' },
    ]

    expect(mergeQuickFragmentWithNeighbor(source, 1)[0]!.text).toBe(
      '  前段  \n\t后段\n',
    )
  })
})
