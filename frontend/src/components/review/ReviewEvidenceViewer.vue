<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ReviewItem } from '../../api/review'
import { reviewShortcutBus } from '../../composables/review-shortcuts'
import {
  useEvidenceViewer,
  type EvidenceSource,
  type ViewerPoint,
} from '../../composables/use-evidence-viewer'

const props = defineProps<{
  item: ReviewItem
  previousItem: ReviewItem | null
  nextItem: ReviewItem | null
}>()

const sourceOptions = [
  { value: 'crop', label: '裁剪证据' },
  { value: 'original_front', label: '原卷正面' },
  { value: 'original_back', label: '原卷反面' },
] as const

const viewer = useEvidenceViewer()
const canvasElement = ref<HTMLElement | null>(null)
const isDragging = ref(false)
const sourceUrl = computed(() => ({
  crop: props.item.media.crop_url,
  original_front: props.item.media.original_front_url,
  original_back: props.item.media.original_back_url,
})[viewer.source.value])
const recordKey = computed(() => `${props.item.session_id}:${props.item.detail_id}`)
const sourceLabel = computed(() =>
  sourceOptions.find((option) => option.value === viewer.source.value)?.label ?? '裁剪证据',
)
const imageRenderKey = computed(() =>
  `${recordKey.value}:${viewer.source.value}:${viewer.imageKey.value}`,
)
const scaleLabel = computed(() => `${Math.round(viewer.scale.value * 100)}%`)
const errorMessage = computed(() =>
  viewer.source.value === 'crop'
    ? '裁剪图暂时无法读取。'
    : '原卷页暂时无法读取。',
)

let resizeObserver: ResizeObserver | null = null
let stopShortcuts = () => undefined
let activeLoad = { key: '', generation: 0 }
let activePointerId: number | null = null
let lastPointer: ViewerPoint = { x: 0, y: 0 }
let preloadImages: HTMLImageElement[] = []

function clearPreloads(): void {
  for (const image of preloadImages) {
    image.onload = null
    image.onerror = null
    image.src = ''
  }
  preloadImages = []
}

function refreshPreloads(): void {
  clearPreloads()
  const urls = [
    props.previousItem?.media.crop_url,
    props.nextItem?.media.crop_url,
  ].filter((url): url is string => Boolean(url) && url !== props.item.media.crop_url)

  for (const url of [...new Set(urls)].slice(0, 2)) {
    const image = new Image()
    image.onerror = () => undefined
    image.src = url
    preloadImages.push(image)
  }
}

function selectSource(source: EvidenceSource): void {
  releaseDragging()
  viewer.selectSource(source)
}

function onImageLoad(event: Event): void {
  const image = event.currentTarget as HTMLImageElement
  const key = image.dataset.loadKey ?? ''
  if (key !== activeLoad.key) return
  viewer.acceptImage(activeLoad.generation, {
    width: image.naturalWidth,
    height: image.naturalHeight,
  })
}

function onImageError(event: Event): void {
  const image = event.currentTarget as HTMLImageElement
  const key = image.dataset.loadKey ?? ''
  if (key !== activeLoad.key) return
  viewer.failImage(activeLoad.generation)
}

function retryImage(): void {
  viewer.retry()
}

function pointerPosition(event: PointerEvent): ViewerPoint {
  return { x: event.clientX, y: event.clientY }
}

function onPointerDown(event: PointerEvent): void {
  if (
    event.button !== 0 ||
    (event.target instanceof Element && event.target.closest('button'))
  ) return
  activePointerId = event.pointerId
  lastPointer = pointerPosition(event)
  isDragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  if (!isDragging.value || event.pointerId !== activePointerId) return
  const next = pointerPosition(event)
  viewer.panBy({ x: next.x - lastPointer.x, y: next.y - lastPointer.y })
  lastPointer = next
}

function stopDragging(event?: PointerEvent): void {
  if (event && activePointerId !== null && event.pointerId !== activePointerId) return
  releaseDragging()
}

function releaseDragging(): void {
  const canvas = canvasElement.value
  if (canvas && activePointerId !== null && canvas.hasPointerCapture?.(activePointerId)) {
    canvas.releasePointerCapture?.(activePointerId)
  }
  activePointerId = null
  isDragging.value = false
}

function onWheel(event: WheelEvent): void {
  const canvas = canvasElement.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const previousScale = viewer.scale.value
  viewer.zoomBy(event.deltaY < 0 ? 0.25 : -0.25, {
    x: event.clientX - (rect.left + rect.width / 2),
    y: event.clientY - (rect.top + rect.height / 2),
  })
  if (viewer.scale.value !== previousScale) event.preventDefault()
}

