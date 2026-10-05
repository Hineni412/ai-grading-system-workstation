<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogOverlay,
  AlertDialogPortal,
  AlertDialogRoot,
  AlertDialogTitle,
} from 'reka-ui'
import AppButton from './AppButton.vue'
import { confirmRequest, settleConfirm } from '../../composables/useConfirm'

const cancelButton = ref<{ focus?: () => void } | null>(null)
const confirmButton = ref<{ focus?: () => void } | null>(null)

const isAlert = computed(() => confirmRequest.value?.kind === 'alert')

function onOpenChange(open: boolean) {
  if (!open && confirmRequest.value) settleConfirm(false)
}

function onOpenAutoFocus(event: Event) {
  event.preventDefault()
  const target = confirmRequest.value?.danger ? cancelButton.value : confirmButton.value
  target?.focus?.()
}

function onConfirm() {
  settleConfirm(true)
}

function onCancel() {
  settleConfirm(false)
}
</script>

<template>
  <AlertDialogRoot :open="!!confirmRequest" @update:open="onOpenChange">
    <AlertDialogPortal>
      <AlertDialogOverlay class="app-confirm-overlay fx-overlay" />
      <AlertDialogContent
        v-if="confirmRequest"
        :key="confirmRequest.id"
        class="app-confirm fx-dialog"
        v-bind="confirmRequest.message ? {} : { 'aria-describedby': undefined }"
        data-testid="app-confirm-dialog"
        @open-auto-focus="onOpenAutoFocus"
      >
        <AlertDialogTitle class="app-confirm__title">{{ confirmRequest.title }}</AlertDialogTitle>
        <AlertDialogDescription v-if="confirmRequest.message" class="app-confirm__message">
          {{ confirmRequest.message }}
        </AlertDialogDescription>
        <footer class="app-confirm__footer">
          <AppButton v-if="!isAlert" ref="cancelButton" variant="secondary" @click="onCancel">{{ confirmRequest.cancelLabel }}</AppButton>
          <AppButton ref="confirmButton" :variant="confirmRequest.danger ? 'danger' : 'primary'" @click="onConfirm">{{ confirmRequest.confirmLabel }}</AppButton>
        </footer>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>

<style>
.app-confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: var(--color-overlay-mask);
}

.app-confirm {
  position: fixed;
  top: 50%;
  left: 50%;
  translate: -50% -50%;
  z-index: 1201;
  width: min(440px, calc(100vw - 2 * var(--space-5)));
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  border-radius: var(--radius-overlay);
  box-shadow: var(--shadow-overlay);
}

.app-confirm__title {
  margin: 0;
  padding: var(--space-4) var(--space-5) 0;
  font-size: var(--font-size-h3);
  font-weight: var(--font-weight-semibold);
}

.app-confirm__message {
  margin: 0;
  padding: var(--space-2) var(--space-5) 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
  line-height: var(--line-height-body);
  overflow-wrap: anywhere;
}

.app-confirm__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5) var(--space-5);
}
</style>
