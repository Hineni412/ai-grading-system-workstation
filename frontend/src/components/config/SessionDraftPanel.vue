<script setup lang="ts">
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, ref, watch } from 'vue'

import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { SessionDraftOutcomeUnknownError, useSessionStore } from '../../stores/session'
import { ApiError } from '../../api/errors'
import type { SessionKind } from '../../api/sessions'

const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const name = ref('')
const curriculumVolumeId = ref<string | null>(null)
const kind = defineModel<SessionKind>('kind', { default: 'scan' })
const examDate = ref('')
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
    if (value) kind.value = 'scan'
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
    await sessionStore.createDraft(name.value, {
      curriculumVolumeId: curriculumVolumeId.value,
      kind: kind.value,
      examDate: kind.value === 'zhixue_import' ? examDate.value : null,
    })
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
      <template v-if="!sessionStore.currentSession">
        <label class="sr-only" for="session-draft-kind">考试方式</label>
        <select class="app-input"
          id="session-draft-kind"
          v-model="kind"
          :disabled="busy"
        >
          <option value="scan">扫描批改（周测等）</option>
          <option value="zhixue_import">智学网成绩导入</option>
        </select>
        <p v-if="kind === 'zhixue_import'" class="session-draft-panel__hint">
          导入智学网导出的成绩表（月考、期中、期末等已批改完的考试），不需要扫描答卷。
        </p>
        <template v-if="kind === 'zhixue_import'">
          <label class="sr-only" for="session-draft-exam-date">考试日期</label>
          <input class="app-input"
            id="session-draft-exam-date"
            v-model="examDate"
            type="date"
            required
            :disabled="busy"
          >
        </template>
      </template>
      <AppButton variant="primary"
        v-if="isDirty"
        type="submit"
        :disabled="busy || !name.trim() || (kind === 'zhixue_import' && !examDate)"
      >
        {{ sessionStore.currentSession ? '保存' : '创建草稿' }}
      </AppButton>
      <FeedbackBanner v-if="message" role="status" tone="info" :description="message" />
    </form>
  </section>
</template>
