<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import {
  homeIntakeApi,
  type HomeIntakeHandoff,
  type HomeIntakeOperation,
  type HomeIntakePreview,
  type HomeIntakeRecommendation,
} from '../api/homeIntake'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'

const props = defineProps<{ module: OrdinaryWorkModule; token?: string }>()
const emit = defineEmits<{ handoff: [value: HomeIntakeHandoff] }>()
const text = ref('')
const submittedSourceText = ref('')
const preview = ref<HomeIntakePreview | null>(null)
const operation = ref<HomeIntakeOperation | null>(null)
const busy = ref(false)
const message = ref('')
const requestOperationId = ref('')
const manualFallbackOperationId = ref('')
const submittedManualTitle = ref('')
const followUpAnswer = ref('')
const manualTitle = ref('')
const statusRegion = ref<HTMLElement | null>(null)

const unresolvedOperation = computed(() => ['result_unknown', 'in_progress'].includes(operation.value?.state ?? ''))
const unresolvedRequest = computed(() => Boolean(requestOperationId.value) && (!operation.value || unresolvedOperation.value))
const unresolvedManualFallback = computed(() => Boolean(manualFallbackOperationId.value))
const sourceLocked = computed(() => busy.value || unresolvedRequest.value || unresolvedManualFallback.value)
const canStart = computed(() => Boolean(text.value.trim()) && !busy.value && !requestOperationId.value && !manualFallbackOperationId.value)
const plan = computed(() => operation.value?.result?.kind === 'ordinary_plan' ? operation.value.result.value : null)
const recommendation = computed(() => {
  const result = operation.value?.result
  return result?.kind === 'affair_recommendation' || result?.kind === 'student_support_recommendation'
    ? result.value
    : null
})
const plainText = computed(() => operation.value?.result?.kind === 'plain_text' ? operation.value.result.value.text : '')
const canManualFallback = computed(() => operation.value?.route === 'ordinary' && [
  'unavailable', 'invalid_result', 'result_unknown', 'destination_changed', 'unsafe_output_suppressed', 'failed_before_send',
].includes(operation.value.state))
const canRetry = computed(() => Boolean(operation.value) && !unresolvedOperation.value && [
  'unavailable', 'invalid_result', 'destination_changed', 'unsafe_output_suppressed', 'failed_before_send',
].includes(operation.value?.state ?? ''))

function focusStatus(): void {
  void nextTick(() => statusRegion.value?.focus())
}

function resetResultAfterEdit(): void {
  if (sourceLocked.value) {
    text.value = submittedSourceText.value
    return
  }
  submittedSourceText.value = ''
  preview.value = null
  operation.value = null
  requestOperationId.value = ''
  manualFallbackOperationId.value = ''
  submittedManualTitle.value = ''
  followUpAnswer.value = ''
  manualTitle.value = text.value.slice(0, 240)
  message.value = ''
}

function clearAll(receipt: string): void {
  text.value = ''
  submittedSourceText.value = ''
  preview.value = null
  operation.value = null
  requestOperationId.value = ''
  manualFallbackOperationId.value = ''
  submittedManualTitle.value = ''
  followUpAnswer.value = ''
  manualTitle.value = ''
  message.value = receipt
  focusStatus()
}

function discard(): void {
  if (sourceLocked.value) return
  clearAll('已按教师选择放弃本次内容，没有写入任何工作区。')
}

function resultMessage(value: HomeIntakeOperation): string {
  const safeReason = value.validation_issue || value.assistant_message
  const reason = safeReason ? `；原因：${safeReason}` : ''
  if (value.state === 'succeeded') return value.result_kind === 'ordinary_plan'
    ? 'AI 已返回工作方案草案，请逐项核对后再确认写入。'
    : 'AI 已返回待教师判断的参考内容，尚未写入。'
  if (value.state === 'needs_information') return 'AI 只提出了追问。只有教师明确补充并提交，才会开始下一轮。'
  if (value.state === 'result_unknown') return `本轮结果暂不明确${reason}。只能查询同一操作，不能自动重发。`
  if (value.state === 'in_progress') return '同一操作仍在处理中。请只查询状态，不要另发一轮。'
  if (value.state === 'unavailable' || value.state === 'failed_before_send') return `模型当前不可用${reason}。原文仍保留。`
  if (value.state === 'invalid_result') return `AI 返回内容未通过校验${reason}，没有形成可写入草案。`
  if (value.state === 'destination_changed') return `发送前模型目的地发生变化${reason}，本轮没有继续发送。`
  if (value.state === 'unsafe_output_suppressed') return 'AI 输出触及安全边界，已被抑制，不会展示或写入。'
  return `本轮状态：${value.state}${reason}`
}

