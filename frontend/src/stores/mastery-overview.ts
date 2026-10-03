import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import { fetchStudents, type StudentSummary } from '../api/students'
import { trainingApi, type TrainingOverview } from '../api/training'
import { loadEvidenceScope, saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'

export const useMasteryOverviewStore = defineStore('mastery-overview', () => {
  const overview = shallowRef<TrainingOverview | null>(null)
  const loadState = ref<'idle' | 'loading' | 'ready' | 'error' | 'stale-error'>('idle')
  const errorMessage = ref('')
  const scopeSelection = ref('all')
  const students = shallowRef<StudentSummary[]>([])
  const studentsError = ref(false)
  let rosterRequest: Promise<void> | null = null
  let controller: AbortController | null = null
  let resultKey = ''
  let requestKey = ''
  let request: Promise<void> | null = null

  async function load(volumeId: string | null, force = false): Promise<void> {
    if (!volumeId) {
      controller?.abort(); overview.value = null; loadState.value = 'idle'; resultKey = ''; requestKey = ''
      return
    }
    const key = JSON.stringify([volumeId, scopeSelection.value])
    const scope = scopeSelection.value === 'all' ? { mode: 'all' as const }
      : { mode: 'class' as const, class_id: scopeSelection.value, class_ids: [scopeSelection.value] }
    const query = semesterEvidenceQuery(scope, volumeId)
    saveEvidenceScope(query)
    if (!force && key === resultKey && loadState.value === 'ready') return
    if (!force && key === requestKey && request) return request
    controller?.abort()
    const current = new AbortController()
    controller = current
    requestKey = key
    if (resultKey !== key) overview.value = null
    loadState.value = 'loading'
    errorMessage.value = ''
    request = (async () => {
      try {
        const result = await trainingApi.overview({ scope: { student_ids: [], ...query.scope },
          exam_scope: { session_ids: [], ...query.exam_scope } }, current.signal)
        if (current.signal.aborted) return
        overview.value = result; resultKey = key; loadState.value = 'ready'
      } catch {
        if (current.signal.aborted) return
        loadState.value = overview.value ? 'stale-error' : 'error'
        errorMessage.value = '学情总览暂时无法读取，请稍后重试。'
      } finally {
        if (controller === current) { controller = null; request = null; requestKey = '' }
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
  function activate(volumeId: string | null) {
    const saved = loadEvidenceScope()?.scope
    const classes = saved?.class_ids?.length ? saved.class_ids : saved?.class_id ? [saved.class_id] : []
    scopeSelection.value = saved?.mode === 'class' && classes.length === 1 ? classes[0]! : 'all'
    void loadStudents()
    void load(volumeId)
  }
  function selectScope(selection: string, volumeId: string | null) {
    scopeSelection.value = selection
    void load(volumeId)
  }
  return { overview, loadState, errorMessage, scopeSelection, students, studentsError,
    load, loadStudents, activate, selectScope }
})