function onCanvasKeydown(event: KeyboardEvent): void {
  if (event.target !== event.currentTarget) return
  let handled = true
  switch (event.key.toLocaleLowerCase()) {
    case '+':
    case '=':
      viewer.zoomBy(0.25)
      break
    case '-':
      viewer.zoomBy(-0.25)
      break
    case '0':
      viewer.setActualSize()
      break
    case 'z':
      viewer.fitWidth()
      break
    case 'arrowleft':
      viewer.panBy({ x: -32, y: 0 })
      break
    case 'arrowright':
      viewer.panBy({ x: 32, y: 0 })
      break
    case 'arrowup':
      viewer.panBy({ x: 0, y: -32 })
      break
    case 'arrowdown':
      viewer.panBy({ x: 0, y: 32 })
      break
    default:
      handled = false
  }
  if (handled) event.preventDefault()
}

watch(recordKey, (key) => {
  releaseDragging()
  viewer.resetForRecord(key)
}, { immediate: true, flush: 'sync' })
watch(imageRenderKey, (key) => {
  activeLoad = { key, generation: viewer.beginImageLoad() }
}, { immediate: true, flush: 'sync' })
watch(
  () => [
    props.item.media.crop_url,
    props.previousItem?.media.crop_url,
    props.nextItem?.media.crop_url,
  ],
  refreshPreloads,
  { immediate: true },
)

onMounted(() => {
  stopShortcuts = reviewShortcutBus.subscribe((command) => {
    if (command === 'fit-width') viewer.fitWidth()
    else if (command === 'zoom-in') viewer.zoomBy(0.25)
    else if (command === 'zoom-out') viewer.zoomBy(-0.25)
  })
  const canvas = canvasElement.value
  if (!canvas) return
  const updateSize = (width: number, height: number) => {
    viewer.setCanvasSize({ width, height })
  }
  const initialRect = canvas.getBoundingClientRect()
  updateSize(initialRect.width, initialRect.height)
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) updateSize(rect.width, rect.height)
    })
    resizeObserver.observe(canvas)
  }
})

onBeforeUnmount(() => {
  stopShortcuts()
  releaseDragging()
  resizeObserver?.disconnect()
  clearPreloads()
})
</script>

<template>
  <section class="review-evidence-viewer" aria-label="答卷证据查看器">
    <div class="review-evidence-source" role="group" aria-label="证据来源">
      <button
        v-for="option in sourceOptions"
        :key="option.value"
        type="button"
        :aria-pressed="viewer.source.value === option.value"
        @click="selectSource(option.value)"
      >
        {{ option.label }}
      </button>
    </div>

    <div class="review-evidence-toolbar" role="toolbar" aria-label="图片查看工具">
      <button type="button" @click="viewer.fitWidth">适应宽度</button>
      <button type="button" @click="viewer.setActualSize">原比例</button>
      <button type="button" @click="viewer.zoomBy(-0.25)">缩小</button>
      <output aria-label="当前缩放比例">{{ scaleLabel }}</output>
      <button type="button" @click="viewer.zoomBy(0.25)">放大</button>
      <button type="button" @click="viewer.rotateBy(-90)">向左旋转</button>
      <button type="button" @click="viewer.rotateBy(90)">向右旋转</button>
    </div>

    <div
      ref="canvasElement"
      class="review-evidence-canvas"
      :class="{ 'is-dragging': isDragging }"
      tabindex="0"
      aria-label="答卷图片画布"
      @keydown="onCanvasKeydown"
      @wheel="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="stopDragging"
      @pointercancel="stopDragging"
      @lostpointercapture="stopDragging"
    >
      <img
        :key="imageRenderKey"
        :src="sourceUrl"
        :data-load-key="imageRenderKey"
        :alt="`${item.student_name} 的${sourceLabel}`"
        :style="{ transform: viewer.transform.value }"
        :class="{ 'is-unavailable': viewer.loadState.value === 'error' }"
        draggable="false"
        decoding="async"
        @load="onImageLoad"
        @error="onImageError"
      >
      <p v-if="viewer.loadState.value === 'loading'" class="review-evidence-state">
        正在读取图片…
      </p>
      <div v-else-if="viewer.loadState.value === 'error'" class="review-evidence-state" role="alert">
        <p>{{ errorMessage }}</p>
        <button type="button" @click.stop="retryImage">重新加载</button>
      </div>
    </div>

    <p class="review-evidence-help">画布聚焦后可用 Z、0、+、− 和方向键。</p>
  </section>
</template>
