import {
  fetchReviewRubric,
  type ReviewRubricSection,
} from '../../api/review'

const cache = new Map<string, ReviewRubricSection | null>()
const inflight = new Map<string, {
  controller: AbortController
  request: Promise<ReviewRubricSection | null>
}>()

export function reviewRubricCacheKey(sessionId: number, questionId: string): string {
  return `${sessionId}::${questionId.trim()}`
}

export function hasReviewRubric(key: string): boolean {
  return cache.has(key)
}

export function cachedReviewRubric(key: string): ReviewRubricSection | null | undefined {
  return cache.get(key)
}

export function loadReviewRubric(
  sessionId: number,
  questionId: string,
): Promise<ReviewRubricSection | null> {
  const key = reviewRubricCacheKey(sessionId, questionId)
  if (cache.has(key)) return Promise.resolve(cache.get(key) ?? null)
  const pending = inflight.get(key)
  if (pending) return pending.request

  const controller = new AbortController()
  const request = fetchReviewRubric(sessionId, questionId, controller.signal)
    .then((loaded) => {
      if (!controller.signal.aborted) cache.set(key, loaded)
      return loaded
    })
    .finally(() => {
      inflight.delete(key)
    })
  inflight.set(key, { controller, request })
  return request
}

export function clearReviewRubrics(): void {
  for (const pending of inflight.values()) pending.controller.abort()
  inflight.clear()
  cache.clear()
}
