<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  workApi,
  type WorkKind,
  type WorkNode,
  type WorkPlanEdge,
  type WorkPlanPreview,
  type WorkPlanResult,
  type WorkSnapshot,
  type WorkStatus,
} from '../api/work'

const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const notice = ref('')
const snapshot = ref<WorkSnapshot | null>(null)
const workPreview = ref<WorkPlanPreview | null>(null)
const workPlanResult = ref<WorkPlanResult | null>(null)
const taskText = ref('')
const dueDate = ref('')
const selectedCalendarDate = ref(localDate(new Date()))
const weekOffset = ref(0)
const selectedProgressNode = ref<WorkNode | null>(null)
const progressText = ref('')
const progressDueDate = ref('')
const progressPreview = ref<WorkPlanPreview | null>(null)
const progressPlanResult = ref<WorkPlanResult | null>(null)
const workModelOperationId = ref('')
const workPersistOperationId = ref('')
const progressModelOperationId = ref('')
const progressPersistOperationId = ref('')
const statusOperationIds = new Map<string, string>()
const recoveringOperationIds = new Set<string>()
const TRACKED_PLAN_OPERATIONS_KEY = 'class-teacher:ordinary-ai-plan-operations:v1'
let componentActive = true

type WorkEdge = WorkSnapshot['edges'][number]
type PlanningMode = 'new_work' | 'progress_update'

interface TrackedPlanOperation {
  operation_id: string
  mode: PlanningMode
}

interface VisibleGraph {
  goal: WorkNode
  steps: WorkNode[]
  relations: WorkEdge[]
}

interface VisibleBranch {
  branchId: string
  parents: WorkNode[]
  root: WorkNode
  nodes: WorkNode[]
  relations: WorkEdge[]
}

const KIND_LABELS: Record<WorkKind, string> = {
  goal: '目标',
  task: '行动',
  waiting: '等待',
  decision: '决定',
  collection: '收集',
  communication: '沟通',
  sop: '流程',
  restricted_projection: '敏感事项',
}

const STATUS_LABELS: Record<WorkStatus, string> = {
  draft: '待确认',
  pending: '待推进',
  in_progress: '进行中',
  waiting: '等待中',
  completed: '已完成',
  cancelled: '已取消',
}

const monday = computed(() => {
  const value = new Date()
  value.setHours(0, 0, 0, 0)
  const weekday = value.getDay() || 7
  value.setDate(value.getDate() - weekday + 1 + weekOffset.value * 7)
  return value
})

const weekDays = computed(() => Array.from({ length: 7 }, (_, index) => {
  const value = new Date(monday.value)
  value.setDate(value.getDate() + index)
  return {
    iso: localDate(value),
    weekday: `周${'一二三四五六日'[index]}`,
    day: value.getDate(),
    isToday: localDate(value) === localDate(new Date()),
  }
}))

function stableNodeOrder(left: WorkNode, right: WorkNode): number {
  return (left.due_date ?? '9999-12-31').localeCompare(right.due_date ?? '9999-12-31')
    || left.created_at.localeCompare(right.created_at)
    || left.node_id.localeCompare(right.node_id)
}

function connectedComponent(rootId: string): Set<string> {
  const nodes = snapshot.value?.nodes ?? []
  const known = new Set(nodes.map((node) => node.node_id))
  if (!known.has(rootId)) return new Set()
  const adjacency = new Map<string, Set<string>>(
    nodes.map((node) => [node.node_id, new Set<string>()]),
  )
  for (const edge of snapshot.value?.edges ?? []) {
    if (edge.relation === 'review_of') continue
    if (!known.has(edge.source_node_id) || !known.has(edge.target_node_id)) continue
    adjacency.get(edge.source_node_id)?.add(edge.target_node_id)
    adjacency.get(edge.target_node_id)?.add(edge.source_node_id)
  }
  const reached = new Set([rootId])
  const pending = [rootId]
  while (pending.length) {
    const current = pending.pop()!
    for (const next of adjacency.get(current) ?? []) {
      if (reached.has(next)) continue
      reached.add(next)
      pending.push(next)
    }
  }
  return reached
}

function orderedNodes(nodes: WorkNode[], relations: WorkEdge[]): WorkNode[] {
  const byId = new Map(nodes.map((node) => [node.node_id, node]))
  const outgoing = new Map(nodes.map((node) => [node.node_id, new Set<string>()]))
  const indegree = new Map(nodes.map((node) => [node.node_id, 0]))
  for (const edge of relations) {
    if (edge.relation === 'contains') continue
    const before = edge.relation === 'depends_on' ? edge.target_node_id : edge.source_node_id
    const after = edge.relation === 'depends_on' ? edge.source_node_id : edge.target_node_id
    if (!byId.has(before) || !byId.has(after) || outgoing.get(before)?.has(after)) continue
    outgoing.get(before)?.add(after)
    indegree.set(after, (indegree.get(after) ?? 0) + 1)
  }
  const ready = nodes.filter((node) => indegree.get(node.node_id) === 0).sort(stableNodeOrder)
  const ordered: WorkNode[] = []
  while (ready.length) {
    const current = ready.shift()!
    ordered.push(current)
    for (const target of outgoing.get(current.node_id) ?? []) {
      const remaining = (indegree.get(target) ?? 1) - 1
      indegree.set(target, remaining)
      if (remaining === 0) {
        const targetNode = byId.get(target)
        if (targetNode) {
          ready.push(targetNode)
          ready.sort(stableNodeOrder)
        }
      }
    }
  }
  return ordered.length === nodes.length ? ordered : [...nodes].sort(stableNodeOrder)
}

const goalGraphs = computed<VisibleGraph[]>(() => {
  const nodes = snapshot.value?.nodes ?? []
  const edges = snapshot.value?.edges ?? []
  const assigned = new Set<string>()
  const graphs: VisibleGraph[] = []
  for (const goal of nodes.filter((node) => node.kind === 'goal')) {
    if (assigned.has(goal.node_id)) continue
    const component = connectedComponent(goal.node_id)
    component.forEach((nodeId) => assigned.add(nodeId))
    const relations = edges.filter((edge) => (
      edge.relation !== 'review_of'
      && component.has(edge.source_node_id)
      && component.has(edge.target_node_id)
    ))
    const steps = orderedNodes(
      nodes.filter((node) => component.has(node.node_id) && node.node_id !== goal.node_id),
      relations,
    )
    graphs.push({ goal, steps, relations })
  }
  return graphs
})

