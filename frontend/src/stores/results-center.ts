import { ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchResultsCenter,
  type ResultsCenterResponse,
} from '../api/results-center'

export type ResultsCenterState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'stale-error'
  | 'error'

export type ResultsCenterLoader = (
  sessionId: number,
  signal: AbortSignal,
) => Promise<ResultsCenterResponse>

export interface ReviewNavigation {
  sessionId: number
  studentIds: number[]
}

export interface ResultsViewState {
  sessionId: number
  fullPath: string
  searchQuery: string
  selectedClass: string | null
  matrixSort: {
    key: 'student' | 'total' | 'question'
    direction: 'ascending' | 'descending'
    questionId: string | null
  }
  scrollTop: number
  scrollLeft: number
  matrixScrollTop: number
  matrixScrollLeft: number
}

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object'
      && error !== null
      && 'name' in error
      && error.name === 'AbortError')
    || (error instanceof ApiError && error.kind === 'cancelled')
  )
}

export const useResultsCenterStore = defineStore('results-center', () => {
  const sessionId = ref<number | null>(null)
  const results = ref<ResultsCenterResponse | null>(null)
  const state = ref<ResultsCenterState>('idle')
  const errorMessage = ref('')
  const updatedAt = ref<string | null>(null)
  const viewState = ref<ResultsViewState | null>(null)
  const reportReturn = ref<{sessionId: number; studentId: number; reportSessionId: number} | null>(null)
  const reviewNavigation = ref<ReviewNavigation | null>(null)

  let controller: AbortController | null = null
  let generation = 0

  function reset(): void {
    controller?.abort()
    controller = null
    generation += 1
    sessionId.value = null
    results.value = null
    state.value = 'idle'
    errorMessage.value = ''
    updatedAt.value = null
    viewState.value = null
    reviewNavigation.value = null
    reportReturn.value = null
  }

  function setReviewNavigation(value: ReviewNavigation | null): void {
    reviewNavigation.value = value
  }

  async function load(
    nextSessionId: number,
    loader: ResultsCenterLoader = fetchResultsCenter,
  ): Promise<void> {
    if (!Number.isSafeInteger(nextSessionId) || nextSessionId <= 0) {
      throw new Error('Invalid session id')
    }
    controller?.abort()
    const nextController = new AbortController()
    controller = nextController
    const requestGeneration = ++generation
    const sameSession = sessionId.value === nextSessionId
    if (!sameSession) {
      sessionId.value = nextSessionId
      results.value = null
      updatedAt.value = null
    }
    state.value = 'loading'
    errorMessage.value = ''

    try {
      const loaded = await loader(nextSessionId, nextController.signal)
      if (
        requestGeneration !== generation
        || nextController.signal.aborted
        || sessionId.value !== nextSessionId
      ) return
      if (loaded.session_id !== nextSessionId) {
        throw new Error('Results center scope mismatch')
      }
      results.value = loaded
      state.value = loaded.students.length === 0 ? 'empty' : 'ready'
      updatedAt.value = new Date().toISOString()
    } catch (error) {
      if (
        requestGeneration !== generation
        || nextController.signal.aborted
        || sessionId.value !== nextSessionId
      ) return
      if (isCancelled(error)) {
        state.value = updatedAt.value === null
          ? 'idle'
          : results.value?.students.length ? 'ready' : 'empty'
        return
      }
      state.value = updatedAt.value === null ? 'error' : 'stale-error'
      errorMessage.value = error instanceof ApiError && error.message.trim()
        ? error.message
        : '当前考试的成绩暂时无法读取，请稍后重试。'
    } finally {
      if (controller === nextController) controller = null
    }
  }

  return {
    sessionId,
    results,
    state,
    errorMessage,
    updatedAt,
    viewState,
    reviewNavigation,
    reportReturn,
    setReviewNavigation,
    load,
    reset,
  }
})
