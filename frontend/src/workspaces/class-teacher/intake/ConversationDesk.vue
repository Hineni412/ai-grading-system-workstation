<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { intakeApi, type HandlingMode, type HomeroomPreference, type IntakeConversation, type IntakeConversationSummary, type IntakeHandoffSummary } from '../api/intake'

const props = defineProps<{ conversationId?: string | null; focusWorkItemId?: string | null }>()
const emit = defineEmits<{
  conversationChanged: [conversationId: string]
  openHandoff: [handoff: IntakeHandoffSummary]
}>()

const conversation = ref<IntakeConversation | null>(null)
const recent = ref<IntakeConversationSummary[]>([])
const preference = ref<HomeroomPreference | null>(null)
const selectedClass = ref('')
const message = ref('')
const busy = ref(false)
const notice = ref('')
const error = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
let pollTimer: number | null = null
let pollGeneration = 0

const latestTurn = computed(() => {
  const turns = conversation.value?.turns ?? []
  return turns.length ? turns[turns.length - 1]! : null
})
const pendingHandoffs = computed(() => conversation.value?.handoffs.filter((item) => ['pending', 'opened', 'adoption_started'].includes(item.adoption_state)) ?? [])
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
    emit('conversationChanged', id)
    await nextTick()
    document.querySelector<HTMLElement>(`[data-work-item="${props.focusWorkItemId ?? ''}"]`)?.focus()
    if (conversation.value.state === 'ai_running') pollUntilSettled(id)
  } catch { error.value = '这次会话暂时无法读取，请从最近会话重新打开。' }
  finally { busy.value = false }
}

