<script setup lang="ts">
import type { QuestionAnalysisItem } from '../../api/analysis'

defineProps<{
  items: QuestionAnalysisItem[]
  selectedQuestionId: string | null
}>()

defineEmits<{
  select: [questionId: string]
}>()

function scoreText(value: number | null): string {
  return value === null ? '无法计算' : `${value}%`
}

function scoreValue(value: number | null): string {
  return value === null ? '—' : String(value)
}
</script>

<template>
  <div class="workbench-table-wrap">
    <table class="analysis-table">
      <caption class="sr-only">题目得分、样本量和失分人数</caption>
      <thead>
        <tr>
          <th scope="col">题目与得分率</th>
          <th scope="col">平均得分</th>
          <th scope="col">满分</th>
          <th scope="col">失分人数</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in items"
          :key="`${item.class_name}:${item.question_id}`"
          :aria-current="item.question_id === selectedQuestionId ? 'true' : undefined"
        >
          <td>
            <button
              type="button"
              class="question-rate"
              :aria-pressed="item.question_id === selectedQuestionId"
              @click="$emit('select', item.question_id)"
            >
              <span class="question-rate__label">{{ item.question_id }}</span>
              <span class="question-rate__track" aria-hidden="true">
                <span
                  v-if="item.score_rate !== null"
                  class="question-rate__fill"
                  :style="{ inlineSize: `${Math.min(100, Math.max(0, item.score_rate))}%` }"
                />
              </span>
              <span>{{ scoreText(item.score_rate) }}</span>
              <span>{{ item.attempt_count }} 份</span>
            </button>
          </td>
          <td>{{ scoreValue(item.average_score) }}</td>
          <td>{{ scoreValue(item.max_score) }}</td>
          <td>{{ item.deduction_count }} 人</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
