import { describe, expect, it } from 'vitest';

import { dedupeAcceptedAnswers, normalizeAnswerText } from '../utils/answer-dedupe';

describe('dedupeAcceptedAnswers', () => {
  it('drops standard-answer equivalents regardless of width or spacing', () => {
    // Q14(P8): 标准答案与四个标点/空白变体等价，只有 "1/9" 是真正不同的答案。
    const deduped = dedupeAcceptedAnswers(
      [
        'x＝（5＋3）/2',
        ' x = (5+3)/2 ',
        'x =（5+3）/2',
        'x ＝ (5+3)/2',
        '1/9',
      ],
      'x = (5+3)/2',
    )
    expect(deduped).toEqual(['1/9'])
    expect(dedupeAcceptedAnswers(['x = (5+3)/2。'], 'x = (5+3)/2')).toEqual(['x = (5+3)/2。'])
  })

  it('keeps the first spelling and never treats ×/÷ as equal to ASCII operators', () => {
    expect(normalizeAnswerText('（a； b）：c， d')).toBe('(a;b):c,d')
    expect(dedupeAcceptedAnswers(['a×b', 'a*b', 'a×b '], 'c')).toEqual(['a×b', 'a*b'])
    expect(dedupeAcceptedAnswers(['  ', '2－1', '2-1'], 'x')).toEqual(['2－1'])
  })
})
