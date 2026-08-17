<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import type {
  PptxExecution,
  ResourcePack,
  SlideOperationDecision,
  SlidePlan,
} from '../../api/catalog'
import { teachingPrepWorkbenchApi, type AdaptationTrace } from '../../api/workbench'
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
const activeOperation = computed(() => selectedPlan.value?.payload.operations.find(
  item => item.operation_id === activeOperationId.value,
) ?? selectedPlan.value?.payload.operations[0] ?? null)
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

function describeChange(item: Record<string, unknown>): string {
  return String(item.summary ?? item.reason ?? item.kind ?? '页面调整')
}

function operationLabel(kind: string): string {
  return {
    delete_slide: '删除整页',
    delete_shape: '删除页内题目',
    add_text_box: '填写教材页码',
    insert_static_image: '插入教辅题',
    add_slide: '新增练习页',
    manual_note: '需要人工处理',
  }[kind] ?? kind
}

function editablePosition(item: NonNullable<typeof activeOperation.value>): boolean {
  return item.kind === 'insert_static_image' || item.kind === 'add_text_box'
}

function isTextbookLabel(item: NonNullable<typeof activeOperation.value>): boolean {
  return item.kind === 'add_text_box' && item.details.semantic_role === 'textbook_page_label'
}

function initializeEditableOperation(item: NonNullable<typeof activeOperation.value>): void {
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

function slideIndexFor(operation: NonNullable<typeof activeOperation.value>): number {
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

onUnmounted(() => {
  stopTracePoll()
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
          <ol v-if="adaptationTrace?.events.length" class="tp-adaptation-trace__list">
            <li
              v-for="(event, index) in adaptationTrace.events"
              :key="`${event.phase}-${index}`"
              class="tp-adaptation-trace__item"
            >
              <strong>{{ event.summary }}</strong>
              <p v-if="event.thinking_excerpt" class="tp-adaptation-trace__thinking">{{ event.thinking_excerpt }}</p>
              <img
                v-if="event.result?.ok && event.result.preview_url"
                :src="event.result.preview_url"
                :alt="event.result.label"
                class="tp-adaptation-trace__thumb"
              >
            </li>
          </ol>
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
          <span class="tp-panel__hint">改后页来自隔离副本的本地渲染。原 PPTX 不会被修改。</span>
        </div>
        <div class="tp-slide-checker">
          <aside class="tp-slide-checker__thumbs" aria-label="对照页缩略图">
            <button
              v-for="index in afterSlideCount"
              :key="`page-${index}`"
              type="button"
              :class="{ 'is-active': index - 1 === activeSlideIndex }"
              @click="activeSlideIndex = index - 1"
            >
              <span>{{ index }}</span>
              <strong>{{ describeSlide(preview?.before[index - 1] ?? {}, index - 1) }}</strong>
            </button>
          </aside>

          <div class="tp-slide-checker__compare">
            <section class="tp-slide-checker__canvas" aria-label="改前页">
              <header><span>改前</span><strong>第 {{ activeSlideIndex + 1 }} 页</strong></header>
              <div class="tp-slide-stage">
                <img v-if="beforeSlide?.preview_url" :src="String(beforeSlide.preview_url)" alt="改前页预览">
                <div v-else class="tp-slide-stage__paper"><strong>{{ describeSlide(beforeSlide ?? {}, activeSlideIndex) }}</strong><span>这一页还没有改前图，可能是新增页。</span></div>
              </div>
            </section>
            <section class="tp-slide-checker__canvas" aria-label="改后页">
              <header><span>改后</span><strong>{{ previewReady || latestRun?.status === 'published' ? '本地渲染' : '尚未生成' }}</strong></header>
              <div class="tp-slide-stage">
                <img v-if="afterPreviewUrl" :src="afterPreviewUrl" alt="改后页预览">
                <div v-else class="tp-slide-stage__paper">
                  <strong>{{ previewBusy ? '正在生成本地改后页' : '还没有改后页' }}</strong>
                  <span>{{ pptxExecutionAvailable ? '完成后会显示 WPS 渲染结果。' : '当前电脑还不能生成本地改后页。' }}</span>
                </div>
              </div>
            </section>
          </div>

          <section class="tp-slide-checker__inspector">
            <p class="tp-muted" v-if="pageOperations.length">本页改动</p>
            <article
              v-for="item in pageOperations"
              :key="item.operation_id"
              class="tp-operation-row"
            >
              <div class="tp-operation-row__selector">
                <strong>{{ operationLabel(item.kind) }}</strong>
                <span>{{ item.reason }}</span>
                <small>{{ item.support_note || describeChange(item.details) }}</small>
              </div>
            </article>
            <p v-if="!pageOperations.length" class="tp-muted">这一页没有单独列出的改动。</p>
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
        <div class="tp-panel__foot">
          <span class="tp-inline-message" role="status">{{ reviewMessage }}</span>
        </div>
      </section>
    </template>
  </div>
</template>
