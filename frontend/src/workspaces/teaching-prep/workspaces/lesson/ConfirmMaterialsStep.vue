<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type {
  ExerciseSuggestionPayload,
  ExerciseSuggestionRun,
  ReferenceMaterialLink,
  ReferenceSelectionDraft,
  ReferenceSelectionPayload,
  SlideAnimationRun,
} from '../../api/workbench'
import { ApiError, isAuthoritativeNotFoundError } from '../../../../api/errors'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import {
  teachingPrepCatalogApi,
  type LessonDraft,
  type MaterialLinkPurpose,
  type MaterialUnit,
  type ResourcePack,
} from '../../api/catalog'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { useTeachingPrepLessonWorkbenchContext } from '../../workbench/routeContext'
import { parseAnimationPageText } from './animationPages'
import MaterialPagePreview from './MaterialPagePreview.vue'
import PptAnimationPagePicker, {
  type PptAnimationDraftTask,
  type PptAnimationPickerUnit,
} from './PptAnimationPagePicker.vue'

const LOCAL_SLIDE_CONTRACT_FINGERPRINT = '34b984e97a114fea15394bfdb1c73e8f667b340918e72c21bc15a21934a20a1b'
const ACTIVE_TASK_STATUSES = new Set(['prepared', 'queued', 'running', 'needs_input', 'proposal_ready'])
const ANIMATION_PAGE_LIMIT = 4
const ANIMATION_BILLED_LIMIT = 3

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()
const primaryPptLinkId = ref<string | null>(null)
const supportLinkIds = ref<string[]>([])
const submitting = ref(false)
const allowPptOnly = ref(false)
const DEFAULT_CONFIRM_MESSAGE = '先确认主课件和参考资料。勾选后点一次发送，AI 会改编课件。预计最多 6 次模型调用，按实际用量计费。失败不会自动再发，原 PPT 不会被改。'
const message = ref(DEFAULT_CONFIRM_MESSAGE)
const quickMaterialRecordId = ref('')
const quickStartUnit = ref<number | null>(null)
const quickEndUnit = ref<number | null>(null)
const quickPurpose = ref<MaterialLinkPurpose | ''>('')
const quickAdding = ref(false)
const quickMessage = ref('')
const quickRequestToken = ref<string | null>(null)
const quickUnits = ref<MaterialUnit[]>([])
const quickPreviewPage = ref(1)
const previewingLinkId = ref<string | null>(null)
const previewingLinkPage = ref(1)
const previewUnitsByLink = reactive<Record<string, MaterialUnit[]>>({})
const QUICK_PURPOSE_OPTIONS: Array<[Exclude<MaterialLinkPurpose, 'reference_ppt'>, string]> = [
  ['textbook', '教材依据'],
  ['exercise', '课堂练习'],
  ['answer', '答案 / 解析'],
  ['supplement', '补充资料'],
]
const exerciseRun = ref<ExerciseSuggestionRun | null>(null)
const exerciseEdits = reactive<Record<string, ExerciseSuggestionPayload>>({})
const exerciseAccepted = reactive<Record<string, boolean>>({})
const selectedExerciseCandidateIds = ref<string[]>([])
const exerciseSuggestionsConfirmed = ref(false)
const cancellingTask = ref(false)
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
let exerciseSession = 0
let pollAbort: AbortController | null = null

