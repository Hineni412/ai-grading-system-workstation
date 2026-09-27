import type {
  ConfigQuestionGenerationState,
  ConfigQuestionPreview,
  ConfigSourceDuplicateItem,
  QuestionDecision,
} from '../../api/config-workspace'

export interface ConfigQuestionFlag {
  label: string
  title: string
  /** warning = 默认警示色；neutral/info 用于题库命中与已决状态的轻量标记。 */
  tone?: 'warning' | 'neutral' | 'info'
}

/** Kinds that count toward 需核对；exact/near matches are tags only. */
export const REVIEW_DUPLICATE_KINDS = new Set<ConfigSourceDuplicateItem['kind']>([
  'answer_conflict',
  'image_uncertain',
])

export const BANK_DUPLICATE_KINDS = new Set<ConfigSourceDuplicateItem['kind']>([
  'exact_reusable',
  'exact_needs_analysis',
])

export function configSourceDuplicateTag(
  item: ConfigSourceDuplicateItem,
  decision?: QuestionDecision,
): ConfigQuestionFlag | null {
  if (decision) {
    const decided = decidedDuplicateTag(item, decision)
    if (decided) return decided
  }
  switch (item.kind) {
    case 'exact_reusable':
      return {
        label: '题库已有',
        title: item.reason || '复用已有分析，不调用 AI',
        tone: 'neutral',
      }
    case 'exact_needs_analysis':
      return { label: '题库已有·需补分析', title: item.reason, tone: 'neutral' }
    case 'image_uncertain':
      return { label: '图片待确认', title: item.reason }
    case 'answer_conflict':
      return { label: '答案与题库不同', title: item.reason }
    case 'variant':
      // 变式不在界面上展示（无标签、不计入需核对、不可对照）。
      return null
    case 'suspected':
      return { label: '相似', title: item.reason, tone: 'info' }
    default:
      return null
  }
}

function decidedDuplicateTag(
  item: ConfigSourceDuplicateItem,
  decision: QuestionDecision,
): ConfigQuestionFlag | null {
  switch (decision.bank_match) {
    case 'same':
      return {
        label: '已确认同一题',
        title: '已确认为题库同一题，将复用题库分析，不调用 AI。',
        tone: 'neutral',
      }
    case 'different':
      return item.kind === 'answer_conflict'
        ? {
            label: '以本卷答案为准',
            title: '已确认本卷答案正确，本题按新题分析入库。',
            tone: 'neutral',
          }
        : {
            label: '按新题处理',
            title: '已确认不是同一题，本题按新题分析入库。',
            tone: 'neutral',
          }
    case 'reanalyze':
      return {
        label: '将重新分析',
        title: '已确认重新分析本题，结果将写回题库。',
        tone: 'neutral',
      }
    default:
      break
  }
  if (decision.answer_confirmed && decision.answer_override) {
    return {
      label: '已改用题库答案',
      title: `本卷将改用题库答案：${decision.answer_override}`,
      tone: 'neutral',
    }
  }
  return null
}

/** A teacher decision resolving this duplicate is recorded when set. */
export function duplicateDecisionRecorded(
  decision: QuestionDecision | undefined,
): boolean {
  return Boolean(decision?.bank_match
    || (decision?.answer_confirmed && decision.answer_override))
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
    if (state.category === 'duplicate_content_uncertain') {
      return {
        label: '图片待确认',
        title: state.reason || '题库存在相同题干的带图题，图片内容需先在题库核对。',
      }
    }
    if (state.category === 'duplicate_analysis_missing') {
      return {
        label: '题库已有·分析缺失',
        title: state.reason || '题库已存在相同题目，但已存分析缺失或无法复用。',
      }
    }
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
