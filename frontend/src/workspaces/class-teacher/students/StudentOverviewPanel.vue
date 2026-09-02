<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type Directive } from 'vue'

import { intakeApi, type HandoffDraft, type IntakeConversation, type IntakeHandoffSummary } from '../api/intake'
import { supportApi, type SupportRecord } from '../api/support'
import { autoAdoptStudentRecordHandoffs } from '../intake/auto-adopt'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'
import { ApiError } from '@/api/errors'
import {
  studentR1Api,
  studentRefOf,
  type AcademicSummarySubject,
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

/* —— 概览页：维度折叠、置顶维度、条目截断 —— */
// 「当前需要支持」「已验证有效的方法」是班主任最常看的两类信息，置顶并默认展开；
// 渐进披露是给复杂度排序，不是把重要信息折叠起来。
const PINNED_DIMENSIONS = ['support_needs', 'effective_methods']
const DIMENSION_CAP = 5
const openDims = reactive(new Set<string>(PINNED_DIMENSIONS))
const showAllDims = reactive(new Set<string>())
const clampedItemIds = reactive(new Set<string>())
const openedItemIds = reactive(new Set<string>())
const summaryExpanded = ref(false)
const summaryOverflows = ref(false)
const summaryTextEl = ref<HTMLElement | null>(null)

const orderedDimensions = computed(() => {
  const order = (key: string) => {
    const index = PINNED_DIMENSIONS.indexOf(key)
    return index === -1 ? 99 : index
  }
  return [...profile.value.dimensions].sort((a, b) => order(a.key) - order(b.key))
})
const allDimsOpen = computed(() => orderedDimensions.value.every((dimension) => openDims.has(dimension.key)))
function isPinnedDim(key: string): boolean {
  return PINNED_DIMENSIONS.includes(key)
}
function hasRoundNewItem(dimension: StudentProfileDimension): boolean {
  return dimension.items.some((item) => isRoundNewItem(dimension.key, item))
}
function dimPreviewText(dimension: StudentProfileDimension): string {
  return dimension.items.find((item) => isRoundNewItem(dimension.key, item)) ?? dimension.items[0] ?? ''
}
function toggleDim(key: string): void {
  if (openDims.has(key)) openDims.delete(key)
  else openDims.add(key)
}
function foldAllDims(): void {
  if (allDimsOpen.value) openDims.clear()
  else orderedDimensions.value.forEach((dimension) => openDims.add(dimension.key))
}
function toggleShowAllDim(key: string): void {
  if (showAllDims.has(key)) showAllDims.delete(key)
  else showAllDims.add(key)
}
function dimItemId(dimensionKey: string, item: string): string {
  return `${dimensionKey}::${item}`
}
function toggleItemClamp(id: string): void {
  if (openedItemIds.has(id)) openedItemIds.delete(id)
  else openedItemIds.add(id)
}
// 条目默认两行截断，只有真实溢出的条目才显示「展开」。
const vMeasureClamp: Directive<HTMLElement, string> = {
  mounted(el, binding) { measureClamp(el, binding.value) },
  updated(el, binding) { measureClamp(el, binding.value) },
}
function measureClamp(el: HTMLElement, id: string): void {
  if (openedItemIds.has(id)) return
  if (el.scrollHeight > el.clientHeight + 2) clampedItemIds.add(id)
  else clampedItemIds.delete(id)
}
function measureSummaryOverflow(): void {
  const el = summaryTextEl.value
  summaryOverflows.value = Boolean(el && !summaryExpanded.value && el.scrollHeight > el.clientHeight + 2)
}

/* —— 本轮更新聚合条：替代散落在各条目里的小「新」徽章导航 —— */
const dimensionLabelOf = computed(() => {
  const map = new Map<string, string>()
  for (const dimension of profile.value.dimensions) map.set(dimension.key, dimension.label)
  return map
})
const roundLabel = computed(() => {
  const adoptedAt = profile.value.latest_round?.adopted_at
  return adoptedAt ? `${updatedLabel(adoptedAt)} 并入 1 轮更新` : ''
})
const roundChips = computed(() => {
  const changed = roundChanges.value
  if (!changed) return []
  const chips: { key: string; label: string; go: () => void }[] = []
  for (const [dimensionKey, items] of Object.entries(changed.dimensions)) {
    if (!items.length) continue
    chips.push({
      key: `dim-${dimensionKey}`,
      label: `${dimensionLabelOf.value.get(dimensionKey) ?? dimensionKey} +${items.length}`,
      go: () => goToDimension(dimensionKey),
    })
  }
  if (changed.open_questions.length) {
    chips.push({ key: 'questions', label: `仍需了解 +${changed.open_questions.length}`, go: () => selectTab('support') })
  }
  if (changed.support_focus.length) {
    chips.push({ key: 'focus', label: `支持重点 +${changed.support_focus.length}`, go: () => selectTab('support') })
  }
  return chips
})
async function goToDimension(key: string): Promise<void> {
  selectTab('overview')
  openDims.add(key)
  await nextTick()
  dialog.value?.querySelector(`[data-dim-key="${key}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/* —— 学业摘要卡：后端 academic_ai_summary_v1 的只读透传，无成绩时为 null —— */
const academic = computed(() => card.value?.academic_summary ?? null)
const academicTrendLabels: Record<string, string> = { improving: '持续进步', declining: '持续退步', fluctuating: '起伏', flat: '持平', insufficient: '场次不足' }
const academicStabilityLabels: Record<string, string> = { stable: '稳定', moderate: '有波动', volatile: '波动大', insufficient: '场次不足' }
const academicSkewLabels: Record<string, string> = { balanced: '总体均衡', skewed: '明显偏科', insufficient: '场次不足' }
const academicSkewText = computed(() => {
  const skew = academic.value?.skew
  if (!skew) return ''
  if (skew.label === 'skewed' && (skew.strongest.length || skew.weakest.length)) {
    return `明显偏科（强 ${skew.strongest.join('、')} / 弱 ${skew.weakest.join('、')}）`
  }
  return academicSkewLabels[skew.label] ?? skew.label
})
function formatTopRatio(ratio: number): string {
  const percent = ratio * 100
  return percent >= 10 ? `${Math.round(percent)}%` : `${Math.round(percent * 10) / 10}%`
}
function rankDeltaText(delta: number | null): string {
  if (delta == null) return '—'
  if (delta > 0) return `↑${delta} 名`
  if (delta < 0) return `↓${-delta} 名`
  return '持平'
}
function rankDeltaClass(delta: number | null): string {
  if (delta == null || delta === 0) return 'delta-flat'
  return delta > 0 ? 'delta-up' : 'delta-down'
}
interface AcademicSnapshotView {
  termLabel: string
  title: string
  occurredOn: string
  score: number | null
  rank: number
  count: number | null
  topRatio: string | null
  delta: number | null
  previousTitle: string | null
  previousRank: number | null
  trend: string
  stability: string
  skew: string
  subjects: AcademicSummarySubject[]
  notes: string[]
}
const snapshot = computed<AcademicSnapshotView | null>(() => {
  const summary = academic.value
  const total = summary?.total
  const exam = summary?.latest_exam
  if (!summary || !total || total.rank == null || !exam) return null
  return {
    termLabel: exam.term_label || exam.title,
    title: exam.title,
    occurredOn: exam.occurred_on,
    score: total.score,
    rank: total.rank,
    count: total.participant_count,
    topRatio: total.top_ratio != null ? formatTopRatio(total.top_ratio) : null,
    delta: summary.rank_change_vs_previous?.delta ?? null,
    previousTitle: summary.rank_change_vs_previous?.previous_title ?? null,
    previousRank: summary.rank_change_vs_previous?.previous_rank ?? null,
    trend: academicTrendLabels[summary.trend] ?? summary.trend,
    stability: academicStabilityLabels[summary.stability] ?? summary.stability,
    skew: academicSkewText.value,
    subjects: summary.subjects,
    notes: summary.notes,
  }
})
// 概览页的一行学业定位：少量关键事实 + 深链，不堆放成绩图表。
const academicStrip = computed(() => {
  const snap = snapshot.value
  if (!snap) return null
  const attention = snap.subjects.find((item) => item.attention)
  return { ...snap, attentionName: attention?.name ?? null }
})

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
    void nextTick(measureSummaryOverflow)
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
  // 三个页签内容高度差异大，切换时回到顶部，避免落在另一页签的滚动位置上。
  const scroller = dialog.value?.querySelector('.dossier__scroll')
  if (scroller) scroller.scrollTop = 0
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
    void nextTick(measureSummaryOverflow)
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
    notice.value = ''
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
        ? ''
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
    openDims.clear()
    for (const key of PINNED_DIMENSIONS) openDims.add(key)
    showAllDims.clear()
    clampedItemIds.clear()
    openedItemIds.clear()
    summaryExpanded.value = false
    summaryOverflows.value = false
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

      <section v-if="roundChips.length" class="round-strip" aria-label="本轮档案更新">
        <span class="round-strip__label">{{ roundLabel }}</span>
        <button
          v-for="chip in roundChips"
          :key="chip.key"
          type="button"
          class="round-strip__chip"
          @click="chip.go()"
        >{{ chip.label }}</button>
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
          <template v-if="hasProfile">
            <div class="summary-card">
              <div class="summary-card__head"><span class="smallcaps">此刻对这名学生的认识</span><small>{{ profile.summary.length }} 字</small></div>
              <p ref="summaryTextEl" class="summary-text" :class="{ clamp: !summaryExpanded }"><span v-if="roundChanges?.summary_changed" class="round-badge">新</span>{{ profile.summary }}</p>
              <button v-if="summaryOverflows || summaryExpanded" type="button" class="summary-toggle" @click="summaryExpanded = !summaryExpanded">{{ summaryExpanded ? '收起' : '展开全文' }}</button>
            </div>
            <div class="stat-line">
              <span><b>{{ profile.dimensions.length }}</b> 个有效维度</span><span>·</span>
              <span><b>{{ subject.confirmed_entry_count }}</b> 条已确认记录</span><span>·</span>
              <span>最近完善 <b>{{ updatedLabel(profile.updated_at) }}</b></span>
            </div>
            <button v-if="academicStrip" type="button" class="aca-strip" @click="selectTab('academic')">
              <span class="aca-strip__k">学业定位</span>
              <b>{{ academicStrip.termLabel }} · 第 {{ academicStrip.rank }} 名</b>
              <span v-if="academicStrip.count">/ {{ academicStrip.count }} 人<template v-if="academicStrip.topRatio"> · 前 {{ academicStrip.topRatio }}</template></span>
              <span v-if="academicStrip.delta" :class="academicStrip.delta > 0 ? 'aca-strip__up' : 'aca-strip__down'">{{ academicStrip.delta > 0 ? `↑${academicStrip.delta}` : `↓${-academicStrip.delta}` }}</span>
              <span v-if="academicStrip.attentionName" class="aca-strip__warn">{{ academicStrip.attentionName }}需关注</span>
              <span class="aca-strip__go">学业证据 →</span>
            </button>
            <div v-if="profile.support_focus.length || profile.open_questions.length" class="priority-grid">
              <div v-if="profile.support_focus.length" class="priority priority--warn">
                <small>当前支持重点</small>
                <strong>{{ profile.support_focus[0]?.title }}</strong>
                <p>{{ profile.support_focus[0]?.need }}</p>
                <button type="button" class="priority__go" @click="selectTab('support')">{{ profile.support_focus[0]?.effective_methods.length }} 个已验证方法 · {{ profile.support_focus[0]?.next_actions.length }} 个下一步 →</button>
              </div>
              <div v-if="profile.open_questions.length" class="priority priority--info">
                <small>仍需了解 · {{ profile.open_questions.length }} 个</small>
                <p>{{ profile.open_questions[0] }}</p>
                <button v-if="profile.open_questions.length > 1" type="button" class="priority__go" @click="selectTab('support')">其余 {{ profile.open_questions.length - 1 }} 条 →</button>
              </div>
            </div>
            <header class="dim-head">
              <h2>当前结构化档案</h2><span class="hint">「需要支持 · 有效方法」已置顶展开</span>
              <button type="button" class="dim-head__fold" @click="foldAllDims">{{ allDimsOpen ? '全部收起' : '全部展开' }}</button>
            </header>
            <div class="dim-list">
              <article
                v-for="dimension in orderedDimensions"
                :key="dimension.key"
                class="dim"
                :class="{ open: openDims.has(dimension.key) }"
                :data-dim-key="dimension.key"
              >
                <button type="button" class="dim-row" :aria-expanded="openDims.has(dimension.key)" @click="toggleDim(dimension.key)">
                  <span class="dim-row__name">{{ dimension.label }}</span>
                  <span class="dim-row__count">{{ dimension.items.length }} 条</span>
                  <span v-if="isPinnedDim(dimension.key)" class="dim-row__pin">置顶</span>
                  <span v-if="hasRoundNewItem(dimension)" class="round-badge">新</span>
                  <span class="dim-row__prev">{{ dimPreviewText(dimension) }}</span>
                  <span class="dim-row__chev" aria-hidden="true">▶</span>
                </button>
                <div class="dim-body">
                  <ul class="dim-items">
                    <li v-for="(item, index) in dimension.items" :key="item" v-show="index < DIMENSION_CAP || showAllDims.has(dimension.key)">
                      <span class="dim-item__dot" aria-hidden="true"></span>
                      <span class="dim-item__text"><span v-if="isRoundNewItem(dimension.key, item)" class="round-badge">新</span><span
                        class="clampbox"
                        :class="{ opened: openedItemIds.has(dimItemId(dimension.key, item)) }"
                        v-measure-clamp="dimItemId(dimension.key, item)"
                      >{{ item }}</span><button
                        v-if="clampedItemIds.has(dimItemId(dimension.key, item))"
                        type="button"
                        class="it-more"
                        @click="toggleItemClamp(dimItemId(dimension.key, item))"
                      >{{ openedItemIds.has(dimItemId(dimension.key, item)) ? '收起' : '展开' }}</button></span>
                    </li>
                  </ul>
                  <button v-if="dimension.items.length > DIMENSION_CAP" type="button" class="dim-more" @click="toggleShowAllDim(dimension.key)">{{ showAllDims.has(dimension.key) ? '收起多出的条目' : `展开其余 ${dimension.items.length - DIMENSION_CAP} 条` }}</button>
                </div>
              </article>
            </div>
          </template>
          <div v-else class="empty-profile">
            <strong>{{ profileNotCreated ? '档案尚未建立' : '这份档案还没有开始生长' }}</strong>
            <p v-if="profileNotCreated">核对下方待并入的草稿，确认保存后会自动创建档案。</p>
            <p v-else>到“成长与支持”写下已经了解的情况，形成第一份结构化档案。</p>
          </div>

          <div v-if="!homeBound" class="ai-entry">
            <p>把最近了解到的情况直接告诉 AI，会自动整理进上面的档案维度。</p>
            <AppButton variant="primary" @click="continueProfile">向 AI 补充这名学生的情况</AppButton>
          </div>
          <p v-if="homeBound && notice" class="notice home-notice" role="status">{{ notice }}</p>
          <p v-if="homeBound && error" class="error home-error" role="alert">{{ error }}</p>
          <section v-if="homeBound && adoptedHandoff" class="proposal" aria-labelledby="home-adopted-title">
            <header><div><small>本轮档案更新</small><h2 id="home-adopted-title">已自动并入当前档案</h2></div><AppButton variant="secondary" :disabled="busy" @click="revertProposal">撤回这次更新</AppButton></header>
            <footer><span>原始记录仍保留。</span></footer>
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
            <header><div><small>本轮档案更新</small><h2 id="adopted-title">已自动并入当前档案</h2></div><AppButton variant="secondary" :disabled="busy" @click="revertProposal">撤回这次更新</AppButton></header>
            <footer><span>原始记录仍保留。</span></footer>
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
                <h3><span v-if="isRoundNewFocus(focus.key)" class="round-badge">新</span>{{ focus.title }}</h3><p>{{ focus.need }}</p>
                <template v-if="focus.effective_methods.length"><strong>已经有效</strong><ul><li v-for="item in focus.effective_methods" :key="item">{{ item }}</li></ul></template>
                <template v-if="focus.next_actions.length"><strong>接下来尝试</strong><ul><li v-for="item in focus.next_actions" :key="item">{{ item }}</li></ul></template>
              </article>
              <p v-if="!profile.support_focus.length" class="compact-empty">暂无明确支持重点。继续完善后，这里会显示已经有效的方法和下一步。</p>
            </section>
            <section class="support-section questions">
              <header><small>后续谈话可以留意</small><h2>仍需了解</h2></header>
              <ol v-if="profile.open_questions.length"><li v-for="item in profile.open_questions" :key="item"><span v-if="isRoundNewQuestion(item)" class="round-badge">新</span>{{ item }}</li></ol>
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
          <template v-if="snapshot">
            <p class="aca-note">学业快照由已确认成绩自动计算，口径与完整学业证据页一致</p>
            <div class="snap">
              <div class="snap__head">
                <span class="snap__term">{{ snapshot.termLabel }}</span>
                <strong>{{ snapshot.title }}</strong>
                <time>{{ snapshot.occurredOn }}</time>
              </div>
              <div class="snap__metrics">
                <div v-if="snapshot.score != null" class="snap__metric"><strong>{{ snapshot.score }}</strong><span>总分</span></div>
                <div class="snap__metric"><strong>第 {{ snapshot.rank }} 名</strong><span>校次 · 共 {{ snapshot.count }} 人<template v-if="snapshot.topRatio"> · 前 {{ snapshot.topRatio }}</template></span></div>
                <div v-if="snapshot.delta" class="snap__metric"><strong :class="rankDeltaClass(snapshot.delta)">{{ rankDeltaText(snapshot.delta) }}</strong><span>较 {{ snapshot.previousTitle }}<template v-if="snapshot.previousRank != null">（第 {{ snapshot.previousRank }} 名）</template></span></div>
              </div>
              <div class="snap__tags">
                <span class="tag">趋势：{{ snapshot.trend }}</span>
                <span class="tag">稳定性：{{ snapshot.stability }}</span>
                <span class="tag">偏科：{{ snapshot.skew }}</span>
              </div>
              <div v-if="snapshot.subjects.length" class="snap__subj">
                <h3>各科最近校次</h3>
                <table>
                  <thead><tr><th>科目</th><th>校次</th><th>较上一场</th><th></th></tr></thead>
                  <tbody>
                    <tr v-for="item in snapshot.subjects" :key="item.name">
                      <td>{{ item.name }}</td>
                      <td><template v-if="item.latest_rank != null">第 {{ item.latest_rank }} 名 <span v-if="item.participant_count" class="snap__count">/ {{ item.participant_count }}</span></template><template v-else>—</template></td>
                      <td><span :class="rankDeltaClass(item.rank_delta)">{{ rankDeltaText(item.rank_delta) }}</span></td>
                      <td><span v-if="item.attention" class="snap__attn">需要关注</span></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-if="snapshot.notes.length" class="snap__notes">{{ snapshot.notes.join(' · ') }}</p>
              <div class="snap__foot"><AppButton variant="primary" block @click="emit('open', 'academic')">打开完整学业证据（走势 · 雷达 · 历次热力表）</AppButton></div>
            </div>
            <p v-if="subject.attention_pending_count" class="aca-pending">有 {{ subject.attention_pending_count }} 个待处理关注项需要教师决定。<button type="button" @click="emit('open', 'academic')">去处理 →</button></p>
          </template>
          <div v-else-if="!academic" class="aca-empty">
            <strong>还没有大考成绩</strong>
            <p>导入成绩表后，这里会自动生成这名学生的学业快照（定位、趋势、偏科、各科校次），不需要手工填写。</p>
            <AppButton variant="primary" @click="emit('open', 'academic')">打开成绩管理导入成绩</AppButton>
          </div>
          <div v-else class="aca-empty">
            <strong>已有成绩，还缺校次数据</strong>
            <p>学业快照的定位、趋势和偏科都基于「校次（年级名次）」计算；这名学生已导入的成绩里还没有可用的校次。补齐校次后快照自动更新，不需要重新导入。</p>
            <AppButton variant="secondary" @click="emit('open', 'academic')">查看完整学业证据</AppButton>
          </div>
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
.dossier{position:absolute;inset-block:0;right:0;display:flex;flex-direction:column;width:min(620px,92vw);border:0;border-left:1px solid var(--border);outline:0;background:var(--card);box-shadow:var(--shadow-overlay);animation:drawer-in .22s ease-out}
.folder-tab{position:absolute;top:78px;left:-42px;width:42px;padding:11px 9px;border-radius:var(--radius) 0 0 var(--radius);background:var(--primary);color:var(--primary-foreground);font-size:12px;font-weight:700;letter-spacing:.12em;text-align:center;writing-mode:vertical-rl}
.dossier__masthead{display:flex;align-items:center;gap:13px;padding:16px 20px;border-bottom:1px solid var(--border);background:var(--card)}.identity{display:flex;min-width:0;align-items:center;gap:13px;flex:1}.identity__seal{display:grid;width:44px;height:44px;place-items:center;flex:0 0 auto;border-radius:var(--radius);background:var(--primary);color:var(--primary-foreground);font-size:22px;font-weight:700}.identity h1{margin:0;font-size:22px}.identity p{margin:2px 0 0;color:var(--muted-foreground);font-size:13px}.close{width:40px;height:40px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);color:var(--foreground);font:inherit;font-size:24px;cursor:pointer}.close:hover{background:var(--accent)}
.dossier-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--muted)}.dossier-tabs button{min-height:36px;border:0;border-radius:var(--radius);background:transparent;color:var(--muted-foreground);font:inherit;cursor:pointer}.dossier-tabs button:hover{color:var(--foreground)}.dossier-tabs button[aria-selected=true]{background:var(--card);color:var(--primary);font-weight:700}
.pending-banner{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border);background:var(--color-warning-subtle)}.pending-banner strong{font-size:14px}.pending-banner p{margin:4px 0 0;color:var(--color-text-secondary);font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.pending-banner__actions{display:flex;flex:0 0 auto;gap:8px}
.dossier__scroll{flex:1;min-height:0;overflow:auto;padding:16px 20px 24px}.tab-panel{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}.page-state{display:grid;place-content:center;justify-items:center;gap:8px;padding:28px;text-align:center}.page-state p{color:var(--muted-foreground)}
.empty-profile{display:grid;min-height:160px;place-content:center;padding:20px;text-align:center}.empty-profile p{color:var(--muted-foreground)}.ai-desk small,.proposal small,.support-section small{color:var(--primary);font-size:12px;font-weight:700;letter-spacing:.1em}.ai-desk h2,.proposal h2,.support-section h2{margin:2px 0 12px;font-size:18px}.support-section ul,.support-section ol{margin:0;padding-left:19px;color:var(--color-text-secondary);line-height:1.65}.ai-entry{display:grid;gap:10px;justify-items:start;padding:14px;border:1px solid var(--border);border-radius:var(--radius);background:var(--accent)}.ai-entry p{margin:0;color:var(--color-text-secondary);line-height:1.55}
.ai-desk{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.ai-desk__heading{padding:16px 18px 12px;background:var(--accent)}.ai-desk__heading p{margin:5px 0 0;color:var(--color-text-secondary);line-height:1.6}.dialogue{display:grid;gap:12px;max-height:280px;overflow:auto;padding:14px 16px 0}.teacher-quote{justify-self:end;max-width:88%;margin:0;padding:10px 13px;border-radius:var(--radius) var(--radius) 2px var(--radius);background:var(--accent);line-height:1.6}.ai-reply{padding:12px 14px;border:1px solid var(--border);border-left:3px solid var(--primary);border-radius:var(--radius);background:var(--card)}.ai-reply p,.ai-reply ul{margin:5px 0 0;line-height:1.6}.composer{margin:14px 16px 16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.composer textarea{box-sizing:border-box;width:100%;padding:12px;border:0;border-radius:var(--radius) var(--radius) 0 0;outline:0;resize:vertical;background:transparent;font:inherit;line-height:1.6}.composer:focus-within{border-color:var(--ring);box-shadow:var(--focus-ring)}.composer footer{display:flex;align-items:center;justify-content:space-between;padding:8px 10px 8px 13px;border-top:1px solid var(--border);color:var(--muted-foreground);font-size:12px}.notice,.error{margin:-6px 16px 14px;padding:9px 11px;border-radius:var(--radius)}.notice{background:var(--accent);color:var(--primary)}.error{background:var(--color-danger-subtle);color:var(--destructive)}.home-notice,.home-error{margin:0}
.proposal{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.proposal>header{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 16px;border-bottom:1px solid var(--border);background:var(--color-warning-subtle)}.proposal__summary{margin:0;padding:14px 16px;font-size:16px;line-height:1.6}.proposal__grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:0 16px 14px}.proposal__grid article{padding:10px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.proposal__grid ul{margin:7px 0 0;padding-left:18px}.proposal>footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 16px;border-top:1px solid var(--border);color:var(--color-text-secondary);font-size:12px}
.support-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.support-section{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.support-section>header{padding:12px 16px;border-bottom:1px solid var(--border);background:var(--muted)}.support-section h2{margin-bottom:0}.support-section article{margin:12px;padding:12px;border:1px solid var(--border);border-radius:var(--radius)}.support-section article h3{margin:0}.support-section article p{margin:7px 0;line-height:1.55}.support-section article strong{display:block;margin-top:10px;color:var(--muted-foreground);font-size:12px}.compact-empty{margin:0;padding:16px;color:var(--muted-foreground);line-height:1.6}.questions ol{padding:14px 34px}.active-plans{grid-column:1/-1}.source-materials{padding:14px 16px;border:1px dashed var(--border);border-radius:var(--radius);background:var(--muted);color:var(--color-text-secondary)}.source-materials summary{cursor:pointer;font-weight:700}.source-materials p{margin-bottom:0}.source-materials .notice,.source-materials .error{margin:10px 0 0}.source-hint{margin:10px 0 0}.source-list{display:grid;gap:10px;margin:12px 0 0;padding:0;list-style:none}.source-list>li{display:grid;gap:6px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.source-list>li.withdrawn{opacity:.6}.source-line{display:flex;align-items:baseline;gap:10px}.source-line strong{color:var(--foreground)}.source-line time{color:var(--muted-foreground);font-size:12px}.state-badge{padding:0 6px;border:1px solid var(--border);border-radius:999px;color:var(--muted-foreground);font-size:11px;font-weight:700}.source-content{margin:0;line-height:1.6;color:var(--color-text-secondary)}.source-actions{display:flex;justify-content:flex-end}.withdraw-confirm{display:grid;gap:6px}.withdraw-confirm>div{display:flex;gap:6px}.withdraw-confirm input{min-height:34px}.add-observation{margin-top:12px}.add-form{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.add-form label{display:grid;gap:4px;font-size:12px;font-weight:650;color:var(--foreground)}.add-form .wide{grid-column:1/-1}.add-hint{grid-column:1/-1;margin:0;font-size:12px;color:var(--muted-foreground)}.add-form footer{grid-column:1/-1;display:flex;gap:8px}
.dossier-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-top:1px solid var(--border);background:var(--card)}.dossier-actions>div{display:flex;gap:8px}.dossier button:focus-visible,.dossier textarea:focus-visible,.source-materials:focus-visible,.source-materials summary:focus-visible{outline:2px solid var(--ring);outline-offset:2px}
.round-badge{display:inline-block;margin-right:6px;padding:0 6px;border:1px solid var(--primary);border-radius:999px;color:var(--primary);font-size:11px;font-weight:700;vertical-align:1px}
.round-legend{margin:0;color:var(--muted-foreground);font-size:12px}
.questions-legend{padding:0 16px 12px}
@keyframes drawer-in{from{transform:translateX(24px)}to{transform:none}}
/* 概览页 A+：摘要截断卡 / 统计行 / 学业定位微条 / 优先预览区 */
.smallcaps{color:var(--primary);font-size:11px;font-weight:700;letter-spacing:.1em}
.summary-card{padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-panel);background:var(--card)}
.summary-card__head{display:flex;align-items:baseline;gap:8px;margin-bottom:6px}
.summary-card__head small{margin-left:auto;color:var(--muted-foreground);font-size:12px}
.summary-text{margin:0;color:var(--foreground);font-size:15px;line-height:1.7}
.summary-text.clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.summary-toggle{margin-top:4px;padding:0;border:0;background:none;color:var(--primary);font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.stat-line{display:flex;align-items:center;flex-wrap:wrap;gap:6px 14px;padding:0 2px;color:var(--muted-foreground);font-size:12px}
.stat-line b{color:var(--color-text-secondary);font-weight:600}
.aca-strip{display:flex;align-items:center;flex-wrap:wrap;gap:8px;width:100%;padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius);background:var(--color-info-subtle);color:var(--color-text-secondary);font:inherit;font-size:12.5px;text-align:left;cursor:pointer}
.aca-strip__k{color:var(--color-info);font-size:11px;font-weight:700;letter-spacing:.06em}
.aca-strip b{color:var(--foreground)}
.aca-strip__up{color:var(--color-success);font-weight:700}
.aca-strip__down{color:var(--color-danger);font-weight:700}
.aca-strip__warn{color:var(--color-warning);font-weight:600}
.aca-strip__go{margin-left:auto;color:var(--color-info);font-size:12px;font-weight:600}
.priority-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.priority{display:grid;gap:5px;align-content:start;padding:11px 13px;border-radius:var(--radius-panel)}
.priority--warn{background:var(--color-warning-subtle)}
.priority--info{background:var(--color-info-subtle)}
.priority small{font-size:11px;font-weight:700;letter-spacing:.06em}
.priority--warn small{color:var(--color-warning)}
.priority--info small{color:var(--color-info)}
.priority strong{color:var(--foreground);font-size:13.5px}
.priority p{margin:0;color:var(--color-text-secondary);font-size:12.5px;line-height:1.55;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.priority__go{justify-self:start;padding:0;border:0;background:none;color:var(--color-text-secondary);font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.priority__go:hover{color:var(--primary)}
/* 概览页 A+：维度单列分组行 */
.dim-head{display:flex;align-items:baseline;gap:10px;padding-bottom:6px;border-bottom:1px solid var(--color-border-subtle)}
.dim-head h2{margin:0;font-size:16px}
.dim-head .hint{color:var(--muted-foreground);font-size:12px}
.dim-head__fold{margin-left:auto;padding:0;border:0;background:none;color:var(--primary);font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.dim-list{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
.dim{border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.dim.open{border-color:var(--color-border-strong);box-shadow:0 1px 3px rgba(28,39,51,.06)}
.dim-row{display:flex;align-items:center;gap:9px;width:100%;min-height:46px;padding:10px 12px;border:0;background:transparent;font:inherit;color:inherit;text-align:left;cursor:pointer}
.dim-row__name{flex:0 0 auto;font-size:13.5px;font-weight:600}
.dim-row__count{flex:0 0 auto;padding:0 7px;border:1px solid var(--color-border-subtle);border-radius:999px;background:var(--muted);color:var(--color-text-secondary);font-size:11px;font-weight:700;line-height:18px}
.dim-row__pin{flex:0 0 auto;padding:0 6px;border-radius:999px;background:var(--accent);color:var(--primary);font-size:11px;font-weight:700;line-height:18px}
.dim-row__prev{flex:1;min-width:0;overflow:hidden;color:var(--muted-foreground);font-size:12.5px;text-overflow:ellipsis;white-space:nowrap}
.dim.open .dim-row__prev{display:none}
.dim-row__chev{flex:0 0 auto;color:var(--muted-foreground);font-size:11px;transition:transform .15s}
.dim.open .dim-row__chev{transform:rotate(90deg)}
.dim-body{padding:2px 12px 12px;border-top:1px dashed var(--color-border-subtle)}
.dim:not(.open) .dim-body{display:none}
.dim-items{display:grid;gap:9px;margin:8px 0 0;padding:0;list-style:none}
.dim-items li{display:flex;gap:8px;align-items:flex-start}
.dim-item__dot{flex:0 0 auto;width:6px;height:6px;margin-top:8px;border-radius:50%;background:var(--primary);opacity:.65}
.dim-item__text{flex:1;min-width:0;color:var(--foreground);font-size:13.5px;line-height:1.65}
.clampbox{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:100%}
.clampbox.opened{display:block;-webkit-line-clamp:unset}
.it-more{margin-left:6px;padding:0;border:0;background:none;color:var(--primary);font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.dim-more{margin-top:2px;padding:0;border:0;background:none;color:var(--primary);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer}
/* 本轮更新聚合条 */
.round-strip{display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:9px 16px;border-bottom:1px solid var(--color-border-subtle);background:var(--color-ai-subtle);color:var(--color-ai);font-size:12px}
.round-strip__label{font-weight:700}
.round-strip__chip{padding:2px 8px;border:1px solid var(--color-border-strong);border-radius:999px;background:var(--card);color:var(--color-ai);font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.round-strip__chip:hover{border-color:var(--color-ai)}
/* 学业证据页签：学业快照卡 + 分级空态 */
.aca-note{margin:0;color:var(--muted-foreground);font-size:12px}
.snap{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius-panel);background:var(--card)}
.snap__head{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--color-border-subtle)}
.snap__term{flex:0 0 auto;padding:1px 8px;border-radius:var(--radius);background:var(--primary);color:var(--primary-foreground);font-size:11px;font-weight:700}
.snap__head strong{font-size:15px}
.snap__head time{margin-left:auto;color:var(--muted-foreground);font-size:12px}
.snap__metrics{display:flex;flex-wrap:wrap;gap:18px;padding:12px 14px 6px}
.snap__metric{display:grid;gap:2px}
.snap__metric strong{color:var(--primary);font-size:20px}
.snap__metric strong.delta-up{color:var(--color-success)}
.snap__metric strong.delta-down{color:var(--color-danger)}
.snap__metric span{color:var(--muted-foreground);font-size:12px}
.snap__tags{display:flex;flex-wrap:wrap;gap:6px;padding:6px 14px 12px;border-bottom:1px dashed var(--color-border-subtle)}
.snap__tags .tag{padding:3px 10px;border:1px solid var(--border);border-radius:999px;background:var(--card);color:var(--color-text-secondary);font-size:12px}
.snap__subj{padding:6px 14px 8px}
.snap__subj h3{margin:6px 0 4px;color:var(--color-text-secondary);font-size:13px}
.snap__subj table{width:100%;border-collapse:collapse;font-size:12.5px}
.snap__subj th{padding:5px 4px;border-bottom:1px solid var(--color-border-subtle);color:var(--muted-foreground);font-size:11.5px;font-weight:600;text-align:left}
.snap__subj td{padding:5px 4px;border-bottom:1px solid var(--color-border-subtle)}
.snap__subj tr:last-child td{border-bottom:0}
.snap__count{color:var(--muted-foreground)}
.delta-up{color:var(--color-success);font-weight:700}
.delta-down{color:var(--color-danger);font-weight:700}
.delta-flat{color:var(--muted-foreground)}
.snap__attn{padding:0 6px;border-radius:999px;background:var(--color-warning-subtle);color:var(--color-warning);font-size:11px;font-weight:700;white-space:nowrap}
.snap__notes{margin:0;padding:2px 14px 10px;color:var(--muted-foreground);font-size:11.5px}
.snap__foot{padding:0 14px 14px}
.aca-empty{display:grid;gap:8px;justify-items:start;padding:18px;border:1px dashed var(--color-border-strong);border-radius:var(--radius-panel);background:var(--muted)}
.aca-empty strong{font-size:15px}
.aca-empty p{margin:0;color:var(--color-text-secondary);font-size:13px;line-height:1.6}
.aca-pending{margin:0;color:var(--color-warning);font-size:12.5px}
.aca-pending button{padding:0;border:0;background:none;color:var(--primary);font:inherit;font-weight:600;cursor:pointer}
@media(max-width:700px){.dossier{width:100%;border-left:0}.folder-tab{display:none}.support-grid,.proposal__grid,.add-form,.priority-grid{grid-template-columns:1fr}.active-plans{grid-column:auto}.dossier-actions{align-items:stretch;flex-direction:column}.dossier-actions>div{display:grid;grid-template-columns:1fr 1fr}.dossier-actions>.quiet{display:none}.identity p{font-size:12px}.dossier__scroll{padding-inline:14px}}
@media(max-width:430px){.dossier__masthead{padding:14px}.identity__seal{width:42px;height:42px}.identity h1{font-size:20px}.dossier-tabs{padding-inline:8px}.dossier-tabs button{padding-inline:5px}.dossier-actions>div{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.dossier{animation:none}}
</style>
