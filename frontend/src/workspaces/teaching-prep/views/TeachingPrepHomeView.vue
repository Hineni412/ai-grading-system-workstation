<script setup lang="ts">
import {
  computed,
  onMounted,
  reactive,
  ref,
  toRaw,
  watch,
} from 'vue'

import type {
  CurriculumVolume,
  AssessmentChoice,
  ExerciseAnswerStatus,
  ExerciseCandidate,
  ExerciseCandidateInput,
  ExerciseRegionInput,
  ExerciseSelectionStatus,
  LessonNode,
  LessonNodeType,
  LessonDraft,
  LessonDraftPayload,
  MaterialLinkPurpose,
  MaterialUnit,
  MaterialVersion,
  NormalizedCrop,
  QuestionEvidenceChoice,
  ResourcePack,
  SemesterMappingProposal,
  SemesterLessonProgressStatus,
  SemesterMaterialMappingStatus,
  SemesterMaterialRecord,
  SemesterMaterialRole,
  SemesterStatus,
  PptxExecution,
  PostLessonReview,
  SlideOperation,
  SlideOperationReviewInput,
  SlidePlan,
  TeachingPreferencesPayload,
  UpClassPackage,
} from '../api/catalog'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import '../styles/teaching-prep.css'

const catalog = useTeachingPrepCatalogStore()
const showCurriculumForm = ref(false)
const showLessonForm = ref(false)
const editingNodeId = ref<string | null>(null)
const editingTitle = ref('')
const selectedUnitIds = ref<string[]>([])
const importMaterialRole = ref<SemesterMaterialRole>('supplement')
const materialRoleDrafts = reactive<Record<string, SemesterMaterialRole>>({})
const selectedSemesterMaterialId = ref('')
const linkPurpose = ref<MaterialLinkPurpose>('textbook')
const linkNote = ref('')
const editingUnitId = ref<string | null>(null)
const unitTitle = ref('')
const unitManualText = ref('')
type ExerciseRegionRole = 'question' | 'answer'
interface DraftExerciseRegion extends ExerciseRegionInput {
  key: string
  unit_index: number
  material_name: string
  preview_url: string
}
const editingExercise = ref<ExerciseCandidate | null>(null)
const questionRegions = ref<DraftExerciseRegion[]>([])
const answerRegions = ref<DraftExerciseRegion[]>([])
const cropUnitId = ref<string | null>(null)
const cropRole = ref<ExerciseRegionRole>('question')
const cropStart = ref<{ x: number; y: number } | null>(null)
const cropDraft = ref<NormalizedCrop | null>(null)
const exerciseForm = reactive<{
  questionNumber: string
  contentLabel: string
  difficulty: ExerciseCandidate['difficulty']
  classroomUse: ExerciseCandidate['classroom_use']
  estimatedMinutes: number
  teachingFocus: string
  teacherNote: string
  selectionStatus: ExerciseSelectionStatus
}>({
  questionNumber: '',
  contentLabel: '',
  difficulty: 'unrated',
  classroomUse: 'guided_practice',
  estimatedMinutes: 5,
  teachingFocus: '',
  teacherNote: '',
  selectionStatus: 'classroom_candidate',
})
const selectedAssessmentIds = ref<number[]>([])
const selectedQuestionIds = ref<number[]>([])
const packFormError = ref('')
const referencePptIntents = reactive<Record<
  string,
  'keep' | 'candidate_delete'
>>({})
const resourcePackForm = reactive({
  className: '',
  teacherContext: '',
  knowledgeScope: '',
})
const preparationPreferences = reactive<TeachingPreferencesPayload>({
  schema_version: 1,
  label_textbook_pages: true,
  page_label_font_size: 28,
  trim_excess_practice: true,
  practice_trim_level: 'moderate',
  preserve_teaching_examples: true,
  prefer_short_practice: true,
  supplement_from_references: true,
  supplement_question_limit: 2,
  supplement_as_source_image: true,
  prioritize_homework_workbook: true,
  avoid_direct_homework_copy: true,
  avoid_ppt_duplicates: true,
})
const editingDraft = ref<LessonDraft | null>(null)
const draftEdit = ref<LessonDraftPayload | null>(null)
const editingSlidePlan = ref<SlidePlan | null>(null)
const slideOperationEdits = ref<SlideOperationReviewInput[]>([])
const slideReviewNote = ref('')
const reviewingPackageId = ref<string | null>(null)
const selectedPriorReviewIds = ref<string[]>([])
const classVariantForm = reactive({
  className: '',
  teacherContext: '',
})
const postReviewForm = reactive<{
  timing: PostLessonReview['payload']['timing']
  questionOutcome: PostLessonReview['payload']['question_outcome']
  reteachPoints: string
  nextAction: PostLessonReview['payload']['next_action']
  note: string
  useInNextVersion: boolean
}>({
  timing: 'on_time',
  questionOutcome: 'appropriate',
  reteachPoints: '',
  nextAction: 'keep',
  note: '',
  useInNextVersion: true,
})

const curriculumForm = reactive({
  title: '',
  gradeLevel: 7,
  volume: 'first' as CurriculumVolume,
  publisher: '',
  editionLabel: '',
  schoolYear: (() => {
    const today = new Date()
    const startYear = today.getMonth() >= 6
      ? today.getFullYear()
      : today.getFullYear() - 1
    return `${startYear}-${startYear + 1}`
  })(),
  term: 'first' as 'first' | 'second',
  plannedNewLessonCount: 48,
})
const semesterEdit = reactive<{
  plannedNewLessonCount: number
  status: SemesterStatus
}>({
  plannedNewLessonCount: 0,
  status: 'planning',
})

const lessonForm = reactive({
  nodeType: 'chapter' as LessonNodeType,
  parentId: '',
  title: '',
  durationMinutes: 45,
})

const parentOptions = computed(() => {
  if (lessonForm.nodeType === 'chapter') return []
  const requiredType = lessonForm.nodeType === 'section' ? 'chapter' : 'section'
  return catalog.lessonNodes.filter(
    ({ node_type: nodeType, is_active: isActive }) => (
      nodeType === requiredType && isActive
    ),
  )
})

const nodeDepths = computed(() => {
  const parents = new Map(
    catalog.lessonNodes.map(({ id, parent_id: parentId }) => [id, parentId]),
  )
  return Object.fromEntries(catalog.lessonNodes.map((node) => {
    let depth = 0
    let parentId = node.parent_id
    const visited = new Set<string>()
    while (parentId !== null && !visited.has(parentId)) {
      visited.add(parentId)
      depth += 1
      parentId = parents.get(parentId) ?? null
    }
    return [node.id, depth]
  }))
})

const selectedRanges = computed(() => {
  const indexes = catalog.materialUnits
    .filter(({ id }) => selectedUnitIds.value.includes(id))
    .map(({ unit_index: unitIndex }) => unitIndex)
    .sort((left, right) => left - right)
  const ranges: Array<{ start: number; end: number }> = []
  for (const index of indexes) {
    const current = ranges.at(-1)
    if (current && index === current.end + 1) current.end = index
    else ranges.push({ start: index, end: index })
  }
  return ranges
})
const activeCropUnit = computed(
  () => catalog.materialUnits.find(({ id }) => id === cropUnitId.value) ?? null,
)
const activeCropRegions = computed(() => (
  (cropRole.value === 'question' ? questionRegions.value : answerRegions.value)
    .filter(({ material_unit_id: unitId }) => unitId === cropUnitId.value)
))
const referencePptLinks = computed(() => catalog.materialLinks.filter(
  (link) => (
    link.is_active
    && link.confirmation_status === 'confirmed'
    && link.purpose === 'reference_ppt'
  ),
))
const semesterMaterialBySource = computed(() => Object.fromEntries(
  catalog.semesterMaterials.map((record) => [
    record.material_source_id,
    record,
  ]),
))
const lessonProgressByNode = computed(() => Object.fromEntries(
  catalog.semesterLessonProgress.map((progress) => [
    progress.lesson_node_id,
    progress.status,
  ]),
))

watch(
  () => lessonForm.nodeType,
  () => {
    lessonForm.parentId = ''
    lessonForm.durationMinutes = lessonForm.nodeType === 'lesson' ? 45 : 0
  },
)
watch(
  () => catalog.semesterMaterials,
  (records) => {
    const eligible = new Set(
      records
        .filter((record) => (
          record.is_active
          && record.parse_status === 'parsed'
          && !record.has_unparsed_update
        ))
        .map(({ id }) => id),
    )
    if (!eligible.has(selectedSemesterMaterialId.value)) {
      selectedSemesterMaterialId.value = [...eligible][0] ?? ''
    }
  },
  { immediate: true },
)
watch(
  () => catalog.selectedSemester,
  (semester) => {
    if (!semester) return
    semesterEdit.plannedNewLessonCount = semester.planned_new_lesson_count
    semesterEdit.status = semester.status
  },
  { immediate: true },
)
watch(
  () => catalog.teachingPreferences,
  (preferences) => {
    if (preferences) applyPreparationPreferences(preferences.payload)
  },
  { immediate: true },
)
watch(
  () => preparationPreferences.supplement_from_references,
  (enabled) => {
    if (!enabled) {
      preparationPreferences.supplement_question_limit = 0
      preparationPreferences.supplement_as_source_image = false
    } else if (preparationPreferences.supplement_question_limit === 0) {
      preparationPreferences.supplement_question_limit = 2
      preparationPreferences.supplement_as_source_image = true
    }
  },
)
watch(
  () => catalog.selectedLessonId,
  () => {
    selectedAssessmentIds.value = []
    selectedQuestionIds.value = []
    packFormError.value = ''
  },
)
watch(
  referencePptLinks,
  (links) => {
    const activeIds = new Set(links.map(({ id }) => id))
    for (const link of links) {
      if (!referencePptIntents[link.id]) {
        referencePptIntents[link.id] = 'keep'
      }
    }
    for (const linkId of Object.keys(referencePptIntents)) {
      if (!activeIds.has(linkId)) delete referencePptIntents[linkId]
    }
  },
)

onMounted(() => {
  void catalog.load()
})

