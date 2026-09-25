<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppButton from '../design-system/AppButton.vue'
import AppIconButton from '../design-system/AppIconButton.vue'
import SessionManagementDrawer from '../sessions/SessionManagementDrawer.vue'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useSessionStore } from '../../stores/session'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useSessionSwitch } from '../../composables/useSessionSwitch'
import { workspaceRegistry } from '../../workspaces/registry'
import type { WorkspaceSubNavigationItem } from '../../workspaces/contracts'
import WorkspaceAITaskDrawer from '../../workspaces/shared/ai-tasks/WorkspaceAITaskDrawer.vue'

const props = defineProps<{
  navigationOpen: boolean
}>()

const emit = defineEmits<{
  toggleNavigation: []
}>()

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const configStore = useConfigWorkspaceStore()
const showOtherSessions = ref(false)
const pageTitle = computed(() => String(route.meta.title ?? '工作台'))
const pageDescription = computed(() => String(route.meta.description ?? ''))
const knowledgeTraining = computed(() => ['knowledge-graph', 'training', 'student-evidence'].includes(String(route.name)))
const showCurrentExamContext = computed(
  () => !knowledgeTraining.value && route.meta.topbarContext !== 'workspace',
)
const currentWorkspace = computed(() => workspaceRegistry.modules.find(
  ({ manifest }) => route.path === manifest.routePrefix
    || route.path.startsWith(`${manifest.routePrefix}/`),
))
const showCurriculumScope = computed(() => (
  currentWorkspace.value?.manifest.curriculumScope
  ?? route.meta.curriculumScope !== false
))
const workspaceSubNavigation = computed(() => [
  ...(currentWorkspace.value?.manifest.subNavigation ?? []),
].sort((left, right) => left.order - right.order))
const scopedSessions = computed(() => {
  const selectedVolumeId = curriculumScope.selectedVolumeId
  if (!showCurriculumScope.value || !selectedVolumeId || showOtherSessions.value) {
    return sessionStore.sessions
  }
  return sessionStore.sessions.filter((session) => (
    session.curriculum_volume_id === selectedVolumeId
    || session.id === sessionStore.selectedSessionId
  ))
})
const otherSessionCount = computed(() => {
  const selectedVolumeId = curriculumScope.selectedVolumeId
  if (!selectedVolumeId) return 0
  return sessionStore.sessions.filter(
    session => session.curriculum_volume_id !== selectedVolumeId,
  ).length
})
const currentSessionOutsideScope = computed(() => Boolean(
  showCurriculumScope.value
  && curriculumScope.selectedVolumeId
  && sessionStore.currentSession
  && sessionStore.currentSession.curriculum_volume_id !== curriculumScope.selectedVolumeId,
))

function isCurrentSubNavigation(item: WorkspaceSubNavigationItem): boolean {
  return Object.entries(item.query).every(
    ([key, value]) => route.query[key] === value,
  )
}

/* 滑动 pill 指示条（Inspira Animated Tabs 的测量式实现）：
   量出当前 tab 按钮的位置后由 CSS 过渡滑动；jsdom 无布局（offsetWidth 为 0）
   时不渲染 pill，直接退化为无指示条终态。 */
const workspaceTabsRef = ref<HTMLElement | null>(null)
const workspaceTabsPill = ref<{ left: string; width: string } | null>(null)

async function updateWorkspaceTabsPill(): Promise<void> {
  await nextTick()
  const nav = workspaceTabsRef.value
  const current = nav?.querySelector<HTMLElement>('button[aria-current="page"]')
  if (!nav || !current || current.offsetWidth === 0) {
    workspaceTabsPill.value = null
    return
  }
  workspaceTabsPill.value = {
    left: `${current.offsetLeft}px`,
    width: `${current.offsetWidth}px`,
  }
}

watch(() => route.fullPath, () => { void updateWorkspaceTabsPill() })
watch(workspaceSubNavigation, () => { void updateWorkspaceTabsPill() })
onMounted(() => {
  void updateWorkspaceTabsPill()
  window.addEventListener('resize', updateWorkspaceTabsPill)
})
onUnmounted(() => {
  window.removeEventListener('resize', updateWorkspaceTabsPill)
})

async function openWorkspaceDestination(item: WorkspaceSubNavigationItem): Promise<void> {
  const current = currentWorkspace.value
  if (!current) return
  await router.push({
    path: current.manifest.routePrefix,
    query: { ...route.query, ...item.query },
  })
}

const { switchSession } = useSessionSwitch()

function selectSession(event: Event): void {
  const selector = event.currentTarget as HTMLSelectElement
  const nextSessionId = selector.value === '' ? null : Number(selector.value)
  if (!switchSession(nextSessionId)) {
    selector.value = sessionStore.selectedSessionId === null
      ? ''
      : String(sessionStore.selectedSessionId)
  }
}

function retrySessions(): void {
  void sessionStore.initialize()
}

