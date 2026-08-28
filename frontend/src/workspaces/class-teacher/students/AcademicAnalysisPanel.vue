<script setup lang="ts">
import { BarChart, LineChart, RadarChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import {
  studentR1Api,
  studentRefOf,
  type AcademicAnalysis,
  type AcademicPoint,
  type AcademicProfilePoint,
  type AttentionCard,
  type DirectorySubject,
} from '../api/r1'

use([BarChart, LineChart, RadarChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ subject: DirectorySubject }>()
const analysis = ref<AcademicAnalysis | null>(null)
const trendChart = ref<HTMLElement | null>(null)
const radarChart = ref<HTMLElement | null>(null)
const deviationChart = ref<HTMLElement | null>(null)
const charts: ECharts[] = []
const decision = ref('observe')
const reason = ref('')
const reviewAt = ref('')
const message = ref('')
let loadSequence = 0

const trendLabels: Record<string, string> = { improving: '持续进步', declining: '持续退步', fluctuating: '起伏', flat: '持平', insufficient: '场次不足' }
const stabilityLabels: Record<string, string> = { stable: '稳定', moderate: '有波动', volatile: '波动大', insufficient: '场次不足' }
const skewLabels: Record<string, string> = { balanced: '总体均衡', skewed: '明显偏科', insufficient: '场次不足' }
const stateLabels: Record<string, string> = { normal: '正常', absent: '缺考', exempt: '免考', missing: '缺失', incomplete: '未完成', pending_review: '待核对', makeup: '补测' }
const gradeOrder: Record<string, number> = { 七年级: 7, 八年级: 8, 九年级: 9 }
const termOrder: Record<string, number> = { 上学期: 0, 下学期: 1 }
// 等级徽章配色与总览等级分布图一致
const GRADE_BADGE_STYLE: Record<string, { background: string; color: string }> = {
  'A+': { background: '#8fd0bd', color: '#14532d' },
  A: { background: '#2e7d5b', color: '#ffffff' },
  'B+': { background: '#4a7fb5', color: '#ffffff' },
  B: { background: '#c9a227', color: '#3d2f00' },
  'C+': { background: '#c05a2e', color: '#ffffff' },
  C: { background: '#8e979e', color: '#ffffff' },
}
// 同学期内期中<期末，其他性质排最后；与后端 session_order 同口径
function phaseOrder(session: { exam_type?: string | null; title?: string | null }): number {
  for (const source of [session.exam_type, session.title]) {
    const text = String(source ?? '')
    if (text.includes('期中')) return 0
    if (text.includes('期末')) return 1
  }
  return 2
}

const profile = computed(() => analysis.value?.profile ?? null)

interface HeatRow { sessionId: string; title: string; shortLabel: string; cells: Record<string, AcademicPoint> }
const heatmap = computed(() => {
  const sessions = [...(analysis.value?.sessions ?? [])]
  sessions.sort((a, b) => {
    const chainA = (gradeOrder[a.grade ?? ''] ?? 99) * 100 + (termOrder[a.term ?? ''] ?? 99) * 10 + phaseOrder(a)
    const chainB = (gradeOrder[b.grade ?? ''] ?? 99) * 100 + (termOrder[b.term ?? ''] ?? 99) * 10 + phaseOrder(b)
    return chainA - chainB || a.occurred_on.localeCompare(b.occurred_on) || a.session_id.localeCompare(b.session_id)
  })
  // 每场次一行：不同场次即使 occurred_on 相同也不合并（登记日期不可靠）
  const rows: HeatRow[] = []
  const subjects = new Set<string>()
  for (const session of sessions) {
    const row: HeatRow = {
      sessionId: session.session_id,
      title: session.title,
      shortLabel: session.short_label || termShort(session),
      cells: {},
    }
    for (const point of session.evidence) {
      if (point.measure_role && point.measure_role !== 'subject_score') continue
      row.cells[point.subject_name] = point
      subjects.add(point.subject_name)
    }
    rows.push(row)
  }
  return { rows, subjects: [...subjects].sort() }
})

const subjectRows = computed(() => {
  const rows = [...(profile.value?.subjects ?? [])]
  rows.sort((a, b) => Number(b.attention) - Number(a.attention) || a.subject_name.localeCompare(b.subject_name))
  return rows
})

const latestSession = computed(() => {
  const sessions = [...(analysis.value?.sessions ?? [])]
  sessions.sort((a, b) => a.occurred_on.localeCompare(b.occurred_on))
  return sessions[sessions.length - 1] ?? null
})

// 标准分雷达数据：Z =（学生得分 − 班级均分）÷ 班级标准差；班级基准即 Z=0。
interface SubjectZ { subject_name: string; z: number; score: number | null; rate: number | null }
const subjectZScores = ref<SubjectZ[]>([])
const radarSkipped = ref(0)

async function prepareRadar(): Promise<void> {
  subjectZScores.value = []
  radarSkipped.value = 0
  const session = latestSession.value
  if (!session) return
  const candidates = session.evidence.filter((point) =>
    (!point.measure_role || point.measure_role === 'subject_score')
    && point.subject_name !== '总分'
    && point.result_state === 'normal'
    && point.score != null)
  if (!candidates.length) return
  let classResults = null
  try {
    classResults = await studentR1Api.sessionClassResults(session.session_id)
  } catch {
    classResults = null
  }
  const dimensions: SubjectZ[] = []
  for (const point of candidates) {
    const stats = classResults?.subjects.find((item) => item.subject_name === point.subject_name)
    const average = stats?.stats.average
    const stddev = stats?.stats.stddev
    // 缺班级均分或标准差（如只有 1 个有效分数）的科目不参与标准分
    if (!stats || average == null || !stddev) {
      radarSkipped.value += 1
      continue
    }
    dimensions.push({
      subject_name: point.subject_name,
      z: Math.round(((point.score! - average) / stddev) * 100) / 100,
      score: point.score,
      rate: point.max_score ? Math.round((point.score! / point.max_score) * 1000) / 10 : null,
    })
  }
  subjectZScores.value = dimensions
}

// 偏离个人均线：该科 Z − 学生各科 Z 的均值；右侧为相对优势。
const deviations = computed(() => {
  const items = subjectZScores.value
  if (items.length < 2) return []
  const mean = items.reduce((sum, item) => sum + item.z, 0) / items.length
  return items.map((item) => ({
    subject_name: item.subject_name,
    dev: Math.round((item.z - mean) * 100) / 100,
  }))
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
// 学科卡右侧的常显小折线：按相对位置（校次越靠前越高）画细线加小点。
function sparklinePoints(points: AcademicProfilePoint[]): Array<{ x: number; y: number }> {
  const usable = points.filter((point) => point.relative_position != null)
  if (usable.length < 2) return []
  const width = 120
  const height = 36
  const padX = 3
  const padY = 4
  const step = (width - padX * 2) / (usable.length - 1)
  return usable.map((point, index) => ({
    x: Math.round((padX + index * step) * 10) / 10,
    y: Math.round((height - padY - (point.relative_position ?? 0) * (height - padY * 2)) * 10) / 10,
  }))
}
function sparklinePath(points: AcademicProfilePoint[]): string {
  return sparklinePoints(points).map((point) => `${point.x},${point.y}`).join(' ')
}
// 最近一场等级徽章：取最新一条带等级的点
function latestGrade(points: AcademicProfilePoint[]): string | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const level = points[index]?.grade_level
    if (level) return level
  }
  return null
}
function gradeBadgeStyle(level: string): { background: string; color: string } {
  return GRADE_BADGE_STYLE[level] ?? { background: 'var(--muted)', color: 'var(--muted-foreground)' }
}
// 迷你时间线：等距节点圆点 + 上方「短标签·校次」，场次多于 6 个时横向滚动
const TL_SPACING = 72
interface TimelineNode { x: number; label: string; normal: boolean }
function timelineWidth(points: AcademicProfilePoint[]): number {
  return Math.max(points.length, 1) * TL_SPACING
}
function timelineNodes(points: AcademicProfilePoint[]): TimelineNode[] {
  return points.map((point, index) => ({
    x: TL_SPACING / 2 + index * TL_SPACING,
    label: [
      point.short_label || point.term_label || point.occurred_on || '',
      point.result_state === 'normal'
        ? (point.rank != null ? point.rank : '—')
        : (stateLabels[String(point.result_state)] ?? '—'),
    ].join('·'),
    normal: point.result_state === 'normal',
  }))
}

// 科目简评口径：前百分比 = 校次 / 年级人数（越小越靠前）。
// 优势/劣势：最新一场该科前百分比与总分前百分比相差 10 个百分点以上；
// 波动较大：历次前百分比极差 ≥ 20 个百分点（有效场次 < 2 不判定）。
// 接口缺年级人数或相对位置的科目/场次跳过。points 不带年级人数时按 1 - relative_position 折算。
const REVIEW_GAP = 0.1
const REVIEW_SWING = 0.2
const REVIEW_EPS = 1e-9
function frontRatio(point: { rank?: number | null; participant_count?: number | null; relative_position?: number | null }): number | null {
  if (point.rank != null && point.participant_count != null && point.participant_count > 0) return point.rank / point.participant_count
  if (point.relative_position != null) return 1 - point.relative_position
  return null
}
const subjectReview = computed(() => {
  const review = { strengths: [] as string[], weaknesses: [] as string[], swings: [] as string[], evaluated: 0 }
  const profileValue = profile.value
  if (!profileValue) return review
  const current = profileValue.current
  const totalRatio = current
    ? current.top_ratio ?? (current.rank != null && current.participant_count ? current.rank / current.participant_count : null)
    : null
  for (const subject of profileValue.subjects) {
    const ratios = subject.points
      .filter((point) => point.result_state === 'normal')
      .map((point) => frontRatio(point))
      .filter((ratio): ratio is number => ratio != null)
    if (ratios.length >= 2 && Math.max(...ratios) - Math.min(...ratios) >= REVIEW_SWING - REVIEW_EPS) review.swings.push(subject.subject_name)
    if (totalRatio == null) continue
    const latestRatio = subject.latest && subject.latest.result_state === 'normal' ? frontRatio(subject.latest) : null
    if (latestRatio == null) continue
    review.evaluated += 1
    if (totalRatio - latestRatio >= REVIEW_GAP - REVIEW_EPS) review.strengths.push(subject.subject_name)
    else if (latestRatio - totalRatio >= REVIEW_GAP - REVIEW_EPS) review.weaknesses.push(subject.subject_name)
  }
  return review
})

async function load() {
  const sequence = ++loadSequence
  const next = await studentR1Api.academic(studentRefOf(props.subject))
  if (sequence !== loadSequence) return
  analysis.value = next
  await prepareRadar()
  await nextTick(); render()
}
function formatTopRatio(ratio: number): string {
  const percent = ratio * 100
  return percent >= 10 ? `${Math.round(percent)}%` : `${Math.round(percent * 10) / 10}%`
}
function render() {
  charts.splice(0).forEach((item) => item.dispose())
  if (!analysis.value) return
  if (trendChart.value) {
    const totals = (profile.value?.total_trend ?? [])
    const labels = totals.map((point) => point.short_label || point.term_label || String(point.occurred_on ?? ''))
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
          return `${point.session_title ?? ''}<br/>校次：${rank}${total}`
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
  if (subjectZScores.value.length && radarChart.value) {
    const items = subjectZScores.value
    const maxAbs = Math.max(...items.map((item) => Math.abs(item.z)), 1)
    const span = Math.max(2, Math.ceil((maxAbs + 0.4) * 2) / 2)
    const radar = init(radarChart.value)
    radar.setOption({
      aria: { enabled: true },
      tooltip: {
        formatter: (params: { seriesName: string; value: number[] }) => {
          if (params.seriesName !== '学生标准分') return params.seriesName
          return items
            .map((item, index) => {
              const rate = item.rate != null ? `（得分率 ${item.rate}%）` : ''
              return `${item.subject_name}：${item.score ?? '—'} 分${rate}，Z=${params.value[index]}`
            })
            .join('<br/>')
        },
      },
      legend: { top: 0 },
      radar: {
        indicator: items.map((item) => ({ name: item.subject_name, min: -span, max: span })),
        radius: '68%',
        center: ['50%', '56%'],
      },
      series: [{
        type: 'radar',
        data: [
          { name: '学生标准分', value: items.map((item) => item.z) },
          // 0 基准圆环即班级平均
          { name: '班级平均（Z=0）', value: items.map(() => 0), lineStyle: { type: 'dashed' }, symbol: 'none' },
        ],
      }],
    })
    charts.push(radar)
  }
  if (deviations.value.length && deviationChart.value) {
    const devs = deviations.value
    const maxAbs = Math.max(...devs.map((item) => Math.abs(item.dev)), 0.5)
    const span = Math.ceil((maxAbs + 0.2) * 10) / 10
    const chart = init(deviationChart.value)
    chart.setOption({
      aria: { enabled: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: Array<{ name: string; value: number }>) => {
          const item = params[0]
          if (!item) return ''
          const value = item.value
          return `${item.name}：${value > 0 ? '+' : ''}${value}（相对个人均线）`
        },
      },
      grid: { left: 64, right: 44, top: 12, bottom: 24 },
      xAxis: { type: 'value', min: -span, max: span },
      yAxis: { type: 'category', data: devs.map((item) => item.subject_name) },
      series: [{
        name: '偏离个人均线',
        type: 'bar',
        barWidth: 14,
        data: devs.map((item) => ({
          value: item.dev,
          itemStyle: { color: item.dev >= 0 ? '#2e7d5b' : '#b03a2e' },
          label: {
            show: true,
            position: item.dev >= 0 ? 'right' : 'left',
            formatter: `${item.dev > 0 ? '+' : ''}${item.dev}`,
          },
        })),
        markLine: {
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: '#8e979e' },
          data: [{ xAxis: 0 }],
        },
      }],
    })
    charts.push(chart)
  }
}
async function decide(card: AttentionCard) { if (!analysis.value || !reason.value.trim()) return; await studentR1Api.decideAttention(card, analysis.value, { decision: decision.value, reason: reason.value, reviewAt: decision.value === 'no_action' ? null : reviewAt.value || null, planId: null }); message.value = '教师决定已保存；需要跟进时只生成一条匿名待办。'; await load() }
function resize() { charts.forEach((item) => item.resize()) }
onMounted(() => { void load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach((item) => item.dispose()) })
</script>

<template>
  <section class="academic">
    <header><div><p>学业画像</p><h2>{{ subject.display_name }} 的大考表现</h2></div><span>名次以校次为主 · 只比较同年级同口径场次 · 缺考不计入</span></header>

    <p v-if="analysis && !profile?.current" class="empty">还没有带校次的总分成绩，导入大考成绩表后这里会显示定位。</p>

    <div class="layout">
      <div class="col-main">
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
            <div v-if="profile.current.rank_delta != null && profile.current.previous"><strong>{{ rankDeltaText(profile.current.rank_delta) }}</strong><span>较{{ profile.current.previous.short_label || profile.current.previous.term_label || '前一场' }}（第 {{ profile.current.previous.rank }} 名）</span></div>
          </div>
          <div class="standing__tags">
            <span class="tag" :title="`最近 ${profile.trend.session_count} 场同年级总分校次`">趋势：{{ trendLabels[profile.trend.label] ?? profile.trend.label }}</span>
            <span class="tag" :title="profile.stability.swing_ratio != null ? `近几场校次振幅约 ${Math.round(profile.stability.swing_ratio * 100)}%` : ''">稳定性：{{ stabilityLabels[profile.stability.label] ?? profile.stability.label }}</span>
            <span class="tag" :title="profile.skew.label === 'skewed' ? `最强 ${profile.skew.strongest.map((item) => item.subject_name).join('、')}，最弱 ${profile.skew.weakest.map((item) => item.subject_name).join('、')}` : ''">偏科：{{ skewLabels[profile.skew.label] ?? profile.skew.label }}</span>
          </div>
          <div class="standing__review" title="按校次前百分比（校次 / 年级人数）比较：与总分相差 10 个百分点以上记为优势或劣势；历次极差 20 个百分点以上记为波动较大。缺年级人数的科目不纳入。">
            <span class="review__label">科目简评</span>
            <template v-if="subjectReview.strengths.length || subjectReview.weaknesses.length || subjectReview.swings.length">
              <span v-if="subjectReview.strengths.length" class="review__group"><b>优势</b>：{{ subjectReview.strengths.join('、') }}</span>
              <span v-if="subjectReview.weaknesses.length" class="review__group"><b>劣势</b>：{{ subjectReview.weaknesses.join('、') }}</span>
              <span v-if="subjectReview.swings.length" class="review__group"><b>波动</b>：{{ subjectReview.swings.join('、') }}</span>
            </template>
            <span v-else class="review__group">{{ subjectReview.evaluated ? '总体均衡' : '暂无足够数据' }}</span>
          </div>
        </section>

        <figure v-if="profile?.current"><figcaption><strong>总分校次走势</strong><span>按学期链条排列；跨年级名次不直接比较</span></figcaption><div ref="trendChart" class="chart" role="img" aria-label="总分校次走势图"></div></figure>

        <div class="charts-row">
          <figure v-if="latestSession">
            <figcaption><strong>学科标准分雷达</strong><span>最新一场 · Z=（得分−班级均分）÷班级标准差，Z=0 即班级平均</span></figcaption>
            <div v-if="subjectZScores.length" ref="radarChart" class="chart chart--radar" role="img" aria-label="学科标准分雷达图"></div>
            <p v-else class="hint">暂无足够的班级统计数据生成标准分雷达。</p>
            <p v-if="subjectZScores.length && radarSkipped" class="hint">部分科目因缺班级标准差未显示。</p>
          </figure>
          <figure v-if="deviations.length">
            <figcaption><strong>偏离个人均线</strong><span>各科标准分 − 个人各科均值；右侧为相对优势</span></figcaption>
            <div ref="deviationChart" class="chart chart--radar" role="img" aria-label="学科偏离条形图"></div>
          </figure>
        </div>
      </div>

      <div class="col-side">
        <section v-if="subjectRows.length" class="subjects" aria-label="学科明细">
          <h3>学科明细</h3>
          <div v-for="row in subjectRows" :key="row.subject_name" class="subject" :class="{ 'subject--attention': row.attention }">
            <div class="subject__main">
              <div class="subject__row1">
                <strong>{{ row.subject_name }}</strong>
                <span v-if="row.attention" class="mark">需要关注</span>
                <span v-if="latestGrade(row.points)" class="grade-badge" :style="gradeBadgeStyle(latestGrade(row.points)!)">{{ latestGrade(row.points) }}</span>
                <span v-if="row.latest?.rank != null" class="subject__rank-big">第 {{ row.latest.rank }} 名<small v-if="row.latest.participant_count"> / {{ row.latest.participant_count }} 人</small></span>
                <span v-else class="subject__rank">暂无有效名次</span>
                <span v-if="row.rank_delta != null" class="delta-badge" :class="row.rank_delta > 0 ? 'delta-badge--up' : row.rank_delta < 0 ? 'delta-badge--down' : 'delta-badge--flat'">{{ row.rank_delta > 0 ? '↑' : row.rank_delta < 0 ? '↓' : '＝' }}{{ row.rank_delta === 0 ? '' : Math.abs(row.rank_delta) }}</span>
              </div>
              <svg v-if="sparklinePoints(row.points).length" class="spark" viewBox="0 0 120 36" role="img" :aria-label="`${row.subject_name}近期位次走势`">
                <polyline :points="sparklinePath(row.points)" fill="none" stroke="var(--primary)" stroke-width="1.5" />
                <circle v-for="(point, index) in sparklinePoints(row.points)" :key="index" :cx="point.x" :cy="point.y" r="1.8" fill="var(--primary)" />
              </svg>
            </div>
            <div v-if="row.points.length" class="timeline-wrap">
              <svg class="timeline" :width="timelineWidth(row.points)" height="46" :viewBox="`0 0 ${timelineWidth(row.points)} 46`" role="img" :aria-label="`${row.subject_name}历次校次时间线`">
                <line v-if="row.points.length > 1" :x1="TL_SPACING / 2" :x2="timelineWidth(row.points) - TL_SPACING / 2" y1="30" y2="30" class="tl-line" />
                <g v-for="node in timelineNodes(row.points)" :key="node.x">
                  <text :x="node.x" y="14" text-anchor="middle" class="tl-label">{{ node.label }}</text>
                  <circle :cx="node.x" cy="30" r="3.5" :class="node.normal ? 'tl-dot' : 'tl-dot tl-dot--void'" />
                </g>
              </svg>
            </div>
          </div>
        </section>

        <section v-if="heatmap.rows.length" class="heat" aria-label="学科位次热力表">
          <h3>历次大考学科校次</h3>
          <table>
            <thead><tr><th>考试</th><th v-for="name in heatmap.subjects" :key="name">{{ name }}</th></tr></thead>
            <tbody>
              <tr v-for="row in heatmap.rows" :key="row.sessionId">
                <th scope="row"><span v-if="row.shortLabel" class="term">{{ row.shortLabel }}</span>{{ row.title }}</th>
                <td v-for="name in heatmap.subjects" :key="name" :style="cellStyle(row.cells[name])">
                  <template v-if="row.cells[name]">{{ row.cells[name].result_state === 'normal' ? (row.cells[name].rank ?? '—') : (stateLabels[row.cells[name].result_state] ?? row.cells[name].result_state) }}</template>
                  <template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="hint">颜色越绿校次越靠前，琥珀色表示落在年级后 60%；灰格为缺考或无有效名次。</p>
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
.standing{display:grid;grid-template-columns:minmax(200px,1.2fr) 2fr;gap:var(--space-4);margin-bottom:var(--space-4);padding-bottom:var(--space-4);border-bottom:1px solid var(--color-border-subtle)}.standing__exam{display:grid;gap:var(--space-1);align-content:start;padding:var(--space-3);border:1px solid var(--color-border-subtle);border-left:3px solid var(--accent);border-radius:var(--radius);background:var(--muted)}.standing__exam strong{font-size:var(--font-size-h3)}.standing__exam span:last-child{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.term{display:inline-block;width:max-content;padding:1px var(--space-2);border-radius:var(--radius);background:var(--accent);color:var(--primary);font-size:var(--font-size-caption);font-weight:700}.standing__metrics{display:flex;flex-wrap:wrap;gap:var(--space-4);align-content:center}.standing__metrics div{display:grid;gap:2px}.standing__metrics strong{font-size:var(--font-size-h2);color:var(--primary)}.standing__metrics span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.standing__tags{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:var(--space-2)}.tag{padding:var(--space-1) var(--space-3);border:1px solid var(--border);border-radius:999px;background:var(--card);font-size:var(--font-size-dense)}.standing__review{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--space-1) var(--space-4);color:var(--color-text-secondary);font-size:var(--font-size-dense)}.review__label{color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}.review__group b{color:var(--color-text);font-weight:600}
.empty{padding:var(--space-5);color:var(--color-text-secondary)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.col-main,.col-side{padding:var(--space-4)}.col-side{border-left:1px solid var(--border)}figure{margin:0 0 var(--space-4);padding-bottom:var(--space-3);border-bottom:1px solid var(--color-border-subtle)}figcaption{display:flex;justify-content:space-between}figcaption span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.chart{height:260px}.chart--radar{height:340px}.charts-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--space-4)}.charts-row figure{margin-bottom:0}
.heat{margin-bottom:var(--space-4)}.heat h3,.subjects h3{margin:0 0 var(--space-2)}.heat table{width:100%;border-collapse:collapse;font-size:var(--font-size-dense)}.heat th,.heat td{padding:var(--space-1) var(--space-2);border:1px solid var(--color-border-subtle);text-align:center}.heat th[scope="row"]{text-align:left;font-weight:600}.heat thead th{background:var(--muted)}.heat .term{margin-right:var(--space-1)}.hint{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.subjects{display:grid;gap:var(--space-2);margin-bottom:var(--space-4)}.subject{border:1px solid var(--color-border-subtle);border-radius:var(--radius)}.subject--attention{border-color:rgba(155,106,43,.55)}.subject__main{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);padding:var(--space-2) var(--space-3)}.subject__row1{display:flex;align-items:baseline;gap:var(--space-2);min-width:0}.subject__row1 strong{flex:none}.subject__rank-big{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:var(--font-size-h3);font-weight:700;color:var(--primary)}.subject__rank-big small{font-size:var(--font-size-dense);font-weight:400;color:var(--color-text-secondary)}.subject__row1 .subject__rank{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.grade-badge{flex:none;padding:0 var(--space-2);border-radius:var(--radius);font-size:var(--font-size-caption);font-weight:700;white-space:nowrap}.delta-badge{flex:none;padding:0 var(--space-2);border-radius:999px;font-size:var(--font-size-dense);font-weight:700;white-space:nowrap}.delta-badge--up{background:rgba(46,125,91,.14);color:#2e7d5b}.delta-badge--down{background:rgba(176,58,46,.12);color:#b03a2e}.delta-badge--flat{background:var(--muted);color:var(--muted-foreground)}.subject .mark{flex:none;padding:0 var(--space-2);border-radius:999px;background:rgba(155,106,43,.16);color:#9b6a2b;font-size:var(--font-size-caption);font-weight:700;white-space:nowrap}.spark{flex:none;width:120px;height:36px}.timeline-wrap{margin:0;padding:0 var(--space-3) var(--space-2);overflow-x:auto}.timeline{display:block}.tl-line{stroke:var(--color-border-subtle)}.tl-label{font-size:12px;fill:var(--color-text)}.tl-dot{fill:var(--primary)}.tl-dot--void{fill:var(--card);stroke:var(--muted-foreground)}
.layout>aside{grid-column:1/-1;padding:var(--space-4);border-top:1px solid var(--border);background:var(--muted)}aside>section{margin-bottom:var(--space-4)}.attention label{display:grid;gap:var(--space-1);margin:var(--space-2) 0}.attention select,.attention textarea,.attention input{border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit;padding:var(--space-1) var(--space-2)}.success{color:var(--primary);font-weight:600}
@media(max-width:1100px){.layout{grid-template-columns:1fr}.col-side{border-left:0;border-top:1px solid var(--border)}.standing{grid-template-columns:1fr}.charts-row{grid-template-columns:1fr}}
</style>
