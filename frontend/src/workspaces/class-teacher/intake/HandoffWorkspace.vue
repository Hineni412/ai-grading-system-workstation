<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getActivePinia } from 'pinia'

import { intakeApi, type HandoffDraft } from '../api/intake'
import { studentR1Api, type ExistingRosterStudent } from '../api/r1'
import { workspaceAITaskApi } from '../../shared/ai-tasks/api'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'

const props = defineProps<{ handoffId: string }>()
const emit = defineEmits<{ back: [conversationId: string, workItemId: string]; completed: [conversationId: string] }>()

const draft = ref<HandoffDraft | null>(null)
const content = ref<Record<string, unknown>>({})
const students = ref<ExistingRosterStudent[]>([])
const selectedSubjectId = ref('')
const selectedSubjectRevision = ref('')
const revisionInstruction = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
let revisionTimer: number | null = null
let revisionGeneration = 0

type PlanActionDraft = {
  draft_action_id: string
  title: string
  details: string
  due_at: string
  depends_on_draft_action_ids: string[]
  [key: string]: unknown
}

type SopStepDraft = {
  key: string
  title: string
  details: string
  safety_required: boolean
}

type SopStudentProfileDraft = {
  display_name: string
  class_label: string
  profile: {
    summary?: string
    dimensions?: Array<{ key?: string; label?: string; items?: string[] }>
    open_questions?: string[]
    support_focus?: Array<{ key?: string; title?: string; need?: string; next_actions?: string[] }>
  }
}

type SopProfileUpdateDraft = {
  subject_ref: { kind: 'student'; id: string; revision: string }
  include: boolean
  display_name: string
  record_kind: string
  source: string
  basis: string
  observed_at: string
  review_at: string
  expires_at: string
  record_summary: string
  profile_base_revision: number | null
  profile_update: {
    summary: string
    dimensions: Array<{ key?: string; label?: string; items?: string[] }>
    open_questions: string[]
    support_focus: Array<{ key?: string; title?: string; need?: string; effective_methods?: string[]; next_actions?: string[] }>
  }
  [key: string]: unknown
}

const planActions = ref<PlanActionDraft[]>([])
const sopProfileUpdates = ref<SopProfileUpdateDraft[]>([])
let planActionSequence = 0
const sopTemplates = [
  ['baseline.student_conflict', '普通学生矛盾'],
  ['baseline.suspected_bullying', '疑似欺凌核查与学校交接'],
  ['baseline.student_injury', '学生伤害与紧急安全'],
  ['baseline.family_communication', '家校沟通'],
  ['baseline.care_conversation', '日常关怀谈话与跟进'],
  ['baseline.school_activity', '学校活动'],
] as const

