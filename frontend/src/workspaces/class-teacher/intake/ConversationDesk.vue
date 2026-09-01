<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getActivePinia } from 'pinia'

import { intakeApi, type HandlingMode, type HomeroomPreference, type IntakeConversation, type IntakeConversationSummary, type IntakeHandoffSummary, type IntakeTurn } from '../api/intake'
import { workApi, type WorkNode, type WorkSnapshot } from '../api/work'
import { autoAdoptStudentRecordHandoffs } from './auto-adopt'
import { workspaceAITaskApi } from '../../shared/ai-tasks/api'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import AppButton from '@/components/design-system/AppButton.vue'
import { TypewriterText } from '@/components/ui/typewriter'
import LocalVoiceInputButton from './LocalVoiceInputButton.vue'
import { ApiError } from '@/api/errors'

type ClassTeacherDomain =
  | 'student_growth'
  | 'student_support'
  | 'conflict_safety'
  | 'class_operations'
  | 'activities_culture'
  | 'school_coordination'

const props = defineProps<{ conversationId?: string | null; focusWorkItemId?: string | null }>()
const emit = defineEmits<{
  conversationChanged: [conversationId: string, options?: { fresh?: boolean }]
  openHandoff: [handoff: IntakeHandoffSummary]
  openStudentProfile: [handoff: IntakeHandoffSummary]
  openCalendar: []
  openRestricted: [projectionId: string, projectionType: string | null]
  openDomain: [domain: ClassTeacherDomain]
}>()

const conversation = ref<IntakeConversation | null>(null)
const recent = ref<IntakeConversationSummary[]>([])
const nearWork = ref<WorkNode[]>([])
const nearEdges = ref<WorkSnapshot['edges']>([])
const nearAsOf = ref('')
const preference = ref<HomeroomPreference | null>(null)
const message = ref('')
const busy = ref(false)
const notice = ref('')
const error = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
const voiceBusy = ref(false)
const maxMessageChars = 4000
let pollTimer: number | null = null
let pollGeneration = 0

const latestTurn = computed(() => {
  const turns = conversation.value?.turns ?? []
  return turns.length ? turns[turns.length - 1]! : null
})
const pendingHandoffs = computed(() => conversation.value?.handoffs.filter((item) => ['pending', 'opened', 'adoption_started'].includes(item.adoption_state)) ?? [])
// 学生档案卡按学生去重：同一学生只保留最新一张（列表按创建顺序排列），撤回按钮也挂在最新卡上。
// 交接卡片：过期（stale）的草稿也要显示——教师回答追问后旧草稿会作废，
// 但里面仍有已收集的上下文；隐藏会让草稿在模型失败时彻底不可达。
const visibleHandoffs = computed(() => {
  const all = conversation.value?.handoffs ?? []
  const latestByStudent = new Map<string, number>()
  all.forEach((item, index) => {
    if (isStudentRecord(item) && item.subject_id) latestByStudent.set(item.subject_id, index)
  })
  return all.filter((item, index) => {
    if (!isStudentRecord(item) || !item.subject_id) return true
    return latestByStudent.get(item.subject_id) === index
  })
})
const recentConversations = computed(() => recent.value.filter((item) => Boolean(item.first_message)).slice(0, 5))
// 「本周事务」日程带：左侧时间面板按快照 as_of 分已逾期/今天/本周稍后（每组最多露 3 条，其余进日历页），
// 右侧事务卡用 contains 边归并大任务子项、depends_on 边标前置；沟通类与学生支持/关注跟进投影算软性。
const nearById = computed(() => new Map(nearWork.value.map((item) => [item.node_id, item])))
function nearDay(item: WorkNode): string { return (item.due_date ?? '').slice(0, 10) }
function isSoftWork(item: WorkNode): boolean {
  return item.kind === 'communication'
    || item.projection_type === 'student_support'
    || item.projection_type === 'attention_followup'
}
// contains：source=大任务（goal），target=子项；大任务不在未完成列表时子项按独立事项处理。
const parentOf = computed(() => {
  const map = new Map<string, WorkNode>()
  for (const edge of nearEdges.value) {
    if (edge.relation !== 'contains') continue
    const parent = nearById.value.get(edge.source_node_id)
    if (parent && parent.node_id !== edge.target_node_id) map.set(edge.target_node_id, parent)
  }
  return map
})
// depends_on：source=前置，target=被卡住的事项；前置出现在未完成列表里即视为未完成。
const blockedBy = computed(() => {
  const map = new Map<string, WorkNode>()
  for (const edge of nearEdges.value) {
    if (edge.relation !== 'depends_on') continue
    const blocker = nearById.value.get(edge.source_node_id)
    if (blocker) map.set(edge.target_node_id, blocker)
  }
  return map
})
const workLanes = computed(() => {
  const lanes = new Map<string, { node: WorkNode; children: WorkNode[] }>()
  for (const item of nearWork.value) {
    const parent = parentOf.value.get(item.node_id)
    if (!parent) continue
    if (!lanes.has(parent.node_id)) lanes.set(parent.node_id, { node: parent, children: [] })
    lanes.get(parent.node_id)!.children.push(item)
  }
  return [...lanes.values()]
    .map((lane) => ({ ...lane, children: lane.children.slice().sort((a, b) => nearDay(a).localeCompare(nearDay(b))) }))
    .sort((a, b) => nearDay(a.children[0]!).localeCompare(nearDay(b.children[0]!)))
})
// 有可见子项的大任务由车道卡头代表，不再作为普通条目出现在时间面板。
const laneParentIds = computed(() => new Set(workLanes.value.map((lane) => lane.node.node_id)))
const timeItems = computed(() => nearWork.value.filter((item) => !laneParentIds.value.has(item.node_id)))
const timeOver = computed(() => timeItems.value.filter((item) => nearDay(item) && nearDay(item) < nearAsOf.value))
const timeToday = computed(() => timeItems.value.filter((item) => nearDay(item) === nearAsOf.value))
const timeLater = computed(() => timeItems.value.filter((item) => !nearDay(item) || nearDay(item) > nearAsOf.value))
// 时间面板每组最多平铺 3 条，超出的进日历页，保证下方对话框不被顶掉。
const TIME_GROUP_CAP = 3
const timeOverShown = computed(() => timeOver.value.slice(0, TIME_GROUP_CAP))
const timeTodayShown = computed(() => timeToday.value.slice(0, TIME_GROUP_CAP))
const timeLaterShown = computed(() => timeLater.value.slice(0, TIME_GROUP_CAP))
const timeHiddenCount = computed(() =>
  (timeOver.value.length - timeOverShown.value.length)
  + (timeToday.value.length - timeTodayShown.value.length)
  + (timeLater.value.length - timeLaterShown.value.length))
