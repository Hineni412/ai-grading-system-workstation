<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import StatusBadge from '../../../../components/design-system/StatusBadge.vue'
import type {
  MaterialLinkPurpose,
  MaterialVersion,
  SemesterMappingProposal,
  SemesterMappingProposalRange,
} from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { adoptTeachingPrepProposal } from '../../aiAdoption'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { LINK_PURPOSES, roleLabel } from './libraryShared'
import MaterialDetail from './MaterialDetail.vue'

const props = defineProps<{ materialId: string }>()
const emit = defineEmits<{ notice: [message: string] }>()

const catalog = useTeachingPrepCatalogStore()
const aiTasks = useWorkspaceAITaskStore()

const material = computed<MaterialVersion | null>(() => (
  catalog.materials.find(item => item.id === props.materialId) ?? null
))
const record = computed(() => (
  catalog.semesterMaterials.find(item => (
    item.current_material_version_id === props.materialId
  )) ?? null
))

const isExerciseBook = computed(() => (
  ['exercise_workbook', 'homework_workbook'].includes(record.value?.material_role ?? '')
))

// 本书最新一份 AI 对应建议（本地 PPT 建议只服务于课时树，不在此展示）
const bookProposal = computed<SemesterMappingProposal | null>(() => (
  catalog.semesterMappingProposals
    .filter(item => (
      item.status !== 'rejected'
      && item.payload.generation_source !== 'local_reference_ppt_names'
      && item.payload.source_material_record_ids.includes(record.value?.id ?? '')
    ))
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0]
  ?? null
))

function notice(message: string): void {
  emit('notice', message)
}

/* ===== 逐行审核（自 MappingPanel 迁移） ===== */

const mappingEdits = reactive<Record<string, {
  lessonRef: string
  startUnit: number
  endUnit: number
  reason: string
}>>({})
const bulkReviewRunning = ref(false)
const applyRunning = ref(false)

const proposalLessonOptions = computed(() => (
  bookProposal.value?.payload.tree.flatMap(chapter => (
    chapter.sections.flatMap(section => section.lessons.map(lesson => ({
      value: `proposal:${lesson.key}`,
      title: lesson.title,
      path: chapter.title,
    })))
  )) ?? []
))
const formalLessonOptions = computed(() => catalog.lessonNodes
  .filter(node => node.node_type === 'lesson' && node.is_active)
  .map(node => ({ value: node.id, title: node.title, path: '本学期课时树' })))
const mappingLessonOptions = computed(() => [
  ...proposalLessonOptions.value,
  ...formalLessonOptions.value,
])
const mappingGroups = computed(() => {
  const proposal = bookProposal.value
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
const proposalEvidenceById = computed(() => {
  const evidence = bookProposal.value?.payload.directory_evidence
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
const pendingMappingCount = computed(() => (
  bookProposal.value?.payload.mappings.filter(item => item.decision === 'pending').length ?? 0
))
const allMappingsDecided = computed(() => {
  const mappings = bookProposal.value?.payload.mappings ?? []
  return mappings.length > 0 && mappings.every(item => item.decision !== 'pending')
})

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
  const proposal = bookProposal.value
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
      ? '已排除本条对应，原始建议仍保留。'
      : decision === 'modified'
        ? '已保存你的修改，原始建议仍可追溯。'
        : '已确认本条对应。')
  } catch {
    notice('本条决定没有保存，请刷新后核对版本。')
  }
}

async function acceptAllPendingMappings(): Promise<void> {
  if (bulkReviewRunning.value) return
  if (pendingMappingCount.value === 0) {
    notice('当前没有待确认的对应；如已逐条决定，可直接应用。')
    return
  }
  const count = pendingMappingCount.value
  if (!window.confirm(`确认接受剩余 ${count} 条页码对应吗？接受后仍需点击“应用全部接受项”才会正式生效。`)) return
  bulkReviewRunning.value = true
  notice(`正在接受剩余 ${count} 条对应…`)
  try {
    while (true) {
      const pending = bookProposal.value?.payload.mappings.find(
        item => item.decision === 'pending',
      )
      if (!pending) break
      const pendingId = pending.mapping_id
      await decideMapping(pending, 'accepted')
      const saved = bookProposal.value?.payload.mappings.find(
        item => item.mapping_id === pendingId,
      )
      if (saved?.decision === 'pending') return
    }
    notice(`已接受 ${count} 条对应；现在可以应用。`)
  } finally {
    bulkReviewRunning.value = false
  }
}

async function applyProposal(): Promise<void> {
  const proposal = bookProposal.value
  if (!proposal || proposal.payload.mappings.length === 0) {
    notice('请先取得对应建议并逐条完成决定，再应用。')
    return
  }
  if (!allMappingsDecided.value) {
    notice(`还有 ${pendingMappingCount.value} 条对应待你确认；全部处理后才能应用。`)
    return
  }
  applyRunning.value = true
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
      notice('对应已写入且已有回执。')
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        catalog.load(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        notice('对应已写入且已有回执；页面状态暂未刷新，请刷新页面。')
      }
    } else {
      await catalog.applySemesterMapping(proposal)
      await catalog.load()
      notice('全部接受项已对应到本学期课时树。')
    }
  } catch {
    notice(catalog.errorMessage || '对应尚未确认；已保存的逐条决定仍保留，请直接重试。')
  } finally {
    applyRunning.value = false
  }
}

