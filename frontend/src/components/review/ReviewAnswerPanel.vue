<script setup lang="ts">
import AppIconButton from '../design-system/AppIconButton.vue'
import AppButton from '../design-system/AppButton.vue'
import { computed, ref, watch } from 'vue'

import {
  classAnalysisApi,
  type ClassQuestionPreview,
} from '../../api/class-analysis'
import type {
  ReviewRubricPoint,
  ReviewRubricSection,
} from '../../api/review'
import {
  formatScore,
  subQuestionOf,
} from '../results-center/results-overview'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import { dedupeAcceptedAnswers } from '../../utils/answer-dedupe'
import { loadReviewRubric } from './review-rubric-cache'
import '../../styles/review-answer-panel.css'

const props = defineProps<{
  sessionId: number
  questionId: string
  embedded?: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const preview = ref<ClassQuestionPreview | null>(null)
const previewState = ref<'loading' | 'ready' | 'error'>('loading')
const previewCache = new Map<string, ClassQuestionPreview>()
let previewGeneration = 0

const rubric = ref<ReviewRubricSection | null>(null)
const rubricState = ref<'loading' | 'ready' | 'error'>('loading')
let rubricGeneration = 0

const subIndex = computed(() => subQuestionOf(props.questionId)?.index ?? null)

const rubricParts = computed(() => {
  const parts = new Map<string, { partId: string; label: string; reference: ReviewRubricPoint; points: ReviewRubricPoint[] }>()
  for (const point of rubric.value?.points ?? []) {
    const part = parts.get(point.part_id)
    if (part) part.points.push(point)
    else parts.set(point.part_id, { partId: point.part_id, label: point.part_label, reference: point, points: [point] })
  }
  return [...parts.values()]
})

function dedupedAnswers(point: ReviewRubricPoint): string[] {
  return dedupeAcceptedAnswers(point.accepted_answers, point.standard_answer)
}

function finalAnswerText(point: ReviewRubricPoint): string {
  const base = point.require_final_answer ? '必须明确写出' : '不要求单独写出'
  return point.final_answer_rule ? `${base}；${point.final_answer_rule}` : base
}

async function loadPreview(): Promise<void> {
  const generation = ++previewGeneration
  const cacheKey = `${props.sessionId}::${props.questionId}`
  const cached = previewCache.get(cacheKey)
  preview.value = cached ?? null
  previewState.value = cached ? 'ready' : 'loading'
  if (cached) return
  try {
    const loaded = await classAnalysisApi.getQuestionPreview(
      props.sessionId,
      props.questionId,
    )
    if (generation !== previewGeneration) return
    previewCache.set(cacheKey, loaded)
    preview.value = loaded
    previewState.value = 'ready'
  } catch {
    if (generation !== previewGeneration) return
    previewState.value = 'error'
  }
}

async function loadRubric(): Promise<void> {
  const generation = ++rubricGeneration
  rubric.value = null
  rubricState.value = 'loading'
  try {
    const loaded = await loadReviewRubric(props.sessionId, props.questionId)
    if (generation !== rubricGeneration) return
    rubric.value = loaded
    rubricState.value = 'ready'
  } catch {
    if (generation !== rubricGeneration) return
    rubricState.value = 'error'
  }
}

watch(
  () => [props.sessionId, props.questionId],
  () => {
    void loadPreview()
    void loadRubric()
  },
  { immediate: true },
)
</script>

<template>
  <aside
    class="review-answer-panel"
    :class="{ 'review-answer-panel--embedded': props.embedded === true }"
    data-testid="review-answer-panel"
    aria-label="题目与答案"
  >
    <header v-if="props.embedded !== true" class="review-answer-panel__header">
      <h3>题目与答案</h3><AppIconButton label="关闭题目与答案面板"
       
       
        @click="emit('close')"
       icon="close" />
    </header>

    <div class="review-answer-panel__scroll">
      <section class="review-answer-panel__section" aria-labelledby="review-answer-question-title">
        <h4 id="review-answer-question-title">原题</h4>
        <p v-if="subIndex !== null" class="review-answer-panel__muted">
          本题为第 {{ subIndex }} 小问
        </p>
        <div
          v-if="previewState === 'loading'"
          class="review-answer-panel__skeleton"
          role="status"
        >
          正在读取原题…
        </div>
        <p v-else-if="previewState === 'error'" class="review-answer-panel__warning" role="alert">
          原题暂时无法读取。
          <AppButton type="button" variant="ghost" size="small" @click="loadPreview">重试</AppButton>
        </p>
        <template v-else-if="preview">
          <p v-if="preview.notice" class="review-answer-panel__muted">{{ preview.notice }}</p>
          <QuestionContentRenderer
            :blocks="preview.rich_content.question_blocks"
            :fallback="preview.text"
            empty-label="此题尚无可预览的原题内容"
            image-alt="原题配图"
            media-mode="detail"
            typeset-text
          />
        </template>
      </section>

      <section class="review-answer-panel__section" aria-labelledby="review-answer-rubric-title">
        <h4 id="review-answer-rubric-title">参考答案与给分要点</h4>
        <p v-if="rubricState === 'loading'" class="review-answer-panel__muted">
          正在读取评分标准…
        </p>
        <p v-else-if="rubricState === 'error'" class="review-answer-panel__warning" role="alert">
          评分标准暂时无法读取。
          <AppButton type="button" variant="ghost" size="small" @click="loadRubric">重试</AppButton>
        </p>
        <p v-else-if="rubric && rubric.points.length === 0" class="review-answer-panel__muted">
          当前题没有更细的评分要点。
        </p>
        <p v-else-if="!rubric" class="review-answer-panel__muted">
          当前题没有可展示的评分标准。
        </p>
        <section v-for="part in rubricParts" :key="part.partId" class="review-answer-panel__part">
          <h5 v-if="rubricParts.length > 1">{{ part.label || part.partId }}</h5>
          <details class="review-answer-panel__reference">
            <summary>参考解答与小问规则</summary>
            <dl class="review-answer-panel__details">
              <template v-if="part.reference.standard_answer">
                <dt>{{ part.points.some(point => point.answer_kind === 'conditions') ? '参考解答与示例答案' : '参考解答' }}</dt>
                <dd><QuestionHtmlBlock :text="part.reference.standard_answer" :inline="false" typeset-text /></dd>
              </template>
              <template v-if="dedupedAnswers(part.reference).length">
                <dt>等价答案</dt>
                <dd><QuestionHtmlBlock :text="dedupedAnswers(part.reference).join('；')" typeset-text /></dd>
              </template>
              <template v-if="part.reference.answer_only_max_score !== null">
                <dt>仅写答案</dt>
                <dd>最高 {{ formatScore(part.reference.answer_only_max_score) }} 分</dd>
              </template>
              <template v-if="part.reference.require_final_answer !== null">
                <dt>最终答案要求</dt>
                <dd><QuestionHtmlBlock :text="finalAnswerText(part.reference)" typeset-text /></dd>
              </template>
            </dl>
          </details>
          <details
            v-for="point in part.points"
            :key="`${point.part_id}:${point.step_id}`"
            class="review-answer-panel__point"
          >
            <summary class="review-answer-panel__point-header">
              <span class="review-answer-panel__point-identity">{{ point.step_id }}</span>
              <QuestionHtmlBlock
                class="review-answer-panel__point-goal"
                :text="point.core_goal || point.part_label"
                inline
                typeset-text
              />
              <span class="review-answer-panel__point-score">{{ formatScore(point.score) }} 分</span>
            </summary>
            <p v-if="point.answer_kind === 'conditions'" class="review-answer-panel__answer-kind">
              按条件判对，参考答案只是示例。满足全部条件即可得分。
            </p>
            <dl class="review-answer-panel__details">
              <template v-if="point.required_elements.length">
                <dt>{{ point.answer_kind === 'conditions' ? '判对条件' : '得分条件' }}</dt>
                <dd><QuestionHtmlBlock :text="point.required_elements.join('；')" typeset-text /></dd>
              </template>
            </dl>
            <details v-if="point.deduction_rules.length" class="review-answer-panel__exceptions">
              <summary>扣分例外</summary>
              <QuestionHtmlBlock :text="point.deduction_rules.join('；')" typeset-text />
            </details>
          </details>
        </section>
      </section>
    </div>
  </aside>
</template>
