import type {
  ConfigQuestionGenerationState,
  ConfigQuestionPreview,
} from '../../api/config-workspace'

export interface ConfigQuestionFlag {
  label: string
  title: string
}

const UNTRUSTED_ANSWER_TYPES = new Set([
  'choice',
  'single_choice',
  'multi_choice',
  'fill_blank',
])

function warningLabel(warning: string): string {
  if (/小问/.test(warning) && /答案/.test(warning)) return '小问数与答案不一致'
  if (/图片/.test(warning)) return '图片未归属'
  if (/答案区/.test(warning)) return '答案区多出题号'
  if (/图文结构|未能完整读取|Word/.test(warning)) return 'Word 结构未完整读取'
  return '来源需核对'
}

function generationFlag(
  state: ConfigQuestionGenerationState | undefined,
): ConfigQuestionFlag | null {
  if (!state) return null
  if (state.reason === 'question_bank_intake') {
    return { label: '入库异常', title: '本题入库未完成，完整入库后才能赋分。' }
  }
  if (state.state === 'blocked' || state.state === 'failed') {
    return {
      label: '入库异常',
      title: state.reason || '本题分析入库未完成。',
    }
  }
  return null
}

export function configQuestionFlags(
  question: ConfigQuestionPreview,
  generation?: ConfigQuestionGenerationState,
): ConfigQuestionFlag[] {
  const flags: ConfigQuestionFlag[] = []
  if (question.question_type_review_required) {
    flags.push({
      label: '题型待确认',
      title:
        question.question_type_review_reason
        || '题面形式与解析内容存在冲突，请确认题型。',
    })
  }
  for (const warning of question.parse_warnings ?? []) {
    flags.push({ label: warningLabel(warning), title: warning })
  }
  if (
    UNTRUSTED_ANSWER_TYPES.has(question.question_type)
    && question.local_answer_trusted === false
  ) {
    flags.push({
      label: '答案未识别',
      title: '生成评分依据时以 AI 结果为准',
    })
  }
  const state = generationFlag(generation)
  if (state) flags.push(state)
  return flags
}
