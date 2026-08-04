<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

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

const operation = computed(() => draft.value?.operation ?? null)
const plan = computed(() => operation.value?.result?.kind === 'ordinary_plan' ? operation.value.result.value : null)
const recommendation = computed<HomeIntakeRecommendation | null>(() => {
  const result = operation.value?.result
  return result?.kind === 'affair_recommendation' || result?.kind === 'student_support_recommendation'
    ? result.value
    : null
})
const plainText = computed(() => operation.value?.result?.kind === 'plain_text' ? operation.value.result.value.text : '')

const selectedStepKeys = computed(() => {
  if (!recommendation.value || !selectedRootStepKey.value) return []
  const children = new Map<string, string[]>()
  for (const step of recommendation.value.steps) {
    for (const parent of step.depends_on) children.set(parent, [...(children.get(parent) || []), step.key])
  }
  const selected = new Set([selectedRootStepKey.value])
  const queue = [selectedRootStepKey.value]
  while (queue.length) {
    for (const child of children.get(queue.shift() as string) || []) {
      if (!selected.has(child)) {
        selected.add(child)
        queue.push(child)
      }
    }
  }
  return recommendation.value.steps.filter((step) => selected.has(step.key)).map((step) => step.key)
})

const selectedCalendarKeys = computed(() => {
  if (!recommendation.value) return []
  const selected = new Set(selectedStepKeys.value)
  return recommendation.value.calendar_items
    .filter((item) => selected.has(item.step_key))
    .map((item) => item.key)
})

const stepDates = computed(() => {
  const dates = new Map<string, string>()
  for (const item of recommendation.value?.calendar_items || []) {
    if (item.due_date && !dates.has(item.step_key)) dates.set(item.step_key, item.due_date)
  }
  return dates
})

function toggleStep(key: string): void {
  selectedRootStepKey.value = selectedRootStepKey.value === key ? null : key
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

async function loadDraft(): Promise<void> {
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

onMounted(() => { void loadDraft() })
watch(() => props.draftId, () => { void loadDraft() })
</script>

<template>
  <section class="draft-workspace" :aria-busy="loading || busy">
    <header class="draft-head">
      <button type="button" class="back" @click="emit('close')">← 返回事务列表</button>
      <div>
        <p>AI 事务草稿 · 自动保存</p>
        <h2>{{ recommendation?.title || plan?.nodes[0]?.title || '事务处理方案' }}</h2>
      </div>
      <span v-if="draft">第 {{ draft.version }} 版</span>
    </header>

    <p v-if="loading" class="state">正在恢复草稿…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }}</p>

    <template v-else-if="draft && operation">
      <section class="draft-context">
        <div><strong>当前状态</strong><span>草稿已保存，最后确认前不会写入正式学生档案或日历</span></div>
        <div><strong>AI 建议由教师判断</strong><span>页面完整展示，不按建议内容拦截</span></div>
      </section>

      <section v-if="plan" class="content-block">
        <div class="block-title"><div><p>普通工作事务</p><h3>执行流程与日期</h3></div><span>同一天可以推进多个步骤</span></div>
        <ul v-if="plan.assumptions.length" class="assumptions"><li v-for="item in plan.assumptions" :key="item">{{ item }}</li></ul>
        <ol class="flow-track flow-track--ordinary">
          <li v-for="(node, index) in plan.nodes" :key="node.draft_key">
            <span class="index">{{ index + 1 }}</span>
            <div><strong>{{ node.title }} <time>{{ node.due_date || '日期待定' }}</time></strong><p>{{ node.details || node.rationale || '按实际进展完成此步骤。' }}</p></div>
          </li>
        </ol>
        <button class="primary" type="button" :disabled="busy" @click="confirmPlan">最后确认，写入工作图与日历</button>
      </section>

      <section v-else-if="recommendation" class="content-block">
        <p class="summary">{{ recommendation.summary || '以下是基于当前信息形成的初步方案。' }}</p>
        <p v-if="recommendation.model_advice" class="model-advice"><strong>AI 原始建议</strong>{{ recommendation.model_advice }}</p>
        <section v-if="recommendation.emergency_prompt" class="notice"><strong>优先提醒</strong><p>{{ recommendation.emergency_prompt }}</p></section>

        <div class="block-title"><div><p>流程轨迹</p><h3>SOP 与日期合并安排</h3></div><span>点击某一步，将标记它和全部后续步骤</span></div>
        <ol class="flow-track">
          <li v-for="(step, index) in recommendation.steps" :key="step.key">
            <button type="button" :class="{ selected: selectedStepKeys.includes(step.key) }" :aria-pressed="selectedStepKeys.includes(step.key)" @click="toggleStep(step.key)">
              <span class="index">{{ index + 1 }}</span>
              <div>
                <strong>{{ step.title }} <time>{{ stepDates.get(step.key) || '日期待定' }}</time></strong>
                <p>{{ step.details }}</p>
                <em v-if="step.safety_required">关键步骤</em>
              </div>
            </button>
          </li>
        </ol>

        <section v-if="recommendation.to_verify.length" class="notice"><strong>后续核对，不阻止先采用方案</strong><ul><li v-for="item in recommendation.to_verify" :key="item">{{ item }}</li></ul></section>

        <fieldset class="student-links">
          <legend>最后保存时关联到学生档案</legend>
          <p>当前我班名单中姓名唯一匹配时已自动勾选；请由教师最终核对。</p>
          <label v-for="student in roster" :key="student.subject_id"><input v-model="selectedSubjectIds" type="checkbox" :value="student.subject_id">{{ student.display_name }} · {{ student.class_label || '未分班' }}</label>
          <p v-if="!roster.length">当前没有可关联的“我班学生”，请先到学生目录设置。</p>
        </fieldset>

        <div class="actions"><button class="primary" type="button" :disabled="busy || !selectedSubjectIds.length" @click="adoptRecommendation">最后确认，保存方案</button><button type="button" :disabled="busy" @click="discardDraft">放弃草稿</button></div>

        <section class="revision">
          <h3>让 AI 只调整标记部分</h3>
          <p>点击新的卡片会直接更换调整起点；再次点击当前起点可清除选择。未选内容保持不变。</p>
          <label>补充情况或说明不合适的原因<textarea v-model="feedback" rows="4" maxlength="4000"></textarea></label>
          <button type="button" :disabled="busy || !feedback.trim() || !selectedStepKeys.length" @click="requestRevision">发送标记和补充，生成新版本</button>
        </section>

        <section v-if="pendingPreview" class="anonymous-preview">
          <strong>补充内容已完成匿名处理</strong>
          <p>身份信息仍只在本机；确认后调用一次 AI，现有草稿在新版本成功前不会被替换。</p>
          <button type="button" :disabled="busy" @click="confirmRevisionDispatch">确认匿名发送</button>
        </section>
      </section>

      <section v-else-if="plainText" class="content-block"><h3>AI 返回建议</h3><p>{{ plainText }}</p><p>该内容完整显示，由教师判断是否采用。</p></section>
      <p v-if="message" class="message" role="status">{{ message }}</p>
    </template>
  </section>
