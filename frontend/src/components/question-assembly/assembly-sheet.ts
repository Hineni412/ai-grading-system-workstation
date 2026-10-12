import { computed } from 'vue'

import type { QuestionBankRichBlock } from '../../api/question-bank'
import type { AssemblyQuestion } from '../../api/assembly'
import { useAssemblyStore } from '../../stores/assembly'

// 卷面展示清理，与导出端一致（只影响显示，不改数据）：
// - 题号前缀：paper_docx_exporter.py:56 LEADING_QUESTION_NUMBER_PATTERN
// - 印刷分值：rich_content_service.py _source_score_span（题号可选 + （N分））
const LEADING_PRINT_NUMBER = /^\s*(?:第\s*)?\d{1,3}\s*(?:[.．、]|题)[ \t]*/
const LEADING_PRINT_SCORE = /^\s*(?:(?:第\s*)?\d+\s*(?:[.．、]|题)\s*)?[（(][ \t ]*\d+(?:\.\d+)?[ \t ]*分[ \t ]*[）)][ \t ]*/
// 与 _STEM_DISPLAY_MARKUP 同义：匹配时跳过图片标记与内联标签。
const DISPLAY_MARKUP = /\[\[IMAGE:[^\]]*\]\]|<[^>]+>/g

function visibleChars(text: string): { visible: string; offsets: number[] } {
  const offsets: number[] = []
  const parts: string[] = []
  let cursor = 0
  for (const match of text.matchAll(DISPLAY_MARKUP)) {
    parts.push(text.slice(cursor, match.index))
    for (let index = cursor; index < match.index; index += 1) offsets.push(index)
    cursor = match.index + match[0].length
  }
  parts.push(text.slice(cursor))
  for (let index = cursor; index < text.length; index += 1) offsets.push(index)
  return { visible: parts.join(''), offsets }
}

export function stripLeadingPrint(text: string): string {
  const { visible, offsets } = visibleChars(text)
  const match = LEADING_PRINT_SCORE.exec(visible) ?? LEADING_PRINT_NUMBER.exec(visible)
  if (!match) return text
  const removed = new Set(offsets.slice(match.index, match.index + match[0].length))
  return [...text].filter((_, index) => !removed.has(index)).join('')
}

// 与 strip_question_source_score_blocks 一致：只处理第一块有可见文字的题干块。
export function stripStemBlocks(
  blocks: QuestionBankRichBlock[] | undefined,
): QuestionBankRichBlock[] | undefined {
  if (!blocks?.length) return blocks
  let stripped = false
  return blocks.map((block) => {
    if (stripped) return block
    if (!visibleChars(block.text || block.html || '').visible.trim()) return block
    stripped = true
    return {
      ...block,
      text: stripLeadingPrint(block.text),
      html: block.html ? stripLeadingPrint(block.html) : block.html,
    }
  })
}

const ANSWER_REVEAL = /故\s*(?:答案为|选)\s*[:：]/g

// 从 answer_text 提取最后一个「故答案为：X / 故选：X」片段（到 。或行尾）。
// HTML 只用于定位，截取仍回到原文，保留 <sup>/<span class="qm"> 等排版标记。
export function extractPrintedAnswer(answerText: string): string | null {
  const { visible, offsets } = visibleChars(answerText)
  let last: RegExpExecArray | null = null
  for (const match of visible.matchAll(ANSWER_REVEAL)) last = match
  if (!last) return null
  const start = last.index + last[0].length
  let end = visible.length
  for (const stop of ['。', '\n']) {
    const index = visible.indexOf(stop, start)
    if (index >= 0 && index < end) end = index
  }
  if (end <= start) return null
  const first = offsets[start]
  const lastOffset = offsets[end - 1]
  if (first === undefined || lastOffset === undefined) return null
  const fragment = answerText.slice(first, lastOffset + 1).trim()
  return fragment || null
}

const ANALYSIS_MARK = '【'

// 无「故答案为/故选」时，解析展示从第一个【标记起（块级），否则全部。
export function analysisBlocks(
  blocks: QuestionBankRichBlock[] | undefined,
): QuestionBankRichBlock[] | undefined {
  if (!blocks?.length) return blocks
  const index = blocks.findIndex(
    (block) => visibleChars(block.text || block.html || '').visible.includes(ANALYSIS_MARK),
  )
  return index > 0 ? blocks.slice(index) : blocks
}

export function analysisFallback(answerText: string | null | undefined): string {
  const text = answerText ?? ''
  const index = text.indexOf(ANALYSIS_MARK)
  return index > 0 ? text.slice(index) : text
}

// 与 paper_docx_exporter._canonical_type_group 一致的三归并：
// 选择（含多选）/填空/解答；解答题子类是标签，不再单独成组。
export function canonicalTypeGroup(questionType: string | null | undefined): string {
  const value = (questionType ?? '').trim()
  if (!value) return '未分类'
  if (['choice', 'single_choice', 'multiple_choice', 'multi_choice', '选择题', '多选题'].includes(value)) {
    return '选择题'
  }
  if (['fill_blank', 'blank', '填空题'].includes(value)) return '填空题'
  return '解答题'
}

// Mirrors word_renderer.answer_space_lines: choice/fill types get no reserved
// answer area, everything else reserves three handwriting lines.
export function answerSpaceLines(questionType: string | null): number {
  const value = String(questionType ?? '').toLowerCase()
  return /选择|填空|choice|fill/.test(value) ? 0 : 3
}

// 卷面一次最多渲染这么多题；更多内容用"加载更多"分块展开，
// 避免大题篮（上限 500 题）一次性渲染全部富文本与配图。
export const SHEET_CHUNK_SIZE = 20

export interface SheetSection {
  id: string
  title: string
  /** canonical type name when layout is grouped_by_type, else null. */
  type: string | null
  questions: AssemblyQuestion[]
}

export function useSheetSections() {
  const assembly = useAssemblyStore()
  const editableSections = computed(() => assembly.draft.sections.filter(
    (section) => section.id !== 'unassigned',
  ))
  const assignedQuestionIds = computed(() => new Set(
    editableSections.value.flatMap((section) => section.question_ids),
  ))
  const sheetSections = computed<SheetSection[]>(() => {
    if (assembly.draft.layout_mode === 'sections' && editableSections.value.length > 0) {
      const manualSections = editableSections.value.map((section) => ({
        id: section.id,
        title: section.title,
        type: null,
        questions: section.question_ids
          .map((id) => assembly.questionMap.get(id))
          .filter((item): item is AssemblyQuestion => item !== undefined),
      }))
      const remaining = assembly.orderedQuestions.filter(
        (question) => !assignedQuestionIds.value.has(question.id),
      )
      return remaining.length
        ? [{ id: 'unassigned', title: '未分节', type: null, questions: remaining }, ...manualSections]
        : manualSections
    }
    if (assembly.draft.layout_mode === 'grouped_by_type') {
      const groups = new Map<string, AssemblyQuestion[]>()
      for (const question of assembly.orderedQuestions) {
        const type = canonicalTypeGroup(question.question_type)
        groups.set(type, [...(groups.get(type) ?? []), question])
      }
      return [...groups.entries()].map(([title, questions], index) => ({
        id: `type-${index}`,
        title,
        type: title,
        questions,
      }))
    }
    return [{
      id: 'all',
      title: '试题',
      type: null,
      questions: assembly.orderedQuestions,
    }]
  })
  return { editableSections, assignedQuestionIds, sheetSections }
}
