<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { intakeApi, type HandoffDraft, type IntakeConversation, type IntakeHandoffSummary } from '../api/intake'
import { supportApi, type SupportRecord } from '../api/support'
import { autoAdoptStudentRecordHandoffs } from '../intake/auto-adopt'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'
import { ApiError } from '@/api/errors'
import {
  studentR1Api,
  studentRefOf,
  type CurrentStudentProfile,
  type DirectorySubject,
  type StudentCard,
  type StudentProfileDimension,
  type StudentSupportFocus,
} from '../api/r1'

type DrawerTab = 'overview' | 'support' | 'academic'
const props = defineProps<{
  subject: DirectorySubject
  conversationId?: string | null
  initialTab?: DrawerTab
}>()
const emit = defineEmits<{ close: []; open: [panel: 'support' | 'academic'] }>()

const card = ref<StudentCard | null>(null)
const conversation = ref<IntakeConversation | null>(null)
const proposal = ref<HandoffDraft | null>(null)
const adoptedHandoff = ref<IntakeHandoffSummary | null>(null)
const revertedHandoff = ref<IntakeHandoffSummary | null>(null)
const message = ref('')
const state = ref<'loading' | 'ready' | 'error'>('loading')
const busy = ref(false)
const notice = ref('')
const error = ref('')
let pollTimer: number | null = null
let pollGeneration = 0

const profile = computed<CurrentStudentProfile>(() => card.value?.current_profile ?? ({
  entry_id: null,
  revision: 0,
  summary: '',
  dimensions: [],
  open_questions: [],
  support_focus: [],
  updated_at: null,
}))
const proposedProfile = computed<CurrentStudentProfile | null>(() => {
  const raw = proposal.value?.content.profile_update
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const item = raw as Record<string, unknown>
  if (typeof item.summary !== 'string' || !Array.isArray(item.dimensions)) return null
  return {
    entry_id: profile.value.entry_id,
    revision: profile.value.revision,
    summary: item.summary,
    dimensions: item.dimensions as StudentProfileDimension[],
    open_questions: Array.isArray(item.open_questions) ? item.open_questions.map(String) : [],
    support_focus: Array.isArray(item.support_focus) ? item.support_focus as StudentSupportFocus[] : [],
    updated_at: profile.value.updated_at,
  }
})
const hasProfile = computed(() => Boolean(profile.value.summary || profile.value.dimensions.length))
const roundChanges = computed(() => profile.value.latest_round?.changed ?? null)
const hasRoundChanges = computed(() => Boolean(
  roundChanges.value
  && (roundChanges.value.summary_changed
    || Object.keys(roundChanges.value.dimensions).length
    || roundChanges.value.open_questions.length
    || roundChanges.value.support_focus.length),
))

function isRoundNewItem(dimensionKey: string, item: string): boolean {
  return Boolean(roundChanges.value?.dimensions[dimensionKey]?.includes(item))
}

function isRoundNewQuestion(question: string): boolean {
  return Boolean(roundChanges.value?.open_questions.includes(question))
}

function isRoundNewFocus(key: string): boolean {
  return Boolean(roundChanges.value?.support_focus.includes(key))
}
const profileNotCreated = computed(() => card.value?.profile_state === 'not_created' || props.subject.profile_state === 'not_created')
const supportPlans = computed(() => card.value?.support_plans ?? [])
const homeBound = computed(() => Boolean(props.conversationId))
const activeTab = ref<DrawerTab>(props.initialTab ?? 'overview')
const dialog = ref<HTMLElement | null>(null)
const messageInput = ref<HTMLTextAreaElement | null>(null)
const sourceDetails = ref<HTMLDetailsElement | null>(null)
let previouslyFocused: HTMLElement | null = null
let previousBodyOverflow = ''

// 参考材料区：原始支持记录的查看、撤回/恢复与补录，不经过 AI。
const recordKindLabels: Record<string, string> = {
  fact: '可核对事实',
  student_statement: '学生陈述',
  reported_statement: '转述信息',
  teacher_observation: '教师观察',
  provisional_judgment: '阶段性判断',
  professional_conclusion: '专业结论',
}
const sourceRecords = ref<SupportRecord[] | null>(null)
const sourceLoading = ref(false)
const recordBusy = ref(false)
const recordNotice = ref('')
const recordError = ref('')
const withdrawingId = ref<string | null>(null)
const withdrawReason = ref('记录有误，撤回')
const addOpen = ref(false)
const addContent = ref('')
const addObservedAt = ref(new Date().toISOString().slice(0, 10))
const addReviewAt = ref('')
const addExpiresAt = ref('')
const visibleSourceRecords = computed(() =>
  (sourceRecords.value ?? []).filter((record) => record.record_kind !== 'ai_draft'),
)
const canAddObservation = computed(() => Boolean(
  addContent.value.trim() && addObservedAt.value && addReviewAt.value && addExpiresAt.value,
))

