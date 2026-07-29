<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import type { QuestionBankPaper } from '../api/question-bank'
import PaperLibrary from '../components/question-bank/PaperLibrary.vue'
import QuestionBankFilters from '../components/question-bank/QuestionBankFilters.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import QuestionInspector from '../components/question-bank/QuestionInspector.vue'
import QuestionLedger from '../components/question-bank/QuestionLedger.vue'
import TaxonomyCandidateReview from '../components/question-bank/TaxonomyCandidateReview.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'
import { useTaxonomyReviewStore } from '../stores/taxonomy-review'

const bank = useQuestionBankStore()
const jobStore = useJobStore()
const taxonomyReview = useTaxonomyReviewStore()
const activePaper = ref<QuestionBankPaper | null>(null)
const showImport = ref(false)
const showTaxonomyReview = ref(false)

const activeProgress = computed(() => {
  if (!activePaper.value?.question_count) return 0
  return Math.round(
    (activePaper.value.tagged_question_count / activePaper.value.question_count) * 100,
  )
})

onMounted(() => {
  void Promise.all([
    bank.loadPapers(),
    jobStore.initialize(),
    taxonomyReview.load(),
  ])
})

function openPaper(paper: QuestionBankPaper): void {
  activePaper.value = paper
  bank.clearSelection()
  void bank.selectQuestion(null)
  void bank.loadQuestions({
    page: 1,
    pageSize: 20,
    paperIds: [paper.id],
    tagStatus: 'all',
    sort: 'difficulty_desc',
  })
}

function closePaper(): void {
  activePaper.value = null
  bank.clearSelection()
  void bank.selectQuestion(null)
}

function openTaxonomyReview(): void {
  showImport.value = false
  showTaxonomyReview.value = true
}
</script>

<template>
  <section class="question-bank">
    <PaperLibrary
      v-if="!activePaper"
      :pending-taxonomy-count="taxonomyReview.pendingCount"
      @open="openPaper"
      @import="showImport = true"
      @review-taxonomy="openTaxonomyReview"
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
          <button type="button" class="paper-button is-primary" @click="showImport = true">
            上传与 AI 标注
          </button>
        </div>
      </header>

      <div v-if="bank.lastDeleted" class="qb-feedback is-warning qb-undo" role="status">
        <span>{{ bank.writeMessage }}</span>
        <button type="button" class="qb-link" @click="bank.restoreLastDeleted()">立即恢复</button>
      </div>

      <QuestionBankFilters :paper-id="activePaper.id" />
      <QuestionLedger paper-mode />
    </template>

    <QuestionInspector />

    <Teleport to="body">
      <div v-if="showImport" class="qb-modal-layer" role="presentation" @click.self="showImport = false">
        <div class="qb-import-dialog" role="dialog" aria-modal="true" aria-label="上传试卷与任务">
          <button type="button" class="qb-drawer-close" aria-label="关闭上传窗口" @click="showImport = false">×</button>
          <QuestionImportJobs
            :pending-taxonomy-count="taxonomyReview.pendingCount"
            @review-taxonomy="openTaxonomyReview"
          />
        </div>
      </div>
    </Teleport>

    <TaxonomyCandidateReview
      :open="showTaxonomyReview"
      @close="showTaxonomyReview = false"
    />
  </section>
</template>
