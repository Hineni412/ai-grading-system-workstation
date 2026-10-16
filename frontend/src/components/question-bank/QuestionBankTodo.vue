<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowRight, Check, ChevronDown, CircleHelp, Clock3, FileSearch, Layers3 } from '@lucide/vue'
import { questionBankApi, type QuestionBankListItem, type QuestionRepairKind, type QuestionRepairPart, type QuestionSkillIndex, type SkillCandidateSummary } from '../../api/question-bank'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { useQuestionBankStore } from '../../stores/question-bank'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import QuestionBankStandardSummary from './QuestionBankStandardSummary.vue'
import SkillCandidateRunDialog from './SkillCandidateRunDialog.vue'
import SkillCandidateReview from './SkillCandidateReview.vue'
import { repairNext, repairPartLabels, repairReason, repairStage, type RepairStage } from './repair-workflow'
import '../../styles/skill-candidates.css'
import '../../styles/question-bank-workflow.css'

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
const stage = ref<RepairStage>('matching')
const stageChosen = ref(false)
const selectedId = ref<number | null>(null)
const maintenanceRef = ref<HTMLDetailsElement | null>(null)
const stageDefinitions = [
  { key: 'analysis', label: '基础分析', description: '判定点、解题证据、基础标签与难度', help: '先形成可用的题目分析资料。已有合格内容保留，只补缺失或失效的部分。' },
  { key: 'matching', label: '题型与关联匹配', description: '已有题型、知识点、关键步骤', help: '依据分析结果匹配现有标准。部分关联可能在首次分析后补充，不代表每次都要重新分析整题。' },
  { key: 'organize', label: '题型／标准整理', description: '无候选、明确无匹配，或标准不可用', help: '先整理题型或标准，再继续匹配。保留具体原因，不把这类题一律转成人工逐题归类。' },
] as const
const allItems = computed(() => repairPreview.value?.items ?? [])
const stages = computed(() => stageDefinitions.map(entry => ({ ...entry, count: allItems.value.filter(item => repairStage(item) === entry.key).length })))
const reviewCount = computed(() => allItems.value.filter(item => repairStage(item) === 'review').length)
const organizeCount = computed(() => allItems.value.filter(item => repairStage(item) === 'organize').length)
const stageInfo = computed(() => stageDefinitions.find(entry => entry.key === stage.value)
  ?? { key: 'review', label: '需核对资料', description: '题源、判定点或教师确认的关联', help: '先核对具体阻断原因。修正题源或确认资料后，再检查是否需要补分析；不要求手工补全全部标签。' })
const stageItems = computed(() => allItems.value.filter(item => repairStage(item) === stage.value))
const filterDefinitions = [
  { key: 'all', label: '全部' },
  { key: 'types', label: '缺题型' },
  { key: 'knowledge_points', label: '缺知识点' },
  { key: 'skills', label: '缺关键步骤' },
] as const
const filter = ref<typeof filterDefinitions[number]['key']>('all')
const filters = computed(() => filterDefinitions.filter(entry => entry.key !== 'skills' || skillsRequired.value).map(entry => ({ ...entry, count: stageItems.value.filter(item => entry.key === 'all' || item.missing.includes(entry.key)).length })))
const items = computed(() => stageItems.value.filter(item => stage.value !== 'matching' || filter.value === 'all' || item.missing.includes(filter.value)))
const displayedCount = computed(() => Math.min(itemLimit.value, items.value.length))
const selectedItem = computed(() => items.value.find(item => item.id === selectedId.value) ?? items.value[0])
const shownParts = computed<QuestionRepairPart[]>(() => ['tags', 'evidence', 'criteria', 'knowledge_points', ...(skillsRequired.value ? ['skills' as const] : []), 'types'])
const organizeHint = computed(() => scope.selectedVolumeId === 'bnu24-math-g8-lower'
  ? '八下按章整理需要至少 30 道可用题、来自 3 份试卷；已有题型的章还需累计 30 道待归类题。整理在导入或分析时单独确认，本页 AI 补齐不会启动章节整理。'
  : '当前册尚未接入自动按章整理。保留待整理题目，后续安排题型或标准整理；不会自动发起整理任务。')
