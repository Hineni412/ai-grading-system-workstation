import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchConfigEditor,
  fetchConfigSource,
  type ConfigGenerationRequest,
  type GenerationMode,
  type ConfigEditorCommand,
  type ConfigEditorEdit,
  type ConfigEditorResponse,
  type ConfigSource,
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
  generationSummary?: ConfigGenerationSummary
}

export interface ConfigGenerationSummary {
  totalQuestions: number
  succeededQuestions: number
  failedQuestions: number
}

export type ConfigSourceLoader = (
  sessionId: number,
  sourceId: string,
  signal?: AbortSignal,
) => Promise<ConfigSource>

export interface ConfigWorkspaceHydrationDependencies {
  loadSource: ConfigSourceLoader
  loadJob: (jobId: number, signal?: AbortSignal) => Promise<JobResponse>
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
  return Object.keys(item).sort().join(',') === 'excluded,question_id,question_type'
    && typeof item.question_id === 'string'
    && ['choice', 'fill_blank', 'calculation', 'proof', 'comprehensive']
      .includes(String(item.question_type))
    && typeof item.excluded === 'boolean'
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
    const keys = Object.keys(item).sort().join(',')
    if (keys !== 'decisions,jobId,phase,sessionId,sourceId,sourceRevision'
      && keys !== 'decisions,generationSummary,jobId,phase,sessionId,sourceId,sourceRevision') return null
    if (!positiveInteger(item.sessionId) || typeof item.phase !== 'string'
      || !phases.has(item.phase as ConfigPhase)
      || (item.sourceId !== null && !validSourceId(item.sourceId))
      || (item.sourceRevision !== null && !validRevision(item.sourceRevision))
      || (item.jobId !== null && !positiveInteger(item.jobId))
      || !Array.isArray(item.decisions) || !item.decisions.every(validDecision)
      || ('generationSummary' in item && !validGenerationSummary(item.generationSummary))) return null
    if ((item.sourceId === null) !== (item.sourceRevision === null)) return null
    return item as unknown as PersistedConfigWorkspace
  } catch {
    return null
  }
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'not_found'
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
  const source = ref<ConfigSource | null>(null)
  const editor = ref<ConfigEditorResponse | null>(null)
  const editorEdits = ref<ConfigEditorEdit[]>([])
  const editorCommands = ref<ConfigEditorCommand[]>([])
  const editorDirty = ref(false)
  const generationSummary = ref<ConfigGenerationSummary | null>(null)
  const sourceLoading = ref(false)
  const sourceError = ref('')
  let sourceLoadGeneration = 0
  let hydrationRequest = 0
  let generationContext = 0
  let requestedSourceId: string | null = null

  const hasDirtyEditor = computed(() => editorDirty.value)
  const canGenerate = computed(() => {
    if (sessionId.value === null || source.value === null || sourceId.value === null
      || sourceRevision.value === null || source.value.questions.length === 0) return false
    const excluded = new Set(decisions.value.filter((item) => item.excluded)
      .map((item) => item.question_id))
    return source.value.questions.some((question) => !excluded.has(question.question_id))
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
      decisions: decisions.value.map((item) => ({ ...item })),
    }
    if (generationSummary.value !== null) {
      snapshot.generationSummary = { ...generationSummary.value }
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
    source.value = null
    generationSummary.value = null
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    sourceLoading.value = false
    sourceError.value = ''
    requestedSourceId = null
    sourceLoadGeneration += 1
  }

  function forceClearWorkspace(): void {
    hydrationRequest += 1
    sessionId.value = null
    sourceId.value = null
    sourceRevision.value = null
    jobId.value = null
    decisions.value = []
    phase.value = 'draft'
    resetMemory()
    localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
  }

  function clearWorkspace(discardDirty = false): boolean {
    if (editorDirty.value && !discardDirty) return false
    forceClearWorkspace()
    editorDirty.value = false
    return true
  }

  function selectSession(id: number | null, discardDirty = false): boolean {
    if (id === null) return clearWorkspace(discardDirty)
    if (!positiveInteger(id)) throw new Error('Invalid session id')
    if (sessionId.value === id) return true
    if (editorDirty.value && !discardDirty) return false
    hydrationRequest += 1
    sessionId.value = id
    sourceId.value = null
    sourceRevision.value = null
    jobId.value = null
    decisions.value = []
    phase.value = 'draft'
    resetMemory()
    editorDirty.value = false
    persistSafeIndex()
    return true
  }

  function selectSource(id: string | null, discardDirty = false): boolean {
    if (id !== null && !validSourceId(id)) throw new Error('Invalid source id')
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
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    editorDirty.value = false
    phase.value = 'draft'
    persistSafeIndex()
    return true
  }

  function discardEditorDraft(): void {
    editorEdits.value = []
    editorCommands.value = []
    editorDirty.value = false
  }

  function setSource(value: ConfigSource): void {
    if (sessionId.value !== value.session_id) throw new Error('Source session does not match')
    if (sourceId.value !== value.source_id || sourceRevision.value !== value.source_revision) {
      generationContext += 1
      generationSummary.value = null
    }
    requestedSourceId = null
    sourceId.value = value.source_id
    sourceRevision.value = value.source_revision
    source.value = value
    generationSummary.value = null
    phase.value = derivePhase()
    sourceError.value = ''
    persistSafeIndex()
  }

  function acceptUploadedSource(value: ConfigSource): void {
    if (sessionId.value !== value.session_id) throw new Error('Source session does not match')
    hydrationRequest += 1
    generationContext += 1
    sourceLoadGeneration += 1
    requestedSourceId = null
    sourceId.value = value.source_id
    sourceRevision.value = value.source_revision
    source.value = value
    jobId.value = null
    decisions.value = []
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    editorDirty.value = false
    sourceLoading.value = false
    sourceError.value = ''
    phase.value = 'source'
    persistSafeIndex()
  }

  function updateDecisions(value: QuestionDecision[]): void {
    if (source.value === null) return
    const knownIds = new Set(source.value.questions.map((question) => question.question_id))
    decisions.value = value.filter((decision) => knownIds.has(decision.question_id)
      && validDecision(decision)).map((decision) => ({ ...decision }))
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
    if (context !== generationContext || sessionId.value === null || sourceId.value === null) return false
    if (retainedSummary !== undefined && !validGenerationSummary(retainedSummary)) {
      throw new Error('Invalid generation summary')
    }
    jobId.value = id
    generationSummary.value = retainedSummary === undefined ? null : { ...retainedSummary }
    phase.value = derivePhase()
    persistSafeIndex()
    return true
  }

  function sourceRequest(mode: GenerationMode): ConfigGenerationRequest {
    if (!canGenerate.value || sourceId.value === null || sourceRevision.value === null) {
      throw new Error('Config source is not ready')
    }
    return {
      source_id: sourceId.value,
      source_revision: sourceRevision.value,
      generation_mode: mode,
      decisions: decisions.value.map((item) => ({ ...item })),
    }
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

  async function hydrateSafeIndex(
    activeSessionIds: readonly number[],
    selectedSessionId: number | null,
    overrides: Partial<ConfigWorkspaceHydrationDependencies> = {},
  ): Promise<void> {
    const raw = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)
    const candidate = parsePersisted(raw)
    if (candidate === null) {
      if (raw !== null) localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
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
    phase.value = 'draft'
    resetMemory()
    const dependencies: ConfigWorkspaceHydrationDependencies = {
      loadSource: overrides.loadSource ?? fetchConfigSource,
      loadJob: overrides.loadJob ?? jobApi.getJob,
      loadEditor: overrides.loadEditor ?? fetchConfigEditor,
    }
    const [sourceResult, jobResult, editorResult] = await Promise.allSettled([
      candidate.sourceId === null
        ? Promise.resolve<ConfigSource | null>(null)
        : dependencies.loadSource(candidate.sessionId, candidate.sourceId),
      candidate.jobId === null
        ? Promise.resolve<JobResponse | null>(null)
        : dependencies.loadJob(candidate.jobId),
      dependencies.loadEditor(candidate.sessionId),
    ])
    if (request !== hydrationRequest || sessionId.value !== candidate.sessionId) return

    const sanitized: PersistedConfigWorkspace = {
      ...candidate,
      decisions: candidate.decisions.map((item) => ({ ...item })),
    }
    let candidateChanged = false
    if (sourceResult.status === 'fulfilled' && sourceResult.value !== null) {
      const loaded = sourceResult.value
      if (loaded.session_id === candidate.sessionId && loaded.source_id === candidate.sourceId
        && loaded.source_revision === candidate.sourceRevision) {
        source.value = loaded
        sourceId.value = loaded.source_id
        sourceRevision.value = loaded.source_revision
        decisions.value = candidate.decisions.map((item) => ({ ...item }))
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

    if (jobResult.status === 'fulfilled' && jobResult.value !== null) {
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
    phase.value = derivePhase()
    if (candidateChanged) {
      sanitized.phase = phase.value
      localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(sanitized))
    }
  }

  function updateEditor(edit: ConfigEditorEdit): void {
    const index = editorEdits.value.findIndex((item) => item.row_id === edit.row_id)
    if (index < 0) editorEdits.value.push({ ...edit })
    else editorEdits.value[index] = { ...editorEdits.value[index], ...edit }
    editorDirty.value = true
  }

  function addEditorCommand(command: ConfigEditorCommand): void {
    editorCommands.value.push(command)
    editorDirty.value = true
  }

  function noteSaveFailed(): void {
    // A failed write must not clear teacher work.
  }

  return {
    sessionId, phase, sourceId, sourceRevision, jobId, decisions,
    source, editor, editorEdits, editorCommands, generationSummary, sourceLoading, sourceError,
    hasDirtyEditor, canGenerate, hydrateSafeIndex, persistSafeIndex, clearWorkspace,
    selectSession, selectSource, discardEditorDraft, setSource, acceptUploadedSource,
    updateDecisions, loadSource,
    setEditor, captureGenerationContext, attachJob, sourceRequest,
    reloadEditorForGeneration, updateEditor, addEditorCommand, noteSaveFailed,
  }
})
