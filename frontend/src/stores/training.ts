import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  trainingApi,
  type TrainingDiagnosis,
  type TrainingDiagnosisRequest,
  type TrainingExamScopeRequest,
  type TrainingStudentScopeRequest,
} from '../api/training'

export type TrainingRequestState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'

export interface TrainingStudentScopeInput {
  mode: TrainingStudentScopeRequest['mode']
  studentIds: string[]
  classId: string
  classIds: string[]
  scoreRateMin: number | null
  scoreRateMax: number | null
  includeStudentIds: string[]
  excludeStudentIds: string[]
  useHistoricalFallback: boolean
}

export type TrainingStudentScopeUpdate = Pick<
  TrainingStudentScopeInput,
  'mode' | 'studentIds' | 'classId'
> & Partial<Omit<TrainingStudentScopeInput, 'mode' | 'studentIds' | 'classId'>>

export interface TrainingExamScopeInput {
  mode: TrainingExamScopeRequest['mode']
  sessionIds: number[]
  curriculumVolumeId?: string
}

export interface TrainingWorkflowApi {
  diagnose(
    body: TrainingDiagnosisRequest,
    signal?: AbortSignal,
  ): Promise<TrainingDiagnosis>
}

function uniqueText(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort()
}

function uniquePositiveIntegers(values: number[]): number[] {
  return [...new Set(values.filter(
    (value) => Number.isSafeInteger(value) && value > 0,
  ))].sort((left, right) => left - right)
}