async function dispatchPreview(value: HomeIntakePreview): Promise<void> {
  if (!value.dispatch_ready || !value.fingerprint) return
  submittedSourceText.value = value.source_text ?? submittedSourceText.value
  requestOperationId.value = globalThis.crypto.randomUUID()
  try {
    operation.value = await homeIntakeApi.dispatch(value, requestOperationId.value, props.token)
    message.value = resultMessage(operation.value)
  } catch {
    message.value = '发送响应未能确认。原文仍保留；请查询同一操作，不要再次发送。'
  }
  focusStatus()
}

async function start(): Promise<void> {
  if (!canStart.value) return
  const sourceText = text.value
  submittedSourceText.value = sourceText
  busy.value = true
  message.value = ''
  operation.value = null
  requestOperationId.value = ''
  manualFallbackOperationId.value = ''
  submittedManualTitle.value = ''
  try {
    preview.value = await homeIntakeApi.preview(sourceText, props.token)
    submittedSourceText.value = preview.value.source_text ?? sourceText
    manualTitle.value = sourceText.slice(0, 240)
    if (preview.value.date_interpretation.status === 'conflict') {
      message.value = '原文中出现相互冲突的日期，请直接修改原文后再整理；本次没有发送模型请求。'
    } else if (preview.value.local_only) {
      message.value = '内容包含只能留在本机的类别，无法准备匿名发送；本次模型请求为 0。'
    } else if (!preview.value.dispatch_ready) {
      message.value = '本地预处理尚未达到可发送状态；本次模型请求为 0。'
    } else if (preview.value.route === 'ordinary') {
      await dispatchPreview(preview.value)
    } else {
      message.value = '匿名逐字预览已在本机准备；模型请求为 0。请先核对，再由教师确认发送。'
    }
  } catch {
    message.value = '本地整理接口暂时不可用。原文仍保留，没有确认任何模型发送。'
  } finally {
    busy.value = false
    focusStatus()
  }
}

async function confirmSensitiveDispatch(): Promise<void> {
  if (!preview.value || preview.value.route === 'ordinary' || busy.value) return
  busy.value = true
  try { await dispatchPreview(preview.value) } finally { busy.value = false }
}

async function querySameOperation(): Promise<void> {
  if (!requestOperationId.value || busy.value) return
  busy.value = true
  try {
    operation.value = await homeIntakeApi.status(requestOperationId.value, props.token)
    message.value = `已查询同一操作，没有追加发送。${resultMessage(operation.value)}`
  } catch {
    message.value = '同一操作状态暂时无法查询；没有追加发送，原文仍保留。'
  } finally { busy.value = false; focusStatus() }
}

async function retryWithNewOperation(): Promise<void> {
  if (!canRetry.value || busy.value) return
  submittedSourceText.value = ''
  preview.value = null
  operation.value = null
  requestOperationId.value = ''
  manualFallbackOperationId.value = ''
  submittedManualTitle.value = ''
  followUpAnswer.value = ''
  message.value = ''
  await start()
}

async function submitFollowUp(): Promise<void> {
  if (!operation.value?.can_follow_up || !followUpAnswer.value.trim() || busy.value) return
  busy.value = true
  try {
    preview.value = await homeIntakeApi.followUpPreview(operation.value.operation_id, followUpAnswer.value, props.token)
    submittedSourceText.value = preview.value.source_text ?? submittedSourceText.value
    operation.value = null
    requestOperationId.value = ''
    manualFallbackOperationId.value = ''
    submittedManualTitle.value = ''
    followUpAnswer.value = ''
    if (preview.value.route === 'ordinary' && preview.value.dispatch_ready) {
      await dispatchPreview(preview.value)
    } else if (preview.value.dispatch_ready) {
      message.value = '下一轮匿名逐字预览已准备；本轮模型请求仍为 0，请再次核对后确认发送。'
    } else {
      message.value = preview.value.local_only
        ? '补充内容只能留在本机，未发送模型请求。'
        : '补充内容仍有日期冲突或尚不可发送，未发送模型请求。'
    }
  } catch {
    message.value = '下一轮预览未能准备，原文和补充内容均未写入。'
  } finally { busy.value = false; focusStatus() }
}

async function confirmPlan(): Promise<void> {
  if (!operation.value || !plan.value || busy.value) return
  busy.value = true
  try {
    await homeIntakeApi.confirmPlan(operation.value, globalThis.crypto.randomUUID())
    const refreshed = await props.module.load('today')
    clearAll(refreshed
      ? '教师确认的工作方案已写入唯一工作图。'
      : '工作方案已经保存，只是列表暂未刷新；请稍后点击刷新，不要重复写入。')
  } catch {
    message.value = '工作图写入未确认。草案和原文仍保留，请不要假定已经写入。'
  } finally { busy.value = false; focusStatus() }
}

