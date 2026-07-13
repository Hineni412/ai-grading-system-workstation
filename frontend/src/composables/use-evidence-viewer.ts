import { computed, ref } from 'vue'

export type EvidenceSource = 'crop' | 'original_front' | 'original_back'
export type ViewerMode = 'fit-width' | 'manual'
export type ViewerLoadState = 'idle' | 'loading' | 'ready' | 'error'
export interface ViewerPoint { x: number; y: number }
export interface ViewerSize { width: number; height: number }

const MIN_SCALE = 0.1
const MAX_SCALE = 4
const FIT_INSET = 48
const VISIBLE_EDGE = 48
const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value))

export function useEvidenceViewer() {
  const recordKey = ref('')
  const source = ref<EvidenceSource>('crop')
  const mode = ref<ViewerMode>('fit-width')
  const loadState = ref<ViewerLoadState>('idle')
  const rotation = ref(0)
  const scale = ref(1)
  const pan = ref<ViewerPoint>({ x: 0, y: 0 })
  const canvas = ref<ViewerSize>({ width: 0, height: 0 })
  const image = ref<ViewerSize>({ width: 0, height: 0 })
  const imageKey = ref(0)
  let loadGeneration = 0

  const rotatedSize = computed<ViewerSize>(() =>
    Math.abs(rotation.value) % 180 === 90
      ? { width: image.value.height, height: image.value.width }
      : image.value,
  )
  const transform = computed(() =>
    `translate(-50%, -50%) translate3d(${pan.value.x}px, ${pan.value.y}px, 0) rotate(${rotation.value}deg) scale(${scale.value})`,
  )

  function boundPan(next = pan.value): ViewerPoint {
    const renderedWidth = rotatedSize.value.width * scale.value
    const renderedHeight = rotatedSize.value.height * scale.value
    const maxX = Math.max(0, (renderedWidth - canvas.value.width) / 2 + VISIBLE_EDGE)
    const maxY = Math.max(0, (renderedHeight - canvas.value.height) / 2 + VISIBLE_EDGE)
    return {
      x: maxX === 0 ? 0 : clamp(next.x, -maxX, maxX),
      y: maxY === 0 ? 0 : clamp(next.y, -maxY, maxY),
    }
  }

  function fitWidth(): void {
    mode.value = 'fit-width'
    const available = Math.max(1, canvas.value.width - FIT_INSET)
    scale.value = clamp(available / Math.max(1, rotatedSize.value.width), MIN_SCALE, MAX_SCALE)
    pan.value = boundPan({ x: 0, y: 0 })
  }

  function setActualSize(): void {
    mode.value = 'manual'
    scale.value = 1
    pan.value = boundPan({ x: 0, y: 0 })
  }

  function zoomBy(delta: number, anchor: ViewerPoint = { x: 0, y: 0 }): void {
    mode.value = 'manual'
    const previous = scale.value
    const next = clamp(previous + delta, MIN_SCALE, MAX_SCALE)
    const ratio = next / previous
    scale.value = next
    pan.value = boundPan({
      x: anchor.x - (anchor.x - pan.value.x) * ratio,
      y: anchor.y - (anchor.y - pan.value.y) * ratio,
    })
  }

  function rotateBy(delta: -90 | 90): void {
    rotation.value = (rotation.value + delta + 360) % 360
    if (mode.value === 'fit-width') fitWidth()
    else pan.value = boundPan()
  }

  function panBy(delta: ViewerPoint): void {
    pan.value = boundPan({ x: pan.value.x + delta.x, y: pan.value.y + delta.y })
  }

  function resetView(): void {
    rotation.value = 0
    pan.value = { x: 0, y: 0 }
    mode.value = 'fit-width'
    if (image.value.width > 0) fitWidth()
  }

  function resetForRecord(nextKey: string): void {
    recordKey.value = nextKey
    source.value = 'crop'
    image.value = { width: 0, height: 0 }
    loadState.value = 'idle'
    imageKey.value += 1
    resetView()
  }

  function selectSource(next: EvidenceSource): void {
    if (source.value === next) return
    source.value = next
    image.value = { width: 0, height: 0 }
    imageKey.value += 1
    resetView()
  }

  function beginImageLoad(): number {
    loadState.value = 'loading'
    return ++loadGeneration
  }

  function acceptImage(generation: number, size: ViewerSize): boolean {
    if (generation !== loadGeneration) return false
    image.value = size
    loadState.value = 'ready'
    fitWidth()
    return true
  }

  function failImage(generation: number): boolean {
    if (generation !== loadGeneration) return false
    loadState.value = 'error'
    return true
  }

  function retry(): void {
    imageKey.value += 1
    loadState.value = 'loading'
  }

  function setCanvasSize(size: ViewerSize): void {
    canvas.value = size
    if (mode.value === 'fit-width' && image.value.width > 0) fitWidth()
    else pan.value = boundPan()
  }

  return {
    source, mode, loadState, rotation, scale, pan, imageKey, transform,
    resetForRecord, selectSource, beginImageLoad, acceptImage, failImage,
    retry, setCanvasSize, fitWidth, setActualSize, zoomBy, rotateBy, panBy,
  }
}