async function start(): Promise<void> {
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
  if (!conversation.value || !message.value.trim() || busy.value) return
  const outgoing = message.value.trim()
  busy.value = true; error.value = ''; notice.value = '正在整理；离开页面后仍可从最近会话返回。'
  try {
    conversation.value = await intakeApi.appendTurn(conversation.value, outgoing)
    message.value = ''
    if (conversation.value.state === 'failed') notice.value = 'AI 任务没有发出。原文已保留，可直接选择一种处理方式继续。'
    else if (conversation.value.state === 'ai_running') pollUntilSettled(conversation.value.conversation_id)
    else maybeOpenSingleHandoff(conversation.value)
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

async function changeHomeroom(): Promise<void> {
  if (!preference.value || busy.value) return
  busy.value = true; error.value = ''
  try {
    preference.value = await intakeApi.setHomeroom(preference.value, selectedClass.value || null)
    notice.value = selectedClass.value
      ? `默认班级已改为 ${selectedClass.value}。学生和历史关系没有删除。`
      : '已取消默认班级筛选。学生和历史关系没有删除。'
  } catch { error.value = '默认班级没有更新。学生库可能已经变化，请刷新后再选。' }
  finally { busy.value = false }
}

watch(() => props.conversationId, (id) => { if (id && id !== conversation.value?.conversation_id) void loadConversation(id) })
onBeforeUnmount(stopPolling)
onMounted(async () => {
  await Promise.all([
    loadRecent(),
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
        <label><span>我的班主任班级</span><select v-model="selectedClass" :disabled="busy" @change="changeHomeroom"><option value="">尚未选择</option><option v-for="item in preference?.classes ?? []" :key="item" :value="item">{{ item }}</option></select></label>
        <button type="button" :disabled="busy" @click="start">新对话</button>
      </div>
    </header>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="desk__body">
      <article class="conversation" aria-label="持续会话">
        <div v-if="!conversation?.turns.length" class="opening">
          <p>学生、班级、活动、学校任务都可以直接说，不必先选分类。</p>
          <ul><li>“补录某同学最近的课堂状态”</li><li>“黑板报两周后检查，帮我拆一下”</li><li>“两个学生课间发生冲突，目前没人受伤”</li></ul>
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

        <section v-if="conversation?.handoffs.length" class="handoffs" aria-label="交接草稿">
          <button v-for="handoff in conversation.handoffs" :key="handoff.handoff_id" type="button" :data-mode="handoff.handling_mode" :data-work-item="handoff.work_item_id" @click="emit('openHandoff', handoff)">
            <span>{{ domainLabels[handoff.domain] }}</span><strong>{{ modeLabels[handoff.handling_mode] }}草稿</strong><small>{{ handoff.adoption_state === 'adopted' ? '教师已确认保存' : handoff.adoption_state === 'discarded' ? '已丢弃' : '打开核对，不会自动保存' }}</small>
          </button>
        </section>

        <div v-if="latestTurn && ['failed_before_dispatch','failed','invalid_result','result_unknown'].includes(latestTurn.task_state)" class="manual-route">
          <p>无需再次调用 AI，也可以直接把原文带到一种处理页：</p>
          <button v-for="mode in (['record','plan_calendar','sop'] as const)" :key="mode" type="button" :disabled="busy" @click="manual(mode)">{{ modeLabels[mode] }}</button>
        </div>

        <form class="composer" @submit.prevent="send">
          <label for="class-teacher-message">继续说明或补充</label>
          <textarea id="class-teacher-message" ref="composer" v-model="message" rows="3" maxlength="4000" placeholder="例如：月底提醒我复查；已确认双方目前都安全"></textarea>
          <div><small>发送后直接进入已配置模型任务，无需额外预览确认。</small><button type="submit" :disabled="busy || !message.trim()">{{ busy ? '处理中…' : '发送并整理' }}</button></div>
        </form>
      </article>

      <aside class="side-notes" aria-label="今日与近期事项">
        <section><header><span>待核对</span><strong>{{ pendingHandoffs.length }}</strong></header><p>{{ pendingHandoffs.length ? '草稿只有在你确认后才会成为正式记录。' : '当前没有等待确认的交接草稿。' }}</p></section>
        <section class="recent"><header><span>最近会话</span></header><button v-for="item in recent.slice(0,6)" :key="item.conversation_id" type="button" @click="loadConversation(item.conversation_id)"><strong>{{ item.first_message || '尚未发送内容' }}</strong><small>{{ item.pending_count ? `${item.pending_count} 项待处理` : '暂无待处理草稿' }}</small></button><p v-if="!recent.length">开始第一段对话后，会在这里保留入口。</p></section>
      </aside>
    </div>

    <nav class="domain-band" aria-label="六个事务领域">
      <div v-for="item in domains" :key="item[0]" :title="item[2]"><span>{{ item[1] }}</span><small>{{ item[2] }}</small></div>
    </nav>
  </section>
</template>

<style scoped>
.desk{--ink:#233239;--paper:#fbfcfa;--teal:#176b6a;--teal-soft:#e8f2ef;--amber:#a45b16;--red:#9a3e35;color:var(--ink)}.desk__masthead{display:flex;align-items:end;justify-content:space-between;gap:24px;padding:22px 26px;border:1px solid var(--color-border-default);border-bottom:3px solid var(--teal);border-radius:18px 18px 0 0;background:linear-gradient(105deg,#f4f8f6,var(--paper) 60%)}.desk__masthead p{margin:0 0 4px;color:var(--teal);font-size:12px;font-weight:800;letter-spacing:.12em}.desk__masthead h1{max-width:720px;margin:0;font-family:"Microsoft YaHei UI","Noto Sans CJK SC",sans-serif;font-size:clamp(24px,3vw,38px);font-weight:760;letter-spacing:-.03em}.desk__tools{display:flex;align-items:end;gap:10px}.desk__tools label{display:grid;gap:5px;font-size:12px;font-weight:700}.desk__tools select,.desk__tools button{min-height:40px;padding:0 12px;border:1px solid var(--color-border-default);border-radius:10px;background:white;font:inherit}.desk__body{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(260px,.72fr);border-right:1px solid var(--color-border-default);border-left:1px solid var(--color-border-default);background:var(--paper)}.conversation{min-width:0;padding:24px 26px;border-right:1px solid var(--color-border-default);background:repeating-linear-gradient(to bottom,transparent 0,transparent 37px,rgba(54,87,91,.055) 38px)}.opening{min-height:230px;display:grid;align-content:center;justify-items:center;text-align:center}.opening p{font-size:18px;font-weight:700}.opening ul{display:grid;gap:8px;padding:0;list-style:none;color:var(--color-text-secondary)}.messages{display:grid;gap:22px;min-height:220px;margin:0;padding:0;list-style:none}.turn{display:grid;gap:10px}.turn blockquote{max-width:78%;margin:0 0 0 auto;padding:12px 15px;border-radius:14px 14px 3px 14px;background:#dfeceb}.turn blockquote span,.assistant>span{display:block;margin-bottom:4px;color:var(--teal);font-size:11px;font-weight:800;letter-spacing:.08em}.assistant{max-width:86%;padding:14px 16px;border-left:4px solid var(--teal);background:rgba(255,255,255,.9);box-shadow:0 6px 18px rgba(29,55,58,.06)}.assistant p{margin:0}.assistant[data-state="failed_before_dispatch"]{border-left-color:var(--amber)}.handoffs{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:22px 0}.handoffs button{display:grid;gap:5px;min-height:112px;padding:14px;border:1px solid var(--color-border-default);border-top:4px solid var(--teal);border-radius:11px;background:white;text-align:left}.handoffs button[data-mode="plan_calendar"]{border-top-color:#3d70a6}.handoffs button[data-mode="sop"]{border-top-color:var(--amber)}.handoffs span,.handoffs small{color:var(--color-text-secondary);font-size:12px}.manual-route{padding:14px;border:1px dashed var(--amber);background:#fff8ef}.manual-route p{margin-top:0}.manual-route button{margin-right:8px;min-height:36px}.composer{display:grid;gap:8px;margin-top:22px;padding-top:16px;border-top:1px solid var(--color-border-default);background:var(--paper)}.composer label{font-weight:750}.composer textarea{width:100%;resize:vertical;padding:12px;border:1px solid #9cb1af;border-radius:12px;background:white;font:inherit;line-height:1.6}.composer>div{display:flex;align-items:center;justify-content:space-between;gap:15px}.composer small{color:var(--color-text-secondary)}.composer button{min-height:42px;padding:0 18px;border:0;border-radius:10px;background:var(--teal);color:white;font:inherit;font-weight:750}.side-notes{display:grid;align-content:start;gap:14px;padding:18px;background:#f2f5f1}.side-notes section{padding:15px;border:1px solid var(--color-border-default);border-radius:12px;background:white}.side-notes header{display:flex;align-items:center;justify-content:space-between}.side-notes header strong{font-size:28px;color:var(--teal)}.side-notes p{color:var(--color-text-secondary);font-size:13px}.recent{display:grid;gap:7px}.recent button{display:grid;gap:3px;padding:10px 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left}.recent strong{display:-webkit-box;overflow:hidden;-webkit-line-clamp:2;-webkit-box-orient:vertical}.recent small{color:var(--color-text-secondary)}.domain-band{display:grid;grid-template-columns:repeat(6,1fr);border:1px solid var(--color-border-default);border-radius:0 0 18px 18px;overflow:hidden;background:white}.domain-band>div{display:grid;gap:3px;min-height:74px;padding:12px;border-right:1px solid var(--color-border-subtle);background:white;text-align:left}.domain-band span{font-weight:750}.domain-band small{color:var(--color-text-secondary);font-size:11px}.notice,.error{margin:0;padding:10px 16px;border-inline:1px solid var(--color-border-default)}.notice{background:var(--teal-soft)}.error{background:#fff0ed;color:var(--red)}button:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #e29d45;outline-offset:2px}@media(max-width:980px){.desk__masthead{align-items:flex-start;flex-direction:column}.desk__body{grid-template-columns:1fr}.conversation{border-right:0}.side-notes{grid-template-columns:1fr 1fr}.domain-band{grid-template-columns:repeat(3,1fr)}}@media(max-width:640px){.desk__masthead,.conversation{padding:18px}.desk__tools{width:100%;align-items:stretch;flex-direction:column}.desk__tools label{width:100%}.turn blockquote,.assistant{max-width:100%}.composer>div{align-items:stretch;flex-direction:column}.side-notes{grid-template-columns:1fr}.domain-band{grid-template-columns:repeat(2,1fr)}}
</style>
