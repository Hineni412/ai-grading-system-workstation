import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  copyModelTaskBindings,
  ModelProfileInputError,
  modelProfilesApi,
  type ModelProfile,
  type ModelProfilesState,
  type ModelProfileUpsertInput,
  type ModelTaskBindings,
} from '../api/model-profiles'

export type ModelProfilesApi = typeof modelProfilesApi
export type ModelProfilesLoadState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'
export type ModelProfilesOperationState =
  | 'idle'
  | 'saving'
  | 'activating'
  | 'deleting'

function friendlyModelProfileError(
  error: unknown,
  action: 'load' | 'save' | 'activate' | 'delete',
): string {
  if (error instanceof ModelProfileInputError) return error.message
  if (error instanceof ApiError) {
    if (error.kind === 'contract') {
      return '模型配置返回了无法安全读取的内容。系统没有显示密钥，也没有改变当前配置。'
    }
    if (error.kind === 'conflict') {
      return action === 'save'
        ? '已有同名配置，或配置刚被其他操作更新。请换一个名称或重新加载后再保存。'
        : '当前配置刚被其他操作更新，请重新加载后再切换。'
    }
    if (error.kind === 'not_found') {
      return '这项模型配置已经不存在，请重新加载配置列表。'
    }
    if (error.kind === 'validation') {
      return '有一项配置内容无法保存，请检查名称、地址和模型名称。'
    }
    if (error.kind === 'network' || error.kind === 'timeout') {
      if (action === 'load') {
        return '暂时无法读取本机模型配置，请确认应用仍在运行后重试。'
      }
      return action === 'save'
        ? '保存结果暂时无法确认。页面没有调用模型，也不会产生费用；请重新加载后核对。'
        : action === 'delete'
          ? '删除结果暂时无法确认。请重新加载配置列表后核对。'
          : '切换结果暂时无法确认。页面没有调用模型；请重新加载后核对当前标记。'
    }
  }
  if (action === 'load') {
    return '本机模型配置暂时无法读取，请稍后重新加载。'
  }
  return action === 'save'
    ? '配置没有保存完成，原配置保持不变。'
    : action === 'delete'
      ? '配置没有删除完成，请重新加载后核对。'
      : '当前配置没有切换完成，请稍后重试。'
}

