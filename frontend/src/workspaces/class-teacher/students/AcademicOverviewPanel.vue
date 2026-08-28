<script setup lang="ts">
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  studentR1Api,
  type AcademicClassTrend,
  type AcademicOverview,
  type AcademicSessionClassResults,
  type AcademicSessionSummary,
} from '../api/r1'
import { formatClassLabel } from '../format_class_label'
import AppButton from '@/components/design-system/AppButton.vue'

use([BarChart, LineChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const emit = defineEmits<{ select: [subjectId: string] }>()
const overview = ref<AcademicOverview | null>(null)
const failed = ref(false)
const selectedSessionId = ref('')
const classResults = ref<AcademicSessionClassResults | null>(null)
const classResultsFailed = ref(false)
const trend = ref<AcademicClassTrend | null>(null)
const compareChart = ref<HTMLElement | null>(null)
const trendChart = ref<HTMLElement | null>(null)
const bandsChart = ref<HTMLElement | null>(null)
const charts: ECharts[] = []

// 分数段堆叠条的固定段序与配色：绿系到红系，未定标灰色。
const BAND_ORDER = ['90-100%', '80-89%', '70-79%', '60-69%', '60%以下', '未定标'] as const
const BAND_COLORS: Record<(typeof BAND_ORDER)[number], string> = {
  '90-100%': '#2e7d5b',
  '80-89%': '#6aa84f',
  '70-79%': '#c9a227',
  '60-69%': '#dd8b3e',
  '60%以下': '#b03a2e',
  '未定标': '#b6bec7',
}
// 等级分布堆叠条的固定段序与配色：A+ 浅青绿、A 深绿、B+ 蓝、B 黄、C+ 橙红、C/其他 灰。
const GRADE_ORDER = ['A+', 'A', 'B+', 'B', 'C+', 'C', '其他'] as const
const GRADE_COLORS: Record<(typeof GRADE_ORDER)[number], string> = {
  'A+': '#8fd0bd',
  'A': '#2e7d5b',
  'B+': '#4a7fb5',
  'B': '#c9a227',
  'C+': '#c05a2e',
  'C': '#8e979e',
  '其他': '#c4c9cf',
}

function termShort(session: { grade?: string | null; term?: string | null }): string {
  const grade = (session.grade ?? '').replace('年级', '')
  const half = session.term === '上学期' ? '上' : session.term === '下学期' ? '下' : ''
  return grade && half ? `${grade}${half}` : ''
}
function sessionLabel(session: AcademicSessionSummary): string {
  return [session.short_label || termShort(session), session.occurred_on, session.title].filter(Boolean).join(' · ')
}
function cellStyle(result: { relative_position: number | null; result_state: string } | undefined): Record<string, string> {
  if (!result || result.relative_position == null || result.result_state !== 'normal') return { background: 'var(--muted)', color: 'var(--muted-foreground)' }
  const position = result.relative_position
  if (position >= 0.75) return { background: 'rgba(53,104,89,.22)' }
  if (position >= 0.4) return { background: 'transparent' }
  return { background: 'rgba(155,106,43,.24)' }
}

const noRankSubjects = computed(() => (classResults.value?.subjects ?? []).filter((item) => item.stats.average_rank == null).map((item) => item.subject_name))
// 有任一科采集到等级即切换为等级分布；旧场次（无等级列）回退得分率分段。
const hasGradeDistribution = computed(() => Boolean(classResults.value?.subjects.some((item) => item.stats.grade_counts?.length)))

async function loadSession(sessionId: string): Promise<void> {
  classResultsFailed.value = false
  classResults.value = null
  try {
    classResults.value = await studentR1Api.sessionClassResults(sessionId)
  } catch {
    classResultsFailed.value = true
  }
  await nextTick()
  render()
}

async function load(): Promise<void> {
  failed.value = false
  try {
    overview.value = await studentR1Api.academicOverview()
  } catch {
    failed.value = true
    return
  }
  const latest = overview.value.latest_session?.session_id ?? overview.value.sessions[0]?.session_id ?? ''
  selectedSessionId.value = latest
  if (latest) await loadSession(latest)
  try {
    trend.value = await studentR1Api.classTrend()
  } catch {
    trend.value = null
  }
  await nextTick()
  render()
}

function render(): void {
  charts.splice(0).forEach((item) => item.dispose())
  const subjects = classResults.value?.subjects ?? []
  const ranked = subjects.filter((item) => item.subject_name !== '总分')
  if (ranked.length && compareChart.value) {
    // 总分不单独成条：画一条竖直虚线参考线，位置 = 该场总分平均校次，
    // 一眼看出某科平均校次在总分之前还是之后。
    const totalRank = subjects.find((item) => item.subject_name === '总分')?.stats.average_rank
    const instance = init(compareChart.value)
    instance.setOption({
      aria: { enabled: true },
      tooltip: {
        trigger: 'axis',
        formatter: (params: { dataIndex: number }[]) => {
          const item = ranked[params[0]?.dataIndex ?? 0]
          if (!item) return ''
          const rank = item.stats.average_rank != null ? `平均校次：${item.stats.average_rank}` : '无校次数据'
          return `${item.subject_name}<br/>${rank} · 均分 ${item.stats.average} · ${item.stats.count} 人`
        },
      },
      grid: { left: 64, right: 40, top: 20, bottom: 24 },
      // 校次越小越靠前：x 轴反向，名次靠前的科目条形更长
      xAxis: { type: 'value', inverse: true },
      yAxis: { type: 'category', data: ranked.map((item) => item.subject_name) },
      series: [{
        name: '平均校次',
        type: 'bar',
        label: { show: true, position: 'left' },
        data: ranked.map((item) => item.stats.average_rank),
        ...(totalRank != null
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { type: 'dashed', color: '#9b6a2b', width: 2 },
                label: { show: true, formatter: `总分 ${Math.round(totalRank)}` },
                data: [{ xAxis: totalRank }],
              },
            }
          : {}),
      }],
    })
    charts.push(instance)
  }
  const totals = (trend.value?.sessions ?? [])
    .map((session) => ({ session, total: session.subjects.find((item) => item.subject_name === '总分') }))
    .filter((item) => item.total != null)
  if (totals.length && trendChart.value) {
    // 纵轴按数据动态取范围（上下各留边距，左轴不小于 1），避免小幅变化被压成平线；
    // minInterval 保证整数刻度，不出现小数网格标签。
    const rankValues = totals.map((item) => item.total!.average_rank).filter((value): value is number => value != null)
    const topCounts = totals.map((item) => item.total!.top100_count)
    const rankMin = rankValues.length ? Math.max(1, Math.floor(Math.min(...rankValues)) - 10) : 1
    const rankMax = rankValues.length ? Math.ceil(Math.max(...rankValues)) + 10 : 10
    const topMax = topCounts.length ? Math.max(...topCounts) + 2 : 10
    const instance = init(trendChart.value)
    instance.setOption({
      aria: { enabled: true },
      tooltip: {
        trigger: 'axis',
        formatter: (params: { dataIndex: number }[]) => {
          const item = totals[params[0]?.dataIndex ?? 0]
          if (!item) return ''
          const rank = item.total!.average_rank != null ? `总分平均校次：${item.total!.average_rank}` : '总分平均校次：无校次数据'
          return `${item.session.title}<br/>${rank} · 前 100 名：${item.total!.top100_count} 人<br/>总分均分：${item.total!.average}`
        },
      },
      legend: { top: 0 },
      grid: { left: 46, right: 46, top: 34, bottom: 30 },
      xAxis: { type: 'category', data: totals.map((item) => item.session.short_label || termShort(item.session) || item.session.title) },
      yAxis: [
        { type: 'value', name: '平均校次', inverse: true, min: rankMin, max: rankMax, minInterval: 1 },
        { type: 'value', name: '前 100 名人数', min: 0, max: topMax, minInterval: 1 },
      ],
      series: [
        // 主线强化：粗线、大点、直接标整数校次。
        {
          name: '总分平均校次',
          type: 'line',
          symbol: 'circle',
          symbolSize: 12,
          yAxisIndex: 0,
          connectNulls: false,
          lineStyle: { width: 3, color: '#356859' },
          itemStyle: { color: '#356859' },
          label: {
            show: true,
            formatter: (params: { value: unknown }) => (typeof params.value === 'number' ? String(Math.round(params.value)) : ''),
          },
          data: totals.map((item) => item.total!.average_rank),
        },
        // 参考线弱化：细线、浅色、小点。
        {
          name: '前100名人数',
          type: 'line',
          symbol: 'triangle',
          symbolSize: 6,
          yAxisIndex: 1,
          lineStyle: { width: 1, color: '#9aa5b1' },
          itemStyle: { color: '#9aa5b1' },
          data: totals.map((item) => item.total!.top100_count),
        },
      ],
    })
    charts.push(instance)
  }
  const gradeMode = hasGradeDistribution.value
  const rows = gradeMode
    ? subjects.filter((item) => item.stats.grade_counts?.length)
    : subjects.filter((item) => item.stats.bands.length)
  if (rows.length && bandsChart.value) {
    // 每科一条 100% 堆叠条；类目轴首项渲染在底部，总分放数组末尾即最上方。
    const ordered = [...rows.filter((item) => item.subject_name !== '总分'), ...rows.filter((item) => item.subject_name === '总分')]
    // 段序固定 A+→C；「其他」仅当某科计数大于 0 才显示，全 0 时不进图例。
    const order: readonly string[] = gradeMode
      ? GRADE_ORDER.filter((label) => label !== '其他' || ordered.some((item) => (item.stats.grade_counts?.find((grade) => grade.label === '其他')?.count ?? 0) > 0))
      : BAND_ORDER
    const colors: Record<string, string> = gradeMode ? GRADE_COLORS : BAND_COLORS
    const segmentCount = (item: (typeof ordered)[number], label: string): number =>
      gradeMode
        ? item.stats.grade_counts?.find((grade) => grade.label === label)?.count ?? 0
        : item.stats.bands.find((band) => band.label === label)?.count ?? 0
    const segmentPercent = (item: (typeof ordered)[number], label: string): number => {
      if (!item.stats.count) return 0
      return Math.round((segmentCount(item, label) / item.stats.count) * 1000) / 10
    }
    const instance = init(bandsChart.value)
    instance.setOption({
      aria: { enabled: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: { seriesName: string; dataIndex: number }[]) => {
          const item = ordered[params[0]?.dataIndex ?? 0]
          if (!item) return ''
          const head = `${item.subject_name}<br/>满分 ${item.max_score ?? '未定标'} · 均分 ${item.stats.average} · 最高 ${item.stats.maximum} · 最低 ${item.stats.minimum} · ${item.stats.count} 人`
          const lines = params.map((param) => `${param.seriesName}：${segmentCount(item, param.seriesName)} 人（${segmentPercent(item, param.seriesName)}%）`)
          return [head, ...lines].join('<br/>')
        },
      },
      legend: { top: 0 },
      grid: { left: 64, right: 24, top: 34, bottom: 24 },
      xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
      yAxis: { type: 'category', data: ordered.map((item) => item.subject_name) },
      series: order.map((label) => ({
        name: label,
        type: 'bar' as const,
        stack: 'bands',
        barWidth: 18,
        itemStyle: { color: colors[label] },
        data: ordered.map((item) => segmentPercent(item, label)),
      })),
    })
    charts.push(instance)
  }
}

