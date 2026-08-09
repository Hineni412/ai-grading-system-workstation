<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import type { JobResponse } from '../../../api/jobs'
import type {
  MaterialLinkPurpose,
  MaterialDeletionPreview,
  DeleteMaterialSourceResult,
  MaterialVersion,
  ReferencePptCollection,
  SemesterMappingProposalRange,
  SemesterMaterialRecord,
  SemesterMaterialRole,
} from '../api/catalog'
import { teachingPrepCatalogApi } from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../aiAdoption'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'

const workbench = useTeachingPrepWorkbenchContext()
const curriculumScope = useCurriculumScopeStore()
const aiTasks = useWorkspaceAITaskStore()
const activeUnitId = ref<string | null>(null)
const importBatchRunning = ref(false)
const attachingMaterialId = ref<string | null>(null)
const existingRoleDrafts = reactive<Record<string, SemesterMaterialRole>>({})
const workbookSeriesDrafts = reactive<Record<string, string>>({})
const workbookVolumeDrafts = reactive<Record<string, 'A' | 'B'>>({})
const pageInput = ref(1)
const mappingEdits = reactive<Record<string, {
  lessonRef: string
  startUnit: number
  endUnit: number
  reason: string
}>>({})
const manualMapping = reactive<{
  lessonId: string
  startUnit: number
  endUnit: number
  purpose: MaterialLinkPurpose
  note: string
}>({
  lessonId: '',
  startUnit: 1,
  endUnit: 1,
  purpose: 'supplement',
  note: '',
})
const mappingMessage = ref('先导入并解析资料，再逐份建立课时目录。')
const bulkReviewRunning = ref(false)
const reviewLessonRef = ref('')
const semesterSetup = reactive({
  title: '初中数学',
  gradeLevel: 8,
  volume: 'first' as 'first' | 'second' | 'whole_year',
  schoolYear: '2026-2027',
  term: 'first' as 'first' | 'second',
  plannedLessonCount: 60,
})
const semesterSetupMessage = ref('')
let refreshedMappingTaskRevision = ''

const MATERIAL_ROLES: Array<{ value: SemesterMaterialRole; label: string }> = [
  { value: 'textbook', label: '教材' },
  { value: 'reference_ppt', label: '参考课件 / 备选 PPT' },
  { value: 'exercise_workbook', label: '普通教辅' },
  { value: 'homework_workbook', label: '日常作业教辅' },
  { value: 'answer_book', label: '答案册' },
  { value: 'supplement', label: '补充资料' },
]

const LINK_PURPOSES: Array<{ value: MaterialLinkPurpose; label: string }> = [
  { value: 'textbook', label: '教材依据' },
  { value: 'reference_ppt', label: '参考课件' },
  { value: 'exercise', label: '课堂练习' },
  { value: 'answer', label: '答案 / 解析' },
  { value: 'supplement', label: '补充资料' },
]

type PendingImportState =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'done'
  | 'failed'
  | 'cancelled'

interface PendingMaterialImport {
  id: string
  file: File
  role: SemesterMaterialRole
  workbookSeries: string
  workbookVolume: 'A' | 'B'
  state: PendingImportState
  materialId: string | null
  error: string
  folderBatchId: string | null
  relativePath: string
}

const pendingImports = ref<PendingMaterialImport[]>([])

interface PendingPptFolderBatch {
  id: string
  requestToken: string
  displayName: string
  pptCount: number
  ignoredFileCount: number
  duplicatePptCount: number
  state: 'pending' | 'creating' | 'done' | 'failed'
  error: string
}

const pendingPptFolders = ref<PendingPptFolderBatch[]>([])
const referencePptCollections = ref<ReferencePptCollection[]>([])
const refreshedPreviewUrls = new Set<string>()

const activeUnit = computed(
  () => workbench.catalog.materialUnits.find(item => item.id === activeUnitId.value)
    ?? workbench.catalog.materialUnits[0]
    ?? null,
)
const totalPages = computed(() => workbench.catalog.materialUnits.length)
const printedPageNumber = computed(() => {
  const value = activeUnit.value?.object_summary.printed_page_number
  return typeof value === 'number' && Number.isInteger(value) ? value : null
})
// Legacy archived rows are surfaced as ordinary materials so teachers can
// delete them; the recycle-bin concept is no longer part of this workspace.
const activeMaterials = computed(() => workbench.catalog.materials)
const showOtherTermMaterials = ref(false)
const showReferencePptMaterials = ref(false)
const includeReferencePptInDirectory = ref(false)
const batchArchiveRunning = ref(false)
type MaterialDeletionState = 'ready' | 'deleting' | 'checking' | 'succeeded' | 'failed' | 'unknown' | 'blocked'
interface MaterialDeletionPlanItem {
  material: MaterialVersion
  preview: MaterialDeletionPreview
  operationId: string
  state: MaterialDeletionState
  result: DeleteMaterialSourceResult | null
  detail: string
}
const deletionPlan = ref<MaterialDeletionPlanItem[]>([])
const deletionPlanMode = ref<'single' | 'batch'>('single')
const currentTermMaterials = computed(() => activeMaterials.value.filter(item => Boolean(
  semesterRecordFor(item),
)))
const otherTermMaterials = computed(() => activeMaterials.value.filter(item => !semesterRecordFor(item)))
const referencePptMaterials = computed(() => {
  const scoped = !curriculumScope.selectedVolumeId
    ? activeMaterials.value
    : showOtherTermMaterials.value
      ? [...currentTermMaterials.value, ...otherTermMaterials.value]
      : currentTermMaterials.value
  return scoped.filter(item => (
    semesterRecordFor(item)?.material_role === 'reference_ppt'
    || item.material_type === 'pptx'
    || /\.pptx?$/i.test(item.safe_filename || item.display_name)
  ))
})
const visibleMaterials = computed(() => {
  const scoped = !curriculumScope.selectedVolumeId
    ? activeMaterials.value
    : showOtherTermMaterials.value
    ? [...currentTermMaterials.value, ...otherTermMaterials.value]
    : currentTermMaterials.value
  return scoped.filter(item => (
    showReferencePptMaterials.value
    || semesterRecordFor(item)?.material_role !== 'reference_ppt'
    || item.id === workbench.catalog.selectedMaterialId
  ))
})
watch(() => [
  curriculumScope.selectedVolumeId,
  workbench.catalog.selectedSemester?.id ?? null,
], () => {
  showOtherTermMaterials.value = false
  showReferencePptMaterials.value = false
  includeReferencePptInDirectory.value = false
})
const activeMaterial = computed(
  () => workbench.catalog.materials.find(
    item => item.id === workbench.catalog.selectedMaterialId,
  ) ?? null,
)
const activePreviewKind = computed(() => {
  if (activeMaterial.value?.material_type !== 'pptx') return null
  const kind = activeUnit.value?.object_summary.preview_kind
  return kind === 'rendered' || kind === 'structural' ? kind : null
})
const activePreviewLabel = computed(() => {
  if (activePreviewKind.value === 'rendered') return '真实原页'
  if (activePreviewKind.value === 'structural') return '结构预览，不是原页'
  return null
})
const activeProposal = computed(
  () => workbench.catalog.currentSemesterMappingProposal,
)
const proposalLessonOptions = computed(() => (
  activeProposal.value?.payload.tree.flatMap(chapter => (
    chapter.sections.flatMap(section => section.lessons.map(lesson => ({
      value: `proposal:${lesson.key}`,
      title: lesson.title,
      path: `${chapter.title} / ${section.title}`,
    })))
  )) ?? []
))
const formalLessonOptions = computed(() => workbench.catalog.lessonNodes
  .filter(node => node.node_type === 'lesson' && node.is_active)
  .map(node => ({ value: node.id, title: node.title, path: '正式课时树' })))
const mappingLessonOptions = computed(() => [
  ...proposalLessonOptions.value,
  ...formalLessonOptions.value,
])
const mappingGroups = computed(() => {
  const proposal = activeProposal.value
  if (!proposal) return []
  const refs = [...new Set(proposal.payload.mappings.map(item => item.lesson_ref))]
  return refs.map((lessonRef) => {
    const option = mappingLessonOptions.value.find(item => item.value === lessonRef)
    const mappings = proposal.payload.mappings.filter(item => item.lesson_ref === lessonRef)
    return {
      lessonRef,
      title: option?.title ?? '未识别课时',
      path: option?.path ?? lessonRef,
      mappings,
      pendingCount: mappings.filter(item => item.decision === 'pending').length,
    }
  })
})
const activeMappingGroup = computed(() => (
  mappingGroups.value.find(group => group.lessonRef === reviewLessonRef.value)
    ?? mappingGroups.value[0]
    ?? null
))
const proposalEvidenceById = computed(() => {
  const evidence = activeProposal.value?.payload.directory_evidence
  const entries = [
    ...(evidence?.toc_entries ?? []),
    ...(evidence?.resolved_ranges ?? []),
    ...(evidence?.anchors ?? []),
  ]
  return new Map(entries.flatMap((entry) => {
    const id = typeof entry.evidence_id === 'string' ? entry.evidence_id : ''
    return id ? [[id, entry] as const] : []
  }))
})
const allMappingsDecided = computed(() => {
  const mappings = activeProposal.value?.payload.mappings ?? []
  return mappings.length > 0
    && mappings.every(item => item.decision !== 'pending')
})
const pendingMappingCount = computed(() => (
  activeProposal.value?.payload.mappings.filter(item => item.decision === 'pending').length ?? 0
))
const pendingLocalHighConfidenceCount = computed(() => (
  activeProposal.value?.payload.generation_source === 'local_reference_ppt_names'
    ? activeProposal.value.payload.mappings.filter(item => (
        item.decision === 'pending' && item.confidence === 'high'
      )).length
    : 0
))
const eligibleSemesterMaterials = computed(
  () => workbench.catalog.semesterMaterials.filter(item => (
    item.is_active
    && item.parse_status === 'parsed'
    && !item.has_unparsed_update
    && (item.current_unit_count ?? 0) > 0
    && (includeReferencePptInDirectory.value || !isPptSemesterRecord(item))
  )),
)
const activeSemesterRecord = computed(() => (
  activeMaterial.value ? semesterRecordFor(activeMaterial.value) : null
))
const selectedDirectoryMaterialId = computed(() => (
  eligibleSemesterMaterials.value.some(record => (
    record.current_material_version_id === workbench.catalog.selectedMaterialId
  ))
    ? workbench.catalog.selectedMaterialId ?? ''
    : ''
))
const currentMappingPreflight = computed(
  () => workbench.catalog.currentSemesterMappingPreflight,
)
const currentMappingJob = computed(
  () => workbench.catalog.currentSemesterMappingJob,
)
const currentMappingTask = computed(() => {
  const semesterId = workbench.catalog.selectedSemester?.id
  const record = activeSemesterRecord.value
  const sourceRevision = currentMappingPreflight.value?.source_state_sha256
  if (!semesterId || !record) return null
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.semester_mapping'
    && task.source_ref.kind === 'semester'
    && task.source_ref.id === semesterId
    && (!sourceRevision || task.source_ref.revision === sourceRevision)
    && task.context_refs.some(reference => (
      reference.kind === 'material'
      && reference.id === record.id
      && reference.revision === String(record.revision)
    ))
  )) ?? null
})
const currentMappingJobSyncError = computed(
  () => workbench.catalog.currentSemesterMappingJobSyncError,
)
const activeMaterialReadyForMapping = computed(() => (
  activeMaterial.value !== null
  && activeSemesterRecord.value?.parse_status === 'parsed'
  && !activeSemesterRecord.value.has_unparsed_update
  && ['ready', 'scanned_no_text'].includes(
    activeMaterial.value.inspection_status,
  )
))
const pendingImportCount = computed(
  () => pendingImports.value.filter(item => (
    item.state === 'pending' || item.state === 'failed'
    || item.state === 'cancelled'
  )).length,
)

