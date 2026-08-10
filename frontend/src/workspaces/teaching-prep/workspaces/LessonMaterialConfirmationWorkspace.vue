<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'

import type {
  ExerciseSuggestion,
  ExerciseSuggestionPayload,
  ExerciseSuggestionRun,
  ReferenceSelectionDraft,
  ReferenceSelectionPayload,
} from '../api/workbench'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import {
  teachingPrepCatalogApi,
  type LessonDraft,
  type MaterialLinkPurpose,
  type ResourcePack,
} from '../api/catalog'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'

const LOCAL_SLIDE_CONTRACT_FINGERPRINT = '34b984e97a114fea15394bfdb1c73e8f667b340918e72c21bc15a21934a20a1b'
const ACTIVE_TASK_STATUSES = new Set(['prepared', 'queued', 'running', 'needs_input', 'proposal_ready'])

const workbench = useTeachingPrepWorkbenchContext()
const aiTasks = useWorkspaceAITaskStore()
const primaryPptLinkId = ref<string | null>(null)
const supportLinkIds = ref<string[]>([])
const activePreviewUnitId = ref<string | null>(null)
const previewZoom = ref(100)
const previewFitWidth = ref(false)
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
const refreshedPreviewUrls = new Set<string>()
const exerciseRun = ref<ExerciseSuggestionRun | null>(null)
const exerciseEdits = reactive<Record<string, ExerciseSuggestionPayload>>({})
const exerciseAccepted = reactive<Record<string, boolean>>({})
const selectedExerciseCandidateIds = ref<string[]>([])
const exerciseSuggestionsConfirmed = ref(false)

