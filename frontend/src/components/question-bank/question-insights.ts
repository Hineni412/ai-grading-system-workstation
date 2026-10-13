import type { QuestionBankListItem } from '../../api/question-bank'
import type {
  TrainingKeyStepTypeStatsResponse,
  TrainingOverviewDistribution,
  TrainingOverviewNode,
} from '../../api/training'

export interface InsightExamUse {
  session: string
  label: string
  rate: number | null
}

export interface InsightStepRow {
  key: string
  name: string
  independent: number
  observed: number
  rate: number
  repeated: number
  common: boolean
}

export type OverviewLoadState = 'idle' | 'loading' | 'ready' | 'error' | 'stale-error'

export interface QuestionInsightInputs {
  loadState: OverviewLoadState
  hasOverview: boolean
  examUsesByQuestion: Map<number, InsightExamUse[]>
  typeNodeByKey: Map<string, TrainingOverviewNode>
  keyStepState: 'idle' | 'loading' | 'ready' | 'error'
  keyStepStats: TrainingKeyStepTypeStatsResponse | null
}

export interface QuestionInsight {
  exam: {
    state: 'loading' | 'unavailable' | 'ready'
    rows: InsightExamUse[]
  }
  type: {
    state: 'loading' | 'unavailable' | 'untyped' | 'no-data' | 'ready'
    name: string
    mastery: number | null
    need: number
    evidenced: number
    distribution: TrainingOverviewDistribution | null
  }
  steps: {
    state: 'loading' | 'unavailable' | 'no-steps' | 'no-data' | 'ready'
    rows: InsightStepRow[]
  }
  frequency: { midterm: number | null; final: number | null } | null
}

function remoteState(
  loadState: OverviewLoadState,
  hasData: boolean,
): 'loading' | 'unavailable' | 'ready' {
  if (hasData) return 'ready'
  if (loadState === 'error') return 'unavailable'
  if (loadState === 'idle' || loadState === 'loading') return 'loading'
  return 'ready'
}

export function buildQuestionInsight(
  question: QuestionBankListItem,
  inputs: QuestionInsightInputs,
): QuestionInsight {
  const rows = inputs.examUsesByQuestion.get(question.id) ?? []
  const exam = rows.length
    ? { state: 'ready' as const, rows }
    : { state: remoteState(inputs.loadState, inputs.hasOverview), rows }

  const primaryKey = question.labels?.primary_type?.key ?? ''
  let type: QuestionInsight['type']
  if (!primaryKey) {
    type = { state: 'untyped', name: '', mastery: null, need: 0, evidenced: 0, distribution: null }
  } else {
    const state = remoteState(inputs.loadState, inputs.hasOverview)
    if (state !== 'ready') {
      type = { state, name: '', mastery: null, need: 0, evidenced: 0, distribution: null }
    } else {
      const node = inputs.typeNodeByKey.get(primaryKey)
      type = node
        ? {
            state: 'ready',
            name: node.display_name,
            mastery: node.group_mastery,
            need: node.distribution.weak + node.distribution.unsteady,
            evidenced: node.evidence_student_count,
            distribution: node.distribution,
          }
        : { state: 'no-data', name: '', mastery: null, need: 0, evidenced: 0, distribution: null }
    }
  }

  const skillKeys = (question.skills ?? []).map(skill => skill.stable_key).filter(Boolean)
  let steps: QuestionInsight['steps']
  if (!skillKeys.length) {
    steps = { state: 'no-steps', rows: [] }
  } else {
    const stepState: QuestionInsight['steps']['state'] = inputs.keyStepState === 'error'
      ? 'unavailable'
      : inputs.keyStepState === 'idle' || inputs.keyStepState === 'loading'
        ? 'loading'
        : 'ready'
    if (stepState !== 'ready' || !inputs.keyStepStats) {
      steps = { state: stepState === 'ready' ? 'no-data' : stepState, rows: [] }
    } else {
      const wanted = new Set(skillKeys)
      const stepRows = (inputs.keyStepStats.types[primaryKey] ?? [])
        .filter(stat => wanted.has(stat.key) && stat.observed > 0)
        .map(stat => ({
          key: stat.key,
          name: stat.name,
          independent: stat.independent,
          observed: stat.observed,
          rate: stat.rate,
          repeated: stat.repeated_students.length,
          common: stat.common,
        }))
        .sort((a, b) => b.rate - a.rate)
      steps = stepRows.length ? { state: 'ready', rows: stepRows } : { state: 'no-data', rows: [] }
    }
  }

  return { exam, type, steps, frequency: question.exam_frequency ?? null }
}