function recordDay(value: string | null | undefined): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10)
  const pad = (unit: number) => String(unit).padStart(2, '0')
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`
}

async function loadSourceRecords(): Promise<void> {
  if (sourceLoading.value) return
  sourceLoading.value = true
  recordError.value = ''
  try {
    sourceRecords.value = await supportApi.listRecords(studentRefOf(props.subject))
  } catch {
    recordError.value = '原始记录暂时无法读取；已有内容没有改变。'
  } finally {
    sourceLoading.value = false
  }
}

function onSourceToggle(): void {
  if (sourceDetails.value?.open && sourceRecords.value === null) void loadSourceRecords()
}

async function refreshCard(): Promise<void> {
  try {
    card.value = await studentR1Api.studentCard(studentRefOf(props.subject))
  } catch {
    // 保留抽屉里的现有档案内容，不打扰当前状态。
  }
}

function startWithdrawRecord(record: SupportRecord): void {
  withdrawingId.value = record.record_id
  withdrawReason.value = '记录有误，撤回'
}

async function changeRecordState(record: SupportRecord, nextState: 'active' | 'withdrawn'): Promise<void> {
  if (recordBusy.value) return
  recordBusy.value = true
  recordNotice.value = ''
  recordError.value = ''
  try {
    await supportApi.setRecordState(
      record,
      nextState,
      nextState === 'withdrawn' ? withdrawReason.value.trim() || '教师撤回记录' : '教师恢复记录',
    )
    withdrawingId.value = null
    recordNotice.value = nextState === 'withdrawn'
      ? '已撤回，记录不再计入档案与跟进提醒；仍留痕，可恢复。'
      : '已恢复为有效记录。'
    await loadSourceRecords()
    await refreshCard()
  } catch {
    recordError.value = '状态没有更新；现有记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    recordBusy.value = false
  }
}

function openAddForm(): void {
  addOpen.value = true
  addContent.value = ''
  addObservedAt.value = new Date().toISOString().slice(0, 10)
  addReviewAt.value = ''
  addExpiresAt.value = ''
  recordNotice.value = ''
  recordError.value = ''
}

async function addObservation(): Promise<void> {
  if (!canAddObservation.value || recordBusy.value) return
  recordBusy.value = true
  recordNotice.value = ''
  recordError.value = ''
  try {
    await supportApi.createRecord(studentRefOf(props.subject), {
      record_kind: 'teacher_observation',
      content: addContent.value.trim(),
      scene: '日常观察',
      source: '教师本人观察',
      basis: null,
      counterexample: null,
      category: 'general',
      observed_at: addObservedAt.value,
      review_at: addReviewAt.value,
      expires_at: addExpiresAt.value,
    })
    addOpen.value = false
    recordNotice.value = '已按教师确认补录为观察记录；没有调用 AI，也没有自动生成诊断或结论。'
    await loadSourceRecords()
    await refreshCard()
  } catch {
    recordError.value = '记录没有保存；现有记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    recordBusy.value = false
  }
}

function selectTab(tab: DrawerTab): void {
  activeTab.value = tab
}

async function continueProfile(): Promise<void> {
  activeTab.value = 'support'
  await nextTick()
  messageInput.value?.focus()
}

async function showSourceMaterials(): Promise<void> {
  activeTab.value = 'support'
  await nextTick()
  if (sourceDetails.value) sourceDetails.value.open = true
  if (sourceRecords.value === null) void loadSourceRecords()
  sourceDetails.value?.focus()
}

function closeDrawer(): void {
  emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeDrawer()
}

function stopPolling(): void {
  pollGeneration += 1
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = null
}

async function load(): Promise<void> {
  state.value = 'loading'
  error.value = ''
  try {
    card.value = await studentR1Api.studentCard(studentRefOf(props.subject))
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

async function loadProposal(next: IntakeConversation): Promise<void> {
  const candidate = [...next.handoffs]
    .reverse()
    .find(item => ['pending', 'opened', 'adopted', 'reverted'].includes(item.adoption_state) && item.destination_key === 'class_teacher.student.record')
  proposal.value = null
  adoptedHandoff.value = null
  revertedHandoff.value = null
  if (!candidate) return
  const loaded = await intakeApi.handoff(candidate.handoff_id)
  const boundId = loaded.subject_refs[0]?.id
  if (boundId && boundId !== studentRefOf(props.subject)) return
  if (candidate.adoption_state === 'adopted') adoptedHandoff.value = candidate
  else if (candidate.adoption_state === 'reverted') revertedHandoff.value = candidate
  else proposal.value = loaded
}

async function settleProposal(next: IntakeConversation): Promise<void> {
  const outcome = await autoAdoptStudentRecordHandoffs(next)
  if (outcome === 'adopted') {
    const refreshed = await intakeApi.conversation(next.conversation_id)
    conversation.value = refreshed
    await load()
    await loadProposal(refreshed)
    notice.value = '本轮档案更新已自动并入当前档案；如不合适可一键撤回。'
    return
  }
  if (outcome === 'conflict') {
    notice.value = '档案在自动并入前发生了变化，请核对最新档案后手动应用。'
  }
  await loadProposal(next)
  if (outcome !== 'none') return
  notice.value = next.state === 'needs_input'
    ? 'AI 找到了值得继续了解的问题。可以直接回答，也可以先应用已经整理好的内容。'
    : proposal.value
      ? 'AI 已把这轮信息合并成当前档案草稿，请核对后应用。'
      : '本轮没有形成可应用的档案更新。'
}

async function bindHomeConversation(id: string): Promise<void> {
  try {
    const next = await intakeApi.conversation(id)
    conversation.value = next
    await loadProposal(next)
    notice.value = proposal.value
      ? 'AI 已把这轮信息合并成当前档案草稿；自动并入未完成，请核对后手动应用。'
      : adoptedHandoff.value
        ? '本轮档案更新已自动并入当前档案；如不合适可一键撤回。'
        : revertedHandoff.value
          ? '本轮档案更新已撤回，档案回到更新前。'
          : '本轮没有形成可应用的档案更新。'
  } catch {
    error.value = '本轮档案草稿暂时无法读取。当前档案没有改变。'
  }
}

// 独立打开抽屉（非首页对话绑定）时，主动查询这名学生是否有待核对的档案交接，
// 让「待核对草稿」在抽屉顶部可见，可直接应用或放弃。
async function loadPendingProposal(): Promise<void> {
  try {
    const items = await intakeApi.pendingStudentHandoffs(studentRefOf(props.subject))
    // 后端已按对外学生编号（稳定学籍标识）归一过滤；
    // 这里只认返回项里的 student_ref，不再拿内部编号或草稿原始 ref 直接比较。
    const latest = items.find(item =>
      ['pending', 'opened'].includes(item.adoption_state)
      && item.student_ref === studentRefOf(props.subject))
    if (!latest) return
    proposal.value = await intakeApi.handoff(latest.handoff_id)
  } catch {
    // 待核对提示读取失败时不影响档案查看。
  }
}

function poll(id: string): void {
  stopPolling()
  const generation = pollGeneration
  const read = async () => {
    if (generation !== pollGeneration) return
    try {
      const next = await intakeApi.conversation(id)
      if (generation !== pollGeneration) return
      conversation.value = next
      if (next.state === 'ai_running') {
        pollTimer = window.setTimeout(read, 1400)
        return
      }
      pollTimer = null
      await settleProposal(next)
    } catch {
      pollTimer = window.setTimeout(read, 2400)
    }
  }
  pollTimer = window.setTimeout(read, 700)
}

async function send(): Promise<void> {
  const outgoing = message.value.trim()
  if (!outgoing || busy.value || conversation.value?.state === 'ai_running') return
  busy.value = true
  error.value = ''
  notice.value = 'AI 正在结合这名学生的当前档案整理；可以离开页面，系统不会自动重复发送。'
  try {
    if (!conversation.value) {
      conversation.value = await intakeApi.startStudentConversation(studentRefOf(props.subject))
    }
    conversation.value = await intakeApi.appendTurn(conversation.value, outgoing)
    message.value = ''
    if (conversation.value.state === 'ai_running') poll(conversation.value.conversation_id)
    else await settleProposal(conversation.value)
  } catch {
    error.value = '这次整理没有完成。已输入内容仍在文本框中，请稍后继续。'
  } finally {
    busy.value = false
  }
}

async function applyProposal(): Promise<void> {
  if (!proposal.value || !proposedProfile.value || busy.value) return
  const target = proposal.value.subject_refs[0]?.revision
  if (!target) {
    error.value = '这份草稿没有绑定当前学生，请重新整理。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await intakeApi.adopt(proposal.value, target)
    proposal.value = null
    await load()
    notice.value = '当前学生档案已经更新。下一轮对话会从这份新档案继续整理。'
    if (conversation.value) conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
  } catch {
    error.value = '档案在保存前发生了变化。这份草稿仍保留，请刷新后重新核对。'
  } finally {
    busy.value = false
  }
}

async function discardProposal(): Promise<void> {
  if (!proposal.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await intakeApi.discard(proposal.value.handoff_id)
    proposal.value = null
    notice.value = '这轮档案更新已放弃，当前档案没有改变。'
    if (conversation.value) conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
  } catch {
    error.value = '这轮更新暂时无法放弃，草稿仍然保留，没有写入当前档案。'
  } finally {
    busy.value = false
  }
}

async function revertProposal(): Promise<void> {
  if (!adoptedHandoff.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await intakeApi.revertProfile(adoptedHandoff.value.handoff_id)
    adoptedHandoff.value = null
    await load()
    notice.value = '已撤回，档案回到本轮更新前；本轮原始记录仍保留。'
    if (conversation.value) {
      conversation.value = await intakeApi.conversation(conversation.value.conversation_id)
      await loadProposal(conversation.value)
    }
  } catch (value) {
    error.value = value instanceof ApiError && value.status === 409
      ? '档案已有更新轮次，无法一键撤回，请手动修正。'
      : '撤回没有完成，档案保持当前内容，请稍后再试。'
  } finally {
    busy.value = false
  }
}

function updatedLabel(value: string | null): string {
  if (!value) return '尚未建立'
  return value.replace('T', ' ').slice(0, 16)
}

function planText(plan: Record<string, unknown>, key: string): string {
  const value = plan[key]
  if (Array.isArray(value)) return value.map(String).join('；')
  return String(value ?? '')
}

watch(
  [() => props.subject.student_ref, () => props.conversationId],
  async () => {
    stopPolling()
    activeTab.value = props.initialTab ?? 'overview'
    conversation.value = null
    proposal.value = null
    adoptedHandoff.value = null
    revertedHandoff.value = null
    message.value = ''
    notice.value = ''
    error.value = ''
    sourceRecords.value = null
    withdrawingId.value = null
    addOpen.value = false
    recordNotice.value = ''
    recordError.value = ''
    await load()
    if (props.conversationId) await bindHomeConversation(props.conversationId)
    else await loadPendingProposal()
  },
)
onMounted(() => {
  previouslyFocused = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  previousBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
  void load().then(async () => {
    if (props.conversationId) await bindHomeConversation(props.conversationId)
    else await loadPendingProposal()
  })
  void nextTick(() => dialog.value?.focus())
})
onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = previousBodyOverflow
  previouslyFocused?.focus()
})
</script>

<template>
  <div class="dossier-layer">
    <button class="dossier-backdrop" type="button" aria-label="关闭学生档案" @click="closeDrawer" />
    <aside
      ref="dialog"
      class="dossier"
      role="dialog"
      aria-modal="true"
      aria-labelledby="student-dossier-title"
      tabindex="-1"
    >
      <span class="folder-tab" aria-hidden="true">学生当前档案</span>
      <header class="dossier__masthead">
        <div class="identity">
          <span class="identity__seal" aria-hidden="true">{{ subject.display_name.slice(0, 1) }}</span>
          <div>
            <h1 id="student-dossier-title">{{ subject.display_name }}</h1>
            <p>学号 {{ subject.source_student_id }} · {{ formatClassLabel(subject.class_label) }}</p>
          </div>
        </div>
        <button type="button" class="close" aria-label="关闭学生档案" @click="closeDrawer">×</button>
      </header>

      <section v-if="!homeBound && proposal && proposedProfile" class="pending-banner" aria-label="待核对档案更新">
        <div>
          <strong>有一轮 AI 整理的档案更新待核对</strong>
          <p>{{ proposedProfile.summary }}</p>
        </div>
        <div class="pending-banner__actions">
          <AppButton variant="primary" :disabled="busy" @click="applyProposal">应用更新</AppButton>
          <AppButton variant="ghost" :disabled="busy" @click="discardProposal">放弃</AppButton>
        </div>
      </section>

      <nav class="dossier-tabs" aria-label="学生档案分区">
        <button type="button" :aria-selected="activeTab === 'overview'" @click="selectTab('overview')">当前概览</button>
        <button type="button" :aria-selected="activeTab === 'support'" @click="selectTab('support')">成长与支持</button>
        <button type="button" :aria-selected="activeTab === 'academic'" @click="selectTab('academic')">学业证据</button>
      </nav>

      <div v-if="state === 'loading'" class="page-state" role="status">正在展开学生当前档案…</div>
      <div v-else-if="state === 'error'" class="page-state">
        <strong>学生档案暂时无法读取</strong>
        <p>请重新读取；其他学生和原有资料没有改变。</p>
        <AppButton variant="secondary" @click="load">重新读取</AppButton>
      </div>

      <main v-else class="dossier__scroll">
        <section v-show="activeTab === 'overview'" class="tab-panel overview-panel" aria-label="当前概览">
          <blockquote v-if="profile.summary" class="profile-summary"><span v-if="roundChanges?.summary_changed" class="round-badge">本轮</span>{{ profile.summary }}</blockquote>
          <div v-else class="empty-profile">
            <strong>{{ profileNotCreated ? '档案尚未建立' : '这份档案还没有开始生长' }}</strong>
            <p v-if="profileNotCreated">核对下方待并入的草稿，确认保存后会自动创建档案。</p>
            <p v-else>到“成长与支持”写下已经了解的情况，形成第一份结构化档案。</p>
          </div>

          <div class="quick-stats">
            <div><strong>{{ profile.dimensions.length }}</strong><span>当前有效维度</span></div>
            <div><strong>{{ subject.confirmed_entry_count }}</strong><span>已确认记录</span></div>
            <div><strong>{{ updatedLabel(profile.updated_at) }}</strong><span>最近完善</span></div>
          </div>

          <header class="section-heading">
            <div><small>此刻对这名学生的认识</small><h2>当前结构化档案</h2></div>
          </header>
          <p v-if="hasRoundChanges" class="round-legend">带「本轮」标记的内容是最近一轮对话并入的更新。</p>
          <div v-if="hasProfile" class="dimension-grid">
            <article v-for="dimension in profile.dimensions" :key="dimension.key" class="dimension">
              <h3>{{ dimension.label }}</h3>
              <ul><li v-for="item in dimension.items" :key="item"><span v-if="isRoundNewItem(dimension.key, item)" class="round-badge">本轮</span>{{ item }}</li></ul>
            </article>
          </div>
          <div v-if="profile.support_focus.length" class="support-preview">
            <small>当前最值得关注</small>
            <strong>{{ profile.support_focus[0]?.title }}</strong>
            <p>{{ profile.support_focus[0]?.need }}</p>
            <AppButton variant="ghost" @click="selectTab('support')">查看支持重点与下一步</AppButton>
          </div>
          <div v-if="!homeBound" class="ai-entry">
            <p>把最近了解到的情况直接告诉 AI，会自动整理进上面的档案维度。</p>
            <AppButton variant="primary" @click="continueProfile">向 AI 补充这名学生的情况</AppButton>
          </div>
          <p v-if="homeBound && notice" class="notice home-notice" role="status">{{ notice }}</p>
          <p v-if="homeBound && error" class="error home-error" role="alert">{{ error }}</p>
          <section v-if="homeBound && adoptedHandoff" class="proposal" aria-labelledby="home-adopted-title">
            <header><div><small>本轮档案更新</small><h2 id="home-adopted-title">已自动并入当前档案</h2></div><AppButton variant="secondary" :disabled="busy" @click="revertProposal">撤回本轮更新</AppButton></header>
            <footer><span>撤回后档案回到本轮更新前；本轮原始记录仍保留。</span></footer>
          </section>
          <section v-if="homeBound && proposal && proposedProfile" class="proposal" aria-labelledby="home-proposal-title">
            <header><div><small>本轮拟更新</small><h2 id="home-proposal-title">把新认识并入当前档案</h2></div><AppButton variant="primary" :disabled="busy" @click="applyProposal">应用到当前档案</AppButton></header>
            <p class="proposal__summary">{{ proposedProfile.summary }}</p>
            <div class="proposal__grid"><article v-for="dimension in proposedProfile.dimensions" :key="dimension.key"><strong>{{ dimension.label }}</strong><ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul></article></div>
            <footer><span>应用后仍然只有一份当前档案。关闭抽屉后，可在原对话里告诉 AI 怎么改。</span><AppButton variant="ghost" :disabled="busy" @click="discardProposal">放弃本轮更新</AppButton></footer>
          </section>
        </section>

        <section v-show="activeTab === 'support'" class="tab-panel support-panel" aria-label="成长与支持">
          <section v-if="!homeBound" class="ai-desk" aria-labelledby="ai-desk-title">
            <div class="ai-desk__heading">
              <div><small>和 AI 一起完善这份档案</small><h2 id="ai-desk-title">告诉我最近又了解到了什么</h2></div>
              <p>不用先分类。AI 会结合当前档案整理到合适维度，并提出少量值得继续了解的问题。</p>
            </div>
            <div v-if="conversation?.turns.length" class="dialogue" aria-live="polite">
              <article v-for="turn in conversation.turns" :key="turn.turn_id" class="dialogue__turn">
                <p class="teacher-quote">{{ turn.teacher_message }}</p>
                <div v-if="turn.assistant_message || turn.clarification_questions.length" class="ai-reply">
                  <strong>AI 整理</strong><p v-if="turn.assistant_message">{{ turn.assistant_message }}</p>
                  <ul v-if="turn.clarification_questions.length"><li v-for="question in turn.clarification_questions" :key="question">{{ question }}</li></ul>
                </div>
              </article>
            </div>
            <form class="composer" @submit.prevent="send">
              <textarea
                ref="messageInput"
                v-model="message"
                rows="4"
                maxlength="4000"
                :disabled="busy || conversation?.state === 'ai_running'"
                :placeholder="`例如：${subject.display_name}最近在小组任务中更愿意主动分工，但遇到意见冲突时容易直接退出讨论……`"
              />
              <footer><span>{{ message.length }}/4000</span><AppButton variant="primary" type="submit" :disabled="!message.trim() || busy || conversation?.state === 'ai_running'">{{ conversation?.state === 'ai_running' ? '正在整理当前档案…' : '交给 AI 整理' }}</AppButton></footer>
            </form>
            <p v-if="notice" class="notice" role="status">{{ notice }}</p>
            <p v-if="error" class="error" role="alert">{{ error }}</p>
          </section>

          <section v-if="adoptedHandoff" class="proposal" aria-labelledby="adopted-title">
            <header><div><small>本轮档案更新</small><h2 id="adopted-title">已自动并入当前档案</h2></div><AppButton variant="secondary" :disabled="busy" @click="revertProposal">撤回本轮更新</AppButton></header>
            <footer><span>撤回后档案回到本轮更新前；本轮原始记录仍保留。</span></footer>
          </section>

          <section v-if="proposal && proposedProfile" class="proposal" aria-labelledby="proposal-title">
            <header><div><small>本轮拟更新</small><h2 id="proposal-title">把新认识并入当前档案</h2></div><AppButton variant="primary" :disabled="busy" @click="applyProposal">应用到当前档案</AppButton></header>
            <p class="proposal__summary">{{ proposedProfile.summary }}</p>
            <div class="proposal__grid"><article v-for="dimension in proposedProfile.dimensions" :key="dimension.key"><strong>{{ dimension.label }}</strong><ul><li v-for="item in dimension.items" :key="item">{{ item }}</li></ul></article></div>
            <footer><span>应用后仍然只有一份当前档案。</span><AppButton variant="ghost" :disabled="busy" @click="discardProposal">放弃本轮更新</AppButton></footer>
          </section>

          <div class="support-grid">
            <section class="support-section">
              <header><small>与个人档案直接相连</small><h2>当前学生支持</h2></header>
              <article v-for="focus in profile.support_focus" :key="focus.key">
                <h3><span v-if="isRoundNewFocus(focus.key)" class="round-badge">本轮</span>{{ focus.title }}</h3><p>{{ focus.need }}</p>
                <template v-if="focus.effective_methods.length"><strong>已经有效</strong><ul><li v-for="item in focus.effective_methods" :key="item">{{ item }}</li></ul></template>
                <template v-if="focus.next_actions.length"><strong>接下来尝试</strong><ul><li v-for="item in focus.next_actions" :key="item">{{ item }}</li></ul></template>
              </article>
              <p v-if="!profile.support_focus.length" class="compact-empty">暂无明确支持重点。继续完善后，这里会显示已经有效的方法和下一步。</p>
            </section>
            <section class="support-section questions">
              <header><small>后续谈话可以留意</small><h2>仍需了解</h2></header>
              <ol v-if="profile.open_questions.length"><li v-for="item in profile.open_questions" :key="item"><span v-if="isRoundNewQuestion(item)" class="round-badge">本轮</span>{{ item }}</li></ol>
              <p v-if="hasRoundChanges" class="round-legend questions-legend">带「本轮」标记的内容是最近一轮对话并入的更新。</p>
              <p v-if="!profile.open_questions.length" class="compact-empty">当前没有尚待了解的问题。</p>
            </section>
            <section v-if="supportPlans.length" class="support-section active-plans">
              <header><small>正在执行</small><h2>支持方案</h2></header>
              <article v-for="plan in supportPlans" :key="String(plan.support_plan_id || plan.goal)"><strong>{{ planText(plan, 'goal') }}</strong><p>{{ planText(plan, 'support_actions') }}</p></article>
            </section>
          </div>
          <details ref="sourceDetails" class="source-materials" tabindex="-1" @toggle="onSourceToggle">
            <summary>参考材料 · {{ card?.existing_records.length || 0 }} 条原始记录</summary>
            <p>这些记录只作为完善当前档案的来源，不再占据学生页的主要位置；撤回后不再计入档案与跟进提醒，仍可恢复。</p>
            <p v-if="recordNotice" class="notice source-notice" role="status">{{ recordNotice }}</p>
            <p v-if="recordError" class="error source-error" role="alert">{{ recordError }}</p>
            <p v-if="sourceRecords === null" class="source-hint">{{ sourceLoading ? '正在读取原始记录…' : '展开后读取原始记录。' }}</p>
            <ul v-else-if="visibleSourceRecords.length" class="source-list">
              <li v-for="record in visibleSourceRecords" :key="record.record_id" :class="{ withdrawn: record.state === 'withdrawn' }">
                <div class="source-line">
                  <strong>{{ recordKindLabels[record.record_kind] ?? record.record_kind }}</strong>
                  <time>{{ recordDay(record.observed_at) }}</time>
                  <span v-if="record.state === 'withdrawn'" class="state-badge">已撤回</span>
                </div>
                <p class="source-content">{{ record.content }}</p>
                <div class="source-actions">
                  <AppButton v-if="record.state !== 'withdrawn' && withdrawingId !== record.record_id" variant="ghost" :disabled="recordBusy" @click="startWithdrawRecord(record)">撤回</AppButton>
                  <AppButton v-if="record.state === 'withdrawn'" variant="ghost" :disabled="recordBusy" @click="changeRecordState(record, 'active')">恢复</AppButton>
                </div>
                <div v-if="withdrawingId === record.record_id" class="withdraw-confirm">
                  <input v-model="withdrawReason" maxlength="200" aria-label="撤回原因" placeholder="撤回原因（留痕）">
                  <div>
                    <AppButton variant="primary" :disabled="recordBusy" @click="changeRecordState(record, 'withdrawn')">确认撤回</AppButton>
                    <AppButton variant="ghost" :disabled="recordBusy" @click="withdrawingId = null">取消</AppButton>
                  </div>
                </div>
              </li>
            </ul>
            <p v-else class="source-hint">暂无原始记录。</p>
            <div class="add-observation">
              <AppButton v-if="!addOpen" variant="ghost" @click="openAddForm">＋ 补录一条观察记录</AppButton>
              <form v-else class="add-form" @submit.prevent="addObservation">
                <label class="wide"><span>观察内容</span><textarea v-model="addContent" rows="3" maxlength="8000" placeholder="记录可核对的观察，不贴永久标签"></textarea></label>
                <label><span>观察日期</span><input v-model="addObservedAt" type="date"></label>
                <label><span>复查日期</span><input v-model="addReviewAt" type="date"></label>
                <label><span>失效日期</span><input v-model="addExpiresAt" type="date"></label>
                <p class="add-hint">观察记录需要复查与失效日期，到期后自动不再计入档案摘要；保存只由你确认，不调用 AI。</p>
                <footer>
                  <AppButton variant="primary" type="submit" :disabled="recordBusy || !canAddObservation">确认补录</AppButton>
                  <AppButton variant="ghost" :disabled="recordBusy" @click="addOpen = false">取消</AppButton>
                </footer>
              </form>
            </div>
          </details>
        </section>

        <section v-show="activeTab === 'academic'" class="tab-panel academic-panel" aria-label="学业证据">
          <div class="academic-intro"><small>保持名单上下文</small><h2>需要时再进入完整学业证据</h2><p>当前抽屉不重复堆放成绩与图表。完整页面会沿用这名学生，并展示现有考试证据、可比较变化和教师待处理事项。</p></div>
          <div class="quick-stats academic-stats">
            <div><strong>{{ subject.confirmed_entry_count }}</strong><span>已确认档案记录</span></div>
            <div><strong>{{ subject.attention_pending_count }}</strong><span>待处理关注项</span></div>
            <div><strong>{{ updatedLabel(subject.last_confirmed_at) }}</strong><span>最近确认记录</span></div>
          </div>
          <p v-if="profileNotCreated" class="compact-empty">档案尚未建立，确认保存待并入的草稿后可查看完整学业证据。</p>
          <AppButton v-else variant="primary" block @click="emit('open', 'academic')">打开完整学业证据</AppButton>
        </section>
      </main>

      <footer v-if="state === 'ready'" class="dossier-actions">
        <AppButton variant="ghost" class="quiet" @click="showSourceMaterials">查看 {{ card?.existing_records.length || 0 }} 条原始记录</AppButton>
        <div v-if="!profileNotCreated">
          <AppButton variant="ghost" @click="emit('open', 'support')">支持工作区</AppButton>
          <AppButton v-if="activeTab !== 'academic' && !homeBound" variant="primary" @click="continueProfile">继续完善档案</AppButton>
          <AppButton v-else-if="activeTab === 'academic'" variant="primary" @click="emit('open', 'academic')">打开学业证据</AppButton>
        </div>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.dossier-layer{position:fixed;z-index:65;inset:var(--shell-topbar-height,64px) 0 0;color:var(--foreground)}
.dossier-backdrop{position:absolute;inset:0;border:0;background:var(--color-overlay-mask);cursor:default}
.dossier{position:absolute;inset-block:0;right:0;display:grid;width:min(620px,92vw);grid-template-rows:auto auto minmax(0,1fr) auto;border:0;border-left:1px solid var(--border);outline:0;background:var(--card);box-shadow:var(--shadow-overlay);animation:drawer-in .22s ease-out}
.folder-tab{position:absolute;top:78px;left:-42px;width:42px;padding:11px 9px;border-radius:var(--radius) 0 0 var(--radius);background:var(--primary);color:var(--primary-foreground);font-size:12px;font-weight:700;letter-spacing:.12em;text-align:center;writing-mode:vertical-rl}
.dossier__masthead{display:flex;align-items:center;gap:13px;padding:16px 20px;border-bottom:1px solid var(--border);background:var(--card)}.identity{display:flex;min-width:0;align-items:center;gap:13px;flex:1}.identity__seal{display:grid;width:44px;height:44px;place-items:center;flex:0 0 auto;border-radius:var(--radius);background:var(--primary);color:var(--primary-foreground);font-size:22px;font-weight:700}.identity h1{margin:0;font-size:22px}.identity p{margin:2px 0 0;color:var(--muted-foreground);font-size:13px}.close{width:40px;height:40px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);color:var(--foreground);font:inherit;font-size:24px;cursor:pointer}.close:hover{background:var(--accent)}
.dossier-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--muted)}.dossier-tabs button{min-height:36px;border:0;border-radius:var(--radius);background:transparent;color:var(--muted-foreground);font:inherit;cursor:pointer}.dossier-tabs button:hover{color:var(--foreground)}.dossier-tabs button[aria-selected=true]{background:var(--card);color:var(--primary);font-weight:700}
.pending-banner{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--color-warning-subtle)}.pending-banner strong{font-size:14px}.pending-banner p{margin:4px 0 0;color:var(--color-text-secondary);font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.pending-banner__actions{display:flex;flex:0 0 auto;gap:8px}
.dossier__scroll{overflow:auto;padding:16px 20px 24px}.tab-panel{display:grid;gap:16px}.page-state{display:grid;place-content:center;justify-items:center;gap:8px;padding:28px;text-align:center}.page-state p{color:var(--muted-foreground)}
.profile-summary{margin:0;padding:14px 16px;border:0;border-left:3px solid var(--color-warning);border-radius:0 var(--radius) var(--radius) 0;background:var(--color-warning-subtle);color:var(--foreground);font-size:16px;line-height:1.65}.empty-profile{display:grid;min-height:160px;place-content:center;padding:20px;text-align:center}.empty-profile p{color:var(--muted-foreground)}.quick-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.quick-stats>div{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.quick-stats strong{display:block;color:var(--primary);font-size:20px}.quick-stats span{color:var(--muted-foreground);font-size:12px}.section-heading{padding-top:2px;border-bottom:1px solid var(--border)}.section-heading small,.ai-desk small,.proposal small,.support-section small,.academic-intro small{color:var(--primary);font-size:12px;font-weight:700;letter-spacing:.1em}.section-heading h2,.ai-desk h2,.proposal h2,.support-section h2,.academic-intro h2{margin:2px 0 12px;font-size:18px}.dimension-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.dimension{padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.dimension h3{margin:0 0 8px;font-size:15px}.dimension ul,.support-section ul,.support-section ol{margin:0;padding-left:19px;color:var(--color-text-secondary);line-height:1.65}.support-preview{display:grid;gap:5px;justify-items:start;padding:14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--color-warning-subtle)}.support-preview small{color:var(--color-warning);font-weight:700}.support-preview p{margin:0;color:var(--color-text-secondary);line-height:1.55}.ai-entry{display:grid;gap:10px;justify-items:start;padding:14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--accent)}.ai-entry p{margin:0;color:var(--color-text-secondary);line-height:1.55}
.ai-desk{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.ai-desk__heading{padding:16px 18px 12px;background:var(--accent)}.ai-desk__heading p{margin:5px 0 0;color:var(--color-text-secondary);line-height:1.6}.dialogue{display:grid;gap:12px;max-height:280px;overflow:auto;padding:14px 16px 0}.teacher-quote{justify-self:end;max-width:88%;margin:0;padding:10px 13px;border-radius:var(--radius) var(--radius) 2px var(--radius);background:var(--accent);line-height:1.6}.ai-reply{padding:12px 14px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:var(--radius);background:var(--card)}.ai-reply p,.ai-reply ul{margin:5px 0 0;line-height:1.6}.composer{margin:14px 16px 16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.composer textarea{box-sizing:border-box;width:100%;padding:12px;border:0;border-radius:var(--radius) var(--radius) 0 0;outline:0;resize:vertical;background:transparent;font:inherit;line-height:1.6}.composer:focus-within{border-color:var(--ring);box-shadow:var(--focus-ring)}.composer footer{display:flex;align-items:center;justify-content:space-between;padding:8px 10px 8px 13px;border-top:1px solid var(--border);color:var(--muted-foreground);font-size:12px}.notice,.error{margin:-6px 16px 14px;padding:9px 11px;border-radius:var(--radius)}.notice{background:var(--accent);color:var(--primary)}.error{background:var(--color-danger-subtle);color:var(--destructive)}.home-notice,.home-error{margin:0}
.proposal{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.proposal>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 16px;border-bottom:1px solid var(--border);background:var(--color-warning-subtle)}.proposal__summary{margin:0;padding:14px 16px;font-size:16px;line-height:1.6}.proposal__grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:0 16px 14px}.proposal__grid article{padding:10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.proposal__grid ul{margin:7px 0 0;padding-left:18px}.proposal>footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 16px;border-top:1px solid var(--border);color:var(--color-text-secondary);font-size:12px}
.support-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.support-section{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.support-section>header{padding:12px 16px;border-bottom:1px solid var(--border);background:var(--muted)}.support-section h2{margin-bottom:0}.support-section article{margin:12px;padding:12px;border:1px solid var(--border);border-radius:var(--radius)}.support-section article h3{margin:0}.support-section article p{margin:7px 0;line-height:1.55}.support-section article strong{display:block;margin-top:10px;color:var(--muted-foreground);font-size:12px}.compact-empty{margin:0;padding:16px;color:var(--muted-foreground);line-height:1.6}.questions ol{padding:14px 34px}.active-plans{grid-column:1/-1}.source-materials{padding:14px 16px;border:1px dashed var(--border);border-radius:var(--radius);background:var(--muted);color:var(--color-text-secondary)}.source-materials summary{cursor:pointer;font-weight:700}.source-materials p{margin-bottom:0}.source-materials .notice,.source-materials .error{margin:10px 0 0}.source-hint{margin:10px 0 0}.source-list{display:grid;gap:10px;margin:12px 0 0;padding:0;list-style:none}.source-list>li{display:grid;gap:6px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.source-list>li.withdrawn{opacity:.6}.source-line{display:flex;align-items:baseline;gap:10px}.source-line strong{color:var(--foreground)}.source-line time{color:var(--muted-foreground);font-size:12px}.state-badge{padding:0 6px;border:1px solid var(--border);border-radius:999px;color:var(--muted-foreground);font-size:11px;font-weight:700}.source-content{margin:0;line-height:1.6;color:var(--color-text-secondary)}.source-actions{display:flex;justify-content:flex-end}.withdraw-confirm{display:grid;gap:6px}.withdraw-confirm>div{display:flex;gap:6px}.withdraw-confirm input{min-height:34px}.add-observation{margin-top:12px}.add-form{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.add-form label{display:grid;gap:4px;font-size:12px;font-weight:650;color:var(--foreground)}.add-form .wide{grid-column:1/-1}.add-hint{grid-column:1/-1;margin:0;font-size:12px;color:var(--muted-foreground)}.add-form footer{grid-column:1/-1;display:flex;gap:8px}.academic-intro{padding:20px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.academic-intro p{margin:0;color:var(--color-text-secondary);line-height:1.7}.academic-stats{margin-top:2px}
.dossier-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-top:1px solid var(--border);background:var(--card)}.dossier-actions>div{display:flex;gap:8px}.dossier button:focus-visible,.dossier textarea:focus-visible,.source-materials:focus-visible,.source-materials summary:focus-visible{outline:2px solid var(--ring);outline-offset:2px}
.round-badge{display:inline-block;margin-right:6px;padding:0 6px;border:1px solid var(--primary);border-radius:999px;color:var(--primary);font-size:11px;font-weight:700;vertical-align:1px}
.round-legend{margin:0;color:var(--muted-foreground);font-size:12px}
.questions-legend{padding:0 16px 12px}
@keyframes drawer-in{from{transform:translateX(24px)}to{transform:none}}
@media(max-width:700px){.dossier{width:100%;border-left:0}.folder-tab{display:none}.dimension-grid,.support-grid,.proposal__grid,.add-form{grid-template-columns:1fr}.active-plans{grid-column:auto}.dossier-actions{align-items:stretch;flex-direction:column}.dossier-actions>div{display:grid;grid-template-columns:1fr 1fr}.dossier-actions>.quiet{display:none}.quick-stats{grid-template-columns:1fr}.identity p{font-size:12px}.dossier__scroll{padding-inline:14px}}
@media(max-width:430px){.dossier__masthead{padding:14px}.identity__seal{width:42px;height:42px}.identity h1{font-size:20px}.dossier-tabs{padding-inline:8px}.dossier-tabs button{padding-inline:5px}.dossier-actions>div{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.dossier{animation:none}}
</style>
