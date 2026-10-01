<script setup lang="ts">
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'

import type { QuestionBankListItem, QuestionBankPaper } from '../api/question-bank'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import { useTaxonomyReviewStore } from '../stores/taxonomy-review'
import '../styles/question-bank.css'

const QuestionBankFilters = defineAsyncComponent(
  () => import('../components/question-bank/QuestionBankFilters.vue'),
)
const QuestionImportJobs = defineAsyncComponent(
  () => import('../components/question-bank/QuestionImportJobs.vue'),
)
const QuestionInspector = defineAsyncComponent(
  () => import('../components/question-bank/QuestionInspector.vue'),
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

const bank = useQuestionBankStore()
const jobStore = useJobStore()
const taxonomyReview = useTaxonomyReviewStore()
const activePaper = ref<QuestionBankPaper | null>(null)
const showImport = ref(false)
const showTaxonomyReview = ref(false)
const showCriteriaReview = ref(false)

const activeProgress = computed(() => {
  if (!activePaper.value?.question_count) return 0
  return Math.round(
    (activePaper.value.tagged_question_count / activePaper.value.question_count) * 100,
  )
})

onMounted(() => {
  // The library only needs the pending count. The full controlled catalog is
  // loaded on demand when the teacher opens the review workspace.
  // 任务恢复与试卷列表并行发起，不再固定等待，卡片状态能尽早显示。
  void taxonomyReview.loadSummary()
  void bank.loadPapers()
  void jobStore.initialize()
})

function openPaper(paper: QuestionBankPaper, questionId?: number): void {
  activePaper.value = paper
  bank.clearSelection()
  void bank.loadQuestions({
    page: 1,
    pageSize: 20,
    paperIds: [paper.id],
    tagStatus: 'all',
    sort: 'paper_order',
  })
  void bank.selectQuestion(questionId ?? null)
}

function closePaper(): void {
  activePaper.value = null
  bank.clearSelection()
  void bank.selectQuestion(null)
}

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

const currentPaper = computed(() => {
  const selected = activePaper.value
  if (!selected) return null
  return bank.papers.find((paper) => paper.id === selected.id) ?? selected
})
</script>

<template>
  <section class="question-bank">
    <PaperLibrary
      v-if="!activePaper"
      :pending-taxonomy-count="taxonomyReview.pendingCount"
      :pending-taxonomy-state="taxonomyReview.loadState"
      @open="openPaper"
      @import="showImport = true"
      @review-taxonomy="openTaxonomyReview"
      @review-criteria="openCriteriaReview"
    />

    <template v-else>
      <header class="question-bank__paper-header">
        <div class="question-bank__paper-copy">
          <button type="button" class="qb-back" @click="closePaper">
            <span aria-hidden="true">←</span>
            返回试卷库
          </button>
          <p class="qb-eyebrow">PAPER QUESTIONS</p>
          <h1>{{ activePaper.title || `未命名试卷 #${activePaper.id}` }}</h1>
          <p>
            {{
              [
                activePaper.year,
                activePaper.exam_type,
                activePaper.grade,
                activePaper.semester,
                activePaper.textbook_version,
              ].filter(Boolean).join(' · ') || '试卷信息待补充'
            }}
          </p>
        </div>
        <div class="question-bank__paper-progress">
          <div>
            <span>核心标签完整度</span>
            <strong>{{ activePaper.tagged_question_count }} / {{ activePaper.question_count }} 道</strong>
          </div>
          <div
            class="question-bank__progress-track"
            role="progressbar"
            aria-label="试卷核心标签完整度"
            :aria-valuenow="activeProgress"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span :style="{ width: `${activeProgress}%` }" />
          </div>
          <small>{{ activeProgress }}% 完整</small>
          <p
            v-if="currentPaper && currentPaper.criteria_needs_review_count > 0"
            class="question-bank__paper-review"
            role="status"
          >
            {{ currentPaper.criteria_needs_review_count }} 道题判定点待审核，打开题目后到「判定点」里处理。
          </p>
          <AppButton variant="primary" type="button" class="paper-button is-primary" @click="showImport = true">
            上传与 AI 标注
          </AppButton>
        </div>
      </header>

      <div v-if="bank.lastDeleted" class="qb-feedback is-warning qb-undo" role="status">
        <span>{{ bank.writeMessage }}</span>
        <button type="button" class="qb-link" @click="bank.restoreLastDeleted()">立即恢复</button>
      </div>

      <QuestionBankFilters :paper-id="activePaper.id" />
      <QuestionLedger paper-mode />
    </template>

    <QuestionInspector v-if="activePaper" />

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
