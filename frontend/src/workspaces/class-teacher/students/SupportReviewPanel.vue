<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { supportApi, type SupportRecord } from '../api/support'
import type { DirectorySubject } from '../api/r1'

const props = defineProps<{ token: string; subject: DirectorySubject }>()
const records = ref<SupportRecord[]>([])
const selected = ref<SupportRecord | null>(null)
const editingId = ref<string | null>(null)
const kind = ref('teacher_observation')
const content = ref('')
const scene = ref('日常观察')
const source = ref('教师本人观察')
const observedAt = ref(new Date().toISOString().slice(0, 10))
const reviewAt = ref('')
const expiresAt = ref('')
const revisionReason = ref('补充或纠正教师记录')
const message = ref('')
const busy = ref(false)
const expiringRecord = computed(() => ['teacher_observation', 'provisional_judgment'].includes(kind.value))
const canSave = computed(() => Boolean(
  content.value.trim() && scene.value.trim() && source.value.trim() && observedAt.value
  && (editingId.value || !expiringRecord.value || (reviewAt.value && expiresAt.value)),
))

async function load(): Promise<void> {
  records.value = await supportApi.listRecords(props.token, props.subject.subject_id)
  if (selected.value) selected.value = records.value.find((item) => item.record_id === selected.value?.record_id) ?? null
}

function resetEditor(): void {
  selected.value = null
  editingId.value = null
  kind.value = 'teacher_observation'
  content.value = ''
  scene.value = '日常观察'
  source.value = '教师本人观察'
  observedAt.value = new Date().toISOString().slice(0, 10)
  reviewAt.value = ''
  expiresAt.value = ''
  revisionReason.value = '补充或纠正教师记录'
  message.value = ''
}

function selectRecord(record: SupportRecord): void {
  selected.value = record
  editingId.value = record.record_id
  kind.value = record.record_kind
  content.value = record.content
  scene.value = record.scene
  source.value = record.source
  observedAt.value = record.observed_at.slice(0, 10)
  reviewAt.value = record.review_at?.slice(0, 10) ?? ''
  expiresAt.value = record.expires_at?.slice(0, 10) ?? ''
  message.value = ''
}

async function saveLocal(): Promise<void> {
  if (!content.value.trim() || !scene.value.trim() || !source.value.trim() || !observedAt.value || busy.value) return
  busy.value = true
  try {
    const saved = editingId.value && selected.value
      ? await supportApi.reviseRecord(props.token, selected.value, content.value, revisionReason.value)
      : await supportApi.createRecord(props.token, props.subject.subject_id, {
          record_kind: kind.value,
          content: content.value,
          scene: scene.value,
          source: source.value,
          basis: null,
          counterexample: null,
          category: 'general',
          observed_at: observedAt.value,
          review_at: reviewAt.value || null,
          expires_at: expiresAt.value || null,
        })
    selected.value = saved
    editingId.value = saved.record_id
    message.value = '已按教师确认保存为正式学生记录。系统没有调用 AI，也没有自动生成诊断或结论。'
    await load()
  } catch {
    message.value = '记录没有保存；现有记录未改变。若其他页面已更新，请刷新后再核对。'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="support">
    <header class="support__header"><div><p>当前学生</p><h2>{{ subject.display_name }}</h2><span>{{ subject.source_student_id }} · {{ subject.class_label || '未分班' }}</span></div><p>本页只由教师补录和修订；AI 草稿请从对话首页进入登记交接。</p></header>
    <p v-if="message" class="message" role="status">{{ message }}</p>
    <div class="columns">
      <aside class="record-list"><button class="new" type="button" @click="resetEditor">＋ 新建记录</button><button v-for="record in records" :key="record.record_id" type="button" :class="{ active:selected?.record_id===record.record_id }" @click="selectRecord(record)"><strong>{{ record.record_kind }}</strong><span>{{ record.content }}</span><small>版本 {{ record.current_revision }} · {{ record.observed_at?.slice(0,10) }}</small></button><p v-if="!records.length">暂无记录。</p></aside>
      <article class="editor">
        <header><div><p>{{ editingId ? '修订教师记录' : '新增教师记录' }}</p><h2>区分事实、来源与教师判断</h2></div><span>不诊断、不贴永久标签</span></header>
        <div class="form-grid"><label><span>记录类型</span><select v-model="kind" :disabled="!!editingId"><option value="fact">可核对事实</option><option value="student_statement">学生陈述</option><option value="reported_statement">转述信息</option><option value="teacher_observation">教师观察</option><option value="provisional_judgment">阶段性判断</option><option value="professional_conclusion">有依据的专业结论</option></select></label><label><span>观察日期</span><input v-model="observedAt" type="date"></label><label><span>场景</span><input v-model="scene" maxlength="200"></label><label><span>来源</span><input v-model="source" maxlength="200"></label><label class="wide"><span>教师记录</span><textarea v-model="content" rows="9" maxlength="8000" placeholder="记录可核对的事实、场景和来源"></textarea></label><label v-if="editingId" class="wide"><span>修订理由</span><input v-model="revisionReason" maxlength="500"></label><template v-if="expiringRecord"><label><span>复查日期</span><input v-model="reviewAt" type="date"></label><label><span>失效日期</span><input v-model="expiresAt" type="date"></label></template></div>
        <footer><span>正式保存、修订和后续处理都由教师决定。</span><button type="button" :disabled="busy || !canSave" @click="saveLocal">{{ editingId ? '确认修订记录' : '确认保存记录' }}</button></footer>
      </article>
    </div>
  </section>
</template>

<style scoped>
.support{overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}.support__header{display:flex;align-items:end;justify-content:space-between;gap:var(--space-4);padding:var(--space-5);border-bottom:1px solid var(--color-border-default)}.support__header p,.editor header p{margin:0;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.06em}.support__header h2,.editor h2{margin:2px 0}.support__header>p{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense);font-weight:400;letter-spacing:0}.columns{display:grid;grid-template-columns:minmax(220px,28fr) minmax(480px,72fr);min-height:620px}.record-list{padding:var(--space-3);border-right:1px solid var(--color-border-default);background:var(--color-bg-subtle)}.record-list>button{display:grid;gap:4px;width:100%;padding:var(--space-3);border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left}.record-list>button.active{background:var(--color-bg-surface)}.record-list .new{margin-bottom:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface)}.record-list span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.record-list small{color:var(--color-text-muted)}.editor>header{display:flex;align-items:end;justify-content:space-between;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}.editor header>span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3);padding:var(--space-5)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}.wide{grid-column:1/-1}input,select,textarea,button{padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}button{min-height:38px}.editor footer{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);padding:0 var(--space-5) var(--space-5);color:var(--color-text-secondary)}.editor footer button{border-color:var(--color-accent);background:var(--color-accent);color:white;font-weight:700}.message{margin:0;padding:var(--space-3) var(--space-5);background:var(--color-accent-subtle);color:var(--color-accent-active)}@media(max-width:760px){.support__header{align-items:flex-start;flex-direction:column}.columns{grid-template-columns:1fr}.record-list{border-right:0;border-bottom:1px solid var(--color-border-default)}.form-grid{grid-template-columns:1fr}.wide{grid-column:auto}.editor footer{align-items:stretch;flex-direction:column}}
</style>
