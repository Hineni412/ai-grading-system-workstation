<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppTopbar from '../components/shell/AppTopbar.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const draftStore = useReviewDraftStore()
const configStore = useConfigWorkspaceStore()

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
  void sessionStore.initialize().then(() => {
    configStore.restoreSafeIndex(sessionStore.sessions.map(({ id }) => id))
    if (configStore.sessionId !== sessionStore.selectedSessionId) {
      configStore.selectSession(sessionStore.selectedSessionId)
    }
  })
})

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => configStore.selectSession(sessionId),
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
