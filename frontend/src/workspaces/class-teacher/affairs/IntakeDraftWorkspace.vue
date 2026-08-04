<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import {
  homeIntakeApi,
  type HomeIntakeDraft,
  type HomeIntakeOperation,
  type HomeIntakePreview,
  type HomeIntakeRecommendation,
} from '../api/homeIntake'
import { studentR1Api, type CurrentRosterStudent } from '../api/r1'
import type { OrdinaryWorkModule } from '../ordinary/createOrdinaryWorkModule'

const props = defineProps<{ draftId: string; token: string; module: OrdinaryWorkModule }>()
const emit = defineEmits<{ close: []; completed: [] }>()

const draft = ref<HomeIntakeDraft | null>(null)
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const error = ref('')
const feedback = ref('')
const selectedRootStepKey = ref<string | null>(null)
const roster = ref<CurrentRosterStudent[]>([])
const selectedSubjectIds = ref<string[]>([])
const draftHeading = ref<HTMLElement | null>(null)
const studentPickerOpen = ref(false)
const studentQuery = ref('')
const studentPickerDialog = ref<HTMLElement | null>(null)
const studentSearchInput = ref<HTMLInputElement | null>(null)
const studentPickerTrigger = ref<HTMLButtonElement | null>(null)
const busyTask = ref<'revision' | 'save' | 'discard' | null>(null)

const operation = computed(() => draft.value?.operation ?? null)
const plan = computed(() => operation.value?.result?.kind === 'ordinary_plan' ? operation.value.result.value : null)
const recommendation = computed<HomeIntakeRecommendation | null>(() => {
  const result = operation.value?.result
  return result?.kind === 'affair_recommendation' || result?.kind === 'student_support_recommendation'
    ? result.value
    : null
})
const plainText = computed(() => operation.value?.result?.kind === 'plain_text' ? operation.value.result.value.text : '')

const stepDates = computed(() => {
  const dates = new Map<string, string>()
  for (const item of recommendation.value?.calendar_items || []) {
    if (item.due_date && !dates.has(item.step_key)) dates.set(item.step_key, item.due_date)
  }
  return dates
})

function compareTimelineDates(left: string, right: string): number {
  if (left === right) return 0
  if (left === '日期待定') return 1
  if (right === '日期待定') return -1
  return left.localeCompare(right)
}

const timelineGroups = computed(() => {
  const groups = new Map<string, { date: string; steps: Array<{ index: number; step: HomeIntakeRecommendation['steps'][number] }> }>()
  for (const [index, step] of (recommendation.value?.steps || []).entries()) {
    const date = stepDates.value.get(step.key) || '日期待定'
    const group = groups.get(date) || { date, steps: [] }
    group.steps.push({ index, step })
    groups.set(date, group)
  }
  return [...groups.values()].sort((left, right) => compareTimelineDates(left.date, right.date))
})
const planTimelineGroups = computed(() => {
  const groups = new Map<string, { date: string; nodes: Array<{ index: number; node: NonNullable<typeof plan.value>['nodes'][number] }> }>()
  for (const [index, node] of (plan.value?.nodes || []).entries()) {
    const date = node.due_date || '日期待定'
    const group = groups.get(date) || { date, nodes: [] }
    group.nodes.push({ index, node })
    groups.set(date, group)
  }
  return [...groups.values()].sort((left, right) => compareTimelineDates(left.date, right.date))
})
const orderedRecommendationSteps = computed(() => (
  timelineGroups.value.flatMap((group) => group.steps.map((item) => item.step))
))
const selectedStepKeys = computed(() => {
  if (!selectedRootStepKey.value) return []
  const rootIndex = orderedRecommendationSteps.value.findIndex((step) => step.key === selectedRootStepKey.value)
  if (rootIndex < 0) return []
  return orderedRecommendationSteps.value.slice(rootIndex).map((step) => step.key)
})
const selectedCalendarKeys = computed(() => {
  if (!recommendation.value) return []
  const selected = new Set(selectedStepKeys.value)
  return recommendation.value.calendar_items
    .filter((item) => selected.has(item.step_key))
    .map((item) => item.key)
})
const linkedStudents = computed(() => {
  const selected = new Set(selectedSubjectIds.value)
  return roster.value.filter((student) => selected.has(student.subject_id))
})
const pickerStudents = computed(() => {
  const query = studentQuery.value.trim().toLocaleLowerCase()
  if (!query) return linkedStudents.value
  return roster.value.filter((student) => (
    student.display_name.toLocaleLowerCase().includes(query)
  ))
})
const activeRecommendationStep = computed(() => {
  if (!recommendation.value?.steps.length) return null
  return recommendation.value.steps.find((step) => step.key === selectedRootStepKey.value)
    || recommendation.value.steps[0]
})
const activeStepIndex = computed(() => {
  if (!activeRecommendationStep.value || !recommendation.value) return 0
  return recommendation.value.steps.findIndex((step) => step.key === activeRecommendationStep.value?.key)
})
const verificationByStep = computed(() => {
  const result = new Map<string, string[]>()
  const steps = recommendation.value?.steps || []
  for (const [index, question] of (recommendation.value?.to_verify || []).entries()) {
    const step = steps[Math.min(index, Math.max(steps.length - 1, 0))]
    if (!step) continue
    const items = result.get(step.key) || []
    items.push(question)
    result.set(step.key, items)
  }
  return result
})
const visibleStatus = computed(() => {
  if (busyTask.value === 'revision') return `AI 正在调整；当前第 ${draft.value?.version ?? 1} 版仍可使用。`
  if (busyTask.value === 'save') return '正在保存正式事务、学生关联和日历，请勿重复点击。'
  if (busyTask.value === 'discard') return '正在放弃草稿。'
  return message.value
})

