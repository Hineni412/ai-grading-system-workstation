import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  aiAssemblyApi,
  decodeAiAssemblySpecJobResult,
  type AiAssemblyGap,
  type AiAssemblyPreflight,
  type AiAssemblySpec,
  type AiAssemblySpecRequest,
  type AiAssemblySpecRow,
} from '../api/ai-assembly'
import { assemblyApi, type AssemblyQuestion } from '../api/assembly'
import { ApiError } from '../api/errors'
import { jobApi, TERMINAL_JOB_STATUSES, type JobApi, type JobResponse } from '../api/jobs'
import { questionBankApi, type SimilarQuestionResponse } from '../api/question-bank'
import { useAssemblyStore } from './assembly'
import { jobPollDelay } from './jobs'

export type AiAssemblyPhase = 'params' | 'generating' | 'spec'

export interface AiAssemblyParams {
  templatePaperId: number | null
  scopeKeys: string[]
  // 比例留空（null）表示沿用模板难度，不发送 difficulty_ratio。
  difficultyRatio: { easy: number | null; medium: number | null; hard: number | null }
  typeCounts: Record<string, number>
  examTypes: string[]
  years: number[]
  freeText: string
}

export interface AiAssemblyStoreDependencies {
  aiApi: Pick<typeof aiAssemblyApi, 'getPreflight' | 'submitSpecJob' | 'select' | 'getTemplateStructure'>
  jobApi: JobApi
  assemblyApi: Pick<typeof assemblyApi, 'resolveQuestions'>
  listSimilar: typeof questionBankApi.listSimilar
  schedule: (callback: () => void, milliseconds: number) => ReturnType<typeof setTimeout>
  cancelScheduled: (handle: ReturnType<typeof setTimeout>) => void
  pollIntervalMs: number
  maxBackoffMs: number
}

const defaultDependencies: AiAssemblyStoreDependencies = {
  aiApi: aiAssemblyApi,
  jobApi,
  assemblyApi,
  listSimilar: (questionId, limit, signal) => questionBankApi.listSimilar(questionId, limit, signal),
  schedule: (callback, milliseconds) => setTimeout(callback, milliseconds),
  cancelScheduled: (handle) => clearTimeout(handle),
  pollIntervalMs: 2_000,
  maxBackoffMs: 30_000,
}

function defaultParams(): AiAssemblyParams {
  return {
    templatePaperId: null,
    scopeKeys: [],
    difficultyRatio: { easy: 30, medium: 50, hard: 20 },
    typeCounts: {},
    examTypes: [],
    years: [],
    freeText: '',
  }
}

function safeMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === 'network') return '暂时无法连接服务器，请稍后重试。'
    if (error.kind === 'contract') return '服务器返回了无法识别的数据，请稍后重试。'
  }
  return 'AI 组卷暂时无法完成，请稍后重试。'
}

