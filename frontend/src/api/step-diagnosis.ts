import { isRecord, isNonnegativeInteger, isFiniteNumber, isStringArray } from './validation'

export const STEP_STATE_LABELS = {
  independent: '独立失分', carried: '受前一步影响', met: '已完成',
  uncertain: 'AI 判不准', blank: '整题未作答', unknown: '无逐步记录',
} as const
export type StepState = keyof typeof STEP_STATE_LABELS
export const STEP_SOURCE_LABELS = {
  teacher: '教师逐步确认', ai: 'AI 步骤判断',
  teacher_confirmed_ai: '教师确认总分，步骤为 AI 判断',
} as const
export type StepSource = keyof typeof STEP_SOURCE_LABELS
export interface KeyStep { key: string; name: string }
export interface StepResult {
  step_id: string
  goal: string
  state: StepState
  key_steps: KeyStep[]
  reason: string
}
export type StepDiagnosis =
  | { availability: 'available'; scope: 'key_steps' | 'point_text'; source: StepSource; steps: StepResult[] }
  | { availability: 'teacher_total_only' | 'no_records'; source: null; steps: [] }
export interface KeyStepStats {
  step_id: string
  part_id: string
  goal: string
  score: number
  key_steps: KeyStep[]
  counts: Record<string, number>
}
export interface KeyStepCause {
  reason: string
  category: string
  count: number
  questions: string[]
  examples: string[]
}
export interface KeyStepRepeatedStudent {
  student_id: string
  student_name: string
  fails: number
}
export interface KeyStepHotspot {
  key: string
  name: string
  independent: number
  observed: number
  carried: number
  uncertain: number
  blank: number
  confirmed_ai: number
  questions: string[]
  causes_status: 'ready' | 'partial' | 'unclassified' | 'missing'
  causes: KeyStepCause[]
  patterns: { pattern: string; actual: number; question_id: number }[]
  repeated_students: KeyStepRepeatedStudent[]
}
export interface StepDiagnosisSummary {
  students: number
  confirmed_ai_rows: number
  excluded_teacher_total_only: number
  hotspots: KeyStepHotspot[]
}

function isKeyStep(value: unknown): value is KeyStep {
  return isRecord(value) && typeof value.key === 'string' && typeof value.name === 'string'
}
function isStepState(value: unknown): value is StepState {
  return value === 'independent' || value === 'carried' || value === 'met'
    || value === 'uncertain' || value === 'blank' || value === 'unknown'
}
function isStepResult(value: unknown): value is StepResult {
  return isRecord(value) && typeof value.step_id === 'string' && typeof value.goal === 'string'
    && isStepState(value.state) && typeof value.reason === 'string'
    && Array.isArray(value.key_steps) && value.key_steps.every(isKeyStep)
}
export function isStepDiagnosis(value: unknown): value is StepDiagnosis {
  if (!isRecord(value) || !Array.isArray(value.steps)) return false
  if (value.availability === 'available') {
    return (value.source === 'ai' || value.source === 'teacher' || value.source === 'teacher_confirmed_ai')
      && (value.scope === 'key_steps' || value.scope === 'point_text') && value.steps.every(isStepResult)
  }
  return (value.availability === 'teacher_total_only' || value.availability === 'no_records')
    && value.source === null && value.steps.length === 0
}
function isKeyStepStats(value: unknown): value is KeyStepStats {
  return isRecord(value) && typeof value.step_id === 'string' && typeof value.part_id === 'string'
    && typeof value.goal === 'string' && isFiniteNumber(value.score)
    && Array.isArray(value.key_steps) && value.key_steps.every(isKeyStep)
    && isRecord(value.counts) && Object.values(value.counts).every(isNonnegativeInteger)
}
export function decodeKeyStepStats(value: unknown): KeyStepStats[] {
  if (!Array.isArray(value) || !value.every(isKeyStepStats)) throw new Error('Invalid key step statistics')
  return value
}
function isKeyStepCause(value: unknown): value is KeyStepCause {
  return isRecord(value) && typeof value.reason === 'string' && typeof value.category === 'string'
    && isNonnegativeInteger(value.count) && isStringArray(value.questions) && isStringArray(value.examples)
}
function isKeyStepPattern(value: unknown): value is KeyStepHotspot['patterns'][number] {
  return isRecord(value) && typeof value.pattern === 'string'
    && isNonnegativeInteger(value.actual) && isNonnegativeInteger(value.question_id)
}
function isKeyStepRepeatedStudent(value: unknown): value is KeyStepRepeatedStudent {
  return isRecord(value) && typeof value.student_id === 'string'
    && typeof value.student_name === 'string' && isNonnegativeInteger(value.fails)
}
function isKeyStepHotspot(value: unknown): value is KeyStepHotspot {
  return isRecord(value) && typeof value.key === 'string' && typeof value.name === 'string'
    && isNonnegativeInteger(value.independent) && isNonnegativeInteger(value.observed)
    && value.independent <= value.observed
    && isNonnegativeInteger(value.carried) && isNonnegativeInteger(value.uncertain)
    && isNonnegativeInteger(value.blank) && isNonnegativeInteger(value.confirmed_ai)
    && value.confirmed_ai <= value.independent && isStringArray(value.questions)
    && (value.causes_status === 'ready' || value.causes_status === 'partial'
      || value.causes_status === 'unclassified' || value.causes_status === 'missing')
    && Array.isArray(value.causes) && value.causes.every(isKeyStepCause)
    && Array.isArray(value.patterns) && value.patterns.every(isKeyStepPattern)
    && Array.isArray(value.repeated_students) && value.repeated_students.every(isKeyStepRepeatedStudent)
}
export function decodeStepDiagnosisSummary(value: unknown): StepDiagnosisSummary {
  if (!isRecord(value) || !isNonnegativeInteger(value.students)
    || !isNonnegativeInteger(value.confirmed_ai_rows) || !isNonnegativeInteger(value.excluded_teacher_total_only)
    || !Array.isArray(value.hotspots) || !value.hotspots.every(isKeyStepHotspot)) {
    throw new Error('Invalid step diagnosis summary')
  }
  return { students: value.students, confirmed_ai_rows: value.confirmed_ai_rows,
    excluded_teacher_total_only: value.excluded_teacher_total_only, hotspots: value.hotspots }
}
