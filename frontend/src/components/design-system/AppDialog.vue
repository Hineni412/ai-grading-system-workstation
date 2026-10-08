<script setup lang="ts">
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  VisuallyHidden,
} from 'reka-ui'
import AppIconButton from './AppIconButton.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  open?: boolean
  title: string
  description?: string
  layout?: 'center' | 'fullscreen'
  width?: 'default' | 'wide'
  dismissible?: boolean
  closeOnOutside?: boolean
  hideHeader?: boolean
}>(), {
  open: false,
  description: undefined,
  layout: 'center',
  width: 'default',
  dismissible: true,
  closeOnOutside: true,
  hideHeader: false,
})

const emit = defineEmits<{ 'update:open': [boolean] }>()

function onUpdateOpen(open: boolean) {
  emit('update:open', open)
}

function onEscapeKeyDown(event: KeyboardEvent) {
  if (!props.dismissible) event.preventDefault()
}

function onInteractOutside(event: Event) {
  if (!props.dismissible || !props.closeOnOutside) event.preventDefault()
}
</script>

<template>
  <DialogRoot :open="open" @update:open="onUpdateOpen">
    <DialogPortal>
      <DialogOverlay class="app-dialog-overlay fx-overlay" />
      <DialogContent
        v-bind="{ ...$attrs, ...(description ? {} : { 'aria-describedby': undefined }) }"
        class="app-dialog fx-dialog"
        :class="[`app-dialog--${layout}`, width === 'wide' && 'app-dialog--wide']"
        @escape-key-down="onEscapeKeyDown"
        @interact-outside="onInteractOutside"
      >
        <header v-if="!hideHeader" class="app-dialog__header">
          <DialogTitle class="app-dialog__title">{{ title }}</DialogTitle>
          <slot name="header-extra" />
          <DialogClose as-child>
            <AppIconButton label="关闭" icon="close" :disabled="!dismissible" />
          </DialogClose>
        </header>
        <VisuallyHidden v-else>
          <DialogTitle>{{ title }}</DialogTitle>
        </VisuallyHidden>
        <DialogDescription v-if="description" class="app-dialog__description">{{ description }}</DialogDescription>
        <div class="app-dialog__body">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="app-dialog__footer">
          <slot name="footer" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style>
.app-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: var(--color-overlay-mask);
}

.app-dialog {
  position: fixed;
  z-index: 1101;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.app-dialog--center {
  top: 50%;
  left: 50%;
  translate: -50% -50%;
  width: min(560px, calc(100vw - 2 * var(--space-5)));
  max-height: calc(100dvh - 2 * var(--space-7));
  border-radius: var(--radius-overlay);
  box-shadow: var(--shadow-overlay);
}

.app-dialog--center.app-dialog--wide {
  width: min(760px, calc(100vw - 2 * var(--space-5)));
}

.app-dialog--fullscreen {
  inset: 0;
  width: 100%;
  height: 100dvh;
}

.app-dialog__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
  flex: none;
}

.app-dialog__title {
  margin: 0;
  font-size: var(--font-size-h3);
  font-weight: var(--font-weight-semibold);
}

.app-dialog__header .app-icon-button {
  margin-left: auto;
}

.app-dialog__description {
  margin: 0;
  padding: 0 var(--space-5);
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}

.app-dialog__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-5);
}

.app-dialog--center:has(> .app-dialog__description) .app-dialog__body {
  padding-top: var(--space-3);
}

.app-dialog--fullscreen .app-dialog__body {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
}

.app-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: 0 var(--space-5) var(--space-5);
  flex: none;
}
</style>
