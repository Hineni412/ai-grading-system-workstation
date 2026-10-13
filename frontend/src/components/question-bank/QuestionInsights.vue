<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { knowledgeLeafLabel, type QuestionBankListItem } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../../stores/mastery-overview'
import { buildQuestionInsight } from './question-insights'
import '../../styles/knowledge-overview.css'

const props = defineProps<{ question: QuestionBankListItem; expandable?: boolean }>()
const curriculum = useCurriculumScopeStore()
const mastery = useMasteryOverviewStore()
const detailOpen = ref(false)

watch([() => curriculum.selectedVolumeId, () => mastery.scopeSelection], ([volume]) => {
  if (!volume) return
  if (mastery.loadState === 'idle') void mastery.peek(volume)
  mastery.ensureKeySteps(volume)
}, { immediate: true })

const insight = computed(() => buildQuestionInsight(props.question, {
  loadState: mastery.loadState,
  hasOverview: mastery.overview != null,
  examUsesByQuestion: mastery.examUsesByQuestion,
  typeNodeByKey: mastery.typeNodeByKey,
  keyStepState: mastery.keyStepState,
  keyStepStats: mastery.keyStepStats,
}))

const pct = (value: number | null | undefined) => value == null ? '—' : `${Math.round(value * 100)}%`
const tone = (value: number | null | undefined) => value == null ? '' : value < .4 ? 'is-danger' : value < .6 ? 'is-warning' : value < .8 ? 'is-accent' : 'is-success'

const examRate = computed(() => {
  const rows = insight.value.exam.rows
  if (!rows.length) return null
  if (rows.length === 1) return rows[0]?.rate ?? null
  const rates = rows.map(row => row.rate).filter((rate): rate is number => rate !== null)
  return rates.length ? Math.min(...rates) : null
})

const topStep = computed(() => {
  let best: (typeof insight.value.steps.rows)[number] | null = null
  for (const row of insight.value.steps.rows) {
    if (!best || row.repeated > best.repeated || (row.repeated === best.repeated && row.rate > best.rate)) best = row
  }
  return best
})

const tierTotal = computed(() => {
  const dist = insight.value.type.distribution
  if (!dist) return 0
  return Math.max(insight.value.type.evidenced, dist.weak + dist.unsteady + dist.stable + dist.insufficient, 1)
})
const tierWidth = (count: number) => `${(count / tierTotal.value) * 100}%`

const TIPS = {
  exam: '本学期所选学生考试中这道原题的班级得分率（有效得分合计 ÷ 满分合计），不含训练。',
  type: '本题主题型在所选学生中的群体掌握度；需关注 = 明显薄弱 + 还不稳，分母为有证据的学生数。这是题型整体情况，不是本题得分率。',
  steps: '本题直接关联的关键步骤在本主题型下的统计：独立失分人数 / 有作答记录人数；反复卡住 = 同一学生同一关键步骤在至少 2 道不同小问独立失分；共性 = 至少 2 个题型中独立失分率均达到 30%。',
  frequency: '同主题型、同难度档的题在该阶段同源卷组中的覆盖比例，剔除本题所在卷组。',
}
</script>

