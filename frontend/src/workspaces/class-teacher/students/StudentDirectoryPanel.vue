<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { studentR1Api, type DirectorySubject, type ExistingRosterStudent } from '../api/r1'

const props = defineProps<{ token: string; handoffPending?: boolean }>()
const emit = defineEmits<{ select: [subject: DirectorySubject] }>()
const items = ref<DirectorySubject[]>([])
const q = ref('')
const sort = ref('last_confirmed_desc')
const classLabel = ref('')
const rosterState = ref('')
const cursor = ref<string | null>(null)
const nextCursor = ref<string | null>(null)
const history = ref<string[]>([])
const total = ref(0)
const loading = ref(false)
const sourceItems = ref<ExistingRosterStudent[]>([])
const sourceClasses = ref<string[]>([])
const sourceRevision = ref('')
const sourceClass = ref('')
const sourceQ = ref('')
const sourceTotal = ref(0)
const rosterMessage = ref('')
const rosterBusy = ref(false)
async function load(reset = false) {
  if (reset) { cursor.value = null; history.value = [] }
  loading.value = true
  try {
    const page = await studentR1Api.directory(props.token, { q: q.value, classLabel: classLabel.value, state: 'active', rosterState: rosterState.value, sort: sort.value, cursor: cursor.value || undefined, pageSize: 20 })
    items.value = page.items; total.value = page.total; nextCursor.value = page.cursor
  } finally { loading.value = false }
}
async function nextPage() { if (!nextCursor.value) return; history.value.push(cursor.value || ''); cursor.value = nextCursor.value; await load() }
async function previousPage() { if (!history.value.length) return; cursor.value = history.value.pop() || null; await load() }
async function loadSource() {
  rosterBusy.value = true
  try {
    const page = await studentR1Api.rosterSource(props.token, { q: sourceQ.value, classLabel: sourceClass.value, pageSize: 100 })
    sourceItems.value = page.items; sourceClasses.value = page.classes; sourceRevision.value = page.source_revision; sourceTotal.value = page.total
  } finally { rosterBusy.value = false }
}
async function replaceRoster() {
  if (!sourceClass.value || !sourceRevision.value || rosterBusy.value) return
  rosterBusy.value = true; rosterMessage.value = ''
  try {
    const result = await studentR1Api.replaceCurrentRoster(props.token, { expectedSourceRevision: sourceRevision.value, classLabel: sourceClass.value, q: sourceQ.value })
    rosterMessage.value = `已将筛选出的 ${result.active_count} 名学生设为当前我班学生；原名单中未选到的学生已转为历史档案。`
    await loadSource(); await load(true)
  } catch { rosterMessage.value = '设置未完成。现有学生库可能已经变化，请刷新筛选结果后重试。' }
  finally { rosterBusy.value = false }
}
onMounted(() => { void load(); void loadSource() })
</script>

<template>
  <section class="directory">
    <p v-if="handoffPending" class="handoff-notice" role="status">首页内容尚未保存。请先选择对应学生，再由教师核对预填记录；不会自动保存。</p>
    <section class="roster-setup">
      <header><div><p>现有学生库</p><h2>设置当前“我班学生”</h2></div><span>这里只读取阅卷系统已有名单；点击设置后，你对这些学生同时具有班主任身份。</span></header>
      <form @submit.prevent="loadSource"><label><span>按班级筛选</span><select v-model="sourceClass"><option value="">请选择班级</option><option v-for="item in sourceClasses" :key="item" :value="item">{{ item }}</option></select></label><label><span>姓名或学号（可选）</span><input v-model="sourceQ" placeholder="留空表示整个班"></label><button type="submit" :disabled="rosterBusy">查看筛选结果</button><button class="set-roster" type="button" :disabled="rosterBusy || !sourceClass || !sourceItems.length" @click="replaceRoster">将筛选结果设为我班学生</button></form>
      <p v-if="rosterMessage" class="roster-message" role="status">{{ rosterMessage }}</p>
      <div class="source-preview"><span v-for="student in sourceItems" :key="student.source_key" :class="student.roster_state">{{ student.display_name }}<small>{{ student.student_code || '无学号' }}</small></span><p v-if="!sourceItems.length && !rosterBusy">当前筛选没有学生。</p></div>
      <p class="source-count">筛选结果共 {{ sourceTotal }} 名；重复设置同一结果不会重复建档，改选其他班级会替换当前名单，旧档案不会删除。</p>
    </section>
    <header><div><p>班主任学生目录</p><h2>先找到学生，再读取具体记录</h2></div><span>本页不读取支持正文、AI 草稿或学业详情</span></header>
    <form @submit.prevent="load(true)"><label><span>搜索姓名或学号</span><input v-model="q" autocomplete="off" placeholder="输入后按回车"></label><label><span>班级</span><input v-model="classLabel" placeholder="全部班级"></label><label><span>名册状态</span><select v-model="rosterState"><option value="">全部</option><option value="active">当前我班</option><option value="historical">历史档案</option></select></label><label><span>排序</span><select v-model="sort"><option value="last_confirmed_desc">最近确认</option><option value="name_asc">姓名</option><option value="records_desc">记录最多</option><option value="attention_desc">待处理优先</option></select></label><button type="submit">查询</button></form>
    <div class="table" role="table" aria-label="学生目录">
      <div class="table__head" role="row"><span>学生</span><span>已确认记录</span><span>支持计划</span><span>学业关注</span><span>最近确认</span></div>
      <button v-for="item in items" :key="item.subject_id" role="row" type="button" @click="emit('select', item)"><span><strong>{{ item.display_name }}</strong><small>{{ item.source_student_id }} · {{ item.class_label || '未分班' }} · {{ item.roster_state === 'historical' ? '历史档案' : item.roster_state === 'active' ? '当前我班' : '手动档案' }}</small></span><span>{{ item.support_record_count }}</span><span>{{ item.support_plan_count }}</span><span :class="{ attention: item.attention_pending_count }">{{ item.attention_pending_count }}</span><time>{{ item.last_confirmed_at?.slice(0,10) || '—' }}</time></button>
      <p v-if="!items.length && !loading">没有符合条件的学生。</p>
      <footer><span>共 {{ total }} 名 · 每页最多 20 名</span><div><button type="button" :disabled="!history.length || loading" @click="previousPage">上一页</button><button type="button" :disabled="!nextCursor || loading" @click="nextPage">下一页</button></div></footer>
    </div>
  </section>
