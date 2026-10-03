import { computed, reactive, ref, shallowRef, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { assemblyApi, fetchAssemblyCandidates, fetchAssemblyExams, type AssemblyExamResult, type AssemblyAssistantRequest, type AssemblyAssistantResult, type AssemblyQuestion } from '../api/assembly'
import { ApiError } from '../api/errors'

const STORAGE_KEY = 'ai-grading:assembly-assistant-filters:v1'
const PAGE_SIZE = 12

export const useAssemblyAssistantStore = defineStore('assembly-assistant', () => {
  let previous: Partial<AssemblyAssistantRequest> = {}
  try { previous = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') ?? {} } catch { /* Empty preferences. */ }
  const filters = reactive<AssemblyAssistantRequest>({
    class_id: typeof previous.class_id === 'string' ? previous.class_id : '',
    class_ids: Array.isArray(previous.class_ids) ? previous.class_ids : previous.class_id ? [previous.class_id] : [],
    session_ids: Array.isArray(previous.session_ids) ? previous.session_ids : [],
    curriculum_volume_id: '', chapter_id: '', teaching_progress_chapter_id: '', target_keys: null,
    question_type: '', difficulty_min: 1, difficulty_max: 8,
    exclude_exam_originals: true, exclude_recent: true,
  })
  const result = shallowRef<AssemblyAssistantResult | null>(null)
  const examResult = shallowRef<AssemblyExamResult | null>(null)
  const examState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const view = ref<'exam' | 'skill'>('exam')
  const threshold = ref(70)
  const sort = ref<'loss' | 'exam'>('loss')
  const selectedExamKey = ref('')
  const includeTraining = ref(true)
  let examController: AbortController | null = null
  let examSerial = 0
  const questions = shallowRef<AssemblyQuestion[]>([])
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const message = ref('')
  const appliedKey = ref('')
  const visibleCount = ref(PAGE_SIZE)
  const loadingMore = ref(false)
  const waiting = ref(false)
  const previewCache = new Map<number, AssemblyQuestion>()
  const resultCache = new Map<string, AssemblyAssistantResult>()
  const candidateCounts = new Map<string, number>()
  let scheduled: ReturnType<typeof setTimeout> | undefined
  let serial = 0
  let controller: AbortController | null = null
  let lastTargetSearch = -Infinity
  const requestKey = computed(() => JSON.stringify(filters))
  const isStale = computed(() => Boolean(result.value) && appliedKey.value !== requestKey.value)
  const canSearch = computed(() => Boolean((filters.class_ids?.length || filters.class_id) && filters.curriculum_volume_id) && state.value !== 'loading')
  const hasMore = computed(() => visibleCount.value < (result.value?.candidates.length ?? 0))
  const selectedKey = computed(() => filters.target_keys?.[0] ?? result.value?.selected_target_keys[0] ?? '')

  function cancelScheduled(): void { clearTimeout(scheduled); waiting.value = false }
  onScopeDispose(() => { cancelScheduled(); controller?.abort(); examController?.abort() })

  function changeScope(patch: Partial<Pick<AssemblyAssistantRequest, 'class_id' | 'class_ids' | 'session_ids' | 'curriculum_volume_id' | 'chapter_id' | 'teaching_progress_chapter_id'>>): void {
    if (Object.entries(patch).every(([key, value]) => JSON.stringify(filters[key as keyof AssemblyAssistantRequest]) === JSON.stringify(value))) return
    if (patch.class_ids !== undefined || patch.class_id !== undefined || patch.curriculum_volume_id !== undefined) {
      examSerial += 1
      examController?.abort()
      examResult.value = null
      examState.value = 'idle'
    }
    controller?.abort()
    cancelScheduled()
    serial += 1
    Object.assign(filters, patch, { target_keys: null })
    if (patch.class_ids) filters.class_id = patch.class_ids[0] ?? ''
    else if (patch.class_id !== undefined) filters.class_ids = patch.class_id ? [patch.class_id] : []
    result.value = null
    questions.value = []
    previewCache.clear()
    resultCache.clear()
    candidateCounts.clear()
    loadingMore.value = false
    state.value = 'idle'
    message.value = ''
  }

  function cacheSignature(body: AssemblyAssistantRequest): string {
    return JSON.stringify({
      class_id: body.class_id, class_ids: body.class_ids, session_ids: body.session_ids, curriculum_volume_id: body.curriculum_volume_id,
      chapter_id: body.chapter_id, question_type: body.question_type,
      teaching_progress_chapter_id: body.teaching_progress_chapter_id,
      difficulty_min: body.difficulty_min, difficulty_max: body.difficulty_max,
      exclude_exam_originals: body.exclude_exam_originals, exclude_recent: body.exclude_recent,
      recent_activity_count: body.recent_activity_count, purpose: body.purpose,
    })
  }

  function filterSignature(): string {
    return cacheSignature(filters)
  }

  function rememberResult(body: AssemblyAssistantRequest, next: AssemblyAssistantResult): void {
    const signature = cacheSignature(body)
    for (const point of next.weaknesses) {
      if (point.candidate_count !== null) candidateCounts.set(`${signature}|${point.knowledge_key}`, point.candidate_count)
    }
    resultCache.set(`${signature}|${next.selected_target_keys.join(',')}`, next)
    while (resultCache.size > 40) resultCache.delete(resultCache.keys().next().value!)
  }

  async function applyCached(next: AssemblyAssistantResult): Promise<void> {
    cancelScheduled()
    controller?.abort()
    const token = ++serial
    controller = new AbortController()
    const ids = next.candidates.slice(0, PAGE_SIZE).map(item => item.question_id)
    const resolved = await resolvePreviews(ids, controller.signal)
    if (token !== serial) return
    result.value = next
    questions.value = resolved
    visibleCount.value = PAGE_SIZE
    appliedKey.value = requestKey.value
    state.value = 'ready'
    loadingMore.value = false
    message.value = resolved.length < ids.length ? '部分候选题已不可用，可更新候选题重新筛选。' : ''
  }

  function selectTarget(key: string): void {
    if (!result.value || (selectedKey.value === key && (filters.target_keys ?? result.value.selected_target_keys).length === 1)) return
    filters.target_keys = [key]
    const cached = resultCache.get(`${filterSignature()}|${key}`)
    if (cached) void applyCached(cached)
    else scheduleSearch(Math.max(0, 150 - (performance.now() - lastTargetSearch)), true)
  }

  function moveSelection(delta: number): void {
    const list = result.value?.weaknesses ?? []
    if (!list.length) return
    const index = list.findIndex(item => item.knowledge_key === selectedKey.value)
    const next = Math.max(0, Math.min(list.length - 1, (index < 0 ? 0 : index) + delta))
    selectTarget(list[next]!.knowledge_key)
  }

  function candidateCountFor(key: string): number | null {
    return candidateCounts.get(`${filterSignature()}|${key}`) ?? null
  }

  function scheduleSearch(delay = 250, targetChange = false): void {
    if (!result.value) return
    cancelScheduled()
    controller?.abort()
    serial += 1
    state.value = 'ready'
    loadingMore.value = false
    waiting.value = true
    scheduled = setTimeout(() => {
      if (targetChange) lastTargetSearch = performance.now()
      void search(true)
    }, delay)
  }

  async function resolvePreviews(ids: number[], signal: AbortSignal): Promise<AssemblyQuestion[]> {
    const missing = ids.filter(id => !previewCache.has(id))
    if (missing.length) {
      const resolved = await assemblyApi.resolveQuestions(missing, signal)
      if (signal.aborted) return []
      resolved.items.forEach(item => previewCache.set(item.id, item))
      // Bound in-memory rich content while navigating a large candidate pool.
      while (previewCache.size > 240) previewCache.delete(previewCache.keys().next().value!)
    }
    return ids.flatMap(id => previewCache.get(id) ?? [])
  }

  async function previewsFor(ids: number[]): Promise<AssemblyQuestion[]> {
    const missing = ids.filter(id => !previewCache.has(id))
    if (missing.length) {
      const resolved = await assemblyApi.resolveQuestions(missing)
      resolved.items.forEach(item => previewCache.set(item.id, item))
      while (previewCache.size > 240) previewCache.delete(previewCache.keys().next().value!)
    }
    return ids.flatMap(id => previewCache.get(id) ?? [])
  }

  async function search(reusePreviews = false): Promise<void> {
    if (!(filters.class_ids?.length || filters.class_id) || !filters.curriculum_volume_id) return
    cancelScheduled()
    const token = ++serial
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    if (!reusePreviews) {
      previewCache.clear()
      resultCache.clear()
      candidateCounts.clear()
    }
    loadingMore.value = false
    const body = JSON.parse(requestKey.value) as AssemblyAssistantRequest
    const key = JSON.stringify(body)
    state.value = 'loading'
    message.value = ''
    try {
      const next = await fetchAssemblyCandidates(body, signal)
      if (signal.aborted) return
      rememberResult(body, next)
      if (token !== serial) return
      const ids = next.candidates.slice(0, PAGE_SIZE).map(item => item.question_id)
      const resolved = await resolvePreviews(ids, signal)
      if (token !== serial || key !== requestKey.value) {
        if (token === serial) state.value = result.value ? 'ready' : 'idle'
        return
      }
      result.value = next
      questions.value = resolved
      visibleCount.value = PAGE_SIZE
      filters.target_keys = [...next.selected_target_keys]
      appliedKey.value = requestKey.value
      state.value = 'ready'
      if (resolved.length < ids.length) message.value = '部分候选题已不可用，可更新候选题重新筛选。'
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 2, class_ids: filters.class_ids, session_ids: filters.session_ids })) } catch { /* In-memory results remain usable. */ }
    } catch (error) {
      if (token !== serial) return
      state.value = 'error'
      message.value = error instanceof ApiError && error.code === 'assembly_assistant_scope_invalid'
        ? '请确认已学到的章节或专项范围，再重新查看班级知识与技能。'
        : '暂时无法取得班级学情或候选题，当前选择已保留，请稍后重试。'
    }
  }

  async function loadMore(): Promise<void> {
    if (!hasMore.value || isStale.value || loadingMore.value || state.value !== 'ready') return
    const token = serial
    const key = requestKey.value
    const signal = controller!.signal
    const nextCount = visibleCount.value + PAGE_SIZE
    const ids = result.value!.candidates.slice(visibleCount.value, nextCount).map(item => item.question_id)
    loadingMore.value = true
    message.value = ''
    try {
      const next = await resolvePreviews(ids, signal)
      if (token !== serial || key !== requestKey.value || signal.aborted) return
      questions.value = [...questions.value, ...next]
      visibleCount.value = nextCount
      if (next.length < ids.length) message.value = '部分候选题已不可用，可更新候选题重新筛选。'
    } catch { if (token === serial) message.value = '后续题目暂时无法读取，已显示的题目保留，可重试。' }
    finally { if (token === serial) loadingMore.value = false }
  }

  function resetTargets(): void { filters.target_keys = null }

  async function loadExams(): Promise<void> {
    const token = ++examSerial
    examController?.abort()
    controller?.abort()
    examController = new AbortController()
    examResult.value = null
    if (!filters.class_ids?.length || !filters.curriculum_volume_id) { examState.value = 'idle'; return }
    examState.value = 'loading'
    try {
      const next = await fetchAssemblyExams(filters.class_ids, filters.curriculum_volume_id, examController.signal)
      if (token !== examSerial) return
      examResult.value = next
      const visible = new Set(next.exams.map(e => e.session_id))
      const retained = filters.session_ids?.filter(id => visible.has(id)) ?? []
      changeScope({ session_ids: retained.length ? retained : next.exams.slice(0, 2).map(e => e.session_id) })
      selectedExamKey.value = ''
      examState.value = 'ready'
    } catch { if (token === examSerial) { examState.value = 'error'; message.value = '考试依据暂时无法读取，请重试。' } }
  }

  async function selectExam(key: string, skills: string[]): Promise<void> {
    selectedExamKey.value = key
    filters.target_keys = [...skills]
    const cached = resultCache.get(`${filterSignature()}|${skills.join(',')}`)
    if (cached) await applyCached(cached)
    else await search(true)
  }

  return { filters, result, questions, state, message, isStale, canSearch, waiting, visibleCount, loadingMore, hasMore, selectedKey, changeScope, selectTarget, moveSelection, candidateCountFor, scheduleSearch, search, loadMore, resetTargets, previewsFor,
    examResult, examState, view, threshold, sort, selectedExamKey, includeTraining, loadExams, selectExam }
})
