<script setup lang="ts">
import { computed } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import { parseAnimationPageText } from './animationPages'
import MaterialPagePreview from './MaterialPagePreview.vue'

export interface PptAnimationPickerUnit {
  id?: string
  unit_index: number
  preview_url: string
  title?: string | null
  object_summary?: Record<string, unknown>
  revision?: number
}

export interface PptAnimationDraftTask {
  id: string
  pageText: string
}

const props = defineProps<{
  units: PptAnimationPickerUnit[]
  page: number
  tasks: PptAnimationDraftTask[]
  maxSelected: number
  remaining: number
  billedLimit: number
  loading?: boolean
  loadError?: string
  generating?: boolean
}>()

const emit = defineEmits<{
  'update:page': [number]
  'update:task-text': [string, string]
  'add-task': []
  'remove-task': [string]
  'generate-task': [string]
  'preview-loaded': [number]
  'update:unit': [PptAnimationPickerUnit]
}>()

const indexes = computed(() => (
  [...new Set(props.units.map(item => item.unit_index))].sort((left, right) => left - right)
))
const current = computed(() => (
  props.units.find(item => item.unit_index === props.page) ?? null
))
const canCreate = computed(() => props.tasks.length < props.remaining)
const previewNotice = computed(() => {
  const kind = current.value?.object_summary?.preview_kind
  if (kind === 'rendered') return '当前是放映软件实拍，公式和版式更接近上课画面；不改课件原文件。'
  const status = String(current.value?.object_summary?.preview_render_status ?? '')
  if (status === 'queued' || status === 'running') {
    return '先显示本机拼出的页；停住后会换成放映软件实拍。不改课件原文件。'
  }
  return '先显示本机拼出的页，用来认页和切页；停住后会换成放映软件实拍。不改课件原文件，也不因此打开第③步改编。'
})

function goTo(page: number): void {
  emit('update:page', page)
}

function parsedTask(text: string) {
  return parseAnimationPageText(text, indexes.value, props.maxSelected)
}

function canGenerate(text: string): boolean {
  const parsed = parsedTask(text)
  return (
    parsed.pages.length > 0
    && parsed.error === ''
    && props.remaining > 0
    && !props.generating
  )
}
</script>

<template>
  <div class="tp-animation-picker" data-testid="ppt-animation-page-picker">
    <p class="tp-source-group__label">
      课堂动画
      <small>先看这一页，再按页码写入任务；每个任务单独计费 1 次，最多 {{ billedLimit }} 次，不改课件副本</small>
    </p>
    <p v-if="loading" class="tp-muted">正在打开主课件预览…</p>
    <p v-else-if="loadError" class="tp-error-text">{{ loadError }}</p>
    <p v-else-if="!units.length" class="tp-muted">这份主课件还没有可预览的页。</p>
    <template v-else>
      <div
        class="tp-page-strip"
        data-testid="ppt-animation-page-strip"
        role="list"
        aria-label="主课件页"
      >
        <button
          v-for="item in indexes"
          :key="item"
          type="button"
          role="listitem"
          class="tp-page-strip__cell"
          :class="{ 'is-active': item === page }"
          :data-page-index="item"
          :aria-current="item === page ? 'page' : undefined"
          :aria-label="`查看第 ${item} 页`"
          @click="goTo(item)"
        >
          {{ item }}
        </button>
      </div>
      <MaterialPagePreview
        :units="units"
        :page="page"
        :notice="previewNotice"
        @update:page="goTo"
        @update:unit="emit('update:unit', $event)"
        @loaded="emit('preview-loaded', $event)"
      />
      <div
        v-if="tasks.length"
        class="tp-animation-tasks"
        data-testid="ppt-animation-tasks"
      >
        <article
          v-for="(task, index) in tasks"
          :key="task.id"
          class="tp-animation-task"
          data-testid="slide-animation-task"
        >
          <header>
            <strong>任务 {{ index + 1 }}</strong>
            <AppButton
              variant="ghost"
              data-testid="remove-slide-animation-task"
              @click="emit('remove-task', task.id)"
            >
              去掉
            </AppButton>
          </header>
          <label class="tp-field">
            PPT 页码
            <textarea
              :value="task.pageText"
              data-testid="slide-animation-pages"
              rows="3"
              placeholder="例如：3,5 或 2-4"
              @input="emit('update:task-text', task.id, ($event.target as HTMLTextAreaElement).value)"
            />
          </label>
          <p
            v-if="parsedTask(task.pageText).error"
            class="tp-error-text"
            data-testid="slide-animation-pages-error"
          >
            {{ parsedTask(task.pageText).error }}
          </p>
          <p v-else-if="parsedTask(task.pageText).pages.length" class="tp-muted">
            将使用第 {{ parsedTask(task.pageText).pages.join('、') }} 页
          </p>
          <p v-else class="tp-muted">用逗号、顿号或空格分隔页码，每次最多 {{ maxSelected }} 页</p>
          <AppButton
            variant="secondary"
            data-testid="generate-slide-animation"
            :disabled="!canGenerate(task.pageText)"
            @click="emit('generate-task', task.id)"
          >
            {{ generating ? '正在生成课堂动画…' : '生成课堂动画（单独计费 1 次）' }}
          </AppButton>
        </article>
      </div>
      <div class="tp-inline-actions">
        <AppButton
          variant="secondary"
          data-testid="create-slide-animation-task"
          :disabled="!canCreate"
          @click="emit('add-task')"
        >
          新建课堂动画
        </AppButton>
        <span class="tp-muted" data-testid="ppt-animation-quota">
          {{ canCreate ? `还可新建 ${remaining - tasks.length} 个任务` : remaining > 0 ? '请先完成或去掉已有任务' : `本课已用完 ${billedLimit} 次` }}
          · 本课还可单独发送 {{ remaining }} 次
        </span>
      </div>
    </template>
  </div>
</template>
