<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppSidebar from '../components/shell/AppSidebar.vue'
import AppTopbar from '../components/shell/AppTopbar.vue'
import { useWorkspaceAITaskStore } from '../workspaces/shared/ai-tasks/store'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useJobStore } from '../stores/jobs'

const route = useRoute()
const sessionStore = useSessionStore()
const draftStore = useReviewDraftStore()
const configStore = useConfigWorkspaceStore()
const curriculumScope = useCurriculumScopeStore()
const workspaceAITasks = useWorkspaceAITaskStore()
const jobs = useJobStore()
const hydratingWorkspace = ref(false)
const navigationOpen = ref(false)
const narrowNavigation = ref(false)
let navigationMediaQuery: MediaQueryList | null = null
const sidebarOpen = computed(() => !narrowNavigation.value || navigationOpen.value)

function syncNavigationMode(
  mediaQuery: MediaQueryList | MediaQueryListEvent,
): void {
  narrowNavigation.value = mediaQuery.matches
  if (!mediaQuery.matches) navigationOpen.value = false
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!draftStore.hasDirtyDrafts && !configStore.hasDirtyEditor) return
  event.preventDefault()
  event.returnValue = ''
}

watch(
  () => route.fullPath,
  async () => {
    navigationOpen.value = false
    await nextTick()
    document.querySelector<HTMLElement>('#main-workspace h1')?.focus()
  },
)

onMounted(() => {
  navigationMediaQuery = window.matchMedia('(max-width: 900px)')
  syncNavigationMode(navigationMediaQuery)
  navigationMediaQuery.addEventListener('change', syncNavigationMode)
  window.addEventListener('beforeunload', onBeforeUnload)
  void workspaceAITasks.initialize()
  void jobs.initialize()
  hydratingWorkspace.value = true
  void sessionStore.initialize().then(async () => {
    if (sessionStore.loadState !== 'ready') return
    await configStore.hydrateSafeIndex(
      sessionStore.sessions.map(({ id }) => id),
      sessionStore.selectedSessionId,
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
  }).finally(() => {
    hydratingWorkspace.value = false
  })
})

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    if (hydratingWorkspace.value) return
    const previous = configStore.sessionId
    if (configStore.selectSession(sessionId)) return
    if (previous === null) sessionStore.clearSelection()
    else sessionStore.selectSession(previous)
  },
)

onBeforeUnmount(() => {
  navigationMediaQuery?.removeEventListener('change', syncNavigationMode)
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'is-navigation-open': navigationOpen }"
    data-testid="app-shell"
  >
    <AppSidebar :open="sidebarOpen" @navigate="navigationOpen = false" />
    <AppTopbar
      :navigation-open="navigationOpen"
      @toggle-navigation="navigationOpen = !navigationOpen"
    />
    <main id="main-workspace" class="main-workspace" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>
