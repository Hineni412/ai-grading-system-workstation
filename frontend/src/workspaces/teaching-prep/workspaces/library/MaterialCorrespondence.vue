<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

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
import {
  buildMappingReviewGroups,
  coveringReviewGroup,
  groupHasPending,
  nextPendingReviewGroup,
  pendingReviewCount as countPendingReviewGroups,
  type MappingReviewGroup,
} from './mappingSectionGroups'

const props = defineProps<{ materialId: string }>()
const emit = defineEmits<{ notice: [message: string]; 'open-import': [] }>()

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

const abandonedProposalIds = ref<string[]>([])

// 本书最新一份 AI 对应建议（本地 PPT 建议只服务于课时树，不在此展示）
const bookProposal = computed<SemesterMappingProposal | null>(() => {
  const recordId = record.value?.id ?? ''
  const listed = catalog.semesterMappingProposals.filter(item => (
    item.status === 'proposed'
    && !abandonedProposalIds.value.includes(item.id)
    && item.payload.generation_source !== 'local_reference_ppt_names'
  ))
  const byRecord = listed
    .filter(item => item.payload.source_material_record_ids.includes(recordId))
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0]
  if (byRecord || !record.value) return byRecord ?? null
  const semesterId = catalog.selectedSemester?.id
  if (!semesterId) return null
  const task = aiTasks.orderedTasks.find(item => (
    item.module === 'teaching_prep'
    && item.task_kind === 'teaching_prep.semester_mapping'
    && item.status === 'proposal_ready'
    && Boolean(item.proposal_ref_id)
    && item.source_ref.kind === 'semester'
    && item.source_ref.id === semesterId
    && item.context_refs.some(reference => (
      reference.kind === 'material' && reference.id === recordId
    ))
  ))
  if (!task?.proposal_ref_id) return null
  return listed.find(item => item.id === task.proposal_ref_id) ?? null
})

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
const isTextbookBook = computed(() => record.value?.material_role === 'textbook')
const allMappingsDecided = computed(() => {
  const mappings = bookProposal.value?.payload.mappings ?? []
  return mappings.length > 0 && mappings.every(item => item.decision !== 'pending')
})

const currentPage = ref(1)
const rangeEditorOpen = ref(false)
const pageStripRef = ref<HTMLElement | null>(null)
let initializedProposalId = ''

