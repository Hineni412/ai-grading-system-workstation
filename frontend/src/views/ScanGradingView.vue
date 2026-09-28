<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import type { ResultsCenterQuestion } from '../api/results-center'
import {
  fetchReviewQuestions,
  type ReviewQuestionSummary,
} from '../api/review'
import type {
  AutomatedGradingMode,
  GradingPlanMetrics,
  ScanDecision,
  ScanIssueSuggestion,
  SelectableGradingMode,
} from '../api/scan-grading'
import AppButton from '../components/design-system/AppButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import StepProgress, { type StepProgressStep } from '../components/design-system/StepProgress.vue'
import StudentMatchSelect from '../components/scan-grading/StudentMatchSelect.vue'
import { BorderBeam } from '@/components/ui/border-beam'
import { useResultsCenterStore } from '../stores/results-center'
import { useScanGradingStore } from '../stores/scan-grading'
import '../styles/scan-grading.css'

const route = useRoute()
const router = useRouter()
const store = useScanGradingStore()
const resultsStore = useResultsCenterStore()
const sessionId = computed(() => Number(route.params.sessionId))
const confirmPending = ref(false)
const selectedStudents = ref<Record<string, number | undefined>>({})
const gradingSubmissionPending = ref(false)
const interventionQuestions = ref<ReviewQuestionSummary[]>([])
const interventionState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const viewerTargetKey = ref<string | null>(null)
const viewerSide = ref<'front' | 'back'>('front')
const viewerZoom = ref(1)
const viewerCanvas = ref<HTMLElement | null>(null)
let viewerPointerId: number | null = null
let viewerPointerX = 0
let viewerPointerY = 0
let viewerScrollLeft = 0
let viewerScrollTop = 0
const gradingStarting = computed(() => !store.gradingRun && (
  gradingSubmissionPending.value
  || store.busyAction === 'grading'
  || store.activeJobId !== null
))
const gradingCompletedWithoutRun = computed(() => !store.gradingRun
  && store.workspace?.grading_job?.status === 'succeeded'
  && store.workspace.grading_job.scan_batch_id === store.uploadBatch?.batch_id)
const editableUploadBatch = computed(() => store.replacementBatch ?? store.uploadBatch)
const uploadFrozen = computed(() => store.uploadBatch?.state === 'frozen' && !store.replacementBatch)
const preflightActive = computed(() => Boolean(
  store.preflightJob && !TERMINAL_JOB_STATUSES.has(store.preflightJob.status),
))
const preflightProgress = computed(() => Math.min(
  100,
  Math.max(0, Math.round((store.preflightJob?.progress ?? 0) * 100)),
))
const preflightProgressText = computed(() => {
  const detail = store.preflightJob?.detail.trim()
  if (detail && detail !== 'starting') return detail
  if (store.preflightJob?.status === 'queued') return '正在等待开始预检'
  return '正在准备答卷页面'
})

interface GradingModeOption {
  mode: SelectableGradingMode
  label: string
  title: string
  note: string
}

const gradingModes: GradingModeOption[] = [
  {
    mode: 'ai',
    label: 'AI 批改',
    title: '整页原图，按题批改',
    note: '会调用 AI · 执行前显示请求估算',
  },
  {
    mode: 'manual',
    label: '人工批改',
    title: '直接进入按题评分',
    note: 'AI 请求 0 · 教师分数为最终结果',
  },
]

type StageId = 'prepare' | 'grade' | 'review'
const STAGE_ORDER: StageId[] = ['prepare', 'grade', 'review']
const activeStage = ref<StageId>('prepare')
const stageTransition = ref<'fx-stage-forward' | 'fx-stage-back'>('fx-stage-forward')
const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
const matchConflicts = computed(() => store.preflight?.match_conflicts ?? [])
const reviewMatchConflicts = computed(() => [...matchConflicts.value, ...store.decisionConflicts])
const runTerminal = computed(() => Boolean(
  store.gradingRun && ['completed', 'failed', 'cancelled'].includes(store.gradingRun.state),
))
// defaultStage 只按真实进度计算；activeStage 跟随它，也允许用户手动切换面板。
const defaultStage = computed<StageId>(() => {
  if (runTerminal.value || gradingCompletedWithoutRun.value) return 'review'
  if (store.gradingRun || gradingStarting.value) return 'grade'
  if (preflightActive.value) return 'prepare'
  if (store.preflight) return (matchConflicts.value.length || pendingCount.value) ? 'prepare' : 'grade'
  return 'prepare'
})
watch(defaultStage, (next, previous) => {
  // 核对完最后一份异常时留在预检页，由教师点“下一步”进入批改。
  if (previous === 'prepare' && next === 'grade' && activeStage.value === 'prepare') return
  stageTransition.value = STAGE_ORDER.indexOf(next) >= STAGE_ORDER.indexOf(activeStage.value)
    ? 'fx-stage-forward' : 'fx-stage-back'
  activeStage.value = next
}, { immediate: true })
const interventionPending = computed(() => (
  interventionSummary.value.ungraded + interventionSummary.value.failed + interventionSummary.value.review
))
const stageSteps = computed<StepProgressStep[]>(() => {
  const uploadState = store.uploadBatch?.state
  const preparing = (uploadState === 'draft' && (store.uploadBatch?.file_count ?? 0) > 0)
    || Boolean(store.replacementBatch) || preflightActive.value
  const prepareStatus = store.preflight && !matchConflicts.value.length && !pendingCount.value ? 'done'
    : preparing || (store.preflight && (matchConflicts.value.length || pendingCount.value))
      ? 'in_progress' : 'todo'
  const gradeStatus = runTerminal.value || gradingCompletedWithoutRun.value ? 'done'
    : store.gradingRun || gradingStarting.value ? 'in_progress' : 'todo'
  const reviewStatus = interventionState.value !== 'ready' ? 'todo'
    : interventionPending.value > 0 ? 'in_progress'
      : interventionSummary.value.total > 0 ? 'done' : 'todo'
  return [
    {
      id: 'prepare', label: '答卷与预检',
      status: prepareStatus,
      available: Boolean(store.workspace),
      hint: preflightActive.value ? '预检进行中' : prepareStatus === 'done' ? '已核对'
        : prepareStatus === 'in_progress' ? '需核对' : '待上传',
    },
    {
      id: 'grade', label: '批改',
      status: gradeStatus,
      available: Boolean(store.preflight),
      hint: gradeStatus === 'done' ? '已结束' : gradeStatus === 'in_progress' ? '进行中' : '待发起',
    },
    {
      id: 'review', label: '复核',
      status: reviewStatus,
      available: Boolean(store.preflight),
      hint: reviewStatus === 'in_progress' ? `待处理 ${interventionPending.value} 项`
        : reviewStatus === 'done' ? '已处理完' : '待建立',
    },
  ]
})
function selectStage(id: string): void {
  const next = id as StageId
  if (!STAGE_ORDER.includes(next)) return
  if (!stageSteps.value.find((step) => step.id === next)?.available) return
  stageTransition.value = STAGE_ORDER.indexOf(next) >= STAGE_ORDER.indexOf(activeStage.value)
    ? 'fx-stage-forward' : 'fx-stage-back'
  activeStage.value = next
}
const decisionNotice = ref('')
const decisionFailed = ref(false)
const savingDecisionCount = ref(0)
const canPreviewPlan = computed(() => Boolean(store.preflight)
  && !store.gradingRun && !gradingStarting.value && !gradingCompletedWithoutRun.value
  && !store.busyAction && store.planState !== 'loading')
const canConfirmPlan = computed(() => Boolean(
  store.gradingPlan
  && store.gradingPlan.mode === store.selectedMode
  && store.gradingPlan.status === 'ready'
  && store.planState === 'ready'
  && matchConflicts.value.length === 0
  && (pendingCount.value === 0 || confirmPending.value)
  && !store.busyAction
  && !gradingStarting.value,
))
const invalidCount = computed(() => store.preflight?.decisions
  .filter((item) => item.action === 'invalid').length ?? 0)
