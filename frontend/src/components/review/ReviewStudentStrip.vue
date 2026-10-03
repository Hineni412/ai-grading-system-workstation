<script setup lang="ts">
import type { ResultsCenterItem } from '../../api/results-center'
import { reviewStatus } from './review-status'

export interface StudentStripEntry {
  questionId: string
  item: ResultsCenterItem | null
}

const props = defineProps<{
  entries: StudentStripEntry[]
  selectedQuestionId: string | null
  switching: boolean
  studentName: string
}>()
const emit = defineEmits<{
  'question-nav': [direction: -1 | 1]
  'open-question': [questionId: string, reviewItemId: string]
}>()

function stripScoreText(score: number | null): string {
  return score === null
    ? '—'
    : Number.isInteger(score) ? String(score) : score.toFixed(1).replace(/\.0$/, '')
}

function stripStatusLabel(status: string): string {
  return reviewStatus[status as keyof typeof reviewStatus]?.label ?? status
}

// 与成绩明细热度图同一套刻度：已出分按得分率着色，未评分/失败用中性灰。
function stripChipStyle(item: ResultsCenterItem | null): Record<string, string> | undefined {
  if (!item) return undefined
  const scored = ['ai_ready', 'teacher_final'].includes(item.score_status)
    && item.score_awarded !== null && item.max_score > 0
  const color = reviewStatus[item.score_status].color
  if (!scored) return { '--review-status-color': color }
  const rate = Math.max(0, Math.min(1, item.score_awarded! / item.max_score))
  return { '--review-status-color': color, backgroundColor: `hsl(${Math.round(7 + rate * 126)} 52% 88%)`, color: '#172333' }
}

function questionNavEnabled(direction: -1 | 1): boolean {
  const entries = props.entries
  const index = entries.findIndex(
    (entry) => entry.questionId === props.selectedQuestionId,
  )
  if (index < 0) return false
  return entries.slice(direction < 0 ? 0 : index + 1, direction < 0 ? index : undefined)
    .some((entry) => entry.item !== null)
}
</script>

<template>
  <nav
    class="review-question-strip review-student-strip"
    data-testid="student-question-strip"
    :aria-label="`${studentName} 各题得分与切换`"
  >
    <span
      v-if="switching"
      class="review-student-strip__loading"
      role="status"
      aria-label="正在切换题目"
    />
    <button
      type="button"
      class="review-student-strip__nav"
      :disabled="!questionNavEnabled(-1)"
      aria-label="上一题"
      title="上一题（快捷键 ←）"
      @click="emit('question-nav', -1)"
    >
      ‹
    </button>
    <button
      v-for="entry in entries"
      :key="entry.questionId"
      type="button"
      class="review-student-strip__chip"
      :disabled="entry.item === null"
      :data-status="entry.item?.score_status"
      :style="stripChipStyle(entry.item)"
      :title="entry.item ? `${entry.questionId} · ${stripStatusLabel(entry.item.score_status)} · ${stripScoreText(entry.item.score_awarded)}/${stripScoreText(entry.item.max_score)} 分` : '无作答记录'"
      :aria-current="entry.questionId === selectedQuestionId ? 'true' : undefined"
      :aria-label="entry.item === null
        ? `${entry.questionId}，无作答记录`
        : `${entry.questionId}，得分 ${stripScoreText(entry.item.score_awarded)} 分`"
      @click="entry.item !== null && emit('open-question', entry.questionId, entry.item.review_item_id)"
    >
      <strong>{{ entry.questionId }}</strong>
      <span v-if="entry.item">
        {{ stripScoreText(entry.item.score_awarded) }} / {{ stripScoreText(entry.item.max_score) }}
        · {{ stripStatusLabel(entry.item.score_status) }}
      </span>
      <span v-else>无记录</span>
    </button>
    <button
      type="button"
      class="review-student-strip__nav"
      :disabled="!questionNavEnabled(1)"
      aria-label="下一题"
      title="下一题（快捷键 →）"
      @click="emit('question-nav', 1)"
    >
      ›
    </button>
  </nav>
</template>
