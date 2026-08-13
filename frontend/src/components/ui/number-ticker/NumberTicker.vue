<!--
  数字滚动（来源：Inspira UI · Number Ticker，生产化适配）。
  动画基于项目已有 @vueuse/core 的 useTransition / useElementVisibility。
  jsdom 等无 IntersectionObserver / requestAnimationFrame 的环境（或 duration 为 0）
  直接渲染终值，不进入动画流程。
-->
<script setup lang="ts">
import { cn } from '@/lib/utils'
import { TransitionPresets, useElementVisibility, useTransition } from '@vueuse/core'
import { computed, ref, watch } from 'vue'

type TransitionsPresetsKeys = keyof typeof TransitionPresets

interface NumberTickerProps {
  value?: number
  direction?: 'up' | 'down'
  duration?: number
  delay?: number
  decimalPlaces?: number
  class?: string
  transition?: TransitionsPresetsKeys
}

const props = withDefaults(defineProps<NumberTickerProps>(), {
  value: 0,
  direction: 'up',
  delay: 0,
  duration: 1000,
  decimalPlaces: 0,
  transition: 'easeOutCubic',
})

// 测试环境（jsdom）没有 IntersectionObserver：可见性门控永远不会触发，
// 必须直接落终值，否则数字会停在 0。
const canAnimate =
  props.duration > 0
  && typeof window !== 'undefined'
  && typeof window.IntersectionObserver === 'function'
  && typeof window.requestAnimationFrame === 'function'

const spanRef = ref<HTMLSpanElement>()

const transitionValue = ref(props.direction === 'down' ? props.value : 0)

const transitionOutput = useTransition(transitionValue, {
  delay: props.delay,
  duration: props.duration,
  transition: TransitionPresets[props.transition],
})

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: props.decimalPlaces,
    maximumFractionDigits: props.decimalPlaces,
  }).format(Number(value.toFixed(props.decimalPlaces)))
}

const output = computed(() =>
  formatNumber(canAnimate ? transitionOutput.value : props.value))

if (canAnimate) {
  const isInView = useElementVisibility(spanRef, { threshold: 0 })
  const hasBeenInView = ref(false)

  const stopIsInViewWatcher = watch(
    isInView,
    (isVisible) => {
      if (isVisible && !hasBeenInView.value) {
        hasBeenInView.value = true
        transitionValue.value = props.direction === 'down' ? 0 : props.value
        stopIsInViewWatcher()
      }
    },
    { immediate: true },
  )

  watch(
    () => props.value,
    (newVal) => {
      if (hasBeenInView.value) {
        transitionValue.value = props.direction === 'down' ? 0 : newVal
      }
    },
  )
}
</script>

<template>
  <span
    ref="spanRef"
    :class="cn('inline-block tracking-wider tabular-nums', props.class)"
  >
    {{ output }}
  </span>
</template>
