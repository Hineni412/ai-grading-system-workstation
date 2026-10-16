<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { ExamAssessment } from '../../api/question-bank'
defineProps<{ assessment?: ExamAssessment | null }>()
</script>
<template>
  <div v-if="assessment && assessment.status !== 'ordinary'" class="exam-reference-badge" :class="{ 'is-matched': assessment.status === 'matched' }">
    <details v-if="assessment.status === 'matched'">
      <summary>符合深圳参考 · 对应 {{ assessment.matched_paper_count }}/{{ assessment.reference_count }} 套</summary>
      <p v-for="match in assessment.matches" :key="`${match.paper_id}:${match.question_id}`"><RouterLink :to="{ path: '/question-bank', query: { tab: 'paper', paper: match.paper_id, question: match.question_id } }">{{ match.year ?? '年份待核对' }} · 第 {{ match.question_number }} 题</RouterLink>：{{ match.reason }}</p>
    </details>
    <span v-else>{{ assessment.reason }}<template v-if="['unmatched', 'pending', 'reference'].includes(assessment.status)"> · 可手动选入</template></span>
  </div>
</template>
<style scoped>
.exam-reference-badge { padding: 8px 12px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.exam-reference-badge summary { cursor: pointer; width: fit-content; }
.exam-reference-badge p { margin: 6px 0; }
</style>