const modeTitle = computed(() => ({ record: '登记草稿', plan_calendar: '计划／日历草稿', sop: 'SOP 处理草稿' }[draft.value?.handling_mode ?? 'record']))
const isStudentRecord = computed(() => draft.value?.destination_key === 'class_teacher.student.record')
const summary = computed({ get: () => String(content.value.summary ?? content.value.content ?? ''), set: (value) => { content.value.summary = value } })
const scene = computed({ get: () => String(content.value.scene ?? ''), set: (value) => { content.value.scene = value } })
const source = computed({ get: () => String(content.value.source ?? ''), set: (value) => { content.value.source = value } })
const basis = computed({ get: () => String(content.value.basis ?? ''), set: (value) => { content.value.basis = value } })
const observedAt = computed({ get: () => String(content.value.observed_at ?? '').slice(0, 16), set: (value) => { content.value.observed_at = value } })
const professionalObservedDate = computed({ get: () => String(content.value.observed_at ?? '').slice(0, 10), set: (value) => { content.value.observed_at = value } })
const recordKind = computed({ get: () => String(content.value.record_kind ?? ''), set: (value) => { content.value.record_kind = value } })
const reviewAt = computed({ get: () => String(content.value.review_at ?? '').slice(0, 16), set: (value) => { content.value.review_at = value } })
const expiresAt = computed({ get: () => String(content.value.expires_at ?? '').slice(0, 16), set: (value) => { content.value.expires_at = value } })
const expiringRecord = computed(() => ['teacher_observation', 'provisional_judgment'].includes(recordKind.value))
const factText = computed({ get: () => String(content.value.fact ?? ''), set: (value) => { content.value.fact = value } })
const reportedText = computed({ get: () => String(content.value.reported ?? ''), set: (value) => { content.value.reported = value } })
const judgmentText = computed({ get: () => String(content.value.judgment ?? ''), set: (value) => { content.value.judgment = value } })
const currentSchoolSupport = computed({ get: () => String(content.value.current_school_support ?? ''), set: (value) => { content.value.current_school_support = value } })
const professionalRecommendations = computed({ get: () => String(content.value.professional_recommendations ?? ''), set: (value) => { content.value.professional_recommendations = value } })
const avoidances = computed({ get: () => String(content.value.avoidances ?? ''), set: (value) => { content.value.avoidances = value } })
const recordProfileUpdate = computed<Record<string, unknown>>(() => (
  content.value.profile_update && typeof content.value.profile_update === 'object' && !Array.isArray(content.value.profile_update)
    ? content.value.profile_update as Record<string, unknown>
    : {}
))
const recordProfileSummary = computed({
  get: () => String(recordProfileUpdate.value.summary ?? ''),
  set: (value) => { content.value.profile_update = { ...recordProfileUpdate.value, summary: value } },
})
const recordProfileQuestions = computed({
  get: () => Array.isArray(recordProfileUpdate.value.open_questions)
    ? recordProfileUpdate.value.open_questions.map(String).join('\n')
    : '',
  set: (value) => {
    content.value.profile_update = {
      ...recordProfileUpdate.value,
      open_questions: value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
    }
  },
})
const planTitle = computed({ get: () => String(content.value.plan_title ?? content.value.summary ?? ''), set: (value) => { content.value.plan_title = value } })
const deadline = computed({ get: () => String(content.value.final_deadline ?? '').slice(0, 16), set: (value) => { content.value.final_deadline = value } })
const participantRefs = computed({ get: () => (content.value.participant_refs as string[] | undefined)?.join('\n') ?? '', set: (value) => { content.value.participant_refs = value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) } })
const sopTemplateKey = computed({ get: () => String(content.value.template_key ?? ''), set: (value) => { content.value.template_key = value } })
const sopSteps = computed<SopStepDraft[]>(() => {
  if (!Array.isArray(content.value.steps)) return []
  return content.value.steps
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item, index) => ({
      key: String(item.key ?? `step-${index + 1}`),
      title: String(item.title ?? '').trim(),
      details: String(item.details ?? '').trim(),
      safety_required: Boolean(item.safety_required),
    }))
    .filter((item) => item.title)
})
const sopStudentProfiles = computed<SopStudentProfileDraft[]>(() => {
  if (!Array.isArray(content.value.student_profiles)) return []
  return content.value.student_profiles
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      display_name: String(item.display_name ?? '').trim(),
      class_label: String(item.class_label ?? '').trim(),
      profile: item.profile && typeof item.profile === 'object' && !Array.isArray(item.profile)
        ? item.profile as SopStudentProfileDraft['profile']
        : {},
    }))
    .filter((item) => item.display_name)
})
const sopQuestions = computed(() => Array.isArray(content.value.to_verify)
  ? content.value.to_verify.map(String).map((item) => item.trim()).filter(Boolean)
  : [])
const selectedSopProfileUpdates = computed(() => sopProfileUpdates.value.filter((item) => item.include))

function cloneContent(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
}

function allocatePlanActionId(): string {
  const used = new Set(planActions.value.map((item) => item.draft_action_id))
  let candidate = ''
  do candidate = `action-${++planActionSequence}`
  while (used.has(candidate))
  return candidate
}

function hydratePlanActions(): void {
  const actions = Array.isArray(content.value.actions) ? content.value.actions as Array<Record<string, unknown>> : []
  planActionSequence = actions.length
  const used = new Set<string>()
  planActions.value = actions.map((item, index) => {
    let actionId = String(item.draft_action_id ?? '').trim()
    if (!actionId || used.has(actionId)) actionId = `action-${index + 1}`
    while (used.has(actionId)) actionId = `action-${++planActionSequence}`
    used.add(actionId)
    return {
      ...cloneContent(item),
      draft_action_id: actionId,
      title: String(item.title ?? ''),
      details: String(item.details ?? ''),
      due_at: String(item.due_at ?? '').slice(0, 16),
      depends_on_draft_action_ids: Array.isArray(item.depends_on_draft_action_ids)
        ? item.depends_on_draft_action_ids.map(String).filter(Boolean)
        : [],
    }
  })
}

function serializedPlanActions(): Array<Record<string, unknown>> {
  const validIds = new Set(planActions.value.map((item) => item.draft_action_id))
  return planActions.value.map((item) => ({
    ...cloneContent(item),
    draft_action_id: item.draft_action_id,
    title: item.title.trim(),
    details: item.details.trim(),
    due_at: item.due_at,
    depends_on_draft_action_ids: item.depends_on_draft_action_ids.filter(
      (dependency) => dependency !== item.draft_action_id && validIds.has(dependency),
    ),
  }))
}