const preflight = computed(() => workbench.referencePreflight.value)
const references = computed(() => preflight.value?.catalog.material_links ?? [])
const referencePpts = computed(() => references.value.filter(item => item.purpose === 'reference_ppt'))
const primaryPpt = computed(() => (
  referencePpts.value.find(item => item.link_id === primaryPptLinkId.value) ?? null
))
const supportMaterials = computed(() => references.value.filter(item => item.purpose !== 'reference_ppt'))
const animationAllowedPages = computed(() => animationUnits.value.map(item => item.unit_index))
const animationSelectionLabel = computed(() => {
  const labels = animationDrafts.value.flatMap((task, index) => {
    const parsed = parseAnimationPageText(
      task.pageText,
      animationAllowedPages.value,
      ANIMATION_PAGE_LIMIT,
    )
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
  animationBusy.value
  || Boolean(runningAnimation.value)
  || Boolean(reviewingAnimation.value)
))
const quickMaterialCandidates = computed(() => {
  const semesterId = catalog.selectedSemester?.id
  const linkedVersionIds = new Set(references.value.map(item => item.material_version_id))
  if (!semesterId) return []
  return catalog.semesterMaterials.flatMap((record) => {
    if (
      record.semester_id !== semesterId
      || !record.is_active
      || record.parse_status !== 'parsed'
      || record.has_unparsed_update
      || !record.current_material_version_id
      || (record.current_unit_count ?? 0) < 1
      || linkedVersionIds.has(record.current_material_version_id)
      || record.material_role === 'reference_ppt'
    ) return []
    const material = catalog.materials.find(item => (
      item.id === record.current_material_version_id
      && item.availability === 'available'
      && item.material_type !== 'pptx'
      && !item.source_archived_at
    ))
    return material ? [{ record, material }] : []
  })
})
const quickMaterialCandidate = computed(() => quickMaterialCandidates.value.find(
  item => item.record.id === quickMaterialRecordId.value,
) ?? null)
const quickMaterialMaximum = computed(() => (
  quickMaterialCandidate.value?.record.current_unit_count
  ?? quickMaterialCandidate.value?.material.unit_count
  ?? 0
))
const quickPreviewReady = computed(() => quickUnits.value.some(item => item.preview_url))
const selectedLinkIds = computed(() => [
  ...(primaryPptLinkId.value ? [primaryPptLinkId.value] : []),
  ...supportLinkIds.value,
])
const parsedSemesterMaterials = computed(() => {
  const semesterId = catalog.selectedSemester?.id
  return catalog.semesterMaterials.filter(item => (
    item.semester_id === semesterId
    && item.is_active
    && item.parse_status === 'parsed'
    && !item.has_unparsed_update
    && item.current_material_version_id === item.last_parsed_version_id
  ))
})
const requiredSupportRecords = computed(() => parsedSemesterMaterials.value.filter(item => (
  item.material_role === 'textbook'
  || item.material_role === 'exercise_workbook'
  || item.material_role === 'homework_workbook'
)))
const selectedSupportMaterials = computed(() => supportMaterials.value.filter(
  item => supportLinkIds.value.includes(item.link_id),
))
const selectedExerciseMaterialVersionIds = computed(() => new Set(
  selectedSupportMaterials.value
    .filter(item => item.purpose === 'exercise')
    .map(item => item.material_version_id),
))
const reusableExerciseCandidates = computed(() => (catalog.exerciseCandidates ?? []).filter(item => (
  item.is_active
  && item.selection_status !== 'excluded'
  && item.question_regions.some(region => selectedExerciseMaterialVersionIds.value.has(region.material_version_id))
)))
const hasSelectedExercisePages = computed(() => selectedExerciseMaterialVersionIds.value.size > 0)
const selectedTextbookMaterialVersionIds = computed(() => new Set(
  selectedSupportMaterials.value
    .filter(item => item.purpose === 'textbook')
    .map(item => item.material_version_id),
))
const hasSelectedQuestionSources = computed(() => (
  hasSelectedExercisePages.value || selectedTextbookMaterialVersionIds.value.size > 0
))
const pendingExerciseSuggestions = computed(() => exerciseRun.value?.status === 'succeeded'
  ? exerciseRun.value.suggestions.filter(item => item.decision === 'pending')
  : [])
const missingSupportLabels = computed(() => {
  if (!requiredSupportRecords.value.length) {
    return selectedSupportMaterials.value.length ? [] : ['教材或参考教辅']
  }
  return requiredSupportRecords.value.flatMap((record) => {
    const purpose = record.material_role === 'textbook' ? 'textbook' : 'exercise'
    const ready = selectedSupportMaterials.value.some(item => (
      item.purpose === purpose
      && item.material_version_id === record.last_parsed_version_id
    ))
    if (ready) return []
    const prefix = record.material_role === 'textbook' ? '教材' : '参考教辅'
    return [`${prefix}（${record.display_name}）`]
  })
})
const supportReady = computed(() => missingSupportLabels.value.length === 0)
const selectedDraft = computed(() => catalog.lessonDrafts.find(
  item => item.id === catalog.selectedLessonDraftId,
) ?? catalog.lessonDrafts.find(item => item.status === 'confirmed') ?? null)
const currentSlideTask = computed(() => {
  const lesson = catalog.selectedLesson
  const draft = selectedDraft.value
  if (!lesson || !draft || workbench.dirtyReason.value) return null
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
  Boolean(primaryPptLinkId.value)
  && Boolean(catalog.teachingPreferences?.payload)
  && (supportReady.value || allowPptOnly.value)
  && !submitting.value
  && exerciseRun.value?.status !== 'running'
))
const taskStatusLabel = computed(() => {
  const status = currentSlideTask.value?.status
  if (status === 'prepared') return '等待发送'
  if (status === 'queued') return '等待处理'
  if (status === 'running') return 'AI 正在改编'
  if (status === 'needs_input') return '等待你确认'
  if (status === 'proposal_ready') return '改编建议已返回'
  return null
})

watch(preflight, (next) => {
  if (!next) return
  const savedIds = next.draft?.payload.material_selections.map(item => item.link_id) ?? []
  const savedPrimary = referencePpts.value.find(item => savedIds.includes(item.link_id))
  primaryPptLinkId.value = savedPrimary?.link_id ?? referencePpts.value[0]?.link_id ?? null
  supportLinkIds.value = supportMaterials.value
    .filter(item => savedIds.length === 0 || savedIds.includes(item.link_id))
    .map(item => item.link_id)
  const savedExerciseIds = next.draft?.payload.exercise_candidate_ids ?? []
  selectedExerciseCandidateIds.value = savedExerciseIds.length
    ? [...savedExerciseIds]
    : reusableExerciseCandidates.value.map(item => item.id)
}, { immediate: true })

watch(quickMaterialCandidates, (candidates) => {
  if (!candidates.some(item => item.record.id === quickMaterialRecordId.value)) {
    quickMaterialRecordId.value = ''
    quickPurpose.value = ''
    quickStartUnit.value = null
    quickEndUnit.value = null
  }
})

watch(supportReady, (ready) => {
  if (ready) allowPptOnly.value = false
})

function toPickerUnits(
  units: Array<{
    id?: string
    unit_id?: string
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
      id: item.id ?? item.unit_id,
      unit_index: item.unit_index,
      preview_url: item.preview_url,
      title: item.title ?? null,
      object_summary: item.object_summary,
      revision: item.revision,
    }))
}

watch(
  () => primaryPpt.value?.link_id ?? '',
  async (linkId) => {
    animationDrafts.value = []
    animationUnits.value = []
    animationLoadError.value = ''
    const item = primaryPpt.value
    animationPage.value = item?.start_unit ?? 1
    if (!linkId || !item || item.link_id !== linkId) return
    const linked = toPickerUnits(item.units, item.start_unit, item.end_unit)
    if (linked.length) {
      animationUnits.value = linked
      animationPage.value = linked[0]?.unit_index ?? item.start_unit
      return
    }
    animationLoading.value = true
    try {
      const units = await teachingPrepCatalogApi.listMaterialUnits(item.material_version_id)
      if (primaryPptLinkId.value !== linkId) return
      const picked = toPickerUnits(units, item.start_unit, item.end_unit)
      animationUnits.value = picked
      animationPage.value = picked[0]?.unit_index ?? item.start_unit
      if (!picked.length) {
        animationLoadError.value = '这份主课件还没有可预览的页。请先到资料库生成预览。'
      }
    } catch {
      if (primaryPptLinkId.value !== linkId) return
      animationLoadError.value = '主课件预览没有打开。请先到资料库确认这份课件已生成预览。'
    } finally {
      if (primaryPptLinkId.value === linkId) animationLoading.value = false
    }
  },
  { immediate: true },
)