<template>
  <div class="qb-insights">
    <p class="qb-insight-line" :class="{ 'is-expandable': expandable }">
      <span class="qb-insight-item" :title="TIPS.exam">
        <span class="qb-insight-k">本校</span>
        <span v-if="insight.exam.state === 'loading'" class="qb-insight-mute">读取中…</span>
        <span v-else-if="insight.exam.state === 'unavailable'" class="qb-insight-mute">暂不可用</span>
        <span v-else-if="!insight.exam.rows.length" class="qb-insight-mute">本校未考</span>
        <template v-else>考过{{ insight.exam.rows.length }}次<template v-if="insight.exam.rows.length > 1"> 最低</template> <strong class="qb-insight-v" :class="tone(examRate)">{{ pct(examRate) }}</strong></template>
      </span>
      <span class="qb-insight-item" :title="TIPS.type">
        <span class="qb-insight-k">题型掌握</span>
        <span v-if="insight.type.state === 'loading'" class="qb-insight-mute">读取中…</span>
        <span v-else-if="insight.type.state === 'unavailable'" class="qb-insight-mute">暂不可用</span>
        <span v-else-if="insight.type.state === 'untyped'" class="qb-insight-mute">未归题型</span>
        <span v-else-if="insight.type.state === 'no-data'" class="qb-insight-mute">暂无题型学情</span>
        <span v-else-if="insight.type.mastery === null" class="qb-insight-mute">证据不足</span>
        <template v-else><strong class="qb-insight-v" :class="tone(insight.type.mastery)">{{ pct(insight.type.mastery) }}</strong> <span class="qb-insight-mute">需关注 {{ insight.type.need }}/{{ insight.type.evidenced }}</span><span v-if="insight.type.distribution" class="overview-tier-bar qb-insight-bar" aria-hidden="true"><i class="is-weak" :style="{ width: tierWidth(insight.type.distribution.weak) }" /><i class="is-unsteady" :style="{ width: tierWidth(insight.type.distribution.unsteady) }" /><i class="is-stable" :style="{ width: tierWidth(insight.type.distribution.stable) }" /><i class="is-insufficient" :style="{ width: tierWidth(insight.type.distribution.insufficient) }" /></span></template>
      </span>
      <span class="qb-insight-item" :title="TIPS.steps">
        <span class="qb-insight-k">卡点</span>
        <span v-if="insight.steps.state === 'loading'" class="qb-insight-mute">读取中…</span>
        <span v-else-if="insight.steps.state === 'unavailable'" class="qb-insight-mute">暂不可用</span>
        <span v-else-if="!insight.steps.rows.length" class="qb-insight-mute">暂无步骤证据</span>
        <span v-else-if="!topStep || !topStep.repeated" class="qb-insight-mute">无反复卡点</span>
        <template v-else><span class="qb-insight-v is-warning">{{ topStep.name }} {{ topStep.repeated }}人反复</span><em v-if="topStep.common" class="qb-insight-badge">共性</em></template>
      </span>
      <span class="qb-insight-item" :title="TIPS.frequency">
        <span class="qb-insight-k">考频</span>
        <template v-if="insight.frequency">期末 <strong class="qb-insight-freq" :class="{ 'qb-insight-mute': insight.frequency.final === null }">{{ pct(insight.frequency.final) }}</strong> 期中 <strong class="qb-insight-freq" :class="{ 'qb-insight-mute': insight.frequency.midterm === null }">{{ pct(insight.frequency.midterm) }}</strong></template>
        <span v-else class="qb-insight-mute">暂无考频</span>
      </span>
      <button v-if="expandable" type="button" class="qb-insight-toggle" :aria-expanded="detailOpen" @click="detailOpen = !detailOpen">考情详情 {{ detailOpen ? '▾' : '▸' }}</button>
    </p>
    <div v-if="expandable && detailOpen" class="qb-insight-detail">
      <section>
        <h4>本校作答</h4>
        <template v-if="insight.exam.rows.length">
          <p v-for="row in insight.exam.rows" :key="`${row.session}|${row.label}`" class="qb-insight-use"><span class="qb-insight-use__name">{{ row.session }} · 第{{ row.label }}题</span><strong class="qb-insight-v" :class="tone(row.rate)">{{ pct(row.rate) }}</strong><i class="qb-insight-use__bar"><i :class="tone(row.rate)" :style="{ width: `${(row.rate ?? 0) * 100}%` }" /></i></p>
        </template>
        <p v-else class="qb-insight-mute">本学期考试中未出现这道原题</p>
      </section>
      <section>
        <h4>本题型学情<template v-if="insight.type.name"> · {{ knowledgeLeafLabel(insight.type.name).replace(/^题型[·：:]\s*/, '') }}</template></h4>
        <template v-if="insight.type.state === 'ready'">
          <p><strong class="qb-insight-v" :class="tone(insight.type.mastery)">掌握度 {{ pct(insight.type.mastery) }}</strong></p>
          <span v-if="insight.type.distribution" class="overview-tier-bar"><i class="is-weak" :style="{ width: tierWidth(insight.type.distribution.weak) }" /><i class="is-unsteady" :style="{ width: tierWidth(insight.type.distribution.unsteady) }" /><i class="is-stable" :style="{ width: tierWidth(insight.type.distribution.stable) }" /><i class="is-insufficient" :style="{ width: tierWidth(insight.type.distribution.insufficient) }" /></span>
          <p v-if="insight.type.distribution" class="qb-insight-mute">明显薄弱 {{ insight.type.distribution.weak }} · 还不稳 {{ insight.type.distribution.unsteady }} · 较稳定 {{ insight.type.distribution.stable }} · 证据不足 {{ insight.type.distribution.insufficient }}</p>
        </template>
        <p v-else class="qb-insight-mute">{{ insight.type.state === 'loading' ? '读取中…' : insight.type.state === 'unavailable' ? '暂不可用' : insight.type.state === 'untyped' ? '未归题型' : '暂无题型学情' }}</p>
      </section>
      <section>
        <h4>卡在哪一步（本题关键步骤）</h4>
        <template v-if="insight.steps.rows.length">
          <p v-for="row in insight.steps.rows.slice(0, 3)" :key="row.key" class="qb-insight-use"><span class="qb-insight-use__name">{{ row.name }}</span> · 独立失分 {{ row.independent }}/{{ row.observed }} · 反复 {{ row.repeated }}人 <em v-if="row.common" class="qb-insight-badge">共性</em></p>
        </template>
        <p v-else class="qb-insight-mute">暂无本题关键步骤的作答证据</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.qb-insight-line { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; margin: 7px 0 0; padding-top: 7px; border-top: 1px solid var(--color-border-subtle); font-size: var(--font-size-caption); color: var(--color-text-secondary); }
