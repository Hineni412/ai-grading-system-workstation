<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type {
  ExerciseSuggestionPayload,
  ExerciseSuggestionRun,
  ReferenceMaterialLink,
  ReferenceSelectionDraft,
  ReferenceSelectionPayload,
} from '../../api/workbench'
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
import MaterialPagePreview from './MaterialPagePreview.vue'

const LOCAL_SLIDE_CONTRACT_FINGERPRINT = '34b984e97a114fea15394bfdb1c73e8f667b340918e72c21bc15a21934a20a1b'
const ACTIVE_TASK_STATUSES = new Set(['prepared', 'queued', 'running', 'needs_input', 'proposal_ready'])

const workbench = useTeachingPrepLessonWorkbenchContext()
const routeState = workbench.routeState
const catalog = workbench.catalog
const aiTasks = useWorkspaceAITaskStore()
const primaryPptLinkId = ref<string | null>(null)
const supportLinkIds = ref<string[]>([])
const submitting = ref(false)
const allowPptOnly = ref(false)
const message = ref('先确认主课件和参考资料。只有勾选的内容会进入本次 AI 改编。')
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

const preflight = computed(() => workbench.referencePreflight.value)
const references = computed(() => preflight.value?.catalog.material_links ?? [])
const referencePpts = computed(() => references.value.filter(item => item.purpose === 'reference_ppt'))
const supportMaterials = computed(() => references.value.filter(item => item.purpose !== 'reference_ppt'))
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

function previewUnitsFor(item: ReferenceMaterialLink): Array<{ unit_index: number, preview_url: string }> {
  if (previewUnitsByLink[item.link_id]?.length) return previewUnitsByLink[item.link_id] ?? []
  return item.units.map(unit => ({
    unit_index: unit.unit_index,
    preview_url: unit.preview_url,
  }))
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

function matchingCurrentPack(): ResourcePack | null {
  if (catalog.resourcePackStatus?.local_sources_changed) return null
  const expected = [...selectedLinkIds.value].sort().join('|')
  const expectedExercises = [...selectedExerciseCandidateIds.value].sort().join('|')
  return catalog.resourcePacks.find(pack => (
    selectedIdsInPack(pack).join('|') === expected
    && exerciseIdsInPack(pack).join('|') === expectedExercises
  )) ?? null
}

async function saveConfirmedSelection(): Promise<ReferenceSelectionDraft> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId || !preflight.value) throw new Error('请先选择课时')
  const savedExerciseCandidateIds = [...selectedExerciseCandidateIds.value]
  const saved = await teachingPrepWorkbenchApi.saveReferenceDraft(lessonId, {
    expected_revision: preflight.value.draft?.revision ?? null,
    source_state_sha256: preflight.value.source_state_sha256,
    selection: selectionPayload(),
  })
  workbench.setDirty(null)
  await workbench.refresh()
  await nextTick()
  selectedExerciseCandidateIds.value = savedExerciseCandidateIds
  return saved
}