const bookUnits = computed(() => (
  catalog.selectedMaterialId === props.materialId ? catalog.materialUnits : []
))
const orderedMappings = computed(() => (
  [...(bookProposal.value?.payload.mappings ?? [])].sort((left, right) => (
    left.start_unit - right.start_unit || left.end_unit - right.end_unit
  ))
))
const reviewGroups = computed(() => buildMappingReviewGroups(
  orderedMappings.value,
  {
    textbook: isTextbookBook.value,
    lessonNodes: catalog.lessonNodes,
  },
))
const pendingReviewCount = computed(() => countPendingReviewGroups(reviewGroups.value))
const currentReviewGroup = computed(() => coveringReviewGroup(
  reviewGroups.value,
  currentPage.value,
))
const pageCount = computed(() => Math.max(
  record.value?.current_unit_count ?? 0,
  bookUnits.value.reduce((max, unit) => Math.max(max, unit.unit_index), 0),
  orderedMappings.value.reduce((max, item) => Math.max(max, item.end_unit), 0),
  1,
))
const pageIndexes = computed(() => (
  Array.from({ length: pageCount.value }, (_, index) => index + 1)
))
const pageStatuses = computed(() => {
  const statuses = new Map<number, 'pending' | 'accepted' | 'modified' | 'rejected' | 'unmapped'>()
  for (let page = 1; page <= pageCount.value; page += 1) statuses.set(page, 'unmapped')
  for (const item of orderedMappings.value) {
    for (let page = item.start_unit; page <= item.end_unit; page += 1) {
      const current = statuses.get(page)
      if (item.decision === 'pending' || current === 'unmapped' || current === 'rejected') {
        statuses.set(page, item.decision)
      }
    }
  }
  return statuses
})
const pageLessonTones = computed(() => {
  const tones = new Map<number, 'a' | 'b'>()
  let previous: string | null = null
  let tone: 'a' | 'b' = 'a'
  for (let page = 1; page <= pageCount.value; page += 1) {
    const group = coveringReviewGroup(reviewGroups.value, page)
    const groupKey = group?.key ?? null
    if (!groupKey) continue
    if (previous !== null && groupKey !== previous) {
      tone = tone === 'a' ? 'b' : 'a'
    }
    previous = groupKey
    tones.set(page, tone)
  }
  return tones
})
const currentRange = computed(() => {
  const group = currentReviewGroup.value
  if (group) {
    return group.mappings.find(item => item.decision === 'pending') ?? group.mappings[0] ?? null
  }
  const page = currentPage.value
  const covering = orderedMappings.value.filter(item => (
    item.start_unit <= page && page <= item.end_unit
  ))
  return covering.find(item => item.decision === 'pending') ?? covering[0] ?? null
})
const currentRangeStart = computed(() => (
  currentReviewGroup.value?.startUnit ?? currentRange.value?.start_unit ?? currentPage.value
))
const currentRangeEnd = computed(() => (
  currentReviewGroup.value?.endUnit ?? currentRange.value?.end_unit ?? currentPage.value
))
const currentLessonLabel = computed(() => {
  const group = currentReviewGroup.value
  if (group?.label) return group.label
  const item = currentRange.value
  if (!item) return '未对应课时'
  return mappingLessonOptions.value.find(option => option.value === item.lesson_ref)?.title
    ?? '未识别课时'
})
const previewWindow = computed(() => {
  const page = currentPage.value
  return [page - 1, page, page + 1].flatMap((index) => {
    const unit = bookUnits.value.find(item => item.unit_index === index)
    return unit ? [unit] : []
  })
})
const currentPreviewMissing = computed(() => (
  !previewWindow.value.some(unit => unit.unit_index === currentPage.value)
))
const confirmRangeLabel = computed(() => {
  if (!currentRange.value) return '下一页'
  const unit = isTextbookBook.value ? '小节' : '段'
  if (!currentReviewGroup.value || !groupHasPending(currentReviewGroup.value)) {
    return `看下一${unit}未确认`
  }
  if (pendingReviewCount.value <= 1) return isTextbookBook.value ? '确认本小节' : '确认本段'
  return isTextbookBook.value ? '确认本小节，看下一小节' : '确认本段，看下一段'
})

function pageStatusOf(page: number): 'pending' | 'accepted' | 'modified' | 'rejected' | 'unmapped' {
  return pageStatuses.value.get(page) ?? 'unmapped'
}

function pageLessonTone(page: number): 'a' | 'b' | null {
  return pageLessonTones.value.get(page) ?? null
}

function pageStatusLabel(page: number): string {
  return ({
    pending: '待确认',
    accepted: '已对应',
    modified: '已调整',
    rejected: '已排除',
    unmapped: '未对应',
  }[pageStatusOf(page)])
}

function pageLessonLabel(page: number): string {
  const group = coveringReviewGroup(reviewGroups.value, page)
  if (group?.label) return group.label
  const covering = orderedMappings.value.filter(item => (
    item.decision !== 'rejected'
    && item.start_unit <= page
    && page <= item.end_unit
  ))
  const chosen = covering.find(item => item.decision === 'pending') ?? covering[0]
  if (!chosen) return ''
  return mappingLessonOptions.value.find(option => option.value === chosen.lesson_ref)?.title
    ?? '未识别课时'
}

function pageAriaLabel(page: number): string {
  const lesson = pageLessonLabel(page)
  const status = pageStatusLabel(page)
  return lesson ? `第 ${page} 页，${lesson}，${status}` : `第 ${page} 页，${status}`
}

function goToPage(page: number): void {
  currentPage.value = Math.min(pageCount.value, Math.max(1, page))
}

function goToNextPendingRange(afterEnd: number): void {
  const nextGroup = nextPendingReviewGroup(reviewGroups.value, afterEnd)
  if (nextGroup) {
    goToPage(nextGroup.startUnit)
    return
  }
  const pending = orderedMappings.value.filter(item => item.decision === 'pending')
  const next = pending.find(item => item.start_unit > afterEnd) ?? pending[0]
  if (next) goToPage(next.start_unit)
}

async function ensurePageUnits(): Promise<void> {
  const item = material.value
  if (!item) return
  if (
    catalog.selectedMaterialId === item.id
    && catalog.materialUnits.some(unit => unit.material_version_id === item.id)
  ) return
  try {
    if (catalog.selectedMaterialId === item.id) {
      await catalog.refreshCurrentMaterialUnits(item.id)
      return
    }
    await catalog.openMaterial(item)
  } catch {
    notice(catalog.errorMessage || '原页预览还没有打开，仍可按页段确认。')
  }
}

