<script setup lang="ts">
import { computed } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamChapter,
  ChapterExamSection,
  ChapterExamStage,
  ChapterExamStageStats,
} from '../../api/question-bank'
import type { TrainingOverviewNode } from '../../api/training'
import { isWeakAndFrequent, weakShareOf } from './chapterExamWeak'

const props = defineProps<{
  chapter: ChapterExamChapter
  stages: ChapterExamStageStats[]
  mastery: ReadonlyMap<string, TrainingOverviewNode>
  masteryState: 'idle' | 'loading' | 'ready' | 'error'
}>()

const emit = defineEmits<{
  section: [id: string]
  cell: [title: string, ids: number[]]
}>()

const TIERS: { key: ChapterExamCellColumn; label: string }[] = [
  { key: 'basic', label: '基础' },
  { key: 'mid', label: '中档' },
  { key: 'hard', label: '提高' },
]
const STAGES: ChapterExamStage[] = ['midterm', 'final']

const stageInfo = computed(
  () => Object.fromEntries(props.stages.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)
const crossMidterm = computed(() => props.chapter.cross_question_ids.midterm)
const crossFinal = computed(() => props.chapter.cross_question_ids.final)

function stageLabel(stage: ChapterExamStage): string {
  return stageInfo.value[stage]?.label ?? stage
}

function stageSample(stage: ChapterExamStage): string {
  const info = stageInfo.value[stage]
  return `${stageLabel(stage)} ${info?.group_count ?? 0}${info?.unit ?? '组'}`
}

function averageMain(stage: ChapterExamStage): string {
  const count = stageInfo.value[stage]?.group_count ?? 0
  if (!count) return '—'
  return (props.chapter.totals[stage].main / count).toFixed(2)
}

/* A 出卷率哑铃图 */
function coverageDelta(section: ChapterExamSection): number | null {
  const first = section.coverage.midterm.percent
  const second = section.coverage.final.percent
  if (first === null || second === null) return null
  return second - first
}

function coverageDeltaText(section: ChapterExamSection): string {
  const delta = coverageDelta(section)
  if (delta === null) return '—'
  if (delta === 0) return '持平'
  return `${delta > 0 ? '▲ +' : '▼ −'}${Math.abs(delta)} ppt`
}

function coverageAria(section: ChapterExamSection, stage: ChapterExamStage): string {
  const cell = section.coverage[stage]
  const percent = cell.percent === null ? '—' : `${cell.percent}%`
  return `${section.label} · ${stageLabel(stage)}出卷率 ${percent}（${cell.groups}/${cell.of}${stageInfo.value[stage]?.unit ?? '组'}）`
}

function stageIds(section: ChapterExamSection, stage: ChapterExamStage): number[] {
  return [...new Set(TIERS.flatMap(tier => section.overview[stage][tier.key]))]
}

function openCoverage(section: ChapterExamSection, stage: ChapterExamStage): void {
  const ids = stageIds(section, stage)
  if (!ids.length) return
  emit('cell', `${section.label} · ${stageLabel(stage)}覆盖题`, ids)
}

/* B 命题结构蝴蝶图：条长＝该小节该阶段题数 ÷ 该阶段卷组数，按难度档堆叠 */
function stageCount(section: ChapterExamSection, stage: ChapterExamStage): number {
  return stageIds(section, stage).length
}

function stageAvg(section: ChapterExamSection, stage: ChapterExamStage): number {
  const groups = stageInfo.value[stage]?.group_count ?? 0
  return groups ? stageCount(section, stage) / groups : 0
}

const butterflyMax = computed(() => {
  let max = 0
  for (const section of props.chapter.sections) {
    for (const stage of STAGES) max = Math.max(max, stageAvg(section, stage))
  }
  return max
})

function barWidth(avg: number): string {
  if (!avg || !butterflyMax.value) return '0%'
  return `${Math.max(2, (avg / butterflyMax.value) * 100)}%`
}

function segmentAria(section: ChapterExamSection, stage: ChapterExamStage, tier: ChapterExamCellColumn): string {
  const count = section.overview[stage][tier].length
  const groups = stageInfo.value[stage]?.group_count ?? 0
  const avg = groups ? (count / groups).toFixed(2) : '—'
  return `${section.label} · ${stageLabel(stage)} · ${tierLabel(tier)} ${count} 题 · 平均每${stageInfo.value[stage]?.unit ?? '组'} ${avg} 题`
}

function tierLabel(tier: ChapterExamCellColumn): string {
  return TIERS.find(row => row.key === tier)?.label ?? tier
}

function stageTiers(section: ChapterExamSection, stage: ChapterExamStage): ChapterExamCellColumn[] {
  return TIERS.map(tier => tier.key).filter(tier => section.overview[stage][tier].length > 0)
}

function openSegment(section: ChapterExamSection, stage: ChapterExamStage, tier: ChapterExamCellColumn): void {
  const ids = section.overview[stage][tier]
  if (!ids.length) return
  emit('cell', `${section.label} · ${stageLabel(stage)} · ${tierLabel(tier)}`, ids)
}

/* C 本章优先讲题型：同键跨小节合并，x＝n，y＝明显薄弱占比，大小＝卷组数 */
interface QuadRow {
  key: string
  name: string
  total: number
  ids: number[]
  groups: number
  node: TrainingOverviewNode | undefined
}

function stripPrefix(name: string): string {
  return name.replace(/^(题型|技能)[·：:]\s*/, '')
}

function shortSection(section: ChapterExamSection): string {
  return section.label.split(/\s+/)[0] || section.label
}

const quadRows = computed<QuadRow[]>(() => {
  const merged = new Map<string, QuadRow>()
  for (const section of props.chapter.sections) {
    for (const row of section.skills) {
      const key = row.key ?? `${section.id}#unlinked`
      let entry = merged.get(key)
      if (!entry) {
        entry = {
          key,
          name: row.key ? stripPrefix(row.name) : `${stripPrefix(row.name)}（${shortSection(section)}）`,
          total: 0,
          ids: [],
          groups: 0,
          node: row.key ? props.mastery.get(row.key) : undefined,
        }
        merged.set(key, entry)
      }
      entry.total += row.total
      entry.groups = Math.max(entry.groups, row.group_count)
      const seen = new Set(entry.ids)
      for (const stage of STAGES) {
        for (const tier of TIERS) {
          for (const id of row.cells[stage][tier.key]) {
            if (!seen.has(id)) {
              seen.add(id)
              entry.ids.push(id)
            }
          }
        }
      }
    }
  }
  return [...merged.values()]
})

const quadPlotted = computed(() => quadRows.value.filter(row => {
  const { share } = weakShareOf(row.node)
  return share !== null && share >= 0
}))
const quadNoEvidence = computed(() => quadRows.value.filter(row => weakShareOf(row.node).share === null))
const quadInsufficient = computed(() => quadRows.value.filter(row => {
  const { share } = weakShareOf(row.node)
  return share !== null && share < 0
}))

const quadMaxN = computed(() => Math.max(6, ...quadPlotted.value.map(row => row.total)))
const quadMaxY = computed(() => Math.max(
  40,
  ...quadPlotted.value.map(row => Math.ceil((weakShareOf(row.node).share ?? 0) * 100 / 10) * 10),
))

function quadX(row: QuadRow): string {
  return `${(row.total / quadMaxN.value) * 100}%`
}

function quadY(row: QuadRow): string {
  const { share } = weakShareOf(row.node)
  return `${(((share ?? 0) * 100) / quadMaxY.value) * 100}%`
}

function quadSize(row: QuadRow): string {
  return `${Math.round(Math.max(12, Math.min(36, 8 + Math.sqrt(row.groups) * 8)))}px`
}

function quadAria(row: QuadRow): string {
  const result = weakShareOf(row.node)
  const weak = result.share !== null && result.share >= 0
    ? `明显薄弱 ${Math.round(result.share * 100)}%（${result.weak}/${result.denominator} 人）`
    : '无学情证据'
  return `${row.name}：主考 ${row.total} 题，出现 ${row.groups} 个卷组，${weak}`
}

function openQuad(row: QuadRow): void {
  if (!row.ids.length) return
  emit('cell', `${row.name} · 期中＋期末`, row.ids)
}

function ratio(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : '—'
}
</script>

<template>
  <div class="cep-ov-grid">
  <section class="cep-block cep-card" aria-labelledby="cep-coverage-title">
    <header class="cep-card-head">
      <h3 id="cep-coverage-title" class="cep-block-title">{{ chapter.label }} · 出卷率</h3>
      <p class="cep-card-sub">{{ stageSample('midterm') }} · {{ stageSample('final') }}　青点期中、琥珀点期末，连线为同一小节；右侧为两阶段差值</p>
    </header>
    <div class="cep-dumb">
      <div v-for="section in chapter.sections" :key="section.id" class="cep-dumb-row" :class="{ 'is-empty': !section.main_count }">
        <button
          v-if="section.main_count"
          type="button"
          class="cep-row-link cep-dumb-label"
          @click="emit('section', section.id)"
        >{{ section.label }}<small>{{ section.main_count }} 题</small></button>
        <span v-else class="cep-row-static cep-dumb-label">{{ section.label }}</span>
        <div class="cep-dumb-track">
          <template v-if="section.main_count">
            <span class="cep-dumb-axis" aria-hidden="true" />
            <span
              v-if="section.coverage.midterm.percent !== null && section.coverage.final.percent !== null"
              class="cep-dumb-link"
              aria-hidden="true"
              :style="{
                left: `${Math.min(section.coverage.midterm.percent, section.coverage.final.percent)}%`,
                width: `${Math.abs((section.coverage.final.percent ?? 0) - (section.coverage.midterm.percent ?? 0))}%`,
              }"
            />
            <button
              v-if="section.coverage.midterm.percent !== null"
              type="button"
              class="cep-dumb-dot is-midterm"
              :style="{ left: `${section.coverage.midterm.percent}%` }"
              :title="coverageAria(section, 'midterm')"
              :aria-label="coverageAria(section, 'midterm')"
              @click="openCoverage(section, 'midterm')"
            />
            <button
              v-if="section.coverage.final.percent !== null"
              type="button"
              class="cep-dumb-dot is-final"
              :style="{ left: `${section.coverage.final.percent}%` }"
              :title="coverageAria(section, 'final')"
              :aria-label="coverageAria(section, 'final')"
              @click="openCoverage(section, 'final')"
            />
          </template>
          <span v-else class="cep-dumb-empty">无主考题</span>
        </div>
        <span
          v-if="section.main_count"
          class="cep-dumb-delta"
          :class="{ 'is-up': (coverageDelta(section) ?? 0) > 0, 'is-down': (coverageDelta(section) ?? 0) < 0 }"
        >{{ coverageDeltaText(section) }}</span>
      </div>
    </div>
    <p class="cep-info">本章主考整题（同源卷已合并）　期中平均 {{ averageMain('midterm') }} 题/{{ stageInfo.midterm?.unit ?? '组' }}　期末平均 {{ averageMain('final') }} 题/{{ stageInfo.final?.unit ?? '份' }}</p>
    <p class="cep-legend cep-legend--stages"><i class="is-midterm" />期中　<i class="is-final" />期末<span class="cep-legend__tail">0–100% · 点击圆点看该小节该阶段覆盖题</span></p>
    <p class="cep-note">同小节同卷组只计一次 · ppt＝百分点 · 不同阶段样本独立，变化并非同一批学生的追踪。</p>
  </section>

  <section class="cep-block cep-card" aria-labelledby="cep-overview-title">
    <header class="cep-card-head">
      <h3 id="cep-overview-title" class="cep-block-title">命题结构</h3>
      <p class="cep-card-sub">期中向左、期末向右　条长＝每{{ stageInfo.midterm?.unit ?? '组' }}/{{ stageInfo.final?.unit ?? '份' }}平均题数（该阶段题数÷卷组数），按难度档堆叠</p>
    </header>
    <div class="cep-bfly">
      <div class="cep-bfly-axis">
        <span class="is-midterm">◀ {{ stageLabel('midterm') }}</span>
        <span>小节</span>
        <span class="is-final">{{ stageLabel('final') }} ▶</span>
      </div>
      <div v-for="section in chapter.sections" :key="section.id" class="cep-bfly-row" :class="{ 'is-empty': !section.main_count }">
        <div class="cep-bfly-half is-left">
          <div class="cep-bfly-bar" :style="{ width: barWidth(stageAvg(section, 'midterm')) }">
            <button
              v-for="tier in stageTiers(section, 'midterm')"
              :key="tier"
              type="button"
              class="cep-seg"
              :class="`is-${tier}`"
              :style="{ flexGrow: section.overview.midterm[tier].length }"
              :title="segmentAria(section, 'midterm', tier)"
              :aria-label="segmentAria(section, 'midterm', tier)"
              @click="openSegment(section, 'midterm', tier)"
            ><span class="cep-seg-n">{{ section.overview.midterm[tier].length }}</span></button>
          </div>
        </div>
        <button
          v-if="section.main_count"
          type="button"
          class="cep-row-link cep-bfly-label"
          @click="emit('section', section.id)"
        >{{ section.label }}</button>
        <span v-else class="cep-row-static cep-bfly-label">{{ section.label }}</span>
        <div class="cep-bfly-half is-right">
          <div class="cep-bfly-bar" :style="{ width: barWidth(stageAvg(section, 'final')) }">
            <button
              v-for="tier in stageTiers(section, 'final')"
              :key="tier"
              type="button"
              class="cep-seg"
              :class="`is-${tier}`"
              :style="{ flexGrow: section.overview.final[tier].length }"
              :title="segmentAria(section, 'final', tier)"
              :aria-label="segmentAria(section, 'final', tier)"
              @click="openSegment(section, 'final', tier)"
            ><span class="cep-seg-n">{{ section.overview.final[tier].length }}</span></button>
          </div>
        </div>
      </div>
    </div>
    <p class="cep-legend">
      <span class="cep-legend__label">难度档</span>
      <i class="is-basic" />基础 1–3.9
      <i class="is-mid" />中档 4–6.9
      <i class="is-hard" />提高 7–10
      <span class="cep-legend__tail">点击分段看题单</span>
    </p>
    <p class="cep-note">无难度题不进入难度档统计；两阶段卷组数不同，条长按平均每{{ stageInfo.midterm?.unit ?? '组' }}/{{ stageInfo.final?.unit ?? '份' }}折算后才可比较。</p>
  </section>

  <section class="cep-block cep-card" aria-labelledby="cep-quad-title">
    <header class="cep-card-head">
      <h3 id="cep-quad-title" class="cep-block-title">本章优先讲题型</h3>
      <p class="cep-card-sub">横轴＝两阶段主考题数 n，纵轴＝本班明显薄弱占比，气泡大小＝出现卷组数；虚线＝“常考且薄弱”规则（n≥3 且 ≥30%）</p>
    </header>
    <p v-if="masteryState === 'error'" class="cep-note">学情暂不可用，仅列出题数分布无法判断薄弱。</p>
    <p v-else-if="masteryState !== 'ready'" class="cep-note">正在读取本班学情…</p>
    <div v-else class="cep-quad-wrap">
      <div class="cep-quad">
        <span class="cep-quad-ylabel">明显薄弱占比 ↑</span>
        <span class="cep-quad-xlabel">n＝两阶段主考题数 →</span>
        <span
          class="cep-quad-guide is-v"
          :style="{ left: `${(3 / quadMaxN) * 100}%` }"
          aria-hidden="true"
        />
        <span
          class="cep-quad-guide is-h"
          :style="{ bottom: `${(30 / quadMaxY) * 100}%` }"
          aria-hidden="true"
        />
        <span class="cep-quad-tag">常考且薄弱 · 优先讲</span>
        <button
          v-for="row in quadPlotted"
          :key="row.key"
          type="button"
          class="cep-quad-dot"
          :class="{ 'is-priority': isWeakAndFrequent(row.total, row.node) }"
          :style="{ left: quadX(row), bottom: quadY(row), width: quadSize(row), height: quadSize(row) }"
          :title="quadAria(row)"
          :aria-label="quadAria(row)"
          @click="openQuad(row)"
        ><i>{{ row.name.slice(0, 2) }}</i></button>
        <div class="cep-quad-yticks" aria-hidden="true">
          <span :style="{ bottom: `${(30 / quadMaxY) * 100}%` }">30%</span>
          <span :style="{ bottom: '100%' }">{{ quadMaxY }}%</span>
        </div>
      </div>
      <div class="cep-quad-ticks">
        <span>0</span>
        <span>{{ Math.round(quadMaxN / 2) }}</span>
        <span>{{ quadMaxN }}</span>
      </div>
    </div>
    <p v-if="quadInsufficient.length" class="cep-note cep-quad-list">证据不足：{{ quadInsufficient.map(row => row.name).join('、') }}</p>
    <p v-if="quadNoEvidence.length" class="cep-note cep-quad-list">暂无学情证据：{{ quadNoEvidence.map(row => row.name).join('、') }}</p>
    <p class="cep-note">同名行按键跨小节合并：n 与各档题数取合计，卷组数取各小节最大值；明显薄弱占比＝明显薄弱÷（明显薄弱＋不稳＋掌握），证据不足的学生不进分母。</p>
  </section>

  <section class="cep-block cep-card" aria-labelledby="cep-difficulty-title">
    <header class="cep-card-head">
      <h3 id="cep-difficulty-title" class="cep-block-title">难度与题型</h3>
    </header>
    <div class="cep-table-scroll">
    <table class="app-table">
      <thead>
        <tr>
          <th>阶段</th>
          <th class="is-num">总题数</th>
          <th class="is-num">基础 1–3.9</th>
          <th class="is-num">中档 4–6.9</th>
          <th class="is-num">提高 7–10</th>
          <th class="is-num">选择</th>
          <th class="is-num">填空</th>
          <th class="is-num">解答</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="stage in STAGES" :key="stage">
          <td class="cep-row-label">{{ stageLabel(stage) }}</td>
          <td class="is-num">{{ chapter.difficulty[stage].total }}</td>
          <td class="is-num">
            {{ chapter.difficulty[stage].basic }}
            <small class="cep-stage-tag">{{ ratio(chapter.difficulty[stage].basic, chapter.difficulty[stage].total) }}</small>
          </td>
          <td class="is-num">
            {{ chapter.difficulty[stage].mid }}
            <small class="cep-stage-tag">{{ ratio(chapter.difficulty[stage].mid, chapter.difficulty[stage].total) }}</small>
          </td>
          <td class="is-num">
            {{ chapter.difficulty[stage].hard }}
            <small class="cep-stage-tag">{{ ratio(chapter.difficulty[stage].hard, chapter.difficulty[stage].total) }}</small>
          </td>
          <td class="is-num">{{ chapter.difficulty[stage].choice }}</td>
          <td class="is-num">{{ chapter.difficulty[stage].fill }}</td>
          <td class="is-num">{{ chapter.difficulty[stage].written }}</td>
        </tr>
      </tbody>
    </table>
    </div>
    <p class="cep-note">
      另有
      <button type="button" class="cep-inline-link" :disabled="!crossMidterm.length" @click="emit('cell', '跨章涉及 · 期中', crossMidterm)">期中 {{ crossMidterm.length }} 题</button>、
      <button type="button" class="cep-inline-link" :disabled="!crossFinal.length" @click="emit('cell', '跨章涉及 · 期末', crossFinal)">期末 {{ crossFinal.length }} 题</button>
      主要考其他章、同时涉及本章。
    </p>
  </section>
  </div>
</template>
