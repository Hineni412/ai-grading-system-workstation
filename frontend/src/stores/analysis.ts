import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  fetchQuestionAnalysis,
  fetchStudentAnalysis,
  type AnalysisScope,
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
  page?: number,
) => Promise<QuestionAnalysisResponse>
export type StudentLoader = (
  sessionId: number,
  questionId: string,
  className: string | null,
  signal: AbortSignal,
  page?: number,
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
  const questionsScope = ref<AnalysisScope | null>(null)
  const studentsScope = ref<AnalysisScope | null>(null)
  const questionsState = ref<ResourceState>('idle')
  const studentsState = ref<ResourceState>('idle')
  const graphState = ref<ResourceState>('idle')
  const questionsError = ref('')
  const studentsError = ref('')
  const graphError = ref('')
  const questionsUpdatedAt = ref<string | null>(null)
  const studentsUpdatedAt = ref<string | null>(null)
  const graphUpdatedAt = ref<string | null>(null)
  const questionsTotal = ref(0)
  const questionsPage = ref(1)
  const questionsPageSize = ref(100)
  const questionsTotalPages = ref(0)
  const studentsTotal = ref(0)
  const studentsPage = ref(1)
  const studentsPageSize = ref(100)
  const studentsTotalPages = ref(0)

  let questionsController: AbortController | null = null
  let studentsController: AbortController | null = null
  let graphController: AbortController | null = null
  let questionsGeneration = 0
  let studentsGeneration = 0
  let graphGeneration = 0
  let questionsRequestKey: string | null = null
  let studentsRequestKey: string | null = null
  let graphRequestKey: string | null = null

  function classScope(className: string | null): string | null {
    return className === null ? null : className.trim()
  }

  function appendUnique<T>(current: T[], incoming: T[], key: (item: T) => string): T[] {
    const seen = new Set(current.map(key))
    const appended = incoming.filter((item) => {
      const value = key(item)
      if (seen.has(value)) return false
      seen.add(value)
      return true
    })
    return [...current, ...appended]
  }

  function matchesAnalysisScope(
    scope: AnalysisScope,
    expectedSessionId: number,
    expectedClassName: string | null,
    expectedQuestionId: string | null,
  ): boolean {
    return (
      scope.session_id === expectedSessionId &&
      scope.class_name === expectedClassName &&
      scope.question_id === expectedQuestionId
    )
  }

  function matchesGraphScope(
    response: GraphRowsResponse,
    expectedSessionId: number,
    expectedClassName: string,
  ): boolean {
    return (
      response.scope.mode === 'class' &&
      response.scope.class_id === expectedClassName &&
      response.exam_scope.mode === 'current' &&
      response.exam_scope.session_ids.length === 1 &&
      response.exam_scope.session_ids[0] === expectedSessionId
    )
  }

  function resetStudents(): void {
    studentsController?.abort()
    studentsController = null
    studentsGeneration += 1
    studentsRequestKey = null
    students.value = []
    studentsScope.value = null
    studentsState.value = 'idle'
    studentsError.value = ''
    studentsUpdatedAt.value = null
    studentsTotal.value = 0
    studentsPage.value = 1
    studentsPageSize.value = 100
    studentsTotalPages.value = 0
  }

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
    questionsRequestKey = null
    studentsRequestKey = null
    graphRequestKey = null
    sessionId.value = nextSessionId
    questions.value = []
    classes.value = []
    students.value = []
    graph.value = null
    questionsScope.value = null
    studentsScope.value = null
    questionsState.value = 'idle'
    studentsState.value = 'idle'
    graphState.value = 'idle'
    questionsError.value = ''
    studentsError.value = ''
    graphError.value = ''
    questionsUpdatedAt.value = null
    studentsUpdatedAt.value = null
    graphUpdatedAt.value = null
    questionsTotal.value = 0
    questionsPage.value = 1
    questionsPageSize.value = 100
    questionsTotalPages.value = 0
    studentsTotal.value = 0
    studentsPage.value = 1
    studentsPageSize.value = 100
    studentsTotalPages.value = 0
  }

  async function loadQuestions(
    nextSessionId: number,
    className: string | null,
    loader: QuestionLoader = fetchQuestionAnalysis,
    page = 1,
    append = false,
  ): Promise<void> {
    resetForSession(nextSessionId)
    questionsController?.abort()
    const requestKey = JSON.stringify([nextSessionId, classScope(className)])
    if (questionsRequestKey !== requestKey) {
      questions.value = []
      classes.value = []
      questionsScope.value = null
      questionsState.value = 'idle'
      questionsError.value = ''
      questionsUpdatedAt.value = null
      questionsTotal.value = 0
      questionsPage.value = 1
      questionsPageSize.value = 100
      questionsTotalPages.value = 0
    }
    questionsRequestKey = requestKey
    const controller = new AbortController()
    questionsController = controller
    const generation = ++questionsGeneration
    questionsState.value = 'loading'
    questionsError.value = ''
    try {
      const loaded = await loader(nextSessionId, className, controller.signal, page)
      if (generation !== questionsGeneration || sessionId.value !== nextSessionId) return
      if (!matchesAnalysisScope(loaded.scope, nextSessionId, classScope(className), null)) {
        throw new Error('Question analysis scope mismatch')
      }
      questions.value = append
        ? appendUnique(questions.value, loaded.items, (item) => `${item.class_name}\u0000${item.question_id}`)
        : [...loaded.items]
      classes.value = [...loaded.classes]
      questionsScope.value = { ...loaded.scope }
      questionsTotal.value = loaded.total
      questionsPage.value = loaded.page
      questionsPageSize.value = loaded.page_size
      questionsTotalPages.value = loaded.total_pages
      questionsState.value = questions.value.length === 0 ? 'empty' : 'ready'
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

  async function loadMoreQuestions(loader: QuestionLoader = fetchQuestionAnalysis): Promise<void> {
    if (sessionId.value === null || questionsScope.value === null || questionsPage.value >= questionsTotalPages.value) return
    await loadQuestions(
      sessionId.value,
      questionsScope.value.class_name,
      loader,
      questionsPage.value + 1,
      true,
    )
  }

  async function loadStudents(
    nextSessionId: number,
    questionId: string,
    className: string | null,
    loader: StudentLoader = fetchStudentAnalysis,
    page = 1,
    append = false,
  ): Promise<void> {
    resetForSession(nextSessionId)
    studentsController?.abort()
    const requestKey = JSON.stringify([nextSessionId, questionId.trim(), classScope(className)])
    if (studentsRequestKey !== requestKey) {
      students.value = []
      studentsScope.value = null
      studentsState.value = 'idle'
      studentsError.value = ''
      studentsUpdatedAt.value = null
      studentsTotal.value = 0
      studentsPage.value = 1
      studentsPageSize.value = 100
      studentsTotalPages.value = 0
    }
    studentsRequestKey = requestKey
    const controller = new AbortController()
    studentsController = controller
    const generation = ++studentsGeneration
    studentsState.value = 'loading'
    studentsError.value = ''
    try {
      const loaded = await loader(nextSessionId, questionId, className, controller.signal, page)
      if (generation !== studentsGeneration || sessionId.value !== nextSessionId) return
      if (!matchesAnalysisScope(
        loaded.scope,
        nextSessionId,
        classScope(className),
        questionId.trim(),
      )) {
        throw new Error('Student analysis scope mismatch')
      }
      students.value = append
        ? appendUnique(students.value, loaded.items, (item) => String(item.detail_id))
        : [...loaded.items]
      studentsScope.value = { ...loaded.scope }
      studentsTotal.value = loaded.total
      studentsPage.value = loaded.page
      studentsPageSize.value = loaded.page_size
      studentsTotalPages.value = loaded.total_pages
      studentsState.value = students.value.length === 0 ? 'empty' : 'ready'
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

  async function loadMoreStudents(loader: StudentLoader = fetchStudentAnalysis): Promise<void> {
    const scope = studentsScope.value
    if (sessionId.value === null || scope?.question_id === null || scope === null || studentsPage.value >= studentsTotalPages.value) return
    await loadStudents(
      sessionId.value,
      scope.question_id,
      scope.class_name,
      loader,
      studentsPage.value + 1,
      true,
    )
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
      graphRequestKey = null
      graph.value = null
      graphState.value = 'idle'
      graphError.value = ''
      graphUpdatedAt.value = null
      return
    }
    graphController?.abort()
    const normalizedClassName = className.trim()
    const requestKey = JSON.stringify([nextSessionId, normalizedClassName])
    if (graphRequestKey !== requestKey) {
      graph.value = null
      graphState.value = 'idle'
      graphError.value = ''
      graphUpdatedAt.value = null
    }
    graphRequestKey = requestKey
    const controller = new AbortController()
    graphController = controller
    const generation = ++graphGeneration
    graphState.value = 'loading'
    graphError.value = ''
    try {
      const loaded = await loader(nextSessionId, className, controller.signal)
      if (generation !== graphGeneration || sessionId.value !== nextSessionId) return
      if (!matchesGraphScope(loaded, nextSessionId, normalizedClassName)) {
        throw new Error('Graph scope mismatch')
      }
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
    questionsScope,
    studentsScope,
    questionsState,
    studentsState,
    graphState,
    questionsError,
    studentsError,
    graphError,
    questionsUpdatedAt,
    studentsUpdatedAt,
    graphUpdatedAt,
    questionsTotal,
    questionsPage,
    questionsPageSize,
    questionsTotalPages,
    studentsTotal,
    studentsPage,
    studentsPageSize,
    studentsTotalPages,
    loadQuestions,
    loadMoreQuestions,
    loadStudents,
    loadMoreStudents,
    loadGraph,
    resetStudents,
    resetForSession,
  }
})
