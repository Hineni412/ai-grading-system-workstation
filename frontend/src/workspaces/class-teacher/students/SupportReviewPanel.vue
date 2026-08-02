<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { supportApi, type SupportRecord } from '../api/support'
import { studentR1Api, type DirectorySubject, type SupportReview, type SupportReviewPreview } from '../api/r1'

const props = defineProps<{ token: string; subject: DirectorySubject }>()
const records = ref<SupportRecord[]>([])
const selected = ref<SupportRecord | null>(null)
const editingId = ref<string | null>(null)
const kind = ref('observation')
const content = ref('')
const scene = ref('日常观察')
const source = ref('教师本人观察')
const observedAt = ref(new Date().toISOString().slice(0, 10))
const reviewAt = ref('')
const revisionReason = ref('补充或纠正教师记录')
const supplement = ref('')
const preview = ref<SupportReviewPreview | null>(null)
const review = ref<SupportReview | null>(null)
const teacherSummary = ref('')
const message = ref('')
const busy = ref(false)

async function load() {
  records.value = await supportApi.listRecords(props.token, props.subject.subject_id)
  if (selected.value) selected.value = records.value.find((item) => item.record_id === selected.value?.record_id) ?? null
}
function resetEditor() { editingId.value=null; kind.value='observation'; content.value=''; scene.value='日常观察'; source.value='教师本人观察'; observedAt.value=new Date().toISOString().slice(0,10); reviewAt.value=''; revisionReason.value='补充或纠正教师记录'; preview.value=null; review.value=null }
function selectRecord(record: SupportRecord) { selected.value=record; editingId.value=record.record_id; kind.value=record.record_kind; content.value=record.content; scene.value=record.scene; source.value=record.source; observedAt.value=record.observed_at.slice(0,10); reviewAt.value=record.review_at?.slice(0,10) ?? ''; preview.value=null; review.value=null; teacherSummary.value='' }
async function saveLocal(): Promise<SupportRecord | null> {
  if (!content.value.trim() || !scene.value.trim() || !source.value.trim() || !observedAt.value) return null
  busy.value=true
  try {
    const saved = editingId.value && selected.value
      ? await supportApi.reviseRecord(props.token, selected.value, content.value, revisionReason.value)
      : await supportApi.createRecord(props.token, props.subject.subject_id, {
          record_kind:kind.value, content:content.value, scene:scene.value, source:source.value,
          basis:null, counterexample:null, category:'general', observed_at:observedAt.value,
          review_at:reviewAt.value || null, expires_at:null,
        })
    selected.value=saved; editingId.value=saved.record_id; message.value='记录只保存到本机加密保险箱；模型请求为 0。'; await load(); return saved
  } finally { busy.value=false }
}
async function saveAndPrepare() { const saved=await saveLocal(); if (saved) await prepare(saved) }
async function prepare(record = selected.value) { if (!record) return; busy.value=true; try { preview.value = await studentR1Api.prepareReview(props.token, record.record_id, record.current_revision, supplement.value); review.value = null; message.value='匿名预览已准备，尚未发送。' } finally { busy.value=false } }
async function confirm() { if (!preview.value) return; busy.value=true; try { review.value = await studentR1Api.confirmReview(props.token, preview.value); const turns=review.value.turns ?? []; const last=turns[turns.length-1]; teacherSummary.value = last?.model_result?.proposal?.summary ?? last?.model_result?.draft_text ?? ''; message.value=review.value.state==='result_unknown'?'结果未知；只能查询同一操作，不能追加发送。':'本轮发送已经结束，AI 草稿仍未写入。' } finally { busy.value=false } }
async function querySameOperation() { const id=review.value?.model_operation_id; if (!id) return; busy.value=true; try { review.value=await studentR1Api.reviewOperation(props.token,id); message.value='已查询同一操作，没有追加模型请求。' } finally { busy.value=false } }
async function apply() { if (!review.value || !teacherSummary.value.trim()) return; busy.value=true; try { await studentR1Api.applyReview(props.token, review.value, teacherSummary.value); message.value = '教师确认的结构已写入学生卡，并生成一条匿名待办。'; preview.value = null; review.value = null; await load() } finally { busy.value=false } }
async function reject() { if (!review.value) return; await studentR1Api.rejectReview(props.token, review.value.review_id); review.value=null; preview.value=null; message.value='本轮 AI 草稿未采用，没有写入学生卡。' }
onMounted(() => { void load() })
</script>

