<script setup lang="ts">
import PageHeader from '../components/design-system/PageHeader.vue'
import { DialogRoot, DialogPortal, DialogContent, DialogTitle } from 'reka-ui'
import { useRoute, useRouter } from 'vue-router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useAssemblyStore } from '../stores/assembly'
import QuestionBasketDrawer from '../components/question-bank/QuestionBasketDrawer.vue'
import QuestionBankTodo from '../components/question-bank/QuestionBankTodo.vue'
import QuestionLedger from '../components/question-bank/QuestionLedger.vue'
import QuestionSkillBrowser from '../components/question-bank/QuestionSkillBrowser.vue'
import QuestionRepairDialog from '../components/question-bank/QuestionRepairDialog.vue'

import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { questionBankApi, type QuestionBankListItem, type QuestionBankPaper, type QuestionSkillIndex, type QuestionRepairKind } from '../api/question-bank'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import { useTaxonomyReviewStore } from '../stores/taxonomy-review'
import '../styles/question-bank.css'

const QuestionImportJobs = defineAsyncComponent(
  () => import('../components/question-bank/QuestionImportJobs.vue'),
)
const TaxonomyCandidateReview = defineAsyncComponent(
  () => import('../components/question-bank/TaxonomyCandidateReview.vue'),
)
const CriteriaReviewPanel = defineAsyncComponent(
  () => import('../components/question-bank/CriteriaReviewPanel.vue'),
)

const route = useRoute()
const router = useRouter()
const scope = useCurriculumScopeStore()
const assembly = useAssemblyStore()
const bank = useQuestionBankStore()
const index = ref<QuestionSkillIndex | null>(null)
const indexLoading = ref(false)
const indexError = ref('')
let indexController: AbortController | null = null
const tab = computed(() => route.query.tab === 'paper' || route.query.tab === 'todo' ? route.query.tab : 'skill')
const library = ref<InstanceType<typeof PaperLibrary> | null>(null)
const repairOpen = ref(false)
const repairKind = ref<QuestionRepairKind>('all')
function openRepair(kind: QuestionRepairKind = 'all') { repairKind.value = kind; repairOpen.value = true }
watch(() => scope.selectedVolumeId, () => { repairOpen.value = false })
async function loadIndex() {
  indexController?.abort()
  if (!scope.selectedVolumeId) { index.value = null; return }
  const controller = new AbortController()
  indexController = controller
  indexLoading.value = true
  indexError.value = ''
  try { const value = await questionBankApi.skillIndex(scope.selectedVolumeId, controller.signal); if (!controller.signal.aborted) index.value = value }
  catch { if (!controller.signal.aborted) indexError.value = '技能与题目统计暂时无法读取，请重试。' }
  finally { if (!controller.signal.aborted) indexLoading.value = false }
}
watch(() => scope.selectedVolumeId, (value, previous) => { index.value = null; if (previous && value !== previous) void bank.selectQuestion(null); void loadIndex() }, { immediate: true })
watch(() => bank.papers, () => void loadIndex())
watch(() => bank.writeState, (state, previous) => { if (previous === 'saving' && state === 'idle') void loadIndex() })
onBeforeUnmount(() => indexController?.abort())
function setTab(next: 'skill' | 'paper' | 'todo') { void bank.selectQuestion(null); void router.push({ query: { ...route.query, tab: next, question: undefined } }) }
function openSkill(key: string) {
  const chapter = index.value?.chapters.find(chapter => chapter.cross_section_skills.some(skill => skill.stable_key === key) || chapter.sections.some(section => section.skills.some(skill => skill.stable_key === key)))
  const section = chapter?.sections.find(section => section.skills.some(skill => skill.stable_key === key))
  void bank.selectQuestion(null)
  void router.push({ query: { tab: 'skill', section: section?.id || chapter?.id, skill: key } })
}
watch(() => route.query.question, value => {
  const id = Number(value)
  if (Number.isSafeInteger(id) && id > 0 && bank.selectedQuestionId !== id) void bank.selectQuestion(id)
  else if (!value && bank.selectedQuestionId) void bank.selectQuestion(null)
}, { immediate: true })
watch(() => bank.selectedQuestionId, id => {
  if (Number(route.query.question) !== id) void router.replace({ query: { ...route.query, question: id ? String(id) : undefined } })
})
watch([() => bank.selectedQuestionId, () => bank.listState, tab], async ([id, state]) => {
  if (!id || state !== 'ready') return
  await nextTick()
  const card = document.getElementById(`qb-question-${id}`)
  const list = card?.closest('.qb-question-list')
  if (!card || !list) return
  const bounds = card.getBoundingClientRect(), viewport = list.getBoundingClientRect()
  if (bounds.top < viewport.top || bounds.top >= viewport.bottom) card.scrollIntoView({ block: 'start' })
  if (!card.contains(document.activeElement)) card.querySelector<HTMLElement>('.qb-inline-card__title')?.focus({ preventScroll: true })
})
const skillCount = computed(() => new Set(index.value?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).map(skill => skill.stable_key)).size)