watch(quickMaterialRecordId, async (recordId) => {
  quickPurpose.value = ''
  quickStartUnit.value = null
  quickEndUnit.value = null
  quickMessage.value = ''
  quickUnits.value = []
  quickPreviewPage.value = 1
  if (!recordId) return
  const versionId = quickMaterialCandidates.value.find(
    item => item.record.id === recordId,
  )?.record.current_material_version_id
  if (!versionId) return
  try {
    const units = await teachingPrepCatalogApi.listMaterialUnits(versionId)
    if (quickMaterialRecordId.value !== recordId) return
    quickUnits.value = units
    quickPreviewPage.value = units[0]?.unit_index ?? 1
    if (!units.some(item => item.preview_url)) {
      quickMessage.value = '这份资料还没有原页图。请先到资料库生成预览，再按页加入。'
    }
  } catch {
    if (quickMaterialRecordId.value !== recordId) return
    quickMessage.value = '原页预览没有打开。请先到资料库确认这份资料已生成预览。'
  }
})

watch(quickStartUnit, (start) => {
  if (typeof start === 'number' && start >= 1) quickPreviewPage.value = start
})

watch(
  [quickMaterialRecordId, quickPurpose, quickStartUnit, quickEndUnit],
  () => { quickRequestToken.value = null },
  { flush: 'sync' },
)

async function addQuickMaterialLink(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const candidate = quickMaterialCandidate.value
  const maximum = quickMaterialMaximum.value
  const startUnit = Math.trunc(Number(quickStartUnit.value))
  const endUnit = Math.trunc(Number(quickEndUnit.value))
  if (!lessonId || !candidate) {
    quickMessage.value = '请先选择一份本学期已解析资料。'
    return
  }
  if (!quickPurpose.value) {
    quickMessage.value = '请选择这段资料在本课时中的用途。'
    return
  }
  if (startUnit < 1 || endUnit < startUnit || endUnit > maximum) {
    quickMessage.value = `请输入 1—${maximum} 内的连续页段。`
    return
  }
  if (!quickPreviewReady.value) {
    quickMessage.value = '没有原页图时不能按页码加入。请先到资料库生成预览。'
    return
  }
  quickAdding.value = true
  quickMessage.value = '正在保存这段资料与当前课时的对应关系……'
  const requestToken = quickRequestToken.value
    ?? `lesson-material-link-${crypto.randomUUID().replaceAll('-', '')}`
  quickRequestToken.value = requestToken
  try {
    await teachingPrepCatalogApi.createMaterialLink(lessonId, {
      request_token: requestToken,
      material_version_id: candidate.record.current_material_version_id,
      start_unit: startUnit,
      end_unit: endUnit,
      crop: null,
      purpose: quickPurpose.value,
      teacher_note: null,
      confirmation_status: 'confirmed',
    })
    await workbench.refresh()
    quickRequestToken.value = null
    quickMessage.value = `已加入第 ${startUnit}—${endUnit} 页；只有教师明确勾选后才会进入 AI 发送范围。`
  } catch (error) {
    quickMessage.value = error instanceof Error && error.message
      ? `尚未加入：${error.message}`
      : '尚未加入，请检查资料版本后重试。'
  } finally {
    quickAdding.value = false
  }
}

function previewUnitsFor(item: ReferenceMaterialLink): Array<{
  id?: string
  unit_index: number
  preview_url: string
  title?: string | null
  object_summary?: Record<string, unknown>
  revision?: number
}> {
  if (previewUnitsByLink[item.link_id]?.length) return previewUnitsByLink[item.link_id] ?? []
  return item.units.map(unit => ({
    id: unit.unit_id,
    unit_index: unit.unit_index,
    preview_url: unit.preview_url,
    title: unit.title,
    object_summary: unit.object_summary,
  }))
}

function patchPreviewUnit(
  collection: PptAnimationPickerUnit[] | MaterialUnit[],
  next: PptAnimationPickerUnit | MaterialUnit,
): typeof collection {
  const nextId = 'id' in next && next.id ? next.id : ''
  return collection.map((item) => {
    const itemId = 'id' in item ? item.id : ''
    if (nextId && itemId === nextId) return { ...item, ...next }
    if (item.unit_index === next.unit_index && item.preview_url === next.preview_url) {
      return { ...item, ...next }
    }
    return item
  })
}

function applyPreviewUnit(next: PptAnimationPickerUnit): void {
  animationUnits.value = patchPreviewUnit(animationUnits.value, next) as PptAnimationPickerUnit[]
  if (previewingLinkId.value) {
    const current = previewUnitsByLink[previewingLinkId.value]
    if (current?.length) {
      previewUnitsByLink[previewingLinkId.value] = patchPreviewUnit(
        current,
        next,
      ) as MaterialUnit[]
    }
  }
}

async function toggleLinkPreview(item: ReferenceMaterialLink): Promise<void> {
  if (previewingLinkId.value === item.link_id) {
    previewingLinkId.value = null
    return
  }
  previewingLinkId.value = item.link_id
  previewingLinkPage.value = item.start_unit
  if (item.units.length || previewUnitsByLink[item.link_id]?.length) return
  try {
    const units = await teachingPrepCatalogApi.listMaterialUnits(item.material_version_id)
    previewUnitsByLink[item.link_id] = units.filter(unit => (
      unit.unit_index >= item.start_unit && unit.unit_index <= item.end_unit
    ))
    if (previewingLinkId.value === item.link_id && !previewUnitsByLink[item.link_id]?.length) {
      message.value = '这几页还没有原页图。请先到资料库生成预览后再核对。'
    }
  } catch {
    if (previewingLinkId.value === item.link_id) {
      message.value = '原页预览没有打开。请先到资料库确认这份资料已生成预览。'
    }
  }
}

async function removeMaterialLink(linkId: string): Promise<void> {
  const link = catalog.materialLinks.find(item => item.id === linkId)
  if (!link) return
  if (!window.confirm('移除后本课不再关联该资料（资料本身保留在资料库）。确定移除吗？')) return
  try {
    await catalog.deactivateMaterialLink(link)
    await workbench.refresh()
    message.value = `已移除「${link.material_name}」与本课的关联。`
  } catch {
    message.value = catalog.errorMessage || '关联没有移除，请刷新后重试。'
  }
}

function selectPrimaryPpt(linkId: string): void {
  primaryPptLinkId.value = linkId
  if (previewingLinkId.value === linkId) previewingLinkId.value = null
  workbench.setDirty('资料对应关系')
  message.value = '已更换主课件；发送后 AI 将以这份 PPT 为删改底稿。'
}

function toggleSupport(): void {
  workbench.setDirty('资料对应关系')
  message.value = '参考资料选择已调整；未勾选的资料不会发送给 AI。'
}

