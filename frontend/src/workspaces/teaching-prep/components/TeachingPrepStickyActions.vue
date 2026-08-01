<script setup lang="ts">
withDefaults(defineProps<{
  state?: 'clean' | 'dirty' | 'saving' | 'saved' | 'error' | 'conflict'
  message?: string
}>(), {
  state: 'clean',
  message: '没有未保存修改',
})
</script>

<template>
  <footer class="tp-sticky-actions" :class="`is-${state}`">
    <p role="status" aria-live="polite">
      <span class="tp-sticky-actions__dot" aria-hidden="true" />
      {{ message }}
    </p>
    <div class="tp-sticky-actions__buttons">
      <slot />
    </div>
  </footer>
</template>
