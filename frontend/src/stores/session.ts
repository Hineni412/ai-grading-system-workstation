import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  createSessionDraft,
  fetchSessions,
  renameSession,
  updateSessionMetadata,
  type SessionDraftCreator,
  type SessionLoader,
  type SessionMetadataUpdater,
  type SessionRenamer,
  type SessionSummary,
} from '../api/sessions'
import { isAmbiguousWriteError } from '../api/errors'

export const SESSION_STORAGE_KEY = 'ai-grading:selected-session:v1'
export type SessionLoadState = 'idle' | 'loading' | 'ready' | 'error'

export class SessionDraftOutcomeUnknownError extends Error {
  constructor() {
    super('考试草稿创建结果未知')
    this.name = 'SessionDraftOutcomeUnknownError'
  }
}

function readPersistedId(): number | null {
  const raw = localStorage.getItem(SESSION_STORAGE_KEY)
  if (!raw || !/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<SessionSummary[]>([])
  const selectedSessionId = ref<number | null>(null)
  const loadState = ref<SessionLoadState>('idle')
  const errorMessage = ref('')
  let persistedCandidateId: number | null = null
  let loadRequest = 0

  const currentSession = computed(
    () =>
      sessions.value.find((session) => session.id === selectedSessionId.value) ?? null,
  )

  async function initialize(loader: SessionLoader = fetchSessions): Promise<void> {
    const request = ++loadRequest
    persistedCandidateId = readPersistedId()
    const candidateId = persistedCandidateId
    selectedSessionId.value = null
    loadState.value = 'loading'
    errorMessage.value = ''

    try {
      const loadedSessions = await loader()
      if (request !== loadRequest) return
      sessions.value = loadedSessions

      if (
        candidateId !== null &&
        loadedSessions.some((session) => session.id === candidateId)
      ) {
        selectedSessionId.value = candidateId
      } else {
        persistedCandidateId = null
        localStorage.removeItem(SESSION_STORAGE_KEY)
      }

      loadState.value = 'ready'
    } catch {
      if (request !== loadRequest) return
      sessions.value = []
      loadState.value = 'error'
      errorMessage.value = '考试列表暂时无法读取。已保存的选择没有丢失，可以重新加载。'
    }
  }

  async function createDraft(
    name: string,
    creator: SessionDraftCreator = createSessionDraft,
    loader: SessionLoader = fetchSessions,
    curriculumVolumeId: string | null = null,
  ): Promise<SessionSummary> {
    const normalized = name.trim()
    if (!normalized) throw new Error('考试名称不能为空')
    const knownIds = new Set(sessions.value.map((session) => session.id))
    let created: SessionSummary
    try {
      created = curriculumVolumeId
        ? await creator(normalized, curriculumVolumeId)
        : await creator(normalized)
    } catch (error) {
      if (!isAmbiguousWriteError(error)) throw error
      let loaded: SessionSummary[]
      try {
        loaded = await loader()
      } catch {
        throw new SessionDraftOutcomeUnknownError()
      }
      const reconciled = loaded.find((session) => !knownIds.has(session.id)
        && session.name.trim() === normalized
        && (!curriculumVolumeId || session.curriculum_volume_id === curriculumVolumeId)
        && !session.is_deleted)
      sessions.value = loaded
      loadState.value = 'ready'
      if (!reconciled) throw new Error('考试草稿创建请求已核对，服务器未出现新草稿')
      created = reconciled
    }
    persistedCandidateId = created.id
    localStorage.setItem(SESSION_STORAGE_KEY, String(created.id))
    await initialize(loader)
    return created
  }

  /* 对任意考试改名；写请求结果含糊时用权威列表核对，不重发请求 */
  async function renameSessionById(
    sessionId: number,
    name: string,
    renamer: SessionRenamer = renameSession,
    loader: SessionLoader = fetchSessions,
  ): Promise<SessionSummary> {
    const normalized = name.trim()
    if (!normalized) throw new Error('考试名称不能为空')

    let renamed: SessionSummary
    try {
      renamed = await renamer(sessionId, normalized)
    } catch (error) {
      if (!isAmbiguousWriteError(error)) throw error

      const loaded = await loader()
      sessions.value = loaded
      loadState.value = 'ready'
      const confirmed = loaded.find((session) =>
        session.id === sessionId
        && session.name.trim() === normalized
        && !session.is_deleted)
      if (!confirmed) throw error
      renamed = confirmed
    }

    const index = sessions.value.findIndex((session) => session.id === renamed.id)
    if (index >= 0) sessions.value[index] = renamed
    return renamed
  }

  async function renameSelected(
    name: string,
    renamer: SessionRenamer = renameSession,
    loader: SessionLoader = fetchSessions,
  ): Promise<SessionSummary> {
    if (selectedSessionId.value === null) throw new Error('请先选择考试')
    return renameSessionById(selectedSessionId.value, name, renamer, loader)
  }

  async function saveSelectedMetadata(
    name: string,
    curriculumVolumeId: string | null,
    updater: SessionMetadataUpdater = updateSessionMetadata,
    loader: SessionLoader = fetchSessions,
  ): Promise<SessionSummary> {
    if (selectedSessionId.value === null) throw new Error('请先选择考试')
    const sessionId = selectedSessionId.value
    const normalized = name.trim()
    if (!normalized) throw new Error('考试名称不能为空')

    let updated: SessionSummary
    try {
      updated = await updater(sessionId, {
        name: normalized,
        curriculum_volume_id: curriculumVolumeId,
      })
    } catch (error) {
      if (!isAmbiguousWriteError(error)) throw error
      const loaded = await loader()
      sessions.value = loaded
      loadState.value = 'ready'
      const confirmed = loaded.find((session) => (
        session.id === sessionId
        && session.name.trim() === normalized
        && session.curriculum_volume_id === curriculumVolumeId
        && !session.is_deleted
      ))
      if (!confirmed) throw error
      updated = confirmed
    }

    const index = sessions.value.findIndex((session) => session.id === updated.id)
    if (index >= 0) sessions.value[index] = updated
    return updated
  }

  function selectSession(id: number | null): void {
    if (id === null) {
      clearSelection()
      return
    }
    if (!sessions.value.some((session) => session.id === id)) {
      throw new Error('所选考试不在当前列表中')
    }

    selectedSessionId.value = id
    persistedCandidateId = id
    localStorage.setItem(SESSION_STORAGE_KEY, String(id))
  }

  function clearSelection(): void {
    selectedSessionId.value = null
    persistedCandidateId = null
    localStorage.removeItem(SESSION_STORAGE_KEY)
  }

  return {
    sessions,
    selectedSessionId,
    currentSession,
    loadState,
    errorMessage,
    initialize,
    createDraft,
    renameSelected,
    renameSessionById,
    saveSelectedMetadata,
    selectSession,
    clearSelection,
  }
})
