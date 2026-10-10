<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import type {
  ChapterExamCellColumn,
  ChapterExamSection,
  ChapterExamSkillRow,
  ChapterExamStage,
  ChapterExamStageStats,
  ChapterExamTypicalPick,
  QuestionBankListItem,
} from '../../api/question-bank'
import type { TrainingOverviewNode } from '../../api/training'
import { isWeakAndFrequent, weakShareOf, weakShareText } from './chapterExamWeak'
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

const TIERS: { key: ChapterExamCellColumn; label: string }[] = [
  { key: 'basic', label: '基础' },
  { key: 'mid', label: '中档' },
  { key: 'hard', label: '提高' },
]

const POS_BUCKETS = [...Array.from({ length: 19 }, (_, index) => String(index + 1)), '20+']
const POS_STAGES: ChapterExamStage[] = ['midterm', 'final']
const POS_BANDS = [
  { label: '第1—8题', start: 0, span: 8 },
  { label: '第9—13题', start: 8, span: 5 },
  { label: '第14题起', start: 13, span: 7 },
]

const stageInfo = computed(
  () => Object.fromEntries(props.stages.map(row => [row.stage, row])) as Record<ChapterExamStage, ChapterExamStageStats>,
)

const stageSample = computed(() => POS_STAGES
  .map(stage => `${stageLabel(stage)} ${stageInfo.value[stage]?.group_count ?? 0}${stageInfo.value[stage]?.unit ?? '组'}`)
  .join(' · '))

const targetLabel = computed(() => props.section.skills.some(row => row.target_kind === 'type') ? '题型' : '技能')

function stageLabel(stage: ChapterExamStage): string {
  return stageInfo.value[stage]?.label ?? stage
}

function skillName(skill: ChapterExamSkillRow): string {
  return skill.name.replace(/^(题型|技能)[·：:]\s*/, '')
}

function tierLabel(tier: ChapterExamCellColumn): string {
  return TIERS.find(row => row.key === tier)?.label ?? tier
}

/* D 怎么考：行内堆叠条，期中＋期末合并，分段＝难度档 */
function cellIds(skill: ChapterExamSkillRow, tier: ChapterExamCellColumn): number[] {
  return [...new Set([...skill.cells.midterm[tier], ...skill.cells.final[tier]])]
}

const barMax = computed(() => Math.max(1, ...props.section.skills.map(skill => skill.total)))

function barWidth(skill: ChapterExamSkillRow): string {
  return `${Math.max(2, (skill.total / barMax.value) * 100)}%`
}

function skillTiers(skill: ChapterExamSkillRow): ChapterExamCellColumn[] {
  return TIERS.map(tier => tier.key).filter(tier => cellIds(skill, tier).length > 0)
}

function stageBuckets(skill: ChapterExamSkillRow, stage: ChapterExamStage): string[] {
  return POS_BUCKETS.filter(bucket => (skill.positions[stage]?.[bucket]?.length ?? 0) > 0)
}

function bucketIndex(bucket: string): number {
  return POS_BUCKETS.indexOf(bucket)
}

function segmentAria(skill: ChapterExamSkillRow, tier: ChapterExamCellColumn): string {
  const mid = skill.cells.midterm[tier].length
  const fin = skill.cells.final[tier].length
  return `${skillName(skill)} · ${tierLabel(tier)} ${mid + fin} 题（期中 ${mid} ＋ 期末 ${fin}）`
}

function openCell(skill: ChapterExamSkillRow, tier: ChapterExamCellColumn): void {
  const ids = cellIds(skill, tier)
  if (!ids.length) return
  emit('cell', `${props.section.label} · ${skillName(skill)} · ${tierLabel(tier)} · 期中＋期末`, ids)
}

/* 本班明显薄弱：分母剔除证据不足 */
function masteryNode(skill: ChapterExamSkillRow): TrainingOverviewNode | undefined {
  return skill.key ? props.mastery.get(skill.key) : undefined
}

