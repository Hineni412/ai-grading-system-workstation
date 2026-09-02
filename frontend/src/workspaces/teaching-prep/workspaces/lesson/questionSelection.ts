import type { CurriculumEdition } from '../../api/catalog'
import type {
  QuestionBankSection,
  QuestionSelectionItem,
  QuestionSelectionPreview,
  QuestionSelectionPreviewInput,
} from '../../api/workbench'

/**
 * 题库册别 id：备课教材版本的年级+册别映射到内置题库的五册目录。
 * 题库暂无九年级下册与全年册，返回 null 时页面退化为不选题。
 */
export function questionBankVolumeId(
  curriculum: Pick<CurriculumEdition, 'grade_level' | 'volume'> | null,
): string | null {
  if (!curriculum) return null
  const suffix = curriculum.volume === 'first'
    ? 'upper'
    : curriculum.volume === 'second' ? 'lower' : null
  if (!suffix) return null
  if (curriculum.grade_level < 7 || curriculum.grade_level > 9) return null
  const id = `bnu24-math-g${curriculum.grade_level}-${suffix}`
  return id === 'bnu24-math-g9-lower' ? null : id
}

/** 小节显示名 "七年级下册｜第一章 整式的乘除｜1 幂的乘除" 中的短名部分。 */
export function sectionShortName(section: Pick<QuestionBankSection, 'section_name'>): string {
  const last = section.section_name.split('｜').pop() ?? section.section_name
  return last.replace(/^\d+(?:\.\d+)*\s*/, '').trim()
}

function normalizeTitle(raw: string): string {
  return raw
    .replace(/第\s*\d+\s*课时/g, '')
    .replace(/[\s·•、，,。:：;；()（）\-—–_｜|]/g, '')
    .trim()
}

/**
 * 按课时标题在小节清单中找唯一候选：小节短名与课时标题互相包含才算匹配；
 * 多个命中时取短名最长者，最长者有并列才放弃，交由教师手动选择。
 */
export function matchSectionForLesson(
  lessonTitle: string,
  sections: readonly QuestionBankSection[],
): QuestionBankSection | null {
  const lesson = normalizeTitle(lessonTitle)
  if (!lesson) return null
  const matched = sections.flatMap((section) => {
    const short = normalizeTitle(sectionShortName(section))
    if (short.length < 2) return []
    return lesson.includes(short) || short.includes(lesson)
      ? [{ section, length: short.length }]
      : []
  })
  if (!matched.length) return null
  const longest = Math.max(...matched.map(item => item.length))
  const best = matched.filter(item => item.length === longest)
  return best.length === 1 ? best[0]!.section : null
}

export const QUESTION_PREVIEW_DEFAULTS = {
  difficulty_max: 5,
  stem_max_chars: 220,
  limit: 12,
  max_per_method: 2,
} as const

/** 组装选题预览请求：移除过的题进入 exclude，重跑时会补进替代题。 */
export function buildPreviewRequest(
  volumeId: string,
  sectionIds: string[],
  excludeQuestionIds: readonly number[],
): QuestionSelectionPreviewInput {
  return {
    volume_id: volumeId,
    section_ids: [...new Set(sectionIds)],
    ...QUESTION_PREVIEW_DEFAULTS,
    exclude_question_ids: [...new Set(excludeQuestionIds)],
  }
}

/** 预览结果转成资源包冻结时随附的 question_selection 负载。 */
export function selectionPayloadForFreeze(
  preview: { request: QuestionSelectionPreview['request'] },
  items: readonly QuestionSelectionItem[],
): {
  volume_id: string
  section_ids: string[]
  difficulty_max: number
  stem_max_chars: number
  limit: number
  max_per_method: number
  items: Array<{
    question_id: number
    method: string | null
    difficulty: number | null
    frequency_score: number | null
  }>
} {
  return {
    volume_id: preview.request.volume_id,
    section_ids: [...preview.request.section_ids],
    difficulty_max: preview.request.difficulty_max,
    stem_max_chars: preview.request.stem_max_chars,
    limit: preview.request.limit,
    max_per_method: preview.request.max_per_method,
    items: items.map(item => ({
      question_id: item.question_id,
      method: item.method || null,
      difficulty: item.difficulty,
      frequency_score: item.frequency_score,
    })),
  }
}