const missingBackCount = computed(() => store.preflight?.issues
  .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
const preflightPageAssignment = computed(() => (
  store.preflight?.page_assignment
  ?? { first_page_role: 'front' as const, front_page_parity: 'odd' as const }
))
const preflightGroups = computed(() => store.preflight?.groups ?? [])

interface AssignedPaper {
  targetType: 'group' | 'issue'
  targetId: string
  label: string
}

interface ReviewRow {
  key: string
  targetType: 'group' | 'issue'
  item: Record<string, unknown>
  bucket: 'conflict' | 'unmatched' | 'auto' | 'done'
}

type ReviewFilter = 'todo' | 'auto' | 'done' | 'all'
const REVIEW_FILTERS: { key: ReviewFilter; label: string }[] = [
  { key: 'todo', label: '需处理' },
  { key: 'auto', label: '自动匹配' },
  { key: 'done', label: '已处理' },
  { key: 'all', label: '全部' },
]
const REVIEW_BUCKET_LABELS: Record<ReviewRow['bucket'], string> = {
  conflict: '冲突', unmatched: '待匹配', auto: '自动匹配', done: '已处理',
}
const reviewFilter = ref<ReviewFilter>('todo')
const selectedRowKey = ref<string | null>(null)
const selectedIndex = ref(0)
const detailSide = ref<'front' | 'back'>('front')
let reviewFilterNeedsDefault = true

const decisionMap = computed(() => new Map(
  (store.preflight?.decisions ?? []).map((item) => [`${item.target_type}:${item.target_id}`, item]),
))

// Mirrors effective_preflight_papers: which paper currently belongs to each
// student, so picking an already-assigned student can be resolved inline.
const assignedByStudent = computed(() => {
  const map = new Map<number, AssignedPaper[]>()
  const add = (studentId: unknown, paper: AssignedPaper) => {
    const id = Number(studentId)
    if (!Number.isInteger(id) || id <= 0) return
    const list = map.get(id) ?? []
    list.push(paper)
    map.set(id, list)
  }
  for (const group of store.preflight?.groups ?? []) {
    const targetId = String(group.id ?? '')
    const decision = decisionMap.value.get(`group:${targetId}`)
    if (decision && (decision.action === 'invalid' || decision.action === 'pending')) continue
    const studentId = decision?.action === 'match' ? decision.student_id : group.student_id
    add(studentId, {
      targetType: 'group',
      targetId,
      label: displaySourceLabel(String(group.source_label || group.student_name || group.detected_name || '答卷')),
    })
  }
  const issuesById = new Map(
    (store.preflight?.issues ?? []).map((item) => [String(item.id ?? ''), item]),
  )
  for (const decision of store.preflight?.decisions ?? []) {
    if (decision.target_type !== 'issue' || decision.action !== 'match') continue
    const issue = issuesById.get(decision.target_id)
    if (!issue?.back_media_url) continue
    add(decision.student_id, {
      targetType: 'issue',
      targetId: decision.target_id,
      label: displaySourceLabel(String(issue.source_label || issue.detected_name || '异常答卷')),
    })
  }
  return map
})

const assignedLabels = computed(() => {
  const labels: Record<number, string> = {}
  for (const [studentId, papers] of assignedByStudent.value) {
    labels[studentId] = papers.map((paper) => paper.label).join('、')
  }
  return labels
})

const conflictTargetKeys = computed(() => new Set(
  reviewMatchConflicts.value.flatMap((conflict) => conflict.targets.map(
    (target) => `${target.target_type}:${target.target_id}`,
  )),
))

function rowSortName(row: ReviewRow): string {
  if (row.targetType === 'group') {
    const decision = decisionMap.value.get(row.key)
    const savedStudent = store.students.find((item) => item.id === decision?.student_id)
    return String(
      (decision?.action === 'match' ? savedStudent?.name : undefined)
      || row.item.student_name || row.item.detected_name || '',
    )
  }
  return String(row.item.detected_name || row.item.suggested_student_name || '')
}

const reviewRows = computed<ReviewRow[]>(() => {
  const rows: ReviewRow[] = []
  for (const item of preflightGroups.value) {
    const targetId = String(item.id ?? '')
    const key = `group:${targetId}`
    const decision = decisionMap.value.get(key)
    let bucket: ReviewRow['bucket'] = 'auto'
    if (conflictTargetKeys.value.has(key)) bucket = 'conflict'
    else if (decision?.action === 'match' || decision?.action === 'invalid') bucket = 'done'
    else if (decision?.action === 'pending') bucket = 'unmatched'
    rows.push({ key, targetType: 'group', item, bucket })
  }
  for (const item of store.preflight?.issues ?? []) {
    const targetId = String(item.id ?? '')
    const key = `issue:${targetId}`
    const decision = decisionMap.value.get(key)
    let bucket: ReviewRow['bucket'] = 'unmatched'
    if (conflictTargetKeys.value.has(key)) bucket = 'conflict'
    else if (decision?.action === 'match' || decision?.action === 'invalid') bucket = 'done'
    rows.push({ key, targetType: 'issue', item, bucket })
  }
  const order = { conflict: 0, unmatched: 1, auto: 2, done: 3 } as const
  return rows.sort((a, b) => (
    order[a.bucket] - order[b.bucket]
    || rowSortName(a).localeCompare(rowSortName(b), 'zh-Hans-CN')
    || String(a.item.source_label ?? '').localeCompare(String(b.item.source_label ?? ''), 'zh-Hans-CN')
  ))
})

const reviewCounts = computed<Record<ReviewFilter, number>>(() => {
  const counts: Record<ReviewFilter, number> = {
    todo: 0, auto: 0, done: 0, all: reviewRows.value.length,
  }
  for (const row of reviewRows.value) {
    if (row.bucket === 'conflict' || row.bucket === 'unmatched') counts.todo += 1
    else counts[row.bucket] += 1
  }
  return counts
})
const filteredReviewRows = computed<ReviewRow[]>(() => {
  if (reviewFilter.value === 'all') return reviewRows.value
  if (reviewFilter.value === 'todo') {
    return reviewRows.value.filter(
      (row) => row.bucket === 'conflict' || row.bucket === 'unmatched',
    )
  }
  return reviewRows.value.filter((row) => row.bucket === reviewFilter.value)
})
function defaultReviewFilter(): ReviewFilter {
  return reviewCounts.value.todo > 0 ? 'todo' : 'auto'
}
// 默认筛选只在首次预检载入（或换场次后重新载入）时计算，保存决定不再重置它。
watch(() => store.preflight, (preflight) => {
  if (!preflight || !reviewFilterNeedsDefault) return
  reviewFilterNeedsDefault = false
  reviewFilter.value = defaultReviewFilter()
  selectedRowKey.value = null
  selectedIndex.value = 0
})

interface ReviewListEntry {
  type: 'row' | 'subheader'
  key: string
  row?: ReviewRow
  text?: string
}
// 需处理筛选里把归属冲突的行按冲突小标题分组，未匹配行排在后面。
const reviewListItems = computed<ReviewListEntry[]>(() => {
  const rows = filteredReviewRows.value
  if (reviewFilter.value !== 'todo') {
    return rows.map((row) => ({ type: 'row' as const, key: row.key, row }))
  }
  const items: ReviewListEntry[] = []
  const emitted = new Set<string>()
  for (const card of conflictCards.value) {
    const cardRows = card.targets
      .map((target) => rows.find((row) => row.key === `${target.targetType}:${target.targetId}`))
      .filter((row): row is ReviewRow => Boolean(row))
    if (!cardRows.length) continue
    items.push({
      type: 'subheader',
      key: `conflict:${card.studentId}`,
      text: `${card.studentLabel} · ${card.targets.length} 份`,
    })
    for (const row of cardRows) {
      items.push({ type: 'row', key: row.key, row })
      emitted.add(row.key)
    }
  }
  for (const row of rows) {
    if (!emitted.has(row.key)) items.push({ type: 'row', key: row.key, row })
  }
  return items
})

const selectedRowIndex = computed(() => filteredReviewRows.value.findIndex(
  (row) => row.key === selectedRow.value?.key,
))
const selectedRow = computed<ReviewRow | null>(() => {
  const rows = filteredReviewRows.value
  if (!rows.length) return null
  let index = rows.findIndex((row) => row.key === selectedRowKey.value)
  if (index < 0) index = Math.min(selectedIndex.value, rows.length - 1)
  return rows[index] ?? null
})
const selectedRowId = computed(() => (selectedRow.value ? String(selectedRow.value.item.id) : ''))
function selectRow(row: ReviewRow): void {
  selectedRowKey.value = row.key
  const index = filteredReviewRows.value.findIndex((item) => item.key === row.key)
  if (index >= 0) selectedIndex.value = index
}
function selectRowAt(index: number): void {
  const row = filteredReviewRows.value[Math.min(Math.max(index, 0), filteredReviewRows.value.length - 1)]
  if (row) selectRow(row)
}
function stepRow(offset: number): void {
  const current = selectedRowIndex.value
  if (current < 0) return
  selectRowAt(current + offset)
}
function jumpToRow(key: string): void {
  if (!filteredReviewRows.value.some((row) => row.key === key)) reviewFilter.value = 'todo'
  const row = filteredReviewRows.value.find((item) => item.key === key)
  if (row) selectRow(row)
}
function setReviewFilter(key: ReviewFilter | null): void {
  if (key) reviewFilter.value = key
}
function onReviewListKeydown(event: KeyboardEvent): void {
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  event.preventDefault()
  const rows = filteredReviewRows.value
  if (!rows.length) return
  const current = selectedRowIndex.value
  selectRowAt((current < 0 ? 0 : current) + (event.key === 'ArrowDown' ? 1 : -1))
  void nextTick(() => {
    const key = selectedRow.value?.key
    if (!key) return
    const button = [...document.querySelectorAll<HTMLElement>('.scan-review-item')]
      .find((item) => item.getAttribute('data-row-key') === key)
    button?.focus()
  })
}
const nextNonEmptyFilter = computed<ReviewFilter | null>(() => {
  for (const filter of REVIEW_FILTERS) {
    if (filter.key !== reviewFilter.value && reviewCounts.value[filter.key] > 0) return filter.key
  }
  return null
})
const reviewNeedsHandling = computed(() => (
  reviewCounts.value.todo > 0 || reviewCounts.value.auto > 0
))
const selectedConflict = computed(() => {
  const row = selectedRow.value
  if (!row) return null
  const card = conflictCards.value.find((item) => item.targets.some(
    (target) => `${target.targetType}:${target.targetId}` === row.key,
  ))
  const target = card?.targets.find(
    (item) => `${item.targetType}:${item.targetId}` === row.key,
  ) ?? null
  return card && target ? { card, target } : null
})
const otherConflictTargets = computed(() => {
  const conflict = selectedConflict.value
  const row = selectedRow.value
  if (!conflict || !row) return []
  return conflict.card.targets.filter(
    (target) => `${target.targetType}:${target.targetId}` !== row.key,
  )
})
function rowDisplayName(row: ReviewRow): string {
  const decision = decisionMap.value.get(row.key)
  if (decision?.action === 'match') {
    const student = store.students.find((item) => item.id === decision.student_id)
    if (student?.name) return student.name
  }
  return String(row.item.student_name || row.item.detected_name || '待核对姓名')
}
function pendingPickName(row: ReviewRow): string {
  const studentId = selectedStudents.value[String(row.item.id)]
  if (!studentId) return ''
  const saved = decisionFor(row.targetType, String(row.item.id))
  if (saved?.action === 'match' && saved.student_id === studentId) return ''
  return store.students.find((item) => item.id === studentId)?.name ?? ''
}
const detailImageUrl = computed(() => {
  const row = selectedRow.value
  if (!row) return ''
  return detailSide.value === 'back'
    ? String(row.item.back_media_url ?? '')
    : String(row.item.front_media_url ?? '')
})
watch(() => selectedRow.value?.key, () => {
  detailSide.value = 'front'
})

function preflightItem(targetType: 'group' | 'issue', targetId: string): Record<string, unknown> | undefined {
  const items = targetType === 'group' ? store.preflight?.groups : store.preflight?.issues
  return items?.find((item) => String(item.id ?? '') === targetId)
}

interface ConflictTarget {
  targetType: 'group' | 'issue'
  targetId: string
  item?: Record<string, unknown>
  label: string
  rawLabel: string
  owner: string
}

const conflictCards = computed(() => reviewMatchConflicts.value.map((conflict) => {
  const student = store.students.find((item) => item.id === conflict.student_id)
  const targets: ConflictTarget[] = conflict.targets.map((target) => {
    const item = preflightItem(target.target_type, target.target_id)
    const decision = decisionMap.value.get(`${target.target_type}:${target.target_id}`)
    const rawLabel = String(
      item?.source_label || item?.student_name || item?.detected_name || target.target_id,
    )
    return {
      targetType: target.target_type,
      targetId: target.target_id,
      item,
      label: displaySourceLabel(rawLabel),
      rawLabel,
      owner: decision
        ? { match: '已手动指定', invalid: '已标无效', pending: '已转待处理' }[decision.action]
        : '自动匹配',
    }
  })
  return {
    studentId: conflict.student_id,
    studentLabel: student
      ? [student.name, student.student_code, student.class_name].filter(Boolean).join(' · ')
      : `学生 ${conflict.student_id}`,
    message: conflict.message,
    targets,
  }
}))

function otherAssignedPapers(targetId: string): AssignedPaper[] {
  const studentId = selectedStudents.value[targetId]
  if (!studentId) return []
  return (assignedByStudent.value.get(studentId) ?? []).filter(
    (paper) => paper.targetId !== targetId,
  )
}

function transferMatch(
  targetType: 'group' | 'issue',
  targetId: string,
  otherAction: 'pending' | 'invalid',
): void {
  const studentId = selectedStudents.value[targetId]
  const others = otherAssignedPapers(targetId)
  if (!studentId || !others.length) return
  void submitDecisions([
    { target_type: targetType, target_id: targetId, action: 'match', student_id: studentId },
    ...others.map((paper) => ({
      target_type: paper.targetType,
      target_id: paper.targetId,
      action: otherAction,
    })),
  ])
}

function keepConflictTarget(card: { targets: ConflictTarget[] }, kept: ConflictTarget): void {
  const changes = card.targets
    .filter((target) => target.targetId !== kept.targetId || target.targetType !== kept.targetType)
    .map((target) => ({
      target_type: target.targetType,
      target_id: target.targetId,
      action: 'pending' as const,
    }))
  if (changes.length) void submitDecisions(changes)
}

const MATCH_METHOD_LABELS: Record<string, string> = {
  exact: '精确匹配',
  reduced_fuzzy: '模糊匹配',
  fuzzy: '模糊匹配',
  roster: '名单比对',
}

function rowSuggestions(row: ReviewRow | null): ScanIssueSuggestion[] {
  if (!row || row.targetType !== 'issue') return []
  const list = (row.item as { suggested_students?: ScanIssueSuggestion[] }).suggested_students
  return Array.isArray(list) ? list : []
}

function applySuggestion(row: ReviewRow, suggestion: ScanIssueSuggestion): void {
  selectedStudents.value[String(row.item.id)] = suggestion.student_id
}
function suggestionLabel(suggestion: ScanIssueSuggestion): string {
  const klass = suggestion.class_name
    ? `${suggestion.class_name}${suggestion.class_name.endsWith('班') ? '' : '班'}`
    : ''
  return [suggestion.student_name, klass].filter(Boolean).join(' · ')
}

const PREFLIGHT_TRACK = ['转换页面', '识别姓名与班级', '名单比对', '整理答卷'] as const
const preflightTrackIndex = computed(() => {
  const stage = store.preflightJob?.stage ?? ''
  if (stage === '识别姓名') return 1
  if (stage === '名单比对') return 2
  if (stage === '整理答卷' || stage === '生成结果') return 3
  return 0
})


function matchMethodLabel(item: Record<string, unknown>): string {
  const method = String(item.match_method ?? '')
  const label = MATCH_METHOD_LABELS[method] ?? method
  const score = Number(item.match_score ?? 0)
  return score > 0 && score < 1 ? `${label} ${(score * 100).toFixed(0)}%` : label
}
const selectedMatches = computed<ScanDecision[]>(() => {
  const selected: ScanDecision[] = []
  for (const [targetType, items] of [
    ['group', preflightGroups.value], ['issue', store.preflight?.issues ?? []],
  ] as const) {
    for (const item of items) {
      const targetId = String(item.id)
      const studentId = selectedStudents.value[targetId]
      const saved = decisionFor(targetType, targetId)
      if (!studentId || !item.back_media_url || (saved?.action === 'match' && saved.student_id === studentId)) continue
      selected.push({ target_type: targetType, target_id: targetId, action: 'match', student_id: studentId })
    }
  }
  return selected
})
const runProcessed = computed(() => {
  const counts = store.gradingRun?.counts
  if (!counts) return 0
  return Math.max(0, counts.total - counts.pending - counts.grading)
})
const runProgress = computed(() => {
  const total = store.gradingRun?.counts.total ?? 0
  const savedPaperProgress = total > 0 ? Math.round((runProcessed.value / total) * 100) : 0
  const durableJobProgress = Math.round((store.gradingJob?.progress ?? 0) * 100)
  return Math.min(100, Math.max(savedPaperProgress, durableJobProgress))
})
const runProgressText = computed(() => {
  const total = store.gradingRun?.counts.total ?? 0
  if (total > 0 && runProcessed.value > 0) return `已保存 ${runProcessed.value} / ${total} 份答卷结果`
  if (store.gradingJob) return `后台批改进度 ${runProgress.value}%`
  return runProgressDetail.value
})
const runProgressDetail = computed(() => {
  const job = store.gradingJob
  const detail = job?.detail.trim() ?? ''
  const legacyTotal = detail.match(/^total=(\d+)$/i)
  if (legacyTotal) return `批改队列已建立，共 ${legacyTotal[1]} 份答卷`
  const stage = job?.stage ?? ''
  const stageLabels: Record<string, string> = {
    grading_starting: '正在建立本次批改队列',
    grading_queue_ready: '批改队列已建立，正在准备识别作答',
    grading_objective: '正在识别客观题作答',
    grading_subjective: '正在批改主观题',
    grading_saving: '正在保存批改成绩',
    grading_paused: '本次批改已安全暂停',
    grading_completed: '本次批改已完成',
    grading_cancelled: '本次批改已取消',
    grading_finished: '本次批改处理已结束',
  }
  if (stageLabels[stage]) return stageLabels[stage]
  if (!job && runTerminal.value) {
    return {
      completed: '本次批改已完成',
      failed: '本次运行有失败项',
      cancelled: '本次运行已取消',
    }[store.gradingRun?.state ?? ''] ?? '本次批改处理已结束'
  }
  if (/[\u3400-\u9fff]/u.test(detail)) return detail
  return '正在处理本次批改任务'
})
const interventionSummary = computed(() => interventionQuestions.value.reduce(
  (summary, question) => ({
    total: summary.total + question.total_count,
    ungraded: summary.ungraded + (question.ungraded_count ?? 0),
    failed: summary.failed + (question.failed_count ?? 0),
    review: summary.review + question.needs_review_count,
    aiReady: summary.aiReady + (question.ai_ready_count ?? 0),
    teacher: summary.teacher + (question.teacher_confirmed_count ?? 0),
  }),
  { total: 0, ungraded: 0, failed: 0, review: 0, aiReady: 0, teacher: 0 },
))
const hybridPhase = computed(() => {
  const stage = store.gradingJob?.stage ?? ''
  if (['grading_saving', 'grading_completed', 'grading_finished'].includes(stage)) return 4
  if (stage === 'grading_subjective') return 3
  if (stage === 'grading_objective') return 2
  if (!store.gradingJob && runTerminal.value) return 5
  return 1
})
const hybridAiPending = computed(() => (
  interventionSummary.value.ungraded + interventionSummary.value.failed
))
const runStateLabel = computed(() => ({
  running: '批改运行中', pause_requested: '正在安全暂停', paused: '已安全暂停',
  starting: '批改任务正在启动',
  interrupted: '上次运行已中断', cancel_requested: '正在安全取消', cancelled: '本次运行已取消',
  completed: '本次运行已完成', failed: '本次运行有失败项',
}[store.gradingRun?.state ?? ''] ?? store.gradingRun?.state ?? ''))
// 边框流光只在批改实际运行（含启动与安全收尾）时显示，暂停、中断和终态立即消失。
const gradingRunActive = computed(() => (
  ['running', 'starting', 'pause_requested', 'cancel_requested'].includes(store.gradingRun?.state ?? '')
))
const selectedModeOption = computed(() => gradingModes.find((item) => item.mode === store.selectedMode) ?? null)
const confirmPlanLabel = computed(() => {
  if (store.selectedMode === 'manual') return '进入人工批改'
  return '确认并开始 AI 批改'
})
const requestTotal = computed(() => {
  if (store.selectedMode === 'manual') return 0
  return planNumber(store.gradingPlan?.requests, ['total', 'total_requests', 'request_count'])
})

interface ViewerTarget {
  key: string
  targetType: 'group' | 'issue'
  targetId: string
  title: string
  detectedName: string
  frontUrl: string
  backUrl: string | null
}

function itemText(item: Record<string, unknown>, key: string): string {
  const value = item[key]
  return typeof value === 'string' ? value.trim() : ''
}

function viewerTarget(
  targetType: 'group' | 'issue',
  item: Record<string, unknown>,
): ViewerTarget | null {
  const targetId = itemText(item, 'id')
  const frontUrl = itemText(item, 'front_media_url')
  if (!targetId || !frontUrl) return null
  return {
    key: `${targetType}:${targetId}`,
    targetType,
    targetId,
    title: itemSourceLabel(item) || (targetType === 'group' ? '自动匹配答卷' : '异常答卷'),
    detectedName: itemText(item, 'student_name') || itemText(item, 'detected_name') || '未识别姓名',
    frontUrl,
    backUrl: itemText(item, 'back_media_url') || null,
  }
}

const viewerTargets = computed(() => reviewRows.value.flatMap((row) => {
  const target = viewerTarget(row.targetType, row.item)
  return target ? [target] : []
}))
const activeViewerTarget = computed(() => (
  viewerTargets.value.find((item) => item.key === viewerTargetKey.value) ?? null
))
const activeViewerIndex = computed(() => (
  activeViewerTarget.value
    ? viewerTargets.value.findIndex((item) => item.key === activeViewerTarget.value?.key)
    : -1
))
const activeViewerUrl = computed(() => {
  const target = activeViewerTarget.value
  if (!target) return ''
  return viewerSide.value === 'back' && target.backUrl ? target.backUrl : target.frontUrl
})

async function loadInterventionSummary(id: number): Promise<void> {
  interventionState.value = 'loading'
  try {
    const questions = await fetchReviewQuestions(id, { scope: 'all' })
    if (sessionId.value !== id) return
    interventionQuestions.value = questions
    interventionState.value = 'ready'
  } catch {
    if (sessionId.value !== id) return
    interventionState.value = 'error'
  }
}

async function loadRoute(): Promise<void> {
  const id = sessionId.value
  if (!Number.isSafeInteger(id) || id <= 0) return
  interventionQuestions.value = []
  interventionState.value = 'idle'
  await store.load(id)
  if (sessionId.value === id && store.preflight) void loadInterventionSummary(id)
}

function openPendingReview(): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId.value),
      scope: 'teacher_pending',
      entry: 'intervention',
      ...(firstPendingQuestionId.value ? { question: firstPendingQuestionId.value } : {}),
    },
  })
}
function openResults(): void {
  void router.push({
    path: '/results',
    query: { session: String(sessionId.value) },
  })
}
function openQuestionReview(questionId: string): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId.value),
      scope: 'all',
      entry: 'intervention',
      question: questionId,
    },
  })
}