function rangeWasEdited(item: SemesterMappingProposalRange): boolean {
  const edit = editFor(item)
  return edit.lessonRef !== item.lesson_ref
    || edit.startUnit !== item.start_unit
    || edit.endUnit !== item.end_unit
}

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
  options: { silent?: boolean } = {},
): Promise<boolean> {
  const proposal = bookProposal.value
  if (!proposal) return false
  const edit = editFor(item)
  if (!options.silent) notice('正在保存本条决定…')
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
    if (!options.silent) {
      notice(decision === 'rejected'
        ? '已排除本条对应，原始建议仍保留。'
        : decision === 'modified'
          ? '已保存你的修改，原始建议仍可追溯。'
          : '已确认本条对应。')
    }
    return true
  } catch {
    notice('本条决定没有保存，请刷新后核对版本。')
    return false
  }
}

function groupRangeFromEditor(group: MappingReviewGroup): { start: number, end: number, edited: boolean } {
  const representative = group.mappings.find(item => item.decision === 'pending') ?? group.mappings[0]
  if (!representative || !rangeWasEdited(representative)) {
    return { start: group.startUnit, end: group.endUnit, edited: false }
  }
  const edit = editFor(representative)
  return { start: edit.startUnit, end: edit.endUnit, edited: true }
}

async function confirmReviewGroup(
  group: MappingReviewGroup,
  decision: 'accepted' | 'rejected',
): Promise<boolean> {
  const pending = group.mappings.filter(item => item.decision === 'pending')
  if (pending.length === 0) return true
  const { start, end, edited } = groupRangeFromEditor(group)
  notice(decision === 'rejected' ? '正在排除本小节对应…' : '正在确认本小节对应…')
  for (const item of pending) {
    if (decision === 'rejected') {
      const saved = await decideMapping(item, 'rejected', { silent: true })
      if (!saved) return false
      continue
    }
    const needsRangeRewrite = item.start_unit !== start || item.end_unit !== end
    if (needsRangeRewrite || edited) {
      const slot = editFor(item)
      slot.lessonRef = item.lesson_ref
      slot.startUnit = start
      slot.endUnit = end
      if (!slot.reason) slot.reason = '同小节共用教材页码'
      const saved = await decideMapping(item, 'modified', { silent: true })
      if (!saved) return false
      continue
    }
    const saved = await decideMapping(item, 'accepted', { silent: true })
    if (!saved) return false
  }
  const lessonCount = new Set(group.mappings.map(item => item.lesson_ref)).size
  notice(decision === 'rejected'
    ? '已排除本小节对应，原始建议仍保留。'
    : lessonCount > 1
      ? `已确认本小节，${lessonCount} 个课时将共用 ${start}—${end} 页。`
      : '已确认本小节对应。')
  return true
}

async function confirmCurrentRangeAndAdvance(): Promise<void> {
  const group = currentReviewGroup.value
  const item = currentRange.value
  if (!group && !item) {
    goToPage(currentPage.value + 1)
    return
  }
  if (group && isTextbookBook.value) {
    const end = groupRangeFromEditor(group).end
    if (groupHasPending(group)) {
      const saved = await confirmReviewGroup(group, 'accepted')
      if (!saved) return
    }
    goToNextPendingRange(end)
    return
  }
  if (!item) {
    goToPage(currentPage.value + 1)
    return
  }
  const end = Math.max(item.end_unit, editFor(item).endUnit)
  if (item.decision === 'pending') {
    const saved = await decideMapping(item, rangeWasEdited(item) ? 'modified' : 'accepted')
    if (!saved) return
  }
  goToNextPendingRange(end)
}

async function excludeCurrentRange(): Promise<void> {
  const group = currentReviewGroup.value
  if (group && isTextbookBook.value) {
    if (!groupHasPending(group)) return
    const end = group.endUnit
    const saved = await confirmReviewGroup(group, 'rejected')
    if (!saved) return
    goToNextPendingRange(end)
    return
  }
  const item = currentRange.value
  if (!item || item.decision === 'rejected') return
  const end = item.end_unit
  const saved = await decideMapping(item, 'rejected')
  if (!saved) return
  goToNextPendingRange(end)
}

