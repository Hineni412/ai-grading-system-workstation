import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchActiveConfigSource,
  fetchActiveSourceDuplicates,
  fetchConfigEditor,
  fetchConfigSource,
  fetchLatestConfigGenerationJob,
  type ConfigGenerationRequest,
  type ConfigSourceDuplicateItem,
  type GenerationMode,
  type ConfigEditorCommand,
  type ConfigEditorEdit,
  type ConfigEditorIssue,
  type ConfigEditorResponse,
  type ConfigEditorSaveRequest,
  type ConfigEditorSaveResponse,
  type ConfigSource,
  type ConfigQuestionGenerationState,
  type ConfigAmbiguousAssetDecision,
  type QuestionDecision,
} from '../api/config-workspace'
import { jobApi, type JobResponse } from '../api/jobs'
import { useJobStore } from './jobs'

export const CONFIG_WORKSPACE_STORAGE_KEY = 'ai-grading:config-workspace:v1'
export type ConfigPhase = 'draft' | 'source' | 'generation' | 'editor'

export interface PersistedConfigWorkspace {
  sessionId: number
  phase: ConfigPhase
  sourceId: string | null
  sourceRevision: string | null
  jobId: number | null
  decisions: QuestionDecision[]
  assetDecisions?: ConfigAmbiguousAssetDecision[]
  generationSummary?: ConfigGenerationSummary
  pendingGenerationMode?: GenerationMode
  pendingJobRequestToken?: string
  pendingJobRequestKind?: 'generate' | 'retry' | 'refine'
  pendingUploadRequestToken?: string
}

export interface ConfigGenerationSummary {
  totalQuestions: number
  succeededQuestions: number
  failedQuestions: number
}

export type ConfigSaveStatus = 'idle' | 'saving' | 'success' | 'conflict' | 'failure' | 'unknown'

export interface ConfigEditorContextToken {
  sessionId: number | null
  sourceRevision: string | null
  editorRevision: string | null
  generation: number
}

export type ConfigSourceLoader = (
  sessionId: number,
  sourceId: string,
  signal?: AbortSignal,
) => Promise<ConfigSource>

export interface ConfigWorkspaceHydrationDependencies {
  loadActiveSource: (sessionId: number) => Promise<ConfigSource>
  loadSource: ConfigSourceLoader
  loadJob: (jobId: number, signal?: AbortSignal) => Promise<JobResponse>
  loadLatestJob: typeof fetchLatestConfigGenerationJob
  loadEditor: (sessionId: number, signal?: AbortSignal) => Promise<ConfigEditorResponse>
}

const phases = new Set<ConfigPhase>(['draft', 'source', 'generation', 'editor'])

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function validSourceId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)
}

function validRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function validDecision(value: unknown): value is QuestionDecision {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  const allowedKeys = new Set([
    'question_id',
    'excluded',
    'question_type',
    'answer_confirmed',
    'answer_override',
    'bank_match',
    'bank_question_id',
  ])
  if (Object.keys(item).some((key) => !allowedKeys.has(key))) return false
  if (item.question_type !== undefined
    && !['choice', 'fill_blank', 'calculation', 'proof', 'comprehensive']
      .includes(String(item.question_type))) return false
  if (item.answer_confirmed !== undefined && typeof item.answer_confirmed !== 'boolean') return false
  if (item.answer_override !== undefined && item.answer_override !== null
    && typeof item.answer_override !== 'string') return false
  if (item.bank_match !== undefined && item.bank_match !== null
    && !['same', 'different', 'reanalyze'].includes(String(item.bank_match))) return false
  if (item.bank_question_id !== undefined && item.bank_question_id !== null
    && (!Number.isInteger(item.bank_question_id)
      || (item.bank_question_id as number) <= 0)) return false
  if (item.bank_match === 'same'
    && (!Number.isInteger(item.bank_question_id)
      || (item.bank_question_id as number) <= 0)) return false
  return typeof item.question_id === 'string'
    && typeof item.excluded === 'boolean'
}

function safePersistedDecision(decision: QuestionDecision): QuestionDecision {
  // Legacy answer/type fields stay transient (they carry answer text); the
  // bank-match verdict is metadata and safe to persist across reload.
  const safe: QuestionDecision = {
    question_id: decision.question_id,
    excluded: decision.excluded,
  }
  if (decision.bank_match !== undefined) safe.bank_match = decision.bank_match
  if (decision.bank_question_id !== undefined) safe.bank_question_id = decision.bank_question_id
  return safe
}

function validAssetDecision(value: unknown): value is ConfigAmbiguousAssetDecision {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  if (typeof item.candidate_id !== 'string' || !/^[AP][1-9]\d{0,3}$/.test(item.candidate_id)) return false
  if (item.action === 'ignore') {
    return Object.keys(item).sort().join(',') === 'action,candidate_id'
  }
  return item.action === 'bind'
    && Object.keys(item).sort().join(',') === 'action,asset_kind,candidate_id,question_id'
    && typeof item.question_id === 'string'
    && ['question', 'answer'].includes(String(item.asset_kind))
}

function validGenerationSummary(value: unknown): value is ConfigGenerationSummary {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  if (Object.keys(item).sort().join(',') !== 'failedQuestions,succeededQuestions,totalQuestions') {
    return false
  }
  return [item.totalQuestions, item.succeededQuestions, item.failedQuestions]
    .every((count) => Number.isSafeInteger(count) && Number(count) >= 0)
    && Number(item.succeededQuestions) + Number(item.failedQuestions) <= Number(item.totalQuestions)
}

