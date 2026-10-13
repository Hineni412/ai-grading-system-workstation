<script setup lang="ts">
import brandMarkUrl from '../../assets/brand-mark.svg'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { PanelLeftClose, PanelLeftOpen, Search } from '@lucide/vue'

import {
  gradingRunRouteDefinition,
  navigationGroups,
  scoreImportRouteDefinition,
  settingsNavigationItems,
  type WorkspaceRouteDefinition,
  type WorkspaceNavigationGroup,
} from '../../navigation'
import { useSessionStore } from '../../stores/session'
import AppIcon from './AppIcon.vue'
import ExamContextSwitcher from './ExamContextSwitcher.vue'
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

// 导入考试没有扫描批改环节：侧栏把「考试批改」换成「导入成绩」。
const visibleGroups = computed<readonly WorkspaceNavigationGroup[]>(() => {
  if (sessionStore.currentSession?.kind !== 'zhixue_import') return navigationGroups
  return navigationGroups.map((group) => (group.id !== 'exam' ? group : {
    ...group,
    items: group.items.map((item) => (
      item.id === 'grading' ? scoreImportRouteDefinition : item
    )),
  }))
})

function isActive(item: WorkspaceRouteDefinition): boolean {
  if (route.name === gradingRunRouteDefinition.id) return item.id === 'grading'
  if (route.name === scoreImportRouteDefinition.id) return item.id === 'score-import'
  if (
    item.id === 'knowledge-overview'
    && ['knowledge-graph', 'training', 'student-evidence'].includes(String(route.name))
  ) return true
  if (item.id === 'ai-trace') {
    return route.path === '/settings' && route.query.section === 'log'
  }
  if (item.id === 'settings') {
    return route.path === '/settings' && route.query.section !== 'log'
  }
  return route.path === item.path || route.path.startsWith(`${item.path}/`)
}

function completeNavigation(): void {
  emit('navigate')
}

/* 当前项滑动指示条：与顶栏 tp-workspace-tabs__pill 同法，
   量出 aria-current 链接的 offsetTop/offsetHeight 后由 CSS 过渡滑动；
   jsdom 无布局（offsetHeight 为 0）时不渲染，链接保留自身激活底色。 */
const sidebarRef = ref<HTMLElement | null>(null)
const navigationRef = ref<HTMLElement | null>(null)
const settingsNavRef = ref<HTMLElement | null>(null)
const navigationIndicator = ref<{ top: string; height: string } | null>(null)
const settingsIndicator = ref<{ top: string; height: string } | null>(null)
/* 布局驱动的重定位不做过渡（否则悬停/模式切换时指示条会滞后滑动）；
   只有导航切换（路由、所选考试）才保留 top/height 动画。 */
const indicatorInstant = ref(true)

function measureIndicator(
  container: HTMLElement | null,
): { top: string; height: string } | null {
  const current = container?.querySelector<HTMLElement>('a[aria-current="page"]')
  if (!container || !current || current.offsetHeight === 0) return null
  return { top: `${current.offsetTop}px`, height: `${current.offsetHeight}px` }
}

async function updateIndicators(animated: boolean): Promise<void> {
  indicatorInstant.value = !animated
  await nextTick()
  navigationIndicator.value = measureIndicator(navigationRef.value)
  settingsIndicator.value = measureIndicator(settingsNavRef.value)
}

function remeasureIndicators(): void {
  void updateIndicators(false)
}

/* 指示条只跟随容器内部布局：悬停展开/宽度过渡会即时改导航内部行高，
   分两步兜底——容器尺寸每帧变化都重测（ResizeObserver），
   宽度/抽屉动画收尾再校一次（transitionend）；字体就绪也重测一次。
   这些来源都算布局驱动，重定位一律即时完成。 */
function onSidebarTransitionEnd(event: Event): void {
  const property = (event as TransitionEvent).propertyName
  if (property === 'width' || property === 'max-height') {
    remeasureIndicators()
  }
}

let indicatorObserver: ResizeObserver | null = null

watch(() => route.fullPath, () => { void updateIndicators(true) })
watch(() => props.mode, () => { void updateIndicators(false) })
watch(() => sessionStore.selectedSessionId, () => { void updateIndicators(true) })
onMounted(() => {
  void updateIndicators(false)
  window.addEventListener('resize', remeasureIndicators)
  sidebarRef.value?.addEventListener('transitionend', onSidebarTransitionEnd)
  if (typeof ResizeObserver !== 'undefined') {
    indicatorObserver = new ResizeObserver(remeasureIndicators)
    if (navigationRef.value) indicatorObserver.observe(navigationRef.value)
    if (settingsNavRef.value) indicatorObserver.observe(settingsNavRef.value)
  }
  void document.fonts?.ready.then(remeasureIndicators)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', remeasureIndicators)
  sidebarRef.value?.removeEventListener('transitionend', onSidebarTransitionEnd)
  indicatorObserver?.disconnect()
  indicatorObserver = null
})
</script>

<template>
  <aside
    ref="sidebarRef"
    id="application-sidebar"
    class="app-sidebar"
    :class="{ 'is-open': open }"
    :aria-hidden="open ? undefined : 'true'"
    :inert="open ? undefined : true"
    aria-label="应用导航"
  >
    <RouterLink class="app-sidebar__brand" to="/workbench" aria-label="阅知首页" @click="completeNavigation">
      <span class="app-sidebar__brand-mark" aria-hidden="true">
        <img :src="brandMarkUrl" alt="" width="34" height="34" />
      </span>
      <span class="app-sidebar__brand-copy">
        <strong>阅知</strong>
        <small>阅卷与教学</small>
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
        :class="{ 'app-sidebar__indicator--instant': indicatorInstant }"
        aria-hidden="true"
        :style="navigationIndicator"
      />
      <section
        v-for="group in visibleGroups"
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
          :class="{ 'app-sidebar__indicator--instant': indicatorInstant }"
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
