import { describe, expect, it } from 'vitest'

import { translateGradingReason } from '../utils/grading-reasons'

describe('translateGradingReason', () => {
  it('translates machine objective-answer markers instead of falling back', () => {
    expect(translateGradingReason('objective_answer=40')).toBe('作答识别为「40」，与参考答案不符')
    expect(translateGradingReason('objective_answer=C')).toBe('作答识别为「C」，与参考答案不符')
  })

  it('keeps known codes, Chinese text, and the fallback for unknown English', () => {
    expect(translateGradingReason('low_confidence')).toBe('作答辨识度较低，需要教师复核')
    expect(translateGradingReason('角度计算结果错误')).toBe('角度计算结果错误')
    expect(translateGradingReason('The derived relationship is reversed')).toBe('自动处理未完成，请教师复核')
    expect(translateGradingReason('some unknown text', '高置信 AI 结果')).toBe('高置信 AI 结果')
  })
})