function parsePersisted(raw: string | null): PersistedConfigWorkspace | null {
  if (!raw) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
    const item = value as Record<string, unknown>
    const requiredKeys = ['sessionId', 'phase', 'sourceId', 'sourceRevision', 'jobId', 'decisions']
    const allowedKeys = new Set([
      ...requiredKeys, 'generationSummary', 'pendingGenerationMode',
      'pendingJobRequestToken', 'pendingJobRequestKind', 'pendingUploadRequestToken',
      'assetDecisions',
    ])
    if (!requiredKeys.every((key) => key in item)
      || Object.keys(item).some((key) => !allowedKeys.has(key))) return null
    if (!positiveInteger(item.sessionId) || typeof item.phase !== 'string'
      || !phases.has(item.phase as ConfigPhase)
      || (item.sourceId !== null && !validSourceId(item.sourceId))
      || (item.sourceRevision !== null && !validRevision(item.sourceRevision))
      || (item.jobId !== null && !positiveInteger(item.jobId))
      || !Array.isArray(item.decisions) || !item.decisions.every(validDecision)
      || ('assetDecisions' in item && (
        !Array.isArray(item.assetDecisions) || !item.assetDecisions.every(validAssetDecision)
      ))
      || ('pendingGenerationMode' in item
        && !['batched', 'per_question', 'whole_document', 'refine']
          .includes(String(item.pendingGenerationMode)))
      || ('pendingJobRequestToken' in item
        && !/^[0-9a-f]{32}$/.test(String(item.pendingJobRequestToken)))
      || ('pendingJobRequestKind' in item
        && !['generate', 'retry', 'refine'].includes(String(item.pendingJobRequestKind)))
      || ('pendingUploadRequestToken' in item
        && !/^[0-9a-f]{32}$/.test(String(item.pendingUploadRequestToken)))
      || ('generationSummary' in item && !validGenerationSummary(item.generationSummary))) return null
    if (('pendingJobRequestToken' in item) !== ('pendingJobRequestKind' in item)) return null
    if ((item.sourceId === null) !== (item.sourceRevision === null)) return null
    if ('pendingGenerationMode' in item && item.pendingGenerationMode !== 'batched') {
      item.pendingGenerationMode = 'batched'
    }
    return item as unknown as PersistedConfigWorkspace
  } catch {
    return null
  }
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'not_found'
}

function isSourceChanged(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
    && error.code === 'config_source_changed'
}

const editableIssueFields = new Set([
  'score',
  'standard_answer',
  'accepted_answers',
  'answer_only_max_score',
  'require_final_answer',
  'required_elements',
  'deduction_rules',
  'part_deduction_rules',
  'final_answer_rule',
])

function safeServerIssue(value: unknown): value is ConfigEditorIssue {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const issue = value as Record<string, unknown>
  if (Object.keys(issue).sort().join(',') !== 'code,field,message,row_id,severity') return false
  if (typeof issue.code !== 'string' || !/^[a-z][a-z0-9_]{0,99}$/.test(issue.code)
    || (issue.severity !== 'error' && issue.severity !== 'warning')
    || (issue.row_id !== null && (typeof issue.row_id !== 'string'
      || issue.row_id.length < 1 || issue.row_id.length > 256 || /[\\/\u0000-\u001f]/.test(issue.row_id)))
    || typeof issue.field !== 'string' || !/^[a-z][a-z0-9_]{0,99}$/.test(issue.field)
    || typeof issue.message !== 'string' || issue.message.length < 1 || issue.message.length > 2_000
    || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(issue.message)
    || /(?:[a-z]:[\\/]|\\\\|file:\/\/|\/(?:home|users|tmp|var)\/|traceback)/i.test(issue.message)) {
    return false
  }
  return true
}

function jobBelongsTo(job: JobResponse, sessionId: number, candidate: PersistedConfigWorkspace): boolean {
  if (job.job_type !== 'config_generation' || job.payload.session_id !== sessionId) return false
  if ('source_id' in job.payload && job.payload.source_id !== candidate.sourceId) return false
  if ('source_revision' in job.payload && job.payload.source_revision !== candidate.sourceRevision) return false
  return true
}

