<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppSidebar from '../components/shell/AppSidebar.vue'
import AppTopbar from '../components/shell/AppTopbar.vue'
import CommandPalette from '../components/shell/CommandPalette.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useJobStore } from '../stores/jobs'

const route = useRoute()
const questionWorkspace = computed(() => ['/question-bank', '/question-assembly'].includes(route.path))
const sessionStore = useSessionStore()
const draftStore = useReviewDraftStore()
const configStore = useConfigWorkspaceStore()
const curriculumScope = useCurriculumScopeStore()
const jobs = useJobStore()
const SIDEBAR_COLLAPSED_KEY = 'zhiheng.sidebar.collapsed'
const hydratingWorkspace = ref(false)
let hydratedConfigSession: number | null = null
let configHydration: Promise<void> | null = null

async function hydrateConfigWorkspace(): Promise<void> {
  const id = sessionStore.selectedSessionId
  if (route.path !== '/sessions' || sessionStore.loadState !== 'ready'
    || id === null || hydratedConfigSession === id) return
  if (configStore.sessionId === id && (configStore.editor || configStore.source)) {
    hydratedConfigSession = id
    return
  }
  if (configHydration) { await configHydration; return hydrateConfigWorkspace() }
  configHydration = configStore.hydrateSafeIndex(sessionStore.sessions.map(item => item.id), id)
  try {
    await configHydration
    if (sessionStore.selectedSessionId === id) hydratedConfigSession = id
  } finally { configHydration = null }
}
const navigationOpen = ref(false)
const narrowNavigation = ref(false)
const wideViewport = ref(false)
const paletteOpen = ref(false)
/* '1' = ≥1440px 时收起为图标轨；901–1439px 与 ≤900px 不受该偏好影响 */
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1')
let navigationMediaQuery: MediaQueryList | null = null
let wideMediaQuery: MediaQueryList | null = null
const sidebarOpen = computed(() => !narrowNavigation.value || navigationOpen.value)
const sidebarMode = computed<'expanded' | 'rail' | 'drawer'>(() => {
  if (narrowNavigation.value) return 'drawer'
  if ((wideViewport.value || questionWorkspace.value) && !sidebarCollapsed.value) return 'expanded'
  return 'rail'
})

/* 任务中心浮卡（已 Teleport 到 body）据此贴住侧栏右侧，避免遮挡工作区操作 */
const SIDEBAR_OFFSETS: Record<string, string> = { expanded: '180px', rail: '56px', drawer: '0px' }
watch(
  sidebarMode,
  (mode) => {
    document.documentElement.style.setProperty(
      '--shell-sidebar-offset',
      SIDEBAR_OFFSETS[mode] ?? '0px',
    )
  },
  { immediate: true },
)

function syncNavigationMode(
  mediaQuery: MediaQueryList | MediaQueryListEvent,
): void {
  narrowNavigation.value = mediaQuery.matches
  if (!mediaQuery.matches) navigationOpen.value = false
}

function syncWideViewport(
  mediaQuery: MediaQueryList | MediaQueryListEvent,
): void {
  wideViewport.value = mediaQuery.matches
}

function toggleSidebarCollapse(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed.value ? '1' : '0')
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!draftStore.hasDirtyDrafts && !configStore.hasDirtyEditor) return
  event.preventDefault()
  event.returnValue = ''
}

watch(
  () => route.fullPath,
  async (_next, previous) => {
    navigationOpen.value = false
    if (['/question-bank', '/question-assembly'].includes(route.path)
      && previous?.split(/[?#]/)[0] === route.path) return
    await nextTick()
    /* 页面过渡期间旧视图仍挂在 DOM（page-leave-active 隐藏待卸载），跳过它取新页标题 */
    const headings = document.querySelectorAll<HTMLElement>('#main-workspace h1')
    const target = [...headings].find(heading => heading.closest('.page-leave-active') === null)
    target?.focus()
  },
)

onMounted(() => {
  navigationMediaQuery = window.matchMedia('(max-width: 900px)')
  syncNavigationMode(navigationMediaQuery)
  navigationMediaQuery.addEventListener('change', syncNavigationMode)
  wideMediaQuery = window.matchMedia('(min-width: 1440px)')
  syncWideViewport(wideMediaQuery)
  wideMediaQuery.addEventListener('change', syncWideViewport)
  window.addEventListener('beforeunload', onBeforeUnload)
  void jobs.initialize()
  hydratingWorkspace.value = true
  void sessionStore.initialize().then(async () => {
    if (sessionStore.loadState !== 'ready') return
    await configStore.hydrateSafeIndex(
      sessionStore.sessions.map(({ id }) => id),
      sessionStore.selectedSessionId,
      {}, false,
    )
    if (configStore.sessionId === null && sessionStore.selectedSessionId !== null) {
      configStore.selectSession(sessionStore.selectedSessionId)
    }
    await curriculumScope.initialize()
    const currentSession = sessionStore.currentSession
    if (
      curriculumScope.selectedVolumeId
      && currentSession
      && currentSession.curriculum_volume_id !== curriculumScope.selectedVolumeId
      && !configStore.hasPendingSubmission
      && !configStore.hasDirtyEditor
      && configStore.selectSession(null)
    ) {
      sessionStore.clearSelection()
    }
    await hydrateConfigWorkspace()
  }).finally(() => {
    hydratingWorkspace.value = false
  })
})

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    if (hydratingWorkspace.value) return
    const previous = configStore.sessionId
    if (configStore.selectSession(sessionId)) {
      hydratedConfigSession = null
      void hydrateConfigWorkspace()
      return
    }
    if (previous === null) sessionStore.clearSelection()
    else sessionStore.selectSession(previous)
  },
)

watch(() => route.path, () => { void hydrateConfigWorkspace() })

onBeforeUnmount(() => {
  navigationMediaQuery?.removeEventListener('change', syncNavigationMode)
  wideMediaQuery?.removeEventListener('change', syncWideViewport)
  window.removeEventListener('beforeunload', onBeforeUnload)
  document.documentElement.style.removeProperty('--shell-sidebar-offset')
})
</script>

<template>
  <div
    class="app-shell"
    :class="[`app-shell--${sidebarMode}`, { 'is-navigation-open': navigationOpen, 'app-shell--question-workspace': questionWorkspace }]"
    data-testid="app-shell"
  >
    <AppSidebar
      :open="sidebarOpen"
      :mode="sidebarMode"
      :collapsible="wideViewport"
      @navigate="navigationOpen = false"
      @toggle-collapse="toggleSidebarCollapse"
      @open-palette="paletteOpen = true"
    />
    <AppTopbar
      :navigation-open="navigationOpen"
      @toggle-navigation="navigationOpen = !navigationOpen"
    />
    <main id="main-workspace" class="main-workspace" tabindex="-1">
      <RouterView v-slot="{ Component }">
        <Transition name="page">
          <component :is="Component" v-if="Component" />
        </Transition>
      </RouterView>
    </main>
    <CommandPalette v-model:open="paletteOpen" />
  </div>
</template>
