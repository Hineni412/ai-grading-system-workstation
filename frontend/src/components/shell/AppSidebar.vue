<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { PanelLeftClose, PanelLeftOpen, Search } from '@lucide/vue'

import {
  gradingRunRouteDefinition,
  navigationGroups,
  settingsNavigationItems,
  type WorkspaceRouteDefinition,
} from '../../navigation'
import { useSessionStore } from '../../stores/session'
import AppIcon from './AppIcon.vue'
import ExamContextSwitcher from './ExamContextSwitcher.vue'
import WorkspaceAITaskDrawer from '../../workspaces/shared/ai-tasks/WorkspaceAITaskDrawer.vue'
import { resolveNavigationTarget } from './navigation-target'

type SidebarMode = 'expanded' | 'rail' | 'drawer'

const props = defineProps<{
  open: boolean
  mode: SidebarMode
  collapsible: boolean
}>()

const emit = defineEmits<{
  navigate: []
  toggleCollapse: []
  openPalette: []
}>()

const route = useRoute()
const sessionStore = useSessionStore()

function isActive(item: WorkspaceRouteDefinition): boolean {
  if (route.name === gradingRunRouteDefinition.id) return item.id === 'grading'
  if (
    item.id === 'knowledge-overview'
    && ['knowledge-graph', 'training', 'student-evidence'].includes(String(route.name))
  ) return true
  if (item.id === 'ai-trace') {
    return route.path === '/settings' && route.query.section === 'ai-trace'
  }
  if (item.id === 'settings') {
    return route.path === '/settings' && route.query.section !== 'ai-trace'
  }
  return route.path === item.path || route.path.startsWith(`${item.path}/`)
}

function completeNavigation(): void {
  emit('navigate')
}

/* 当前项滑动指示条：与顶栏 tp-workspace-tabs__pill 同法，
   量出 aria-current 链接的 offsetTop/offsetHeight 后由 CSS 过渡滑动；
   jsdom 无布局（offsetHeight 为 0）时不渲染，链接保留自身激活底色。 */
const navigationRef = ref<HTMLElement | null>(null)
const settingsNavRef = ref<HTMLElement | null>(null)
const navigationIndicator = ref<{ top: string; height: string } | null>(null)
const settingsIndicator = ref<{ top: string; height: string } | null>(null)

function measureIndicator(
  container: HTMLElement | null,
): { top: string; height: string } | null {
  const current = container?.querySelector<HTMLElement>('a[aria-current="page"]')
  if (!container || !current || current.offsetHeight === 0) return null
  return { top: `${current.offsetTop}px`, height: `${current.offsetHeight}px` }
}

async function updateIndicators(): Promise<void> {
  await nextTick()
  navigationIndicator.value = measureIndicator(navigationRef.value)
  settingsIndicator.value = measureIndicator(settingsNavRef.value)
}

watch(() => route.fullPath, () => { void updateIndicators() })
watch(() => props.mode, () => { void updateIndicators() })
onMounted(() => {
  void updateIndicators()
  window.addEventListener('resize', updateIndicators)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateIndicators)
})
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
    <RouterLink class="app-sidebar__brand" to="/workbench" aria-label="知衡首页" @click="completeNavigation">
      <span class="app-sidebar__brand-mark" aria-hidden="true">
        <svg viewBox="0 0 48 48" focusable="false">
          <path d="M7 14 25 5v10l-8 4v18L7 32V14Z" />
          <path d="m18 20 9-5 14 8v16l-7 4-9-5 8-5V25l-8-4v17l-7-4V20Z" />
          <circle cx="12" cy="19" r="1.7" class="app-sidebar__brand-accent" />
          <circle cx="12" cy="25" r="1.7" class="app-sidebar__brand-accent" />
        </svg>
      </span>
      <span class="app-sidebar__brand-copy">
        <strong>知衡</strong>
        <small>教师教学工作台</small>
      </span>
    </RouterLink>

    <ExamContextSwitcher :mode="mode" @navigate="emit('navigate')" />

    <button
      type="button"
      class="app-sidebar__link app-sidebar__command"
      aria-label="快速跳转（Ctrl+K）"
      @click="emit('openPalette')"
    >
      <Search :size="18" :stroke-width="1.8" aria-hidden="true" />
      <span>快速跳转</span>
      <kbd aria-hidden="true">Ctrl K</kbd>
    </button>

    <nav
      ref="navigationRef"
      class="app-sidebar__navigation"
      :class="{ 'has-indicator': navigationIndicator !== null }"
      data-testid="app-navigation"
      aria-label="主要导航"
    >
      <span
        v-if="navigationIndicator"
        class="app-sidebar__indicator"
        aria-hidden="true"
        :style="navigationIndicator"
      />
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
          :to="resolveNavigationTarget(item, sessionStore.selectedSessionId)"
          :aria-label="item.label"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="completeNavigation"
        >
          <AppIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </section>
    </nav>

    <div class="app-sidebar__settings">
      <WorkspaceAITaskDrawer />
      <nav
        ref="settingsNavRef"
        id="settings-navigation"
        class="app-sidebar__settings-links"
        :class="{ 'has-indicator': settingsIndicator !== null }"
        aria-label="设置导航"
      >
        <span
          v-if="settingsIndicator"
          class="app-sidebar__indicator"
          aria-hidden="true"
          :style="settingsIndicator"
        />
        <RouterLink
          v-for="item in settingsNavigationItems"
          :key="item.id"
          class="app-sidebar__link"
          :to="item.path"
          :aria-label="item.label"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="completeNavigation"
        >
          <AppIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <button
        v-if="collapsible"
        type="button"
        class="app-sidebar__link app-sidebar__collapse-toggle"
        :aria-label="mode === 'expanded' ? '收起导航' : '展开导航'"
        :aria-expanded="mode === 'expanded'"
        aria-controls="application-sidebar"
        data-testid="sidebar-collapse-toggle"
        @click="emit('toggleCollapse')"
      >
        <PanelLeftClose v-if="mode === 'expanded'" :size="18" :stroke-width="1.8" aria-hidden="true" />
        <PanelLeftOpen v-else :size="18" :stroke-width="1.8" aria-hidden="true" />
        <span>{{ mode === 'expanded' ? '收起导航' : '展开导航' }}</span>
      </button>
    </div>
  </aside>
</template>
