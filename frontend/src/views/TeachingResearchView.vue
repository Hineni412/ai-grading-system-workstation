<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { paperReviewApi, type PaperReview, type PaperMark } from '../api/paper-review'
import { fetchSessions, type SessionSummary } from '../api/sessions'
import { exportsApi } from '../api/exports'
import PageHeader from '../components/design-system/PageHeader.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import PaperHero from '../components/paper-review/PaperHero.vue'
import PaperScatter from '../components/paper-review/PaperScatter.vue'
import PaperTable from '../components/paper-review/PaperTable.vue'
import PaperCoverage from '../components/paper-review/PaperCoverage.vue'
import PaperMarks from '../components/paper-review/PaperMarks.vue'
import { displayRate, dots, pct, rules } from '../components/paper-review/display'
import '../components/paper-review/paper-review.css'

const route = useRoute(), router = useRouter()
const sessions = ref<SessionSummary[]>([]), selected = ref<number | null>(null)
const review = ref<PaperReview | null>(null), cls = ref(''), loading = ref(true), error = ref('')
const saving = ref<string | null>(null), saveError = ref(''), table = ref<InstanceType<typeof PaperTable> | null>(null)
let controller: AbortController | null = null, request = 0, alive = true
function message(e: unknown) { return e instanceof Error ? e.message : '读取失败，请重试。' }
async function initialize() {
  error.value = ''; loading.value = true
  try {
    const all = await fetchSessions()
    const availability = await Promise.all(all.map(async s => ({session:s,context:await exportsApi.getReportContext(s.id,1,1)})))
    if (!alive) return
    sessions.value = availability.filter(s=>s.context.has_results).map(s=>s.session).sort((a,b)=>(b.updated_at ?? b.created_at ?? '').localeCompare(a.updated_at ?? a.created_at ?? '') || b.id-a.id)
    const requested = Number(route.query.session)
    selected.value = sessions.value.find(s=>s.id===requested)?.id ?? sessions.value[0]?.id ?? null
    if (selected.value == null) loading.value = false
  } catch (e) { if (alive) { error.value = message(e); loading.value = false } }
}
async function load(session: number | null) {
  const current = ++request
  controller?.abort(); controller = new AbortController()
  review.value = null; cls.value = ''; saving.value = null; saveError.value = ''; error.value = ''
  if (session == null) { loading.value = false; return }
  loading.value = true
  try {
    const data = await paperReviewApi.get(session,controller.signal)
    if (current !== request || !alive) return
    review.value = data
    void router.replace({query:{...route.query,session:String(session)}})
  } catch (e) { if (current===request && alive) error.value=message(e) }
  finally { if (current===request && alive) loading.value=false }
}
async function mark(parent: string, value: PaperMark) {
  const data = review.value
  if (!data || saving.value) return
  const current = request
  saving.value = parent; saveError.value = ''
  try {
    const marks = await paperReviewApi.setMark(data.session_id,parent,data.marks[parent]===value ? null : value)
    if (current===request && alive && review.value) review.value.marks=marks
  } catch (e) { if (current===request && alive) saveError.value=`标记未保存：${message(e)}` }
  finally { if (current===request && alive) saving.value=null }
}
function watchEntries(kind: PaperMark) { const data=review.value; return data ? data.review_lists[kind].flatMap(entry=> { const item=data.items.find(i=>i.id===entry.id); return item ? [{...entry,item}] : [] }) : [] }
async function go(id: string) { await nextTick(); await table.value?.go(id) }
watch(selected,load)
onMounted(initialize)
onBeforeUnmount(()=>{alive=false; ++request; controller?.abort()})
</script>
<template>
  <main class="paper-review">
    <PageHeader title="教研提升"><template #navigation><span class="pr-page-tab">试卷评价</span></template></PageHeader>
    <div class="pr-toolbar"><label>考试 <select v-model="selected" :disabled="!sessions.length" aria-label="选择考试"><option v-for="s in sessions" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
      <nav v-if="review" class="pr-seg" aria-label="班级切换"><button :class="{on:!cls}" :aria-pressed="!cls" @click="cls=''">全部</button><button v-for="(n,name) in review.classes" :key="name" :class="{on:cls===name}" :aria-pressed="cls===name" @click="cls=name">{{ name }}</button></nav><span class="pr-hint">{{ cls ? '仅题卡和表格的得分率按本班，其余按全场' : '回看已批改试卷，评价命题效果' }}</span>
    </div>
    <div class="pr-content">
      <StatePanel v-if="loading" kind="loading" title="正在计算试卷评价" description="读取本场成绩、已保存错因和章节考情。" />
      <StatePanel v-else-if="error" kind="error" title="试卷评价暂时无法读取" :description="error" retry-label="重新读取" @retry="selected ? load(selected) : initialize()" />
      <StatePanel v-else-if="!review || review.status==='no_data'" kind="empty" title="暂无可评价的成绩" description="本页只评价有完整评分的参考学生。批改后再来查看。" />
      <template v-else>
        <FeedbackBanner v-if="review.small_sample" tone="warning" title="人数较少，区分度等指标仅供参考" />
        <FeedbackBanner v-if="saveError" tone="error" :title="saveError" />
        <PaperHero :key="review.session_id" :review="review" :cls="cls" @go="go" />
        <div class="pr-watch">
          <section v-for="kind in (['fix','keep'] satisfies PaperMark[])" :key="kind" class="pr-card"><header><h2>{{ kind==='fix' ? '可能要改的题' : '值得保留的题' }}</h2><span class="pr-hint">从出题角度回看</span></header><div class="pr-watch-list">
            <template v-for="entry in watchEntries(kind)" :key="entry.id"><article class="pr-watch-row" tabindex="0" role="button" :aria-label="`${entry.id} ${entry.reason}`" @click="go(entry.id)" @keydown.enter="go(entry.id)"><b>{{ entry.id }}</b><span class="pr-watch-rate"><span class="pr-bar"><i :style="{width:`${(displayRate(entry.item,cls)??0)*100}%`}"></i></span><small>{{ pct(displayRate(entry.item,cls)) }}</small></span><span class="pr-dots"><i v-for="n in 4" :key="n" :class="{filled:n<=dots(entry.item.r_rest)}"></i></span><span class="pr-watch-reason" :title="entry.reason">{{ entry.reason }}</span><PaperMarks :mark="review.marks[entry.item.parent_id]" :busy="saving!=null" :question-id="entry.item.parent_id" @change="mark(entry.item.parent_id,$event)" /></article></template>
            <p v-if="!review.review_lists[kind].length" class="pr-empty">{{ kind==='fix' ? '暂无需要优先核对的题。' : '暂无符合保留条件的题。' }}</p>
          </div></section>
        </div>
        <PaperScatter :key="`scatter-${review.session_id}`" :review="review" :cls="cls" @go="go" />
        <PaperTable :key="`table-${review.session_id}`" ref="table" :review="review" :cls="cls" :saving="saving" @mark="mark" />
        <PaperCoverage :key="`coverage-${review.session_id}`" :review="review" @go="go" />
        <footer class="pr-footer"><p>本页依据本场学生作答，反映这批学生做这张卷的情况，不等于题目的绝对难度。</p><p>小问预估优先使用已保存的小问关联；没有关联时按考试小问顺序对应题库 part-n，没有小问难度时使用整题难度。分类、区分度、错法人数、维度分与总分按全场计算。</p><details><summary>评分规则</summary><ul><li v-for="rule in rules" :key="rule">{{ rule }}</li></ul></details></footer>
      </template>
    </div>
  </main>
</template>
