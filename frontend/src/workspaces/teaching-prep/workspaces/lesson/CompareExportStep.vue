<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import { type SlideOperation, type SlideOperationDecision, type SlidePlan } from '../../api/catalog'
import {
  teachingPrepWorkbenchApi,
  type PptxLocalOutput,
} from '../../api/workbench'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../../aiAdoption'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'
import MaterialPagePreview from './MaterialPagePreview.vue'
import { auditBadges, downloadFilename } from './outputAudit'

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()

const reviewMessage = ref('改编返回后在这里逐页对照改前改后。确认后本机生成改编副本并自动审计，原 PPT 不会被改。')
const busy = ref(false)
const autoReviewedPlanIds = new Set<string>()
const decisions = reactive<Record<string, SlideOperationDecision>>({})
const activeSlideIndex = ref(0)
const showOnlyChanged = ref(false)

const outputs = ref<PptxLocalOutput[]>([])
const outputsError = ref('')
const exporting = ref(false)
const worksheetBusy = ref(false)
let outputsRequest = 0

const selectedPlan = computed(() => catalog.slidePlans.find(
  item => item.id === catalog.selectedSlidePlanId,
) ?? catalog.slidePlans[0] ?? null)
const preview = computed(() => catalog.slidePlanPreview)
const latestOutput = computed(() => outputs.value[0] ?? null)
const badges = computed(() => (latestOutput.value ? auditBadges(latestOutput.value) : []))

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
  if (!task) return { title: '还没有发送改编任务', detail: '返回上一步，确认课件与题目后再发送。', tone: 'idle' }
  if (task.status === 'prepared' || task.status === 'queued') return { title: '改编任务正在排队', detail: '资料范围已经锁定，可以离开此页面；任务不会重复发送。', tone: 'running' }
  if (task.status === 'running') return { title: 'AI 正在改编课件', detail: task.teacher_message || '完成后这里会出现改前/改后对照。', tone: 'running' }
  if (task.status === 'proposal_ready') return { title: '改编建议已经返回', detail: '正在整理对照页，请稍候。', tone: 'ready' }
  if (task.status === 'needs_input') return { title: '需要教师确认后继续', detail: task.teacher_message || task.next_action, tone: 'attention' }
  if (['failed', 'failed_before_dispatch', 'invalid_result', 'result_unknown'].includes(task.status)) {
    return { title: '本次改编没有完成', detail: task.teacher_message || '没有改动原 PPT。返回上一步后可由教师明确重新发送。', tone: 'error' }
  }
  return { title: '正在恢复改编状态', detail: task.teacher_message || '请稍候。', tone: 'idle' }
})

/* ===== 改前/改后逐页对照 ===== */

interface CompareUnit {
  id?: string
  unit_index: number
  preview_url: string
  title?: string | null
  object_summary?: Record<string, unknown>
}

const beforeUnits = computed<CompareUnit[]>(() => (
  (preview.value?.before ?? []).map(item => ({
    id: typeof item.material_unit_id === 'string' ? item.material_unit_id : undefined,
    unit_index: Number(item.original_index) || 0,
    preview_url: typeof item.preview_url === 'string' ? item.preview_url : '',
    title: typeof item.title === 'string' ? item.title : null,
    object_summary: (item.object_summary ?? {}) as Record<string, unknown>,
  })).filter(item => item.unit_index > 0)
))

interface AfterPage {
  planned_index: number
  state: string
  title: string
  signature: string
  originalIndex: number | null
}

const afterPages = computed<AfterPage[]>(() => (
  (preview.value?.after ?? []).map(item => ({
    planned_index: Number(item.planned_index) || 0,
    state: String(item.state ?? 'kept'),
    title: String(item.title ?? '未命名页'),
    signature: String(item.stable_signature ?? ''),
    originalIndex: Number.isFinite(Number(item.original_index)) && item.original_index !== null
      ? Number(item.original_index)
      : null,
  }))
))

