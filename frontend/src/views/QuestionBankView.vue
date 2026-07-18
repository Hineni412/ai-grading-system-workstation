<script setup lang="ts">
import { computed, onMounted } from 'vue'

import QuestionBankFilters from '../components/question-bank/QuestionBankFilters.vue'
import QuestionImportJobs from '../components/question-bank/QuestionImportJobs.vue'
import QuestionInspector from '../components/question-bank/QuestionInspector.vue'
import QuestionLedger from '../components/question-bank/QuestionLedger.vue'
import { useJobStore } from '../stores/jobs'
import { useQuestionBankStore } from '../stores/question-bank'

const bank = useQuestionBankStore()
const jobStore = useJobStore()
const taggedCount = computed(() => bank.papers.reduce(
  (sum, paper) => sum + paper.tagged_question_count,
  0,
))

onMounted(() => {
  void Promise.all([
    bank.loadPapers(),
    bank.loadQuestions({ page: 1, pageSize: 20, tagStatus: 'all', sort: 'newest' }),
    jobStore.initialize(),
  ])
})
</script>

<template>
  <section class="question-bank">
    <header class="question-bank__hero">
      <div>
        <p class="qb-eyebrow">Question ledger</p>
        <h1 tabindex="-1">题库管理</h1>
        <p>按试卷和标签找到题目，核对完整证据，再安全确认标签、导入或批量处理。</p>
      </div>
      <div class="question-bank__summary" aria-label="题库概况">
        <span><strong>{{ bank.total }}</strong> 道题</span>
        <span><strong>{{ taggedCount }}</strong> 道已标注</span>
        <i aria-hidden="true" />
      </div>
    </header>

    <div v-if="bank.lastDeleted" class="qb-feedback is-warning qb-undo" role="status">
      <span>{{ bank.writeMessage }}</span>
      <button type="button" class="qb-link" @click="bank.restoreLastDeleted()">立即恢复</button>
    </div>

    <QuestionImportJobs />
    <QuestionBankFilters />

    <div class="question-bank__workspace">
      <QuestionLedger />
      <QuestionInspector />
    </div>
  </section>
</template>
