<script setup lang="ts">
import { nextTick, ref } from 'vue'

import AppIconButton from '../design-system/AppIconButton.vue'
import SessionDeletionPanel from '../config/SessionDeletionPanel.vue'

const open = ref(false)
const closeButton = ref<HTMLButtonElement | null>(null)

function show(): void {
  open.value = true
  void nextTick(() => closeButton.value?.focus())
}

function close(): void {
  open.value = false
}
</script>

<template>
  <AppIconButton
    label="打开考试管理"
    icon="trash"
    variant="secondary"
    @click="show"
  />

  <Teleport to="body">
    <div
      v-if="open"
      class="session-management-layer"
      @click.self="close"
    >
      <aside
        class="session-management-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-management-title"
      >
        <header class="session-management-drawer__header">
          <div>
            <p>考试管理</p>
            <h2 id="session-management-title">考试管理</h2>
            <span>核对当前考试的影响后，可直接彻底删除。</span>
          </div>
          <button
            ref="closeButton"
            type="button"
            aria-label="关闭考试管理"
            @click="close"
          >×</button>
        </header>
        <div class="session-management-drawer__body">
          <SessionDeletionPanel />
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
  width: min(760px, 94vw);
}

.session-management-drawer__header {
  align-items: flex-start;
  border-block-end: var(--border-width) solid var(--color-border-default);
  display: flex;
  gap: var(--space-4);
  justify-content: space-between;
  padding: var(--space-5);
}

.session-management-drawer__header p,
.session-management-drawer__header h2,
.session-management-drawer__header span {
  margin: 0;
}

.session-management-drawer__header p {
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
}

.session-management-drawer__header h2 {
  font-size: var(--font-size-h2);
  margin-block: var(--space-1);
}

.session-management-drawer__header span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
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
  font-size: 22px;
  height: var(--control-height-default);
  justify-content: center;
  width: var(--control-height-default);
}

.session-management-drawer__body {
  overflow: auto;
  padding: 0 var(--space-5) var(--space-6);
}

.session-management-drawer__body :deep(.session-lifecycle) {
  border-block-start: 0;
  margin-block-start: 0;
  padding-block-start: var(--space-5);
}

.session-management-drawer__body :deep(.session-lifecycle__intro) {
  display: none;
}

.session-management-drawer__body :deep(.session-lifecycle__card) {
  grid-template-columns: 1fr;
}

.session-management-drawer__body :deep(.session-lifecycle__impact-grid) {
  grid-template-columns: repeat(2, minmax(120px, 1fr));
}
</style>
