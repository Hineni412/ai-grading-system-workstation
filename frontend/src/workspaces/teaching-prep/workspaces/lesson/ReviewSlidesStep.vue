<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import {
  teachingPrepCatalogApi,
  type PptxExecution,
  type ResourcePack,
  type SlideOperation,
  type SlideOperationDecision,
  type SlidePlan,
} from '../../api/catalog'
import { teachingPrepWorkbenchApi, type AdaptationTrace, type AdaptationTraceEvent, type AdaptationTraceFinding } from '../../api/workbench'
import { isAuthoritativeNotFoundError } from '../../../../api/errors'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../../aiAdoption'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'

const LOCAL_SLIDE_CONTRACT_FINGERPRINT = '34b984e97a114fea15394bfdb1c73e8f667b340918e72c21bc15a21934a20a1b'

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()
const decisions = reactive<Record<string, SlideOperationDecision>>({})
const reasons = reactive<Record<string, string>>({})
const teacherNotes = reactive<Record<string, string>>({})
const plannedMinutes = reactive<Record<string, number>>({})
const targetSlideNumbers = reactive<Record<string, number>>({})
const positions = reactive<Record<string, { x: number; y: number; width: number; height: number }>>({})
const textbookLabels = reactive<Record<string, string>>({})
const pageNotes = reactive<Record<number, string>>({})
const reviewMessage = ref('改编返回后会在隔离副本上生成本地改后页。满意再导出；某一页不满意就写一句意见重发。')
const adaptationTrace = ref<AdaptationTrace | null>(null)
let traceAbort: AbortController | null = null
let traceTimer: ReturnType<typeof setTimeout> | null = null
const activeOperationId = ref<string | null>(null)
const activeSlideIndex = ref(0)
const handledTaskRevisions = new Set<string>()
const autoReviewedPlanIds = new Set<string>()
const previewStartedPlanIds = new Set<string>()
const busy = ref(false)
const resending = ref(false)
const decisionSaving = ref(false)

const selectedPlan = computed(() => catalog.slidePlans.find(
  item => item.id === catalog.selectedSlidePlanId,
) ?? catalog.slidePlans[0] ?? null)
const preview = computed(() => catalog.slidePlanPreview)
const latestRun = computed(() => catalog.pptxExecutions[0] ?? null)
const pptxExecutionAvailable = computed(() => catalog.moduleStatus?.wps_execution_available === true)
const previewReady = computed(() => isPreviewReady(latestRun.value))
const publishedVersion = computed(() => (
  workbench.pptxVersions.value.find(item => item.is_current)
  ?? workbench.pptxVersions.value[0]
  ?? null
))
const afterSlideCount = computed(() => {
  if (previewReady.value) return latestRun.value?.expected_slide_count ?? 0
  if (latestRun.value?.status === 'published') {
    return publishedVersion.value?.slide_count ?? latestRun.value.expected_slide_count
  }
  return preview.value?.before_slide_count ?? 0
})
const currentSlideTask = computed(() => {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) return null
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.slide_change_proposal'
    && task.source_ref.kind === 'lesson'
    && task.source_ref.id === lessonId
    && task.status !== 'discarded'
  )) ?? null
})
const taskPresentation = computed(() => {
  const task = currentSlideTask.value
  if (!task) return { title: '还没有发送改编任务', detail: '返回上一步，确认主课件和参考资料后再发送。', tone: 'idle' }
  if (task.status === 'prepared' || task.status === 'queued') return { title: '改编任务正在排队', detail: '资料范围已经锁定，可以离开此页面；任务不会重复发送。', tone: 'running' }
  if (task.status === 'running') return { title: 'AI 正在逐页分析课件', detail: task.teacher_message || '完成后会自动生成本地改后页，供你对照。', tone: 'running' }
  if (task.status === 'proposal_ready') return { title: '改编建议已经返回', detail: '正在生成本地改后页，请稍候对照。', tone: 'ready' }
  if (task.status === 'needs_input') return { title: '需要教师确认后继续', detail: task.teacher_message || task.next_action, tone: 'attention' }
  if (['failed', 'failed_before_dispatch', 'invalid_result', 'result_unknown'].includes(task.status)) {
    return { title: '本次改编没有完成', detail: task.teacher_message || '没有改动原 PPT。返回确认资料后，可由教师明确重新发送。', tone: 'error' }
  }
  return { title: '正在恢复改编状态', detail: task.teacher_message || '请稍候。', tone: 'idle' }
})
const beforeSlide = computed(() => preview.value?.before[activeSlideIndex.value] ?? null)
const afterPreviewUrl = computed(() => {
  const page = activeSlideIndex.value + 1
  if (previewReady.value && latestRun.value) {
    return `/api/teaching-prep/pptx-executions/${latestRun.value.id}/preview?slide=${page}`
  }
  if (latestRun.value?.status === 'published' && publishedVersion.value) {
    return `${publishedVersion.value.preview_url}?slide=${page}`
  }
  return null
})
const pageOperations = computed(() => (
  selectedPlan.value?.payload.operations.filter(item => slideIndexFor(item) === activeSlideIndex.value) ?? []
))
const pageChangeOperations = computed(() => (
  pageOperations.value.filter(item => item.kind !== 'keep_slide')
))
const plannedAfterOperations = computed(() => (
  pageChangeOperations.value.filter(item => operationDecision(item) !== 'rejected')
))
const pageChangeMap = computed(() => {
  const map = new Map<number, Set<ChangeCategory>>()
  for (const item of selectedPlan.value?.payload.operations ?? []) {
    if (item.kind === 'keep_slide') continue
    if (operationDecision(item) === 'rejected') continue
    const index = slideIndexFor(item)
    if (index < 0) continue
    const categories = map.get(index) ?? new Set<ChangeCategory>()
    categories.add(operationCategory(item))
    map.set(index, categories)
  }
  return map
})
const reviewSummary = computed(() => {
  const plan = selectedPlan.value
  if (!plan) return null
  const counts: Record<ChangeCategory, number> = { delete: 0, insert: 0, adjust: 0, manual: 0 }
  for (const item of plan.payload.operations) {
    if (item.kind === 'keep_slide') continue
    if (operationDecision(item) === 'rejected') continue
    counts[operationCategory(item)] += 1
  }
  const parts: string[] = []
  if (counts.delete) parts.push(`删 ${counts.delete} 处`)
  if (counts.insert) parts.push(`插 ${counts.insert} 题`)
  if (counts.adjust) parts.push(`调 ${counts.adjust} 处`)
  if (counts.manual) parts.push(`需人工 ${counts.manual} 处`)
  return { changedPages: pageChangeMap.value.size, parts }
})
const showOnlyChanged = ref(false)
const visibleSlideIndices = computed(() => {
  const all = Array.from({ length: afterSlideCount.value }, (_, index) => index)
  if (!showOnlyChanged.value) return all
  return all.filter(index => pageChangeMap.value.has(index))
})

interface ReviewFindingView {
  finding: string
  category: string
  suggestedAction: string | null
  pages: number[]
}

const slideRefPages = computed(() => {
  const map = new Map<string, number>()
  for (const slide of selectedPlan.value?.payload.slides ?? []) {
    const linkId = String(slide.source_link_id ?? '')
    const page = Number(slide.original_index)
    if (linkId && Number.isFinite(page)) {
      map.set(`material:${linkId}:unit:${page}`, page)
    }
  }
  return map
})

const reviewFindings = computed<ReviewFindingView[]>(() => (
  (selectedPlan.value?.payload.review_findings ?? []).map(item => ({
    finding: item.finding,
    category: item.category,
    suggestedAction: item.suggested_action?.trim() || null,
    pages: [...new Set(
      item.slide_refs
        .map(ref => slideRefPages.value.get(ref))
        .filter((page): page is number => typeof page === 'number'),
    )].sort((a, b) => a - b),
  }))
))

