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
const pendingPreview = ref<HomeIntakePreview | null>(null)
const roster = ref<CurrentRosterStudent[]>([])
const selectedSubjectIds = ref<string[]>([])
const revisionSection = ref<HTMLDetailsElement | null>(null)
const draftHeading = ref<HTMLElement | null>(null)

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

function openRevision(): void {
  void nextTick(() => {
    if (revisionSection.value) revisionSection.value.open = true
    revisionSection.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    revisionSection.value?.querySelector<HTMLTextAreaElement>('textarea')?.focus()
  })
}

function sourceText(value: HomeIntakeOperation | null): string {
  const raw = value?.local_context.source_text
  return typeof raw === 'string' ? raw : ''
}

async function loadRoster(value: HomeIntakeOperation): Promise<void> {
  if (!recommendation.value) return
  try {
    const current = await studentR1Api.currentRoster(props.token)
    roster.value = current.items.filter((item) => item.state === 'active')
  } catch {
    roster.value = []
    return
  }
  const names = new Map<string, number>()
  for (const item of roster.value) names.set(item.display_name, (names.get(item.display_name) || 0) + 1)
  const original = sourceText(value)
  selectedSubjectIds.value = roster.value
    .filter((item) => names.get(item.display_name) === 1 && original.includes(item.display_name))
    .map((item) => item.subject_id)
}

async function loadDraft(focusHeading = false): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    draft.value = await homeIntakeApi.getDraft(props.draftId, props.token)
    selectedRootStepKey.value = null
    pendingPreview.value = null
    feedback.value = ''
    await loadRoster(draft.value.operation)
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
      await loadDraft()
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

async function requestRevision(): Promise<void> {
  if (!operation.value || !feedback.value.trim() || !selectedStepKeys.value.length || busy.value) return
  busy.value = true
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
    if (next.route === 'ordinary' && next.dispatch_ready) await dispatchRevision(next)
    else if (next.dispatch_ready) {
      pendingPreview.value = next
      message.value = '补充内容的匿名发送预览已准备；确认后才会请求 AI 调整。'
    } else {
      message.value = '补充内容尚不能发送，上一版方案仍保留。'
    }
  } catch {
    message.value = '调整预览没有准备成功，上一版方案仍保留。'
  } finally {
    busy.value = false
  }
}

async function confirmRevisionDispatch(): Promise<void> {
  if (!pendingPreview.value || busy.value) return
  busy.value = true
  const value = pendingPreview.value
  pendingPreview.value = null
  try {
    await dispatchRevision(value)
  } finally {
    busy.value = false
  }
}

async function confirmPlan(): Promise<void> {
  if (!operation.value || !plan.value || busy.value) return
  busy.value = true
  message.value = ''
  try {
    await homeIntakeApi.adoptOrdinaryDraft(operation.value, props.token)
    await props.module.load('today')
    emit('completed')
  } catch {
    message.value = '正式保存尚未全部确认；再次点击会沿用同一写入编号续做，不会重新请求 AI。'
  } finally {
    busy.value = false
  }
}

async function adoptRecommendation(): Promise<void> {
  if (!operation.value || !recommendation.value || !selectedSubjectIds.value.length || busy.value) return
  busy.value = true
  message.value = ''
  try {
    const stableId = `adopt-${operation.value.operation_id}`.slice(0, 128)
    await homeIntakeApi.adopt(operation.value, stableId, selectedSubjectIds.value, props.token)
    await props.module.load('today')
    emit('completed')
  } catch {
    message.value = '正式保存尚未全部确认；再次点击只会续做未完成阶段，不会重复请求 AI。'
  } finally {
    busy.value = false
  }
}

