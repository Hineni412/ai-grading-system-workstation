<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppNavigation from '../components/shell/AppNavigation.vue'
import AppTopbar from '../components/shell/AppTopbar.vue'
import SessionInspector from '../components/shell/SessionInspector.vue'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const sessionStore = useSessionStore()
const navigationOpen = ref(true)
const inspectorOpen = ref(true)
const overlayMode = ref(false)
let mediaQuery: MediaQueryList | null = null
let returnFocusTo: HTMLElement | null = null

function applyMediaMode(event: MediaQueryList | MediaQueryListEvent): void {
  overlayMode.value = event.matches
  navigationOpen.value = !event.matches
  inspectorOpen.value = !event.matches
}

function rememberTrigger(): void {
  returnFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
}

function openNavigation(): void {
  rememberTrigger()
  navigationOpen.value = true
  if (overlayMode.value) inspectorOpen.value = false
}

function openInspector(): void {
  rememberTrigger()
  inspectorOpen.value = true
  if (overlayMode.value) navigationOpen.value = false
}

function toggleNavigation(): void {
  if (navigationOpen.value) navigationOpen.value = false
  else openNavigation()
}

function toggleInspector(): void {
  if (inspectorOpen.value) inspectorOpen.value = false
  else openInspector()
}

async function closeOverlay(): Promise<void> {
  if (!overlayMode.value || (!navigationOpen.value && !inspectorOpen.value)) return
  navigationOpen.value = false
  inspectorOpen.value = false
  const target = returnFocusTo
  returnFocusTo = null
  await nextTick()
  target?.focus()
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') void closeOverlay()
}

watch(
  () => route.fullPath,
  async () => {
    await nextTick()
    document.querySelector<HTMLElement>('#main-workspace h1')?.focus()
  },
)

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 1023px)')
  applyMediaMode(mediaQuery)
  mediaQuery.addEventListener('change', applyMediaMode)
  window.addEventListener('keydown', handleKeydown)
  void sessionStore.initialize()
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', applyMediaMode)
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div
    class="app-shell"
    data-testid="app-shell"
    :data-overlay="overlayMode ? 'true' : 'false'"
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
    <SessionInspector @retry="sessionStore.initialize" />
    <button
      v-if="overlayMode && (navigationOpen || inspectorOpen)"
      class="app-shell__backdrop"
      data-testid="shell-backdrop"
      type="button"
      aria-label="关闭当前面板"
      @click="closeOverlay"
    />
  </div>
</template>
