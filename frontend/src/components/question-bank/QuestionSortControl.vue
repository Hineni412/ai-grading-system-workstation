<script setup lang="ts">
import { computed } from 'vue'

import type { QuestionBankSort } from '../../api/question-bank'

const props = defineProps<{
  modelValue: QuestionBankSort
}>()

const emit = defineEmits<{
  'update:modelValue': [value: QuestionBankSort]
  change: [value: QuestionBankSort]
}>()

const activeField = computed(() => props.modelValue.startsWith('difficulty_')
  ? 'difficulty'
  : 'frequency')
const direction = computed(() => props.modelValue.endsWith('_asc') ? 'asc' : 'desc')

function choose(field: 'difficulty' | 'frequency'): void {
  const nextDirection = activeField.value === field && direction.value === 'desc' ? 'asc' : 'desc'
  const next = `${field}_${nextDirection}` as QuestionBankSort
  emit('update:modelValue', next)
  emit('change', next)
}

function label(field: 'difficulty' | 'frequency'): string {
  if (activeField.value !== field) return `${field === 'difficulty' ? '难度' : '考频'}，点击后从高到低`
  return `${field === 'difficulty' ? '难度' : '考频'}，当前${direction.value === 'desc' ? '从高到低' : '从低到高'}，点击切换`
}
</script>

<template>
  <div class="question-sort" role="group" aria-label="试题排序">
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
