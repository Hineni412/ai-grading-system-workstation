<script setup lang="ts">
import PageHeader from '../components/design-system/PageHeader.vue'
import { useRoute, useRouter } from 'vue-router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useAssemblyStore } from '../stores/assembly'
import QuestionSkillBrowser from '../components/question-bank/QuestionSkillBrowser.vue'

import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { questionBankApi, type QuestionBankListItem, type QuestionBankPaper, type QuestionSkillIndex } from '../api/question-bank'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import { useTaxonomyReviewStore } from '../stores/taxonomy-review'
import '../styles/question-bank.css'

const QuestionImportJobs = defineAsyncComponent(
  () => import('../components/question-bank/QuestionImportJobs.vue'),
)
const QuestionLedger = defineAsyncComponent(
  () => import('../components/question-bank/QuestionLedger.vue'),
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
watch(() => scope.selectedVolumeId, () => { index.value = null; void bank.selectQuestion(null); void loadIndex() }, { immediate: true })
watch(() => bank.papers, () => void loadIndex())
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
const skillCount = computed(() => new Set(index.value?.chapters.flatMap(chapter => [...chapter.cross_section_skills, ...chapter.sections.flatMap(section => section.skills)]).map(skill => skill.stable_key)).size)

const jobStore = useJobStore()
const taxonomyReview = useTaxonomyReviewStore()
const activePaper = ref<QuestionBankPaper | null>(null)
const showImport = ref(false)
const showTaxonomyReview = ref(false)
const showCriteriaReview = ref(false)

onMounted(() => {
  // The library only needs the pending count. The full controlled catalog is
  // loaded on demand when the teacher opens the review workspace.
  // 任务恢复与试卷列表并行发起，不再固定等待，卡片状态能尽早显示。
  void taxonomyReview.loadSummary()
  void bank.loadPapers()
  void scope.initialize()
  void jobStore.initialize()
})

function openPaper(paper: QuestionBankPaper, questionId?: number): void {
  void router.push({ query: { tab: 'paper', paper: String(paper.id), question: questionId ? String(questionId) : undefined } })
}
watch([() => route.query.paper, tab, () => bank.papers], () => {
  if (tab.value !== 'paper') return
  const paper = bank.papers.find(paper => paper.id === Number(route.query.paper))
  activePaper.value = paper ?? null
  if (!paper) return
  bank.clearSelection()
  void bank.loadQuestions({ page: 1, pageSize: 100, paperIds: [paper.id], tagStatus: 'all', sort: 'paper_order', includeSkills: true })
}, { immediate: true })
function openTaxonomyReview(): void {
  showImport.value = false
  showCriteriaReview.value = false
  showTaxonomyReview.value = true
  void taxonomyReview.load()
}

function openCriteriaReview(): void {
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
    <PageHeader title="题库管理"><template #meta>{{ scope.selectedVolume?.label || '请选择教学学期' }} · {{ index?.question_count ?? 0 }} 题 · {{ skillCount }} 项技能</template><template #actions><RouterLink class="qb-button" :to="{ path: '/question-assembly', query: { mode: 'edit' } }">试卷篮 · {{ assembly.selectedQuestionCount }}</RouterLink><div id="qb-library-actions" /></template></PageHeader>
    <nav class="page-tabs" aria-label="题库视图"><button v-for="item in [{ key: 'skill', label: '按技能' }, { key: 'paper', label: '按试卷' }, { key: 'todo', label: '待处理' }]" :key="item.key" type="button" :class="{ 'is-active': tab === item.key }" :aria-current="tab === item.key ? 'page' : undefined" @click="setTab(item.key as 'skill' | 'paper' | 'todo')">{{ item.label }}</button></nav>
    <div v-if="bank.lastDeleted" class="qb-feedback is-warning qb-undo" role="status"><span>{{ bank.writeMessage }}</span><button type="button" class="qb-link" @click="bank.restoreLastDeleted()">立即恢复</button></div>
    <QuestionSkillBrowser v-if="tab === 'skill'" :index="index" :loading="indexLoading" :error="indexError" @retry="loadIndex" @skill="openSkill" />
    <div v-show="tab === 'paper'">
      <PaperLibrary ref="library" :header-target="'#qb-library-actions'" :pending-taxonomy-count="taxonomyReview.pendingCount" :pending-taxonomy-state="taxonomyReview.loadState" @open="openPaper" @import="showImport = true" @review-taxonomy="openTaxonomyReview" @review-criteria="openCriteriaReview" />
      <div v-if="activePaper"><h2>{{ activePaper.title }}</h2><QuestionLedger paper-mode @skill="openSkill" /></div>
    </div>
    <div v-if="tab === 'todo'" class="qb-todo"><h2>待处理</h2><button class="qb-link" @click="openCriteriaReview">判定点批量审核</button><button class="qb-link" @click="openTaxonomyReview">新词例外 · {{ taxonomyReview.pendingCount }}</button></div>
    <Teleport to="body">
      <div v-if="showImport" class="qb-modal-layer" role="presentation" @click.self="showImport = false">
        <div class="qb-import-dialog" role="dialog" aria-modal="true" aria-label="上传试卷与任务">
          <button type="button" class="qb-drawer-close" aria-label="关闭上传窗口" @click="showImport = false">×</button>
          <QuestionImportJobs
            :pending-taxonomy-count="taxonomyReview.pendingCount"
            :pending-taxonomy-state="taxonomyReview.loadState"
            @review-taxonomy="openTaxonomyReview"
          />
        </div>
      </div>
    </Teleport>

    <TaxonomyCandidateReview
      v-if="showTaxonomyReview"
      :open="showTaxonomyReview"
      @close="showTaxonomyReview = false"
    />
    <CriteriaReviewPanel
      v-if="showCriteriaReview"
      :open="showCriteriaReview"
      @close="showCriteriaReview = false"
      @open-question="openCriteriaQuestion"
    />
  </section>
</template>
