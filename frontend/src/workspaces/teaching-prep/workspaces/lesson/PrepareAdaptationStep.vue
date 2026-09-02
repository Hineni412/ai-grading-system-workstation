<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type {
  QuestionBankSection,
  QuestionSelectionPreview,
  SlideAnimationRun,
} from '../../api/workbench'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import {
  teachingPrepCatalogApi,
  type LessonDraft,
  type MaterialLink,
  type ResourcePack,
} from '../../api/catalog'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'
import { parseAnimationPageText } from './animationPages'
import PptAnimationPagePicker, {
  type PptAnimationDraftTask,
  type PptAnimationPickerUnit,
} from './PptAnimationPagePicker.vue'
import {
  buildPreviewRequest,
  inferLessonKind,
  matchSectionForLesson,
  questionBankVolumeId,
  sectionShortName,
  selectionPayloadForFreeze,
  type LessonKind,
} from './questionSelection'

const LOCAL_SLIDE_CONTRACT_FINGERPRINT = '34b984e97a114fea15394bfdb1c73e8f667b340918e72c21bc15a21934a20a1b'
const ACTIVE_TASK_STATUSES = new Set(['prepared', 'queued', 'running', 'needs_input', 'proposal_ready'])
const ANIMATION_PAGE_LIMIT = 4
const ANIMATION_BILLED_LIMIT = 3
const DEFAULT_MESSAGE = '打开课时后已自动载入本课绑定的参考课件并跑出候选题组。确认题目后点一次「发给 AI 改编」，AI 最多调用 2 次（1 次改编 + 必要时 1 次修复），失败不会自动再发，原 PPT 不会被改。'

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()

const message = ref(DEFAULT_MESSAGE)
const submitting = ref(false)

/* ===== 主课件（每个活跃课时恰好绑定一份 reference_ppt 关联） ===== */

const referencePptLinks = computed(() => catalog.materialLinks.filter(item => (
  item.purpose === 'reference_ppt' && item.is_active
)))
const primaryPptLinkId = ref<string | null>(null)
const primaryPpt = computed(() => (
  referencePptLinks.value.find(item => item.id === primaryPptLinkId.value)
  ?? referencePptLinks.value[0]
  ?? null
))

watch(referencePptLinks, (links) => {
  if (!links.some(item => item.id === primaryPptLinkId.value)) {
    primaryPptLinkId.value = links[0]?.id ?? null
  }
}, { immediate: true })

function changePrimaryPpt(): void {
  if (referencePptLinks.value.length > 1) return
  void routeState.openLibrary()
}

/* ===== 题库自动选题 ===== */

const lessonKind = ref<LessonKind>('new_lesson')
const volumeId = computed(() => questionBankVolumeId(catalog.selectedCurriculum))
const sections = ref<QuestionBankSection[]>([])
const sectionsLoading = ref(false)
const sectionsError = ref('')
const selectedSectionId = ref('')
const preview = ref<QuestionSelectionPreview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
const excludedQuestionIds = ref<number[]>([])
let sectionsRequest = 0
let previewRequest = 0

const previewItems = computed(() => preview.value?.items ?? [])
const removedItems = ref<QuestionSelectionPreview['items']>([])

function sectionOptionLabel(section: QuestionBankSection): string {
  return `${section.chapter_name.split('｜').pop() ?? section.chapter_name} · ${sectionShortName(section)}（${section.question_count} 题）`
}

async function loadSections(): Promise<void> {
  const volume = volumeId.value
  const lesson = workbench.selectedLesson.value
  sections.value = []
  selectedSectionId.value = ''
  preview.value = null
  excludedQuestionIds.value = []
  removedItems.value = []
  if (!volume || !lesson) return
  const requestId = ++sectionsRequest
  sectionsLoading.value = true
  sectionsError.value = ''
  try {
    const items = await teachingPrepWorkbenchApi.questionBankSections(volume)
    if (requestId !== sectionsRequest) return
    sections.value = items.filter(item => item.question_count > 0)
    const matched = matchSectionForLesson(lesson.title, sections.value)
    selectedSectionId.value = matched?.section_id ?? ''
  } catch {
    if (requestId !== sectionsRequest) return
    sectionsError.value = '题库小节清单暂时没有载入。仍可只按课件改编，或稍后重新打开本课。'
  } finally {
    if (requestId === sectionsRequest) sectionsLoading.value = false
  }
}

