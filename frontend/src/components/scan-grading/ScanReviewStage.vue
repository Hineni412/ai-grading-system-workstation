<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import type { ResultsCenterQuestion } from '../../api/results-center'
import type { ReviewQuestionSummary } from '../../api/review'
import AppButton from '../design-system/AppButton.vue'
import { useResultsCenterStore } from '../../stores/results-center'
import type { InterventionState, InterventionSummary, ScanStageId } from './scan-stage'

const props = defineProps<{
  sessionId: number
  questions: ReviewQuestionSummary[]
  state: InterventionState
  summary: InterventionSummary
}>()
const emit = defineEmits<{ 'select-stage': [id: ScanStageId] }>()

const router = useRouter()
const resultsStore = useResultsCenterStore()

const interventionPending = computed(() => (
  props.summary.ungraded + props.summary.failed + props.summary.review
))

function questionPending(question: ReviewQuestionSummary): number {
  return (question.ungraded_count ?? 0) + (question.failed_count ?? 0) + question.needs_review_count
}
const firstPendingQuestionId = computed(() => (
  props.questions.find((question) => questionPending(question) > 0)?.question_id ?? null
))

const OBJECTIVE_QUESTION_TYPES = new Set(['choice', 'single_choice', 'multi_choice', 'fill_blank'])
const resultsQuestions = computed(() => new Map<string, ResultsCenterQuestion>(
  (resultsStore.results?.questions ?? []).map((question) => [question.question_id, question]),
))
const resultsSummary = computed(() => resultsStore.results?.summary ?? null)
const resultsLoading = computed(() => !resultsStore.results
  && (resultsStore.state === 'idle' || resultsStore.state === 'loading'))
const resultsFailed = computed(() => !resultsStore.results
  && (resultsStore.state === 'error' || resultsStore.state === 'stale-error'))

interface ReviewTile {
  question: ReviewQuestionSummary
  pending: number
  confirmed: number
  rate: number | null
  average: number | null
}
function reviewTile(question: ReviewQuestionSummary): ReviewTile {
  const results = resultsQuestions.value.get(question.question_id)
  const rate = results?.average_score != null && question.max_score > 0
    ? Math.min(100, Math.round((results.average_score / question.max_score) * 100))
    : null
  return {
    question,
    pending: questionPending(question),
    confirmed: question.teacher_confirmed_count ?? 0,
    rate,
    average: results?.average_score ?? null,
  }
}
const reviewRateGroups = computed(() => [
  {
    label: '客观题',
    tiles: props.questions
      .filter((question) => OBJECTIVE_QUESTION_TYPES.has(question.question_type ?? ''))
      .map(reviewTile),
  },
  {
    label: '解答题',
    tiles: props.questions
      .filter((question) => !OBJECTIVE_QUESTION_TYPES.has(question.question_type ?? ''))
      .map(reviewTile),
  },
].filter((group) => group.tiles.length))
function tileStatus(tile: ReviewTile): string {
  if (tile.pending > 0) return `待处理 ${tile.pending}`
  return tile.confirmed > 0 ? `确认 ${tile.confirmed}` : '未确认'
}
function tileAriaLabel(tile: ReviewTile): string {
  const rateText = tile.rate === null ? '得分率 —' : `得分率 ${tile.rate}%`
  const statusText = tile.pending > 0
    ? `待处理 ${tile.pending} 项`
    : `教师已确认 ${tile.confirmed} 项`
  return `${tile.question.question_id}，${rateText}，${statusText}，进入复核`
}
function tileTone(tile: ReviewTile): string {
  if (tile.rate === null) return ''
  if (tile.rate >= 80) return 'is-high'
  if (tile.rate < 50) return 'is-low'
  return ''
}
const reviewStatusSegments = computed(() => [
  { label: '教师已确认', count: props.summary.teacher, color: 'var(--color-accent)' },
  { label: 'AI 已完成', count: props.summary.aiReady, color: 'var(--chart-2)' },
  { label: 'AI 待复核', count: props.summary.review, color: 'var(--color-warning)' },
  { label: '处理失败', count: props.summary.failed, color: 'var(--color-danger)' },
  { label: '未评分', count: props.summary.ungraded, color: 'var(--color-border-strong)' },
].filter((segment) => segment.count > 0)
  .map((segment) => ({
    ...segment,
    percent: props.summary.total > 0
      ? (segment.count / props.summary.total) * 100
      : 0,
  })))