function hydrateSopProfileUpdates(): void {
  const updates = Array.isArray(content.value.student_profile_updates)
    ? content.value.student_profile_updates as Array<Record<string, unknown>>
    : []
  sopProfileUpdates.value = updates
    .filter((item) => item && typeof item === 'object' && !Array.isArray(item))
    .map((item, index) => {
      const rawRef = item.subject_ref && typeof item.subject_ref === 'object' && !Array.isArray(item.subject_ref)
        ? item.subject_ref as Record<string, unknown>
        : {}
      const rawProfile = item.profile_update && typeof item.profile_update === 'object' && !Array.isArray(item.profile_update)
        ? item.profile_update as Record<string, unknown>
        : {}
      return {
        ...cloneContent(item),
        subject_ref: {
          kind: 'student' as const,
          id: String(rawRef.id ?? ''),
          revision: String(rawRef.revision ?? ''),
        },
        include: item.include !== false,
        display_name: String(item.display_name ?? `学生 ${index + 1}`),
        record_kind: String(item.record_kind ?? ''),
        source: String(item.source ?? ''),
        basis: String(item.basis ?? ''),
        observed_at: String(item.observed_at ?? '').slice(0, 16),
        review_at: String(item.review_at ?? '').slice(0, 16),
        expires_at: String(item.expires_at ?? '').slice(0, 16),
        record_summary: String(item.record_summary ?? ''),
        profile_base_revision: typeof item.profile_base_revision === 'number'
          ? item.profile_base_revision
          : null,
        profile_update: {
          summary: String(rawProfile.summary ?? ''),
          dimensions: Array.isArray(rawProfile.dimensions)
            ? rawProfile.dimensions as SopProfileUpdateDraft['profile_update']['dimensions']
            : [],
          open_questions: Array.isArray(rawProfile.open_questions)
            ? rawProfile.open_questions.map(String)
            : [],
          support_focus: Array.isArray(rawProfile.support_focus)
            ? rawProfile.support_focus as SopProfileUpdateDraft['profile_update']['support_focus']
            : [],
        },
      }
    })
}

function serializedSopProfileUpdates(): Array<Record<string, unknown>> {
  return sopProfileUpdates.value.map((item) => ({
    ...cloneContent(item),
    include: item.include,
    display_name: item.display_name.trim(),
    record_kind: item.record_kind,
    source: item.source.trim(),
    basis: item.basis.trim(),
    observed_at: item.observed_at,
    review_at: item.review_at,
    expires_at: item.expires_at,
    record_summary: item.record_summary.trim(),
    profile_update: cloneContent(item.profile_update),
  }))
}

function subjectRefsForSave(current: HandoffDraft): HandoffDraft['subject_refs'] {
  if (isStudentRecord.value) {
    return selectedSubjectId.value
      ? [{ kind: 'student', id: selectedSubjectId.value, revision: selectedSubjectRevision.value }]
      : current.subject_refs
  }
  if (current.handling_mode !== 'sop') return current.subject_refs
  const refs = new Map(current.subject_refs.map((item) => [item.id, item]))
  for (const update of sopProfileUpdates.value) {
    if (update.subject_ref.id) refs.set(update.subject_ref.id, update.subject_ref)
  }
  return [...refs.values()]
}

function addPlanAction(): void {
  planActions.value.push({
    draft_action_id: allocatePlanActionId(),
    title: '',
    details: '',
    due_at: '',
    depends_on_draft_action_ids: [],
  })
}

function removePlanAction(actionId: string): void {
  planActions.value = planActions.value
    .filter((item) => item.draft_action_id !== actionId)
    .map((item) => ({
      ...item,
      depends_on_draft_action_ids: item.depends_on_draft_action_ids.filter((dependency) => dependency !== actionId),
    }))
}

function stopRevisionPolling(): void {
  revisionGeneration += 1
  if (revisionTimer !== null) window.clearTimeout(revisionTimer)
  revisionTimer = null
}

function pollRevision(requestId: string): void {
  stopRevisionPolling()
  const generation = revisionGeneration
  const poll = async () => {
    if (generation !== revisionGeneration) return
    try {
      const snapshot = await intakeApi.draftRevision(requestId)
      if (generation !== revisionGeneration) return
      if (['preparing', 'queued', 'running'].includes(snapshot.task_state)) {
        revisionTimer = window.setTimeout(poll, 1500)
        return
      }
      revisionTimer = null
      if (snapshot.task_state === 'response_persisted') {
        await load()
        message.value = 'AI 调整已形成新的草稿版本，仍需你核对和确认。'
      } else if (snapshot.task_state === 'result_unknown') {
        error.value = '调整请求可能已经发出，但本机没有可靠结果；系统不会自动重发。'
      } else {
        error.value = 'AI 调整没有形成新草稿。原草稿仍保留，系统不会自动重发。'
      }
    } catch {
      revisionTimer = window.setTimeout(poll, 2500)
    }
  }
  revisionTimer = window.setTimeout(poll, 800)
}

async function load(): Promise<void> {
  busy.value = true; error.value = ''
  try {
    const loaded = await intakeApi.handoff(props.handoffId)
    draft.value = loaded
    content.value = cloneContent(loaded.content)
    if (loaded.handling_mode === 'record' && !content.value.fact && !content.value.reported && !content.value.judgment) {
      content.value.fact = String(content.value.summary ?? content.value.content ?? '')
    }
    selectedSubjectId.value = loaded.subject_refs[0]?.id ?? ''
    selectedSubjectRevision.value = loaded.subject_refs[0]?.revision ?? ''
    hydratePlanActions()
    hydrateSopProfileUpdates()
    if (isStudentRecord.value) {
      const preference = await intakeApi.homeroom()
      const result = await studentR1Api.rosterSource({ classLabel: preference.homeroom_class ?? undefined, pageSize: 100 })
      students.value = result.items
    }
  } catch { error.value = '草稿暂时无法打开。可以返回原会话后重试。' }
  finally { busy.value = false }
}