async function runPreview(): Promise<void> {
  const volume = volumeId.value
  const sectionId = selectedSectionId.value
  if (!volume || !sectionId) {
    preview.value = null
    return
  }
  const requestId = ++previewRequest
  previewLoading.value = true
  previewError.value = ''
  try {
    const result = await teachingPrepWorkbenchApi.questionSelectionPreview(
      buildPreviewRequest(volume, [sectionId], excludedQuestionIds.value, lessonKind.value),
    )
    if (requestId !== previewRequest) return
    preview.value = result
  } catch {
    if (requestId !== previewRequest) return
    previewError.value = '候选题组没有跑出来。仍可只按课件改编，或点「重新选题」重试。'
  } finally {
    if (requestId === previewRequest) previewLoading.value = false
  }
}

function removeQuestion(questionId: number): void {
  const item = previewItems.value.find(entry => entry.question_id === questionId)
  if (!item) return
  excludedQuestionIds.value = [...excludedQuestionIds.value, questionId]
  removedItems.value = [...removedItems.value, item]
  void runPreview()
}

function restoreQuestion(questionId: number): void {
  excludedQuestionIds.value = excludedQuestionIds.value.filter(id => id !== questionId)
  removedItems.value = removedItems.value.filter(item => item.question_id !== questionId)
  void runPreview()
}

watch(
  () => [
    routeState.currentLessonId.value,
    volumeId.value,
    workbench.selectedLesson.value?.id ?? '',
  ] as const,
  () => {
    const title = workbench.selectedLesson.value?.title ?? ''
    lessonKind.value = inferLessonKind(title)
    void loadSections()
  },
  { immediate: true },
)

watch(selectedSectionId, () => {
  excludedQuestionIds.value = []
  removedItems.value = []
  void runPreview()
})

watch(lessonKind, () => {
  void runPreview()
})

/* ===== 课堂动画（保留入口，单独计费的 HTML 动画） ===== */

const animationDrafts = ref<PptAnimationDraftTask[]>([])
const animationPage = ref(1)
const animationUnits = ref<PptAnimationPickerUnit[]>([])
const animationLoading = ref(false)
const animationLoadError = ref('')
const animationBusy = ref(false)
const animationMessage = ref('')
const animationRuns = ref<SlideAnimationRun[]>([])
const animationBilledCount = ref(0)
const animationBilledLimit = ref(ANIMATION_BILLED_LIMIT)

const animationAllowedPages = computed(() => animationUnits.value.map(item => item.unit_index))
const animationSelectionLabel = computed(() => {
  const labels = animationDrafts.value.flatMap((task, index) => {
    const parsed = parseAnimationPageText(task.pageText, animationAllowedPages.value, ANIMATION_PAGE_LIMIT)
    if (!parsed.pages.length) return []
    return [`任务${index + 1}：第 ${parsed.pages.join('、')} 页`]
  })
  if (!labels.length) return '未建任务（生成动画才单独计费，不改课件）'
  return `${labels.join('；')} · 每个任务另计 1 次`
})
const animationRemaining = computed(() => (
  Math.max(0, animationBilledLimit.value - animationBilledCount.value)
))
const pendingAnimation = computed(() => (
  animationRuns.value.find(item => item.status === 'running' || item.status === 'succeeded') ?? null
))
const reviewingAnimation = computed(() => (
  pendingAnimation.value?.status === 'succeeded' ? pendingAnimation.value : null
))
const runningAnimation = computed(() => (
  pendingAnimation.value?.status === 'running' ? pendingAnimation.value : null
))
const acceptedAnimations = computed(() => (
  animationRuns.value.filter(item => item.status === 'accepted')
))
const animationGenerateBlocked = computed(() => (
  animationBusy.value || Boolean(runningAnimation.value) || Boolean(reviewingAnimation.value)
))

