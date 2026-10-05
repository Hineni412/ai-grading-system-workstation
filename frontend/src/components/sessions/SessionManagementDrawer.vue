<script setup lang="ts">
import { computed, nextTick } from 'vue'

import { useSessionStore } from '../../stores/session'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import SessionManager from './SessionManager.vue'

const open = defineModel<boolean>('open', { default: false })
const sessionStore = useSessionStore()
const sessionCount = computed(() => sessionStore.sessions.length)

function close(): void {
  open.value = false
}

function focusClose(event: Event): void {
  event.preventDefault()
  void nextTick(() => document.querySelector<HTMLElement>('.session-management-drawer .app-icon-button')?.focus())
}
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent
      class="session-management-drawer"
      :aria-describedby="undefined"
      @open-auto-focus="focusClose"
    >
      <SheetHeader class="session-management-drawer__header">
        <SheetTitle as="h2">
          考试管理
          <span class="session-management-drawer__count">共 {{ sessionCount }} 场</span>
        </SheetTitle>
      </SheetHeader>
      <div class="session-management-drawer__body">
        <SessionManager @navigate="close" />
      </div>
    </SheetContent>
  </Sheet>
</template>

<style scoped>
.session-management-drawer {
  max-width: 100%;
  width: min(560px, 100vw - 24px);
  gap: 0;
  padding: 0;
}

.session-management-drawer__header {
  align-items: center;
  border-block-end: var(--border-width) solid var(--color-border-default);
  display: flex;
  gap: var(--space-4);
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
}

.session-management-drawer__header h2 {
  align-items: baseline;
  display: flex;
  font-size: var(--font-size-h3);
  font-weight: var(--font-weight-semibold);
  gap: var(--space-2);
  margin: 0;
}

.session-management-drawer__count {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.session-management-drawer__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-5);
}
</style>
