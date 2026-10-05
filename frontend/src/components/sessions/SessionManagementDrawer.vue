<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { useSessionStore } from '../../stores/session'
import SessionManager from './SessionManager.vue'

const open = defineModel<boolean>('open', { default: false })
const closeButton = ref<HTMLButtonElement | null>(null)
const sessionStore = useSessionStore()
const sessionCount = computed(() => sessionStore.sessions.length)

function close(): void {
  open.value = false
}

watch(open, (value) => {
  if (value) void nextTick(() => closeButton.value?.focus())
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="session-management-layer fx-overlay"
      @click.self="close"
    >
      <aside
        class="session-management-drawer fx-drawer-right"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-management-title"
      >
        <header class="session-management-drawer__header">
          <h2 id="session-management-title">
            考试管理
            <span class="session-management-drawer__count">共 {{ sessionCount }} 场</span>
          </h2>
          <button
            ref="closeButton"
            type="button"
            aria-label="关闭考试管理"
            @click="close"
          >×</button>
        </header>
        <div class="session-management-drawer__body">
          <SessionManager @navigate="close" />
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.session-management-layer {
  background: var(--color-overlay-mask);
  display: flex;
  inset: 0;
  justify-content: flex-end;
  position: fixed;
  z-index: 1300;
}

.session-management-drawer {
  background: var(--color-bg-surface);
  border-inline-start: var(--border-width) solid var(--color-border-default);
  box-shadow: var(--shadow-overlay);
  display: flex;
  flex-direction: column;
  max-width: 100%;
  width: min(560px, 100vw - 24px);
}

.session-management-drawer__header {
  align-items: center;
  border-block-end: var(--border-width) solid var(--color-border-default);
  display: flex;
  gap: var(--space-4);
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
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

.session-management-drawer__header button {
  align-items: center;
  background: var(--color-bg-surface);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  color: var(--color-text-secondary);
  cursor: pointer;
  display: inline-flex;
  flex: 0 0 auto;
  font: inherit;
  font-size: var(--font-size-h2);
  height: var(--control-height-default);
  justify-content: center;
  width: var(--control-height-default);
}

.session-management-drawer__body {
  min-height: 0;
  overflow: auto;
  padding: var(--space-3) var(--space-4) var(--space-6);
}
</style>