function toPickerUnits(
  units: Array<{
    id?: string
    unit_index: number
    preview_url: string
    title?: string | null
    object_summary?: Record<string, unknown>
    revision?: number
  }>,
  startUnit: number,
  endUnit: number,
): PptAnimationPickerUnit[] {
  return units
    .filter(item => item.unit_index >= startUnit && item.unit_index <= endUnit)
    .map(item => ({
      id: item.id,
      unit_index: item.unit_index,
      preview_url: item.preview_url,
      title: item.title ?? null,
      object_summary: item.object_summary,
      revision: item.revision,
    }))
}

watch(
  () => primaryPpt.value?.id ?? '',
  async (linkId) => {
    animationDrafts.value = []
    animationUnits.value = []
    animationLoadError.value = ''
    const item = primaryPpt.value
    animationPage.value = item?.start_unit ?? 1
    if (!linkId || !item || item.id !== linkId) return
    animationLoading.value = true
    try {
      const units = await teachingPrepCatalogApi.listMaterialUnits(item.material_version_id)
      if (primaryPpt.value?.id !== linkId) return
      const picked = toPickerUnits(units, item.start_unit, item.end_unit)
      animationUnits.value = picked
      animationPage.value = picked[0]?.unit_index ?? item.start_unit
      if (!picked.length) {
        animationLoadError.value = '这份课件还没有可预览的页。请先到资料库生成预览。'
      }
    } catch {
      if (primaryPpt.value?.id !== linkId) return
      animationLoadError.value = '课件预览没有打开。请先到资料库确认这份课件已生成预览。'
    } finally {
      if (primaryPpt.value?.id === linkId) animationLoading.value = false
    }
  },
  { immediate: true },
)

async function loadAnimationRuns(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) {
    animationRuns.value = []
    animationBilledCount.value = 0
    return
  }
  try {
    const listed = await teachingPrepWorkbenchApi.listSlideAnimationRuns(lessonId)
    animationRuns.value = listed.items
    animationBilledCount.value = listed.billed_count
    animationBilledLimit.value = listed.billed_limit
  } catch {
    animationMessage.value = animationMessage.value || '课堂动画记录暂时没有打开，可稍后刷新。'
  }
}

function animationPreviewUrl(runId: string): string {
  return `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}/preview`
}

function addAnimationDraft(): void {
  if (animationDrafts.value.length >= animationRemaining.value) return
  animationDrafts.value = [...animationDrafts.value, { id: crypto.randomUUID(), pageText: '' }]
}

function updateAnimationDraftText(taskId: string, pageText: string): void {
  animationDrafts.value = animationDrafts.value.map(item => (
    item.id === taskId ? { ...item, pageText } : item
  ))
}

function removeAnimationDraft(taskId: string): void {
  animationDrafts.value = animationDrafts.value.filter(item => item.id !== taskId)
}

function applyPreviewUnit(next: PptAnimationPickerUnit): void {
  animationUnits.value = animationUnits.value.map((item) => {
    if (next.id && item.id === next.id) return { ...item, ...next }
    if (item.unit_index === next.unit_index && item.preview_url === next.preview_url) {
      return { ...item, ...next }
    }
    return item
  })
}

function wait(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}

async function generateAnimation(taskId: string): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const link = primaryPpt.value
  const task = animationDrafts.value.find(item => item.id === taskId)
  const parsed = parseAnimationPageText(task?.pageText ?? '', animationAllowedPages.value, ANIMATION_PAGE_LIMIT)
  if (
    !lessonId
    || !link
    || !task
    || parsed.error
    || !parsed.pages.length
    || animationGenerateBlocked.value
    || animationRemaining.value < 1
  ) return
  animationBusy.value = true
  animationMessage.value = '正在单独发送所选课件页，生成课堂动画（计费 1 次）……'
  try {
    let run = await teachingPrepWorkbenchApi.startSlideAnimationRun(lessonId, {
      operationId: `slide-animation-${crypto.randomUUID().replaceAll('-', '')}`,
      materialLinkId: link.id,
      pageIndexes: [...parsed.pages],
    })
    animationRuns.value = [run, ...animationRuns.value.filter(item => item.id !== run.id)]
    for (let attempt = 0; attempt < 120 && run.status === 'running'; attempt += 1) {
      await wait(1_000)
      run = await teachingPrepWorkbenchApi.slideAnimationRun(run.id)
      animationRuns.value = [run, ...animationRuns.value.filter(item => item.id !== run.id)]
    }
    await loadAnimationRuns()
    if (run.status === 'succeeded') {
      animationMessage.value = '课堂动画草稿已生成。请预览后再保存为本课附件；这次调用不改课件副本。'
      return
    }
    animationMessage.value = run.status === 'failed'
      ? `课堂动画没有生成：${run.error_code ?? '模型未返回可用分镜'}。已填页码仍保留，不会自动重试。`
      : `课堂动画未完成：${run.error_code ?? run.status}`
  } catch {
    animationMessage.value = `课堂动画没有发出。本课最多 ${animationBilledLimit.value} 次计费发送，失败不自动重试。`
  } finally {
    animationBusy.value = false
  }
}

