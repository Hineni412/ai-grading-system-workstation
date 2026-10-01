<script setup lang="ts">
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, ref, watch } from 'vue'

import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { SessionDraftOutcomeUnknownError, useSessionStore } from '../../stores/session'
import { ApiError } from '../../api/errors'

const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const name = ref('')
const curriculumVolumeId = ref<string | null>(null)
const busy = ref(false)
const message = ref('')
const volumeManuallyChanged = ref(false)

watch(
  () => sessionStore.currentSession,
  (value) => {
    volumeManuallyChanged.value = false
    name.value = value?.name ?? ''
    curriculumVolumeId.value = value?.curriculum_volume_id
      ?? curriculumScope.selectedVolumeId
  },
  { immediate: true },
)

// 未在本面板手动改过、且当前考试没有自己保存的学期时，跟随顶部全局教学学期。
watch(
  () => curriculumScope.selectedVolumeId,
  (volumeId) => {
    if (volumeManuallyChanged.value) return
    if (sessionStore.currentSession?.curriculum_volume_id) return
    curriculumVolumeId.value = volumeId
  },
)

const curriculumOptions = computed(() => curriculumScope.volumes)

const isDirty = computed(() => {
  const current = sessionStore.currentSession
  if (!current) return true
  return name.value !== current.name
    || curriculumVolumeId.value !== current.curriculum_volume_id
})

function writeErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.code === 'session_name_conflict') {
    return '已存在同名考试，请换一个名称。'
  }
  return fallback
}

async function createDraft(): Promise<void> {
  busy.value = true
  message.value = ''
  try {
    await sessionStore.createDraft(
      name.value,
      undefined,
      undefined,
      curriculumVolumeId.value,
    )
    message.value = '考试草稿已创建。'
  } catch (error) {
    message.value = error instanceof SessionDraftOutcomeUnknownError
      ? '创建结果未知，暂时无法核对考试列表。为避免重复创建，请恢复连接后刷新页面确认。'
      : writeErrorMessage(error, '服务器未创建考试草稿，请检查名称后重试。')
  } finally {
    busy.value = false
  }
}

async function renameDraft(): Promise<void> {
  busy.value = true
  message.value = ''
  try {
    const renamed = await sessionStore.saveSelectedMetadata(
      name.value,
      curriculumVolumeId.value,
    )
    name.value = renamed.name
    message.value = `考试信息已保存为“${renamed.name}”。`
  } catch (error) {
    message.value = writeErrorMessage(error, '考试名称未更新，请重试。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section
    id="session-draft-title"
    class="session-draft-panel"
    tabindex="-1"
    aria-label="考试草稿"
  >
    <form
      class="session-draft-panel__form"
      @submit.prevent="sessionStore.currentSession ? renameDraft() : createDraft()"
    >
      <label class="sr-only" for="session-draft-name">考试名称</label>
      <input class="app-input"
        id="session-draft-name"
        v-model="name"
        :disabled="busy"
        required
        maxlength="200"
        placeholder="考试名称"
        autocomplete="off"
      >
      <label class="sr-only" for="session-draft-curriculum">所属教学学期</label>
      <select class="app-input"
        id="session-draft-curriculum"
        v-model="curriculumVolumeId"
        :disabled="busy || curriculumScope.loadState === 'loading'"
        @change="volumeManuallyChanged = true"
      >
        <option :value="null">暂不归类</option>
        <option v-for="volume in curriculumOptions" :key="volume.id" :value="volume.id">
          {{ volume.label }}
        </option>
      </select>
      <AppButton variant="primary"
        v-if="isDirty"
        type="submit"
        :disabled="busy || !name.trim()"
      >
        {{ sessionStore.currentSession ? '保存' : '创建草稿' }}
      </AppButton>
      <p v-if="message" class="session-draft-panel__message" role="status">{{ message }}</p>
    </form>
  </section>
</template>
