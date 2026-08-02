<script setup lang="ts">
import { LineChart, ScatterChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { studentR1Api, type AcademicAnalysis, type AttentionCard, type DirectorySubject } from '../api/r1'

use([LineChart, ScatterChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ token: string; subject: DirectorySubject }>()
const analysis = ref<AcademicAnalysis | null>(null)
interface EvidenceSnapshot { language: string; evidence: { title: string; occurred_on: string; result_state: string; score: number | null } }
const selectedEvidence = ref<EvidenceSnapshot | null>(null)
const rankChart = ref<HTMLElement | null>(null)
const pairChart = ref<HTMLElement | null>(null)
const timelineChart = ref<HTMLElement | null>(null)
const charts: ECharts[] = []
const decision = ref('observe')
const reason = ref('')
const reviewAt = ref('')
const message = ref('')
const pairCount = ref(0)
const timeFilter = ref<'all'|'recent_90'|'year'>('all')
const seriesFilter = ref('')
const subjectFilter = ref('')
const comparableOnly = ref(false)
let loadSequence = 0
const stateLabels: Record<string, string> = { normal:'正常', absent:'缺考', exempt:'免考', missing:'缺失', incomplete:'未完成', pending_review:'待核对', makeup:'补测' }
const stateSymbols: Record<string, string> = { normal:'circle', absent:'emptyCircle', exempt:'triangle', missing:'rect', incomplete:'roundRect', pending_review:'diamond', makeup:'pin' }
const seriesOptions = computed(() => analysis.value?.filter_options?.series ?? [...new Set((analysis.value?.sessions ?? []).map((item) => item.comparison_series).filter((item): item is string => !!item))])
const subjectOptions = computed(() => analysis.value?.filter_options?.subjects ?? [...new Set((analysis.value?.series ?? []).map((item) => item.subject_name))])
const filteredSessions = computed(() => analysis.value?.sessions ?? [])

async function load() {
  const sequence = ++loadSequence
  const next = await studentR1Api.academic(props.token, props.subject.subject_id, {
    timeRange: timeFilter.value,
    comparisonSeries: seriesFilter.value || undefined,
    subjectName: subjectFilter.value || undefined,
    comparableOnly: comparableOnly.value,
  })
  if (sequence !== loadSequence) return
  analysis.value = next
  await nextTick(); render()
}
function chart(element: HTMLElement | null, option: Record<string, unknown>) { if (!element) return; const instance = init(element); instance.setOption(option); instance.on('click', (params) => { const data = params.data as unknown; if (data && typeof data === 'object' && !Array.isArray(data) && 'evidenceId' in data && typeof data.evidenceId === 'string') void inspect(data.evidenceId) }); charts.push(instance) }
function render() {
  charts.splice(0).forEach((item) => item.dispose())
  if (!analysis.value) return
  const common = { aria: { enabled: true }, tooltip: { trigger: 'axis' }, grid: { left: 46, right: 18, top: 42, bottom: 42 } }
  const visibleSeries = analysis.value.series
  const pairs = analysis.value.rank_change_pairs
  const rankSeries = visibleSeries.flatMap((series) => [
    {
      name: series.subject_name,
      type: 'scatter',
      symbol: 'circle',
      data: series.points.map((point) => ({
        evidenceId: point.evidence_version_id,
        value: [point.occurred_on, point.relative_position ?? null],
      })),
    },
    ...series.segments.flatMap((segment, index) => {
      if (segment.dimensions.rank.status !== 'directly_comparable') return []
      const from = series.points[index]
      const to = series.points[index + 1]
      if (!from || !to) return []
      return [{
        name: series.subject_name,
        type: 'line',
        symbol: 'none',
        data: [
          { evidenceId:from.evidence_version_id, value:[from.occurred_on, from.relative_position ?? null] },
          { evidenceId:to.evidence_version_id, value:[to.occurred_on, to.relative_position ?? null] },
        ],
      }]
    }),
  ])
  pairCount.value = pairs.length
  chart(rankChart.value, { ...common, legend: {}, xAxis: { type: 'category' }, yAxis: { type: 'value', min: 0, max: 1, name: '相对位次' }, series: rankSeries })
  chart(pairChart.value, { ...common, tooltip: { trigger: 'item' }, xAxis: { type: 'value', min: 0, max: 1, name: '相对位次' }, yAxis: { type: 'category', data: pairs.map((item) => item.subject_name) }, series: [{ name: '前次', type: 'scatter', symbol: 'circle', data: pairs.map((item, index) => { const points=visibleSeries.find((series)=>series.subject_name===item.subject_name)?.points ?? []; return { value:[item.from,index], evidenceId:points[points.length-2]?.evidence_version_id } }) }, { name: '本次', type: 'scatter', symbol: 'diamond', data: pairs.map((item, index) => { const points=visibleSeries.find((series)=>series.subject_name===item.subject_name)?.points ?? []; return { value:[item.to,index], evidenceId:points[points.length-1]?.evidence_version_id } }) }] })
  chart(timelineChart.value, { ...common, tooltip: { trigger:'item', formatter:(params:{data?:{label?:string}})=>params.data?.label ?? '' }, xAxis: { type: 'time' }, yAxis: { type: 'category', data: ['证据'] }, series: [{ type: 'scatter', symbolSize: 15, label:{show:true,position:'top',formatter:(params:{data?:{stateText?:string}})=>params.data?.stateText ?? ''}, data: filteredSessions.value.flatMap((session) => session.evidence.map((point) => ({ value:[point.occurred_on,0], evidenceId:point.evidence_version_id, stateText:stateLabels[point.result_state] ?? point.result_state, label:`${session.title} · ${point.subject_name} · ${stateLabels[point.result_state] ?? point.result_state}`, name:session.title, symbol:stateSymbols[point.result_state] ?? 'diamond', itemStyle:{color:session.metadata_complete ? '#356859' : '#9b6a2b'} }))) }] })
}
async function inspect(evidenceId: string) { selectedEvidence.value = await studentR1Api.evidenceSnapshot(props.token, evidenceId) as unknown as EvidenceSnapshot }
async function decide(card: AttentionCard) { if (!analysis.value || !reason.value.trim()) return; await studentR1Api.decideAttention(props.token, card, analysis.value, { decision: decision.value, reason: reason.value, reviewAt: decision.value === 'no_action' ? null : reviewAt.value || null, planId: null }); message.value = '教师决定已保存；需要跟进时只生成一条匿名待办。'; await load() }
function resize() { charts.forEach((item) => item.resize()) }
onMounted(() => { void load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach((item) => item.dispose()) })
watch([timeFilter, seriesFilter, subjectFilter, comparableOnly], () => { void load() })
</script>

<template>
  <section class="academic">
    <header><div><p>学业证据 · {{ analysis?.ruleset_version || '正在读取' }}</p><h2>{{ subject.display_name }} 的证据变化</h2></div><span>缺考不是 0 分 · 不可比点保留但断线 · 不显示风险分</span></header>
    <div class="filters" aria-label="学业证据筛选"><label><span>时间</span><select v-model="timeFilter"><option value="all">全部时间</option><option value="recent_90">最近 90 天</option><option value="year">最近学年</option></select></label><label><span>考试系列</span><select v-model="seriesFilter"><option value="">全部系列</option><option v-for="item in seriesOptions" :key="item" :value="item">{{ item }}</option></select></label><label><span>学科</span><select v-model="subjectFilter"><option value="">全部学科</option><option v-for="item in subjectOptions" :key="item" :value="item">{{ item }}</option></select></label><label class="check"><input v-model="comparableOnly" type="checkbox"><span>只看可比证据</span></label></div>
    <div class="summary"><div><strong>{{ filteredSessions.length }}</strong><span>考试场次</span></div><div><strong>{{ analysis?.series.length ?? 0 }}</strong><span>学科序列</span></div><div><strong>{{ pairCount }}</strong><span>可比位次对</span></div><div><strong>{{ analysis?.attention_cards.filter(card=>card.state==='draft').length ?? 0 }}</strong><span>待教师决定</span></div></div>
    <div class="layout">
      <div class="charts">
        <figure><figcaption><strong>1. 总体相对位次如何变化？</strong><span>只有后端判定可比的相邻点才连线</span></figcaption><div ref="rankChart" class="chart" role="img" aria-label="相对位次趋势图"></div></figure>
        <figure><figcaption><strong>2. 哪些学科前移或后移？</strong><span>圆点为前次，菱形为本次</span></figcaption><div ref="pairChart" class="chart chart--short" role="img" aria-label="学科位次变化图"></div></figure>
        <figure><figcaption><strong>3. 哪些证据可直接比较？</strong><span>空心点、菱形与琥珀色表示特殊或信息不完整</span></figcaption><div ref="timelineChart" class="chart chart--short" role="img" aria-label="证据时间带"></div></figure>
        <section class="signals"><h3>4. 相对学科线索</h3><div v-for="signal in analysis?.relative_subject_signals" :key="signal.subject_name"><strong>{{ signal.subject_name }}</strong><span>{{ signal.signal }} · {{ signal.eligible_session_count }} 个合格场次</span></div><p v-for="reasonItem in analysis?.insufficient_reasons" :key="reasonItem">{{ reasonItem }}</p></section>
        <details><summary>键盘可访问的数据点</summary><template v-for="session in filteredSessions" :key="session.session_id"><button v-for="point in session.evidence.filter((item)=>!subjectFilter || item.subject_name===subjectFilter)" :key="point.evidence_version_id" type="button" @click="inspect(point.evidence_version_id)">{{ session.occurred_on }} · {{ session.title }} · {{ point.subject_name }} · {{ stateLabels[point.result_state] ?? point.result_state }} · {{ point.score ?? '无分数' }}</button></template></details>
      </div>
      <aside>
        <section><p class="eyebrow">证据检查器</p><template v-if="selectedEvidence"><h3>{{ selectedEvidence.language }}</h3><dl><div><dt>考试</dt><dd>{{ selectedEvidence.evidence.title }}</dd></div><div><dt>日期</dt><dd>{{ selectedEvidence.evidence.occurred_on }}</dd></div><div><dt>成绩状态</dt><dd>{{ selectedEvidence.evidence.result_state }}</dd></div><div><dt>分数</dt><dd>{{ selectedEvidence.evidence.score ?? '无数值' }}</dd></div></dl><p>原始文件：未保留；来源：教师确认的快照。</p></template><p v-else>点击图下的数据点，核对教师确认的证据快照。</p></section>
        <section v-for="card in analysis?.attention_cards.filter(item=>item.state==='draft')" :key="card.attention_card_id" class="attention"><p class="eyebrow">待教师决定</p><h3>{{ card.observed_fact }}</h3><p>{{ card.evidence_sufficiency }}</p><label><span>决定</span><select v-model="decision"><option value="follow_up">跟进</option><option value="observe">观察</option><option value="no_action">暂不行动</option></select></label><label><span>教师理由（必填）</span><textarea v-model="reason" rows="3"></textarea></label><label v-if="decision!=='no_action'"><span>复查日期（必填）</span><input v-model="reviewAt" type="date"></label><button type="button" :disabled="!reason.trim() || (decision!=='no_action'&&!reviewAt)" @click="decide(card)">保存教师决定</button></section>
        <p v-if="message" class="success">{{ message }}</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.academic{overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--color-border-subtle)}header p,.eyebrow{margin:0 0 2px;color:var(--color-accent);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}h2{margin:0;font-size:var(--font-size-h2)}header>span{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}.summary{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--color-border-subtle)}.summary div{display:flex;gap:var(--space-2);align-items:baseline;padding:var(--space-3) var(--space-5);border-right:1px solid var(--color-border-subtle)}.summary strong{font-size:var(--font-size-h2)}.summary span{color:var(--color-text-secondary)}.layout{display:grid;grid-template-columns:minmax(0,72fr) minmax(300px,28fr)}.charts{padding:var(--space-4)}figure{margin:0 0 var(--space-4);border-bottom:1px solid var(--color-border-subtle)}figcaption{display:flex;justify-content:space-between}figcaption span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.chart{height:300px}.chart--short{height:230px}.signals{padding:var(--space-4);background:var(--color-bg-subtle)}.signals h3{margin-top:0}.signals div{display:flex;justify-content:space-between;padding:var(--space-2) 0;border-bottom:1px solid var(--color-border-subtle)}.signals p{color:var(--color-text-secondary)}details{margin-top:var(--space-3)}details button{display:block;width:100%;padding:var(--space-2);border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left}.layout>aside{padding:var(--space-4);border-left:1px solid var(--color-border-default);background:var(--color-bg-subtle)}aside>section{margin-bottom:var(--space-4);padding:var(--space-4);background:var(--color-bg-surface);border:1px solid var(--color-border-default);border-radius:var(--radius-control)}aside h3{margin-top:0}dl div{display:flex;justify-content:space-between;padding:var(--space-1) 0}dd{margin:0;font-weight:650}.attention{border-left:3px solid var(--color-warning)}label{display:grid;gap:var(--space-1);margin-top:var(--space-3);font-weight:650}select,input,textarea,aside button{min-height:38px;padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}aside button{margin-top:var(--space-3)}.success{color:var(--color-success)}@media(max-width:1000px){.layout{grid-template-columns:1fr}.layout>aside{border-top:1px solid var(--color-border-default);border-left:0}}@media(max-width:700px){header{align-items:flex-start;flex-direction:column}.summary{grid-template-columns:repeat(2,1fr)}}
.filters{display:grid;grid-template-columns:repeat(3,minmax(150px,1fr)) auto;gap:var(--space-3);align-items:end;padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-subtle);background:var(--color-bg-subtle)}.filters label{display:grid;gap:var(--space-1);margin:0}.filters .check{display:flex;align-items:center;min-height:40px}.filters select{min-height:38px;padding:0 var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}@media(max-width:800px){.filters{grid-template-columns:1fr 1fr}}
</style>
