<script setup lang="ts">
import { computed } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import MaterialPagePreview from './MaterialPagePreview.vue'

export interface PptAnimationPickerUnit {
  unit_index: number
  preview_url: string
  title?: string | null
  object_summary?: Record<string, unknown>
}

const props = defineProps<{
  units: PptAnimationPickerUnit[]
  page: number
  selectedPages: number[]
  maxSelected: number
  loading?: boolean
  loadError?: string
}>()

const emit = defineEmits<{
  'update:page': [number]
  'update:selectedPages': [number[]]
}>()

const indexes = computed(() => (
  [...new Set(props.units.map(item => item.unit_index))].sort((left, right) => left - right)
))
const current = computed(() => (
  props.units.find(item => item.unit_index === props.page) ?? null
))
const selectedSet = computed(() => new Set(props.selectedPages))
const currentSelected = computed(() => selectedSet.value.has(props.page))
const atLimit = computed(() => props.selectedPages.length >= props.maxSelected)
const selectedLabel = computed(() => {
  if (!props.selectedPages.length) return '还没有选页'
  return `已选第 ${props.selectedPages.join('、')} 页`
})
const previewNotice = computed(() => {
  const summary = current.value?.object_summary ?? {}
  const notice = typeof summary.preview_notice === 'string' ? summary.preview_notice.trim() : ''
  if (notice) return notice
  return summary.preview_kind === 'structural' ? '结构预览，不是原页' : ''
})

function goTo(page: number): void {
  emit('update:page', page)
}

function toggleCurrent(): void {
  if (!indexes.value.includes(props.page)) return
  if (currentSelected.value) {
    emit('update:selectedPages', props.selectedPages.filter(item => item !== props.page))
    return
  }
  if (atLimit.value) return
  emit('update:selectedPages', [...props.selectedPages, props.page].sort((left, right) => left - right))
}
</script>

<template>
  <div class="tp-animation-picker" data-testid="ppt-animation-page-picker">
    <p class="tp-source-group__label">
      课堂动画用页
      <small>先对着课件勾选，最多 {{ maxSelected }} 页；生成课堂动画会单独计费 1 次，不改课件副本</small>
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
          :class="{
            'is-active': item === page,
            'is-picked': selectedSet.has(item),
          }"
          :data-page-index="item"
          :aria-current="item === page ? 'page' : undefined"
          :aria-pressed="selectedSet.has(item)"
          :aria-label="`第 ${item} 页${selectedSet.has(item) ? '，已选入课堂动画' : ''}`"
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
      />
      <div class="tp-inline-actions">
        <AppButton
          variant="secondary"
          data-testid="toggle-animation-page"
          :disabled="!currentSelected && atLimit"
          @click="toggleCurrent"
        >
          {{ currentSelected ? '取消本页' : atLimit ? `已选满 ${maxSelected} 页` : '选入课堂动画' }}
        </AppButton>
        <span class="tp-muted" data-testid="ppt-animation-selection">{{ selectedLabel }}</span>
      </div>
    </template>
  </div>
</template>
