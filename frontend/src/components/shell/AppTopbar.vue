<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppButton from '../design-system/AppButton.vue'
import AppIconButton from '../design-system/AppIconButton.vue'
import SessionManagementDrawer from '../sessions/SessionManagementDrawer.vue'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useSessionStore } from '../../stores/session'

const props = defineProps<{
  navigationOpen: boolean
}>()

const emit = defineEmits<{
  toggleNavigation: []
}>()

const route = useRoute()
const sessionStore = useSessionStore()
const configStore = useConfigWorkspaceStore()
const pageTitle = computed(() => String(route.meta.title ?? '工作台'))
const pageDescription = computed(() => String(route.meta.description ?? ''))
const showCurrentExamContext = computed(
  () => route.meta.topbarContext !== 'workspace',
)

function selectSession(event: Event): void {
  const selector = event.currentTarget as HTMLSelectElement
  const nextSessionId = selector.value === '' ? null : Number(selector.value)
  const changesSession = nextSessionId !== configStore.sessionId
  const restoreSelection = () => {
    selector.value = sessionStore.selectedSessionId === null
      ? ''
      : String(sessionStore.selectedSessionId)
  }
  if (changesSession && configStore.hasPendingSubmission) {
    window.alert('上一次上传或生成的结果仍在核对。为避免重复处理，请先回到考试配置完成核对，再切换考试。')
    restoreSelection()
    return
  }
  if (changesSession && configStore.hasDirtyEditor) {
    const discard = window.confirm('当前评分依据有未保存修改。切换考试会丢弃这些修改，是否继续？')
    if (!discard) {
      restoreSelection()
      return
    }
    configStore.discardEditorDraft()
  }
  if (!configStore.selectSession(nextSessionId)) return
  sessionStore.selectSession(nextSessionId)
  if (nextSessionId !== null) void configStore.loadSelectedSessionWorkspace(nextSessionId)
}

function retrySessions(): void {
  void sessionStore.initialize()
}
</script>

<template>
  <header
    class="app-topbar"
    :class="{ 'app-topbar--workspace-context': !showCurrentExamContext }"
    data-testid="app-topbar"
  >
    <a class="app-topbar__skip-link" href="#main-workspace">跳到主要工作区</a>

    <div class="app-topbar__page">
      <AppIconButton
        class="app-topbar__navigation-toggle"
        :label="props.navigationOpen ? '收起导航' : '展开导航'"
        :icon="props.navigationOpen ? 'close' : 'menu'"
        variant="secondary"
        :aria-expanded="props.navigationOpen"
        aria-controls="application-sidebar"
        @click="emit('toggleNavigation')"
      />
      <div class="app-topbar__page-copy">
        <strong>{{ pageTitle }}</strong>
        <span v-if="pageDescription">{{ pageDescription }}</span>
      </div>
    </div>

    <div v-if="showCurrentExamContext" class="app-topbar__session">
      <label for="current-session">当前考试</label>
      <select
        id="current-session"
        :value="sessionStore.selectedSessionId ?? ''"
        :disabled="sessionStore.loadState === 'loading' || sessionStore.loadState === 'error'"
        @change="selectSession"
      >
        <option value="">未选择</option>
        <option v-for="session in sessionStore.sessions" :key="session.id" :value="session.id">
          {{ session.name }}
        </option>
      </select>
      <SessionManagementDrawer />
    </div>

    <div
      v-if="showCurrentExamContext && sessionStore.loadState === 'loading'"
      class="app-topbar__status"
      role="status"
    >
      正在读取考试列表
    </div>
    <div
      v-else-if="showCurrentExamContext && sessionStore.loadState === 'error'"
      class="app-topbar__status"
      role="alert"
    >
      <span>考试列表加载失败。</span>
      <AppButton variant="secondary" @click="retrySessions">重新加载</AppButton>
    </div>
  </header>
</template>