function selectionPayload(): ReferenceSelectionPayload {
  const preferences = catalog.teachingPreferences?.payload
  if (!preflight.value || !preferences) throw new Error('资料或个人备课偏好尚未载入')
  return {
    material_selections: references.value
      .filter(item => selectedLinkIds.value.includes(item.link_id))
      .map(item => ({
        link_id: item.link_id,
        start_unit: item.start_unit,
        end_unit: item.end_unit,
        ...(item.purpose === 'reference_ppt' ? { ppt_intent: 'keep' as const } : {}),
      })),
    exercise_candidate_ids: [...selectedExerciseCandidateIds.value],
    question_ids: [],
    assessment_ids: [],
    knowledge_scope: [],
    preparation_preferences: preferences,
    class_name: null,
    teacher_context: null,
  }
}

function selectedIdsInPack(pack: ResourcePack): string[] {
  const materials = Array.isArray(pack.payload.materials) ? pack.payload.materials : []
  return materials.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const id = (item as Record<string, unknown>).link_id
    return typeof id === 'string' ? [id] : []
  }).sort()
}

function exerciseIdsInPack(pack: ResourcePack): string[] {
  const selection = pack.payload.selection
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return []
  const ids = (selection as Record<string, unknown>).exercise_candidate_ids
  return Array.isArray(ids)
    ? ids.filter((item): item is string => typeof item === 'string').sort()
    : []
}

function contextInPack(pack: ResourcePack): string | null {
  const classroom = pack.payload.classroom
  if (!classroom || typeof classroom !== 'object' || Array.isArray(classroom)) return null
  const value = (classroom as Record<string, unknown>).teacher_context
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function matchingCurrentPack(teacherContext: string | null = null): ResourcePack | null {
  if (catalog.resourcePackStatus?.local_sources_changed) return null
  const expected = [...selectedLinkIds.value].sort().join('|')
  const expectedExercises = [...selectedExerciseCandidateIds.value].sort().join('|')
  const expectedContext = teacherContext?.trim() || null
  return catalog.resourcePacks.find(pack => (
    selectedIdsInPack(pack).join('|') === expected
    && exerciseIdsInPack(pack).join('|') === expectedExercises
    && contextInPack(pack) === expectedContext
  )) ?? null
}

function isTeachingPrepConflictError(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.status === 409
    && error.kind === 'conflict'
    && error.code === 'teaching_prep_conflict'
}

async function saveConfirmedSelection(allowConflictRetry = true): Promise<ReferenceSelectionDraft> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId || !preflight.value) throw new Error('请先选择课时')
  const savedExerciseCandidateIds = [...selectedExerciseCandidateIds.value]
  let saved: ReferenceSelectionDraft
  try {
    saved = await teachingPrepWorkbenchApi.saveReferenceDraft(lessonId, {
      expected_revision: preflight.value.draft?.revision ?? null,
      source_state_sha256: preflight.value.source_state_sha256,
      selection: selectionPayload(),
    })
  } catch (error) {
    if (!allowConflictRetry || !isTeachingPrepConflictError(error)) throw error
    return retryConfirmedSelectionAfterRefresh()
  }
  workbench.setDirty(null)
  await workbench.refresh()
  await nextTick()
  selectedExerciseCandidateIds.value = savedExerciseCandidateIds
  return saved
}

// 冲突说明页面停留期间后端状态已被推进：刷新拿到新指纹后，按用户当前选择重试一次。
async function retryConfirmedSelectionAfterRefresh(): Promise<ReferenceSelectionDraft> {
  const snapshot = {
    primary: primaryPptLinkId.value,
    support: [...supportLinkIds.value],
    exerciseIds: [...selectedExerciseCandidateIds.value],
  }
  await workbench.refresh()
  // preflight 刷新会用已保存草稿重置本地勾选，先按用户当前选择恢复再重试。
  await nextTick()
  if (snapshot.primary && !referencePpts.value.some(item => item.link_id === snapshot.primary)) {
    throw new Error('页面数据刚有更新，你勾选的主课件已不在本课关联资料中，请确认选择后重新发送。')
  }
  if (snapshot.primary) primaryPptLinkId.value = snapshot.primary
  const availableSupport = new Set(supportMaterials.value.map(item => item.link_id))
  supportLinkIds.value = snapshot.support.filter(id => availableSupport.has(id))
  selectedExerciseCandidateIds.value = snapshot.exerciseIds
  return saveConfirmedSelection(false)
}

async function ensureResourcePack(teacherContext: string | null = null): Promise<ResourcePack> {
  const lessonId = routeState.currentLessonId.value
  const preferences = catalog.teachingPreferences?.payload
  if (!lessonId || !preferences) throw new Error('课时或个人备课偏好尚未载入')
  const current = matchingCurrentPack(teacherContext)
  if (current) {
    await catalog.selectResourcePack(current)
    return current
  }
  const referencePptIntents = primaryPptLinkId.value
    ? { [primaryPptLinkId.value]: 'keep' as const }
    : {}
  await teachingPrepWorkbenchApi.resourcePackPreflight(lessonId, {
    reference_ppt_intents: referencePptIntents,
    selected_material_link_ids: selectedLinkIds.value,
    selected_exercise_candidate_ids: [...selectedExerciseCandidateIds.value],
  })
  await catalog.freezeResourcePack({
    class_name: null,
    lesson_type: 'new_lesson',
    teacher_context: teacherContext,
    reference_ppt_intents: referencePptIntents,
    question_ids: [],
    assessment_ids: [],
    knowledge_scope: [],
    preparation_preferences: preferences,
    selected_material_link_ids: selectedLinkIds.value,
    selected_exercise_candidate_ids: [...selectedExerciseCandidateIds.value],
  })
  const created = catalog.resourcePacks[0]
  if (!created) throw new Error('资料快照没有成功建立')
  return created
}

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
      await catalog.reviseLessonDraft(
        generated,
        generated.payload,
        true,
      )
    }
    draft = catalog.lessonDrafts.find(item => (
      item.resource_pack_id === pack.id && item.status === 'confirmed'
    )) ?? null
  }
  if (!draft) throw new Error('内部课件结构没有准备完成')
  await catalog.selectLessonDraft(draft)
  return draft
}

