<script setup lang="ts">
import { computed, ref } from 'vue'

import { workApi, type WorkPlanPreview, type WorkPlanResult } from '../api/work'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'

const props = defineProps<{ module: OrdinaryWorkModule }>()
const text = ref('')
const dueDate = ref(new Date().toISOString().slice(0, 10))
const preview = ref<WorkPlanPreview | null>(null)
const result = ref<WorkPlanResult | null>(null)
const busy = ref(false)
const message = ref('')
const operationId = ref('')
const canDispatch = computed(() => preview.value?.model_enabled && preview.value.physical_request_count === 0)

function resetAfterEdit() {
  preview.value = null
  result.value = null
  operationId.value = ''
  message.value = ''
}

async function prepare() {
  if (!text.value.trim()) return
  busy.value = true; message.value = ''
  try {
    preview.value = await workApi.previewPlan(text.value, dueDate.value || null)
    result.value = null
    operationId.value = ''
  } catch {
    message.value = '匿名发送预览暂时无法生成；没有创建工作，也没有调用模型。'
  } finally { busy.value = false }
}

async function dispatchOnce() {
  if (!preview.value || !canDispatch.value) return
  busy.value = true
  operationId.value = globalThis.crypto.randomUUID()
  try {
    result.value = await workApi.invokePlan(preview.value, operationId.value)
    if (result.value.state === 'result_unknown') message.value = '结果状态未知；只能查询这次操作，不能再次发送。'
    else if (result.value.state === 'unavailable') message.value = '模型当前不可用；没有生成本地伪方案。'
  } catch {
    message.value = '本轮发送结果未知；请查询同一操作，不要追加请求。'
  } finally { busy.value = false }
}

async function querySameOperation() {
  if (!operationId.value) return
  busy.value = true
  try { result.value = await workApi.planStatus(operationId.value) }
  finally { busy.value = false }
}

async function confirmPlan() {
  if (!result.value?.plan || !result.value.plan_fingerprint) return
  busy.value = true
  try {
    await workApi.confirmPlan(result.value, globalThis.crypto.randomUUID())
    text.value = ''; preview.value = null; result.value = null; operationId.value = ''
    message.value = '教师确认的工作方案已写入唯一工作图。'
    await props.module.load('today')
  } finally { busy.value = false }
}
</script>

<template>
  <section class="capture" aria-label="一句话快速录入">
    <div class="capture__input">
      <label><span>一句话记下普通班务</span><input v-model="text" maxlength="800" placeholder="例如：周五前收齐家长会回执并汇总缺交名单" @input="resetAfterEdit"></label>
      <label class="date"><span>明确截止日期</span><input v-model="dueDate" type="date" @input="resetAfterEdit"></label>
      <button type="button" :disabled="busy || !text.trim()" @click="prepare">生成匿名发送预览</button>
    </div>
    <div v-if="preview" class="preview">
      <div><strong>发送前逐字核对</strong><span>最多 1 次物理请求 · 当前 {{ preview.physical_request_count }} 次</span></div>
      <pre>{{ JSON.stringify(preview.exact_payload, null, 2) }}</pre>
      <p v-if="!preview.model_enabled">真实模型当前关闭。预览已生成，但不会发送，也不会伪造本地 AI 结果。</p>
      <button type="button" :disabled="busy || !canDispatch" @click="dispatchOnce">确认只发送这一次</button>
    </div>
    <div v-if="result" class="proposal" :data-state="result.state">
      <strong>AI 草案 · 尚未进入正式工作图</strong>
      <p v-if="result.questions.length">还需教师补充：{{ result.questions.join('；') }}</p>
      <ol v-if="result.plan"><li v-for="node in result.plan.nodes" :key="node.draft_key">{{ node.title }} · {{ node.due_date || '日期待定' }}</li></ol>
      <button v-if="result.state==='result_unknown'" type="button" :disabled="busy" @click="querySameOperation">查询同一操作</button>
      <button v-if="result.state==='succeeded' && result.plan" type="button" :disabled="busy" @click="confirmPlan">教师确认，写入工作图</button>
    </div>
    <p v-if="message" class="message" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.capture{padding:var(--space-4) var(--space-5);border-bottom:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.capture__input{display:grid;grid-template-columns:minmax(280px,1fr) 180px auto;align-items:end;gap:var(--space-3)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}input,button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.capture__input>button{border-color:var(--color-accent);background:var(--color-accent);color:white}.preview,.proposal{margin-top:var(--space-3);padding:var(--space-3);border-left:3px solid var(--color-warning);background:var(--color-warning-subtle)}.preview>div{display:flex;justify-content:space-between}.preview span,.preview p{color:var(--color-text-secondary);font-size:var(--font-size-dense)}pre{max-height:180px;overflow:auto;white-space:pre-wrap}.proposal{border-left-style:dashed;border-left-color:#75658b;background:#f5f2f8}.proposal ol{margin-block:var(--space-2)}.preview button,.proposal button{margin-right:var(--space-2)}.message{margin:var(--space-2) 0 0;color:var(--color-accent-active)}@media(max-width:850px){.capture__input{grid-template-columns:1fr}.date{max-width:220px}}
</style>
