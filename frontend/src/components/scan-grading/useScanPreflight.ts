import { computed, nextTick, ref, watch, type Ref } from 'vue'

import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import type {
  ScanDecision,
  ScanIssueSuggestion,
} from '../../api/scan-grading'
import { useScanGradingStore } from '../../stores/scan-grading'
import { useConfirm } from '../../composables/useConfirm'
import { formatBytes } from '../../lib/format'

export interface AssignedPaper {
  targetType: 'group' | 'issue'
  targetId: string
  label: string
}

export interface ReviewRow {
  key: string
  targetType: 'group' | 'issue'
  item: Record<string, unknown>
  bucket: 'conflict' | 'unmatched' | 'auto' | 'done'
}

export type ReviewFilter = 'todo' | 'auto' | 'done' | 'all'
export const REVIEW_FILTERS: { key: ReviewFilter; label: string }[] = [
  { key: 'todo', label: '需处理' },
  { key: 'auto', label: '自动匹配' },
  { key: 'done', label: '已处理' },
  { key: 'all', label: '全部' },
]
export const REVIEW_BUCKET_LABELS: Record<ReviewRow['bucket'], string> = {
  conflict: '冲突', unmatched: '待匹配', auto: '自动匹配', done: '已处理',
}

export interface ReviewListEntry {
  type: 'row' | 'subheader'
  key: string
  row?: ReviewRow
  text?: string
}

export interface ConflictTarget {
  targetType: 'group' | 'issue'
  targetId: string
  item?: Record<string, unknown>
  label: string
  rawLabel: string
  owner: string
}

export interface ViewerTarget {
  key: string
  targetType: 'group' | 'issue'
  targetId: string
  title: string
  detectedName: string
  frontUrl: string
  backUrl: string | null
}

const MATCH_METHOD_LABELS: Record<string, string> = {
  exact: '精确匹配',
  reduced_fuzzy: '模糊匹配',
  fuzzy: '模糊匹配',
  roster: '名单比对',
}

export const PREFLIGHT_TRACK = ['转换页面', '识别姓名与班级', '名单比对', '整理答卷'] as const

export { formatBytes }

export type ScanPreflightController = ReturnType<typeof useScanPreflight>

// 答卷核对、归属决定与原卷查看器的共享状态。由 ScanGradingView 创建一次并传给
// 预检面板和查看器，切换阶段时不会因组件卸载丢失已选学生和核对进度。
export function useScanPreflight(sessionId: Ref<number>) {
  const store = useScanGradingStore()
  const { confirm } = useConfirm()

  const selectedStudents = ref<Record<string, number | undefined>>({})
  const decisionNotice = ref('')
  const decisionFailed = ref(false)
  const savingDecisionCount = ref(0)
  const viewerTargetKey = ref<string | null>(null)
  const viewerSide = ref<'front' | 'back'>('front')
  const viewerZoom = ref(1)
  const viewerCanvas = ref<HTMLElement | null>(null)
  let viewerPointerId: number | null = null
  let viewerPointerX = 0
  let viewerPointerY = 0
  let viewerScrollLeft = 0
  let viewerScrollTop = 0

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
  const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
  const matchConflicts = computed(() => store.preflight?.match_conflicts ?? [])
  const reviewMatchConflicts = computed(() => [...matchConflicts.value, ...store.decisionConflicts])
  const invalidCount = computed(() => store.preflight?.decisions
    .filter((item) => item.action === 'invalid').length ?? 0)
  const missingBackCount = computed(() => store.preflight?.issues
    .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
  const preflightPageAssignment = computed(() => (
    store.preflight?.page_assignment
    ?? { first_page_role: 'front' as const, front_page_parity: 'odd' as const }
  ))
  const preflightGroups = computed(() => store.preflight?.groups ?? [])

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
    if (!viewerCanvas.value || viewerPointerId !== event.pointerId) return
    viewerPointerId = null
    if (viewerCanvas.value?.hasPointerCapture(event.pointerId)) {
      viewerCanvas.value.releasePointerCapture(event.pointerId)
    }
  }

  function chooseFiles(event: Event): void {
    const input = event.target as HTMLInputElement
    if (input.files?.length) void store.addFiles([...input.files])
    input.value = ''
  }

  function chooseAppendFiles(event: Event): void {
    const input = event.target as HTMLInputElement
    if (input.files?.length) void store.appendFiles([...input.files])
    input.value = ''
  }

  // 冻结批次追加了新文件后，旧预检结果不再覆盖全部答卷，须重新预检。
  const inputChanged = computed(() => store.preflight?.input_changed === true)
  const supplementReady = computed(() => Boolean(
    store.gradingRun?.allowed_actions.includes('supplement_new_matches')
    && !inputChanged.value
    && !preflightActive.value,
  ))

  async function confirmReplacement(): Promise<void> {
    const confirmed = await confirm({
      title: '改用这批最新答卷？',
      message: '确认后会永久删除旧答卷、预检结果、批改进度、教师最终分、报表和知识图谱贡献，且无法恢复。考试配置、评分规则、模板、题框和学生名单会保留。',
      confirmLabel: '改用',
      danger: true,
    })
    if (confirmed) void store.commitReplacement()
  }

  watch(sessionId, () => {
    selectedStudents.value = {}
    decisionNotice.value = ''
    decisionFailed.value = false
    reviewFilterNeedsDefault = true
    reviewFilter.value = 'todo'
    selectedRowKey.value = null
    selectedIndex.value = 0
    detailSide.value = 'front'
    closeViewer()
  })

  return {
    store,
    selectedStudents,
    decisionNotice,
    decisionFailed,
    savingDecisionCount,
    viewerTargetKey,
    viewerSide,
    viewerZoom,
    viewerCanvas,
    editableUploadBatch,
    uploadFrozen,
    inputChanged,
    supplementReady,
    preflightActive,
    preflightProgress,
    preflightProgressText,
    preflightTrackIndex,
    pendingCount,
    matchConflicts,
    reviewMatchConflicts,
    invalidCount,
    missingBackCount,
    preflightPageAssignment,
    preflightGroups,
    reviewFilter,
    selectedRowKey,
    detailSide,
    decisionMap,
    assignedLabels,
    reviewRows,
    reviewCounts,
    filteredReviewRows,
    reviewListItems,
    selectedRowIndex,
    selectedRow,
    selectedRowId,
    nextNonEmptyFilter,
    reviewNeedsHandling,
    selectedConflict,
    otherConflictTargets,
    conflictCards,
    rowSuggestions,
    selectedMatches,
    viewerTargets,
    activeViewerTarget,
    activeViewerIndex,
    activeViewerUrl,
    detailImageUrl,
    PREFLIGHT_TRACK,
    REVIEW_FILTERS,
    REVIEW_BUCKET_LABELS,
    selectRow,
    stepRow,
    jumpToRow,
    setReviewFilter,
    onReviewListKeydown,
    rowDisplayName,
    pendingPickName,
    rowSortName,
    preflightItem,
    otherAssignedPapers,
    applySuggestion,
    suggestionLabel,
    matchMethodLabel,
    itemText,
    displaySourceLabel,
    itemSourceLabel,
    decisionFor,
    decisionStatus,
    submitDecisions,
    saveDecision,
    conflictMessages,
    classEvidence,
    transferMatch,
    keepConflictTarget,
    openViewer,
    closeViewer,
    moveViewer,
    setViewerSide,
    changeViewerZoom,
    beginViewerPan,
    moveViewerPan,
    endViewerPan,
    chooseFiles,
    chooseAppendFiles,
    confirmReplacement,
    formatBytes,
  }
}
