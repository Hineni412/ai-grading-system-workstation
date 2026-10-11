<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { questionBankApi, type QuestionBankListItem, type QuestionRepairKind, type QuestionRepairPart, type QuestionSkillIndex, type SkillCandidateSummary } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionBankStandardSummary from './QuestionBankStandardSummary.vue'
import SkillCandidateRunDialog from './SkillCandidateRunDialog.vue'
import SkillCandidateReview from './SkillCandidateReview.vue'
import '../../styles/skill-candidates.css'

const props = defineProps<{ index: QuestionSkillIndex | null; pendingCount: number }>()
const emit = defineEmits<{ question: [question: QuestionBankListItem]; openQuestion: [ref: { questionId: number; paperId: number | null }]; skill: [key: string]; criteria: []; taxonomy: []; repair: [kind: QuestionRepairKind] }>()
const scope = useCurriculumScopeStore()
const bank = useQuestionBankStore()
const summaryRef = ref<InstanceType<typeof QuestionBankStandardSummary> | null>(null)
const candidateSummary = ref<SkillCandidateSummary | null>(null)
const maintenanceLoaded = ref(false)
const runDialogOpen = ref(false)
const reviewOpen = ref(false)
const gapActive = computed(() => !!candidateSummary.value?.active_run)
const reviewDisabled = computed(() => !candidateSummary.value || (!candidateSummary.value.pending_suggestion_count && !candidateSummary.value.approved_unpublished_skill_count))
const repairPreview = computed(() => scope.selectedVolumeId ? bank.repairPreviews.get(`${scope.selectedVolumeId}:all`) ?? null : null)
const skillsRequired = computed(() => repairPreview.value?.skills_required === true)
const previewFailed = ref(false)
const previewLoading = ref(false)
const itemLimit = ref(20)
const analysisParts: QuestionRepairPart[] = ['tags', 'evidence', 'criteria']
const partLabels: Record<QuestionRepairPart, string> = { tags: '题目标签', evidence: '解题证据', criteria: '判定点', skills: '关键步骤', types: '题型', knowledge_points: '知识点关联' }
const filterDefinitions = [
  { key: 'all', label: '全部' },
  { key: 'analysis', label: '分析未完成' },
  { key: 'types', label: '缺题型' },
  { key: 'knowledge_points', label: '缺知识点' },
  { key: 'skills', label: '缺关键步骤' },
] as const
const filter = ref<typeof filterDefinitions[number]['key']>('all')
function matches(missing: QuestionRepairPart[], key: typeof filter.value): boolean {
  return key === 'all' || (key === 'analysis' ? missing.some(part => analysisParts.includes(part)) : missing.includes(key))
}
const filters = computed(() => filterDefinitions.filter(definition => definition.key !== 'skills' || skillsRequired.value).map(definition => ({ ...definition, count: repairPreview.value?.items.filter(item => matches(item.missing, definition.key)).length ?? null })))
const items = computed(() => (repairPreview.value?.items ?? []).filter(item => matches(item.missing, filter.value)))
const repairKind = computed<QuestionRepairKind>(() => filter.value === 'analysis' ? 'analysis' : filter.value === 'skills' ? 'skills' : 'all')
const repairLabel = computed(() => repairKind.value === 'analysis' ? 'AI 补齐分析' : repairKind.value === 'skills' ? 'AI 补挂关键步骤' : 'AI 补齐')
watch(skillsRequired, required => { if (!required && filter.value === 'skills') filter.value = 'all' })
watch(filter, () => { itemLimit.value = 20 })

