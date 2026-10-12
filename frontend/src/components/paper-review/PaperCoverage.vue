<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CoverageType, PaperReview } from '../../api/paper-review'
import { assemblyApi, type AssemblyQuestion } from '../../api/assembly'
import AppDialog from '../design-system/AppDialog.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import { pct } from './display'
const props = defineProps<{ review: PaperReview }>()
defineEmits<{ go: [id: string] }>()
const coverage = computed(() => props.review.dimensions.find(d => d.key === 'coverage'))
const reference = ref<{ name: string; role: string; stem: string } | null>(null)
const referenceQuestion = ref<AssemblyQuestion | null>(null)
const referenceLoading = ref(false)
const showReference = ref(false)
function ids(t: CoverageType) { const d = props.review.coverage_types[t.key]; return [...new Set([...(d?.covered_by ?? []),...(d?.secondary_by ?? [])])] }
function parents(t: CoverageType) { return parentsOf(t.key) }
function parentsOf(key: string) {
  const d = props.review.coverage_types[key]
  return [...new Set([...(d?.covered_by ?? []),...(d?.secondary_by ?? [])].map(id => props.review.items.find(i => i.id === id)?.parent_id ?? id))].sort((a,b) => a.localeCompare(b,undefined,{numeric:true}))
}
function tier(d: number | null) { return d == null ? '难度暂无' : `${d<4 ? '基础' : d<7 ? '中档' : '偏难'} ${d.toFixed(1)}` }
function difficulty(d: number | null) { return d == null ? '难度暂无' : `难度 ${d.toFixed(1)}` }
function priorText(p: number | null) { return p == null ? '考前无数据' : `考前 ${pct(p,0)}` }
const statusBadges: Record<CoverageType['status'], string> = { picked:'', replace:'建议替换', swap_in:'可换入', tradeoff:'正常取舍', out_of_range:'难度超出本卷范围', hard:'偏难·可不选', low:'', elsewhere:'其他小节已考' }
function tileClass(t: CoverageType) {
  if (t.status === 'elsewhere') return 'selected muted'
  if (t.credit > 0) return [t.credit === 1 ? 'selected' : 'secondary', t.status === 'replace' ? 'replace' : '']
  return t.status === 'swap_in' || t.status === 'tradeoff' ? 'swappable' : 'unfit'
}
async function openReference(t: CoverageType) {
  const typical = props.review.coverage_types[t.key]?.typical[0]
  reference.value = {name:t.name.replace(/^题型·/,''),role:typical?.role ?? '参考典型题',stem:typical?.stem ?? ''}
  referenceQuestion.value = null
  showReference.value = true
  if (typical?.question_id == null) return
  referenceLoading.value = true
  try {
    const list = await assemblyApi.resolveQuestions([typical.question_id])
    referenceQuestion.value = list.items.find(q => q.id === typical.question_id) ?? null
  } catch {
    referenceQuestion.value = null
  } finally {
    referenceLoading.value = false
  }
}
</script>
<template>
  <section id="paper-coverage" class="pr-coverage">
    <h2>高频题型覆盖</h2>
    <p class="pr-hint">同一小节、同等名额、难度在本节已选范围 ±1 内比较：没选的题型更高频且考前掌握至少低 10 个百分点（缺考前数据时只比频次）才建议替换；系统看不到教学进度和出题意图，仅供参考。</p>
    <template v-if="coverage?.key==='coverage'">
      <article v-for="s in coverage.detail.sections" :key="s.section" class="pr-card">
        <header><h3>{{ s.section }} · 选题效率 {{ pct(s.ratio,0) }}</h3><span class="pr-hint">本卷选了 {{ s.types.filter(t=>t.credit>0).length }} 个题型</span></header>
        <div class="pr-coverage-tiles">
          <div v-for="t in s.types.filter(t=>t.shown)" :key="t.key" class="pr-type-tile" :class="tileClass(t)">
            <button class="pr-type-title" :disabled="t.credit>0" @click="openReference(t)">{{ t.name.replace(/^题型·/,'') }}</button>
            <span class="pr-type-meta">{{ t.group_count }}套 · {{ tier(t.difficulty) }} · {{ priorText(t.prior) }}<b v-if="t.credit===.5">次要</b><b v-if="statusBadges[t.status]">{{ statusBadges[t.status] }}</b></span>
            <div v-if="t.credit>0" class="pr-type-ids" :title="ids(t).join('、')"><button v-for="id in parents(t)" :key="id" @click="$emit('go',id)">{{ id }}</button></div>
          </div>
        </div>
        <ul v-if="s.suggestions.length" class="pr-swap-list">
          <li v-for="g in s.suggestions" :key="`${g.in_key}-${g.out_key}`">可考虑用 <b>{{ g.in_name.replace(/^题型·/,'') }}</b>（{{ g.in_count }}套 · {{ priorText(g.in_prior) }} · {{ difficulty(g.in_difficulty) }}）替换 <b>{{ g.out_name.replace(/^题型·/,'') }}</b>（{{ g.out_count }}套 · {{ priorText(g.out_prior) }} · {{ difficulty(g.out_difficulty) }} <button v-for="id in parentsOf(g.out_key)" :key="id" @click="$emit('go',id)">{{ id }}</button>）：{{ g.by_frequency_only ? '更高频（考前无数据，只比较频次）' : '更高频，也更薄弱' }}</li>
        </ul>
        <details v-if="s.types.some(t=>!t.shown)" class="pr-hidden-types"><summary>另有 {{ s.types.filter(t=>!t.shown).length }} 个题型低于本卷最低频选题（{{ s.floor }}套），未列出</summary><button v-for="t in s.types.filter(t=>!t.shown)" :key="t.key" class="pr-chip na" @click="openReference(t)">{{ t.name.replace(/^题型·/,'') }} · {{ t.group_count }}套 · {{ tier(t.difficulty) }}</button></details>
      </article>
      <p v-if="!coverage.detail.sections.length" class="pr-empty">没有可对照的小节，覆盖维度未计入总分。</p>
    </template>
    <p v-if="review.unlinked_sections" class="pr-hint">另有 {{ review.unlinked_sections }} 个小节未关联章节，未做对照。</p>
    <AppDialog v-model:open="showReference" :title="reference?.name ?? '参考典型题'" description="来自章节考情的参考题，供核对题型。">
      <template v-if="reference"><p>{{ reference.role }}</p><p v-if="referenceLoading">正在读取参考题…</p><QuestionContentRenderer v-else-if="referenceQuestion || reference.stem" :blocks="referenceQuestion?.rich_content?.question_blocks" :fallback="reference.stem" media-mode="list" typeset-text /><p v-else>该题型暂无参考典型题。</p></template>
    </AppDialog>
  </section>
</template>
