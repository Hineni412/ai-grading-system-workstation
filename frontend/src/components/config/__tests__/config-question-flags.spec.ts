import { describe, expect, it } from 'vitest'

import type {
  ConfigQuestionGenerationState,
  ConfigQuestionPreview,
} from '../../../api/config-workspace'
import { configQuestionFlags } from '../config-question-flags'

function question(overrides: Partial<ConfigQuestionPreview> = {}): ConfigQuestionPreview {
  return {
    question_id: 'Q1',
    question_type: 'calculation',
    question_preview: '题面',
    answer_preview: '答案',
    answer_present: true,
    needs_review: false,
    local_answer_trusted: true,
    has_question_asset: false,
    has_answer_asset: false,
    ...overrides,
  }
}

describe('configQuestionFlags', () => {
  it('flags a question whose type needs confirmation with its reason', () => {
    const flags = configQuestionFlags(question({
      question_type_review_required: true,
      question_type_review_reason: '题面既有多个小问，也有填空位置。',
    }))

    expect(flags.map((flag) => flag.label)).toEqual(['题型待确认'])
    expect(flags[0]!.title).toContain('多个小问')
  })

  it('maps each parse warning to a specific label and keeps the full text', () => {
    const flags = configQuestionFlags(question({
      parse_warnings: [
        '题面有 3 个小问，同号答案有 2 个小问；请核对原卷合并或删题后的答案对应关系。',
        '有 2 张图片没有明确的题号位置，已保留供核对归属。',
        '答案区保留了第 13 题，但题面没有同号大题；请核对是否需要并入本题。',
        'Word 图文结构未能完整读取，当前为文字提取结果；请核对配图、公式与题目边界。',
        '完全无法识别的提示。',
      ],
    }))

    expect(flags.map((flag) => flag.label)).toEqual([
      '小问数与答案不一致',
      '图片未归属',
      '答案区多出题号',
      'Word 结构未完整读取',
      '来源需核对',
    ])
    expect(flags[0]!.title).toContain('题面有 3 个小问')
  })

  it('flags untrusted answers only on objective question types', () => {
    const choice = configQuestionFlags(question({
      question_type: 'choice',
      local_answer_trusted: false,
    }))
    const fillBlank = configQuestionFlags(question({
      question_type: 'fill_blank',
      local_answer_trusted: false,
    }))
    const comprehensive = configQuestionFlags(question({
      question_type: 'comprehensive',
      local_answer_trusted: false,
      needs_review: true,
    }))
    const calculation = configQuestionFlags(question({
      question_type: 'calculation',
      local_answer_trusted: false,
    }))
    const proof = configQuestionFlags(question({
      question_type: 'proof',
      local_answer_trusted: false,
    }))

    expect(choice[0]?.label).toBe('答案未识别')
    expect(choice[0]?.title).toBe('生成评分依据时以 AI 结果为准')
    expect(fillBlank[0]?.label).toBe('答案未识别')
    expect(comprehensive).toEqual([])
    expect(calculation).toEqual([])
    expect(proof).toEqual([])
  })

  it('flags blocked, failed and intake-failed generation states', () => {
    const blocked: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'blocked', reason: 'local_validation', retryable: true,
    }
    const failed: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'failed', reason: 'model_error', retryable: true,
    }
    const intake: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'passed', reason: 'question_bank_intake', retryable: false,
    }
    const passed: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'passed', reason: '', retryable: false,
    }

    expect(configQuestionFlags(question(), blocked)[0]?.label).toBe('入库异常')
    expect(configQuestionFlags(question(), failed)[0]?.label).toBe('入库异常')
    expect(configQuestionFlags(question(), intake)[0]?.label).toBe('入库异常')
    expect(configQuestionFlags(question(), passed)).toEqual([])
  })

  it('produces no flag for a clean question', () => {
    expect(configQuestionFlags(question())).toEqual([])
    expect(configQuestionFlags(question({ needs_review: true }))).toEqual([])
  })
})
