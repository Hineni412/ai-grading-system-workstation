<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineOptions({ inheritAttrs: false })

defineProps<{
  title: string
  titleId?: string
}>()

const sentinel = ref<HTMLElement | null>(null)
const isScrolled = ref(false)

let observer: IntersectionObserver | undefined
let scrollParent: HTMLElement | null = null

function updateFromScroll() {
  if (!sentinel.value || !scrollParent) return
  isScrolled.value = sentinel.value.getBoundingClientRect().top
    < scrollParent.getBoundingClientRect().top
}

onMounted(() => {
  if (!sentinel.value) return
  if (typeof IntersectionObserver !== 'undefined') {
    scrollParent = sentinel.value.closest('.main-workspace')
    observer = new IntersectionObserver(
      (entries) => { isScrolled.value = !entries[0]!.isIntersecting },
      { root: scrollParent, threshold: 0 },
    )
    observer.observe(sentinel.value)
    return
  }
  scrollParent = sentinel.value.closest('.main-workspace')
  scrollParent?.addEventListener('scroll', updateFromScroll, { passive: true })
  updateFromScroll()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  scrollParent?.removeEventListener('scroll', updateFromScroll)
})
</script>

<template>
  <div ref="sentinel" class="page-header__sentinel" aria-hidden="true"></div>
  <header v-bind="$attrs" class="page-header" :class="{ 'page-header--scrolled': isScrolled }">
    <div class="page-header__lead" :class="{ 'page-header__lead--with-back': $slots.back }">
      <slot name="back" />
      <h1 :id="titleId" class="page-header__title" tabindex="-1">{{ title }}</h1>
      <div v-if="$slots.meta" class="page-header__meta"><slot name="meta" /></div>
    </div>
    <div v-if="$slots.navigation" class="page-header__navigation"><slot name="navigation" /></div>
    <div v-if="$slots.actions" class="page-header__actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.page-header__sentinel {
  position: absolute;
  inset-inline: 0;
  height: 1px;
  pointer-events: none;
}

.page-header {
  position: sticky;
  top: 0;
  z-index: var(--shell-topbar-z-index);
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-height: 56px;
  padding: 0 var(--page-inset-x);
  background: color-mix(in srgb, var(--color-bg-app) 88%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid transparent;
  transition: border-color var(--duration-base) var(--ease-out);
}

.page-header--scrolled {
  border-bottom-color: var(--color-border-subtle);
}

.page-header__lead {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
  flex-wrap: wrap;
}

.page-header__lead--with-back {
  align-items: center;
}

.page-header__title {
  margin: 0;
  font-size: var(--font-size-h2);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  line-height: 1.2;
}

.page-header__meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--font-size-dense);
  color: var(--color-text-secondary);
}

.page-header__actions {
  margin-inline-start: auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
}

.page-header__navigation {
  align-self: stretch;
  display: flex;
  align-items: stretch;
  margin-inline-start: auto;
}

.page-header__navigation + .page-header__actions {
  margin-inline-start: 0;
}
</style>
