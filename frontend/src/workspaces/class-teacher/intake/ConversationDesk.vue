<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getActivePinia } from 'pinia'

import { intakeApi, type HandlingMode, type HomeroomPreference, type IntakeConversation, type IntakeConversationSummary, type IntakeHandoffSummary, type IntakeTurn } from '../api/intake'
import { workApi, type WorkNode } from '../api/work'
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
// 右栏「本周应做的事」只保留最近 5 条：事务一多会把「最近会话」整个顶出屏幕，
// 完整列表始终在日历页，这里只做快速入口。
const nearWorkLimit = 5
const visibleNearWork = computed(() => nearWork.value.slice(0, nearWorkLimit))
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
const modeLabels: Record<HandlingMode, string> = { record: '登记', plan_calendar: '计划／日历', sop: 'SOP' }
const domainLabels: Record<string, string> = Object.fromEntries(domains.map((item) => [item[0], item[1]]))

function errorText(): string {
  return '这次操作没有完成。已输入内容仍保留在会话中，可刷新后继续或手动选择处理方式。'
}

function taskMessage(state: string): string {
  if (state === 'failed_before_dispatch') return '这次任务尚未发出。原文已保留。'
  if (state === 'result_unknown') return '这次请求可能已经发出，但本机没有可靠结果。系统不会自动重发。'
  if (state === 'invalid_result') return '返回内容未通过校验，没有形成正式草稿。原文仍保留。'
  if (state === 'truncated_result') return '模型输出达到长度上限被截断，没有形成正式草稿。原文仍保留，可点击下方「重新整理本轮」重试。'
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
    if (handoff.handling_mode === 'sop' && handoff.affair_id) return '已建立 · 打开 SOP 工作区'
    return isStudentRecord(handoff) ? '已自动并入档案，可一键撤回' : '教师已确认保存'
  }
  if (handoff.adoption_state === 'reverted') return '已撤回，档案回到本轮更新前'
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
    notice.value = '已撤回，档案回到本轮更新前；本轮原始记录仍保留。'
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
  } catch {
    nearWork.value = []
  }
}