function questionPending(question: ReviewQuestionSummary): number {
  return (question.ungraded_count ?? 0) + (question.failed_count ?? 0) + question.needs_review_count
}
const firstPendingQuestionId = computed(() => (
  interventionQuestions.value.find((question) => questionPending(question) > 0)?.question_id ?? null
))

const OBJECTIVE_QUESTION_TYPES = new Set(['choice', 'single_choice', 'multi_choice', 'fill_blank'])
const resultsQuestions = computed(() => new Map<string, ResultsCenterQuestion>(
  (resultsStore.results?.questions ?? []).map((question) => [question.question_id, question]),
))
const resultsSummary = computed(() => resultsStore.results?.summary ?? null)
const resultsLoading = computed(() => !resultsStore.results
  && (resultsStore.state === 'idle' || resultsStore.state === 'loading'))
const resultsFailed = computed(() => !resultsStore.results
  && (resultsStore.state === 'error' || resultsStore.state === 'stale-error'))

interface ReviewTile {
  question: ReviewQuestionSummary
  pending: number
  confirmed: number
  rate: number | null
  average: number | null
}
function reviewTile(question: ReviewQuestionSummary): ReviewTile {
  const results = resultsQuestions.value.get(question.question_id)
  const rate = results?.average_score != null && question.max_score > 0
    ? Math.min(100, Math.round((results.average_score / question.max_score) * 100))
    : null
  return {
    question,
    pending: questionPending(question),
    confirmed: question.teacher_confirmed_count ?? 0,
    rate,
    average: results?.average_score ?? null,
  }
}
const reviewRateGroups = computed(() => [
  {
    label: '客观题',
    tiles: interventionQuestions.value
      .filter((question) => OBJECTIVE_QUESTION_TYPES.has(question.question_type ?? ''))
      .map(reviewTile),
  },
  {
    label: '解答题',
    tiles: interventionQuestions.value
      .filter((question) => !OBJECTIVE_QUESTION_TYPES.has(question.question_type ?? ''))
      .map(reviewTile),
  },
].filter((group) => group.tiles.length))
function tileStatus(tile: ReviewTile): string {
  if (tile.pending > 0) return `待处理 ${tile.pending}`
  return tile.confirmed > 0 ? `确认 ${tile.confirmed}` : '未确认'
}
function tileAriaLabel(tile: ReviewTile): string {
  const rateText = tile.rate === null ? '得分率 —' : `得分率 ${tile.rate}%`
  const statusText = tile.pending > 0
    ? `待处理 ${tile.pending} 项`
    : `教师已确认 ${tile.confirmed} 项`
  return `${tile.question.question_id}，${rateText}，${statusText}，进入复核`
}
function tileTone(tile: ReviewTile): string {
  if (tile.rate === null) return ''
  if (tile.rate >= 80) return 'is-high'
  if (tile.rate < 50) return 'is-low'
  return ''
}
const reviewStatusSegments = computed(() => [
  { label: '教师已确认', count: interventionSummary.value.teacher, color: 'var(--color-accent)' },
  { label: 'AI 已完成', count: interventionSummary.value.aiReady, color: 'var(--chart-2)' },
  { label: 'AI 待复核', count: interventionSummary.value.review, color: 'var(--color-warning)' },
  { label: '处理失败', count: interventionSummary.value.failed, color: 'var(--color-danger)' },
  { label: '未评分', count: interventionSummary.value.ungraded, color: 'var(--color-border-strong)' },
].filter((segment) => segment.count > 0)
  .map((segment) => ({
    ...segment,
    percent: interventionSummary.value.total > 0
      ? (segment.count / interventionSummary.value.total) * 100
      : 0,
  })))