<template>
  <section class="support">
    <header class="steps"><span class="active">1 保存记录</span><span :class="{active:preview}">2 核对匿名内容</span><span :class="{active:review}">3 确认本轮发送</span><span :class="{active:review}">4 AI 追问 / 草稿</span><span :class="{active:message.includes('学生卡')}">5 教师确认写入</span></header>
    <div class="columns">
      <aside class="record-list"><div><p class="eyebrow">当前学生</p><h2>{{ subject.display_name }}</h2><small>{{ subject.source_student_id }} · {{ subject.class_label || '未分班' }}</small></div><button class="new" type="button" @click="resetEditor">＋ 新建本机记录</button><button v-for="record in records" :key="record.record_id" type="button" :class="{ active:selected?.record_id===record.record_id }" @click="selectRecord(record)"><strong>{{ record.record_kind }}</strong><span>{{ record.content }}</span><small>版本 {{ record.current_revision }} · {{ record.observed_at?.slice(0,10) }}</small></button><p v-if="!records.length">暂无记录。</p></aside>
      <article class="editor">
        <header><div><p class="eyebrow">{{ editingId ? '修订教师记录' : '新增教师记录' }}</p><h2>事实先保存在本机</h2></div><span>不诊断、不贴永久标签</span></header>
        <div class="form-grid"><label><span>记录类型</span><select v-model="kind" :disabled="!!editingId"><option value="observation">观察</option><option value="communication">沟通</option><option value="support">支持</option><option value="follow_up">复查</option></select></label><label><span>观察日期</span><input v-model="observedAt" type="date"></label><label><span>场景</span><input v-model="scene" maxlength="200"></label><label><span>来源</span><input v-model="source" maxlength="200"></label><label class="wide"><span>教师记录</span><textarea v-model="content" rows="7" maxlength="8000" placeholder="记录可核对的事实、场景和来源"></textarea></label><label v-if="editingId" class="wide"><span>修订理由</span><input v-model="revisionReason" maxlength="500"></label><label><span>复查日期（可空）</span><input v-model="reviewAt" type="date"></label></div>
        <div class="actions"><button type="button" :disabled="busy || !content.trim()" @click="saveLocal">仅保存到本机</button><button class="primary" type="button" :disabled="busy || !content.trim()" @click="saveAndPrepare">保存并准备 AI 讨论</button></div>
        <section v-if="selected" class="timeline"><p class="eyebrow">当前已保存版本</p><blockquote>{{ selected.content }}</blockquote><span>版本 {{ selected.current_revision }} · {{ selected.observed_at.slice(0,10) }}</span></section>
      </article>
      <aside class="review-panel">
        <p class="eyebrow">逐轮人工确认</p><h2>匿名预览与 AI 草稿</h2>
        <label><span>本轮用自己的话补充（可空）</span><textarea v-model="supplement" rows="3" maxlength="4000"></textarea></label><button type="button" :disabled="busy || !selected" @click="prepare()">与 AI 讨论这条记录</button>
        <div v-if="preview" class="preview"><strong>发送前逐字核对</strong><pre>{{ JSON.stringify(preview.exact_payload, null, 2) }}</pre><button type="button" :disabled="busy" @click="confirm">确认本轮只发送一次</button></div>
        <div v-if="review" class="result"><strong>AI 原始草稿保持不变</strong><p v-if="review.state==='awaiting_teacher'">AI 需要补充信息；请修改上方补充并生成新的逐字预览。</p><p v-else-if="review.state==='result_unknown'">本轮结果未知。只能查询原操作，不能再次发送。</p><label v-else><span>教师最终摘要</span><textarea v-model="teacherSummary" rows="6"></textarea></label><div><button v-if="review.state==='result_unknown'" type="button" :disabled="busy" @click="querySameOperation">查询同一操作</button><button v-else class="primary" type="button" :disabled="busy || !teacherSummary.trim()" @click="apply">核对并写入加密学生卡</button><button type="button" @click="reject">不采用</button></div></div>
        <p v-if="message" class="message" role="status">{{ message }}</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.support{overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}.steps{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.steps span{padding:var(--space-3);color:var(--color-text-muted);font-size:var(--font-size-dense);border-right:1px solid var(--color-border-subtle)}.steps span.active{color:var(--color-accent-active);font-weight:700}.columns{display:grid;grid-template-columns:minmax(190px,18fr) minmax(420px,52fr) minmax(280px,30fr);min-height:620px}.record-list{padding:var(--space-3);border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.record-list h2{margin:0}.record-list>button{display:grid;gap:4px;width:100%;padding:var(--space-3);border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left}.record-list>button.active{background:var(--color-bg-surface)}.record-list .new{margin:var(--space-3) 0;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface)}.record-list span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.record-list small{color:var(--color-text-muted)}.editor>header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}.eyebrow{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.06em}h2{margin:0;font-size:var(--font-size-h2)}.editor header>span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3);padding:var(--space-5)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}.wide{grid-column:1/-1}input,select,textarea,button{padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}button{min-height:38px}.actions{display:flex;justify-content:flex-end;gap:var(--space-2);padding:0 var(--space-5) var(--space-5)}.primary{border-color:var(--color-accent);background:var(--color-accent);color:white}.timeline{margin:0 var(--space-5) var(--space-5);padding:var(--space-4);border-top:1px solid var(--color-border-default);background:var(--color-bg-subtle)}blockquote{margin:var(--space-2) 0;padding-left:var(--space-3);border-left:3px solid var(--color-accent)}.timeline>span{color:var(--color-text-muted);font-size:var(--font-size-dense)}.review-panel{padding:var(--space-4);border-left:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.review-panel>button{margin:var(--space-3) 0}.preview,.result{margin-top:var(--space-3);padding:var(--space-3);border-left:3px solid var(--color-warning);background:var(--color-warning-subtle)}pre{max-height:220px;overflow:auto;white-space:pre-wrap}.result>div{display:flex;flex-wrap:wrap;gap:var(--space-2);margin-top:var(--space-2)}.message{color:var(--color-accent-active)}@media(max-width:1050px){.columns{grid-template-columns:220px 1fr}.review-panel{grid-column:1/-1;border-top:1px solid var(--color-border-default);border-left:0}}@media(max-width:760px){.steps{grid-template-columns:1fr}.columns{grid-template-columns:1fr}.record-list{border-right:0;border-bottom:1px solid var(--color-border-default)}.form-grid{grid-template-columns:1fr}.wide{grid-column:auto}}
</style>
