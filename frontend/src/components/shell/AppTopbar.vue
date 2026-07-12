<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useSessionStore } from '../../stores/session'

defineProps<{
  navigationOpen: boolean
  inspectorOpen: boolean
}>()

defineEmits<{
  toggleNavigation: []
  toggleInspector: []
}>()

const route = useRoute()
const sessionStore = useSessionStore()
const breadcrumb = computed(() => String(route.meta.breadcrumb ?? route.meta.title ?? ''))
const pageTitle = computed(() => String(route.meta.title ?? ''))

function selectSession(event: Event): void {
  const value = (event.currentTarget as HTMLSelectElement).value
  sessionStore.selectSession(value === '' ? null : Number(value))
}
</script>

<template>
  <header class="app-topbar" data-testid="app-topbar">
    <a class="app-topbar__skip-link" href="#main-workspace">跳到主要工作区</a>
    <button
      type="button"
      data-testid="navigation-toggle"
      aria-controls="app-navigation"
      :aria-expanded="navigationOpen"
      aria-label="切换应用导航"
      @click="$emit('toggleNavigation')"
    >
      <span aria-hidden="true">导</span>
    </button>

    <div class="app-topbar__route">
      <span>{{ breadcrumb }}</span>
      <strong>{{ pageTitle }}</strong>
    </div>

    <div class="app-topbar__session">
      <label for="current-session">当前考试</label>
      <select
        id="current-session"
        :value="sessionStore.selectedSessionId ?? ''"
        :disabled="sessionStore.loadState === 'loading'"
        @change="selectSession"
      >
        <option value="">未选择</option>
        <option v-for="session in sessionStore.sessions" :key="session.id" :value="session.id">
          {{ session.name }}
        </option>
      </select>
    </div>

    <button
      type="button"
      data-testid="inspector-toggle"
      aria-controls="session-inspector"
      :aria-expanded="inspectorOpen"
      aria-label="切换考试检查器"
      @click="$emit('toggleInspector')"
    >
      <span aria-hidden="true">考</span>
    </button>
  </header>
</template>