const scoreDistribution = computed(() => {
  const bins = Array.from({ length: 10 }, () => 0)
  let complete = 0
  for (const student of resultsStore.results?.students ?? []) {
    if (student.status !== 'complete') continue
    complete += 1
    const ratio = student.max_score > 0 ? student.current_score / student.max_score : 0
    const index = Math.min(9, Math.max(0, Math.floor(ratio * 10)))
    bins[index] = (bins[index] ?? 0) + 1
  }
  return {
    bins,
    incomplete: (resultsStore.results?.students.length ?? 0) - complete,
    max: Math.max(0, ...bins),
  }
})
const scoreDistributionLabel = computed(() => (
  `成绩分布：${scoreDistribution.value.bins
    .map((count, index) => `${index * 10}–${index * 10 + 10}% ${count} 人`)
    .join('，')}`
))
function formatScore(value: number | null): string {
  return value === null ? '—' : String(Math.round(value * 10) / 10)
}
function retryResults(): void {
  if (sessionId.value > 0) void resultsStore.load(sessionId.value)
}
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files?.length) void store.addFiles([...input.files])
  input.value = ''
}
function chooseMode(mode: SelectableGradingMode): void {
  if (!canPreviewPlan.value) return
  void store.previewPlan(mode)
}
function retryPlan(): void {
  if (store.selectedMode && canPreviewPlan.value) void store.previewPlan(store.selectedMode)
}
async function startAutomated(mode: AutomatedGradingMode): Promise<void> {
  gradingSubmissionPending.value = true
  const submission = store.begin(mode, confirmPending.value)
  await nextTick()
  selectStage('grade')
  await submission
  if (store.errorMessage && !store.gradingRun && store.activeJobId === null) {
    gradingSubmissionPending.value = false
  }
}
async function confirmPlan(): Promise<void> {
  const plan = store.gradingPlan
  if (!plan || !canConfirmPlan.value) return
  if (plan.mode === 'manual') {
    await router.push({
      path: '/grading',
      query: {
        session: String(sessionId.value),
        scope: 'all',
        entry: 'manual',
      },
    })
    return
  }
  await startAutomated(plan.mode)
}
function planNumber(source: GradingPlanMetrics | undefined, keys: string[]): number | null {
  if (!source) return null
  for (const key of keys) {
    const raw = source[key]
    const value = typeof raw === 'number'
      ? raw
      : typeof raw === 'string' && raw.trim() !== ''
        ? Number(raw)
        : Number.NaN
    if (Number.isFinite(value) && value >= 0) return value
  }
  return null
}
function formatBytes(bytes: number): string {
  const kb = bytes / 1024
  if (kb < 1024) return `${Math.ceil(kb)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}
// 仅展示用：把 64 位 sha256 文件名换回上传文件名（匹配 sha256_prefix 前 12 位），否则缩写成前 8 位哈希。
function displaySourceLabel(label: string): string {
  const match = /^([0-9a-f]{64})\.(pdf|png|jpe?g)/i.exec(label)
  if (!match) return label
  const hash = match[1]!.toLowerCase()
  const file = [
    ...(store.uploadBatch?.files ?? []),
    ...(store.replacementBatch?.files ?? []),
  ].find((item) => Boolean(item.sha256_prefix)
    && hash.startsWith(item.sha256_prefix!.toLowerCase()))
  const head = file ? file.name : `${hash.slice(0, 8)}….${match[2]}`
  return `${head}${label.slice(match[0].length)}`
}
function itemSourceLabel(item: Record<string, unknown>): string {
  return displaySourceLabel(itemText(item, 'source_label'))
}
function formatPlanNumber(source: GradingPlanMetrics | undefined, keys: string[]): string {
  const value = planNumber(source, keys)
  return value === null ? '—' : value.toLocaleString('zh-CN')
}
function issueText(message: string, count?: number): string {
  return count === undefined ? message : `${message}（${count} 项）`
}
function decisionFor(targetType: 'group' | 'issue', targetId: string): ScanDecision | undefined {
  return store.preflight?.decisions.find((item) => (
    item.target_type === targetType && item.target_id === targetId
  ))
}
function decisionStatus(decision: ScanDecision | undefined): string {
  if (!decision) return ''
  if (decision.action === 'invalid') return '已保存：标记无效'
  if (decision.action === 'pending') return '已保存：稍后处理'
  const student = store.students.find((item) => item.id === decision.student_id)
  return `已保存：匹配至 ${student ? [student.name, student.student_code, student.class_name].filter(Boolean).join(' · ') : '已选学生'}`
}
async function submitDecisions(changes: ScanDecision[], allowPartialMatches = false): Promise<void> {
  if (!changes.length || store.busyAction) return
  // Freeze this click's selection before the saved state updates computed lists.
  changes = changes.map((change) => ({ ...change }))
  decisionNotice.value = ''
  decisionFailed.value = false
  savingDecisionCount.value = changes.length
  const decisions = (store.preflight?.decisions ?? []).filter((item) => !changes.some(
    (change) => change.target_type === item.target_type && change.target_id === item.target_id,
  ))
  const succeeded = await store.saveDecisions([...decisions, ...changes], allowPartialMatches)
  savingDecisionCount.value = 0
  if (!succeeded) {
    decisionFailed.value = true
    decisionNotice.value = `${changes.length} 项匹配未确认保存。${store.errorMessage || '请核对当前结果后重试。'} 当前选择已保留。`
    return
  }
  const accepted = changes.filter((change) => {
    const saved = decisionFor(change.target_type, change.target_id)
    return saved?.action === change.action && (change.action !== 'match' || saved.student_id === change.student_id)
  })
  for (const change of accepted) {
    if (selectedStudents.value[change.target_id] === change.student_id) delete selectedStudents.value[change.target_id]
  }
  const rejected = changes.length - accepted.length
  decisionFailed.value = rejected > 0
  decisionNotice.value = rejected
    ? `已保存 ${accepted.length} 项；${rejected} 项因重复归属未保存。相关答卷已在下方列出，请核对后更正学生或标记重复卷无效。未保存的选择已保留。`
    : `已保存 ${accepted.length} 项；仍有 ${pendingCount.value} 份待处理。`
}
function saveDecision(targetType: 'group' | 'issue', targetId: string, action: 'match' | 'invalid' | 'pending'): void {
  void submitDecisions([{ target_type: targetType, target_id: targetId, action,
    ...(action === 'match' ? { student_id: selectedStudents.value[targetId] } : {}) }])
}
function conflictMessages(targetType: 'group' | 'issue', targetId: string): string {
  return reviewMatchConflicts.value.filter((c) => c.targets.some((t) => t.target_type === targetType && t.target_id === targetId))
    .map((c) => {
      const student = store.students.find((s) => s.id === c.student_id)
      return `${student ? [student.name, student.student_code, student.class_name].filter(Boolean).join(' · ') + '：' : ''}${c.message}`
    }).join(' ')
}
function classEvidence(item: Record<string, unknown>, targetType: 'group' | 'issue'): string {
  if (!item.detected_class_name) return ''
  const saved = decisionFor(targetType, String(item.id))
  const student = store.students.find((s) => s.id === (selectedStudents.value[String(item.id)]
    ?? (saved?.action === 'match' ? saved.student_id : item.student_id)))
  return `卷面班级：${item.detected_class_name}；所选学生班级：${student?.class_name || '尚未选择'}。请看卷核对。`
}
function openViewer(
  targetType: 'group' | 'issue',
  item: Record<string, unknown>,
  side: 'front' | 'back',
): void {
  const target = viewerTarget(targetType, item)
  if (!target) return
  viewerTargetKey.value = target.key
  viewerSide.value = side === 'back' && target.backUrl ? 'back' : 'front'
  viewerZoom.value = 1
  void nextTick(() => {
    if (viewerCanvas.value) {
      viewerCanvas.value.scrollLeft = 0
      viewerCanvas.value.scrollTop = 0
    }
  })
}
function closeViewer(): void {
  viewerTargetKey.value = null
  viewerZoom.value = 1
}
function moveViewer(offset: number): void {
  if (viewerTargets.value.length === 0) return
  const nextIndex = Math.min(
    viewerTargets.value.length - 1,
    Math.max(0, activeViewerIndex.value + offset),
  )
  const next = viewerTargets.value[nextIndex]
  if (!next) return
  viewerTargetKey.value = next.key
  viewerSide.value = 'front'
  viewerZoom.value = 1
  void nextTick(() => {
    if (viewerCanvas.value) {
      viewerCanvas.value.scrollLeft = 0
      viewerCanvas.value.scrollTop = 0
    }
  })
}
function setViewerSide(side: 'front' | 'back'): void {
  if (side === 'back' && !activeViewerTarget.value?.backUrl) return
  viewerSide.value = side
  viewerZoom.value = 1
}
function changeViewerZoom(delta: number): void {
  viewerZoom.value = Math.min(2.5, Math.max(0.75, Number((viewerZoom.value + delta).toFixed(2))))
}
function beginViewerPan(event: PointerEvent): void {
  if (!viewerCanvas.value || viewerZoom.value <= 1) return
  viewerPointerId = event.pointerId
  viewerPointerX = event.clientX
  viewerPointerY = event.clientY
  viewerScrollLeft = viewerCanvas.value.scrollLeft
  viewerScrollTop = viewerCanvas.value.scrollTop
  viewerCanvas.value.setPointerCapture(event.pointerId)
}
function moveViewerPan(event: PointerEvent): void {
  if (!viewerCanvas.value || viewerPointerId !== event.pointerId) return
  viewerCanvas.value.scrollLeft = viewerScrollLeft - (event.clientX - viewerPointerX)
  viewerCanvas.value.scrollTop = viewerScrollTop - (event.clientY - viewerPointerY)
}
function endViewerPan(event: PointerEvent): void {
  if (viewerPointerId !== event.pointerId) return
  viewerPointerId = null
  if (viewerCanvas.value?.hasPointerCapture(event.pointerId)) {
    viewerCanvas.value.releasePointerCapture(event.pointerId)
  }
}
function cancelRun(): void {
  if (window.confirm('取消后，本次运行会结束；已经完成的成绩会保留，未完成部分以后可重新发起。确认取消吗？')) {
    void store.cancel()
  }
}
function confirmReplacement(): void {
  const confirmed = window.confirm(
    '确认改用这批最新答卷？\n\n确认后会永久删除旧答卷、预检结果、批改进度、教师最终分、报表和知识图谱贡献，且无法恢复。考试配置、评分规则、模板、题框和学生名单会保留。',
  )
  if (confirmed) void store.commitReplacement()
}

function onWindowKeydown(event: KeyboardEvent): void {
  if (!activeViewerTarget.value) return
  if (event.key === 'Escape') closeViewer()
  if (event.key === 'ArrowLeft') moveViewer(-1)
  if (event.key === 'ArrowRight') moveViewer(1)
}

onMounted(() => {
  window.addEventListener('keydown', onWindowKeydown)
  void loadRoute()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
})
watch(sessionId, () => {
  selectedStudents.value = {}
  decisionNotice.value = ''
  gradingSubmissionPending.value = false
  reviewFilterNeedsDefault = true
  reviewFilter.value = 'todo'
  selectedRowKey.value = null
  selectedIndex.value = 0
  detailSide.value = 'front'
  closeViewer()
  void loadRoute()
})
watch(() => store.gradingRun, (run) => {
  if (run) gradingSubmissionPending.value = false
})
watch(() => store.workspace?.grading_job?.status ?? null, (status) => {
  if (status && TERMINAL_JOB_STATUSES.has(status) && !store.gradingRun) {
    gradingSubmissionPending.value = false
    if (sessionId.value > 0) void loadInterventionSummary(sessionId.value)
  }
})
watch(
  () => `${store.gradingRun?.state ?? ''}:${store.preflight?.revision ?? -1}`,
  (marker, previous) => {
    const runState = store.gradingRun?.state ?? ''
    if (
      marker !== previous
      && store.preflight
      && sessionId.value > 0
      && (
        previous === undefined
        || ['completed', 'failed', 'cancelled'].includes(runState)
        || marker.split(':').slice(-1)[0] !== previous.split(':').slice(-1)[0]
      )
    ) {
      void loadInterventionSummary(sessionId.value)
    }
  },
)
watch(
  () => `${store.uploadBatch?.batch_id ?? ''}:${store.uploadBatch?.revision ?? -1}:${store.preflight?.revision ?? -1}`,
  () => { confirmPending.value = false },
)
// 复核面板显示且摘要就绪时读取成绩；摘要重载（批改完成等）会再次触发。
watch(
  () => activeStage.value === 'review'
    && interventionState.value === 'ready'
    && interventionSummary.value.total > 0,
  (showReview) => {
    if (showReview && sessionId.value > 0) void resultsStore.load(sessionId.value)
  },
)
</script>

<template>
  <article class="scan-grading">
    <PageHeader title="考试批改">
      <template #actions>
        <StepProgress
          class="scan-stage-rail"
          aria-label="批改执行阶段"
          :steps="stageSteps"
          :current="activeStage"
          @select="selectStage"
        />
      </template>
    </PageHeader>

    <div class="scan-grading__body">
      <main class="scan-grading__main">

    <p v-if="store.errorMessage" class="scan-grading__notice" role="alert">{{ store.errorMessage }}</p>
    <p v-if="store.loadState === 'loading'" class="scan-grading__notice" role="status">正在恢复本次批改工作区…</p>

    <Transition v-if="store.workspace" :name="stageTransition" mode="out-in">
      <section v-if="activeStage === 'prepare'" key="prepare" class="scan-stage" aria-labelledby="prepare-title">
        <div class="scan-stage__heading">
          <h2 id="prepare-title">答卷与预检</h2>
          <div class="scan-stage__side">
            <strong v-if="store.preflight">自动匹配 {{ store.preflight.summary.auto_matched ?? 0 }} · 异常 {{ store.preflight.summary.issues ?? 0 }}</strong>
            <strong v-else>{{ editableUploadBatch?.file_count ?? 0 }} 个文件 · {{ formatBytes(editableUploadBatch?.total_bytes ?? 0) }}</strong>
            <AppButton
              v-if="store.preflight && !matchConflicts.length"
              :variant="pendingCount ? 'secondary' : 'primary'"
              @click="selectStage('grade')"
            >下一步：批改</AppButton>
          </div>
        </div>
        <p v-if="store.replacementBatch" class="scan-warning">
          <strong>正在准备替换答卷。</strong>旧答卷和已有成果目前仍然保留；只有点击“确认替换并开始预检”后才会永久清除。
        </p>
        <div v-if="uploadFrozen" class="scan-upload-summary">
          <div class="scan-upload-summary__files">
            <span v-for="file in editableUploadBatch?.files ?? []" :key="file.id" class="scan-upload-summary__file">
              <strong>{{ file.name }}</strong><small>{{ formatBytes(file.size_bytes) }}</small>
            </span>
          </div>
          <label class="scan-upload-summary__action" :data-disabled="Boolean(store.busyAction)">
            重新上传答卷
            <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              :disabled="Boolean(store.busyAction)" @change="chooseFiles">
          </label>
        </div>
        <label v-else class="scan-drop" :data-disabled="Boolean(store.busyAction)">
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="Boolean(store.busyAction)" @change="chooseFiles">
          <span class="scan-drop__icon" aria-hidden="true">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 16.5V4.81m0 0L8.03 8.03M12 4.81l3.22 3.22" />
              <path d="M3.75 15.75v1.5A2.25 2.25 0 0 0 6 19.5h12a2.25 2.25 0 0 0 2.25-2.25v-1.5" />
            </svg>
          </span>
          <strong>选择或继续添加答卷</strong>
          <span>支持 PDF、JPG、PNG；重复内容会自动跳过。</span>
        </label>
        <div v-if="!uploadFrozen && editableUploadBatch?.files.length" class="scan-file-list">
          <div v-for="file in editableUploadBatch.files" :key="file.id">
            <span><strong>{{ file.name }}</strong><small>{{ file.sha256_prefix }} · {{ formatBytes(file.size_bytes) }}</small></span>
            <button v-if="editableUploadBatch.state === 'draft'" type="button" class="text-button" @click="store.remove(file.id)">移除</button>
          </div>
        </div>
        <div v-if="store.replacementBatch" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空新答卷</button>
          <button type="button" class="secondary" :disabled="Boolean(store.busyAction)" @click="store.cancelReplacement">取消重新上传</button>
          <button type="button" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="confirmReplacement">确认替换并开始预检</button>
        </div>
        <div v-else-if="store.uploadBatch?.state === 'draft'" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空</button>
          <button type="button" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.analyze">开始预检</button>
        </div>

        <div v-if="preflightActive" class="scan-progress" role="status" aria-live="polite">
          <div class="scan-progress__copy">
            <div>
              <strong>扫描预检 {{ preflightProgress }}%</strong>
              <span>{{ preflightProgressText }}</span>
            </div>
            <span>{{ store.preflightJob?.stage || '准备中' }}</span>
          </div>
          <ol class="scan-preflight-track" aria-label="预检步骤">
            <li
              v-for="(label, index) in PREFLIGHT_TRACK" :key="label"
              :class="{ 'is-current': index === preflightTrackIndex, 'is-done': index < preflightTrackIndex }"
            >{{ label }}</li>
          </ol>
          <progress :value="preflightProgress" max="100" aria-label="扫描预检进度">
            {{ preflightProgress }}%
          </progress>
          <p v-if="store.preflight">正在生成新结果，下方暂时显示上一次完整预检结果。</p>
        </div>
        <div v-if="!store.preflight" class="scan-empty">
          <p>{{ store.preflightJobId ? '预检正在后台运行，完成后这里会自动更新。' : '上传答卷后运行识别，再在这里核对学生归属和异常页。' }}</p>
          <button v-if="store.uploadBatch?.state === 'frozen' && !store.preflightJobId" data-action="retry-preflight" type="button" class="secondary" @click="store.analyze">运行或重新运行预检</button>
        </div>
        <template v-else>
          <p v-if="store.preflight.identity" class="scan-identity">
            本机识别，未调用 AI · 自动匹配 {{ store.preflight.identity.auto }} 份 · 需确认 {{ store.preflight.identity.needs_confirmation }} 份
          </p>
          <div class="scan-stats" data-scan-reconciliation>
            <span>扫描 <strong>{{ store.preflight.summary.scanned_papers ?? 0 }}</strong> 份</span>
            <span>已匹配 <strong>{{ store.preflight.summary.matched_papers ?? 0 }}</strong> 份</span>
            <span>对应 <strong>{{ store.preflight.summary.unique_students ?? 0 }}</strong> 名学生</span>
            <span>有效可批改 <strong>{{ store.preflight.summary.ready_to_grade ?? 0 }}</strong> 份</span>
            <span>未决 <strong>{{ pendingCount }}</strong> 份</span>
            <span>无效 <strong>{{ invalidCount }}</strong> 份</span>
            <span v-if="matchConflicts.length" class="scan-stats__alert" role="alert">有 {{ matchConflicts.length }} 份答卷归属冲突，处理后才能开始批改</span>
            <span v-else-if="pendingCount" class="scan-stats__warn">仍有 {{ pendingCount }} 份异常答卷待处理</span>
            <span class="scan-stats__note">PDF 第 1 页为{{ preflightPageAssignment.first_page_role === 'front' ? '正面' : '反面' }}（正面在{{ preflightPageAssignment.front_page_parity === 'odd' ? '奇数页' : '偶数页' }}）</span>
          </div>
          <div v-if="!reviewRows.length" class="scan-review-empty">
            <p>所有答卷已自动匹配。</p>
            <AppButton :variant="pendingCount ? 'secondary' : 'primary'" @click="selectStage('grade')">下一步：批改</AppButton>
          </div>
          <div v-else class="scan-review">
            <nav class="scan-review-nav" aria-label="答卷核对列表" @keydown="onReviewListKeydown">
              <div class="scan-review-filters" role="group" aria-label="核对筛选">
                <button
                  v-for="filter in REVIEW_FILTERS" :key="filter.key" type="button"
                  class="scan-review-filter" :data-review-filter="filter.key"
                  :aria-pressed="reviewFilter === filter.key"
                  @click="reviewFilter = filter.key"
                >{{ filter.label }} {{ reviewCounts[filter.key] }}</button>
              </div>
              <div class="scan-review-list">
                <template v-for="entry in reviewListItems" :key="entry.key">
                  <div v-if="entry.type === 'subheader'" class="scan-review-subheader">{{ entry.text }}</div>
                  <button
                    v-else-if="entry.row" type="button" class="scan-review-item"
                    :data-row-key="entry.row.key"
                    :aria-current="selectedRow?.key === entry.row.key ? 'true' : undefined"
                    @click="selectRow(entry.row)"
                  >
                    <span class="scan-review-item__line">
                      <strong>{{ rowDisplayName(entry.row) }}</strong>
                      <span class="scan-review-item__tag" :data-kind="entry.row.bucket">{{ REVIEW_BUCKET_LABELS[entry.row.bucket] }}</span>
                    </span>
                    <small class="scan-review-item__meta">
                      {{ itemSourceLabel(entry.row.item) || (entry.row.targetType === 'group' ? '答卷' : '异常答卷') }}<template v-if="entry.row.targetType === 'group'"> · {{ matchMethodLabel(entry.row.item) }}</template>
                    </small>
                    <small v-if="pendingPickName(entry.row)" class="scan-review-item__pick">已选：{{ pendingPickName(entry.row) }}</small>
                  </button>
                </template>
              </div>
              <div
                v-if="selectedMatches.length || store.busyAction === 'decisions' || savingDecisionCount || decisionNotice"
                class="scan-review-footer"
              >
                <div v-if="selectedMatches.length || store.busyAction === 'decisions'" class="scan-stage__actions scan-match-actions">
                  <button type="button" class="secondary" data-match-selected :disabled="!selectedMatches.length || Boolean(store.busyAction)" @click="submitDecisions(selectedMatches, true)">
                    {{ store.busyAction === 'decisions' ? '正在保存…' : `一键匹配（${selectedMatches.length} 项）` }}
                  </button>
                  <span>重复归属的项会保留待处理</span>
                </div>
                <p v-if="savingDecisionCount" class="scan-decision-state" role="status">正在保存 {{ savingDecisionCount }} 项，请稍候…</p>
                <p v-else-if="decisionNotice" data-match-result :class="decisionFailed ? 'scan-match-conflict' : 'scan-decision-state'" :role="decisionFailed ? 'alert' : 'status'">{{ decisionNotice }}</p>
              </div>
            </nav>
            <section class="scan-review-detail" aria-live="polite">
              <template v-if="selectedRow">
                <header class="scan-review-detail__header">
                  <div class="scan-review-detail__title">
                    <h3>{{ rowDisplayName(selectedRow) }}<span class="scan-review-item__tag" :data-kind="selectedRow.bucket">{{ REVIEW_BUCKET_LABELS[selectedRow.bucket] }}</span></h3>
                    <small :title="itemText(selectedRow.item, 'source_label') || undefined">
                      {{ itemSourceLabel(selectedRow.item) || (selectedRow.targetType === 'group' ? '答卷' : '异常答卷') }}<template v-if="selectedRow.targetType === 'group'"> · {{ matchMethodLabel(selectedRow.item) }}</template>
                    </small>
                  </div>
                  <div class="scan-review-detail__pager">
                    <button type="button" class="secondary" :disabled="selectedRowIndex <= 0" @click="stepRow(-1)">上一份</button>
                    <button type="button" class="secondary" :disabled="selectedRowIndex < 0 || selectedRowIndex >= filteredReviewRows.length - 1" @click="stepRow(1)">下一份</button>
                    <button type="button" class="secondary" :disabled="!selectedRow.item.front_media_url" @click="openViewer(selectedRow.targetType, selectedRow.item, 'front')">全屏查看</button>
                  </div>
                </header>
                <div class="scan-review-detail__decision">
                  <div class="scan-review-detail__messages">
                    <small v-if="selectedRow.item.detected_name && (selectedRow.targetType === 'issue' || selectedRow.item.student_name)">识别姓名：{{ selectedRow.item.detected_name }}</small>
                    <small v-if="selectedRow.item.issue_type === 'ambiguous_name'">名单中有重名，请按学号和班级选择。</small>
                    <small v-if="selectedRow.targetType === 'issue' && !selectedRow.item.back_media_url" class="scan-match-conflict">缺反面，不能直接归属；可标无效或稍后处理。</small>
                    <small v-if="classEvidence(selectedRow.item, selectedRow.targetType)">{{ classEvidence(selectedRow.item, selectedRow.targetType) }}</small>
                    <small v-if="conflictMessages(selectedRow.targetType, String(selectedRow.item.id))" class="scan-match-conflict">{{ conflictMessages(selectedRow.targetType, String(selectedRow.item.id)) }}</small>
                    <small v-else-if="selectedRow.targetType === 'group' && !decisionFor('group', String(selectedRow.item.id))" class="scan-decision-state">已自动匹配，可直接批改；如有误可更正。</small>
                    <small v-if="decisionFor(selectedRow.targetType, String(selectedRow.item.id))"
                      :data-saved-decision="selectedRow.key" class="scan-decision-state">
                      {{ decisionStatus(decisionFor(selectedRow.targetType, String(selectedRow.item.id))) }}
                    </small>
                  </div>
                  <div v-if="selectedConflict" class="scan-review-detail__conflict">
                    <button type="button" class="secondary" :disabled="Boolean(store.busyAction)"
                      @click="keepConflictTarget(selectedConflict.card, selectedConflict.target)">保留这份</button>
                    <span v-for="other in otherConflictTargets" :key="`${other.targetType}:${other.targetId}`">
                      另一份：<button type="button" class="text-button" @click="jumpToRow(`${other.targetType}:${other.targetId}`)">{{ other.label }}</button>
                    </span>
                  </div>
                  <div v-if="rowSuggestions(selectedRow).length" class="scan-review-detail__suggest">
                    <span>建议</span>
                    <button
                      v-for="(suggestion, index) in rowSuggestions(selectedRow)" :key="suggestion.student_id"
                      type="button" class="scan-suggestion-chip"
                      :data-suggestion-id="suggestion.student_id"
                      :aria-pressed="selectedStudents[selectedRowId] === suggestion.student_id"
                      @click="applySuggestion(selectedRow, suggestion)"
                    >{{ suggestionLabel(suggestion) }}<em v-if="index === 0" class="scan-suggestion-chip__best">最可能</em></button>
                  </div>
                  <div class="scan-review-detail__controls">
                    <StudentMatchSelect
                      v-model="selectedStudents[String(selectedRow.item.id)]"
                      :students="store.students"
                      :assigned="assignedLabels"
                      placeholder="姓名、学号或拼音"
                      :aria-label="selectedRow.targetType === 'group' ? '重新选择学生' : '选择学生'"
                    />
                    <button v-if="!otherAssignedPapers(String(selectedRow.item.id)).length" type="button" class="secondary"
                      :disabled="!selectedStudents[String(selectedRow.item.id)] || !selectedRow.item.back_media_url || Boolean(store.busyAction)"
                      @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'match')">{{ selectedRow.targetType === 'group' ? '确认归属' : '匹配' }}</button>
                    <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'invalid')">标记无效</button>
                    <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision(selectedRow.targetType, String(selectedRow.item.id), 'pending')">稍后处理</button>
                  </div>
                  <div v-if="otherAssignedPapers(String(selectedRow.item.id)).length" class="scan-transfer" data-transfer-panel>
                    <span>
                      {{ store.students.find((s) => s.id === selectedStudents[selectedRowId])?.name || '该学生' }}
                      已归属「{{ otherAssignedPapers(String(selectedRow.item.id)).map((p) => p.label).join('、') }}」。
                    </span>
                    <button type="button" class="secondary" :disabled="!selectedRow.item.back_media_url || Boolean(store.busyAction)"
                      @click="transferMatch(selectedRow.targetType, String(selectedRow.item.id), 'pending')">改用当前卷（原卷转待处理）</button>
                    <button type="button" class="text-button" :disabled="Boolean(store.busyAction)"
                      @click="transferMatch(selectedRow.targetType, String(selectedRow.item.id), 'invalid')">原卷标无效</button>
                  </div>
                </div>
                <div class="scan-review-detail__image">
                  <div class="scan-review-detail__sides" role="group" aria-label="正反面">
                    <button type="button" :aria-pressed="detailSide === 'front'" @click="detailSide = 'front'">正面</button>
                    <button type="button" :disabled="!selectedRow.item.back_media_url" :aria-pressed="detailSide === 'back'" @click="detailSide = 'back'">
                      {{ selectedRow.item.back_media_url ? '反面' : '无反面' }}
                    </button>
                  </div>
                  <img
                    v-if="detailImageUrl"
                    :src="detailImageUrl"
                    :alt="`${itemSourceLabel(selectedRow.item) || '答卷'}${detailSide === 'front' ? '正面' : '反面'}`"
                    loading="lazy" decoding="async"
                  >
                  <p v-else class="scan-review-detail__missing">暂无{{ detailSide === 'front' ? '正面' : '反面' }}图片</p>
                </div>
              </template>
              <div v-else class="scan-review-detail__empty">
                <template v-if="reviewNeedsHandling && nextNonEmptyFilter">
                  <p>这一类已处理完</p>
                  <AppButton variant="secondary" @click="setReviewFilter(nextNonEmptyFilter)">
                    查看{{ REVIEW_FILTERS.find((f) => f.key === nextNonEmptyFilter)?.label }}
                  </AppButton>
                </template>
                <template v-else>
                  <p>可以开始批改</p>
                  <AppButton :variant="pendingCount ? 'secondary' : 'primary'" @click="selectStage('grade')">下一步：批改</AppButton>
                </template>
              </div>
            </section>
          </div>

        </template>
      </section>

      <section v-else-if="activeStage === 'grade'" key="grade" class="scan-stage" aria-labelledby="grade-title">
        <div class="scan-stage__heading">
          <h2 id="grade-title">批改</h2>
          <strong v-if="store.gradingRun">{{ runStateLabel }}</strong>
          <strong v-else-if="gradingCompletedWithoutRun">批改处理已结束</strong>
        </div>
        <div v-if="store.gradingRun" class="run-console">
          <div class="scan-progress scan-progress--run" role="status" aria-live="polite">
            <BorderBeam v-if="gradingRunActive" :size="120" :duration="4" />
            <div class="scan-progress__copy">
              <div>
                <strong>{{ runProgressText }}（{{ runProgress }}%）</strong>
                <span>{{ runProgressDetail }}</span>
              </div>
              <span v-if="store.gradingRun.counts.conflict">
                冲突 {{ store.gradingRun.counts.conflict }}
              </span>
            </div>
            <progress :value="runProgress" max="100" aria-label="批改运行进度">
              {{ runProgress }}%
            </progress>
          </div>
          <section
            v-if="['hybrid_batch', 'ai'].includes(store.gradingRun.mode)"
            class="hybrid-run-board"
            aria-label="AI 批改统计"
          >
            <header>
              <strong>AI 批改流水</strong>
              <b>{{ runStateLabel }}</b>
            </header>
            <ol class="hybrid-run-board__phases">
              <li v-for="(label, index) in ['答卷入队', '客观题识别', '解答题分批', '教师交接']" :key="label" :class="{ 'is-current': hybridPhase === index + 1, 'is-done': hybridPhase > index + 1 }">
                <span>{{ index + 1 }}</span><strong>{{ label }}</strong>
              </li>
            </ol>
            <div class="hybrid-run-board__metrics">
              <span>本轮答卷 <strong>{{ store.gradingRun.counts.total }}</strong></span>
              <span>已完成 <strong>{{ store.gradingRun.counts.graded }}</strong> 个批改单元</span>
              <span>等待 AI <strong>{{ hybridAiPending }}</strong></span>
            </div>
          </section>
          <div v-else class="run-counts">
            <span><strong>已完成 {{ store.gradingRun.counts.graded }}</strong></span>
            <span>处理中 {{ store.gradingRun.counts.grading }}</span><span>待处理 {{ store.gradingRun.counts.pending }}</span>
            <span>跳过 {{ store.gradingRun.counts.skipped }}</span><span>失败 {{ store.gradingRun.counts.failed }}</span>
            <span>冲突 {{ store.gradingRun.counts.conflict }}</span>
          </div>
          <div
            v-if="(store.gradingRun.incomplete_item_count ?? 0) > 0"
            class="scan-warning"
            role="alert"
            data-incomplete-warning
          >
            <strong>有 {{ store.gradingRun.incomplete_item_count }} 个小题没有 AI 评分结果。</strong>
            这些题保留为未评分，不会自动给分；可「仅重试失败项」或在下方人工干预中评分。
          </div>
          <p
            v-if="store.gradingRun.allowed_actions.includes('pause') || store.gradingRun.allowed_actions.includes('cancel')"
            class="run-control-help"
          >安全暂停后可继续；取消后未完成的答卷需重新发起。已保存的成绩都会保留。</p>
          <div class="scan-stage__actions">
            <button v-if="runTerminal" type="button" data-action="go-review" @click="selectStage('review')">去复核</button>
            <button v-if="store.gradingRun.allowed_actions.includes('pause')" data-action="pause" type="button" @click="store.control('pause')">安全暂停（可继续）</button>
            <button v-if="store.gradingRun.allowed_actions.includes('resume')" data-action="resume" type="button" @click="store.control('resume')">继续本次运行</button>
            <button v-if="store.gradingRun.allowed_actions.includes('retry_failed')" data-action="retry-failed" type="button" class="secondary" @click="store.control('retry-failed')">仅重试失败项</button>
            <button v-if="store.gradingRun.allowed_actions.includes('supplement_new_matches')" data-action="supplement" type="button" class="secondary" @click="store.supplement">补批后来匹配的答卷</button>
            <button v-if="store.gradingRun.allowed_actions.includes('cancel')" data-action="cancel" type="button" class="danger" @click="cancelRun">取消本次运行（不可继续）</button>
          </div>
        </div>
        <p v-else-if="gradingStarting" data-grading-starting class="scan-empty" role="status">
          启动请求已接收，正在建立本次批改进度。可以留在本页等待，刷新后也会自动恢复。
        </p>
        <div v-else-if="gradingCompletedWithoutRun" data-grading-completed-without-run class="scan-empty" role="status">
          <p>批改处理已结束，但本次运行进度记录没有生成。任务结束不代表每份答卷都成功，请先核对完成与失败数量；这里不会开放重复提交。</p>
          <div class="scan-stage__actions">
            <button type="button" data-open-grading-results @click="openResults">查看成绩</button>
          </div>
        </div>
        <div v-else class="scan-grade-start" :class="{ 'scan-grade-start--split': Boolean(store.selectedMode) }">
        <div class="scan-grade-start__intro">
        <label v-if="pendingCount" class="scan-confirm">
          <input v-model="confirmPending" data-confirm-pending type="checkbox">
          我已知晓：{{ pendingCount }} 份异常答卷本轮会跳过，之后可继续匹配和补批。
        </label>
        <p v-if="store.preflight" class="scan-start-summary">
          本轮可批改 {{ store.preflight.summary.ready_to_grade ?? 0 }} 份；待处理异常 {{ pendingCount }} 份；
          已标无效 {{ invalidCount }} 份；缺反面/孤页 {{ missingBackCount }} 份；缺考候选 {{ store.preflight.summary.absent_candidates ?? 0 }} 人。
        </p>
        <div class="grading-modes" aria-label="选择批改方式">
          <button v-for="option in gradingModes" :key="option.mode" type="button"
            :data-grading-mode="option.mode" :data-selected="store.selectedMode === option.mode"
            :aria-pressed="store.selectedMode === option.mode" :disabled="!canPreviewPlan"
            @click="chooseMode(option.mode)">
            <span>{{ option.label }}</span>
            <strong>{{ option.title }}</strong>
            <em>{{ option.note }}</em>
          </button>
        </div>
        </div>
        <section v-if="store.selectedMode" class="grading-plan"
          :data-status="store.gradingPlan?.status ?? store.planState"
          aria-labelledby="grading-plan-title" aria-live="polite">
          <header class="grading-plan__header">
            <div>
              <span>执行前确认</span>
              <h3 id="grading-plan-title">{{ selectedModeOption?.label }}计划</h3>
            </div>
            <strong v-if="store.planState === 'loading'">正在计算</strong>
            <strong v-else-if="store.gradingPlan?.status === 'ready'">可以执行</strong>
            <strong v-else-if="store.gradingPlan?.status === 'blocked'">暂不可执行</strong>
            <strong v-else-if="store.planState === 'error'">读取失败</strong>
            <strong v-else>需要重新预览</strong>
          </header>

          <p v-if="store.planState === 'loading'" class="grading-plan__state" role="status">
            正在核对可处理答卷、教师已完成项目和预计 AI 请求…
          </p>
          <div v-else-if="store.planState === 'error'" class="grading-plan__state grading-plan__state--error">
            <p>{{ store.planErrorMessage || '计划没有读取成功，请重新预览。' }}</p>
            <button type="button" class="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</button>
          </div>
          <div v-else-if="store.planState === 'idle'" class="grading-plan__state">
            <p>答卷或核对结果已经变化，执行前需要按最新数据重新计算。</p>
            <button type="button" class="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</button>
          </div>

          <template v-else-if="store.gradingPlan">
            <div class="grading-plan__metrics">
              <div>
                <span>可处理答卷</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts, ['eligible_papers', 'ready_to_grade', 'matched_papers']) }}</strong>
                <small>份</small>
              </div>
              <div>
                <span>教师已完成</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts, ['teacher_locked_items', 'teacher_final_items']) }}</strong>
                <small>题项，不会覆盖</small>
              </div>
              <div>
                <span>{{ store.selectedMode === 'manual' ? '待人工评分' : '本轮 AI 评分' }}</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts,
                  store.selectedMode === 'manual'
                    ? ['manual_target_items', 'pending_items', 'total_score_items']
                    : ['ai_target_items', 'pending_items', 'total_score_items']) }}</strong>
                <small>题项</small>
              </div>
              <div>
                <span>预计 AI 请求</span>
                <strong>{{ requestTotal === null ? '—' : requestTotal.toLocaleString('zh-CN') }}</strong>
                <small>次</small>
              </div>
            </div>

            <p v-if="store.selectedMode === 'manual'" class="grading-plan__batching">
              人工模式不会调用模型。进入后按题查看全班答题区域，教师保存的分数作为最终结果。
            </p>
            <p v-else class="grading-plan__batching">
              客观题整区 {{ formatPlanNumber(store.gradingPlan.requests, ['objective_sheet', 'objective_requests']) }} 次 ·
              解答题 {{ formatPlanNumber(store.gradingPlan.requests, ['subjective_batches', 'subjective_requests']) }} 次
              （每位考生每道解答题一次，整页原图）。<span data-teacher-score-priority-note>已人工确认的分数始终有效，AI 不会替代人工分。</span>
            </p>

            <ul v-if="store.gradingPlan.warnings.length" class="grading-plan__issues grading-plan__issues--warning">
              <li v-for="issue in store.gradingPlan.warnings" :key="`warning:${issue.code}:${issue.message}`">
                {{ issueText(issue.message, issue.count) }}
              </li>
            </ul>
            <ul v-if="store.gradingPlan.blockers.length" class="grading-plan__issues grading-plan__issues--blocked">
              <li v-for="issue in store.gradingPlan.blockers" :key="`blocker:${issue.code}:${issue.message}`">
                {{ issueText(issue.message, issue.count) }}
              </li>
            </ul>

            <div class="grading-plan__confirm">
              <p v-if="store.gradingPlan.status === 'blocked'">请先处理上方问题，再重新预览计划。</p>
              <p v-else-if="pendingCount && !confirmPending">请先勾选上方确认，未匹配的异常答卷才会在本轮安全跳过。</p>
              <p v-else-if="store.selectedMode === 'manual'">确认后只会打开人工评分工作台，不会产生 AI 请求。</p>
              <p v-else>确认后才会正式创建批改任务，并按上方估算调用 AI。</p>
              <AppButton
                variant="primary"
                data-confirm-grading-plan
                :disabled="!canConfirmPlan"
                ripple
                @click="confirmPlan"
              >
                {{ store.gradingPlan.status === 'blocked' ? '当前计划不可执行' : confirmPlanLabel }}
              </AppButton>
            </div>
          </template>
        </section>
        </div>
      </section>

      <section v-else key="review" class="scan-stage" aria-labelledby="review-title">
        <div class="scan-stage__heading">
          <h2 id="review-title">复核</h2>
        </div>
        <p v-if="interventionState === 'loading'" class="scan-empty">正在读取人工干预摘要…</p>
        <p v-else-if="interventionState === 'error'" class="scan-empty">摘要暂时无法读取，仍可进入工作台查看完整队列。</p>
        <template v-else-if="interventionState === 'ready' && interventionSummary.total > 0">
          <div class="review-status" data-review-status>
            <div class="review-status__main">
              <strong
                class="review-status__headline"
                :class="{ 'is-pending': interventionPending > 0 }"
              >{{ interventionPending > 0 ? `还有 ${interventionPending} 项需要老师处理` : 'AI 批改结果已全部就绪' }}</strong>
              <p class="review-status__sub">
                <template v-if="interventionPending > 0">
                  <span><i class="review-dot is-ungraded" aria-hidden="true" />未评分 {{ interventionSummary.ungraded }}</span>
                  <span><i class="review-dot is-failed" aria-hidden="true" />处理失败 {{ interventionSummary.failed }}</span>
                  <span><i class="review-dot is-review" aria-hidden="true" />AI 待复核 {{ interventionSummary.review }}</span>
                </template>
                <template v-else>
                  <span><i class="review-dot is-teacher" aria-hidden="true" />教师已确认 {{ interventionSummary.teacher }} 项</span>
                  <span><i class="review-dot is-ai" aria-hidden="true" />AI 已完成 {{ interventionSummary.aiReady }} 项</span>
                  <span>共 {{ interventionSummary.total }} 项评分</span>
                </template>
              </p>
              <div class="review-status__bar" role="img" :aria-label="reviewStatusSegments.map((segment) => `${segment.label} ${segment.count} 项`).join('，')">
                <i
                  v-for="segment in reviewStatusSegments" :key="segment.label"
                  :style="{ width: `${segment.percent}%`, background: segment.color }"
                  :title="`${segment.label} ${segment.count} 项`"
                />
              </div>
            </div>
            <AppButton
              v-if="interventionPending > 0" variant="primary" data-pending-review
              @click="openPendingReview"
            >处理待复核 {{ interventionPending }} 项</AppButton>
          </div>
          <div class="review-main">
            <section class="review-rates" aria-label="各题得分率">
              <div class="review-rates__head">
                <h3>各题得分率</h3>
                <small>点击题目进入复核</small>
              </div>
              <div v-for="group in reviewRateGroups" :key="group.label" class="review-rate-group">
                <h4>{{ group.label }}</h4>
                <div class="review-rate-grid">
                  <button
                    v-for="tile in group.tiles" :key="tile.question.question_id"
                    type="button"
                    class="review-rate-tile fx-lift"
                    :class="[tileTone(tile), { 'has-pending': tile.pending > 0 }]"
                    :data-question-tile="tile.question.question_id"
                    :aria-label="tileAriaLabel(tile)"
                    @click="openQuestionReview(tile.question.question_id)"
                  >
                    <span class="review-rate-tile__top">
                      <strong :title="tile.question.question_id">{{ tile.question.question_id }}</strong>
                      <small v-if="tile.pending > 0" class="review-rate-tile__badge">待 {{ tile.pending }}</small>
                    </span>
                    <span class="review-rate-tile__rate">
                      <i v-if="resultsLoading" class="review-skeleton" aria-hidden="true" />
                      <template v-else>
                        <strong>{{ tile.rate === null ? '—' : `${tile.rate}%` }}</strong>
                        <small
                          v-if="tile.rate !== null"
                          :title="`均分 ${formatScore(tile.average)}/${tile.question.max_score}`"
                        >{{ formatScore(tile.average) }}/{{ tile.question.max_score }}</small>
                      </template>
                    </span>
                    <small
                      class="review-rate-tile__meta"
                      :class="{ 'is-pending': tile.pending > 0 }"
                    >{{ tileStatus(tile) }}</small>
                    <span class="review-rate-tile__bar" aria-hidden="true">
                      <i :style="{ width: `${tile.rate ?? 0}%` }" />
                    </span>
                  </button>
                </div>
              </div>
            </section>
            <aside class="review-distribution" aria-label="成绩分布">
              <div class="review-rates__head">
                <h3>成绩分布</h3>
              </div>
              <template v-if="resultsLoading">
                <i class="review-skeleton review-skeleton--line" aria-hidden="true" />
                <div class="review-distribution__chart is-skeleton" aria-hidden="true">
                  <i v-for="index in 10" :key="index" class="review-skeleton" />
                </div>
              </template>
              <template v-else-if="resultsFailed">
                <p class="review-distribution__note">成绩暂时无法读取</p>
                <button type="button" class="text-button" @click="retryResults">重试</button>
              </template>
              <template v-else-if="resultsSummary">
                <p class="review-distribution__summary">
                  {{ resultsSummary.student_count }} 人 · 均分 {{ formatScore(resultsSummary.average_score) }} · 最高 {{ formatScore(resultsSummary.highest_score) }} · 最低 {{ formatScore(resultsSummary.lowest_score) }}
                </p>
                <div class="review-distribution__chart" role="img" :aria-label="scoreDistributionLabel">
                  <i
                    v-for="(count, index) in scoreDistribution.bins" :key="index"
                    :data-score-bin="index"
                    :style="{ height: scoreDistribution.max > 0 ? `${(count / scoreDistribution.max) * 100}%` : '0%' }"
                    :title="`${index * 10}–${index * 10 + 10}%：${count} 人`"
                  ><b>{{ count }}</b></i>
                </div>
                <ol class="review-distribution__scale" aria-hidden="true">
                  <li v-for="index in 10" :key="index">{{ (index - 1) * 10 }}</li>
                </ol>
                <p v-if="scoreDistribution.incomplete > 0" class="review-distribution__note">
                  另有 {{ scoreDistribution.incomplete }} 人评分未完成，未计入
                </p>
                <button type="button" class="text-button" @click="openResults">在成绩中心查看明细</button>
              </template>
            </aside>
          </div>
        </template>
        <div v-else-if="interventionState === 'ready'" class="scan-empty">
          <p>还没有评分结果，批改后这里显示各题得分与待处理项。</p>
          <button type="button" class="secondary" @click="selectStage('grade')">去批改</button>
        </div>
        <p v-else class="scan-empty">完成扫描预检后，这里会建立本场考试的人工干预队列。</p>
      </section>
    </Transition>
      </main>
    </div>

    <div
      v-if="activeViewerTarget"
      class="scan-viewer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="scan-viewer-title"
    >
      <div class="scan-viewer__shell">
        <header class="scan-viewer__header">
          <div>
            <span>原卷核对</span>
            <h2 id="scan-viewer-title">{{ activeViewerTarget.title }}</h2>
          </div>
          <button type="button" class="secondary" aria-label="关闭原卷大图" @click="closeViewer">关闭</button>
        </header>
        <div class="scan-viewer__toolbar" aria-label="原卷查看工具">
          <button type="button" :disabled="activeViewerIndex <= 0" @click="moveViewer(-1)">上一份</button>
          <button type="button" :aria-pressed="viewerSide === 'front'" @click="setViewerSide('front')">正面</button>
          <button type="button" :disabled="!activeViewerTarget.backUrl" :aria-pressed="viewerSide === 'back'" @click="setViewerSide('back')">反面</button>
          <button type="button" @click="changeViewerZoom(-0.25)">缩小</button>
          <span>{{ Math.round(viewerZoom * 100) }}%</span>
          <button type="button" @click="changeViewerZoom(0.25)">放大</button>
          <button type="button" :disabled="activeViewerIndex >= viewerTargets.length - 1" @click="moveViewer(1)">下一份</button>
        </div>
        <div class="scan-viewer__body">
          <div
            ref="viewerCanvas"
            class="scan-viewer__canvas"
            :data-pannable="viewerZoom > 1"
            @pointerdown="beginViewerPan"
            @pointermove="moveViewerPan"
            @pointerup="endViewerPan"
            @pointercancel="endViewerPan"
          >
            <img
              :src="activeViewerUrl"
              :alt="`${activeViewerTarget.title}${viewerSide === 'front' ? '正面' : '反面'}整页原卷`"
              :style="{ width: `${viewerZoom * 100}%` }"
            >
          </div>
          <aside class="scan-viewer__decision">
            <span>识别结果</span>
            <strong>{{ activeViewerTarget.detectedName }}</strong>
            <small>{{ activeViewerTarget.title }}</small>
            <StudentMatchSelect
              v-model="selectedStudents[activeViewerTarget.targetId]"
              :students="store.students"
              placeholder="姓名、学号或拼音"
              aria-label="为当前原卷选择学生"
            />
            <button
              type="button"
              :disabled="!selectedStudents[activeViewerTarget.targetId] || !activeViewerTarget.backUrl || Boolean(store.busyAction)"
              @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'match')"
            >
              {{ activeViewerTarget.targetType === 'group' ? '确认归属' : '匹配为该学生' }}
            </button>
            <button type="button" class="secondary" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'invalid')">标记无效</button>
            <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'pending')">稍后处理</button>
            <p v-if="decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)" class="scan-decision-state">
              {{ decisionStatus(decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)) }}
            </p>
          </aside>
        </div>
      </div>
    </div>
  </article>
</template>