function requestToken(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID().replaceAll('-', '')}`
}

async function submitCurriculum(): Promise<void> {
  if (!curriculumForm.title.trim()) return
  try {
    await catalog.createSemesterWorkspace({
      curriculum: {
        title: curriculumForm.title.trim(),
        grade_level: curriculumForm.gradeLevel,
        volume: curriculumForm.volume,
        publisher: curriculumForm.publisher.trim() || null,
        edition_label: curriculumForm.editionLabel.trim() || null,
      },
      semester: {
        school_year: curriculumForm.schoolYear.trim(),
        term: curriculumForm.term,
        planned_new_lesson_count: curriculumForm.plannedNewLessonCount,
      },
    })
    curriculumForm.title = ''
    curriculumForm.publisher = ''
    curriculumForm.editionLabel = ''
    showCurriculumForm.value = false
  } catch {
    // The store keeps the user-safe message and the form stays intact.
  }
}

async function saveSemesterState(): Promise<void> {
  try {
    await catalog.updateSemester({
      plannedNewLessonCount: semesterEdit.plannedNewLessonCount,
      status: semesterEdit.status,
    })
  } catch {
    // The store keeps the user-safe message and the form stays intact.
  }
}

async function submitLesson(): Promise<void> {
  if (!lessonForm.title.trim()) return
  if (lessonForm.nodeType !== 'chapter' && !lessonForm.parentId) return
  try {
    await catalog.createLesson({
      request_token: requestToken('lesson'),
      parent_id: lessonForm.parentId || null,
      node_type: lessonForm.nodeType,
      title: lessonForm.title.trim(),
      duration_minutes: lessonForm.nodeType === 'lesson'
        ? lessonForm.durationMinutes
        : null,
      source_kind: 'teacher',
    })
    lessonForm.title = ''
    showLessonForm.value = false
  } catch {
    // The store keeps the user-safe message and the form stays intact.
  }
}

function startRename(node: LessonNode): void {
  editingNodeId.value = node.id
  editingTitle.value = node.title
}

async function saveRename(node: LessonNode): Promise<void> {
  const title = editingTitle.value.trim()
  if (!title) return
  try {
    await catalog.updateLesson(node, { title })
    editingNodeId.value = null
  } catch {
    // Keep the edit visible so the teacher can refresh or retry.
  }
}

async function toggleActive(node: LessonNode): Promise<void> {
  try {
    await catalog.updateLesson(node, { isActive: !node.is_active })
  } catch {
    // The store displays the conflict or load error.
  }
}

function siblingPosition(node: LessonNode): { index: number; count: number } {
  const siblings = catalog.lessonNodes
    .filter(({ parent_id: parentId }) => parentId === node.parent_id)
    .sort((left, right) => left.sort_order - right.sort_order)
  return {
    index: siblings.findIndex(({ id }) => id === node.id),
    count: siblings.length,
  }
}

function canMove(node: LessonNode, direction: -1 | 1): boolean {
  const { index, count } = siblingPosition(node)
  return direction < 0 ? index > 0 : index >= 0 && index < count - 1
}

function nodeTypeLabel(nodeType: LessonNodeType): string {
  return {
    chapter: '章',
    section: '节',
    lesson: '课时',
  }[nodeType]
}

function materialStatusLabel(status: string): string {
  return {
    available: '可用',
    missing: '文件缺失',
    needs_relocation: '需重新定位',
  }[status] ?? status
}

async function openMaterial(material: MaterialVersion): Promise<void> {
  selectedUnitIds.value = []
  cropUnitId.value = null
  cropDraft.value = null
  await catalog.openMaterial(material)
}

async function importMaterialCopy(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    await catalog.importMaterialCopy(file, importMaterialRole.value)
  } catch {
    // The store keeps a safe upload error visible.
  }
}

function semesterParseStatusLabel(
  record: SemesterMaterialRecord | undefined,
): string {
  if (!record) return '尚未登记'
  if (record.has_unparsed_update) return '有新版本待解析'
  return {
    not_started: '尚未解析',
    parsed: '已解析',
    needs_review: '解析待核对',
    failed: '解析失败',
  }[record.parse_status]
}

function semesterMappingStatusLabel(
  status: SemesterMaterialMappingStatus | undefined,
): string {
  if (!status) return '尚未映射课时'
  return {
    unmapped: '尚未映射课时',
    proposed: 'AI 建议待确认',
    partial: '部分已映射',
    confirmed: '课时映射已确认',
    needs_review: '新版本映射待复核',
    conflict: '映射有冲突',
  }[status]
}

function lessonProgressStatus(
  lessonNodeId: string,
): SemesterLessonProgressStatus {
  return lessonProgressByNode.value[lessonNodeId] ?? 'not_started'
}

function proposalLessonLabel(
  proposal: SemesterMappingProposal,
  lessonRef: string,
): string {
  if (!lessonRef.startsWith('proposal:')) {
    return catalog.lessonNodes.find(({ id }) => id === lessonRef)?.title
      ?? '现有课时（名称已变化）'
  }
  const key = lessonRef.slice('proposal:'.length)
  for (const chapter of proposal.payload.tree) {
    for (const section of chapter.sections) {
      const lesson = section.lessons.find((item) => item.key === key)
      if (lesson) return `${chapter.title} / ${section.title} / ${lesson.title}`
    }
  }
  return '拟建课时（名称不可用）'
}

function proposalMaterialLabel(materialRecordId: string): string {
  return catalog.semesterMaterials.find(
    ({ id }) => id === materialRecordId,
  )?.display_name ?? '本学期资料'
}

async function changeLessonProgress(
  lessonNodeId: string,
  event: Event,
): Promise<void> {
  const target = event.target as HTMLSelectElement
  await catalog.setSemesterLessonProgress(
    lessonNodeId,
    target.value as SemesterLessonProgressStatus,
  )
}

async function attachMaterialToSemester(
  material: MaterialVersion,
): Promise<void> {
  await catalog.attachSemesterMaterial(
    material,
    materialRoleDrafts[material.id] ?? 'supplement',
  )
}

async function changeSemesterMaterialRole(
  record: SemesterMaterialRecord,
  event: Event,
): Promise<void> {
  const target = event.target as HTMLSelectElement
  await catalog.updateSemesterMaterial(record, {
    materialRole: target.value as SemesterMaterialRole,
  })
}

async function changeSemesterMaterialRoleBySource(
  sourceId: string,
  event: Event,
): Promise<void> {
  const record = semesterMaterialBySource.value[sourceId]
  if (!record) return
  await changeSemesterMaterialRole(record, event)
}

async function prepareSemesterMapping(): Promise<void> {
  if (!selectedSemesterMaterialId.value) return
  await catalog.prepareSemesterMapping([selectedSemesterMaterialId.value])
}

async function generateSemesterMapping(): Promise<void> {
  if (!selectedSemesterMaterialId.value) return
  await catalog.generateSemesterMapping([selectedSemesterMaterialId.value])
}

async function relocateMaterialCopy(
  material: MaterialVersion,
  event: Event,
): Promise<void> {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    await catalog.relocateMaterialCopy(material, file)
  } catch {
    // The store keeps a safe relocation error visible.
  }
}

function toggleUnit(unit: MaterialUnit): void {
  selectedUnitIds.value = selectedUnitIds.value.includes(unit.id)
    ? selectedUnitIds.value.filter((id) => id !== unit.id)
    : [...selectedUnitIds.value, unit.id]
}

async function confirmRanges(): Promise<void> {
  if (selectedRanges.value.length === 0) return
  try {
    await catalog.confirmMaterialRanges(
      selectedRanges.value,
      linkPurpose.value,
      linkNote.value.trim() || null,
    )
    selectedUnitIds.value = []
    linkNote.value = ''
  } catch {
    // The store reports whether any range was saved.
  }
}

function startUnitEdit(unit: MaterialUnit): void {
  editingUnitId.value = unit.id
  unitTitle.value = unit.title ?? ''
  unitManualText.value = unit.text_excerpt
}

async function saveUnitEdit(unit: MaterialUnit): Promise<void> {
  try {
    await catalog.saveMaterialUnitLabel(unit, {
      title: unitTitle.value.trim() || null,
      manualText: unitManualText.value.trim(),
      formulaReviewRequired: unit.formula_review_required
        || Boolean(unitManualText.value.trim()),
    })
    editingUnitId.value = null
  } catch {
    // Keep the correction form open on conflict.
  }
}

function unitIsConfirmed(unit: MaterialUnit): boolean {
  return catalog.materialLinks.some((link) => (
    link.is_active
    && link.confirmation_status === 'confirmed'
    && link.material_version_id === unit.material_version_id
    && unit.unit_index >= link.start_unit
    && unit.unit_index <= link.end_unit
  ))
}

function beginCrop(unit: MaterialUnit, role: ExerciseRegionRole): void {
  if (!catalog.selectedLessonId || !unitIsConfirmed(unit)) return
  cropUnitId.value = unit.id
  cropRole.value = role
  cropStart.value = null
  cropDraft.value = null
}

function pointerPosition(event: PointerEvent): { x: number; y: number } {
  const target = event.currentTarget as HTMLElement
  const bounds = target.getBoundingClientRect()
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  }
}

function startCrop(event: PointerEvent): void {
  const target = event.currentTarget as HTMLElement
  target.setPointerCapture(event.pointerId)
  cropStart.value = pointerPosition(event)
  cropDraft.value = {
    x0: cropStart.value.x,
    y0: cropStart.value.y,
    x1: cropStart.value.x,
    y1: cropStart.value.y,
  }
}

function moveCrop(event: PointerEvent): void {
  if (cropStart.value === null) return
  const current = pointerPosition(event)
  cropDraft.value = {
    x0: Math.min(cropStart.value.x, current.x),
    y0: Math.min(cropStart.value.y, current.y),
    x1: Math.max(cropStart.value.x, current.x),
    y1: Math.max(cropStart.value.y, current.y),
  }
}

function finishCrop(event: PointerEvent): void {
  moveCrop(event)
  const crop = cropDraft.value
  const unit = activeCropUnit.value
  cropStart.value = null
  cropDraft.value = null
  if (
    crop === null
    || unit === null
    || crop.x1 - crop.x0 < 0.01
    || crop.y1 - crop.y0 < 0.01
  ) return
  const materialName = catalog.materials.find(
    ({ id }) => id === unit.material_version_id,
  )?.display_name ?? '已确认资料'
  const region: DraftExerciseRegion = {
    key: globalThis.crypto.randomUUID(),
    material_unit_id: unit.id,
    unit_index: unit.unit_index,
    material_name: materialName,
    preview_url: unit.preview_url,
    crop,
  }
  if (cropRole.value === 'question') questionRegions.value.push(region)
  else answerRegions.value.push(region)
}

function cropStyle(crop: NormalizedCrop): Record<string, string> {
  return {
    left: `${crop.x0 * 100}%`,
    top: `${crop.y0 * 100}%`,
    width: `${(crop.x1 - crop.x0) * 100}%`,
    height: `${(crop.y1 - crop.y0) * 100}%`,
  }
}

function removeRegion(role: ExerciseRegionRole, key: string): void {
  if (role === 'question') {
    questionRegions.value = questionRegions.value.filter(
      (region) => region.key !== key,
    )
  } else {
    answerRegions.value = answerRegions.value.filter(
      (region) => region.key !== key,
    )
  }
}

function resetExerciseEditor(): void {
  editingExercise.value = null
  questionRegions.value = []
  answerRegions.value = []
  cropUnitId.value = null
  cropDraft.value = null
  exerciseForm.questionNumber = ''
  exerciseForm.contentLabel = ''
  exerciseForm.difficulty = 'unrated'
  exerciseForm.classroomUse = 'guided_practice'
  exerciseForm.estimatedMinutes = 5
  exerciseForm.teachingFocus = ''
  exerciseForm.teacherNote = ''
  exerciseForm.selectionStatus = 'classroom_candidate'
}

function startExerciseEdit(candidate: ExerciseCandidate): void {
  editingExercise.value = candidate
  exerciseForm.questionNumber = candidate.question_number ?? ''
  exerciseForm.contentLabel = candidate.content_label ?? ''
  exerciseForm.difficulty = candidate.difficulty
  exerciseForm.classroomUse = candidate.classroom_use
  exerciseForm.estimatedMinutes = candidate.estimated_minutes ?? 5
  exerciseForm.teachingFocus = candidate.teaching_focus ?? ''
  exerciseForm.teacherNote = candidate.teacher_note ?? ''
  exerciseForm.selectionStatus = candidate.selection_status
  questionRegions.value = candidate.question_regions.map((region) => ({
    key: region.id,
    material_unit_id: region.material_unit_id,
    unit_index: region.unit_index,
    material_name: region.material_name,
    preview_url: region.preview_url,
    crop: region.crop,
  }))
  answerRegions.value = candidate.answer_regions.map((region) => ({
    key: region.id,
    material_unit_id: region.material_unit_id,
    unit_index: region.unit_index,
    material_name: region.material_name,
    preview_url: region.preview_url,
    crop: region.crop,
  }))
}

async function saveExercise(): Promise<void> {
  if (questionRegions.value.length === 0) return
  const input: ExerciseCandidateInput = {
    question_number: exerciseForm.questionNumber.trim() || null,
    content_label: exerciseForm.contentLabel.trim() || null,
    difficulty: exerciseForm.difficulty,
    classroom_use: exerciseForm.classroomUse,
    estimated_minutes: exerciseForm.estimatedMinutes || null,
    teaching_focus: exerciseForm.teachingFocus.trim() || null,
    teacher_note: exerciseForm.teacherNote.trim() || null,
    selection_status: exerciseForm.selectionStatus,
    answer_status: answerRegions.value.length
      ? editingExercise.value?.answer_status ?? 'candidate'
      : 'missing',
    question_regions: questionRegions.value.map(
      ({ material_unit_id: materialUnitId, crop }) => ({
        material_unit_id: materialUnitId,
        crop,
      }),
    ),
    answer_regions: answerRegions.value.map(
      ({ material_unit_id: materialUnitId, crop }) => ({
        material_unit_id: materialUnitId,
        crop,
      }),
    ),
  }
  try {
    await catalog.saveExerciseCandidate(editingExercise.value, input)
    resetExerciseEditor()
  } catch {
    // Keep all teacher-drawn regions available for correction or retry.
  }
}

async function changeSelection(
  candidate: ExerciseCandidate,
  selectionStatus: ExerciseSelectionStatus,
): Promise<void> {
  try {
    await catalog.updateExerciseCandidate(candidate, {
      selection_status: selectionStatus,
    })
  } catch {
    // The existing selection remains visible on conflict.
  }
}

function onSelectionChange(
  candidate: ExerciseCandidate,
  event: Event,
): void {
  const value = (event.target as HTMLSelectElement).value
  if (!['classroom_candidate', 'backup', 'excluded'].includes(value)) return
  void changeSelection(candidate, value as ExerciseSelectionStatus)
}

async function changeAnswerStatus(
  candidate: ExerciseCandidate,
  answerStatus: ExerciseAnswerStatus,
): Promise<void> {
  try {
    await catalog.updateExerciseCandidate(candidate, {
      answer_status: answerStatus,
    })
  } catch {
    // The server keeps the previous verified state on conflict.
  }
}

function answerStatusLabel(status: ExerciseAnswerStatus): string {
  return {
    candidate: '答案待核对',
    teacher_verified: '教师已核对',
    rejected: '答案不匹配',
    missing: '暂无答案',
  }[status]
}

function duplicateBasisLabel(basis: string[]): string {
  const labels = basis.map((item) => ({
    same_teacher_clue: '教师线索相同',
    same_question_number_and_source: '同一资料题号相同',
  }[item] ?? item))
  return labels.join('、')
}

function toggleAssessment(assessment: AssessmentChoice): void {
  selectedAssessmentIds.value = selectedAssessmentIds.value.includes(
    assessment.assessment_id,
  )
    ? selectedAssessmentIds.value.filter(
      (id) => id !== assessment.assessment_id,
    )
    : [...selectedAssessmentIds.value, assessment.assessment_id]
}

function toggleQuestion(question: QuestionEvidenceChoice): void {
  selectedQuestionIds.value = selectedQuestionIds.value.includes(
    question.question_id,
  )
    ? selectedQuestionIds.value.filter((id) => id !== question.question_id)
    : [...selectedQuestionIds.value, question.question_id]
}

function assessmentMatchesClass(assessment: AssessmentChoice): boolean {
  const className = resourcePackForm.className.trim()
  return !className || assessment.classes.some(
    (item) => item.class_name === className,
  )
}

function textList(value: string): string[] {
  return [...new Set(
    value
      .split(/[\n,，、;；]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  )]
}

function applyPreparationPreferences(
  value: TeachingPreferencesPayload,
): void {
  Object.assign(preparationPreferences, clonePlain(value))
}

function clonePlain<T>(value: T): T {
  return structuredClone(toRaw(value))
}

function resetPreparationPreferences(): void {
  if (catalog.teachingPreferences) {
    applyPreparationPreferences(catalog.teachingPreferences.payload)
  }
}

async function savePersonalPreparationPreferences(): Promise<void> {
  try {
    await catalog.saveTeachingPreferences(
      clonePlain(preparationPreferences),
    )
  } catch {
    // Keep the current checkboxes visible so the teacher can refresh or retry.
  }
}

function preparationPreferenceSummary(
  value: TeachingPreferencesPayload,
): string {
  const items: string[] = []
  if (value.label_textbook_pages) items.push('教材页码 28 号')
  if (value.trim_excess_practice) {
    const level = {
      light: '少量精简',
      moderate: '适度精简',
      strong: '大幅精简',
    }[value.practice_trim_level]
    items.push(level)
  }
  if (value.preserve_teaching_examples) items.push('保留讲授例题')
  if (value.supplement_from_references) {
    items.push(`原图补题最多 ${value.supplement_question_limit} 道`)
  }
  return items.join(' · ') || '不启用自动改编偏好'
}

async function freezeResourcePack(): Promise<void> {
  const className = resourcePackForm.className.trim()
  packFormError.value = ''
  if (selectedAssessmentIds.value.length && !className) {
    packFormError.value = '选择历史考试时必须填写对应班级。'
    return
  }
  try {
    await catalog.freezeResourcePack({
      class_name: className || null,
      lesson_type: 'new_lesson',
      teacher_context: resourcePackForm.teacherContext.trim() || null,
      reference_ppt_intents: Object.fromEntries(
        referencePptLinks.value.map((link) => [
          link.id,
          referencePptIntents[link.id] ?? 'keep',
        ]),
      ),
      question_ids: selectedQuestionIds.value,
      assessment_ids: selectedAssessmentIds.value,
      knowledge_scope: textList(resourcePackForm.knowledgeScope),
      preparation_preferences: clonePlain(preparationPreferences),
    })
    selectedAssessmentIds.value = []
    selectedQuestionIds.value = []
  } catch {
    // Keep every teacher choice visible for correction or retry.
  }
}

function packMissingCount(pack: ResourcePack): number {
  const value = pack.payload.missing_and_uncertain
  return Array.isArray(value) ? value.length : 0
}

function packPreparationPreferenceSummary(pack: ResourcePack): string {
  const value = pack.payload.preparation_preferences
  if (!value || typeof value !== 'object') return '旧版本未记录课件改编偏好'
  return preparationPreferenceSummary(
    value as TeachingPreferencesPayload,
  )
}

function startDraftEdit(draft: LessonDraft): void {
  editingDraft.value = draft
  draftEdit.value = clonePlain(draft.payload)
}

async function saveDraftEdit(confirmed: boolean): Promise<void> {
  if (!editingDraft.value || !draftEdit.value) return
  try {
    await catalog.reviseLessonDraft(
      editingDraft.value,
      draftEdit.value,
      confirmed,
    )
    editingDraft.value = null
    draftEdit.value = null
  } catch {
    // Keep teacher edits visible for correction.
  }
}

function phaseLabel(phase: string): string {
  return {
    introduction: '导入',
    exploration: '探索',
    example: '例题',
    practice: '练习',
    summary: '总结',
  }[phase] ?? phase
}

function recommendationLabel(action: string): string {
  return {
    include: '进入课堂',
    backup: '作为备用',
    move_after_class: '移到课后',
    exclude: '不使用',
    replace_shorter: '换成更短题',
  }[action] ?? action
}

function citationLabel(sourceId: string): string {
  return catalog.lessonDraftPreflight?.references.find(
    ({ id }) => id === sourceId,
  )?.label ?? sourceId
}

const selectedSlidePlan = computed(
  () => catalog.slidePlans.find(
    ({ id }) => id === catalog.selectedSlidePlanId,
  ) ?? null,
)

async function startSlidePlanReview(plan: SlidePlan): Promise<void> {
  await catalog.selectSlidePlan(plan)
  editingSlidePlan.value = plan
  slideOperationEdits.value = plan.payload.operations.map((item) => ({
    operation_id: item.operation_id,
    decision: item.decision,
    reason: item.reason,
    planned_minutes: item.planned_minutes,
    teacher_note: item.teacher_note,
  }))
  slideReviewNote.value = ''
}

async function saveSlidePlanReview(
  approveLowRiskDeletions = false,
): Promise<void> {
  const plan = editingSlidePlan.value ?? selectedSlidePlan.value
  if (!plan) return
  try {
    await catalog.reviewSlidePlan(
      plan,
      approveLowRiskDeletions ? [] : slideOperationEdits.value,
      {
        approveLowRiskDeletions,
        reviewNote: slideReviewNote.value,
      },
    )
    const latest = catalog.slidePlans[0]
    if (latest) await startSlidePlanReview(latest)
  } catch {
    // Keep every review decision visible for correction.
  }
}

function operationKindLabel(kind: string): string {
  return {
    keep_slide: '保留整页',
    hide_slide: '隐藏整页（人工）',
    delete_slide: '删除整页',
    reorder_slide: '调整整页顺序',
    copy_slide: '复制整页（人工）',
    delete_shape: '删除普通对象',
    modify_text_box: '修改文本框（人工）',
    add_text_box: '新增文本框',
    add_slide: '新增普通页面',
    insert_static_image: '插入静态图片',
    move_static_image: '移动静态图片',
    scale_static_image: '缩放静态图片',
    crop_static_image: '裁剪静态图片',
    replace_static_image: '替换静态图片',
    manual_note: '人工修改清单',
  }[kind] ?? kind
}

function planStatusLabel(status: SlidePlan['status']): string {
  return {
    in_review: '审核中',
    approved: '全部决定完成',
    invalidated: '来源变化，计划已失效',
  }[status]
}

function executionStatusLabel(status: PptxExecution['status']): string {
  return {
    running: '正在创建隔离副本',
    verifying: '正在逐项验证',
    publishing: '正在发布新版本',
    published: '验证通过，已生成新版本',
    failed: '执行或验证未通过',
    cancelled: '已取消，未发布',
    interrupted: '上次运行中断，等待处理',
  }[status]
}

function executionErrorLabel(code: string | null): string {
  if (code === null) return ''
  return {
    wps_helper_timeout: 'WPS 响应超时，源文件未修改。',
    generation_budget_exceeded: '机器处理超过 5 分钟，本次已停止且没有发布。',
    source_or_output_locked: '文件正在被占用，请关闭对应文件后创建新计划再试。',
    execution_state_conflict: '来源或执行状态发生变化，本次没有发布。',
    verification_failed: '候选副本未通过完整验证，本次没有发布。',
    wps_execution_failed: 'WPS 执行失败，本次没有发布。',
    application_restarted: '应用运行期间中断，暂存内容已保留。',
    teacher_cancelled: '教师已取消，本次没有发布。',
  }[code] ?? '本次没有发布，请保留诊断信息后处理。'
}

function formatMachineElapsed(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`
}