const FINDINGS_COLLAPSED_COUNT = 3
const findingsExpanded = ref(false)
const visibleReviewFindings = computed(() => (
  findingsExpanded.value
    ? reviewFindings.value
    : reviewFindings.value.slice(0, FINDINGS_COLLAPSED_COUNT)
))

function jumpToFindingPage(page: number): void {
  const before = preview.value?.before ?? []
  let index = before.findIndex(item => Number(item.original_index) === page)
  if (index < 0) index = Math.min(Math.max(page - 1, 0), Math.max(afterSlideCount.value - 1, 0))
  if (!visibleSlideIndices.value.includes(index)) showOnlyChanged.value = false
  activeSlideIndex.value = index
}
const afterStatusLabel = computed(() => {
  if (previewReady.value || latestRun.value?.status === 'published') return '已渲染副本'
  if (previewBusy.value) return '模拟改后 · 示意（真实渲染生成中…）'
  return '模拟改后 · 示意'
})

function pageCategories(index: number): ChangeCategory[] {
  return [...(pageChangeMap.value.get(index) ?? [])]
}

const CHANGE_TAG_LABELS: Record<ChangeCategory, string> = {
  delete: '删',
  insert: '插',
  adjust: '调',
  manual: '人工',
}

const beforeLoadFailed = ref(false)
const beforeRetryTick = ref(0)
const requestedRenderUnitIds = new Set<string>()
let beforeRetryTimer: ReturnType<typeof setTimeout> | null = null

const beforeUnitId = computed(() => {
  const value = beforeSlide.value?.material_unit_id
  return typeof value === 'string' ? value : ''
})
const beforeImageSrc = computed(() => {
  const raw = beforeSlide.value?.preview_url
  if (typeof raw !== 'string' || !raw.trim()) return null
  const summary = (beforeSlide.value?.object_summary ?? {}) as Record<string, unknown>
  const params = [`retry=${beforeRetryTick.value}`]
  const kind = String(summary.preview_kind ?? '')
  const compositor = String(summary.preview_compositor ?? '')
  if (kind) params.push(`kind=${encodeURIComponent(kind)}`)
  if (compositor) params.push(`compose=${encodeURIComponent(compositor)}`)
  const separator = raw.includes('?') ? '&' : '?'
  return `${raw}${separator}${params.join('&')}`
})

function scheduleBeforeRetry(): void {
  if (beforeRetryTimer !== null) clearTimeout(beforeRetryTimer)
  beforeRetryTimer = setTimeout(() => {
    beforeRetryTimer = null
    beforeRetryTick.value += 1
    beforeLoadFailed.value = false
  }, 1500)
}

function requestBeforeRender(): void {
  const unitId = beforeUnitId.value
  if (!unitId || requestedRenderUnitIds.has(unitId)) return
  const summary = (beforeSlide.value?.object_summary ?? {}) as Record<string, unknown>
  if (!('preview_kind' in summary)) return
  requestedRenderUnitIds.add(unitId)
  void teachingPrepCatalogApi.requestPptPreviewRender(unitId)
    .then(() => { scheduleBeforeRetry() })
    .catch(() => { requestedRenderUnitIds.delete(unitId) })
}

function onBeforeImageError(): void {
  beforeLoadFailed.value = true
  requestBeforeRender()
}

function retryBeforeImage(): void {
  beforeLoadFailed.value = false
  beforeRetryTick.value += 1
  requestBeforeRender()
}

watch(beforeUnitId, () => {
  beforeLoadFailed.value = false
  const summary = (beforeSlide.value?.object_summary ?? {}) as Record<string, unknown>
  if (String(summary.preview_kind ?? '') === 'structural') requestBeforeRender()
})

type SimulatedOverlayTone = 'delete' | 'insert' | 'note'

function simulatedToneFor(item: SlideOperation): SimulatedOverlayTone | null {
  if (item.kind === 'delete_shape' || item.kind === 'delete_slide') return 'delete'
  if (item.kind === 'insert_static_image') return 'insert'
  if (item.kind === 'add_text_box') return 'note'
  return null
}

interface NormalizedPosition {
  x: number
  y: number
  width: number
  height: number
}

function normalizedPositionFor(item: SlideOperation): NormalizedPosition | null {
  const edited = positions[item.operation_id]
  const raw = edited ?? (item.target.position as Record<string, unknown> | null | undefined)
  if (!raw || typeof raw !== 'object') return null
  const values = [raw.x, raw.y, raw.width, raw.height].map(value => Number(value))
  if (values.some(value => !Number.isFinite(value))) return null
  const [rawX, rawY, rawWidth, rawHeight] = values as [number, number, number, number]
  const x = Math.min(Math.max(rawX, 0), 1)
  const y = Math.min(Math.max(rawY, 0), 1)
  const width = Math.min(Math.max(rawWidth, 0), 1 - x)
  const height = Math.min(Math.max(rawHeight, 0), 1 - y)
  if (width <= 0 || height <= 0) return null
  return { x, y, width, height }
}

interface SimulatedOverlayBox {
  key: string
  tone: SimulatedOverlayTone
  tag: string
  text: string | null
  imageUrl: string | null
  alt: string
  style: { left: string; top: string; width: string; height: string }
}

const simulatedOverlayBoxes = computed<SimulatedOverlayBox[]>(() => (
  plannedAfterOperations.value.flatMap((item) => {
    const tone = simulatedToneFor(item)
    if (!tone) return []
    const position = normalizedPositionFor(item)
    if (!position) return []
    const style = {
      left: `${position.x * 100}%`,
      top: `${position.y * 100}%`,
      width: `${position.width * 100}%`,
      height: `${position.height * 100}%`,
    }
    const base = { key: item.operation_id, tone, style, alt: cardTitle(item) }
    if (tone === 'insert') {
      const asset = item.details.asset_ref
      const imageUrl = typeof asset === 'string' && asset.trim() ? asset : null
      return [{ ...base, tag: '插', text: imageUrl ? null : cardTitle(item), imageUrl }]
    }
    if (tone === 'note') {
      const text = String(item.details.text ?? item.target.content_summary ?? '').trim()
      return [{ ...base, tag: '注', text: text || operationLabel(item.kind), imageUrl: null }]
    }
    return [{ ...base, tag: '删', text: null, imageUrl: null }]
  })
))

const simulatedPageLevelOperations = computed(() => {
  const drawn = new Set(simulatedOverlayBoxes.value.map(box => box.key))
  return plannedAfterOperations.value.filter(item => !drawn.has(item.operation_id))
})

const showSimulatedAfter = computed(() => afterPreviewUrl.value === null)

const simViewportEl = ref<HTMLElement | null>(null)
const simViewportSize = ref<{ width: number; height: number } | null>(null)
const simBaseNatural = ref<{ width: number; height: number } | null>(null)
let simResizeObserver: ResizeObserver | null = null

const simCanvasStyle = computed(() => {
  const viewport = simViewportSize.value
  const natural = simBaseNatural.value
  if (!viewport || !natural || viewport.width <= 0 || viewport.height <= 0) {
    return { width: '100%', height: '100%' }
  }
  const scale = Math.min(viewport.width / natural.width, viewport.height / natural.height)
  return {
    width: `${Math.round(natural.width * scale)}px`,
    height: `${Math.round(natural.height * scale)}px`,
  }
})

function onSimBaseLoad(event: Event): void {
  const image = event.target as HTMLImageElement
  if (image.naturalWidth > 0 && image.naturalHeight > 0) {
    simBaseNatural.value = { width: image.naturalWidth, height: image.naturalHeight }
  }
}

