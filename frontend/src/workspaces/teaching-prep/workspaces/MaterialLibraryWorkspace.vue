<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import type { JobResponse } from '../../../api/jobs'
import type {
  MaterialLinkPurpose,
  MaterialVersion,
  SemesterMappingProposalRange,
  SemesterMaterialRecord,
  SemesterMaterialRole,
} from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../aiAdoption'
import TeachingPrepDocumentWorkspace from '../components/TeachingPrepDocumentWorkspace.vue'
import TeachingPrepStickyActions from '../components/TeachingPrepStickyActions.vue'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
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
const reviewLessonRef = ref('')
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
}

const pendingImports = ref<PendingMaterialImport[]>([])

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
const activeMaterials = computed(() => workbench.catalog.materials.filter(item => !item.source_archived_at))
const archivedMaterials = computed(() => workbench.catalog.materials.filter(item => Boolean(item.source_archived_at)))
const activeMaterial = computed(
  () => workbench.catalog.materials.find(
    item => item.id === workbench.catalog.selectedMaterialId,
  ) ?? null,
)
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
const eligibleSemesterMaterials = computed(
  () => workbench.catalog.semesterMaterials.filter(item => (
    item.is_active
    && item.parse_status === 'parsed'
    && !item.has_unparsed_update
    && (item.current_unit_count ?? 0) > 0
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
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.semester_mapping'
    && task.source_ref.kind === 'semester'
    && task.source_ref.id === semesterId
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

function openMappingRange(startUnit: number): void {
  goToPage(startUnit)
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
  return name.toLowerCase().endsWith('.pptx')
    ? 'reference_ppt'
    : 'supplement'
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
    })
  }
  if (files.length > 0) {
    mappingMessage.value = `已加入 ${files.length} 个文件，请逐份确认资料角色后开始导入。`
  }
}

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
    mappingMessage.value = failed > 0
      ? `已完成 ${completed} 份，${failed} 份未完成；其余资料已保留，可单独重试失败项。`
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
    mappingMessage.value = `已把“${item.display_name}”作为${roleLabel(role)}加入本学期。`
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

async function archiveMaterial(item: MaterialVersion): Promise<void> {
  await workbench.catalog.updateMaterialSource(item, { archived: true })
  mappingMessage.value = '资料已移入回收区，可随时恢复；原文件和历史引用没有删除。'
}

async function restoreMaterial(item: MaterialVersion): Promise<void> {
  await workbench.catalog.updateMaterialSource(item, { archived: false })
  mappingMessage.value = '资料已从回收区恢复。'
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
  if (!activeProposal.value || !allMappingsDecided.value) return
  try {
    const proposal = activeProposal.value
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
      mappingMessage.value = '正式映射已写入，并已形成 AI 采用回执。'
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        workbench.refreshCurrentWorkspace(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        mappingMessage.value = '正式映射已写入且已有回执；页面状态暂未刷新，请刷新页面。'
      }
    } else {
      await workbench.catalog.applySemesterMapping(proposal)
      await workbench.refreshCurrentWorkspace()
      mappingMessage.value = '全部接受项已写入正式映射。'
    }
  } catch {
    mappingMessage.value = workbench.catalog.errorMessage
      || '正式映射尚未确认；已保存的逐条决定仍保留，请直接重试。'
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
    </header>

    <div
      v-if="workbench.catalog.errorMessage"
      class="tp-global-notice"
      role="alert"
    >
      <strong>资料处理没有完成</strong>
      <span>{{ workbench.catalog.errorMessage }}</span>
    </div>

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

    <section class="tp-section-block tp-directory-actions">
      <div>
        <p class="tp-eyebrow">课时目录整理</p>
        <h2>一次整理一份已解析资料</h2>
        <p>先检查会参考哪些页，再由模型提出待审核建议；模型不会直接修改正式课时树。</p>
      </div>
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
      <div class="tp-inline-actions">
        <button
          type="button"
          :disabled="!activeSemesterRecord || workbench.catalog.loadState === 'loading'"
          @click="prepareSemesterMapping()"
        >
          重新检查
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
                : '生成待确认建议' }}
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
          应用重启后无法确认模型结果；为避免重复费用，本页不提供重新生成入口。
        </p>
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
            未发送全部逐页正文，只发送目录线索与抽样正文锚点。
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

    <TeachingPrepDocumentWorkspace
      :title="activeMaterial?.display_name ?? '原页预览'"
      :subtitle="activeUnit ? `第 ${activeUnit.unit_index} 页/张 · ${activeUnit.formula_review_required ? '公式待核对' : '原文可核对'}` : undefined"
      :preview-url="activeUnit?.preview_url"
      :empty-message="workbench.catalog.saveState === 'saving'
        ? '正在复制并解析资料，大文件需要一些时间。'
        : '选择左侧资料后在这里核对原页；若解析失败，上方会显示原因。'"
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
        <p v-if="activeMaterials.length === 0" class="tp-muted">
          尚未导入教材、教辅或课件。
        </p>
        <article
          v-for="item in activeMaterials"
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
              {{ attachingMaterialId === item.id ? '正在加入…' : '加入本学期' }}
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
              <button v-if="!semesterRecordFor(item)?.is_active" class="is-danger" type="button" @click="archiveMaterial(item)">移入回收区</button>
            </div>
          </details>
        </article>
        <details v-if="archivedMaterials.length" class="tp-archive-list">
          <summary>回收区（{{ archivedMaterials.length }}）</summary>
          <article v-for="item in archivedMaterials" :key="item.id" class="tp-material-card is-archived">
            <strong>{{ item.display_name }}</strong>
            <small>保留原文件与历史引用</small>
            <button type="button" @click="restoreMaterial(item)">恢复资料</button>
          </article>
        </details>
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
            <summary>模型标记的疑点（{{ activeProposal.payload.uncertainties.length }}）</summary>
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
            <button type="button" @click="openMappingRange(item.start_unit)">
              {{ item.start_unit }}—{{ item.end_unit }} 页/张 · 查看原页
            </button>
            <span>{{ item.decision === 'pending' ? '待处理' : item.decision }}</span>
          </header>
          <div class="tp-mapping-basis">
            <strong>模型映射依据</strong>
            <p>{{ item.basis ?? '旧建议未保存模型依据，请结合原页人工复核。' }}</p>
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
        class="tp-button tp-button--primary"
        type="button"
        :disabled="!allMappingsDecided"
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