const review = ref<Awaited<ReturnType<typeof questionBankApi.listQuestions>> | null>(null)
const reviewState = ref<'loading' | 'ready' | 'error'>('loading')
const moreReviewLoading = ref(false)
let reviewController: AbortController | null = null
let previewSequence = 0
async function loadPreview() {
  const volumeId = scope.selectedVolumeId
  if (!volumeId) return
  const sequence = ++previewSequence
  previewLoading.value = true
  previewFailed.value = false
  try { await bank.loadRepairPreview(volumeId, 'all') }
  catch { if (sequence === previewSequence) previewFailed.value = true }
  finally { if (sequence === previewSequence) previewLoading.value = false }
}
async function loadReview() {
  reviewController?.abort()
  const volumeId = scope.selectedVolumeId
  if (!volumeId) return
  const request = new AbortController()
  reviewController = request
  reviewState.value = 'loading'
  try {
    const result = await questionBankApi.listQuestions({ page: 1, pageSize: 20, curriculumVolumeIds: [volumeId], criteriaNeedsReview: true }, request.signal)
    if (!request.signal.aborted) { review.value = result; reviewState.value = 'ready' }
  } catch { if (!request.signal.aborted) reviewState.value = 'error' }
}
async function moreReview() {
  const current = review.value
  const volumeId = scope.selectedVolumeId
  const signal = reviewController?.signal
  if (!current || !volumeId || moreReviewLoading.value) return
  moreReviewLoading.value = true
  try {
    const page = await questionBankApi.listQuestions({ page: current.page + 1, pageSize: 20, curriculumVolumeIds: [volumeId], criteriaNeedsReview: true }, signal)
    if (!signal?.aborted && review.value === current) review.value = { ...page, items: [...current.items, ...page.items] }
  } catch { if (!signal?.aborted) reviewState.value = 'error' }
  finally { moreReviewLoading.value = false }
}
function afterCandidateChange() { void summaryRef.value?.refresh(); void loadPreview(); void loadReview() }
function openMaintenance(event: Event) {
  if (event.currentTarget instanceof HTMLDetailsElement && event.currentTarget.open) maintenanceLoaded.value = true
}
const underused = computed(() => [...new Map(props.index?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).filter(skill => skill.question_count < 5).map(skill => [skill.stable_key, skill])).values()])
const underusedLimit = ref(20)
watch(() => scope.selectedVolumeId, () => {
  previewSequence++
  reviewController?.abort()
  review.value = null
  previewFailed.value = false
  filter.value = 'all'
  itemLimit.value = 20
  maintenanceLoaded.value = false
  candidateSummary.value = null
  runDialogOpen.value = false
  reviewOpen.value = false
  void loadPreview()
  void loadReview()
}, { immediate: true })
watch(() => repairPreview.value?.fingerprint, (value, previous) => { if (previous && value && value !== previous) void loadReview() })
onBeforeUnmount(() => { previewSequence++; reviewController?.abort() })
</script>