function operationStateLabel(state: HomeIntakeOperation['state']): string {
  const labels: Partial<Record<HomeIntakeOperation['state'], string>> = {
    succeeded: '已成功生成',
    needs_information: '等待补充',
    unavailable: '当前不可用',
    invalid_result: '结果未通过校验',
    result_unknown: '结果待确认',
    in_progress: '处理中',
    destination_changed: '模型目的地已变化',
    unsafe_output_suppressed: '结果已抑制',
    failed_before_send: '发送前失败',
  }
  return labels[state] || state
}

function toggleStep(key: string): void {
  selectedRootStepKey.value = selectedRootStepKey.value === key ? null : key
}

function sourceText(value: HomeIntakeOperation | null): string {
  const raw = value?.local_context.source_text
  return typeof raw === 'string' ? raw : ''
}

async function loadRoster(value: HomeIntakeOperation, preserveSelection = false): Promise<void> {
  if (!recommendation.value) return
  const previousSelection = new Set(selectedSubjectIds.value)
  try {
    const current = await studentR1Api.currentRoster(props.token)
    roster.value = current.items.filter((item) => item.state === 'active')
  } catch {
    roster.value = []
    return
  }
  if (preserveSelection) {
    selectedSubjectIds.value = roster.value
      .filter((student) => previousSelection.has(student.subject_id))
      .map((student) => student.subject_id)
    return
  }
  const names = new Map<string, number>()
  for (const item of roster.value) names.set(item.display_name, (names.get(item.display_name) || 0) + 1)
  const original = sourceText(value)
  selectedSubjectIds.value = roster.value
    .filter((item) => names.get(item.display_name) === 1 && original.includes(item.display_name))
    .map((item) => item.subject_id)
}

async function loadDraft(focusHeading = false, preserveStudentSelection = false): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    draft.value = await homeIntakeApi.getDraft(props.draftId, props.token)
    selectedRootStepKey.value = null
    feedback.value = ''
    await loadRoster(draft.value.operation, preserveStudentSelection)
  } catch {
    error.value = '事务草稿暂时无法读取；现有草稿没有被删除。'
  } finally {
    loading.value = false
    if (focusHeading) void nextTick(() => draftHeading.value?.focus())
  }
}

async function dispatchRevision(value: HomeIntakePreview): Promise<void> {
  try {
    const revised = await homeIntakeApi.dispatch(value, globalThis.crypto.randomUUID(), props.token)
    if (revised.state === 'succeeded' && revised.draft_id === props.draftId) {
      await loadDraft(false, true)
      message.value = '已保存新的草稿版本；未选中的流程保持不变。'
      return
    }
    if (revised.previous_result_preserved) {
      message.value = `本次调整没有形成有效新版本，仍保留第 ${draft.value?.version ?? 1} 版方案。${revised.validation_issue || revised.assistant_message || ''}`
      return
    }
    message.value = '本次调整没有形成有效新版本，上一版方案仍保留。'
  } catch {
    message.value = '本次调整请求没有完成，上一版方案仍保留。'
  }
}