async function confirmManualFallback(): Promise<void> {
  if (!operation.value || !canManualFallback.value || busy.value) return
  if (!manualFallbackOperationId.value) {
    manualFallbackOperationId.value = globalThis.crypto.randomUUID()
    submittedManualTitle.value = manualTitle.value.trim()
  }
  busy.value = true
  try {
    await homeIntakeApi.confirmManualFallback(
      operation.value.operation_id,
      manualFallbackOperationId.value,
      submittedManualTitle.value || null,
      props.token,
    )
    const refreshed = await props.module.load('today')
    clearAll(refreshed
      ? '教师确认的单节点工作已写入工作图；本次兜底没有追加模型请求。'
      : '单节点工作已经保存，只是列表暂未刷新；本次兜底没有追加模型请求。')
  } catch {
    message.value = '单节点工作写入未确认。原文和本次写入编号仍保留；请先核对工作图，或再次点击同一按钮重试。'
  } finally { busy.value = false; focusStatus() }
}

function preserveManualTitle(): void {
  if (unresolvedManualFallback.value) manualTitle.value = submittedManualTitle.value
}

function handoff(): void {
  if (!operation.value || !recommendation.value) return
  const destination = operation.value.result_kind === 'affair_recommendation' ? 'affair' : 'student_support'
  emit('handoff', {
    id: globalThis.crypto.randomUUID(),
    destination,
    sourceText: submittedSourceText.value,
    aiReference: recommendation.value as HomeIntakeRecommendation,
    route: operation.value.route,
    interpretedDate: preview.value?.final_due_date ?? null,
  })
}
</script>