function cloneSuggestion(payload: ExerciseSuggestionPayload): ExerciseSuggestionPayload {
  return JSON.parse(JSON.stringify(payload)) as ExerciseSuggestionPayload
}

function initializeExerciseReview(run: ExerciseSuggestionRun): void {
  exerciseRun.value = run
  exerciseSuggestionsConfirmed.value = false
  for (const suggestion of run.suggestions) {
    exerciseEdits[suggestion.id] = cloneSuggestion(suggestion.original_payload)
    exerciseAccepted[suggestion.id] = true
  }
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError')
    || (error instanceof Error && error.name === 'AbortError')
  )
}

function beginExerciseSession(): { session: number; signal: AbortSignal } {
  exerciseSession += 1
  pollAbort?.abort()
  pollAbort = new AbortController()
  return { session: exerciseSession, signal: pollAbort.signal }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    const handle = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    function onAbort() {
      window.clearTimeout(handle)
      reject(new DOMException('aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function exerciseRunFailureMessage(run: ExerciseSuggestionRun): string {
  if (run.status === 'result_unknown' || run.error_code === 'application_restarted') {
    return '应用重启后，这次教辅识题结果可能未知。系统没有自动重发，请确认后重新识别。'
  }
  if (run.error_code === 'model_timeout') {
    return '教辅识题等待超时，没有自动重试。当前勾选仍保留，可重新识别。'
  }
  if (run.error_code === 'model_response_invalid') {
    return '模型已返回，但教辅识题结果无法使用。没有自动重试，可重新识别。'
  }
  return '教辅识题没有完成，系统没有自动重试。当前勾选仍保留，可重新识别。'
}

async function waitForExerciseRun(
  run: ExerciseSuggestionRun,
  signal: AbortSignal,
): Promise<ExerciseSuggestionRun> {
  let current = run
  for (let attempt = 0; attempt < 120 && current.status === 'running'; attempt += 1) {
    exerciseRun.value = current
    await wait(1_000, signal)
    current = await teachingPrepWorkbenchApi.exerciseSuggestionRun(current.id, signal)
  }
  return current
}

async function applyExerciseRun(
  run: ExerciseSuggestionRun,
  session: number,
): Promise<void> {
  if (session !== exerciseSession) return
  if (run.status === 'succeeded') {
    const pending = run.suggestions.filter(item => item.decision === 'pending')
    if (!pending.length) {
      exerciseRun.value = run
      message.value = run.suggestions.length
        ? '上次题目识别已经完成，发送时会自动带入改编。'
        : '上次没有从教材或教辅中找到可插入的题。仍可发送课件改编。'
      return
    }
    initializeExerciseReview(run)
    message.value = `上次已识别到 ${pending.length} 道题。点发送后会自动带入改编，不需要再核对裁切。`
    return
  }
  if (run.status === 'running') {
    exerciseRun.value = run
    message.value = 'AI 正在从教材和教辅中识别题目（发送后自动带入改编）。离开本页后再回来也会继续显示进度。'
    return
  }
  if (run.status === 'cancelled') {
    exerciseRun.value = null
    message.value = '题目识别已取消；已保存的资料对应关系不受影响。'
    return
  }
  exerciseRun.value = null
  message.value = exerciseRunFailureMessage(run)
}

async function restoreLatestExerciseRun(): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) return
  const { session, signal } = beginExerciseSession()
  try {
    const run = await teachingPrepWorkbenchApi.latestExerciseSuggestionRun(lessonId, signal)
    if (session !== exerciseSession) return
    if (run.status === 'running') {
      await applyExerciseRun(run, session)
      const finished = await waitForExerciseRun(run, signal)
      await applyExerciseRun(finished, session)
      return
    }
    await applyExerciseRun(run, session)
  } catch (error) {
    if (session !== exerciseSession || isAbortError(error)) return
    if (isAuthoritativeNotFoundError(error, 'teaching_prep_not_found')) {
      if (!exerciseRun.value) message.value = DEFAULT_CONFIRM_MESSAGE
    }
  }
}

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
  animationDrafts.value = [
    ...animationDrafts.value,
    { id: crypto.randomUUID(), pageText: '' },
  ]
}

function updateAnimationDraftText(taskId: string, pageText: string): void {
  animationDrafts.value = animationDrafts.value.map(item => (
    item.id === taskId ? { ...item, pageText } : item
  ))
}

function removeAnimationDraft(taskId: string): void {
  animationDrafts.value = animationDrafts.value.filter(item => item.id !== taskId)
}

async function generateAnimation(taskId: string): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  const link = primaryPpt.value
  const task = animationDrafts.value.find(item => item.id === taskId)
  const parsed = parseAnimationPageText(
    task?.pageText ?? '',
    animationAllowedPages.value,
    ANIMATION_PAGE_LIMIT,
  )
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
      materialLinkId: link.link_id,
      pageIndexes: [...parsed.pages],
    })
    animationRuns.value = [run, ...animationRuns.value.filter(item => item.id !== run.id)]
    for (let attempt = 0; attempt < 120 && run.status === 'running'; attempt += 1) {
      await wait(1_000)
      run = await teachingPrepWorkbenchApi.slideAnimationRun(run.id)
      animationRuns.value = [run, ...animationRuns.value.filter(item => item.id !== run.id)]
    }
    await loadAnimationRuns()
    if (!animationRuns.value.some(item => item.id === run.id)) {
      animationRuns.value = [run, ...animationRuns.value]
    }
    if (run.status === 'succeeded') {
      animationMessage.value = '课堂动画草稿已生成。请预览后再保存为本课附件；这次调用不改课件副本。'
      return
    }
    if (run.status === 'running') {
      animationMessage.value = '课堂动画等待超时，没有自动重试。已填页码仍保留。'
      return
    }
    animationMessage.value = run.status === 'failed'
      ? `课堂动画没有生成：${run.error_code ?? '模型未返回可用分镜'}。已填页码仍保留，不会自动重试。`
      : `课堂动画未完成：${run.error_code ?? run.status}`
  } catch (error) {
    animationMessage.value = error instanceof Error && error.message
      ? `课堂动画没有发出。请确认页码为 1—4 页、每页都能打开原图，且本课还没超过 ${animationBilledLimit.value} 次计费发送。`
      : `课堂动画没有发出。本课最多 ${animationBilledLimit.value} 次计费发送，失败不自动重试。`
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
  () => {
    exerciseRun.value = null
    exerciseSuggestionsConfirmed.value = false
    void loadAnimationRuns()
    void restoreLatestExerciseRun()
  },
  { immediate: true },
)