function openStudentPicker(): void {
  studentQuery.value = ''
  studentPickerOpen.value = true
  void nextTick(() => studentSearchInput.value?.focus())
}

function closeStudentPicker(): void {
  studentPickerOpen.value = false
  void nextTick(() => studentPickerTrigger.value?.focus())
}

function handleStudentPickerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeStudentPicker()
    return
  }
  if (event.key !== 'Tab' || !studentPickerDialog.value) return
  const focusable = [...studentPickerDialog.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])')]
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

async function requestRevision(): Promise<void> {
  if (!operation.value || !feedback.value.trim() || !selectedStepKeys.value.length || busy.value) return
  busy.value = true
  busyTask.value = 'revision'
  message.value = ''
  try {
    const next = await homeIntakeApi.followUpPreview(
      operation.value.operation_id,
      feedback.value,
      props.token,
      undefined,
      selectedStepKeys.value,
      selectedCalendarKeys.value,
    )
    if (next.dispatch_ready) await dispatchRevision(next)
    else {
      message.value = '补充内容尚不能发送，上一版方案仍保留。'
    }
  } catch {
    message.value = '调整预览没有准备成功，上一版方案仍保留。'
  } finally {
    busy.value = false
    busyTask.value = null
  }
}

async function confirmPlan(): Promise<void> {
  if (!operation.value || !plan.value || busy.value) return
  busy.value = true
  busyTask.value = 'save'
  message.value = ''
  try {
    await homeIntakeApi.adoptOrdinaryDraft(operation.value, props.token, draft.value ? { draftId: draft.value.draft_id, version: draft.value.version } : undefined)
    await props.module.load('today')
    emit('completed')
  } catch {
    message.value = '正式保存尚未全部确认；再次点击会沿用同一写入编号续做，不会重新请求 AI。'
  } finally {
    busy.value = false
    busyTask.value = null
  }
}

async function adoptRecommendation(): Promise<void> {
  if (!operation.value || !recommendation.value || !selectedSubjectIds.value.length || busy.value) return
  busy.value = true
  busyTask.value = 'save'
  message.value = ''
  try {
    const stableId = `adopt-${operation.value.operation_id}`.slice(0, 128)
    await homeIntakeApi.adopt(
      operation.value,
      stableId,
      selectedSubjectIds.value,
      props.token,
      draft.value ? { draftId: draft.value.draft_id, version: draft.value.version } : undefined,
    )
    await props.module.load('today')
    emit('completed')
  } catch {
    message.value = '正式保存尚未全部确认；再次点击只会续做未完成阶段，不会重复请求 AI。'
  } finally {
    busy.value = false
    busyTask.value = null
  }
}

async function discardDraft(): Promise<void> {
  if (!draft.value || busy.value) return
  busy.value = true
  busyTask.value = 'discard'
  try {
    await homeIntakeApi.discardDraft(draft.value.draft_id, draft.value.version, props.token)
    emit('close')
  } catch {
    message.value = '草稿已经变化或暂时无法放弃，请刷新后再试。'
  } finally {
    busy.value = false
    busyTask.value = null
  }
}

onMounted(() => { void loadDraft(true) })
watch(() => props.draftId, () => { void loadDraft(true) })
</script>

