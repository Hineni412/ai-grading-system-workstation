<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppIconButton from '../design-system/AppIconButton.vue'
import { workspaceRegistry } from '../../workspaces/registry'
import type { WorkspaceSubNavigationItem } from '../../workspaces/contracts'

const props = defineProps<{
  navigationOpen: boolean
}>()

const emit = defineEmits<{
  toggleNavigation: []
}>()

const route = useRoute()
const router = useRouter()
const pageTitle = computed(() => String(route.meta.title ?? '工作台'))
const currentWorkspace = computed(() => workspaceRegistry.modules.find(
  ({ manifest }) => route.path === manifest.routePrefix
    || route.path.startsWith(`${manifest.routePrefix}/`),
))
const workspaceSubNavigation = computed(() => [
  ...(currentWorkspace.value?.manifest.subNavigation ?? []),
].sort((left, right) => left.order - right.order))

function isCurrentSubNavigation(item: WorkspaceSubNavigationItem): boolean {
  return Object.entries(item.query).every(
    ([key, value]) => route.query[key] === value,
  )
}

/* 滑动 pill 指示条（Inspira Animated Tabs 的测量式实现）：
   量出当前 tab 按钮的位置后由 CSS 过渡滑动；jsdom 无布局（offsetWidth 为 0）
   时不渲染 pill，直接退化为无指示条终态。 */
const workspaceTabsRef = ref<HTMLElement | null>(null)
const workspaceTabsPill = ref<{ left: string; width: string } | null>(null)

async function updateWorkspaceTabsPill(): Promise<void> {
  await nextTick()
  const nav = workspaceTabsRef.value
  const current = nav?.querySelector<HTMLElement>('button[aria-current="page"]')
  if (!nav || !current || current.offsetWidth === 0) {
    workspaceTabsPill.value = null
    return
  }
  workspaceTabsPill.value = {
    left: `${current.offsetLeft}px`,
    width: `${current.offsetWidth}px`,
  }
}

watch(() => route.fullPath, () => { void updateWorkspaceTabsPill() })
watch(workspaceSubNavigation, () => { void updateWorkspaceTabsPill() })
onMounted(() => {
  void updateWorkspaceTabsPill()
  window.addEventListener('resize', updateWorkspaceTabsPill)
})
onUnmounted(() => {
  window.removeEventListener('resize', updateWorkspaceTabsPill)
})

async function openWorkspaceDestination(item: WorkspaceSubNavigationItem): Promise<void> {
  const current = currentWorkspace.value
  if (!current) return
  await router.push({
    path: current.manifest.routePrefix,
    query: { ...route.query, ...item.query },
  })
}
</script>

<template>
  <header
    class="app-topbar"
    :class="{ 'app-topbar--with-tabs': workspaceSubNavigation.length > 0 }"
    data-testid="app-topbar"
  >
    <a class="app-topbar__skip-link" href="#main-workspace">跳到主要工作区</a>

    <div class="app-topbar__page">
      <AppIconButton
        class="app-topbar__navigation-toggle"
        :label="props.navigationOpen ? '收起导航' : '展开导航'"
        :icon="props.navigationOpen ? 'close' : 'menu'"
        variant="secondary"
        :aria-expanded="props.navigationOpen"
        aria-controls="application-sidebar"
        @click="emit('toggleNavigation')"
      />
      <div class="app-topbar__page-copy">
        <strong>{{ pageTitle }}</strong>
      </div>
    </div>

    <div
      v-if="workspaceSubNavigation.length"
      id="workspace-topbar-tabs"
      class="app-topbar__workspace-navigation"
      :aria-label="`${currentWorkspace?.manifest.displayName ?? '工作台'}入口`"
    >
      <nav ref="workspaceTabsRef" class="tp-workspace-tabs" aria-label="工作台子导航">
        <span
          v-if="workspaceTabsPill"
          class="tp-workspace-tabs__pill"
          aria-hidden="true"
          :style="workspaceTabsPill"
        />
        <button
          v-for="item in workspaceSubNavigation"
          :key="item.destinationKey"
          type="button"
          :aria-current="isCurrentSubNavigation(item) ? 'page' : undefined"
          :class="{ 'is-current': isCurrentSubNavigation(item) }"
          @click="openWorkspaceDestination(item)"
        >
          {{ item.label }}
        </button>
      </nav>
    </div>
  </header>
</template>
