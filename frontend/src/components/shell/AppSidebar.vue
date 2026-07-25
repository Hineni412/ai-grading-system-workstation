<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  navigationGroups,
  settingsNavigationItems,
  workbenchRouteDefinition,
  type WorkspaceRouteDefinition,
} from '../../navigation'
import AppIcon from './AppIcon.vue'

defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  navigate: []
}>()

const route = useRoute()
const settingsOpen = ref(false)

function isActive(item: WorkspaceRouteDefinition): boolean {
  return route.path === item.path || route.path.startsWith(`${item.path}/`)
}

const settingsActive = computed(() => (
  settingsNavigationItems.some((item) => isActive(item))
))

watch(
  settingsActive,
  (active) => {
    if (active) settingsOpen.value = true
  },
  { immediate: true },
)

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
          :to="item.path"
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
        v-show="settingsOpen"
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
      <button
        type="button"
        class="app-sidebar__settings-toggle"
        :class="{ 'is-active': settingsActive }"
        aria-controls="settings-navigation"
        :aria-expanded="settingsOpen"
        @click="settingsOpen = !settingsOpen"
      >
        <AppIcon name="settings" />
        <span>设置</span>
        <AppIcon
          class="app-sidebar__settings-chevron"
          :class="{ 'is-open': settingsOpen }"
          name="chevron-down"
          :size="16"
        />
      </button>
    </div>
  </aside>
</template>
