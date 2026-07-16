<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppTopbar from '../components/shell/AppTopbar.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const draftStore = useReviewDraftStore()
const configStore = useConfigWorkspaceStore()
const hydratingWorkspace = ref(false)

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!draftStore.hasDirtyDrafts && !configStore.hasDirtyEditor) return
  event.preventDefault()
  event.returnValue = ''
}

watch(
  () => route.fullPath,
  async () => {
    await nextTick()
    document.querySelector<HTMLElement>('#main-workspace h1')?.focus()
  },
)

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
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
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div class="app-shell" data-testid="app-shell">
    <AppTopbar />
    <main id="main-workspace" class="main-workspace" tabindex="-1">
      <RouterView />
    </main>
  </div>
</template>