const graphNodeIds = computed(() => {
  const ids = new Set<string>()
  for (const graph of goalGraphs.value) {
    ids.add(graph.goal.node_id)
    for (const step of graph.steps) ids.add(step.node_id)
  }
  for (const edge of snapshot.value?.edges ?? []) {
    if (edge.relation !== 'review_of') continue
    connectedComponent(edge.target_node_id).forEach((nodeId) => ids.add(nodeId))
  }
  return ids
})

const standaloneNodes = computed(() => (
  (snapshot.value?.nodes ?? []).filter((node) => !graphNodeIds.value.has(node.node_id))
))

const branchGroups = computed<VisibleBranch[]>(() => {
  const edges = snapshot.value?.edges ?? []
  const nodes = snapshot.value?.nodes ?? []
  const byId = new Map(nodes.map((node) => [node.node_id, node]))
  const groups = new Map<string, {
    component: Set<string>
    parentIds: Set<string>
    targetIds: Set<string>
  }>()
  for (const edge of edges) {
    if (edge.relation !== 'review_of') continue
    if (!byId.has(edge.source_node_id) || !byId.has(edge.target_node_id)) continue
    const component = connectedComponent(edge.target_node_id)
    const branchId = [...component].sort().join(':')
    if (!branchId) continue
    const group = groups.get(branchId) ?? {
      component,
      parentIds: new Set<string>(),
      targetIds: new Set<string>(),
    }
    group.parentIds.add(edge.source_node_id)
    group.targetIds.add(edge.target_node_id)
    groups.set(branchId, group)
  }
  const visible = [...groups.entries()].flatMap(([branchId, group]) => {
    const component = group.component
    const branchNodes = nodes.filter((candidate) => component.has(candidate.node_id))
    const relations = edges.filter((edge) => (
      edge.relation !== 'review_of'
      && component.has(edge.source_node_id)
      && component.has(edge.target_node_id)
    ))
    const ordered = orderedNodes(branchNodes, relations)
    const root = ordered.find((candidate) => group.targetIds.has(candidate.node_id))
    if (!root) return []
    const parents = [...group.parentIds]
      .map((parentId) => byId.get(parentId))
      .filter((parent): parent is WorkNode => Boolean(parent))
      .sort(stableNodeOrder)
    return [{ branchId, parents, root, nodes: ordered, relations }]
  })
  return visible.sort((left, right) => stableNodeOrder(left.root, right.root))
})

function localDate(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return '普通工作清单暂时无法更新，已经保存的内容没有改变。'
}

function clearNotices(): void {
  errorMessage.value = ''
  notice.value = ''
}

function trackedOperations(): TrackedPlanOperation[] {
  try {
    const raw = globalThis.localStorage?.getItem(TRACKED_PLAN_OPERATIONS_KEY)
    const decoded: unknown = raw ? JSON.parse(raw) : []
    if (!Array.isArray(decoded)) return []
    return decoded.filter((item): item is TrackedPlanOperation => (
      Boolean(item)
      && typeof item === 'object'
      && typeof (item as TrackedPlanOperation).operation_id === 'string'
      && ['new_work', 'progress_update'].includes((item as TrackedPlanOperation).mode)
    )).slice(-4)
  } catch {
    return []
  }
}

function writeTrackedOperations(entries: TrackedPlanOperation[]): void {
  try {
    if (!entries.length) globalThis.localStorage?.removeItem(TRACKED_PLAN_OPERATIONS_KEY)
    else globalThis.localStorage?.setItem(TRACKED_PLAN_OPERATIONS_KEY, JSON.stringify(entries))
  } catch {
    // Tracking failure never authorizes a second physical model request.
  }
}

function trackOperation(operationId: string, mode: PlanningMode): void {
  const entries = trackedOperations().filter((item) => item.operation_id !== operationId)
  entries.push({ operation_id: operationId, mode })
  writeTrackedOperations(entries)
}

function untrackOperation(operationId: string): void {
  writeTrackedOperations(
    trackedOperations().filter((item) => item.operation_id !== operationId),
  )
}

function applyRecoveredResult(result: WorkPlanResult, mode: PlanningMode): void {
  if (mode === 'new_work') {
    workModelOperationId.value = result.operation_id
    workPlanResult.value = result
    if (result.state === 'succeeded') {
      workPersistOperationId.value ||= globalThis.crypto.randomUUID()
    }
    return
  }
  progressModelOperationId.value = result.operation_id
  progressPlanResult.value = result
  const parentId = typeof result.local_context.parent_node_id === 'string'
    ? result.local_context.parent_node_id
    : ''
  selectedProgressNode.value = (snapshot.value?.nodes ?? [])
    .find((node) => node.node_id === parentId) ?? null
  if (result.state === 'succeeded') {
    progressPersistOperationId.value ||= globalThis.crypto.randomUUID()
  }
}

function recoveryDelay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds))
}

async function recoverOperation(entry: TrackedPlanOperation): Promise<void> {
  if (recoveringOperationIds.has(entry.operation_id)) return
  recoveringOperationIds.add(entry.operation_id)
  try {
    for (let attempt = 0; componentActive && attempt < 75; attempt += 1) {
      const result = await workApi.planStatus(entry.operation_id)
      applyRecoveredResult(result, entry.mode)
      if (result.state !== 'in_progress') {
        if (result.state !== 'succeeded') untrackOperation(entry.operation_id)
        return
      }
      notice.value = 'AI 请求仍在处理中；系统正在核对同一个操作，不会重新发送。'
      await recoveryDelay(1_500)
    }
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    recoveringOperationIds.delete(entry.operation_id)
  }
}

async function recoverTrackedOperations(): Promise<void> {
  await Promise.all(trackedOperations().map(recoverOperation))
}

function invalidateWorkPreview(): void {
  workPreview.value = null
  workPlanResult.value = null
  workModelOperationId.value = ''
  workPersistOperationId.value = ''
}