function resize(): void { charts.forEach((item) => item.resize()) }

watch(selectedSessionId, (next, previous) => {
  if (next && next !== previous) void loadSession(next)
})
onMounted(() => { void load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach((item) => item.dispose()) })
</script>

<template>
  <section class="academic-overview">
    <header>
      <div><p>学业证据 · 全班</p><h2>全班学业概览</h2></div>
      <span>均分只按教师确认的成绩计算；缺考不计入</span>
    </header>
    <div v-if="failed" class="empty" role="alert">
      <p>全班学业概览暂时无法读取，已登记的成绩未受影响。</p>
      <AppButton variant="secondary" @click="load">重新读取</AppButton>
    </div>
    <template v-else-if="overview">
      <div v-if="!overview.sessions.length" class="empty">
        <h3>还没有登记大考成绩</h3>
        <p>用上方上传入口登记第一场次；登记后这里会出现场次列表和班级统计。</p>
      </div>
      <template v-else>
        <section class="block session-picker">
          <label><span>查看场次</span>
            <select v-model="selectedSessionId" aria-label="查看场次">
              <option v-for="session in overview.sessions" :key="session.session_id" :value="session.session_id">{{ sessionLabel(session) }}</option>
            </select>
          </label>
        </section>
        <p v-if="classResultsFailed" class="hint block" role="alert">这一场次的班级统计暂时无法读取，请稍后重试。</p>
        <template v-else-if="classResults">
          <section v-if="classResults.subjects.length" class="block" aria-label="各科平均校次">
            <h3>各科班级平均校次 · {{ classResults.title }}</h3>
            <div ref="compareChart" class="chart" role="img" aria-label="各科班级平均校次对比图"></div>
            <p v-if="noRankSubjects.length" class="hint">以下科目暂无校次数据：{{ noRankSubjects.join('、') }}（校次来自成绩表中的校次列）。</p>
          </section>
        </template>
        <section v-if="trend?.sessions.some((session) => session.subjects.some((item) => item.subject_name === '总分'))" class="block" aria-label="历次位次走势">
          <h3>历次位次走势</h3>
          <div ref="trendChart" class="chart" role="img" aria-label="历次总分平均校次与前100名人数走势图"></div>
        </section>
        <template v-if="classResults && !classResultsFailed">
          <section v-if="classResults.subjects.length" class="block" :aria-label="hasGradeDistribution ? '等级分布' : '分数段'">
            <h3>{{ hasGradeDistribution ? '等级分布' : '分数段' }} · {{ classResults.title }}</h3>
            <template v-if="hasGradeDistribution">
              <div ref="bandsChart" class="chart" role="img" aria-label="各科等级分布堆叠图"></div>
              <p class="hint">每科按等级（A+ 到 C，其余计入"其他"）堆叠；悬停查看各等级人数、占比与满分·均分·最高·最低。</p>
            </template>
            <template v-else-if="classResults.subjects.some((subject) => subject.stats.bands.length)">
              <div ref="bandsChart" class="chart" role="img" aria-label="各科分数段分布堆叠图"></div>
              <p class="hint">本场次未采集等级，重新上传含等级列的表格后可按等级显示；当前每科按得分率分成五段加未定标，悬停查看各段人数、占比与满分·均分·最高·最低。</p>
            </template>
            <p v-else class="hint">在成绩管理中补填满分后显示分数段。</p>
          </section>
          <section v-if="classResults.students.length" class="block" aria-label="学生名次热力表">
            <h3>学生 × 科目校次</h3>
            <table class="heat">
              <thead><tr><th>学生</th><th v-for="subject in classResults.subjects" :key="subject.subject_name">{{ subject.subject_name }}</th></tr></thead>
              <tbody>
                <tr v-for="student in classResults.students" :key="student.subject_id" @click="emit('select', student.student_ref ?? student.subject_id)">
                  <th scope="row"><strong>{{ student.display_name }}</strong><span>{{ formatClassLabel(student.class_label) }}</span></th>
                  <td v-for="subject in classResults.subjects" :key="subject.subject_name" :style="cellStyle(student.results[subject.subject_name])">
                    <template v-if="student.results[subject.subject_name]?.result_state === 'normal'">{{ student.results[subject.subject_name]?.rank ?? '—' }}</template>
                    <template v-else>—</template>
                  </td>
                </tr>
              </tbody>
            </table>
            <p class="hint">颜色越绿校次越靠前，琥珀色表示落在年级后 60%；灰格为缺考或无有效名次。点击学生行可打开这名学生的学业证据。</p>
          </section>
        </template>
      </template>
      <section class="block">
        <h3>未决关注卡</h3>
        <p v-if="!overview.attention_students.length" class="hint">当前没有等待教师决定的关注卡。</p>
        <button v-for="item in overview.attention_students" :key="item.subject_id" type="button" class="row" @click="emit('select', item.student_ref ?? item.subject_id)">
          <strong>{{ item.display_name }}</strong>
          <span>{{ formatClassLabel(item.class_label) }}</span>
          <small>{{ item.pending_count }} 张待决定</small>
        </button>
      </section>
    </template>
  </section>