<template>
  <section class="qb-todo">
    <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
    <template v-else>
      <header class="qb-todo-overview">
        <div>
          <p class="qb-todo-scope">{{ scope.selectedVolume?.label || '当前教学学期' }} · 题目维护</p>
          <h2>待补齐 <strong>{{ repairPreview ? repairPreview.question_count : '—' }}</strong> 题</h2>
          <p>每道题只计一次。同一道题可有多类缺失，下方分类数量不能相加。</p>
        </div>
        <AppButton v-if="repairPreview?.question_count" variant="primary" @click="emit('repair', 'all')">查看并补齐</AppButton>
      </header>
      <div class="qb-todo-list">
        <section class="qb-todo-group qb-todo-questions" aria-label="待补齐题目">
          <header><h2>待补齐题目</h2><span v-if="repairPreview" class="qb-todo-list-status">显示 {{ Math.min(itemLimit, items.length) }} / {{ items.length }} 题</span></header>
          <nav class="qb-todo-filters" aria-label="按缺失部分筛选">
            <button v-for="entry in filters" :key="entry.key" class="qb-filter-chip" :class="{ 'is-active': filter === entry.key }" :aria-pressed="filter === entry.key" @click="filter = entry.key">{{ entry.label }} <strong>{{ entry.count ?? '—' }}</strong> 题</button>
          </nav>
          <StatePanel v-if="!repairPreview && previewLoading" kind="loading" title="正在读取待补齐题目…" />
          <StatePanel v-else-if="!repairPreview && previewFailed" kind="error" title="待补齐题目暂时无法读取，请重试。" retry-label="重试" @retry="loadPreview" />
          <template v-else-if="repairPreview">
            <p v-if="previewFailed" class="qb-todo-read-error" role="alert">最新清单读取失败，当前保留上次结果。<button class="qb-link" @click="loadPreview">重试</button></p>
            <div v-if="filter !== 'all' && items.length" class="qb-todo-filter-action">
              <span>{{ filters.find(entry => entry.key === filter)?.label }} · {{ items.length }} 题</span>
              <button class="qb-link" @click="emit('repair', repairKind)">{{ repairLabel }}<template v-if="repairKind === 'all'"> · 查看全部缺失</template></button>
            </div>
            <p v-if="!items.length" class="qb-todo-empty">{{ filter === 'all' ? '当前学期没有需要补齐的题目。' : '当前学期没有这类待处理题目。' }}</p>
            <article v-for="item in items.slice(0, itemLimit)" :key="item.id" class="qb-todo-question">
              <strong>第{{ item.question_number }}题</strong>
              <div class="qb-todo-question-copy"><span>{{ item.paper_title }}</span><div class="qb-todo-missing"><span v-for="part in item.missing" :key="part">缺{{ partLabels[part] }}</span></div><small v-if="item.blocked_reason">{{ item.blocked_reason }}</small><small v-else-if="item.type_pending_reason">{{ item.type_pending_reason }}</small></div>
              <AppButton variant="secondary" @click="emit('openQuestion', { questionId: item.id, paperId: null })">去处理 →</AppButton>
            </article>
            <button v-if="itemLimit < items.length" class="qb-link" @click="itemLimit += 20">查看更多题目</button>
          </template>
        </section>
        <details class="qb-todo-group qb-todo-fold">
          <summary><ChevronDown class="qb-caret" aria-hidden="true" /><strong>判定点待审核</strong><span>{{ review ? `${review.total} 题` : reviewState === 'error' ? '读取失败' : '正在统计…' }}</span></summary>
          <p>这些题需要人工确认判定点，可能同时出现在待补齐清单中。</p>
          <StatePanel v-if="reviewState === 'error'" kind="error" title="人工审核清单读取失败，待补齐清单仍可使用。" retry-label="重试" @retry="loadReview" />
          <p v-else-if="reviewState === 'loading' && !review" role="status">正在读取人工审核清单…</p>
          <template v-if="review">
            <button v-if="review.total" class="qb-link" @click="emit('criteria')">判定点批量审核</button>
            <p v-else>当前学期没有判定点待审核的题目。</p>
            <article v-for="question in review.items" :key="question.id"><strong>第{{ question.question_number }}题</strong><span>{{ question.paper_title }}</span><AppButton variant="secondary" @click="emit('question', question)">去处理 →</AppButton></article>
            <button v-if="review.items.length < review.total" class="qb-link" :disabled="moreReviewLoading" @click="moreReview">{{ moreReviewLoading ? '正在读取…' : '查看更多' }}</button>
          </template>
        </details>
        <details :key="scope.selectedVolumeId" class="qb-todo-group qb-todo-fold" @toggle="openMaintenance">
          <summary><ChevronDown class="qb-caret" aria-hidden="true" /><strong>辅助维护</strong><span>{{ skillsRequired ? '关键步骤缺口、标准与新词' : '标准与新词' }}</span></summary>
          <template v-if="maintenanceLoaded">
            <template v-if="skillsRequired">
              <p>关键步骤缺口按涉及题数展示。一道题可有多个缺口判定点，此处题数不与待补齐题数相加。</p>
              <section class="qb-todo-maintenance">
                <header><h2>关键步骤缺口</h2><span v-if="candidateSummary" class="qb-todo-count">{{ candidateSummary.gap_question_count }} 题</span></header>
                <p v-if="candidateSummary">涉及 {{ candidateSummary.gap_question_count }} 题、{{ candidateSummary.counts.ready + candidateSummary.counts.pending_review }} 个判定点。AI 补挂后仍无法归入关键步骤，需要整理并审核。</p>
                <div class="qb-todo-maintenance-actions"><button v-if="candidateSummary" class="qb-link" :disabled="gapActive || !candidateSummary.counts.ready" @click="runDialogOpen = true">{{ gapActive ? '正在整理…' : '整理成关键步骤候选' }}</button><button v-if="candidateSummary" class="qb-link" :disabled="reviewDisabled" @click="reviewOpen = true">审核关键步骤候选 · {{ candidateSummary.pending_suggestion_count }} 项</button></div>
              </section>
            </template>
            <QuestionBankStandardSummary ref="summaryRef" :volume-id="scope.selectedVolumeId" :skills-required="skillsRequired" @candidate="candidateSummary = $event" />
            <section class="qb-todo-maintenance"><header><h2>新词例外</h2><span>{{ pendingCount }} 条</span><button class="qb-link" @click="emit('taxonomy')">去处理 →</button></header><p>分析中遇到的未收录表述，确认后归入知识点或细条目。</p></section>
            <details v-if="skillsRequired" class="qb-todo-maintenance"><summary>题量少的关键步骤 · {{ underused.length }} 项</summary><p>当前题量少于 5 道的关键步骤。</p><article v-for="skill in underused.slice(0, underusedLimit)" :key="skill.stable_key"><span>{{ skill.display_name }} · {{ skill.question_count }} 题</span><AppButton variant="secondary" @click="emit('skill', skill.stable_key)">查看题目 →</AppButton></article><button v-if="underusedLimit < underused.length" class="qb-link" @click="underusedLimit += 20">查看更多</button></details>
          </template>
        </details>
      </div>
      <p class="qb-todo-note">查看清单和人工审核不产生模型费用。AI 补齐会先列出题目和缺失部分，确认后开始。</p>
      <SkillCandidateRunDialog :open="runDialogOpen" :volume-id="scope.selectedVolumeId" :active-run-id="candidateSummary?.active_run?.run_id ?? null" @close="runDialogOpen = false" @changed="afterCandidateChange" @review="runDialogOpen = false; reviewOpen = true" />
      <SkillCandidateReview :open="reviewOpen" :volume-id="scope.selectedVolumeId" :index="index" @close="reviewOpen = false" @changed="afterCandidateChange" @open-question="emit('openQuestion', $event)" />
    </template>
  </section>
</template>
