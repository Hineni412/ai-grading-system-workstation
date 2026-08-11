<script setup lang="ts">
import { computed } from 'vue'

import type { QuestionBankSort } from '../../api/question-bank'

const props = defineProps<{
  modelValue: QuestionBankSort
  showPaperOrder?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: QuestionBankSort]
  change: [value: QuestionBankSort]
}>()

type SortField = 'difficulty' | 'frequency'

const activeField = computed<SortField | 'paper'>(() => {
  if (props.modelValue === 'paper_order') return 'paper'
  return props.modelValue.startsWith('difficulty_') ? 'difficulty' : 'frequency'
})
const direction = computed(() => props.modelValue.endsWith('_asc') ? 'asc' : 'desc')

function choose(field: SortField): void {
  const nextDirection = activeField.value === field && direction.value === 'desc' ? 'asc' : 'desc'
  const next = `${field}_${nextDirection}` as QuestionBankSort
  emit('update:modelValue', next)
  emit('change', next)
}

function choosePaperOrder(): void {
  emit('update:modelValue', 'paper_order')
  emit('change', 'paper_order')
}

function label(field: SortField): string {
  if (activeField.value !== field) return `${field === 'difficulty' ? '难度' : '考频'}，点击后从高到低`
  return `${field === 'difficulty' ? '难度' : '考频'}，当前${direction.value === 'desc' ? '从高到低' : '从低到高'}，点击切换`
}
</script>

<template>
  <div class="question-sort" role="group" aria-label="试题排序">
    <button
      v-if="showPaperOrder"
      type="button"
      :class="{ 'is-active': activeField === 'paper' }"
      :aria-pressed="activeField === 'paper'"
      aria-label="题号，当前按题号从小到大，点击切换"
      @click="choosePaperOrder"
    >
      题号
      <span aria-hidden="true">
        {{ activeField === 'paper' ? '↓' : '↕' }}
      </span>
    </button>
    <button
      v-for="field in (['difficulty', 'frequency'] as const)"
      :key="field"
      type="button"
      :class="{ 'is-active': activeField === field }"
      :aria-pressed="activeField === field"
      :aria-label="label(field)"
      @click="choose(field)"
    >
      {{ field === 'difficulty' ? '难度' : '考频' }}
      <span aria-hidden="true">
        {{ activeField === field ? (direction === 'desc' ? '↓' : '↑') : '↕' }}
      </span>
    </button>
  </div>
</template>
