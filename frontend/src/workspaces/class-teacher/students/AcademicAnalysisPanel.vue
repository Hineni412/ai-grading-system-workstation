<script setup lang="ts">
import { LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import {
  studentR1Api,
  type AcademicAnalysis,
  type AcademicPoint,
  type AcademicProfilePoint,
  type AttentionCard,
  type DirectorySubject,
} from '../api/r1'

use([LineChart, AriaComponent, GridComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ subject: DirectorySubject }>()
const analysis = ref<AcademicAnalysis | null>(null)
const trendChart = ref<HTMLElement | null>(null)
const charts: ECharts[] = []
const decision = ref('observe')
const reason = ref('')
const reviewAt = ref('')
const message = ref('')
const expanded = ref<Set<string>>(new Set())
let loadSequence = 0

const trendLabels: Record<string, string> = { improving: '持续进步', declining: '持续退步', fluctuating: '起伏', flat: '持平', insufficient: '场次不足' }
const stabilityLabels: Record<string, string> = { stable: '稳定', moderate: '有波动', volatile: '波动大', insufficient: '场次不足' }
const skewLabels: Record<string, string> = { balanced: '总体均衡', skewed: '明显偏科', insufficient: '场次不足' }
const stateLabels: Record<string, string> = { normal: '正常', absent: '缺考', exempt: '免考', missing: '缺失', incomplete: '未完成', pending_review: '待核对', makeup: '补测' }
const gradeOrder: Record<string, number> = { 七年级: 7, 八年级: 8, 九年级: 9 }
const termOrder: Record<string, number> = { 上学期: 0, 下学期: 1 }

const profile = computed(() => analysis.value?.profile ?? null)

interface HeatRow { occurred_on: string; title: string; termLabel: string; cells: Record<string, AcademicPoint> }
const heatmap = computed(() => {
  const sessions = [...(analysis.value?.sessions ?? [])]
  sessions.sort((a, b) => {
    const chainA = (gradeOrder[a.grade ?? ''] ?? 99) * 10 + (termOrder[a.term ?? ''] ?? 99)
    const chainB = (gradeOrder[b.grade ?? ''] ?? 99) * 10 + (termOrder[b.term ?? ''] ?? 99)
    return chainA - chainB || a.occurred_on.localeCompare(b.occurred_on)
  })
  const rows: HeatRow[] = []
  const byDate = new Map<string, HeatRow>()
  const subjects = new Set<string>()
  for (const session of sessions) {
    let row = byDate.get(session.occurred_on)
    if (!row) {
      row = { occurred_on: session.occurred_on, title: session.title, termLabel: termShort(session), cells: {} }
      byDate.set(session.occurred_on, row)
      rows.push(row)
    }
    for (const point of session.evidence) {
      if (point.measure_role && point.measure_role !== 'subject_score') continue
      row.cells[point.subject_name] = point
      subjects.add(point.subject_name)
    }
  }
  return { rows, subjects: [...subjects].sort() }
})

const subjectRows = computed(() => {
  const rows = [...(profile.value?.subjects ?? [])]
  rows.sort((a, b) => Number(b.attention) - Number(a.attention) || a.subject_name.localeCompare(b.subject_name))
  return rows
})

function termShort(session: { grade?: string | null; term?: string | null }): string {
  const grade = (session.grade ?? '').replace('年级', '')
  const half = session.term === '上学期' ? '上' : session.term === '下学期' ? '下' : ''
  return grade && half ? `${grade}${half}` : ''
}
function rankDeltaText(delta: number | null | undefined): string {
  if (typeof delta !== 'number') return ''
  return delta > 0 ? `进步 ${delta} 名` : delta < 0 ? `退步 ${-delta} 名` : '名次持平'
}
function cellStyle(point: AcademicPoint | undefined): Record<string, string> {
  if (!point || point.relative_position == null || point.result_state !== 'normal') return { background: 'var(--muted)', color: 'var(--muted-foreground)' }
  const position = point.relative_position
  if (position >= 0.75) return { background: 'rgba(53,104,89,.22)' }
  if (position >= 0.4) return { background: 'transparent' }
  return { background: 'rgba(155,106,43,.24)' }
}
function sparkline(points: AcademicProfilePoint[]): string {
  const usable = points.filter((point) => point.relative_position != null)
  if (usable.length < 2) return ''
  const width = 120
  const height = 28
  const step = width / (usable.length - 1)
  return usable
    .map((point, index) => `${(index * step).toFixed(1)},${(height - 3 - (point.relative_position ?? 0) * (height - 6)).toFixed(1)}`)
    .join(' ')
}
function toggle(subjectName: string) {
  const next = new Set(expanded.value)
  if (next.has(subjectName)) next.delete(subjectName)
  else next.add(subjectName)
  expanded.value = next
}

async function load() {
  const sequence = ++loadSequence
  const next = await studentR1Api.academic(props.subject.subject_id)
  if (sequence !== loadSequence) return
  analysis.value = next
  expanded.value = new Set((next.profile?.subjects ?? []).filter((item) => item.attention).map((item) => item.subject_name))
  await nextTick(); render()
}
function formatTopRatio(ratio: number): string {
  const percent = ratio * 100
  return percent >= 10 ? `${Math.round(percent)}%` : `${Math.round(percent * 10) / 10}%`
}
function render() {
  charts.splice(0).forEach((item) => item.dispose())
  if (!analysis.value || !trendChart.value) return
  const totals = (profile.value?.total_trend ?? [])
  const labels = totals.map((point) => point.term_label || String(point.occurred_on ?? ''))
  const instance = init(trendChart.value)
  instance.setOption({
    aria: { enabled: true },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { dataIndex: number }[]) => {
        const point = totals[params[0]?.dataIndex ?? 0]
        if (!point) return ''
        const rank = point.rank != null ? `第 ${point.rank} 名` : (stateLabels[String(point.result_state)] ?? '无名次')
        const total = point.participant_count ? ` / 共 ${point.participant_count} 人` : ''
        return `${point.session_title ?? ''}（${point.occurred_on ?? ''}）<br/>校次：${rank}${total}`
      },
    },
    grid: { left: 46, right: 18, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: labels },
    yAxis: { type: 'value', inverse: true, name: '校次', min: 1 },
    series: [{
      name: '总分校次',
      type: 'line',
      symbol: 'circle',
      symbolSize: 10,
      connectNulls: false,
      data: totals.map((point) => (point.result_state === 'normal' ? point.rank : null)),
    }],
  })
  charts.push(instance)
}
async function decide(card: AttentionCard) { if (!analysis.value || !reason.value.trim()) return; await studentR1Api.decideAttention(card, analysis.value, { decision: decision.value, reason: reason.value, reviewAt: decision.value === 'no_action' ? null : reviewAt.value || null, planId: null }); message.value = '教师决定已保存；需要跟进时只生成一条匿名待办。'; await load() }
function resize() { charts.forEach((item) => item.resize()) }
onMounted(() => { void load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach((item) => item.dispose()) })
</script>