watch(
  () => workbench.catalog.materialUnits,
  units => {
    if (!units.some(item => item.id === activeUnitId.value)) {
      activeUnitId.value = units[0]?.id ?? null
    }
    if (units.length > 0) {
      const maximum = units.at(-1)?.unit_index ?? 1
      manualMapping.startUnit = Math.min(manualMapping.startUnit, maximum)
      manualMapping.endUnit = Math.min(
        Math.max(manualMapping.endUnit, manualMapping.startUnit),
        maximum,
      )
    }
  },
  { immediate: true },
)

watch(
  () => currentMappingTask.value ? `${currentMappingTask.value.task_id}:${currentMappingTask.value.revision}` : '',
  async (revision) => {
    const task = currentMappingTask.value
    if (!revision || revision === refreshedMappingTaskRevision || !task) return
    if (!['proposal_ready', 'needs_input'].includes(task.status)) return
    refreshedMappingTaskRevision = revision
    await workbench.catalog.load()
  },
)

watch(activeUnit, unit => { pageInput.value = unit?.unit_index ?? 1 }, { immediate: true })

watch(
  mappingGroups,
  groups => {
    if (!groups.some(group => group.lessonRef === reviewLessonRef.value)) {
      reviewLessonRef.value = groups[0]?.lessonRef ?? ''
    }
  },
  { immediate: true },
)

function goToPage(value = pageInput.value): void {
  const target = Math.max(1, Math.min(totalPages.value || 1, Math.round(Number(value) || 1)))
  pageInput.value = target
  activeUnitId.value = workbench.catalog.materialUnits.find(item => item.unit_index === target)?.id ?? null
}

function stepPage(offset: number): void {
  goToPage((activeUnit.value?.unit_index ?? 1) + offset)
}

async function refreshRenderedPreview(url: string): Promise<void> {
  const material = activeMaterial.value
  const unit = activeUnit.value
  if (
    !material
    || material.material_type !== 'pptx'
    || unit?.preview_url !== url
    || unit.object_summary.preview_kind === 'rendered'
  ) return
  const key = `${material.id}:${url}`
  if (refreshedPreviewUrls.has(key)) return
  refreshedPreviewUrls.add(key)
  try {
    await workbench.catalog.refreshCurrentMaterialUnits(material.id)
  } catch {
    // The already-loaded structural preview remains the honest fallback.
  }
}

function openMappingRange(startUnit: number): void {
  goToPage(startUnit)
}

async function openMappingItem(item: SemesterMappingProposalRange): Promise<void> {
  const record = workbench.catalog.semesterMaterials.find(
    value => value.id === item.material_record_id,
  )
  const material = record
    ? workbench.catalog.materials.find(
        value => value.id === record.current_material_version_id,
      )
    : null
  if (material && material.id !== workbench.catalog.selectedMaterialId) {
    await openMaterial(material)
  }
  openMappingRange(item.start_unit)
}

function evidenceDisplay(evidenceId: string): { label: string, unit: number | null } {
  const evidence = proposalEvidenceById.value.get(evidenceId)
  if (!evidence) return { label: `${evidenceId}（旧建议未保存证据详情）`, unit: null }
  const title = typeof evidence.title === 'string' ? evidence.title : ''
  const printedPage = typeof evidence.printed_page === 'number' ? evidence.printed_page : null
  const sourceUnit = typeof evidence.source_unit === 'number' ? evidence.source_unit : null
  const unitIndex = typeof evidence.unit_index === 'number' ? evidence.unit_index : null
  const startUnit = typeof evidence.start_unit === 'number' ? evidence.start_unit : null
  const endUnit = typeof evidence.end_unit === 'number' ? evidence.end_unit : null
  const excerpt = typeof evidence.text_excerpt === 'string'
    ? evidence.text_excerpt.trim().slice(0, 72)
    : ''
  if (startUnit !== null && endUnit !== null) {
    return {
      label: `推断范围：${title || evidenceId} · PDF ${startUnit}—${endUnit} 页`,
      unit: startUnit,
    }
  }
  if (unitIndex !== null) {
    return {
      label: `正文锚点：PDF 第 ${unitIndex} 页 · ${title || excerpt || evidenceId}`,
      unit: unitIndex,
    }
  }
  return {
    label: `目录：${title || evidenceId}${printedPage === null ? '' : ` · 书上第 ${printedPage} 页`}${sourceUnit === null ? '' : ` · 目录位于 PDF 第 ${sourceUnit} 页`}`,
    unit: sourceUnit,
  }
}

function openEvidence(evidenceId: string): void {
  const unit = evidenceDisplay(evidenceId).unit
  if (unit !== null) openMappingRange(unit)
}

function handlePageKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, select, button, summary, [contenteditable="true"]')) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    stepPage(-1)
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    stepPage(1)
  }
}

const mappingClock = ref(Date.now())
let mappingClockTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  window.addEventListener('keydown', handlePageKey)
  mappingClockTimer = globalThis.setInterval(() => {
    mappingClock.value = Date.now()
  }, 1_000)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handlePageKey)
  if (mappingClockTimer !== null) globalThis.clearInterval(mappingClockTimer)
})

watch(
  () => workbench.catalog.lessonNodes,
  nodes => {
    const lessons = nodes.filter(item => item.node_type === 'lesson' && item.is_active)
    if (!lessons.some(item => item.id === manualMapping.lessonId)) {
      manualMapping.lessonId = workbench.catalog.selectedLessonId
        ?? lessons[0]?.id
        ?? ''
    }
  },
  { immediate: true },
)

function semesterRecordFor(material: MaterialVersion): SemesterMaterialRecord | null {
  return workbench.catalog.semesterMaterials.find(item => (
    item.current_material_version_id === material.id
  )) ?? null
}

function isPptSemesterRecord(record: SemesterMaterialRecord): boolean {
  const material = workbench.catalog.materials.find(item => (
    item.id === record.current_material_version_id
  ))
  return record.material_role === 'reference_ppt'
    || material?.material_type === 'pptx'
    || /\.pptx?$/i.test(record.safe_filename || record.display_name)
}

watch(
  () => {
    const record = activeSemesterRecord.value
    return [
      workbench.catalog.selectedSemester?.id ?? '',
      workbench.catalog.selectedMaterialId ?? '',
      record?.id ?? '',
      record?.revision ?? 0,
      record?.current_material_version_id ?? '',
      record?.parse_status ?? '',
      record?.has_unparsed_update ?? false,
      record?.is_active ?? false,
    ] as const
  },
  async () => {
    const record = activeSemesterRecord.value
    if (!record || !eligibleSemesterMaterials.value.some(item => item.id === record.id)) return
    if (record.material_role === 'reference_ppt') return
    await prepareSemesterMapping(true)
  },
  { immediate: true },
)

function roleLabel(role: SemesterMaterialRole): string {
  return MATERIAL_ROLES.find(item => item.value === role)?.label ?? role
}

function rolePurpose(role: SemesterMaterialRole): MaterialLinkPurpose {
  return {
    textbook: 'textbook',
    reference_ppt: 'reference_ppt',
    exercise_workbook: 'exercise',
    homework_workbook: 'exercise',
    answer_book: 'answer',
    supplement: 'supplement',
  }[role] as MaterialLinkPurpose
}

function suggestedRole(name: string): SemesterMaterialRole {
  const normalized = name.toLowerCase()
  if (normalized.endsWith('.pptx')) return 'reference_ppt'
  if (/作业|练习册|同步练/.test(name)) return 'homework_workbook'
  if (/教材|教科书/.test(name)) return 'textbook'
  if (/答案|解析/.test(name)) return 'answer_book'
  return 'supplement'
}

