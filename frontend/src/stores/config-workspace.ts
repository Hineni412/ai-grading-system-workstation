import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  fetchConfigSource,
  type ConfigEditorCommand,
  type ConfigEditorEdit,
  type ConfigEditorResponse,
  type ConfigSource,
  type QuestionDecision,
} from '../api/config-workspace'

export const CONFIG_WORKSPACE_STORAGE_KEY = 'ai-grading:config-workspace:v1'
export type ConfigPhase = 'draft' | 'source' | 'generation' | 'editor'

export interface PersistedConfigWorkspace {
  sessionId: number
  phase: ConfigPhase
  sourceId: string | null
  sourceRevision: string | null
  jobId: number | null
  decisions: QuestionDecision[]
}

export type ConfigSourceLoader = (
  sessionId: number,
  sourceId: string,
  signal?: AbortSignal,
) => Promise<ConfigSource>

const phases = new Set<ConfigPhase>(['draft', 'source', 'generation', 'editor'])

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function sourceId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)
}

function revision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function decision(value: unknown): value is QuestionDecision {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return Object.keys(item).sort().join(',') === 'excluded,question_id,question_type'
    && typeof item.question_id === 'string'
    && ['choice', 'fill_blank', 'calculation', 'proof', 'comprehensive'].includes(String(item.question_type))
    && typeof item.excluded === 'boolean'
}

function parsePersisted(raw: string | null): PersistedConfigWorkspace | null {
  if (!raw) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
    const item = value as Record<string, unknown>
    if (Object.keys(item).sort().join(',') !== 'decisions,jobId,phase,sessionId,sourceId,sourceRevision') return null
    if (!positiveInteger(item.sessionId) || typeof item.phase !== 'string'
      || !phases.has(item.phase as ConfigPhase)
      || (item.sourceId !== null && !sourceId(item.sourceId))
      || (item.sourceRevision !== null && !revision(item.sourceRevision))
      || (item.jobId !== null && !positiveInteger(item.jobId))
      || !Array.isArray(item.decisions) || !item.decisions.every(decision)) return null
    if ((item.sourceId === null) !== (item.sourceRevision === null)) return null
    return item as unknown as PersistedConfigWorkspace
  } catch {
    return null
  }
}

export const useConfigWorkspaceStore = defineStore('config-workspace', () => {
  const sessionId = ref<number | null>(null)
  const phase = ref<ConfigPhase>('draft')
  const sourceIdValue = ref<string | null>(null)
  const sourceRevision = ref<string | null>(null)
  const jobId = ref<number | null>(null)
  const decisions = ref<QuestionDecision[]>([])

  const source = ref<ConfigSource | null>(null)
  const editor = ref<ConfigEditorResponse | null>(null)
  const editorEdits = ref<ConfigEditorEdit[]>([])
  const editorCommands = ref<ConfigEditorCommand[]>([])
  const editorDirty = ref(false)
  const sourceLoading = ref(false)
  const sourceError = ref('')
  let sourceRequest = 0

  const hasDirtyEditor = computed(() => editorDirty.value)

  function safeSnapshot(): PersistedConfigWorkspace | null {
    if (sessionId.value === null) return null
    return {
      sessionId: sessionId.value,
      phase: phase.value,
      sourceId: sourceIdValue.value,
      sourceRevision: sourceRevision.value,
      jobId: jobId.value,
      decisions: decisions.value.map((item) => ({ ...item })),
    }
  }

  function persistSafeIndex(): void {
    const snapshot = safeSnapshot()
    if (snapshot === null) localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
    else localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(snapshot))
  }

  function resetMemory(): void {
    source.value = null
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    sourceLoading.value = false
    sourceError.value = ''
    sourceRequest += 1
  }

  function clearWorkspace(): void {
    sessionId.value = null
    phase.value = 'draft'
    sourceIdValue.value = null
    sourceRevision.value = null
    jobId.value = null
    decisions.value = []
    resetMemory()
    localStorage.removeItem(CONFIG_WORKSPACE_STORAGE_KEY)
  }

  function restoreSafeIndex(activeSessionIds: readonly number[]): void {
    const saved = parsePersisted(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY))
    if (saved === null || !activeSessionIds.includes(saved.sessionId)) {
      clearWorkspace()
      return
    }
    sessionId.value = saved.sessionId
    phase.value = saved.phase
    sourceIdValue.value = saved.sourceId
    sourceRevision.value = saved.sourceRevision
    jobId.value = saved.jobId
    decisions.value = saved.decisions.map((item) => ({ ...item }))
    resetMemory()
  }

  function selectSession(id: number | null): void {
    if (id === null) {
      clearWorkspace()
      return
    }
    if (!positiveInteger(id)) throw new Error('Invalid session id')
    if (sessionId.value !== id) {
      sessionId.value = id
      phase.value = 'draft'
      sourceIdValue.value = null
      sourceRevision.value = null
      jobId.value = null
      decisions.value = []
      resetMemory()
    }
    persistSafeIndex()
  }

  function selectSource(id: string | null): void {
    if (id !== null && !sourceId(id)) throw new Error('Invalid source id')
    sourceRequest += 1
    sourceIdValue.value = id
    sourceRevision.value = null
    source.value = null
    editor.value = null
    editorEdits.value = []
    editorCommands.value = []
    if (id !== null) phase.value = 'source'
    persistSafeIndex()
  }

  function setSource(value: ConfigSource): void {
    if (sessionId.value !== value.session_id) throw new Error('Source session does not match')
    sourceIdValue.value = value.source_id
    sourceRevision.value = value.source_revision
    source.value = value
    phase.value = 'source'
    sourceError.value = ''
    persistSafeIndex()
  }

  async function loadSource(
    expectedSessionId: number,
    expectedSourceId: string,
    loader: ConfigSourceLoader = fetchConfigSource,
  ): Promise<void> {
    const request = ++sourceRequest
    sourceLoading.value = true
    sourceError.value = ''
    try {
      const loaded = await loader(expectedSessionId, expectedSourceId)
      if (request !== sourceRequest || sessionId.value !== expectedSessionId
        || sourceIdValue.value !== expectedSourceId) return
      setSource(loaded)
    } catch {
      if (request !== sourceRequest || sessionId.value !== expectedSessionId
        || sourceIdValue.value !== expectedSourceId) return
      sourceError.value = '试卷来源暂时无法读取，可以重新加载。'
    } finally {
      if (request === sourceRequest) sourceLoading.value = false
    }
  }

  function setEditor(value: ConfigEditorResponse): void {
    if (sessionId.value !== null && value.session_id !== sessionId.value) return
    sessionId.value = value.session_id
    editor.value = value
    editorEdits.value = []
    editorCommands.value = []
    editorDirty.value = false
    if (value.configured) phase.value = 'editor'
    persistSafeIndex()
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
    sessionId,
    phase,
    sourceId: sourceIdValue,
    sourceRevision,
    jobId,
    decisions,
    source,
    editor,
    editorEdits,
    editorCommands,
    sourceLoading,
    sourceError,
    hasDirtyEditor,
    restoreSafeIndex,
    persistSafeIndex,
    clearWorkspace,
    selectSession,
    selectSource,
    setSource,
    loadSource,
    setEditor,
    updateEditor,
    addEditorCommand,
    noteSaveFailed,
  }
})
