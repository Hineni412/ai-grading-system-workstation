<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import type { ResultsCenterResponse } from '../../api/results-center'
import type { PageRole, RegionWorkspace } from '../../api/template-regions'
import {
  useEvidenceViewer,
  type EvidenceSource,
  type ViewerPoint,
} from '../../composables/use-evidence-viewer'
import PaperMarksLayer from '../results-center/PaperMarksLayer.vue'
import { loadSessionResults } from '../results-center/session-results-cache'
import { paperMarks, regionWorkspaceFor, type PaperMark } from '../results-center/student-paper'

const props = withDefaults(defineProps<{
  item: ReviewItemLike
  previousItem: ReviewItemLike | null
  nextItem: ReviewItemLike | null
  source: EvidenceSource
  expandable?: boolean
  marksVersion?: number
}>(), { expandable: true, marksVersion: 0 })

const emit = defineEmits<{ expand: [] }>()

const sourceLabels: Record<EvidenceSource, string> = {
  crop: '裁剪证据',
  original_front: '原卷正面',
  original_back: '原卷反面',
}

const viewer = useEvidenceViewer()
const canvasElement = ref<HTMLElement | null>(null)
const isDragging = ref(false)
const reviewItem = computed(() => resolveReviewItem(props.item))
const mediaUrls = computed<Record<EvidenceSource, string | null>>(() => ({
  crop: reviewItem.value.media.crop_url,
  original_front: reviewItem.value.media.original_front_url,
  original_back: reviewItem.value.media.original_back_url,
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
const isOriginalSource = computed(() =>
  viewer.source.value === 'original_front' || viewer.source.value === 'original_back',
)
const errorMessage = computed(() => {
  if (viewer.source.value === 'crop') return '裁剪图暂时无法读取。'
  return '原卷页暂时无法读取。'
})

const naturalSize = ref({ width: 0, height: 0 })
const marksVisible = ref(true)
const sessionResults = shallowRef<ResultsCenterResponse | null>(null)
const regionWorkspace = shallowRef<RegionWorkspace | null>(null)
let marksGeneration = 0

const pageMarks = computed<PaperMark[]>(() => {
  const results = sessionResults.value
  const workspace = regionWorkspace.value
  if (!results || !workspace) return []
  const student = results.students.find(
    (entry) => entry.student_id === reviewItem.value.student_id,
  )
  if (!student) return []
  const page: PageRole = viewer.source.value === 'original_back' ? 'back' : 'front'
  return paperMarks(
    workspace.formal_regions,
    workspace.template.pages,
    student.items,
  ).filter((mark) => mark.page === page)
})

watch(
  () => [
    reviewItem.value.session_id,
    reviewItem.value.student_id,
    props.marksVersion,
  ] as const,
  ([sessionId, studentId]) => {
    const generation = ++marksGeneration
    sessionResults.value = null
    regionWorkspace.value = null
    if (!sessionId || !studentId || reviewItem.value.result_id === null) return
    Promise.all([
      loadSessionResults(sessionId),
      regionWorkspaceFor(sessionId),
    ]).then(([results, workspace]) => {
      if (generation !== marksGeneration) return
      sessionResults.value = results
      regionWorkspace.value = workspace
    }).catch(() => undefined)
  },
  { immediate: true },
)

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
  if (reviewItem.value.media.originals_available === false) return
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
  naturalSize.value = {
    width: image.naturalWidth,
    height: image.naturalHeight,
  }
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
    <p v-if="reviewItem.media.originals_available === false" class="review-evidence-cleared" role="status">原卷已清理，分数和作答记录仍保留。</p>
    <div v-else class="review-evidence-toolbar" role="toolbar" aria-label="图片查看工具">
      <AppButton type="button" variant="ghost" size="small" @click="viewer.fitWidth">适应宽度</AppButton>
      <AppButton type="button" variant="ghost" size="small" @click="viewer.setActualSize">原比例</AppButton>
      <AppButton type="button" variant="ghost" size="small" @click="viewer.zoomBy(-0.25)">缩小</AppButton>
      <output aria-label="当前缩放比例">{{ scaleLabel }}</output>
      <AppButton type="button" variant="ghost" size="small" @click="viewer.zoomBy(0.25)">放大</AppButton>
      <AppButton type="button" variant="ghost" size="small" @click="viewer.rotateBy(-90)">向左旋转</AppButton>
      <AppButton type="button" variant="ghost" size="small" @click="viewer.rotateBy(90)">向右旋转</AppButton>
      <AppButton v-if="expandable !== false" type="button" variant="ghost" size="small" @click="emit('expand')">弹窗放大</AppButton>
      <AppButton
        v-if="isOriginalSource"
        type="button"
        variant="ghost"
        size="small"
        :aria-pressed="marksVisible"
        @click="marksVisible = !marksVisible"
      >批改标记</AppButton>
    </div>

    <div
      v-if="reviewItem.media.originals_available !== false"
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
      <div
        v-if="isOriginalSource"
        :key="imageRenderKey"
        class="review-evidence-page"
        :style="{
          width: `${naturalSize.width}px`,
          height: `${naturalSize.height}px`,
          transform: viewer.transform.value,
        }"
      >
        <img
          :src="sourceUrl ?? undefined"
          :data-load-key="imageRenderKey"
          :alt="`${item.student_name} 的${sourceLabel}`"
          :class="{ 'is-unavailable': viewer.loadState.value === 'error' }"
          draggable="false"
          decoding="async"
          @load="onImageLoad"
          @error="onImageError"
        >
        <PaperMarksLayer
          v-if="marksVisible && pageMarks.length"
          :marks="pageMarks"
          :selected-item-key="reviewItem.review_item_id"
          :interactive="false"
        />
      </div>
      <img
        v-else
        :key="imageRenderKey"
        :src="sourceUrl ?? undefined"
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
        <AppButton type="button" variant="ghost" size="small" @click.stop="retryImage">重新加载</AppButton>
      </div>
    </div>

    
  </section>
</template>

<style scoped>
.review-evidence-cleared { display: grid; place-items: center; min-height: 260px; margin: 0; padding: 20px; color: var(--color-text-muted); background: var(--color-bg-subtle); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-panel); text-align: center; }
</style>