.qb-insight-line.is-expandable { margin-top: 0; padding-top: 0; border-top: 0; }
.qb-insight-item { display: inline-flex; align-items: center; gap: 4px; min-width: 0; }
.qb-insight-k { color: var(--color-text-muted); }
.qb-insight-mute { color: var(--color-text-muted); }
.qb-insight-v { font-weight: var(--font-weight-semibold); }
.qb-insight-v.is-danger, .qb-insight-use__bar i.is-danger { color: var(--color-danger); }
.qb-insight-v.is-warning, .qb-insight-use__bar i.is-warning { color: var(--color-warning); }
.qb-insight-v.is-accent, .qb-insight-use__bar i.is-accent { color: var(--color-accent); }
.qb-insight-v.is-success, .qb-insight-use__bar i.is-success { color: var(--color-success); }
.qb-insight-freq { font-weight: var(--font-weight-semibold); color: var(--color-text-primary); }
.qb-insight-badge { font-style: normal; font-size: 10px; padding: 0 4px; margin-left: 3px; border-radius: 4px; border: 1px solid var(--color-warning); color: var(--color-warning); line-height: 1.4; }
.qb-insight-bar { flex: none; width: 40px; height: 4px; }
.qb-insight-toggle { margin-left: auto; border: 0; background: none; padding: 0; color: var(--color-accent); font-size: var(--font-size-caption); cursor: pointer; }
.qb-insight-detail { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr) minmax(0,1.2fr); gap: 12px; margin-top: 8px; padding: 9px 11px; border-radius: 8px; background: var(--color-bg-subtle); font-size: var(--font-size-caption); color: var(--color-text-secondary); }
.qb-insight-detail h4 { margin: 0 0 5px; font-size: var(--font-size-caption); font-weight: var(--font-weight-semibold); color: var(--color-text-primary); }
.qb-insight-detail p { margin: 3px 0; }
.qb-insight-use { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.qb-insight-use__name { min-width: 0; }
.qb-insight-use__bar { flex: 1 1 30px; display: block; height: 4px; border-radius: 999px; background: var(--color-border-subtle); overflow: hidden; }
.qb-insight-use__bar i { display: block; height: 100%; background: currentColor; }
@container (max-width: 600px) { .qb-insight-detail { grid-template-columns: 1fr; } }
</style>