const scoreDistribution = computed(() => {
  const bins = Array.from({ length: 10 }, () => 0)
  let complete = 0
  for (const student of resultsStore.results?.students ?? []) {
    if (student.status !== 'complete') continue
    complete += 1
    const ratio = student.max_score > 0 ? student.current_score / student.max_score : 0
    const index = Math.min(9, Math.max(0, Math.floor(ratio * 10)))
    bins[index] = (bins[index] ?? 0) + 1
  }
  return {
    bins,
    incomplete: (resultsStore.results?.students.length ?? 0) - complete,
    max: Math.max(0, ...bins),
  }
})
const scoreDistributionLabel = computed(() => (
  `成绩分布：${scoreDistribution.value.bins
    .map((count, index) => `${index * 10}–${index * 10 + 10}% ${count} 人`)
    .join('，')}`
))
function formatScore(value: number | null): string {
  return value === null ? '—' : String(Math.round(value * 10) / 10)
}
function retryResults(): void {
  if (props.sessionId > 0) void resultsStore.load(props.sessionId)
}

function openPendingReview(): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(props.sessionId),
      scope: 'teacher_pending',
      entry: 'intervention',
      ...(firstPendingQuestionId.value ? { question: firstPendingQuestionId.value } : {}),
    },
  })
}
function openResults(): void {
  void router.push({
    path: '/results',
    query: { session: String(props.sessionId) },
  })
}
function openQuestionReview(questionId: string): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(props.sessionId),
      scope: 'all',
      entry: 'intervention',
      question: questionId,
    },
  })
}
function selectStage(id: ScanStageId): void {
  emit('select-stage', id)
}
</script>

