import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import {
  aiAssemblyApi,
  decodeAiAssemblySpecJobResult,
  type AiAssemblyEssaySubtype,
  type AiAssemblyGap,
  type AiAssemblyPreflight,
  type AiAssemblySession,
  type AiAssemblySessionWrite,
  type AiAssemblySpec,
  type AiAssemblySpecRequest,
  type AiAssemblySpecRow,
} from '../api/ai-assembly'
import { assemblyApi, type AssemblyQuestion } from '../api/assembly'
import { ApiError } from '../api/errors'
import { jobApi, TERMINAL_JOB_STATUSES, type JobApi, type JobResponse } from '../api/jobs'
import { ESSAY_SUBTYPE_TAGS, questionBankApi, type SimilarQuestionResponse } from '../api/question-bank'
import { useAssemblyStore } from './assembly'
import { jobPollDelay } from './jobs'

export type AiAssemblyPhase = 'params' | 'generating' | 'spec'

interface SelectOptions {
  preserveExisting?: boolean
  rowIndex?: number
  relaxation?: 'difficulty' | 'dedupe'
  additionalKnowledgePoints?: string[]
  additionalScopeKeys?: string[]
}

export interface AiAssemblyParams {
  templatePaperId: number | null
  scopeKeys: string[]
  // 比例留空（null）表示沿用模板难度，不发送 difficulty_ratio。
  difficultyRatio: { easy: number | null; medium: number | null; hard: number | null }
  typeCounts: Record<string, number>
  examTypes: string[]
  years: number[]
  freeText: string
  // 解答题子类约束（W5 参数页提供选择入口）；null 表示不限制。
  essaySubtype: AiAssemblyEssaySubtype | null
}

export interface AiAssemblyStoreDependencies {
  aiApi: Pick<typeof aiAssemblyApi, 'getPreflight' | 'submitSpecJob' | 'select' | 'getTemplateStructure' | 'getSession' | 'saveSession' | 'clearSession'>
  jobApi: JobApi
  assemblyApi: Pick<typeof assemblyApi, 'resolveQuestions'>
  listSimilar: typeof questionBankApi.listSimilar
  schedule: (callback: () => void, milliseconds: number) => ReturnType<typeof setTimeout>
  cancelScheduled: (handle: ReturnType<typeof setTimeout>) => void
  pollIntervalMs: number
  maxBackoffMs: number
  sessionSaveDelayMs: number
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
  sessionSaveDelayMs: 800,
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
    essaySubtype: null,
  }
}

function safeMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === 'network') return '暂时无法连接服务器，请稍后重试。'
    if (error.kind === 'contract') return '服务器返回了无法识别的数据，请稍后重试。'
    if (error.kind === 'timeout') return '选题耗时过长（已超过等待上限），请重试。'
    // 422 校验错误带后端脱敏原因（如"第 2 行题型为空"），直接透传给用户。
    if (error.kind === 'validation') {
      const reason = error.details.reason
      if (typeof reason === 'string' && reason.trim()) return reason.trim()
    }
  }
  return 'AI 组卷暂时无法完成，请稍后重试。'
}

