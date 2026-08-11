<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type {
  MaterialLinkPurpose,
  SemesterMappingProposalRange,
  SemesterMaterialRecord,
} from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../../aiAdoption'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { useTeachingPrepRouteStateContext } from '../../workbench/routeContext'
import { LINK_PURPOSES, roleLabel, useSemesterRecordLookup } from './libraryShared'

const catalog = useTeachingPrepCatalogStore()
const aiTasks = useWorkspaceAITaskStore()
const routeState = useTeachingPrepRouteStateContext()
const semesterRecordFor = useSemesterRecordLookup()

const emit = defineEmits<{ notice: [message: string] }>()

const includeReferencePptInDirectory = ref(false)
const reviewLessonRef = ref('')
const bulkReviewRunning = ref(false)
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
let refreshedMappingTaskRevision = ''

const mappingClock = ref(Date.now())
let mappingClockTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  mappingClockTimer = globalThis.setInterval(() => {
    mappingClock.value = Date.now()
  }, 1_000)
})
onBeforeUnmount(() => {
  if (mappingClockTimer !== null) globalThis.clearInterval(mappingClockTimer)
})

const activeMaterial = computed(
  () => catalog.materials.find(item => item.id === catalog.selectedMaterialId) ?? null,
)
const activeSemesterRecord = computed(() => (
  activeMaterial.value ? semesterRecordFor(activeMaterial.value) : null
))
const activeProposal = computed(() => catalog.currentSemesterMappingProposal)
const currentMappingPreflight = computed(() => catalog.currentSemesterMappingPreflight)
const currentMappingJob = computed(() => catalog.currentSemesterMappingJob)
const currentMappingJobSyncError = computed(() => catalog.currentSemesterMappingJobSyncError)

