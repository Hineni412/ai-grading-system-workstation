<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppNavigation from '../components/shell/AppNavigation.vue'
import AppTopbar from '../components/shell/AppTopbar.vue'
import SessionInspector from '../components/shell/SessionInspector.vue'
import ReviewScoringInspector from '../components/review/ReviewScoringInspector.vue'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const navigationOpen = ref(true)
const inspectorOpen = ref(true)
let compactMediaQuery: MediaQueryList | null = null

function applyDesktopMode(): void {
  navigationOpen.value = !(compactMediaQuery?.matches ?? false)
}

function toggleNavigation(): void {
  navigationOpen.value = !navigationOpen.value
}

function toggleInspector(): void {
  inspectorOpen.value = !inspectorOpen.value
}

watch(
  () => route.fullPath,
  async () => {
    await nextTick()
    document.querySelector<HTMLElement>('#main-workspace h1')?.focus()
  },
)

onMounted(() => {
  compactMediaQuery = window.matchMedia('(max-width: 1279px)')
  applyDesktopMode()
  compactMediaQuery.addEventListener('change', applyDesktopMode)
  void sessionStore.initialize()
})

onBeforeUnmount(() => {
  compactMediaQuery?.removeEventListener('change', applyDesktopMode)
})
</script>

<template>
  <div
    class="app-shell"
    data-testid="app-shell"
    :data-navigation-open="navigationOpen ? 'true' : 'false'"
    :data-inspector-open="inspectorOpen ? 'true' : 'false'"
  >
    <AppTopbar
      :navigation-open="navigationOpen"
      :inspector-open="inspectorOpen"
      @toggle-navigation="toggleNavigation"
      @toggle-inspector="toggleInspector"
    />
    <AppNavigation :collapsed="!navigationOpen" />
    <main id="main-workspace" class="main-workspace" tabindex="-1">
      <RouterView />
    </main>
    <ReviewScoringInspector v-if="route.name === 'grading'" />
    <SessionInspector v-else @retry="sessionStore.initialize" />
  </div>
</template>
