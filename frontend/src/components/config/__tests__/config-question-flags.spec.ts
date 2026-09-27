import { describe, expect, it } from 'vitest'

import type {
  ConfigQuestionGenerationState,
  ConfigQuestionPreview,
  ConfigSourceDuplicateItem,
} from '../../../api/config-workspace'
import {
  configQuestionFlags,
  configSourceDuplicateTag,
} from '../config-question-flags'

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

  it('maps duplicate-specific failure categories to specific labels', () => {
    const uncertain: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'blocked', retryable: false,
      reason: '题库存在相同题干的带图题，图片内容需先在题库核对。',
      category: 'duplicate_content_uncertain',
    }
    const missing: ConfigQuestionGenerationState = {
      question_id: 'Q1', state: 'failed', retryable: true,
      reason: '题库已存在相同题目，但已存分析缺失或无法复用。',
      category: 'duplicate_analysis_missing',
    }

    expect(configQuestionFlags(question(), uncertain)[0]?.label).toBe('图片待确认')
    expect(configQuestionFlags(question(), uncertain)[0]?.title)
      .toContain('图片内容需先在题库核对')
    expect(configQuestionFlags(question(), missing)[0]?.label).toBe('题库已有·分析缺失')
    expect(configQuestionFlags(question(), missing)[0]?.title)
      .toContain('分析缺失或无法复用')
  })
})

describe('configSourceDuplicateTag', () => {
  function duplicate(
    kind: ConfigSourceDuplicateItem['kind'],
  ): ConfigSourceDuplicateItem {
    return {
      question_id: 'Q1', kind, matched_question_id: 12,
      matched_paper_title: '样卷', matched_question_number: '3',
      similarity: 1, matched_question_excerpt: '题干节选', reason: '题库已有相同题目',
    }
  }

  it('maps each duplicate kind to its compact label and hides variant and same_session', () => {
    const cases: Array<[ConfigSourceDuplicateItem['kind'], string | null]> = [
      ['exact_reusable', '题库已有'],
      ['exact_needs_analysis', '题库已有·需补分析'],
      ['image_uncertain', '图片待确认'],
      ['answer_conflict', '答案与题库不同'],
      ['variant', null],
      ['suspected', '相似'],
      ['same_session', null],
    ]
    for (const [kind, label] of cases) {
      expect(configSourceDuplicateTag(duplicate(kind))?.label ?? null).toBe(label)
    }
    expect(configSourceDuplicateTag(duplicate('exact_reusable'))?.title)
      .toBe('题库已有相同题目')
  })

  it('uses neutral or info tones for non-warning kinds and warning for review kinds', () => {
    expect(configSourceDuplicateTag(duplicate('exact_reusable'))?.tone).toBe('neutral')
    expect(configSourceDuplicateTag(duplicate('exact_needs_analysis'))?.tone).toBe('neutral')
    expect(configSourceDuplicateTag(duplicate('suspected'))?.tone).toBe('info')
    expect(configSourceDuplicateTag(duplicate('answer_conflict'))?.tone ?? 'warning')
      .toBe('warning')
    expect(configSourceDuplicateTag(duplicate('image_uncertain'))?.tone ?? 'warning')
      .toBe('warning')
  })

  it('relabels tags once a teacher decision is recorded', () => {
    expect(configSourceDuplicateTag(duplicate('suspected'), {
      question_id: 'Q1', excluded: false, bank_match: 'same', bank_question_id: 12,
    })?.label).toBe('已确认同一题')
    expect(configSourceDuplicateTag(duplicate('suspected'), {
      question_id: 'Q1', excluded: false, bank_match: 'different',
    })?.label).toBe('按新题处理')
    expect(configSourceDuplicateTag(duplicate('answer_conflict'), {
      question_id: 'Q1', excluded: false, bank_match: 'different',
    })?.label).toBe('以本卷答案为准')
    expect(configSourceDuplicateTag(duplicate('exact_needs_analysis'), {
      question_id: 'Q1', excluded: false, bank_match: 'reanalyze',
    })?.label).toBe('将重新分析')
    expect(configSourceDuplicateTag(duplicate('answer_conflict'), {
      question_id: 'Q1', excluded: false, answer_confirmed: true, answer_override: 'B',
    })?.label).toBe('已改用题库答案')
  })

  it('falls back to the reuse hint when exact_reusable has no reason', () => {
    const item = { ...duplicate('exact_reusable'), reason: '' }
    expect(configSourceDuplicateTag(item)?.title).toBe('复用已有分析，不调用 AI')
  })
})
