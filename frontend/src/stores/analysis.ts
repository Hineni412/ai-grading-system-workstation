import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  fetchQuestionAnalysis,
  fetchStudentAnalysis,
  type QuestionAnalysisItem,
  type QuestionAnalysisResponse,
  type StudentAnalysisItem,
  type StudentAnalysisResponse,
} from '../api/analysis'
import { ApiError } from '../api/errors'
import { fetchGraphRows, type GraphRowsResponse } from '../api/graph'
import type { ResourceState } from './workbench'

export type QuestionLoader = (
  sessionId: number,
  className: string | null,
  signal: AbortSignal,
) => Promise<QuestionAnalysisResponse>
export type StudentLoader = (
  sessionId: number,
  questionId: string,
  className: string | null,
  signal: AbortSignal,
) => Promise<StudentAnalysisResponse>
export type GraphLoader = (
  sessionId: number,
  className: string,
  signal: AbortSignal,
) => Promise<GraphRowsResponse>

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') ||
    (error instanceof ApiError && error.kind === 'cancelled')
  )
}

export const useAnalysisStore = defineStore('analysis', () => {
  const sessionId = ref<number | null>(null)
  const questions = ref<QuestionAnalysisItem[]>([])
  const classes = ref<string[]>([])
  const students = ref<StudentAnalysisItem[]>([])
  const graph = ref<GraphRowsResponse | null>(null)
  const questionsState = ref<ResourceState>('idle')
  const studentsState = ref<ResourceState>('idle')
  const graphState = ref<ResourceState>('idle')
  const questionsError = ref('')
  const studentsError = ref('')
  const graphError = ref('')
  const questionsUpdatedAt = ref<string | null>(null)
  const studentsUpdatedAt = ref<string | null>(null)
  const graphUpdatedAt = ref<string | null>(null)

  let questionsController: AbortController | null = null
  let studentsController: AbortController | null = null
  let graphController: AbortController | null = null
  let questionsGeneration = 0
  let studentsGeneration = 0
  let graphGeneration = 0

  function resetForSession(nextSessionId: number | null): void {
    if (sessionId.value === nextSessionId) return
    questionsController?.abort()
    studentsController?.abort()
    graphController?.abort()
    questionsController = null
    studentsController = null
    graphController = null
    questionsGeneration += 1
    studentsGeneration += 1
    graphGeneration += 1
    sessionId.value = nextSessionId
    questions.value = []
    classes.value = []
    students.value = []
    graph.value = null
    questionsState.value = 'idle'
    studentsState.value = 'idle'
    graphState.value = 'idle'
    questionsError.value = ''
    studentsError.value = ''
    graphError.value = ''
    questionsUpdatedAt.value = null
    studentsUpdatedAt.value = null
    graphUpdatedAt.value = null
  }

  async function loadQuestions(
    nextSessionId: number,
    className: string | null,
    loader: QuestionLoader = fetchQuestionAnalysis,
  ): Promise<void> {
    resetForSession(nextSessionId)
    questionsController?.abort()
    const controller = new AbortController()
    questionsController = controller
    const generation = ++questionsGeneration
    questionsState.value = 'loading'
    questionsError.value = ''
    try {
      const loaded = await loader(nextSessionId, className, controller.signal)
      if (generation !== questionsGeneration || sessionId.value !== nextSessionId) return
      questions.value = [...loaded.items]
      classes.value = [...loaded.classes]
      questionsState.value = loaded.items.length === 0 ? 'empty' : 'ready'
      questionsUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== questionsGeneration || sessionId.value !== nextSessionId) return
      if (isCancelled(error)) {
        questionsState.value = questionsUpdatedAt.value === null ? 'idle' : questions.value.length === 0 ? 'empty' : 'ready'
        return
      }
      questionsState.value = questionsUpdatedAt.value === null ? 'error' : 'stale-error'
      questionsError.value = '题目分析暂时无法更新'
    } finally {
      if (questionsController === controller) questionsController = null
    }
  }

  async function loadStudents(
    nextSessionId: number,
    questionId: string,
    className: string | null,
    loader: StudentLoader = fetchStudentAnalysis,
  ): Promise<void> {
    resetForSession(nextSessionId)
    studentsController?.abort()
    const controller = new AbortController()
    studentsController = controller
    const generation = ++studentsGeneration
    studentsState.value = 'loading'
    studentsError.value = ''
    try {
      const loaded = await loader(nextSessionId, questionId, className, controller.signal)
      if (generation !== studentsGeneration || sessionId.value !== nextSessionId) return
      students.value = [...loaded.items]
      studentsState.value = loaded.items.length === 0 ? 'empty' : 'ready'
      studentsUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== studentsGeneration || sessionId.value !== nextSessionId) return
      if (isCancelled(error)) {
        studentsState.value = studentsUpdatedAt.value === null ? 'idle' : students.value.length === 0 ? 'empty' : 'ready'
        return
      }
      studentsState.value = studentsUpdatedAt.value === null ? 'error' : 'stale-error'
      studentsError.value = '学生明细暂时无法更新'
    } finally {
      if (studentsController === controller) studentsController = null
    }
  }

  async function loadGraph(
    nextSessionId: number,
    className: string,
    loader: GraphLoader = fetchGraphRows,
  ): Promise<void> {
    resetForSession(nextSessionId)
    if (!className.trim()) {
      graphController?.abort()
      graphController = null
      graphGeneration += 1
      graph.value = null
      graphState.value = 'idle'
      graphError.value = ''
      graphUpdatedAt.value = null
      return
    }
    graphController?.abort()
    const controller = new AbortController()
    graphController = controller
    const generation = ++graphGeneration
    graphState.value = 'loading'
    graphError.value = ''
    try {
      const loaded = await loader(nextSessionId, className, controller.signal)
      if (generation !== graphGeneration || sessionId.value !== nextSessionId) return
      graph.value = loaded
      graphState.value = loaded.nodes.length === 0 ? 'empty' : 'ready'
      graphUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== graphGeneration || sessionId.value !== nextSessionId) return
      if (isCancelled(error)) {
        graphState.value = graphUpdatedAt.value === null ? 'idle' : graph.value?.nodes.length === 0 ? 'empty' : 'ready'
        return
      }
      graphState.value = graphUpdatedAt.value === null ? 'error' : 'stale-error'
      graphError.value = '标签覆盖暂时无法更新'
    } finally {
      if (graphController === controller) graphController = null
    }
  }

  return {
    sessionId,
    questions,
    classes,
    students,
    graph,
    questionsState,
    studentsState,
    graphState,
    questionsError,
    studentsError,
    graphError,
    questionsUpdatedAt,
    studentsUpdatedAt,
    graphUpdatedAt,
    loadQuestions,
    loadStudents,
    loadGraph,
    resetForSession,
  }
})
