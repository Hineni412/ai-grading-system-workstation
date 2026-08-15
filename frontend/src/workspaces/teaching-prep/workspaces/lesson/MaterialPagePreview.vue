<script setup lang="ts">
import { computed } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'

const props = defineProps<{
  units: Array<{ unit_index: number, preview_url: string, title?: string | null }>
  page: number
  rangeLabel?: string
  notice?: string
}>()

const emit = defineEmits<{
  'update:page': [number]
}>()

const indexes = computed(() => (
  [...new Set(props.units.map(item => item.unit_index))].sort((left, right) => left - right)
))
const current = computed(() => (
  props.units.find(item => item.unit_index === props.page) ?? null
))
const currentIndex = computed(() => indexes.value.indexOf(props.page))
const canPrev = computed(() => currentIndex.value > 0)
const canNext = computed(() => (
  currentIndex.value >= 0 && currentIndex.value < indexes.value.length - 1
))

function go(delta: number): void {
  const position = currentIndex.value < 0 ? 0 : currentIndex.value
  const next = indexes.value[position + delta]
  if (next != null) emit('update:page', next)
}
</script>

<template>
  <div class="tp-material-page-preview" data-testid="material-page-preview">
    <header>
      <strong>第 {{ page }} 页{{ current?.title ? ` · ${current.title}` : '原页' }}</strong>
      <span v-if="rangeLabel" class="tp-muted">{{ rangeLabel }}</span>
    </header>
    <p v-if="notice" class="tp-muted">{{ notice }}</p>
    <div class="tp-slide-stage">
      <img
        v-if="current?.preview_url"
        :src="current.preview_url"
        :alt="`第 ${page} 页原页`"
      >
      <div v-else class="tp-slide-stage__paper">
        <p>本页还没有图。没有原页时不建议按页码加入。</p>
      </div>
    </div>
    <div class="tp-inline-actions">
      <AppButton variant="ghost" :disabled="!canPrev" @click="go(-1)">上一页</AppButton>
      <AppButton variant="ghost" :disabled="!canNext" @click="go(1)">下一页</AppButton>
    </div>
  </div>
</template>