<template>
  <section class="academic">
    <header><div><p>学业画像</p><h2>{{ subject.display_name }} 的大考表现</h2></div><span>名次以校次为主 · 只比较同年级同口径场次 · 缺考不计入</span></header>

    <section v-if="profile?.current" class="standing" aria-label="当前定位">
      <div class="standing__exam">
        <span v-if="profile.current.term_label" class="term">{{ profile.current.term_label }}</span>
        <strong>{{ profile.current.session_title }}</strong>
        <span>{{ profile.current.occurred_on }}</span>
      </div>
      <div class="standing__metrics">
        <div v-if="profile.current.score != null"><strong>{{ profile.current.score }}</strong><span>总分</span></div>
        <div><strong>第 {{ profile.current.rank }} 名</strong><span>校次 · 共 {{ profile.current.participant_count }} 人<template v-if="profile.current.top_ratio != null"> · 前 {{ formatTopRatio(profile.current.top_ratio) }}</template></span></div>
        <div v-if="profile.current.class_rank != null"><strong>第 {{ profile.current.class_rank }} 名</strong><span>班次</span></div>
        <div v-if="profile.current.rank_delta != null && profile.current.previous"><strong>{{ rankDeltaText(profile.current.rank_delta) }}</strong><span>较{{ profile.current.previous.term_label || '前一场' }}（第 {{ profile.current.previous.rank }} 名）</span></div>
      </div>
      <div class="standing__tags">
        <span class="tag" :title="`最近 ${profile.trend.session_count} 场同年级总分校次`">趋势：{{ trendLabels[profile.trend.label] ?? profile.trend.label }}</span>
        <span class="tag" :title="profile.stability.swing_ratio != null ? `近几场校次振幅约 ${Math.round(profile.stability.swing_ratio * 100)}%` : ''">稳定性：{{ stabilityLabels[profile.stability.label] ?? profile.stability.label }}</span>
        <span class="tag" :title="profile.skew.label === 'skewed' ? `最强 ${profile.skew.strongest.map((item) => item.subject_name).join('、')}，最弱 ${profile.skew.weakest.map((item) => item.subject_name).join('、')}` : ''">偏科：{{ skewLabels[profile.skew.label] ?? profile.skew.label }}</span>
      </div>
    </section>
    <p v-else-if="analysis" class="empty">还没有带校次的总分成绩，导入大考成绩表后这里会显示定位。</p>

    <div class="layout">
      <div class="charts">
        <figure v-if="profile?.current"><figcaption><strong>总分校次走势</strong><span>按学期链条排列；跨年级名次不直接比较</span></figcaption><div ref="trendChart" class="chart" role="img" aria-label="总分校次走势图"></div></figure>

        <section v-if="heatmap.rows.length" class="heat" aria-label="学科位次热力表">
          <h3>历次大考学科校次</h3>
          <table>
            <thead><tr><th>考试</th><th v-for="name in heatmap.subjects" :key="name">{{ name }}</th></tr></thead>
            <tbody>
              <tr v-for="row in heatmap.rows" :key="row.occurred_on">
                <th scope="row"><span v-if="row.termLabel" class="term">{{ row.termLabel }}</span>{{ row.title }}</th>
                <td v-for="name in heatmap.subjects" :key="name" :style="cellStyle(row.cells[name])">
                  <template v-if="row.cells[name]">{{ row.cells[name].result_state === 'normal' ? (row.cells[name].rank ?? '—') : (stateLabels[row.cells[name].result_state] ?? row.cells[name].result_state) }}</template>
                  <template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="hint">颜色越绿校次越靠前，琥珀色表示落在年级后 60%；灰格为缺考或无有效名次。</p>
        </section>

        <section v-if="subjectRows.length" class="subjects" aria-label="学科明细">
          <h3>学科明细</h3>
          <div v-for="row in subjectRows" :key="row.subject_name" class="subject" :class="{ 'subject--attention': row.attention }">
            <button type="button" @click="toggle(row.subject_name)">
              <strong>{{ row.subject_name }}</strong>
              <span v-if="row.latest?.rank != null">最近校次 第 {{ row.latest.rank }} 名<template v-if="row.latest.participant_count"> / {{ row.latest.participant_count }} 人</template></span>
              <span v-else>暂无有效名次</span>
              <span v-if="row.rank_delta != null" class="delta">{{ rankDeltaText(row.rank_delta) }}</span>
              <span v-if="row.attention" class="mark">需要关注</span>
            </button>
            <svg v-if="expanded.has(row.subject_name) && sparkline(row.points)" class="spark" viewBox="0 0 120 28" role="img" :aria-label="`${row.subject_name}近期位次走势`">
              <polyline :points="sparkline(row.points)" fill="none" stroke="var(--primary)" stroke-width="2" />
            </svg>
            <p v-if="expanded.has(row.subject_name)" class="detail">
              <template v-for="point in row.points" :key="`${point.occurred_on}-${point.rank}`">
                <span>{{ point.term_label || point.occurred_on }} · {{ point.result_state === 'normal' ? (point.rank != null ? `第 ${point.rank} 名` : '无名次') : (stateLabels[String(point.result_state)] ?? point.result_state) }}</span>
              </template>
            </p>
          </div>
        </section>
        <p v-for="reasonItem in analysis?.insufficient_reasons" :key="reasonItem" class="hint">{{ reasonItem }}</p>
      </div>

      <aside>
        <section v-for="card in analysis?.attention_cards.filter(item=>item.state==='draft')" :key="card.attention_card_id" class="attention"><p class="eyebrow">待教师决定</p><h3>{{ card.observed_fact }}</h3><p>{{ card.evidence_sufficiency }}</p><label><span>决定</span><select v-model="decision"><option value="follow_up">跟进</option><option value="observe">观察</option><option value="no_action">暂不行动</option></select></label><label><span>教师理由（必填）</span><textarea v-model="reason" rows="3"></textarea></label><label v-if="decision!=='no_action'"><span>复查日期（必填）</span><input v-model="reviewAt" type="date"></label><AppButton variant="primary" :disabled="!reason.trim() || (decision!=='no_action'&&!reviewAt)" @click="decide(card)">保存教师决定</AppButton></section>
        <p v-if="message" class="success">{{ message }}</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.academic{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}header p,.eyebrow{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}h2{margin:0;font-size:var(--font-size-h2)}header>span{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.standing{display:grid;grid-template-columns:minmax(200px,1.2fr) 2fr;gap:var(--space-4);padding:var(--space-4) var(--space-5);border-bottom:1px solid var(--border)}.standing__exam{display:grid;gap:var(--space-1);align-content:start}.standing__exam strong{font-size:var(--font-size-h3)}.standing__exam span:last-child{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.term{display:inline-block;width:max-content;padding:1px var(--space-2);border-radius:var(--radius);background:var(--accent);color:var(--primary);font-size:var(--font-size-caption);font-weight:700}.standing__metrics{display:flex;flex-wrap:wrap;gap:var(--space-4)}.standing__metrics div{display:grid;gap:2px}.standing__metrics strong{font-size:var(--font-size-h2);color:var(--primary)}.standing__metrics span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.standing__tags{grid-column:1/-1;display:flex;gap:var(--space-2)}.tag{padding:var(--space-1) var(--space-3);border:1px solid var(--border);border-radius:999px;background:var(--card);font-size:var(--font-size-dense)}
.empty{padding:var(--space-5);color:var(--color-text-secondary)}
.layout{display:grid;grid-template-columns:minmax(0,72fr) minmax(300px,28fr)}.charts{padding:var(--space-4)}figure{margin:0 0 var(--space-4);padding-bottom:var(--space-3);border-bottom:1px solid var(--color-border-subtle)}figcaption{display:flex;justify-content:space-between}figcaption span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.chart{height:260px}
.heat{margin-bottom:var(--space-4)}.heat h3,.subjects h3{margin:0 0 var(--space-2)}.heat table{width:100%;border-collapse:collapse;font-size:var(--font-size-dense)}.heat th,.heat td{padding:var(--space-1) var(--space-2);border:1px solid var(--color-border-subtle);text-align:center}.heat th[scope="row"]{text-align:left;font-weight:600}.heat thead th{background:var(--muted)}.heat .term{margin-right:var(--space-1)}.hint{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.subjects{display:grid;gap:var(--space-2)}.subject{border:1px solid var(--color-border-subtle);border-radius:var(--radius)}.subject--attention{border-color:rgba(155,106,43,.55)}.subject>button{display:flex;align-items:baseline;gap:var(--space-3);width:100%;padding:var(--space-2) var(--space-3);border:0;background:transparent;font:inherit;text-align:left;cursor:pointer}.subject>button span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.subject .delta{color:var(--primary);font-weight:600}.subject .mark{margin-left:auto;padding:0 var(--space-2);border-radius:999px;background:rgba(155,106,43,.16);color:#9b6a2b;font-size:var(--font-size-caption);font-weight:700}.spark{display:block;margin:0 var(--space-3)}.detail{display:flex;flex-wrap:wrap;gap:var(--space-2);margin:0;padding:0 var(--space-3) var(--space-2);color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.layout>aside{padding:var(--space-4);border-left:1px solid var(--border);background:var(--muted)}aside>section{margin-bottom:var(--space-4)}.attention label{display:grid;gap:var(--space-1);margin:var(--space-2) 0}.attention select,.attention textarea,.attention input{border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;padding:var(--space-1) var(--space-2)}.success{color:var(--primary);font-weight:600}
@media(max-width:900px){.layout{grid-template-columns:1fr}.layout>aside{border-left:0;border-top:1px solid var(--border)}.standing{grid-template-columns:1fr}}
</style>