function weakText(skill: ChapterExamSkillRow): string {
  if (props.masteryState === 'error') return '学情暂不可用'
  if (props.masteryState !== 'ready') return '…'
  return weakShareText(masteryNode(skill))
}

function hasWeakBar(skill: ChapterExamSkillRow): boolean {
  const { share } = weakShareOf(masteryNode(skill))
  return share !== null && share >= 0
}

function weakWidth(skill: ChapterExamSkillRow): string {
  const { share } = weakShareOf(masteryNode(skill))
  return share === null || share < 0 ? '0%' : `${Math.round(share * 100)}%`
}

function isWeakPick(skill: ChapterExamSkillRow): boolean {
  return props.masteryState === 'ready' && isWeakAndFrequent(skill.total, masteryNode(skill))
}

/* E 落在第几题：气泡带，期中在上、期末在下 */
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

function bubbleSize(count: number): string {
  if (!count || !posMax.value) return '0px'
  return `${Math.round(8 + Math.sqrt(count / posMax.value) * 26)}px`
}

function bucketPlace(bucket: string): string {
  return bucket === '20+' ? '第20题及以后' : `第${bucket}题`
}

function openPosition(skill: ChapterExamSkillRow, stage: ChapterExamStage, bucket: string): void {
  const ids = skill.positions[stage]?.[bucket] ?? []
  if (!ids.length) return
  emit('cell', `${props.section.label} · ${skillName(skill)} · ${bucketPlace(bucket)} · ${stageLabel(stage)}`, ids)
}

function positionAria(skill: ChapterExamSkillRow, stage: ChapterExamStage, bucket: string): string {
  const count = skill.positions[stage]?.[bucket]?.length ?? 0
  return `${skillName(skill)} · ${stageLabel(stage)} · ${bucketPlace(bucket)} · ${count} 题`
}

/* F 典型题：一型一卡，卡内按难度档切换 */
interface TypCard {
  row: ChapterExamSkillRow
  rank: number
  key: string
}

const typicalCards = computed<TypCard[]>(() => {
  const keyed = props.section.skills.filter(row => !row.unlinked)
  return keyed
    .filter(row => row.typical.length)
    .map(row => ({
      row,
      rank: keyed.indexOf(row) + 1,
      key: row.key ?? 'unlinked',
    }))
})

const pickIndex = reactive<Record<string, number>>({})

function currentPick(card: TypCard): ChapterExamTypicalPick {
  const index = Math.min(pickIndex[card.key] ?? 0, card.row.typical.length - 1)
  return card.row.typical[index] ?? card.row.typical[0]!
}

function currentItem(card: TypCard): QuestionBankListItem | undefined {
  return props.items.get(currentPick(card).question_id)
}

function selectPick(card: TypCard, index: number): void {
  pickIndex[card.key] = index
}

function openFull(card: TypCard): void {
  const pick = currentPick(card)
  emit('cell', `${props.section.label} · ${skillName(card.row)} · 典型题`, [pick.question_id])
}

const track = ref<HTMLElement | null>(null)
const activeCard = ref(0)

function scrollToCard(index: number): void {
  const el = track.value?.children.item(Math.max(0, Math.min(index, typicalCards.value.length - 1)))
  el?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' })
}

function stepCard(delta: number): void {
  scrollToCard(activeCard.value + delta)
}

function onTrackScroll(): void {
  const el = track.value
  if (!el || !el.children.length) return
  const first = el.children.item(0) as HTMLElement
  const width = first.getBoundingClientRect().width + 14
  if (width <= 14) return
  activeCard.value = Math.max(0, Math.min(
    typicalCards.value.length - 1,
    Math.round(el.scrollLeft / width),
  ))
}

function onTrackKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    stepCard(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    stepCard(1)
  }
}
</script>