const eligibleSemesterMaterials = computed(
  () => catalog.semesterMaterials.filter(item => (
    item.is_active
    && item.parse_status === 'parsed'
    && !item.has_unparsed_update
    && (item.current_unit_count ?? 0) > 0
    && (includeReferencePptInDirectory.value || !isPptSemesterRecord(item))
  )),
)
const selectedDirectoryMaterialId = computed(() => (
  eligibleSemesterMaterials.value.some(record => (
    record.current_material_version_id === catalog.selectedMaterialId
  ))
    ? catalog.selectedMaterialId ?? ''
    : ''
))
const currentMappingTask = computed(() => {
  const semesterId = catalog.selectedSemester?.id
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
const activeMaterialReadyForMapping = computed(() => (
  activeMaterial.value !== null
  && activeSemesterRecord.value?.parse_status === 'parsed'
  && !activeSemesterRecord.value.has_unparsed_update
  && ['ready', 'scanned_no_text'].includes(activeMaterial.value.inspection_status)
))

function isPptSemesterRecord(record: SemesterMaterialRecord): boolean {
  const material = catalog.materials.find(item => (
    item.id === record.current_material_version_id
  ))
  return record.material_role === 'reference_ppt'
    || material?.material_type === 'pptx'
    || /\.pptx?$/i.test(record.safe_filename || record.display_name)
}

function notice(message: string): void {
  emit('notice', message)
}

watch(
  () => {
    const record = activeSemesterRecord.value
    return [
      catalog.selectedSemester?.id ?? '',
      catalog.selectedMaterialId ?? '',
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

watch(
  () => currentMappingTask.value ? `${currentMappingTask.value.task_id}:${currentMappingTask.value.revision}` : '',
  async (revision) => {
    const task = currentMappingTask.value
    if (!revision || revision === refreshedMappingTaskRevision || !task) return
    if (!['proposal_ready', 'needs_input'].includes(task.status)) return
    refreshedMappingTaskRevision = revision
    await catalog.load()
  },
)

watch(
  () => catalog.materialUnits,
  units => {
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
  () => catalog.lessonNodes,
  nodes => {
    const lessons = nodes.filter(item => item.node_type === 'lesson' && item.is_active)
    if (!lessons.some(item => item.id === manualMapping.lessonId)) {
      manualMapping.lessonId = lessons[0]?.id ?? ''
    }
  },
  { immediate: true },
)

/* ===== 建议分组与逐条审核 ===== */

const proposalLessonOptions = computed(() => (
  activeProposal.value?.payload.tree.flatMap(chapter => (
    chapter.sections.flatMap(section => section.lessons.map(lesson => ({
      value: `proposal:${lesson.key}`,
      title: lesson.title,
      path: `${chapter.title} / ${section.title}`,
    })))
  )) ?? []
))
const formalLessonOptions = computed(() => catalog.lessonNodes
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
  return mappings.length > 0 && mappings.every(item => item.decision !== 'pending')
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

watch(
  mappingGroups,
  groups => {
    if (!groups.some(group => group.lessonRef === reviewLessonRef.value)) {
      reviewLessonRef.value = groups[0]?.lessonRef ?? ''
    }
  },
  { immediate: true },
)

function evidenceDisplay(evidenceId: string): string {
  const evidence = proposalEvidenceById.value.get(evidenceId)
  if (!evidence) return `${evidenceId}（旧建议未保存证据详情）`
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
    return `推断范围：${title || evidenceId} · PDF ${startUnit}—${endUnit} 页`
  }
  if (unitIndex !== null) {
    return `正文锚点：PDF 第 ${unitIndex} 页 · ${title || excerpt || evidenceId}`
  }
  return `目录：${title || evidenceId}${printedPage === null ? '' : ` · 书上第 ${printedPage} 页`}${sourceUnit === null ? '' : ` · 目录位于 PDF 第 ${sourceUnit} 页`}`
}

/* ===== 任务状态（semesterMappingJobs 与 AI task 双源合并展示） ===== */

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
const mappingJobProposalRejected = computed(() => {
  const job = currentMappingJob.value
  const taskProposalId = currentMappingTask.value?.proposal_ref_id ?? null
  const proposalId = job && typeof job.result.proposal_id === 'string'
    ? job.result.proposal_id
    : null
  const operationId = job ? job.result.operation_id ?? job.payload.operation_id : null
  const sourceState = job
    ? job.result.source_state_sha256 ?? job.payload.source_state_sha256
    : null
  return catalog.semesterMappingProposals.some(proposal => (
    proposal.status === 'rejected'
    && (
      (taskProposalId !== null && proposal.id === taskProposalId)
      || (proposalId !== null && proposal.id === proposalId)
      || (
        operationId !== null
        && sourceState !== null
        && proposal.operation_id === operationId
        && proposal.source_state_sha256 === sourceState
      )
    )
  ))
})
const mappingAwaitingProposal = computed(() => (
  (currentMappingJob.value?.status === 'succeeded'
    || ['proposal_ready', 'needs_input'].includes(currentMappingTask.value?.status ?? ''))
  && !mappingJobHasDurableProposal.value
  && !mappingJobProposalRejected.value
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
  currentMappingJob.value?.error || currentMappingJobSyncError.value?.message || ''
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
  if (mappingJobProposalRejected.value) return '旧建议已放弃，可重新判断'
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
  const hasLesson = catalog.lessonNodes.some(item => (
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
  && (
    currentMappingJob.value?.status !== 'succeeded'
    || mappingJobProposalRejected.value
  )
  && catalog.saveState !== 'saving'
))
const mappingStatusTone = computed(() => (
  mappingJobError.value || mappingResultUnknown.value ? 'is-error'
    : mappingJobHasDurableProposal.value ? 'is-success'
      : mappingJobBusy.value || mappingTaskBusy.value || mappingAwaitingProposal.value ? 'is-running' : ''
))

async function selectDirectoryMaterial(event: Event): Promise<void> {
  const materialId = (event.target as HTMLSelectElement).value
  const material = catalog.materials.find(item => item.id === materialId)
  if (!material) return
  notice((material.unit_count ?? 0) > 0 ? '正在打开已保存的页级目录…' : '正在本机解析资料，大文件需要一些时间…')
  try {
    await catalog.openMaterial(material)
    notice(`已选中「${material.display_name}」。`)
  } catch {
    notice(catalog.errorMessage || '资料没有完成解析，请保留原文件后重试。')
  }
}

async function prepareSemesterMapping(silent = false): Promise<void> {
  const record = activeSemesterRecord.value
  if (!record) return
  if (!silent) notice('正在检查本次目录整理范围，不会自动调用模型…')
  try {
    await catalog.prepareSemesterMapping([record.id])
    if (
      catalog.selectedMaterialId !== record.current_material_version_id
      || catalog.currentSemesterMappingPreflight === null
    ) return
    if (!silent) notice('发送范围已准备好；确认后可生成一份待审核的课时目录建议。')
  } catch {
    if (catalog.selectedMaterialId !== record.current_material_version_id) return
    notice(catalog.errorMessage || '发送范围检查未完成。')
  }
}

async function generateSemesterMapping(): Promise<void> {
  const record = activeSemesterRecord.value
  const semester = catalog.selectedSemester
  const preflight = currentMappingPreflight.value
  if (!record || !semester || !preflight) return
  notice('统一 AI 任务正在登记；本次最多调用模型一次，不会自动重试…')
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
    notice('任务已进入统一任务抽屉；可离开页面，重启后只恢复本地结果。')
  } catch {
    notice('任务没有成功登记，系统不会自动重新发送。')
  }
}

async function retrySemesterMapping(): Promise<void> {
  if (!mappingRetryAvailable.value) return
  await prepareSemesterMapping()
  if (catalog.currentSemesterMappingPreflight) {
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
    notice('旧结果已放弃。请重新检查发送范围；只有再次点击“交给 AI”才会产生新调用。')
  } catch {
    notice('旧结果尚未放弃，请保留当前页面后重试。')
  }
}

async function rejectMappingProposalForRegeneration(): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal) return
  if (!window.confirm(
    '确定放弃这份目录建议吗？旧建议和审核记录会保留为“已放弃”，本操作不调用模型。之后只有再次点击“交给 AI 整理课时树”才会产生一次新调用和相应费用。',
  )) return
  try {
    const updated = await teachingPrepCatalogApi.rejectSemesterMappingProposal(proposal)
    const index = catalog.semesterMappingProposals.findIndex(item => item.id === updated.id)
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
    notice('旧建议已放弃。本操作没有调用模型；请重新检查发送范围，再决定是否生成新建议。')
    await prepareSemesterMapping(true)
  } catch {
    notice('旧建议没有成功放弃，请刷新后核对状态。')
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
  notice('正在保存本条决定…')
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
    const index = catalog.semesterMappingProposals.findIndex(
      proposalItem => proposalItem.id === updated.id,
    )
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
    notice(decision === 'rejected'
      ? '已拒绝本条建议，原始建议仍保留。'
      : decision === 'modified'
        ? '已保存教师修改，原始建议仍可追溯。'
        : '已接受本条建议。')
  } catch {
    notice('本条建议没有保存，请刷新后核对版本。')
  }
}

async function applyProposal(): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal || proposal.payload.mappings.length === 0) {
    notice('请先取得建议并逐条完成决定，再应用到正式课时树。')
    return
  }
  if (!allMappingsDecided.value) {
    notice(`还有 ${pendingMappingCount.value} 条建议尚未决定；全部处理后才能应用。`)
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
      notice('正式映射已写入且已有回执。')
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        catalog.load(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        notice('正式映射已写入且已有回执；页面状态暂未刷新，请刷新页面。')
      }
    } else {
      await catalog.applySemesterMapping(proposal)
      await catalog.load()
      notice('全部接受项已写入正式映射。')
    }
    const firstLesson = catalog.lessonNodes.find(
      item => item.node_type === 'lesson' && item.is_active,
    )
    if (firstLesson) await routeState.openLesson(firstLesson.id)
  } catch {
    notice(catalog.errorMessage || '正式映射尚未确认；已保存的逐条决定仍保留，请直接重试。')
  }
}

async function acceptLocalHighConfidenceMappings(): Promise<void> {
  const proposal = activeProposal.value
  if (!proposal || pendingLocalHighConfidenceCount.value === 0) {
    notice('当前没有可直接确认的高置信建议，请查看待审核建议或先生成目录建议。')
    return
  }
  notice('正在批量确认本机高置信课件命名建议…')
  try {
    const updated = await teachingPrepCatalogApi.acceptLocalReferencePptMappings(proposal)
    const index = catalog.semesterMappingProposals.findIndex(item => item.id === updated.id)
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
    notice('高置信建议已确认；请继续核对剩余少量疑点。')
  } catch {
    notice('批量确认没有保存，请刷新后重试。')
  }
}

async function acceptAllPendingMappings(): Promise<void> {
  if (bulkReviewRunning.value) {
    notice('正在接受建议，请等待当前保存完成。')
    return
  }
  if (pendingMappingCount.value === 0) {
    notice('当前没有待接受的映射建议；如已逐条决定，可直接应用。')
    return
  }
  const count = pendingMappingCount.value
  if (!window.confirm(`确认接受剩余 ${count} 条课时目录建议吗？接受后仍需点击“应用全部接受项”才会写入正式课时树。`)) return
  bulkReviewRunning.value = true
  notice(`正在接受剩余 ${count} 条建议…`)
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
    notice(`已接受 ${count} 条建议；现在可以应用到正式课时树。`)
  } finally {
    bulkReviewRunning.value = false
  }
}

async function saveManualMapping(): Promise<void> {
  const material = activeMaterial.value
  const record = activeSemesterRecord.value
  const lesson = catalog.lessonNodes.find(
    item => item.id === manualMapping.lessonId && item.node_type === 'lesson',
  )
  const maximum = catalog.materialUnits.at(-1)?.unit_index ?? 0
  if (!material || !record || !activeMaterialReadyForMapping.value || !lesson || maximum === 0) {
    notice('请先在上方选择已加入本学期且解析完成的资料，并选择现有课时。')
    return
  }
  if (
    manualMapping.startUnit < 1
    || manualMapping.endUnit < manualMapping.startUnit
    || manualMapping.endUnit > maximum
  ) {
    notice(`页段应在 1—${maximum} 之间，且结束页不能早于起始页。`)
    return
  }
  notice('正在保存人工课时页段关联…')
  try {
    if (catalog.selectedLessonId !== lesson.id) {
      await catalog.selectLesson(lesson)
    }
    await catalog.confirmMaterialRanges(
      [{ start: manualMapping.startUnit, end: manualMapping.endUnit }],
      manualMapping.purpose,
      manualMapping.note.trim() || null,
    )
    notice(`已把第 ${manualMapping.startUnit}—${manualMapping.endUnit} 页/张关联到“${lesson.title}”。`)
    manualMapping.note = ''
  } catch {
    notice(catalog.errorMessage || '人工关联没有保存。')
  }
}

const decisionLabels: Record<string, string> = {
  pending: '待处理',
  accepted: '已接受',
  modified: '已修改',
  rejected: '已拒绝',
}
const confidenceLabels: Record<string, string> = {
  high: '置信度 高',
  medium: '置信度 中',
  low: '置信度 低',
}
</script>

<template>
  <section class="tp-panel" aria-label="AI 整理课时树">
    <div class="tp-panel__head">
      <h2>③ AI 整理课时树</h2>
      <AppButton
        v-if="mappingRetryAvailable"
        variant="secondary"
        :disabled="catalog.saveState === 'saving'"
        @click="retrySemesterMapping"
      >
        重新检查并生成新建议
      </AppButton>
    </div>
    <div class="tp-panel__body">
      <div class="tp-form-line">
        <label class="tp-field">
          本次资料
          <select
            data-testid="mapping-directory-material"
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
        <AppButton
          variant="secondary"
          :disabled="!activeSemesterRecord || catalog.loadState === 'loading'"
          @click="prepareSemesterMapping()"
        >
          检查发送范围
        </AppButton>
        <AppButton
          variant="primary"
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
        </AppButton>
      </div>
      <p v-if="currentMappingPreflight?.model_available" class="tp-muted">
        本机无法预估金额；由当前模型服务商按实际用量计费。点击“交给 AI 整理课时树”即确认本次单次调用。
      </p>

      <div
        v-if="activeSemesterRecord"
        class="tp-mapping-job-status"
        :class="mappingStatusTone"
        role="status"
        aria-live="polite"
      >
        <div class="tp-mapping-job-status__heading">
          <div>
            <StatusBadge
              :tone="mappingStatusTone === 'is-error' ? 'danger' : mappingStatusTone === 'is-success' ? 'success' : mappingStatusTone === 'is-running' ? 'info' : 'neutral'"
              :label="mappingStageLabel"
            />
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
        <p v-if="currentMappingJob?.detail && !mappingJobHasDurableProposal" class="tp-muted">{{ currentMappingJob.detail }}</p>
        <p v-if="catalog.currentSemesterMappingJobRecovered" class="tp-muted">
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
        <AppButton
          v-if="currentMappingTask?.status === 'result_unknown'"
          variant="secondary"
          @click="discardUnknownMappingResult"
        >
          放弃旧结果，重新准备
        </AppButton>
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
            仍可在下方把页段人工关联到现有课时。
          </span>
        </div>
      </details>
      <p v-else-if="eligibleSemesterMaterials.length === 0" class="tp-muted">
        暂无已解析的本学期资料。先在上方导入，或把其他资料加入本学期。
      </p>
    </div>

    <div v-if="activeProposal" class="tp-panel__body tp-mapping-review-area">
      <div class="tp-banner tp-banner--ai">
        AI 根据资料目录建议了「资料页 → 课时」对应关系，请逐条确认；全部决定后才能应用到正式课时树。
      </div>
      <div class="tp-inline-actions">
        <label class="tp-field tp-field--inline">
          当前建议课时
          <select v-model="reviewLessonRef">
            <option v-for="group in mappingGroups" :key="group.lessonRef" :value="group.lessonRef">
              {{ group.title }} · {{ group.mappings.length }} 个页段
            </option>
          </select>
        </label>
        <AppButton variant="ghost" @click="rejectMappingProposalForRegeneration">放弃本份建议，重新判断</AppButton>
      </div>
      <div v-if="activeMappingGroup" class="tp-mapping-group-summary">
        <strong>{{ activeMappingGroup.title }}</strong>
        <small>{{ activeMappingGroup.path }}</small>
        <StatusBadge
          :tone="activeMappingGroup.pendingCount ? 'warning' : 'success'"
          :label="activeMappingGroup.pendingCount ? `${activeMappingGroup.pendingCount} 条待确认` : '本组已决定'"
        />
      </div>
      <details v-if="activeProposal.payload.uncertainties.length" class="tp-mapping-uncertainties">
        <summary>
          {{ activeProposal.payload.generation_source === 'local_reference_ppt_names' ? '本机识别疑点' : '模型标记的疑点' }}
          （{{ activeProposal.payload.uncertainties.length }}）
        </summary>
        <ul><li v-for="item in activeProposal.payload.uncertainties" :key="item">{{ item }}</li></ul>
      </details>

      <article
        v-for="item in activeMappingGroup?.mappings ?? []"
        :key="item.mapping_id"
        class="tp-mapping-row"
        :class="`is-${item.decision}`"
      >
        <header>
          <strong>{{ item.start_unit }}—{{ item.end_unit }} 页/张</strong>
          <StatusBadge
            :tone="item.decision === 'pending' ? 'warning' : item.decision === 'rejected' ? 'neutral' : 'success'"
            :label="decisionLabels[item.decision] ?? item.decision"
          />
          <StatusBadge
            v-if="item.confidence"
            :tone="item.confidence === 'high' ? 'success' : item.confidence === 'medium' ? 'warning' : 'neutral'"
            :label="confidenceLabels[item.confidence] ?? item.confidence"
          />
        </header>
        <div class="tp-mapping-basis">
          <strong>{{ activeProposal.payload.generation_source === 'local_reference_ppt_names' ? '本机命名依据' : '模型映射依据' }}</strong>
          <p>
            {{ item.basis ?? item.decision_reason ?? (activeProposal.payload.generation_source === 'local_reference_ppt_names'
              ? '依据文件夹层级、文件编号、课时标记和首屏标题提出。'
              : '旧建议未保存模型依据，请结合原页人工复核。') }}
          </p>
          <div v-if="item.evidence_refs?.length" class="tp-mapping-evidence-list">
            <span>证据</span>
            <span v-for="evidenceId in item.evidence_refs" :key="evidenceId">{{ evidenceDisplay(evidenceId) }}</span>
          </div>
        </div>
        <label class="tp-field">
          目标课时
          <select v-model="editFor(item).lessonRef">
            <option v-for="lesson in mappingLessonOptions" :key="lesson.value" :value="lesson.value">
              {{ lesson.title }} · {{ lesson.path }}
            </option>
          </select>
        </label>
        <div class="tp-field-pair">
          <label class="tp-field">起始页<input v-model.number="editFor(item).startUnit" type="number" min="1"></label>
          <label class="tp-field">结束页<input v-model.number="editFor(item).endUnit" type="number" min="1"></label>
        </div>
        <label class="tp-field">教师修改说明（可选）<input v-model="editFor(item).reason" type="text"></label>
        <div class="tp-inline-actions">
          <AppButton variant="secondary" @click="decideMapping(item, 'accepted')">接受</AppButton>
          <AppButton variant="secondary" @click="decideMapping(item, 'modified')">保存修改</AppButton>
          <AppButton variant="ghost" class="tp-danger-text" @click="decideMapping(item, 'rejected')">拒绝</AppButton>
        </div>
      </article>

      <div class="tp-inline-actions tp-mapping-apply-bar">
        <AppButton variant="secondary" @click="acceptLocalHighConfidenceMappings">
          确认高置信建议（{{ pendingLocalHighConfidenceCount }} 条）
        </AppButton>
        <AppButton variant="secondary" :disabled="bulkReviewRunning" @click="acceptAllPendingMappings">
          {{ bulkReviewRunning ? '正在接受建议…' : `接受全部剩余建议（${pendingMappingCount}）` }}
        </AppButton>
        <AppButton
          variant="primary"
          data-testid="apply-semester-mapping"
          @click="applyProposal"
        >
          应用全部接受项
        </AppButton>
      </div>
    </div>

    <div class="tp-panel__body tp-manual-mapping">
      <h3>人工关联当前页段</h3>
      <p class="tp-muted">模型不可用时，也可以把当前资料的连续页段直接关联到现有课时。</p>
      <p v-if="!activeSemesterRecord" class="tp-error-text">
        当前资料尚未选中，请先在上方「本次资料」中选择已加入本学期的资料。
      </p>
      <p v-else-if="!activeMaterialReadyForMapping" class="tp-error-text">
        当前资料仍在处理或需要续跑；完整发布前不能保存正式课时关联。
      </p>
      <p v-if="!catalog.lessonNodes.some(item => item.node_type === 'lesson' && item.is_active)" class="tp-error-text">
        还没有可关联课时，请先返回备课首页建立课时。
      </p>
      <div class="tp-form-line">
        <label class="tp-field">
          目标课时
          <select v-model="manualMapping.lessonId">
            <option value="">请选择课时</option>
            <option
              v-for="lesson in catalog.lessonNodes.filter(item => item.node_type === 'lesson' && item.is_active)"
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
        <label class="tp-field">起始页<input v-model.number="manualMapping.startUnit" type="number" min="1"></label>
        <label class="tp-field">结束页<input v-model.number="manualMapping.endUnit" type="number" min="1"></label>
        <label class="tp-field">备注（可选）<input v-model="manualMapping.note" type="text" placeholder="例如：第一课时新授范围"></label>
        <AppButton
          variant="primary"
          :disabled="(
            !activeMaterialReadyForMapping
            || !manualMapping.lessonId
            || catalog.materialUnits.length === 0
            || catalog.saveState === 'saving'
          )"
          @click="saveManualMapping"
        >
          保存人工关联
        </AppButton>
      </div>
    </div>
  </section>
</template>