onUnmounted(() => {
  pollAbort?.abort()
})

async function identifyWorkbookQuestions(draft: ReferenceSelectionDraft): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) throw new Error('当前课时已失效')
  const { session, signal } = beginExerciseSession()
  message.value = 'AI 正在从教材和教辅中识别题目……'
  const snapshot = await teachingPrepWorkbenchApi.freezeReferenceSnapshot(
    lessonId,
    `exercise-snapshot-${crypto.randomUUID().replaceAll('-', '')}`,
    draft.revision,
  )
  if (session !== exerciseSession || signal.aborted) {
    throw new DOMException('aborted', 'AbortError')
  }
  let run = await teachingPrepWorkbenchApi.startExerciseSuggestions(
    snapshot.id,
    `exercise-suggestions-${crypto.randomUUID().replaceAll('-', '')}`,
  )
  if (session !== exerciseSession) throw new DOMException('aborted', 'AbortError')
  exerciseRun.value = run.status === 'running' ? run : exerciseRun.value
  run = await waitForExerciseRun(run, signal)
  if (session !== exerciseSession) throw new DOMException('aborted', 'AbortError')
  if (run.status !== 'succeeded') {
    exerciseRun.value = null
    throw new Error(run.status === 'running'
      ? '题目识别等待超时，没有自动重试'
      : `题目识别未完成：${run.error_code ?? run.status}`)
  }
  if (!run.suggestions.length) {
    exerciseRun.value = run
    message.value = '没有从教材或教辅中找到可插入的题，继续生成课件改编。'
    return
  }
  initializeExerciseReview(run)
  message.value = `已识别到 ${run.suggestions.length} 道题，正在自动带入改编。`
}

async function cancelExerciseRun(): Promise<void> {
  const run = exerciseRun.value
  if (!run || run.status !== 'running') return
  cancellingTask.value = true
  try {
    await teachingPrepWorkbenchApi.cancelExerciseSuggestions(run.id)
    exerciseSession += 1
    pollAbort?.abort()
    exerciseRun.value = null
    message.value = '题目识别已取消；已保存的资料对应关系不受影响。'
  } catch {
    message.value = '识题任务暂时没有取消成功，请稍后重试。'
  } finally {
    cancellingTask.value = false
  }
}

async function cancelSlideTask(): Promise<void> {
  const task = currentSlideTask.value
  if (!task) return
  cancellingTask.value = true
  try {
    await aiTasks.cancel(task.task_id)
    message.value = '改编任务已请求取消；已保存的资料对应关系不受影响。'
  } catch {
    message.value = '改编任务暂时没有取消成功，请稍后重试。'
  } finally {
    cancellingTask.value = false
  }
}

async function confirmWorkbookQuestions(): Promise<void> {
  const run = exerciseRun.value
  if (!run || !pendingExerciseSuggestions.value.length) return
  const candidateIds: string[] = []
  for (const suggestion of pendingExerciseSuggestions.value) {
    const accepted = exerciseAccepted[suggestion.id] !== false
    const reviewed = await teachingPrepWorkbenchApi.reviewExerciseSuggestion(
      suggestion.id,
      accepted
        ? {
            expected_revision: suggestion.revision,
            decision: 'modified',
            teacher_payload: cloneSuggestion(exerciseEdits[suggestion.id] ?? suggestion.original_payload),
            rejection_reason: null,
          }
        : {
            expected_revision: suggestion.revision,
            decision: 'rejected',
            teacher_payload: null,
            rejection_reason: '教师未选入本次课件改编',
          },
    )
    if (reviewed.exercise_candidate_id) candidateIds.push(reviewed.exercise_candidate_id)
  }
  exerciseSuggestionsConfirmed.value = true
  if (!candidateIds.length) {
    await workbench.refresh()
    await nextTick()
    return
  }
  await workbench.refresh()
  await nextTick()
  // refresh may replace the preflight object and trigger its watcher.
  // Restore the just-created candidates before saving the selection.
  selectedExerciseCandidateIds.value = candidateIds
}

function withSemesterContext(
  refs: Array<{ kind: string; id: string; revision: string }>,
): Array<{ kind: string; id: string; revision: string }> {
  const semester = catalog.selectedSemester
  return semester
    ? [...refs, { kind: 'semester', id: semester.id, revision: String(semester.revision) }]
    : refs
}

async function confirmAndSend(forceNew = false, teacherContext: string | null = null): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  message.value = '正在保存资料对应关系并准备 AI 改编任务……'
  try {
    const savedSelection = await saveConfirmedSelection()
    if (
      hasSelectedQuestionSources.value
      && !allowPptOnly.value
      && selectedExerciseCandidateIds.value.length === 0
    ) {
      if (!exerciseRun.value || exerciseRun.value.status !== 'succeeded') {
        await identifyWorkbookQuestions(savedSelection)
      }
      if (pendingExerciseSuggestions.value.length && !exerciseSuggestionsConfirmed.value) {
        message.value = '正在把识别到的题目带入改编……'
        await confirmWorkbookQuestions()
        await saveConfirmedSelection()
      }
    }
    const pack = await ensureResourcePack(teacherContext)
    const draft = await ensureConfirmedDraft(pack)
    const existing = !forceNew ? aiTasks.orderedTasks.find(task => (
      task.module === 'teaching_prep'
      && task.task_kind === 'teaching_prep.slide_change_proposal'
      && task.source_ref.kind === 'lesson'
      && task.source_ref.id === routeState.currentLessonId.value
      && task.context_refs.some(ref => ref.kind === 'lesson_draft' && ref.id === draft.id)
      && ACTIVE_TASK_STATUSES.has(task.status)
    )) : undefined
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
      message.value = '资料已确认，AI 改编已经开始。完成后会进入对照页。'
    }
    await routeState.setStep(2)
  } catch (error) {
    if (isAbortError(error)) return
    if (isTeachingPrepConflictError(error)) {
      message.value = '页面数据刚有更新，已为你刷新，请确认选择后重新发送。'
      return
    }
    message.value = error instanceof Error && error.message
      ? `尚未发送：${error.message}`
      : '尚未发送。当前勾选仍保留，请检查资料后重试。'
  } finally {
    submitting.value = false
  }
}

