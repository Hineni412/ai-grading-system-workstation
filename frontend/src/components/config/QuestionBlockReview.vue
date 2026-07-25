<script setup lang="ts">
import { reactive, watch } from 'vue'

import {
  QUESTION_TYPES,
  type ConfigQuestionPreview,
  type ConfigSource,
  type QuestionDecision,
  type QuestionType,
} from '../../api/config-workspace'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const props = withDefaults(defineProps<{
  source: ConfigSource
  decisions?: QuestionDecision[]
}>(), {
  decisions: () => [],
})

const emit = defineEmits<{
  'update:decisions': [decisions: QuestionDecision[]]
}>()

const selectedTypes = reactive<Record<string, QuestionType>>({})
const exclusions = reactive<Record<string, boolean>>({})
const expanded = reactive<Record<string, boolean>>({})
const emptyRichContent: NonNullable<ConfigQuestionPreview['rich_content']> = {
  available: false,
  question_block_count: 0,
  answer_block_count: 0,
  question_blocks: [],
  answer_blocks: [],
}

function richContent(question: ConfigQuestionPreview): NonNullable<ConfigQuestionPreview['rich_content']> {
  return question.rich_content ?? emptyRichContent
}

function knownType(value: string): value is QuestionType {
  return (QUESTION_TYPES as readonly string[]).includes(value)
}

function defaultType(question: ConfigQuestionPreview): QuestionType {
  return knownType(question.question_type) ? question.question_type : 'comprehensive'
}

function resetReview(): void {
  for (const key of Object.keys(selectedTypes)) delete selectedTypes[key]
  for (const key of Object.keys(exclusions)) delete exclusions[key]
  for (const key of Object.keys(expanded)) delete expanded[key]
  const knownIds = new Set(props.source.questions.map((question) => question.question_id))
  for (const question of props.source.questions) {
    selectedTypes[question.question_id] = defaultType(question)
    exclusions[question.question_id] = false
  }
  for (const decision of props.decisions) {
    if (!knownIds.has(decision.question_id) || !knownType(decision.question_type)) continue
    selectedTypes[decision.question_id] = decision.question_type
    exclusions[decision.question_id] = decision.excluded
  }
}

function currentDecisions(): QuestionDecision[] {
  return props.source.questions.flatMap((question) => {
    const questionType = selectedTypes[question.question_id] ?? defaultType(question)
    const excluded = exclusions[question.question_id] ?? false
    if (knownType(question.question_type)
      && questionType === question.question_type && !excluded) return []
    return [{ question_id: question.question_id, question_type: questionType, excluded }]
  })
}

function updateType(question: ConfigQuestionPreview, event: Event): void {
  const value = (event.currentTarget as HTMLSelectElement).value
  if (!knownType(value)) return
  selectedTypes[question.question_id] = value
  emit('update:decisions', currentDecisions())
}

function updateExcluded(question: ConfigQuestionPreview, event: Event): void {
  exclusions[question.question_id] = (event.currentTarget as HTMLInputElement).checked
  emit('update:decisions', currentDecisions())
}

function toggle(questionId: string): void {
  expanded[questionId] = !expanded[questionId]
}

function isLong(question: ConfigQuestionPreview): boolean {
  return question.question_preview.length > 180 || question.answer_preview.length > 180
    || richContent(question).question_block_count > 3
    || richContent(question).answer_block_count > 2
    || question.has_question_asset || question.has_answer_asset
}

function answerStatus(question: ConfigQuestionPreview): string {
  if (question.local_answer_trusted) return '答案已匹配'
  if (question.answer_present) return '识别到答案片段，需核对'
  return '未识别到答案'
}

function assetUrl(questionId: string, assetKind: 'question' | 'answer'): string {
  return `/api/sessions/${props.source.session_id}/config/sources/${encodeURIComponent(props.source.source_id)}`
    + `/questions/${encodeURIComponent(questionId)}/assets/${assetKind}`
}