<template>
  <section class="capture" aria-label="首页 AI 整理">
    <div class="capture__input">
      <label for="home-intake-text">把今天想到的事交给 AI 整理</label>
      <textarea
        id="home-intake-text"
        v-model="text"
        rows="5"
        maxlength="4000"
        placeholder="例如：下周五前收齐家长会回执；或记录需要继续跟进的情况"
        :disabled="sourceLocked"
        @input="resetResultAfterEdit"
      ></textarea>
      <div class="capture__actions">
        <span>{{ text.length }}/4000</span>
        <button type="button" :disabled="!canStart" @click="start">{{ busy ? '正在整理…' : '交给 AI 整理' }}</button>
      </div>
    </div>

    <section v-if="preview || operation || message" ref="statusRegion" class="progress" tabindex="-1" aria-live="polite" :aria-busy="busy">
      <div v-if="preview" class="date-status" :data-state="preview.date_interpretation.status">
        <strong>本地日期理解：{{ preview.date_interpretation.status === 'resolved' ? '已明确' : preview.date_interpretation.status === 'conflict' ? '有冲突' : '日期待定' }}</strong>
        <span v-if="preview.date_interpretation.resolved_date">{{ preview.date_interpretation.resolved_date }}</span>
        <span v-else-if="preview.date_interpretation.candidates.length">候选：{{ preview.date_interpretation.candidates.join('、') }}</span>
        <span v-else>原文未提供可确定日期，后续可以在工作图中补充。</span>
      </div>

      <dl v-if="preview && !operation" class="receipt" aria-label="请求计数">
        <div><dt>逻辑轮次</dt><dd>第 {{ preview.round_number }} 轮</dd></div>
        <div><dt>本轮物理请求</dt><dd>{{ preview.round_physical_request_count }} 次</dd></div>
        <div><dt>累计物理请求</dt><dd>{{ preview.cumulative_physical_request_count }} 次</dd></div>
      </dl>

      <section v-if="preview?.emergency_guidance" class="emergency" role="alert">
        <h3>{{ preview.emergency_guidance.title }}</h3>
        <ol><li v-for="step in preview.emergency_guidance.steps" :key="step">{{ step }}</li></ol>
      </section>

      <section v-if="preview && preview.route !== 'ordinary' && preview.exact_payload && !operation" class="exact-preview">
        <p><strong>敏感内容匿名逐字预览</strong><span>草案，尚未写入 · 当前轮请求 0 次</span></p>
        <pre>{{ JSON.stringify(preview.exact_payload, null, 2) }}</pre>
        <p v-if="preview.removed_categories.length">已移除：{{ preview.removed_categories.join('、') }}</p>
        <p v-if="preview.student_aliases.length">匿名代号：{{ preview.student_aliases.join('、') }}</p>
        <dl class="destination-receipt" aria-label="本轮模型目的地">
          <div><dt>提供方</dt><dd>{{ preview.model_provider || '未提供' }}</dd></div>
          <div><dt>模型</dt><dd>{{ preview.model_name || '未提供' }}</dd></div>
          <div><dt>服务地址（不含凭据）</dt><dd>{{ preview.model_endpoint || '未提供' }}</dd></div>
          <div><dt>目的地指纹</dt><dd><code>{{ preview.destination_fingerprint || '未提供' }}</code></dd></div>
        </dl>
        <p class="destination-note">以上目的地与本次匿名逐字预览绑定；目的地变化后必须重新预览。</p>
        <button type="button" :disabled="busy" @click="confirmSensitiveDispatch">教师已核对，确认发送这一轮</button>
      </section>

      <template v-if="operation">
        <div class="draft-label">草案，尚未写入</div>
        <dl class="receipt">
          <div><dt>逻辑轮次</dt><dd>第 {{ operation.round_number }} 轮</dd></div>
          <div><dt>本轮物理请求</dt><dd>{{ operation.round_physical_request_count }} 次</dd></div>
          <div><dt>累计物理请求</dt><dd>{{ operation.cumulative_physical_request_count }} 次</dd></div>
        </dl>

        <section v-if="plan" class="plan">
          <h3>普通工作方案</h3>
          <p v-if="preview?.final_due_date">解释后的日期：{{ preview.final_due_date }}</p>
          <ul v-if="plan.assumptions.length" class="assumptions"><li v-for="item in plan.assumptions" :key="item">假设：{{ item }}</li></ul>
          <ol class="plan-nodes">
            <li v-for="node in plan.nodes" :key="node.draft_key">
              <div><strong>{{ node.title }}</strong><span>{{ node.kind }} · {{ node.status }} · {{ node.due_date || '日期待定' }}</span></div>
              <p v-if="node.details">具体说明：{{ node.details }}</p>
              <p v-if="node.rationale">安排理由：{{ node.rationale }}</p>
            </li>
          </ol>
          <div v-if="plan.edges.length" class="relations"><strong>节点关系</strong><ul><li v-for="edge in plan.edges" :key="`${edge.source_draft_key}-${edge.target_draft_key}-${edge.relation}`">{{ edge.source_draft_key }} → {{ edge.target_draft_key }}（{{ edge.relation }}）</li></ul></div>
          <button class="primary" type="button" :disabled="busy" @click="confirmPlan">教师确认，写入工作图</button>
        </section>

        <section v-else-if="recommendation" class="recommendation">
          <h3>{{ recommendation.kind === 'affair_recommendation' ? '事务处理建议' : '学生支持建议' }}</h3>
          <p>{{ recommendation.summary || 'AI 未提供摘要，请仅把现有内容作为参考。' }}</p>
          <ul v-if="recommendation.reasons.length"><li v-for="reason in recommendation.reasons" :key="reason">{{ reason }}</li></ul>
          <p v-if="recommendation.assumptions.length">假设：{{ recommendation.assumptions.join('；') }}</p>
          <p class="reference-note">AI 建议仅供参考，不会自动创建事务、学生记录或外发消息。</p>
          <button type="button" :disabled="busy" @click="handoff">{{ recommendation.kind === 'affair_recommendation' ? '转到事务创建预填' : '选择学生并预填支持记录' }}</button>
        </section>

        <section v-else-if="plainText" class="plain-text">
          <h3>安全纯文本说明</h3><p>{{ plainText }}</p><p>这段文字不会自动写入任何记录。</p>
        </section>

        <section v-if="operation.follow_up_questions.length || operation.can_follow_up" class="follow-up">
          <h3>明确开始下一轮</h3>
          <ul v-if="operation.follow_up_questions.length"><li v-for="question in operation.follow_up_questions" :key="question">{{ question }}</li></ul>
          <label>教师补充<textarea v-model="followUpAnswer" rows="3" maxlength="4000"></textarea></label>
          <button type="button" :disabled="busy || !followUpAnswer.trim()" @click="submitFollowUp">提交补充，开始下一轮</button>
        </section>

        <button v-if="unresolvedOperation || requestOperationId" v-show="unresolvedOperation || message.includes('响应未能确认')" type="button" :disabled="busy || !requestOperationId" @click="querySameOperation">只查询同一操作</button>
        <button v-if="canRetry" type="button" :disabled="busy" @click="retryWithNewOperation">重新整理一次（会发起新请求）</button>

        <section v-if="canManualFallback" class="fallback">
          <h3>不用 AI，保存一个普通工作节点</h3>
          <p>只会写入一个待办目标，不会追加模型请求。</p>
          <label>节点标题<input v-model="manualTitle" maxlength="240" :disabled="busy || unresolvedManualFallback" @input="preserveManualTitle"></label>
          <button type="button" :disabled="busy || !manualTitle.trim()" @click="confirmManualFallback">教师确认，写入单节点工作</button>
        </section>
      </template>

      <button v-if="!operation && requestOperationId" type="button" :disabled="busy" @click="querySameOperation">只查询同一操作</button>
      <p v-if="message" class="message" role="status">{{ message }}</p>
      <button v-if="text" class="discard" type="button" :disabled="sourceLocked" @click="discard">明确放弃本次内容</button>
    </section>
  </section>