async function createSemesterContext(): Promise<void> {
  semesterSetupMessage.value = '正在建立本学期资料容器…'
  try {
    if (workbench.catalog.selectedCurriculum) {
      await workbench.catalog.createSemester({
        request_token: `semester-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        school_year: semesterSetup.schoolYear.trim(),
        term: semesterSetup.term,
        planned_new_lesson_count: semesterSetup.plannedLessonCount,
      })
    } else {
      await workbench.catalog.createSemesterWorkspace({
        curriculum: {
          title: semesterSetup.title.trim(),
          grade_level: semesterSetup.gradeLevel,
          volume: semesterSetup.volume,
          publisher: null,
          edition_label: null,
        },
        semester: {
          school_year: semesterSetup.schoolYear.trim(),
          term: semesterSetup.term,
          planned_new_lesson_count: semesterSetup.plannedLessonCount,
        },
      })
    }
    semesterSetupMessage.value = '学期已建立。现在可在资料卡片上确认角色并加入本学期。'
  } catch {
    semesterSetupMessage.value = workbench.catalog.errorMessage || '学期没有建立，请检查填写内容。'
  }
}

function fileSizeLabel(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(size / 1024))} KB`
}

function importStateLabel(item: PendingMaterialImport): string {
  const job = parseJobFor(item)
  if (item.state === 'processing' && job) {
    if (job.status === 'queued') return '等待解析'
    if (job.cancel_requested) return '正在安全停止'
    return job.detail || '正在逐页处理'
  }
  return {
    pending: '等待导入',
    uploading: '正在复制到本机资料库',
    processing: '正在逐页处理',
    done: '已完成',
    failed: '未完成，可重试',
    cancelled: '已停止，可继续',
  }[item.state]
}

function parseJobFor(item: PendingMaterialImport): JobResponse | null {
  return item.materialId
    ? workbench.catalog.materialParseJobs[item.materialId] ?? null
    : null
}

function progressPercent(item: PendingMaterialImport): number {
  if (item.state === 'done') return 100
  const job = parseJobFor(item)
  if (job) return Math.max(0, Math.min(100, Math.round(job.progress * 100)))
  return item.state === 'uploading' ? 2 : 0
}

function parseJobForMaterial(material: MaterialVersion): JobResponse | null {
  return workbench.catalog.materialParseJobs[material.id] ?? null
}

function materialProgressLabel(material: MaterialVersion): string {
  const job = parseJobForMaterial(material)
  const expected = material.parse_expected_unit_count ?? material.unit_count ?? 0
  const checkpoint = expected > 0
    ? `已保存：预览 ${material.preview_completed_count ?? 0}/${expected} · 文字 ${material.ocr_completed_count ?? 0}/${material.ocr_total_count ?? 0}`
    : ''
  if (!job) return checkpoint
  if (job.status === 'succeeded') return '逐页处理完成'
  if (job.status === 'failed') return `${checkpoint} · 处理未完成，可继续`
  if (job.status === 'cancelled') return `${checkpoint} · 已停止，可继续`
  return job.detail || (job.status === 'queued' ? '等待后台处理' : '正在逐页处理')
}

function materialNeedsContinue(material: MaterialVersion): boolean {
  const job = parseJobForMaterial(material)
  if (job && ['queued', 'running'].includes(job.status)) return false
  const expected = material.parse_expected_unit_count ?? material.unit_count
  return expected === null || expected === undefined
    ? material.inspection_status === 'uninspected'
    : (material.preview_completed_count ?? 0) < expected
      || !['ready', 'scanned_no_text'].includes(material.inspection_status)
}

function failureMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message
  return workbench.catalog.errorMessage || '资料处理没有完成，可保留进度后重试。'
}

async function openMaterial(item: MaterialVersion): Promise<void> {
  mappingMessage.value = (item.unit_count ?? 0) > 0
    ? '正在打开已保存的页级目录…'
    : '正在本机解析资料并生成逐页预览，大文件需要一些时间…'
  try {
    const result = await workbench.catalog.openMaterial(item)
    if (
      result === 'stale'
      || workbench.catalog.selectedMaterialId !== item.id
    ) return
    activeUnitId.value = workbench.catalog.materialUnits[0]?.id ?? null
    const record = semesterRecordFor(item)
    if (record) manualMapping.purpose = rolePurpose(record.material_role)
    mappingMessage.value = workbench.catalog.materialUnits.length > 0
      ? `已载入 ${workbench.catalog.materialUnits.length} 页/张，可核对原页并建立课时关联。`
      : '没有取得可预览页面，请查看上方错误后重试。'
  } catch {
    if (workbench.catalog.selectedMaterialId !== item.id) return
    mappingMessage.value = workbench.catalog.errorMessage
      || '资料没有完成解析，请保留原文件后重试。'
  }
}

async function continueMaterial(item: MaterialVersion): Promise<void> {
  mappingMessage.value = '正在从已保存的检查点继续，只补未完成页面…'
  try {
    await workbench.catalog.parseMaterialInBackground(item)
    if (workbench.catalog.selectedMaterialId !== item.id) return
    await openMaterial(item)
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage
      || '续跑没有完成，已经保存的页面仍然保留。'
  }
}

function queueFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  for (const file of files) {
    pendingImports.value.push({
      id: globalThis.crypto.randomUUID(),
      file,
      role: suggestedRole(file.name),
      workbookSeries: file.name.replace(/\.[^.]+$/, '').replace(/[（(]?\s*[AB]\s*本?[）)]?$/i, '').trim(),
      workbookVolume: /(?:^|[^a-z])b\s*本?(?:[^a-z]|$)/i.test(file.name) ? 'B' : 'A',
      state: 'pending',
      materialId: null,
      error: '',
      folderBatchId: null,
      relativePath: file.name,
    })
  }
  if (files.length > 0) {
    mappingMessage.value = `已加入 ${files.length} 个文件，请逐份确认资料角色后开始导入。`
  }
}

function queuePptFolder(event: Event): void {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files ?? [])
  input.value = ''
  if (selected.length === 0) return
  const paths = selected.map(file => (
    (file as File & { webkitRelativePath?: string }).webkitRelativePath
      || file.name
  ).replaceAll('\\', '/'))
  const firstParts = paths[0]?.split('/').filter(Boolean) ?? []
  const rootName = firstParts.length > 1 ? firstParts[0]! : '参考课件合集'
  const pptFiles = selected.filter(file => file.name.toLowerCase().endsWith('.pptx'))
  const batch: PendingPptFolderBatch = {
    id: globalThis.crypto.randomUUID(),
    requestToken: `ppt-folder-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
    displayName: rootName,
    pptCount: pptFiles.length,
    ignoredFileCount: selected.length - pptFiles.length,
    duplicatePptCount: 0,
    state: 'pending',
    error: '',
  }
  if (pptFiles.length === 0) {
    mappingMessage.value = '这个文件夹中没有找到 PPTX，未导入任何文件。'
    return
  }
  pendingPptFolders.value.push(batch)
  for (const file of pptFiles) {
    const rawPath = (
      (file as File & { webkitRelativePath?: string }).webkitRelativePath
        || file.name
    ).replaceAll('\\', '/')
    const parts = rawPath.split('/').filter(Boolean)
    const relativePath = parts.length > 1 ? parts.slice(1).join('/') : file.name
    pendingImports.value.push({
      id: globalThis.crypto.randomUUID(),
      file,
      role: 'reference_ppt',
      workbookSeries: '',
      workbookVolume: 'A',
      state: 'pending',
      materialId: null,
      error: '',
      folderBatchId: batch.id,
      relativePath,
    })
  }
  mappingMessage.value = (
    `已选择“${rootName}”：${pptFiles.length} 份 PPTX`
    + (batch.ignoredFileCount > 0
      ? `，${batch.ignoredFileCount} 个非 PPT 文件将忽略。`
      : '。')
  )
}

async function loadReferencePptCollections(): Promise<void> {
  const semesterId = workbench.catalog.selectedSemester?.id
  if (!semesterId) {
    referencePptCollections.value = []
    return
  }
  try {
    referencePptCollections.value = await (
      teachingPrepCatalogApi.listReferencePptCollections(semesterId)
    )
  } catch {
    // A collection panel failure must not hide the rest of the material library.
  }
}

async function createCompletedPptCollections(): Promise<void> {
  const semesterId = workbench.catalog.selectedSemester?.id
  if (!semesterId) return
  for (const batch of pendingPptFolders.value) {
    if (batch.state === 'done' || batch.state === 'creating') continue
    const imports = pendingImports.value.filter(item => item.folderBatchId === batch.id)
    if (imports.length !== batch.pptCount || imports.some(item => item.state !== 'done')) {
      continue
    }
    const membersByRecord = new Map<string, { material_record_id: string; relative_path: string }>()
    let missingMaterialCount = 0
    for (const item of imports) {
      const record = workbench.catalog.semesterMaterials.find(value => (
        value.current_material_version_id === item.materialId
      ))
      if (!record) {
        missingMaterialCount += 1
        continue
      }
      if (!membersByRecord.has(record.id)) {
        membersByRecord.set(record.id, {
          material_record_id: record.id,
          relative_path: item.relativePath,
        })
      }
    }
    if (missingMaterialCount > 0) {
      batch.state = 'failed'
      batch.error = '个别课件尚未加入本学期，请刷新后重试建立合集。'
      continue
    }
    const members = Array.from(membersByRecord.values())
    batch.duplicatePptCount = imports.length - members.length
    batch.state = 'creating'
    batch.error = ''
    try {
      await teachingPrepCatalogApi.createReferencePptCollection(
        semesterId,
        {
          request_token: batch.requestToken,
          display_name: batch.displayName,
          ignored_file_count: batch.ignoredFileCount,
          members,
        },
      )
      batch.state = 'done'
      await workbench.catalog.load()
      await loadReferencePptCollections()
      mappingMessage.value = (
        `“${batch.displayName}”已收录为课件文件夹，并生成本地课时树建议；`
        + '没有调用大模型，请在下方核对后确认。'
      )
    } catch (error) {
      batch.state = 'failed'
      batch.error = failureMessage(error)
    }
  }
}

async function retryPptCollection(batch: PendingPptFolderBatch): Promise<void> {
  batch.state = 'pending'
  batch.error = ''
  await createCompletedPptCollections()
}

function collectionFolderGroups(collection: ReferencePptCollection): Array<{
  name: string
  members: ReferencePptCollection['members']
}> {
  const grouped = new Map<string, ReferencePptCollection['members']>()
  for (const member of collection.members) {
    const parts = member.relative_path.split('/').filter(Boolean)
    const name = parts.length > 1 ? parts[0]! : '未分章课件'
    grouped.set(name, [...(grouped.get(name) ?? []), member])
  }
  return [...grouped.entries()].map(([name, members]) => ({ name, members }))
}

watch(
  () => workbench.catalog.selectedSemester?.id ?? '',
  () => { void loadReferencePptCollections() },
  { immediate: true },
)

function removePendingImport(id: string): void {
  pendingImports.value = pendingImports.value.filter(item => item.id !== id)
}

function clearFinishedImports(): void {
  pendingImports.value = pendingImports.value.filter(item => item.state !== 'done')
}

async function importQueuedFiles(): Promise<void> {
  if (importBatchRunning.value || pendingImportCount.value === 0) return
  importBatchRunning.value = true
  let completed = 0
  let failed = 0
  const parseTasks: Promise<void>[] = []
  try {
    for (const item of pendingImports.value) {
      if (
        item.state !== 'pending'
        && item.state !== 'failed'
        && item.state !== 'cancelled'
      ) continue
      item.state = 'uploading'
      item.error = ''
      mappingMessage.value = `正在复制“${item.file.name}”，复制后会在后台逐页处理…`
      try {
        const material = item.materialId
          ? workbench.catalog.materials.find(value => value.id === item.materialId)
          : await workbench.catalog.importMaterialCopy(
              item.file,
              item.role,
              item.role === 'homework_workbook'
                ? { series: item.workbookSeries.trim() || item.file.name, volume: item.workbookVolume }
                : undefined,
            )
        if (!material) throw new Error('没有找到可继续处理的资料副本。')
        item.materialId = material.id
        item.state = 'processing'
        parseTasks.push(
          workbench.catalog.parseMaterialInBackground(material)
            .then(() => {
              item.state = 'done'
              completed += 1
            })
            .catch((error: unknown) => {
              const job = parseJobFor(item)
              item.state = job?.status === 'cancelled' ? 'cancelled' : 'failed'
              item.error = failureMessage(error)
              failed += 1
            }),
        )
      } catch (error) {
        item.state = 'failed'
        item.error = failureMessage(error)
        failed += 1
      }
    }
    importBatchRunning.value = false
    await Promise.allSettled(parseTasks)
    await workbench.refreshCurrentWorkspace()
    const completedFolderCount = pendingPptFolders.value.filter(
      batch => batch.state === 'done',
    ).length
    await createCompletedPptCollections()
    const createdFolder = pendingPptFolders.value.filter(
      batch => batch.state === 'done',
    ).length > completedFolderCount
    mappingMessage.value = failed > 0
      ? `已完成 ${completed} 份，${failed} 份未完成；其余资料已保留，可单独重试失败项。`
      : createdFolder
        ? '课件文件夹已完整收录，并生成无需模型的课时树建议，请在下方核对。'
      : `已完成 ${completed} 份资料的受控复制、角色登记和页级解析。`
  } finally {
    importBatchRunning.value = false
  }
}

async function cancelImport(item: PendingMaterialImport): Promise<void> {
  if (!item.materialId || item.state !== 'processing') return
  try {
    await workbench.catalog.cancelMaterialParse(item.materialId)
    mappingMessage.value = `正在安全停止“${item.file.name}”；已完成的预览会保留。`
  } catch (error) {
    item.error = failureMessage(error)
  }
}

async function attachExistingMaterial(item: MaterialVersion): Promise<void> {
  if (semesterRecordFor(item) || !workbench.catalog.selectedSemester) return
  attachingMaterialId.value = item.id
  try {
    const role = existingRoleDrafts[item.id] ?? suggestedRole(item.safe_filename)
    const workbook = role === 'homework_workbook'
      ? {
          series: workbookSeriesDrafts[item.id]?.trim() || item.display_name,
          volume: workbookVolumeDrafts[item.id] ?? 'A' as const,
        }
      : undefined
    await workbench.catalog.attachSemesterMaterial(item, role, workbook)
    manualMapping.purpose = rolePurpose(role)
    await openMaterial(item)
    const record = semesterRecordFor(item)
    if (record && ['textbook', 'exercise_workbook', 'homework_workbook'].includes(role)) {
      await prepareSemesterMapping()
    }
    mappingMessage.value = `已把“${item.display_name}”作为${roleLabel(role)}加入本学期并选中。请核对发送范围后再交给 AI。`
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '资料未能加入本学期。'
  } finally {
    attachingMaterialId.value = null
  }
}

async function updateExistingRole(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  const role = existingRoleDrafts[item.id] ?? record.material_role
  await workbench.catalog.updateSemesterMaterial(record, {
    materialRole: role,
    workbookSeries: role === 'homework_workbook'
      ? workbookSeriesDrafts[item.id]?.trim() || record.workbook_series || item.display_name
      : null,
    workbookVolume: role === 'homework_workbook'
      ? workbookVolumeDrafts[item.id] ?? record.workbook_volume ?? 'A'
      : null,
  })
  mappingMessage.value = '资料角色已更新。'
}

async function removeFromSemester(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  await workbench.catalog.updateSemesterMaterial(record, { isActive: false })
  mappingMessage.value = '已移出本学期，历史课时和版本记录仍保留。'
}

async function restoreToSemester(item: MaterialVersion): Promise<void> {
  const record = semesterRecordFor(item)
  if (!record) return
  await workbench.catalog.updateSemesterMaterial(record, { isActive: true })
  mappingMessage.value = '已恢复到本学期。'
}

async function renameMaterial(item: MaterialVersion): Promise<void> {
  const name = window.prompt('资料名称', item.display_name)?.trim()
  if (!name || name === item.display_name) return
  await workbench.catalog.updateMaterialSource(item, { displayName: name })
  mappingMessage.value = '资料名称已更新。'
}

function deletionOperationId(): string {
  return `delete-material-${crypto.randomUUID().replaceAll('-', '')}`
}

function deletionTermLabel(term: 'first' | 'second'): string {
  return term === 'first' ? '上学期' : '下学期'
}

function deletionImpactSummary(preview: MaterialDeletionPreview): string {
  return `将删除 ${preview.impact_counts.material_versions} 个资料版本、${preview.impact_counts.material_units} 页解析内容、${preview.impact_counts.lesson_material_links} 条课时关联。`
}

function deletionSnapshotDisclosure(previews: MaterialDeletionPreview[]): string {
  const count = previews.reduce(
    (total, preview) => total + preview.preserved_snapshot_count,
    0,
  )
  return count > 0
    ? `会保留 ${count} 份生成事实快照，仅含资料名、安全文件名、类型、版本标识和内容指纹等生成事实；不保留原文件、解析正文或本机路径。`
    : '本次不会保留生成事实快照。'
}

function deletionPermanentRemovalDisclosure(): string {
  return '原文件、解析页、课时和学期关联将永久删除且无法恢复。'
}

async function prepareMaterialDeletionPlan(
  candidates: MaterialVersion[],
  mode: 'single' | 'batch',
): Promise<void> {
  if (batchArchiveRunning.value || candidates.length === 0) return
  batchArchiveRunning.value = true
  deletionPlanMode.value = mode
  deletionPlan.value = []
  mappingMessage.value = mode === 'single' ? '正在核对完整删除影响……' : `正在核对 ${candidates.length} 份资料的删除影响……`
  try {
    const prepared: MaterialDeletionPlanItem[] = []
    for (const material of candidates) {
      try {
        const preview = await workbench.catalog.getMaterialDeletionPreview(material)
        prepared.push({
          material,
          preview,
          operationId: deletionOperationId(),
          state: preview.can_delete ? 'ready' : 'blocked',
          result: null,
          detail: preview.can_delete
            ? '等待教师最终确认'
            : preview.blocking_generation_count > 0
              ? `仍有 ${preview.blocking_generation_count} 个课件生成进行中或等待恢复；请先完成、取消或放弃恢复。`
              : preview.preserved_history_note ?? '当前资料暂时不能彻底删除。',
        })
      } catch {
        mappingMessage.value = workbench.catalog.errorMessage || `无法核对“${material.display_name}”的删除影响。`
      }
    }
    deletionPlan.value = prepared
    const blocked = prepared.filter(item => item.state === 'blocked').length
    const ready = prepared.length - blocked
    mappingMessage.value = prepared.length === 0
      ? '删除影响没有载入，未执行任何删除。'
      : blocked > 0
        ? `影响已载入：${ready} 份可删除，${blocked} 份因课件生成进行中或等待恢复而暂时阻止。`
        : '完整影响已载入；核对后再做一次最终确认。'
  } finally {
    batchArchiveRunning.value = false
  }
}

async function deleteMaterial(item: MaterialVersion): Promise<void> {
  await prepareMaterialDeletionPlan([item], 'single')
}

async function deleteCurrentTermMaterials(): Promise<void> {
  await prepareMaterialDeletionPlan([...currentTermMaterials.value], 'batch')
}

async function deleteReferencePptMaterials(): Promise<void> {
  await prepareMaterialDeletionPlan([...referencePptMaterials.value], 'batch')
}

function applyDeletionResult(
  plan: MaterialDeletionPlanItem,
  result: DeleteMaterialSourceResult,
): void {
  plan.result = result
  if (result.status === 'succeeded') {
    plan.state = 'succeeded'
    plan.detail = `请求已确认成功，已删除 ${result.deleted_file_count} 个受控文件。`
  } else if (result.status === 'failed') {
    plan.state = 'failed'
    plan.detail = result.error_code
      ? `删除未完成（${result.error_code}），不会自动重试。`
      : '删除未完成，不会自动重试。'
  } else {
    plan.state = 'unknown'
    plan.detail = '结果尚未完成；请使用同一请求号继续核对，不要重新发起删除。'
  }
}

async function replayMaterialDeletion(plan: MaterialDeletionPlanItem): Promise<void> {
  plan.state = 'deleting'
  plan.detail = '正在用同一请求号幂等续作，不会生成新的删除请求。'
  try {
    const replayed = await workbench.catalog.deleteMaterialSource(plan.material, {
      operation_id: plan.operationId,
      preview_version: plan.preview.preview_version,
      confirmation_phrase: plan.preview.confirmation_phrase,
    })
    applyDeletionResult(plan, replayed)
  } catch {
    plan.state = 'unknown'
    plan.detail = '网络结果仍不明；请求号已保留，可继续按同一编号核对或幂等重放。'
  }
}

async function checkMaterialDeletion(
  plan: MaterialDeletionPlanItem,
  replayInterrupted = false,
): Promise<void> {
  plan.state = 'checking'
  plan.detail = '正在按同一请求号核对结果……'
  try {
    const result = await workbench.catalog.getMaterialDeletionStatus(
      plan.operationId,
      plan.material,
    )
    if (result.status === 'interrupted' && replayInterrupted) {
      await replayMaterialDeletion(plan)
      return
    }
    applyDeletionResult(plan, result)
  } catch {
    await replayMaterialDeletion(plan)
  }
}

async function executeMaterialDeletion(plan: MaterialDeletionPlanItem): Promise<void> {
  if (plan.state !== 'ready') return
  plan.state = 'deleting'
  plan.detail = '删除请求已提交，正在等待明确结果……'
  try {
    const result = await workbench.catalog.deleteMaterialSource(plan.material, {
      operation_id: plan.operationId,
      preview_version: plan.preview.preview_version,
      confirmation_phrase: plan.preview.confirmation_phrase,
    })
    applyDeletionResult(plan, result)
  } catch {
    await checkMaterialDeletion(plan)
  }
}

async function confirmMaterialDeletionPlan(): Promise<void> {
  const ready = deletionPlan.value.filter(item => item.state === 'ready')
  if (ready.length === 0) {
    mappingMessage.value = '当前没有可执行的删除项；被阻止的资料仍完整保留。'
    return
  }
  const promptTitle = deletionPlanMode.value === 'single'
    ? `确认彻底删除“${ready[0]!.material.display_name}”吗？已展示的原文件副本、解析内容和关联将永久删除。`
    : `确认彻底删除已核对的 ${ready.length} 份资料吗？每份结果和请求号都会保留在本页。`
  const prompt = [
    promptTitle,
    deletionSnapshotDisclosure(ready.map(item => item.preview)),
    deletionPermanentRemovalDisclosure(),
  ].join('\n\n')
  if (!window.confirm(prompt)) return
  batchArchiveRunning.value = true
  try {
    for (const plan of ready) await executeMaterialDeletion(plan)
  } finally {
    batchArchiveRunning.value = false
  }
  const succeeded = deletionPlan.value.filter(item => item.state === 'succeeded').length
  const unresolved = deletionPlan.value.filter(item => !['succeeded', 'blocked'].includes(item.state)).length
  mappingMessage.value = unresolved > 0
    ? `已确认成功 ${succeeded} 份，${unresolved} 份未得到明确结果；请按各自请求号核对。`
    : `请求已确认成功 ${succeeded} 份；被阻止的资料未发生变化。`
}

function closeMaterialDeletionPlan(): void {
  if (deletionPlan.value.some(item => ['deleting', 'checking'].includes(item.state))) return
  deletionPlan.value = []
}

const mappingJobBusy = computed(() => (
  currentMappingJob.value !== null
  && !['succeeded', 'failed', 'cancelled'].includes(currentMappingJob.value.status)
))
const mappingTaskBusy = computed(() => (
  currentMappingTask.value !== null
  && ['prepared', 'queued', 'running'].includes(currentMappingTask.value.status)
))
const mappingJobHasDurableProposal = computed(() => {
  const job = currentMappingJob.value
  const task = currentMappingTask.value
  const proposal = activeProposal.value
  if (!proposal) return false
  if (task?.proposal_ref_id === proposal.id) return true
  if (!job) return false
  return (
    job.result.proposal_id === proposal.id
    || (
      (job.result.operation_id ?? job.payload.operation_id) === proposal.operation_id
      && (job.result.source_state_sha256 ?? job.payload.source_state_sha256)
        === proposal.source_state_sha256
    )
  )
})
const mappingAwaitingProposal = computed(() => (
  (currentMappingJob.value?.status === 'succeeded'
    || ['proposal_ready', 'needs_input'].includes(currentMappingTask.value?.status ?? ''))
  && !mappingJobHasDurableProposal.value
))
const mappingResultUnknown = computed(() => {
  const job = currentMappingJob.value
  if (currentMappingTask.value?.status === 'result_unknown') return true
  return Boolean(
    job
    && !mappingJobHasDurableProposal.value
    && (
      job.stage === 'result_unknown'
      || job.error?.includes('结果可能未知')
      || job.error?.includes('应用重启')
      || (
        job.status === 'cancelled'
        && ['calling_model', 'validating_response', 'persisting_proposal'].includes(job.stage)
      )
    ),
  )
})
const mappingRetryAvailable = computed(() => (
  currentMappingJob.value?.status === 'failed'
  && !mappingResultUnknown.value
  && !mappingJobHasDurableProposal.value
))
const mappingJobError = computed(() => (
  currentMappingJob.value?.error
  || currentMappingJobSyncError.value?.message
  || ''
))
const mappingProgressPercent = computed(() => (
  currentMappingJob.value
    ? Math.max(0, Math.min(100, Math.round(currentMappingJob.value.progress * 100)))
    : 0
))
const mappingElapsedLabel = computed(() => {
  const job = currentMappingJob.value
  if (!job) return ''
  const started = Date.parse(job.started_at ?? job.created_at)
  if (!Number.isFinite(started)) return ''
  const finished = job.finished_at ? Date.parse(job.finished_at) : mappingClock.value
  const seconds = Math.max(0, Math.floor((finished - started) / 1_000))
  const minutes = Math.floor(seconds / 60)
  return minutes > 0 ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`
})
const mappingStageLabel = computed(() => {
  const task = currentMappingTask.value
  if (task) return task.teacher_message || (({
    prepared: '已冻结发送范围', queued: '等待统一任务执行', running: '模型任务处理中',
    proposal_ready: '待确认建议已就绪', needs_input: '需要教师补充信息',
    result_unknown: '结果未知，禁止自动重试', failed: '生成未完成',
  } as Record<string, string>)[task.status] || task.status)
  const job = currentMappingJob.value
  if (!job) return '尚未提交'
  if (mappingJobHasDurableProposal.value) return '待确认建议已就绪'
  if (mappingResultUnknown.value) return '结果未知，禁止自动重试'
  if (job.status === 'failed') return '生成失败'
  if (job.status === 'cancelled') return '已停止'
  if (job.status === 'succeeded') return '正在恢复审核内容'
  return ({
    queued: '等待后台处理',
    checking: '正在核对资料与模型',
    snapshotting: '正在冻结发送范围',
    claiming_operation: '正在取得防重复调用权',
    calling_model: '模型正在生成建议',
    validating_response: '正在校验模型结果',
    persisting_proposal: '正在保存待确认建议',
    completed: '建议已生成',
    recovered: '已恢复已有结果',
    result_unknown: '结果未知，禁止自动重试',
  }[job.stage] ?? job.detail) || job.stage
})
const mappingPurposeLabel = computed(() => {
  const preflight = currentMappingPreflight.value
  if (!preflight) return '等待发送范围检查'
  return preflight.creates_initial_tree
    ? '提出初始章—节—课时树'
    : `映射到现有 ${preflight.existing_lesson_count} 个课时`
})
const mappingInitialTreeBlocker = computed(() => {
  const record = activeSemesterRecord.value
  const hasLesson = workbench.catalog.lessonNodes.some(item => (
    item.node_type === 'lesson' && item.is_active
  ))
  if (!record || hasLesson) return ''
  return ['textbook', 'exercise_workbook', 'homework_workbook'].includes(record.material_role)
    ? ''
    : '正式课时树为空时，只有教材、普通教辅或日常作业教辅可以提出初始目录；当前资料可在已有课时树建立后用于映射。'
})
const canGenerateMapping = computed(() => (
  activeSemesterRecord.value !== null
  && currentMappingPreflight.value?.model_available === true
  && !mappingInitialTreeBlocker.value
  && !mappingJobBusy.value
  && !mappingTaskBusy.value
  && !mappingResultUnknown.value
  && !mappingJobHasDurableProposal.value
  && currentMappingJob.value?.status !== 'succeeded'
  && workbench.catalog.saveState !== 'saving'
))

async function selectDirectoryMaterial(event: Event): Promise<void> {
  const materialId = (event.target as HTMLSelectElement).value
  const material = workbench.catalog.materials.find(item => item.id === materialId)
  if (material) await openMaterial(material)
}

async function prepareSemesterMapping(silent = false): Promise<void> {
  const record = activeSemesterRecord.value
  if (!record) return
  if (!silent) {
    mappingMessage.value = '正在检查本次目录整理范围，不会自动调用模型…'
  }
  try {
    await workbench.catalog.prepareSemesterMapping([record.id])
    if (
      workbench.catalog.selectedMaterialId !== record.current_material_version_id
      || workbench.catalog.currentSemesterMappingPreflight === null
    ) return
    mappingMessage.value = '发送范围已准备好；确认后可生成一份待审核的课时目录建议。'
  } catch {
    if (workbench.catalog.selectedMaterialId !== record.current_material_version_id) return
    mappingMessage.value = workbench.catalog.errorMessage || '发送范围检查未完成。'
  }
}

async function generateSemesterMapping(): Promise<void> {
  const record = activeSemesterRecord.value
  const semester = workbench.catalog.selectedSemester
  const preflight = currentMappingPreflight.value
  if (!record || !semester || !preflight) return
  mappingMessage.value = '统一 AI 任务正在登记；本次最多调用模型一次，不会自动重试…'
  try {
    const prepared = await aiTasks.prepare({
      operation_id: `semester-mapping-${crypto.randomUUID().replaceAll('-', '')}`,
      module: 'teaching_prep',
      task_kind: 'teaching_prep.semester_mapping',
      source_ref: { kind: 'semester', id: semester.id, revision: preflight.source_state_sha256 },
      context_refs: [{ kind: 'material', id: record.id, revision: String(record.revision) }],
      prompt_contract_version: 'teaching-prep-semester-mapping-v1',
      model_destination_fingerprint: preflight.model_destination_fingerprint,
      return_target: 'teaching_prep.library',
    })
    await aiTasks.dispatch(prepared)
    mappingMessage.value = '任务已进入统一任务抽屉；可离开页面，重启后只恢复本地结果。'
  } catch {
    mappingMessage.value = '任务没有成功登记，系统不会自动重新发送。'
  }
}

async function retrySemesterMapping(): Promise<void> {
  if (!mappingRetryAvailable.value) return
  await prepareSemesterMapping()
  if (workbench.catalog.currentSemesterMappingPreflight) {
    await generateSemesterMapping()
  }
}

async function discardUnknownMappingResult(): Promise<void> {
  const task = currentMappingTask.value
  if (!task || task.status !== 'result_unknown') return
  if (!window.confirm(
    '确定放弃这次无法确认的结果吗？旧记录会保留为“已放弃”，不会自动调用模型；之后可重新检查发送范围。',
  )) return
  try {
    await aiTasks.discard(task.task_id)
    mappingMessage.value = '旧结果已放弃。请重新检查发送范围；只有再次点击“交给 AI”才会产生新调用。'
  } catch {
    mappingMessage.value = '旧结果尚未放弃，请保留当前页面后重试。'
  }
}

function editFor(item: SemesterMappingProposalRange) {
  const teacher = item.teacher_revision
  return mappingEdits[item.mapping_id] ??= {
    lessonRef: teacher?.lesson_ref ?? item.lesson_ref,
    startUnit: teacher?.start_unit ?? item.start_unit,
    endUnit: teacher?.end_unit ?? item.end_unit,
    reason: item.decision_reason ?? '',
  }
}

async function decideMapping(
  item: SemesterMappingProposalRange,
  decision: 'accepted' | 'modified' | 'rejected',
): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal) return
  const edit = editFor(item)
  mappingMessage.value = '正在保存本条决定…'
  try {
    const updated = await teachingPrepWorkbenchApi.reviewMapping(
      proposal.id,
      item.mapping_id,
      {
        expected_revision: proposal.revision,
        decision,
        ...(decision === 'accepted'
          ? {
              lesson_ref: item.lesson_ref,
              start_unit: item.start_unit,
              end_unit: item.end_unit,
            }
          : decision === 'modified'
            ? {
                lesson_ref: edit.lessonRef,
                start_unit: edit.startUnit,
                end_unit: edit.endUnit,
              }
            : {}),
        reason: edit.reason || null,
      },
    )
    const index = workbench.catalog.semesterMappingProposals.findIndex(
      proposalItem => proposalItem.id === updated.id,
    )
    if (index >= 0) workbench.catalog.semesterMappingProposals[index] = updated
    mappingMessage.value = decision === 'rejected'
      ? '已拒绝本条建议，原始建议仍保留。'
      : decision === 'modified'
        ? '已保存教师修改，原始建议仍可追溯。'
        : '已接受本条建议。'
  } catch {
    mappingMessage.value = '本条建议没有保存，请刷新后核对版本。'
  }
}

