<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  ListboxContent,
  ListboxFilter,
  ListboxGroup,
  ListboxGroupLabel,
  ListboxItem,
  ListboxRoot,
  VisuallyHidden,
} from 'reka-ui'
import { Search } from '@lucide/vue'

import {
  aiTraceRouteDefinition,
  navigationItems,
  type WorkspaceRouteDefinition,
} from '../../navigation'
import type { SessionSummary } from '../../api/sessions'
import { useSessionStore } from '../../stores/session'
import { useSessionSwitch } from '../../composables/useSessionSwitch'
import AppIcon from './AppIcon.vue'
import { resolveNavigationTarget } from './navigation-target'

const open = defineModel<boolean>('open', { default: false })

const router = useRouter()
const sessionStore = useSessionStore()
const { switchSession } = useSessionSwitch()
const query = ref('')

/* 页面组：全部导航项 + AI 调用记录，按 id 去重 */
const pageItems = computed<WorkspaceRouteDefinition[]>(() => {
  const seen = new Set<string>()
  const items = [...navigationItems, aiTraceRouteDefinition].filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })
  const term = query.value.trim().toLowerCase()
  if (!term) return items
  return items.filter((item) =>
    `${item.label} ${item.description}`.toLowerCase().includes(term),
  )
})

const sessionItems = computed<SessionSummary[]>(() => {
  const term = query.value.trim().toLowerCase()
  if (!term) return sessionStore.sessions
  return sessionStore.sessions.filter((session) =>
    session.name.toLowerCase().includes(term),
  )
})

const hasResults = computed(() => pageItems.value.length + sessionItems.value.length > 0)

function openPage(item: WorkspaceRouteDefinition): void {
  open.value = false
  void router.push(resolveNavigationTarget(item, sessionStore.selectedSessionId))
}

async function chooseSession(session: SessionSummary): Promise<void> {
  if (await switchSession(session.id)) open.value = false
}

function onGlobalKeydown(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    open.value = !open.value
  }
}

watch(open, (value) => {
  if (value) query.value = ''
})

onMounted(() => window.addEventListener('keydown', onGlobalKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKeydown))
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="command-palette__overlay fx-overlay" />
      <DialogContent class="command-palette fx-dialog">
        <VisuallyHidden><DialogTitle>快速跳转</DialogTitle></VisuallyHidden>
        <ListboxRoot class="command-palette__listbox" highlight-on-hover>
          <div class="command-palette__search">
            <Search :size="16" :stroke-width="1.8" aria-hidden="true" />
            <ListboxFilter
              v-model="query"
              auto-focus
              class="command-palette__input"
              placeholder="输入页面名称或考试名称"
              aria-label="搜索页面或考试"
            />
          </div>
          <ListboxContent class="command-palette__content">
            <ListboxGroup v-if="pageItems.length">
              <ListboxGroupLabel class="command-palette__group-label">页面</ListboxGroupLabel>
              <ListboxItem
                v-for="item in pageItems"
                :key="item.id"
                class="command-palette__item"
                :value="`page:${item.id}`"
                :text-value="item.label"
                @select="openPage(item)"
              >
                <AppIcon :name="item.icon" :size="16" />
                <span class="command-palette__item-copy">
                  <strong>{{ item.label }}</strong>
                  <small>{{ item.description }}</small>
                </span>
              </ListboxItem>
            </ListboxGroup>
            <ListboxGroup v-if="sessionItems.length">
              <ListboxGroupLabel class="command-palette__group-label">切换考试</ListboxGroupLabel>
              <ListboxItem
                v-for="session in sessionItems"
                :key="session.id"
                class="command-palette__item"
                :value="`session:${session.id}`"
                :text-value="session.name"
                @select="chooseSession(session)"
              >
                <span class="command-palette__item-copy">
                  <strong>{{ session.name }}</strong>
                </span>
                <em v-if="session.id === sessionStore.selectedSessionId" class="command-palette__current">当前</em>
              </ListboxItem>
            </ListboxGroup>
            <p v-if="!hasResults" class="command-palette__empty">
              没有匹配的页面或考试
            </p>
          </ListboxContent>
          <footer class="command-palette__footer">↑↓ 选择 · Enter 打开 · Esc 关闭</footer>
        </ListboxRoot>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.command-palette__overlay {
  position: fixed;
  inset: 0;
  z-index: 1400;
  background: var(--color-overlay-mask);
  backdrop-filter: blur(2px);
}

.command-palette__overlay[data-state='closed'] {
  animation: command-palette-out 120ms ease-out both;
}

.command-palette {
  position: fixed;
  inset-inline: 0;
  top: 14vh;
  z-index: 1401;
  width: min(560px, calc(100vw - var(--space-6)));
  margin-inline: auto;
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-overlay);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-overlay);
}

.command-palette[data-state='closed'] {
  animation: command-palette-out 120ms ease-out both;
}

@keyframes command-palette-out {
  to { opacity: 0; }
}

.command-palette__listbox {
  display: grid;
}

.command-palette__search {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-block-end: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-muted);
}

.command-palette__input {
  min-width: 0;
  min-height: var(--control-height-default);
  flex: 1;
  border: 0;
  background: transparent;
  color: var(--color-text-primary);
  outline: none;
}

.command-palette__input:focus-visible {
  outline: none;
  box-shadow: none;
}

.command-palette__content {
  display: grid;
  max-height: min(400px, 50vh);
  overflow-y: auto;
  padding: var(--space-2);
}

.command-palette__group-label {
  padding: var(--space-2) var(--space-2) var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
}

.command-palette__item {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.command-palette__item[data-highlighted] {
  background: var(--accent);
  color: var(--color-text-primary);
}

.command-palette__item-copy {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.command-palette__item-copy strong,
.command-palette__item-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-palette__item-copy strong {
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.command-palette__item-copy small {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.command-palette__current {
  margin-inline-start: auto;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-style: normal;
}

.command-palette__empty {
  margin: 0;
  padding: var(--space-6) var(--space-4);
  color: var(--color-text-muted);
  text-align: center;
}

.command-palette__footer {
  padding: var(--space-2) var(--space-4);
  border-block-start: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}
</style>
