<script setup lang="ts">
import { computed } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamChapter,
  ChapterExamSection,
  ChapterExamStage,
  ChapterExamStageStats,
} from '../../api/question-bank'

type StageSelection = ChapterExamStage | 'all'

const props = defineProps<{
  chapter: ChapterExamChapter
  stage: StageSelection
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

const stageInfo = computed(
  () => Object.fromEntries(props.stages.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)
const panels = computed<ChapterExamStage[]>(
  () => (props.stage === 'all' ? ['midterm', 'final'] : [props.stage]),
)
const crossMidterm = computed(() => props.chapter.cross_question_ids.midterm)
const crossFinal = computed(() => props.chapter.cross_question_ids.final)

function stageLabel(stage: ChapterExamStage): string {
  return stageInfo.value[stage]?.label ?? stage
}

function coverageText(section: ChapterExamSection, stage: ChapterExamStage): string {
  const cell = section.coverage[stage]
  const percent = cell.percent === null ? '—' : `${cell.percent}%`
  return `${cell.groups}/${cell.of}${stageInfo.value[stage]?.unit ?? '份'} · ${percent}`
}

function coverageDelta(section: ChapterExamSection): string {
  const first = section.coverage.midterm.percent
  const second = section.coverage.final.percent
  if (first === null || second === null) return '—'
  const delta = second - first
  if (delta === 0) return '持平'
  return `${delta > 0 ? '+' : '−'}${Math.abs(delta)} 个百分点`
}

function coverageHeat(percent: number | null): string {
  if (percent === null) return ''
  if (percent >= 90) return 'cep-heat-5'
  if (percent >= 75) return 'cep-heat-4'
  if (percent >= 60) return 'cep-heat-3'
  if (percent >= 40) return 'cep-heat-2'
  if (percent > 0) return 'cep-heat-1'
  return ''
}

const overviewMax = computed(() => {
  let max = 0
  for (const section of props.chapter.sections) {
    for (const stage of panels.value) {
      for (const col of COLS) {
        max = Math.max(max, section.overview[stage][col.key].length)
      }
    }
  }
  return max
})

function countHeat(value: number): string {
  if (!value) return ''
  return `cep-heat-${Math.max(1, Math.ceil((value / Math.max(overviewMax.value, 1)) * 5))}`
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
  <section class="cep-block" aria-labelledby="cep-coverage-title">
    <h3 id="cep-coverage-title" class="cep-block-title">小节出卷率</h3>
    <div class="cep-table-scroll">
    <table class="cep-table">
      <thead>
        <tr><th>小节</th><th>期中</th><th>期末</th><th>变化</th></tr>
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
          <td :class="coverageHeat(section.coverage.midterm.percent)">{{ coverageText(section, 'midterm') }}</td>
          <td :class="coverageHeat(section.coverage.final.percent)">{{ coverageText(section, 'final') }}</td>
          <td>{{ coverageDelta(section) }}</td>
        </tr>
      </tbody>
    </table>
    </div>
    <p class="cep-note">出卷率＝至少有 1 道本节主考题的试卷数 ÷ 该阶段试卷数（同源卷合并后）。点击表格中的数字查看对应题目。</p>
  </section>

  <section class="cep-block" aria-labelledby="cep-overview-title">
    <h3 id="cep-overview-title" class="cep-block-title">命题总览</h3>
    <div class="cep-panels">
      <div v-for="panel in panels" :key="panel">
        <p class="cep-panel-title">{{ stageLabel(panel) }}</p>
        <div class="cep-table-scroll">
        <table class="cep-table">
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
    <p class="cep-note">基础＝难度 1–3.9，中高＝4–10；填空、解答不分难度。按整题计数。</p>
  </section>

  <section class="cep-block" aria-labelledby="cep-difficulty-title">
    <h3 id="cep-difficulty-title" class="cep-block-title">难度与题型</h3>
    <div class="cep-table-scroll">
    <table class="cep-table">
      <thead>
        <tr>
          <th>阶段</th>
          <th>总题数</th>
          <th v-for="col in DIFFICULTY_COLS" :key="col.key">{{ col.label }}</th>
          <th>选择</th>
          <th>填空</th>
          <th>解答</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="stage in (['midterm', 'final'] as ChapterExamStage[])" :key="stage">
          <td class="cep-row-label">{{ stageLabel(stage) }}</td>
          <td>{{ chapter.difficulty[stage].total }}</td>
          <td v-for="col in DIFFICULTY_COLS" :key="col.key">
            {{ chapter.difficulty[stage][col.key] }}
            <small class="cep-stage-tag">{{ ratio(chapter.difficulty[stage][col.key], chapter.difficulty[stage].total) }}</small>
          </td>
          <td>{{ chapter.difficulty[stage].choice }}</td>
          <td>{{ chapter.difficulty[stage].fill }}</td>
          <td>{{ chapter.difficulty[stage].written }}</td>
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
</template>