async function applyProposal(): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal || proposal.payload.mappings.length === 0) {
    mappingMessage.value = '请先取得建议并逐条完成决定，再应用到正式课时树。'
    return
  }
  if (!allMappingsDecided.value) {
    mappingMessage.value = `还有 ${pendingMappingCount.value} 条建议尚未决定；全部处理后才能应用。`
    return
  }
  try {
    const adopted = await adoptTeachingPrepProposal(
      aiTasks.orderedTasks,
      'teaching_prep.semester_mapping',
      proposal.id,
      {
        kind: 'apply_semester_mapping',
        proposal_revision: proposal.revision,
      },
    )
    if (adopted) {
      mappingMessage.value = '正式映射已写入且已有回执。'
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        workbench.catalog.load(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        mappingMessage.value = '正式映射已写入且已有回执；页面状态暂未刷新，请刷新页面。'
      }
    } else {
      await workbench.catalog.applySemesterMapping(proposal)
      await workbench.refreshCurrentWorkspace()
      mappingMessage.value = '全部接受项已写入正式映射。'
    }
    const firstLesson = workbench.catalog.lessonNodes.find(
      item => item.node_type === 'lesson' && item.is_active,
    )
    if (firstLesson) await workbench.openLesson(firstLesson.id)
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage
      || '正式映射尚未确认；已保存的逐条决定仍保留，请直接重试。'
  }
}

