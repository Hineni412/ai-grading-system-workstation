<script setup lang="ts">
import { computed } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamChapter,
  ChapterExamSection,
  ChapterExamStage,
  ChapterExamStageStats,
} from '../../api/question-bank'

const props = defineProps<{
  chapter: ChapterExamChapter
  stages: ChapterExamStageStats[]
}>()

const emit = defineEmits<{
  section: [id: string]
  cell: [title: string, ids: number[]]
}>()

const COLS: { key: ChapterExamCellColumn; label: string }[] = [
  { key: 'choice_basic', label: '选择·基础' },
  { key: 'choice_advanced', label: '选择·中高' },
  { key: 'fill', label: '填空' },
  { key: 'written', label: '解答' },
]

const DIFFICULTY_COLS: { key: 'basic' | 'mid' | 'hard'; label: string }[] = [
  { key: 'basic', label: '基础 1–3.9' },
  { key: 'mid', label: '中档 4–6.9' },
  { key: 'hard', label: '提高 7–10' },
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

function coveragePercent(section: ChapterExamSection, stage: ChapterExamStage): string {
  const percent = section.coverage[stage].percent
  return percent === null ? '—' : `${percent}%`
}

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
  return `${delta > 0 ? '+' : '−'}${Math.abs(delta)} ppt`
}

function coverageHeat(percent: number | null): string {
  if (percent === null) return ''
  if (percent >= 90) return 'is-heat-5'
  if (percent >= 75) return 'is-heat-4'
  if (percent >= 60) return 'is-heat-3'
  if (percent >= 40) return 'is-heat-2'
  if (percent > 0) return 'is-heat-1'
  return ''
}

const overviewMax = computed(() => {
  let max = 0
  for (const section of props.chapter.sections) {
    for (const stage of STAGES) {
      for (const col of COLS) {
        max = Math.max(max, section.overview[stage][col.key].length)
      }
    }
  }
  return max
})

function countHeat(value: number): string {
  if (!value) return ''
  return `is-heat-${Math.max(1, Math.ceil((value / Math.max(overviewMax.value, 1)) * 5))}`
}

function openCell(section: ChapterExamSection, stage: ChapterExamStage, col: (typeof COLS)[number]): void {
  const ids = section.overview[stage][col.key]
  if (!ids.length) return
  emit('cell', `${section.label} · ${col.label} · ${stageLabel(stage)}`, ids)
}

function ratio(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : '—'
}
</script>

<template>
  <div class="cep-ov-grid">
  <section class="cep-block cep-card" aria-labelledby="cep-coverage-title">
    <header class="cep-card-head">
      <h3 id="cep-coverage-title" class="cep-block-title">{{ chapter.label }} · 考点热力图</h3>
      <p class="cep-card-sub">{{ stageSample('midterm') }} · {{ stageSample('final') }}　颜色＝出卷率（该小节至少出现 1 题的卷组占比）</p>
    </header>
    <div class="app-heat-table-wrap">
    <table class="app-heat-table app-heat-table--compact cep-cov-table">
      <thead>
        <tr>
          <th>小节</th>
          <th>{{ stageSample('midterm') }}</th>
          <th>{{ stageSample('final') }}</th>
          <th>变化</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="section in chapter.sections" :key="section.id" :class="{ 'is-empty': !section.main_count }">
          <td class="cep-row-label">
            <button
              v-if="section.main_count"
              type="button"
              class="cep-row-link"
              @click="emit('section', section.id)"
            >{{ section.label }}<small>{{ section.main_count }} 题</small></button>
            <span v-else class="cep-row-static">{{ section.label }}<small>无主考题</small></span>
          </td>
          <td :class="coverageHeat(section.coverage.midterm.percent)">
            <span class="cep-cov-cell">
              <b>{{ coveragePercent(section, 'midterm') }}</b>
              <small>{{ section.coverage.midterm.groups }}/{{ section.coverage.midterm.of }}</small>
            </span>
          </td>
          <td :class="coverageHeat(section.coverage.final.percent)">
            <span class="cep-cov-cell">
              <b>{{ coveragePercent(section, 'final') }}</b>
              <small>{{ section.coverage.final.groups }}/{{ section.coverage.final.of }}</small>
            </span>
          </td>
          <td class="cep-delta-cell" :class="{ 'is-strong': Math.abs(coverageDelta(section) ?? 0) >= 10 }">
            {{ coverageDeltaText(section) }}
          </td>
        </tr>
      </tbody>
    </table>
    </div>
    <p class="cep-info">本章主考整题（同源卷已合并）　期中平均 {{ averageMain('midterm') }} 题/{{ stageInfo.midterm?.unit ?? '组' }}　期末平均 {{ averageMain('final') }} 题/{{ stageInfo.final?.unit ?? '份' }}</p>
    <p class="cep-legend"><span class="cep-legend__label">出卷率</span><i class="is-heat-1" /><i class="is-heat-2" /><i class="is-heat-3" /><i class="is-heat-4" /><i class="is-heat-5" /><span>低 → 高</span></p>
    <p class="cep-note">同小节同卷组只计一次 · ppt＝百分点 · 不同阶段样本独立，变化并非同一批学生的追踪。</p>
  </section>

  <section class="cep-block cep-card" aria-labelledby="cep-overview-title">
    <header class="cep-card-head">
      <h3 id="cep-overview-title" class="cep-block-title">命题总览</h3>
      <p class="cep-card-sub">期中与期末并排　颜色＝本节主考题数（两阶段共用同一刻度）</p>
    </header>
    <div class="cep-panels">
      <div v-for="panel in STAGES" :key="panel">
        <p class="cep-panel-title">{{ stageLabel(panel) }}</p>
        <div class="app-heat-table-wrap">
        <table class="app-heat-table">
          <thead>
            <tr><th>小节</th><th v-for="col in COLS" :key="col.key">{{ col.label }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="section in chapter.sections" :key="section.id" :class="{ 'is-empty': !section.main_count }">
              <td class="cep-row-label">
                <button
                  v-if="section.main_count"
                  type="button"
                  class="cep-row-link"
                  @click="emit('section', section.id)"
                >{{ section.label }}</button>
                <span v-else class="cep-row-static">{{ section.label }}</span>
              </td>
              <td
                v-for="col in COLS"
                :key="col.key"
                :class="countHeat(section.overview[panel][col.key].length)"
              >
                <button
                  v-if="section.overview[panel][col.key].length"
                  type="button"
                  class="cep-cell"
                  @click="openCell(section, panel, col)"
                >{{ section.overview[panel][col.key].length }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
    </div>
    <p class="cep-legend"><span class="cep-legend__label">出现次数</span><i class="is-heat-1" /><i class="is-heat-2" /><i class="is-heat-3" /><i class="is-heat-4" /><i class="is-heat-5" /><span>少 → 多</span></p>
    <p class="cep-note">基础＝难度 1–3.9；中高＝4–10。填空、解答不按难度拆列。</p>
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
          <th v-for="col in DIFFICULTY_COLS" :key="col.key" class="is-num">{{ col.label }}</th>
          <th class="is-num">选择</th>
          <th class="is-num">填空</th>
          <th class="is-num">解答</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="stage in STAGES" :key="stage">
          <td class="cep-row-label">{{ stageLabel(stage) }}</td>
          <td class="is-num">{{ chapter.difficulty[stage].total }}</td>
          <td v-for="col in DIFFICULTY_COLS" :key="col.key" class="is-num">
            {{ chapter.difficulty[stage][col.key] }}
            <small class="cep-stage-tag">{{ ratio(chapter.difficulty[stage][col.key], chapter.difficulty[stage].total) }}</small>
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
