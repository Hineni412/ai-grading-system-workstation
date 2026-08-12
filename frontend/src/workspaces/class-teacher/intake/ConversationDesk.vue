<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getActivePinia } from 'pinia'

import { intakeApi, type HandlingMode, type HomeroomPreference, type IntakeConversation, type IntakeConversationSummary, type IntakeHandoffSummary } from '../api/intake'
import { workApi, type WorkNode } from '../api/work'
import { workspaceAITaskApi } from '../../shared/ai-tasks/api'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import AppButton from '@/components/design-system/AppButton.vue'
import LocalVoiceInputButton from './LocalVoiceInputButton.vue'

type ClassTeacherDomain =
  | 'student_growth'
  | 'student_support'
  | 'conflict_safety'
  | 'class_operations'
  | 'activities_culture'
  | 'school_coordination'

const props = defineProps<{ conversationId?: string | null; focusWorkItemId?: string | null }>()
const emit = defineEmits<{
  conversationChanged: [conversationId: string]
  openHandoff: [handoff: IntakeHandoffSummary]
  openCalendar: []
  openDomain: [domain: ClassTeacherDomain]
}>()

const conversation = ref<IntakeConversation | null>(null)
const recent = ref<IntakeConversationSummary[]>([])
const nearWork = ref<WorkNode[]>([])
const preference = ref<HomeroomPreference | null>(null)
const selectedClass = ref('')
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
const visibleHandoffs = computed(() => conversation.value?.handoffs.filter((item) => item.adoption_state !== 'stale') ?? [])
const taskInFlight = computed(() => conversation.value?.state === 'ai_running')
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

function maybeOpenSingleHandoff(next: IntakeConversation): void {
  const available = next.handoffs.filter((item) => ['pending', 'opened'].includes(item.adoption_state))
  if (available.length === 1 && available[0]!.auto_open_allowed) emit('openHandoff', available[0]!)
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
          maybeOpenSingleHandoff(next)
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
  stopPolling()
  busy.value = true; error.value = ''; notice.value = ''
  try {
    conversation.value = await intakeApi.startConversation()
    emit('conversationChanged', conversation.value.conversation_id)
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
    else maybeOpenSingleHandoff(conversation.value)
    await loadRecent()
  } catch { error.value = errorText() }
  finally { busy.value = false }
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
    maybeOpenSingleHandoff(next)
    await loadRecent()
    return true
  } catch (value) {
    notice.value = ''
    error.value = cloudVoiceError(value)
    return false
  }
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
    const candidates = [...snapshot.overdue, ...snapshot.today, ...snapshot.nodes]
      .filter((item) => item.due_date && !['completed', 'cancelled'].includes(item.status))
    nearWork.value = [...new Map(candidates.map((item) => [item.node_id, item])).values()]
      .sort((left, right) => String(left.due_date).localeCompare(String(right.due_date)))
      .slice(0, 5)
  } catch {
    nearWork.value = []
  }
}

function dueLabel(value: string | null): string {
  if (!value) return '时间待定'
  return value.replace('T', ' ').slice(0, 16)
}

async function changeHomeroom(): Promise<void> {
  if (!preference.value || busy.value || voiceBusy.value) return
  busy.value = true; error.value = ''
  try {
    preference.value = await intakeApi.setHomeroom(preference.value, selectedClass.value || null)
    const currentHasContent = Boolean(conversation.value?.turns.length)
    const successNotice = selectedClass.value
      ? currentHasContent
        ? `默认班级已改为 ${selectedClass.value}。当前对话仍保留原班级，点击“新对话”即可使用新设置。`
        : `默认班级已改为 ${selectedClass.value}，正在为这个班建立新对话。`
      : currentHasContent
        ? '已取消默认班级筛选。当前对话保持不变，新对话将不限定班级。'
        : '已取消默认班级筛选，正在建立不限定班级的新对话。学生和历史关系没有删除。'
    if (!currentHasContent) await start()
    notice.value = successNotice
  } catch { error.value = '默认班级没有更新。学生库可能已经变化，请刷新后再选。' }
  finally { busy.value = false }
}

