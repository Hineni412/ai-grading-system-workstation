<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { type ExamReferenceProfile, type ExamReferenceRow } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import StatePanel from '../design-system/StatePanel.vue'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{ compact?: boolean }>()
const bank = useQuestionBankStore()
const scope = useCurriculumScopeStore()
const data = ref<ExamReferenceProfile | null>(null)
const error = ref('')
const loading = ref(false)
let controller: AbortController | null = null
async function load() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  const volume = props.compact ? undefined : scope.selectedVolumeId ?? undefined
  data.value = bank.examReferenceProfiles.get(volume ?? '*') ?? null
  error.value = ''
  if (!props.compact && !scope.selectedVolumeId) { loading.value = false; return }
  loading.value = true
  try {
    const result = await bank.loadExamReference(volume, request.signal)
    if (!request.signal.aborted) data.value = result
  } catch (e) {
    if (!request.signal.aborted) error.value = e instanceof Error ? e.message : '读取参照失败'
  } finally { if (!request.signal.aborted) loading.value = false }
}
watch([() => scope.selectedVolumeId, () => bank.readRevision], () => void load(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
const sections = computed<{ title: string; items: ExamReferenceRow[] }[]>(() => [
  { title: '本学期考查的题型', items: data.value?.types ?? [] },
  { title: '本学期直接考查的知识点', items: data.value?.topics ?? [] },
  { title: '具体考法与作答任务', items: data.value?.tasks ?? [] },
])
</script>

<template>
  <aside v-if="compact" class="exam-reference-note" aria-live="polite">
    <span v-if="loading">正在读取中考参照…</span>
    <span v-else-if="error">中考参照读取失败 <AppButton variant="ghost" size="small" @click="load">重试</AppButton></span>
    <RouterLink v-else-if="data?.active" :to="{ path: '/question-bank', query: { tab: 'entrance' } }">深圳中考参照 · {{ data.paper_count }} 套<span v-if="data.pending_year_count"> · {{ data.pending_year_count }} 套年份待核对</span></RouterLink>
    <RouterLink v-else :to="{ path: '/question-bank', query: { tab: 'paper' } }">尚未设置中考参照 · 到试卷库星标深圳中考卷</RouterLink>
  </aside>
  <section v-else class="entrance-profile" aria-label="本学期中考考情">
    <header>
      <h2>{{ scope.selectedVolume?.label || '本学期' }} · 中考考情</h2>
      <p>汇总星标深圳中考卷。重复考查优先，出现一次的考法也保留。较新的年份权重更高。</p>
      <RouterLink :to="{ query: { tab: 'paper' } }">到试卷库调整星标</RouterLink>
    </header>
    <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧当前考试中选择学期，查看该学期内容的中考考法。" />
    <StatePanel v-else-if="loading" kind="loading" title="正在读取中考参照" />
    <StatePanel v-else-if="error" kind="error" :title="error"><AppButton @click="load">重试</AppButton></StatePanel>
    <StatePanel v-else-if="!data?.active" kind="empty" title="先星标深圳中考真题卷" description="可选择最近三套。多份试卷共同确定范围，切换学期后继续使用这些参照。" />
    <template v-else>
      <p class="exam-reference-note">深圳中考参照 · {{ data.paper_count }} 套 · {{ data.total_reference_questions }} 个参考题次（同卷重复上传只计一次）</p>
      <ul class="entrance-profile__papers">
        <li v-for="paper in data.papers" :key="paper.id"><RouterLink :to="{ query: { tab: 'paper', paper: paper.id } }">★ {{ paper.title }}</RouterLink> · {{ paper.year ?? '年份待核对' }} · {{ paper.weight === null ? '暂不计算年份权重' : `年份权重 ${paper.weight}` }}<span v-if="paper.year === null">（{{ paper.year_reason }}）</span></li>
      </ul>
      <p v-if="data.pending_question_count" role="status">{{ data.pending_question_count }} 个参考题次的直接知识点或判定资料待补齐，当前报告可能不完整。</p>
      <p v-if="!data.topics?.length">现有标签中还没有可归入本学期的直接考查内容。请先补齐参考卷分析。</p>
      <section v-for="section in sections.filter(s => s.items.length)" :key="section.title">
        <h3>{{ section.title }}</h3>
        <p v-if="section.title === '具体考法与作答任务'">保留每种已记录的具体任务；表述不同的任务分别列出。</p>
        <ul class="entrance-profile__rows">
          <li v-for="(row, index) in section.items" :key="row.key ?? index">
            <div><strong>{{ row.name }}</strong><small v-if="row.type_name">{{ row.type_name }}</small></div>
            <span>{{ row.paper_count }}/{{ data.paper_count }} 套<span v-if="row.paper_count === data.paper_count && data.paper_count > 1"> · 共同重点</span></span>
            <details><summary>查看出处（{{ row.sources.length }}）</summary><ul><li v-for="(source, i) in row.sources" :key="i"><RouterLink :to="{ query: { tab: 'paper', paper: source.paper_id, question: source.question_id } }">{{ source.year ?? '年份待核对' }} · 第 {{ source.question_number }} 题 {{ source.part_id ?? '' }}</RouterLink></li></ul></details>
          </li>
        </ul>
      </section>
    </template>
  </section>
</template>

<style scoped>
.exam-reference-note { color: var(--color-text-secondary); font-size: 13px; padding: 6px 12px; }
.entrance-profile { padding: 20px; overflow: auto; }
.entrance-profile h2 { font-size: 20px; margin: 0 0 8px; }
.entrance-profile h3 { font-size: 16px; margin: 24px 0 12px; }
.entrance-profile p { color: var(--color-text-secondary); line-height: 1.7; }
.entrance-profile__papers { padding-left: 20px; line-height: 1.9; }
.entrance-profile__rows { list-style: none; padding: 0; margin: 0; }
.entrance-profile__rows > li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 20px; padding: 14px 0; border-bottom: 1px solid var(--color-border-default); }
.entrance-profile small { display: block; color: var(--color-text-secondary); margin-top: 4px; }
.entrance-profile details { grid-column: 1 / -1; }
.entrance-profile summary { cursor: pointer; width: fit-content; }
@media (max-width: 650px) { .entrance-profile__rows > li { grid-template-columns: 1fr; } }
</style>
