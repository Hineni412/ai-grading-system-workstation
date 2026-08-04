<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  title: string
  subtitle?: string
  previewUrl?: string | null
  emptyMessage?: string
}>()

const previewState = ref<'idle' | 'loading' | 'ready' | 'error'>(
  props.previewUrl ? 'loading' : 'idle',
)

watch(
  () => props.previewUrl,
  value => { previewState.value = value ? 'loading' : 'idle' },
)
</script>

<template>
  <section class="tp-document-workspace">
    <aside class="tp-document-workspace__rail">
      <slot name="rail" />
    </aside>
    <main class="tp-document-workspace__canvas">
      <header>
        <div>
          <h2>{{ title }}</h2>
          <p v-if="subtitle">{{ subtitle }}</p>
        </div>
        <slot name="toolbar" />
      </header>
      <img
        v-if="previewUrl"
        v-show="previewState === 'ready'"
        class="tp-document-workspace__preview"
        :src="previewUrl"
        :alt="`${title}原页预览`"
        @load="previewState = 'ready'"
        @error="previewState = 'error'"
      >
      <div v-if="previewState === 'loading'" class="tp-document-workspace__empty" role="status">
        <p>正在载入原页预览…</p>
      </div>
      <div v-else-if="previewState === 'error'" class="tp-document-workspace__empty is-error" role="alert">
        <div>
          <strong>原页预览加载失败</strong>
          <p>资料仍在本机保存。请重新选择本页；若仍失败，请重新解析这份资料。</p>
        </div>
      </div>
      <div v-else-if="!previewUrl" class="tp-document-workspace__empty">
        <p>{{ emptyMessage ?? '选择资料页后在这里核对原内容。' }}</p>
      </div>
      <slot name="overlay" />
    </main>
    <aside class="tp-document-workspace__inspector">
      <slot name="inspector" />
    </aside>
  </section>
</template>