const jobStore = useJobStore()
const taxonomyReview = useTaxonomyReviewStore()
const activePaper = ref<QuestionBankPaper | null>(null)
const showImport = ref(false)
const showBasket = ref(false)
const showTaxonomyReview = ref(false)
const showCriteriaReview = ref(false)
const reviewReturnFocus = ref<HTMLElement | null>(null)
function captureReviewFocus() {
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null
  reviewReturnFocus.value = active?.closest('details')?.querySelector<HTMLElement>('summary') ?? active
}

onMounted(() => {
  // The library only needs the pending count. The full controlled catalog is
  // loaded on demand when the teacher opens the review workspace.
  // 任务恢复与试卷列表并行发起，不再固定等待，卡片状态能尽早显示。
  void taxonomyReview.loadSummary()
  void bank.loadPapers()
  void scope.initialize()
  void jobStore.initialize()
  if (assembly.loadState === 'idle') void assembly.load()
})

function openPaper(paper: QuestionBankPaper, questionId?: number): void {
  void router.push({ query: { tab: 'paper', paper: String(paper.id), question: questionId ? String(questionId) : undefined } })
}
const paperIncomplete = ref(new Set<number>())
let paperController: AbortController | null = null
onBeforeUnmount(() => paperController?.abort())
watch([() => route.query.paper, tab, () => bank.papers], () => {
  paperController?.abort()
  if (tab.value !== 'paper') return
  const paper = bank.papers.find(paper => paper.id === Number(route.query.paper))
  activePaper.value = paper ?? null
  if (!paper) return
  const controller = new AbortController()
  paperController = controller
  bank.clearSelection()
  if (bank.appliedFilters.paperIds?.length !== 1 || bank.appliedFilters.paperIds[0] !== paper.id || bank.appliedFilters.sort !== 'paper_order') {
    bank.questions = []; bank.total = 0; bank.totalPages = 1
  }
  paperIncomplete.value = new Set()
  void bank.loadQuestions({ page: 1, pageSize: 100, paperIds: [paper.id], tagStatus: 'all', sort: 'paper_order', includeSkills: true }, async (filters, signal) => {
    const first = await questionBankApi.listQuestions(filters, signal)
    const items = [...first.items]
    for (let page = 2; page <= first.total_pages; page++) items.push(...(await questionBankApi.listQuestions({ ...filters, page }, signal)).items)
    await nextTick()
    return { ...first, items, total_pages: 1 }
  })
  void (async () => {
    try {
      const first = await questionBankApi.listQuestionRefs({ paperIds: [paper.id], analysisStatus: 'incomplete', pageSize: 500 }, controller.signal)
      const ids = first.items.map(item => item.id)
      for (let page = 2; page <= first.total_pages; page++) ids.push(...(await questionBankApi.listQuestionRefs({ paperIds: [paper.id], analysisStatus: 'incomplete', pageSize: 500, page }, controller.signal)).items.map(item => item.id))
      if (!controller.signal.aborted) paperIncomplete.value = new Set(ids)
    } catch { /* The question list remains usable when status hints are unavailable. */ }
  })()
}, { immediate: true })
async function navigateQuestion(id: number) { await bank.selectQuestion(id); await nextTick(); document.getElementById(`qb-question-${id}`)?.scrollIntoView({ block: 'start' }) }
async function addPaper() { if (assembly.loadState === 'idle') await assembly.load(); await assembly.addQuestions([...new Set(bank.questions.map(question => question.id))]) }
function questionState(question: QuestionBankListItem) { return paperIncomplete.value.has(question.id) ? '分析未完成' : question.criteria_needs_review ? '判定点待审核' : question.skills?.length === 0 ? '未挂技能' : '完整' }
function openTaxonomyReview(): void {
  captureReviewFocus()
  showImport.value = false
  showCriteriaReview.value = false
  showTaxonomyReview.value = true
  void taxonomyReview.load()
}