function discardWorkPlan(): void {
  if (workPlanResult.value) untrackOperation(workPlanResult.value.operation_id)
  invalidateWorkPreview()
}

function selectCalendarDay(dateValue: string): void {
  selectedCalendarDate.value = dateValue
  dueDate.value = dateValue
  invalidateWorkPreview()
  clearNotices()
}

function syncManualDate(): void {
  selectedCalendarDate.value = dueDate.value
  invalidateWorkPreview()
}

function invalidateProgressPreview(): void {
  progressPreview.value = null
  progressPlanResult.value = null
  progressModelOperationId.value = ''
  progressPersistOperationId.value = ''
}

function discardProgressPlan(): void {
  if (progressPlanResult.value) untrackOperation(progressPlanResult.value.operation_id)
  progressPreview.value = null
  progressPlanResult.value = null
  progressModelOperationId.value = ''
  progressPersistOperationId.value = ''
}

function dueCount(dateValue: string): number {
  return (snapshot.value?.nodes ?? []).filter((node) => (
    node.due_date === dateValue
    && !['completed', 'cancelled'].includes(node.status)
  )).length
}

function dueLabel(value: string | null): string {
  if (!value) return '未排期'
  if (value === snapshot.value?.as_of) return '今天'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date(`${value}T00:00:00`))
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const end = weekDays.value[weekDays.value.length - 1]?.iso
    snapshot.value = await workApi.query(monday.value ? localDate(monday.value) : undefined, end)
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    loading.value = false
  }
}

async function moveWeek(amount: number): Promise<void> {
  weekOffset.value += amount
  await refresh()
}

async function previewWork(): Promise<void> {
  if (!taskText.value.trim()) return
  clearNotices()
  saving.value = true
  try {
    workPreview.value = await workApi.previewPlan(
      taskText.value.trim(),
      dueDate.value || null,
    )
    workPlanResult.value = null
    workModelOperationId.value = globalThis.crypto.randomUUID()
    workPersistOperationId.value = ''
  } catch (error) {
    workPreview.value = null
    errorMessage.value = errorText(error)
  } finally {
    saving.value = false
  }
}

async function invokeWorkPlan(): Promise<void> {
  if (!workPreview.value || !workPreview.value.model_enabled) return
  if (!workModelOperationId.value) {
    workModelOperationId.value = globalThis.crypto.randomUUID()
  }
  clearNotices()
  saving.value = true
  trackOperation(workModelOperationId.value, 'new_work')
  try {
    workPlanResult.value = await workApi.invokePlan(
      workPreview.value,
      workModelOperationId.value,
    )
    if (workPlanResult.value.state === 'in_progress') {
      await recoverOperation({
        operation_id: workModelOperationId.value,
        mode: 'new_work',
      })
    } else if (workPlanResult.value.state === 'succeeded') {
      workPersistOperationId.value = globalThis.crypto.randomUUID()
    } else {
      untrackOperation(workModelOperationId.value)
    }
  } catch {
    notice.value = '发送结果暂未返回；系统将查询同一个操作，不会新建或重发请求。'
    await recoverOperation({
      operation_id: workModelOperationId.value,
      mode: 'new_work',
    })
  } finally {
    saving.value = false
  }
}

async function confirmWorkPlan(): Promise<void> {
  if (!workPlanResult.value?.plan || !workPlanResult.value.plan_fingerprint) return
  if (!workPersistOperationId.value) {
    workPersistOperationId.value = globalThis.crypto.randomUUID()
  }
  clearNotices()
  saving.value = true
  try {
    await workApi.confirmPlan(workPlanResult.value, workPersistOperationId.value)
    untrackOperation(workPlanResult.value.operation_id)
    taskText.value = ''
    dueDate.value = ''
    selectedCalendarDate.value = localDate(new Date())
    workPreview.value = null
    workPlanResult.value = null
    workModelOperationId.value = ''
    workPersistOperationId.value = ''
    notice.value = '教师已确认，AI 方案已加入普通工作图。'
    await refresh()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    saving.value = false
  }
}

async function updateStatus(
  node: WorkNode,
  status: Exclude<WorkStatus, 'draft'>,
): Promise<void> {
  clearNotices()
  saving.value = true
  const operationKey = `${node.node_id}:${node.revision}:${status}`
  const operationId = statusOperationIds.get(operationKey) ?? globalThis.crypto.randomUUID()
  statusOperationIds.set(operationKey, operationId)
  try {
    await workApi.update(node, status, operationId)
    statusOperationIds.delete(operationKey)
    await refresh()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    saving.value = false
  }
}

function startProgress(node: WorkNode): void {
  selectedProgressNode.value = node
  progressText.value = ''
  progressDueDate.value = node.due_date ?? ''
  progressPreview.value = null
  progressPlanResult.value = null
  progressModelOperationId.value = ''
  progressPersistOperationId.value = ''
  clearNotices()
}

async function previewProgress(): Promise<void> {
  if (!selectedProgressNode.value || !progressText.value.trim()) return
  clearNotices()
  saving.value = true
  try {
    progressPreview.value = await workApi.previewProgressPlan(
      selectedProgressNode.value,
      progressText.value.trim(),
      progressDueDate.value || null,
    )
    progressPlanResult.value = null
    progressModelOperationId.value = globalThis.crypto.randomUUID()
    progressPersistOperationId.value = ''
  } catch (error) {
    progressPreview.value = null
    errorMessage.value = errorText(error)
  } finally {
    saving.value = false
  }
}

async function invokeProgressPlan(): Promise<void> {
  if (!progressPreview.value?.model_enabled) return
  if (!progressModelOperationId.value) {
    progressModelOperationId.value = globalThis.crypto.randomUUID()
  }
  clearNotices()
  saving.value = true
  trackOperation(progressModelOperationId.value, 'progress_update')
  try {
    progressPlanResult.value = await workApi.invokePlan(
      progressPreview.value,
      progressModelOperationId.value,
    )
    if (progressPlanResult.value.state === 'in_progress') {
      await recoverOperation({
        operation_id: progressModelOperationId.value,
        mode: 'progress_update',
      })
    } else if (progressPlanResult.value.state === 'succeeded') {
      progressPersistOperationId.value = globalThis.crypto.randomUUID()
    } else {
      untrackOperation(progressModelOperationId.value)
    }
  } catch {
    notice.value = '发送结果暂未返回；系统将查询同一个操作，不会新建或重发请求。'
    await recoverOperation({
      operation_id: progressModelOperationId.value,
      mode: 'progress_update',
    })
  } finally {
    saving.value = false
  }
}