export const useAiAssemblyStore = defineStore('ai-assembly', () => {
  const params = reactive<AiAssemblyParams>(defaultParams())
  const phase = ref<AiAssemblyPhase>('params')
  const preflight = ref<AiAssemblyPreflight | null>(null)
  const preflightState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const spec = ref<AiAssemblySpec | null>(null)
  const specModelName = ref('')
  const jobId = ref<number | null>(null)
  const jobStage = ref('')
  const jobDetail = ref('')
  const lockedQuestionIds = ref<number[]>([])
  // 锁定题的行归属：重生成会清空 selections，靠它在重选时把锁定题放回原行。
  const lockedRowById = ref<Record<number, number>>({})
  const selections = ref<Record<number, number[]>>({})
  const gaps = ref<AiAssemblyGap[]>([])
  const detailsById = ref<Record<number, AssemblyQuestion>>({})
  const dedupeEnabled = ref(true)
  const selecting = ref(false)
  const settling = ref(false)
  const settled = ref(false)
  const error = ref('')

  let dependencies = defaultDependencies
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollGeneration = 0

  const selectedQuestionIds = computed(() => (
    (spec.value?.rows ?? []).flatMap((_, rowIndex) => selections.value[rowIndex] ?? [])
  ))
  const gapByRow = computed(() => new Map(gaps.value.map((gap) => [gap.row_index, gap])))
  const hasSelection = computed(() => selectedQuestionIds.value.length > 0)

  function configure(next?: Partial<AiAssemblyStoreDependencies>): void {
    dependencies = { ...defaultDependencies, ...next }
  }

  function stopPolling(): void {
    pollGeneration += 1
    if (pollTimer !== null) dependencies.cancelScheduled(pollTimer)
    pollTimer = null
  }

  async function loadPreflight(): Promise<void> {
    preflightState.value = 'loading'
    error.value = ''
    try {
      preflight.value = await dependencies.aiApi.getPreflight()
      preflightState.value = 'ready'
    } catch (cause) {
      preflight.value = null
      preflightState.value = 'error'
      error.value = safeMessage(cause)
    }
  }

  function setTemplatePaper(paperId: number | null): void {
    params.templatePaperId = paperId
    // 模板继承每题难度档：选模板后比例留空（沿用模板难度），回到自由组卷恢复默认预填。
    params.difficultyRatio = paperId === null
      ? { ...defaultParams().difficultyRatio }
      : { easy: null, medium: null, hard: null }
  }

  /**
   * 章节/知识点勾选：选中时连带选中全部子孙节点，取消时连带取消；
   * 子孙可展开后单独取消（取消子节点不影响兄弟节点）。
   */
  function setScopeChecked(key: string, checked: boolean, descendants: string[] = []): void {
    const cleanKey = key.trim()
    if (!cleanKey) return
    const next = new Set(params.scopeKeys)
    const affected = [cleanKey, ...descendants.map((item) => item.trim()).filter(Boolean)]
    for (const item of affected) {
      if (checked) next.add(item)
      else next.delete(item)
    }
    params.scopeKeys = [...next]
  }

  function buildRequest(newInstruction = ''): AiAssemblySpecRequest {
    // 重生成上下文 = 当前细目表 + 锁定题 + 本次新指令，不带历史口语。
    const regenerating = spec.value !== null
    const request: AiAssemblySpecRequest = {
      scope_keys: [...params.scopeKeys],
      difficulty_ratio: Object.fromEntries(
        Object.entries(params.difficultyRatio)
          .filter((entry): entry is [string, number] => entry[1] !== null),
      ),
      type_counts: { ...params.typeCounts },
      exam_types: [...params.examTypes],
      years: [...params.years],
      free_text: regenerating ? '' : params.freeText,
      locked_question_ids: [...lockedQuestionIds.value],
      new_instruction: newInstruction,
    }
    if (params.templatePaperId !== null) request.template_paper_id = params.templatePaperId
    if (spec.value !== null) request.current_spec = spec.value
    return request
  }

  async function confirmAndSubmitSpec(newInstruction = ''): Promise<boolean> {
    stopPolling()
    error.value = ''
    phase.value = 'generating'
    jobStage.value = ''
    jobDetail.value = ''
    const generation = pollGeneration
    let job: JobResponse
    try {
      job = await dependencies.aiApi.submitSpecJob(buildRequest(newInstruction))
    } catch (cause) {
      phase.value = spec.value ? 'spec' : 'params'
      error.value = safeMessage(cause)
      return false
    }
    jobId.value = job.id
    jobStage.value = job.stage
    jobDetail.value = job.detail
    if (TERMINAL_JOB_STATUSES.has(job.status)) {
      finishJob(job, generation)
      return error.value === ''
    }
    schedulePoll(generation, dependencies.pollIntervalMs)
    return true
  }

  function schedulePoll(generation: number, delay: number, retryCount = 0): void {
    pollTimer = dependencies.schedule(() => {
      pollTimer = null
      void poll(generation, retryCount)
    }, delay)
  }

  async function poll(generation: number, retryCount: number): Promise<void> {
    if (generation !== pollGeneration || jobId.value === null) return
    let job: JobResponse
    try {
      job = await dependencies.jobApi.getJob(jobId.value)
    } catch {
      if (generation !== pollGeneration) return
      if (retryCount >= 3) {
        phase.value = spec.value ? 'spec' : 'params'
        error.value = '任务状态暂时无法更新，请稍后重试。'
        return
      }
      schedulePoll(
        generation,
        jobPollDelay(retryCount + 1, dependencies.pollIntervalMs, dependencies.maxBackoffMs),
        retryCount + 1,
      )
      return
    }
    if (generation !== pollGeneration) return
    jobStage.value = job.stage
    jobDetail.value = job.detail
    if (!TERMINAL_JOB_STATUSES.has(job.status)) {
      schedulePoll(generation, dependencies.pollIntervalMs)
      return
    }
    finishJob(job, generation)
  }

  function finishJob(job: JobResponse, generation: number): void {
    if (generation !== pollGeneration) return
    if (job.status === 'succeeded') {
      try {
        const result = decodeAiAssemblySpecJobResult(job.result)
        spec.value = result.spec
        specModelName.value = result.model_name
        selections.value = {}
        gaps.value = []
        detailsById.value = {}
        phase.value = 'spec'
        error.value = ''
      } catch {
        phase.value = spec.value ? 'spec' : 'params'
        error.value = '服务器返回了无法识别的数据，请稍后重试。'
      }
      return
    }
    phase.value = spec.value ? 'spec' : 'params'
    // 失败不自动重试：把错误放进状态，由用户决定是否再次提交。
    error.value = job.error?.trim() || '细目表生成失败，请调整需求后重新提交。'
  }

  function applySpecEdit(rowIndex: number, patch: Partial<AiAssemblySpecRow>): void {
    const row = spec.value?.rows[rowIndex]
    if (!row) return
    if (patch.question_type !== undefined && patch.question_type.trim()) {
      row.question_type = patch.question_type.trim().slice(0, 40)
    }
    if (patch.count !== undefined && Number.isSafeInteger(patch.count)) {
      row.count = Math.max(1, Math.min(200, patch.count))
    }
    if (patch.knowledge_points !== undefined) {
      row.knowledge_points = patch.knowledge_points
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 50)
    }
    if (patch.difficulty !== undefined) {
      row.difficulty = patch.difficulty === null
        ? null
        : Math.max(1, Math.min(9, Math.round(patch.difficulty)))
    }
    if (patch.score !== undefined) {
      row.score = patch.score === null
        ? null
        : Math.max(0.5, Math.min(1000, patch.score))
    }
    // 细目表改动后旧选题结果作废，避免展示与细目表不一致。
    selections.value = {}
    gaps.value = []
    detailsById.value = {}
  }

  function setSpecTitle(title: string): void {
    if (spec.value) spec.value.title = title.slice(0, 120)
  }

  function toggleLock(questionId: number): void {
    if (!Number.isSafeInteger(questionId) || questionId <= 0) return
    if (lockedQuestionIds.value.includes(questionId)) {
      lockedQuestionIds.value = lockedQuestionIds.value.filter((id) => id !== questionId)
      const next = { ...lockedRowById.value }
      delete next[questionId]
      lockedRowById.value = next
      return
    }
    lockedQuestionIds.value = [...lockedQuestionIds.value, questionId]
    const rowIndex = Object.entries(selections.value).find(([, ids]) => ids.includes(questionId))
    if (rowIndex) {
      lockedRowById.value = { ...lockedRowById.value, [questionId]: Number(rowIndex[0]) }
    }
  }

  async function resolveDetails(ids: readonly number[]): Promise<boolean> {
    const missing = ids.filter((id) => detailsById.value[id] === undefined)
    if (!missing.length) return true
    try {
      const result = await dependencies.assemblyApi.resolveQuestions(missing)
      const next = { ...detailsById.value }
      for (const item of result.items) next[item.id] = item
      detailsById.value = next
      return true
    } catch (cause) {
      error.value = safeMessage(cause)
      return false
    }
  }

  async function runSelect(): Promise<boolean> {
    if (!spec.value || selecting.value) return false
    selecting.value = true
    error.value = ''
    try {
      const result = await dependencies.aiApi.select(spec.value, {
        dedupe_enabled: dedupeEnabled.value,
      })
      const locked = new Set(lockedQuestionIds.value)
      const taken = new Set<number>()
      const next: Record<number, number[]> = {}
      const rowsByIndex = new Map(result.rows.map((row) => [row.row_index, row.question_ids]))
      spec.value.rows.forEach((row, rowIndex) => {
        // 锁定的题留在原行最前（按锁定时的行归属），其余空位由新选题结果补齐。
        const kept = (selections.value[rowIndex] ?? [])
          .filter((id) => locked.has(id))
        for (const [idText, lockedRow] of Object.entries(lockedRowById.value)) {
          const id = Number(idText)
          if (lockedRow === rowIndex && locked.has(id) && !kept.includes(id)) kept.push(id)
        }
        for (const id of kept) taken.add(id)
        const fresh = (rowsByIndex.get(rowIndex) ?? []).filter((id) => !taken.has(id))
        const merged = [...kept, ...fresh].slice(0, row.count)
        for (const id of merged) taken.add(id)
        next[rowIndex] = merged
      })
      selections.value = next
      gaps.value = result.gaps
      return resolveDetails(Object.values(next).flat())
    } catch (cause) {
      error.value = safeMessage(cause)
      return false
    } finally {
      selecting.value = false
    }
  }

  async function listSimilar(questionId: number): Promise<SimilarQuestionResponse | null> {
    try {
      return await dependencies.listSimilar(questionId, 6)
    } catch (cause) {
      error.value = safeMessage(cause)
      return null
    }
  }

  /** 换一题：用相似题候选替换当前行的题目；本地替换，不重新调用模型。 */
  async function replaceQuestion(rowIndex: number, oldId: number, newId: number): Promise<boolean> {
    const row = selections.value[rowIndex]
    if (!row || !row.includes(oldId) || row.includes(newId)) return false
    const wasLocked = lockedQuestionIds.value.includes(oldId)
    selections.value = {
      ...selections.value,
      [rowIndex]: row.map((id) => (id === oldId ? newId : id)),
    }
    if (wasLocked) {
      lockedQuestionIds.value = lockedQuestionIds.value.map((id) => (id === oldId ? newId : id))
      const next = { ...lockedRowById.value }
      const row = next[oldId]
      delete next[oldId]
      if (row !== undefined) next[newId] = row
      lockedRowById.value = next
    }
    return resolveDetails([newId])
  }

  /** 落卷：把选中题写入试卷篮草稿，并标记本次导出来源为 AI 组卷。 */
  async function settle(): Promise<boolean> {
    const ids = selectedQuestionIds.value
    if (!ids.length || settling.value) return false
    settling.value = true
    error.value = ''
    try {
      const assembly = useAssemblyStore()
      const ok = await assembly.addQuestions(ids)
      if (!ok) {
        error.value = assembly.message || '加入试卷篮失败，请稍后重试。'
        return false
      }
      assembly.markExportSource('ai')
      settled.value = true
      return true
    } finally {
      settling.value = false
    }
  }

  function reset(): void {
    stopPolling()
    Object.assign(params, defaultParams())
    phase.value = 'params'
    preflight.value = null
    preflightState.value = 'idle'
    spec.value = null
    specModelName.value = ''
    jobId.value = null
    jobStage.value = ''
    jobDetail.value = ''
    lockedQuestionIds.value = []
    lockedRowById.value = {}
    selections.value = {}
    gaps.value = []
    detailsById.value = {}
    dedupeEnabled.value = true
    settled.value = false
    error.value = ''
  }

  return {
    params,
    phase,
    preflight,
    preflightState,
    spec,
    specModelName,
    jobId,
    jobStage,
    jobDetail,
    lockedQuestionIds,
    lockedRowById,
    selections,
    gaps,
    detailsById,
    dedupeEnabled,
    selecting,
    settling,
    settled,
    error,
    selectedQuestionIds,
    gapByRow,
    hasSelection,
    configure,
    loadPreflight,
    setTemplatePaper,
    setScopeChecked,
    buildRequest,
    confirmAndSubmitSpec,
    applySpecEdit,
    setSpecTitle,
    toggleLock,
    runSelect,
    listSimilar,
    replaceQuestion,
    settle,
    reset,
  }
})
