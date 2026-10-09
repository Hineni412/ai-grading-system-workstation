<script setup lang="ts">
import { computed } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamSection,
  ChapterExamSkillRow,
  ChapterExamStage,
  ChapterExamStageStats,
  QuestionBankListItem,
} from '../../api/question-bank'
import type { TrainingOverviewNode } from '../../api/training'
import QuestionCard from './QuestionCard.vue'

const props = defineProps<{
  section: ChapterExamSection
  stages: ChapterExamStageStats[]
  mastery: ReadonlyMap<string, TrainingOverviewNode>
  masteryState: 'idle' | 'loading' | 'ready' | 'error'
  items: ReadonlyMap<number, QuestionBankListItem>
  itemsState: 'idle' | 'loading' | 'ready' | 'error'
}>()

const emit = defineEmits<{
  cell: [title: string, ids: number[]]
  retryItems: []
  similar: [question: QuestionBankListItem]
}>()

const TIER_LABELS = { basic: '基础入口', mid: '常见变式', hard: '提高变化' } as const
type TypicalPick = ChapterExamSkillRow['typical'][number]

const targetLabel = computed(() => props.section.skills.some(row => row.target_kind === 'type') ? '题型' : '技能')

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

const stageSample = computed(() => POS_STAGES
  .map(stage => `${stageLabel(stage)} ${stageInfo.value[stage]?.group_count ?? 0}${stageInfo.value[stage]?.unit ?? '组'}`)
  .join(' · '))

function stageLabel(stage: ChapterExamStage): string {
  return stageInfo.value[stage]?.label ?? stage
}

function skillName(skill: ChapterExamSkillRow): string {
  return skill.name.replace(/^(题型|技能)[·：:]\s*/, '')
}

function cellIds(skill: ChapterExamSkillRow, col: ChapterExamCellColumn): number[] {
  return [...new Set([...skill.cells.midterm[col], ...skill.cells.final[col]])]
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
  emit('cell', `${props.section.label} · ${skillName(skill)} · ${col.label} · 期中＋期末`, ids)
}

function openPosition(skill: ChapterExamSkillRow, stage: ChapterExamStage, bucket: string): void {
  const ids = skill.positions[stage]?.[bucket] ?? []
  if (!ids.length) return
  const place = bucket === '20+' ? '第20题及以后' : `第${bucket}题`
  emit('cell', `${props.section.label} · ${skillName(skill)} · ${place} · ${stageLabel(stage)}`, ids)
}

const picks = computed<TypicalPick[]>(() => props.section.skills.flatMap(skill => skill.typical))

const typicalCards = computed(() => picks.value
  .map(pick => ({ pick, item: props.items.get(pick.question_id) }))
  .filter((entry): entry is { pick: TypicalPick; item: QuestionBankListItem } => !!entry.item))

function pickLabel(pick: TypicalPick): string {
  const role = pick.role ?? TIER_LABELS[pick.tier]
  const reason = pick.reason ?? `同类 ${pick.same_tier_count} 题 · 出现在 ${pick.group_count} 套试卷`
  return `${role} · ${reason}`
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
  <section class="cep-block cep-card" :aria-labelledby="`cep-structure-${section.id}`">
    <header class="cep-card-head">
      <h3 :id="`cep-structure-${section.id}`" class="cep-block-title">{{ section.label }} · 命题结构</h3>
      <p class="cep-card-sub">{{ stageSample }}　颜色＝题数</p>
    </header>

    <div class="cep-subchart">
      <h4 class="cep-subchart-title">怎么考 <small>题型×难度合成热力 · 期中＋期末</small></h4>
      <div class="app-heat-table-wrap">
      <table class="app-heat-table app-heat-table--compact cep-skill-table">
        <thead>
          <tr>
            <th>{{ targetLabel }}</th>
            <th v-for="col in COLS" :key="col.key">{{ col.label }}</th>
            <th>本班明显薄弱</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="skill in section.skills" :key="skill.key ?? 'unlinked'">
            <td class="cep-row-label cep-skill-cell">
              <span class="cep-skill-name">
                <span class="cep-skill-title">{{ skillName(skill) }}</span>
                <small>n={{ skill.total }}</small>
                <small v-if="skill.home_section_label">（属 {{ skill.home_section_label }}）</small>
                <details v-if="skill.definition" class="cep-skill-definition">
                  <summary :aria-label="`展开${targetLabel}定义`" />
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
      <p class="cep-legend"><span class="cep-legend__label">出现次数</span><i class="is-heat-1" /><i class="is-heat-2" /><i class="is-heat-3" /><i class="is-heat-4" /><i class="is-heat-5" /><span>少 → 多</span></p>
      <p class="cep-note">n 为两阶段主考题数之和。{{ targetLabel === '题型' ? '每道整题只按当前主题型统计。' : '每道整题只计入一个主要技能（判定点关联最多者）。' }}常考且薄弱＝主考 ≥3 题且明显薄弱占比 ≥30%。</p>
    </div>

    <div class="cep-subchart">
      <h4 class="cep-subchart-title">落在第几题 <small>期中 vs 期末 · 题号＝试卷位置</small></h4>
      <div class="app-heat-table-wrap cep-pos-wrap">
      <table class="app-heat-table app-heat-table--compact cep-pos-table">
        <thead>
          <tr>
            <th>{{ targetLabel }}</th>
            <th>阶段</th>
            <th v-for="bucket in POS_BUCKETS" :key="bucket">{{ bucket }}</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="skill in section.skills" :key="skill.key ?? 'unlinked'">
            <tr v-for="(posStage, index) in POS_STAGES" :key="posStage">
              <td v-if="index === 0" class="cep-row-label" :rowspan="POS_STAGES.length">{{ skillName(skill) }}</td>
              <td class="cep-stage-tag" :class="{ 'is-final': posStage === 'final' }">{{ stageLabel(posStage) }}</td>
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
        <tfoot>
          <tr class="cep-pos-band">
            <td class="cep-row-label">分段</td>
            <td class="cep-stage-tag" />
            <td colspan="8">第1—8题</td>
            <td colspan="5">第9—13题</td>
            <td colspan="7">第14题起</td>
          </tr>
        </tfoot>
      </table>
      </div>
      <p class="cep-legend"><span class="cep-legend__label">出现次数</span><i class="is-heat-1" /><i class="is-heat-2" /><i class="is-heat-3" /><i class="is-heat-4" /><i class="is-heat-5" /><span>少 → 多</span></p>
      <p class="cep-note">题号只表示试卷位置；20+ 合并第 20 题及以后，两阶段颜色共用同一刻度。空格表示本批样本未出现；横轴分段仅供找题，不用于推定题型。</p>
    </div>
  </section>

  <section v-if="picks.length" class="cep-typical" :aria-labelledby="`cep-typical-${section.id}`">
    <h4 :id="`cep-typical-${section.id}`" class="cep-typical-title">典型题 · {{ picks.length }} 道</h4>
    <p v-if="itemsState === 'idle' || itemsState === 'loading'" class="cep-note">正在读取典型题…</p>
    <p v-else-if="itemsState === 'error'" class="cep-note">
      典型题暂时无法读取。<button type="button" class="cep-inline-link" @click="emit('retryItems')">重试</button>
    </p>
    <div v-else class="cep-typical-list qb-question-list">
      <QuestionCard
        v-for="entry in typicalCards"
        :key="entry.pick.question_id"
        :question="entry.item"
        :badge-label="pickLabel(entry.pick)"
        @similar="emit('similar', $event)"
      />
    </div>
  </section>
</template>