/* ===== 让 AI 重新推断对应关系（自 MappingPanel 迁移，收进 <details>） ===== */

const aiContextReady = ref(false)
const preparingScope = ref(false)
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

const currentMappingPreflight = computed(() => catalog.currentSemesterMappingPreflight)
const currentMappingJob = computed(() => catalog.currentSemesterMappingJob)
const currentMappingJobSyncError = computed(() => catalog.currentSemesterMappingJobSyncError)

const currentMappingTask = computed(() => {
  const semesterId = catalog.selectedSemester?.id
  const current = record.value
  if (!semesterId || !current) return null
  const sourceRevision = currentMappingPreflight.value?.source_state_sha256
  return aiTasks.orderedTasks.find(task => (
    task.module === 'teaching_prep'
    && task.task_kind === 'teaching_prep.semester_mapping'
    && task.source_ref.kind === 'semester'
    && task.source_ref.id === semesterId
    && (!sourceRevision || task.source_ref.revision === sourceRevision)
    && task.context_refs.some(reference => (
      reference.kind === 'material'
      && reference.id === current.id
      && reference.revision === String(current.revision)
    ))
  )) ?? null
})

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
  const proposal = bookProposal.value
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
const mappingTaskErrorDetail = computed(() => {
  const task = currentMappingTask.value
  if (!task || !['failed', 'invalid_result'].includes(task.status)) return ''
  if (mappingJobHasDurableProposal.value || mappingResultUnknown.value) return ''
  return task.error_detail ?? ''
})
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
const mappingStatusTone = computed(() => (
  mappingJobError.value || mappingResultUnknown.value || mappingTaskErrorDetail.value ? 'is-error'
    : mappingJobHasDurableProposal.value ? 'is-success'
      : mappingJobBusy.value || mappingTaskBusy.value ? 'is-running' : ''
))

const hasActiveLessons = computed(() => catalog.activeLessonCount > 0)
const canGenerateMapping = computed(() => (
  record.value !== null
  && hasActiveLessons.value
  && currentMappingPreflight.value?.model_available === true
  && !mappingJobBusy.value
  && !mappingTaskBusy.value
  && !mappingResultUnknown.value
  && !mappingJobHasDurableProposal.value
  && catalog.saveState !== 'saving'
))

function onAiToggle(event: Event): void {
  if ((event.target as HTMLDetailsElement).open) void ensureAiContext()
}

async function ensureAiContext(): Promise<void> {
  const item = material.value
  const current = record.value
  if (!item || !current || preparingScope.value) return
  preparingScope.value = true
  try {
    if (catalog.selectedMaterialId !== item.id) {
      await catalog.openMaterial(item)
    }
    if (!aiContextReady.value) {
      notice('正在检查本次发送范围，不会自动调用模型…')
      await catalog.prepareSemesterMapping([current.id])
      aiContextReady.value = true
      notice('发送范围已准备好；确认后才会产生一次模型调用。')
    }
  } catch {
    notice(catalog.errorMessage || '发送范围检查未完成。')
  } finally {
    preparingScope.value = false
  }
}

