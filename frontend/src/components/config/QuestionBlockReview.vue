<script setup lang="ts">
import { reactive, watch } from 'vue'

import {
  QUESTION_TYPES,
  type ConfigQuestionPreview,
  type ConfigSource,
  type QuestionDecision,
  type QuestionType,
} from '../../api/config-workspace'

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
    if (questionType === defaultType(question) && !excluded) return []
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
}

function assetUrl(questionId: string, assetKind: 'question' | 'answer'): string {
  return `/api/sessions/${props.source.session_id}/config/sources/${encodeURIComponent(props.source.source_id)}`
    + `/questions/${encodeURIComponent(questionId)}/assets/${assetKind}`
}

watch(() => props.source.source_revision, (_revision, previous) => {
  resetReview()
  if (previous !== undefined) emit('update:decisions', [])
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
          <p
            :class="['question-review__preview', {
              'question-review__preview--clamped': isLong(question) && !expanded[question.question_id],
            }]"
            :data-question-content="question.question_id"
          >
            {{ question.question_preview || '题目文字未提供预览。' }}
          </p>
          <div class="question-review__answer-fact">
            <strong>{{ question.answer_present ? '已识别答案' : '未识别答案' }}</strong>
            <span v-if="question.answer_present && question.answer_preview">{{ question.answer_preview }}</span>
          </div>
          <div v-if="question.has_question_asset || question.has_answer_asset" class="question-review__assets">
            <img
              v-if="question.has_question_asset"
              :src="assetUrl(question.question_id, 'question')"
              :alt="`${question.question_id} 题目图`"
            >
            <img
              v-if="question.has_answer_asset"
              :src="assetUrl(question.question_id, 'answer')"
              :alt="`${question.question_id} 答案图`"
            >
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
