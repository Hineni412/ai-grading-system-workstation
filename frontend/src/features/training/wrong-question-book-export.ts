import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { isAmbiguousWriteError } from '../../api/errors'
import { exportsApi } from '../../api/exports'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { findWrongQuestionBookRequest, previewWrongQuestionBooks, submitWrongQuestionBooks, type WrongQuestionBookPreview } from '../../api/students'
import { isRecord } from '../../api/validation'
import { useJobStore } from '../../stores/jobs'

const STORAGE_KEY = 'ai-grading:wrong-question-book:v2'
export function useWrongQuestionBookExport(scope: () => { studentIds: string[]; volumeId: string; scopeKeys: string[] },
  selectedSessions: Ref<number[] | null>, includeSourceLabel: Ref<boolean>, includeAnswerSpace: Ref<boolean>) {
  const preview = ref<WrongQuestionBookPreview | null>(null)
  const loading = ref(false), submitting = ref(false), downloading = ref(false), downloaded = ref(false)
  const message = ref(''), token = ref(''), jobId = ref<number | null>(null), submittedFingerprint = ref('')
  const jobs = useJobStore()
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastPreviewKey = ''
  let lastScopeKey = ''
  let knownSessionIds: number[] = []
  const normalizedScope = computed(() => ({ curriculum_volume_id: scope().volumeId,
    student_ids: [...new Set(scope().studentIds.map(Number).filter(id => Number.isSafeInteger(id) && id > 0))].sort((a, b) => a - b),
    scope_keys: [...new Set(scope().scopeKeys)].sort() }))
  const previewKey = computed(() => JSON.stringify([normalizedScope.value, selectedSessions.value]))
  const fingerprint = computed(() => JSON.stringify([normalizedScope.value,
    selectedSessions.value === null ? null : [...selectedSessions.value].sort((a, b) => a - b), includeSourceLabel.value, includeAnswerSpace.value]))
  const job = computed(() => jobId.value === null ? null : jobs.jobs[jobId.value] ?? null)
  const busy = computed(() => submitting.value || Boolean(job.value && !TERMINAL_JOB_STATUSES.has(job.value.status)))
  const result = computed(() => job.value?.result ?? {})
  const missing = computed(() => job.value?.status === 'succeeded'
    ? (Array.isArray(result.value.missing_items) ? result.value.missing_items.filter(isRecord) : []) : preview.value?.missing_items ?? [])
  const emptyStudents = computed(() => job.value?.status === 'succeeded'
    ? (Array.isArray(result.value.empty_students) ? result.value.empty_students.filter(isRecord) : []) : preview.value?.empty_students ?? [])
  const failedStudents = computed(() => Array.isArray(result.value.failed_students) ? result.value.failed_students.filter(isRecord) : [])
  const canSelect = computed(() => !token.value && !busy.value)
  function saveRequest() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: token.value, downloaded: downloaded.value, fingerprint: submittedFingerprint.value })) } catch { /* Restricted storage keeps the in-memory request. */ }
  }
  function acceptJob(value: JobResponse) { jobs.track(value); jobId.value = value.id; message.value = '' }
  async function loadPreview() {
    controller?.abort()
    if (!normalizedScope.value.student_ids.length || !normalizedScope.value.curriculum_volume_id) { preview.value = null; loading.value = false; return }
    const active = new AbortController(); controller = active
    const key = previewKey.value
    const scopeKey = JSON.stringify(normalizedScope.value)
    const scopeChanged = scopeKey !== lastScopeKey
    const previousSelection = selectedSessions.value
    const excluded = new Set(knownSessionIds.filter(id => previousSelection !== null && !previousSelection.includes(id)))
    loading.value = true; message.value = ''
    try {
      let response = await previewWrongQuestionBooks({ ...normalizedScope.value, session_ids: scopeChanged ? null : previousSelection }, active.signal)
      if (active.signal.aborted || key !== previewKey.value) return
      if (scopeChanged || previousSelection === null) {
        const available = response.sessions.map(session => session.session_id)
        const selected = available.filter(id => knownSessionIds.length ? !excluded.has(id) : previousSelection === null || previousSelection.includes(id))
        knownSessionIds = available
        lastScopeKey = scopeKey
        selectedSessions.value = selected
        lastPreviewKey = previewKey.value
        if (selected.length !== available.length) {
          response = await previewWrongQuestionBooks({ ...normalizedScope.value, session_ids: selected }, active.signal)
          if (active.signal.aborted || lastPreviewKey !== previewKey.value) return
        }
      }
      preview.value = response
      lastPreviewKey = previewKey.value
    } catch {
      if (active.signal.aborted) return
      preview.value = null; message.value = '导出范围暂时无法读取，请重新加载。'
    } finally { if (controller === active) { controller = null; loading.value = false } }
  }
  async function recover() {
    if (!token.value) return
    submitting.value = true
    try { acceptJob(await findWrongQuestionBookRequest(token.value)) }
    catch { message.value = '尚未确认此次提交的结果，请稍后查询；不会自动重复导出。' }
    finally { submitting.value = false }
  }
  async function submit() {
    if (!preview.value || !preview.value.question_count || loading.value || busy.value || token.value || !selectedSessions.value?.length) return
    token.value = crypto.randomUUID().replace(/-/g, ''); submittedFingerprint.value = fingerprint.value
    saveRequest(); submitting.value = true; message.value = ''
    try { acceptJob(await submitWrongQuestionBooks({ ...normalizedScope.value, session_ids: [...selectedSessions.value],
      include_source_label: includeSourceLabel.value, include_answer_space: includeAnswerSpace.value, client_request_token: token.value })) }
    catch (error) {
      if (isAmbiguousWriteError(error)) { submitting.value = false; await recover() }
      else { token.value = ''; localStorage.removeItem(STORAGE_KEY); message.value = '导出未提交成功，请刷新范围后重试。' }
    } finally { submitting.value = false }
  }
  async function download() {
    if (!job.value || downloading.value || downloaded.value) return
    downloading.value = true; message.value = ''
    try {
      const file = await exportsApi.downloadJobFile(job.value.id)
      const url = URL.createObjectURL(file.blob), link = document.createElement('a')
      link.href = url; link.download = file.filename; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      downloaded.value = true; saveRequest()
    } catch { message.value = '下载未完成或文件已过期，请查询任务后重试；需要时可重新导出。' }
    finally { downloading.value = false }
  }
  function reset() {
    token.value = ''; jobId.value = null; downloaded.value = false; submittedFingerprint.value = ''
    localStorage.removeItem(STORAGE_KEY); lastPreviewKey = ''; void loadPreview()
  }
  watch(fingerprint, key => {
    if (token.value && key !== submittedFingerprint.value) { token.value = ''; jobId.value = null; downloaded.value = false; localStorage.removeItem(STORAGE_KEY) }
  })
  watch(previewKey, key => {
    if (key === lastPreviewKey) return
    controller?.abort(); if (timer) clearTimeout(timer)
    loading.value = Boolean(normalizedScope.value.student_ids.length)
    timer = setTimeout(() => { timer = null; void loadPreview() }, 300)
  }, { immediate: true })
  onMounted(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
      if (isRecord(saved) && typeof saved.token === 'string' && /^[0-9a-f]{32}$/.test(saved.token) && saved.fingerprint === fingerprint.value) {
        token.value = saved.token; submittedFingerprint.value = saved.fingerprint; downloaded.value = saved.downloaded === true; void recover()
      }
    } catch { localStorage.removeItem(STORAGE_KEY) }
  })
  onBeforeUnmount(() => { controller?.abort(); if (timer) clearTimeout(timer) })
  return { preview, loading, submitting, downloading, downloaded, message, token, job, jobs, busy, result,
    missing, emptyStudents, failedStudents, canSelect, loadPreview, recover, submit, download, reset }
}