async function chooseSubject(): Promise<void> {
  if (!selectedSubjectId.value) { selectedSubjectRevision.value = ''; return }
  selectedSubjectRevision.value = students.value.find((item) => item.opaque_ref === selectedSubjectId.value)?.student_revision ?? ''
}

async function save(): Promise<HandoffDraft | null> {
  if (!draft.value) return null
  busy.value = true; error.value = ''; message.value = ''
  try {
    if (draft.value.handling_mode === 'plan_calendar') content.value.actions = serializedPlanActions()
    if (draft.value.handling_mode === 'sop') content.value.student_profile_updates = serializedSopProfileUpdates()
    if (draft.value.handling_mode === 'record') {
      const sections = [
        factText.value.trim() ? `直接事实：${factText.value.trim()}` : '',
        reportedText.value.trim() ? `他人转述：${reportedText.value.trim()}` : '',
        judgmentText.value.trim() ? `教师判断：${judgmentText.value.trim()}` : '',
      ].filter(Boolean)
      content.value.summary = sections.join('\n')
    }
    const refs = subjectRefsForSave(draft.value)
    const updated = await intakeApi.updateDraft(draft.value, cloneContent(content.value), refs)
    draft.value = updated
    content.value = cloneContent(updated.content)
    if (updated.handling_mode === 'plan_calendar') hydratePlanActions()
    message.value = '草稿已保存，尚未进入正式记录。'
    return draft.value
  } catch { error.value = '草稿没有保存；可能已在其他页面更新，请刷新后核对。'; return null }
  finally { busy.value = false }
}

async function adopt(): Promise<void> {
  if (draft.value?.handling_mode === 'plan_calendar' && (
    !planTitle.value.trim()
    || !deadline.value
    || !planActions.value.length
    || planActions.value.some((item) => !item.title.trim() || !item.due_at)
  )) {
    error.value = '加入正式日历前，请填写计划目标、最终截止时间，并逐项补全行动名称和截止时间。'
    return
  }
  if (draft.value?.handling_mode === 'sop' && !sopTemplateKey.value) {
    error.value = '请先选择与实际情况相符的学校流程模板。'
    return
  }
  if (draft.value?.handling_mode === 'record' && (!recordKind.value || !source.value.trim())) {
    error.value = '请明确选择记录性质并填写信息来源。'
    return
  }
  if (draft.value?.handling_mode === 'record' && recordKind.value === 'professional_conclusion' && (
    !basis.value.trim()
    || !professionalObservedDate.value
    || (isStudentRecord.value && (!recordProfileSummary.value.trim() || !currentSchoolSupport.value.trim()))
  )) {
    error.value = '专业结论需核对结论日期、书面依据、当前在校支持和拟更新的学生档案摘要。'
    return
  }
  if (draft.value?.handling_mode === 'sop' && selectedSopProfileUpdates.value.some((item) => (
    !item.record_kind
    || !item.source.trim()
    || !item.observed_at
    || !item.record_summary.trim()
    || !item.profile_update.summary.trim()
    || (item.record_kind === 'professional_conclusion' && !item.basis.trim())
    || (['teacher_observation', 'provisional_judgment'].includes(item.record_kind) && (!item.review_at || !item.expires_at))
  ))) {
    error.value = '请逐名核对记录性质、来源、发生时间、事件摘要和档案摘要；观察或阶段性判断还需复查与失效时间。'
    return
  }
  const current = await save()
  if (!current) return
  if (isStudentRecord.value && (!selectedSubjectId.value || !selectedSubjectRevision.value)) {
    error.value = '保存学生记录前，请选择一名学生。'; return
  }
  busy.value = true; error.value = ''
  try {
    const targetRevision = isStudentRecord.value ? selectedSubjectRevision.value : 'new'
    await intakeApi.adopt(current, targetRevision)
    message.value = '已按教师确认保存为正式内容。'
    emit('completed', current.conversation_id)
  } catch {
    await load()
    error.value = '正式保存没有完成。现有正式数据不会重复创建；若对象已变化，请重新选择后再确认。'
  }
  finally { busy.value = false }
}

async function requestRevision(): Promise<void> {
  if (!draft.value || !revisionInstruction.value.trim() || busy.value) return
  const current = await save()
  if (!current) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const snapshot = await intakeApi.requestDraftRevision(current, revisionInstruction.value.trim())
    if (snapshot.task_id && getActivePinia()) {
      try {
        useWorkspaceAITaskStore().track(await workspaceAITaskApi.get(snapshot.task_id))
      } catch {
        // The B draft revision record remains the recovery path.
      }
    }
    revisionInstruction.value = ''
    if (['preparing', 'queued', 'running'].includes(snapshot.task_state)) {
      message.value = '已提交草稿调整；离开页面后仍可从任务入口和原会话返回。'
      pollRevision(snapshot.request_id)
    } else if (snapshot.task_state === 'failed_before_dispatch') {
      error.value = '草稿调整任务尚未发出。原草稿没有变化。'
    }
  } catch { error.value = '草稿调整没有提交。原草稿没有变化。' }
  finally { busy.value = false }
}