async function discardDraft(): Promise<void> {
  if (!draft.value || busy.value) return
  busy.value = true
  try {
    await homeIntakeApi.discardDraft(draft.value.draft_id, draft.value.version, props.token)
    emit('close')
  } catch {
    message.value = '草稿已经变化或暂时无法放弃，请刷新后再试。'
  } finally {
    busy.value = false
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

        <section v-else-if="recommendation" class="content-block">
          <section v-if="recommendation.emergency_prompt" class="notice notice--emergency" role="alert"><strong>优先提醒</strong><p>{{ recommendation.emergency_prompt }}</p></section>
          <details class="ai-details">
            <summary>查看 AI 说明与调用详情</summary>
            <p>{{ recommendation.summary || '以下是基于当前信息形成的初步方案。' }}</p>
            <p v-if="recommendation.model_advice"><strong>AI 原始建议</strong><br>{{ recommendation.model_advice }}</p>
            <ul v-if="recommendation.assumptions.length"><li v-for="item in recommendation.assumptions" :key="item">{{ item }}</li></ul>
            <p class="call-details">技术状态：{{ operationStateLabel(operation.state) }} · 第 {{ operation.round_number }} 轮 · 本轮 {{ operation.round_physical_request_count }} 次请求 · 累计 {{ operation.cumulative_physical_request_count }} 次</p>
          </details>

          <div class="block-title"><div><p>流程轨迹</p><h3>SOP 与日期安排</h3></div><span>点击一步，即标记它和全部后续步骤</span></div>
          <div class="timeline">
            <section v-for="group in timelineGroups" :key="group.date" class="timeline-day">
              <header><time>{{ group.date }}</time><span>{{ group.steps.length }} 项</span></header>
              <ol class="flow-track">
                <li v-for="item in group.steps" :key="item.step.key">
                  <button type="button" :class="{ selected: selectedStepKeys.includes(item.step.key), root: selectedRootStepKey === item.step.key }" :aria-pressed="selectedStepKeys.includes(item.step.key)" @click="toggleStep(item.step.key)">
                    <span class="index">{{ item.index + 1 }}</span><div><strong>{{ item.step.title }} <time>{{ group.date }}</time></strong><p>{{ item.step.details }}</p><em v-if="item.step.safety_required">关键步骤</em></div>
                  </button>
                </li>
              </ol>
            </section>
          </div>

          <section v-if="recommendation.to_verify.length" class="notice"><strong>后续核对，不阻止先采用方案</strong><ul><li v-for="item in recommendation.to_verify" :key="item">{{ item }}</li></ul></section>

          <details class="student-links">
            <summary><strong>已关联 {{ selectedSubjectIds.length }} 人</strong><span>修改关联学生</span></summary>
            <div class="student-links__grid">
              <p>当前我班名单中姓名唯一匹配时已自动勾选；请由教师最终核对。</p>
              <label v-for="student in roster" :key="student.subject_id"><input v-model="selectedSubjectIds" type="checkbox" :value="student.subject_id">{{ student.display_name }} · {{ student.class_label || '未分班' }}</label>
              <p v-if="!roster.length">当前没有可关联的“我班学生”，请先到学生目录设置。</p>
            </div>
          </details>

          <details ref="revisionSection" class="revision">
            <summary>调整选中步骤</summary>
            <p>点击新卡片会更换调整起点；再点当前起点可清除。未选内容保持不变。</p>
            <label>补充情况或说明不合适的原因<textarea v-model="feedback" rows="3" maxlength="4000"></textarea></label>
            <button type="button" :disabled="busy || !feedback.trim() || !selectedStepKeys.length" @click="requestRevision">发送标记和补充，生成新版本</button>
          </details>

          <section v-if="pendingPreview" class="anonymous-preview"><strong>补充内容已完成匿名处理</strong><p>身份信息仍只在本机；确认后调用一次 AI，新版本成功前不会替换现有草稿。</p><button type="button" :disabled="busy" @click="confirmRevisionDispatch">确认匿名发送</button></section>
        </section>

        <section v-else-if="plainText" class="content-block"><h3>AI 返回建议</h3><p>{{ plainText }}</p><p>该内容完整显示，由教师判断是否采用。</p></section>
        <p v-if="message" class="message" role="status">{{ message }}</p>
      </div>

      <footer v-if="plan || recommendation" class="decision-bar">
        <p v-if="recommendation">{{ selectedStepKeys.length ? `已选中 ${selectedStepKeys.length} 个步骤待调整` : '当前为已自动保存草稿' }}</p>
        <p v-else>当前为已自动保存草稿</p>
        <button v-if="recommendation" type="button" :disabled="busy || !selectedStepKeys.length" @click="openRevision">调整选中步骤</button>
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
</style>
