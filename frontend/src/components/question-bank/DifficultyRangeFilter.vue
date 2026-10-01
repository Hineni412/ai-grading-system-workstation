<script setup lang="ts">
import { computed, ref, useId } from 'vue'

const props = withDefaults(defineProps<{
  min: number
  max: number
  compact?: boolean
  ceiling?: number
}>(), {
  compact: false,
  ceiling: 10,
})

const emit = defineEmits<{
  'update:min': [value: number]
  'update:max': [value: number]
  change: []
}>()

const summaryId = useId()
const activeThumb = ref<'min' | 'max' | null>(null)
const ticks = computed(() => Array.from({ length: props.ceiling }, (_value, index) => index + 1))
const levels = computed(() => Array.from({ length: props.ceiling * 2 - 1 }, (_value, index) => 1 + index / 2))
const minSelection = computed({ get: () => props.min, set: value => { updateMin(String(value)); finishMin() } })
const maxSelection = computed({ get: () => props.max, set: value => { updateMax(String(value)); finishMax() } })

function railPosition(value: number): string {
  const ratio = (value - 1) / (props.ceiling - 1)
  return `calc(${ratio * 100}% + ${16 - 32 * ratio}px)`
}

function railDistanceFromRight(value: number): string {
  const ratio = (props.ceiling - value) / (props.ceiling - 1)
  return `calc(${ratio * 100}% + ${16 - 32 * ratio}px)`
}

const bands = [
  { range: '1.0–2.4', label: '入门补缺', span: 15 },
  { range: '2.5–4.4', label: '基础巩固', span: 20 },
  { range: '4.5–6.4', label: '中档提升', span: 20 },
  { range: '6.5–7.4', label: '综合突破', span: 10 },
  { range: '7.5–10', label: '压轴拔高', span: 25 },
]

function formatLevel(value: number): string {
  return value.toFixed(1)
}

function updateMin(value: string): void {
  emit('update:min', Math.min(Number(value), props.max))
}

function updateMax(value: string): void {
  emit('update:max', Math.max(Number(value), props.min))
}

function finishMin(): void {
  emit('change')
  if (props.min === props.max) activeThumb.value = 'max'
}

function finishMax(): void {
  emit('change')
  if (props.min === props.max) activeThumb.value = 'min'
}

function thumbLayer(thumb: 'min' | 'max'): number {
  if (activeThumb.value === thumb) return 5
  if (props.min !== props.max) return thumb === 'min' ? 4 : 3
  if (activeThumb.value === null) return thumb === 'max' ? 4 : 3
  return 3
}

function keyboardValue(
  event: KeyboardEvent,
  current: number,
  lower: number,
  upper: number,
): number | null {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
    return Math.max(lower, Math.round((current - 0.5) * 10) / 10)
  }
  if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
    return Math.min(upper, Math.round((current + 0.5) * 10) / 10)
  }
  if (event.key === 'Home') return lower
  if (event.key === 'End') return upper
  return null
}

function updateMinFromKeyboard(event: KeyboardEvent): void {
  const value = keyboardValue(event, props.min, 1, props.max)
  if (value === null) return
  event.preventDefault()
  if (value === props.min) return
  emit('update:min', value)
  emit('change')
}

function updateMaxFromKeyboard(event: KeyboardEvent): void {
  const value = keyboardValue(event, props.max, props.min, props.ceiling)
  if (value === null) return
  event.preventDefault()
  if (value === props.max) return
  emit('update:max', value)
  emit('change')
}
</script>

<template>
  <div v-if="compact" class="difficulty-range-compact" role="group" aria-label="难度区间">
    <span>难度</span>
    <select v-model="minSelection" class="app-input" aria-label="最低难度"><option v-for="level in levels" :key="level" :value="level" :disabled="level > max">{{ level }}</option></select>
    <span>–</span>
    <select v-model="maxSelection" class="app-input" aria-label="最高难度"><option v-for="level in levels" :key="level" :value="level" :disabled="level < min">{{ level }}</option></select>
  </div>
  <fieldset v-else class="difficulty-range">
    <legend>
      <span>难度区间</span>
      <strong>{{ formatLevel(min) }}–{{ formatLevel(max) }}</strong>
    </legend>
    <p :id="summaryId" class="difficulty-range__summary">
      难度使用 1 到 {{ ceiling }} 的刻度，可按 0.5 微调，当前选择 {{ formatLevel(min) }} 到 {{ formatLevel(max) }}。
    </p>
    <div class="difficulty-range__rail">
      <div class="difficulty-range__track" />
      <div
        class="difficulty-range__selection"
        :style="{
          left: railPosition(min),
          right: railDistanceFromRight(max),
        }"
      />
      <span
        class="difficulty-range__thumb difficulty-range__thumb--min"
        :class="{ 'is-overlapping': min === max }"
        :style="{ left: railPosition(min) }"
        aria-hidden="true"
      />
      <span
        class="difficulty-range__thumb difficulty-range__thumb--max"
        :class="{ 'is-overlapping': min === max }"
        :style="{ left: railPosition(max) }"
        aria-hidden="true"
      />
      <input
        class="difficulty-range__input difficulty-range__input--min"
        :value="min"
        type="range"
        min="1"
        :max="ceiling"
        step="0.5"
        aria-label="最低难度"
        :aria-valuetext="`最低难度 ${formatLevel(min)}`"
        :aria-describedby="summaryId"
        :style="{ zIndex: thumbLayer('min') }"
        @input="updateMin(($event.currentTarget as HTMLInputElement).value)"
        @change="finishMin"
        @focus="activeThumb = 'min'"
        @pointerdown="activeThumb = 'min'"
        @keydown="updateMinFromKeyboard"
      >
      <input
        class="difficulty-range__input difficulty-range__input--max"
        :value="max"
        type="range"
        min="1"
        :max="ceiling"
        step="0.5"
        aria-label="最高难度"
        :aria-valuetext="`最高难度 ${formatLevel(max)}`"
        :aria-describedby="summaryId"
        :style="{ zIndex: thumbLayer('max') }"
        @input="updateMax(($event.currentTarget as HTMLInputElement).value)"
        @change="finishMax"
        @focus="activeThumb = 'max'"
        @pointerdown="activeThumb = 'max'"
        @keydown="updateMaxFromKeyboard"
      >
    </div>
    <div class="difficulty-range__ticks" aria-hidden="true">
      <span
        v-for="tick in ticks"
        :key="tick"
        :style="{ left: railPosition(tick) }"
      >
        <i />
        <b>{{ tick }}</b>
      </span>
    </div>
    <div v-if="ceiling === 10" class="difficulty-range__bands" aria-label="难度分段">
      <span
        v-for="band in bands"
        :key="band.range"
        :style="{ '--difficulty-band-span': band.span }"
      >
        <b>{{ band.range }}</b>
        <small>{{ band.label }}</small>
      </span>
    </div>
  </fieldset>
</template>
