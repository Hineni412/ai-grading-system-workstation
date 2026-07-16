<script setup lang="ts">
import { ref } from 'vue'

import type { PageRole, TemplateSummary } from '../../api/template-regions'

defineProps<{
  template: TemplateSummary | null
  status: 'idle' | 'uploading' | 'unknown' | 'error'
}>()
const emit = defineEmits<{ upload: [file: File, firstPageRole: PageRole]; reconcile: [] }>()
const file = ref<File | null>(null)
const firstPageRole = ref<PageRole>('front')

function choose(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
function submit(): void {
  if (file.value) emit('upload', file.value, firstPageRole.value)
}
</script>

<template>
  <section class="template-upload" aria-labelledby="template-upload-title">
    <div class="template-upload__intro">
      <div>
        <span class="template-regions__eyebrow">样卷来源</span>
        <h2 id="template-upload-title">上传双页样卷</h2>
        <p>只读取前两页。请说明第一页是正面还是反面，系统会据此建立画框坐标。</p>
      </div>
      <div v-if="template" class="template-upload__thumbs" aria-label="当前样卷预览">
        <figure v-for="page in (['front', 'back'] as const)" :key="page">
          <img :src="template.pages[page].url" :alt="page === 'front' ? '当前样卷正面' : '当前样卷反面'">
          <figcaption>{{ page === 'front' ? '正面' : '反面' }}</figcaption>
        </figure>
      </div>
    </div>
    <form class="template-upload__form" @submit.prevent="submit">
      <label><span>样卷 PDF</span><input type="file" accept="application/pdf,.pdf" @change="choose"></label>
      <fieldset>
        <legend>第一页对应</legend>
        <label><input v-model="firstPageRole" type="radio" value="front"> 正面</label>
        <label><input v-model="firstPageRole" type="radio" value="back"> 反面</label>
      </fieldset>
      <button type="submit" :disabled="!file || status === 'uploading' || status === 'unknown'">
        {{ status === 'uploading' ? '正在上传…' : template ? '替换样卷' : '上传并打开画框' }}
      </button>
      <button v-if="status === 'unknown'" type="button" class="secondary" @click="emit('reconcile')">
        核对本次上传
      </button>
    </form>
  </section>
</template>
