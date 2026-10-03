import { onBeforeUnmount, ref } from 'vue'
import { exportsApi, type AnalysisPreflight } from '../../api/exports'
import { useJobStore } from '../../stores/jobs'

/** 阅读器与导出行共用已有预估确认流程，生成任务只保存叙述。 */
export function usePersonalReportGeneration() {
  const jobs = useJobStore()
  const open = ref(false)
  const loading = ref(false)
  const submitting = ref(false)
  const preflight = ref<AnalysisPreflight | null>(null)
  const error = ref('')
  let controller: AbortController | null = null
  let target: {sessionId: number; studentIds: number[]} | null = null
  function close() { controller?.abort(); open.value = false; loading.value = false; target = null }
  async function prepare(sessionId: number, studentIds: number[]) {
    close()
    const next = new AbortController()
    controller = next
    target = { sessionId, studentIds: [...studentIds] }
    open.value = true
    loading.value = true
    error.value = ''
    preflight.value = null
    try {
      const value = await exportsApi.getAnalysisPreflight(sessionId, 'personal_analysis_html', next.signal, studentIds)
      if (!next.signal.aborted) preflight.value = value
    } catch {
      if (!next.signal.aborted) { error.value = '生成条件暂时无法读取，请稍后重试。'; open.value = false }
    } finally { if (controller === next) loading.value = false }
  }
  async function confirm() {
    if (!target || submitting.value || loading.value || !preflight.value?.configured) return null
    const selected = target
    submitting.value = true
    try {
      const job = await exportsApi.submitReport(selected.sessionId, 'personal_analysis_html', false,
        undefined, undefined, { student_ids: selected.studentIds, publish: false })
      await jobs.track(job)
      close()
      return job
    } catch { error.value = '生成请求未能提交，请稍后重试。'; return null }
    finally { submitting.value = false }
  }
  onBeforeUnmount(close)
  return { open, loading, submitting, preflight, error, prepare, confirm, close }
}