async function generateSemesterMapping(): Promise<void> {
  const current = record.value
  const semester = catalog.selectedSemester
  const preflight = currentMappingPreflight.value
  if (!current || !semester || !preflight) return
  notice('统一 AI 任务正在登记；本次最多调用模型一次，不会自动重试…')
  try {
    const prepared = await aiTasks.prepare({
      operation_id: `semester-mapping-${crypto.randomUUID().replaceAll('-', '')}`,
      module: 'teaching_prep',
      task_kind: 'teaching_prep.semester_mapping',
      source_ref: { kind: 'semester', id: semester.id, revision: preflight.source_state_sha256 },
      context_refs: [{ kind: 'material', id: current.id, revision: String(current.revision) }],
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
  aiContextReady.value = false
  await ensureAiContext()
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
    notice('旧结果已放弃。请重新检查发送范围；只有再次点击“让 AI 重新推断”才会产生新调用。')
  } catch {
    notice('旧结果尚未放弃，请保留当前页面后重试。')
  }
}

async function rejectProposalForRegeneration(): Promise<void> {
  const proposal = bookProposal.value
  if (!proposal) return
  if (!window.confirm(
    '确定放弃这份对应建议吗？旧建议和审核记录会保留为“已放弃”，本操作不调用模型。之后只有再次点击“让 AI 重新推断对应关系”才会产生一次新调用和相应费用。',
  )) return
  try {
    const updated = await teachingPrepCatalogApi.rejectSemesterMappingProposal(proposal)
    const index = catalog.semesterMappingProposals.findIndex(item => item.id === updated.id)
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
    notice('旧建议已放弃。本操作没有调用模型；可重新检查发送范围后再决定是否生成新建议。')
  } catch {
    notice('旧建议没有成功放弃，请刷新后核对状态。')
  }
}

/* ===== 人工关联页段（模型不可用时的备用路径） ===== */

const manualMapping = reactive<{
  lessonId: string
  startUnit: number
  endUnit: number
  purpose: MaterialLinkPurpose
  note: string
}>({ lessonId: '', startUnit: 1, endUnit: 1, purpose: 'supplement', note: '' })

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

async function saveManualMapping(): Promise<void> {
  const item = material.value
  const current = record.value
  const lesson = catalog.lessonNodes.find(
    node => node.id === manualMapping.lessonId && node.node_type === 'lesson',
  )
  const maximum = catalog.materialUnits.at(-1)?.unit_index ?? 0
  if (!item || !current || !lesson) {
    notice('请先选择现有课时。')
    return
  }
  if (maximum === 0) {
    notice('正在打开资料页级目录，请稍候再保存。')
    await ensureAiContext()
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
  notice('正在保存人工页段对应…')
  try {
    if (catalog.selectedLessonId !== lesson.id) {
      await catalog.selectLesson(lesson)
    }
    await catalog.confirmMaterialRanges(
      [{ start: manualMapping.startUnit, end: manualMapping.endUnit }],
      manualMapping.purpose,
      manualMapping.note.trim() || null,
    )
    notice(`已把第 ${manualMapping.startUnit}—${manualMapping.endUnit} 页/张对应到“${lesson.title}”。`)
    manualMapping.note = ''
  } catch {
    notice(catalog.errorMessage || '人工对应没有保存。')
  }
}

const decisionLabels: Record<string, string> = {
  pending: '待你确认',
  accepted: '已对应',
  modified: '已对应（已调整）',
  rejected: '已排除',
}
</script>

<template>
  <section v-if="material && record" class="tp-panel" aria-label="对应到课时树">
    <div class="tp-panel__body">
      <div class="tp-main-head">
        <div>
          <span class="tp-eyebrow">{{ roleLabel(record.material_role) }} · 对应到课时树</span>
          <h2>{{ record.display_name }}</h2>
          <p>
            {{ material.material_type.toUpperCase() }} · {{ record.current_unit_count ?? 0 }} 页
            <template v-if="isExerciseBook"> · 练习按课时对应，备课时按课时直接取用</template>
          </p>
        </div>
        <StatusBadge
          :tone="record.mapping_status === 'confirmed' ? 'success' : pendingMappingCount ? 'warning' : 'info'"
          :label="record.mapping_status === 'confirmed' ? '已对应完成' : pendingMappingCount ? `${pendingMappingCount} 条待你确认` : '待对应'"
        />
      </div>

      <p v-if="record.material_role === 'textbook'" class="tp-muted">
        教材目录只到小节，所以按<b>「小节 ↔ 页码段」</b>对应：该小节下的所有课时共用这一段页码，
        备课时用于快速定位，不强行精确到每一课时。
      </p>

      <template v-if="bookProposal">
        <div v-if="pendingMappingCount" class="tp-banner tp-banner--ai">
          以下对应是按目录推断的草稿，请你逐条过目；全部处理后点击“应用全部接受项”正式生效。
        </div>

        <div v-for="group in mappingGroups" :key="group.lessonRef" class="tp-mapping-group">
          <div class="tp-mapping-group-summary">
            <strong>{{ group.title }}</strong>
            <small>{{ group.path }}</small>
            <StatusBadge
              :tone="group.pendingCount ? 'warning' : 'success'"
              :label="group.pendingCount ? `${group.pendingCount} 条待你确认` : '已对应'"
            />
          </div>
          <article
            v-for="item in group.mappings"
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
            </header>
            <div class="tp-mapping-basis">
              <strong>推断依据</strong>
              <p>{{ item.basis ?? item.decision_reason ?? '旧建议未保存推断依据，请结合原页人工复核。' }}</p>
              <div v-if="item.evidence_refs?.length" class="tp-mapping-evidence-list">
                <span>证据</span>
                <span v-for="evidenceId in item.evidence_refs" :key="evidenceId">{{ evidenceDisplay(evidenceId) }}</span>
              </div>
            </div>
            <label class="tp-field">
              对应课时
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
            <label class="tp-field">修改说明（可选）<input v-model="editFor(item).reason" type="text"></label>
            <div class="tp-inline-actions">
              <AppButton variant="secondary" @click="decideMapping(item, 'accepted')">接受</AppButton>
              <AppButton variant="secondary" @click="decideMapping(item, 'modified')">保存修改</AppButton>
              <AppButton variant="ghost" class="tp-danger-text" @click="decideMapping(item, 'rejected')">排除</AppButton>
            </div>
          </article>
        </div>

        <div v-if="isExerciseBook && bookProposal.payload.uncertainties.length" class="tp-topic-box">
          <h3>未对应页段</h3>
          <p class="tp-muted">
            对不上具体课时的内容不塞进课时树，备课时作为补充材料检索；本期只展示，不做持久化整理。
          </p>
          <ul>
            <li v-for="item in bookProposal.payload.uncertainties" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div class="tp-inline-actions tp-mapping-apply-bar">
          <AppButton variant="ghost" @click="rejectProposalForRegeneration">放弃本份建议</AppButton>
          <AppButton variant="secondary" :disabled="bulkReviewRunning" @click="acceptAllPendingMappings">
            {{ bulkReviewRunning ? '正在接受对应…' : `接受全部剩余对应（${pendingMappingCount}）` }}
          </AppButton>
          <AppButton
            variant="primary"
            data-testid="apply-book-mapping"
            :disabled="applyRunning"
            @click="applyProposal"
          >
            {{ applyRunning ? '正在应用…' : '应用全部接受项' }}
          </AppButton>
        </div>
      </template>
      <p v-else class="tp-muted">
        还没有这本书的对应建议。可在下方让 AI 推断一份草稿，或手动指定页段。
      </p>

      <details class="tp-ai-reinfer" data-testid="ai-reinfer" @toggle="onAiToggle">
        <summary>让 AI 重新推断对应关系</summary>
        <div class="tp-ai-reinfer__body">
          <p v-if="!hasActiveLessons" class="tp-error-text">
            学期里还没有已生效的课时，AI 无法把页码挂到课时上。请先在资料柜底部确认本学期课时树。
          </p>
          <template v-else>
            <p class="tp-muted">
              会发送：本书的<b>目录线索</b>与少量<b>抽样锚点页</b>（用于核对页码），<b>不发送全书</b>；
              会得到一份新的「小节/课时 ↔ 页码段」对应草稿，由你逐条过目后才会生效。
              本次为单次调用、按次计费，发送前需要你确认；本机无法预估金额，由当前模型服务商按实际用量计费。
            </p>
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
                  · 对应到 {{ currentMappingPreflight.existing_lesson_count }} 个已生效课时
                  · 建议需逐条确认后才会生效
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
              </div>
            </details>

            <div
              v-if="currentMappingJob || currentMappingTask"
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
                  <strong>{{ record.display_name }}</strong>
                </div>
                <span v-if="mappingElapsedLabel">已等待 {{ mappingElapsedLabel }}</span>
              </div>
              <p v-if="mappingJobError" class="tp-error-text">{{ mappingJobError }}</p>
              <p v-if="mappingTaskErrorDetail" class="tp-error-text">{{ mappingTaskErrorDetail }}</p>
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

            <div class="tp-inline-actions">
              <AppButton
                v-if="mappingRetryAvailable"
                variant="secondary"
                :disabled="catalog.saveState === 'saving'"
                @click="retrySemesterMapping"
              >
                重新检查并生成新建议
              </AppButton>
              <AppButton
                variant="primary"
                :disabled="!canGenerateMapping || preparingScope"
                @click="generateSemesterMapping"
              >
                {{ mappingResultUnknown
                  ? '结果未知，不能自动重试'
                  : mappingJobBusy || mappingTaskBusy
                    ? '后台生成中…'
                    : '确认发送（单次计费）' }}
              </AppButton>
            </div>
          </template>
        </div>
      </details>

      <details class="tp-ai-reinfer">
        <summary>手动指定页段到课时</summary>
        <div class="tp-ai-reinfer__body">
          <p class="tp-muted">模型不可用时，也可以把连续页段直接对应到现有课时。</p>
          <div class="tp-form-line">
            <label class="tp-field">
              对应课时
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
              :disabled="!manualMapping.lessonId || catalog.saveState === 'saving'"
              @click="saveManualMapping"
            >
              保存人工对应
            </AppButton>
          </div>
        </div>
      </details>

      <details class="tp-ai-reinfer">
        <summary>资料管理（改名 / 角色 / 移出 / 删除）</summary>
        <div class="tp-ai-reinfer__body">
          <MaterialDetail :material-id="materialId" @notice="notice" />
        </div>
      </details>
    </div>
  </section>
</template>