function onCheckerKeydown(event: KeyboardEvent): void {
  const target = event.target
  if (target instanceof HTMLElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    goToPage(currentPage.value + 1)
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goToPage(currentPage.value - 1)
  } else if (event.key === 'Enter' && event.target === event.currentTarget) {
    event.preventDefault()
    void confirmCurrentRangeAndAdvance()
  }
}

function onRangeEditorToggle(event: Event): void {
  const details = event.target
  rangeEditorOpen.value = details instanceof HTMLDetailsElement && details.open
}

async function acceptAllPendingMappings(): Promise<void> {
  if (bulkReviewRunning.value) return
  if (pendingReviewCount.value === 0) {
    notice('当前没有待确认的对应；如已逐条决定，可直接应用。')
    return
  }
  const count = pendingReviewCount.value
  const unit = isTextbookBook.value ? '个小节' : '条'
  if (!window.confirm(`确认接受剩余 ${count} ${unit}对应吗？接受后仍需点击“应用全部接受项”才会正式生效。`)) return
  bulkReviewRunning.value = true
  notice(`正在接受剩余 ${count} ${unit}对应…`)
  try {
    if (isTextbookBook.value) {
      while (true) {
        const group = reviewGroups.value.find(groupHasPending)
        if (!group) break
        const groupKey = group.key
        const saved = await confirmReviewGroup(group, 'accepted')
        if (!saved) return
        const remaining = reviewGroups.value.find(item => item.key === groupKey)
        if (remaining && groupHasPending(remaining)) return
      }
    } else {
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
    }
    notice(`已接受 ${count} ${unit}对应；现在可以应用。`)
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
    notice(`还有 ${pendingReviewCount.value} ${isTextbookBook.value ? '个小节' : '条'}对应待你确认；全部处理后才能应用。`)
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
      closeAppliedReview(proposal.id)
      notice('对应已写入本学期课时树。')
      const refreshes = await Promise.allSettled([
        aiTasks.refresh(adopted.match.task.task_id),
        catalog.load(),
      ])
      if (refreshes.some(result => result.status === 'rejected')) {
        notice('对应已写入本学期课时树；页面状态暂未刷新，请刷新页面。')
      }
    } else {
      await catalog.applySemesterMapping(proposal)
      closeAppliedReview(proposal.id)
      await catalog.load()
      notice('对应已写入本学期课时树。')
    }
  } catch {
    notice(catalog.errorMessage || '对应尚未确认；已保存的逐条决定仍保留，请直接重试。')
  } finally {
    applyRunning.value = false
  }
}

/* ===== 让 AI 重新推断对应关系（自 MappingPanel 迁移，收进 <details>） ===== */

const preparingScope = ref(false)
let refreshedMappingTaskRevision = ''

const mappingClock = ref(Date.now())
let mappingClockTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  mappingClockTimer = globalThis.setInterval(() => {
    mappingClock.value = Date.now()
  }, 1_000)
  void ensurePageUnits()
})
onBeforeUnmount(() => {
  if (mappingClockTimer !== null) globalThis.clearInterval(mappingClockTimer)
})

watch(
  () => props.materialId,
  () => {
    initializedProposalId = ''
    currentPage.value = 1
    rangeEditorOpen.value = false
    void ensurePageUnits()
  },
)

watch(
  () => bookProposal.value?.id ?? '',
  (proposalId) => {
    if (!proposalId || proposalId === initializedProposalId) return
    initializedProposalId = proposalId
    const pending = orderedMappings.value.find(item => item.decision === 'pending')
    goToPage(pending?.start_unit ?? 1)
  },
  { immediate: true },
)

watch(currentPage, async (page) => {
  await nextTick()
  const active = pageStripRef.value?.querySelector(`[data-page-index="${page}"]`)
  if (active instanceof HTMLElement && typeof active.scrollIntoView === 'function') {
    active.scrollIntoView({ inline: 'center', block: 'nearest' })
  }
})

watch(
  () => currentRange.value?.mapping_id ?? '',
  () => {
    rangeEditorOpen.value = false
  },
)

const currentMappingPreflight = computed(() => {
  const current = record.value
  // catalog 里缓存的发送范围属于全局选中书；组件按书实例化，只有选中书恰好是本书时才能用，
  // 否则视为没有准备好，避免把另一本书的发送范围配到本书上发送。
  if (!current || catalog.selectedSemesterMaterial?.id !== current.id) return null
  return catalog.currentSemesterMappingPreflight
})
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