// 右侧车道只补充时间视图看不到的部分：独立事项只列今天之后的，软性事项全列。
const standaloneLater = computed(() => nearWork.value.filter((item) =>
  !parentOf.value.has(item.node_id) && !laneParentIds.value.has(item.node_id)
  && !isSoftWork(item) && nearDay(item) > nearAsOf.value))
const softItems = computed(() => nearWork.value.filter((item) =>
  !parentOf.value.has(item.node_id) && !laneParentIds.value.has(item.node_id) && isSoftWork(item)))

const WEEKDAY_CHARS = '日一二三四五六'
function shiftDay(day: string, delta: number): string {
  const date = new Date(`${day}T00:00:00`)
  date.setDate(date.getDate() + delta)
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${mm}-${dd}`
}
function weekdayChar(day: string): string { return WEEKDAY_CHARS[new Date(`${day}T00:00:00`).getDay()]! }
function shortDay(day: string): string { return day.slice(5).replace('-', '/') }
const asOfLabel = computed(() => {
  if (!nearAsOf.value) return ''
  return `今天 · 周${weekdayChar(nearAsOf.value)} ${shortDay(nearAsOf.value).replace('/', ' / ')}`
})
function nearDueText(item: WorkNode): string {
  const day = nearDay(item)
  if (!day) return '时间待定'
  if (day < nearAsOf.value) return `已逾期 · ${shortDay(day)}`
  if (day === nearAsOf.value) return '今天'
  if (day === shiftDay(nearAsOf.value, 1)) return '明天'
  return `周${weekdayChar(day)} ${shortDay(day)}`
}
function nearDueClass(item: WorkNode): string {
  const day = nearDay(item)
  if (day && day < nearAsOf.value) return 'due--over'
  if (day && day === nearAsOf.value) return 'due--today'
  return 'due--soon'
}
function laneUrgency(items: WorkNode[]): string {
  if (items.some((item) => nearDay(item) && nearDay(item) < nearAsOf.value)) return '有逾期'
  if (items.some((item) => nearDay(item) === nearAsOf.value)) return '今天到期'
  return '本周内'
}
function firstOpenChild(children: WorkNode[]): WorkNode | null {
  return children.find((item) => !blockedBy.value.has(item.node_id)) ?? null
}
function blockedLabel(children: WorkNode[], child: WorkNode): string {
  const blocker = blockedBy.value.get(child.node_id)
  if (!blocker) return ''
  const index = children.findIndex((item) => item.node_id === blocker.node_id)
  return index >= 0 ? `等第 ${index + 1} 步` : `等「${blocker.title}」`
}
const taskInFlight = computed(() => conversation.value?.state === 'ai_running')
// 最新一轮仍有追问且会话已落地时，在输入框上方常驻提示，让"还在等你回答"持续可见。
const awaitingAnswers = computed(() => {
  if (!conversation.value || taskInFlight.value) return []
  return latestTurn.value?.clarification_questions ?? []
})
const domains = [
  ['student_growth', '成长记录', '观察、谈话、阶段变化'],
  ['student_support', '学生支持', '家校沟通、个别关怀'],
  ['conflict_safety', '冲突安全', '冲突、受伤、异常线索'],
  ['class_operations', '班级日常', '值日、通知、常规检查'],
  ['activities_culture', '活动文化', '活动筹备、班级展示'],
  ['school_coordination', '学校协同', '报送、会议、规定流程'],
] as const
const modeLabels: Record<HandlingMode, string> = { record: '登记', plan_calendar: '计划／日历', sop: '处理流程' }
const domainLabels: Record<string, string> = Object.fromEntries(domains.map((item) => [item[0], item[1]]))

function errorText(): string {
  return '这次操作没有完成。已输入内容仍保留在会话中，可刷新后继续或手动选择处理方式。'
}

function taskMessage(state: string): string {
  if (state === 'failed_before_dispatch') return '这次任务尚未发出。原文已保留。'
  if (state === 'result_unknown') return '这次请求可能已经发出，但本机没有可靠结果。系统不会自动重发。'
  if (state === 'invalid_result') return '返回内容未通过校验，没有形成正式草稿。原文仍保留。'
  if (state === 'truncated_result') return '模型输出达到长度上限被截断，没有形成正式草稿。原文仍保留，可点击下方「重新整理」重试。'
  if (state === 'failed') return '这次整理没有完成，系统不会自动再次调用模型。'
  if (state === 'cancel_requested') return '已停止本地后续；外部请求不保证已经撤回。'
  if (state === 'cancelled_before_dispatch') return '任务在发出前已取消，原文仍保留。'
  return '任务正在处理；可以离开页面，稍后再回来。'
}

function stopPolling(): void {
  pollGeneration += 1
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

async function trackTask(taskId: string | null): Promise<void> {
  if (!taskId || !getActivePinia()) return
  try {
    useWorkspaceAITaskStore().track(await workspaceAITaskApi.get(taskId))
  } catch {
    // The conversation remains the domain-owned recovery path.
  }
}

function isStudentRecord(handoff: IntakeHandoffSummary): boolean {
  return handoff.destination_key === 'class_teacher.student.record'
}

function handoffTitle(handoff: IntakeHandoffSummary): string {
  return isStudentRecord(handoff) ? '学生个人档案' : `${modeLabels[handoff.handling_mode]}草稿`
}

function handoffStateLabel(handoff: IntakeHandoffSummary): string {
  if (handoff.adoption_state === 'adopted') return '已并入'
  if (handoff.adoption_state === 'reverted') return '已撤回'
  if (handoff.adoption_state === 'discarded') return '已放弃'
  if (handoff.adoption_state === 'stale') return '已过期'
  return '待核对'
}

function handoffHint(handoff: IntakeHandoffSummary): string {
  if (handoff.adoption_state === 'stale') return '内容在交接后有了新情况；打开核对最新资料，可重新保存或丢弃'
  if (handoff.adoption_state === 'adopted') {
    if (handoff.handling_mode === 'sop' && handoff.affair_id) return '已建立 · 打开处理流程'
    return isStudentRecord(handoff) ? '已自动并入档案，可一键撤回' : '教师已确认保存'
  }
  if (handoff.adoption_state === 'reverted') return '已撤回，档案已恢复'
  if (handoff.adoption_state === 'discarded') return '已丢弃'
  if (isStudentRecord(handoff) && handoff.subject_ref_count !== 1) return '请先在对话里确认是哪名学生'
  if (isStudentRecord(handoff)) return '自动并入未完成时，打开学生档案手动核对'
  return '打开核对，不会自动保存'
}

let autoAdoptBusy = false

function settleHandoffs(next: IntakeConversation): void {
  maybeOpenSingleHandoff(next)
  void autoAdoptStudentRecords(next)
}

async function autoAdoptStudentRecords(next: IntakeConversation): Promise<void> {
  if (autoAdoptBusy) return
  const candidates = next.handoffs.filter((item) =>
    ['pending', 'opened'].includes(item.adoption_state) && isStudentRecord(item))
  if (!candidates.length) return
  autoAdoptBusy = true
  try {
    const outcome = await autoAdoptStudentRecordHandoffs(next)
    if (outcome === 'none') return
    conversation.value = await intakeApi.conversation(next.conversation_id)
    if (outcome === 'adopted') {
      notice.value = '学生档案更新已自动并入当前档案；如不合适可一键撤回。'
    } else {
      error.value = '学生档案自动并入未完成：学生资料可能已变化。请打开学生档案核对后手动应用。'
    }
  } catch {
    error.value = '学生档案自动并入未完成，请打开学生档案核对后手动应用。'
  } finally {
    autoAdoptBusy = false
  }
}

async function revertAdoption(handoff: IntakeHandoffSummary): Promise<void> {
  if (busy.value || !conversation.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await intakeApi.revertProfile(handoff.handoff_id)
    conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
    notice.value = '已撤回，档案已恢复。'
  } catch (value) {
    error.value = value instanceof ApiError && value.status === 409
      ? '档案已有更新轮次，无法一键撤回，请手动修正。'
      : '撤回没有完成，档案保持当前内容，请稍后再试。'
  } finally {
    busy.value = false
  }
}

function maybeOpenSingleHandoff(next: IntakeConversation): void {
  const available = next.handoffs.filter((item) => ['pending', 'opened'].includes(item.adoption_state))
  if (available.length !== 1) return
  const handoff = available[0]!
  if (!handoff.auto_open_allowed || isStudentRecord(handoff)) return
  emit('openHandoff', handoff)
}

function openHandoffCard(handoff: IntakeHandoffSummary): void {
  if (!isStudentRecord(handoff)) {
    emit('openHandoff', handoff)
    return
  }
  if (handoff.subject_ref_count !== 1) {
    error.value = ''
    notice.value = '请先在对话里确认是哪名学生，再打开个人档案。'
    return
  }
  emit('openStudentProfile', handoff)
}

function pollUntilSettled(id: string): void {
  stopPolling()
  const generation = pollGeneration
  const poll = async () => {
    if (generation !== pollGeneration) return
    try {
      const next = await intakeApi.conversation(id)
      if (generation !== pollGeneration) return
      conversation.value = next
      if (next.state === 'ai_running') {
        pollTimer = window.setTimeout(poll, 1500)
      } else {
        pollTimer = null
        if (next.state === 'handoff_ready' || next.state === 'needs_input') {
          notice.value = next.state === 'needs_input' ? 'AI 需要补充少量信息；直接在下方继续回复即可。' : 'AI 草稿已返回，请核对后再决定是否正式保存。'
          settleHandoffs(next)
        }
        await loadRecent()
      }
    } catch {
      pollTimer = window.setTimeout(poll, 2500)
    }
  }
  pollTimer = window.setTimeout(poll, 800)
}

async function loadConversation(id: string): Promise<void> {
  stopPolling()
  busy.value = true; error.value = ''
  try {
    conversation.value = await intakeApi.conversation(id)
    await trackTask(latestTurn.value?.task_id ?? null)
    emit('conversationChanged', id)
    await nextTick()
    document.querySelector<HTMLElement>(`[data-work-item="${props.focusWorkItemId ?? ''}"]`)?.focus()
    if (conversation.value.state === 'ai_running') pollUntilSettled(id)
  } catch { error.value = '这次会话暂时无法读取，请从最近会话重新打开。' }
  finally { busy.value = false }
}

async function start(): Promise<void> {
  if (voiceBusy.value) return
  if (conversation.value && !conversation.value.turns.length) {
    notice.value = ''
    await nextTick(); composer.value?.focus()
    return
  }
  stopPolling()
  busy.value = true; error.value = ''; notice.value = ''
  try {
    conversation.value = await intakeApi.startConversation()
    emit('conversationChanged', conversation.value.conversation_id, { fresh: true })
    message.value = ''
    await nextTick(); composer.value?.focus()
    await loadRecent()
  } catch { error.value = '新会话没有建立，请稍后再试。' }
  finally { busy.value = false }
}

async function send(): Promise<void> {
  if (!conversation.value || !message.value.trim() || busy.value || taskInFlight.value || voiceBusy.value) return
  if (message.value.length > maxMessageChars) {
    notice.value = ''
    error.value = '现有文字加上语音转写超过 4000 字，请先删减后再发送；已回填内容不会丢失。'
    return
  }
  const outgoing = message.value.trim()
  busy.value = true; error.value = ''; notice.value = '正在整理；离开页面后仍可从最近会话返回。'
  try {
    conversation.value = await intakeApi.appendTurn(conversation.value, outgoing)
    await trackTask(latestTurn.value?.task_id ?? null)
    message.value = ''
    if (conversation.value.state === 'failed') notice.value = 'AI 任务没有发出。原文已保留，可直接选择一种处理方式继续。'
    else if (conversation.value.state === 'ai_running') pollUntilSettled(conversation.value.conversation_id)
    else settleHandoffs(conversation.value)
    await loadRecent()
  } catch { error.value = errorText() }
  finally { busy.value = false }
}

function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.isComposing) return
  event.preventDefault()
  void send()
}

async function applyVoiceTranscript(text: string): Promise<void> {
  const current = message.value.trimEnd()
  message.value = current ? `${current}\n${text}` : text
  error.value = ''
  await nextTick()
  if (message.value.length > maxMessageChars) {
    notice.value = ''
    error.value = '现有文字加上语音转写超过 4000 字，请先删减后再发送；已回填内容不会丢失。'
  }
  composer.value?.focus()
}

function voiceInfo(value: string): void {
  error.value = ''
  notice.value = value
}

function voiceError(value: string): void {
  notice.value = ''
  error.value = value
}

function cloudVoiceError(value: unknown): string {
  const code = value && typeof value === 'object' && 'code' in value
    ? String((value as { code: unknown }).code)
    : ''
  if (code === 'class_teacher_cloud_audio_result_unknown') {
    return '这次语音可能已经发给模型，但没有收到可靠结果。系统不会重复发送；录音仍可改为本机文字。'
  }
  if (code === 'class_teacher_model_destination_changed') {
    return '模型配置已经变化，因此录音没有发送。录音仍可改为本机文字。'
  }
  if (code === 'class_teacher_cloud_audio_unavailable') {
    return '当前模型不能直接接收语音。录音仍可改为本机文字。'
  }
  if (code === 'class_teacher_cloud_audio_invalid_result') {
    return '模型没有返回可用的转写和草稿。录音仍可改为本机文字。'
  }
  return '这次语音发送没有完成，系统不会重复发送。录音仍可改为本机文字。'
}

async function submitCloudAudio(wav: Blob, operationId: string, fingerprint: string, signal: AbortSignal): Promise<boolean> {
  if (!conversation.value || busy.value || taskInFlight.value) return false
  error.value = ''
  notice.value = '正在把录音交给模型整理；系统不会自动重试。'
  try {
    const next = await intakeApi.sendCloudAudio(conversation.value, wav, operationId, fingerprint, signal)
    conversation.value = next
    notice.value = next.state === 'needs_input'
      ? '语音已转写，AI 还需要补充少量信息；请先核对原话，再继续回复。'
      : '语音已转写并形成草稿，请先核对姓名、日期、数字和否定词，再决定是否保存。'
    settleHandoffs(next)
    await loadRecent()
    return true
  } catch (value) {
    notice.value = ''
    error.value = cloudVoiceError(value)
    return false
  }
}

async function retryTurn(turn: IntakeTurn): Promise<void> {
  if (!conversation.value || busy.value || taskInFlight.value || voiceBusy.value) return
  busy.value = true; error.value = ''; notice.value = '正在按原文重新整理这一轮；系统不会自动重复发送。'
  try {
    conversation.value = await intakeApi.appendTurn(conversation.value, turn.teacher_message)
    await trackTask(latestTurn.value?.task_id ?? null)
    if (conversation.value.state === 'ai_running') pollUntilSettled(conversation.value.conversation_id)
    else settleHandoffs(conversation.value)
    await loadRecent()
  } catch { error.value = errorText() }
  finally { busy.value = false }
}

async function manual(mode: HandlingMode): Promise<void> {
  if (!latestTurn.value || busy.value) return
  busy.value = true; error.value = ''
  try { conversation.value = await intakeApi.manualRoute(latestTurn.value.turn_id, mode) }
  catch { error.value = errorText() }
  finally { busy.value = false }
}

async function loadRecent(): Promise<void> {
  try { recent.value = await intakeApi.listConversations() } catch { recent.value = [] }
}

async function loadNearWork(): Promise<void> {
  try {
    const snapshot = await workApi.read('week')
    nearWork.value = snapshot.nodes
      .filter((item) => !['completed', 'cancelled'].includes(item.status))
      .sort((left, right) => String(left.due_date || '9999').localeCompare(String(right.due_date || '9999')))
    nearEdges.value = snapshot.edges
    nearAsOf.value = snapshot.as_of.slice(0, 10) || shiftDay(new Date().toISOString().slice(0, 10), 0)
  } catch {
    nearWork.value = []
    nearEdges.value = []
    nearAsOf.value = ''
  }
}

// 事务车道左右轮切：卡片多时按卡片宽度翻页，翻不动时原生滚动条兜底；
// 放得下（无横向溢出）时翻页按钮隐藏。
const lanesEl = ref<HTMLElement | null>(null)
const lanesOverflow = ref(false)
function syncLanesOverflow(): void {
  const el = lanesEl.value
  lanesOverflow.value = Boolean(el && el.scrollWidth > el.clientWidth + 4)
}
function scrollLanes(dir: number): void {
  lanesEl.value?.scrollBy({ left: dir * 360, behavior: 'smooth' })
}

async function openNearWork(item: WorkNode): Promise<void> {
  if (item.classification !== 'restricted_projection') {
    emit('openCalendar')
    return
  }
  try {
    const detail = await workApi.detail(item.node_id)
    if (detail.projection_id) {
      emit('openRestricted', detail.projection_id, item.projection_type ?? null)
      return
    }
  } catch {
    // 详情暂时读不到时退回日历视图。
  }
  emit('openCalendar')
}

// 卡片快捷操作：✓ 完成并归档（completed 后自然离开未完成列表）；✕ 彻底删除（不可恢复，confirm 兜底）。
// 受限学生事项按安全规则只能跳转学生页，不给快捷按钮；等前置的事项不给打勾，完成前置后自动解锁。
const quickBusy = ref<string | null>(null)
function canQuickComplete(item: WorkNode): boolean {
  return item.classification !== 'restricted_projection' && !blockedBy.value.has(item.node_id)
}
function canQuickDelete(item: WorkNode): boolean {
  return item.classification !== 'restricted_projection'
}
async function quickComplete(item: WorkNode): Promise<void> {
  if (quickBusy.value) return
  quickBusy.value = item.node_id
  error.value = ''
  notice.value = ''
  try {
    await workApi.command(item, 'update_status', { status: 'completed' })
    await loadNearWork()
    notice.value = `「${item.title}」已完成并归档。`
  } catch {
    error.value = '完成操作没有成功，事项可能已在其他页面变化。请刷新后再试。'
  } finally {
    quickBusy.value = null
  }
}
async function quickDelete(item: WorkNode): Promise<void> {
  if (quickBusy.value) return
  if (!window.confirm(`彻底删除「${item.title}」后无法恢复：日期、进展和上下游关系会一并删除。`)) return
  quickBusy.value = item.node_id
  error.value = ''
  notice.value = ''
  try {
    await workApi.command(item, 'delete')
    await loadNearWork()
    notice.value = `「${item.title}」已彻底删除。`
  } catch {
    error.value = '删除没有完成，事项可能已在其他页面变化。请刷新后再试。'
  } finally {
    quickBusy.value = null
  }
}

async function removeConversation(item: IntakeConversationSummary): Promise<void> {
  if (busy.value || voiceBusy.value) return
  if (!window.confirm('删除后无法恢复这段对话。已经写入学生档案或日历的正式记录不会被删。')) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await intakeApi.deleteConversation(item.conversation_id)
    const wasCurrent = conversation.value?.conversation_id === item.conversation_id
    if (wasCurrent) {
      stopPolling()
      conversation.value = null
      message.value = ''
    }
    await loadRecent()
    if (wasCurrent) await start()
    notice.value = '这段对话已删除。'
  } catch {
    error.value = '这段对话没有删除，请稍后再试。'
  } finally {
    busy.value = false
  }
}

watch(() => props.conversationId, (id) => { if (id && id !== conversation.value?.conversation_id) void loadConversation(id) })
watch(nearWork, async () => { await nextTick(); syncLanesOverflow() })
function onWindowResize(): void { syncLanesOverflow() }
onBeforeUnmount(() => { stopPolling(); window.removeEventListener('resize', onWindowResize) })
onMounted(async () => {
  window.addEventListener('resize', onWindowResize)
  await Promise.all([
    loadRecent(),
    loadNearWork(),
    intakeApi.homeroom()
      .then((value) => { preference.value = value })
      .catch(() => { preference.value = null; error.value = '默认班级暂时无法读取；仍可处理不涉及学生的事务。' }),
  ])
  await nextTick()
  syncLanesOverflow()
  if (props.conversationId) await loadConversation(props.conversationId)
  else await start()
})
</script>

<template>
  <section class="desk" aria-label="班主任案头">
    <header class="desk__masthead">
      <p class="desk__homeroom"><span>我的班主任班级</span><strong>{{ preference?.homeroom_class || '尚未设置班级' }}</strong></p>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="desk__body">
      <div class="desk__main">
    <section v-if="nearWork.length" class="week-strip" aria-label="本周事务">
      <header>
        <span class="week-strip__title">本周事务 · 未完成 <strong>{{ nearWork.length }}</strong> 项</span>
        <span class="week-strip__legend">🔒 有前置任务，完成后才开始</span>
        <button type="button" class="week-strip__all" @click="emit('openCalendar')">在日历中查看全部 ›</button>
      </header>
      <div class="week-strip__grid">
        <aside class="time-panel" aria-label="按时间看">
          <p class="time-panel__kicker">按时间看</p>
          <p class="time-panel__today">{{ asOfLabel }}</p>
          <div v-if="timeOver.length" class="time-sec time-sec--over">
            <h3>已逾期<span>{{ timeOver.length }} 项</span></h3>
            <div v-for="item in timeOverShown" :key="item.node_id" class="rowline">
              <button type="button" :class="{ 'is-blocked': blockedBy.has(item.node_id) }" @click="openNearWork(item)">
                <span class="time-sec__date">{{ shortDay(nearDay(item)) }}</span>
                <span class="time-sec__title">{{ item.title }}</span>
                <span v-if="blockedBy.has(item.node_id)" class="time-sec__lock">🔒 等前置</span>
              </button>
              <span v-if="canQuickDelete(item)" class="qa">
                <button v-if="canQuickComplete(item)" type="button" class="qa__done" :disabled="quickBusy === item.node_id" title="完成并归档" @click="quickComplete(item)">✓</button>
                <button type="button" class="qa__del" :disabled="quickBusy === item.node_id" title="彻底删除（不可恢复）" @click="quickDelete(item)">✕</button>
              </span>
            </div>
          </div>
          <div v-if="timeToday.length" class="time-sec time-sec--today">
            <h3>今天到期<span>{{ timeToday.length }} 项</span></h3>
            <div v-for="item in timeTodayShown" :key="item.node_id" class="rowline">
              <button type="button" :class="{ 'is-blocked': blockedBy.has(item.node_id) }" @click="openNearWork(item)">
                <span class="time-sec__date">今天</span>
                <span class="time-sec__title">{{ item.title }}</span>
                <span v-if="blockedBy.has(item.node_id)" class="time-sec__lock">🔒 等前置</span>
              </button>
              <span v-if="canQuickDelete(item)" class="qa">
                <button v-if="canQuickComplete(item)" type="button" class="qa__done" :disabled="quickBusy === item.node_id" title="完成并归档" @click="quickComplete(item)">✓</button>
                <button type="button" class="qa__del" :disabled="quickBusy === item.node_id" title="彻底删除（不可恢复）" @click="quickDelete(item)">✕</button>
              </span>
            </div>
          </div>
          <div v-if="timeLaterShown.length" class="time-sec time-sec--later">
            <h3>本周稍后<span>{{ timeLater.length }} 项</span></h3>
            <div v-for="item in timeLaterShown" :key="item.node_id" class="rowline">
              <button type="button" :class="{ 'is-blocked': blockedBy.has(item.node_id) }" @click="openNearWork(item)">
                <span class="time-sec__date">{{ nearDay(item) ? shortDay(nearDay(item)) : '待定' }}</span>
                <span class="time-sec__title">{{ item.title }}</span>
                <span v-if="blockedBy.has(item.node_id)" class="time-sec__lock">🔒 等前置</span>
              </button>
              <span v-if="canQuickDelete(item)" class="qa">
                <button v-if="canQuickComplete(item)" type="button" class="qa__done" :disabled="quickBusy === item.node_id" title="完成并归档" @click="quickComplete(item)">✓</button>
                <button type="button" class="qa__del" :disabled="quickBusy === item.node_id" title="彻底删除（不可恢复）" @click="quickDelete(item)">✕</button>
              </span>
            </div>
          </div>
          <button type="button" class="time-panel__more" @click="emit('openCalendar')">{{ timeHiddenCount ? `本周还有 ${timeHiddenCount} 项 · ` : '' }}进入日历页 ›</button>
        </aside>
        <div class="lanes-wrap" aria-label="按事务看">
          <p class="lanes-wrap__kicker">按事务看 · 大任务与依赖关系<span v-if="lanesOverflow" class="lanes-nav"><button type="button" aria-label="向左翻页" @click="scrollLanes(-1)">‹</button><button type="button" aria-label="向右翻页" @click="scrollLanes(1)">›</button></span></p>
          <div class="lanes" ref="lanesEl">
            <section v-for="lane in workLanes" :key="lane.node.node_id" class="lane">
              <header><strong>{{ lane.node.title }}</strong><em>{{ laneUrgency(lane.children) }} · {{ lane.children.length }} 项</em></header>
              <div v-for="(child, index) in lane.children" :key="child.node_id" class="rowline">
                <button type="button" class="lane__step" :class="{ 'lane__step--now': child === firstOpenChild(lane.children), 'is-blocked': blockedBy.has(child.node_id) }" @click="openNearWork(child)">
                  <span class="lane__no">{{ index + 1 }}</span>
                  <span class="lane__body">
                    <span class="lane__title">{{ child.title }}</span>
                    <span class="lane__meta">
                      <span :class="nearDueClass(child)">{{ nearDueText(child) }}</span>
                      <span v-if="blockedBy.has(child.node_id)" class="lane__lock">🔒 {{ blockedLabel(lane.children, child) }}</span>
                      <span v-else-if="child === firstOpenChild(lane.children)" class="lane__now">← 现在做</span>
                    </span>
                  </span>
                </button>
                <span v-if="canQuickDelete(child)" class="qa">
                  <button v-if="canQuickComplete(child)" type="button" class="qa__done" :disabled="quickBusy === child.node_id" title="完成并归档" @click="quickComplete(child)">✓</button>
                  <button type="button" class="qa__del" :disabled="quickBusy === child.node_id" title="彻底删除（不可恢复）" @click="quickDelete(child)">✕</button>
                </span>
              </div>
            </section>
            <section v-if="standaloneLater.length" class="lane lane--plain">
              <header><strong>独立事项</strong><em>{{ laneUrgency(standaloneLater) }} · {{ standaloneLater.length }} 项</em></header>
              <div v-for="item in standaloneLater" :key="item.node_id" class="rowline">
                <button type="button" class="lane__row" @click="openNearWork(item)">
                  <span class="lane__title">{{ item.title }}</span>
                  <span class="lane__rowdue" :class="nearDueClass(item)">{{ nearDueText(item) }}</span>
                </button>
                <span v-if="canQuickDelete(item)" class="qa">
                  <button v-if="canQuickComplete(item)" type="button" class="qa__done" :disabled="quickBusy === item.node_id" title="完成并归档" @click="quickComplete(item)">✓</button>
                  <button type="button" class="qa__del" :disabled="quickBusy === item.node_id" title="彻底删除（不可恢复）" @click="quickDelete(item)">✕</button>
                </span>
              </div>
            </section>
            <section v-if="softItems.length" class="lane lane--soft">
              <header><strong>软性 · 持续推进</strong><em>不紧急 · {{ softItems.length }} 项</em></header>
              <div v-for="item in softItems" :key="item.node_id" class="rowline">
                <button type="button" class="lane__row" @click="openNearWork(item)">
                  <span class="lane__title">{{ item.title }}</span>
                  <span class="lane__rowdue due--soon">{{ nearDay(item) ? `建议 ${shortDay(nearDay(item))} 前` : '持续推进' }}</span>
                </button>
                <span v-if="canQuickDelete(item)" class="qa">
                  <button v-if="canQuickComplete(item)" type="button" class="qa__done" :disabled="quickBusy === item.node_id" title="完成并归档" @click="quickComplete(item)">✓</button>
                  <button type="button" class="qa__del" :disabled="quickBusy === item.node_id" title="彻底删除（不可恢复）" @click="quickDelete(item)">✕</button>
                </span>
              </div>
            </section>
          </div>
        </div>
      </div>
    </section>

      <article class="conversation" aria-label="持续会话">
        <ol v-if="conversation?.turns.length" class="messages" aria-live="polite">
          <li v-for="turn in conversation.turns" :key="turn.turn_id" class="turn">
            <blockquote><span>你</span>{{ turn.teacher_message }}</blockquote>
            <div class="assistant" :data-state="turn.task_state">
              <span>AI 整理</span>
              <TypewriterText v-if="turn.assistant_message" tag="p" :text="turn.assistant_message" />
              <p v-else>{{ taskMessage(turn.task_state) }}</p>
              <ul v-if="turn.clarification_questions.length"><li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li></ul>
              <div v-if="turn === latestTurn && ['invalid_result','truncated_result'].includes(turn.task_state)" class="turn-retry">
                <AppButton variant="secondary" :disabled="busy || taskInFlight || voiceBusy" @click="retryTurn(turn)">重新整理</AppButton>
              </div>
            </div>
          </li>
        </ol>

        <section v-if="visibleHandoffs.length" class="handoffs" aria-label="待核对草稿">
          <div v-for="handoff in visibleHandoffs" :key="handoff.handoff_id" class="handoff-entry">
            <button type="button" :data-mode="handoff.handling_mode" :data-work-item="handoff.work_item_id" @click="openHandoffCard(handoff)">
              <span>{{ domainLabels[handoff.domain] }}</span><strong>{{ handoffTitle(handoff) }}</strong><em class="handoff-state" :data-state="handoff.adoption_state">{{ handoffStateLabel(handoff) }}</em><small>{{ handoffHint(handoff) }}</small>
              <ul v-if="handoff.missing_fields.length && !['adopted','reverted','discarded'].includes(handoff.adoption_state)" class="handoff-warnings"><li v-for="item in handoff.missing_fields" :key="item">{{ item }}</li></ul>
            </button>
            <AppButton v-if="handoff.adoption_state === 'adopted' && isStudentRecord(handoff)" variant="ghost" class="handoff-revert" :disabled="busy" @click="revertAdoption(handoff)">撤回这次更新</AppButton>
          </div>
        </section>

        <div v-if="latestTurn && ['failed_before_dispatch','failed','invalid_result','truncated_result','result_unknown'].includes(latestTurn.task_state)" class="manual-route">
          <p>无需再次调用 AI，也可以直接把原文带到一种处理页：</p>
          <AppButton v-for="mode in (['record','plan_calendar','sop'] as const)" :key="mode" variant="secondary" :disabled="busy" @click="manual(mode)">{{ modeLabels[mode] }}</AppButton>
        </div>

        <form class="composer" @submit.prevent="send">
          <p v-if="awaitingAnswers.length" class="awaiting-answers" role="status">AI 还有 {{ awaitingAnswers.length }} 个追问待你回答，直接在下方回复即可；回答后追问状态会自动结束。</p>
          <textarea id="class-teacher-message" ref="composer" v-model="message" rows="3" maxlength="4000" :disabled="taskInFlight" aria-label="输入班务" placeholder="例如：月底提醒我复查；已确认双方目前都安全" @keydown="onComposerKeydown"></textarea>
          <div class="composer__actions">
            <small>{{ taskInFlight ? '上一轮正在整理；结果返回后可继续补充。' : '回车直接发送，Shift+回车换行。' }}</small>
            <div class="composer__buttons">
              <LocalVoiceInputButton :disabled="busy || taskInFlight" :context-key="conversation?.conversation_id" :submit-cloud-audio="submitCloudAudio" @transcript="applyVoiceTranscript" @info="voiceInfo" @error="voiceError" @busy-changed="voiceBusy = $event" />
              <AppButton variant="primary" type="submit" :disabled="busy || taskInFlight || voiceBusy || !message.trim() || message.length > maxMessageChars">{{ busy || taskInFlight ? '处理中…' : '发送并整理' }}</AppButton>
            </div>
          </div>
        </form>
      </article>
      </div>

      <aside class="side-notes" aria-label="待核对与最近会话">
        <section><header><span>待核对</span><strong>{{ pendingHandoffs.length }}</strong></header><p>{{ pendingHandoffs.length ? '草稿在你确认后才成为正式记录。' : '当前没有等待确认的交接草稿。' }}</p></section>
        <section class="recent">
          <header>
            <span>最近会话</span>
            <AppButton variant="secondary" :disabled="busy || voiceBusy" @click="start">新对话</AppButton>
          </header>
          <div v-for="item in recentConversations" :key="item.conversation_id" class="recent-item">
            <button type="button" class="recent-open" :disabled="voiceBusy" @click="loadConversation(item.conversation_id)">
              <strong>{{ item.first_message }}</strong>
              <small>{{ item.pending_count ? `${item.pending_count} 项待处理` : '暂无待处理草稿' }}</small>
            </button>
            <AppButton variant="ghost" class="recent-delete" :disabled="busy || voiceBusy" @click="removeConversation(item)">删除</AppButton>
          </div>
          <p v-if="!recentConversations.length">发送过内容的对话会保留在这里，最多 5 条。</p>
        </section>
      </aside>
    </div>

    <nav class="domain-band" aria-label="六个事务领域">
      <button v-for="item in domains" :key="item[0]" type="button" :title="item[2]" @click="emit('openDomain', item[0])"><span>{{ item[1] }}</span><small>{{ item[2] }}</small></button>
    </nav>
  </section>
</template>

<style scoped>
.desk{color:var(--foreground)}.desk__masthead{display:flex;align-items:center;justify-content:flex-end;gap:16px;padding:6px 16px;border:1px solid var(--border);border-radius:var(--radius) var(--radius) 0 0;background:var(--card)}.desk__homeroom{display:flex;align-items:baseline;gap:8px;margin:0;font-size:12px;font-weight:600;color:var(--color-text-secondary);white-space:nowrap}.desk__homeroom strong{font-size:16px;font-weight:700;color:var(--foreground)}.desk__body{display:grid;grid-template-columns:minmax(0,1fr) 220px;border-right:1px solid var(--border);border-left:1px solid var(--border);background:var(--card)}.desk__main{min-width:0;display:flex;flex-direction:column}.conversation{min-width:0;padding:14px 16px 16px}.messages{display:grid;gap:10px;margin:0;padding:0;list-style:none}.turn{display:grid;gap:6px}.turn blockquote{width:fit-content;max-width:76%;margin:0 0 0 auto;padding:6px 10px;border-radius:var(--radius) var(--radius) 2px var(--radius);background:var(--accent);font-size:13px;line-height:1.45}.turn blockquote span,.assistant>span{display:inline;margin:0 6px 0 0;color:var(--primary);font-size:11px;font-weight:700;letter-spacing:.04em}.assistant{width:fit-content;max-width:82%;padding:6px 10px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:var(--radius);background:var(--card);font-size:13px;line-height:1.45}.assistant p{display:inline;margin:0}.assistant ul{display:grid;gap:2px;margin:6px 0 0;padding-left:1.15em}.assistant li{line-height:1.4}.assistant[data-state="failed_before_dispatch"]{border-left-color:var(--color-warning)}.handoffs{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0}.handoffs button{display:grid;gap:2px;min-width:min(100%,168px);max-width:280px;min-height:0;padding:8px 10px;border:1px solid var(--border);border-top:3px solid var(--primary);border-radius:var(--radius);background:var(--card);text-align:left;cursor:pointer}.handoffs button:hover{background:var(--accent)}.handoffs button[data-mode="plan_calendar"]{border-top-color:var(--color-info)}.handoffs button[data-mode="sop"]{border-top-color:var(--color-warning)}.handoffs span,.handoffs small{color:var(--color-text-secondary);font-size:12px;line-height:1.35}.manual-route{display:flex;flex-wrap:wrap;gap:8px;padding:14px;border:1px dashed var(--color-warning);border-radius:var(--radius);background:var(--color-warning-subtle)}.manual-route p{flex:1 1 100%;margin:0 0 4px}.composer{display:grid;gap:6px;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)}.composer label{font-weight:600;font-size:13px}.composer textarea{width:100%;resize:vertical;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.5}.composer textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}.composer>div{display:flex;align-items:center;justify-content:space-between;gap:15px}.composer small{color:var(--muted-foreground)}.side-notes{display:grid;align-content:start;gap:8px;padding:10px;border-left:1px solid var(--border);background:var(--background)}.side-notes section{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.side-notes header{display:flex;align-items:center;justify-content:space-between}.side-notes header strong{font-size:18px;color:var(--primary)}.side-notes p{margin:4px 0 0;color:var(--color-text-secondary);font-size:12px}.recent{display:grid;gap:5px}.recent-open{display:grid;gap:2px;padding:6px 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;cursor:pointer}.recent-open:hover strong{color:var(--primary)}.recent-open strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.recent-open small{color:var(--muted-foreground)}.domain-band{display:grid;grid-template-columns:repeat(6,1fr);border:1px solid var(--border);border-radius:0 0 var(--radius) var(--radius);overflow:hidden;background:var(--card)}.domain-band>div{display:grid;gap:3px;min-height:74px;padding:12px;border-right:1px solid var(--color-border-subtle);background:var(--card);text-align:left}.domain-band span{font-weight:600}.domain-band small{color:var(--muted-foreground);font-size:11px}.notice,.error{margin:0;padding:7px 16px;border-inline:1px solid var(--border);font-size:13px}.notice{background:var(--accent)}.error{background:var(--color-danger-subtle);color:var(--destructive)}button:focus-visible,select:focus-visible{outline:2px solid var(--ring);outline-offset:2px}@media(max-width:980px){.desk__body{grid-template-columns:1fr}.side-notes{grid-template-columns:1fr 1fr;border-left:0;border-top:1px solid var(--border)}.domain-band{grid-template-columns:repeat(3,1fr)}}@media(max-width:640px){.desk__masthead,.conversation{padding:16px}.desk__homeroom{width:100%}.turn blockquote,.assistant{max-width:100%}.composer>div{align-items:stretch;flex-direction:column}.side-notes{grid-template-columns:1fr}.domain-band{grid-template-columns:repeat(2,1fr)}}
.composer__buttons{display:flex;align-items:flex-start;gap:9px}.composer__buttons>button[type="submit"]{flex:0 0 auto}.recent-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;border-bottom:1px solid var(--color-border-subtle)}.recent-item .recent-open{border-bottom:0;min-width:0}.recent-item .recent-delete{min-height:26px;padding:0 6px;font-size:12px;color:var(--destructive)}.recent>header :deep(.app-button){min-height:28px;padding:0 10px;font-size:12px}@media(max-width:640px){.composer__buttons{width:100%;align-items:stretch;flex-direction:column}.composer__buttons>button[type="submit"]{width:100%}}
.domain-band > button {
  background: var(--card);
  border: 0;
  border-right: 1px solid var(--color-border-subtle);
  color: inherit;
  cursor: pointer;
  display: grid;
  font: inherit;
  gap: 3px;
  min-height: 74px;
  padding: 12px;
  text-align: left;
}
.domain-band > button:hover { background: var(--accent); }
.handoff-warnings{margin:2px 0 0;padding-left:16px;color:var(--destructive);font-size:12px;line-height:1.35}
.handoff-entry{display:grid;gap:4px;justify-items:start}.handoff-revert{min-height:28px;padding:0 8px;font-size:12px}
.handoff-state{margin:0 6px;padding:0 6px;border:1px solid var(--border);border-radius:999px;font-style:normal;font-size:11px;font-weight:700;color:var(--muted-foreground);vertical-align:1px}.handoff-state[data-state=pending],.handoff-state[data-state=opened],.handoff-state[data-state=adoption_started]{border-color:var(--color-warning);color:var(--color-warning)}.handoff-state[data-state=adopted]{border-color:var(--primary);color:var(--primary)}.handoff-state[data-state=stale]{border-style:dashed;color:var(--muted-foreground)}
.awaiting-answers{margin:0 0 8px;padding:8px 11px;border:1px solid var(--color-warning);border-radius:var(--radius);background:var(--color-warning-subtle);color:var(--foreground);font-size:12px;font-weight:600;line-height:1.5}
.turn-retry{margin-top:6px}
/* 本周事务日程带：左侧深色时间面板 + 右侧浅色事务卡，两种卡片语言强区分 */
/* 整区限高：时间面板与车道区各自内部滚动，下方对话框不被顶掉 */
.week-strip{display:grid;gap:10px;padding:12px 16px;border-bottom:1px solid var(--color-border-subtle);background:var(--card)}
.week-strip>header{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.week-strip__title{font-size:13px;font-weight:700}
.week-strip__title strong{margin:0 2px;color:var(--primary)}
.week-strip__legend{font-size:11px;color:var(--color-text-secondary)}
.week-strip__all{margin-left:auto;padding:0;border:0;background:none;color:var(--primary);font-size:12px;font-weight:600;cursor:pointer}
.week-strip__grid{display:grid;grid-template-columns:250px minmax(0,1fr);gap:12px;align-items:start}
.time-panel{display:grid;gap:6px;align-content:start;max-height:340px;overflow-y:auto;padding:10px 12px;border-radius:var(--radius);background:linear-gradient(165deg,#26374a 0%,#1c2733 100%);color:#e9eef4;box-shadow:0 4px 14px rgba(28,39,51,.18)}
.time-panel__kicker{margin:0;font-size:11px;font-weight:700;letter-spacing:.16em;opacity:.65}
.time-panel__today{margin:0;font-size:15px;font-weight:700}
.time-sec h3{display:flex;justify-content:space-between;align-items:baseline;margin:2px 0 1px;font-size:11px;font-weight:700;letter-spacing:.06em}
.time-sec h3 span{font-weight:500;opacity:.75}
.time-sec--over h3,.time-sec--over .time-sec__date{color:#f2acac}
.time-sec--today h3,.time-sec--today .time-sec__date{color:#9fd4d8}
.time-sec--later h3,.time-sec--later .time-sec__date{color:#93a1b1}
.rowline{display:flex;align-items:center;gap:4px;position:relative}
.time-sec .rowline>button{display:flex;flex:1;gap:8px;align-items:baseline;min-width:0;padding:3px 0;border:0;border-bottom:1px solid rgba(255,255,255,.08);background:none;color:inherit;font:inherit;font-size:12px;text-align:left;cursor:pointer}
.time-sec .rowline:last-of-type>button{border-bottom:0}
.time-sec .rowline>button:hover .time-sec__title{color:#fff;text-decoration:underline}
.time-sec__date{flex:0 0 40px;font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:11px;font-weight:700}
.time-sec__title{flex:1;min-width:0;font-weight:600;line-height:1.45}
.time-sec .is-blocked .time-sec__title,button.is-blocked .time-sec__title{color:#8d99a8;font-weight:500}
.time-sec__lock{font-size:10px;color:#d9b36c;white-space:nowrap}
.time-panel__more{margin-top:2px;padding:6px 0 0;border:0;border-top:1px solid rgba(255,255,255,.14);background:none;color:inherit;font:inherit;font-size:11px;opacity:.8;text-align:left;cursor:pointer}
.time-panel__more:hover{opacity:1;text-decoration:underline}
/* 卡片快捷操作：✓ 完成并归档 / ✕ 彻底删除 */
.qa{flex:0 0 auto;display:flex;gap:4px}
.qa button{width:22px;height:22px;padding:0;border:1px solid;border-radius:6px;background:none;font:inherit;font-size:12px;line-height:1;display:grid;place-items:center;cursor:pointer}
.qa button:disabled{opacity:.4;cursor:default}
.time-panel .qa__done{color:#a3cfae;border-color:rgba(163,207,174,.55)}
.time-panel .qa__done:hover:not(:disabled){background:rgba(163,207,174,.16)}
.time-panel .qa__del{color:#f2acac;border-color:rgba(242,172,172,.5)}
.time-panel .qa__del:hover:not(:disabled){background:rgba(242,172,172,.14)}
.lane .qa{padding-right:4px}
.lane .qa__done{color:var(--color-success);border-color:var(--color-success)}
.lane .qa__done:hover:not(:disabled){background:var(--color-success-subtle)}
.lane .qa__del{color:var(--destructive);border-color:var(--color-border-strong)}
.lane .qa__del:hover:not(:disabled){border-color:var(--destructive);background:var(--color-danger-subtle)}
.lanes-wrap{display:grid;gap:6px;min-width:0;max-height:340px;overflow-y:auto}
.lanes-wrap__kicker{display:flex;justify-content:space-between;align-items:center;margin:0;padding-left:2px;font-size:11px;font-weight:700;letter-spacing:.14em;color:var(--muted-foreground)}.lanes-nav{display:inline-flex;gap:4px;letter-spacing:0}.lanes-nav button{width:22px;height:22px;border:1px solid var(--border-strong);border-radius:6px;background:var(--card);color:var(--color-text-secondary);font-size:13px;line-height:1;display:grid;place-items:center;cursor:pointer}.lanes-nav button:hover{border-color:var(--primary);color:var(--primary)}
.lanes{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(160px,calc((100% - 20px) / 3));gap:10px;overflow-x:auto;padding-bottom:4px}
.lane{display:grid;align-content:start;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);overflow:hidden}
.lane>header{display:flex;align-items:baseline;justify-content:space-between;gap:6px;padding:6px 8px;background:var(--primary);color:#fff}
.lane>header strong{font-size:12px;line-height:1.35}
.lane>header em{flex:0 0 auto;padding:0 6px;border-radius:999px;background:rgba(255,255,255,.22);font-style:normal;font-size:10px;font-weight:700;line-height:1.7;white-space:nowrap}
.lane--plain>header{background:var(--foreground)}
.lane--soft>header{background:var(--color-teacher)}
.lane__step{display:flex;flex:1;gap:6px;align-items:flex-start;min-width:0;padding:5px 8px;border:0;border-bottom:1px dashed var(--color-border-subtle);background:none;font:inherit;text-align:left;cursor:pointer}
.rowline:last-child .lane__step,.rowline:last-child .lane__row{border-bottom:0}
.lane__step:hover,.lane__row:hover{background:var(--accent)}
.lane__no{flex:0 0 auto;width:15px;height:15px;margin-top:1px;border:1px solid var(--color-border-strong);border-radius:50%;background:var(--background);color:var(--color-text-secondary);font-size:9px;font-weight:700;display:grid;place-items:center}
.lane__step--now .lane__no{border-color:var(--color-warning);background:var(--color-warning-subtle);color:var(--color-warning)}
.lane__body{flex:1;min-width:0;display:grid;gap:1px}
.lane__title{font-size:12px;font-weight:600;line-height:1.4}
.is-blocked .lane__title{color:var(--muted-foreground)}
.lane__meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:10px;color:var(--muted-foreground)}
.lane__lock{color:var(--color-warning);font-weight:600}
.lane__now{color:var(--color-warning);font-weight:700}
.lane__row{display:flex;flex:1;gap:6px;align-items:center;min-width:0;padding:5px 8px;border:0;border-bottom:1px dashed var(--color-border-subtle);background:none;font:inherit;font-size:12px;text-align:left;cursor:pointer}
.lane__row .lane__title{flex:1;min-width:0}
.lane__rowdue{flex:0 0 auto;font-size:10px;white-space:nowrap}
.due--over{color:var(--destructive);font-weight:700}
.due--today{color:var(--primary);font-weight:700}
.due--soon{color:var(--color-text-secondary)}
@media(max-width:980px){.week-strip__grid{grid-template-columns:1fr}}
</style>