async function runPrimaryAction(): Promise<void> {
  const current = currentSlideTask.value
  if (current && current.status !== 'proposal_ready') {
    await routeState.setStep(2)
    return
  }
  await confirmAndSend(current?.status === 'proposal_ready')
}

function restartExerciseRecognition(): void {
  exerciseSession += 1
  pollAbort?.abort()
  selectedExerciseCandidateIds.value = []
  exerciseRun.value = null
  exerciseSuggestionsConfirmed.value = false
  message.value = '已改为重新识别当前勾选的教材和教辅原页；下一步发送时会自动带入改编。'
}

const primaryActionLabel = computed(() => {
  if (submitting.value) return '正在准备…'
  if (exerciseRun.value?.status === 'running') return '正在识别题目…'
  if (currentSlideTask.value?.status === 'proposal_ready') {
    return '重新生成 AI 改编（最多 6 次）'
  }
  if (currentSlideTask.value) return '查看正在处理的改编'
  return '发给 AI 改编'
})

const purposeLabels: Record<string, string> = {
  textbook: '教材依据',
  reference_ppt: '参考课件',
  exercise: '课堂练习',
  answer: '答案 / 解析',
  supplement: '补充资料',
}

defineExpose({
  primaryLabel: primaryActionLabel,
  primaryDisabled: computed(() => !canSubmit.value),
  inspectorMessage: message,
  runPrimary: runPrimaryAction,
})
</script>