async function confirmProgressPlan(): Promise<void> {
  if (!progressPlanResult.value?.plan || !progressPlanResult.value.plan_fingerprint) return
  if (!progressPersistOperationId.value) {
    progressPersistOperationId.value = globalThis.crypto.randomUUID()
  }
  clearNotices()
  saving.value = true
  try {
    await workApi.confirmPlan(progressPlanResult.value, progressPersistOperationId.value)
    untrackOperation(progressPlanResult.value.operation_id)
    selectedProgressNode.value = null
    progressText.value = ''
    progressDueDate.value = ''
    progressPreview.value = null
    progressPlanResult.value = null
    progressModelOperationId.value = ''
    progressPersistOperationId.value = ''
    notice.value = '教师已确认，AI 调整方案已作为分支加入工作图。'
    await refresh()
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    saving.value = false
  }
}

function exactPayload(preview: WorkPlanPreview): string {
  return JSON.stringify(preview.exact_payload, null, 2)
}

const RELATION_LABELS: Record<WorkEdge['relation'], string> = {
  contains: '包含',
  depends_on: '依赖于',
  next: '下一步',
  review_of: '根据最新情况新增',
}

function nodeTitle(nodeId: string): string {
  return (snapshot.value?.nodes ?? []).find((node) => node.node_id === nodeId)?.title
    ?? '未知节点'
}

function relationText(edge: WorkEdge): string {
  return `${nodeTitle(edge.source_node_id)} → ${RELATION_LABELS[edge.relation]} → ${nodeTitle(edge.target_node_id)}`
}

function planRelationText(result: WorkPlanResult, edge: WorkPlanEdge): string {
  const nodes = result.plan?.nodes ?? []
  const source = nodes.find((node) => node.draft_key === edge.source_draft_key)?.title ?? '未知节点'
  const target = nodes.find((node) => node.draft_key === edge.target_draft_key)?.title ?? '未知节点'
  return `${source} → ${RELATION_LABELS[edge.relation]} → ${target}`
}

function hasDirectNext(
  graph: VisibleGraph,
  node: WorkNode,
  nextNode: WorkNode | undefined,
): boolean {
  return Boolean(nextNode && graph.relations.some((edge) => (
    edge.relation === 'next'
    && edge.source_node_id === node.node_id
    && edge.target_node_id === nextNode.node_id
  )))
}

function planStateMessage(result: WorkPlanResult): string {
  if (result.state === 'needs_information') return 'AI 认为信息不足，请补充后重新生成发送预览。'
  if (result.state === 'unavailable') return 'AI 当前不可用；系统没有编造步骤，也没有保存这项工作。'
  if (result.state === 'invalid_result') return 'AI 返回的流程未通过本地校验，没有保存。'
  if (result.state === 'result_unknown') return '本次请求结果未知，系统不会自动重发，以免重复调用或计费。'
  if (result.state === 'in_progress') return 'AI 仍在处理；系统只查询同一个操作，不会重新发送。'
  if (result.state === 'destination_changed') return '服务商或模型已变化，本次未发送。请重新生成并确认新的目的地。'
  return 'AI 已返回待教师复核的流程；确认前不会写入工作图。'
}

onMounted(async () => {
  await refresh()
  await recoverTrackedOperations()
})

onUnmounted(() => {
  componentActive = false
})
</script>