<template>
  <section class="cep-block cep-card" :aria-labelledby="`cep-structure-${section.id}`">
    <header class="cep-card-head">
      <h3 :id="`cep-structure-${section.id}`" class="cep-block-title">{{ section.label }} · 命题结构</h3>
      <p class="cep-card-sub">{{ stageSample }}　怎么考＝{{ targetLabel }}×难度档（期中＋期末合并），下方为题号分布</p>
    </header>

    <div class="cep-subchart">
      <h4 class="cep-subchart-title">怎么考 <small>{{ targetLabel }}×难度档 · 期中＋期末 · 条长＝n</small></h4>
      <div class="cep-how">
        <div v-for="skill in section.skills" :key="skill.key ?? 'unlinked'" class="cep-how-row">
          <div class="cep-how-label">
            <span class="cep-skill-title">{{ skillName(skill) }}</span>
            <small>n={{ skill.total }}</small>
            <small v-if="skill.home_section_label">（属 {{ skill.home_section_label }}）</small>
            <details v-if="skill.definition" class="cep-skill-definition">
              <summary :aria-label="`展开${targetLabel}定义`" />
              <p>{{ skill.definition }}</p>
            </details>
            <span v-if="isWeakPick(skill)" class="cep-badge-warn">常考且薄弱</span>
          </div>
          <div class="cep-how-track">
            <div class="cep-how-bar" :style="{ width: barWidth(skill) }">
              <button
                v-for="tier in skillTiers(skill)"
                :key="tier"
                type="button"
                class="cep-seg"
                :class="`is-${tier}`"
                :style="{ flexGrow: cellIds(skill, tier).length }"
                :title="segmentAria(skill, tier)"
                :aria-label="segmentAria(skill, tier)"
                @click="openCell(skill, tier)"
              ><span class="cep-seg-n">{{ cellIds(skill, tier).length }}</span></button>
            </div>
          </div>
          <div class="cep-how-weak">
            <span v-if="masteryState === 'ready' && hasWeakBar(skill)" class="cep-mastery-bar"><i :style="{ width: weakWidth(skill) }" /></span>
            <span class="cep-weak-text">{{ weakText(skill) }}</span>
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
      <p class="cep-note">n 为两阶段主考题数之和，无难度题不进入分段。{{ targetLabel === '题型' ? '每道整题只按当前主题型统计。' : '每道整题只计入一个主要技能（判定点关联最多者）。' }}常考且薄弱＝主考 ≥3 题且明显薄弱占比 ≥30%（分母剔除证据不足的学生）。</p>
    </div>

    <div class="cep-subchart">
      <h4 class="cep-subchart-title">落在第几题 <small>期中在上 · 期末在下 · 题号＝试卷位置 · 气泡面积∝题数</small></h4>
      <div class="cep-pos">
        <div class="cep-pos-bandrow" aria-hidden="true">
          <span class="cep-pos-bandcell" />
          <span class="cep-pos-bandflex">
            <span
              v-for="band in POS_BANDS"
              :key="band.label"
              class="cep-pos-bandlabel"
              :style="{ width: `${(band.span / POS_BUCKETS.length) * 100}%` }"
            >{{ band.label }}</span>
          </span>
        </div>
        <div v-for="skill in section.skills" :key="skill.key ?? 'unlinked'" class="cep-pos-row">
          <div class="cep-pos-label">{{ skillName(skill) }}</div>
          <div class="cep-pos-track">
            <span
              v-for="band in POS_BANDS"
              :key="band.label"
              class="cep-pos-band"
              aria-hidden="true"
              :style="{ left: `${(band.start / POS_BUCKETS.length) * 100}%`, width: `${(band.span / POS_BUCKETS.length) * 100}%` }"
            />
            <span class="cep-pos-center" aria-hidden="true" />
            <template v-for="stage in POS_STAGES" :key="stage">
              <button
                v-for="bucket in stageBuckets(skill, stage)"
                :key="`${stage}-${bucket}`"
                type="button"
                class="cep-pos-dot"
                :class="`is-${stage}`"
                :style="{
                  left: `${((bucketIndex(bucket) + 0.5) / POS_BUCKETS.length) * 100}%`,
                  width: bubbleSize(skill.positions[stage]?.[bucket]?.length ?? 0),
                  height: bubbleSize(skill.positions[stage]?.[bucket]?.length ?? 0),
                }"
                :title="positionAria(skill, stage, bucket)"
                :aria-label="positionAria(skill, stage, bucket)"
                @click="openPosition(skill, stage, bucket)"
              />
            </template>
          </div>
        </div>
        <div class="cep-pos-axisrow" aria-hidden="true">
          <span class="cep-pos-bandcell" />
          <span v-for="bucket in POS_BUCKETS" :key="bucket" class="cep-pos-axiscell">{{ bucket }}</span>
        </div>
      </div>
      <p class="cep-note">题号只表示试卷位置；20+ 合并第 20 题及以后。空位表示本批样本未出现；横轴分段仅供找题，不用于推定题型。</p>
    </div>
  </section>

  <section v-if="typicalCards.length" class="cep-typical" :aria-labelledby="`cep-typical-${section.id}`">
    <h4 :id="`cep-typical-${section.id}`" class="cep-typical-title">典型题 · {{ typicalCards.length }} 个常考{{ targetLabel }}</h4>
    <p v-if="itemsState === 'idle' || itemsState === 'loading'" class="cep-note">正在读取典型题…</p>
    <p v-else-if="itemsState === 'error'" class="cep-note">
      典型题暂时无法读取。<button type="button" class="cep-inline-link" @click="emit('retryItems')">重试</button>
    </p>
    <div v-else class="cep-car">
      <div class="cep-car-ctl">
        <button type="button" :disabled="activeCard <= 0" @click="stepCard(-1)">‹ 上一卡</button>
        <span class="cep-car-count">{{ activeCard + 1 }} / {{ typicalCards.length }}</span>
        <button type="button" :disabled="activeCard >= typicalCards.length - 1" @click="stepCard(1)">下一卡 ›</button>
      </div>
      <div
        ref="track"
        class="cep-car-track"
        tabindex="0"
        aria-label="典型题卡片，左右方向键切换"
        @scroll.passive="onTrackScroll"
        @keydown="onTrackKeydown"
      >
        <article v-for="card in typicalCards" :key="card.key" class="cep-qcard">
          <header class="cep-qcard-head">
            <b class="cep-qcard-name">{{ skillName(card.row) }}</b>
            <small>本节第{{ card.rank }}常考 · 共 {{ card.row.total }} 题 · 出现在 {{ card.row.group_count }} 套卷</small>
          </header>
          <div class="cep-qcard-tiers" role="tablist" :aria-label="`${skillName(card.row)}难度档`">
            <button
              v-for="(pick, index) in card.row.typical"
              :key="pick.question_id"
              type="button"
              role="tab"
              class="cep-tier-tab"
              :class="[`is-${pick.tier}`, { 'is-on': currentPick(card) === pick }]"
              :aria-selected="currentPick(card) === pick"
              @click="selectPick(card, index)"
            >{{ tierLabel(pick.tier) }}</button>
          </div>
          <div class="cep-qcard-body">
            <QuestionCard
              v-if="currentItem(card)"
              :question="currentItem(card)!"
              :badge-label="currentPick(card).reason ?? ''"
              @similar="emit('similar', $event)"
            />
            <p v-else class="cep-note">题面未收录。</p>
            <span class="cep-qcard-fade" aria-hidden="true" />
          </div>
          <button type="button" class="cep-inline-link cep-qcard-more" @click="openFull(card)">展开全文 ›</button>
        </article>
      </div>
      <div class="cep-car-dots">
        <button
          v-for="(card, index) in typicalCards"
          :key="card.key"
          type="button"
          :class="[`is-${currentPick(card).tier}`, { 'is-on': index === activeCard }]"
          :aria-label="`跳到第${index + 1}卡：${skillName(card.row)}`"
          @click="scrollToCard(index)"
        />
      </div>
    </div>
  </section>
</template>