async function executeApprovedPlan(plan: SlidePlan): Promise<void> {
  if (!window.confirm(
    '将只修改隔离副本，并在全部验证通过后生成一个新文件。确认继续吗？',
  )) return
  await catalog.executePptx(plan)
}

async function discardExecutionStaging(run: PptxExecution): Promise<void> {
  if (!window.confirm(
    '确认清理这次运行保留的暂存副本和诊断预览吗？已发布课件不会删除。',
  )) return
  await catalog.discardPptxStaging(run)
}

function packageForVersion(versionId: string | null): UpClassPackage | undefined {
  if (versionId === null) return undefined
  return catalog.upClassPackages.find(
    ({ pptx_version_id: pptxVersionId }) => pptxVersionId === versionId,
  )
}

async function createFinalPackage(versionId: string | null): Promise<void> {
  if (versionId === null) return
  if (!window.confirm(
    '确认这份课件已经定稿，并生成可离线上课的完整包吗？生成后不会覆盖旧版本。',
  )) return
  await catalog.createUpClassPackage(versionId)
}

async function activateFinalPackage(item: UpClassPackage): Promise<void> {
  if (!window.confirm(
    `确认把第 ${item.version_number} 版重新设为当前上课版本吗？之后生成的版本不会删除。`,
  )) return
  await catalog.activateUpClassPackage(item)
}

async function submitPostReview(item: UpClassPackage): Promise<void> {
  try {
    await catalog.savePostLessonReview(item, {
      timing: postReviewForm.timing,
      question_outcome: postReviewForm.questionOutcome,
      reteach_points: postReviewForm.reteachPoints
        .split(/[；;\n]/)
        .map((value) => value.trim())
        .filter(Boolean),
      next_action: postReviewForm.nextAction,
      note: postReviewForm.note.trim() || null,
      use_in_next_version: postReviewForm.useInNextVersion,
    })
    reviewingPackageId.value = null
    postReviewForm.reteachPoints = ''
    postReviewForm.note = ''
  } catch {
    // Keep the one-minute review visible for correction or retry.
  }
}