function openCriteriaReview(): void {
  captureReviewFocus()
  showImport.value = false
  showTaxonomyReview.value = false
  showCriteriaReview.value = true
}

function openCriteriaQuestion(question: QuestionBankListItem): void {
  const paper = bank.papers.find((item) => item.id === question.paper_id)
  if (!paper || question.paper_id == null) return
  showCriteriaReview.value = false
  openPaper(paper, question.id)
}

</script>

<template>
  <section class="question-bank">
    <PageHeader title="题库管理"><template #meta>{{ scope.selectedVolume?.label || '请选择教学学期' }} · {{ index?.question_count ?? 0 }} 题 · {{ skillCount }} 项技能</template><template #navigation><nav class="page-tabs" aria-label="题库视图"><button v-for="item in [{ key: 'skill', label: '按技能' }, { key: 'paper', label: '按试卷' }, { key: 'todo', label: '待处理' }]" :key="item.key" type="button" :class="{ 'is-active': tab === item.key }" :aria-current="tab === item.key ? 'page' : undefined" @click="setTab(item.key as 'skill' | 'paper' | 'todo')">{{ item.label }}</button></nav></template><template #actions><button class="qb-button" @click="showBasket = true">试卷篮 · {{ assembly.selectedQuestionCount }}</button><button class="qb-button" :disabled="!scope.selectedVolumeId" @click="openRepair()">AI 补齐缺失</button><div id="qb-library-actions" /></template></PageHeader>
    <div class="qb-workspace">
    <div v-if="bank.lastDeleted" class="qb-feedback is-warning qb-undo" role="status"><span>{{ bank.writeMessage }}</span><button type="button" class="qb-link" @click="bank.restoreLastDeleted()">立即恢复</button></div>
    <QuestionSkillBrowser v-if="tab === 'skill'" :index="index" :loading="indexLoading" :error="indexError" @retry="loadIndex" @skill="openSkill" @repair="openRepair('skills')" />
    <div v-show="tab === 'paper'" class="qb-paper-layout">
      <aside class="qb-paper-sidebar qb-browse-pane"><PaperLibrary ref="library" :active-paper-id="activePaper?.id" :header-target="'#qb-library-actions'" :pending-taxonomy-count="taxonomyReview.pendingCount" :pending-taxonomy-state="taxonomyReview.loadState" @open="openPaper" @import="showImport = true" @review-taxonomy="openTaxonomyReview" @review-criteria="openCriteriaReview" /></aside>
      <main class="qb-paper-detail qb-browse-pane"><template v-if="activePaper"><header class="qb-paper-heading"><div><h2>{{ activePaper.title }}</h2><p>{{ [activePaper.year, activePaper.grade, activePaper.semester, activePaper.exam_type].filter(Boolean).join(' · ') }} · {{ activePaper.question_count }} 题</p></div><button class="qb-button" :disabled="bank.listState !== 'ready' || assembly.saveState === 'saving'" @click="addPaper">整卷加入试卷篮</button><details class="qb-card-menu"><summary aria-label="试卷更多操作">⋯</summary><div><button @click="bank.selectCurrentPage(true)">选择整卷题目</button><button @click="library?.editPaper(activePaper)">编辑信息</button><button @click="library?.continuePaper(activePaper)">继续分析</button><button @click="library?.retagPaper(activePaper)">重新打标签</button><button @click="library?.answerPaper(activePaper)">生成答案草稿</button></div></details></header><nav class="qb-number-nav" aria-label="试卷题号"><button v-for="question in bank.questions" :key="`${question.id}:${question.question_number}`" :title="`第 ${question.question_number} 题 · ${questionState(question)}`" :aria-label="`第 ${question.question_number} 题 · ${questionState(question)}`" :class="{ 'is-active': bank.selectedQuestionId === question.id, 'is-warning': questionState(question) === '判定点待审核', 'is-unlinked': questionState(question) === '未挂技能', 'is-incomplete': questionState(question) === '分析未完成' }" @click="navigateQuestion(question.id)">{{ question.question_number }} <small>{{ questionState(question) === '完整' ? '✓' : questionState(question) === '判定点待审核' ? '!' : questionState(question) === '未挂技能' ? '◇' : '…' }}</small></button></nav><div class="qb-number-legend"><span>□ 完整</span><span>▣ 待审</span><span>◇ 未挂技能</span><span>▧ 未完成</span></div><p v-if="assembly.message" class="qb-feedback" role="status">{{ assembly.message }}</p><QuestionLedger paper-mode @skill="openSkill" /></template><p v-else class="qb-help">在左侧选择一份试卷，即可逐题浏览整卷。</p></main>
    </div>
    <QuestionBankTodo v-if="tab === 'todo'" :index="index" :pending-count="taxonomyReview.pendingCount" @question="openCriteriaQuestion" @skill="openSkill" @criteria="openCriteriaReview" @taxonomy="openTaxonomyReview" @repair="openRepair" />
    </div>
    <QuestionBasketDrawer v-model:open="showBasket" />
    <QuestionRepairDialog :open="repairOpen" :volume-id="scope.selectedVolumeId || ''" :volume-label="scope.selectedVolume?.label || ''" :kind="repairKind" @close="repairOpen = false" @refreshed="bank.loadPapers(); loadIndex()" />
    <DialogRoot :open="showImport" @update:open="showImport = $event"><DialogPortal>
      <div v-if="showImport" class="qb-modal-layer" role="presentation" @click.self="showImport = false">
        <DialogContent class="qb-import-dialog" :aria-describedby="undefined"><DialogTitle class="sr-only">上传试卷与任务</DialogTitle>
          <button type="button" class="qb-drawer-close" aria-label="关闭上传窗口" @click="showImport = false">×</button>
          <QuestionImportJobs
            :pending-taxonomy-count="taxonomyReview.pendingCount"
            :pending-taxonomy-state="taxonomyReview.loadState"
            @review-taxonomy="openTaxonomyReview"
          />
        </DialogContent>
      </div>
    </DialogPortal></DialogRoot>

    <TaxonomyCandidateReview
      v-if="showTaxonomyReview"
      :open="showTaxonomyReview"
      :return-focus="reviewReturnFocus"
      @close="showTaxonomyReview = false"
    />
    <CriteriaReviewPanel
      v-if="showCriteriaReview"
      :open="showCriteriaReview"
      :return-focus="reviewReturnFocus"
      @close="showCriteriaReview = false"
      @open-question="openCriteriaQuestion"
    />
  </section>
</template>
