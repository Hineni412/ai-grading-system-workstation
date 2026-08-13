<!--
  环形进度（来源：Inspira UI · Animated Circular Progress Bar，生产化适配）。
  挂载后从 0 过渡到目标值一次；之后 value 变化经 CSS transition 平滑过渡。
  jsdom 等无 IntersectionObserver / requestAnimationFrame 的环境（或 duration 为 0）
  直接渲染终值，不进入动画流程。
-->
<script setup lang="ts">
import { cn } from '@/lib/utils'
import { computed, onMounted, ref, watch } from 'vue'

interface Props {
  max?: number
  value?: number
  min?: number
  gaugePrimaryColor?: string
  gaugeSecondaryColor?: string
  class?: string
  circleStrokeWidth?: number
  showPercentage?: boolean
  duration?: number
}

const props = withDefaults(defineProps<Props>(), {
  max: 100,
  min: 0,
  value: 0,
  gaugePrimaryColor: 'var(--primary)',
  gaugeSecondaryColor: 'var(--border)',
  circleStrokeWidth: 10,
  showPercentage: true,
  duration: 1,
})

const circumference = 2 * Math.PI * 45
const percentPx = circumference / 100

const currentPercent = computed(() => ((props.value - props.min) / (props.max - props.min)) * 100)
const percentageInPx = computed(() => `${percentPx}px`)
const durationInSeconds = computed(() => `${props.duration}s`)

// 测试环境（jsdom）没有 IntersectionObserver：视为无动画环境，直接落终值。
const canAnimate =
  props.duration > 0
  && typeof window !== 'undefined'
  && typeof window.IntersectionObserver === 'function'
  && typeof window.requestAnimationFrame === 'function'

const displayedPercent = ref(canAnimate ? 0 : currentPercent.value)

onMounted(() => {
  if (!canAnimate) return
  // 首帧先渲染 0，下一帧再写到目标值，让 CSS transition 完整播放一次。
  requestAnimationFrame(() => {
    displayedPercent.value = currentPercent.value
  })
})

watch(currentPercent, (next) => {
  displayedPercent.value = next
})
</script>

<template>
  <div :class="cn('progress-circle-base relative size-32 text-xl font-semibold', props.class)">
    <svg
      fill="none"
      class="size-full"
      stroke-width="2"
      viewBox="0 0 100 100"
    >
      <circle
        v-if="displayedPercent <= 90 && displayedPercent >= 0"
        cx="50"
        cy="50"
        r="45"
        :stroke-width="circleStrokeWidth"
        stroke-dashoffset="0"
        stroke-linecap="round"
        stroke-linejoin="round"
        class="gauge-secondary-stroke opacity-100"
      />
      <circle
        cx="50"
        cy="50"
        r="45"
        :stroke-width="circleStrokeWidth"
        stroke-dashoffset="0"
        stroke-linecap="round"
        stroke-linejoin="round"
        class="gauge-primary-stroke opacity-100"
      />
    </svg>
    <span
      v-if="showPercentage"
      :data-current-value="displayedPercent"
      class="absolute inset-0 m-auto size-fit"
    >
      {{ Math.round(displayedPercent) }}
    </span>
  </div>
</template>

<style scoped lang="css">
.progress-circle-base {
  --circle-size: 100px;
  --circumference: v-bind(circumference);
  --percent-to-px: v-bind(percentageInPx);
  --gap-percent: 5;
  --offset-factor: 0;
  --percent-to-deg: 3.6deg;
  transform: translateZ(0);
}

.gauge-primary-stroke {
  stroke: v-bind(gaugePrimaryColor);
  --stroke-percent: v-bind(displayedPercent);
  stroke-dasharray: calc(var(--stroke-percent) * var(--percent-to-px)) var(--circumference);
  transition:
    v-bind(durationInSeconds) ease,
    stroke v-bind(durationInSeconds) ease;
  transition-property: stroke-dasharray, transform;
  transform: rotate(
    calc(-90deg + var(--gap-percent) * var(--offset-factor) * var(--percent-to-deg))
  );
  transform-origin: calc(var(--circle-size) / 2) calc(var(--circle-size) / 2);
}

.gauge-secondary-stroke {
  stroke: v-bind(gaugeSecondaryColor);
  --stroke-percent: 90 - v-bind(displayedPercent);
  --offset-factor-secondary: calc(1 - var(--offset-factor));
  stroke-dasharray: calc(var(--stroke-percent) * var(--percent-to-px)) var(--circumference);
  transform: rotate(
      calc(
        1turn - 90deg -
          (var(--gap-percent) * var(--percent-to-deg) * var(--offset-factor-secondary))
      )
    )
    scaleY(-1);
  transition: all v-bind(durationInSeconds) ease;
  transform-origin: calc(var(--circle-size) / 2) calc(var(--circle-size) / 2);
}
</style>