</template>

<style scoped>
.draft-workspace{min-height:560px;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);overflow:hidden}.draft-head{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:var(--space-5);padding:var(--space-5);border-bottom:1px solid var(--color-border-default);background:linear-gradient(100deg,var(--color-bg-subtle),var(--color-bg-surface))}.draft-head p,.draft-head h2{margin:0}.draft-head p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}.draft-head h2{margin-top:3px}.draft-head>span{padding:var(--space-1) var(--space-2);border-radius:var(--radius-tag);background:var(--color-accent-subtle);color:var(--color-accent);font-weight:700}.back{border:0;background:transparent;color:var(--color-text-secondary)}.draft-context{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--color-border-subtle);border-bottom:1px solid var(--color-border-default)}.draft-context div{display:grid;gap:3px;padding:var(--space-3) var(--space-5);background:var(--color-bg-surface)}.draft-context span,.block-title>span,.state{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.content-block{padding:var(--space-5)}.block-title{display:flex;align-items:end;justify-content:space-between;gap:var(--space-4);margin:var(--space-5) 0 var(--space-3)}.block-title p,.block-title h3{margin:0}.block-title p{color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.06em}.summary{font-size:var(--font-size-body);line-height:1.7}.model-advice,.notice,.revision,.anonymous-preview{margin:var(--space-4) 0;padding:var(--space-4);border-left:4px solid var(--color-accent);background:var(--color-bg-subtle)}.model-advice strong{display:block;margin-bottom:var(--space-2)}.notice{border-color:var(--color-warning);background:var(--color-warning-subtle)}.flow-track{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:var(--space-3);margin:0;padding:0;list-style:none}.flow-track li{min-width:0}.flow-track button,.flow-track--ordinary li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:var(--space-3);width:100%;height:100%;min-height:150px;padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);text-align:left}.flow-track button:hover{border-color:var(--color-accent)}.flow-track button.selected{border:2px solid var(--color-warning);background:var(--color-warning-subtle)}.index{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--color-accent-subtle);color:var(--color-accent);font-weight:800}.flow-track strong{line-height:1.45}.flow-track time{margin-left:var(--space-1);color:var(--color-text-secondary);font-size:var(--font-size-caption);font-weight:500;white-space:nowrap}.flow-track p{margin:var(--space-2) 0;color:var(--color-text-secondary);line-height:1.55}.flow-track em{color:var(--color-danger);font-style:normal;font-size:var(--font-size-caption);font-weight:700}.student-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-2);margin:var(--space-5) 0;padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.student-links legend,.student-links>p{grid-column:1/-1}.student-links label{display:flex;align-items:center;gap:var(--space-2)}.actions{display:flex;gap:var(--space-2)}button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.primary{border-color:var(--color-accent);background:var(--color-accent);color:white}.revision label{display:grid;gap:var(--space-2)}.revision textarea{box-sizing:border-box;width:100%;padding:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);font:inherit;resize:vertical}.message,.error,.state{margin:var(--space-4) var(--space-5)}.error{color:var(--color-danger)}
@media(max-width:800px){.draft-head{grid-template-columns:1fr}.draft-context{grid-template-columns:1fr}.block-title{align-items:start;flex-direction:column}.flow-track{grid-template-columns:1fr}}
@media(prefers-reduced-motion:no-preference){.draft-workspace{animation:draft-enter .24s ease-out both}@keyframes draft-enter{from{opacity:.25;transform:translateX(28px)}to{opacity:1;transform:none}}}
</style>