async function discard(): Promise<void> {
  if (!draft.value || busy.value) return
  busy.value = true
  try { await intakeApi.discard(draft.value.handoff_id); emit('back', draft.value.conversation_id, draft.value.work_item_id) }
  catch { error.value = '草稿没有丢弃。若已经正式保存，请返回查看正式内容。' }
  finally { busy.value = false }
}

watch(() => props.handoffId, () => { void load() })
onBeforeUnmount(stopRevisionPolling)
onMounted(() => { void load() })
</script>

<template>
  <section v-if="draft" class="handoff-page" :data-mode="draft.handling_mode">
    <header class="handoff-page__header">
      <div><p>{{ draft.domain }} · {{ draft.intent }}</p><h1>{{ modeTitle }}</h1><span>AI 建议，待教师判断；打开和修改都不会自动正式保存。</span></div>
      <button type="button" @click="emit('back', draft.conversation_id, draft.work_item_id)">返回这次对话</button>
    </header>
    <p v-if="draft.adoption_state === 'stale'" class="error" role="alert">目标资料已在交接后变化。草稿仍保留；请重新选择学生并核对最新资料后再保存。</p><p v-if="message" class="status" role="status">{{ message }}</p><p v-if="error" class="error" role="alert">{{ error }}</p>

    <div v-if="draft.handling_mode === 'record'" class="record-layout">
      <aside><h2>对象与来源</h2><label v-if="isStudentRecord"><span>学生</span><select v-model="selectedSubjectId" @change="chooseSubject"><option value="">请选择学生</option><option v-for="student in students" :key="student.opaque_ref" :value="student.opaque_ref">{{ student.display_name }} · {{ student.class_label || '未分班' }}</option></select></label><p v-else>这是一项一般事务登记，不会写入学生档案。</p><label><span>记录性质</span><select v-model="recordKind"><option value="">请由教师选择</option><option value="fact">可核对事实</option><option value="student_statement">学生陈述</option><option value="reported_statement">转述信息</option><option value="teacher_observation">教师观察</option><option value="provisional_judgment">阶段性判断</option><option value="professional_conclusion">有依据的专业结论</option></select></label><label v-if="recordKind === 'professional_conclusion'"><span>结论日期</span><input v-model="professionalObservedDate" type="date"></label><label v-else><span>发生时间</span><input v-model="observedAt" type="datetime-local"></label><label><span>场景</span><input v-model="scene" maxlength="200"></label><label><span>来源</span><input v-model="source" maxlength="200" placeholder="例如：教师观察、家长转述、医院书面材料"></label><template v-if="recordKind === 'professional_conclusion'"><label><span>专业依据</span><textarea v-model="basis" rows="3" maxlength="1000" placeholder="例如：材料名称、出具机构；采用前由教师核对"></textarea></label><label><span>当前在校支持</span><textarea v-model="currentSchoolSupport" rows="3" maxlength="1500" placeholder="如暂无，请明确填写“当前暂无”"></textarea></label><label><span>专业建议（如有）</span><textarea v-model="professionalRecommendations" rows="3" maxlength="1500"></textarea></label><label><span>需要避免的做法（如有）</span><textarea v-model="avoidances" rows="3" maxlength="1500"></textarea></label></template><template v-if="expiringRecord"><label><span>复查时间</span><input v-model="reviewAt" type="datetime-local"></label><label><span>失效时间</span><input v-model="expiresAt" type="datetime-local"></label></template></aside>
      <main><h2>事实、转述与判断</h2><label><span>直接观察或可核事实</span><textarea v-model="factText" rows="5" maxlength="6000"></textarea></label><label><span>他人转述（如有）</span><textarea v-model="reportedText" rows="4" maxlength="4000"></textarea></label><label><span>教师当前判断（可留空）</span><textarea v-model="judgmentText" rows="3" maxlength="3000"></textarea></label><section v-if="isStudentRecord && (recordKind === 'professional_conclusion' || Object.keys(recordProfileUpdate).length)" class="record-profile-update"><h3>拟更新的学生当前档案</h3><p>这里记录当前支持需要，不把诊断当作性格、能力或纪律标签。</p><label><span>合并后的档案摘要</span><textarea v-model="recordProfileSummary" rows="4" maxlength="4000"></textarea></label><label><span>仍需了解（每行一个）</span><textarea v-model="recordProfileQuestions" rows="3" maxlength="2000" placeholder="例如：当前在校支持是否有效"></textarea></label></section><p>三类内容会分段保存；系统不会把线索升级为诊断、欺凌认定或惩戒结论。</p></main>
      <aside class="review"><h2>保存前核对</h2><ul><li v-for="item in draft.missing_fields" :key="item">{{ item }}</li><li>正式保存由你点击确认</li><li>需要复查时另建计划</li></ul></aside>
    </div>

    <div v-else-if="draft.handling_mode === 'plan_calendar'" class="plan-layout">
      <aside><h2>事务简报</h2><label><span>目标</span><input v-model="planTitle" maxlength="240"></label><label><span>最终截止</span><input v-model="deadline" type="datetime-local"></label><p>{{ summary }}</p></aside>
      <main><div class="plan-actions__heading"><div><h2>行动与日期</h2><p>逐项核对说明、日期和前置依赖；空日期不会自动套用总截止。</p></div><button type="button" @click="addPlanAction">增加行动</button></div><div class="plan-actions"><article v-for="(action, index) in planActions" :key="action.draft_action_id" class="plan-action"><header><strong>行动 {{ index + 1 }}</strong><button type="button" @click="removePlanAction(action.draft_action_id)">移除</button></header><label><span>行动名称</span><input v-model="action.title" maxlength="240"></label><label><span>截止时间</span><input v-model="action.due_at" type="datetime-local"></label><label><span>行动说明</span><textarea v-model="action.details" rows="3" maxlength="2000"></textarea></label><fieldset><legend>需要先完成</legend><label v-for="candidate in planActions.filter((item) => item.draft_action_id !== action.draft_action_id)" :key="candidate.draft_action_id" class="dependency"><input v-model="action.depends_on_draft_action_ids" type="checkbox" :value="candidate.draft_action_id"><span>{{ candidate.title || '未命名行动' }}</span></label><small v-if="planActions.length < 2">当前没有其他行动可作为前置依赖。</small></fieldset></article><p v-if="!planActions.length" class="empty-plan">还没有行动。请先增加至少一项，再加入正式计划。</p></div></main>
      <aside class="review"><h2>当前决定</h2><p>日期可修改，依赖也由你逐项确认；系统不会把并行行动静默改成串行，也不会用总截止填补空日期。所有行动正式加入同一计划，不在首页复制另一份。</p></aside>
    </div>

    <div v-else class="sop-layout">
      <section class="safety"><strong>先确认即时安全</strong><p>若冲突仍在发生、有人受伤或存在迫近危险，先保护学生并联系有权角色，不等待表单或 AI。</p></section>
      <aside>
        <h2>对象、档案与待核对项</h2>
        <label><span>学校流程模板</span><select v-model="sopTemplateKey"><option value="">请根据实际情况选择</option><option v-for="item in sopTemplates" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select></label>
        <p v-if="!sopTemplateKey">模型不可用时系统不会替你猜测冲突、受伤或疑似欺凌；请由教师选择。</p>
        <label><span>参与人（每行一个）</span><textarea v-model="participantRefs" rows="4"></textarea></label>
        <p>{{ summary }}</p>
        <section v-if="sopStudentProfiles.length" class="student-profiles">
          <h3>学生当前档案背景</h3>
          <p class="section-note">只读用于本次判断；需要写入的内容在下方逐名确认。</p>
          <article v-for="student in sopStudentProfiles" :key="`${student.display_name}-${student.class_label}`"><header><strong>{{ student.display_name }}</strong><span>{{ student.class_label || '班级待核对' }}</span></header><p>{{ student.profile.summary || '当前档案还没有总体摘要。' }}</p><div v-for="dimension in student.profile.dimensions ?? []" :key="dimension.key || dimension.label"><b>{{ dimension.label || '当前维度' }}</b><span>{{ (dimension.items ?? []).join('；') }}</span></div></article>
        </section>
        <section v-if="sopProfileUpdates.length" class="profile-updates">
          <h3>拟写入学生档案</h3>
          <p class="section-note">确认 SOP 时，只保存已勾选学生的事件记录和当前档案更新；不会预设责任方。</p>
          <article v-for="update in sopProfileUpdates" :key="update.subject_ref.id" :class="{ excluded: !update.include }">
            <label class="include-update"><input v-model="update.include" type="checkbox"><span>同步更新 {{ update.display_name }} 的档案</span></label>
            <template v-if="update.include">
              <label><span>记录性质</span><select v-model="update.record_kind"><option value="">请由教师选择</option><option value="fact">可核对事实</option><option value="student_statement">学生陈述</option><option value="reported_statement">转述信息</option><option value="teacher_observation">教师观察</option><option value="provisional_judgment">阶段性判断</option><option value="professional_conclusion">有依据的专业结论</option></select></label>
              <label><span>信息来源</span><input v-model="update.source" maxlength="200"></label>
              <label><span>发生时间</span><input v-model="update.observed_at" type="datetime-local"></label>
              <template v-if="['teacher_observation', 'provisional_judgment'].includes(update.record_kind)"><label><span>复查时间</span><input v-model="update.review_at" type="datetime-local"></label><label><span>失效时间</span><input v-model="update.expires_at" type="datetime-local"></label></template>
              <label><span>这名学生的事件记录</span><textarea v-model="update.record_summary" rows="3" maxlength="4000"></textarea></label>
              <label><span>合并后的当前档案摘要</span><textarea v-model="update.profile_update.summary" rows="3" maxlength="4000"></textarea></label>
              <label><span>事实或材料依据</span><textarea v-model="update.basis" rows="2" maxlength="1000"></textarea></label>
              <div v-if="update.profile_update.open_questions.length" class="profile-update-list"><b>后续仍需了解</b><ul><li v-for="question in update.profile_update.open_questions" :key="question">{{ question }}</li></ul></div>
            </template>
          </article>
        </section>
        <section v-if="sopQuestions.length" class="verify-list"><h3>本轮需要补充</h3><ul><li v-for="question in sopQuestions" :key="question">{{ question }}</li></ul></section>
      </aside>
      <main><h2>流程草稿</h2><ol v-if="sopSteps.length"><li v-for="(step, index) in sopSteps" :key="step.key"><small>步骤 {{ index + 1 }}<template v-if="step.safety_required"> · 安全必做</template></small><strong>{{ step.title }}</strong><span>{{ step.details || '采用前请结合本校流程补充具体做法。' }}</span></li></ol><ol v-else><li><strong>确保现场安全</strong><span>安全必做步骤不能由模型自动删除</span></li><li><strong>分别记录直接事实与转述</strong><span>不先作欺凌认定</span></li><li><strong>由教师选择学校交接</strong><span>AI 不作惩戒、诊断或结案决定</span></li></ol></main>
      <aside class="review"><h2>教师决定</h2><p>确认后只建立待处理 SOP。每一步完成、对外沟通、惩戒、认定和结案仍需教师或有权人员决定。</p></aside>
    </div>

    <section class="ai-revision" aria-labelledby="ai-revision-title"><div><h2 id="ai-revision-title">让 AI 调整这份草稿</h2><p>这会建立一次新的、最多发送一次的模型任务；只更新草稿，不会正式保存。</p></div><textarea v-model="revisionInstruction" rows="2" maxlength="2000" placeholder="例如：把行动拆得更细，但保留原截止时间"></textarea><button type="button" :disabled="busy || !revisionInstruction.trim()" @click="requestRevision">提交调整</button></section>
    <footer><button type="button" :disabled="busy" @click="discard">丢弃草稿</button><span>草稿版本 {{ draft.draft_revision }}</span><button type="button" :disabled="busy" @click="save">保存草稿</button><button class="primary" type="button" :disabled="busy" @click="adopt">{{ draft.handling_mode === 'record' ? '确认保存记录' : draft.handling_mode === 'plan_calendar' ? '确认加入计划／日历' : selectedSopProfileUpdates.length ? `确认建立 SOP 并更新 ${selectedSopProfileUpdates.length} 份档案` : '确认建立 SOP' }}</button></footer>
  </section>
  <section v-else-if="error" class="loading error" role="alert">{{ error }}</section>
  <section v-else class="loading" aria-live="polite">正在打开草稿…</section>