async function acceptLocalHighConfidenceMappings(): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal || pendingLocalHighConfidenceCount.value === 0) {
    mappingMessage.value = '当前没有可直接确认的高置信建议，请查看待审核建议或先生成目录建议。'
    return
  }
  mappingMessage.value = '正在批量确认本机高置信课件命名建议…'
  try {
    const updated = await teachingPrepCatalogApi.acceptLocalReferencePptMappings(
      proposal,
    )
    const index = workbench.catalog.semesterMappingProposals.findIndex(
      item => item.id === updated.id,
    )
    if (index >= 0) workbench.catalog.semesterMappingProposals[index] = updated
    mappingMessage.value = '高置信建议已确认；请继续核对剩余少量疑点。'
  } catch {
    mappingMessage.value = '批量确认没有保存，请刷新后重试。'
  }
}

async function acceptAllPendingMappings(): Promise<void> {
  if (bulkReviewRunning.value) {
    mappingMessage.value = '正在接受建议，请等待当前保存完成。'
    return
  }
  if (pendingMappingCount.value === 0) {
    mappingMessage.value = '当前没有待接受的映射建议；如已逐条决定，可直接应用。'
    return
  }
  const count = pendingMappingCount.value
  if (!window.confirm(`确认接受剩余 ${count} 条课时目录建议吗？接受后仍需点击“应用全部接受项”才会写入正式课时树。`)) return
  bulkReviewRunning.value = true
  mappingMessage.value = `正在接受剩余 ${count} 条建议…`
  try {
    while (true) {
      const pending = activeProposal.value?.payload.mappings.find(
        item => item.decision === 'pending',
      )
      if (!pending) break
      const pendingId = pending.mapping_id
      await decideMapping(pending, 'accepted')
      const saved = activeProposal.value?.payload.mappings.find(
        item => item.mapping_id === pendingId,
      )
      if (saved?.decision === 'pending') return
    }
    mappingMessage.value = `已接受 ${count} 条建议；现在可以应用到正式课时树。`
  } finally {
    bulkReviewRunning.value = false
  }
}

async function saveManualMapping(): Promise<void> {
  const material = activeMaterial.value
  const record = activeSemesterRecord.value
  const lesson = workbench.catalog.lessonNodes.find(
    item => item.id === manualMapping.lessonId && item.node_type === 'lesson',
  )
  const maximum = workbench.catalog.materialUnits.at(-1)?.unit_index ?? 0
  if (
    !material
    || !record
    || !activeMaterialReadyForMapping.value
    || !lesson
    || maximum === 0
  ) {
    mappingMessage.value = '请先打开已加入本学期且解析完成的资料，并选择现有课时。'
    return
  }
  if (
    manualMapping.startUnit < 1
    || manualMapping.endUnit < manualMapping.startUnit
    || manualMapping.endUnit > maximum
  ) {
    mappingMessage.value = `页段应在 1—${maximum} 之间，且结束页不能早于起始页。`
    return
  }
  mappingMessage.value = '正在保存人工课时页段关联…'
  try {
    if (workbench.catalog.selectedLessonId !== lesson.id) {
      await workbench.openLesson(lesson.id)
    }
    await workbench.catalog.confirmMaterialRanges(
      [{ start: manualMapping.startUnit, end: manualMapping.endUnit }],
      manualMapping.purpose,
      manualMapping.note.trim() || null,
    )
    await workbench.refreshCurrentWorkspace()
    mappingMessage.value = `已把第 ${manualMapping.startUnit}—${manualMapping.endUnit} 页/张关联到“${lesson.title}”。`
    manualMapping.note = ''
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage || '人工关联没有保存。'
  }
}
</script>

