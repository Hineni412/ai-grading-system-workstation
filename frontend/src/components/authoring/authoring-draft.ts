import type { AuthoringSoloLevel } from '../../api/authoring'
import { AUTHORING_SOLO_LABELS } from '../../api/authoring'

export interface PartRow {
  part_label: string
  predicted_difficulty: string
  predicted_solo: '' | AuthoringSoloLevel
}

export interface DraftState {
  intent: string
  knowledgeText: string
  keyStepsText: string
  expectedErrorsText: string
  predictedDifficulty: string
  predictedSolo: '' | AuthoringSoloLevel
  questionText: string
  answerText: string
  questionType: string
  targetKnowledgeText: string
  caseListText: string
  notes: string
  parts: PartRow[]
}

export const CONTENT_FIELD_LABELS: Record<string, string> = {
  intent: '命题意图',
  knowledge_points: '考点',
  key_steps: '关键步骤',
  expected_errors: '预期错法',
  predicted_difficulty: '预估难度',
  predicted_solo: '预估SOLO',
  parts: '小问',
  question_text: '题干',
  answer_text: '答案',
  question_type: '题型',
  target_knowledge: '目标考点',
  case_list: '分类讨论',
  notes: '备注',
}

export function formatField(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === 'object' && item !== null && 'part_label' in item) {
        const part = item as Record<string, unknown>
        const bits = [String(part.part_label ?? '')]
        if (part.predicted_difficulty != null) bits.push(`难度${part.predicted_difficulty}`)
        if (part.predicted_solo) {
          bits.push(AUTHORING_SOLO_LABELS[part.predicted_solo as AuthoringSoloLevel] ?? String(part.predicted_solo))
        }
        return bits.join(' ')
      }
      return String(item)
    }).join('\n')
  }
  return JSON.stringify(value)
}
