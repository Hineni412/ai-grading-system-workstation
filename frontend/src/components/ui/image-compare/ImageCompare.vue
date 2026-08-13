<!--
  来源：Inspira UI · Compare（原型 .worktrees/ui-component-proposals .../prototype/inspira/Compare.vue）
  生产化：只保留复核场景需要的 drag 模式（去掉 autoplay/hover 模式与 rAF 循环）；
  拖动时同步更新滑块位置，jsdom（无布局、getBoundingClientRect 全为 0）下保持初始终态，
  不转圈不报错；颜色全部走语义令牌。
-->
<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'
import { ref, useTemplateRef, watch } from 'vue'

interface Props {
  firstImage?: string
  secondImage?: string
  firstImageAlt?: string
  secondImageAlt?: string
  class?: HTMLAttributes['class']
  initialSliderPercentage?: number
}

const props = withDefaults(defineProps<Props>(), {
  firstImage: '',
  secondImage: '',
  firstImageAlt: '对比前图片',
  secondImageAlt: '对比后图片',
  class: '',
  initialSliderPercentage: 50,
})

const emit = defineEmits<{
  (e: 'update:percentage', value: number): void
}>()

const sliderRef = useTemplateRef('sliderRef')
const sliderXPercent = ref(props.initialSliderPercentage)
const isDragging = ref(false)

function clampPercentage(value: number): number {
  return Math.max(0, Math.min(100, value))
}

function setPercentage(value: number): void {
  const next = clampPercentage(value)
  if (next === sliderXPercent.value) return
  sliderXPercent.value = next
  emit('update:percentage', next)
}

function handleMove(clientX: number): void {
  const slider = sliderRef.value
  if (!slider) return
  const rect = slider.getBoundingClientRect()
  // jsdom 等无布局环境宽度为 0：保持当前位置（终态），不产生 NaN
  if (!rect.width) return
  setPercentage(((clientX - rect.left) / rect.width) * 100)
}

function handlePointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  isDragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  handleMove(event.clientX)
}

function handlePointerMove(event: PointerEvent): void {
  if (!isDragging.value) return
  handleMove(event.clientX)
}

function handlePointerUp(event: PointerEvent): void {
  if (!isDragging.value) return
  isDragging.value = false
  const slider = event.currentTarget as HTMLElement
  if (slider.hasPointerCapture?.(event.pointerId)) {
    slider.releasePointerCapture?.(event.pointerId)
  }
}

function handleKeydown(event: KeyboardEvent): void {
  const step = event.shiftKey ? 10 : 2
  if (event.key === 'ArrowLeft') {
    setPercentage(sliderXPercent.value - step)
  } else if (event.key === 'ArrowRight') {
    setPercentage(sliderXPercent.value + step)
  } else {
    return
  }
  event.preventDefault()
}

watch(
  () => props.initialSliderPercentage,
  (newValue) => {
    sliderXPercent.value = newValue
  },
)
</script>

<template>
  <div
    ref="sliderRef"
    :class="cn('image-compare relative h-72 w-full overflow-hidden rounded-lg', props.class)"
    :style="{ cursor: isDragging ? 'grabbing' : 'grab' }"
    role="slider"
    aria-label="拖动对比两张图片"
    :aria-valuenow="Math.round(sliderXPercent)"
    aria-valuemin="0"
    aria-valuemax="100"
    tabindex="0"
    @pointerdown="handlePointerDown"
    @pointermove="handlePointerMove"
    @pointerup="handlePointerUp"
    @pointercancel="handlePointerUp"
    @keydown="handleKeydown"
  >
    <!-- 滑杆与手柄 -->
    <div
      class="via-primary pointer-events-none absolute top-0 z-30 m-auto h-full w-px bg-gradient-to-b from-transparent from-5% to-transparent to-95%"
      :style="{ left: `${sliderXPercent}%` }"
    >
      <div
        class="from-primary/40 absolute top-1/2 left-0 z-20 h-full w-36 -translate-y-1/2 bg-gradient-to-r via-transparent to-transparent opacity-50 [mask-image:radial-gradient(100px_at_left,white,transparent)]"
      />
      <div
        class="bg-card pointer-events-auto absolute top-1/2 -right-2.5 z-30 flex size-5 -translate-y-1/2 cursor-grab items-center justify-center rounded-md border shadow-sm"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke-width="1.5"
          stroke="currentColor"
          class="text-foreground size-4"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M8.25 15 4.5 12l3.75-3m7.5 6 3.75-3-3.75-3"
          />
        </svg>
      </div>
    </div>

    <!-- 左层（滑块左侧可见） -->
    <div class="relative z-20 size-full overflow-hidden" :style="{ pointerEvents: isDragging ? 'none' : 'auto' }">
      <div
        class="absolute inset-0 z-20 h-full w-full shrink-0 overflow-hidden rounded-lg select-none"
        :style="{ clipPath: `inset(0 ${100 - sliderXPercent}% 0 0)` }"
      >
        <slot name="first-content">
          <img
            v-if="props.firstImage"
            :alt="props.firstImageAlt"
            :src="props.firstImage"
            :draggable="false"
            class="absolute inset-0 z-20 h-full w-full shrink-0 rounded-lg object-contain select-none"
          >
        </slot>
      </div>
    </div>

    <!-- 底层（滑块右侧可见） -->
    <div
      class="absolute top-0 left-0 z-10 h-full w-full rounded-lg select-none"
      :style="{ pointerEvents: isDragging ? 'none' : 'auto' }"
    >
      <slot name="second-content">
        <img
          v-if="props.secondImage"
          :alt="props.secondImageAlt"
          :src="props.secondImage"
          :draggable="false"
          class="h-full w-full object-contain"
        >
      </slot>
    </div>
  </div>
</template>
