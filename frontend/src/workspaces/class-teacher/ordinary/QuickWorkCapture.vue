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
const canGenerate = computed(() => Boolean(text.value.trim()))

function resetAfterEdit() {
  preview.value = null
  result.value = null
  operationId.value = ''
  message.value = ''
}

async function generate() {
  if (!canGenerate.value) return
  busy.value = true; message.value = ''
  try {
    preview.value = await workApi.previewPlan(text.value, dueDate.value || null)
    result.value = null
    if (!preview.value.model_enabled) {
      operationId.value = ''
      message.value = '当前未配置可用模型，尚未发出请求。'
      return
    }
    operationId.value = globalThis.crypto.randomUUID()
    result.value = await workApi.invokePlan(preview.value, operationId.value)
    const count = result.value.physical_request_count
    const reason = result.value.error_category || result.value.state
    if (result.value.state === 'invalid_result') message.value = `AI 返回内容未通过校验，未生成草案；原因：${reason}；已请求 ${count} 次。可以再次点击生成。`
    else if (result.value.state === 'result_unknown') message.value = `AI 请求结果仍不明确；原因：${reason}；已请求 ${count} 次。可以再次点击生成。`
    else if (result.value.state === 'unavailable') message.value = `模型当前不可用，尚未生成草案；原因：${reason}；已请求 ${count} 次。`
    else if (result.value.state === 'destination_changed') message.value = `模型配置在请求前发生变化；原因：${reason}；已请求 ${count} 次。请再次点击生成。`
    else if (result.value.state === 'needs_information') message.value = `AI 还需要补充信息；原因：${reason}；已请求 ${count} 次。`
    else if (result.value.state === 'succeeded') message.value = `AI 草案已生成；已请求 ${count} 次，请核对后再写入工作图。`
  } catch {
    message.value = 'AI 草案暂时无法生成；原因：请求接口失败；请求次数无法确认。可以再次点击生成。'
  } finally { busy.value = false }
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
      <button type="button" :disabled="busy || !canGenerate" @click="generate">{{ busy ? 'AI 正在处理…' : '生成 AI 草案' }}</button>
    </div>
    <div v-if="result" class="proposal" :data-state="result.state">
      <strong>{{ result.plan ? 'AI 草案 · 尚未进入正式工作图' : 'AI 处理结果' }}</strong>
      <p v-if="result.questions.length">还需教师补充：{{ result.questions.join('；') }}</p>
      <ol v-if="result.plan"><li v-for="node in result.plan.nodes" :key="node.draft_key">{{ node.title }} · {{ node.due_date || '日期待定' }}</li></ol>
      <button v-if="result.state==='succeeded' && result.plan" type="button" :disabled="busy" @click="confirmPlan">教师确认，写入工作图</button>
    </div>
    <p v-if="message" class="message" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.capture{padding:var(--space-4) var(--space-5);border-bottom:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.capture__input{display:grid;grid-template-columns:minmax(280px,1fr) 180px auto;align-items:end;gap:var(--space-3)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}input,button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.capture__input>button{border-color:var(--color-accent);background:var(--color-accent);color:white}.proposal{margin-top:var(--space-3);padding:var(--space-3);border-left:3px dashed #75658b;background:#f5f2f8}.proposal ol{margin-block:var(--space-2)}.proposal button{margin-right:var(--space-2)}.message{margin:var(--space-2) 0 0;color:var(--color-accent-active)}@media(max-width:850px){.capture__input{grid-template-columns:1fr}.date{max-width:220px}}
</style>