function applyListedProposals(items: SemesterMappingProposal[]): void {
  catalog.semesterMappingProposals = items.map(item => (
    abandonedProposalIds.value.includes(item.id) && item.status !== 'rejected'
      ? { ...item, status: 'rejected' }
      : item
  ))
}

watch(
  () => currentMappingTask.value ? `${currentMappingTask.value.task_id}:${currentMappingTask.value.revision}` : '',
  async (revision) => {
    const task = currentMappingTask.value
    if (!revision || revision === refreshedMappingTaskRevision || !task) return
    if (!['proposal_ready', 'needs_input'].includes(task.status)) return
    refreshedMappingTaskRevision = revision
    const semester = catalog.selectedSemester
    if (!semester) return
    try {
      applyListedProposals(await teachingPrepCatalogApi.listSemesterMappingProposals(semester.id))
    } catch {
      notice(catalog.errorMessage || '对应草稿还没有同步到本页，可点“重新载入草稿”。本操作没有调用模型。')
    }
  },
  { immediate: true },
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
const unreadSavedDraft = computed(() => {
  if (bookProposal.value) return false
  const task = currentMappingTask.value
  if (task?.status !== 'proposal_ready' || !task.proposal_ref_id) return false
  if (abandonedProposalIds.value.includes(task.proposal_ref_id)) return false
  const saved = catalog.semesterMappingProposals.find(item => item.id === task.proposal_ref_id)
  if (saved?.status === 'applied' || saved?.status === 'rejected') return false
  return true
})
const blockingSavedDraft = computed(() => (
  Boolean(bookProposal.value) || unreadSavedDraft.value
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
  && !blockingSavedDraft.value
  && catalog.saveState !== 'saving'
))

async function reloadBookProposal(): Promise<void> {
  const semester = catalog.selectedSemester
  if (!semester) {
    notice('还没有选中本学期，请先回到备课首页。')
    return
  }
  notice('正在重新读取已保存的对应草稿，不会调用模型…')
  try {
    applyListedProposals(await teachingPrepCatalogApi.listSemesterMappingProposals(semester.id))
    if (bookProposal.value) {
      notice('对应草稿已重新载入，请从第 1 页开始核对。')
      return
    }
    const task = currentMappingTask.value
    const saved = task?.proposal_ref_id
      ? catalog.semesterMappingProposals.find(item => item.id === task.proposal_ref_id)
      : null
    if (
      saved?.status === 'rejected'
      || (task?.proposal_ref_id && abandonedProposalIds.value.includes(task.proposal_ref_id))
    ) {
      notice('旧建议已放弃。可以重新推断，会再计一次费。')
      return
    }
    notice('本页仍读不到这份草稿。若对应不准，请先放弃后再重新推断（会再计费）。')
  } catch {
    notice(catalog.errorMessage || '草稿还没有重新载入，请稍后重试。本操作没有调用模型。')
  }
}

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
    if (!currentMappingPreflight.value) {
      notice('正在检查本次发送范围，不会自动调用模型…')
      await catalog.prepareSemesterMapping([current.id])
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
  await ensureAiContext()
  if (currentMappingPreflight.value) {
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

function closeAppliedReview(proposalId: string): void {
  const index = catalog.semesterMappingProposals.findIndex(item => item.id === proposalId)
  const current = index >= 0 ? catalog.semesterMappingProposals[index] : null
  if (current && current.status === 'proposed') {
    catalog.semesterMappingProposals[index] = {
      ...current,
      status: 'applied',
    }
  }
  const recordId = record.value?.id
  const materialIndex = catalog.semesterMaterials.findIndex(item => item.id === recordId)
  const material = materialIndex >= 0 ? catalog.semesterMaterials[materialIndex] : null
  if (material && material.mapping_status !== 'confirmed') {
    catalog.semesterMaterials[materialIndex] = {
      ...material,
      mapping_status: 'confirmed',
    }
  }
}

async function rejectProposalForRegeneration(): Promise<void> {
  const task = currentMappingTask.value
  const proposal = bookProposal.value
    ?? catalog.semesterMappingProposals.find(item => item.id === (task?.proposal_ref_id ?? ''))
  const proposalId = proposal?.id ?? task?.proposal_ref_id
  const revision = proposal?.revision ?? Number(task?.proposal_revision)
  if (!proposalId || !Number.isSafeInteger(revision) || revision < 1) {
    notice('找不到可放弃的旧建议，请刷新页面后再试。')
    return
  }
  if (!window.confirm(
    '确定放弃这份对应建议吗？旧建议和审核记录会保留为“已放弃”，本操作不调用模型。之后打开“让 AI 重新推断对应关系”并确认发送，才会产生一次新调用和相应费用。',
  )) return
  if (!abandonedProposalIds.value.includes(proposalId)) {
    abandonedProposalIds.value = [...abandonedProposalIds.value, proposalId]
  }
  try {
    const updated = await teachingPrepCatalogApi.rejectSemesterMappingProposal({
      id: proposalId,
      revision,
    })
    const index = catalog.semesterMappingProposals.findIndex(item => item.id === updated.id)
    if (index >= 0) catalog.semesterMappingProposals[index] = updated
    else catalog.semesterMappingProposals = [...catalog.semesterMappingProposals, updated]
    notice('旧建议已放弃。本操作没有调用模型。打开“让 AI 重新推断”并确认发送后，才会产生一次新调用和费用。')
  } catch {
    abandonedProposalIds.value = abandonedProposalIds.value.filter(id => id !== proposalId)
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
  <section
    v-if="material && record"
    class="tp-panel"
    :class="{ 'tp-panel--correspondence': Boolean(bookProposal) }"
    aria-label="对应到课时树"
  >
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
        <div class="tp-inline-actions">
          <AppButton variant="ghost" data-testid="open-import-from-book" @click="emit('open-import')">
            导入其他资料
          </AppButton>
          <StatusBadge
            :tone="record.mapping_status === 'confirmed' ? 'success' : pendingReviewCount ? 'warning' : 'info'"
            :label="record.mapping_status === 'confirmed' ? '已对应完成' : pendingReviewCount ? (isTextbookBook ? `${pendingReviewCount} 个小节待你确认` : `${pendingReviewCount} 条待你确认`) : '待对应'"
          />
        </div>
      </div>

      <p v-if="record.material_role === 'textbook'" class="tp-muted">
        教材目录只到小节，所以按<b>「小节 ↔ 页码段」</b>对应：该小节下的所有课时共用这一段页码，
        备课时用于快速定位，不强行精确到每一课时。
      </p>

      <template v-if="bookProposal">
        <div
          class="tp-page-checker"
          data-testid="mapping-page-checker"
          tabindex="0"
          aria-label="按页确认对应"
          @keydown="onCheckerKeydown"
        >
          <div class="tp-page-checker__toolbar">
            <p v-if="pendingReviewCount" class="tp-banner tp-banner--ai">
              {{ isTextbookBook
                ? '对着原页确认本段页码属于哪一小节；该小节下的课时共用这一段。全部处理后点击“应用全部接受项”正式生效。'
                : '对着原页确认本段页码属于哪一课时；全部处理后点击“应用全部接受项”正式生效。' }}
            </p>
            <p v-else class="tp-muted">
              本份建议已全部处理。核对无误后可应用到本学期课时树。
            </p>
            <div class="tp-inline-actions tp-mapping-apply-bar">
              <AppButton
                variant="ghost"
                data-testid="abandon-saved-mapping"
                @click="rejectProposalForRegeneration"
              >
                放弃本份建议
              </AppButton>
              <AppButton variant="ghost" :disabled="bulkReviewRunning || pendingReviewCount === 0" @click="acceptAllPendingMappings">
                {{ bulkReviewRunning ? '正在接受对应…' : `接受剩余（${pendingReviewCount}）` }}
              </AppButton>
              <AppButton
                :variant="currentRange?.decision === 'pending' ? 'primary' : 'secondary'"
                data-testid="confirm-current-range"
                @click="confirmCurrentRangeAndAdvance"
              >
                {{ confirmRangeLabel }}
              </AppButton>
              <AppButton
                variant="primary"
                data-testid="apply-book-mapping"
                :disabled="applyRunning || !allMappingsDecided"
                @click="applyProposal"
              >
                {{ applyRunning ? '正在应用…' : '应用全部接受项' }}
              </AppButton>
            </div>
          </div>

          <div
            ref="pageStripRef"
            class="tp-page-strip"
            data-testid="mapping-page-strip"
            role="list"
            aria-label="按页顺序，相邻段用两种底色区分"
          >
            <button
              v-for="page in pageIndexes"
              :key="page"
              type="button"
              role="listitem"
              class="tp-page-strip__cell"
              :class="[
                `is-${pageStatusOf(page)}`,
                pageLessonTone(page) ? `is-lesson-${pageLessonTone(page)}` : '',
                { 'is-active': page === currentPage },
              ]"
              :data-page-index="page"
              :data-lesson-tone="pageLessonTone(page) ?? undefined"
              :aria-current="page === currentPage ? 'page' : undefined"
              :aria-label="pageAriaLabel(page)"
              @click="goToPage(page)"
            >
              {{ page }}
            </button>
          </div>
          <p class="tp-muted">{{ isTextbookBook ? '相邻小节用两种底色区分，方便看出页码是怎么切开的。' : '相邻课时用两种底色区分，方便看出页码是怎么切开的。' }}</p>

          <div class="tp-page-checker__work">
            <section class="tp-page-checker__stage" aria-label="当前原页">
              <header>
                <strong>第 {{ currentPage }} / {{ pageCount }} 页</strong>
                <span>{{ pageStatusLabel(currentPage) }}</span>
              </header>
              <div class="tp-slide-stage" data-testid="mapping-page-preview">
                <img
                  v-for="unit in previewWindow"
                  :key="unit.id"
                  v-show="unit.unit_index === currentPage"
                  :src="unit.preview_url"
                  :alt="`第 ${unit.unit_index} 页原页`"
                >
                <div
                  v-if="currentPreviewMissing"
                  class="tp-slide-stage__paper"
                >
                  <p>本页还没有图，仍可按页段确认或修改。</p>
                </div>
              </div>
              <div class="tp-inline-actions">
                <AppButton variant="ghost" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
                  上一页
                </AppButton>
                <AppButton variant="ghost" :disabled="currentPage >= pageCount" @click="goToPage(currentPage + 1)">
                  下一页
                </AppButton>
              </div>
            </section>

            <aside class="tp-page-checker__dock" data-testid="mapping-current-range">
              <template v-if="currentRange">
                <header>
                  <strong>{{ currentRangeStart }}—{{ currentRangeEnd }} 页/张</strong>
                  <StatusBadge
                    :tone="currentRange.decision === 'pending' ? 'warning' : currentRange.decision === 'rejected' ? 'neutral' : 'success'"
                    :label="decisionLabels[currentRange.decision] ?? currentRange.decision"
                  />
                </header>
                <p class="tp-page-checker__lesson">{{ currentLessonLabel }}</p>
                <p class="tp-muted">
                  {{ isTextbookBook
                    ? '本页属于这一小节。确认后，该小节下的课时共用这一段页码，并跳到下一小节未确认的第一页。'
                    : '本页属于这一段。确认后会跳到下一段未确认的第一页。' }}
                </p>
                <div class="tp-mapping-basis">
                  <strong>推断依据</strong>
                  <p>{{ currentRange.basis ?? currentRange.decision_reason ?? '旧建议未保存推断依据，请结合原页人工复核。' }}</p>
                  <div v-if="currentRange.evidence_refs?.length" class="tp-mapping-evidence-list">
                    <span>证据</span>
                    <span v-for="evidenceId in currentRange.evidence_refs" :key="evidenceId">{{ evidenceDisplay(evidenceId) }}</span>
                  </div>
                </div>
                <div class="tp-inline-actions">
                  <AppButton
                    variant="ghost"
                    class="tp-danger-text"
                    data-testid="exclude-current-range"
                    @click="excludeCurrentRange"
                  >
                    排除本{{ isTextbookBook ? '小节' : '段' }}
                  </AppButton>
                </div>
                <details
                  class="tp-operation-editor"
                  :open="rangeEditorOpen"
                  @toggle="onRangeEditorToggle"
                >
                  <summary>{{ isTextbookBook ? '改本小节页段' : '改课时或页段' }}</summary>
                  <p v-if="isTextbookBook" class="tp-muted">改页段后点“确认本小节”，该小节下的课时都会用这一段。</p>
                  <label v-if="!isTextbookBook" class="tp-field">
                    对应课时
                    <select v-model="editFor(currentRange).lessonRef">
                      <option v-for="lesson in mappingLessonOptions" :key="lesson.value" :value="lesson.value">
                        {{ lesson.title }} · {{ lesson.path }}
                      </option>
                    </select>
                  </label>
                  <div class="tp-field-pair">
                    <label class="tp-field">起始页<input v-model.number="editFor(currentRange).startUnit" type="number" min="1"></label>
                    <label class="tp-field">结束页<input v-model.number="editFor(currentRange).endUnit" type="number" min="1"></label>
                  </div>
                  <label class="tp-field">修改说明（可选）<input v-model="editFor(currentRange).reason" type="text"></label>
                  <AppButton variant="secondary" @click="decideMapping(currentRange, 'modified')">保存修改</AppButton>
                </details>
              </template>
              <template v-else>
                <header>
                  <strong>第 {{ currentPage }} 页</strong>
                  <StatusBadge tone="neutral" label="未对应" />
                </header>
                <p class="tp-muted">本页尚未对应到课时。可翻到有建议的页，或在下方手工指定页段。</p>
              </template>
            </aside>
          </div>
        </div>

        <details
          v-if="isExerciseBook && bookProposal.payload.uncertainties.length"
          class="tp-ai-reinfer"
        >
          <summary>未对应页段</summary>
          <div class="tp-ai-reinfer__body">
            <p class="tp-muted">
              对不上具体课时的内容不塞进课时树，备课时作为补充材料检索；本期只展示，不做持久化整理。
            </p>
            <ul>
              <li v-for="item in bookProposal.payload.uncertainties" :key="item">{{ item }}</li>
            </ul>
          </div>
        </details>
      </template>
      <p v-else-if="unreadSavedDraft" class="tp-banner tp-banner--ai" data-testid="saved-proposal-missing">
        这次 AI 草稿已经保存在本机，但本页还没读出来。请先点“重新载入草稿”。
        若仍没有页段，或对应不准，请先放弃这份草稿，再重新推断（会再计一次费）。
      </p>
      <p v-else-if="record.mapping_status === 'confirmed'" class="tp-muted">
        这本书已经对应到本学期课时树。若要调整，可在下方重新推断或手动指定页段。
      </p>
      <p v-else class="tp-muted">
        还没有这本书的对应建议。可在下方让 AI 推断一份草稿，或手动指定页段。
      </p>
      <div v-if="!bookProposal && unreadSavedDraft" class="tp-inline-actions">
        <AppButton variant="secondary" data-testid="reload-book-proposal" @click="reloadBookProposal">
          重新载入草稿
        </AppButton>
        <AppButton
          variant="ghost"
          data-testid="abandon-saved-mapping"
          @click="rejectProposalForRegeneration"
        >
          放弃这份草稿，准备重新推断
        </AppButton>
      </div>

      <details class="tp-ai-reinfer" data-testid="ai-reinfer" @toggle="onAiToggle">
        <summary>让 AI 重新推断对应关系</summary>
        <div class="tp-ai-reinfer__body">
          <p v-if="!hasActiveLessons" class="tp-error-text">
            学期里还没有已生效的课时，AI 无法把页码挂到课时上。请先在资料柜底部确认本学期课时树。
          </p>
          <template v-else>
            <p class="tp-muted">
              会发送：本书的<b>目录线索</b>与少量<b>抽样锚点页</b>（用于核对页码），<b>不发送全书</b>；
              会得到一份新的「{{ isTextbookBook ? '小节' : '课时' }} ↔ 页码段」对应草稿，由你{{ isTextbookBook ? '按小节' : '逐条' }}过目后才会生效。
              本次为单次调用、按次计费，发送前需要你确认；本机无法预估金额，由当前模型服务商按实际用量计费。
            </p>
            <p v-if="blockingSavedDraft" class="tp-banner tp-banner--ai">
              当前已有一份草稿。对应不准或本页读不出时，请先放弃旧草稿，再确认发送（会再计一次费）。
            </p>
            <div v-if="blockingSavedDraft" class="tp-inline-actions">
              <AppButton
                variant="secondary"
                data-testid="abandon-saved-mapping"
                @click="rejectProposalForRegeneration"
              >
                放弃这份草稿，准备重新推断
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
                  · 对应到 {{ currentMappingPreflight.existing_lesson_count }} 个已生效课时
                  · 建议需{{ isTextbookBook ? '按小节' : '逐条' }}确认后才会生效
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
                data-testid="generate-mapping"
                :disabled="!canGenerateMapping || preparingScope"
                @click="generateSemesterMapping"
              >
                {{ mappingResultUnknown
                  ? '结果未知，不能自动重试'
                  : blockingSavedDraft
                    ? '请先放弃当前草稿再发送'
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
