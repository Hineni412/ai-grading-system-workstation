import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'

import {
  questionBankApi,
  type CurriculumCatalog,
  type CurriculumVolume,
} from '../api/question-bank'

export const CURRICULUM_SCOPE_STORAGE_KEY = 'ai-grading:curriculum-scope:v1'
export type CurriculumScopeLoadState = 'idle' | 'loading' | 'ready' | 'error'

function readPersistedVolumeId(): string | null {
  const value = globalThis.localStorage?.getItem(CURRICULUM_SCOPE_STORAGE_KEY)?.trim()
  return value || null
}

export const useCurriculumScopeStore = defineStore('curriculum-scope', () => {
  const catalog = shallowRef<CurriculumCatalog | null>(null)
  const volumes = ref<CurriculumVolume[]>([])
  const selectedVolumeId = ref<string | null>(null)
  const loadState = ref<CurriculumScopeLoadState>('idle')
  const errorMessage = ref('')
  let initializeRequest: Promise<void> | null = null

  const selectedVolume = computed(() => volumes.value.find(
    volume => volume.id === selectedVolumeId.value,
  ) ?? null)

  function applySelection(volumeId: string | null): void {
    selectedVolumeId.value = volumeId
    if (volumeId === null) {
      globalThis.localStorage?.removeItem(CURRICULUM_SCOPE_STORAGE_KEY)
    } else {
      globalThis.localStorage?.setItem(CURRICULUM_SCOPE_STORAGE_KEY, volumeId)
    }
  }

  async function initialize(
    loader: () => Promise<CurriculumCatalog> = () => questionBankApi.getCurriculum(undefined, false),
  ): Promise<void> {
    if (initializeRequest) return initializeRequest
    if (loadState.value === 'ready') return
    const request = (async () => {
      loadState.value = 'loading'
      errorMessage.value = ''
      const persisted = readPersistedVolumeId()
      try {
        const loaded = await loader()
        catalog.value = loaded
        volumes.value = [...loaded.volumes].sort((left, right) => left.order - right.order)
        if (persisted && volumes.value.some(volume => volume.id === persisted)) {
          selectedVolumeId.value = persisted
        } else {
          selectedVolumeId.value = null
          if (persisted) globalThis.localStorage?.removeItem(CURRICULUM_SCOPE_STORAGE_KEY)
        }
        loadState.value = 'ready'
      } catch {
        catalog.value = null
        loadState.value = 'error'
        errorMessage.value = '教学学期目录暂时无法读取，已暂停学期筛选。'
      }
    })().finally(() => {
      initializeRequest = null
    })
    initializeRequest = request
    return request
  }

  function selectVolume(volumeId: string | null): void {
    if (volumeId === null || volumeId === '') {
      applySelection(null)
      return
    }
    if (!volumes.value.some(volume => volume.id === volumeId)) {
      throw new Error('所选教学学期不在当前教材目录中')
    }
    applySelection(volumeId)
  }

  function clearSelection(): void {
    applySelection(null)
  }

  return {
    catalog,
    volumes,
    selectedVolumeId,
    selectedVolume,
    loadState,
    errorMessage,
    initialize,
    selectVolume,
    clearSelection,
  }
})