watch(simViewportEl, (element) => {
  simResizeObserver?.disconnect()
  simResizeObserver = null
  simViewportSize.value = null
  if (!element || typeof ResizeObserver === 'undefined') return
  const observer = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect
    if (rect && rect.width > 0 && rect.height > 0) {
      simViewportSize.value = { width: rect.width, height: rect.height }
    }
  })
  observer.observe(element)
  simResizeObserver = observer
})

watch(beforeImageSrc, () => {
  simBaseNatural.value = null
})
const currentPageNote = computed({
  get: () => pageNotes[activeSlideIndex.value + 1] ?? '',
  set: (value: string) => {
    pageNotes[activeSlideIndex.value + 1] = value
    workbench.setDirty('对照页意见')
  },
})
const hasPageNotes = computed(() => Object.values(pageNotes).some(item => item.trim()))
const previewBusy = computed(() => {
  const run = latestRun.value
  if (!run) return false
  if (run.status === 'running') return true
  return run.status === 'verifying' && !isPreviewReady(run)
})
const canConfirmExport = computed(() => (
  previewReady.value || latestRun.value?.status === 'published'
))
const waitingForAdaptation = computed(() => (
  !selectedPlan.value && currentSlideTask.value != null
  && ['prepared', 'queued', 'running'].includes(currentSlideTask.value.status)
))
const traceRounds = computed(() => {
  const trace = adaptationTrace.value
  if (!trace) return null
  return {
    used: trace.model_calls_used,
    max: trace.model_calls_max,
  }
})

interface TraceThumb {
  key: string
  url: string
  label: string
}

interface TraceLine {
  key: string
  text: string
  tone: 'normal' | 'success' | 'warn' | 'error'
}

interface TraceFindingBlock {
  key: string
  items: AdaptationTraceFinding[]
}

interface TraceGroup {
  key: string
  header: string | null
  lines: TraceLine[]
  thinking: string | null
  thumbs: TraceThumb[]
  findingBlocks: TraceFindingBlock[]
}

const FINDING_CATEGORY_LABELS: Record<string, string> = {
  content: '内容',
  sequence: '顺序',
  practice_load: '练习量',
  alignment: '教材对应',
  other: '其他',
}

function findingCategoryLabel(category: string): string {
  return FINDING_CATEGORY_LABELS[category] ?? '其他'
}

function findingCategoryTone(category: string): string {
  return category in FINDING_CATEGORY_LABELS ? category : 'other'
}

const traceGroups = computed<TraceGroup[]>(() => {
  const events = adaptationTrace.value?.events ?? []
  const buckets = new Map<string, { round: number, ending: boolean, events: AdaptationTraceEvent[] }>()
  for (const event of events) {
    const ending = event.phase === 'final_accepted' || event.phase === 'failed'
    const key = ending ? 'final' : `round-${event.round}`
    const bucket = buckets.get(key) ?? { round: event.round, ending, events: [] }
    bucket.events.push(event)
    buckets.set(key, bucket)
  }
  return [...buckets.entries()].map(([key, bucket]) => {
    const lines: TraceLine[] = []
    const thinking: string[] = []
    const thumbs: TraceThumb[] = []
    const findingBlocks: TraceFindingBlock[] = []
    let doneSummary: string | null = null
    bucket.events.forEach((event, index) => {
      if (event.result?.ok && event.result.preview_url) {
        thumbs.push({ key: `${key}-thumb-${index}`, url: event.result.preview_url, label: event.result.label })
      }
      if (event.thinking_excerpt) thinking.push(event.thinking_excerpt)
      const line = (text: string, tone: TraceLine['tone'] = 'normal') => {
        lines.push({ key: `${key}-line-${index}`, text, tone })
      }
      switch (event.phase) {
        case 'round_done':
          doneSummary = event.summary
          break
        case 'findings_ready':
          if (event.findings?.length) {
            findingBlocks.push({ key: `${key}-findings-${index}`, items: event.findings })
          } else {
            line(event.summary)
          }
          break
        case 'tool_call': {
          const next = bucket.events[index + 1]
          if (next?.phase === 'tool_result') {
            const failedFetch = next.result?.ok === false
            line(`${event.summary} ${failedFetch ? '✗' : '✓'}`, failedFetch ? 'error' : 'normal')
          } else {
            line(`${event.summary} …`)
          }
          break
        }
        case 'tool_result': {
          if (bucket.events[index - 1]?.phase === 'tool_call') break
          line(event.summary, event.result?.ok === false ? 'error' : 'normal')
          break
        }
        case 'round_retry':
          line(event.summary, 'warn')
          break
        case 'final_accepted':
          line(event.summary, 'success')
          break
        case 'failed':
          line(event.summary, 'error')
          break
        case 'thinking':
          break
        default:
          line(event.summary)
      }
    })
    const header = bucket.ending
      ? null
      : doneSummary ?? (bucket.round > 0 ? `第 ${bucket.round} 轮 · 进行中` : '开始')
    return { key, header, lines, thinking: thinking.join('\n') || null, thumbs, findingBlocks }
  })
})

const expandedThinkingGroups = ref<string[]>([])
const activeTraceThumb = ref<TraceThumb | null>(null)
const traceThumbDialog = ref<HTMLDialogElement | null>(null)
const waitElapsedSeconds = ref(0)
let waitTimer: ReturnType<typeof setInterval> | null = null
let waitStartedAt: number | null = null

const traceHasEnding = computed(() => Boolean(
  adaptationTrace.value?.events.some(event => event.phase === 'final_accepted' || event.phase === 'failed'),
))
const showWaitHint = computed(() => (
  currentSlideTask.value != null
  && ['prepared', 'queued', 'running'].includes(currentSlideTask.value.status)
  && !traceHasEnding.value
))

function toggleTraceThinking(key: string): void {
  expandedThinkingGroups.value = expandedThinkingGroups.value.includes(key)
    ? expandedThinkingGroups.value.filter(item => item !== key)
    : [...expandedThinkingGroups.value, key]
}

function openTraceThumb(thumb: TraceThumb): void {
  activeTraceThumb.value = thumb
  void nextTick(() => traceThumbDialog.value?.showModal?.())
}

function closeTraceThumb(): void {
  traceThumbDialog.value?.close?.()
  activeTraceThumb.value = null
}

