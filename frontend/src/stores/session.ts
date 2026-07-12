import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchSessions, type SessionLoader, type SessionSummary } from '../api/sessions'

export const SESSION_STORAGE_KEY = 'ai-grading:selected-session:v1'
export type SessionLoadState = 'idle' | 'loading' | 'ready' | 'error'

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

  const currentSession = computed(
    () =>
      sessions.value.find((session) => session.id === selectedSessionId.value) ?? null,
  )

  async function initialize(loader: SessionLoader = fetchSessions): Promise<void> {
    persistedCandidateId = readPersistedId()
    selectedSessionId.value = null
    loadState.value = 'loading'
    errorMessage.value = ''

    try {
      const loadedSessions = await loader()
      sessions.value = loadedSessions

      if (
        persistedCandidateId !== null &&
        loadedSessions.some((session) => session.id === persistedCandidateId)
      ) {
        selectedSessionId.value = persistedCandidateId
      } else {
        persistedCandidateId = null
        localStorage.removeItem(SESSION_STORAGE_KEY)
      }

      loadState.value = 'ready'
    } catch {
      sessions.value = []
      loadState.value = 'error'
      errorMessage.value = '考试列表暂时无法读取。已保存的选择没有丢失，可以重新加载。'
    }
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
    selectSession,
    clearSelection,
  }
})