// 空会话（从未保存过）不覆盖内存默认状态；任一实质内容存在即视为有进度。
function hasSessionContent(session: AiAssemblySession): boolean {
  const params = session.params
  return (
    session.spec !== null ||
    session.spec_job_id !== null ||
    params.template_paper_id !== null ||
    params.scope_keys.length > 0 ||
    Object.keys(params.type_counts).length > 0 ||
    params.exam_types.length > 0 ||
    params.years.length > 0 ||
    params.free_text.trim().length > 0 ||
    params.essay_subtype !== null ||
    Object.keys(session.selections).length > 0 ||
    session.locked_question_ids.length > 0
  )
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
  const basketConflict = ref(false)
  const error = ref('')

  let dependencies = defaultDependencies
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollGeneration = 0
  // 会话持久化：revision 本地维护；恢复/复位进行中不触发防抖保存。
  let sessionRevision = ''
  let sessionReady = false
  let sessionRestored = false
  let sessionBusy = false
  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let sessionSaving: Promise<void> | null = null
  let saveAgain = false

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
    if (params.essaySubtype !== null) request.essay_subtype = params.essaySubtype
    if (spec.value !== null) request.current_spec = spec.value
    return request
  }

  async function confirmAndSubmitSpec(newInstruction = ''): Promise<boolean> {
    if (selecting.value || settling.value) return false
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
        for (const index of Object.values(lockedRowById.value)) {
          const before = spec.value?.rows[index]
          const after = result.spec.rows[index]
          if (!before || !after || (['question_type', 'count', 'knowledge_points', 'difficulty', 'score', 'essay_subtype'] as const).some((key) => JSON.stringify(before[key] ?? null) !== JSON.stringify(after[key] ?? null))) {
            error.value = '新细目表改变了锁定题所在配置，请先解锁相关题目再调整。原结果已保留。'
            phase.value = 'spec'
            return
          }
        }
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
    const current = spec.value?.rows[rowIndex]
    if (!current || !spec.value || selecting.value || settling.value) return
    const row = { ...current }
    if (params.templatePaperId !== null && (
      (patch.count !== undefined && patch.count !== current.count) ||
      (patch.question_type !== undefined && patch.question_type !== current.question_type)
    )) {
      error.value = '模板组卷保留模板题型与题量。'
      return
    }
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
    if (patch.essay_subtype !== undefined) row.essay_subtype = patch.essay_subtype
    const locked = lockedQuestionIds.value.filter((id) => lockedRowById.value[id] === rowIndex)
    const changesEligibility = row.question_type !== current.question_type || row.difficulty !== current.difficulty || row.essay_subtype !== current.essay_subtype || (!spec.value.scope_knowledge_points.length && JSON.stringify(row.knowledge_points) !== JSON.stringify(current.knowledge_points))
    if (locked.length > row.count || (changesEligibility && locked.some((id) => !isEligible(detailsById.value[id], row, spec.value!)))) {
      error.value = '此调整与锁定题冲突，请先解锁相关题目。原配置和题目已保留。'
      return
    }
    const eligible = (selections.value[rowIndex] ?? []).filter((id) => !changesEligibility || isEligible(detailsById.value[id], row, spec.value!))
    const keep = new Set(locked)
    for (const id of eligible) if (keep.size < row.count) keep.add(id)
    spec.value.rows[rowIndex] = row
    selections.value = { ...selections.value, [rowIndex]: eligible.filter((id) => keep.has(id)) }
    gaps.value = gaps.value.filter((gap) => gap.row_index !== rowIndex)
    settled.value = false
    error.value = ''
  }

  function setSpecTitle(title: string): void {
    if (spec.value) spec.value.title = title.slice(0, 120)
  }

  function toggleLock(questionId: number): void {
    if (selecting.value || settling.value) return
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

  function selectionSnapshot(): string {
    return JSON.stringify([spec.value, selections.value, lockedQuestionIds.value, lockedRowById.value, dedupeEnabled.value, params.scopeKeys, params.templatePaperId])
  }

  async function runSelect(options: SelectOptions = {}): Promise<boolean> {
    if (!spec.value || selecting.value || settling.value || phase.value === 'generating') return false
    selecting.value = true
    error.value = ''
    const snapshot = selectionSnapshot()
    const candidateSpec: AiAssemblySpec = JSON.parse(JSON.stringify(spec.value))
    const target = options.rowIndex === undefined ? undefined : candidateSpec.rows[options.rowIndex]
    const nextDedupe = options.relaxation === 'dedupe' ? false : dedupeEnabled.value
    try {
      if (options.rowIndex !== undefined && !target) return false
      if (options.relaxation === 'difficulty' && target) target.difficulty = null
      if (options.additionalKnowledgePoints?.length) {
        const original = candidateSpec.scope_knowledge_points.length ? candidateSpec.scope_knowledge_points : candidateSpec.rows.flatMap((row) => row.knowledge_points)
        if (!original.length) {
          error.value = '当前没有明确的考察范围，请先设置范围。'
          return false
        }
        candidateSpec.scope_knowledge_points = [...new Set([...original, ...options.additionalKnowledgePoints])]
      }
      const locked = new Set(lockedQuestionIds.value)
      const keptByRow: Record<number, number[]> = {}
      const reserved = new Set<number>()
      candidateSpec.rows.forEach((row, rowIndex) => {
        const preserve = options.preserveExisting || (options.rowIndex !== undefined && options.rowIndex !== rowIndex)
        const kept = [...(selections.value[rowIndex] ?? []), ...Object.entries(lockedRowById.value).filter(([, index]) => index === rowIndex).map(([id]) => Number(id))]
          .filter((id) => preserve || locked.has(id))
        keptByRow[rowIndex] = [...new Set(kept)].filter((id) => !reserved.has(id))
        if (keptByRow[rowIndex]!.length > row.count) throw new Error('retained-count-conflict')
        keptByRow[rowIndex]!.forEach((id) => reserved.add(id))
      })
      const pendingRows = candidateSpec.rows.map((row, rowIndex) => ({ rowIndex, row: { ...row, count: row.count - keptByRow[rowIndex]!.length } })).filter(({ row, rowIndex }) => row.count > 0 && (options.rowIndex === undefined || options.rowIndex === rowIndex))
      const result = pendingRows.length ? await dependencies.aiApi.select({
        ...candidateSpec, rows: pendingRows.map(({ row }) => row),
      }, {
        dedupe_enabled: nextDedupe,
        exclude_ids: [...reserved],
      }) : { rows: [], gaps: [] }
      const taken = new Set(reserved)
      const next: Record<number, number[]> = {}
      const rowsByIndex = new Map(result.rows.map((row) => [pendingRows[row.row_index]?.rowIndex, row.question_ids]))
      candidateSpec.rows.forEach((row, rowIndex) => {
        // 后端只分配空位，锁定题在所有行中预先保留，不能被其他行占走。
        const kept = keptByRow[rowIndex] ?? []
        const fresh = (rowsByIndex.get(rowIndex) ?? []).filter((id) => !taken.has(id))
        const merged = [...kept, ...fresh].slice(0, row.count)
        for (const id of merged) taken.add(id)
        next[rowIndex] = merged
      })
      const nextGaps = result.gaps.flatMap((gap) => {
        const rowIndex = pendingRows[gap.row_index]?.rowIndex
        return rowIndex === undefined ? [] : [{ ...gap, row_index: rowIndex }]
      })
      const nextDetails = { ...detailsById.value }
      const missing = [...new Set(Object.values(next).flat())].filter((id) => !nextDetails[id])
      if (missing.length) {
        const resolved = await dependencies.assemblyApi.resolveQuestions(missing)
        for (const item of resolved.items) nextDetails[item.id] = item
        if (missing.some((id) => !nextDetails[id])) throw new Error('missing-question-details')
      }
      if (snapshot !== selectionSnapshot()) {
        error.value = '配置已变化，本次选题结果未覆盖当前内容。请按当前配置重试。'
        return false
      }
      // 所有请求成功后一起提交；请求中的旧结果仍可显示和保存。
      spec.value = candidateSpec
      selections.value = next
      detailsById.value = nextDetails
      gaps.value = [...gaps.value.filter((gap) => !pendingRows.some(({ rowIndex }) => rowIndex === gap.row_index)), ...nextGaps]
      dedupeEnabled.value = nextDedupe
      if (options.additionalScopeKeys?.length) params.scopeKeys = [...new Set([...params.scopeKeys, ...options.additionalScopeKeys])]
      settled.value = false
      return true
    } catch (cause) {
      error.value = safeMessage(cause)
      return false
    } finally {
      selecting.value = false
    }
  }

  function isEligible(question: AssemblyQuestion | undefined, row: AiAssemblySpecRow, currentSpec: AiAssemblySpec): boolean {
    if (!question) return false
    const legacySubtype = question.question_type?.match(/^解答题（(画图|计算|证明)）$/)?.[1]
    const type = legacySubtype ? '解答题' : question.question_type
    if (type !== row.question_type) return false
    const scope = currentSpec.scope_knowledge_points.length ? currentSpec.scope_knowledge_points : row.knowledge_points
    if (scope.length && !question.tags.some((tag) => tag.tag_type === 'knowledge_point' && scope.includes(tag.tag_value))) return false
    if (row.difficulty !== null && (!question.difficulty?.trim() || !Number.isFinite(Number(question.difficulty)) || Math.abs(Number(question.difficulty) - row.difficulty) > 1)) return false
    const subtypes = question.tags.filter((tag) => tag.tag_type === 'special_type' && (ESSAY_SUBTYPE_TAGS as readonly string[]).includes(tag.tag_value)).map((tag) => tag.tag_value)
    if (!subtypes.length && legacySubtype) subtypes.push(legacySubtype)
    return !row.essay_subtype || !subtypes.length || subtypes.includes(row.essay_subtype)
  }

  async function listSimilar(questionId: number): Promise<SimilarQuestionResponse | null> {
    try {
      return await dependencies.listSimilar(questionId, 6)
    } catch (cause) {
      error.value = safeMessage(cause)
      return null
    }
  }

  async function listReplacements(rowIndex: number, questionId: number): Promise<AssemblyQuestion[]> {
    const row = spec.value?.rows[rowIndex]
    if (!row || !spec.value || !selections.value[rowIndex]?.includes(questionId)) return []
    const snapshot = selectionSnapshot()
    try {
      const result = await dependencies.aiApi.select({ ...spec.value, rows: [{ ...row, count: 6 }] }, {
        exclude_ids: [...selectedQuestionIds.value], dedupe_enabled: dedupeEnabled.value,
      })
      const ids = result.rows[0]?.question_ids ?? []
      const resolved = ids.length ? await dependencies.assemblyApi.resolveQuestions(ids) : { items: [] }
      if (snapshot !== selectionSnapshot()) return []
      return resolved.items.filter((item) => ids.includes(item.id) && !selectedQuestionIds.value.includes(item.id) && isEligible(item, row, spec.value!))
    } catch (cause) {
      error.value = safeMessage(cause)
      return []
    }
  }

  /** 换一题：用相似题候选替换当前行的题目；本地替换，不重新调用模型。 */
  async function replaceQuestion(rowIndex: number, oldId: number, newId: number): Promise<boolean> {
    const row = selections.value[rowIndex]
    if (!row || !spec.value || selecting.value || settling.value || !row.includes(oldId) || selectedQuestionIds.value.includes(newId)) return false
    const snapshot = selectionSnapshot()
    let replacement = detailsById.value[newId]
    try {
      if (!replacement) replacement = (await dependencies.assemblyApi.resolveQuestions([newId])).items.find((item) => item.id === newId)
    } catch (cause) {
      error.value = safeMessage(cause)
      return false
    }
    if (snapshot !== selectionSnapshot() || !isEligible(replacement, spec.value.rows[rowIndex]!, spec.value)) {
      error.value = '这道题不符合当前配置，或配置已变化。原题已保留。'
      return false
    }
    detailsById.value = { ...detailsById.value, [newId]: replacement! }
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
    settled.value = false
    return true
  }

  /** 落卷：把选中题写入试卷篮草稿，并标记本次导出来源为 AI 组卷。 */
  async function settle(replaceExisting = false): Promise<boolean> {
    const ids = selectedQuestionIds.value
    if (!ids.length || settling.value || selecting.value) return false
    settling.value = true
    error.value = ''
    try {
      const assembly = useAssemblyStore()
      const previous = assembly.draft
      const sameOrder = JSON.stringify(previous.order_ids) === JSON.stringify(ids)
      if (previous.basket_ids.length && !sameOrder && !replaceExisting) {
        basketConflict.value = true
        error.value = '试卷篮已有不同内容，请确认是否用当前组卷替换草稿。'
        return false
      }
      const ok = await assembly.save({
        ...previous, basket_ids: [...ids], order_ids: [...ids], sections: [],
        layout_mode: 'sequential', title: spec.value?.title ?? previous.title,
      })
      if (!ok) {
        error.value = assembly.message || '加入试卷篮失败，请稍后重试。'
        return false
      }
      assembly.markExportSource('ai')
      basketConflict.value = false
      settled.value = true
      return true
    } finally {
      settling.value = false
    }
  }

  function buildSessionPayload(): AiAssemblySessionWrite {
    return {
      params: {
        template_paper_id: params.templatePaperId,
        scope_keys: [...params.scopeKeys],
        difficulty_ratio: { ...params.difficultyRatio },
        type_counts: { ...params.typeCounts },
        exam_types: [...params.examTypes],
        years: [...params.years],
        free_text: params.freeText,
        essay_subtype: params.essaySubtype,
      },
      spec: spec.value,
      spec_model_name: specModelName.value,
      selections: { ...selections.value },
      locked_question_ids: [...lockedQuestionIds.value],
      locked_row_by_id: { ...lockedRowById.value },
      gaps: gaps.value,
      dedupe_enabled: dedupeEnabled.value,
      title: spec.value?.title ?? '',
      spec_job_id: jobId.value,
    }
  }

  function scheduleSessionSave(): void {
    if (!sessionReady || sessionBusy) return
    if (saveTimer !== null) dependencies.cancelScheduled(saveTimer)
    saveTimer = dependencies.schedule(() => {
      saveTimer = null
      void persistSession()
    }, dependencies.sessionSaveDelayMs)
  }

  async function persistSession(): Promise<void> {
    if (!sessionReady || sessionBusy || !sessionRevision) return
    if (sessionSaving) {
      saveAgain = true
      return
    }
    const payload: AiAssemblySessionWrite = JSON.parse(JSON.stringify(buildSessionPayload()))
    sessionSaving = (async () => {
      try {
        const saved = await dependencies.aiApi.saveSession(sessionRevision, payload)
        sessionRevision = saved.revision
      } catch (cause) {
        // 串行保存后，409 只处理其他会话产生的冲突。
        if (cause instanceof ApiError && cause.status === 409) {
          sessionRestored = false
          await restoreSession()
        }
        // 其他失败不打断使用；下次状态变更会再次尝试保存。
      }
    })()
    await sessionSaving
    sessionSaving = null
    if (saveAgain) {
      saveAgain = false
      await persistSession()
    }
  }

  function applySession(session: AiAssemblySession): void {
    params.templatePaperId = session.params.template_paper_id
    params.scopeKeys = [...session.params.scope_keys]
    params.difficultyRatio = { ...session.params.difficulty_ratio }
    params.typeCounts = { ...session.params.type_counts }
    params.examTypes = [...session.params.exam_types]
    params.years = [...session.params.years]
    params.freeText = session.params.free_text
    params.essaySubtype = session.params.essay_subtype
    spec.value = session.spec
    specModelName.value = session.spec_model_name
    selections.value = { ...session.selections }
    lockedQuestionIds.value = [...session.locked_question_ids]
    lockedRowById.value = { ...session.locked_row_by_id }
    gaps.value = session.gaps
    dedupeEnabled.value = session.dedupe_enabled
    jobId.value = session.spec_job_id
    phase.value = session.spec !== null
      ? 'spec'
      : session.spec_job_id !== null ? 'generating' : 'params'
  }

  /** 挂载时恢复一次会话；细目表 job 仍在跑时恢复轮询直到取回结果。 */
  async function restoreSession(): Promise<void> {
    if (sessionRestored || sessionBusy) return
    sessionBusy = true
    try {
      const session = await dependencies.aiApi.getSession()
      sessionRevision = session.revision
      sessionReady = true
      sessionRestored = true
      if (!hasSessionContent(session)) return
      applySession(session)
      const ids = Object.values(selections.value).flat()
      if (ids.length) await resolveDetails(ids)
      if (spec.value === null && jobId.value !== null) {
        schedulePoll(pollGeneration, dependencies.pollIntervalMs)
      }
    } catch {
      // 读取失败不阻塞页面；本次不启用持久化，避免用内存状态覆盖服务端会话。
    } finally {
      sessionBusy = false
    }
  }

  function reset(): void {
    stopPolling()
    if (saveTimer !== null) {
      dependencies.cancelScheduled(saveTimer)
      saveTimer = null
    }
    // 复位期间的批量变更不触发防抖保存；服务端会话由 clearSession 一次复位。
    sessionBusy = true
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
    basketConflict.value = false
    error.value = ''
    const resetPayload = JSON.stringify(buildSessionPayload())
    void (sessionSaving ?? Promise.resolve()).then(() => dependencies.aiApi.clearSession())
      .then((cleared) => {
        sessionRevision = cleared.revision
        sessionReady = true
      })
      .catch(() => {})
      .finally(() => {
        sessionBusy = false
        if (JSON.stringify(buildSessionPayload()) !== resetPayload) scheduleSessionSave()
      })
  }

  // 关键状态变化后防抖保存会话；sync flush 保证防抖计时从最后一次变更起算。
  watch(
    [params, spec, specModelName, selections, lockedQuestionIds, lockedRowById, gaps, dedupeEnabled, jobId],
    () => scheduleSessionSave(),
    { deep: true, flush: 'sync' },
  )

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
    basketConflict,
    error,
    selectedQuestionIds,
    gapByRow,
    hasSelection,
    configure,
    loadPreflight,
    restoreSession,
    setTemplatePaper,
    setScopeChecked,
    buildRequest,
    confirmAndSubmitSpec,
    applySpecEdit,
    setSpecTitle,
    toggleLock,
    runSelect,
    listSimilar,
    listReplacements,
    replaceQuestion,
    settle,
    reset,
  }
})
