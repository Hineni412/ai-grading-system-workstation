import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import { fetchStudents, type StudentSummary } from '../api/students'
import { trainingApi, type TrainingOverview } from '../api/training'
import { requestWithRefresh } from '../api/display-refresh'
import { loadEvidenceScope, saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'

export const useMasteryOverviewStore = defineStore('mastery-overview', () => {
  const overview = shallowRef<TrainingOverview | null>(null)
  const loadState = ref<'idle' | 'loading' | 'ready' | 'error' | 'stale-error'>('idle')
  const errorMessage = ref('')
  const refreshing = ref(false)
  const refreshMessage = ref('')
  const scopeSelection = ref('all')
  const students = shallowRef<StudentSummary[]>([])
  const studentsError = ref(false)
  let rosterRequest: Promise<void> | null = null
  let controller: AbortController | null = null
  let resultKey = ''
  let requestKey = ''
  let request: Promise<void> | null = null

  async function load(volumeId: string | null, force = false, persistScope = true): Promise<void> {
    if (!volumeId) {
      controller?.abort(); overview.value = null; loadState.value = 'idle'; resultKey = ''; requestKey = ''
      refreshing.value = false; refreshMessage.value = ''
      return
    }
    const key = JSON.stringify([volumeId, scopeSelection.value])
    const scope = scopeSelection.value === 'all' ? { mode: 'all' as const }
      : { mode: 'class' as const, class_id: scopeSelection.value, class_ids: [scopeSelection.value] }
    const query = semesterEvidenceQuery(scope, volumeId)
    if (persistScope) saveEvidenceScope(query)
    if (!force && key === resultKey && loadState.value === 'ready') return
    if (!force && key === requestKey && request) return request
    controller?.abort()
    const current = new AbortController()
    controller = current
    requestKey = key
    if (resultKey !== key) overview.value = null
    loadState.value = 'loading'
    errorMessage.value = ''
    refreshing.value = false
    refreshMessage.value = ''
    request = (async () => {
      try {
        await requestWithRefresh(
          onRefreshing => trainingApi.overview(
            { scope: { student_ids: [], ...query.scope },
              exam_scope: { session_ids: [], ...query.exam_scope } },
            current.signal, onRefreshing),
          {
            apply: value => { overview.value = value; resultKey = key; loadState.value = 'ready' },
            report: (value, exhausted) => {
              refreshing.value = value
              refreshMessage.value = exhausted ? '仍在后台计算，可稍后刷新页面' : ''
            },
            aborted: () => current.signal.aborted,
          })
        if (current.signal.aborted) return
        loadState.value = 'ready'
      } catch {
        if (current.signal.aborted) return
        loadState.value = overview.value ? 'stale-error' : 'error'
        errorMessage.value = '学情总览暂时无法读取，请稍后重试。'
      } finally {
        if (controller === current) {
          controller = null; request = null; requestKey = ''
          refreshing.value = false
        }
      }
    })()
    return request
  }
  async function loadStudents() {
    if (rosterRequest) return rosterRequest
    if (students.value.length && !studentsError.value) return
    rosterRequest = (async () => {
      try { students.value = await fetchStudents(); studentsError.value = false }
      catch { studentsError.value = true }
      finally { rosterRequest = null }
    })()
    return rosterRequest
  }
  function restoreScope() {
    const saved = loadEvidenceScope()?.scope
    const classes = saved?.class_ids?.length ? saved.class_ids : saved?.class_id ? [saved.class_id] : []
    scopeSelection.value = saved?.mode === 'class' && classes.length === 1 ? classes[0]! : 'all'
  }
  function peek(volumeId: string | null) {
    restoreScope()
    return load(volumeId, false, false)
  }
  function activate(volumeId: string | null) {
    restoreScope()
    void loadStudents()
    void load(volumeId)
  }
  function selectScope(selection: string, volumeId: string | null) {
    scopeSelection.value = selection
    void load(volumeId)
  }
  return { overview, loadState, errorMessage, refreshing, refreshMessage, scopeSelection, students, studentsError,
    load, loadStudents, peek, activate, selectScope }
})
