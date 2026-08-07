<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'

import {
  gradingRunRouteDefinition,
  navigationGroups,
  settingsNavigationItems,
  workbenchRouteDefinition,
  type WorkspaceRouteDefinition,
} from '../../navigation'
import { useSessionStore } from '../../stores/session'
import AppIcon from './AppIcon.vue'

defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  navigate: []
}>()

const route = useRoute()
const sessionStore = useSessionStore()

function isActive(item: WorkspaceRouteDefinition): boolean {
  if (route.name === gradingRunRouteDefinition.id) return item.id === 'grading'
  if (item.id === 'knowledge-graph' && route.name === 'training') return true
  return route.path === item.path || route.path.startsWith(`${item.path}/`)
}

function navigationTarget(item: WorkspaceRouteDefinition): string {
  if (item.id === 'grading' && sessionStore.selectedSessionId !== null) {
    return `/sessions/${sessionStore.selectedSessionId}/grading-run`
  }
  return item.path
}

function completeNavigation(): void {
  emit('navigate')
}
</script>

<template>
  <aside
    id="application-sidebar"
    class="app-sidebar"
    :class="{ 'is-open': open }"
    :aria-hidden="open ? undefined : 'true'"
    :inert="open ? undefined : true"
    aria-label="应用导航"
  >
    <RouterLink class="app-sidebar__brand" to="/workbench" @click="completeNavigation">
      <span class="app-sidebar__brand-mark" aria-hidden="true">AI</span>
      <span class="app-sidebar__brand-copy">
        <strong>AI 阅卷系统</strong>
        <small>P3.5 协作验收</small>
      </span>
    </RouterLink>

    <nav class="app-sidebar__navigation" data-testid="app-navigation" aria-label="主要导航">
      <RouterLink
        class="app-sidebar__link app-sidebar__link--workbench"
        :to="workbenchRouteDefinition.path"
        :aria-current="isActive(workbenchRouteDefinition) ? 'page' : undefined"
        @click="completeNavigation"
      >
        <AppIcon :name="workbenchRouteDefinition.icon" />
        <span>{{ workbenchRouteDefinition.label }}</span>
      </RouterLink>

      <section
        v-for="group in navigationGroups"
        :key="group.id"
        class="app-sidebar__group"
        :aria-labelledby="`navigation-group-${group.id}`"
      >
        <p :id="`navigation-group-${group.id}`">{{ group.label }}</p>
        <RouterLink
          v-for="item in group.items"
          :key="item.id"
          class="app-sidebar__link"
          :to="navigationTarget(item)"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="completeNavigation"
        >
          <AppIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </section>
    </nav>

    <div class="app-sidebar__settings">
      <nav
        id="settings-navigation"
        class="app-sidebar__settings-links"
        aria-label="设置导航"
      >
        <RouterLink
          v-for="item in settingsNavigationItems"
          :key="item.id"
          class="app-sidebar__link"
          :to="item.path"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="completeNavigation"
        >
          <AppIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </div>
  </aside>
</template>