async function submitClassVariant(): Promise<void> {
  const pack = catalog.resourcePacks.find(
    ({ id }) => id === catalog.selectedResourcePackId,
  )
  if (!pack || !classVariantForm.className.trim()) return
  try {
    await catalog.deriveClassVariant(pack, {
      class_name: classVariantForm.className.trim(),
      teacher_context: classVariantForm.teacherContext.trim() || null,
      assessment_ids: [...selectedAssessmentIds.value],
      knowledge_scope: resourcePackForm.knowledgeScope
        .split(/[；;,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
      prior_review_ids: [...selectedPriorReviewIds.value],
    })
    classVariantForm.teacherContext = ''
    selectedPriorReviewIds.value = []
  } catch {
    // Keep class-specific choices visible for correction or retry.
  }
}

function operationTargetLabel(operation: SlideOperation): string {
  const page = operation.target.generated_page_number
  const summary = operation.target.content_summary
  if (typeof page === 'number') {
    return `生成时第 ${page} 页${typeof summary === 'string' ? ` · ${summary}` : ''}`
  }
  return typeof summary === 'string' ? summary : '新增或人工处理对象'
}

function displayValue(
  record: Record<string, unknown>,
  key: string,
  fallback = '',
): string {
  const value = record[key]
  return typeof value === 'string' || typeof value === 'number'
    ? String(value)
    : fallback
}

function hasBatchDeletions(plan: SlidePlan): boolean {
  return plan.payload.operations.some(
    (item) => (
      item.kind === 'delete_slide'
      && item.risk === 'low'
      && item.decision === 'proposed'
    ),
  )
}

function operationForReview(operationId: string): SlideOperation {
  const operation = selectedSlidePlan.value?.payload.operations.find(
    ({ operation_id: id }) => id === operationId,
  )
  return operation ?? {
    operation_id: operationId,
    kind: 'manual_note',
    decision: 'proposed',
    target: {},
    reason: '操作信息不可用',
    citations: [],
    planned_minutes: 0,
    risk: 'blocked',
    execution_mode: 'manual_only',
    support_note: '请刷新计划后继续。',
    details: {},
    teacher_note: null,
  }
}
</script>

<template>
  <section class="teaching-prep-page" aria-labelledby="teaching-prep-title">
    <header class="teaching-prep-page__header">
      <div>
        <p class="teaching-prep-page__eyebrow">初中数学 · 个人备课</p>
        <h1 id="teaching-prep-title" tabindex="-1">备课工作台</h1>
        <p class="teaching-prep-page__summary">
          先建立本学期计划和资料台账，再按实际进度准备每一课时。
        </p>
      </div>
      <span class="teaching-prep-page__safety">原课件不会被覆盖</span>
    </header>

    <div
      v-if="catalog.errorMessage"
      class="teaching-prep-page__error"
      role="alert"
    >
      <span>{{ catalog.errorMessage }}</span>
      <button type="button" @click="catalog.load">重新读取</button>
    </div>

    <div class="teaching-prep-workspace">
      <aside class="teaching-prep-panel teaching-prep-panel--curricula">
        <div class="teaching-prep-panel__header">
          <div>
            <p>第一步</p>
            <h2>学期与教材</h2>
          </div>
          <button
            type="button"
            class="teaching-prep-button teaching-prep-button--quiet"
            @click="showCurriculumForm = !showCurriculumForm"
          >
            新建学期库
          </button>
        </div>

        <form
          v-if="showCurriculumForm"
          class="teaching-prep-form"
          @submit.prevent="submitCurriculum"
        >
          <label>
            本学期教材名称
            <input
              v-model="curriculumForm.title"
              required
              maxlength="160"
              placeholder="例如：七年级下册"
            >
          </label>
          <div class="teaching-prep-form__row">
            <label>
              年级
              <select v-model.number="curriculumForm.gradeLevel">
                <option :value="7">七年级</option>
                <option :value="8">八年级</option>
                <option :value="9">九年级</option>
              </select>
            </label>
            <label>
              册别
              <select v-model="curriculumForm.volume">
                <option value="first">上册</option>
                <option value="second">下册</option>
                <option value="whole_year">全一册</option>
              </select>
            </label>
          </div>
          <div class="teaching-prep-form__row">
            <label>
              学年
              <input
                v-model="curriculumForm.schoolYear"
                required
                maxlength="20"
                placeholder="例如：2026-2027"
              >
            </label>
            <label>
              学期
              <select v-model="curriculumForm.term">
                <option value="first">第一学期</option>
                <option value="second">第二学期</option>
              </select>
            </label>
          </div>
          <label>
            计划新授课时数
            <input
              v-model.number="curriculumForm.plannedNewLessonCount"
              type="number"
              min="0"
              max="500"
            >
          </label>
          <label>
            出版社（可选）
            <input v-model="curriculumForm.publisher" maxlength="120">
          </label>
          <div class="teaching-prep-form__actions">
            <button
              type="submit"
              class="teaching-prep-button teaching-prep-button--primary"
              :disabled="catalog.saveState === 'saving'"
            >
              建立学期库
            </button>
            <button
              type="button"
              class="teaching-prep-button teaching-prep-button--quiet"
              @click="showCurriculumForm = false"
            >
              取消
            </button>
          </div>
        </form>

        <p
          v-if="catalog.loadState === 'loading' && catalog.curricula.length === 0"
          class="teaching-prep-empty"
        >
          正在读取课时树…
        </p>
        <p v-else-if="catalog.curricula.length === 0" class="teaching-prep-empty">
          先新建一个教材版本；不需要一次录完整册。
        </p>
        <template v-else>
          <button
            v-for="item in catalog.curricula"
            :key="item.id"
            type="button"
            class="teaching-prep-curriculum"
            :class="{ 'is-selected': item.id === catalog.selectedCurriculumId }"
            @click="catalog.selectCurriculum(item.id)"
          >
            <strong>{{ item.title }}</strong>
            <span>{{ item.grade_level }} 年级 · {{ item.volume === 'first' ? '上册' : item.volume === 'second' ? '下册' : '全一册' }}</span>
            <span
              v-if="catalog.semesters.find(({ curriculum_id: id }) => id === item.id)"
            >
              {{
                catalog.semesters.find(({ curriculum_id: id }) => id === item.id)?.school_year
              }}
              ·
              {{
                catalog.semesters.find(({ curriculum_id: id }) => id === item.id)?.term === 'first'
                  ? '第一学期'
                  : '第二学期'
              }}
            </span>
          </button>
        </template>
      </aside>

      <main class="teaching-prep-panel teaching-prep-panel--tree">
        <div class="teaching-prep-panel__header">
          <div>
            <p>第二步</p>
            <h2>{{ catalog.selectedCurriculum?.title ?? '个人课时树' }}</h2>
          </div>
          <button
            type="button"
            class="teaching-prep-button teaching-prep-button--primary"
            :disabled="catalog.selectedCurriculumId === null"
            @click="showLessonForm = !showLessonForm"
          >
            新增节点
          </button>
        </div>

        <section
          v-if="catalog.selectedSemester"
          class="teaching-prep-semester-summary"
          aria-label="本学期状态"
        >
          <div>
            <strong>
              {{ catalog.selectedSemester.school_year }}
              ·
              {{ catalog.selectedSemester.term === 'first' ? '第一学期' : '第二学期' }}
            </strong>
            <span>
              已上 {{ catalog.selectedSemester.taught_lesson_count }}
              / 计划 {{ catalog.selectedSemester.planned_new_lesson_count }} 个新授课时
            </span>
          </div>
          <div>
            <span>备课中 {{ catalog.selectedSemester.preparing_lesson_count }}</span>
            <span>待上课 {{ catalog.selectedSemester.ready_lesson_count }}</span>
            <span>
              资料已解析
              {{ catalog.selectedSemester.parsed_material_count }}
              / {{ catalog.selectedSemester.material_count }}
            </span>
            <span>
              映射已确认
              {{ catalog.selectedSemester.mapped_material_count }}
              / {{ catalog.selectedSemester.material_count }}
            </span>
          </div>
          <form
            class="teaching-prep-semester-summary__edit"
            aria-label="调整学期计划"
            @submit.prevent="saveSemesterState"
          >
            <label>
              计划新授课时
              <input
                v-model.number="semesterEdit.plannedNewLessonCount"
                type="number"
                min="0"
                max="500"
                required
              >
            </label>
            <label>
              学期状态
              <select v-model="semesterEdit.status">
                <option value="planning">规划中</option>
                <option value="active">进行中</option>
                <option value="completed">已结束</option>
                <option value="archived">已归档</option>
              </select>
            </label>
            <button
              type="submit"
              class="teaching-prep-button teaching-prep-button--quiet"
              :disabled="catalog.saveState === 'saving'"
            >
              保存学期状态
            </button>
          </form>
        </section>

        <section
          v-if="catalog.selectedSemester"
          class="teaching-prep-semester-mapping"
          aria-labelledby="semester-mapping-title"
        >
          <div class="teaching-prep-units__header">
            <div>
              <h3 id="semester-mapping-title">AI 整理课时与页码</h3>
              <span>每次整理一份已完成本地解析的资料；建议确认前不会改正式课时树。</span>
            </div>
          </div>
          <p
            v-if="catalog.semesterMaterials.length === 0"
            class="teaching-prep-empty"
          >
            先把教材、教辅或 PPT 加入本学期并完成逐份解析。
          </p>
          <div v-else class="teaching-prep-semester-mapping__materials">
            <label
              v-for="record in catalog.semesterMaterials"
              :key="record.id"
              :class="{ 'is-disabled': (
                record.parse_status !== 'parsed'
                || record.has_unparsed_update
                || !record.is_active
              ) }"
            >
              <input
                v-model="selectedSemesterMaterialId"
                type="radio"
                name="semester-mapping-material"
                :value="record.id"
                :disabled="(
                  record.parse_status !== 'parsed'
                  || record.has_unparsed_update
                  || !record.is_active
                )"
              >
              <span>
                <strong>{{ record.display_name }}</strong>
                {{ semesterParseStatusLabel(record) }}
              </span>
            </label>
          </div>
          <div class="teaching-prep-form__actions">
            <button
              type="button"
              class="teaching-prep-button teaching-prep-button--quiet"
              :disabled="(
                !selectedSemesterMaterialId
                || catalog.loadState === 'loading'
              )"
              @click="prepareSemesterMapping"
            >
              检查发送范围
            </button>
            <button
              type="button"
              class="teaching-prep-button teaching-prep-button--primary"
              :disabled="(
                !selectedSemesterMaterialId
                || !catalog.semesterMappingPreflight?.model_available
                || catalog.saveState === 'saving'
              )"
              @click="generateSemesterMapping"
            >
              生成待确认建议
            </button>
          </div>
          <div
            v-if="catalog.semesterMappingPreflight"
            class="teaching-prep-page__notice"
            role="status"
          >
            <strong>
              本次 {{ catalog.semesterMappingPreflight.material_count }} 份资料，
              {{ catalog.semesterMappingPreflight.unit_count }} 页
            </strong>
            <span>
              {{
                catalog.semesterMappingPreflight.creates_initial_tree
                  ? '将建议初始章—节—课时树'
                  : `将映射到现有 ${catalog.semesterMappingPreflight.existing_lesson_count} 个课时`
              }}
              · 只调用模型一次 · 不自动重试
            </span>
            <span v-if="!catalog.semesterMappingPreflight.model_available">
              当前尚未配置真实学期整理模型；不会产生调用或费用。
            </span>
          </div>
          <article
            v-for="proposal in catalog.semesterMappingProposals"
            :key="proposal.id"
            class="teaching-prep-semester-mapping__proposal"
          >
            <div>
              <strong>
                {{
                  proposal.status === 'applied'
                    ? '已确认并写入'
                    : proposal.status === 'rejected'
                      ? '已拒绝'
                      : '待人工确认'
                }}
              </strong>
              <span>
                {{ proposal.payload.tree.length }} 章建议 ·
                {{ proposal.payload.mappings.length }} 条页码映射 ·
                {{ proposal.payload.uncertainties.length }} 项不确定
              </span>
            </div>
            <div class="teaching-prep-semester-mapping__review">
              <details v-if="proposal.payload.tree.length" open>
                <summary>查看拟建课时树</summary>
                <ol>
                  <li
                    v-for="chapter in proposal.payload.tree"
                    :key="chapter.key"
                  >
                    <strong>{{ chapter.title }}</strong>
                    <ul>
                      <li
                        v-for="section in chapter.sections"
                        :key="section.key"
                      >
                        {{ section.title }}
                        <span>
                          {{
                            section.lessons
                              .map(({ title }) => title)
                              .join('、')
                          }}
                        </span>
                      </li>
                    </ul>
                  </li>
                </ol>
              </details>
              <details v-if="proposal.payload.mappings.length" open>
                <summary>逐条查看课时—页码对应</summary>
                <ol>
                  <li
                    v-for="(mapping, index) in proposal.payload.mappings"
                    :key="`${mapping.material_record_id}-${mapping.lesson_ref}-${index}`"
                  >
                    <strong>
                      {{ proposalMaterialLabel(mapping.material_record_id) }}
                    </strong>
                    <span>
                      → {{ proposalLessonLabel(proposal, mapping.lesson_ref) }}
                      · 文件第 {{ mapping.start_unit }}
                      <template v-if="mapping.end_unit !== mapping.start_unit">
                        —{{ mapping.end_unit }}
                      </template>
                      页/张
                    </span>
                  </li>
                </ol>
              </details>
              <details v-if="proposal.payload.uncertainties.length">
                <summary>
                  查看 {{ proposal.payload.uncertainties.length }} 项不确定内容
                </summary>
                <ul>
                  <li
                    v-for="(item, index) in proposal.payload.uncertainties"
                    :key="`${index}-${item}`"
                  >
                    {{ item }}
                  </li>
                </ul>
              </details>
            </div>
            <button
              v-if="proposal.status === 'proposed'"
              type="button"
              :disabled="catalog.saveState === 'saving'"
              @click="catalog.applySemesterMapping(proposal)"
            >
              确认并写入
            </button>
          </article>
        </section>

        <form
          v-if="showLessonForm"
          class="teaching-prep-form teaching-prep-form--lesson"
          @submit.prevent="submitLesson"
        >
          <div class="teaching-prep-form__row">
            <label>
              类型
              <select v-model="lessonForm.nodeType">
                <option value="chapter">章</option>
                <option value="section">节</option>
                <option value="lesson">课时</option>
              </select>
            </label>
            <label v-if="lessonForm.nodeType !== 'chapter'">
              上级节点
              <select v-model="lessonForm.parentId" required>
                <option value="" disabled>请选择</option>
                <option
                  v-for="parent in parentOptions"
                  :key="parent.id"
                  :value="parent.id"
                >
                  {{ parent.title }}
                </option>
              </select>
            </label>
          </div>
          <label>
            名称
            <input
              v-model="lessonForm.title"
              required
              maxlength="160"
              :placeholder="lessonForm.nodeType === 'lesson' ? '例如：加减消元第一课时' : '输入章节名称'"
            >
          </label>
          <label v-if="lessonForm.nodeType === 'lesson'">
            预计时长（分钟）
            <input
              v-model.number="lessonForm.durationMinutes"
              type="number"
              min="1"
              max="300"
            >
          </label>
          <div class="teaching-prep-form__actions">
            <button
              type="submit"
              class="teaching-prep-button teaching-prep-button--primary"
              :disabled="catalog.saveState === 'saving'"
            >
              保存节点
            </button>
            <button
              type="button"
              class="teaching-prep-button teaching-prep-button--quiet"
              @click="showLessonForm = false"
            >
              取消
            </button>
          </div>
        </form>

        <p v-if="catalog.selectedCurriculumId === null" class="teaching-prep-empty">
          选择或新建教材版本后开始整理。
        </p>
        <p v-else-if="catalog.lessonNodes.length === 0" class="teaching-prep-empty">
          从当前需要备课的章开始，不必先录完整目录。
        </p>
        <ol v-else class="teaching-prep-tree" aria-label="个人课时树">
          <li
            v-for="node in catalog.lessonNodes"
            :key="node.id"
            class="teaching-prep-node"
            :class="{
              'is-inactive': !node.is_active,
              'is-selected': node.id === catalog.selectedLessonId,
            }"
            :style="{ '--node-depth': nodeDepths[node.id] ?? 0 }"
          >
            <span class="teaching-prep-node__type">
              {{ nodeTypeLabel(node.node_type) }}
            </span>
            <div class="teaching-prep-node__content">
              <template v-if="editingNodeId === node.id">
                <input
                  v-model="editingTitle"
                  maxlength="160"
                  aria-label="节点名称"
                  @keyup.enter="saveRename(node)"
                >
              </template>
              <template v-else>
                <button
                  v-if="node.node_type === 'lesson'"
                  type="button"
                  class="teaching-prep-node__select"
                  @click="catalog.selectLesson(node)"
                >
                  <strong>{{ node.title }}</strong>
                </button>
                <strong v-else>{{ node.title }}</strong>
                <span v-if="node.duration_minutes">{{ node.duration_minutes }} 分钟</span>
                <label
                  v-if="node.node_type === 'lesson' && catalog.selectedSemester"
                  class="teaching-prep-node__progress"
                >
                  进度
                  <select
                    :value="lessonProgressStatus(node.id)"
                    :disabled="catalog.saveState === 'saving'"
                    @change="changeLessonProgress(node.id, $event)"
                  >
                    <option value="not_started">未开始</option>
                    <option value="preparing">备课中</option>
                    <option value="ready">已备好</option>
                    <option value="taught">已上课</option>
                    <option value="skipped">本学期跳过</option>
                  </select>
                </label>
              </template>
            </div>
            <div class="teaching-prep-node__actions">
              <template v-if="editingNodeId === node.id">
                <button type="button" @click="saveRename(node)">保存</button>
                <button type="button" @click="editingNodeId = null">取消</button>
              </template>
              <template v-else>
                <button
                  type="button"
                  :disabled="!canMove(node, -1) || catalog.saveState === 'saving'"
                  aria-label="上移"
                  @click="catalog.moveLesson(node, -1)"
                >
                  ↑
                </button>
                <button
                  type="button"
                  :disabled="!canMove(node, 1) || catalog.saveState === 'saving'"
                  aria-label="下移"
                  @click="catalog.moveLesson(node, 1)"
                >
                  ↓
                </button>
                <button type="button" @click="startRename(node)">改名</button>
                <button type="button" @click="toggleActive(node)">
                  {{ node.is_active ? '停用' : '恢复' }}
                </button>
              </template>
            </div>
          </li>
        </ol>
      </main>

      <aside class="teaching-prep-panel teaching-prep-panel--materials">
        <div class="teaching-prep-panel__header">
          <div>
            <p>第三步</p>
            <h2>资料库</h2>
          </div>
          <div class="teaching-prep-material-import">
            <span class="teaching-prep-panel__count">
              {{ catalog.materials.length }} 个版本
            </span>
            <label
              class="teaching-prep-button teaching-prep-button--quiet"
              :class="{ 'is-disabled': catalog.saveState === 'saving' }"
            >
              导入副本
              <input
                class="teaching-prep-file-input"
                type="file"
                accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"
                :disabled="catalog.saveState === 'saving'"
                @change="importMaterialCopy"
              >
            </label>
            <label v-if="catalog.selectedSemester">
              资料角色
              <select v-model="importMaterialRole">
                <option value="textbook">教材</option>
                <option value="reference_ppt">参考课件</option>
                <option value="exercise_workbook">普通教辅</option>
                <option value="homework_workbook">日常作业教辅</option>
                <option value="answer_book">答案册</option>
                <option value="supplement">补充资料</option>
              </select>
            </label>
          </div>
        </div>
        <div class="teaching-prep-page__notice" role="status">
          <strong>{{ catalog.selectedLesson?.title ?? '尚未选择课时' }}</strong>
          <span>选择 PDF、PPTX 或图片后，会明确导入一份受控副本；原文件保持不变。</span>
        </div>
        <p v-if="catalog.materials.length === 0" class="teaching-prep-empty">
          尚未登记教材、课件或教辅版本。
        </p>
        <template v-else>
          <article
            v-for="material in catalog.materials"
            :key="material.id"
            class="teaching-prep-material"
            :class="{ 'is-selected': material.id === catalog.selectedMaterialId }"
          >
            <button type="button" @click="openMaterial(material)">
              <strong>{{ material.display_name }}</strong>
              <span>{{ material.safe_filename }}</span>
            </button>
            <div class="teaching-prep-material__status">
              <span :class="`is-${material.availability}`">
                {{ materialStatusLabel(material.availability) }}
              </span>
              <label
                v-if="material.availability !== 'available'"
                class="teaching-prep-material__relocate"
                :class="{ 'is-disabled': catalog.saveState === 'saving' }"
              >
                重新选择文件
                <input
                  class="teaching-prep-file-input"
                  type="file"
                  accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"
                  :disabled="catalog.saveState === 'saving'"
                  @change="relocateMaterialCopy(material, $event)"
                >
              </label>
            </div>
            <div
              v-if="catalog.selectedSemester"
              class="teaching-prep-material__semester"
            >
              <template
                v-if="semesterMaterialBySource[material.source_id]"
              >
                <label>
                  本学期角色
                  <select
                    :value="semesterMaterialBySource[material.source_id]?.material_role"
                    :disabled="catalog.saveState === 'saving'"
                    @change="changeSemesterMaterialRoleBySource(
                      material.source_id,
                      $event,
                    )"
                  >
                    <option value="textbook">教材</option>
                    <option value="reference_ppt">参考课件</option>
                    <option value="exercise_workbook">普通教辅</option>
                    <option value="homework_workbook">日常作业教辅</option>
                    <option value="answer_book">答案册</option>
                    <option value="supplement">补充资料</option>
                  </select>
                </label>
                <span>
                  {{
                    semesterParseStatusLabel(
                      semesterMaterialBySource[material.source_id],
                    )
                  }}
                </span>
                <span>
                  {{
                    semesterMappingStatusLabel(
                      semesterMaterialBySource[material.source_id]?.mapping_status,
                    )
                  }}
                </span>
              </template>
              <template v-else>
                <select
                  v-model="materialRoleDrafts[material.id]"
                  aria-label="加入学期时的资料角色"
                >
                  <option value="textbook">教材</option>
                  <option value="reference_ppt">参考课件</option>
                  <option value="exercise_workbook">普通教辅</option>
                  <option value="homework_workbook">日常作业教辅</option>
                  <option value="answer_book">答案册</option>
                  <option value="supplement">补充资料</option>
                </select>
                <button
                  type="button"
                  :disabled="catalog.saveState === 'saving'"
                  @click="attachMaterialToSemester(material)"
                >
                  加入本学期
                </button>
              </template>
            </div>
          </article>
        </template>

        <section
          v-if="catalog.selectedMaterialId !== null"
          class="teaching-prep-units"
          aria-labelledby="material-units-title"
        >
          <div class="teaching-prep-units__header">
            <h3 id="material-units-title">资料页面</h3>
            <span>{{ catalog.materialUnits.length }} 页</span>
          </div>
          <p
            v-if="catalog.loadState === 'loading'"
            class="teaching-prep-empty"
          >
            正在生成本地预览…
          </p>
          <div v-else class="teaching-prep-unit-grid">
            <article
              v-for="unit in catalog.materialUnits"
              :key="unit.id"
              class="teaching-prep-unit"
              :class="{ 'is-selected': selectedUnitIds.includes(unit.id) }"
            >
              <button
                type="button"
                class="teaching-prep-unit__preview"
                :aria-pressed="selectedUnitIds.includes(unit.id)"
                @click="toggleUnit(unit)"
              >
                <img :src="unit.preview_url" :alt="`第 ${unit.unit_index} 页预览`">
                <span>第 {{ unit.unit_index }} 页</span>
              </button>
              <div class="teaching-prep-unit__meta">
                <template v-if="editingUnitId === unit.id">
                  <input
                    v-model="unitTitle"
                    maxlength="160"
                    aria-label="页面标签"
                  >
                  <textarea
                    v-model="unitManualText"
                    maxlength="4000"
                    rows="3"
                    aria-label="人工识别文字"
                  />
                  <div>
                    <button type="button" @click="saveUnitEdit(unit)">保存</button>
                    <button type="button" @click="editingUnitId = null">取消</button>
                  </div>
                </template>
                <template v-else>
                  <strong>{{ unit.title ?? '未标注页面' }}</strong>
                  <span v-if="unit.formula_review_required">公式文字待核对</span>
                  <span v-else-if="unit.text_status === 'empty'">无内嵌文字，可人工标注</span>
                  <div class="teaching-prep-unit__actions">
                    <button type="button" @click="startUnitEdit(unit)">修正标签</button>
                    <button
                      type="button"
                      :disabled="!catalog.selectedLessonId || !unitIsConfirmed(unit)"
                      @click="beginCrop(unit, 'question')"
                    >
                      框题目
                    </button>
                    <button
                      type="button"
                      :disabled="!catalog.selectedLessonId || !unitIsConfirmed(unit)"
                      @click="beginCrop(unit, 'answer')"
                    >
                      框答案
                    </button>
                  </div>
                </template>
              </div>
            </article>
          </div>
        </section>

        <section
          v-if="activeCropUnit"
          class="teaching-prep-crop-editor"
          aria-labelledby="exercise-crop-title"
        >
          <div class="teaching-prep-units__header">
            <div>
              <h3 id="exercise-crop-title">
                {{ cropRole === 'question' ? '框选题目区域' : '框选答案区域' }}
              </h3>
              <span>第 {{ activeCropUnit.unit_index }} 页 · 可拖动框选多个区域</span>
            </div>
            <button type="button" @click="cropUnitId = null">完成本页</button>
          </div>
          <div
            class="teaching-prep-crop-canvas"
            @pointerdown.prevent="startCrop"
            @pointermove.prevent="moveCrop"
            @pointerup.prevent="finishCrop"
          >
            <img
              :src="activeCropUnit.preview_url"
              :alt="`第 ${activeCropUnit.unit_index} 页原图`"
              draggable="false"
            >
            <span
              v-for="region in activeCropRegions"
              :key="region.key"
              class="teaching-prep-crop-box is-saved"
              :style="cropStyle(region.crop)"
            />
            <span
              v-if="cropDraft"
              class="teaching-prep-crop-box"
              :style="cropStyle(cropDraft)"
            />
          </div>
          <p>框选结果只引用原图，不用识别文字替代数学公式、图形或复杂排版。</p>
        </section>

        <form
          v-if="catalog.selectedLessonId && selectedRanges.length"
          class="teaching-prep-form"
          @submit.prevent="confirmRanges"
        >
          <strong>
            已选择 {{ selectedUnitIds.length }} 页，共 {{ selectedRanges.length }} 段
          </strong>
          <label>
            用途
            <select v-model="linkPurpose">
              <option value="textbook">教材</option>
              <option value="reference_ppt">参考课件</option>
              <option value="exercise">课堂练习</option>
              <option value="answer">答案</option>
              <option value="supplement">补充资料</option>
            </select>
          </label>
          <label>
            教师备注（可选）
            <input v-model="linkNote" maxlength="500">
          </label>
          <button
            type="submit"
            class="teaching-prep-button teaching-prep-button--primary"
            :disabled="catalog.saveState === 'saving'"
          >
            确认关联到当前课时
          </button>
        </form>

        <section
          v-if="catalog.selectedLessonId"
          class="teaching-prep-links"
          aria-labelledby="material-links-title"
        >
          <h3 id="material-links-title">已确认范围</h3>
          <p v-if="catalog.materialLinks.length === 0" class="teaching-prep-empty">
            当前课时尚未关联资料范围。
          </p>
          <template v-else>
            <article
              v-for="link in catalog.materialLinks"
              :key="link.id"
              :class="{ 'is-inactive': !link.is_active }"
            >
              <div>
                <strong>{{ link.material_name }}</strong>
                <span>第 {{ link.start_unit }}—{{ link.end_unit }} 页</span>
              </div>
              <button
                v-if="link.is_active"
                type="button"
                @click="catalog.deactivateMaterialLink(link)"
              >
                移除
              </button>
              <span v-else>已移除</span>
            </article>
          </template>
        </section>

        <section
          v-if="catalog.selectedLessonId"
          class="teaching-prep-exercise-editor"
          aria-labelledby="exercise-editor-title"
        >
          <div class="teaching-prep-units__header">
            <div>
              <h3 id="exercise-editor-title">
                {{ editingExercise ? '修正候选题' : '建立候选题' }}
              </h3>
              <span>题目和答案可分别来自多个页面</span>
            </div>
            <button
              v-if="editingExercise || questionRegions.length || answerRegions.length"
              type="button"
              @click="resetExerciseEditor"
            >
              放弃本次编辑
            </button>
          </div>

          <div class="teaching-prep-region-summary">
            <div>
              <strong>题目区域（{{ questionRegions.length }}）</strong>
              <p v-if="questionRegions.length === 0">请在已确认的教辅页面点击“框题目”。</p>
              <ol v-else>
                <li v-for="region in questionRegions" :key="region.key">
                  <span>{{ region.material_name }} · 第 {{ region.unit_index }} 页</span>
                  <button type="button" @click="removeRegion('question', region.key)">移除</button>
                </li>
              </ol>
            </div>
            <div>
              <strong>答案区域（{{ answerRegions.length }}）</strong>
              <p v-if="answerRegions.length === 0">可以暂时没有答案，系统会明确标记。</p>
              <ol v-else>
                <li v-for="region in answerRegions" :key="region.key">
                  <span>{{ region.material_name }} · 第 {{ region.unit_index }} 页</span>
                  <button type="button" @click="removeRegion('answer', region.key)">移除</button>
                </li>
              </ol>
            </div>
          </div>

          <form class="teaching-prep-form" @submit.prevent="saveExercise">
            <div class="teaching-prep-form__row">
              <label>
                题号（可选）
                <input v-model="exerciseForm.questionNumber" maxlength="80">
              </label>
              <label>
                预计课堂时间
                <input
                  v-model.number="exerciseForm.estimatedMinutes"
                  type="number"
                  min="1"
                  max="60"
                >
              </label>
            </div>
            <label>
              题目识别线索（可选）
              <input
                v-model="exerciseForm.contentLabel"
                maxlength="500"
                placeholder="帮助人工辨认和发现可能重复，不代替原图"
              >
            </label>
            <div class="teaching-prep-form__row">
              <label>
                难度
                <select v-model="exerciseForm.difficulty">
                  <option value="unrated">未评定</option>
                  <option value="easy">基础</option>
                  <option value="medium">中等</option>
                  <option value="hard">挑战</option>
                </select>
              </label>
              <label>
                课堂用途
                <select v-model="exerciseForm.classroomUse">
                  <option value="introduction">导入</option>
                  <option value="example">例题</option>
                  <option value="guided_practice">引导练习</option>
                  <option value="independent_practice">独立练习</option>
                  <option value="diagnostic">诊断</option>
                  <option value="challenge">挑战</option>
                  <option value="summary">总结</option>
                </select>
              </label>
            </div>
            <label>
              教学重点
              <input v-model="exerciseForm.teachingFocus" maxlength="500">
            </label>
            <label>
              教师备注
              <textarea v-model="exerciseForm.teacherNote" maxlength="1000" rows="2" />
            </label>
            <label>
              课堂安排
              <select v-model="exerciseForm.selectionStatus">
                <option value="classroom_candidate">课堂候选</option>
                <option value="backup">备用题</option>
                <option value="excluded">排除</option>
              </select>
            </label>
            <button
              type="submit"
              class="teaching-prep-button teaching-prep-button--primary"
              :disabled="questionRegions.length === 0 || catalog.saveState === 'saving'"
            >
              {{ editingExercise ? '保存修正' : '保存候选题' }}
            </button>
          </form>
        </section>

        <section
          v-if="catalog.selectedLessonId"
          class="teaching-prep-exercises"
          aria-labelledby="exercise-list-title"
        >
          <div class="teaching-prep-units__header">
            <h3 id="exercise-list-title">本节候选题</h3>
            <span>{{ catalog.exerciseCandidates.length }} 道</span>
          </div>
          <p
            v-if="catalog.exerciseCandidates.length === 0"
            class="teaching-prep-empty"
          >
            尚未框选候选题；整个流程可以离线完成。
          </p>
          <article
            v-for="candidate in catalog.exerciseCandidates"
            :key="candidate.id"
            :class="{ 'is-inactive': !candidate.is_active }"
          >
            <div class="teaching-prep-exercise__header">
              <div>
                <strong>
                  {{ candidate.question_number ? `第 ${candidate.question_number} 题` : '未编号题目' }}
                </strong>
                <span>{{ candidate.content_label ?? '以原图为准' }}</span>
              </div>
              <span :class="`is-${candidate.answer_status}`">
                {{ answerStatusLabel(candidate.answer_status) }}
              </span>
            </div>
            <div class="teaching-prep-exercise__previews">
              <figure
                v-for="region in candidate.question_regions"
                :key="region.id"
              >
                <img :src="region.preview_url" :alt="`题目区域 ${region.sequence}`">
                <figcaption>{{ region.material_name }} · 第 {{ region.unit_index }} 页</figcaption>
              </figure>
              <figure
                v-for="region in candidate.answer_regions"
                :key="region.id"
                class="is-answer"
              >
                <img :src="region.preview_url" :alt="`答案区域 ${region.sequence}`">
                <figcaption>答案 · {{ region.material_name }} · 第 {{ region.unit_index }} 页</figcaption>
              </figure>
            </div>
            <div
              v-if="candidate.duplicate_suggestions.length"
              class="teaching-prep-duplicate-note"
            >
              <strong>可能重复，仅供教师判断</strong>
              <span
                v-for="suggestion in candidate.duplicate_suggestions"
                :key="suggestion.candidate_id"
              >
                {{ suggestion.question_number ? `第 ${suggestion.question_number} 题` : '未编号题' }}：
                {{ duplicateBasisLabel(suggestion.basis) }}
              </span>
            </div>
            <div class="teaching-prep-exercise__actions">
              <label>
                课堂安排
                <select
                  :value="candidate.selection_status"
                  :disabled="catalog.saveState === 'saving'"
                  @change="onSelectionChange(candidate, $event)"
                >
                  <option value="classroom_candidate">课堂候选</option>
                  <option value="backup">备用题</option>
                  <option value="excluded">排除</option>
                </select>
              </label>
              <button type="button" @click="startExerciseEdit(candidate)">修正区域与标签</button>
              <button
                v-if="candidate.answer_regions.length && candidate.answer_status !== 'teacher_verified'"
                type="button"
                @click="changeAnswerStatus(candidate, 'teacher_verified')"
              >
                确认答案匹配
              </button>
              <button
                v-if="candidate.answer_regions.length && candidate.answer_status !== 'rejected'"
                type="button"
                @click="changeAnswerStatus(candidate, 'rejected')"
              >
                标记不匹配
              </button>
              <button
                v-if="candidate.is_active"
                type="button"
                @click="catalog.updateExerciseCandidate(candidate, { is_active: false })"
              >
                停用
              </button>
            </div>
          </article>
          <p class="teaching-prep-exercises__rule">
            只有“教师已核对”的答案才能进入教师答案页；修改任何题目或答案区域后，旧确认会自动失效。
          </p>
        </section>

        <section
          v-if="catalog.selectedLessonId"
          class="teaching-prep-resource-packs"
          aria-labelledby="resource-pack-title"
        >
          <div class="teaching-prep-units__header">
            <div>
              <h3 id="resource-pack-title">冻结本节资源包</h3>
              <span>冻结后旧版本不再改写，来源变化会生成新版本</span>
            </div>
            <span
              v-if="catalog.resourcePackStatus?.local_sources_changed"
              class="teaching-prep-resource-packs__changed"
            >
              来源已有变化
            </span>
          </div>

          <div class="teaching-prep-page__notice">
            <strong>学情证据只在你明确选择后读取</strong>
            <span>
              只保存班级汇总，不保存姓名、学号、答卷图片或个人错误详情；没有考情也可以继续冻结。
            </span>
          </div>

          <form class="teaching-prep-form" @submit.prevent="freezeResourcePack">
            <fieldset class="teaching-prep-preferences">
              <legend>我的课件改编偏好</legend>
              <p>
                当前勾选会冻结进本次备课；模型只把它作为结构化倾向，
                页码字号、数量上限和不覆盖原件仍由本地程序强制检查。
              </p>
              <div class="teaching-prep-preferences__grid">
                <label>
                  <input
                    v-model="preparationPreferences.label_textbook_pages"
                    type="checkbox"
                  >
                  <span>
                    <strong>标注教材页码</strong>
                    在安全空白处添加“教材 P××”，固定 28 号
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.trim_excess_practice"
                    type="checkbox"
                  >
                  <span>
                    <strong>精简过多课堂练习</strong>
                    重点检查课件后半部分
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.preserve_teaching_examples"
                    type="checkbox"
                  >
                  <span>
                    <strong>保留讲授过程中的例题</strong>
                    不因练习精简误删讲解主线
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.prefer_short_practice"
                    type="checkbox"
                  >
                  <span>
                    <strong>优先保留短题</strong>
                    题干较短、适合当堂完成的题目优先
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.supplement_from_references"
                    type="checkbox"
                  >
                  <span>
                    <strong>从参考资料补少量重点题</strong>
                    只选择原 PPT 没有且值得进入作业设计的题
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.supplement_as_source_image"
                    type="checkbox"
                    :disabled="!preparationPreferences.supplement_from_references"
                  >
                  <span>
                    <strong>补题使用原资料截图</strong>
                    新开一页插入清晰裁图，不生成 AI 风格题图
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.prioritize_homework_workbook"
                    type="checkbox"
                  >
                  <span>
                    <strong>重点参考作业教辅</strong>
                    用于判断作业题型和难度倾向
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.avoid_direct_homework_copy"
                    type="checkbox"
                  >
                  <span>
                    <strong>尽量不直接照搬作业教辅原题</strong>
                    优先选择同类重点或交给教师确认
                  </span>
                </label>
                <label>
                  <input
                    v-model="preparationPreferences.avoid_ppt_duplicates"
                    type="checkbox"
                  >
                  <span>
                    <strong>避免与原 PPT 重复</strong>
                    重复或高度相似的补题不自动进入课件
                  </span>
                </label>
              </div>
              <div class="teaching-prep-form__row">
                <label>
                  练习精简程度
                  <select
                    v-model="preparationPreferences.practice_trim_level"
                    :disabled="!preparationPreferences.trim_excess_practice"
                  >
                    <option value="light">少量精简</option>
                    <option value="moderate">适度精简</option>
                    <option value="strong">大幅精简</option>
                  </select>
                </label>
                <label>
                  每节补题上限
                  <select
                    v-model.number="preparationPreferences.supplement_question_limit"
                    :disabled="!preparationPreferences.supplement_from_references"
                  >
                    <option :value="0">0 道</option>
                    <option :value="1">1 道</option>
                    <option :value="2">2 道</option>
                    <option :value="3">最多 3 道</option>
                  </select>
                </label>
              </div>
              <div class="teaching-prep-preferences__actions">
                <button
                  type="button"
                  :disabled="catalog.saveState === 'saving'"
                  @click="savePersonalPreparationPreferences"
                >
                  保存为个人默认
                </button>
                <button type="button" @click="resetPreparationPreferences">
                  恢复个人默认
                </button>
                <span>{{ preparationPreferenceSummary(preparationPreferences) }}</span>
              </div>
            </fieldset>

            <div class="teaching-prep-form__row">
              <label>
                班级（选择历史考试时必填）
                <input
                  v-model="resourcePackForm.className"
                  maxlength="120"
                  placeholder="例如：七年级一班"
                >
              </label>
              <label>
                课型
                <select disabled>
                  <option>新授课</option>
                </select>
              </label>
            </div>

            <label>
              本班整体情况（可选）
              <textarea
                v-model="resourcePackForm.teacherContext"
                maxlength="2000"
                rows="3"
                placeholder="只填写班级整体情况，不要填写学生姓名或个人信息"
              />
            </label>

            <div
              v-if="referencePptLinks.length"
              class="teaching-prep-pack-intents"
            >
              <strong>参考课件范围意图</strong>
              <label v-for="link in referencePptLinks" :key="link.id">
                <span>
                  {{ link.material_name }} · 第 {{ link.start_unit }}—{{ link.end_unit }} 页
                </span>
                <select v-model="referencePptIntents[link.id]">
                  <option value="keep">保留</option>
                  <option value="candidate_delete">候选删除</option>
                </select>
              </label>
            </div>

            <label>
              精确知识点范围（可选）
              <input
                v-model="resourcePackForm.knowledgeScope"
                placeholder="例如：一元一次方程；多个知识点用逗号分隔"
              >
            </label>

            <div class="teaching-prep-assessment-picker">
              <div>
                <strong>现有题库证据（可选）</strong>
                <button
                  type="button"
                  :disabled="catalog.loadState === 'loading'"
                  @click="catalog.loadAvailableQuestions"
                >
                  {{ catalog.availableQuestions.length ? '重新读取题目清单' : '读取题目清单' }}
                </button>
              </div>
              <p v-if="catalog.availableQuestions.length === 0">
                尚未读取。点击后只显示题目摘要、题型和知识点，不显示文件路径。
              </p>
              <label
                v-for="question in catalog.availableQuestions"
                :key="question.question_id"
              >
                <input
                  type="checkbox"
                  :checked="selectedQuestionIds.includes(question.question_id)"
                  @change="toggleQuestion(question)"
                >
                <span>
                  <strong>
                    第 {{ question.question_number }} 题 ·
                    {{ question.question_type ?? '未标题型' }}
                  </strong>
                  {{ question.text_excerpt }}
                  <small>
                    {{ question.knowledge_points.join('、') || '缺少精确知识点标签' }}
                  </small>
                </span>
              </label>
            </div>

            <div class="teaching-prep-assessment-picker">
              <div>
                <strong>历史考试证据（可选）</strong>
                <button
                  type="button"
                  :disabled="catalog.loadState === 'loading'"
                  @click="catalog.loadAvailableAssessments"
                >
                  {{ catalog.availableAssessments.length ? '重新读取考试清单' : '读取考试清单' }}
                </button>
              </div>
              <p v-if="catalog.availableAssessments.length === 0">
                尚未读取。点击后只显示考试名称、班级和人数。
              </p>
              <label
                v-for="assessment in catalog.availableAssessments"
                v-else
                :key="assessment.assessment_id"
                :class="{ 'is-mismatch': !assessmentMatchesClass(assessment) }"
              >
                <input
                  type="checkbox"
                  :checked="selectedAssessmentIds.includes(assessment.assessment_id)"
                  :disabled="!assessmentMatchesClass(assessment)"
                  @change="toggleAssessment(assessment)"
                >
                <span>
                  <strong>{{ assessment.title }}</strong>
                  {{
                    assessment.classes.map(
                      (item) => `${item.class_name}（${item.student_count}人）`,
                    ).join('、') || '暂无班级结果'
                  }}
                </span>
              </label>
            </div>

            <p v-if="packFormError" class="teaching-prep-form__error">
              {{ packFormError }}
            </p>
            <button
              type="submit"
              class="teaching-prep-button teaching-prep-button--primary"
              :disabled="catalog.saveState === 'saving'"
            >
              冻结为新资源包版本
            </button>
          </form>

          <div class="teaching-prep-pack-list">
            <h3>已冻结版本</h3>
            <p v-if="catalog.resourcePacks.length === 0" class="teaching-prep-empty">
              当前课时尚无资源包。
            </p>
            <article
              v-for="pack in catalog.resourcePacks"
              :key="pack.id"
              :class="{ 'is-selected': catalog.selectedResourcePackId === pack.id }"
            >
              <div>
                <strong>版本 {{ pack.version_number }}</strong>
                <span>
                  {{ packMissingCount(pack) }} 项缺失或待确认 ·
                  {{ new Date(pack.created_at).toLocaleString() }}
                </span>
                <small
                  v-if="pack.payload.preparation_preferences"
                >
                  {{ packPreparationPreferenceSummary(pack) }}
                </small>
              </div>
              <div class="teaching-prep-pack-list__actions">
                <button type="button" @click="catalog.selectResourcePack(pack)">
                  {{ catalog.selectedResourcePackId === pack.id ? '正在使用' : '用于草稿' }}
                </button>
                <a
                  :href="`/api/teaching-prep/resource-packs/${pack.id}/manifest`"
                  download
                >
                  导出安全清单
                </a>
              </div>
            </article>
          </div>

          <section
            v-if="catalog.selectedResourcePackId"
            class="teaching-prep-drafts"
            aria-labelledby="lesson-draft-title"
          >
            <div class="teaching-prep-units__header">
              <div>
                <h3 id="lesson-draft-title">重难点与课堂流程草稿</h3>
                <span>先核对引用范围，再生成；所有结论仍需教师确认</span>
              </div>
              <button
                v-if="!catalog.lessonDraftPreflight"
                type="button"
                @click="catalog.prepareLessonDraft('local_template')"
              >
                查看生成范围
              </button>
            </div>

            <div
              v-if="catalog.lessonDraftPreflight"
              class="teaching-prep-draft-preflight"
            >
              <strong>本地模板，不调用模型、不产生费用</strong>
              <span>
                将引用 {{ catalog.lessonDraftPreflight.references.length }} 项已冻结来源；
                资源包还有 {{ catalog.lessonDraftPreflight.missing_and_uncertain_count }} 项缺失或待确认。
              </span>
              <span>
                不会读取资源包外资料，也不会生成 WPS 修改指令。
              </span>
              <span>
                本次偏好：
                {{
                  preparationPreferenceSummary(
                    catalog.lessonDraftPreflight.preparation_preferences,
                  )
                }}
              </span>
              <button
                type="button"
                class="teaching-prep-button teaching-prep-button--primary"
                :disabled="catalog.saveState === 'saving'"
                @click="catalog.generateLessonDraft('local_template')"
              >
                确认并生成本地草稿
              </button>
            </div>

            <p v-if="catalog.lessonDrafts.length === 0" class="teaching-prep-empty">
              当前资源包还没有课堂草稿。
            </p>
            <article
              v-for="draft in catalog.lessonDrafts"
              :key="draft.id"
              class="teaching-prep-draft-card"
            >
              <header>
                <div>
                  <strong>
                    草稿版本 {{ draft.version_number }}
                    · {{ draft.status === 'confirmed' ? '教师已确认' : '待审核' }}
                  </strong>
                  <span>
                    {{ draft.source_kind === 'local_template' ? '本地模板' : draft.source_kind === 'teacher' ? '教师修订' : draft.model_label }}
                  </span>
                </div>
                <div class="teaching-prep-draft-card__actions">
                  <button type="button" @click="startDraftEdit(draft)">
                    审核并编辑
                  </button>
                  <button
                    v-if="draft.status === 'confirmed'"
                    type="button"
                    :disabled="catalog.saveState === 'saving'"
                    @click="catalog.createSlidePlan(draft)"
                  >
                    生成逐页改编计划
                  </button>
                </div>
              </header>

              <div
                class="teaching-prep-capacity"
                :class="{ 'is-over': !draft.capacity.within_capacity }"
              >
                <strong>
                  计划 {{ draft.capacity.planned_minutes }} 分钟 /
                  课时 {{ draft.capacity.lesson_minutes }} 分钟
                </strong>
                <span>
                  环节 {{ draft.capacity.flow_minutes }} 分钟 ·
                  课堂题 {{ draft.capacity.exercise_minutes }} 分钟 ·
                  机动 {{ draft.capacity.buffer_minutes }} 分钟
                </span>
                <span v-if="draft.capacity.overrun_minutes">
                  超出 {{ draft.capacity.overrun_minutes }} 分钟，请按下方题目取舍缩减。
                </span>
              </div>

              <template v-if="editingDraft?.id === draft.id && draftEdit">
                <div class="teaching-prep-draft-editor">
                  <fieldset>
                    <legend>知识目标</legend>
                    <label
                      v-for="(item, index) in draftEdit.knowledge_objectives"
                      :key="`objective-${index}`"
                    >
                      <textarea v-model="item.text" maxlength="1000" rows="2" />
                      <small>{{ item.citations.map(citationLabel).join('；') }}</small>
                    </label>
                  </fieldset>

                  <fieldset>
                    <legend>重难点</legend>
                    <label
                      v-for="(item, index) in draftEdit.focus_points"
                      :key="`focus-${index}`"
                    >
                      <input v-model="item.title" maxlength="300">
                      <textarea v-model="item.rationale" maxlength="1000" rows="2" />
                      <small>{{ item.citations.map(citationLabel).join('；') }}</small>
                    </label>
                  </fieldset>

                  <fieldset>
                    <legend>预计困难</legend>
                    <label
                      v-for="(item, index) in draftEdit.anticipated_difficulties"
                      :key="`difficulty-${index}`"
                    >
                      <textarea v-model="item.text" maxlength="1000" rows="2" />
                      <small>{{ item.citations.map(citationLabel).join('；') }}</small>
                    </label>
                  </fieldset>

                  <fieldset>
                    <legend>课堂流程</legend>
                    <div
                      v-for="item in draftEdit.lesson_flow"
                      :key="item.phase"
                      class="teaching-prep-flow-edit"
                    >
                      <strong>{{ phaseLabel(item.phase) }}</strong>
                      <input v-model="item.title" maxlength="200">
                      <textarea v-model="item.purpose" maxlength="800" rows="2" />
                      <label>
                        建议分钟
                        <input
                          v-model.number="item.suggested_minutes"
                          type="number"
                          min="0"
                          max="120"
                        >
                      </label>
                      <small>{{ item.citations.map(citationLabel).join('；') }}</small>
                    </div>
                  </fieldset>

                  <fieldset>
                    <legend>课堂题取舍</legend>
                    <div
                      v-for="item in draftEdit.exercise_recommendations"
                      :key="item.source_ref"
                      class="teaching-prep-flow-edit"
                    >
                      <strong>{{ item.title }}</strong>
                      <select v-model="item.action">
                        <option value="include">进入课堂</option>
                        <option value="backup">作为备用</option>
                        <option value="move_after_class">移到课后</option>
                        <option value="exclude">不使用</option>
                        <option value="replace_shorter">换成更短题</option>
                      </select>
                      <textarea v-model="item.reason" maxlength="1000" rows="2" />
                      <label>
                        预计分钟
                        <input
                          v-model.number="item.estimated_minutes"
                          type="number"
                          min="1"
                          max="60"
                        >
                      </label>
                      <small>
                        {{ recommendationLabel(item.action) }} ·
                        {{ item.citations.map(citationLabel).join('；') }}
                      </small>
                    </div>
                  </fieldset>

                  <fieldset>
                    <legend>不确定项</legend>
                    <label
                      v-for="(_item, index) in draftEdit.uncertainties"
                      :key="`uncertain-${index}`"
                    >
                      <input v-model="draftEdit.uncertainties[index]" maxlength="500">
                    </label>
                  </fieldset>

                  <div class="teaching-prep-draft-editor__actions">
                    <button type="button" @click="editingDraft = null; draftEdit = null">
                      取消编辑
                    </button>
                    <button
                      type="button"
                      :disabled="catalog.saveState === 'saving'"
                      @click="saveDraftEdit(false)"
                    >
                      保存新草稿版本
                    </button>
                    <button
                      type="button"
                      class="teaching-prep-button teaching-prep-button--primary"
                      :disabled="catalog.saveState === 'saving'"
                      @click="saveDraftEdit(true)"
                    >
                      确认此版本
                    </button>
                  </div>
                </div>
              </template>
              <template v-else>
                <div class="teaching-prep-draft-summary">
                  <section>
                    <strong>重难点</strong>
                    <p v-for="item in draft.payload.focus_points" :key="item.title">
                      {{ item.title }}：{{ item.rationale }}
                    </p>
                  </section>
                  <section>
                    <strong>课堂流程</strong>
                    <ol>
                      <li v-for="item in draft.payload.lesson_flow" :key="item.phase">
                        {{ phaseLabel(item.phase) }} · {{ item.suggested_minutes }} 分钟
                      </li>
                    </ol>
                  </section>
                  <section>
                    <strong>课堂题</strong>
                    <p
                      v-for="item in draft.payload.exercise_recommendations"
                      :key="item.source_ref"
                    >
                      {{ recommendationLabel(item.action) }}：{{ item.title }}
                    </p>
                  </section>
                </div>
              </template>
            </article>

            <section
              v-if="catalog.selectedLessonDraftId"
              class="teaching-prep-slide-plans"
              aria-labelledby="slide-plan-title"
            >
              <div class="teaching-prep-units__header">
                <div>
                  <h3 id="slide-plan-title">逐页课件改编计划</h3>
                  <span>这里只审核计划，不会打开或修改 WPS 文件</span>
                </div>
              </div>

              <p v-if="catalog.slidePlans.length === 0" class="teaching-prep-empty">
                选择“教师已确认”的课堂草稿后，可以生成第一版逐页计划。
              </p>

              <div v-else class="teaching-prep-plan-version-list">
                <button
                  v-for="plan in catalog.slidePlans"
                  :key="plan.id"
                  type="button"
                  :class="{ 'is-selected': catalog.selectedSlidePlanId === plan.id }"
                  @click="startSlidePlanReview(plan)"
                >
                  <strong>计划版本 {{ plan.version_number }}</strong>
                  <span>{{ planStatusLabel(plan.status) }}</span>
                </button>
              </div>

              <article
                v-if="selectedSlidePlan"
                class="teaching-prep-slide-plan"
              >
                <div
                  v-if="selectedSlidePlan.status === 'invalidated'"
                  class="teaching-prep-page__notice is-danger"
                  role="alert"
                >
                  <strong>来源已经变化，这份计划已失效</strong>
                  <span>请重新冻结资源包并确认新草稿；旧审批历史会继续保留。</span>
                </div>

                <div class="teaching-prep-plan-toolbar">
                  <div>
                    <strong>
                      执行前预计：
                      {{ catalog.slidePlanPreview?.before_slide_count ?? 0 }} 页
                      →
                      {{ catalog.slidePlanPreview?.after_slide_count ?? 0 }} 页
                    </strong>
                    <span>
                      {{ selectedSlidePlan.payload.operations.length }} 项建议 ·
                      {{ selectedSlidePlan.payload.approval_history.length }} 条审批记录
                    </span>
                  </div>
                  <div>
                    <button
                      v-if="hasBatchDeletions(selectedSlidePlan)"
                      type="button"
                      :disabled="catalog.saveState === 'saving' || selectedSlidePlan.status === 'invalidated'"
                      @click="saveSlidePlanReview(true)"
                    >
                      批量批准低风险删除
                    </button>
                    <a
                      :href="`/api/teaching-prep/slide-plans/${selectedSlidePlan.id}/checklist`"
                      download
                    >
                      导出人工修改清单
                    </a>
                  </div>
                </div>

                <div
                  v-if="selectedSlidePlan.payload.unsupported_objects.length"
                  class="teaching-prep-plan-unsupported"
                >
                  <strong>以下对象不会自动修改</strong>
                  <span
                    v-for="(item, index) in selectedSlidePlan.payload.unsupported_objects"
                    :key="`${displayValue(item, 'slide_signature')}-${index}`"
                  >
                    生成时第 {{ displayValue(item, 'generated_page_number', '?') }} 页 ·
                    {{ displayValue(item, 'label', '受保护对象') }} ·
                    {{ displayValue(item, 'policy', '只读保留') }}
                  </span>
                </div>

                <div
                  v-if="catalog.slidePlanPreview"
                  class="teaching-prep-plan-preview"
                >
                  <section>
                    <strong>原计划页</strong>
                    <div>
                      <figure
                        v-for="slide in catalog.slidePlanPreview.before"
                        :key="displayValue(slide, 'stable_signature')"
                      >
                        <img
                          v-if="displayValue(slide, 'preview_url')"
                          :src="displayValue(slide, 'preview_url')"
                          alt=""
                        >
                        <figcaption>
                          第 {{ displayValue(slide, 'original_index', '?') }} 页 ·
                          {{ displayValue(slide, 'title', '未命名页') }}
                        </figcaption>
                      </figure>
                    </div>
                  </section>
                  <section>
                    <strong>建议执行后</strong>
                    <ol>
                      <li
                        v-for="slide in catalog.slidePlanPreview.after"
                        :key="displayValue(slide, 'stable_signature')"
                      >
                        第 {{ displayValue(slide, 'planned_index', '?') }} 页 ·
                        {{ displayValue(slide, 'title', '未命名页') }} ·
                        {{ displayValue(slide, 'state', '保留') }}
                      </li>
                    </ol>
                    <p v-if="catalog.slidePlanPreview.manual_only.length">
                      另有 {{ catalog.slidePlanPreview.manual_only.length }} 项只能人工处理。
                    </p>
                  </section>
                </div>

                <div
                  v-if="editingSlidePlan?.id === selectedSlidePlan.id"
                  class="teaching-prep-plan-operations"
                >
                  <article
                    v-for="edit in slideOperationEdits"
                    :key="edit.operation_id"
                    :class="`risk-${operationForReview(edit.operation_id).risk}`"
                  >
                    <header>
                      <div>
                        <strong>
                          {{ operationKindLabel(operationForReview(edit.operation_id).kind) }}
                        </strong>
                        <span>
                          {{ operationTargetLabel(operationForReview(edit.operation_id)) }}
                        </span>
                      </div>
                      <span>
                        {{
                          operationForReview(edit.operation_id).execution_mode === 'manual_only'
                            ? '仅人工处理'
                            : operationForReview(edit.operation_id).execution_mode === 'noop'
                              ? '保留确认'
                              : 'A00 白名单'
                        }}
                      </span>
                    </header>

                    <label>
                      教师决定
                      <select
                        v-model="edit.decision"
                        :disabled="selectedSlidePlan.status === 'invalidated'"
                      >
                        <option value="proposed">继续复核</option>
                        <option
                          value="approved"
                          :disabled="operationForReview(edit.operation_id).execution_mode === 'manual_only'"
                        >
                          批准
                        </option>
                        <option value="rejected">拒绝</option>
                      </select>
                    </label>
                    <label>
                      原因
                      <textarea
                        v-model="edit.reason"
                        maxlength="1000"
                        rows="2"
                        :disabled="selectedSlidePlan.status === 'invalidated'"
                      />
                    </label>
                    <label>
                      对课堂时间的影响（分钟）
                      <input
                        v-model.number="edit.planned_minutes"
                        type="number"
                        min="0"
                        max="120"
                        :disabled="selectedSlidePlan.status === 'invalidated'"
                      >
                    </label>
                    <label>
                      教师备注（可选）
                      <input
                        v-model="edit.teacher_note"
                        maxlength="1000"
                        :disabled="selectedSlidePlan.status === 'invalidated'"
                      >
                    </label>
                    <small>
                      来源：{{
                        operationForReview(edit.operation_id).citations
                          .map(citationLabel)
                          .join('；')
                      }}
                    </small>
                    <small v-if="operationForReview(edit.operation_id).support_note">
                      {{ operationForReview(edit.operation_id).support_note }}
                    </small>
                  </article>

                  <label>
                    本轮审批说明（可选）
                    <textarea
                      v-model="slideReviewNote"
                      maxlength="1000"
                      rows="2"
                      :disabled="selectedSlidePlan.status === 'invalidated'"
                    />
                  </label>
                  <div class="teaching-prep-draft-editor__actions">
                    <button
                      type="button"
                      :disabled="catalog.saveState === 'saving' || selectedSlidePlan.status === 'invalidated'"
                      @click="saveSlidePlanReview(false)"
                    >
                      保存为新计划版本
                    </button>
                  </div>
                </div>
                <button
                  v-else
                  type="button"
                  @click="startSlidePlanReview(selectedSlidePlan)"
                >
                  逐项审核
                </button>

                <section class="teaching-prep-pptx-execution">
                  <div class="teaching-prep-units__header">
                    <div>
                      <h4>生成 WPS 副本</h4>
                      <span>源课件不会被覆盖；任何一步验证失败都不会发布。</span>
                    </div>
                    <button
                      v-if="catalog.moduleStatus?.wps_execution_available"
                      type="button"
                      :disabled="
                        catalog.saveState === 'saving'
                          || selectedSlidePlan.status !== 'approved'
                          || catalog.pptxExecutions.length > 0
                      "
                      @click="executeApprovedPlan(selectedSlidePlan)"
                    >
                      确认并生成新副本
                    </button>
                  </div>

                  <div
                    v-if="!catalog.moduleStatus?.wps_execution_available"
                    class="teaching-prep-page__notice"
                  >
                    <strong>执行入口当前未启用</strong>
                    <span>
                      审批结果已经保存。启用真实 WPS 和操作真实课件仍需单独授权，
                      当前不会打开或修改任何课件。
                    </span>
                  </div>

                  <p
                    v-else-if="selectedSlidePlan.status !== 'approved'"
                    class="teaching-prep-empty"
                  >
                    所有建议都作出决定并通过白名单检查后，才可生成副本。
                  </p>

                  <section
                    v-if="catalog.lessonGenerationPerformance"
                    class="teaching-prep-performance"
                    aria-label="本次生成性能"
                  >
                    <header>
                      <strong>机器处理用时</strong>
                      <span
                        :class="{
                          'is-exceeded': (
                            catalog.lessonGenerationPerformance.budget_status
                            === 'exceeded'
                          ),
                        }"
                      >
                        {{
                          formatMachineElapsed(
                            catalog.lessonGenerationPerformance.total_machine_elapsed_ms,
                          )
                        }}
                        / 5 分钟
                      </span>
                    </header>
                    <ul>
                      <li>
                        模型调用
                        {{ catalog.lessonGenerationPerformance.model_call_count }}
                        次
                      </li>
                      <li>
                        WPS 执行
                        {{ catalog.lessonGenerationPerformance.wps_execution_count }}
                        次
                      </li>
                      <li>
                        技术重试
                        {{ catalog.lessonGenerationPerformance.technical_retry_count }}
                        次
                      </li>
                    </ul>
                    <p>
                      {{
                        catalog.lessonGenerationPerformance.budget_status === 'exceeded'
                          ? '已超过硬上限，系统不会自行追加模型调用或再次修改。'
                          : catalog.lessonGenerationPerformance.budget_status === 'running'
                            ? '正在处理；这里只计算程序运行时间。'
                            : '符合 5 分钟硬上限。'
                      }}
                      人工查看和审核停留时间不计入。
                    </p>
                  </section>

                  <article
                    v-for="run in catalog.pptxExecutions"
                    :key="run.id"
                    class="teaching-prep-execution-card"
                  >
                    <header>
                      <strong>{{ executionStatusLabel(run.status) }}</strong>
                      <span>预计 {{ run.expected_slide_count }} 页</span>
                    </header>
                    <p v-if="run.error_code">
                      {{ executionErrorLabel(run.error_code) }}
                    </p>
                    <ul v-if="run.verification_report">
                      <li>
                        页数：
                        {{ displayValue(run.verification_report, 'actual_slide_count', '?') }}
                      </li>
                      <li>
                        完整预览：
                        {{ displayValue(run.verification_report, 'rendered_preview_count', '?') }} 页
                      </li>
                      <li>源课件指纹未变化</li>
                      <li>公式、组合图形等受保护对象未意外变化</li>
                    </ul>
                    <p v-if="run.status === 'published'">
                      已验证文件结构和可播放性；数学内容仍需教师确认。
                    </p>
                    <div class="teaching-prep-draft-editor__actions">
                      <a
                        v-if="run.published_version_id"
                        :href="`/api/teaching-prep/pptx-versions/${run.published_version_id}/download`"
                        download
                      >
                        下载验证后的 PPTX
                      </a>
                      <button
                        v-if="
                          run.status === 'published'
                            && run.published_version_id
                            && !packageForVersion(run.published_version_id)
                        "
                        type="button"
                        :disabled="catalog.saveState === 'saving'"
                        @click="createFinalPackage(run.published_version_id)"
                      >
                        确认定稿并生成上课包
                      </button>
                      <button
                        v-if="run.recovery_actions.includes('resume_publication')"
                        type="button"
                        :disabled="catalog.saveState === 'saving'"
                        @click="catalog.recoverPptxExecution(run)"
                      >
                        恢复已验证的发布
                      </button>
                      <button
                        v-if="['running', 'verifying'].includes(run.status)"
                        type="button"
                        @click="catalog.cancelPptxExecution(run)"
                      >
                        取消
                      </button>
                      <button
                        v-if="run.recovery_actions.includes('discard_staging')"
                        type="button"
                        @click="discardExecutionStaging(run)"
                      >
                        清理暂存副本
                      </button>
                    </div>
                  </article>

                  <section
                    v-if="catalog.upClassPackages.length"
                    class="teaching-prep-pptx-execution"
                  >
                    <div class="teaching-prep-units__header">
                      <div>
                        <h4>完整上课包</h4>
                        <span>
                          包含课件、学生练习、教师答案、课堂流程、来源与离线检查。
                        </span>
                      </div>
                    </div>
                    <article
                      v-for="item in catalog.upClassPackages"
                      :key="item.id"
                      class="teaching-prep-execution-card"
                    >
                      <header>
                        <strong>
                          第 {{ item.version_number }} 版
                          <template v-if="item.class_name">
                            · {{ item.class_name }}
                          </template>
                        </strong>
                        <span>{{ item.is_current ? '当前上课版本' : item.status }}</span>
                      </header>
                      <p v-if="item.manifest">
                        文件清单和每个文件的指纹已保存；原资料移走后仍可核对来源版本。
                      </p>
                      <div class="teaching-prep-draft-editor__actions">
                        <a
                          v-if="item.download_url"
                          :href="item.download_url"
                          download
                        >
                          下载完整上课包
                        </a>
                        <button
                          v-if="item.status === 'complete' && !item.is_current"
                          type="button"
                          @click="activateFinalPackage(item)"
                        >
                          恢复为当前版本
                        </button>
                        <button
                          v-if="item.recovery_actions.includes('resume_publish')"
                          type="button"
                          @click="catalog.recoverUpClassPackage(item)"
                        >
                          恢复中断的发布
                        </button>
                        <button
                          v-if="item.recovery_actions.includes('discard_staging')"
                          type="button"
                          @click="catalog.discardUpClassPackageStaging(item)"
                        >
                          清理暂存内容
                        </button>
                        <button
                          v-if="item.status === 'complete'"
                          type="button"
                          @click="reviewingPackageId = item.id"
                        >
                          课后 1 分钟复盘
                        </button>
                      </div>

                      <form
                        v-if="reviewingPackageId === item.id"
                        class="teaching-prep-draft-editor"
                        @submit.prevent="submitPostReview(item)"
                      >
                        <label>
                          课堂时间
                          <select v-model="postReviewForm.timing">
                            <option value="on_time">基本准时</option>
                            <option value="over">超时</option>
                            <option value="early">提前结束</option>
                          </select>
                        </label>
                        <label>
                          练习效果
                          <select v-model="postReviewForm.questionOutcome">
                            <option value="appropriate">合适</option>
                            <option value="too_hard">偏难</option>
                            <option value="too_easy">偏易</option>
                            <option value="ineffective">没有起到作用</option>
                            <option value="not_observed">未观察</option>
                          </select>
                        </label>
                        <label>
                          需要重讲的点（用分号分隔）
                          <input
                            v-model="postReviewForm.reteachPoints"
                            maxlength="1000"
                          >
                        </label>
                        <label>
                          下一版处理
                          <select v-model="postReviewForm.nextAction">
                            <option value="keep">保留</option>
                            <option value="adjust">调整</option>
                            <option value="delete">删除</option>
                          </select>
                        </label>
                        <label>
                          简短说明（可选）
                          <textarea
                            v-model="postReviewForm.note"
                            maxlength="1000"
                            rows="2"
                          />
                        </label>
                        <label class="teaching-prep-checkbox">
                          <input
                            v-model="postReviewForm.useInNextVersion"
                            type="checkbox"
                          >
                          下一版备课时允许我主动选择引用这条复盘
                        </label>
                        <div class="teaching-prep-draft-editor__actions">
                          <button
                            type="submit"
                            :disabled="catalog.saveState === 'saving'"
                          >
                            保存复盘
                          </button>
                          <button
                            type="button"
                            @click="reviewingPackageId = null"
                          >
                            取消
                          </button>
                        </div>
                      </form>
                    </article>
                  </section>

                  <section
                    v-if="catalog.resourcePacks.length"
                    class="teaching-prep-pptx-execution"
                  >
                    <div class="teaching-prep-units__header">
                      <div>
                        <h4>同课不同班</h4>
                        <span>
                          共用已经冻结的资料，但班情、取舍、草稿和输出各自独立。
                        </span>
                      </div>
                    </div>
                    <form
                      class="teaching-prep-draft-editor"
                      @submit.prevent="submitClassVariant"
                    >
                      <label>
                        目标班级
                        <input
                          v-model="classVariantForm.className"
                          maxlength="120"
                          required
                        >
                      </label>
                      <label>
                        该班整体情况（可选）
                        <textarea
                          v-model="classVariantForm.teacherContext"
                          maxlength="2000"
                          rows="2"
                        />
                      </label>
                      <fieldset v-if="catalog.postLessonReviews.length">
                        <legend>可选：引用该班以往复盘</legend>
                        <label
                          v-for="review in catalog.postLessonReviews"
                          :key="review.id"
                          class="teaching-prep-checkbox"
                        >
                          <input
                            v-model="selectedPriorReviewIds"
                            type="checkbox"
                            :value="review.id"
                            :disabled="
                              !review.use_in_next_version
                                || review.class_name !== classVariantForm.className.trim()
                            "
                          >
                          {{ review.class_name || '未指定班级' }}
                          · {{ review.payload.reteach_points.join('；') || '无重讲点' }}
                        </label>
                      </fieldset>
                      <div class="teaching-prep-draft-editor__actions">
                        <button
                          type="submit"
                          :disabled="
                            catalog.saveState === 'saving'
                              || !classVariantForm.className.trim()
                          "
                        >
                          创建独立班级版本
                        </button>
                      </div>
                    </form>
                    <p v-if="catalog.classVariants.length">
                      已创建 {{ catalog.classVariants.length }} 个独立班级版本。
                    </p>
                  </section>
                </section>
              </article>
            </section>
          </section>
        </section>
      </aside>
    </div>
  </section>
</template>