async function ensureResourcePack(): Promise<ResourcePack> {
  const lessonId = routeState.currentLessonId.value
  const preferences = catalog.teachingPreferences?.payload
  if (!lessonId || !preferences) throw new Error('课时或个人备课偏好尚未载入')
  const current = matchingCurrentPack()
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
    teacher_context: null,
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

function wait(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}

async function identifyWorkbookQuestions(draft: ReferenceSelectionDraft): Promise<void> {
  const lessonId = routeState.currentLessonId.value
  if (!lessonId) throw new Error('当前课时已失效')
  message.value = 'AI 正在从两本参考教辅中识别题目和裁切范围（第 1/2 次调用）……'
  const snapshot = await teachingPrepWorkbenchApi.freezeReferenceSnapshot(
    lessonId,
    `exercise-snapshot-${crypto.randomUUID().replaceAll('-', '')}`,
    draft.revision,
  )
  let run = await teachingPrepWorkbenchApi.startExerciseSuggestions(
    snapshot.id,
    `exercise-suggestions-${crypto.randomUUID().replaceAll('-', '')}`,
  )
  for (let attempt = 0; attempt < 120 && run.status === 'running'; attempt += 1) {
    exerciseRun.value = run.status === 'running' ? run : exerciseRun.value
    await wait(1_000)
    run = await teachingPrepWorkbenchApi.exerciseSuggestionRun(run.id)
  }
  if (run.status !== 'succeeded') {
    exerciseRun.value = null
    throw new Error(run.status === 'running'
      ? '教辅识题等待超时，没有自动重试'
      : `教辅识题未完成：${run.error_code ?? run.status}`)
  }
  if (!run.suggestions.length) throw new Error('AI 没有找到可核对的教辅题，请先人工调整教辅页段')
  initializeExerciseReview(run)
  message.value = `AI 找到 ${run.suggestions.length} 道候选题。请核对题目范围，再继续生成课件改编。`
}

async function cancelExerciseRun(): Promise<void> {
  const run = exerciseRun.value
  if (!run || run.status !== 'running') return
  cancellingTask.value = true
  try {
    await teachingPrepWorkbenchApi.cancelExerciseSuggestions(run.id)
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
  if (!candidateIds.length) throw new Error('至少保留一道教辅候选题，才能执行插题改编')
  exerciseSuggestionsConfirmed.value = true
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

async function confirmAndSend(forceNew = false): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  message.value = '正在保存资料对应关系并准备 AI 改编任务……'
  try {
    const savedSelection = await saveConfirmedSelection()
    if (
      hasSelectedExercisePages.value
      && !allowPptOnly.value
      && selectedExerciseCandidateIds.value.length === 0
      && !exerciseRun.value
    ) {
      await identifyWorkbookQuestions(savedSelection)
      return
    }
    if (pendingExerciseSuggestions.value.length && !exerciseSuggestionsConfirmed.value) {
      message.value = '正在保存教辅题审核并准备第 2/2 次 AI 调用……'
      await confirmWorkbookQuestions()
      await saveConfirmedSelection()
    }
    const pack = await ensureResourcePack()
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
      message.value = '资料已确认，AI 改编任务已经开始。'
    }
    await routeState.setStep(2)
  } catch (error) {
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
  selectedExerciseCandidateIds.value = []
  exerciseRun.value = null
  exerciseSuggestionsConfirmed.value = false
  message.value = '已改为重新识别当前勾选的教辅原页；下一步会先调用 AI 识题，再由你核对裁切范围。'
}

const primaryActionLabel = computed(() => {
  if (submitting.value) return '正在准备…'
  if (currentSlideTask.value?.status === 'proposal_ready') {
    return '重新生成 AI 改编（调用 1 次）'
  }
  if (currentSlideTask.value) return '查看正在处理的改编'
  if (pendingExerciseSuggestions.value.length && !exerciseSuggestionsConfirmed.value) {
    return '采用候选题并生成改编（第 2/2 次）'
  }
  if (hasSelectedExercisePages.value && selectedExerciseCandidateIds.value.length === 0) {
    return '先让 AI 识别教辅题（第 1/2 次）'
  }
  return '确认资料并生成 AI 改编（调用 1 次）'
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
              variant="ghost"
              data-testid="preview-lesson-material"
              @click="toggleLinkPreview(item)"
            >
              {{ previewingLinkId === item.link_id ? '收起预览' : '预览这几页' }}
            </AppButton>
            <AppButton variant="ghost" class="tp-danger-text" @click="removeMaterialLink(item.link_id)">移除</AppButton>
          </div>
          <MaterialPagePreview
            v-if="previewingLinkId === item.link_id"
            :units="previewUnitsFor(item)"
            :page="previewingLinkPage"
            :range-label="`本课关联第 ${item.start_unit}—${item.end_unit} 页`"
            @update:page="previewingLinkPage = $event"
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
            <div><dt>主课件</dt><dd>{{ referencePpts.find(item => item.link_id === primaryPptLinkId)?.material_name ?? '未选择' }}</dd></div>
            <div><dt>参考依据</dt><dd>{{ supportLinkIds.length ? `${supportLinkIds.length} 份` : allowPptOnly ? '降级：不使用' : '待补齐' }}</dd></div>
            <div><dt>模型调用</dt><dd>{{ hasSelectedExercisePages && selectedExerciseCandidateIds.length === 0 ? '共 2 次：教辅识题 + 课件改编' : '1 次：课件改编' }} · 失败不自动重试</dd></div>
            <div><dt>调用费用</dt><dd>本机无法预估金额；由当前模型服务商按实际用量计费，点击生成即确认本次调用</dd></div>
            <div><dt>原始文件</dt><dd>不会覆盖</dd></div>
          </dl>
          <div v-if="taskStatusLabel || exerciseRun?.status === 'running'" class="tp-inline-actions">
            <StatusBadge v-if="taskStatusLabel" tone="ai" :label="taskStatusLabel" />
            <StatusBadge v-if="exerciseRun?.status === 'running'" tone="ai" label="AI 正在识别教辅题" />
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
              v-if="hasSelectedExercisePages && selectedExerciseCandidateIds.length"
              variant="ghost"
              data-testid="restart-exercise-recognition"
              @click="restartExerciseRecognition"
            >
              重新识别教辅题
            </AppButton>
          </div>
        </div>
      </div>
    </section>

    <section v-if="pendingExerciseSuggestions.length" class="tp-panel" aria-label="AI 找到的题目裁切">
      <div class="tp-panel__head">
        <h2>AI 找到的题目裁切</h2>
        <StatusBadge tone="ai" :label="`AI 建议 · ${pendingExerciseSuggestions.filter(item => exerciseAccepted[item.id] !== false).length} / ${pendingExerciseSuggestions.length} 道待采用`" />
      </div>
      <div class="tp-panel__body">
        <p class="tp-muted">数值可直接修正；取消勾选的题不会进入课件。</p>
        <article v-for="item in pendingExerciseSuggestions" :key="item.id" class="tp-exercise-proof__item">
          <label class="tp-field--check">
            <input v-model="exerciseAccepted[item.id]" type="checkbox">
            采用这道题
          </label>
          <div class="tp-exercise-proof__fields">
            <label class="tp-field">题号<input v-model="exerciseEdits[item.id]!.question_number" maxlength="80"></label>
            <label class="tp-field">课堂显示名称<input v-model="exerciseEdits[item.id]!.content_label" maxlength="300"></label>
            <p class="tp-muted">{{ exerciseEdits[item.id]!.reason }}</p>
            <fieldset v-for="region in exerciseEdits[item.id]!.question_regions" :key="`${region.material_unit_id}-${region.sequence}`" class="tp-crop-fieldset">
              <legend>题目裁切范围（0—1）</legend>
              <label>x 起点<input v-model.number="region.crop.x0" type="number" min="0" max="1" step="0.01"></label>
              <label>y 起点<input v-model.number="region.crop.y0" type="number" min="0" max="1" step="0.01"></label>
              <label>x 终点<input v-model.number="region.crop.x1" type="number" min="0" max="1" step="0.01"></label>
              <label>y 终点<input v-model.number="region.crop.y1" type="number" min="0" max="1" step="0.01"></label>
            </fieldset>
          </div>
        </article>
      </div>
    </section>

    <p class="tp-inline-message" role="status">{{ message }}</p>
  </div>
</template>
