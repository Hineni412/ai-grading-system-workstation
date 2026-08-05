<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { intakeApi, type HomeroomPreference } from '../api/intake'
import { studentR1Api, type DirectorySubject, type ExistingRosterStudent } from '../api/r1'

const props = defineProps<{ token: string }>()
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
const preference = ref<HomeroomPreference | null>(null)
const directoryMessage = ref('')
const currentRosterClasses = computed(() => preference.value?.homeroom_class || '尚未选择')
async function load(reset = false) {
  if (reset) { cursor.value = null; history.value = [] }
  loading.value = true
  directoryMessage.value = ''
  try {
    const page = await studentR1Api.directory(props.token, { q: q.value, classLabel: classLabel.value, state: 'active', rosterState: rosterState.value, sort: sort.value, cursor: cursor.value || undefined, pageSize: 20 })
    items.value = page.items; total.value = page.total; nextCursor.value = page.cursor
  } catch { items.value = []; total.value = 0; directoryMessage.value = '学生档案暂时无法读取；已保存的我班名单没有丢失。' }
  finally { loading.value = false }
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
  if (!sourceClass.value || !sourceRevision.value || !preference.value || rosterBusy.value) return
  rosterBusy.value = true; rosterMessage.value = ''
  try {
    preference.value = await intakeApi.setHomeroom(preference.value, sourceClass.value)
    classLabel.value = sourceClass.value
    rosterMessage.value = `默认班级已改为 ${sourceClass.value}；只改变筛选，未删除学生或历史关系。`
    await loadSource(); await load(true)
  } catch { rosterMessage.value = '设置未完成。现有学生库可能已经变化，请刷新筛选结果后重试。' }
  finally { rosterBusy.value = false }
}
async function loadCurrentRoster() {
  preference.value = await intakeApi.homeroom()
  sourceClass.value = preference.value.homeroom_class || ''
  classLabel.value = sourceClass.value
}
async function initialize(): Promise<void> {
  try { await loadCurrentRoster() } catch { preference.value = null }
  await Promise.all([load(), loadSource()])
}
onMounted(() => { void initialize() })
</script>

<template>
  <section class="directory">
    <section class="current-roster-bar">
      <div><strong>我的班主任班级</strong><span>{{ currentRosterClasses }} · 一直沿用到手动更改</span></div>
    </section>
    <section class="roster-setup">
      <header><div><p>现有学生库</p><h2>选择默认班级</h2></div><span>这里只改变首页、学生页和姓名匹配的默认筛选，不删除学生或历史关系。</span></header>
      <form @submit.prevent="loadSource"><label><span>班级</span><select v-model="sourceClass"><option value="">请选择班级</option><option v-for="item in sourceClasses" :key="item" :value="item">{{ item }}</option></select></label><label><span>姓名或学号（仅预览）</span><input v-model="sourceQ" placeholder="留空表示整个班"></label><button type="submit" :disabled="rosterBusy">查看学生</button><button class="set-roster" type="button" :disabled="rosterBusy || !sourceClass" @click="replaceRoster">设为默认班级</button></form>
      <p v-if="rosterMessage" class="roster-message" role="status">{{ rosterMessage }}</p>
      <div class="source-preview"><span v-for="student in sourceItems" :key="student.source_key" :class="student.roster_state">{{ student.display_name }}<small>{{ student.student_code || '无学号' }}</small></span><p v-if="!sourceItems.length && !rosterBusy">当前筛选没有学生。</p></div>
      <p class="source-count">当前预览 {{ sourceTotal }} 名；偶发多一名学生不会触发自动剔除或复杂权限判断。</p>
    </section>
    <header><div><p>班主任学生目录</p><h2>先找到学生，再读取具体记录</h2></div><span>本页不读取支持正文、AI 草稿或学业详情</span></header>
    <form @submit.prevent="load(true)"><label><span>搜索姓名或学号</span><input v-model="q" autocomplete="off" placeholder="输入后按回车"></label><label><span>班级</span><input v-model="classLabel" placeholder="全部班级"></label><label><span>名册状态</span><select v-model="rosterState"><option value="">全部</option><option value="active">当前我班</option><option value="historical">历史档案</option></select></label><label><span>排序</span><select v-model="sort"><option value="last_confirmed_desc">最近确认</option><option value="name_asc">姓名</option><option value="records_desc">记录最多</option><option value="attention_desc">待处理优先</option></select></label><button type="submit">查询</button></form>
    <p v-if="directoryMessage" class="directory-message" role="alert">{{ directoryMessage }}</p>
    <div class="table" role="table" aria-label="学生目录">
      <div class="table__head" role="row"><span>学生</span><span>已确认记录</span><span>支持计划</span><span>学业关注</span><span>最近确认</span></div>
      <button v-for="item in items" :key="item.subject_id" role="row" type="button" @click="emit('select', item)"><span><strong>{{ item.display_name }}</strong><small>{{ item.source_student_id }} · {{ item.class_label || '未分班' }} · {{ item.roster_state === 'historical' ? '历史档案' : item.roster_state === 'active' ? '当前我班' : '手动档案' }}</small></span><span>{{ item.support_record_count }}</span><span>{{ item.support_plan_count }}</span><span :class="{ attention: item.attention_pending_count }">{{ item.attention_pending_count }}</span><time>{{ item.last_confirmed_at?.slice(0,10) || '—' }}</time></button>
      <p v-if="!items.length && !loading">没有符合条件的学生。</p>
      <footer><span>共 {{ total }} 名 · 每页最多 20 名</span><div><button type="button" :disabled="!history.length || loading" @click="previousPage">上一页</button><button type="button" :disabled="!nextCursor || loading" @click="nextPage">下一页</button></div></footer>
    </div>
  </section>
