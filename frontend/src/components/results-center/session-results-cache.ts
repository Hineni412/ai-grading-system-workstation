import { fetchResultsCenter, type ResultsCenterResponse } from '../../api/results-center'

/**
 * 当前考试的成绩中心数据：复核页原卷题框与成绩中心共用一份数据，
 * 同一场考试只请求一次；保存分数后由复核页调用 invalidateSessionResults
 * 让下一次查看重新读取。
 */
const cache = new Map<number, Promise<ResultsCenterResponse>>()

export function loadSessionResults(sessionId: number): Promise<ResultsCenterResponse> {
  const cached = cache.get(sessionId)
  if (cached) return cached
  const promise = Promise.resolve().then(() => fetchResultsCenter(sessionId))
  promise.catch(() => {
    if (cache.get(sessionId) === promise) cache.delete(sessionId)
  })
  cache.set(sessionId, promise)
  return promise
}

export function invalidateSessionResults(sessionId: number): void {
  cache.delete(sessionId)
}
