<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import { computed, ref } from 'vue'

import type { PageRole, TemplateSummary } from '../../api/template-regions'

const props = withDefaults(defineProps<{
  template: TemplateSummary | null
  status: 'idle' | 'uploading' | 'unknown' | 'error'
  assignmentStatus?: 'idle' | 'saving' | 'error'
}>(), { assignmentStatus: 'idle' })
const emit = defineEmits<{
  upload: [file: File, firstPageRole: PageRole]
  reconcile: []
  assign: [firstPageRole: PageRole]
}>()
const file = ref<File | null>(null)
const firstPageRole = ref<PageRole>('front')
const secondPageRole = computed<PageRole>(() => firstPageRole.value === 'front' ? 'back' : 'front')
const assignmentCopy = computed(() =>
  `PDF 第 1 页 → ${firstPageRole.value === 'front' ? '正面' : '反面'}；第 2 页 → ${secondPageRole.value === 'front' ? '正面' : '反面'}`,
)
const currentSecondPageRole = computed<PageRole>(() =>
  props.template?.first_page_role === 'back' ? 'front' : 'back',
)
const currentAssignmentCopy = computed(() => {
  const first = props.template?.first_page_role === 'back' ? '反面' : '正面'
  const second = currentSecondPageRole.value === 'front' ? '正面' : '反面'
  return `PDF 第 1 页 → ${first}；第 2 页 → ${second}`
})

function choose(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
function submit(): void {
  if (file.value) emit('upload', file.value, firstPageRole.value)
}
function assignCurrent(): void {
  const current = props.template?.first_page_role
  if (current) emit('assign', current === 'front' ? 'back' : 'front')
}
</script>

<template>
  <section
    class="template-upload"
    :class="{ 'template-upload--has-current': template }"
    aria-labelledby="template-upload-title"
  >
    <div v-if="template" class="template-upload__current">
      <div class="template-upload__current-copy">
        <h2 id="template-upload-title">当前页序</h2>
        <strong>{{ currentAssignmentCopy }}</strong>
      </div>
      <AppButton
        type="button" variant="secondary"
        data-action="swap-current-pages"
        aria-label="交换当前正反面。题框坐标保持不变，已确认题框会回到待确认状态"
        :disabled="assignmentStatus === 'saving' || status === 'uploading' || status === 'unknown'"
        @click="assignCurrent"
      >
        {{ assignmentStatus === 'saving' ? '正在交换…' : '交换当前正反面' }}
      </AppButton>
      <details class="template-upload__replacement">
        <summary>
          <span>样卷信息 / 更换样卷</span>
        </summary>
        <div class="template-upload__replacement-panel">
          <div class="template-upload__source-summary">
            <div class="template-upload__thumbs" aria-label="当前样卷预览">
              <figure v-for="page in (['front', 'back'] as const)" :key="page">
                <img :src="template.pages[page].url" :alt="page === 'front' ? '当前样卷正面' : '当前样卷反面'">
                <figcaption>{{ page === 'front' ? '正面' : '反面' }}</figcaption>
              </figure>
            </div>
            <div>
              <strong>当前样卷信息</strong>
              <p>交换页序会保留题框坐标，并把已确认题框恢复为待确认；只有文件本身不对时才需要更换 PDF。</p>
            </div>
          </div>
          <form class="template-upload__form" @submit.prevent="submit">
            <label><span>更换为新的样卷 PDF</span><input type="file" accept="application/pdf,.pdf" @change="choose"></label>
            <fieldset>
              <legend>新 PDF 第 1 页对应</legend>
              <div class="template-upload__role-options">
                <label :class="{ 'is-selected': firstPageRole === 'front' }">
                  <input v-model="firstPageRole" type="radio" value="front"> 正面
                </label>
                <label :class="{ 'is-selected': firstPageRole === 'back' }">
                  <input v-model="firstPageRole" type="radio" value="back"> 反面
                </label>
              </div>
              <output class="template-upload__assignment" aria-live="polite">{{ assignmentCopy }}</output>
              <small>该选择只作用于这次新上传的 PDF。</small>
            </fieldset>
            <AppButton type="submit" :disabled="!file || status === 'uploading' || status === 'unknown'" variant="primary">
              {{ status === 'uploading' ? '正在上传…' : '确认更换样卷' }}
            </AppButton>
            <AppButton v-if="status === 'unknown'" type="button" variant="secondary" @click="emit('reconcile')">
              核对本次上传
            </AppButton>
          </form>
        </div>
      </details>
    </div>
    <div v-else class="template-upload__intro">
      <div>
        <h2 id="template-upload-title">上传双页样卷</h2>
        <p>只读取前两页。请说明第一页是正面还是反面，系统会据此建立画框坐标。</p>
      </div>
    </div>
    <details v-if="!template" class="template-upload__replacement" open>
      <summary aria-label="准备上传样卷 PDF">
        <span>准备上传样卷 PDF</span>
      </summary>
      <form class="template-upload__form" @submit.prevent="submit">
        <label><span>样卷 PDF</span><input type="file" accept="application/pdf,.pdf" @change="choose"></label>
        <fieldset>
          <legend>新 PDF 第 1 页对应</legend>
          <div class="template-upload__role-options">
            <label :class="{ 'is-selected': firstPageRole === 'front' }">
              <input v-model="firstPageRole" type="radio" value="front"> 正面
            </label>
            <label :class="{ 'is-selected': firstPageRole === 'back' }">
              <input v-model="firstPageRole" type="radio" value="back"> 反面
            </label>
          </div>
          <output class="template-upload__assignment" aria-live="polite">{{ assignmentCopy }}</output>
          <small>该选择只作用于这次新上传的 PDF。</small>
        </fieldset>
        <AppButton type="submit" :disabled="!file || status === 'uploading' || status === 'unknown'" variant="primary">
          {{ status === 'uploading' ? '正在上传…' : '上传并打开画框' }}
        </AppButton>
        <AppButton v-if="status === 'unknown'" type="button" variant="secondary" @click="emit('reconcile')">
          核对本次上传
        </AppButton>
      </form>
    </details>
  </section>
</template>