</template>

<style scoped>
.directory{border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}header p{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.08em}h2{margin:0;font-size:var(--font-size-h2)}header>span{max-width:360px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}form{display:grid;grid-template-columns:minmax(220px,1fr) 150px 120px 160px auto;align-items:end;gap:var(--space-3);padding:var(--space-4) var(--space-5);background:var(--color-bg-subtle)}label{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}input,select,button{min-height:40px;font:inherit;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface)}button{padding:0 var(--space-3)}.table__head,.table>button{display:grid;grid-template-columns:minmax(230px,1.5fr) repeat(3,minmax(90px,.55fr)) minmax(100px,.7fr);align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-5);text-align:left}.table__head{color:var(--color-text-muted);font-size:var(--font-size-caption);font-weight:700;border-bottom:1px solid var(--color-border-default)}.table>button{width:100%;border:0;border-bottom:1px solid var(--color-border-subtle);border-radius:0}.table>button:hover{background:var(--color-accent-subtle)}.table>button>span:first-child{display:grid;gap:2px}.table small,.table time{color:var(--color-text-secondary)}.attention{color:var(--color-warning);font-weight:700}.table>p{padding:var(--space-5);color:var(--color-text-secondary)}footer{display:flex;justify-content:space-between;align-items:center;padding:var(--space-3) var(--space-5);color:var(--color-text-secondary)}footer div{display:flex;gap:var(--space-2)}@media(max-width:980px){header{align-items:flex-start;flex-direction:column;gap:var(--space-2)}form{grid-template-columns:1fr 1fr}.table{overflow:auto}.table__head,.table>button{min-width:780px}}@media(max-width:600px){form{grid-template-columns:1fr}}
.roster-setup{border-bottom:2px solid var(--color-accent)}.roster-setup form{grid-template-columns:minmax(160px,.7fr) minmax(220px,1fr) auto auto}.set-roster{border-color:var(--color-accent);background:var(--color-accent);color:white;font-weight:700}.source-preview{display:flex;flex-wrap:wrap;gap:var(--space-2);max-height:190px;overflow:auto;padding:var(--space-3) var(--space-5)}.source-preview>span{display:grid;gap:2px;min-width:150px;padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}.source-preview>span.active{border-color:var(--color-accent);background:var(--color-accent-subtle)}.source-preview small{color:var(--color-text-secondary)}.source-count,.roster-message{margin:0;padding:0 var(--space-5) var(--space-3);color:var(--color-text-secondary);font-size:var(--font-size-dense)}.roster-message{color:var(--color-accent-active);font-weight:650}@media(max-width:900px){.roster-setup form{grid-template-columns:1fr 1fr}}@media(max-width:600px){.roster-setup form{grid-template-columns:1fr}}
.current-roster-bar{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-default);background:var(--color-accent-subtle)}.current-roster-bar div{display:grid;gap:2px}.current-roster-bar span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.current-roster-bar button,.cancel-roster-edit{min-height:36px}.cancel-roster-edit{margin:0 var(--space-5) var(--space-3)}.directory-message{margin:0;padding:var(--space-3) var(--space-5);color:var(--color-danger);background:var(--color-danger-subtle)}
</style>