</template>

<style scoped>
.academic-overview{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}
header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}
h2{margin:0;font-size:var(--font-size-h2)}
header>span{max-width:420px;color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.block{padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.block h3{margin:var(--space-1) 0 var(--space-2)}
.hint{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.session-picker label{display:flex;align-items:center;gap:var(--space-3);font-size:var(--font-size-dense);font-weight:650}
.session-picker select{min-height:38px;padding:0 var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}
.chart{height:280px}
.heat{width:100%;border-collapse:collapse;font-size:var(--font-size-dense)}
.heat th,.heat td{padding:var(--space-1) var(--space-2);border:1px solid var(--color-border-subtle);text-align:center}
.heat th[scope="row"]{text-align:left;font-weight:600}
.heat th[scope="row"] span{margin-left:var(--space-2);color:var(--color-text-secondary);font-weight:400}
.heat thead th{background:var(--muted)}
.heat tbody tr{cursor:pointer}
.heat tbody tr:hover{outline:2px solid var(--accent);outline-offset:-2px}
.row{display:flex;align-items:baseline;gap:var(--space-3);width:100%;padding:var(--space-2) 0;border:0;border-bottom:1px solid var(--color-border-subtle);background:transparent;text-align:left;font:inherit;cursor:pointer}
.row:hover{background:var(--accent)}
.row span{color:var(--color-text-secondary)}
.row small{margin-left:auto;color:var(--muted-foreground)}
.empty{display:grid;place-items:center;align-content:center;gap:var(--space-2);min-height:200px;padding:var(--space-6);text-align:center}
.empty h3{margin-bottom:0}
.empty p{max-width:460px;color:var(--color-text-secondary)}
</style>