function selectCurriculumVolume(event: Event): void {
  const selector = event.currentTarget as HTMLSelectElement
  const nextVolumeId = selector.value || null
  const currentSession = sessionStore.currentSession
  const changesCurrentExam = Boolean(
    nextVolumeId
    && currentSession
    && currentSession.curriculum_volume_id !== nextVolumeId,
  )
  const restoreScopeSelection = () => {
    selector.value = curriculumScope.selectedVolumeId ?? ''
  }
  if (changesCurrentExam && configStore.hasPendingSubmission) {
    window.alert('当前考试仍有上传或生成结果等待核对。请先完成核对，再切换教学学期。')
    restoreScopeSelection()
    return
  }
  if (changesCurrentExam && configStore.hasDirtyEditor) {
    const discard = window.confirm('当前评分依据有未保存修改。切换教学学期会收起当前考试并丢弃这些修改，是否继续？')
    if (!discard) {
      restoreScopeSelection()
      return
    }
    configStore.discardEditorDraft()
  }
  if (changesCurrentExam) {
    if (!configStore.selectSession(null, true)) {
      restoreScopeSelection()
      return
    }
    sessionStore.clearSelection()
  }
  curriculumScope.selectVolume(nextVolumeId)
}

watch(() => curriculumScope.selectedVolumeId, () => {
  showOtherSessions.value = false
})

watch(showCurriculumScope, (visible) => {
  if (visible) void curriculumScope.initialize()
}, { immediate: true })
</script>

<template>
  <header
    class="app-topbar"
    :class="{
      'app-topbar--workspace-context': !showCurrentExamContext,
      'app-topbar--curriculum-context': showCurriculumScope,
      'app-topbar--knowledge-training': knowledgeTraining,
    }"
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

    <div v-if="showCurriculumScope" class="app-topbar__curriculum">
      <label for="current-curriculum-volume">教学学期</label>
      <select
        id="current-curriculum-volume"
        :value="curriculumScope.selectedVolumeId ?? ''"
        :disabled="curriculumScope.loadState === 'loading' || curriculumScope.loadState === 'error'"
        @change="selectCurriculumVolume"
      >
        <option value="">{{ knowledgeTraining ? '请选择教学学期' : '未选择（显示全部）' }}</option>
        <option v-for="volume in curriculumScope.volumes" :key="volume.id" :value="volume.id">
          {{ volume.label }}
        </option>
      </select>
    </div>

    <div
      v-if="workspaceSubNavigation.length"
      id="workspace-topbar-tabs"
      class="app-topbar__workspace-navigation"
      :aria-label="`${currentWorkspace?.manifest.displayName ?? '工作台'}入口`"
    >
      <nav ref="workspaceTabsRef" class="tp-workspace-tabs" aria-label="工作台子导航">
        <span
          v-if="workspaceTabsPill"
          class="tp-workspace-tabs__pill"
          aria-hidden="true"
          :style="workspaceTabsPill"
        />
        <button
          v-for="item in workspaceSubNavigation"
          :key="item.destinationKey"
          type="button"
          :aria-current="isCurrentSubNavigation(item) ? 'page' : undefined"
          :class="{ 'is-current': isCurrentSubNavigation(item) }"
          @click="openWorkspaceDestination(item)"
        >
          {{ item.label }}
        </button>
      </nav>
    </div>

    <WorkspaceAITaskDrawer class="app-topbar__tasks" />

    <div v-if="showCurrentExamContext" class="app-topbar__session">
      <label for="current-session">当前考试</label>
      <select
        id="current-session"
        :value="sessionStore.selectedSessionId ?? ''"
        :disabled="sessionStore.loadState === 'loading' || sessionStore.loadState === 'error'"
        @change="selectSession"
      >
        <option value="">未选择</option>
        <option v-for="session in scopedSessions" :key="session.id" :value="session.id">
          {{ session.name }}
        </option>
      </select>
      <button
        v-if="curriculumScope.selectedVolumeId && otherSessionCount > 0"
        class="app-topbar__other-sessions"
        type="button"
        :aria-expanded="showOtherSessions"
        @click="showOtherSessions = !showOtherSessions"
      >
        {{ showOtherSessions ? '收起其他学期' : `其他学期 ${otherSessionCount}` }}
      </button>
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
      v-else-if="showCurrentExamContext && currentSessionOutsideScope"
      class="app-topbar__status"
      role="status"
    >
      当前考试不属于所选教学学期，已保留当前选择。
    </div>
    <div
      v-else-if="showCurrentExamContext && sessionStore.loadState === 'error'"
      class="app-topbar__status"
      role="alert"
    >
      <span>考试列表加载失败。</span>
      <AppButton variant="secondary" @click="retrySessions">重新加载</AppButton>
    </div>
    <div
      v-if="showCurriculumScope && curriculumScope.loadState === 'error'"
      class="app-topbar__curriculum-status"
      role="alert"
    >
      <span>{{ curriculumScope.errorMessage }}</span>
      <AppButton variant="secondary" @click="curriculumScope.initialize()">重新加载</AppButton>
    </div>
  </header>
</template>

<style scoped>
@media (max-width: 620px) {
  .app-topbar.app-topbar--knowledge-training {
    grid-template-areas: "page tasks" "curriculum curriculum" "curriculum-status curriculum-status";
    grid-template-columns: minmax(0, 1fr) auto;
  }
}
</style>