</template>

<style scoped>
.directory{border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}.handoff-notice{margin:0;padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-warning);background:var(--color-warning-subtle);color:var(--color-text-primary)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}header p{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}h2{margin:0;font-size:var(--font-size-h2)}header>span{max-width:360px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}form{display:grid;grid-template-columns:minmax(220px,1fr) 150px 120px 160px auto;align-items:end;gap:var(--space-3);padding:var(--space-4) var(--space-5);background:var(--color-bg-subtle)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}input,select,button{min-height:40px;font:inherit;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface)}button{padding:0 var(--space-3)}.table__head,.table>button{display:grid;grid-template-columns:minmax(230px,1.5fr) repeat(3,minmax(90px,.55fr)) minmax(100px,.7fr);align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-5);text-align:left}.table__head{color:var(--color-text-muted);font-size:var(--font-size-caption);font-weight:700;border-bottom:1px solid var(--color-border-default)}.table>button{width:100%;border:0;border-bottom:1px solid var(--color-border-subtle);border-radius:0}.table>button:hover{background:var(--color-accent-subtle)}.table>button>span:first-child{display:grid;gap:2px}.table small,.table time{color:var(--color-text-secondary)}.attention{color:var(--color-warning);font-weight:700}.table>p{padding:var(--space-5);color:var(--color-text-secondary)}footer{display:flex;justify-content:space-between;align-items:center;padding:var(--space-3) var(--space-5);color:var(--color-text-secondary)}footer div{display:flex;gap:var(--space-2)}@media(max-width:980px){header{align-items:flex-start;flex-direction:column;gap:var(--space-2)}form{grid-template-columns:1fr 1fr}.table{overflow:auto}.table__head,.table>button{min-width:780px}}@media(max-width:600px){form{grid-template-columns:1fr}}
.roster-setup{border-bottom:2px solid var(--color-accent)}.roster-setup form{grid-template-columns:minmax(160px,.7fr) minmax(220px,1fr) auto auto}.set-roster{border-color:var(--color-accent);background:var(--color-accent);color:white;font-weight:700}.source-preview{display:flex;flex-wrap:wrap;gap:var(--space-2);max-height:190px;overflow:auto;padding:var(--space-3) var(--space-5)}.source-preview>span{display:grid;gap:2px;min-width:150px;padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.source-preview>span.active{border-color:var(--color-accent);background:var(--color-accent-subtle)}.source-preview small{color:var(--color-text-secondary)}.source-count,.roster-message{margin:0;padding:0 var(--space-5) var(--space-3);color:var(--color-text-secondary);font-size:var(--font-size-dense)}.roster-message{color:var(--color-accent-active);font-weight:650}@media(max-width:900px){.roster-setup form{grid-template-columns:1fr 1fr}}@media(max-width:600px){.roster-setup form{grid-template-columns:1fr}}
</style>