function supplementalAssetUrls(
  question: ConfigQuestionPreview,
  assetKind: 'question' | 'answer',
): string[] {
  const blocks = assetKind === 'question'
    ? richContent(question).question_blocks
    : richContent(question).answer_blocks
  if (blocks.some((block) => block.asset_urls.length > 0)) return []
  const hasAsset = assetKind === 'question'
    ? question.has_question_asset
    : question.has_answer_asset
  return hasAsset ? [assetUrl(question.question_id, assetKind)] : []
}

watch(() => props.source.source_revision, (_revision, previous) => {
  resetReview()
  const normalized = currentDecisions()
  if (previous !== undefined || normalized.length > 0) emit('update:decisions', normalized)
}, { immediate: true })
</script>

<template>
  <section class="question-review" aria-labelledby="question-review-title">
    <header class="config-section-heading">
      <div>
        <h2 id="question-review-title">核对拆题结果</h2>
        <p>只修正题型或排除错误拆出的题目；题号由来源保持不变。</p>
      </div>
      <span v-if="source.questions.length" class="question-review__count">
        共 {{ source.questions.length }} 题
      </span>
    </header>

    <p v-if="source.questions.length === 0" class="question-review__empty" role="status">
      当前来源没有可核对的题目，请更换文件后重试。
    </p>
    <ol v-else class="question-review__list">
      <li
        v-for="question in source.questions"
        :key="question.question_id"
        class="question-review__row"
      >
        <div class="question-review__identity">
          <strong class="question-review__id">{{ question.question_id }}</strong>
          <span v-if="question.needs_review" class="question-review__warning">需要核对</span>
        </div>

        <label class="question-review__type">
          <span>题型</span>
          <select
            :aria-label="`${question.question_id} 题型`"
            :value="selectedTypes[question.question_id]"
            @change="updateType(question, $event)"
          >
            <option value="choice">选择题</option>
            <option value="fill_blank">填空题</option>
            <option value="calculation">计算题</option>
            <option value="proof">证明题</option>
            <option value="comprehensive">综合题</option>
          </select>
        </label>

        <div class="question-review__content">
          <div
            :class="['question-review__preview', {
              'question-review__preview--clamped': isLong(question) && !expanded[question.question_id],
            }]"
            :data-question-content="question.question_id"
          >
            <QuestionContentRenderer
              :blocks="richContent(question).question_blocks"
              :fallback="question.question_preview"
              empty-label="题目文字未提供预览。"
              :image-alt="`${question.question_id} 题目图`"
              :supplemental-image-urls="supplementalAssetUrls(question, 'question')"
              compact
            />
          </div>
          <div class="question-review__answer-fact">
            <strong :class="{ 'is-untrusted': question.answer_present && !question.local_answer_trusted }">
              {{ answerStatus(question) }}
            </strong>
            <QuestionContentRenderer
              v-if="question.answer_present || richContent(question).answer_blocks.length || question.has_answer_asset"
              class="question-review__answer-content"
              :blocks="richContent(question).answer_blocks"
              :fallback="question.answer_preview"
              empty-label="答案内容暂未识别。"
              :image-alt="`${question.question_id} 答案图`"
              :supplemental-image-urls="supplementalAssetUrls(question, 'answer')"
              compact
            />
          </div>
        </div>

        <div class="question-review__actions">
          <button
            v-if="isLong(question)"
            type="button"
            :aria-label="`${expanded[question.question_id] ? '收起' : '展开'} ${question.question_id} 题目`"
            :aria-expanded="expanded[question.question_id] ? 'true' : 'false'"
            @click="toggle(question.question_id)"
          >
            {{ expanded[question.question_id] ? '收起题目' : '展开题目' }}
          </button>
          <label class="question-review__exclude">
            <input
              type="checkbox"
              :aria-label="`排除 ${question.question_id}`"
              :checked="exclusions[question.question_id]"
              @change="updateExcluded(question, $event)"
            >
            排除此题
          </label>
        </div>
      </li>
    </ol>
  </section>
</template>
