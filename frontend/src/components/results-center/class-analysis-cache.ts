import type { ClassAnalysisResponse } from '../../api/class-analysis'

// 总览与试题诊断共用同一份 class-analysis summary 响应；
// 成绩刷新、错因整理完成或错因修改后按考试失效。
const cache = new Map<string, ClassAnalysisResponse>()
const MAX_ENTRIES = 12

export function cachedClassAnalysis(
  sessionId: number,
  className: string,
): ClassAnalysisResponse | null {
  return cache.get(`${sessionId}::${className}`) ?? null
}

export function rememberClassAnalysis(
  sessionId: number,
  className: string,
  value: ClassAnalysisResponse,
): void {
  if (cache.size >= MAX_ENTRIES) {
    cache.delete(cache.keys().next().value!)
  }
  cache.set(`${sessionId}::${className}`, value)
}

export function invalidateClassAnalysis(sessionId: number): void {
  for (const key of [...cache.keys()]) {
    if (key.startsWith(`${sessionId}::`)) cache.delete(key)
  }
}