<template>
  <section class="draft-workspace" :aria-busy="loading || busy">
    <header class="draft-head">
      <button type="button" class="back" @click="emit('close')">← 返回事务列表</button>
      <h2 ref="draftHeading" tabindex="-1">
        <span>{{ recommendation?.title || plan?.nodes[0]?.title || '事务处理方案' }}</span>
        <small v-if="draft">第 {{ draft.version }} 版 · 草稿已自动保存</small>
      </h2>
    </header>

    <p v-if="loading" class="state">正在恢复草稿…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }}</p>

    <template v-else-if="draft && operation">
      <div class="draft-body">
        <section v-if="plan" class="content-block">
          <details class="ai-details">
            <summary>查看 AI 说明与调用详情</summary>
            <ul v-if="plan.assumptions.length"><li v-for="item in plan.assumptions" :key="item">{{ item }}</li></ul>
            <p class="call-details">技术状态：{{ operationStateLabel(operation.state) }} · 第 {{ operation.round_number }} 轮 · 本轮 {{ operation.round_physical_request_count }} 次请求 · 累计 {{ operation.cumulative_physical_request_count }} 次</p>
          </details>
          <div class="block-title"><div><p>执行时间线</p><h3>工作流程</h3></div><span>同一天可以推进多个步骤</span></div>
          <div class="timeline">
            <section v-for="group in planTimelineGroups" :key="group.date" class="timeline-day">
              <header><time>{{ group.date }}</time><span>{{ group.nodes.length }} 项</span></header>
              <ol class="flow-track flow-track--ordinary">
                <li v-for="item in group.nodes" :key="item.node.draft_key"><span class="index">{{ item.index + 1 }}</span><div><strong>{{ item.node.title }} <time>{{ group.date }}</time></strong><p>{{ item.node.details || item.node.rationale || '按实际进展完成此步骤。' }}</p></div></li>
              </ol>
            </section>
          </div>
        </section>

        <section v-else-if="recommendation" class="content-block content-block--affair">
          <section v-if="recommendation.emergency_prompt" class="notice notice--emergency" role="alert"><strong>优先提醒</strong><p>{{ recommendation.emergency_prompt }}</p></section>

          <div class="affair-cockpit">
            <aside class="case-brief" aria-label="事务简报">
              <p class="eyebrow">{{ recommendation.transaction_type || '学生事务' }}</p>
              <h3>事务简报</h3>
              <p class="case-summary">{{ recommendation.summary || '以下是基于当前信息形成的初步方案。' }}</p>
              <div class="linked-heading"><strong>关联学生 · {{ linkedStudents.length }} 人</strong><button ref="studentPickerTrigger" type="button" class="text-button" @click="openStudentPicker">更换</button></div>
              <div class="linked-students">
                <div v-for="student in linkedStudents" :key="student.subject_id" class="student-chip"><span>{{ student.display_name.slice(0, 1) }}</span><div><strong>{{ student.display_name }}</strong><small>{{ student.class_label || '未分班' }}</small></div></div>
                <p v-if="!linkedStudents.length">尚未匹配到学生，请先选择关联学生。</p>
              </div>
              <details class="ai-details">
                <summary>AI 说明与技术状态</summary>
                <p v-if="recommendation.model_advice">{{ recommendation.model_advice }}</p>
                <ul v-if="recommendation.assumptions.length"><li v-for="item in recommendation.assumptions" :key="item">{{ item }}</li></ul>
                <p class="call-details">{{ operationStateLabel(operation.state) }} · 第 {{ operation.round_number }} 轮 · 累计 {{ operation.cumulative_physical_request_count }} 次请求</p>
              </details>
            </aside>

            <section class="flow-canvas" aria-label="SOP 与日期安排">
              <div class="block-title"><div><p>流程轨迹</p><h3>SOP 与日期安排</h3></div><span>点击一步，即标记它和全部后续步骤</span></div>
              <div class="flow-columns">
                <section v-for="group in timelineGroups" :key="group.date" class="flow-phase">
                  <header><time>{{ group.date }}</time><span>{{ group.steps.length }} 项</span></header>
                  <ol>
                    <li v-for="item in group.steps" :key="item.step.key">
                      <button type="button" :class="{ selected: selectedStepKeys.includes(item.step.key), root: selectedRootStepKey === item.step.key }" :aria-pressed="selectedStepKeys.includes(item.step.key)" @click="toggleStep(item.step.key)">
                        <span class="index">{{ item.index + 1 }}</span><div><strong>{{ item.step.title }}</strong><p>{{ item.step.details }}</p><em v-if="item.step.safety_required">关键步骤</em></div>
                      </button>
                      <div v-for="question in verificationByStep.get(item.step.key) || []" :key="question" class="flow-question"><b>待核对</b><span>{{ question }}</span></div>
                    </li>
                  </ol>
                </section>
              </div>
            </section>

            <aside class="step-inspector" aria-label="当前步骤与调整">
              <p class="eyebrow">当前步骤 {{ activeStepIndex + 1 }}</p>
              <h3>{{ activeRecommendationStep?.title || '请选择步骤' }}</h3>
              <p>{{ activeRecommendationStep?.details || '点击中间流程中的步骤查看并选择调整起点。' }}</p>
              <div v-if="activeRecommendationStep && verificationByStep.get(activeRecommendationStep.key)?.length" class="inspector-question"><strong>会影响这一步的信息</strong><span v-for="question in verificationByStep.get(activeRecommendationStep.key)" :key="question">{{ question }}</span></div>
              <label class="revision-label">补充情况或说明不合适的原因<textarea v-model="feedback" rows="5" maxlength="4000" placeholder="例如：双方已经分开，没有人受伤；后续谈话安排到午休。"></textarea></label>
              <p class="revision-help">选中一步后，该步及全部后续步骤会交给 AI 调整；未选内容保持不变。</p>
              <p v-if="visibleStatus" class="operation-status" :class="{ busy: busyTask }" role="status">{{ visibleStatus }}</p>
            </aside>
          </div>

          <div v-if="studentPickerOpen" ref="studentPickerDialog" class="student-picker" role="dialog" aria-modal="true" aria-label="更换关联学生" @keydown="handleStudentPickerKeydown">
            <section><header><div><strong>更换关联学生</strong><span>默认只显示已关联学生；输入姓名再查找其他学生</span></div><button type="button" class="text-button" @click="closeStudentPicker">关闭</button></header><label class="student-search">查找学生<input ref="studentSearchInput" v-model="studentQuery" type="search" placeholder="输入学生姓名"></label><div class="student-picker__grid"><label v-for="student in pickerStudents" :key="student.subject_id"><input v-model="selectedSubjectIds" type="checkbox" :value="student.subject_id"><span>{{ student.display_name }}<small>{{ student.class_label || '未分班' }}</small></span></label><p v-if="!pickerStudents.length">没有找到匹配学生。</p></div><footer><span>已选择 {{ selectedSubjectIds.length }} 人</span><button type="button" class="primary" @click="closeStudentPicker">完成</button></footer></section>
          </div>
        </section>

        <section v-else-if="plainText" class="content-block"><h3>AI 返回建议</h3><p>{{ plainText }}</p><p>该内容完整显示，由教师判断是否采用。</p></section>
        <p v-if="message && !recommendation" class="message" role="status">{{ message }}</p>
      </div>

      <footer v-if="plan || recommendation" class="decision-bar">
        <p v-if="recommendation">{{ selectedStepKeys.length ? `已选中 ${selectedStepKeys.length} 个步骤待调整` : '当前为已自动保存草稿' }}</p>
        <p v-else>当前为已自动保存草稿</p>
        <button v-if="recommendation" type="button" :disabled="busy || !feedback.trim() || !selectedStepKeys.length" @click="requestRevision">发送补充，更新方案</button>
        <button v-if="recommendation" class="primary" type="button" :disabled="busy || !selectedSubjectIds.length" @click="adoptRecommendation">最后确认，保存方案</button>
        <button v-else class="primary" type="button" :disabled="busy" @click="confirmPlan">最后确认，写入工作图与日历</button>
        <button type="button" class="discard" :disabled="busy" @click="discardDraft">放弃草稿</button>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.draft-workspace{min-height:560px;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);overflow:hidden}.draft-head{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:var(--space-5);padding:var(--space-5);border-bottom:1px solid var(--color-border-default);background:linear-gradient(100deg,var(--color-bg-subtle),var(--color-bg-surface))}.draft-head p,.draft-head h2{margin:0}.draft-head p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}.draft-head h2{margin-top:3px}.draft-head>span{padding:var(--space-1) var(--space-2);border-radius:var(--radius-tag);background:var(--color-accent-subtle);color:var(--color-accent);font-weight:700}.back{border:0;background:transparent;color:var(--color-text-secondary)}.draft-context{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--color-border-subtle);border-bottom:1px solid var(--color-border-default)}.draft-context div{display:grid;gap:3px;padding:var(--space-3) var(--space-5);background:var(--color-bg-surface)}.draft-context span,.block-title>span,.state{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.content-block{padding:var(--space-5)}.block-title{display:flex;align-items:end;justify-content:space-between;gap:var(--space-4);margin:var(--space-5) 0 var(--space-3)}.block-title p,.block-title h3{margin:0}.block-title p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.06em}.summary{font-size:var(--font-size-body);line-height:1.7}.model-advice,.notice,.revision,.anonymous-preview{margin:var(--space-4) 0;padding:var(--space-4);border-left:4px solid var(--color-accent);background:var(--color-bg-subtle)}.model-advice strong{display:block;margin-bottom:var(--space-2)}.notice{border-color:var(--color-warning);background:var(--color-warning-subtle)}.flow-track{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:var(--space-3);margin:0;padding:0;list-style:none}.flow-track li{min-width:0}.flow-track button,.flow-track--ordinary li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:var(--space-3);width:100%;height:100%;min-height:150px;padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);text-align:left}.flow-track button:hover{border-color:var(--color-accent)}.flow-track button.selected{border:2px solid var(--color-warning);background:var(--color-warning-subtle)}.index{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--color-accent-subtle);color:var(--color-accent);font-weight:800}.flow-track strong{line-height:1.45}.flow-track time{margin-left:var(--space-1);color:var(--color-text-secondary);font-size:var(--font-size-caption);font-weight:500;white-space:nowrap}.flow-track p{margin:var(--space-2) 0;color:var(--color-text-secondary);line-height:1.55}.flow-track em{color:var(--color-danger);font-style:normal;font-size:var(--font-size-caption);font-weight:700}.student-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-2);margin:var(--space-5) 0;padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.student-links legend,.student-links>p{grid-column:1/-1}.student-links label{display:flex;align-items:center;gap:var(--space-2)}.actions{display:flex;gap:var(--space-2)}button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.primary{border-color:var(--color-accent);background:var(--color-accent);color:white}.revision label{display:grid;gap:var(--space-2)}.revision textarea{box-sizing:border-box;width:100%;padding:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);font:inherit;resize:vertical}.message,.error,.state{margin:var(--space-4) var(--space-5)}.error{color:var(--color-danger)}