const preflight = computed(() => workbench.referencePreflight.value)
const references = computed(() => preflight.value?.catalog.material_links ?? [])
const referencePpts = computed(() => references.value.filter(item => item.purpose === 'reference_ppt'))
const supportMaterials = computed(() => references.value.filter(item => item.purpose !== 'reference_ppt'))
const quickMaterialCandidates = computed(() => {
  const semesterId = workbench.catalog.selectedSemester?.id
  const linkedVersionIds = new Set(references.value.map(item => item.material_version_id))
  if (!semesterId) return []
  return workbench.catalog.semesterMaterials.flatMap((record) => {
    if (
      record.semester_id !== semesterId
      || !record.is_active
      || record.parse_status !== 'parsed'
      || record.has_unparsed_update
      || !record.current_material_version_id
      || (record.current_unit_count ?? 0) < 1
      || linkedVersionIds.has(record.current_material_version_id)
    ) return []
    const material = workbench.catalog.materials.find(item => (
      item.id === record.current_material_version_id
      && item.availability === 'available'
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
const selectedLinkIds = computed(() => [
  ...(primaryPptLinkId.value ? [primaryPptLinkId.value] : []),
  ...supportLinkIds.value,
])
const parsedSemesterMaterials = computed(() => {
  const semesterId = workbench.catalog.selectedSemester?.id
  return workbench.catalog.semesterMaterials.filter(item => (
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
const reusableExerciseCandidates = computed(() => (workbench.catalog.exerciseCandidates ?? []).filter(item => (
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
const previewUnits = computed(() => references.value.flatMap(link => (
  link.units.map(unit => ({ ...unit, material_name: link.material_name, purpose: link.purpose }))
)))
const activePreviewUnitIndex = computed(() => Math.max(
  0,
  previewUnits.value.findIndex(item => item.unit_id === activePreviewUnitId.value),
))
const activePreviewUnit = computed(() => previewUnits.value[activePreviewUnitIndex.value] ?? null)
const activePreviewUrl = computed(() => activePreviewUnit.value?.preview_url ?? null)
const activePreviewKind = computed(() => {
  if (activePreviewUnit.value?.unit_kind !== 'ppt_slide') return null
  const kind = activePreviewUnit.value.object_summary?.preview_kind
  return kind === 'rendered' || kind === 'structural' ? kind : null
})
const activePreviewLabel = computed(() => {
  if (activePreviewKind.value === 'rendered') return '真实原页'
  if (activePreviewKind.value === 'structural') return '结构预览，不是原页'
  return null
})
const selectedDraft = computed(() => workbench.catalog.lessonDrafts.find(
  item => item.id === workbench.catalog.selectedLessonDraftId,
) ?? workbench.catalog.lessonDrafts.find(item => item.status === 'confirmed') ?? null)
const currentSlideTask = computed(() => {
  const lesson = workbench.catalog.selectedLesson
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
  && Boolean(workbench.catalog.teachingPreferences?.payload)
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
  if (!previewUnits.value.some(item => item.unit_id === activePreviewUnitId.value)) {
    activePreviewUnitId.value = previewUnits.value[0]?.unit_id ?? null
  }
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

watch(quickMaterialRecordId, () => {
  quickPurpose.value = ''
  quickStartUnit.value = null
  quickEndUnit.value = null
  quickMessage.value = ''
})

watch(
  [quickMaterialRecordId, quickPurpose, quickStartUnit, quickEndUnit],
  () => { quickRequestToken.value = null },
  { flush: 'sync' },
)

async function addQuickMaterialLink(): Promise<void> {
  const lessonId = workbench.catalog.selectedLessonId
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
    await workbench.refreshCurrentWorkspace()
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

function selectPrimaryPpt(linkId: string): void {
  primaryPptLinkId.value = linkId
  selectLinkPreview(linkId)
  workbench.setDirty('资料对应关系')
  message.value = '已更换主课件；发送后 AI 将以这份 PPT 为删改底稿。'
}

function toggleSupport(linkId: string): void {
  selectLinkPreview(linkId)
  workbench.setDirty('资料对应关系')
  message.value = '参考资料选择已调整；未勾选的资料不会发送给 AI。'
}

function selectLinkPreview(linkId: string): void {
  const link = references.value.find(item => item.link_id === linkId)
  activePreviewUnitId.value = link?.units[0]?.unit_id ?? activePreviewUnitId.value
}

function movePreview(offset: number): void {
  const nextIndex = Math.min(
    previewUnits.value.length - 1,
    Math.max(0, activePreviewUnitIndex.value + offset),
  )
  activePreviewUnitId.value = previewUnits.value[nextIndex]?.unit_id ?? null
}

function jumpPreview(event: Event): void {
  const index = Math.trunc(Number((event.target as HTMLInputElement).value)) - 1
  if (!Number.isFinite(index) || index < 0 || index >= previewUnits.value.length) return
  activePreviewUnitId.value = previewUnits.value[index]?.unit_id ?? null
}

async function refreshRenderedReferencePreview(url: string): Promise<void> {
  const lessonId = workbench.catalog.selectedLessonId
  const unit = activePreviewUnit.value
  if (
    !lessonId
    || unit?.preview_url !== url
    || unit.unit_kind !== 'ppt_slide'
    || unit.object_summary?.preview_kind === 'rendered'
  ) return
  const key = `${lessonId}:${url}`
  if (refreshedPreviewUrls.has(key)) return
  refreshedPreviewUrls.add(key)
  try {
    const next = await teachingPrepWorkbenchApi.referencePreflight(lessonId)
    if (workbench.catalog.selectedLessonId === lessonId) {
      workbench.referencePreflight.value = next
    }
  } catch {
    // The structural preview stays visible and explicitly labelled as fallback.
  }
}

function selectionPayload(): ReferenceSelectionPayload {
  const preferences = workbench.catalog.teachingPreferences?.payload
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
  if (workbench.catalog.resourcePackStatus?.local_sources_changed) return null
  const expected = [...selectedLinkIds.value].sort().join('|')
  const expectedExercises = [...selectedExerciseCandidateIds.value].sort().join('|')
  return workbench.catalog.resourcePacks.find(pack => (
    selectedIdsInPack(pack).join('|') === expected
    && exerciseIdsInPack(pack).join('|') === expectedExercises
  )) ?? null
}

async function saveConfirmedSelection(): Promise<ReferenceSelectionDraft> {
  const lessonId = workbench.catalog.selectedLessonId
  if (!lessonId || !preflight.value) throw new Error('请先选择课时')
  const savedExerciseCandidateIds = [...selectedExerciseCandidateIds.value]
  const saved = await teachingPrepWorkbenchApi.saveReferenceDraft(lessonId, {
    expected_revision: preflight.value.draft?.revision ?? null,
    source_state_sha256: preflight.value.source_state_sha256,
    selection: selectionPayload(),
  })
  workbench.setDirty(null)
  await workbench.refreshCurrentWorkspace()
  await nextTick()
  selectedExerciseCandidateIds.value = savedExerciseCandidateIds
  return saved
}

async function ensureResourcePack(): Promise<ResourcePack> {
  const lessonId = workbench.catalog.selectedLessonId
  const preferences = workbench.catalog.teachingPreferences?.payload
  if (!lessonId || !preferences) throw new Error('课时或个人备课偏好尚未载入')
  const current = matchingCurrentPack()
  if (current) {
    await workbench.catalog.selectResourcePack(current)
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
  await workbench.catalog.freezeResourcePack({
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
  const created = workbench.catalog.resourcePacks[0]
  if (!created) throw new Error('资料快照没有成功建立')
  return created
}

async function ensureConfirmedDraft(pack: ResourcePack): Promise<LessonDraft> {
  let draft = workbench.catalog.lessonDrafts.find(item => (
    item.resource_pack_id === pack.id && item.status === 'confirmed'
  )) ?? null
  if (!draft) {
    let generated = workbench.catalog.lessonDrafts.find(item => (
      item.resource_pack_id === pack.id && item.status === 'draft'
    )) ?? null
    if (!generated) {
      await workbench.catalog.prepareLessonDraft('local_template')
      await workbench.catalog.generateLessonDraft('local_template')
      generated = workbench.catalog.lessonDrafts.find(item => (
        item.resource_pack_id === pack.id && item.status === 'draft'
      )) ?? null
    }
    if (generated) {
      await workbench.catalog.reviseLessonDraft(
        generated,
        generated.payload,
        true,
      )
    }
    draft = workbench.catalog.lessonDrafts.find(item => (
      item.resource_pack_id === pack.id && item.status === 'confirmed'
    )) ?? null
  }
  if (!draft) throw new Error('内部课件结构没有准备完成')
  await workbench.catalog.selectLessonDraft(draft)
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
  const lessonId = workbench.catalog.selectedLessonId
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
    await wait(1_000)
    run = await teachingPrepWorkbenchApi.exerciseSuggestionRun(run.id)
  }
  if (run.status !== 'succeeded') {
    throw new Error(run.status === 'running'
      ? '教辅识题等待超时，没有自动重试'
      : `教辅识题未完成：${run.error_code ?? run.status}`)
  }
  if (!run.suggestions.length) throw new Error('AI 没有找到可核对的教辅题，请先人工调整教辅页段')
  initializeExerciseReview(run)
  message.value = `AI 找到 ${run.suggestions.length} 道候选题。请核对题目范围，再继续生成课件改编。`
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
  await workbench.refreshCurrentWorkspace()
  await nextTick()
  // refreshCurrentWorkspace may replace the preflight object and trigger its
  // watcher. Restore the just-created candidates before saving the selection.
  selectedExerciseCandidateIds.value = candidateIds
}

function suggestionUnit(suggestion: ExerciseSuggestion) {
  const region = exerciseEdits[suggestion.id]?.question_regions[0]
  return region
    ? previewUnits.value.find(unit => unit.unit_id === region.material_unit_id) ?? null
    : null
}

function cropImageStyle(suggestion: ExerciseSuggestion): Record<string, string> {
  const crop = exerciseEdits[suggestion.id]?.question_regions[0]?.crop
  if (!crop) return {}
  const width = Math.max(0.01, crop.x1 - crop.x0)
  const height = Math.max(0.01, crop.y1 - crop.y0)
  return {
    position: 'absolute',
    width: `${100 / width}%`,
    height: `${100 / height}%`,
    left: `${-100 * crop.x0 / width}%`,
    top: `${-100 * crop.y0 / height}%`,
    maxWidth: 'none',
    maxHeight: 'none',
    objectFit: 'fill',
  }
}

function withSemesterContext(
  refs: Array<{ kind: string; id: string; revision: string }>,
): Array<{ kind: string; id: string; revision: string }> {
  const semester = workbench.catalog.selectedSemester
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
      && task.source_ref.id === workbench.catalog.selectedLessonId
      && task.context_refs.some(ref => ref.kind === 'lesson_draft' && ref.id === draft.id)
      && ACTIVE_TASK_STATUSES.has(task.status)
    )) : undefined
    if (existing) {
      if (existing.status === 'prepared' && existing.send_attempt_count === 0) {
        await aiTasks.dispatch(existing)
      }
      message.value = '已找到本课时正在处理的改编任务，没有重复发送。'
    } else {
      const lesson = workbench.catalog.selectedLesson
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
    await workbench.openStage('slides', { panel: 'slides' })
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
    await openCurrentTask()
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

async function openCurrentTask(): Promise<void> {
  await workbench.openStage('slides', { panel: 'slides' })
}
</script>

<template>
  <section class="tp-workspace tp-material-confirmation">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">第 1 步 · 对准本节资料</p>
        <h1 data-workbench-title tabindex="-1">确认主课件和参考依据</h1>
        <p>主课件是 AI 实际删改的底稿；教材、教辅只提供本节内容依据。系统不会读取未勾选资料。</p>
      </div>
      <span class="tp-trust-badge">原 PPT 始终只读</span>
    </header>

    <div v-if="!referencePpts.length" class="tp-inline-error" role="alert">
      本课时还没有对应的参考 PPT。请先到资料库完成课时对应，之后再发送给 AI。
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openWorkspace('materials')">打开资料库</button>
    </div>

    <div v-if="missingSupportLabels.length" class="tp-inline-error" role="alert">
      <div>
        <strong>还缺少{{ missingSupportLabels.join('、') }}的已确认页段</strong>
        <p>先在资料库让 AI 根据目录、正文锚点和页码提出课时对应；高置信结果可批量确认，页码有误时可以人工修正。</p>
      </div>
      <div class="tp-inline-actions">
        <button
          class="tp-button tp-button--secondary"
          data-testid="open-material-mapping"
          type="button"
          @click="workbench.openWorkspace('materials')"
        >
          去资料库自动判断页段
        </button>
      </div>
      <label class="tp-field--check">
        <input v-model="allowPptOnly" data-testid="allow-ppt-only" type="checkbox">
        我确认本次暂时只依据 PPT 生成降级建议
      </label>
      <p v-if="allowPptOnly" class="tp-error-text">本次只依据 PPT，可能偏离教材与教辅；生成后需要重点复核。</p>
    </div>

    <section class="tp-quick-material-link" aria-labelledby="quick-material-link-title">
      <div>
        <p class="tp-eyebrow">补充本节依据</p>
        <h2 id="quick-material-link-title">从本学期资料添加</h2>
        <p>只建立你明确指定的连续页段，不会自动关联整本资料，也不会在这里调用 AI。</p>
      </div>
      <label class="tp-field">
        已解析资料
        <select v-model="quickMaterialRecordId" data-testid="lesson-material-candidate">
          <option value="">请选择资料</option>
          <option
            v-for="item in quickMaterialCandidates"
            :key="item.record.id"
            :value="item.record.id"
          >
            {{ item.record.display_name }} · 共 {{ item.record.current_unit_count }} 页
          </option>
        </select>
      </label>
      <label class="tp-field">
        本课时用途
        <select v-model="quickPurpose" data-testid="lesson-material-purpose">
          <option value="">请选择用途</option>
          <option value="textbook">教材依据</option>
          <option value="reference_ppt">参考课件</option>
          <option value="exercise">课堂练习</option>
          <option value="answer">答案 / 解析</option>
          <option value="supplement">补充资料</option>
        </select>
      </label>
      <div class="tp-field-pair">
        <label class="tp-field">
          起始页
          <input v-model.number="quickStartUnit" data-testid="lesson-material-range" type="number" min="1" :max="quickMaterialMaximum || 1">
        </label>
        <label class="tp-field">
          结束页
          <input v-model.number="quickEndUnit" data-testid="lesson-material-range" type="number" min="1" :max="quickMaterialMaximum || 1">
        </label>
      </div>
      <button
        class="tp-button tp-button--secondary"
        data-testid="add-lesson-material"
        type="button"
        :disabled="quickAdding"
        @click="addQuickMaterialLink"
      >
        {{ quickAdding ? '正在加入…' : '确认页段并加入本课时' }}
      </button>
      <p v-if="quickMessage" class="tp-inline-guidance" role="status">{{ quickMessage }}</p>
      <p v-else-if="quickMaterialCandidates.length === 0" class="tp-muted">当前没有尚未关联且已完成解析的本学期资料。</p>
    </section>

    <section v-if="pendingExerciseSuggestions.length" class="tp-exercise-proof" aria-labelledby="exercise-proof-title">
      <header>
        <div>
          <p class="tp-eyebrow">第 2 步 · 核对教辅题</p>
          <h2 id="exercise-proof-title">AI 找到的题目裁切</h2>
          <p>绿色窗口就是将插入 PPT 的实际范围。数值可直接修正；取消勾选的题不会进入课件。</p>
        </div>
        <strong>{{ pendingExerciseSuggestions.filter(item => exerciseAccepted[item.id] !== false).length }} / {{ pendingExerciseSuggestions.length }} 道待采用</strong>
      </header>
      <article v-for="item in pendingExerciseSuggestions" :key="item.id" class="tp-exercise-proof__item">
        <label class="tp-exercise-proof__choice">
          <input v-model="exerciseAccepted[item.id]" type="checkbox">
          <span>采用这道题</span>
        </label>
        <div class="tp-exercise-proof__crop">
          <img
            v-if="suggestionUnit(item)?.preview_url"
            data-testid="exercise-crop-preview"
            :src="suggestionUnit(item)?.preview_url"
            :alt="exerciseEdits[item.id]?.content_label ?? '教辅题裁切预览'"
            :style="cropImageStyle(item)"
          >
          <span v-else>原页预览暂不可用</span>
        </div>
        <div class="tp-exercise-proof__fields">
          <label class="tp-field">题号<input v-model="exerciseEdits[item.id]!.question_number" maxlength="80"></label>
          <label class="tp-field">课堂显示名称<input v-model="exerciseEdits[item.id]!.content_label" maxlength="300"></label>
          <p>{{ exerciseEdits[item.id]!.reason }}</p>
          <fieldset v-for="region in exerciseEdits[item.id]!.question_regions" :key="`${region.material_unit_id}-${region.sequence}`">
            <legend>题目裁切范围（0—1）</legend>
            <label>x 起点<input v-model.number="region.crop.x0" type="number" min="0" max="1" step="0.01"></label>
            <label>y 起点<input v-model.number="region.crop.y0" type="number" min="0" max="1" step="0.01"></label>
            <label>x 终点<input v-model.number="region.crop.x1" type="number" min="0" max="1" step="0.01"></label>
            <label>y 终点<input v-model.number="region.crop.y1" type="number" min="0" max="1" step="0.01"></label>
          </fieldset>
        </div>
      </article>
    </section>

    <TeachingPrepDocumentWorkspace
      title="资料原页核对"
      subtitle="点击任一资料可查看它在本课时的已定位页段。"
      :preview-url="activePreviewUrl"
      :active-pane="workbench.pane.value"
      :preview-zoom="previewZoom"
      :fit-width="previewFitWidth"
      @update:active-pane="workbench.setPane"
      @preview-loaded="refreshRenderedReferencePreview"
    >
      <template #rail>
        <section class="tp-source-group">
          <div><p class="tp-eyebrow">主课件</p><small>只能选一份，AI 将在它的副本上改编</small></div>
          <label v-for="item in referencePpts" :key="item.link_id" class="tp-check-row tp-check-row--primary">
            <input
              type="radio"
              name="primary-reference-ppt"
              :checked="primaryPptLinkId === item.link_id"
              @change="selectPrimaryPpt(item.link_id)"
            >
            <span><strong>{{ item.material_name }}</strong><small>第 {{ item.start_unit }}—{{ item.end_unit }} 页 · PPT</small></span>
          </label>
        </section>
        <section class="tp-source-group">
          <div><p class="tp-eyebrow">参考依据</p><small>已确认的教材、教辅页段默认全部发送，也可以逐项取消</small></div>
          <label v-for="item in supportMaterials" :key="item.link_id" class="tp-check-row">
            <input v-model="supportLinkIds" type="checkbox" :value="item.link_id" @change="toggleSupport(item.link_id)">
            <span><strong>{{ item.material_name }}</strong><small>第 {{ item.start_unit }}—{{ item.end_unit }} 页 · {{ item.material_type.toUpperCase() }}</small></span>
          </label>
          <p v-if="!supportMaterials.length" class="tp-error-text">本节还没有教材或教辅页段，请先自动判断并确认；也可以在上方人工补充连续页段。</p>
        </section>
      </template>

      <template #toolbar>
        <div class="tp-document-toolbar">
          <button type="button" aria-label="上一页" :disabled="activePreviewUnitIndex <= 0" @click="movePreview(-1)">←</button>
          <label class="tp-document-toolbar__jump">
            <span class="tp-visually-hidden">跳转页码</span>
            <input aria-label="跳转页码" type="number" min="1" :max="previewUnits.length" :value="activePreviewUnitIndex + 1" @change="jumpPreview">
            <span>/ {{ previewUnits.length }}</span>
          </label>
          <button type="button" aria-label="下一页" :disabled="activePreviewUnitIndex >= previewUnits.length - 1" @click="movePreview(1)">→</button>
          <button type="button" @click="previewFitWidth = !previewFitWidth">{{ previewFitWidth ? '实际比例' : '适应宽度' }}</button>
          <button type="button" aria-label="缩小预览" @click="previewFitWidth = false; previewZoom = Math.max(50, previewZoom - 10)">−</button>
          <span class="tp-document-toolbar__zoom">{{ previewZoom }}%</span>
          <button type="button" aria-label="放大预览" @click="previewFitWidth = false; previewZoom = Math.min(200, previewZoom + 10)">＋</button>
          <span
            v-if="activePreviewLabel"
            class="tp-preview-kind"
            :class="{ 'is-structural': activePreviewKind === 'structural' }"
          >
            {{ activePreviewLabel }}
          </span>
        </div>
      </template>

      <template #inspector>
        <div class="tp-confirmation-summary">
          <p class="tp-eyebrow">本次发送范围</p>
          <h2>{{ selectedLinkIds.length }} 份资料</h2>
          <dl>
            <div><dt>主课件</dt><dd>{{ referencePpts.find(item => item.link_id === primaryPptLinkId)?.material_name ?? '未选择' }}</dd></div>
            <div><dt>参考依据</dt><dd>{{ supportLinkIds.length ? `${supportLinkIds.length} 份` : allowPptOnly ? '降级：不使用' : '待补齐' }}</dd></div>
            <div><dt>教学文字</dt><dd>无需填写</dd></div>
            <div><dt>模型调用</dt><dd>{{ hasSelectedExercisePages && selectedExerciseCandidateIds.length === 0 ? '共 2 次：教辅识题 + 课件改编' : '1 次：课件改编' }} · 失败不自动重试</dd></div>
            <div><dt>调用费用</dt><dd>本机无法预估金额；由当前模型服务商按实际用量计费，点击生成即确认本次调用</dd></div>
            <div><dt>原始文件</dt><dd>不会覆盖</dd></div>
          </dl>
          <div v-if="taskStatusLabel" class="tp-ai-task-note" role="status">
            <span aria-hidden="true" />
            <div><strong>{{ taskStatusLabel }}</strong><small>这是当前资料版本对应的任务</small></div>
          </div>
          <button v-if="currentSlideTask" class="tp-button tp-button--secondary" type="button" @click="openCurrentTask">查看当前改编</button>
          <button
            v-if="hasSelectedExercisePages && selectedExerciseCandidateIds.length"
            class="tp-button tp-button--secondary"
            data-testid="restart-exercise-recognition"
            type="button"
            @click="restartExerciseRecognition"
          >
            重新识别教辅题
          </button>
        </div>
      </template>
    </TeachingPrepDocumentWorkspace>

    <TeachingPrepStickyActions
      :state="submitting ? 'saving' : workbench.dirtyReason.value ? 'dirty' : message.startsWith('尚未') ? 'error' : 'saved'"
      :message="message"
    >
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('select')">返回备课首页</button>
      <button class="tp-button tp-button--primary" data-testid="send-slide-adaptation" type="button" :disabled="!canSubmit" @click="runPrimaryAction">
        {{ primaryActionLabel }}
      </button>
    </TeachingPrepStickyActions>
  </section>
</template>