export const useTrainingStore = defineStore('training', () => {
  const studentScope = ref<TrainingStudentScopeInput>({
    mode: 'all',
    studentIds: [],
    classId: '',
    classIds: [],
    scoreRateMin: null,
    scoreRateMax: null,
    includeStudentIds: [],
    excludeStudentIds: [],
    useHistoricalFallback: true,
  })
  const examScope = ref<TrainingExamScopeInput>({
    mode: 'current',
    sessionIds: [],
  })
  const diagnosis = ref<TrainingDiagnosis | null>(null)
  const analysisState = ref<TrainingRequestState>('idle')
  const errorMessage = ref('')

  let generation = 0
  let diagnosisController: AbortController | null = null
  let diagnosisScopeKey = ''
  // Browsing a chapter or returning from its editor reuses the last result.
  // Adoption still makes a fresh request; these previews never authorize a write.
  const groupDiagnosisCache = new Map<string, {
    basis: string; diagnosis: TrainingDiagnosis
  }>()

  function groupDiagnosisBasis(value: TrainingDiagnosis): string {
    return JSON.stringify([value.scope, value.exam_scope, value.students, value.knowledge_catalog])
  }

  function cachedGroupDiagnosis(key: string, basis: TrainingDiagnosis): TrainingDiagnosis | null {
    const entry = groupDiagnosisCache.get(key)
    if (!entry || entry.basis !== groupDiagnosisBasis(basis)) return null
    return entry.diagnosis
  }

  function rememberGroupDiagnosis(key: string, basis: TrainingDiagnosis, value: TrainingDiagnosis): void {
    groupDiagnosisCache.delete(key)
    groupDiagnosisCache.set(key, { basis: groupDiagnosisBasis(basis), diagnosis: value })
    while (groupDiagnosisCache.size > 8) groupDiagnosisCache.delete(groupDiagnosisCache.keys().next().value!)
  }

  const hasCurrentDiagnosis = computed(
    () => diagnosis.value !== null && diagnosisScopeKey === scopeKey(),
  )
  const knowledgePoints = computed(() => uniqueText(
    diagnosis.value?.students.flatMap(
      (student) => student.weak_points.map(({ knowledge_point }) => knowledge_point),
    ) ?? [],
  ))

  function normalizedStudentScope(): TrainingStudentScopeRequest {
    const studentIds = uniqueText(studentScope.value.studentIds)
    const result: TrainingStudentScopeRequest = {
      mode: studentScope.value.mode,
      student_ids: studentIds,
    }
    if (studentScope.value.scoreRateMin !== null) result.score_rate_min = studentScope.value.scoreRateMin
    if (studentScope.value.scoreRateMax !== null) result.score_rate_max = studentScope.value.scoreRateMax
    const included = uniqueText(studentScope.value.includeStudentIds)
    const excluded = uniqueText(studentScope.value.excludeStudentIds)
    if (included.length) result.include_student_ids = included
    if (excluded.length) result.exclude_student_ids = excluded
    if (!studentScope.value.useHistoricalFallback) result.use_historical_fallback = false
    const classId = studentScope.value.classId.trim()
    const classIds = uniqueText([
      ...studentScope.value.classIds,
      ...(classId ? [classId] : []),
    ])
    if (studentScope.value.mode === 'class' && classIds.length) {
      result.class_ids = classIds
      if (classIds.length === 1) result.class_id = classIds[0]
    }
    return result
  }

  function normalizedExamScope(): TrainingExamScopeRequest {
    return {
      mode: examScope.value.mode,
      session_ids: uniquePositiveIntegers(examScope.value.sessionIds),
      ...(examScope.value.mode === 'semester' ? { curriculum_volume_id: examScope.value.curriculumVolumeId ?? '' } : {}),
    }
  }

  function scopeKey(): string {
    return JSON.stringify({
      scope: normalizedStudentScope(),
      exam_scope: normalizedExamScope(),
    })
  }

  function requestBody(): TrainingDiagnosisRequest {
    const scope = normalizedStudentScope()
    const nextExamScope = normalizedExamScope()
    if ((scope.mode === 'student' || scope.mode === 'selected') && scope.student_ids.length === 0) {
      throw new Error('请至少选择一名学生')
    }
    if (nextExamScope.mode !== 'semester' && nextExamScope.session_ids.length === 0) throw new Error('请至少选择一场考试')
    return {
      scope,
      exam_scope: nextExamScope,
    }
  }

  function invalidateScopeResults(): void {
    generation += 1
    diagnosisController?.abort()
    diagnosisController = null
    diagnosisScopeKey = ''
    analysisState.value = 'idle'
    errorMessage.value = ''
  }

  function setStudentScope(next: TrainingStudentScopeUpdate): void {
    const normalized: TrainingStudentScopeInput = {
      mode: next.mode,
      studentIds: uniqueText(next.studentIds),
      classId: next.classId.trim(),
      classIds: uniqueText(next.classIds ?? []),
      scoreRateMin: next.scoreRateMin ?? null,
      scoreRateMax: next.scoreRateMax ?? null,
      includeStudentIds: uniqueText(next.includeStudentIds ?? []),
      excludeStudentIds: uniqueText(next.excludeStudentIds ?? []),
      useHistoricalFallback: next.useHistoricalFallback !== false,
    }
    if (JSON.stringify(normalized) === JSON.stringify(studentScope.value)) return
    studentScope.value = normalized
    invalidateScopeResults()
  }

  function setExamScope(next: TrainingExamScopeInput): void {
    const normalized: TrainingExamScopeInput = {
      mode: next.mode,
      sessionIds: uniquePositiveIntegers(next.sessionIds),
      ...(next.mode === 'semester' ? { curriculumVolumeId: next.curriculumVolumeId ?? '' } : {}),
    }
    if (JSON.stringify(normalized) === JSON.stringify(examScope.value)) return
    if (normalized.mode === 'semester' && (examScope.value.mode !== 'semester'
      || normalized.curriculumVolumeId !== examScope.value.curriculumVolumeId)) diagnosis.value = null
    examScope.value = normalized
    invalidateScopeResults()
  }

  async function analyze(
    api: TrainingWorkflowApi = trainingApi,
  ): Promise<TrainingDiagnosis | null> {
    const body = requestBody()
    diagnosisController?.abort()
    const controller = new AbortController()
    diagnosisController = controller
    const requestGeneration = generation
    const requestScopeKey = scopeKey()
    diagnosisScopeKey = ''
    analysisState.value = 'loading'
    errorMessage.value = ''
    try {
      const next = await api.diagnose(body, controller.signal)
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestScopeKey !== scopeKey()
      ) return null
      diagnosis.value = next
      diagnosisScopeKey = requestScopeKey
      analysisState.value = next.students.some(
        (student) => student.weak_points.length > 0,
      ) ? 'ready' : 'empty'
      return next
    } catch (error) {
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestScopeKey !== scopeKey()
      ) return null
      analysisState.value = 'error'
      errorMessage.value = '薄弱点诊断暂时无法完成，请保留当前范围后重试。'
      throw error
    } finally {
      if (diagnosisController === controller) diagnosisController = null
    }
  }

  function reset(): void {
    invalidateScopeResults()
    studentScope.value = {
      mode: 'all',
      studentIds: [],
      classId: '',
      classIds: [],
      scoreRateMin: null,
      scoreRateMax: null,
      includeStudentIds: [],
      excludeStudentIds: [],
      useHistoricalFallback: true,
    }
    examScope.value = { mode: 'current', sessionIds: [] }
    diagnosis.value = null
    groupDiagnosisCache.clear()
  }

  return {
    studentScope,
    examScope,
    diagnosis,
    analysisState,
    errorMessage,
    hasCurrentDiagnosis,
    cachedGroupDiagnosis,
    groupDiagnosisBasis,
    rememberGroupDiagnosis,
    knowledgePoints,
    setStudentScope,
    setExamScope,
    analyze,
    reset,
  }
})
