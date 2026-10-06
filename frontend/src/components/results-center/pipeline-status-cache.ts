import { reportPipelineApi, type ReportPipelineStatus } from '../../api/report-pipeline'

// AI 整理弹窗的状态读取缓存：打开时先显示上次结果，后台再核对；
// 任务进入终态后按考试失效。写入顺序与 class-analysis-cache 一致。
const cache = new Map<number, ReportPipelineStatus>()
const requests = new Map<number, Promise<ReportPipelineStatus>>()
const MAX_ENTRIES = 12

export function cachedPipelineStatus(sessionId: number): ReportPipelineStatus | null {
  return cache.get(sessionId) ?? null
}

export function requestPipelineStatus(sessionId: number): Promise<ReportPipelineStatus> {
  let request = requests.get(sessionId)
  if (!request) {
    request = reportPipelineApi.getStatus(sessionId)
      .then(value => {
        if (cache.size >= MAX_ENTRIES) cache.delete(cache.keys().next().value!)
        cache.set(sessionId, value)
        return value
      })
      .finally(() => {
        if (requests.get(sessionId) === request) requests.delete(sessionId)
      })
    requests.set(sessionId, request)
  }
  return request
}

export function invalidatePipelineStatus(sessionId?: number): void {
  if (sessionId === undefined) {
    cache.clear()
    return
  }
  cache.delete(sessionId)
}