function defaultDecisionFor(item: SlideOperation): SlideOperationDecision {
  return item.execution_mode === 'manual_only' ? 'rejected' : 'approved'
}

function operationDecision(item: SlideOperation): SlideOperationDecision {
  const decision = decisions[item.operation_id] ?? item.decision
  return decision === 'proposed' ? defaultDecisionFor(item) : decision
}

function slideIndexFor(operation: SlideOperation): number {
  const page = Number(operation.target.generated_page_number)
  const signature = String(operation.target.slide_signature ?? '')
  const slides = preview.value?.before ?? []
  if (signature) {
    const index = slides.findIndex(item => String(item.stable_signature ?? '') === signature)
    if (index >= 0) return index
  }
  if (!Number.isFinite(page)) return -1
  return Math.max(0, page - 1)
}

const pageChangeMap = computed(() => {
  const map = new Map<number, Set<string>>()
  for (const item of selectedPlan.value?.payload.operations ?? []) {
    if (item.kind === 'keep_slide') continue
    if (operationDecision(item) === 'rejected') continue
    const index = slideIndexFor(item)
    if (index < 0) continue
    const tags = map.get(index) ?? new Set<string>()
    tags.add(operationCategory(item))
    map.set(index, tags)
  }
  return map
})

const afterCount = computed(() => preview.value?.after_slide_count ?? 0)
const visibleSlideIndices = computed(() => {
  const all = Array.from({ length: afterCount.value }, (_, index) => index)
  if (!showOnlyChanged.value) return all
  return all.filter(index => pageChangeMap.value.has(index))
})

const activeAfterPage = computed(() => afterPages.value[activeSlideIndex.value] ?? null)
const activeBeforeUnitIndex = computed(() => {
  const after = activeAfterPage.value
  if (!after) return null
  if (after.originalIndex !== null) return after.originalIndex
  const before = preview.value?.before ?? []
  const match = before.findIndex(item => String(item.stable_signature ?? '') === after.signature)
  return match >= 0 ? Number(before[match]?.original_index) || null : null
})

const manualOperations = computed(() => (
  (selectedPlan.value?.payload.operations ?? []).filter(item => item.execution_mode === 'manual_only')
))

const reviewSummary = computed(() => {
  const plan = selectedPlan.value
  if (!plan) return null
  const counts: Record<string, number> = { delete: 0, insert: 0, adjust: 0, manual: 0 }
  for (const item of plan.payload.operations) {
    if (item.kind === 'keep_slide') continue
    if (operationDecision(item) === 'rejected') continue
    const category = operationCategory(item)
    counts[category] = (counts[category] ?? 0) + 1
  }
  const parts: string[] = []
  if (counts.delete) parts.push(`删 ${counts.delete} 处`)
  if (counts.insert) parts.push(`插 ${counts.insert} 题`)
  if (counts.adjust) parts.push(`调 ${counts.adjust} 处`)
  if (counts.manual) parts.push(`需人工 ${counts.manual} 处`)
  return { changedPages: pageChangeMap.value.size, parts }
})

type ChangeCategory = 'delete' | 'insert' | 'adjust' | 'manual'

function operationCategory(item: SlideOperation): ChangeCategory {
  if (item.execution_mode === 'manual_only') return 'manual'
  if (item.kind === 'delete_slide' || item.kind === 'delete_shape') return 'delete'
  if (item.kind === 'add_slide' || item.kind === 'add_question_slide' || item.kind === 'insert_static_image') return 'insert'
  return 'adjust'
}

const CHANGE_TAG_LABELS: Record<ChangeCategory, string> = {
  delete: '删',
  insert: '插',
  adjust: '调',
  manual: '人工',
}

function pageCategories(index: number): ChangeCategory[] {
  return [...(pageChangeMap.value.get(index) ?? [])] as ChangeCategory[]
}

function describePage(index: number): string {
  const after = afterPages.value[index]
  if (after) return after.title
  const before = preview.value?.before?.[index]
  return String(before?.title ?? `第 ${index + 1} 页`)
}