async function cancelAnimation(): Promise<void> {
  const run = runningAnimation.value
  if (!run) return
  animationBusy.value = true
  try {
    await teachingPrepWorkbenchApi.cancelSlideAnimationRun(run.id)
    await loadAnimationRuns()
    animationMessage.value = '课堂动画已取消；若模型尚未发出则不计费。课件改编不受影响。'
  } catch {
    animationMessage.value = '课堂动画暂时没有取消成功，请稍后重试。'
  } finally {
    animationBusy.value = false
  }
}

async function acceptAnimation(): Promise<void> {
  const run = reviewingAnimation.value
  if (!run) return
  animationBusy.value = true
  try {
    await teachingPrepWorkbenchApi.acceptSlideAnimationRun(run.id, run.revision)
    await loadAnimationRuns()
    animationMessage.value = '已保存为本课课堂动画附件。可下载到本机，用浏览器打开播放。'
  } catch {
    animationMessage.value = '课堂动画没有保存成功，请刷新后重试。'
  } finally {
    animationBusy.value = false
  }
}

async function discardAnimation(): Promise<void> {
  const run = reviewingAnimation.value
  if (!run) return
  animationBusy.value = true
  try {
    await teachingPrepWorkbenchApi.discardSlideAnimationRun(run.id, run.revision)
    await loadAnimationRuns()
    animationMessage.value = '已放弃这份课堂动画草稿。本次计费仍计入本课次数。'
  } catch {
    animationMessage.value = '课堂动画草稿没有放弃成功，请刷新后重试。'
  } finally {
    animationBusy.value = false
  }
}

async function downloadAnimation(run: SlideAnimationRun): Promise<void> {
  try {
    const { blob } = await teachingPrepWorkbenchApi.downloadSlideAnimation(run.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = '课堂动画.html'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    animationMessage.value = '已开始下载课堂动画。用浏览器打开即可离线播放。'
  } catch {
    animationMessage.value = '课堂动画还不能下载。请先预览并保存为本课附件。'
  }
}

watch(
  () => routeState.currentLessonId.value,
  () => { void loadAnimationRuns() },
  { immediate: true },
)

onUnmounted(() => {
  sectionsRequest += 1
  previewRequest += 1
})

/* ===== 发给 AI 改编 ===== */

const currentSlideTask = computed(() => {
  const lesson = catalog.selectedLesson
  const draft = catalog.lessonDrafts.find(item => item.id === catalog.selectedLessonDraftId)
    ?? catalog.lessonDrafts.find(item => item.status === 'confirmed')
    ?? null
  if (!lesson || !draft) return null
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.slide_change_proposal'
    && task.source_ref.kind === 'lesson'
    && task.source_ref.id === lesson.id
    && task.context_refs.some(ref => ref.kind === 'lesson_draft' && ref.id === draft.id)
    && ACTIVE_TASK_STATUSES.has(task.status)
  )) ?? null
})

const canSubmit = computed(() => (
  Boolean(primaryPpt.value)
  && Boolean(catalog.teachingPreferences?.payload)
  && !submitting.value
  && !previewLoading.value
))