function formatWaitElapsed(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`
}

function stopWaitTimer(): void {
  if (waitTimer !== null) {
    clearInterval(waitTimer)
    waitTimer = null
  }
  waitStartedAt = null
  waitElapsedSeconds.value = 0
}

function startWaitTimer(): void {
  if (waitTimer !== null) return
  if (waitStartedAt === null) waitStartedAt = Date.now()
  waitElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - waitStartedAt) / 1000))
  waitTimer = setInterval(() => {
    if (waitStartedAt === null) return
    waitElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - waitStartedAt) / 1000))
  }, 1000)
}

function isPreviewReady(run: PptxExecution | null): boolean {
  return Boolean(
    run
    && run.status === 'verifying'
    && run.published_version_id === null
    && run.verification_report
    && Object.keys(run.verification_report).length > 0
  )
}

function describeSlide(item: Record<string, unknown>, index: number): string {
  return String(item.title ?? item.name ?? item.slide_ref ?? `第 ${index + 1} 页`)
}

const OPERATION_LABELS: Record<string, string> = {
  delete_slide: '删除整页',
  reorder_slide: '调整页序',
  delete_shape: '删除页内内容',
  add_text_box: '添加文本标注',
  add_slide: '新增页面',
  insert_static_image: '插入图片',
  move_static_image: '移动图片',
  scale_static_image: '缩放图片',
  crop_static_image: '裁剪图片',
  replace_static_image: '替换图片',
  keep_slide: '保留本页',
  hide_slide: '隐藏本页',
  copy_slide: '复制本页',
  modify_text_box: '修改文本',
  manual_note: '人工处理',
}

function operationLabel(kind: string): string {
  return OPERATION_LABELS[kind] ?? '页面调整'
}

type ChangeCategory = 'delete' | 'insert' | 'adjust' | 'manual'

function operationCategory(item: SlideOperation): ChangeCategory {
  if (item.execution_mode === 'manual_only') return 'manual'
  if (item.kind === 'delete_slide' || item.kind === 'delete_shape') return 'delete'
  if (item.kind === 'add_slide' || item.kind === 'insert_static_image') return 'insert'
  return 'adjust'
}

function defaultDecisionFor(item: SlideOperation): SlideOperationDecision {
  return item.execution_mode === 'manual_only' ? 'rejected' : 'approved'
}

function operationDecision(item: SlideOperation): SlideOperationDecision {
  const decision = decisions[item.operation_id] ?? item.decision
  return decision === 'proposed' ? defaultDecisionFor(item) : decision
}

function riskLabel(risk: SlideOperation['risk']): string {
  return {
    low: '低风险',
    medium: '中风险',
    high: '高风险',
    blocked: '需先解除阻塞',
  }[risk] ?? '风险未知'
}

function cardTitle(item: SlideOperation): string {
  const summary = String(item.target.content_summary ?? '').trim()
  if (summary) return summary
  const firstSentence = item.reason.split(/[。!?\n]/)[0]?.trim()
  return firstSentence || operationLabel(item.kind)
}

function operationSupplement(item: SlideOperation): string | null {
  if (item.support_note?.trim()) return item.support_note
  const summary = item.details.summary
  return typeof summary === 'string' && summary.trim() ? summary : null
}

function materialNameFor(linkId: string): string | null {
  const presentations = selectedPlan.value?.payload.source_presentations ?? []
  const match = presentations.find(item => String(item.link_id ?? '') === linkId)
  const name = match?.material_name
  return typeof name === 'string' && name.trim() ? name : null
}

function describeCitation(ref: string): string {
  const material = /^material:([^:]+):unit:(\d+)$/.exec(ref)
  const linkId = material?.[1]
  const unitPage = material?.[2]
  if (linkId && unitPage) {
    const name = materialNameFor(linkId)
    return name ? `${name} 第 ${unitPage} 页` : `资料第 ${unitPage} 页`
  }
  if (ref.startsWith('exercise:')) return '题库候选题'
  return ref
}

function editablePosition(item: SlideOperation): boolean {
  return item.kind === 'insert_static_image' || item.kind === 'add_text_box'
}

function isTextbookLabel(item: SlideOperation): boolean {
  return item.kind === 'add_text_box' && item.details.semantic_role === 'textbook_page_label'
}

function initializeEditableOperation(item: SlideOperation): void {
  const rawPosition = item.target.position
  if (rawPosition && typeof rawPosition === 'object' && !Array.isArray(rawPosition)) {
    const value = rawPosition as Record<string, unknown>
    positions[item.operation_id] = {
      x: Number(value.x),
      y: Number(value.y),
      width: Number(value.width),
      height: Number(value.height),
    }
  }
  if (item.kind === 'insert_static_image' && item.target.target_kind === 'existing_slide') {
    targetSlideNumbers[item.operation_id] = Number(item.target.generated_page_number)
  }
  if (isTextbookLabel(item)) {
    textbookLabels[item.operation_id] = String(item.details.text ?? item.target.content_summary ?? '')
  }
}

function slideIndexFor(operation: SlideOperation): number {
  const hasEditedTargetPage = (
    operation.kind === 'insert_static_image'
    && operation.target.target_kind === 'existing_slide'
    && targetSlideNumbers[operation.operation_id] !== undefined
  )
  const page = Number(
    hasEditedTargetPage
      ? targetSlideNumbers[operation.operation_id] ?? operation.target.generated_page_number
      : operation.target.generated_page_number,
  )
  const signature = String(operation.target.slide_signature ?? '')
  const unitId = String(operation.target.material_unit_id ?? '')
  const sourceLinkId = String(operation.target.source_link_id ?? '')
  const slides = preview.value?.before ?? []

  if (hasEditedTargetPage && Number.isFinite(page)) {
    const index = slides.findIndex(item => Number(item.original_index) === page)
    if (index >= 0) return index
  }
  if (signature) {
    const index = slides.findIndex(item => String(item.stable_signature ?? '') === signature)
    if (index >= 0) return index
  }
  if (unitId) {
    const index = slides.findIndex(item => String(item.material_unit_id ?? '') === unitId)
    if (index >= 0) return index
  }
  if (sourceLinkId && Number.isFinite(page)) {
    const index = slides.findIndex(item => (
      String(item.source_link_id ?? '') === sourceLinkId
      && Number(item.original_index) === page
    ))
    if (index >= 0) return index
  }
  if (!Number.isFinite(page)) return -1
  return Math.max(0, page - 1)
}

function hydratePlan(plan: SlidePlan): void {
  for (const item of plan.payload.operations) {
    decisions[item.operation_id] = item.decision === 'proposed'
      ? (item.execution_mode === 'manual_only' ? 'rejected' : 'approved')
      : item.decision
    reasons[item.operation_id] = item.reason
    teacherNotes[item.operation_id] = item.teacher_note ?? ''
    plannedMinutes[item.operation_id] = item.planned_minutes
    initializeEditableOperation(item)
  }
  const first = plan.payload.operations[0]
  activeOperationId.value = first?.operation_id ?? null
  activeSlideIndex.value = first ? Math.max(0, slideIndexFor(first)) : 0
}

function wait(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}

function withSemesterContext(
  refs: Array<{ kind: string; id: string; revision: string }>,
): Array<{ kind: string; id: string; revision: string }> {
  const semester = catalog.selectedSemester
  return semester
    ? [...refs, { kind: 'semester', id: semester.id, revision: String(semester.revision) }]
    : refs
}

function packLinkIds(pack: ResourcePack): string[] {
  const materials = Array.isArray(pack.payload.materials) ? pack.payload.materials : []
  return materials.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const id = (item as Record<string, unknown>).link_id
    return typeof id === 'string' ? [id] : []
  })
}

function packExerciseIds(pack: ResourcePack): string[] {
  const selection = pack.payload.selection
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return []
  const ids = (selection as Record<string, unknown>).exercise_candidate_ids
  return Array.isArray(ids) ? ids.filter((item): item is string => typeof item === 'string') : []
}

function packPptIntents(pack: ResourcePack): Record<string, 'keep'> {
  const materials = Array.isArray(pack.payload.materials) ? pack.payload.materials : []
  const intents: Record<string, 'keep'> = {}
  for (const item of materials) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const record = item as Record<string, unknown>
    if (record.purpose === 'reference_ppt' && typeof record.link_id === 'string') {
      intents[record.link_id] = 'keep'
    }
  }
  return intents
}

function teacherFeedbackText(): string {
  const lines = Object.entries(pageNotes)
    .flatMap(([page, note]) => {
      const text = note.trim()
      return text ? [`第${page}页：${text}`] : []
    })
  return lines.length ? `教师对照意见：\n${lines.join('\n')}` : ''
}

watch(visibleSlideIndices, (list) => {
  const first = list[0]
  if (first !== undefined && !list.includes(activeSlideIndex.value)) {
    activeSlideIndex.value = first
  }
})

async function saveReview(): Promise<boolean> {
  if (!selectedPlan.value) return false
  const plan = selectedPlan.value
  const operationReviews = plan.payload.operations.map(item => ({
      operation_id: item.operation_id,
      decision: decisions[item.operation_id] ?? (
        item.execution_mode === 'manual_only' ? 'rejected' as const : 'approved' as const
      ),
      reason: reasons[item.operation_id] || item.reason,
      planned_minutes: plannedMinutes[item.operation_id] ?? item.planned_minutes,
      teacher_note: teacherNotes[item.operation_id]?.trim() || null,
      ...(item.kind === 'insert_static_image' && item.target.target_kind === 'existing_slide'
        ? { target_slide_number: targetSlideNumbers[item.operation_id] }
        : {}),
      ...(editablePosition(item) && positions[item.operation_id]
        ? { position: positions[item.operation_id] }
        : {}),
      ...(isTextbookLabel(item)
        ? { text: textbookLabels[item.operation_id] }
        : {}),
  }))
  try {
    const adopted = await adoptTeachingPrepProposal(
      aiTasks.orderedTasks,
      'teaching_prep.slide_change_proposal',
      plan.id,
      {
        kind: 'review_slide_plan',
        operation_reviews: operationReviews,
        approve_low_risk_deletions: false,
        review_note: '对照页默认采用 AI 草稿，导出前再由教师确认',
      },
    )
    if (adopted) {
      workbench.setDirty(null)
      await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        workbench.refresh(),
      ])
      return true
    }
    await catalog.reviewSlidePlan(
      plan,
      operationReviews,
      { reviewNote: '对照页默认采用 AI 草稿，导出前再由教师确认' },
    )
    workbench.setDirty(null)
    await workbench.refresh()
    return true
  } catch {
    reviewMessage.value = '改编草稿还没有保存成功，请稍后重试。原 PPT 没有被修改。'
    return false
  }
}

async function setOperationDecision(
  item: SlideOperation,
  decision: SlideOperationDecision,
): Promise<void> {
  if (busy.value || decisionSaving.value) return
  const previous = operationDecision(item)
  if (previous === decision) return
  decisions[item.operation_id] = decision
  decisionSaving.value = true
  busy.value = true
  reviewMessage.value = '正在保存你的选择并重新生成本地改后页……'
  try {
    const saved = await saveReview()
    if (!saved) {
      decisions[item.operation_id] = previous
      return
    }
    const refreshed = selectedPlan.value
    if (refreshed) {
      previewStartedPlanIds.delete(refreshed.id)
      await ensurePreview(refreshed)
    }
  } finally {
    busy.value = false
    decisionSaving.value = false
  }
}

async function ensurePreview(plan: SlidePlan): Promise<void> {
  if (!pptxExecutionAvailable.value) {
    reviewMessage.value = '这台电脑还不能生成本地改后页。可先按页写下意见重新发给 AI，或稍后再对照导出。'
    return
  }
  if (isPreviewReady(latestRun.value) || latestRun.value?.status === 'published') {
    reviewMessage.value = '改后页已经生成本地预览。左右对照后，满意就导出，不满意就写一句意见再发给 AI。'
    return
  }
  if (latestRun.value && ['failed', 'cancelled', 'interrupted'].includes(latestRun.value.status)) {
    reviewMessage.value = `本地改后页没有生成：${latestRun.value.error_code ?? latestRun.value.status}。原课件没有被修改，也不会自动重试。`
    return
  }
  if (!latestRun.value) {
    if (previewStartedPlanIds.has(plan.id)) return
    previewStartedPlanIds.add(plan.id)
    reviewMessage.value = '正在隔离副本上生成本地改后页。原课件不会被覆盖。'
    try {
      await catalog.executePptx(plan, { previewOnly: true })
    } catch {
      previewStartedPlanIds.delete(plan.id)
      reviewMessage.value = catalog.errorMessage || '本地改后页没有开始生成。原课件没有被修改。'
      return
    }
  }
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      await catalog.selectSlidePlan(plan)
    } catch {
      // Keep polling the execution even if the performance summary is unavailable.
    }
    const run = catalog.pptxExecutions[0]
    if (isPreviewReady(run ?? null) || run?.status === 'published') {
      reviewMessage.value = '改后页已经生成本地预览。左右对照后，满意就导出，不满意就写一句意见再发给 AI。'
      return
    }
    if (run && ['failed', 'cancelled', 'interrupted'].includes(run.status)) {
      reviewMessage.value = `本地改后页没有生成：${run.error_code ?? run.status}。原课件没有被修改，也不会自动重试。`
      return
    }
    await wait(1_000)
  }
  reviewMessage.value = '本地改后页等待超时，没有自动重试。原课件没有被修改。'
}

async function autoAdoptAndPreview(plan: SlidePlan): Promise<void> {
  if (busy.value) return
  hydratePlan(plan)
  const needsReview = plan.status === 'in_review' && plan.payload.operations.some(item => item.decision === 'proposed')
  if (needsReview && !autoReviewedPlanIds.has(plan.id)) {
    autoReviewedPlanIds.add(plan.id)
    busy.value = true
    reviewMessage.value = '正在采用 AI 草稿并生成本地改后页……'
    try {
      const saved = await saveReview()
      if (!saved) {
        autoReviewedPlanIds.delete(plan.id)
        return
      }
    } finally {
      busy.value = false
    }
    const refreshed = selectedPlan.value
    if (refreshed?.status === 'approved') {
      await ensurePreview(refreshed)
    }
    return
  }
  if (plan.status === 'approved') {
    await ensurePreview(plan)
  }
}

async function selectPlan(plan: SlidePlan): Promise<void> {
  await catalog.selectSlidePlan(plan)
  hydratePlan(plan)
}

async function confirmExport(): Promise<void> {
  const run = latestRun.value
  if (run?.status === 'published') {
    await routeState.setStep(3)
    return
  }
  if (!previewReady.value || !run) return
  busy.value = true
  reviewMessage.value = '正在把这一版发布为上课副本。原课件不会被覆盖。'
  try {
    await catalog.confirmPptxPreview(run)
    reviewMessage.value = '这一版已经导出为上课副本。可继续生成上课包。'
    await workbench.refresh()
    await routeState.setStep(3)
  } catch {
    reviewMessage.value = catalog.errorMessage || '这一版还没有导出成功。隔离预览仍在，原课件没有被修改。'
  } finally {
    busy.value = false
  }
}

async function ensureConfirmedDraft(pack: ResourcePack) {
  let draft = catalog.lessonDrafts.find(item => (
    item.resource_pack_id === pack.id && item.status === 'confirmed'
  )) ?? null
  if (!draft) {
    let generated = catalog.lessonDrafts.find(item => (
      item.resource_pack_id === pack.id && item.status === 'draft'
    )) ?? null
    if (!generated) {
      await catalog.prepareLessonDraft('local_template')
      await catalog.generateLessonDraft('local_template')
      generated = catalog.lessonDrafts.find(item => (
        item.resource_pack_id === pack.id && item.status === 'draft'
      )) ?? null
    }
    if (generated) {
      await catalog.reviseLessonDraft(generated, generated.payload, true)
    }
    draft = catalog.lessonDrafts.find(item => (
      item.resource_pack_id === pack.id && item.status === 'confirmed'
    )) ?? null
  }
  if (!draft) throw new Error('内部课件结构没有准备完成')
  await catalog.selectLessonDraft(draft)
  return draft
}

async function resendWithPageNotes(): Promise<void> {
  const feedback = teacherFeedbackText()
  if (!feedback) {
    reviewMessage.value = '请先点出不满意的页，写一句意见，再重新发给 AI。'
    return
  }
  const plan = selectedPlan.value
  const pack = catalog.resourcePacks.find(item => item.id === plan?.resource_pack_id)
    ?? catalog.resourcePacks[0]
  const preferences = catalog.teachingPreferences?.payload
  const lesson = catalog.selectedLesson
  if (!pack || !preferences || !lesson) {
    reviewMessage.value = '当前课时资料还没有准备好，无法按意见重发。'
    return
  }
  resending.value = true
  reviewMessage.value = '正在按你的意见重新发给 AI（计费 1 次）……'
  try {
    await catalog.freezeResourcePack({
      class_name: null,
      lesson_type: 'new_lesson',
      teacher_context: feedback,
      reference_ppt_intents: packPptIntents(pack),
      question_ids: [],
      assessment_ids: [],
      knowledge_scope: [],
      preparation_preferences: preferences,
      selected_material_link_ids: packLinkIds(pack),
      selected_exercise_candidate_ids: packExerciseIds(pack),
    })
    const nextPack = catalog.resourcePacks[0]
    if (!nextPack) throw new Error('资料快照没有成功建立')
    const draft = await ensureConfirmedDraft(nextPack)
    const prepared = await aiTasks.prepare({
      operation_id: `slide-proposal-${crypto.randomUUID().replaceAll('-', '')}`,
      module: 'teaching_prep',
      task_kind: 'teaching_prep.slide_change_proposal',
      source_ref: { kind: 'lesson', id: lesson.id, revision: String(lesson.revision) },
      context_refs: withSemesterContext([{
        kind: 'lesson_draft',
        id: draft.id,
        revision: String(draft.version_number),
      }]),
      prompt_contract_version: 'teaching-prep-slide-proposal-v1',
      model_destination_fingerprint: LOCAL_SLIDE_CONTRACT_FINGERPRINT,
      return_target: 'teaching_prep.lesson.slides',
    })
    await aiTasks.dispatch(prepared)
    workbench.setDirty(null)
    Object.keys(pageNotes).forEach(key => { delete pageNotes[Number(key)] })
    reviewMessage.value = '已按你的意见重新发给 AI。完成后会再出一版对照。失败不会自动重试。'
  } catch (error) {
    reviewMessage.value = error instanceof Error && error.message
      ? `还没有重发：${error.message}`
      : '还没有重发。当前对照页仍保留，不会自动重试。'
  } finally {
    resending.value = false
  }
}

watch(selectedPlan, (plan) => {
  findingsExpanded.value = false
  if (!plan) return
  void autoAdoptAndPreview(plan)
}, { immediate: true })

function stopTracePoll(): void {
  traceAbort?.abort()
  traceAbort = null
  if (traceTimer !== null) {
    clearTimeout(traceTimer)
    traceTimer = null
  }
}

async function refreshAdaptationTrace(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const task = currentSlideTask.value
  if (!lessonId || !task) {
    adaptationTrace.value = null
    return
  }
  traceAbort?.abort()
  const controller = new AbortController()
  traceAbort = controller
  try {
    adaptationTrace.value = await teachingPrepWorkbenchApi.getAdaptationTrace(
      lessonId,
      task.operation_id,
      controller.signal,
    )
  } catch (error) {
    if (controller.signal.aborted) return
      if (isAuthoritativeNotFoundError(error, 'teaching_prep_not_found')) {
      if (!waitingForAdaptation.value) adaptationTrace.value = null
      return
    }
  }
}

function scheduleTracePoll(): void {
  stopTracePoll()
  if (!waitingForAdaptation.value && currentSlideTask.value?.status !== 'failed'
    && currentSlideTask.value?.status !== 'result_unknown'
    && currentSlideTask.value?.status !== 'invalid_result') {
    return
  }
  void refreshAdaptationTrace().finally(() => {
    if (!waitingForAdaptation.value) return
    traceTimer = setTimeout(() => {
      traceTimer = null
      scheduleTracePoll()
    }, 2000)
  })
}

watch(
  () => [
    routeState.currentLessonId.value,
    currentSlideTask.value?.task_id,
    currentSlideTask.value?.status,
    currentSlideTask.value?.operation_id,
    selectedPlan.value?.id ?? null,
  ],
  () => {
    scheduleTracePoll()
  },
  { immediate: true },
)

watch(showWaitHint, (active) => {
  if (active) startWaitTimer()
  else stopWaitTimer()
}, { immediate: true })

watch(
  () => {
    const events = adaptationTrace.value?.events
    if (!events?.length) return ''
    const last = events[events.length - 1]
    if (!last) return ''
    return `${events.length}:${last.phase}:${last.summary}`
  },
  () => {
    if (waitStartedAt === null) return
    waitStartedAt = Date.now()
    waitElapsedSeconds.value = 0
  },
)

onUnmounted(() => {
  stopTracePoll()
  stopWaitTimer()
  if (beforeRetryTimer !== null) {
    clearTimeout(beforeRetryTimer)
    beforeRetryTimer = null
  }
  simResizeObserver?.disconnect()
  simResizeObserver = null
})

watch(
  () => currentSlideTask.value ? `${currentSlideTask.value.task_id}:${currentSlideTask.value.revision}` : '',
  async (key) => {
    const task = currentSlideTask.value
    if (!key || !task || handledTaskRevisions.has(key) || task.status !== 'proposal_ready' || !task.proposal_ref_id) return
    handledTaskRevisions.add(key)
    const lesson = catalog.selectedLesson
    if (lesson) await catalog.selectLesson(lesson)
  },
  { immediate: true },
)

async function runPrimary(): Promise<void> {
  if (currentSlideTask.value && ['prepared', 'queued', 'running'].includes(currentSlideTask.value.status) && !selectedPlan.value) {
    return
  }
  await confirmExport()
}

defineExpose({
  primaryLabel: computed(() => {
    if (busy.value || previewBusy.value) return '正在生成本地改后页…'
    if (latestRun.value?.status === 'published') return '进入上课包'
    if (previewReady.value) return '确认这一版并导出副本'
    if (!pptxExecutionAvailable.value) return '这台电脑还不能导出副本'
    return '等待对照页'
  }),
  primaryDisabled: computed(() => busy.value || resending.value || !canConfirmExport.value),
  inspectorMessage: reviewMessage,
  runPrimary,
})
</script>

<template>
  <div class="tp-step-canvas">
    <section v-if="!selectedPlan" class="tp-panel tp-ai-waiting" :class="`is-${taskPresentation.tone}`" role="status">
      <div class="tp-panel__body">
        <h2>{{ taskPresentation.title }}</h2>
        <p>{{ taskPresentation.detail }}</p>
        <section
          v-if="adaptationTrace || waitingForAdaptation"
          class="tp-adaptation-trace"
          data-testid="adaptation-trace"
          aria-label="改编观察"
        >
          <p class="tp-adaptation-trace__rounds">
            {{ traceRounds ? `第 ${traceRounds.used} / ${traceRounds.max} 轮` : '等待模型开始取页' }}
          </p>
          <p
            v-if="showWaitHint"
            class="tp-adaptation-trace__wait"
            data-testid="adaptation-wait-hint"
            role="status"
          >
            本轮请求已发出，已等待 {{ formatWaitElapsed(waitElapsedSeconds) }}
          </p>
          <div v-if="traceGroups.length" class="tp-adaptation-trace__groups">
            <section
              v-for="group in traceGroups"
              :key="group.key"
              class="tp-adaptation-trace__group"
            >
              <p v-if="group.header" class="tp-adaptation-trace__group-head">{{ group.header }}</p>
              <ul v-if="group.lines.length" class="tp-adaptation-trace__lines">
                <li
                  v-for="line in group.lines"
                  :key="line.key"
                  class="tp-adaptation-trace__line"
                  :class="`is-${line.tone}`"
                >
                  {{ line.text }}
                </li>
              </ul>
              <div
                v-for="block in group.findingBlocks"
                :key="block.key"
                class="tp-findings-card"
                data-testid="trace-findings"
              >
                <p class="tp-findings-card__title">初步审课发现（细看后可能修正）</p>
                <ul class="tp-findings-card__list">
                  <li
                    v-for="(item, findingIndex) in block.items"
                    :key="`${block.key}-${findingIndex}`"
                    class="tp-findings-item"
                  >
                    <span class="tp-finding-chip" :class="`is-${findingCategoryTone(item.category)}`">{{ findingCategoryLabel(item.category) }}</span>
                    <span class="tp-findings-item__text">{{ item.finding }}</span>
                    <span v-if="item.pages.length" class="tp-findings-item__pages">涉及第 {{ item.pages.join('、') }} 页</span>
                  </li>
                </ul>
              </div>
              <div v-if="group.thumbs.length" class="tp-adaptation-trace__thumbs">
                <button
                  v-for="thumb in group.thumbs"
                  :key="thumb.key"
                  type="button"
                  class="tp-adaptation-trace__thumb-button"
                  :aria-label="`放大查看${thumb.label}`"
                  @click="openTraceThumb(thumb)"
                >
                  <img :src="thumb.url" :alt="thumb.label" class="tp-adaptation-trace__thumb" loading="lazy">
                </button>
              </div>
              <div v-if="group.thinking" class="tp-adaptation-trace__thinking-block">
                <button
                  type="button"
                  class="tp-adaptation-trace__thinking-toggle"
                  :aria-expanded="expandedThinkingGroups.includes(group.key)"
                  @click="toggleTraceThinking(group.key)"
                >
                  {{ expandedThinkingGroups.includes(group.key) ? '收起模型思考' : '查看模型思考' }}
                </button>
                <p
                  v-if="expandedThinkingGroups.includes(group.key)"
                  class="tp-adaptation-trace__thinking"
                >{{ group.thinking }}</p>
              </div>
            </section>
          </div>
        </section>
        <div class="tp-inline-actions">
          <AppButton variant="secondary" @click="routeState.setStep(1)">返回确认资料</AppButton>
          <AppButton v-if="currentSlideTask" variant="ghost" @click="aiTasks.refresh(currentSlideTask.task_id)">刷新任务状态</AppButton>
        </div>
      </div>
    </section>

    <template v-else>
      <section v-if="catalog.slidePlans.length > 1" class="tp-panel">
        <div class="tp-panel__head"><h2>计划版本</h2></div>
        <div class="tp-panel__body tp-inline-actions">
          <AppButton
            v-for="plan in catalog.slidePlans"
            :key="plan.id"
            :variant="plan.id === catalog.selectedSlidePlanId ? 'primary' : 'secondary'"
            @click="selectPlan(plan)"
          >
            计划 {{ plan.version_number }} · {{ plan.status }}
          </AppButton>
        </div>
      </section>

      <section class="tp-panel" aria-label="改前改后对照">
        <div class="tp-panel__head">
          <h2>改前 / 改后对照</h2>
          <span class="tp-panel__hint">改后位默认是按改编计划即时画的示意图；本地副本渲染完成后自动换成真实改后页。原 PPTX 不会被修改。</span>
        </div>
        <div v-if="reviewSummary" class="tp-review-summary" data-testid="review-summary">
          <strong>计划 {{ selectedPlan?.version_number }}</strong>
          <span>共 {{ afterSlideCount }} 页 · 有改动 {{ reviewSummary.changedPages }} 页</span>
          <span v-if="reviewSummary.parts.length">（{{ reviewSummary.parts.join(' / ') }}）</span>
          <span v-else>（无实质改动）</span>
        </div>
        <section v-if="reviewFindings.length" class="tp-review-findings" data-testid="review-findings" aria-label="AI 审课发现">
          <p class="tp-review-findings__title">AI 审课发现</p>
          <ul class="tp-review-findings__list">
            <li
              v-for="(item, findingIndex) in visibleReviewFindings"
              :key="`review-finding-${findingIndex}`"
              class="tp-findings-item"
            >
              <span class="tp-finding-chip" :class="`is-${findingCategoryTone(item.category)}`">{{ findingCategoryLabel(item.category) }}</span>
              <span class="tp-findings-item__text">{{ item.finding }}</span>
              <span v-if="item.suggestedAction" class="tp-findings-item__action">建议：{{ item.suggestedAction }}</span>
              <span v-if="item.pages.length" class="tp-findings-item__page-links">
                <button
                  v-for="page in item.pages"
                  :key="`finding-page-${findingIndex}-${page}`"
                  type="button"
                  class="tp-findings-page-link"
                  data-testid="finding-page-link"
                  @click="jumpToFindingPage(page)"
                >第 {{ page }} 页</button>
              </span>
            </li>
          </ul>
          <button
            v-if="reviewFindings.length > FINDINGS_COLLAPSED_COUNT"
            type="button"
            class="tp-review-findings__toggle"
            data-testid="findings-toggle"
            :aria-expanded="findingsExpanded"
            @click="findingsExpanded = !findingsExpanded"
          >
            {{ findingsExpanded ? '收起' : `展开全部 ${reviewFindings.length} 条` }}
          </button>
        </section>
        <div class="tp-slide-checker">
          <aside class="tp-slide-checker__pages" aria-label="页面列表">
            <label class="tp-slide-checker__filter">
              <input v-model="showOnlyChanged" type="checkbox" data-testid="filter-changed-only">
              <span>只看改动</span>
            </label>
            <button
              v-for="index in visibleSlideIndices"
              :key="`page-${index}`"
              type="button"
              class="tp-slide-checker__page-row"
              :class="{ 'is-active': index === activeSlideIndex }"
              @click="activeSlideIndex = index"
            >
              <span class="tp-slide-checker__page-no">{{ index + 1 }}</span>
              <strong class="tp-slide-checker__page-title">{{ describeSlide(preview?.before[index] ?? {}, index) }}</strong>
              <span v-if="pageCategories(index).length" class="tp-slide-checker__page-tags">
                <span
                  v-for="category in pageCategories(index)"
                  :key="category"
                  class="tp-page-tag"
                  :class="`is-${category}`"
                >{{ CHANGE_TAG_LABELS[category] }}</span>
              </span>
            </button>
            <p v-if="!visibleSlideIndices.length" class="tp-muted">没有符合条件的改动页。</p>
          </aside>

          <div class="tp-slide-checker__main">
            <div class="tp-slide-checker__compare">
              <section class="tp-slide-checker__canvas" aria-label="改前页">
                <header><span>改前</span><strong>第 {{ activeSlideIndex + 1 }} 页</strong></header>
                <div class="tp-slide-stage">
                  <img
                    v-if="beforeImageSrc && !beforeLoadFailed"
                    :src="beforeImageSrc"
                    alt="改前页预览"
                    @error="onBeforeImageError"
                  >
                  <div v-else-if="beforeImageSrc" class="tp-slide-stage__paper" data-testid="before-load-failed">
                    <strong>第 {{ activeSlideIndex + 1 }} 页改前图暂时打不开</strong>
                    <span>正在尝试重新生成这一页的预览图。</span>
                    <AppButton variant="ghost" data-testid="before-retry" @click="retryBeforeImage">重试</AppButton>
                  </div>
                  <div v-else class="tp-slide-stage__paper"><strong>{{ describeSlide(beforeSlide ?? {}, activeSlideIndex) }}</strong><span>这一页还没有改前图，可能是新增页。</span></div>
                </div>
              </section>
              <section class="tp-slide-checker__canvas" aria-label="改后页">
                <header><span>改后</span><strong>{{ afterStatusLabel }}</strong></header>
                <div class="tp-slide-stage">
                  <img v-if="afterPreviewUrl" :src="afterPreviewUrl" alt="改后页预览（已渲染副本）">
                  <div v-else-if="showSimulatedAfter && beforeImageSrc && !beforeLoadFailed" class="tp-slide-stage__sim" data-testid="simulated-after">
                    <div v-if="simulatedPageLevelOperations.length" class="tp-sim-opsbar" data-testid="simulated-opsbar">
                      <span
                        v-for="item in simulatedPageLevelOperations"
                        :key="item.operation_id"
                        class="tp-op-chip"
                        :class="`is-${operationCategory(item)}`"
                      >{{ operationLabel(item.kind) }}</span>
                    </div>
                    <div ref="simViewportEl" class="tp-sim-viewport">
                      <div class="tp-sim-canvas" :style="simCanvasStyle">
                        <img
                          class="tp-sim-canvas__base"
                          :src="beforeImageSrc"
                          :alt="`第 ${activeSlideIndex + 1} 页改前底图`"
                          @load="onSimBaseLoad"
                          @error="onBeforeImageError"
                        >
                        <div
                          v-for="box in simulatedOverlayBoxes"
                          :key="box.key"
                          class="tp-change-overlay tp-sim-box"
                          :class="`is-${box.tone}`"
                          :style="box.style"
                          data-testid="simulated-overlay-box"
                        >
                          <img v-if="box.imageUrl" :src="box.imageUrl" :alt="box.alt" class="tp-sim-box__image">
                          <span v-else-if="box.text" class="tp-sim-box__text">{{ box.text }}</span>
                          <span class="tp-sim-box__tag">{{ box.tag }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div v-else class="tp-slide-stage__paper">
                    <strong>{{ describeSlide(beforeSlide ?? {}, activeSlideIndex) }}</strong>
                    <span>这一页没有改前底图（可能是新增页），暂时画不出模拟改后图。</span>
                  </div>
                </div>
              </section>
            </div>

            <section class="tp-slide-checker__details" aria-label="本页改动与意见">
              <p v-if="pageChangeOperations.length" class="tp-muted">本页改动</p>
              <div v-if="pageChangeOperations.length" class="tp-page-operations">
                <article
                  v-for="item in pageChangeOperations"
                  :key="item.operation_id"
                  class="tp-operation-row"
                  :class="{ 'is-rejected': operationDecision(item) === 'rejected' }"
                >
                  <header class="tp-operation-row__head">
                    <span class="tp-op-chip" :class="`is-${operationCategory(item)}`" data-testid="operation-kind">{{ operationLabel(item.kind) }}</span>
                    <strong class="tp-operation-row__title">{{ cardTitle(item) }}</strong>
                    <span v-if="item.execution_mode === 'manual_only'" class="tp-op-flag">需人工在 WPS 中处理</span>
                  </header>
                  <p class="tp-operation-row__reason">{{ item.reason }}</p>
                  <p v-if="operationSupplement(item)" class="tp-operation-row__note">{{ operationSupplement(item) }}</p>
                  <p class="tp-operation-row__meta">
                    <span v-for="ref in item.citations" :key="ref" class="tp-operation-row__cite">{{ describeCitation(ref) }}</span>
                    <span class="tp-op-risk" :class="`is-${item.risk}`">{{ riskLabel(item.risk) }}</span>
                    <span v-if="item.planned_minutes > 0">预计 {{ item.planned_minutes }} 分钟</span>
                  </p>
                  <div class="tp-operation-row__decision" role="group" :aria-label="`${operationLabel(item.kind)}采纳开关`">
                    <button
                      type="button"
                      data-testid="operation-approve"
                      :class="{ 'is-on': operationDecision(item) === 'approved' }"
                      :disabled="busy || decisionSaving"
                      @click="setOperationDecision(item, 'approved')"
                    >采纳</button>
                    <button
                      type="button"
                      data-testid="operation-reject"
                      :class="{ 'is-on': operationDecision(item) === 'rejected' }"
                      :disabled="busy || decisionSaving"
                      @click="setOperationDecision(item, 'rejected')"
                    >不采纳</button>
                  </div>
                </article>
              </div>
              <p v-if="!pageChangeOperations.length" class="tp-muted" data-testid="page-kept">本页保留，无改动。</p>
              <label class="tp-field">
                <span>这一页不满意就写一句</span>
                <textarea
                  v-model="currentPageNote"
                  rows="3"
                  maxlength="500"
                  data-testid="compare-page-note"
                  placeholder="例如：插题太大，挡住例题"
                />
              </label>
              <AppButton
                variant="secondary"
                data-testid="resend-with-page-notes"
                :disabled="resending || busy || !hasPageNotes"
                @click="resendWithPageNotes"
              >
                {{ resending ? '正在重发…' : '按这些意见重新发给 AI' }}
              </AppButton>
              <div v-if="preview?.source_changed" class="tp-banner tp-banner--warn" role="alert">来源 PPTX 已变化，本计划不可执行；请基于新来源重新发送。</div>
            </section>
          </div>
        </div>
        <div class="tp-panel__foot">
          <span class="tp-inline-message" role="status">{{ reviewMessage }}</span>
        </div>
      </section>
    </template>

    <dialog
      ref="traceThumbDialog"
      class="tp-adaptation-trace__preview"
      :aria-label="activeTraceThumb ? `放大查看${activeTraceThumb.label}` : '放大查看页面'"
      @click="closeTraceThumb"
    >
      <img v-if="activeTraceThumb" :src="activeTraceThumb.url" :alt="activeTraceThumb.label">
    </dialog>
  </div>
</template>

<style>
.tp-adaptation-trace {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  background: var(--muted);
}

.tp-adaptation-trace__rounds {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--muted-foreground);
}

.tp-adaptation-trace__wait {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

.tp-adaptation-trace__groups {
  display: grid;
  gap: var(--space-3);
}

.tp-adaptation-trace__group {
  display: grid;
  gap: var(--space-1);
}

.tp-adaptation-trace__group-head {
  margin: 0;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
}

.tp-adaptation-trace__lines {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 2px;
}

.tp-adaptation-trace__line {
  font-size: var(--font-size-dense);
  color: var(--color-text-secondary);
}

.tp-adaptation-trace__line.is-success {
  color: var(--color-success);
}

.tp-adaptation-trace__line.is-warn {
  color: var(--color-warning);
  font-weight: var(--font-weight-semibold);
}

.tp-adaptation-trace__line.is-error {
  color: var(--color-danger);
  font-weight: var(--font-weight-semibold);
}

.tp-adaptation-trace__thumbs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.tp-adaptation-trace__thumb-button {
  padding: 0;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  background: transparent;
  cursor: zoom-in;
  line-height: 0;
}

.tp-adaptation-trace__thumb {
  height: 72px;
  width: auto;
  border-radius: calc(var(--radius) - var(--border-width));
}

.tp-adaptation-trace__thinking-block {
  display: grid;
  gap: var(--space-1);
  justify-items: start;
}

.tp-adaptation-trace__thinking-toggle {
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: var(--font-size-caption);
  color: var(--muted-foreground);
  text-decoration: underline;
}

.tp-adaptation-trace__thinking {
  margin: 0;
  font-size: var(--font-size-dense);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
}

.tp-adaptation-trace__preview {
  max-width: min(90vw, 960px);
  padding: var(--space-2);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  cursor: zoom-out;
}

.tp-adaptation-trace__preview::backdrop {
  background: rgb(0 0 0 / 55%);
}

.tp-adaptation-trace__preview img {
  max-width: 100%;
  max-height: 80vh;
  display: block;
}
</style>