export const useConfigWorkspaceStore = defineStore('config-workspace', () => {
  const sessionId = ref<number | null>(null)
  const phase = ref<ConfigPhase>('draft')
  const sourceId = ref<string | null>(null)
  const sourceRevision = ref<string | null>(null)
  const jobId = ref<number | null>(null)
  const decisions = ref<QuestionDecision[]>([])
  const assetDecisions = ref<ConfigAmbiguousAssetDecision[]>([])
  const source = ref<ConfigSource | null>(null)
  const questionStates = ref<ConfigQuestionGenerationState[]>([])
  const editor = ref<ConfigEditorResponse | null>(null)
  const editorEdits = ref<ConfigEditorEdit[]>([])
  const editorCommands = ref<ConfigEditorCommand[]>([])
  const serverIssues = ref<ConfigEditorIssue[]>([])
  const editorDirty = ref(false)
  const saveStatus = ref<ConfigSaveStatus>('idle')
  const mappingStatus = ref<ConfigEditorSaveResponse['save_result']['mapping_status'] | null>(null)
  const generationSummary = ref<ConfigGenerationSummary | null>(null)
  const pendingGenerationMode = ref<GenerationMode | null>(null)
  const pendingJobRequestToken = ref<string | null>(null)
  const pendingJobRequestKind = ref<'generate' | 'retry' | 'refine' | null>(null)
  const pendingUploadRequestToken = ref<string | null>(null)
  const sourceLoading = ref(false)
  const sourceError = ref('')
  const sourceDuplicates = ref<ConfigSourceDuplicateItem[]>([])
  const duplicatesUnavailable = ref(false)
  let duplicatesRequest = 0
  let sourceLoadGeneration = 0
  let hydrationRequest = 0
  let generationContext = 0
  let editorContextGeneration = 0
  let requestedSourceId: string | null = null

  const hasDirtyEditor = computed(() => editorDirty.value)
  const hasPendingSubmission = computed(() => pendingJobRequestToken.value !== null
    || pendingUploadRequestToken.value !== null)
  const effectiveEditorRows = computed(() => {
    const edits = new Map(editorEdits.value.map((edit) => [edit.row_id, edit]))
    return (editor.value?.rows ?? []).map((row) => {
      const edit = edits.get(row.row_id)
      return edit ? { ...row, ...Object.fromEntries(
        Object.entries(edit).filter(([key, value]) => key !== 'row_id'
          && (value !== null || key === 'answer_only_max_score')),
      ) } : row
    })
  })
  const effectiveTotalScore = computed(() => effectiveEditorRows.value
    .reduce((total, row) => total + row.score, 0))
  const canGenerate = computed(() => {
    if (sessionId.value === null || source.value === null || sourceId.value === null
      || sourceRevision.value === null || source.value.questions.length === 0) return false
    const excluded = new Set(decisions.value.filter((item) => item.excluded)
      .map((item) => item.question_id))
    const resolvedCandidates = new Set(assetDecisions.value.map((item) => item.candidate_id))
    const uncertainAssetIds = source.value.assets === undefined
      ? (source.value.ambiguous_assets ?? []).map((item) => item.candidate_id)
      : source.value.assets
        .filter((item) => item.assignment_state === 'uncertain')
        .map((item) => item.asset_id)
    const hasUnresolvedAssets = uncertainAssetIds
      .some((assetId) => !resolvedCandidates.has(assetId))
    return !hasUnresolvedAssets && source.value.questions.some(
      (question) => !excluded.has(question.question_id),
    )
  })
  function derivePhase(): ConfigPhase {
    if (editor.value?.configured) return 'editor'
    if (jobId.value !== null) return 'generation'
    if (sourceId.value !== null) return 'source'
    return 'draft'
  }

  function safeSnapshot(): PersistedConfigWorkspace | null {
    if (sessionId.value === null) return null
    const snapshot: PersistedConfigWorkspace = {
      sessionId: sessionId.value,
      phase: derivePhase(),
      sourceId: sourceId.value,
      sourceRevision: sourceRevision.value,
      jobId: jobId.value,
      decisions: decisions.value.map(safePersistedDecision),
    }
    if (assetDecisions.value.length > 0) {
      snapshot.assetDecisions = assetDecisions.value.map((item) => ({ ...item }))
    }
    if (generationSummary.value !== null) {
      snapshot.generationSummary = { ...generationSummary.value }
    }
    if (pendingGenerationMode.value !== null) {
      snapshot.pendingGenerationMode = pendingGenerationMode.value
    }
    if (pendingJobRequestToken.value !== null && pendingJobRequestKind.value !== null) {
      snapshot.pendingJobRequestToken = pendingJobRequestToken.value
      snapshot.pendingJobRequestKind = pendingJobRequestKind.value
    }
    if (pendingUploadRequestToken.value !== null) {
      snapshot.pendingUploadRequestToken = pendingUploadRequestToken.value
    }
    return snapshot
  }

  function persistSafeIndex(): void {
    const snapshot = safeSnapshot()
    if (snapshot === null) localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
    else localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(snapshot))
  }

  function resetMemory(): void {
    generationContext += 1
    editorContextGeneration += 1
    source.value = null
    questionStates.value = []
    generationSummary.value = null
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    serverIssues.value = []
    saveStatus.value = 'idle'
    mappingStatus.value = null
    sourceLoading.value = false
    sourceError.value = ''
    sourceDuplicates.value = []
    duplicatesUnavailable.value = false
    duplicatesRequest += 1
    requestedSourceId = null
    sourceLoadGeneration += 1
  }

  function forceClearWorkspace(): void {
    hydrationRequest += 1
    sessionId.value = null
    sourceId.value = null
    sourceRevision.value = null
    jobId.value = null
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    pendingUploadRequestToken.value = null
    decisions.value = []
    assetDecisions.value = []
    phase.value = 'draft'
    resetMemory()
    localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
  }

  function clearWorkspace(discardDirty = false): boolean {
    if (hasPendingSubmission.value) return false
    if (editorDirty.value && !discardDirty) return false
    forceClearWorkspace()
    editorDirty.value = false
    return true
  }

  function selectSession(id: number | null, discardDirty = false): boolean {
    if (id === null) return clearWorkspace(discardDirty)
    if (!positiveInteger(id)) throw new Error('Invalid session id')
    if (sessionId.value === id) return true
    if (hasPendingSubmission.value) return false
    if (editorDirty.value && !discardDirty) return false
    hydrationRequest += 1
    sessionId.value = id
    sourceId.value = null
    sourceRevision.value = null
    jobId.value = null
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    pendingUploadRequestToken.value = null
    decisions.value = []
    assetDecisions.value = []
    phase.value = 'draft'
    resetMemory()
    editorDirty.value = false
    persistSafeIndex()
    return true
  }

  function selectSource(id: string | null, discardDirty = false): boolean {
    if (id !== null && !validSourceId(id)) throw new Error('Invalid source id')
    if (hasPendingSubmission.value) return false
    if (editorDirty.value && !discardDirty) return false
    hydrationRequest += 1
    generationContext += 1
    sourceLoadGeneration += 1
    requestedSourceId = id
    sourceId.value = null
    sourceRevision.value = null
    source.value = null
    generationSummary.value = null
    jobId.value = null
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    pendingUploadRequestToken.value = null
    decisions.value = []
    assetDecisions.value = []
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    editorDirty.value = false
    phase.value = 'draft'
    sourceDuplicates.value = []
    duplicatesUnavailable.value = false
    duplicatesRequest += 1
    persistSafeIndex()
    return true
  }

  function discardEditorDraft(): void {
    editorContextGeneration += 1
    editorEdits.value = []
    editorCommands.value = []
    serverIssues.value = []
    editorDirty.value = false
    saveStatus.value = 'idle'
    mappingStatus.value = null
  }

  function setSource(value: ConfigSource): void {
    if (sessionId.value !== value.session_id) throw new Error('Source session does not match')
    if (sourceId.value !== value.source_id || sourceRevision.value !== value.source_revision) {
      generationContext += 1
      generationSummary.value = null
    }
    editorContextGeneration += 1
    requestedSourceId = null
    sourceId.value = value.source_id
    sourceRevision.value = value.source_revision
    source.value = value
    sourceDuplicates.value = []
    duplicatesUnavailable.value = false
    generationSummary.value = null
    phase.value = derivePhase()
    sourceError.value = ''
    persistSafeIndex()
    void loadSourceDuplicates()
  }

  function acceptUploadedSource(value: ConfigSource): void {
    if (sessionId.value !== value.session_id) throw new Error('Source session does not match')
    hydrationRequest += 1
    generationContext += 1
    editorContextGeneration += 1
    sourceLoadGeneration += 1
    requestedSourceId = null
    sourceId.value = value.source_id
    sourceRevision.value = value.source_revision
    source.value = value
    jobId.value = null
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    pendingUploadRequestToken.value = null
    decisions.value = []
    assetDecisions.value = []
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    serverIssues.value = []
    editorDirty.value = false
    sourceLoading.value = false
    sourceError.value = ''
    sourceDuplicates.value = []
    duplicatesUnavailable.value = false
    phase.value = 'source'
    persistSafeIndex()
    void loadSourceDuplicates()
  }

  // 查重只是提示信息：失败时记为暂不可用，不阻断页面任何操作。
  async function loadSourceDuplicates(
    loader: typeof fetchActiveSourceDuplicates = fetchActiveSourceDuplicates,
  ): Promise<void> {
    const expectedSessionId = sessionId.value
    const expectedSourceId = sourceId.value
    const expectedRevision = sourceRevision.value
    if (expectedSessionId === null || expectedSourceId === null
      || expectedRevision === null) return
    const request = ++duplicatesRequest
    try {
      const result = await loader(expectedSessionId)
      if (request !== duplicatesRequest || sessionId.value !== expectedSessionId
        || sourceId.value !== expectedSourceId
        || sourceRevision.value !== expectedRevision) return
      if (result.source_id !== expectedSourceId
        || result.source_revision !== expectedRevision) return
      sourceDuplicates.value = result.items
      duplicatesUnavailable.value = false
    } catch {
      if (request !== duplicatesRequest || sessionId.value !== expectedSessionId
        || sourceId.value !== expectedSourceId
        || sourceRevision.value !== expectedRevision) return
      sourceDuplicates.value = []
      duplicatesUnavailable.value = true
    }
  }

  function updateDecisions(value: QuestionDecision[]): void {
    if (source.value === null) return
    const knownIds = new Set(source.value.questions.map((question) => question.question_id))
    decisions.value = value.filter((decision) => knownIds.has(decision.question_id)
      && validDecision(decision)).map((decision) => ({ ...decision }))
    persistSafeIndex()
  }

  function updateAssetDecisions(value: ConfigAmbiguousAssetDecision[]): void {
    if (source.value === null) return
    const candidateIds = new Set([
      ...(source.value.assets ?? []).map((item) => item.asset_id),
      ...(source.value.ambiguous_assets ?? []).map((item) => item.candidate_id),
    ])
    const knownQuestionIds = new Set(source.value.questions.map((item) => item.question_id))
    const seen = new Set<string>()
    assetDecisions.value = value.filter((decision) => {
      if (!candidateIds.has(decision.candidate_id) || seen.has(decision.candidate_id)
        || !validAssetDecision(decision)) return false
      seen.add(decision.candidate_id)
      return decision.action === 'ignore'
        || knownQuestionIds.has(decision.question_id)
    }).map((item) => ({ ...item }))
    persistSafeIndex()
  }

  async function loadSource(
    expectedSessionId: number,
    expectedSourceId: string,
    loader: ConfigSourceLoader = fetchConfigSource,
  ): Promise<void> {
    const request = ++sourceLoadGeneration
    requestedSourceId = expectedSourceId
    sourceLoading.value = true
    sourceError.value = ''
    try {
      const loaded = await loader(expectedSessionId, expectedSourceId)
      if (request !== sourceLoadGeneration || sessionId.value !== expectedSessionId
        || requestedSourceId !== expectedSourceId) return
      if (loaded.session_id !== expectedSessionId || loaded.source_id !== expectedSourceId) {
        requestedSourceId = null
        persistSafeIndex()
        return
      }
      setSource(loaded)
    } catch (error) {
      if (request !== sourceLoadGeneration || sessionId.value !== expectedSessionId
        || requestedSourceId !== expectedSourceId) return
      if (isNotFound(error)) {
        requestedSourceId = null
        sourceId.value = null
        sourceRevision.value = null
        jobId.value = null
        persistSafeIndex()
      } else {
        sourceError.value = '试卷来源暂时无法读取，可以重新加载。'
      }
    } finally {
      if (request === sourceLoadGeneration) sourceLoading.value = false
    }
  }

  function setEditor(value: ConfigEditorResponse): void {
    if (sessionId.value !== null && value.session_id !== sessionId.value) return
    sessionId.value = value.session_id
    editor.value = value
    discardEditorDraft()
    phase.value = derivePhase()
    persistSafeIndex()
  }

  function captureGenerationContext(): number {
    return generationContext
  }

  function attachJob(
    id: number,
    context: number,
    retainedSummary?: ConfigGenerationSummary,
  ): boolean {
    if (!positiveInteger(id)) throw new Error('Invalid Job id')
    const attachesLegacyRefine = pendingJobRequestKind.value === 'refine'
    if (context !== generationContext || sessionId.value === null
      || (sourceId.value === null && !attachesLegacyRefine)) return false
    if (retainedSummary !== undefined && !validGenerationSummary(retainedSummary)) {
      throw new Error('Invalid generation summary')
    }
    jobId.value = id
    questionStates.value = []
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    generationSummary.value = retainedSummary === undefined ? null : { ...retainedSummary }
    phase.value = derivePhase()
    persistSafeIndex()
    return true
  }

  function detachJob(id?: number): void {
    if (id !== undefined && jobId.value !== id) return
    jobId.value = null
    questionStates.value = []
    generationSummary.value = null
    phase.value = derivePhase()
    persistSafeIndex()
  }

  function setQuestionStates(value: ConfigQuestionGenerationState[]): void {
    questionStates.value = value.map((item) => ({ ...item }))
  }

  function markJobSubmissionPending(
    token: string,
    kind: 'generate' | 'retry',
    mode: GenerationMode | null = null,
    retainedSummary?: ConfigGenerationSummary,
  ): boolean {
    if (!/^[0-9a-f]{32}$/.test(token)) throw new Error('Invalid client request token')
    if (kind === 'generate' && mode === null) throw new Error('Generation mode is required')
    if (retainedSummary !== undefined && !validGenerationSummary(retainedSummary)) {
      throw new Error('Invalid generation summary')
    }
    if (hasPendingSubmission.value) return false
    pendingJobRequestToken.value = token
    pendingJobRequestKind.value = kind
    pendingGenerationMode.value = mode
    if (retainedSummary !== undefined) generationSummary.value = { ...retainedSummary }
    persistSafeIndex()
    return true
  }

  function clearGenerationSubmissionPending(): void {
    pendingGenerationMode.value = null
    pendingJobRequestToken.value = null
    pendingJobRequestKind.value = null
    persistSafeIndex()
  }

  function markUploadSubmissionPending(token: string): boolean {
    if (!/^[0-9a-f]{32}$/.test(token)) throw new Error('Invalid client request token')
    if (hasPendingSubmission.value) return false
    pendingUploadRequestToken.value = token
    persistSafeIndex()
    return true
  }

  function clearUploadSubmissionPending(): void {
    pendingUploadRequestToken.value = null
    persistSafeIndex()
  }

  function sourceRequest(
    mode: GenerationMode,
    syncToQuestionBank = false,
    curriculumVolumeId?: string,
  ): ConfigGenerationRequest {
    if (!canGenerate.value || sourceId.value === null || sourceRevision.value === null) {
      throw new Error('Config source is not ready')
    }
    const request: ConfigGenerationRequest = {
      source_id: sourceId.value,
      source_revision: sourceRevision.value,
      generation_mode: mode,
      sync_to_question_bank: syncToQuestionBank,
      decisions: decisions.value.map((item) => ({ ...item })),
    }
    if (assetDecisions.value.length > 0) {
      request.asset_decisions = assetDecisions.value.map((item) => ({ ...item }))
    }
    if (syncToQuestionBank) {
      const normalizedVolumeId = String(curriculumVolumeId ?? '').trim()
      if (!normalizedVolumeId) {
        throw new Error('Curriculum volume is required for question analysis')
      }
      request.curriculum_volume_id = normalizedVolumeId
    }
    return request
  }

  async function reloadEditorForGeneration(
    context: number,
    loader: (sessionId: number) => Promise<ConfigEditorResponse> = fetchConfigEditor,
  ): Promise<boolean> {
    const expectedSessionId = sessionId.value
    if (expectedSessionId === null) return false
    const loaded = await loader(expectedSessionId)
    if (context !== generationContext || sessionId.value !== expectedSessionId) return false
    setEditor(loaded)
    return true
  }

  async function loadSelectedSessionWorkspace(
    expectedSessionId: number,
    overrides: Partial<ConfigWorkspaceHydrationDependencies> = {},
  ): Promise<void> {
    if (!positiveInteger(expectedSessionId) || sessionId.value !== expectedSessionId) return
    const request = ++hydrationRequest
    const loadActiveSource = overrides.loadActiveSource ?? fetchActiveConfigSource
    const loadEditor = overrides.loadEditor ?? fetchConfigEditor
    const [sourceResult, editorResult] = await Promise.allSettled([
      loadActiveSource(expectedSessionId),
      loadEditor(expectedSessionId),
    ])
    if (request !== hydrationRequest || sessionId.value !== expectedSessionId) return

    if (sourceResult.status === 'fulfilled'
      && sourceResult.value.session_id === expectedSessionId) {
      source.value = sourceResult.value
      sourceId.value = sourceResult.value.source_id
      sourceRevision.value = sourceResult.value.source_revision
    } else if (sourceResult.status === 'rejected' && isNotFound(sourceResult.reason)) {
      source.value = null
      sourceId.value = null
      sourceRevision.value = null
      sourceDuplicates.value = []
      duplicatesUnavailable.value = false
      duplicatesRequest += 1
    }
    if (editorResult.status === 'fulfilled'
      && editorResult.value.session_id === expectedSessionId) {
      editor.value = editorResult.value
    }
    await restoreLatestGenerationJob(request, overrides)
    if (request !== hydrationRequest || sessionId.value !== expectedSessionId) return
    phase.value = derivePhase()
    persistSafeIndex()
    if (phase.value !== 'editor') void loadSourceDuplicates()
  }

  async function restoreLatestGenerationJob(
    request: number,
    overrides: Partial<ConfigWorkspaceHydrationDependencies>,
  ): Promise<void> {
    const currentSource = source.value
    const currentJobId = jobId.value
    const currentJob = currentJobId === null ? null : useJobStore().jobs[currentJobId]
    if (currentSource === null || hasPendingSubmission.value
      || (currentJobId !== null && !['succeeded', 'failed', 'cancelled'].includes(currentJob?.status ?? ''))) return
    try {
      const latest = await (overrides.loadLatestJob ?? fetchLatestConfigGenerationJob)(
        currentSource.session_id,
        {
          source_id: currentSource.source_id,
          source_revision: currentSource.source_revision,
          generation_mode: 'batched',
          decisions: [],
          sync_to_question_bank: true,
        },
      )
      if (request !== hydrationRequest || source.value !== currentSource
        || sessionId.value !== currentSource.session_id || jobId.value !== currentJobId
        || hasPendingSubmission.value) return
      if (latest.job_type !== 'config_generation'
        || latest.payload.session_id !== currentSource.session_id
        || latest.payload.source_id !== currentSource.source_id
        || latest.payload.source_revision !== currentSource.source_revision
        || (currentJobId !== null && latest.id <= currentJobId)) return
      useJobStore().track(latest)
      jobId.value = latest.id
    } catch {
      // A source may have no generation yet; leave its current workspace intact.
    }
  }

  async function hydrateSafeIndex(
    activeSessionIds: readonly number[],
    selectedSessionId: number | null,
    overrides: Partial<ConfigWorkspaceHydrationDependencies> = {},
    loadWorkspace = true,
  ): Promise<void> {
    const raw = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)
    const candidate = parsePersisted(raw)
    if (candidate === null) {
      if (raw !== null) localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
      if (selectedSessionId !== null && activeSessionIds.includes(selectedSessionId)) {
        if (selectSession(selectedSessionId)) {
          if (loadWorkspace) await loadSelectedSessionWorkspace(selectedSessionId, overrides)
        }
      }
      return
    }
    if (!activeSessionIds.includes(candidate.sessionId) || selectedSessionId !== candidate.sessionId) {
      forceClearWorkspace()
      return
    }

    const request = ++hydrationRequest
    sessionId.value = candidate.sessionId
    sourceId.value = null
    sourceRevision.value = null
    jobId.value = null
    decisions.value = []
    assetDecisions.value = []
    phase.value = 'draft'
    resetMemory()
    pendingGenerationMode.value = candidate.pendingGenerationMode ?? null
    pendingJobRequestToken.value = candidate.pendingJobRequestToken ?? null
    pendingJobRequestKind.value = candidate.pendingJobRequestKind ?? null
    pendingUploadRequestToken.value = candidate.pendingUploadRequestToken ?? null
    if (!loadWorkspace) {
      // Restore only safe metadata and submission guards outside exam setup.
      sourceId.value = candidate.sourceId
      sourceRevision.value = candidate.sourceRevision
      jobId.value = candidate.jobId
      decisions.value = candidate.decisions.map(item => ({ ...item }))
      assetDecisions.value = (candidate.assetDecisions ?? []).map(item => ({ ...item }))
      generationSummary.value = candidate.generationSummary ?? null
      phase.value = candidate.phase
      return
    }
    const dependencies: ConfigWorkspaceHydrationDependencies = {
      loadActiveSource: overrides.loadActiveSource ?? fetchActiveConfigSource,
      loadSource: overrides.loadSource ?? fetchConfigSource,
      loadJob: overrides.loadJob ?? jobApi.getJob,
      loadLatestJob: overrides.loadLatestJob ?? fetchLatestConfigGenerationJob,
      loadEditor: overrides.loadEditor ?? fetchConfigEditor,
    }
    const [initialSourceResult, jobResult, editorResult] = await Promise.allSettled([
      candidate.sourceId === null
        ? dependencies.loadActiveSource(candidate.sessionId)
        : dependencies.loadSource(candidate.sessionId, candidate.sourceId),
      candidate.jobId === null
        ? Promise.resolve<JobResponse | null>(null)
        : dependencies.loadJob(candidate.jobId),
      dependencies.loadEditor(candidate.sessionId),
    ])
    if (request !== hydrationRequest || sessionId.value !== candidate.sessionId) return

    let sourceResult = initialSourceResult
    let sourceWasReplaced = false
    if (candidate.sourceId !== null && sourceResult.status === 'rejected'
      && (isSourceChanged(sourceResult.reason) || isNotFound(sourceResult.reason))) {
      sourceWasReplaced = true
      const [activeSourceResult] = await Promise.allSettled([
        dependencies.loadActiveSource(candidate.sessionId),
      ])
      sourceResult = activeSourceResult
      if (request !== hydrationRequest || sessionId.value !== candidate.sessionId) return
    }

    const sanitized: PersistedConfigWorkspace = {
      ...candidate,
      decisions: candidate.decisions.map((item) => ({ ...item })),
      assetDecisions: (candidate.assetDecisions ?? []).map((item) => ({ ...item })),
    }
    let candidateChanged = false
    if (sourceWasReplaced) {
      sanitized.sourceId = null
      sanitized.sourceRevision = null
      sanitized.jobId = null
      sanitized.decisions = []
      sanitized.assetDecisions = []
      delete sanitized.generationSummary
      candidateChanged = true
    }
    if (sourceResult.status === 'fulfilled' && sourceResult.value !== null) {
      const loaded = sourceResult.value
      const matchesCandidate = sourceWasReplaced || candidate.sourceId === null
        || (loaded.source_id === candidate.sourceId
          && loaded.source_revision === candidate.sourceRevision)
      if (loaded.session_id === candidate.sessionId && matchesCandidate) {
        source.value = loaded
        sourceId.value = loaded.source_id
        sourceRevision.value = loaded.source_revision
        decisions.value = sourceWasReplaced || candidate.sourceId === null
          ? []
          : candidate.decisions.map((item) => ({ ...item }))
        const candidateAssetIds = new Set([
          ...(loaded.assets ?? []).map((item) => item.asset_id),
          ...(loaded.ambiguous_assets ?? []).map((item) => item.candidate_id),
        ])
        assetDecisions.value = sourceWasReplaced || candidate.sourceId === null
          ? []
          : (candidate.assetDecisions ?? [])
            .filter((item) => candidateAssetIds.has(item.candidate_id))
            .map((item) => ({ ...item }))
        if (candidate.sourceId === null || sourceWasReplaced) {
          sanitized.sourceId = loaded.source_id
          sanitized.sourceRevision = loaded.source_revision
          sanitized.decisions = []
          sanitized.assetDecisions = []
          candidateChanged = true
        }
      } else {
        sanitized.sourceId = null
        sanitized.sourceRevision = null
        sanitized.decisions = []
        candidateChanged = true
      }
    } else if (sourceResult.status === 'rejected' && isNotFound(sourceResult.reason)) {
      sanitized.sourceId = null
      sanitized.sourceRevision = null
      sanitized.decisions = []
      candidateChanged = true
    }

    if (sourceWasReplaced) {
      sanitized.jobId = null
      candidateChanged = true
    } else if (jobResult.status === 'fulfilled' && jobResult.value !== null) {
      if (jobBelongsTo(jobResult.value, candidate.sessionId, candidate)) {
        useJobStore().track(jobResult.value)
        jobId.value = jobResult.value.id
        generationSummary.value = candidate.generationSummary
          ? { ...candidate.generationSummary }
          : null
      } else {
        sanitized.jobId = null
        candidateChanged = true
      }
    } else if (jobResult.status === 'rejected' && isNotFound(jobResult.reason)) {
      sanitized.jobId = null
      candidateChanged = true
    }

    if (editorResult.status === 'fulfilled'
      && editorResult.value.session_id === candidate.sessionId) {
      editor.value = editorResult.value
    }
    await restoreLatestGenerationJob(request, overrides)
    if (request !== hydrationRequest || sessionId.value !== candidate.sessionId) return
    if (jobId.value !== null && jobId.value !== sanitized.jobId) {
      sanitized.jobId = jobId.value
      candidateChanged = true
    }
    phase.value = derivePhase()
    if (phase.value !== 'editor') void loadSourceDuplicates()
    if (candidateChanged) {
      sanitized.phase = phase.value
      localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(sanitized))
    }
  }

  function updateEditor(edit: ConfigEditorEdit): void {
    const index = editorEdits.value.findIndex((item) => item.row_id === edit.row_id)
    if (index < 0) editorEdits.value.push({ ...edit })
    else editorEdits.value[index] = { ...editorEdits.value[index], ...edit }
    editorContextGeneration += 1
    const changedFields = new Set(Object.keys(edit).filter((key) => key !== 'row_id'))
    serverIssues.value = serverIssues.value.filter((issue) => issue.row_id !== null
      && (issue.row_id !== edit.row_id || !changedFields.has(issue.field)))
    if (saveStatus.value === 'saving' || saveStatus.value === 'failure') saveStatus.value = 'idle'
    editorDirty.value = true
  }

  function addEditorCommand(command: ConfigEditorCommand): void {
    editorCommands.value.push(command)
    editorContextGeneration += 1
    serverIssues.value = []
    if (saveStatus.value === 'saving' || saveStatus.value === 'failure') saveStatus.value = 'idle'
    editorDirty.value = true
  }

  function captureEditorContext(): ConfigEditorContextToken {
    return {
      sessionId: sessionId.value,
      sourceRevision: sourceRevision.value,
      editorRevision: editor.value?.revision ?? null,
      generation: editorContextGeneration,
    }
  }

  function isEditorContextCurrent(token: ConfigEditorContextToken): boolean {
    return token.sessionId === sessionId.value
      && token.sourceRevision === sourceRevision.value
      && token.editorRevision === (editor.value?.revision ?? null)
      && token.generation === editorContextGeneration
  }

  function recordServerIssues(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.status !== 422
      || error.code !== 'invalid_config_editor' || !Array.isArray(error.details.issues)
      || error.details.issues.length < 1 || error.details.issues.length > 1_000) {
      serverIssues.value = []
      return false
    }
    const safeIssues = error.details.issues.filter(safeServerIssue)
    if (safeIssues.length === 0) {
      serverIssues.value = []
      return false
    }
    const knownRowIds = new Set(effectiveEditorRows.value.map((row) => row.row_id))
    serverIssues.value = safeIssues.map((safeIssue) => {
      return {
        ...safeIssue,
        row_id: safeIssue.row_id !== null && knownRowIds.has(safeIssue.row_id)
          && editableIssueFields.has(safeIssue.field)
          ? safeIssue.row_id
          : null,
      }
    })
    return true
  }

  function noteSaveFailed(): void {
    // A failed write must not clear teacher work.
    saveStatus.value = 'failure'
  }

  function noteSaveUnknown(): void {
    saveStatus.value = 'unknown'
  }

  function buildSaveRequest(): ConfigEditorSaveRequest {
    if (editor.value === null) throw new Error('Config editor is not loaded')
    return {
      revision: editor.value.revision,
      edits: editorEdits.value.map((edit) => ({ ...edit })),
      commands: editorCommands.value.map((command) => {
        if (command.kind === 'replace_parts') {
          return { ...command, parts: command.parts.map((part) => ({ ...part })) }
        }
        if (command.kind === 'replace_question_structure') {
          return {
            ...command,
            parts: command.parts.map((part) => ({
              ...part,
              steps: part.steps.map((step) => ({ ...step })),
            })),
          }
        }
        return { ...command }
      }),
    }
  }

  function beginSave(): boolean {
    if (saveStatus.value === 'saving' || saveStatus.value === 'unknown' || editor.value === null) return false
    saveStatus.value = 'saving'
    mappingStatus.value = null
    serverIssues.value = []
    return true
  }

  function markConflict(): void {
    saveStatus.value = 'conflict'
  }

  function replaceWithAuthoritativeEditor(value: ConfigEditorSaveResponse): void {
    setEditor(value)
    saveStatus.value = 'success'
    mappingStatus.value = value.save_result.mapping_status
  }

  function replaceWithReconciledEditor(value: ConfigEditorResponse): void {
    setEditor(value)
    saveStatus.value = 'success'
    mappingStatus.value = null
  }

  return {
    sessionId, phase, sourceId, sourceRevision, jobId, decisions, assetDecisions,
    source, questionStates, editor, editorEdits, editorCommands, serverIssues, generationSummary,
    pendingGenerationMode, pendingJobRequestToken, pendingJobRequestKind,
    pendingUploadRequestToken, sourceLoading, sourceError,
    sourceDuplicates, duplicatesUnavailable, loadSourceDuplicates,
    saveStatus, mappingStatus, hasDirtyEditor, hasPendingSubmission,
    effectiveEditorRows, effectiveTotalScore,
    canGenerate, hydrateSafeIndex, persistSafeIndex, clearWorkspace,
    selectSession, selectSource, discardEditorDraft, setSource, acceptUploadedSource,
    updateDecisions, updateAssetDecisions, loadSource,
    setEditor, captureGenerationContext, attachJob, detachJob, sourceRequest,
    setQuestionStates,
    markJobSubmissionPending, clearGenerationSubmissionPending,
    markUploadSubmissionPending, clearUploadSubmissionPending,
    reloadEditorForGeneration, loadSelectedSessionWorkspace,
    updateEditor, addEditorCommand, captureEditorContext,
    isEditorContextCurrent, recordServerIssues, noteSaveFailed, noteSaveUnknown,
    buildSaveRequest, beginSave, markConflict, replaceWithAuthoritativeEditor,
    replaceWithReconciledEditor,
  }
})
