<script setup lang="ts">
import { computed } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamSection,
  ChapterExamSkillRow,
  ChapterExamStage,
  ChapterExamStageStats,
} from '../../api/question-bank'
import type { TrainingOverviewNode } from '../../api/training'

type StageSelection = ChapterExamStage | 'all'

const props = defineProps<{
  section: ChapterExamSection
  stage: StageSelection
  stages: ChapterExamStageStats[]
  mastery: ReadonlyMap<string, TrainingOverviewNode>
  masteryState: 'idle' | 'loading' | 'ready' | 'error'
}>()

const emit = defineEmits<{ cell: [title: string, ids: number[]] }>()

const COLS: { key: ChapterExamCellColumn; label: string }[] = [
  { key: 'choice_basic', label: '选择·基础' },
  { key: 'choice_advanced', label: '选择·中高' },
  { key: 'fill', label: '填空' },
  { key: 'written', label: '解答' },
]

const POS_BUCKETS = [...Array.from({ length: 19 }, (_, index) => String(index + 1)), '20+']
const POS_STAGES: ChapterExamStage[] = ['midterm', 'final']

const stageInfo = computed(
  () => Object.fromEntries(props.stages.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)

function stageLabel(stage: ChapterExamStage): string {
  return stageInfo.value[stage]?.label ?? stage
}

const stageKey = computed<ChapterExamStage>(() => (props.stage === 'all' ? 'midterm' : props.stage))
const stageTitle = computed(() => (props.stage === 'all' ? '合计' : stageLabel(stageKey.value)))

function cellIds(skill: ChapterExamSkillRow, col: ChapterExamCellColumn): number[] {
  if (props.stage === 'all') {
    return [...skill.cells.midterm[col], ...skill.cells.final[col]]
  }
  return skill.cells[stageKey.value][col]
}

const heatMax = computed(() => {
  let max = 0
  for (const skill of props.section.skills) {
    for (const col of COLS) max = Math.max(max, cellIds(skill, col.key).length)
  }
  return max
})

const posMax = computed(() => {
  let max = 0
  for (const skill of props.section.skills) {
    for (const stage of POS_STAGES) {
      for (const bucket of POS_BUCKETS) {
        max = Math.max(max, skill.positions[stage]?.[bucket]?.length ?? 0)
      }
    }
  }
  return max
})

function heatOf(value: number, max: number): string {
  if (!value) return ''
  return `is-heat-${Math.max(1, Math.ceil((value / Math.max(max, 1)) * 5))}`
}

function openCell(skill: ChapterExamSkillRow, col: (typeof COLS)[number]): void {
  const ids = cellIds(skill, col.key)
  if (!ids.length) return
  emit('cell', `${props.section.label} · ${skill.name} · ${col.label} · ${stageTitle.value}`, ids)
}

function openPosition(skill: ChapterExamSkillRow, stage: ChapterExamStage, bucket: string): void {
  const ids = skill.positions[stage]?.[bucket] ?? []
  if (!ids.length) return
  const place = bucket === '20+' ? '第20题及以后' : `第${bucket}题`
  emit('cell', `${props.section.label} · ${skill.name} · ${place} · ${stageLabel(stage)}`, ids)
}

function masteryNode(skill: ChapterExamSkillRow): TrainingOverviewNode | undefined {
  return skill.key ? props.mastery.get(skill.key) : undefined
}

function weakShare(node: TrainingOverviewNode): number {
  return node.distribution.weak / node.evidence_student_count
}

function isWeakPick(skill: ChapterExamSkillRow): boolean {
  const node = masteryNode(skill)
  return !!node && node.evidence_student_count > 0
    && skill.total >= 3 && weakShare(node) >= 0.3
}
</script>

<template>
  <section class="cep-block" aria-labelledby="cep-heat-title">
    <h3 id="cep-heat-title" class="cep-block-title">考法热力 · {{ stageTitle }}</h3>
    <div class="app-heat-table-wrap">
    <table class="app-heat-table app-heat-table--compact cep-skill-table">
      <thead>
        <tr>
          <th>技能</th>
          <th v-for="col in COLS" :key="col.key">{{ col.label }}</th>
          <th>本班明显薄弱</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="skill in section.skills" :key="skill.key ?? 'unlinked'">
          <td class="cep-row-label cep-skill-cell">
            <span class="cep-skill-name">
              <span class="cep-skill-title">{{ skill.name }}</span>
              <small>n={{ skill.total }}</small>
              <small v-if="skill.home_section_label">（属 {{ skill.home_section_label }}）</small>
              <details v-if="skill.definition" class="cep-skill-definition">
                <summary aria-label="展开技能定义" />
                <p>{{ skill.definition }}</p>
              </details>
              <span v-if="isWeakPick(skill)" class="cep-badge-warn">常考且薄弱</span>
            </span>
          </td>
          <td
            v-for="col in COLS"
            :key="col.key"
            :class="heatOf(cellIds(skill, col.key).length, heatMax)"
          >
            <button
              v-if="cellIds(skill, col.key).length"
              type="button"
              class="cep-cell"
              @click="openCell(skill, col)"
            >{{ cellIds(skill, col.key).length }}</button>
          </td>
          <td class="cep-mastery">
            <template v-if="masteryState === 'error'">
              <span class="cep-stage-tag">学情暂不可用</span>
            </template>
            <template v-else-if="masteryState !== 'ready'">
              <span class="cep-stage-tag">…</span>
            </template>
            <template v-else-if="masteryNode(skill) && masteryNode(skill)!.evidence_student_count">
              <span class="cep-mastery-bar"><i :style="{ width: `${Math.round(weakShare(masteryNode(skill)!) * 100)}%` }" /></span>
              {{ Math.round(weakShare(masteryNode(skill)!) * 100) }}%（{{ masteryNode(skill)!.distribution.weak }}/{{ masteryNode(skill)!.evidence_student_count }} 人）
            </template>
            <template v-else>
              <span class="cep-stage-tag">无证据</span>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
    </div>
    <p class="cep-note">每道整题只计入一个主要技能（判定点关联最多者）。常考且薄弱＝主考 ≥3 题且明显薄弱占比 ≥30%。</p>
  </section>

  <section class="cep-block" aria-labelledby="cep-pos-title">
    <h3 id="cep-pos-title" class="cep-block-title">题位分布</h3>
    <div class="app-heat-table-wrap">
    <table class="app-heat-table app-heat-table--compact cep-pos-table">
      <thead>
        <tr>
          <th>技能</th>
          <th>阶段</th>
          <th v-for="bucket in POS_BUCKETS" :key="bucket">{{ bucket }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="skill in section.skills" :key="skill.key ?? 'unlinked'">
          <tr v-for="(posStage, index) in POS_STAGES" :key="posStage">
            <td v-if="index === 0" class="cep-row-label" :rowspan="POS_STAGES.length">{{ skill.name }}</td>
            <td class="cep-stage-tag">{{ stageLabel(posStage) }}</td>
            <td
              v-for="bucket in POS_BUCKETS"
              :key="bucket"
              :class="heatOf(skill.positions[posStage]?.[bucket]?.length ?? 0, posMax)"
            >
              <button
                v-if="skill.positions[posStage]?.[bucket]?.length"
                type="button"
                class="cep-cell"
                @click="openPosition(skill, posStage, bucket)"
              >{{ skill.positions[posStage][bucket].length }}</button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    </div>
    <p class="cep-note">题号只表示在试卷中的位置；20+ 合并第 20 题及以后。</p>
  </section>
</template>