<template>
  <section class="tp-workspace tp-material-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">资料库</p>
        <h1 data-workbench-title tabindex="-1">建立可复用的学期资料目录</h1>
        <p>逐份确认角色并在本机解析成页级索引；以后备课只引用当前课时需要的几页。</p>
      </div>
      <div class="tp-inline-actions">
        <label
          class="tp-button tp-button--secondary tp-file-button"
          :class="{ 'is-disabled': importBatchRunning || !workbench.catalog.selectedSemester }"
          title="选择整个文件夹；只收录其中的 PPTX"
        >
          导入课件文件夹
          <input
            type="file"
            multiple
            webkitdirectory
            directory
            :disabled="importBatchRunning || !workbench.catalog.selectedSemester"
            @change="queuePptFolder"
          >
        </label>
        <label
          class="tp-button tp-button--secondary tp-file-button"
          :class="{ 'is-disabled': importBatchRunning }"
        >
          选择多份资料
          <input
            type="file"
            multiple
            accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"
            :disabled="importBatchRunning"
            @change="queueFiles"
          >
        </label>
      </div>
    </header>

    <div
      v-if="workbench.catalog.errorMessage"
      class="tp-global-notice"
      role="alert"
    >
      <strong>部分资料信息未更新</strong>
      <span>{{ workbench.catalog.errorMessage }}</span>
    </div>

    <section
      v-if="pendingPptFolders.length || referencePptCollections.length"
      class="tp-section-block"
    >
      <div class="tp-section-heading">
        <div>
          <p class="tp-eyebrow">参考课件文件夹</p>
          <h2>保留原层级，按命名提出课时树</h2>
          <p>系统只保存受控副本和虚拟目录；不会移动桌面原文件，也不会调用大模型。</p>
        </div>
      </div>
      <article
        v-for="batch in pendingPptFolders"
        :key="batch.id"
        class="tp-material-card"
      >
        <strong>{{ batch.displayName }}</strong>
        <small>
          {{ batch.pptCount }} 份 PPTX
          <template v-if="batch.ignoredFileCount"> · 忽略 {{ batch.ignoredFileCount }} 个其他文件</template>
          <template v-if="batch.duplicatePptCount"> · {{ batch.duplicatePptCount }} 份重复课件仅收录一次</template>
          · {{ batch.state === 'done' ? '合集已建立' : batch.state === 'creating' ? '正在建立虚拟目录' : batch.state === 'failed' ? '合集未完成' : '等待导入' }}
        </small>
        <span v-if="batch.error" class="tp-error-text">{{ batch.error }}</span>
        <button
          v-if="batch.state === 'failed'"
          type="button"
          @click="retryPptCollection(batch)"
        >
          重试建立合集
        </button>
      </article>
      <details
        v-for="collection in referencePptCollections"
        :key="collection.id"
        class="tp-reference-ppt-collection"
      >
        <summary>
          {{ collection.display_name }}（{{ collection.members.length }} 份 PPT）
        </summary>
        <div v-for="folder in collectionFolderGroups(collection)" :key="folder.name">
          <strong>{{ folder.name }}</strong>
          <ul>
            <li v-for="member in folder.members" :key="member.id">
              {{ member.relative_path.split('/').at(-1) }}
              · {{ member.kind === 'lesson' ? '课时候选' : '章内参考' }}
              · {{ member.confidence === 'high' ? '高置信' : member.confidence === 'medium' ? '待核对' : '未对应' }}
            </li>
          </ul>
        </div>
      </details>
    </section>

    <section v-if="pendingImports.length" class="tp-section-block tp-import-queue">
      <div class="tp-section-heading">
        <div>
          <h2>待导入资料</h2>
          <p>先逐份复制，再由后台并行处理；每一页的预览和文字识别进度都会保留。</p>
        </div>
        <div class="tp-inline-actions">
          <button
            type="button"
            :disabled="importBatchRunning || !pendingImports.some(item => item.state === 'done')"
            @click="clearFinishedImports"
          >
            清除已完成
          </button>
          <button
            class="tp-button--primary"
            type="button"
            :disabled="importBatchRunning || pendingImportCount === 0"
            @click="importQueuedFiles"
          >
            {{ importBatchRunning ? '正在复制资料…' : `开始导入 ${pendingImportCount} 份` }}
          </button>
        </div>
      </div>
      <div class="tp-import-queue__list">
        <article
          v-for="item in pendingImports"
          :key="item.id"
          class="tp-import-row"
          :class="`is-${item.state}`"
        >
          <div class="tp-import-row__identity">
            <strong>{{ item.file.name }}</strong>
            <small v-if="item.folderBatchId">{{ item.relativePath }}</small>
            <small>{{ fileSizeLabel(item.file.size) }} · {{ importStateLabel(item) }}</small>
            <small v-if="item.error" class="tp-error-text">{{ item.error }}</small>
            <div
              v-if="item.state !== 'pending'"
              class="tp-import-row__progress"
            >
              <progress
                :value="progressPercent(item)"
                max="100"
                :aria-label="`${item.file.name}：${importStateLabel(item)}`"
              />
              <span>{{ progressPercent(item) }}%</span>
            </div>
          </div>
          <label class="tp-field">
            资料角色
            <select
              v-model="item.role"
              :disabled="item.state === 'uploading' || item.state === 'processing' || item.state === 'done'"
            >
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
          </label>
          <template v-if="item.role === 'homework_workbook'">
            <label class="tp-field">
              教辅套组
              <input v-model="item.workbookSeries" type="text" placeholder="例如：全品学练考">
            </label>
            <label class="tp-field">
              分册
              <select v-model="item.workbookVolume">
                <option value="A">A 本</option>
                <option value="B">B 本</option>
              </select>
            </label>
          </template>
          <div class="tp-import-row__actions">
            <button
              v-if="item.state === 'processing'"
              type="button"
              :disabled="parseJobFor(item)?.cancel_requested"
              @click="cancelImport(item)"
            >
              {{ parseJobFor(item)?.cancel_requested ? '正在停止…' : '停止' }}
            </button>
            <button
              v-else
              type="button"
              :disabled="item.state === 'uploading'"
              @click="removePendingImport(item.id)"
            >
              移除
            </button>
          </div>
        </article>
      </div>
    </section>

    <section v-if="!workbench.catalog.selectedSemester" class="tp-section-block tp-semester-setup">
      <div>
        <p class="tp-eyebrow">首次使用</p>
        <h2>先建立本学期，再选择已解析资料</h2>
        <p>这一步只建立资料归属，不会调用 AI，也不会重复解析已有文件。</p>
      </div>
      <label v-if="!workbench.catalog.selectedCurriculum" class="tp-field">教材名称<input v-model="semesterSetup.title" type="text"></label>
      <label v-if="!workbench.catalog.selectedCurriculum" class="tp-field">年级<input v-model.number="semesterSetup.gradeLevel" type="number" min="1" max="12"></label>
      <label v-if="!workbench.catalog.selectedCurriculum" class="tp-field">册别<select v-model="semesterSetup.volume"><option value="first">上册</option><option value="second">下册</option><option value="whole_year">全一册</option></select></label>
      <label class="tp-field">学年<input v-model="semesterSetup.schoolYear" type="text" placeholder="2026-2027"></label>
      <label class="tp-field">学期<select v-model="semesterSetup.term"><option value="first">第一学期</option><option value="second">第二学期</option></select></label>
      <label class="tp-field">计划课时数<input v-model.number="semesterSetup.plannedLessonCount" type="number" min="1" max="300"></label>
      <div class="tp-inline-actions"><button class="tp-button--primary" type="button" :disabled="workbench.catalog.saveState === 'saving'" @click="createSemesterContext">{{ workbench.catalog.saveState === 'saving' ? '正在建立…' : '建立本学期' }}</button></div>
      <p v-if="semesterSetupMessage" class="tp-muted" role="status">{{ semesterSetupMessage }}</p>
    </section>

    <section class="tp-section-block tp-directory-actions">
      <div>
        <p class="tp-eyebrow">课时目录整理</p>
        <h2>教材教辅逐份整理，课件文件夹批量整理</h2>
        <p>教材教辅先检查发送页再由模型建议；课件合集只在本机按命名建议。两者都须确认后才修改正式课时树。</p>
      </div>
      <div class="tp-directory-material-field">
        <label class="tp-field">
          本次资料
          <select
            :value="selectedDirectoryMaterialId"
            @change="selectDirectoryMaterial"
          >
            <option value="">请选择已解析资料</option>
            <option
              v-for="record in eligibleSemesterMaterials"
              :key="record.id"
              :value="record.current_material_version_id"
            >
              {{ roleLabel(record.material_role) }} · {{ record.display_name }} · {{ record.current_unit_count }} 页/张
            </option>
          </select>
        </label>
        <label class="tp-field--check">
          <input
            v-model="includeReferencePptInDirectory"
            data-testid="include-reference-ppt-directory"
            type="checkbox"
          >
          本次解析包含参考 PPT（默认不包含参考 PPT）
        </label>
      </div>
      <div class="tp-inline-actions">
        <button
          type="button"
          :disabled="!activeSemesterRecord || workbench.catalog.loadState === 'loading'"
          @click="prepareSemesterMapping()"
        >
          检查发送范围
        </button>
        <button
          v-if="mappingRetryAvailable"
          class="tp-button--primary"
          type="button"
          :disabled="workbench.catalog.saveState === 'saving'"
          @click="retrySemesterMapping"
        >
          重新检查并生成新建议
        </button>
        <button
          v-else
          class="tp-button--primary"
          type="button"
          :disabled="!canGenerateMapping || mappingAwaitingProposal"
          @click="generateSemesterMapping"
        >
          {{ mappingResultUnknown
            ? '结果未知，不能自动重试'
            : mappingJobBusy
              ? '后台生成中…'
              : mappingAwaitingProposal
                ? '正在恢复审核内容…'
                : '交给 AI 整理课时树' }}
        </button>
      </div>
      <div
        v-if="activeSemesterRecord"
        class="tp-mapping-job-status"
        :class="{
          'is-running': mappingJobBusy || mappingTaskBusy || mappingAwaitingProposal,
          'is-error': Boolean(mappingJobError) || mappingResultUnknown,
          'is-success': mappingJobHasDurableProposal,
        }"
        role="status"
        aria-live="polite"
      >
        <div class="tp-mapping-job-status__heading">
          <div>
            <span class="tp-status-pill">{{ mappingStageLabel }}</span>
            <strong>{{ activeMaterial?.display_name }}</strong>
          </div>
          <span v-if="currentMappingJob || currentMappingTask">
            {{ currentMappingTask ? Math.round(currentMappingTask.progress * 100) : mappingProgressPercent }}%
            <template v-if="mappingElapsedLabel"> · 已等待 {{ mappingElapsedLabel }}</template>
          </span>
        </div>
        <progress
          v-if="currentMappingJob || currentMappingTask"
          :value="currentMappingTask ? Math.round(currentMappingTask.progress * 100) : mappingProgressPercent"
          max="100"
          :aria-label="`目录建议任务：${mappingStageLabel}`"
        />
        <p class="tp-mapping-job-status__summary">
          <span>{{ roleLabel(activeSemesterRecord.material_role) }} · {{ activeSemesterRecord.current_unit_count }} 页/张</span>
          <span>{{ mappingPurposeLabel }}</span>
          <span>单次调用 · 不自动重试</span>
        </p>
        <p
          v-if="currentMappingJob?.detail && !mappingJobHasDurableProposal"
          class="tp-muted"
        >{{ currentMappingJob.detail }}</p>
        <p v-if="workbench.catalog.currentSemesterMappingJobRecovered" class="tp-muted">
          已从服务端恢复同一后台任务，没有再次发送模型请求。
        </p>
        <p v-if="mappingInitialTreeBlocker" class="tp-error-text">{{ mappingInitialTreeBlocker }}</p>
        <p v-if="mappingJobError" class="tp-error-text">{{ mappingJobError }}</p>
        <p v-if="mappingRetryAvailable" class="tp-error-text">
          已确认本次失败。只有点击“重新检查并生成新建议”才会创建新的模型请求。
        </p>
        <p v-if="mappingResultUnknown" class="tp-error-text">
          应用重启后无法确认模型结果；为避免重复费用，系统不会自动重试。
        </p>
        <button
          v-if="currentMappingTask?.status === 'result_unknown'"
          type="button"
          @click="discardUnknownMappingResult"
        >
          放弃旧结果，重新准备
        </button>
      </div>
      <details v-if="currentMappingPreflight" class="tp-mapping-scope-summary">
        <summary>
          发送范围：{{ currentMappingPreflight.scanned_unit_count ?? currentMappingPreflight.unit_count }} 页检查
          · {{ currentMappingPreflight.anchor_count ?? 0 }} 个正文锚点
          <template v-if="currentMappingPreflight.estimated_input_characters">
            · 约 {{ currentMappingPreflight.estimated_input_characters }} 字
          </template>
        </summary>
        <div>
          <span>
            {{ currentMappingPreflight.model_label ?? (currentMappingPreflight.model_available ? '已配置模型' : '模型不可用') }}
            · 建议需逐条确认后才会写入课时树
          </span>
          <span v-if="currentMappingPreflight.full_page_text_sent === false">
            未发送全部逐页正文；
            <template v-if="currentMappingPreflight.directory_page_images_sent">
              将发送 {{ currentMappingPreflight.directory_page_image_count ?? 0 }} 张已定位的目录页图片、目录线索与抽样正文锚点。
            </template>
            <template v-else>
              只发送目录线索与抽样正文锚点。
            </template>
          </span>
          <span
            v-for="issue in currentMappingPreflight.evidence_issues ?? []"
            :key="issue"
            class="tp-muted"
          >{{ issue }}</span>
          <span v-if="!currentMappingPreflight.model_available">
            仍可在右侧把页段人工关联到现有课时。
          </span>
        </div>
      </details>
      <p v-else-if="eligibleSemesterMaterials.length === 0" class="tp-muted">
        暂无已解析的本学期资料。先在上方导入，或把左侧已有资料补充加入本学期。
      </p>
    </section>

    <section v-if="deletionPlan.length" class="tp-section-block tp-deletion-preview" aria-labelledby="material-deletion-title">
      <div class="tp-section-heading">
        <div>
          <p class="tp-eyebrow">彻底删除影响预览</p>
          <h2 id="material-deletion-title">
            {{ deletionPlanMode === 'single' ? '先核对这一份资料' : `逐项核对 ${deletionPlan.length} 份资料` }}
          </h2>
          <p>此处仅展示影响。只有下方一次最终确认后才会提交删除；网络结果不明时只按原请求号查询。</p>
        </div>
        <button type="button" :disabled="batchArchiveRunning" @click="closeMaterialDeletionPlan">关闭预览</button>
      </div>
      <article
        v-for="plan in deletionPlan"
        :key="plan.operationId"
        class="tp-deletion-preview__item"
        :class="`is-${plan.state}`"
      >
        <header>
          <div>
            <strong>{{ plan.preview.display_name }}</strong>
            <small>请求号：<span data-testid="deletion-operation-id">{{ plan.operationId }}</span></small>
          </div>
          <span>{{ plan.state === 'ready' ? '等待确认' : plan.state === 'blocked' ? '禁止删除' : plan.state === 'succeeded' ? '已确认成功' : plan.state === 'failed' ? '已失败' : plan.state === 'unknown' ? '结果待核对' : '正在核对' }}</span>
        </header>
        <p>{{ deletionImpactSummary(plan.preview) }}</p>
        <dl class="tp-deletion-impact-grid">
          <div><dt>资料源</dt><dd>{{ plan.preview.impact_counts.material_sources }}</dd></div>
          <div><dt>资料版本</dt><dd>{{ plan.preview.impact_counts.material_versions }}</dd></div>
          <div><dt>解析页/张</dt><dd>{{ plan.preview.impact_counts.material_units }}</dd></div>
          <div><dt>课时关联</dt><dd>{{ plan.preview.impact_counts.lesson_material_links }}</dd></div>
          <div><dt>学期资料记录</dt><dd>{{ plan.preview.impact_counts.semester_material_records }}</dd></div>
          <div><dt>目录建议</dt><dd>{{ plan.preview.impact_counts.semester_mapping_proposals }}</dd></div>
          <div><dt>参考 PPT 合集</dt><dd>{{ plan.preview.impact_counts.reference_ppt_collections }}</dd></div>
          <div><dt>习题区域</dt><dd>{{ plan.preview.impact_counts.exercise_regions }}</dd></div>
          <div><dt>习题候选</dt><dd>{{ plan.preview.impact_counts.exercise_candidates }}</dd></div>
          <div><dt>受控文件</dt><dd>{{ plan.preview.owned_file_count }}</dd></div>
          <div><dt>正式生成历史</dt><dd>{{ plan.preview.generation_history_count }}</dd></div>
          <div><dt>保留事实快照</dt><dd>{{ plan.preview.preserved_snapshot_count }}</dd></div>
          <div><dt>阻断中的生成</dt><dd>{{ plan.preview.blocking_generation_count }}</dd></div>
        </dl>
        <div v-if="plan.preview.affected_semesters.length" class="tp-deletion-semesters">
          <strong>受影响学期</strong>
          <span v-for="semesterItem in plan.preview.affected_semesters" :key="semesterItem.semester_id">
            {{ semesterItem.title }} · {{ semesterItem.school_year }} · {{ deletionTermLabel(semesterItem.term) }}
          </span>
        </div>
        <p v-if="plan.preview.preserved_history_note" class="tp-inline-guidance">{{ plan.preview.preserved_history_note }}</p>
        <p v-if="plan.preview.blocking_generation_count > 0" class="tp-error-text">
          仍有 {{ plan.preview.blocking_generation_count }} 个课件生成进行中或等待恢复；请先完成、取消或放弃恢复，再重新核对删除影响。
        </p>
        <p v-else-if="plan.preview.blocker_code" class="tp-error-text">
          当前资料暂时不能删除（{{ plan.preview.blocker_code }}），请按上方说明处理后重新核对。
        </p>
        <p role="status" :class="{ 'tp-error-text': ['failed', 'unknown', 'blocked'].includes(plan.state) }">{{ plan.detail }}</p>
        <button
          v-if="plan.state === 'unknown' || plan.state === 'failed'"
          type="button"
          @click="checkMaterialDeletion(plan, true)"
        >
          按此请求号核对 / 幂等重放
        </button>
      </article>
      <div class="tp-inline-actions">
        <button type="button" :disabled="batchArchiveRunning" @click="closeMaterialDeletionPlan">取消，不删除</button>
        <button
          class="tp-button tp-button--primary is-danger"
          data-testid="confirm-material-deletion"
          type="button"
          :disabled="batchArchiveRunning"
          @click="confirmMaterialDeletionPlan"
        >
          {{ batchArchiveRunning ? '正在核对删除结果…' : '确认彻底删除可删除项' }}
        </button>
      </div>
    </section>

    <TeachingPrepDocumentWorkspace
      :title="activeMaterial?.display_name ?? '原页预览'"
      :subtitle="activeUnit
        ? `第 ${activeUnit.unit_index} 页/张 · ${activePreviewLabel ?? (activeUnit.formula_review_required ? '公式待核对' : '原文可核对')}`
        : undefined"
      :preview-url="activeUnit?.preview_url"
      :empty-message="workbench.catalog.saveState === 'saving'
        ? '正在复制并解析资料，大文件需要一些时间。'
        : '选择左侧资料后在这里核对原页；若解析失败，上方会显示原因。'"
      @preview-loaded="refreshRenderedPreview"
    >
      <template #rail>
        <section v-if="activeProposal?.payload.tree.length" class="tp-proposal-tree">
          <p class="tp-eyebrow">模型建议目录</p>
          <h2>待确认课时树</h2>
          <details
            v-for="chapter in activeProposal.payload.tree"
            :key="chapter.key"
            open
          >
            <summary>{{ chapter.title }}</summary>
            <div v-for="section in chapter.sections" :key="section.key">
              <strong>{{ section.title }}</strong>
              <button
                v-for="lesson in section.lessons"
                :key="lesson.key"
                type="button"
                :class="{ 'is-selected': reviewLessonRef === `proposal:${lesson.key}` }"
                @click="reviewLessonRef = `proposal:${lesson.key}`"
              >
                <span>{{ lesson.title }}</span>
                <small>{{ lesson.duration_minutes }} 分钟</small>
              </button>
            </div>
          </details>
        </section>
        <h2>资料版本</h2>
        <div v-if="workbench.catalog.selectedSemester" class="tp-inline-actions">
          <button
            v-if="currentTermMaterials.length"
            class="is-danger"
            type="button"
            :disabled="batchArchiveRunning"
            @click="deleteCurrentTermMaterials"
          >
            {{ batchArchiveRunning
              ? '正在彻底删除…'
              : `删除本学期全部资料（${currentTermMaterials.length}）` }}
          </button>
          <button
            v-if="referencePptMaterials.length"
            type="button"
            :aria-expanded="showReferencePptMaterials"
            @click="showReferencePptMaterials = !showReferencePptMaterials"
          >
            {{ showReferencePptMaterials
              ? '收起参考 PPT'
              : `展开参考 PPT（${referencePptMaterials.length}）` }}
          </button>
          <button
            v-if="referencePptMaterials.length"
            class="is-danger"
            type="button"
            :disabled="batchArchiveRunning"
            @click="deleteReferencePptMaterials"
          >
            删除当前范围参考 PPT（{{ referencePptMaterials.length }}）
          </button>
        </div>
        <p v-if="activeMaterials.length === 0" class="tp-muted">
          尚未导入教材、教辅或课件。
        </p>
        <article
          v-for="item in visibleMaterials"
          :key="item.id"
          class="tp-material-card"
          :class="{ 'is-selected': item.id === workbench.catalog.selectedMaterialId }"
        >
          <button class="tp-rail-item" type="button" @click="openMaterial(item)">
            <strong>{{ item.display_name }}</strong>
            <small>{{ item.material_type.toUpperCase() }} · {{ item.unit_count ?? 0 }} 页/张</small>
            <span>{{ item.availability === 'available' ? '可用' : '需重新定位' }}</span>
            <span v-if="semesterRecordFor(item)" class="tp-source-tag">
              {{ roleLabel(semesterRecordFor(item)!.material_role) }}
              <template v-if="semesterRecordFor(item)!.workbook_volume">
                · {{ semesterRecordFor(item)!.workbook_series }} {{ semesterRecordFor(item)!.workbook_volume }} 本
              </template>
              · {{ semesterRecordFor(item)!.is_active ? '已加入本学期' : '未加入本学期' }}
            </span>
            <span
              v-if="parseJobForMaterial(item) || item.parse_expected_unit_count"
              class="tp-material-card__progress"
            >
              <progress
                :value="Math.round((parseJobForMaterial(item)?.progress ?? 0) * 100)"
                max="100"
              />
              <small>{{ materialProgressLabel(item) }}</small>
            </span>
          </button>
          <button
            v-if="materialNeedsContinue(item)"
            class="tp-material-card__continue"
            type="button"
            @click="continueMaterial(item)"
          >
            继续处理未完成页面
          </button>
          <div v-if="!semesterRecordFor(item) && workbench.catalog.selectedSemester" class="tp-material-card__attach">
            <select v-model="existingRoleDrafts[item.id]">
              <option :value="undefined">选择资料角色</option>
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
            <template v-if="existingRoleDrafts[item.id] === 'homework_workbook'">
              <input v-model="workbookSeriesDrafts[item.id]" type="text" placeholder="教辅套组名称">
              <select v-model="workbookVolumeDrafts[item.id]">
                <option value="A">A 本</option><option value="B">B 本</option>
              </select>
            </template>
            <button
              type="button"
              :disabled="!existingRoleDrafts[item.id] || attachingMaterialId === item.id"
              @click="attachExistingMaterial(item)"
            >
              {{ attachingMaterialId === item.id ? '正在加入…' : '加入本学期并选中' }}
            </button>
          </div>
          <details class="tp-material-card__manage">
            <summary>管理资料</summary>
            <template v-if="semesterRecordFor(item)">
              <select v-model="existingRoleDrafts[item.id]">
                <option :value="undefined">{{ roleLabel(semesterRecordFor(item)!.material_role) }}</option>
                <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">{{ role.label }}</option>
              </select>
              <template v-if="(existingRoleDrafts[item.id] ?? semesterRecordFor(item)!.material_role) === 'homework_workbook'">
                <input v-model="workbookSeriesDrafts[item.id]" type="text" :placeholder="semesterRecordFor(item)!.workbook_series ?? '教辅套组名称'">
                <select v-model="workbookVolumeDrafts[item.id]">
                  <option value="A">A 本</option><option value="B">B 本</option>
                </select>
              </template>
            </template>
            <div class="tp-inline-actions">
              <button type="button" @click="renameMaterial(item)">重命名</button>
              <button v-if="semesterRecordFor(item)" type="button" @click="updateExistingRole(item)">保存角色</button>
              <button v-if="semesterRecordFor(item)?.is_active" type="button" @click="removeFromSemester(item)">移出本学期</button>
              <button v-else-if="semesterRecordFor(item)" type="button" @click="restoreToSemester(item)">恢复到本学期</button>
              <button class="is-danger" type="button" @click="deleteMaterial(item)">彻底删除</button>
            </div>
          </details>
        </article>
        <button
          v-if="curriculumScope.selectedVolumeId && otherTermMaterials.length"
          class="tp-button tp-button--secondary"
          type="button"
          :aria-expanded="showOtherTermMaterials"
          @click="showOtherTermMaterials = !showOtherTermMaterials"
        >
          {{ showOtherTermMaterials
            ? '收起其他学期或未归类资料'
            : `展开其他学期或未归类资料（${otherTermMaterials.length}）` }}
        </button>
      </template>
      <template #toolbar>
        <div class="tp-page-picker" aria-label="原页导航">
          <button
            type="button"
            aria-label="上一页"
            :disabled="!activeUnit || activeUnit.unit_index <= 1"
            @click="stepPage(-1)"
          >
            ←
          </button>
          <label>
            第
            <input
              v-model.number="pageInput"
              type="number"
              min="1"
              :max="totalPages || 1"
              aria-label="当前页码"
              @change="goToPage()"
              @keydown.enter.prevent="goToPage()"
            >
            页 / 共 {{ totalPages }} 页
          </label>
          <button
            type="button"
            aria-label="下一页"
            :disabled="!activeUnit || activeUnit.unit_index >= totalPages"
            @click="stepPage(1)"
          >
            →
          </button>
          <span v-if="printedPageNumber" class="tp-printed-page">
            书上页码 {{ printedPageNumber }}
          </span>
        </div>
      </template>
      <template #inspector>
        <h2>按课时审核页段</h2>
        <p v-if="!activeProposal" class="tp-inline-guidance">
          暂无待审核建议。可在上方生成目录建议，或在下方人工关联当前资料页段。
        </p>
        <template v-else>
          <label class="tp-field">
            当前建议课时
            <select v-model="reviewLessonRef">
              <option
                v-for="group in mappingGroups"
                :key="group.lessonRef"
                :value="group.lessonRef"
              >
                {{ group.title }} · {{ group.mappings.length }} 个页段
              </option>
            </select>
          </label>
          <div v-if="activeMappingGroup" class="tp-mapping-group-summary">
            <strong>{{ activeMappingGroup.title }}</strong>
            <small>{{ activeMappingGroup.path }}</small>
            <span>{{ activeMappingGroup.pendingCount }} 条待确认</span>
          </div>
          <details v-if="activeProposal.payload.uncertainties.length" class="tp-mapping-uncertainties">
            <summary>
              {{ activeProposal.payload.generation_source === 'local_reference_ppt_names' ? '本机识别疑点' : '模型标记的疑点' }}
              （{{ activeProposal.payload.uncertainties.length }}）
            </summary>
            <ul><li v-for="item in activeProposal.payload.uncertainties" :key="item">{{ item }}</li></ul>
          </details>
        </template>
        <article
          v-for="item in activeMappingGroup?.mappings ?? []"
          :key="item.mapping_id"
          class="tp-mapping-review"
          :class="`is-${item.decision}`"
        >
          <header>
            <button type="button" @click="openMappingItem(item)">
              {{ item.start_unit }}—{{ item.end_unit }} 页/张 · 查看原页
            </button>
            <span>{{ item.decision === 'pending' ? '待处理' : item.decision }}</span>
          </header>
          <div class="tp-mapping-basis">
            <strong>{{ activeProposal?.payload.generation_source === 'local_reference_ppt_names' ? '本机命名依据' : '模型映射依据' }}</strong>
            <p>
              {{ item.basis ?? item.decision_reason ?? (activeProposal?.payload.generation_source === 'local_reference_ppt_names'
                ? '依据文件夹层级、文件编号、课时标记和首屏标题提出。'
                : '旧建议未保存模型依据，请结合原页人工复核。') }}
            </p>
            <div v-if="item.evidence_refs?.length" class="tp-mapping-evidence-list">
              <span>证据</span>
              <button
                v-for="evidenceId in item.evidence_refs"
                :key="evidenceId"
                type="button"
                :disabled="evidenceDisplay(evidenceId).unit === null"
                @click="openEvidence(evidenceId)"
              >
                {{ evidenceDisplay(evidenceId).label }}
              </button>
            </div>
          </div>
          <label>
            目标课时
            <select v-model="editFor(item).lessonRef">
              <option
                v-for="lesson in mappingLessonOptions"
                :key="lesson.value"
                :value="lesson.value"
              >
                {{ lesson.title }} · {{ lesson.path }}
              </option>
            </select>
          </label>
          <div class="tp-field-pair">
            <label>起始页<input v-model.number="editFor(item).startUnit" type="number" min="1"></label>
            <label>结束页<input v-model.number="editFor(item).endUnit" type="number" min="1"></label>
          </div>
          <label>教师修改说明（可选）<input v-model="editFor(item).reason" type="text"></label>
          <div class="tp-inline-actions">
            <button type="button" @click="decideMapping(item, 'accepted')">接受</button>
            <button type="button" @click="decideMapping(item, 'modified')">保存修改</button>
            <button class="is-danger" type="button" @click="decideMapping(item, 'rejected')">拒绝</button>
          </div>
        </article>

        <section class="tp-manual-mapping">
          <h3>人工关联当前页段</h3>
          <p class="tp-muted">模型不可用时，也可以把当前资料的连续页段直接关联到现有课时。</p>
          <p v-if="!activeSemesterRecord" class="tp-error-text">
            当前资料尚未加入本学期，请先在左侧选择角色并加入。
          </p>
          <p
            v-else-if="!activeMaterialReadyForMapping"
            class="tp-error-text"
          >
            当前资料仍在处理或需要续跑；完整发布前不能保存正式课时关联。
          </p>
          <p v-if="!workbench.catalog.lessonNodes.some(item => item.node_type === 'lesson' && item.is_active)" class="tp-error-text">
            还没有可关联课时，请先返回“个人课时树”建立课时。
          </p>
          <label class="tp-field">
            目标课时
            <select v-model="manualMapping.lessonId">
              <option value="">请选择课时</option>
              <option
                v-for="lesson in workbench.catalog.lessonNodes.filter(item => item.node_type === 'lesson' && item.is_active)"
                :key="lesson.id"
                :value="lesson.id"
              >
                {{ lesson.title }}
              </option>
            </select>
          </label>
          <label class="tp-field">
            资料用途
            <select v-model="manualMapping.purpose">
              <option v-for="purpose in LINK_PURPOSES" :key="purpose.value" :value="purpose.value">
                {{ purpose.label }}
              </option>
            </select>
          </label>
          <div class="tp-field-pair">
            <label class="tp-field">起始页<input v-model.number="manualMapping.startUnit" type="number" min="1"></label>
            <label class="tp-field">结束页<input v-model.number="manualMapping.endUnit" type="number" min="1"></label>
          </div>
          <label class="tp-field">
            备注（可选）
            <input v-model="manualMapping.note" type="text" placeholder="例如：第一课时新授范围">
          </label>
          <button
            class="tp-button tp-button--primary"
            type="button"
            :disabled="(
              !activeMaterialReadyForMapping
              || !manualMapping.lessonId
              || workbench.catalog.materialUnits.length === 0
              || workbench.catalog.saveState === 'saving'
            )"
            @click="saveManualMapping"
          >
            保存人工关联
          </button>
        </section>
      </template>
    </TeachingPrepDocumentWorkspace>

    <TeachingPrepStickyActions
      :state="mappingJobError || mappingResultUnknown
        ? 'error'
        : mappingJobBusy || workbench.catalog.saveState === 'saving'
          ? 'saving'
          : allMappingsDecided
            ? 'saved'
            : 'dirty'"
      :message="mappingMessage"
    >
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.openStage('select')">
        返回选课时
      </button>
      <button
        class="tp-button tp-button--secondary"
        type="button"
        @click="acceptLocalHighConfidenceMappings"
      >
        确认高置信建议（{{ pendingLocalHighConfidenceCount }} 条）
      </button>
      <button
        class="tp-button tp-button--secondary"
        type="button"
        @click="acceptAllPendingMappings"
      >
        {{ bulkReviewRunning ? '正在接受建议…' : `接受全部剩余建议（${pendingMappingCount}）` }}
      </button>
      <button
        class="tp-button tp-button--primary"
        type="button"
        aria-describedby="mapping-gate"
        @click="applyProposal"
      >
        应用全部接受项
      </button>
      <span id="mapping-gate" class="tp-visually-hidden">
        {{ allMappingsDecided ? '可以应用' : '请先决定每一条映射建议' }}
      </span>
    </TeachingPrepStickyActions>
  </section>
</template>