watch(() => props.conversationId, (id) => { if (id && id !== conversation.value?.conversation_id) void loadConversation(id) })
onBeforeUnmount(stopPolling)
onMounted(async () => {
  await Promise.all([
    loadRecent(),
    loadNearWork(),
    intakeApi.homeroom()
      .then((value) => { preference.value = value; selectedClass.value = value.homeroom_class ?? '' })
      .catch(() => { preference.value = null; selectedClass.value = ''; error.value = '默认班级暂时无法读取；仍可处理不涉及学生的事务。' }),
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
        <label><span>我的班主任班级</span><select v-model="selectedClass" :disabled="busy || voiceBusy" @change="changeHomeroom"><option value="">尚未选择</option><option v-for="item in preference?.classes ?? []" :key="item" :value="item">{{ item }}</option></select></label>
        <AppButton variant="secondary" :disabled="busy || voiceBusy" @click="start">新对话</AppButton>
      </div>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="desk__body">
      <article class="conversation" aria-label="持续会话">
        <div v-if="!conversation?.turns.length" class="opening">
          <p>直接输入今天需要处理的班务即可，不必先选分类。系统只负责整理和起草，正式记录与处置仍由你确认。</p>
        </div>
        <ol v-else class="messages" aria-live="polite">
          <li v-for="turn in conversation.turns" :key="turn.turn_id" class="turn">
            <blockquote><span>你</span>{{ turn.teacher_message }}</blockquote>
            <div class="assistant" :data-state="turn.task_state">
              <span>AI 整理</span>
              <p v-if="turn.assistant_message">{{ turn.assistant_message }}</p>
              <p v-else>{{ taskMessage(turn.task_state) }}</p>
              <ul v-if="turn.clarification_questions.length"><li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li></ul>
            </div>
          </li>
        </ol>

        <section v-if="visibleHandoffs.length" class="handoffs" aria-label="交接草稿">
          <button v-for="handoff in visibleHandoffs" :key="handoff.handoff_id" type="button" :data-mode="handoff.handling_mode" :data-work-item="handoff.work_item_id" @click="emit('openHandoff', handoff)">
            <span>{{ domainLabels[handoff.domain] }}</span><strong>{{ modeLabels[handoff.handling_mode] }}草稿</strong><small>{{ handoff.adoption_state === 'adopted' ? '教师已确认保存' : handoff.adoption_state === 'discarded' ? '已丢弃' : '打开核对，不会自动保存' }}</small>
            <ul v-if="handoff.missing_fields.length" class="handoff-warnings"><li v-for="item in handoff.missing_fields" :key="item">{{ item }}</li></ul>
          </button>
        </section>

        <div v-if="latestTurn && ['failed_before_dispatch','failed','invalid_result','result_unknown'].includes(latestTurn.task_state)" class="manual-route">
          <p>无需再次调用 AI，也可以直接把原文带到一种处理页：</p>
          <AppButton v-for="mode in (['record','plan_calendar','sop'] as const)" :key="mode" variant="secondary" :disabled="busy" @click="manual(mode)">{{ modeLabels[mode] }}</AppButton>
        </div>

        <form class="composer" @submit.prevent="send">
          <label for="class-teacher-message">继续说明或补充</label>
          <textarea id="class-teacher-message" ref="composer" v-model="message" rows="3" maxlength="4000" :disabled="taskInFlight" placeholder="例如：月底提醒我复查；已确认双方目前都安全"></textarea>
          <div class="composer__actions">
            <small>{{ taskInFlight ? '上一轮正在整理；结果返回后可继续补充。' : '发送后直接进入已配置模型任务，无需额外预览确认。' }}</small>
            <div class="composer__buttons">
              <LocalVoiceInputButton :disabled="busy || taskInFlight" :context-key="conversation?.conversation_id" :submit-cloud-audio="submitCloudAudio" @transcript="applyVoiceTranscript" @info="voiceInfo" @error="voiceError" @busy-changed="voiceBusy = $event" />
              <AppButton variant="primary" type="submit" :disabled="busy || taskInFlight || voiceBusy || !message.trim() || message.length > maxMessageChars">{{ busy || taskInFlight ? '处理中…' : '发送并整理' }}</AppButton>
            </div>
          </div>
        </form>
      </article>

      <aside class="side-notes" aria-label="今日与近期事项">
        <section class="near-work"><header><span>今日与接下来</span></header><button v-for="item in nearWork" :key="item.node_id" type="button" @click="emit('openCalendar')"><strong>{{ item.title }}</strong><small>{{ dueLabel(item.due_date) }}</small></button><p v-if="!nearWork.length">当前没有需要提醒的日历节点。</p></section>
        <section><header><span>待核对</span><strong>{{ pendingHandoffs.length }}</strong></header><p>{{ pendingHandoffs.length ? '草稿只有在你确认后才会成为正式记录。' : '当前没有等待确认的交接草稿。' }}</p></section>
        <section class="recent"><header><span>最近会话</span></header><button v-for="item in recent.slice(0,6)" :key="item.conversation_id" type="button" :disabled="voiceBusy" @click="loadConversation(item.conversation_id)"><strong>{{ item.first_message || '尚未发送内容' }}</strong><small>{{ item.pending_count ? `${item.pending_count} 项待处理` : '暂无待处理草稿' }}</small></button><p v-if="!recent.length">开始第一段对话后，会在这里保留入口。</p></section>
      </aside>
    </div>

    <nav class="domain-band" aria-label="六个事务领域">
      <button v-for="item in domains" :key="item[0]" type="button" :title="item[2]" @click="emit('openDomain', item[0])"><span>{{ item[1] }}</span><small>{{ item[2] }}</small></button>
    </nav>
  </section>
</template>

<style scoped>
.desk{color:var(--foreground)}.desk__masthead{display:flex;align-items:end;justify-content:space-between;gap:24px;padding:20px 24px;border:1px solid var(--border);border-radius:var(--radius) var(--radius) 0 0;background:var(--card)}.desk__masthead p{margin:0 0 4px;color:var(--primary);font-size:12px;font-weight:700;letter-spacing:.12em}.desk__masthead h1{max-width:720px;margin:0;font-size:clamp(22px,2.6vw,30px);font-weight:700;letter-spacing:-.02em}.desk__tools{display:flex;align-items:end;gap:10px}.desk__tools label{display:grid;gap:5px;font-size:12px;font-weight:600;color:var(--color-text-secondary)}.desk__tools select{min-height:36px;padding:0 8px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}.desk__body{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(260px,.72fr);border-right:1px solid var(--border);border-left:1px solid var(--border);background:var(--card)}.conversation{min-width:0;padding:24px;border-right:1px solid var(--border)}.opening{min-height:230px;display:grid;align-content:center;justify-items:center;text-align:center}.opening p{font-size:16px;font-weight:600}.opening ul{display:grid;gap:8px;padding:0;list-style:none;color:var(--color-text-secondary)}.messages{display:grid;gap:20px;min-height:220px;margin:0;padding:0;list-style:none}.turn{display:grid;gap:10px}.turn blockquote{max-width:78%;margin:0 0 0 auto;padding:10px 14px;border-radius:var(--radius) var(--radius) 2px var(--radius);background:var(--accent)}.turn blockquote span,.assistant>span{display:block;margin-bottom:4px;color:var(--primary);font-size:11px;font-weight:700;letter-spacing:.08em}.assistant{max-width:86%;padding:12px 14px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:var(--radius);background:var(--card)}.assistant p{margin:0}.assistant[data-state="failed_before_dispatch"]{border-left-color:var(--color-warning)}.handoffs{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:20px 0}.handoffs button{display:grid;gap:5px;min-height:112px;padding:14px;border:1px solid var(--border);border-top:3px solid var(--primary);border-radius:var(--radius);background:var(--card);text-align:left;cursor:pointer}.handoffs button:hover{background:var(--accent)}.handoffs button[data-mode="plan_calendar"]{border-top-color:var(--color-info)}.handoffs button[data-mode="sop"]{border-top-color:var(--color-warning)}.handoffs span,.handoffs small{color:var(--color-text-secondary);font-size:12px}.manual-route{display:flex;flex-wrap:wrap;gap:8px;padding:14px;border:1px dashed var(--color-warning);border-radius:var(--radius);background:var(--color-warning-subtle)}.manual-route p{flex:1 1 100%;margin:0 0 4px}.composer{display:grid;gap:8px;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)}.composer label{font-weight:600}.composer textarea{width:100%;resize:vertical;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;line-height:1.6}.composer textarea:focus-visible{border-color:var(--ring);box-shadow:var(--focus-ring);outline:0}.composer>div{display:flex;align-items:center;justify-content:space-between;gap:15px}.composer small{color:var(--muted-foreground)}.side-notes{display:grid;align-content:start;gap:12px;padding:16px;background:var(--background)}.side-notes section{padding:14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.side-notes header{display:flex;align-items:center;justify-content:space-between}.side-notes header strong{font-size:24px;color:var(--primary)}.side-notes p{color:var(--color-text-secondary);font-size:13px}.recent,.near-work{display:grid;gap:7px}.recent button,.near-work button{display:grid;gap:3px;padding:10px 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;cursor:pointer}.recent button:hover strong,.near-work button:hover strong{color:var(--primary)}.recent strong,.near-work strong{display:-webkit-box;overflow:hidden;-webkit-line-clamp:2;-webkit-box-orient:vertical}.recent small,.near-work small{color:var(--muted-foreground)}.domain-band{display:grid;grid-template-columns:repeat(6,1fr);border:1px solid var(--border);border-radius:0 0 var(--radius) var(--radius);overflow:hidden;background:var(--card)}.domain-band>div{display:grid;gap:3px;min-height:74px;padding:12px;border-right:1px solid var(--color-border-subtle);background:var(--card);text-align:left}.domain-band span{font-weight:600}.domain-band small{color:var(--muted-foreground);font-size:11px}.notice,.error{margin:0;padding:10px 16px;border-inline:1px solid var(--border)}.notice{background:var(--accent)}.error{background:var(--color-danger-subtle);color:var(--destructive)}button:focus-visible,select:focus-visible{outline:2px solid var(--ring);outline-offset:2px}@media(max-width:980px){.desk__masthead{align-items:flex-start;flex-direction:column}.desk__body{grid-template-columns:1fr}.conversation{border-right:0}.side-notes{grid-template-columns:1fr 1fr}.domain-band{grid-template-columns:repeat(3,1fr)}}@media(max-width:640px){.desk__masthead,.conversation{padding:16px}.desk__tools{width:100%;align-items:stretch;flex-direction:column}.desk__tools label{width:100%}.turn blockquote,.assistant{max-width:100%}.composer>div{align-items:stretch;flex-direction:column}.side-notes{grid-template-columns:1fr}.domain-band{grid-template-columns:repeat(2,1fr)}}
.composer__buttons{display:flex;align-items:flex-start;gap:9px}.composer__buttons>button[type="submit"]{flex:0 0 auto}@media(max-width:640px){.composer__buttons{width:100%;align-items:stretch;flex-direction:column}.composer__buttons>button[type="submit"]{width:100%}}
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
.handoff-warnings{margin:3px 0 0;padding-left:18px;color:var(--destructive);font-size:12px;line-height:1.45}
</style>
