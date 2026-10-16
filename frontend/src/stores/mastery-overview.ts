import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref, shallowRef } from 'vue'
import { fetchStudents, type StudentSummary } from '../api/students'
import { trainingApi, type TrainingKeyStepTypeStatsResponse, type TrainingOverview, type TrainingOverviewNode, type TrainingOverviewExamQuestion } from '../api/training'
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
  const results = new Map<string, TrainingOverview>()
  function invalidate(event: Event) {
    const path = (event as CustomEvent<string>).detail
    if (/^\/api\/(question-bank|students|sessions|review|training\/(evidence|assessments|submissions))\b/.test(path)) {
      results.clear(); resultKey = ''; keyStepKey = ''
      controller?.abort(); keyStepController?.abort()
      controller = null; request = null; requestKey = ''
    }
  }
  window.addEventListener('api-data-written', invalidate)
  onScopeDispose(() => window.removeEventListener('api-data-written', invalidate))

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
    if (!force && key === resultKey && loadState.value === 'ready' && results.get(key) === overview.value) return
    if (!force && key === requestKey && request) return request
    controller?.abort()
    const cached = results.get(key)
    if (!force && cached) {
      overview.value = cached; resultKey = key; loadState.value = 'ready'
      refreshing.value = false; refreshMessage.value = ''; errorMessage.value = ''
      return
    }
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
        if (overview.value && !refreshing.value) {
          results.delete(key); results.set(key, overview.value)
          while (results.size > 16) results.delete(results.keys().next().value!)
        }
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

  // ---- 题卡考情（只读派生数据） ----

  const typeNodeByKey = computed(() => {
    const map = new Map<string, TrainingOverviewNode>()
    for (const node of overview.value?.nodes ?? []) {
      if (node.kind === 'type') map.set(node.knowledge_key, node)
    }
    return map
  })

  const examUsesByQuestion = computed(() => {
    const map = new Map<number, { session: string; label: string; rate: number | null }[]>()
    const seen = new Map<number, Set<string>>()
    const add = (item: TrainingOverviewExamQuestion | null | undefined) => {
      if (!item) return
      const id = item.bank_question_id ?? 0
      if (id <= 0) return
      const session = item.session_name.replace(/学情反馈$/, '') || item.session_name
      const key = String(item.session_id)
      let keys = seen.get(id)
      if (!keys) { keys = new Set(); seen.set(id, keys) }
      if (keys.has(key)) return
      keys.add(key)
      const rows = map.get(id) ?? []
      rows.push({ session, label: item.question_label, rate: item.class_rate })
      map.set(id, rows)
    }
    for (const node of overview.value?.nodes ?? []) {
      if (node.kind !== 'type') continue
      add(node.typical_question)
      for (const item of node.other_questions ?? []) add(item)
    }
    return map
  })

  const keyStepStats = shallowRef<TrainingKeyStepTypeStatsResponse | null>(null)
  const keyStepState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  let keyStepController: AbortController | null = null
  let keyStepKey = ''

  function ensureKeySteps(volumeId: string | null) {
    if (!volumeId) {
      keyStepController?.abort()
      keyStepStats.value = null
      keyStepState.value = 'idle'
      keyStepKey = ''
      return
    }
    const key = JSON.stringify([volumeId, scopeSelection.value])
    if (key === keyStepKey && (keyStepState.value === 'loading' || keyStepState.value === 'ready')) return
    keyStepController?.abort()
    const current = new AbortController()
    keyStepController = current
    if (key !== keyStepKey) keyStepStats.value = null
    keyStepKey = key
    const scope = scopeSelection.value === 'all' ? { mode: 'all' as const }
      : { mode: 'class' as const, class_id: scopeSelection.value, class_ids: [scopeSelection.value] }
    const query = semesterEvidenceQuery(scope, volumeId)
    keyStepState.value = 'loading'
    trainingApi.keyStepTypeStats(
      { scope: { student_ids: [], ...query.scope },
        exam_scope: { session_ids: [], ...query.exam_scope } },
      current.signal,
    ).then(value => {
      if (current.signal.aborted) return
      keyStepStats.value = value
      keyStepState.value = 'ready'
    }).catch(() => {
      if (current.signal.aborted) return
      keyStepState.value = 'error'
    })
  }

  return { overview, loadState, errorMessage, refreshing, refreshMessage, scopeSelection, students, studentsError,
    typeNodeByKey, examUsesByQuestion, keyStepStats, keyStepState,
    load, loadStudents, peek, activate, selectScope, ensureKeySteps }
})