export const useModelProfilesStore = defineStore('model-profiles', () => {
  const profiles = ref<ModelProfile[]>([])
  const activeProfileName = ref<string | null>(null)
  const selectedProfileName = ref<string | null>(null)
  const loadState = ref<ModelProfilesLoadState>('idle')
  const operationState = ref<ModelProfilesOperationState>('idle')
  const errorMessage = ref('')
  const noticeMessage = ref('')
  const taskBindings = ref<ModelTaskBindings>({
    content_generation: { profile_name: null, model: '' },
    grading: { profile_name: null, model: '' },
  })

  let loadGeneration = 0
  let loadController: AbortController | null = null

  const selectedProfile = computed<ModelProfile | null>(() => (
    profiles.value.find(({ name }) => name === selectedProfileName.value) ?? null
  ))

  const activeProfile = computed<ModelProfile | null>(() => (
    profiles.value.find(({ name }) => name === activeProfileName.value) ?? null
  ))

  function applyState(
    state: ModelProfilesState,
    preferredSelection?: string | null,
  ): void {
    profiles.value = state.profiles.map((profile) => ({ ...profile }))
    activeProfileName.value = state.active_profile_name
    taskBindings.value = copyModelTaskBindings(state.task_bindings)
    const candidate = preferredSelection === undefined
      ? selectedProfileName.value
      : preferredSelection
    selectedProfileName.value = (
      candidate !== null
      && profiles.value.some(({ name }) => name === candidate)
    )
      ? candidate
      : (
          activeProfileName.value
          ?? profiles.value[0]?.name
          ?? null
        )
    loadState.value = profiles.value.length === 0 ? 'empty' : 'ready'
  }

  async function load(api: ModelProfilesApi = modelProfilesApi): Promise<boolean> {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    const generation = ++loadGeneration
    loadState.value = 'loading'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const state = await api.getState(controller.signal)
      if (generation !== loadGeneration || controller.signal.aborted) return false
      applyState(state)
      return true
    } catch (error) {
      if (generation !== loadGeneration || controller.signal.aborted) return false
      loadState.value = 'error'
      errorMessage.value = friendlyModelProfileError(error, 'load')
      return false
    } finally {
      if (loadController === controller) loadController = null
    }
  }

  function selectProfile(name: string | null): void {
    if (operationState.value !== 'idle') return
    if (
      name !== null
      && !profiles.value.some((profile) => profile.name === name)
    ) return
    selectedProfileName.value = name
    errorMessage.value = ''
    noticeMessage.value = ''
  }

  async function saveProfile(
    sourceName: string | null,
    input: ModelProfileUpsertInput,
    api: ModelProfilesApi = modelProfilesApi,
  ): Promise<ModelProfile | null> {
    if (operationState.value !== 'idle') return null
    operationState.value = 'saving'
    errorMessage.value = ''
    noticeMessage.value = ''
    const savedName = input.name.trim()
    try {
      const state = await api.saveProfile(sourceName ?? savedName, input, {
        requireApiKey: sourceName === null,
      })
      applyState(state, savedName)
      const saved = profiles.value.find(({ name }) => name === savedName) ?? null
      if (!saved) {
        errorMessage.value = '服务器已响应，但没有返回刚保存的配置。请重新加载后核对。'
        return null
      }
      noticeMessage.value = '模型配置已保存。保存过程没有调用模型，也不会产生费用。'
      return saved
    } catch (error) {
      errorMessage.value = friendlyModelProfileError(error, 'save')
      return null
    } finally {
      operationState.value = 'idle'
    }
  }

  async function activateProfile(
    name: string,
    api: ModelProfilesApi = modelProfilesApi,
  ): Promise<boolean> {
    if (
      operationState.value !== 'idle'
      || !profiles.value.some((profile) => profile.name === name)
    ) return false
    operationState.value = 'activating'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const state = await api.activateProfile(name)
      applyState(state, name)
      noticeMessage.value = `已将“${name}”设为当前配置。切换本身不会调用模型，也不会产生费用。`
      return true
    } catch (error) {
      errorMessage.value = friendlyModelProfileError(error, 'activate')
      return false
    } finally {
      operationState.value = 'idle'
    }
  }

  async function deleteProfile(
    name: string,
    api: ModelProfilesApi = modelProfilesApi,
  ): Promise<boolean> {
    if (
      operationState.value !== 'idle'
      || !profiles.value.some((profile) => profile.name === name)
    ) return false
    operationState.value = 'deleting'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      const state = await api.deleteProfile(name)
      applyState(state)
      noticeMessage.value = `已删除“${name}”。这项操作没有调用模型，也不会产生费用。`
      return true
    } catch (error) {
      errorMessage.value = friendlyModelProfileError(error, 'delete')
      return false
    } finally {
      operationState.value = 'idle'
    }
  }

  async function saveTaskBindings(
    bindings: ModelTaskBindings,
    api: ModelProfilesApi = modelProfilesApi,
  ): Promise<boolean> {
    if (operationState.value !== 'idle') return false
    operationState.value = 'saving'
    errorMessage.value = ''
    noticeMessage.value = ''
    try {
      applyState(await api.saveTaskBindings(copyModelTaskBindings(bindings)))
      noticeMessage.value = '工作模型已保存；保存过程不会调用 AI。'
      return true
    } catch (error) {
      errorMessage.value = friendlyModelProfileError(error, 'save')
      return false
    } finally {
      operationState.value = 'idle'
    }
  }

  function clearMessages(): void {
    errorMessage.value = ''
    noticeMessage.value = ''
  }

  return {
    profiles,
    activeProfileName,
    activeProfile,
    selectedProfileName,
    selectedProfile,
    loadState,
    operationState,
    errorMessage,
    noticeMessage,
    taskBindings,
    load,
    selectProfile,
    saveProfile,
    activateProfile,
    deleteProfile,
    saveTaskBindings,
    clearMessages,
  }
})