@media(max-width:800px){.draft-head{grid-template-columns:1fr}.draft-context{grid-template-columns:1fr}.block-title{align-items:start;flex-direction:column}.flow-track{grid-template-columns:1fr}}
@media(prefers-reduced-motion:no-preference){.draft-workspace{animation:draft-enter .24s ease-out both}@keyframes draft-enter{from{opacity:.25;transform:translateX(28px)}to{opacity:1;transform:none}}}
.draft-workspace{display:grid;grid-template-rows:auto minmax(0,1fr) auto;height:calc(100vh - 64px);min-height:620px;overflow:hidden}.draft-head{grid-template-columns:auto minmax(0,1fr);gap:var(--space-4);padding:var(--space-3) var(--space-5);background:var(--color-bg-surface)}.draft-head h2{display:flex;align-items:baseline;gap:var(--space-3);min-width:0;margin:0;font-size:var(--font-size-h3)}.draft-head h2>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.draft-head h2 small{flex:0 0 auto;color:var(--color-text-secondary);font-size:var(--font-size-caption);font-weight:500}.back{min-height:34px;padding-inline:0;cursor:pointer}.draft-body{min-height:0;overflow-y:auto;overscroll-behavior:contain}.content-block{padding:var(--space-4) var(--space-5) var(--space-6)}.block-title{margin:var(--space-4) 0 var(--space-3)}.ai-details,.student-links,.revision{margin:0 0 var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-subtle)}.ai-details>summary,.student-links>summary,.revision>summary{padding:var(--space-3) var(--space-4);color:var(--color-text-secondary);font-size:var(--font-size-dense);font-weight:650;cursor:pointer}.ai-details[open],.student-links[open],.revision[open]{padding-bottom:var(--space-3)}.ai-details[open]>:not(summary),.revision[open]>:not(summary){margin-inline:var(--space-4)}.call-details{color:var(--color-text-muted);font-size:var(--font-size-caption)}.notice--emergency{margin-top:0;border-left-width:5px;border-color:var(--color-danger);background:var(--color-danger-subtle)}.timeline{display:grid;gap:var(--space-1)}.timeline-day{position:relative;display:grid;grid-template-columns:112px minmax(0,1fr);gap:var(--space-4);padding:var(--space-3) 0}.timeline-day::before{position:absolute;top:0;bottom:0;left:105px;width:1px;background:var(--color-border-default);content:''}.timeline-day>header{position:relative;z-index:1;display:grid;align-content:start;justify-items:start;gap:2px;padding-top:var(--space-2);background:var(--color-bg-surface)}.timeline-day>header time{color:var(--color-accent-active);font-size:var(--font-size-dense);font-weight:750}.timeline-day>header span{color:var(--color-text-muted);font-size:var(--font-size-caption)}.timeline .flow-track{display:grid;grid-template-columns:1fr;gap:var(--space-2)}.timeline .flow-track button,.timeline .flow-track--ordinary li{min-height:0;padding:var(--space-3);border-radius:var(--radius-control)}.timeline .flow-track p{margin:var(--space-1) 0}.timeline .flow-track button.root{box-shadow:inset 4px 0 0 var(--color-warning)}.student-links{display:block;padding:0}.student-links>summary{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);list-style-position:inside}.student-links>summary span{color:var(--color-accent)}.student-links__grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-2);padding:0 var(--space-4) var(--space-3)}.student-links__grid>p{grid-column:1/-1;margin:0;color:var(--color-text-secondary);font-size:var(--font-size-dense)}.student-links__grid label{display:flex;align-items:center;gap:var(--space-2)}.revision{padding:0;border-left:1px solid var(--color-border-default)}.revision label{display:grid;gap:var(--space-2)}.revision textarea{min-height:88px}.anonymous-preview{border-left-color:var(--color-warning)}.decision-bar{z-index:3;display:flex;align-items:center;justify-content:flex-end;gap:var(--space-2);padding:var(--space-3) var(--space-5);border-top:1px solid var(--color-border-default);background:color-mix(in srgb,var(--color-bg-surface) 94%,transparent);box-shadow:0 -8px 20px rgb(15 32 42 / 7%);backdrop-filter:blur(10px)}.decision-bar p{margin:0 auto 0 0;color:var(--color-text-secondary);font-size:var(--font-size-dense)}.decision-bar .discard{border-color:transparent;background:transparent;color:var(--color-text-secondary)}
@media(max-width:800px){.draft-workspace{height:calc(100vh - 24px);min-height:540px}.draft-head{grid-template-columns:1fr;gap:var(--space-2);padding:var(--space-3)}.draft-head h2{align-items:flex-start;flex-direction:column;gap:2px}.draft-head h2>span{white-space:normal}.content-block{padding:var(--space-3)}.timeline-day{grid-template-columns:1fr;gap:var(--space-2)}.timeline-day::before{display:none}.timeline-day>header{display:flex;align-items:baseline;gap:var(--space-2);padding-top:0}.decision-bar{align-items:stretch;flex-direction:column;padding:var(--space-3)}.decision-bar p{margin:0}.decision-bar button{width:100%}}
.content-block--affair{padding:var(--space-3) var(--space-4) var(--space-5)}
.content-block--affair>.notice--emergency{margin:0 0 var(--space-3);padding:var(--space-3) var(--space-4)}
.content-block--affair>.notice--emergency p{display:inline;margin:0 0 0 var(--space-2)}
.affair-cockpit{display:grid;grid-template-columns:220px minmax(620px,1fr) 290px;gap:var(--space-3);align-items:start}
.case-brief,.flow-canvas,.step-inspector{min-width:0;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}
.case-brief,.step-inspector{position:sticky;top:0;padding:var(--space-4)}
.case-brief .eyebrow,.step-inspector .eyebrow{margin:0 0 var(--space-1);color:var(--color-accent);font-size:var(--font-size-caption);font-weight:750;letter-spacing:.08em}
.case-brief h3,.step-inspector h3{margin:0 0 var(--space-2);font-size:var(--font-size-h3)}
.case-summary,.step-inspector>p{margin:0 0 var(--space-3);color:var(--color-text-secondary);font-size:var(--font-size-dense);line-height:1.6}
.linked-heading{display:flex;align-items:center;justify-content:space-between;gap:var(--space-2);padding-top:var(--space-3);border-top:1px solid var(--color-border-subtle)}
.text-button{min-height:auto;padding:0;border:0;background:transparent;color:var(--color-accent);font-weight:700}
.linked-students{display:grid;gap:var(--space-2);margin-top:var(--space-2)}
.linked-students>p{margin:0;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.student-chip{display:grid;grid-template-columns:32px 1fr;gap:var(--space-2);align-items:center;padding:var(--space-2);border:1px solid var(--color-border-subtle);border-radius:var(--radius-control);background:var(--color-bg-subtle)}
.student-chip>span{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:var(--color-accent-subtle);color:var(--color-accent);font-weight:800}
.student-chip strong,.student-chip small{display:block}.student-chip small{color:var(--color-text-secondary);font-size:var(--font-size-caption)}
.case-brief .ai-details{margin:var(--space-3) 0 0}.case-brief .ai-details>summary{padding:var(--space-2)}.case-brief .ai-details[open]>:not(summary){margin-inline:var(--space-2);font-size:var(--font-size-caption)}
.flow-canvas{padding:var(--space-4);overflow:hidden}.flow-canvas .block-title{margin:0 0 var(--space-3)}
.flow-columns{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(220px,1fr);gap:var(--space-4);overflow-x:auto;padding:0 2px var(--space-2);scrollbar-gutter:stable}
.flow-phase{position:relative;min-width:0}.flow-phase:not(:last-child)::after{position:absolute;top:51px;right:calc(-1 * var(--space-4));width:var(--space-4);height:2px;background:var(--color-accent-subtle);content:''}
.flow-phase>header{display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-2);margin-bottom:var(--space-2);color:var(--color-text-secondary);font-size:var(--font-size-caption)}
.flow-phase>header time{color:var(--color-accent-active);font-weight:750}.flow-phase>ol{display:grid;gap:var(--space-2);margin:0;padding:0;list-style:none}
.flow-phase li{display:grid;gap:var(--space-2)}.flow-phase button{display:grid;grid-template-columns:auto minmax(0,1fr);gap:var(--space-2);width:100%;min-height:104px;padding:var(--space-3);text-align:left}
.flow-phase button strong{display:block;line-height:1.4}.flow-phase button p{margin:var(--space-1) 0 0;color:var(--color-text-secondary);font-size:var(--font-size-dense);line-height:1.5}.flow-phase button em{display:block;margin-top:var(--space-1);color:var(--color-danger);font-size:var(--font-size-caption);font-style:normal;font-weight:700}
.flow-phase button.selected{border-color:var(--color-warning);background:var(--color-warning-subtle)}.flow-phase button.root{box-shadow:inset 4px 0 0 var(--color-warning)}
.flow-question{display:grid;grid-template-columns:auto 1fr;gap:var(--space-2);padding:var(--space-2);border:1px solid color-mix(in srgb,var(--color-warning) 35%,var(--color-border-default));border-radius:var(--radius-control);background:var(--color-warning-subtle);color:var(--color-text-primary);font-size:var(--font-size-caption);line-height:1.45}.flow-question b{color:var(--color-warning);white-space:nowrap}
.step-inspector{display:grid;gap:var(--space-2)}.step-inspector h3,.step-inspector>p{margin-bottom:0}.revision-label{display:grid;gap:var(--space-2);margin-top:var(--space-2);font-size:var(--font-size-dense);font-weight:700}.revision-label textarea{box-sizing:border-box;width:100%;min-height:112px;padding:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);font:inherit;line-height:1.55;resize:vertical}.revision-label textarea:focus{outline:2px solid var(--color-focus-ring);outline-offset:1px}
.revision-help{font-size:var(--font-size-caption)!important}.inspector-question{display:grid;gap:var(--space-1);padding:var(--space-3);border-left:3px solid var(--color-warning);background:var(--color-warning-subtle);font-size:var(--font-size-caption)}.inspector-question span{line-height:1.45}
.operation-status{margin:0!important;padding:var(--space-2);border-radius:var(--radius-control);background:var(--color-accent-subtle);color:var(--color-accent-active);font-size:var(--font-size-caption);line-height:1.45}.operation-status.busy{font-weight:700}
.student-picker{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:var(--space-5);background:rgb(20 38 44 / 42%)}.student-picker>section{display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;width:min(760px,100%);max-height:min(680px,calc(100vh - 48px));border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);box-shadow:0 18px 48px rgb(15 32 42 / 18%)}.student-picker header,.student-picker footer{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);padding:var(--space-4);border-bottom:1px solid var(--color-border-default)}.student-picker header div{display:grid;gap:2px}.student-picker header span,.student-picker footer>span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.student-picker footer{border-top:1px solid var(--color-border-default);border-bottom:0}.student-search{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-4);border-bottom:1px solid var(--color-border-subtle)}.student-search input{min-height:40px;padding:0 var(--space-3);font:inherit;border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.student-picker__grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-2);overflow:auto;padding:var(--space-4)}.student-picker__grid label{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-2);border:1px solid var(--color-border-subtle);border-radius:var(--radius-control)}.student-picker__grid label span,.student-picker__grid small{display:block}.student-picker__grid small{color:var(--color-text-secondary);font-size:var(--font-size-caption)}
@media(max-width:1250px){.affair-cockpit{grid-template-columns:200px minmax(540px,1fr) 260px}}
@media(max-width:1000px){.affair-cockpit{grid-template-columns:210px minmax(0,1fr)}.step-inspector{position:static;grid-column:1/-1}.flow-columns{grid-auto-columns:minmax(210px,260px)}}
@media(max-width:760px){.affair-cockpit{grid-template-columns:1fr}.case-brief,.step-inspector{position:static}.flow-columns{grid-auto-columns:minmax(220px,80vw)}.student-picker__grid{grid-template-columns:1fr 1fr}}
</style>