</template>

<style scoped>
.plan-actions__heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.plan-actions__heading h2,.plan-actions__heading p{margin:0}.plan-actions__heading button,.plan-action header button{min-height:34px;padding:0 11px;border:1px solid var(--color-border-strong);border-radius:8px;background:var(--color-bg-surface);color:var(--color-info);font:inherit}.plan-actions{display:grid;gap:14px;margin-top:18px}.plan-action{padding:16px;border:1px solid var(--color-border-strong);border-radius:12px;background:var(--color-info-subtle)}.plan-action>header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.plan-action fieldset{display:grid;gap:8px;padding:11px;border:1px solid var(--color-border-strong);border-radius:9px}.plan-action legend{padding:0 5px;font-size:13px;font-weight:800}.plan-action .dependency{display:flex;align-items:center;gap:8px;margin:0;font-weight:500}.plan-action .dependency input{width:auto}.empty-plan{padding:18px;border:1px dashed var(--color-border-strong);border-radius:10px;text-align:center}
.handoff-page{--mode:var(--color-accent);display:grid;gap:0;color:var(--color-text-primary)}.handoff-page[data-mode="plan_calendar"]{--mode:var(--color-info)}.handoff-page[data-mode="sop"]{--mode:var(--color-warning)}.handoff-page__header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:22px 26px;border:1px solid var(--color-border-default);border-top:5px solid var(--mode);border-radius:16px 16px 0 0;background:var(--color-bg-subtle)}.handoff-page__header p{margin:0;color:var(--mode);font-size:12px;font-weight:800;letter-spacing:.08em}.handoff-page__header h1{margin:3px 0;font-size:30px}.handoff-page__header span{color:var(--color-text-secondary)}.handoff-page__header button,footer button,.ai-revision button{min-height:40px;padding:0 14px;border:1px solid var(--color-border-default);border-radius:9px;background:var(--color-bg-surface);font:inherit}.record-layout,.plan-layout,.sop-layout{display:grid;grid-template-columns:minmax(260px,.9fr) minmax(380px,1.5fr) minmax(220px,.72fr);min-height:520px;border-inline:1px solid var(--color-border-default)}.record-layout>*,.plan-layout>*,.sop-layout>*{padding:22px;border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.record-layout>main,.plan-layout>main,.sop-layout>main{background:var(--color-bg-surface)}.sop-layout{grid-template-areas:"safety safety safety" "left middle right";grid-template-rows:auto 1fr}.sop-layout .safety{grid-area:safety;padding:14px 22px;border-bottom:1px solid var(--color-warning);background:var(--color-warning-subtle)}.safety p{display:inline;margin-left:14px}.sop-layout>aside:first-of-type{grid-area:left}.sop-layout>main{grid-area:middle}.sop-layout>.review{grid-area:right}.handoff-page h2{margin:0 0 18px;font-size:17px}.handoff-page label{display:grid;gap:6px;margin-bottom:14px;font-size:13px;font-weight:700}.handoff-page input,.handoff-page select,.handoff-page textarea{width:100%;padding:10px;border:1px solid var(--color-border-strong);border-radius:8px;background:var(--color-bg-surface);font:inherit;line-height:1.5}.handoff-page main p,.review p,.review li,.handoff-page aside p{color:var(--color-text-secondary);line-height:1.55}.sop-layout ol{display:grid;gap:13px;padding:0;list-style:none}.sop-layout li{display:grid;gap:5px;padding:15px;border-left:4px solid var(--mode);background:var(--color-warning-subtle)}.sop-layout li small{color:var(--color-warning);font-weight:800}.sop-layout li span{color:var(--color-text-secondary)}.student-profiles,.verify-list{margin-top:18px;padding-top:16px;border-top:1px solid var(--color-border-default)}.student-profiles h3,.verify-list h3{margin:0 0 6px;font-size:14px}.student-profiles .section-note{margin:0 0 10px;font-size:12px}.student-profiles article{display:grid;gap:7px;margin-top:10px;padding:12px;border:1px solid var(--color-border-default);border-radius:10px;background:var(--color-bg-surface)}.student-profiles article header{display:flex;justify-content:space-between;gap:8px}.student-profiles article header span{color:var(--color-text-secondary);font-size:12px}.student-profiles article p{margin:0}.student-profiles article div{display:grid;gap:3px;font-size:12px}.student-profiles article div span{color:var(--color-text-secondary)}.verify-list ul{display:grid;gap:7px;margin:8px 0 0;padding-left:18px}.verify-list li{padding:0;border:0;background:transparent}.ai-revision{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(320px,1.5fr) auto;align-items:center;gap:16px;padding:18px 22px;border:1px solid var(--color-border-default);background:var(--color-warning-subtle)}.ai-revision h2,.ai-revision p{margin:0}.ai-revision p{margin-top:4px;color:var(--color-text-secondary);font-size:12px}.ai-revision textarea{resize:vertical}.ai-revision button{border-color:var(--mode);color:var(--mode);font-weight:750}footer{display:flex;align-items:center;justify-content:flex-end;gap:10px;padding:14px 20px;border:1px solid var(--color-border-default);border-radius:0 0 16px 16px;background:var(--color-bg-app);position:sticky;bottom:0}footer span{margin-right:auto;color:var(--color-text-secondary);font-size:12px}footer .primary{border-color:var(--mode);background:var(--mode);color:var(--color-bg-surface);font-weight:750}.status,.error{margin:0;padding:10px 18px;border-inline:1px solid var(--color-border-default)}.status{background:var(--color-success-subtle)}.error{background:var(--color-danger-subtle);color:var(--color-danger)}.loading{min-height:420px;display:grid;place-items:center}.handoff-page button:focus-visible,.handoff-page input:focus-visible,.handoff-page select:focus-visible,.handoff-page textarea:focus-visible{outline:3px solid var(--color-warning);outline-offset:2px}@media(max-width:980px){.record-layout,.plan-layout,.sop-layout{grid-template-columns:1fr}.sop-layout{display:grid;grid-template-areas:"safety" "left" "middle" "right";grid-template-rows:auto}.record-layout>*,.plan-layout>*,.sop-layout>*{border-right:0;border-bottom:1px solid var(--color-border-default)}.ai-revision{grid-template-columns:1fr}}@media(max-width:640px){.handoff-page__header{align-items:flex-start;flex-direction:column}footer{flex-wrap:wrap;position:static}footer span{width:100%;margin:0}.safety p{display:block;margin:6px 0 0}}
.profile-updates,.record-profile-update{margin-top:18px;padding-top:16px;border-top:1px solid var(--color-border-default)}.profile-updates h3,.record-profile-update h3{margin:0 0 6px;font-size:14px}.profile-updates .section-note{margin:0 0 10px;font-size:12px}.profile-updates article{display:grid;gap:7px;margin-top:10px;padding:12px;border:1px solid var(--color-border-default);border-radius:10px;background:var(--color-bg-surface)}.profile-updates article.excluded{opacity:.68}.profile-updates .include-update{display:flex;grid-template-columns:auto 1fr;align-items:center;gap:8px}.profile-updates .include-update input{width:auto}.profile-update-list{font-size:12px}.profile-update-list ul{margin:5px 0 0;padding-left:18px}
</style>