function selectStage(value: RepairStage) {
  stage.value = value; stageChosen.value = true; filter.value = 'all'; itemLimit.value = 20; selectedId.value = null
}
async function showMaintenance() {
  maintenanceLoaded.value = true
  if (maintenanceRef.value) maintenanceRef.value.open = true
  await nextTick()
  maintenanceRef.value?.scrollIntoView({ block: 'nearest' })
}
watch(repairPreview, preview => {
  if (!preview || stageChosen.value) return
  stage.value = stages.value.find(entry => entry.count)?.key ?? (reviewCount.value ? 'review' : 'analysis')
})
watch(skillsRequired, required => { if (!required && filter.value === 'skills') filter.value = 'all' })
watch(filter, () => { itemLimit.value = 20; selectedId.value = null })

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
  stage.value = stages.value.find(entry => entry.count)?.key ?? (reviewCount.value ? 'review' : 'analysis')
  stageChosen.value = false
  selectedId.value = null
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
  <section class="qb-todo qb-workflow">
    <StatePanel v-if="!scope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
    <template v-else>
      <header class="qb-workflow-heading">
        <div>
          <h2>题目处理流程<span v-if="repairPreview">待处理 {{ repairPreview.question_count }} 题</span></h2>
          <p>先完成基础分析，再检查题型与标准关联。未能匹配的题，需要先区分原因。</p>
        </div>
        <AppButton v-if="repairPreview?.repairable_count" variant="primary" @click="emit('repair', 'all')">查看全册补齐清单 · {{ repairPreview.repairable_count }} 题</AppButton>
      </header>
      <div class="qb-todo-list">
        <StatePanel v-if="!repairPreview && previewLoading" kind="loading" title="正在读取题目处理进度…" />
        <StatePanel v-else-if="!repairPreview && previewFailed" kind="error" title="待处理题目暂时无法读取" description="请重试，不会因此重新分析题目。" retry-label="重试" @retry="loadPreview" />
        <template v-if="repairPreview">
          <p v-if="previewFailed" class="qb-workflow-read-error" role="alert">最新清单读取失败，当前保留上次结果。<AppButton variant="ghost" size="small" @click="loadPreview">重试</AppButton></p>
          <nav class="qb-workflow-stages" aria-label="按题目处理阶段查看">
            <template v-for="(entry, position) in stages" :key="entry.key">
              <ArrowRight v-if="position" class="qb-workflow-arrow" aria-hidden="true" />
              <button type="button" class="qb-workflow-stage" :class="{ 'is-active': stage === entry.key, 'is-organize': entry.key === 'organize' }" :aria-pressed="stage === entry.key" @click="selectStage(entry.key)">
                <span class="qb-workflow-stage-number">{{ position + 1 }}</span>
                <span class="qb-workflow-stage-copy"><strong>{{ entry.label }}</strong><span>{{ entry.description }}</span></span>
                <span class="qb-workflow-stage-count"><b>{{ entry.count }}</b><span>题待处理</span></span>
              </button>
            </template>
          </nav>
          <div class="qb-workflow-context">
            <p><CircleHelp :size="15" aria-hidden="true" />各环节可能在同次分析中完成，也可能需要后续补齐。这里展示缺失环节，不代表 AI 请求次数。</p>
            <button type="button" class="qb-workflow-review" :class="{ 'is-active': stage === 'review' }" :aria-pressed="stage === 'review'" @click="selectStage('review')"><FileSearch :size="17" aria-hidden="true" />需核对资料 <strong>{{ reviewCount }} 题</strong><ArrowRight :size="15" aria-hidden="true" /></button>
          </div>
          <section class="qb-workflow-detail" aria-label="当前阶段待处理题目">
            <header class="qb-workflow-detail-heading"><div><h3>{{ stageInfo.label }}<span>{{ stageItems.length }} 题</span></h3><p>{{ stageInfo.help }}</p></div><AppButton v-if="stage === 'organize'" variant="secondary" size="small" @click="showMaintenance">查看标准与缺口</AppButton></header>
            <div v-if="stage === 'organize'" class="qb-workflow-organize-note"><Layers3 :size="18" aria-hidden="true" /><p>{{ organizeHint }}</p></div>
            <nav v-if="stage === 'matching' && stageItems.length" class="qb-workflow-filters" aria-label="按缺失关联筛选"><button v-for="entry in filters" :key="entry.key" type="button" class="qb-filter-chip" :class="{ 'is-active': filter === entry.key }" :aria-pressed="filter === entry.key" @click="filter = entry.key">{{ entry.label }} {{ entry.count }}</button><span>同题可缺多种关联，分类数量不相加。</span></nav>
            <StatePanel v-if="!items.length" kind="empty" :title="stageItems.length ? '当前筛选没有题目' : '当前没有题目停在这个环节'" :description="stage === 'organize' ? '缺少题型归属不等于已确认无匹配。有可核验的无匹配结论，或没有候选题型时，才保留为待整理。' : stage === 'review' ? '没有需要先核对资料的阻断项。判定点审核和标准维护仍可在下方查看。' : '可切换其他阶段。各题按当前最先需要处理的环节归类，不会重复计入阶段总数。'" />
            <div v-else class="qb-workflow-columns">
              <div class="qb-workflow-question-list" aria-label="选择题目查看原因">
                <button v-for="item in items.slice(0, itemLimit)" :key="item.id" type="button" class="qb-workflow-question" :class="{ 'is-selected': selectedItem?.id === item.id }" :aria-pressed="selectedItem?.id === item.id" @click="selectedId = item.id"><span><strong>第 {{ item.question_number }} 题</strong><small>{{ item.missing.length }} 项待处理</small></span><span class="qb-workflow-paper">{{ item.paper_title }}</span><span class="qb-workflow-question-next">{{ repairNext(item) }}</span></button>
                <div class="qb-workflow-list-footer"><span>显示 {{ displayedCount }} / {{ items.length }} 题</span><AppButton v-if="displayedCount < items.length" variant="ghost" size="small" @click="itemLimit += 20">查看更多</AppButton></div>
              </div>
              <section v-if="selectedItem" class="qb-workflow-inspector" aria-label="题目处理原因">
                <header><h4>第 {{ selectedItem.question_number }} 题</h4><AppButton variant="secondary" size="small" @click="emit('openQuestion', { questionId: selectedItem.id, paperId: null })">{{ stage === 'review' ? '打开题目核对' : '查看题目' }}<ArrowRight :size="15" aria-hidden="true" /></AppButton></header>
                <p class="qb-workflow-inspector-source">{{ selectedItem.paper_title }}</p>
                <dl class="qb-workflow-parts"><div v-for="part in shownParts" :key="part" :class="{ 'is-pending': selectedItem.missing.includes(part) }"><dt>{{ repairPartLabels[part] }}</dt><dd><Clock3 v-if="selectedItem.missing.includes(part)" :size="14" aria-hidden="true" /><Check v-else :size="14" aria-hidden="true" />{{ selectedItem.missing.includes(part) ? part === 'types' && selectedItem.type_pending_reason ? '待归类' : '待处理' : '已完成' }}</dd></div></dl>
                <div class="qb-workflow-reason"><h5>为什么停在这里</h5><p>{{ repairReason(selectedItem) }}</p></div>
                <div class="qb-workflow-next" :class="{ 'is-organize': stage === 'organize', 'is-review': stage === 'review' }"><h5>下一步</h5><strong>{{ repairNext(selectedItem) }}</strong><p v-if="!selectedItem.blocked_reason">从页面顶部查看全册补齐清单，确认本次题目与缺失部分后开始。题型明确无匹配时保留为待归类。</p><p v-else-if="stage === 'organize'">题目与候选标准未变化时，不反复补打；有把握时也可在题目详情中选择已有题型。</p><p v-else>先打开题目核对具体条件与已有资料，再决定是否补分析。</p></div>
              </section>
            </div>
          </section>
          <p class="qb-workflow-count-note">本册 {{ repairPreview.question_count }} 道待处理题各归入一个当前环节：可由 AI 补齐 {{ repairPreview.repairable_count }} 题，待整理 {{ organizeCount }} 题，需核对资料 {{ reviewCount }} 题。</p>
        </template>
        <details class="qb-todo-group qb-todo-fold">
          <summary><ChevronDown class="qb-caret" aria-hidden="true" /><strong>判定点待审核</strong><span>{{ review ? `${review.total} 题` : reviewState === 'error' ? '读取失败' : '正在统计…' }}</span></summary>
          <p>这些题需要人工确认判定点，可能同时出现在上方待处理清单中。</p>
          <StatePanel v-if="reviewState === 'error'" kind="error" title="人工审核清单读取失败，上方待处理清单仍可使用。" retry-label="重试" @retry="loadReview" />
          <p v-else-if="reviewState === 'loading' && !review" role="status">正在读取人工审核清单…</p>
          <template v-if="review">
            <button v-if="review.total" class="qb-link" @click="emit('criteria')">判定点批量审核</button>
            <p v-else>当前学期没有判定点待审核的题目。</p>
            <article v-for="question in review.items" :key="question.id"><strong>第{{ question.question_number }}题</strong><span>{{ question.paper_title }}</span><AppButton variant="secondary" @click="emit('question', question)">去处理 →</AppButton></article>
            <button v-if="review.items.length < review.total" class="qb-link" :disabled="moreReviewLoading" @click="moreReview">{{ moreReviewLoading ? '正在读取…' : '查看更多' }}</button>
          </template>
        </details>
        <details :key="scope.selectedVolumeId" ref="maintenanceRef" class="qb-todo-group qb-todo-fold" @toggle="openMaintenance">
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