watch(visibleSlideIndices, (list) => {
  const first = list[0]
  if (first !== undefined && !list.includes(activeSlideIndex.value)) {
    activeSlideIndex.value = first
  }
})

/* ===== 计划审核：默认采用 AI 草稿（人工处理项除外），全部决定后计划才可执行 ===== */

function hydratePlan(plan: SlidePlan): void {
  for (const item of plan.payload.operations) {
    decisions[item.operation_id] = item.decision === 'proposed'
      ? defaultDecisionFor(item)
      : item.decision
  }
}

async function saveReview(plan: SlidePlan): Promise<boolean> {
  const operationReviews = plan.payload.operations.map(item => ({
    operation_id: item.operation_id,
    decision: decisions[item.operation_id] ?? defaultDecisionFor(item),
    reason: item.reason,
    planned_minutes: item.planned_minutes,
    teacher_note: item.teacher_note,
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
    await workbench.refresh()
    return true
  } catch {
    reviewMessage.value = '改编草稿还没有保存成功，请稍后重试。原 PPT 没有被修改。'
    return false
  }
}

async function autoAdoptPlan(plan: SlidePlan): Promise<void> {
  if (busy.value) return
  hydratePlan(plan)
  const needsReview = plan.status === 'in_review'
    && plan.payload.operations.some(item => item.decision === 'proposed')
  if (!needsReview || autoReviewedPlanIds.has(plan.id)) return
  autoReviewedPlanIds.add(plan.id)
  busy.value = true
  reviewMessage.value = '正在采用 AI 草稿，随后可确认导出……'
  try {
    const saved = await saveReview(plan)
    if (!saved) autoReviewedPlanIds.delete(plan.id)
  } finally {
    busy.value = false
  }
}

watch(selectedPlan, (plan) => {
  if (!plan) return
  void autoAdoptPlan(plan)
}, { immediate: true })

// AI 任务返回建议后，重新载入课时数据以拿到新的改编计划
const handledTaskRevisions = new Set<string>()
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

/* ===== 本机执行产物：审计徽标与下载 ===== */

async function loadOutputs(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const requestId = ++outputsRequest
  if (!lessonId) {
    outputs.value = []
    return
  }
  outputsError.value = ''
  try {
    const items = await teachingPrepWorkbenchApi.listPptxOutputs(lessonId)
    if (requestId !== outputsRequest) return
    outputs.value = items
  } catch {
    if (requestId !== outputsRequest) return
    outputsError.value = '改编产物清单暂时没有载入，可稍后刷新。'
  }
}

watch(
  () => routeState.currentLessonId.value,
  () => { void loadOutputs() },
  { immediate: true },
)

const canConfirmExport = computed(() => {
  const plan = selectedPlan.value
  if (!plan || busy.value || exporting.value) return false
  if (plan.status === 'in_review') return false
  return preview.value?.valid_for_execution === true
})

async function ensureWorksheet(output: PptxLocalOutput): Promise<PptxLocalOutput> {
  if (!workbench.worksheetRequested.value || output.worksheet_filename) return output
  worksheetBusy.value = true
  try {
    const updated = await teachingPrepWorkbenchApi.createWorksheet(
      output.id,
      `worksheet-${crypto.randomUUID().replaceAll('-', '')}`,
    )
    outputs.value = outputs.value.map(item => (item.id === updated.id ? updated : item))
    return updated
  } catch {
    reviewMessage.value = '学案没有生成成功；改编 PPTX 不受影响，仍可下载。'
    return output
  } finally {
    worksheetBusy.value = false
  }
}

async function confirmExport(): Promise<void> {
  const plan = selectedPlan.value
  if (!plan || !canConfirmExport.value) return
  exporting.value = true
  reviewMessage.value = '正在本机生成改编副本并做机械审计。原课件不会被覆盖。'
  try {
    const output = await teachingPrepWorkbenchApi.executeSlidePlanLocal(
      plan.id,
      `pptx-local-${crypto.randomUUID().replaceAll('-', '')}`,
    )
    outputs.value = [output, ...outputs.value.filter(item => item.id !== output.id)]
    const finalOutput = await ensureWorksheet(output)
    reviewMessage.value = finalOutput.audit.passed
      ? '改编副本已生成，审计通过。可以下载上课。'
      : '改编副本已生成，审计有需要人工复核的地方，见下方徽标。'
    await workbench.refresh()
  } catch (error) {
    reviewMessage.value = error instanceof Error && error.message
      ? `导出没有完成：${error.message}。原课件没有被修改，不会自动重试。`
      : '导出没有完成。原课件没有被修改，不会自动重试。'
  } finally {
    exporting.value = false
  }
}

async function downloadOutput(output: PptxLocalOutput): Promise<void> {
  try {
    const { blob, contentDisposition } = await teachingPrepWorkbenchApi.downloadPptxOutput(output.id)
    saveBlob(blob, downloadFilename(contentDisposition, output.output_filename || '改编课件.pptx'))
    reviewMessage.value = '已开始下载改编 PPTX。'
  } catch {
    reviewMessage.value = '改编 PPTX 暂时不能下载，请稍后重试。'
  }
}

async function downloadWorksheetFile(output: PptxLocalOutput): Promise<void> {
  try {
    const { blob, contentDisposition } = await teachingPrepWorkbenchApi.downloadWorksheet(output.id)
    saveBlob(blob, downloadFilename(contentDisposition, output.worksheet_filename || '学案.docx'))
    reviewMessage.value = '已开始下载学案 DOCX。'
  } catch {
    reviewMessage.value = '学案暂时不能下载，请稍后重试。'
  }
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

async function runPrimary(): Promise<void> {
  if (currentSlideTask.value && ['prepared', 'queued', 'running'].includes(currentSlideTask.value.status) && !selectedPlan.value) {
    return
  }
  await confirmExport()
}

onUnmounted(() => {
  outputsRequest += 1
})

defineExpose({
  primaryLabel: computed(() => {
    if (exporting.value) return '正在导出改编副本…'
    if (busy.value) return '正在整理改编计划…'
    if (selectedPlan.value && canConfirmExport.value) return latestOutput.value ? '再次导出' : '确认导出'
    if (selectedPlan.value) return '等待计划就绪'
    return '等待改编返回'
  }),
  primaryDisabled: computed(() => exporting.value || busy.value || !canConfirmExport.value),
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
        <div class="tp-inline-actions">
          <AppButton variant="secondary" @click="routeState.setStep(1)">返回课件与选题</AppButton>
          <AppButton v-if="currentSlideTask" variant="ghost" @click="aiTasks.refresh(currentSlideTask.task_id)">刷新任务状态</AppButton>
        </div>
      </div>
    </section>

    <template v-else>
      <section class="tp-panel" aria-label="改前改后对照">
        <div class="tp-panel__head">
          <h2>改前 / 改后对照</h2>
          <span class="tp-panel__hint">改前页是本机按需渲染的课件原页；改后页按计划即时排布，导出后下方给出真实页数。原 PPTX 不会被修改。</span>
        </div>
        <div v-if="reviewSummary" class="tp-review-summary" data-testid="review-summary">
          <strong>计划 {{ selectedPlan.version_number }}</strong>
          <span>共 {{ afterCount }} 页 · 有改动 {{ reviewSummary.changedPages }} 页</span>
          <span v-if="reviewSummary.parts.length">（{{ reviewSummary.parts.join(' / ') }}）</span>
          <span v-else>（无实质改动）</span>
        </div>
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
              <strong class="tp-slide-checker__page-title">{{ describePage(index) }}</strong>
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
                <MaterialPagePreview
                  v-if="activeBeforeUnitIndex !== null"
                  :units="beforeUnits"
                  :page="activeBeforeUnitIndex"
                  range-label="改前原页"
                />
                <div v-else class="tp-slide-stage">
                  <div class="tp-slide-stage__paper"><strong>新增页</strong><span>这一页是改编时新插入的，没有改前原页。</span></div>
                </div>
              </section>
              <section class="tp-slide-checker__canvas" aria-label="改后页">
                <header><span>改后</span><strong>{{ activeAfterPage?.state === 'new' ? '新增页' : '计划排布' }}</strong></header>
                <div class="tp-slide-stage">
                  <div v-if="activeAfterPage?.state === 'new'" class="tp-slide-stage__paper" data-testid="after-new-page">
                    <strong>{{ activeAfterPage.title }}</strong>
                    <span>题页正文在导出时由本机写入；导出完成后的页码核对见下方审计徽标。</span>
                  </div>
                  <div v-else-if="activeAfterPage?.state === 'hidden'" class="tp-slide-stage__paper">
                    <strong>{{ activeAfterPage.title }}</strong>
                    <span>这一页在改编副本中被隐藏，上课时不会放映。</span>
                  </div>
                  <MaterialPagePreview
                    v-else-if="activeBeforeUnitIndex !== null"
                    :units="beforeUnits"
                    :page="activeBeforeUnitIndex"
                    range-label="保留原页"
                  />
                  <div v-else class="tp-slide-stage__paper"><strong>{{ activeAfterPage?.title ?? '这一页' }}</strong><span>这一页暂时没有预览图。</span></div>
                </div>
              </section>
            </div>
            <section v-if="manualOperations.length" class="tp-slide-checker__details" aria-label="需人工处理">
              <p class="tp-muted">以下改动本机不会自动执行，导出后请人工处理：</p>
              <article v-for="item in manualOperations" :key="item.operation_id" class="tp-operation-row is-rejected">
                <header class="tp-operation-row__head">
                  <span class="tp-op-chip is-manual">人工</span>
                  <strong class="tp-operation-row__title">{{ item.reason }}</strong>
                </header>
              </article>
            </section>
            <div v-if="preview?.source_changed" class="tp-banner tp-banner--warn" role="alert">来源 PPTX 已变化，本计划不可执行；请回到第①步重新发送。</div>
          </div>
        </div>
      </section>

      <section class="tp-panel" aria-label="审计与导出" data-testid="audit-export-panel">
        <div class="tp-panel__head">
          <h2>审计与导出</h2>
          <span class="tp-panel__hint">确认导出后本机生成改编副本并自动审计；学案仅在第①步勾选时生成</span>
        </div>
        <div class="tp-panel__body">
          <p v-if="outputsError" class="tp-error-text">{{ outputsError }}</p>
          <template v-if="latestOutput">
            <div class="tp-audit-badges" data-testid="audit-badges">
              <div v-for="badge in badges" :key="badge.key" class="tp-audit-badge">
                <StatusBadge :tone="badge.tone" :label="badge.label" />
                <p class="tp-muted">{{ badge.detail }}</p>
              </div>
            </div>
            <p class="tp-muted">
              产物：{{ latestOutput.output_filename }} · 由「{{ latestOutput.source_file_name }}」{{ latestOutput.source_page_count }} 页改编为 {{ latestOutput.final_page_count }} 页
            </p>
            <div class="tp-inline-actions">
              <AppButton variant="primary" data-testid="download-adapted-pptx" @click="downloadOutput(latestOutput)">
                下载改编 PPTX
              </AppButton>
              <AppButton
                v-if="latestOutput.worksheet_filename"
                variant="secondary"
                data-testid="download-worksheet"
                @click="downloadWorksheetFile(latestOutput)"
              >
                下载学案 DOCX
              </AppButton>
              <span v-else-if="worksheetBusy" class="tp-muted">正在生成学案……</span>
            </div>
          </template>
          <p v-else class="tp-muted">还没有改编副本。对照满意后点右下「确认导出」。</p>
        </div>
        <div class="tp-panel__foot">
          <span class="tp-inline-message" role="status">{{ reviewMessage }}</span>
        </div>
      </section>
    </template>
  </div>
</template>
