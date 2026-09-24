<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import {
  useEvidenceViewer,
  type EvidenceSource,
  type ViewerPoint,
} from '../../composables/use-evidence-viewer'

const props = withDefaults(defineProps<{
  item: ReviewItemLike
  previousItem: ReviewItemLike | null
  nextItem: ReviewItemLike | null
  source: EvidenceSource
  expandable?: boolean
}>(), { expandable: true })

const emit = defineEmits<{ expand: [] }>()

const sourceLabels: Record<EvidenceSource, string> = {
  crop: '裁剪证据',
  original_front: '原卷正面',
  original_back: '原卷反面',
  annotated_front: '标注正面',
  annotated_back: '标注反面',
}

const viewer = useEvidenceViewer()
const canvasElement = ref<HTMLElement | null>(null)
const isDragging = ref(false)
const reviewItem = computed(() => resolveReviewItem(props.item))
const mediaUrls = computed<Record<EvidenceSource, string | null>>(() => ({
  crop: reviewItem.value.media.crop_url,
  original_front: reviewItem.value.media.original_front_url,
  original_back: reviewItem.value.media.original_back_url,
  annotated_front: reviewItem.value.media.annotated_front_url,
  annotated_back: reviewItem.value.media.annotated_back_url,
}))
const effectiveSource = computed<EvidenceSource>(() =>
  mediaUrls.value[props.source] !== null ? props.source : 'crop',
)
const sourceUrl = computed(() =>
  mediaUrls.value[viewer.source.value] ?? reviewItem.value.media.crop_url,
)
const recordKey = computed(() => reviewItem.value.review_item_id)
const sourceLabel = computed(() => sourceLabels[viewer.source.value])
const imageRenderKey = computed(() =>
  `${recordKey.value}:${viewer.source.value}:${viewer.imageKey.value}`,
)
const scaleLabel = computed(() => `${Math.round(viewer.scale.value * 100)}%`)
const errorMessage = computed(() => {
  if (viewer.source.value === 'crop') return '裁剪图暂时无法读取。'
  if (viewer.source.value.startsWith('annotated')) return '标注图暂时无法读取。'
  return '原卷页暂时无法读取。'
})

let resizeObserver: ResizeObserver | null = null
let activeLoad = { key: '', generation: 0 }
let activePointerId: number | null = null
let lastPointer: ViewerPoint = { x: 0, y: 0 }
let pointerTravel = 0
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
  pointerTravel = 0
  isDragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  if (!isDragging.value || event.pointerId !== activePointerId) return
  const next = pointerPosition(event)
  pointerTravel += Math.abs(next.x - lastPointer.x) + Math.abs(next.y - lastPointer.y)
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

function onCanvasKeydown(event: KeyboardEvent): void {
  if (event.target !== event.currentTarget) return
  let handled = true
  switch (event.key.toLocaleLowerCase()) {
    case 'enter':
      if (props.expandable !== false) emit('expand')
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
  viewer.selectSource(effectiveSource.value)
}, { immediate: true, flush: 'sync' })
watch(effectiveSource, (source) => {
  releaseDragging()
  viewer.selectSource(source)
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
  releaseDragging()
  resizeObserver?.disconnect()
  clearPreloads()
})
</script>

<template>
  <section class="review-evidence-viewer" aria-label="答卷证据查看器">
    <div class="review-evidence-toolbar" role="toolbar" aria-label="图片查看工具">
      <button type="button" @click="viewer.fitWidth">适应宽度</button>
      <button type="button" @click="viewer.setActualSize">原比例</button>
      <button type="button" @click="viewer.zoomBy(-0.25)">缩小</button>
      <output aria-label="当前缩放比例">{{ scaleLabel }}</output>
      <button type="button" @click="viewer.zoomBy(0.25)">放大</button>
      <button type="button" @click="viewer.rotateBy(-90)">向左旋转</button>
      <button type="button" @click="viewer.rotateBy(90)">向右旋转</button>
      <button v-if="expandable !== false" type="button" @click="emit('expand')">弹窗放大</button>
    </div>

    <div
      ref="canvasElement"
      class="review-evidence-canvas"
      :class="{ 'is-dragging': isDragging }"
      tabindex="0"
      aria-label="答卷图片画布"
      @keydown="onCanvasKeydown"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="stopDragging"
      @pointercancel="stopDragging"
      @lostpointercapture="stopDragging"
      @click="expandable !== false && pointerTravel < 5 && emit('expand')"
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

    <p class="review-evidence-help">可拖动图片或用方向键移动；使用上方工具缩放和旋转<span v-if="expandable !== false">，点击图片可弹窗放大</span>。</p>
  </section>
</template>
