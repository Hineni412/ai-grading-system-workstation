<script setup lang="ts">
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

watch(
  () => sessionStore.currentSession,
  (value) => {
    name.value = value?.name ?? ''
    curriculumVolumeId.value = value?.curriculum_volume_id
      ?? curriculumScope.selectedVolumeId
  },
  { immediate: true },
)

const curriculumOptions = computed(() => curriculumScope.volumes)

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
  <section class="session-draft-panel" aria-labelledby="session-draft-title">
    <div>
      <p class="session-draft-panel__eyebrow">第 1 阶段</p>
      <h2 id="session-draft-title" tabindex="-1">考试草稿</h2>
      <p>先保存考试名称，后续试卷来源与评分依据都会归入这场考试。</p>
    </div>
    <form @submit.prevent="sessionStore.currentSession ? renameDraft() : createDraft()">
      <label for="session-draft-name">考试名称</label>
      <div class="session-draft-panel__controls">
        <input id="session-draft-name" v-model="name" :disabled="busy" required maxlength="200">
        <button type="submit" :disabled="busy || !name.trim()">
          {{ sessionStore.currentSession ? '保存名称' : '创建考试草稿' }}
        </button>
      </div>
      <label for="session-draft-curriculum">所属教学学期</label>
      <select
        id="session-draft-curriculum"
        v-model="curriculumVolumeId"
        :disabled="busy || curriculumScope.loadState === 'loading'"
      >
        <option :value="null">暂不归类</option>
        <option v-for="volume in curriculumOptions" :key="volume.id" :value="volume.id">
          {{ volume.label }}
        </option>
      </select>
      <p v-if="message" role="status">{{ message }}</p>
    </form>
  </section>
</template>

<style scoped>
.session-draft-panel {
  display: grid;
  grid-template-columns: minmax(260px, .8fr) minmax(360px, 1.2fr);
  gap: var(--space-7);
  padding-block: var(--space-6);
  border-block-end: var(--border-width) solid var(--border);
}
.session-draft-panel h2,
.session-draft-panel p { margin: 0; }
.session-draft-panel h2 { margin-block-end: var(--space-2); font-size: var(--font-size-h2); }
.session-draft-panel p { color: var(--color-text-secondary); }
.session-draft-panel__eyebrow { margin-block-end: var(--space-1) !important; font-size: var(--font-size-caption); font-weight: var(--font-weight-semibold); }
.session-draft-panel form { display: grid; align-content: start; gap: var(--space-2); }
.session-draft-panel label { font-size: var(--font-size-dense); font-weight: var(--font-weight-medium); }
.session-draft-panel__controls { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); }
.session-draft-panel input,
.session-draft-panel select,
.session-draft-panel button { min-height: var(--control-height-large); border: var(--border-width) solid var(--border); border-radius: var(--radius-control); }
.session-draft-panel input,
.session-draft-panel select { min-width: 0; padding-inline: var(--space-3); background: var(--card); }
.session-draft-panel button { padding-inline: var(--space-4); border-color: var(--color-accent); background: var(--color-accent); color: var(--primary-foreground); font-weight: var(--font-weight-medium); cursor: pointer; }
.session-draft-panel button:hover:not(:disabled) { background: var(--color-accent-hover); }
.session-draft-panel button:disabled { cursor: not-allowed; opacity: var(--opacity-disabled); }
@media (max-width: 1100px) { .session-draft-panel { grid-template-columns: 1fr; gap: var(--space-4); } }
</style>
