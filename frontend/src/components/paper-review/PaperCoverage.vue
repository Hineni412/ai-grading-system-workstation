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
function parents(t: CoverageType) {
  return [...new Set(ids(t).map(id => props.review.items.find(i => i.id === id)?.parent_id ?? id))].sort((a,b) => a.localeCompare(b,undefined,{numeric:true}))
}
function tier(d: number | null) { return d == null ? '难度暂无' : `${d<4 ? '基础' : d<7 ? '中档' : '偏难'} ${d.toFixed(1)}` }
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
    <p class="pr-hint">只看本卷已选名额有没有给高频题型，不要求塞满。偏难题型没选不扣分。</p>
    <template v-if="coverage?.key==='coverage'">
      <article v-for="s in coverage.detail.sections" :key="s.section" class="pr-card">
        <header><h3>{{ s.section }} · 选题效率 {{ pct(s.ratio,0) }}</h3><span class="pr-hint">本卷选了 {{ s.types.filter(t=>t.credit>0).length }} 个题型</span></header>
        <div class="pr-coverage-tiles">
          <div v-for="t in s.types.filter(t=>t.shown)" :key="t.key" class="pr-type-tile" :class="t.credit===1 ? 'selected' : t.credit===.5 ? 'secondary' : t.hard ? 'hard' : 'skipped'">
            <button class="pr-type-title" :disabled="t.credit>0" @click="openReference(t)">{{ t.name.replace(/^题型·/,'') }}</button>
            <span class="pr-type-meta">{{ t.group_count }}套 · {{ tier(t.difficulty) }} <b v-if="t.credit===.5">次要</b><b v-else-if="!t.credit">{{ t.hard ? '偏难·可不选' : '更高频未选' }}</b></span>
            <div v-if="t.credit>0" class="pr-type-ids" :title="ids(t).join('、')"><button v-for="id in parents(t)" :key="id" @click="$emit('go',id)">{{ id }}</button></div>
          </div>
        </div>
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
