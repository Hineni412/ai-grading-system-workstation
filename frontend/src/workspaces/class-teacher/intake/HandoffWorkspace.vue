<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getActivePinia } from 'pinia'

import { intakeApi, type HandoffDraft } from '../api/intake'
import { studentR1Api, type ExistingRosterStudent } from '../api/r1'
import { workspaceAITaskApi } from '../../shared/ai-tasks/api'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'

const props = defineProps<{ handoffId: string; token?: string }>()
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

const planActions = ref<PlanActionDraft[]>([])
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
const observedAt = computed({ get: () => String(content.value.observed_at ?? '').slice(0, 16), set: (value) => { content.value.observed_at = value } })
const recordKind = computed({ get: () => String(content.value.record_kind ?? 'fact'), set: (value) => { content.value.record_kind = value } })
const reviewAt = computed({ get: () => String(content.value.review_at ?? '').slice(0, 16), set: (value) => { content.value.review_at = value } })
const expiresAt = computed({ get: () => String(content.value.expires_at ?? '').slice(0, 16), set: (value) => { content.value.expires_at = value } })
const expiringRecord = computed(() => ['teacher_observation', 'provisional_judgment'].includes(recordKind.value))
const factText = computed({ get: () => String(content.value.fact ?? ''), set: (value) => { content.value.fact = value } })
const reportedText = computed({ get: () => String(content.value.reported ?? ''), set: (value) => { content.value.reported = value } })
const judgmentText = computed({ get: () => String(content.value.judgment ?? ''), set: (value) => { content.value.judgment = value } })
const planTitle = computed({ get: () => String(content.value.plan_title ?? content.value.summary ?? ''), set: (value) => { content.value.plan_title = value } })
const deadline = computed({ get: () => String(content.value.final_deadline ?? '').slice(0, 16), set: (value) => { content.value.final_deadline = value } })
const participantRefs = computed({ get: () => (content.value.participant_refs as string[] | undefined)?.join('\n') ?? '', set: (value) => { content.value.participant_refs = value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) } })
const sopTemplateKey = computed({ get: () => String(content.value.template_key ?? ''), set: (value) => { content.value.template_key = value } })

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
    if (isStudentRecord.value) {
      const preference = await intakeApi.homeroom()
      const result = await studentR1Api.rosterSource(props.token ?? '', { classLabel: preference.homeroom_class ?? undefined, pageSize: 100 })
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
    if (draft.value.handling_mode === 'record') {
      const sections = [
        factText.value.trim() ? `直接事实：${factText.value.trim()}` : '',
        reportedText.value.trim() ? `他人转述：${reportedText.value.trim()}` : '',
        judgmentText.value.trim() ? `教师判断：${judgmentText.value.trim()}` : '',
      ].filter(Boolean)
      content.value.summary = sections.join('\n')
    }
    const refs = selectedSubjectId.value ? [{ kind: 'student', id: selectedSubjectId.value, revision: selectedSubjectRevision.value }] : draft.value.subject_refs
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
  if (draft.value?.handling_mode === 'sop' && !sopTemplateKey.value) {
    error.value = '请先选择与实际情况相符的学校流程模板。'
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
    await intakeApi.adopt(props.token ?? '', current, targetRevision)
    message.value = '已按教师确认保存为正式内容。'
    emit('completed', current.conversation_id)
  } catch {
    error.value = '正式保存没有完成。现有正式数据不会重复创建；若对象已变化，请重新选择后再确认。'
    await load()
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
      <aside><h2>对象与来源</h2><label v-if="isStudentRecord"><span>学生</span><select v-model="selectedSubjectId" @change="chooseSubject"><option value="">请选择学生</option><option v-for="student in students" :key="student.opaque_ref" :value="student.opaque_ref">{{ student.display_name }} · {{ student.class_label || '未分班' }}</option></select></label><p v-else>这是一项一般事务登记，不会写入学生档案。</p><label><span>记录性质</span><select v-model="recordKind"><option value="fact">事实记录</option><option value="reported_statement">转述记录</option><option value="teacher_observation">教师观察</option><option value="provisional_judgment">暂定判断</option></select></label><label><span>发生时间</span><input v-model="observedAt" type="datetime-local"></label><label><span>场景</span><input v-model="scene" maxlength="200"></label><label><span>来源</span><input v-model="source" maxlength="200"></label><template v-if="expiringRecord"><label><span>复查时间</span><input v-model="reviewAt" type="datetime-local"></label><label><span>失效时间</span><input v-model="expiresAt" type="datetime-local"></label></template></aside>
      <main><h2>事实、转述与判断</h2><label><span>直接观察或可核事实</span><textarea v-model="factText" rows="5" maxlength="6000"></textarea></label><label><span>他人转述（如有）</span><textarea v-model="reportedText" rows="4" maxlength="4000"></textarea></label><label><span>教师当前判断（可留空）</span><textarea v-model="judgmentText" rows="3" maxlength="3000"></textarea></label><p>三类内容会分段保存；系统不会把线索升级为诊断、欺凌认定或惩戒结论。</p></main>
      <aside class="review"><h2>保存前核对</h2><ul><li v-for="item in draft.missing_fields" :key="item">{{ item }}</li><li>正式保存由你点击确认</li><li>需要复查时另建计划</li></ul></aside>
    </div>

    <div v-else-if="draft.handling_mode === 'plan_calendar'" class="plan-layout">
      <aside><h2>事务简报</h2><label><span>目标</span><input v-model="planTitle" maxlength="240"></label><label><span>最终截止</span><input v-model="deadline" type="datetime-local"></label><p>{{ summary }}</p></aside>
      <main><div class="plan-actions__heading"><div><h2>行动与日期</h2><p>逐项核对说明、日期和前置依赖；空日期不会自动套用总截止。</p></div><button type="button" @click="addPlanAction">增加行动</button></div><div class="plan-actions"><article v-for="(action, index) in planActions" :key="action.draft_action_id" class="plan-action"><header><strong>行动 {{ index + 1 }}</strong><button type="button" @click="removePlanAction(action.draft_action_id)">移除</button></header><label><span>行动名称</span><input v-model="action.title" maxlength="240"></label><label><span>截止时间</span><input v-model="action.due_at" type="datetime-local"></label><label><span>行动说明</span><textarea v-model="action.details" rows="3" maxlength="2000"></textarea></label><fieldset><legend>需要先完成</legend><label v-for="candidate in planActions.filter((item) => item.draft_action_id !== action.draft_action_id)" :key="candidate.draft_action_id" class="dependency"><input v-model="action.depends_on_draft_action_ids" type="checkbox" :value="candidate.draft_action_id"><span>{{ candidate.title || '未命名行动' }}</span></label><small v-if="planActions.length < 2">当前没有其他行动可作为前置依赖。</small></fieldset></article><p v-if="!planActions.length" class="empty-plan">还没有行动。请先增加至少一项，再加入正式计划。</p></div></main>
      <aside class="review"><h2>当前决定</h2><p>日期可修改，依赖也由你逐项确认；系统不会把并行行动静默改成串行，也不会用总截止填补空日期。所有行动正式加入同一计划，不在首页复制另一份。</p></aside>
    </div>

    <div v-else class="sop-layout">
      <section class="safety"><strong>先确认即时安全</strong><p>若冲突仍在发生、有人受伤或存在迫近危险，先保护学生并联系有权角色，不等待表单或 AI。</p></section>
      <aside><h2>已知与未知</h2><label><span>学校流程模板</span><select v-model="sopTemplateKey"><option value="">请根据实际情况选择</option><option v-for="item in sopTemplates" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select></label><p v-if="!sopTemplateKey">模型不可用时系统不会替你猜测冲突、受伤或疑似欺凌；请由教师选择。</p><label><span>参与人（每行一个）</span><textarea v-model="participantRefs" rows="6"></textarea></label><p>{{ summary }}</p></aside>
      <main><h2>流程草稿</h2><ol><li><strong>确保现场安全</strong><span>安全必做步骤不能由模型自动删除</span></li><li><strong>分别记录直接事实与转述</strong><span>不先作欺凌认定</span></li><li><strong>由教师选择学校交接</strong><span>AI 不作惩戒、诊断或结案决定</span></li></ol></main>
      <aside class="review"><h2>教师决定</h2><p>确认后只建立待处理 SOP。每一步完成、对外沟通、惩戒、认定和结案仍需教师或有权人员决定。</p></aside>
    </div>

    <section class="ai-revision" aria-labelledby="ai-revision-title"><div><h2 id="ai-revision-title">让 AI 调整这份草稿</h2><p>这会建立一次新的、最多发送一次的模型任务；只更新草稿，不会正式保存。</p></div><textarea v-model="revisionInstruction" rows="2" maxlength="2000" placeholder="例如：把行动拆得更细，但保留原截止时间"></textarea><button type="button" :disabled="busy || !revisionInstruction.trim()" @click="requestRevision">提交调整</button></section>
    <footer><button type="button" :disabled="busy" @click="discard">丢弃草稿</button><span>草稿版本 {{ draft.draft_revision }}</span><button type="button" :disabled="busy" @click="save">保存草稿</button><button class="primary" type="button" :disabled="busy" @click="adopt">{{ draft.handling_mode === 'record' ? '确认保存记录' : draft.handling_mode === 'plan_calendar' ? '确认加入计划／日历' : '确认建立 SOP' }}</button></footer>
  </section>
  <section v-else-if="error" class="loading error" role="alert">{{ error }}</section>
  <section v-else class="loading" aria-live="polite">正在打开草稿…</section>
</template>

<style scoped>
.plan-actions__heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.plan-actions__heading h2,.plan-actions__heading p{margin:0}.plan-actions__heading button,.plan-action header button{min-height:34px;padding:0 11px;border:1px solid var(--color-border-strong);border-radius:8px;background:var(--color-bg-surface);color:var(--color-info);font:inherit}.plan-actions{display:grid;gap:14px;margin-top:18px}.plan-action{padding:16px;border:1px solid var(--color-border-strong);border-radius:12px;background:var(--color-info-subtle)}.plan-action>header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.plan-action fieldset{display:grid;gap:8px;padding:11px;border:1px solid var(--color-border-strong);border-radius:9px}.plan-action legend{padding:0 5px;font-size:13px;font-weight:800}.plan-action .dependency{display:flex;align-items:center;gap:8px;margin:0;font-weight:500}.plan-action .dependency input{width:auto}.empty-plan{padding:18px;border:1px dashed var(--color-border-strong);border-radius:10px;text-align:center}
.handoff-page{--mode:var(--color-accent);display:grid;gap:0;color:var(--color-text-primary)}.handoff-page[data-mode="plan_calendar"]{--mode:var(--color-info)}.handoff-page[data-mode="sop"]{--mode:var(--color-warning)}.handoff-page__header{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:22px 26px;border:1px solid var(--color-border-default);border-top:5px solid var(--mode);border-radius:16px 16px 0 0;background:var(--color-bg-subtle)}.handoff-page__header p{margin:0;color:var(--mode);font-size:12px;font-weight:800;letter-spacing:.08em}.handoff-page__header h1{margin:3px 0;font-size:30px}.handoff-page__header span{color:var(--color-text-secondary)}.handoff-page__header button,footer button,.ai-revision button{min-height:40px;padding:0 14px;border:1px solid var(--color-border-default);border-radius:9px;background:var(--color-bg-surface);font:inherit}.record-layout,.plan-layout,.sop-layout{display:grid;grid-template-columns:minmax(220px,.72fr) minmax(360px,1.5fr) minmax(220px,.72fr);min-height:520px;border-inline:1px solid var(--color-border-default)}.record-layout>*,.plan-layout>*,.sop-layout>*{padding:22px;border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.record-layout>main,.plan-layout>main,.sop-layout>main{background:var(--color-bg-surface)}.sop-layout{grid-template-areas:"safety safety safety" "left middle right";grid-template-rows:auto 1fr}.sop-layout .safety{grid-area:safety;padding:14px 22px;border-bottom:1px solid var(--color-warning);background:var(--color-warning-subtle)}.safety p{display:inline;margin-left:14px}.sop-layout>aside:first-of-type{grid-area:left}.sop-layout>main{grid-area:middle}.sop-layout>.review{grid-area:right}.handoff-page h2{margin:0 0 18px;font-size:17px}.handoff-page label{display:grid;gap:6px;margin-bottom:14px;font-size:13px;font-weight:700}.handoff-page input,.handoff-page select,.handoff-page textarea{width:100%;padding:10px;border:1px solid var(--color-border-strong);border-radius:8px;background:var(--color-bg-surface);font:inherit;line-height:1.5}.handoff-page main p,.review p,.review li,.handoff-page aside p{color:var(--color-text-secondary);line-height:1.55}.sop-layout ol{display:grid;gap:13px;padding:0;list-style:none}.sop-layout li{display:grid;gap:5px;padding:15px;border-left:4px solid var(--mode);background:var(--color-warning-subtle)}.sop-layout li span{color:var(--color-text-secondary)}.ai-revision{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(320px,1.5fr) auto;align-items:center;gap:16px;padding:18px 22px;border:1px solid var(--color-border-default);background:var(--color-warning-subtle)}.ai-revision h2,.ai-revision p{margin:0}.ai-revision p{margin-top:4px;color:var(--color-text-secondary);font-size:12px}.ai-revision textarea{resize:vertical}.ai-revision button{border-color:var(--mode);color:var(--mode);font-weight:750}footer{display:flex;align-items:center;justify-content:flex-end;gap:10px;padding:14px 20px;border:1px solid var(--color-border-default);border-radius:0 0 16px 16px;background:var(--color-bg-app);position:sticky;bottom:0}footer span{margin-right:auto;color:var(--color-text-secondary);font-size:12px}footer .primary{border-color:var(--mode);background:var(--mode);color:var(--color-bg-surface);font-weight:750}.status,.error{margin:0;padding:10px 18px;border-inline:1px solid var(--color-border-default)}.status{background:var(--color-success-subtle)}.error{background:var(--color-danger-subtle);color:var(--color-danger)}.loading{min-height:420px;display:grid;place-items:center}.handoff-page button:focus-visible,.handoff-page input:focus-visible,.handoff-page select:focus-visible,.handoff-page textarea:focus-visible{outline:3px solid var(--color-warning);outline-offset:2px}@media(max-width:980px){.record-layout,.plan-layout,.sop-layout{grid-template-columns:1fr}.sop-layout{display:grid;grid-template-areas:"safety" "left" "middle" "right";grid-template-rows:auto}.record-layout>*,.plan-layout>*,.sop-layout>*{border-right:0;border-bottom:1px solid var(--color-border-default)}.ai-revision{grid-template-columns:1fr}}@media(max-width:640px){.handoff-page__header{align-items:flex-start;flex-direction:column}footer{flex-wrap:wrap;position:static}footer span{width:100%;margin:0}.safety p{display:block;margin:6px 0 0}}
</style>
