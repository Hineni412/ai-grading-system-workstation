<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  min: number
  max: number
  compact?: boolean
}>(), {
  compact: false,
})

const emit = defineEmits<{
  'update:min': [value: number]
  'update:max': [value: number]
  change: []
}>()

const lowerPercent = computed(() => ((props.min - 1) / 9) * 100)
const upperPercent = computed(() => ((props.max - 1) / 9) * 100)

const bands = [
  { range: '1–2', label: '入门补缺' },
  { range: '3–4', label: '基础巩固' },
  { range: '5–6', label: '中档提升' },
  { range: '7–8', label: '综合突破' },
  { range: '9–10', label: '压轴拔高' },
]

function updateMin(value: string): void {
  emit('update:min', Math.min(Number(value), props.max))
}

function updateMax(value: string): void {
  emit('update:max', Math.max(Number(value), props.min))
}
</script>

<template>
  <fieldset class="difficulty-range" :class="{ 'is-compact': compact }">
    <legend>
      <span>难度范围</span>
      <strong>{{ min }}–{{ max }}</strong>
    </legend>
    <div class="difficulty-range__rail">
      <div class="difficulty-range__track" />
      <div
        class="difficulty-range__selection"
        :style="{ left: `${lowerPercent}%`, right: `${100 - upperPercent}%` }"
      />
      <input
        :value="min"
        type="range"
        min="1"
        max="10"
        step="1"
        aria-label="最低难度"
        :aria-valuetext="`最低难度 ${min}`"
        @input="updateMin(($event.currentTarget as HTMLInputElement).value)"
        @change="emit('change')"
      >
      <input
        :value="max"
        type="range"
        min="1"
        max="10"
        step="1"
        aria-label="最高难度"
        :aria-valuetext="`最高难度 ${max}`"
        @input="updateMax(($event.currentTarget as HTMLInputElement).value)"
        @change="emit('change')"
      >
    </div>
    <div class="difficulty-range__bands" aria-hidden="true">
      <span v-for="band in bands" :key="band.range">
        <b>{{ band.range }}</b>
        <small>{{ band.label }}</small>
      </span>
    </div>
  </fieldset>
</template>
