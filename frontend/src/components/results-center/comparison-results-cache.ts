import { fetchResultsCenter, type ResultsCenterResponse } from '../../api/results-center'

/**
 * 上一场对比考试的成绩中心数据：同一场考试只请求一次（GET），
 * 供考情总览面板的 previousResults 与学生抽屉的对比行共用。
 */
const cache = new Map<number, Promise<ResultsCenterResponse>>()

export function loadComparisonResults(sessionId: number): Promise<ResultsCenterResponse> {
  const cached = cache.get(sessionId)
  if (cached) return cached
  const promise = fetchResultsCenter(sessionId)
  promise.catch(() => {
    if (cache.get(sessionId) === promise) cache.delete(sessionId)
  })
  cache.set(sessionId, promise)
  return promise
}