function dueLabel(value: string | null): string {
  if (!value) return '时间待定'
  return value.replace('T', ' ').slice(0, 16)
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
onBeforeUnmount(stopPolling)
onMounted(async () => {
  await Promise.all([
    loadRecent(),
    loadNearWork(),
    intakeApi.homeroom()
      .then((value) => { preference.value = value })
      .catch(() => { preference.value = null; error.value = '默认班级暂时无法读取；仍可处理不涉及学生的事务。' }),
  ])
  if (props.conversationId) await loadConversation(props.conversationId)
  else await start()
})
</script>

<template>
  <section class="desk" aria-labelledby="desk-title">
    <header class="desk__masthead">
      <div><p>班主任案头</p><h1 id="desk-title">先把事情说清楚，再决定怎么处理</h1></div>
      <div class="desk__tools">
        <p class="desk__homeroom"><span>我的班主任班级</span><strong>{{ preference?.homeroom_class || '尚未设置班级' }}</strong></p>
      </div>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="desk__body">
      <article class="conversation" aria-label="持续会话">
        <div v-if="!conversation?.turns.length" class="opening">
          <p>直接输入今天需要处理的班务即可，不必先选分类。学生档案更新会自动并入、可一键撤回；其余正式记录与处置仍由你确认。</p>
        </div>
        <ol v-else class="messages" aria-live="polite">
          <li v-for="turn in conversation.turns" :key="turn.turn_id" class="turn">
            <blockquote><span>你</span>{{ turn.teacher_message }}</blockquote>
            <div class="assistant" :data-state="turn.task_state">
              <span>AI 整理</span>
              <TypewriterText v-if="turn.assistant_message" tag="p" :text="turn.assistant_message" />
              <p v-else>{{ taskMessage(turn.task_state) }}</p>
              <ul v-if="turn.clarification_questions.length"><li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li></ul>
              <div v-if="turn === latestTurn && ['invalid_result','truncated_result'].includes(turn.task_state)" class="turn-retry">
                <AppButton variant="secondary" :disabled="busy || taskInFlight || voiceBusy" @click="retryTurn(turn)">重新整理本轮</AppButton>
              </div>
            </div>
          </li>
        </ol>

        <section v-if="visibleHandoffs.length" class="handoffs" aria-label="交接草稿">
          <div v-for="handoff in visibleHandoffs" :key="handoff.handoff_id" class="handoff-entry">
            <button type="button" :data-mode="handoff.handling_mode" :data-work-item="handoff.work_item_id" @click="openHandoffCard(handoff)">
              <span>{{ domainLabels[handoff.domain] }}</span><strong>{{ handoffTitle(handoff) }}</strong><em class="handoff-state" :data-state="handoff.adoption_state">{{ handoffStateLabel(handoff) }}</em><small>{{ handoffHint(handoff) }}</small>
              <ul v-if="handoff.missing_fields.length && !['adopted','reverted','discarded'].includes(handoff.adoption_state)" class="handoff-warnings"><li v-for="item in handoff.missing_fields" :key="item">{{ item }}</li></ul>
            </button>
            <AppButton v-if="handoff.adoption_state === 'adopted' && isStudentRecord(handoff)" variant="ghost" class="handoff-revert" :disabled="busy" @click="revertAdoption(handoff)">撤回本轮更新</AppButton>
          </div>
        </section>

        <div v-if="latestTurn && ['failed_before_dispatch','failed','invalid_result','truncated_result','result_unknown'].includes(latestTurn.task_state)" class="manual-route">
          <p>无需再次调用 AI，也可以直接把原文带到一种处理页：</p>
          <AppButton v-for="mode in (['record','plan_calendar','sop'] as const)" :key="mode" variant="secondary" :disabled="busy" @click="manual(mode)">{{ modeLabels[mode] }}</AppButton>
        </div>

        <form class="composer" @submit.prevent="send">
          <p v-if="awaitingAnswers.length" class="awaiting-answers" role="status">AI 还有 {{ awaitingAnswers.length }} 个追问待你回答，直接在下方回复即可；回答后追问状态会自动结束。</p>
          <label for="class-teacher-message">继续说明或补充</label>
          <textarea id="class-teacher-message" ref="composer" v-model="message" rows="3" maxlength="4000" :disabled="taskInFlight" placeholder="例如：月底提醒我复查；已确认双方目前都安全" @keydown="onComposerKeydown"></textarea>
          <div class="composer__actions">
            <small>{{ taskInFlight ? '上一轮正在整理；结果返回后可继续补充。' : '回车直接发送，Shift+回车换行；发送后进入已配置模型任务，无需额外预览确认。' }}</small>
            <div class="composer__buttons">
              <LocalVoiceInputButton :disabled="busy || taskInFlight" :context-key="conversation?.conversation_id" :submit-cloud-audio="submitCloudAudio" @transcript="applyVoiceTranscript" @info="voiceInfo" @error="voiceError" @busy-changed="voiceBusy = $event" />
              <AppButton variant="primary" type="submit" :disabled="busy || taskInFlight || voiceBusy || !message.trim() || message.length > maxMessageChars">{{ busy || taskInFlight ? '处理中…' : '发送并整理' }}</AppButton>
            </div>
          </div>
        </form>
      </article>

      <aside class="side-notes" aria-label="本周事项与最近会话">
        <section class="near-work"><header><span>本周应做的事</span><strong v-if="nearWork.length">{{ nearWork.length }}</strong></header><button v-for="item in visibleNearWork" :key="item.node_id" type="button" @click="openNearWork(item)"><strong>{{ item.title }}</strong><small>{{ dueLabel(item.due_date) }}</small></button><button v-if="nearWork.length > visibleNearWork.length" type="button" class="near-work__more" @click="emit('openCalendar')">查看全部 {{ nearWork.length }} 项</button><p v-if="!nearWork.length">本周还没有应做的事。</p></section>
        <section><header><span>待核对</span><strong>{{ pendingHandoffs.length }}</strong></header><p>{{ pendingHandoffs.length ? '学生档案草稿会自动并入档案、可一键撤回；其余草稿在你确认后才会成为正式记录。' : '当前没有等待确认的交接草稿。' }}</p></section>
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
.desk{color:var(--foreground)}.desk__masthead{display:flex;align-items:end;justify-content:space-between;gap:16px;padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius) var(--radius) 0 0;background:var(--card)}.desk__masthead p{margin:0 0 2px;color:var(--primary);font-size:11px;font-weight:700;letter-spacing:.12em}.desk__masthead h1{max-width:720px;margin:0;font-size:clamp(18px,2.1vw,24px);font-weight:700;letter-spacing:-.02em}.desk__tools{display:flex;align-items:end;gap:10px}.desk__homeroom{display:flex;align-items:baseline;gap:8px;margin:0;font-size:12px;font-weight:600;color:var(--color-text-secondary);white-space:nowrap}.desk__homeroom strong{font-size:16px;font-weight:700;color:var(--foreground)}.desk__body{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(240px,.64fr);border-right:1px solid var(--border);border-left:1px solid var(--border);background:var(--card)}.conversation{min-width:0;padding:14px 16px 16px;border-right:1px solid var(--border)}.opening{min-height:120px;display:grid;align-content:center;justify-items:center;text-align:center}.opening p{max-width:36em;margin:0;font-size:14px;font-weight:600;line-height:1.5}.opening ul{display:grid;gap:8px;padding:0;list-style:none;color:var(--color-text-secondary)}.messages{display:grid;gap:10px;margin:0;padding:0;list-style:none}.turn{display:grid;gap:6px}.turn blockquote{width:fit-content;max-width:76%;margin:0 0 0 auto;padding:6px 10px;border-radius:var(--radius) var(--radius) 2px var(--radius);background:var(--accent);font-size:13px;line-height:1.45}.turn blockquote span,.assistant>span{display:inline;margin:0 6px 0 0;color:var(--primary);font-size:11px;font-weight:700;letter-spacing:.04em}.assistant{width:fit-content;max-width:82%;padding:6px 10px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:var(--radius);background:var(--card);font-size:13px;line-height:1.45}.assistant p{display:inline;margin:0}.assistant ul{display:grid;gap:2px;margin:6px 0 0;padding-left:1.15em}.assistant li{line-height:1.4}.assistant[data-state="failed_before_dispatch"]{border-left-color:var(--color-warning)}.handoffs{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0}.handoffs button{display:grid;gap:2px;min-width:min(100%,168px);max-width:280px;min-height:0;padding:8px 10px;border:1px solid var(--border);border-top:3px solid var(--primary);border-radius:var(--radius);background:var(--card);text-align:left;cursor:pointer}.handoffs button:hover{background:var(--accent)}.handoffs button[data-mode="plan_calendar"]{border-top-color:var(--color-info)}.handoffs button[data-mode="sop"]{border-top-color:var(--color-warning)}.handoffs span,.handoffs small{color:var(--color-text-secondary);font-size:12px;line-height:1.35}.manual-route{display:flex;flex-wrap:wrap;gap:8px;padding:14px;border:1px dashed var(--color-warning);border-radius:var(--radius);background:var(--color-warning-subtle)}.manual-route p{flex:1 1 100%;margin:0 0 4px}.composer{display:grid;gap:6px;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)}.composer label{font-weight:600;font-size:13px}.composer textarea{width:100%;resize:vertical;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.5}.composer textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}.composer>div{display:flex;align-items:center;justify-content:space-between;gap:15px}.composer small{color:var(--muted-foreground)}.side-notes{display:grid;align-content:start;gap:12px;padding:16px;background:var(--background)}.side-notes section{padding:14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.side-notes header{display:flex;align-items:center;justify-content:space-between}.side-notes header strong{font-size:24px;color:var(--primary)}.side-notes p{color:var(--color-text-secondary);font-size:13px}.recent,.near-work{display:grid;gap:7px}.near-work button,.recent-open{display:grid;gap:3px;padding:10px 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;cursor:pointer}.near-work button:hover strong,.recent-open:hover strong{color:var(--primary)}.recent-open strong,.near-work strong{display:-webkit-box;overflow:hidden;-webkit-line-clamp:2;-webkit-box-orient:vertical}.recent-open small,.near-work small{color:var(--muted-foreground)}.domain-band{display:grid;grid-template-columns:repeat(6,1fr);border:1px solid var(--border);border-radius:0 0 var(--radius) var(--radius);overflow:hidden;background:var(--card)}.domain-band>div{display:grid;gap:3px;min-height:74px;padding:12px;border-right:1px solid var(--color-border-subtle);background:var(--card);text-align:left}.domain-band span{font-weight:600}.domain-band small{color:var(--muted-foreground);font-size:11px}.notice,.error{margin:0;padding:7px 16px;border-inline:1px solid var(--border);font-size:13px}.notice{background:var(--accent)}.error{background:var(--color-danger-subtle);color:var(--destructive)}button:focus-visible,select:focus-visible{outline:2px solid var(--ring);outline-offset:2px}@media(max-width:980px){.desk__masthead{align-items:flex-start;flex-direction:column}.desk__body{grid-template-columns:1fr}.conversation{border-right:0}.side-notes{grid-template-columns:1fr 1fr}.domain-band{grid-template-columns:repeat(3,1fr)}}@media(max-width:640px){.desk__masthead,.conversation{padding:16px}.desk__tools{width:100%;align-items:stretch;flex-direction:column}.desk__homeroom{width:100%}.turn blockquote,.assistant{max-width:100%}.composer>div{align-items:stretch;flex-direction:column}.side-notes{grid-template-columns:1fr}.domain-band{grid-template-columns:repeat(2,1fr)}}
.composer__buttons{display:flex;align-items:flex-start;gap:9px}.composer__buttons>button[type="submit"]{flex:0 0 auto}.recent-item{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;border-bottom:1px solid var(--color-border-subtle)}.recent-item .recent-open{border-bottom:0;min-width:0}.recent-item .recent-delete{min-height:32px;padding:0 6px;font-size:12px;color:var(--destructive)}.recent>header :deep(.app-button){min-height:32px;padding:0 10px;font-size:12px}.near-work__more{text-align:center;color:var(--primary);font-weight:600;border-bottom:0}@media(max-width:640px){.composer__buttons{width:100%;align-items:stretch;flex-direction:column}.composer__buttons>button[type="submit"]{width:100%}}
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
</style>