<template>
  <div class="tp-step-canvas">
    <section class="tp-panel" aria-label="本课关联资料">
      <div class="tp-panel__head">
        <h2>本课关联资料</h2>
        <span class="tp-panel__hint">主课件一份，参考资料不限；不合适的可直接移除</span>
      </div>
      <div class="tp-panel__body">
        <div v-if="!referencePpts.length" class="tp-banner tp-banner--warn" role="alert">
          本课时还没有对应的参考 PPT。请先到资料库完成课时对应，之后再发送给 AI。
          <AppButton variant="secondary" @click="routeState.openLibrary()">打开资料库</AppButton>
        </div>
        <div v-if="missingSupportLabels.length" class="tp-banner tp-banner--warn" role="alert">
          <div>
            <strong>还缺少{{ missingSupportLabels.join('、') }}的已确认页段</strong>
            <p>先在资料库让 AI 根据目录、正文锚点和页码提出课时对应；页码有误时可以人工修正。</p>
          </div>
          <AppButton
            variant="secondary"
            data-testid="open-material-mapping"
            @click="routeState.openLibrary()"
          >
            去资料库自动判断页段
          </AppButton>
          <label class="tp-field--check">
            <input v-model="allowPptOnly" data-testid="allow-ppt-only" type="checkbox">
            我确认本次暂时只依据 PPT 生成降级建议
          </label>
          <p v-if="allowPptOnly" class="tp-error-text">本次只依据 PPT，可能偏离教材与教辅；生成后需要重点复核。</p>
        </div>
        <div v-for="item in references" :key="item.link_id" class="tp-link-block">
          <div class="tp-link-row">
            <div class="tp-link-row__meta">
              <strong>{{ item.material_name }}</strong>
              <small>第 {{ item.start_unit }}—{{ item.end_unit }} 页 · {{ item.material_type.toUpperCase() }}</small>
            </div>
            <StatusBadge
              :tone="item.purpose === 'reference_ppt' ? 'success' : 'info'"
              :label="purposeLabels[item.purpose] ?? item.purpose"
            />
            <AppButton
              v-if="item.link_id !== primaryPptLinkId"
              variant="ghost"
              data-testid="preview-lesson-material"
              @click="toggleLinkPreview(item)"
            >
              {{ previewingLinkId === item.link_id ? '收起预览' : '预览这几页' }}
            </AppButton>
            <span v-else class="tp-muted">在下方发送范围确认中查看</span>
            <AppButton variant="ghost" class="tp-danger-text" @click="removeMaterialLink(item.link_id)">移除</AppButton>
          </div>
          <MaterialPagePreview
            v-if="previewingLinkId === item.link_id && item.link_id !== primaryPptLinkId"
            :units="previewUnitsFor(item)"
            :page="previewingLinkPage"
            :range-label="`本课关联第 ${item.start_unit}—${item.end_unit} 页`"
            @update:page="previewingLinkPage = $event"
            @update:unit="applyPreviewUnit"
          />
        </div>
        <p v-if="!references.length" class="tp-muted">本课还没有关联资料，请在下方添加。</p>
      </div>
    </section>

    <section class="tp-panel" aria-label="添加资料">
      <div class="tp-panel__head">
        <h2>添加资料</h2>
        <span class="tp-panel__hint">只加入教材、教辅等参考页段。课件已在本课主资料里，不在这里添加；没有原页图时不能按页码加入</span>
      </div>
      <div class="tp-panel__body">
        <div class="tp-form-line">
          <label class="tp-field">
            已解析资料
            <select v-model="quickMaterialRecordId" data-testid="lesson-material-candidate">
              <option value="">请选择资料</option>
              <option v-for="item in quickMaterialCandidates" :key="item.record.id" :value="item.record.id">
                {{ item.record.display_name }} · 共 {{ item.record.current_unit_count }} 页
              </option>
            </select>
          </label>
          <label class="tp-field">
            本课时用途
            <select v-model="quickPurpose" data-testid="lesson-material-purpose">
              <option value="">请选择用途</option>
              <option v-for="purpose in QUICK_PURPOSE_OPTIONS" :key="purpose[0]" :value="purpose[0]">
                {{ purpose[1] }}
              </option>
            </select>
          </label>
          <label class="tp-field">
            起始页
            <input v-model.number="quickStartUnit" data-testid="lesson-material-range" type="number" min="1" :max="quickMaterialMaximum || 1">
          </label>
          <label class="tp-field">
            结束页
            <input v-model.number="quickEndUnit" data-testid="lesson-material-range" type="number" min="1" :max="quickMaterialMaximum || 1">
          </label>
          <AppButton
            variant="secondary"
            data-testid="add-lesson-material"
            :disabled="quickAdding || !quickPreviewReady"
            @click="addQuickMaterialLink"
          >
            {{ quickAdding ? '正在加入…' : '确认页段并加入本课时' }}
          </AppButton>
        </div>
        <MaterialPagePreview
          v-if="quickMaterialCandidate"
          :units="quickUnits"
          :page="quickPreviewPage"
          :range-label="quickMaterialCandidate.record.display_name"
          @update:page="quickPreviewPage = $event"
        />
        <p v-if="quickMessage" class="tp-inline-message" role="status">{{ quickMessage }}</p>
        <p v-else-if="quickMaterialCandidates.length === 0" class="tp-muted">当前没有尚未关联的教材或教辅。课件请用本课已对应的主课件，不在这里添加。</p>
      </div>
    </section>

    <section class="tp-panel" aria-label="发送范围确认">
      <div class="tp-panel__head">
        <h2>发送范围确认</h2>
        <span class="tp-panel__hint">系统不会读取未勾选资料；原 PPT 始终只读</span>
      </div>
      <div class="tp-panel__body">
        <div class="tp-source-groups">
          <div class="tp-source-group">
            <p class="tp-source-group__label">主课件<small>只能选一份，AI 将在它的副本上改编</small></p>
            <label v-for="item in referencePpts" :key="item.link_id" class="tp-check-row tp-check-row--primary">
              <input
                type="radio"
                name="primary-reference-ppt"
                :checked="primaryPptLinkId === item.link_id"
                @change="selectPrimaryPpt(item.link_id)"
              >
              <span><strong>{{ item.material_name }}</strong><small>第 {{ item.start_unit }}—{{ item.end_unit }} 页 · PPT</small></span>
            </label>
            <PptAnimationPagePicker
              v-if="primaryPpt"
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
            <div v-if="primaryPpt && runningAnimation" class="tp-inline-actions" data-testid="ppt-animation-actions">
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
            <div
              v-if="reviewingAnimation"
              class="tp-animation-draft"
              data-testid="ppt-animation-draft"
            >
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
                <AppButton
                  data-testid="accept-slide-animation"
                  :disabled="animationBusy"
                  @click="acceptAnimation"
                >
                  保存为本课附件
                </AppButton>
                <AppButton
                  variant="secondary"
                  data-testid="discard-slide-animation"
                  :disabled="animationBusy"
                  @click="discardAnimation"
                >
                  放弃这份草稿
                </AppButton>
              </div>
            </div>
            <div v-if="acceptedAnimations.length" class="tp-animation-attachments">
              <p class="tp-source-group__label">已保存的课堂动画</p>
              <div
                v-for="item in acceptedAnimations"
                :key="item.id"
                class="tp-inline-actions"
              >
                <span>{{ item.storyboard?.title || '课堂动画' }} · 第 {{ item.page_indexes.join('、') }} 页</span>
                <AppButton
                  variant="ghost"
                  data-testid="download-slide-animation"
                  @click="downloadAnimation(item)"
                >
                  下载 HTML
                </AppButton>
              </div>
            </div>
          </div>
          <div class="tp-source-group">
            <p class="tp-source-group__label">参考依据<small>已确认的教材、教辅页段默认全部发送，也可以逐项取消</small></p>
            <label v-for="item in supportMaterials" :key="item.link_id" class="tp-check-row">
              <input v-model="supportLinkIds" type="checkbox" :value="item.link_id" @change="toggleSupport">
              <span><strong>{{ item.material_name }}</strong><small>第 {{ item.start_unit }}—{{ item.end_unit }} 页 · {{ item.material_type.toUpperCase() }}</small></span>
            </label>
            <p v-if="!supportMaterials.length" class="tp-error-text">本节还没有教材或教辅页段，请先自动判断并确认；也可以在上方人工补充连续页段。</p>
          </div>
        </div>
        <div class="tp-confirmation-summary">
          <dl>
            <div><dt>本次发送</dt><dd>{{ selectedLinkIds.length }} 份资料</dd></div>
            <div><dt>主课件</dt><dd>{{ primaryPpt?.material_name ?? '未选择' }}</dd></div>
            <div><dt>课堂动画页</dt><dd>{{ animationSelectionLabel }}</dd></div>
            <div><dt>参考依据</dt><dd>{{ supportLinkIds.length ? `${supportLinkIds.length} 份` : allowPptOnly ? '降级：不使用' : '待补齐' }}</dd></div>
            <div><dt>模型调用</dt><dd>{{ hasSelectedQuestionSources && selectedExerciseCandidateIds.length === 0 ? '内部最多 2 次：识题 + 改编' : '1 次：课件改编' }} · 失败不自动重试</dd></div>
            <div><dt>调用费用</dt><dd>本机无法预估金额；由当前模型服务商按实际用量计费，点击生成即确认本次调用</dd></div>
            <div><dt>原始文件</dt><dd>不会覆盖</dd></div>
          </dl>
          <div v-if="taskStatusLabel || exerciseRun?.status === 'running'" class="tp-inline-actions">
            <StatusBadge v-if="taskStatusLabel" tone="ai" :label="taskStatusLabel" />
            <StatusBadge v-if="exerciseRun?.status === 'running'" tone="ai" label="AI 正在识别题目" />
            <AppButton
              v-if="exerciseRun?.status === 'running'"
              variant="secondary"
              :disabled="cancellingTask"
              data-testid="cancel-exercise-run"
              @click="cancelExerciseRun"
            >
              取消题目识别
            </AppButton>
            <AppButton
              v-if="currentSlideTask && ['prepared', 'queued', 'running'].includes(currentSlideTask.status)"
              variant="secondary"
              :disabled="cancellingTask"
              data-testid="cancel-slide-task"
              @click="cancelSlideTask"
            >
              取消改编任务
            </AppButton>
            <AppButton v-if="currentSlideTask" variant="ghost" @click="routeState.setStep(2)">查看当前改编</AppButton>
            <AppButton
              v-if="hasSelectedQuestionSources && selectedExerciseCandidateIds.length"
              variant="ghost"
              data-testid="restart-exercise-recognition"
              @click="restartExerciseRecognition"
            >
              重新识别题目
            </AppButton>
          </div>
        </div>
      </div>
    </section>

    <p class="tp-inline-message" role="status">{{ message }}</p>
  </div>
</template>