<template>
  <section class="work-board" aria-labelledby="work-board-title">
    <header class="work-board__heading">
      <div>
        <p class="section-kicker">普通工作区 · 无需解锁</p>
        <h2 id="work-board-title">这一周要推进什么</h2>
        <p>这里只放不含学生身份和敏感正文的工作安排；具体学生事项请从页面底部进入。</p>
      </div>
      <div class="week-navigation" aria-label="切换周">
        <button type="button" aria-label="上一周" @click="moveWeek(-1)">‹</button>
        <button type="button" @click="moveWeek(-weekOffset)">本周</button>
        <button type="button" aria-label="下一周" @click="moveWeek(1)">›</button>
      </div>
    </header>

    <div class="week-strip" aria-label="本周日历">
      <button
        v-for="day in weekDays"
        :key="day.iso"
        type="button"
        class="week-day"
        :class="{
          'week-day--today': day.isToday,
          'week-day--selected': selectedCalendarDate === day.iso,
        }"
        :aria-label="`${day.weekday} ${day.iso}，${dueCount(day.iso)} 项工作`"
        :aria-pressed="selectedCalendarDate === day.iso"
        :data-date="day.iso"
        @click="selectCalendarDay(day.iso)"
      >
        <span>{{ day.weekday }}</span>
        <strong>{{ day.day }}</strong>
        <small>{{ dueCount(day.iso) ? `${dueCount(day.iso)} 项` : '—' }}</small>
      </button>
    </div>

    <form class="quick-capture" @submit.prevent="previewWork">
      <label class="quick-capture__text">
        <span class="sr-only">一句话工作目标</span>
        <input
          v-model="taskText"
          maxlength="240"
          placeholder="一句话写下目标、已知要求和最终日期"
          @input="invalidateWorkPreview"
        >
      </label>
      <label>
        <span class="sr-only">已选日历日期，可留空让系统从文字识别</span>
        <input v-model="dueDate" type="date" @input="syncManualDate">
      </label>
      <button type="submit" :disabled="saving || !taskText.trim()">
        {{ saving ? '正在准备…' : '生成 AI 发送预览' }}
      </button>
    </form>
    <p v-if="selectedCalendarDate" class="selected-day-scope" role="status">
      日历已定位到 {{ dueLabel(selectedCalendarDate) }}；点击某天可指定日期，也可以在一句话中直接写日期。
    </p>

    <p v-if="notice" class="work-notice work-notice--success" role="status">{{ notice }}</p>
    <p v-if="errorMessage" class="work-notice work-notice--danger" role="alert">
      {{ errorMessage }}
    </p>

    <div v-if="workPreview" class="model-request-preview">
      <div>
        <strong>即将发送给 AI 的完整内容</strong>
        <span>下面就是唯一的外发消息，没有隐藏提示词；确认后最多请求 1 次。</span>
      </div>
      <dl class="model-destination" aria-label="本次 AI 请求目的地">
        <div><dt>服务商配置</dt><dd>{{ workPreview.model_provider ?? '未配置' }}</dd></div>
        <div><dt>接口</dt><dd>{{ workPreview.model_endpoint ?? '未配置' }}</dd></div>
        <div><dt>模型</dt><dd>{{ workPreview.model_name }}</dd></div>
      </dl>
      <pre>{{ exactPayload(workPreview) }}</pre>
      <p v-if="!workPreview.model_enabled" class="manual-state" role="status">
        AI 当前未启用。系统不会用本地模板伪造步骤，也不会发送或保存这项工作。
      </p>
      <div class="draft-preview__actions">
        <button type="button" class="button-quiet" @click="invalidateWorkPreview">返回修改</button>
        <button
          type="button"
          :disabled="saving || !workPreview.model_enabled"
          @click="invokeWorkPlan"
        >
          {{ workPreview.model_enabled ? '确认发送这份内容' : 'AI 未启用，不会发送' }}
        </button>
      </div>
    </div>

    <div v-if="workPlanResult" class="draft-preview" aria-live="polite">
      <div>
        <strong>AI 返回结果</strong>
        <span>{{ planStateMessage(workPlanResult) }}</span>
      </div>
      <ul v-if="workPlanResult.questions.length" class="planning-notes">
        <li v-for="question in workPlanResult.questions" :key="question">{{ question }}</li>
      </ul>
      <ol v-if="workPlanResult.plan">
        <li v-for="item in workPlanResult.plan.nodes" :key="item.draft_key">
          <span>{{ KIND_LABELS[item.kind] }} · {{ STATUS_LABELS[item.status] }}</span>
          <strong>{{ item.title }}</strong>
          <small>{{ dueLabel(item.due_date) }}</small>
        </li>
      </ol>
      <ul v-if="workPlanResult.plan?.edges.length" class="relation-list" aria-label="AI 方案关系">
        <li
          v-for="edge in workPlanResult.plan.edges"
          :key="`${edge.source_draft_key}:${edge.relation}:${edge.target_draft_key}`"
          :data-relation="edge.relation"
        >
          {{ planRelationText(workPlanResult, edge) }}
        </li>
      </ul>
      <ul v-if="workPlanResult.assumptions.length" class="planning-notes">
        <li v-for="assumption in workPlanResult.assumptions" :key="assumption">
          待教师复核的假设：{{ assumption }}
        </li>
      </ul>
      <div v-if="workPlanResult.plan" class="draft-preview__actions">
        <button type="button" class="button-quiet" @click="discardWorkPlan">放弃方案</button>
        <button type="button" :disabled="saving" @click="confirmWorkPlan">
          教师确认并加入工作图
        </button>
      </div>
    </div>

    <div class="work-summary" aria-label="工作摘要">
      <span><strong>{{ snapshot?.today.length ?? 0 }}</strong> 今天</span>
      <span><strong>{{ snapshot?.overdue.length ?? 0 }}</strong> 已逾期</span>
      <span><strong>{{ snapshot?.waiting.length ?? 0 }}</strong> 等待复查</span>
    </div>

    <p v-if="loading" class="empty-line" aria-live="polite">正在读取普通工作清单…</p>
    <div v-else class="work-graphs">
      <p v-if="!goalGraphs.length && !standaloneNodes.length" class="empty-line">
        还没有工作图。写一句目标，确认拆解后就会在这里形成流程。
      </p>

      <section v-for="graph in goalGraphs" :key="graph.goal.node_id" class="goal-graph">
        <article
          class="graph-node graph-node--goal"
          :data-status="graph.goal.status"
        >
          <div class="graph-node__meta">
            <span>{{ KIND_LABELS[graph.goal.kind] }}</span>
            <span class="state-label" :data-status="graph.goal.status">
              {{ STATUS_LABELS[graph.goal.status] }}
            </span>
          </div>
          <h3>{{ graph.goal.title }}</h3>
          <time :datetime="graph.goal.due_date ?? undefined">{{ dueLabel(graph.goal.due_date) }}</time>
          <div v-if="graph.goal.status !== 'completed'" class="graph-node__actions">
            <button type="button" @click="startProgress(graph.goal)">写最新情况</button>
            <button type="button" :disabled="saving" @click="updateStatus(graph.goal, 'completed')">
              完成
            </button>
          </div>
        </article>

        <div
          v-if="graph.relations.some((edge) => edge.relation === 'contains' && edge.source_node_id === graph.goal.node_id)"
          class="contains-arrow"
          aria-label="目标包含以下步骤"
        >
          <span>包含</span><b aria-hidden="true">↓</b>
        </div>

        <div class="graph-flow">
          <div
            v-for="(node, index) in graph.steps"
            :key="node.node_id"
            class="graph-flow__unit"
            :class="{ 'graph-flow__unit--has-next': hasDirectNext(graph, node, graph.steps[index + 1]) }"
          >
            <article class="graph-node" :data-status="node.status">
              <div class="graph-node__meta">
                <span>{{ KIND_LABELS[node.kind] }}</span>
                <span class="state-label" :data-status="node.status">
                  {{ STATUS_LABELS[node.status] }}
                </span>
              </div>
              <h4>{{ node.title }}</h4>
              <time :datetime="node.due_date ?? undefined">{{ dueLabel(node.due_date) }}</time>
              <div v-if="node.status !== 'completed'" class="graph-node__actions">
                <button type="button" @click="startProgress(node)">写最新情况</button>
                <button
                  v-if="node.status !== 'waiting'"
                  type="button"
                  :disabled="saving"
                  @click="updateStatus(node, 'waiting')"
                >
                  等待
                </button>
                <button type="button" :disabled="saving" @click="updateStatus(node, 'completed')">
                  完成
                </button>
              </div>
            </article>

          </div>
        </div>
        <ul v-if="graph.relations.length" class="relation-list" aria-label="正式工作图关系">
          <li
            v-for="edge in graph.relations"
            :key="`${edge.source_node_id}:${edge.relation}:${edge.target_node_id}`"
            :data-relation="edge.relation"
          >
            {{ relationText(edge) }}
          </li>
        </ul>
      </section>

      <section v-if="standaloneNodes.length" class="standalone-graph">
        <h3>独立事项</h3>
        <div class="graph-flow">
          <article
            v-for="node in standaloneNodes"
            :key="node.node_id"
            class="graph-node"
            :data-status="node.status"
          >
            <div class="graph-node__meta">
              <span>{{ KIND_LABELS[node.kind] }}</span>
              <span class="state-label" :data-status="node.status">
                {{ STATUS_LABELS[node.status] }}
              </span>
            </div>
            <h4>{{ node.title }}</h4>
            <time :datetime="node.due_date ?? undefined">{{ dueLabel(node.due_date) }}</time>
            <div v-if="node.status !== 'completed'" class="graph-node__actions">
              <button type="button" @click="startProgress(node)">写最新情况</button>
              <button type="button" :disabled="saving" @click="updateStatus(node, 'completed')">
                完成
              </button>
            </div>
          </article>
        </div>
      </section>

      <section v-if="branchGroups.length" class="branch-graph" aria-label="根据最新情况新增的工作分支">
        <h3>根据最新情况新增的 AI 分支</h3>
        <div v-for="branch in branchGroups" :key="branch.branchId" class="branch-node">
          <div class="branch-node__heading">
            <span class="branch-node__arrow" aria-hidden="true">↳</span>
            <strong>
              来自“{{ branch.parents.map((parent) => parent.title).join('、') }}”的最新情况
            </strong>
          </div>
          <div class="branch-flow">
            <article
              v-for="branchNode in branch.nodes"
              :key="branchNode.node_id"
              class="graph-node graph-node--branch"
              :data-status="branchNode.status"
            >
              <div class="graph-node__meta">
                <span>最新情况 · {{ KIND_LABELS[branchNode.kind] }}</span>
                <span class="state-label" :data-status="branchNode.status">
                  {{ STATUS_LABELS[branchNode.status] }}
                </span>
              </div>
              <h4>{{ branchNode.title }}</h4>
              <time :datetime="branchNode.due_date ?? undefined">{{ dueLabel(branchNode.due_date) }}</time>
              <div v-if="branchNode.status !== 'completed'" class="graph-node__actions">
                <button type="button" @click="startProgress(branchNode)">继续补充</button>
                <button
                  type="button"
                  :disabled="saving"
                  @click="updateStatus(branchNode, 'completed')"
                >
                  完成
                </button>
              </div>
            </article>
          </div>
          <ul v-if="branch.relations.length" class="relation-list relation-list--branch">
            <li
              v-for="edge in branch.relations"
              :key="`${edge.source_node_id}:${edge.relation}:${edge.target_node_id}`"
              :data-relation="edge.relation"
            >
              {{ relationText(edge) }}
            </li>
          </ul>
        </div>
      </section>
    </div>

    <form
      v-if="selectedProgressNode"
      class="progress-composer"
      @submit.prevent="previewProgress"
    >
      <header>
        <div>
          <p class="section-kicker">为“{{ selectedProgressNode.title }}”添加分支</p>
          <h3>写下最新情况</h3>
        </div>
        <button type="button" class="button-quiet" @click="selectedProgressNode = null">
          关闭
        </button>
      </header>
      <div class="progress-composer__inputs">
        <input
          v-model="progressText"
          maxlength="240"
          placeholder="例如：场地审批已通过，下一步联系摄影老师"
          @input="invalidateProgressPreview"
        >
        <input v-model="progressDueDate" type="date" @input="invalidateProgressPreview">
        <button type="submit" :disabled="saving || !progressText.trim()">生成 AI 调整预览</button>
      </div>
      <div v-if="progressPreview" class="model-request-preview model-request-preview--progress">
        <div>
          <strong>即将发送给 AI 的完整内容</strong>
          <span>确认后最多请求 1 次，返回前不会改变工作图。</span>
        </div>
        <dl class="model-destination" aria-label="本次 AI 调整请求目的地">
          <div><dt>服务商配置</dt><dd>{{ progressPreview.model_provider ?? '未配置' }}</dd></div>
          <div><dt>接口</dt><dd>{{ progressPreview.model_endpoint ?? '未配置' }}</dd></div>
          <div><dt>模型</dt><dd>{{ progressPreview.model_name }}</dd></div>
        </dl>
        <pre>{{ exactPayload(progressPreview) }}</pre>
        <p v-if="!progressPreview.model_enabled" class="manual-state" role="status">
          AI 当前不可用；系统不会把教师原话伪装成 AI 调整方案。
        </p>
        <div class="draft-preview__actions">
          <button type="button" class="button-quiet" @click="invalidateProgressPreview">
            返回修改
          </button>
          <button
            type="button"
            :disabled="saving || !progressPreview.model_enabled"
            @click="invokeProgressPlan"
          >
            {{ progressPreview.model_enabled ? '确认发送这份内容' : 'AI 未启用，不会发送' }}
          </button>
        </div>
      </div>
      <div v-if="progressPlanResult" class="progress-preview" aria-live="polite">
        <span class="branch-node__arrow" aria-hidden="true">↳</span>
        <div>
          <small>{{ planStateMessage(progressPlanResult) }}</small>
          <template v-if="progressPlanResult.plan">
            <strong v-for="item in progressPlanResult.plan.nodes" :key="item.draft_key">
              {{ item.title }} · {{ dueLabel(item.due_date) }}
            </strong>
            <ul v-if="progressPlanResult.plan.edges.length" class="relation-list">
              <li
                v-for="edge in progressPlanResult.plan.edges"
                :key="`${edge.source_draft_key}:${edge.relation}:${edge.target_draft_key}`"
                :data-relation="edge.relation"
              >
                {{ planRelationText(progressPlanResult, edge) }}
              </li>
            </ul>
          </template>
          <span v-for="question in progressPlanResult.questions" :key="question">
            需补充：{{ question }}
          </span>
        </div>
        <div v-if="progressPlanResult.plan" class="draft-preview__actions">
          <button type="button" class="button-quiet" @click="discardProgressPlan">放弃调整</button>
          <button type="button" :disabled="saving" @click="confirmProgressPlan">
            教师确认并加入工作图
          </button>
        </div>
      </div>
    </form>

    <section
      v-if="!selectedProgressNode && progressPlanResult"
      class="progress-preview recovered-progress"
      aria-live="polite"
    >
      <div>
        <small>已恢复上次 AI 调整操作</small>
        <strong>{{ planStateMessage(progressPlanResult) }}</strong>
        <template v-if="progressPlanResult.plan">
          <span v-for="item in progressPlanResult.plan.nodes" :key="item.draft_key">
            {{ item.title }} · {{ dueLabel(item.due_date) }}
          </span>
          <ul v-if="progressPlanResult.plan.edges.length" class="relation-list">
            <li
              v-for="edge in progressPlanResult.plan.edges"
              :key="`${edge.source_draft_key}:${edge.relation}:${edge.target_draft_key}`"
              :data-relation="edge.relation"
            >
              {{ planRelationText(progressPlanResult, edge) }}
            </li>
          </ul>
        </template>
      </div>
      <div class="draft-preview__actions">
        <button type="button" class="button-quiet" @click="discardProgressPlan">放弃调整</button>
        <button
          v-if="progressPlanResult.plan"
          type="button"
          :disabled="saving"
          @click="confirmProgressPlan"
        >
          教师确认并加入工作图
        </button>
      </div>
    </section>
  </section>