</template>

<style scoped>
.capture{padding:var(--space-5);border-bottom:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.capture__input{display:grid;gap:var(--space-2)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}textarea,input,button{box-sizing:border-box;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.capture__input textarea{width:100%;min-height:128px;padding:var(--space-3);line-height:1.6;resize:vertical;overflow-wrap:anywhere}.capture__actions{display:flex;align-items:center;justify-content:flex-end;gap:var(--space-3)}.capture__actions span{color:var(--color-text-muted);font-size:var(--font-size-caption)}button{min-height:40px;padding:0 var(--space-3)}.capture__actions button,.primary{border-color:var(--color-accent);background:var(--color-accent);color:white}.progress{display:grid;gap:var(--space-3);margin-top:var(--space-4);outline:2px solid transparent;outline-offset:var(--space-1)}.progress:focus{outline-color:var(--color-accent)}.date-status,.receipt{display:flex;flex-wrap:wrap;gap:var(--space-2) var(--space-4);padding:var(--space-3);border-left:3px solid var(--color-accent);background:var(--color-bg-surface)}.date-status[data-state="conflict"]{border-color:var(--color-danger)}.date-status[data-state="pending"]{border-color:var(--color-warning)}.emergency{padding:var(--space-4);border:2px solid var(--color-danger);border-radius:var(--radius-control);background:var(--color-danger-subtle)}.emergency h3{margin-top:0}.exact-preview,.plan,.recommendation,.plain-text,.follow-up,.fallback{padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);overflow-wrap:anywhere}.exact-preview>p:first-child{display:flex;justify-content:space-between;gap:var(--space-3)}.exact-preview pre{max-height:320px;overflow:auto;padding:var(--space-3);white-space:pre-wrap;overflow-wrap:anywhere;background:var(--color-bg-subtle)}.destination-receipt{display:grid;gap:var(--space-2);margin:var(--space-3) 0;padding:var(--space-3);background:var(--color-bg-subtle)}.destination-receipt div{display:grid;grid-template-columns:minmax(7rem,auto) minmax(0,1fr);gap:var(--space-2)}.destination-receipt dt{color:var(--color-text-secondary)}.destination-receipt dd{min-width:0;margin:0;overflow-wrap:anywhere;font-weight:650}.destination-note{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.draft-label{width:max-content;padding:var(--space-1) var(--space-2);border:1px dashed var(--color-warning);border-radius:var(--radius-tag);color:var(--color-warning);font-weight:700}.receipt{margin:0}.receipt div{display:flex;gap:var(--space-1)}.receipt dt{color:var(--color-text-secondary)}.receipt dd{margin:0;font-weight:700}.plan h3,.recommendation h3,.plain-text h3,.follow-up h3,.fallback h3{margin-top:0}.plan-nodes{display:grid;gap:var(--space-3);padding-left:var(--space-5)}.plan-nodes li{padding:var(--space-3);border-left:3px solid var(--color-accent);background:var(--color-bg-subtle)}.plan-nodes div{display:flex;flex-wrap:wrap;justify-content:space-between;gap:var(--space-2)}.plan-nodes span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.plan-nodes p{margin-bottom:0;white-space:pre-wrap}.assumptions,.relations,.reference-note{color:var(--color-text-secondary)}.follow-up label,.fallback label{margin:var(--space-3) 0}.follow-up textarea,.fallback input{width:100%;padding:var(--space-2)}.message{margin:0;color:var(--color-accent-active);overflow-wrap:anywhere}.discard{justify-self:start;border-color:transparent;background:transparent;color:var(--color-text-secondary);text-decoration:underline}button:disabled{cursor:not-allowed;opacity:var(--opacity-disabled)}@media(max-width:700px){.exact-preview>p:first-child,.plan-nodes div{flex-direction:column}.capture{padding:var(--space-4)}}
</style>
