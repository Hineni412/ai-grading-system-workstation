<script setup lang="ts">
import { computed, ref, watch } from 'vue'

type DocumentPane = 'source' | 'preview' | 'review'

const props = defineProps<{
  title: string
  subtitle?: string
  previewUrl?: string | null
  emptyMessage?: string
  activePane?: DocumentPane
  previewZoom?: number
  fitWidth?: boolean
}>()
const emit = defineEmits<{
  'update:activePane': [value: DocumentPane]
  'preview-loaded': [url: string]
}>()

const previewState = ref<'idle' | 'loading' | 'ready' | 'error'>(
  props.previewUrl ? 'loading' : 'idle',
)
const lastEmittedPreviewUrl = ref<string | null>(null)

watch(
  () => props.previewUrl,
  value => { previewState.value = value ? 'loading' : 'idle' },
)

function handlePreviewLoad(): void {
  previewState.value = 'ready'
  const url = props.previewUrl
  if (!url || lastEmittedPreviewUrl.value === url) return
  lastEmittedPreviewUrl.value = url
  emit('preview-loaded', url)
}

const previewStyle = computed(() => {
  if (props.fitWidth) return { inlineSize: '100%', maxInlineSize: '100%' }
  const zoom = Math.min(200, Math.max(50, props.previewZoom ?? 100))
  return { inlineSize: `${zoom}%`, maxInlineSize: 'none' }
})
</script>

<template>
  <section class="tp-document-workspace" :class="{ 'is-mobile-controlled': activePane }">
    <nav v-if="activePane" class="tp-document-workspace__mobile-tabs" aria-label="窄屏资料工作栏">
      <button
        v-for="item in ([['source', '来源'], ['preview', '预览'], ['review', '审阅']] as const)"
        :key="item[0]"
        type="button"
        :aria-current="activePane === item[0] ? 'page' : undefined"
        @click="emit('update:activePane', item[0])"
      >
        {{ item[1] }}
      </button>
    </nav>
    <aside class="tp-document-workspace__rail" :class="{ 'is-mobile-active': activePane === 'source' }">
      <slot name="rail" />
    </aside>
    <main class="tp-document-workspace__canvas" :class="{ 'is-mobile-active': activePane === 'preview' }">
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
        :style="previewStyle"
        @load="handlePreviewLoad"
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
    <aside class="tp-document-workspace__inspector" :class="{ 'is-mobile-active': activePane === 'review' }">
      <slot name="inspector" />
    </aside>
  </section>
</template>