</template>

<style scoped>
.work-board {
  margin-bottom: var(--space-8);
  padding-bottom: var(--space-8);
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.work-board__heading,
.week-navigation,
.quick-capture,
.draft-preview > div,
.model-request-preview > div,
.work-summary,
.graph-node__meta,
.graph-node__actions,
.progress-composer > header,
.progress-preview {
  display: flex;
  align-items: center;
}

.work-board__heading {
  justify-content: space-between;
  gap: var(--space-5);
  margin-bottom: var(--space-4);
}

.work-board h2,
.work-board h3,
.work-board h4,
.work-board p {
  margin-top: 0;
}

.work-board h2 {
  margin-bottom: var(--space-1);
  font-size: var(--font-size-h2);
}

.work-board__heading p:last-child {
  margin-bottom: 0;
  color: var(--color-text-secondary);
}

.section-kicker {
  margin-bottom: var(--space-1);
  color: var(--color-accent-active);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: .08em;
}

.week-navigation {
  flex: 0 0 auto;
  gap: var(--space-1);
  padding: var(--space-1);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.week-navigation button,
.quick-capture button,
.draft-preview button,
.model-request-preview button,
.graph-node__actions button,
.progress-composer button,
.progress-preview button {
  min-height: var(--control-height-default);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  cursor: pointer;
}

.week-navigation button {
  min-width: var(--control-height-default);
  border-color: transparent;
  background: transparent;
}

.week-navigation button:hover,
.week-navigation button:focus-visible,
.graph-node__actions button:hover,
.graph-node__actions button:focus-visible {
  background: var(--color-bg-selected);
  outline: none;
}

.week-strip {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel) var(--radius-panel) 0 0;
  background: var(--color-bg-surface);
}

.week-day {
  display: grid;
  place-items: center;
  gap: 2px;
  min-height: 72px;
  padding: var(--space-2);
  border: 0;
  border-inline-start: var(--border-width) solid var(--color-border-subtle);
  border-radius: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}

.week-day:first-child {
  border-inline-start: 0;
}

.week-day strong {
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.week-day small {
  color: var(--color-text-muted);
}

.week-day--today {
  background: var(--color-accent-subtle);
  box-shadow: inset 0 -3px var(--color-accent);
}

.week-day--selected {
  background: var(--color-bg-selected);
  box-shadow: inset 0 0 0 2px var(--color-accent);
}

.week-day:focus-visible {
  position: relative;
  z-index: 1;
  outline: 3px solid var(--color-accent);
  outline-offset: -3px;
}

.week-day--today strong {
  color: var(--color-accent-active);
}

.quick-capture {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 168px auto;
  gap: var(--space-2);
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-top: 0;
  border-radius: 0 0 var(--radius-panel) var(--radius-panel);
  background: var(--color-bg-subtle);
}

.quick-capture input {
  width: 100%;
  min-height: var(--control-height-large);
}

.quick-capture button,
.draft-preview__actions button:last-child {
  padding: 0 var(--space-4);
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
}

.selected-day-scope {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.work-notice {
  margin: var(--space-3) 0 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
}

.work-notice--success {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.work-notice--danger {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.draft-preview {
  margin-top: var(--space-3);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-panel);
  background: var(--color-accent-subtle);
}

.model-request-preview {
  margin-top: var(--space-3);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-ai);
  border-radius: var(--radius-panel);
  background: var(--color-ai-subtle);
}

.model-request-preview--progress {
  margin-bottom: var(--space-3);
}

.model-request-preview > div {
  justify-content: space-between;
  gap: var(--space-4);
}

.model-request-preview > div > span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.model-request-preview pre {
  overflow: auto;
  max-height: 320px;
  margin: var(--space-3) 0;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
  line-height: var(--line-height-normal);
  white-space: pre-wrap;
  word-break: break-word;
}

.model-destination {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
}

.model-destination > div {
  display: grid;
  gap: 2px;
  min-width: 0;
  padding: var(--space-2);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.model-destination dt {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.model-destination dd {
  overflow-wrap: anywhere;
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
}

.manual-state,
.planning-notes {
  margin: var(--space-3) 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.draft-preview > div {
  justify-content: space-between;
  gap: var(--space-4);
}

.draft-preview > div > span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.draft-preview ol {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: var(--space-2);
  margin: var(--space-3) 0;
  padding: 0;
  list-style: none;
}

.draft-preview li {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.draft-preview li > span,
.draft-preview li > small {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.draft-preview__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: flex-end !important;
}

.relation-list,
.draft-preview .relation-list {
  display: grid;
  gap: var(--space-1);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
}

.relation-list li,
.draft-preview .relation-list li {
  display: block;
  padding: var(--space-2) var(--space-3);
  border-inline-start: 3px solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.relation-list li[data-relation="depends_on"] {
  border-inline-start-color: var(--color-warning);
}

.relation-list li[data-relation="next"] {
  border-inline-start-color: var(--color-info);
}

.draft-preview .button-quiet {
  padding: 0 var(--space-3);
  background: transparent;
}

.model-request-preview .button-quiet {
  padding: 0 var(--space-3);
  background: transparent;
}

.work-summary {
  gap: var(--space-6);
  margin-top: var(--space-5);
  padding-bottom: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.work-summary strong {
  margin-inline-end: var(--space-1);
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.work-graphs {
  display: grid;
  gap: var(--space-4);
}

.goal-graph,
.standalone-graph {
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-subtle);
}

.standalone-graph > h3 {
  margin-bottom: var(--space-3);
  font-size: var(--font-size-h3);
}

.graph-node {
  position: relative;
  min-width: 0;
  min-height: 126px;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-inline-start: 4px solid var(--color-info);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.graph-node--goal {
  width: min(100%, 520px);
  min-height: 118px;
  margin: 0 auto;
  border-inline-start-color: var(--color-accent);
}

.graph-node[data-status="waiting"] {
  border-inline-start-color: var(--color-warning);
}

.graph-node[data-status="completed"] {
  border-inline-start-color: var(--color-success);
  background: var(--color-success-subtle);
}

.graph-node[data-status="cancelled"] {
  border-inline-start-color: var(--color-text-muted);
  opacity: .72;
}

.graph-node__meta {
  justify-content: space-between;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.state-label {
  padding: 2px var(--space-2);
  border-radius: var(--radius-tag);
  background: var(--color-info-subtle);
  color: var(--color-info);
  font-size: var(--font-size-caption);
}

.state-label[data-status="waiting"] {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.state-label[data-status="completed"] {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.state-label[data-status="cancelled"] {
  background: var(--color-border-subtle);
  color: var(--color-text-muted);
}

.graph-node h3,
.graph-node h4 {
  margin: var(--space-2) 0;
  color: var(--color-text-primary);
  line-height: var(--line-height-tight);
}

.graph-node time {
  color: var(--color-text-muted);
  font-size: var(--font-size-dense);
}

.graph-node__actions {
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-2);
}

.graph-node__actions button {
  min-height: var(--control-height-small);
  padding: 0 var(--space-2);
  font-size: var(--font-size-caption);
}

.contains-arrow {
  display: grid;
  place-items: center;
  min-height: 54px;
  color: var(--color-accent);
}

.contains-arrow span {
  font-size: var(--font-size-caption);
}

.contains-arrow b {
  font-size: var(--font-size-h2);
  line-height: 1;
}

.graph-flow {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-5) var(--space-7);
}

.graph-flow__unit {
  position: relative;
  min-width: 0;
}

.graph-flow__unit--has-next::after {
  content: "→";
  position: absolute;
  z-index: 1;
  top: 50px;
  right: calc(-1 * var(--space-7) + 8px);
  color: var(--color-accent);
  font-size: var(--font-size-h2);
  font-weight: var(--font-weight-semibold);
}

.graph-flow__unit--has-next:nth-child(3n)::after {
  content: "↓";
  top: auto;
  right: 50%;
  bottom: calc(-1 * var(--space-5) - 4px);
}

.branch-node {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-2) 0 0 var(--space-3);
  padding: var(--space-2);
  border-inline-start: 2px dashed var(--color-ai);
}

.branch-graph {
  display: grid;
  gap: var(--space-2);
}

.branch-graph > h3 {
  margin: 0;
}

.branch-node__heading {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-ai);
  font-size: var(--font-size-dense);
}

.branch-flow {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.branch-node__arrow {
  color: var(--color-ai);
  font-size: var(--font-size-h2);
  font-weight: var(--font-weight-semibold);
}

.graph-node--branch {
  min-height: 112px;
  border-inline-start-color: var(--color-ai);
  background: var(--color-ai-subtle);
}

.progress-composer {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-ai);
  border-radius: var(--radius-panel);
  background: var(--color-ai-subtle);
}

.progress-composer > header {
  justify-content: space-between;
  gap: var(--space-4);
}

.progress-composer h3 {
  margin-bottom: var(--space-3);
}

.progress-composer__inputs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 168px auto;
  gap: var(--space-2);
}

.progress-composer__inputs input {
  min-height: var(--control-height-large);
}

.progress-composer__inputs button,
.progress-preview .draft-preview__actions button:last-child {
  padding: 0 var(--space-4);
  border-color: var(--color-ai);
  background: var(--color-ai);
  color: var(--color-bg-surface);
}

.progress-composer .button-quiet {
  padding: 0 var(--space-3);
  background: transparent;
}

.progress-preview {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: var(--border-width) dashed var(--color-ai);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.progress-preview > div {
  display: grid;
  gap: var(--space-1);
}

.recovered-progress {
  grid-template-columns: minmax(0, 1fr) auto;
  border-style: solid;
}

.progress-preview small,
.progress-preview span {
  color: var(--color-text-muted);
}

.empty-line {
  margin: 0;
  padding: var(--space-5) 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-dense);
}

.sr-only {
  position: absolute;
  overflow: hidden;
  width: 1px;
  height: 1px;
  margin: -1px;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 820px) {
  .work-board__heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .week-day {
    min-height: 62px;
  }

  .week-day span,
  .week-day small {
    font-size: 10px;
  }

  .quick-capture,
  .graph-flow,
  .branch-flow,
  .model-destination,
  .progress-composer__inputs {
    grid-template-columns: 1fr;
  }

  .graph-flow__unit--has-next::after,
  .graph-flow__unit--has-next:nth-child(3n)::after {
    content: "↓";
    top: auto;
    right: 50%;
    bottom: calc(-1 * var(--space-5) - 4px);
  }

  .progress-preview {
    grid-template-columns: 28px minmax(0, 1fr);
  }

  .progress-preview > .draft-preview__actions {
    grid-column: 2;
  }

  .recovered-progress {
    grid-template-columns: 1fr;
  }

  .recovered-progress > .draft-preview__actions {
    grid-column: 1;
  }
}
</style>