async function ensureConfirmedDraft(pack: ResourcePack): Promise<LessonDraft> {
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

function withSemesterContext(
  refs: Array<{ kind: string; id: string; revision: string }>,
): Array<{ kind: string; id: string; revision: string }> {
  const semester = catalog.selectedSemester
  return semester
    ? [...refs, { kind: 'semester', id: semester.id, revision: String(semester.revision) }]
    : refs
}

async function confirmAndSend(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const link: MaterialLink | null = primaryPpt.value
  const preferences = catalog.teachingPreferences?.payload
  if (!lessonId || !link || !preferences || !canSubmit.value) return
  submitting.value = true
  message.value = '正在冻结本课资料快照并准备 AI 改编任务……'
  try {
    const referencePptIntents = { [link.id]: 'keep' as const }
    const selectedLinkIds = [link.id]
    await teachingPrepWorkbenchApi.resourcePackPreflight(lessonId, {
      reference_ppt_intents: referencePptIntents,
      selected_material_link_ids: selectedLinkIds,
      selected_exercise_candidate_ids: [],
    })
    const items = previewItems.value
    await catalog.freezeResourcePack({
      class_name: null,
      lesson_type: lessonKind.value,
      teacher_context: null,
      reference_ppt_intents: referencePptIntents,
      question_ids: items.map(item => item.question_id),
      assessment_ids: [],
      knowledge_scope: [],
      preparation_preferences: preferences,
      selected_material_link_ids: selectedLinkIds,
      selected_exercise_candidate_ids: [],
      question_selection: preview.value
        ? selectionPayloadForFreeze(preview.value, items)
        : null,
    })
    const pack = catalog.resourcePacks[0]
    if (!pack) throw new Error('资料快照没有成功建立')
    const draft = await ensureConfirmedDraft(pack)
    const existing = aiTasks.orderedTasks.find(task => (
      task.module === 'teaching_prep'
      && task.task_kind === 'teaching_prep.slide_change_proposal'
      && task.source_ref.kind === 'lesson'
      && task.source_ref.id === lessonId
      && task.context_refs.some(ref => ref.kind === 'lesson_draft' && ref.id === draft.id)
      && ACTIVE_TASK_STATUSES.has(task.status)
    ))
    if (existing) {
      if (existing.status === 'prepared' && existing.send_attempt_count === 0) {
        await aiTasks.dispatch(existing)
      }
      message.value = '已找到本课时正在处理的改编任务，没有重复发送。'
    } else {
      const lesson = catalog.selectedLesson
      if (!lesson) throw new Error('当前课时已失效')
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
      message.value = '已发给 AI 改编。完成后请进入第②步对照导出。'
    }
    await routeState.setStep(2)
  } catch (error) {
    message.value = error instanceof Error && error.message
      ? `尚未发送：${error.message}`
      : '尚未发送。当前题目勾选仍保留，请检查后重试。'
  } finally {
    submitting.value = false
  }
}

async function runPrimary(): Promise<void> {
  const current = currentSlideTask.value
  if (current && current.status !== 'proposal_ready') {
    await routeState.setStep(2)
    return
  }
  await confirmAndSend()
}

const primaryActionLabel = computed(() => {
  if (submitting.value) return '正在准备…'
  if (currentSlideTask.value?.status === 'proposal_ready') return '重新发给 AI 改编'
  if (currentSlideTask.value) return '查看正在处理的改编'
  return '发给 AI 改编'
})

defineExpose({
  primaryLabel: primaryActionLabel,
  primaryDisabled: computed(() => !canSubmit.value),
  inspectorMessage: message,
  runPrimary,
})
</script>

<template>
  <div class="tp-step-canvas">
    <section class="tp-panel" aria-label="本课课件">
      <div class="tp-panel__head">
        <h2>本课课件</h2>
        <span class="tp-panel__hint">打开课时即自动载入本课绑定的参考 PPT，AI 将在它的副本上改编</span>
      </div>
      <div class="tp-panel__body">
        <div v-if="!primaryPpt" class="tp-banner tp-banner--warn" role="alert">
          本课时还没有绑定参考 PPT。请先到资料库把课件对应到本课，然后再发给 AI。
          <AppButton variant="secondary" @click="routeState.openLibrary()">打开资料库</AppButton>
        </div>
        <template v-else>
          <div class="tp-link-row">
            <div class="tp-link-row__meta">
              <strong data-testid="primary-ppt-name">{{ primaryPpt.material_name }}</strong>
              <small>第 {{ primaryPpt.start_unit }}—{{ primaryPpt.end_unit }} 页 · {{ primaryPpt.material_type.toUpperCase() }}</small>
            </div>
            <StatusBadge tone="success" label="参考课件" />
            <label v-if="referencePptLinks.length > 1" class="tp-field">
              更换课件
              <select v-model="primaryPptLinkId" data-testid="change-primary-ppt">
                <option v-for="item in referencePptLinks" :key="item.id" :value="item.id">
                  {{ item.material_name }}
                </option>
              </select>
            </label>
            <AppButton v-else variant="ghost" data-testid="change-primary-ppt" @click="changePrimaryPpt">
              更换
            </AppButton>
          </div>
        </template>
      </div>
    </section>

    <section class="tp-panel" aria-label="题库自动选题" data-testid="question-selection-panel">
      <div class="tp-panel__head">
        <h2>题库自动选题</h2>
        <span class="tp-panel__hint">按考频、难度和方法差异自动选出候选题组，可增删个别题</span>
      </div>
      <div class="tp-panel__body">
        <div class="tp-form-line" data-testid="lesson-kind-picker">
          <label class="tp-field--check">
            <input v-model="lessonKind" type="radio" value="new_lesson" data-testid="kind-new">
            新授课（难度≤5，约 8 题）
          </label>
          <label class="tp-field--check">
            <input v-model="lessonKind" type="radio" value="review" data-testid="kind-review">
            复习课（难度≤7，约 12 题）
          </label>
        </div>
        <p v-if="!volumeId" class="tp-muted">当前册别暂未接入题库，本次只按课件改编。</p>
        <p v-else-if="sectionsLoading" class="tp-muted">正在载入题库小节……</p>
        <p v-else-if="sectionsError" class="tp-error-text">{{ sectionsError }}</p>
        <template v-else-if="sections.length">
          <div class="tp-form-line">
            <label class="tp-field">
              出题范围（小节）
              <select v-model="selectedSectionId" data-testid="question-section-select">
                <option value="" disabled>请选择小节</option>
                <option v-for="section in sections" :key="section.section_id" :value="section.section_id">
                  {{ sectionOptionLabel(section) }}
                </option>
              </select>
            </label>
            <AppButton variant="ghost" :disabled="previewLoading" @click="runPreview">
              重新选题
            </AppButton>
          </div>
          <p v-if="previewError" class="tp-error-text">{{ previewError }}</p>
          <p v-else-if="previewLoading" class="tp-muted">正在按考频和难度选题……</p>
          <template v-else-if="preview">
            <ul v-if="previewItems.length" class="tp-question-list" data-testid="question-list">
              <li v-for="item in previewItems" :key="item.question_id" class="tp-question-row">
                <div class="tp-question-row__main">
                  <span class="tp-question-row__stem">{{ item.stem }}</span>
                  <span class="tp-question-row__badges">
                    <StatusBadge tone="info" :label="item.selection_reason.frequency" />
                    <StatusBadge tone="neutral" :label="item.selection_reason.difficulty" />
                    <StatusBadge tone="ai" :label="item.selection_reason.method" />
                  </span>
                </div>
                <AppButton
                  variant="ghost"
                  :data-testid="`remove-question-${item.question_id}`"
                  @click="removeQuestion(item.question_id)"
                >
                  去掉
                </AppButton>
              </li>
            </ul>
            <p v-else class="tp-muted">这个小节下没有符合难度的候选题，本次只按课件改编。</p>
            <div v-if="removedItems.length" class="tp-question-removed" data-testid="removed-questions">
              <p class="tp-muted">已去掉 {{ removedItems.length }} 题（去掉后会自动补进替代题）：</p>
              <div v-for="item in removedItems" :key="item.question_id" class="tp-inline-actions">
                <span class="tp-muted">{{ item.selection_reason.frequency }} · {{ item.selection_reason.method }}</span>
                <AppButton
                  variant="ghost"
                  :data-testid="`restore-question-${item.question_id}`"
                  @click="restoreQuestion(item.question_id)"
                >
                  加回来
                </AppButton>
              </div>
            </div>
          </template>
          <p v-else class="tp-muted">选择小节后自动跑出候选题组。</p>
        </template>
        <p v-else class="tp-muted">题库在这个册别下还没有可用小节，本次只按课件改编。</p>
        <label class="tp-field--check">
          <input v-model="workbench.worksheetRequested.value" data-testid="with-worksheet" type="checkbox">
          同时生成学案（导出改编 PPTX 时会多生成一份 DOCX）
        </label>
      </div>
    </section>

    <section v-if="primaryPpt" class="tp-panel" aria-label="课堂动画">
      <div class="tp-panel__head">
        <h2>课堂动画</h2>
        <span class="tp-panel__hint">可选项：选几页让 AI 做 HTML 动画，单独计费，不影响改编</span>
      </div>
      <div class="tp-panel__body">
        <PptAnimationPagePicker
          :units="animationUnits"
          :page="animationPage"
          :tasks="animationDrafts"
          :max-selected="ANIMATION_PAGE_LIMIT"
          :remaining="animationRemaining"
          :billed-limit="animationBilledLimit"
          :loading="animationLoading"
          :load-error="animationLoadError"
          :generating="animationGenerateBlocked"
          @update:page="animationPage = $event"
          @update:unit="applyPreviewUnit"
          @add-task="addAnimationDraft"
          @remove-task="removeAnimationDraft"
          @update:task-text="updateAnimationDraftText"
          @generate-task="generateAnimation"
        />
        <div v-if="runningAnimation" class="tp-inline-actions" data-testid="ppt-animation-actions">
          <AppButton
            variant="secondary"
            data-testid="cancel-slide-animation"
            :disabled="animationBusy"
            @click="cancelAnimation"
          >
            取消生成
          </AppButton>
        </div>
        <p v-if="animationMessage" class="tp-inline-message" role="status">{{ animationMessage }}</p>
        <div v-if="reviewingAnimation" class="tp-animation-draft" data-testid="ppt-animation-draft">
          <p class="tp-muted">
            草稿预览 · {{ reviewingAnimation.storyboard?.title || '课堂动画' }}
            · 第 {{ reviewingAnimation.page_indexes.join('、') }} 页
          </p>
          <iframe
            class="tp-animation-preview"
            sandbox="allow-scripts"
            referrerpolicy="no-referrer"
            title="课堂动画预览"
            :src="animationPreviewUrl(reviewingAnimation.id)"
          />
          <div class="tp-inline-actions">
            <AppButton data-testid="accept-slide-animation" :disabled="animationBusy" @click="acceptAnimation">
              保存为本课附件
            </AppButton>
            <AppButton variant="secondary" data-testid="discard-slide-animation" :disabled="animationBusy" @click="discardAnimation">
              放弃这份草稿
            </AppButton>
          </div>
        </div>
        <div v-if="acceptedAnimations.length" class="tp-animation-attachments">
          <p class="tp-source-group__label">已保存的课堂动画</p>
          <div v-for="item in acceptedAnimations" :key="item.id" class="tp-inline-actions">
            <span>{{ item.storyboard?.title || '课堂动画' }} · 第 {{ item.page_indexes.join('、') }} 页</span>
            <AppButton variant="ghost" data-testid="download-slide-animation" @click="downloadAnimation(item)">
              下载 HTML
            </AppButton>
          </div>
        </div>
      </div>
    </section>

    <section class="tp-panel" aria-label="发送确认">
      <div class="tp-panel__body">
        <div class="tp-confirmation-summary">
          <dl>
            <div><dt>主课件</dt><dd>{{ primaryPpt?.material_name ?? '未绑定' }}</dd></div>
            <div><dt>候选题</dt><dd>{{ previewItems.length ? `${previewItems.length} 题随课件一起改编` : '不带题，只改编课件' }}</dd></div>
            <div><dt>课堂动画页</dt><dd>{{ animationSelectionLabel }}</dd></div>
            <div><dt>模型调用</dt><dd>最多 2 次：1 次改编 + 必要时 1 次修复 · 失败不自动重试</dd></div>
            <div><dt>调用费用</dt><dd>本机无法预估金额；由当前模型服务商按实际用量计费，点击发送即确认本次调用</dd></div>
            <div><dt>原始文件</dt><dd>不会覆盖</dd></div>
          </dl>
        </div>
        <div v-if="currentSlideTask" class="tp-inline-actions">
          <AppButton variant="ghost" @click="routeState.setStep(2)">查看当前改编</AppButton>
        </div>
      </div>
    </section>

    <p class="tp-inline-message" role="status">{{ message }}</p>
  </div>
</template>