<template>
  <section class="scan-stage" aria-labelledby="review-title">
    <div class="scan-stage__heading">
      <h2 id="review-title">复核</h2>
    </div>
    <p v-if="state === 'loading'" class="scan-empty">正在读取人工干预摘要…</p>
    <p v-else-if="state === 'error'" class="scan-empty">摘要暂时无法读取，仍可进入工作台查看完整队列。</p>
    <template v-else-if="state === 'ready' && summary.total > 0">
      <div class="review-status" data-review-status>
        <div class="review-status__main">
          <strong
            class="review-status__headline"
            :class="{ 'is-pending': interventionPending > 0 }"
          >{{ interventionPending > 0 ? `还有 ${interventionPending} 项需要老师处理` : 'AI 批改结果已全部就绪' }}</strong>
          <p class="review-status__sub">
            <template v-if="interventionPending > 0">
              <span><i class="review-dot is-ungraded" aria-hidden="true" />未评分 {{ summary.ungraded }}</span>
              <span><i class="review-dot is-failed" aria-hidden="true" />处理失败 {{ summary.failed }}</span>
              <span><i class="review-dot is-review" aria-hidden="true" />AI 待复核 {{ summary.review }}</span>
            </template>
            <template v-else>
              <span><i class="review-dot is-teacher" aria-hidden="true" />教师已确认 {{ summary.teacher }} 项</span>
              <span><i class="review-dot is-ai" aria-hidden="true" />AI 已完成 {{ summary.aiReady }} 项</span>
              <span>共 {{ summary.total }} 项评分</span>
            </template>
          </p>
          <div class="review-status__bar" role="img" :aria-label="reviewStatusSegments.map((segment) => `${segment.label} ${segment.count} 项`).join('，')">
            <i
              v-for="segment in reviewStatusSegments" :key="segment.label"
              :style="{ width: `${segment.percent}%`, background: segment.color }"
              :title="`${segment.label} ${segment.count} 项`"
            />
          </div>
        </div>
        <AppButton
          v-if="interventionPending > 0" variant="primary" data-pending-review
          @click="openPendingReview"
        >处理待复核 {{ interventionPending }} 项</AppButton>
      </div>
      <div class="review-main">
        <section class="review-rates" aria-label="各题得分率">
          <div class="review-rates__head">
            <h3>各题得分率</h3>
            <small>点击题目进入复核</small>
          </div>
          <div v-for="group in reviewRateGroups" :key="group.label" class="review-rate-group">
            <h4>{{ group.label }}</h4>
            <div class="review-rate-grid">
              <button
                v-for="tile in group.tiles" :key="tile.question.question_id"
                type="button"
                class="review-rate-tile fx-lift"
                :class="[tileTone(tile), { 'has-pending': tile.pending > 0 }]"
                :data-question-tile="tile.question.question_id"
                :aria-label="tileAriaLabel(tile)"
                @click="openQuestionReview(tile.question.question_id)"
              >
                <span class="review-rate-tile__top">
                  <strong :title="tile.question.question_id">{{ tile.question.question_id }}</strong>
                  <small v-if="tile.pending > 0" class="review-rate-tile__badge">待 {{ tile.pending }}</small>
                </span>
                <span class="review-rate-tile__rate">
                  <i v-if="resultsLoading" class="review-skeleton" aria-hidden="true" />
                  <template v-else>
                    <strong>{{ tile.rate === null ? '—' : `${tile.rate}%` }}</strong>
                    <small
                      v-if="tile.rate !== null"
                      :title="`均分 ${formatScore(tile.average)}/${tile.question.max_score}`"
                    >{{ formatScore(tile.average) }}/{{ tile.question.max_score }}</small>
                  </template>
                </span>
                <small
                  class="review-rate-tile__meta"
                  :class="{ 'is-pending': tile.pending > 0 }"
                >{{ tileStatus(tile) }}</small>
                <span class="review-rate-tile__bar" aria-hidden="true">
                  <i :style="{ width: `${tile.rate ?? 0}%` }" />
                </span>
              </button>
            </div>
          </div>
        </section>
        <aside class="review-distribution" aria-label="成绩分布">
          <div class="review-rates__head">
            <h3>成绩分布</h3>
          </div>
          <template v-if="resultsLoading">
            <i class="review-skeleton review-skeleton--line" aria-hidden="true" />
            <div class="review-distribution__chart is-skeleton" aria-hidden="true">
              <i v-for="index in 10" :key="index" class="review-skeleton" />
            </div>
          </template>
          <template v-else-if="resultsFailed">
            <p class="review-distribution__note">成绩暂时无法读取</p>
            <AppButton variant="ghost" @click="retryResults">重试</AppButton>
          </template>
          <template v-else-if="resultsSummary">
            <p class="review-distribution__summary">
              {{ resultsSummary.student_count }} 人 · 均分 {{ formatScore(resultsSummary.average_score) }} · 最高 {{ formatScore(resultsSummary.highest_score) }} · 最低 {{ formatScore(resultsSummary.lowest_score) }}
            </p>
            <div class="review-distribution__chart" role="img" :aria-label="scoreDistributionLabel">
              <i
                v-for="(count, index) in scoreDistribution.bins" :key="index"
                :data-score-bin="index"
                :style="{ height: scoreDistribution.max > 0 ? `${(count / scoreDistribution.max) * 100}%` : '0%' }"
                :title="`${index * 10}–${index * 10 + 10}%：${count} 人`"
              ><b>{{ count }}</b></i>
            </div>
            <ol class="review-distribution__scale" aria-hidden="true">
              <li v-for="index in 10" :key="index">{{ (index - 1) * 10 }}</li>
            </ol>
            <p v-if="scoreDistribution.incomplete > 0" class="review-distribution__note">
              另有 {{ scoreDistribution.incomplete }} 人评分未完成，未计入
            </p>
            <AppButton variant="ghost" @click="openResults">在成绩中心查看明细</AppButton>
          </template>
        </aside>
      </div>
    </template>
    <div v-else-if="state === 'ready'" class="scan-empty">
      <p>还没有评分结果，批改后这里显示各题得分与待处理项。</p>
      <AppButton variant="secondary" @click="selectStage('grade')">去批改</AppButton>
    </div>
    <p v-else class="scan-empty">完成扫描预检后，这里会建立本场考试的人工干预队列。</p>
  </section>
</template>
