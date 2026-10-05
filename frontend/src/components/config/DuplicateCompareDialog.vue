<script setup lang="ts">
import AppIconButton from '../design-system/AppIconButton.vue'
import { computed, ref, watch } from 'vue'
import {
  DialogClose,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'

import type {
  ConfigQuestionPreview,
  ConfigSourceAsset,
  ConfigSourceDuplicateItem,
  QuestionDecision,
} from '../../api/config-workspace'
import { questionBankApi, type QuestionBankDetail } from '../../api/question-bank'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import {
  configSourceDuplicateTag,
  duplicateDecisionRecorded,
  type ConfigQuestionFlag,
} from './config-question-flags'

const props = defineProps<{
  /** null = dialog closed. */
  item: ConfigSourceDuplicateItem | null
  question: ConfigQuestionPreview | null
  /** Local question blocks rendered the same way as the continuous preview. */
  questionBlocks: NonNullable<ConfigQuestionPreview['rich_content']>['question_blocks']
  questionAssets: ConfigSourceAsset[]
  /** Answer-side assets are still question evidence (e.g. a figure the
      splitter attached to the answer region); shown alongside question assets. */
  answerAssets: ConfigSourceAsset[]
  answerBlocks: NonNullable<ConfigQuestionPreview['rich_content']>['answer_blocks']
  decision?: QuestionDecision
  index: number
  total: number
}>()

const emit = defineEmits<{
  close: []
  prev: []
  next: []
  decide: [decision: QuestionDecision]
  reset: []
}>()

const kindTag = computed<ConfigQuestionFlag | null>(() => (
  props.item ? configSourceDuplicateTag(props.item, props.decision) : null
))

const heading = computed(() => {
  const item = props.item
  if (!item) return ''
  return `《${item.matched_paper_title}》${item.matched_question_number ? `第${item.matched_question_number}题` : ''}`
})

const decided = computed(() => duplicateDecisionRecorded(props.decision))
const decidedLabel = computed(() => (decided.value ? kindTag.value?.label ?? '' : ''))

const bankDetail = ref<QuestionBankDetail | null>(null)
const bankState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')
const answerOpen = ref(false)
let bankRequest = 0

watch(() => props.item?.matched_question_id, async (questionId) => {
  const request = ++bankRequest
  bankDetail.value = null
  answerOpen.value = false
  if (!questionId) {
    bankState.value = 'idle'
    return
  }
  bankState.value = 'loading'
  try {
    const detail = await questionBankApi.getQuestion(questionId)
    if (request !== bankRequest) return
    bankDetail.value = detail
    bankState.value = 'ready'
  } catch {
    if (request !== bankRequest) return
    bankState.value = 'failed'
  }
}, { immediate: true })

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowLeft' && props.index > 0) emit('prev')
  else if (event.key === 'ArrowRight' && props.index < props.total - 1) emit('next')
}

function decideBankMatch(bankMatch: 'same' | 'different' | 'reanalyze'): void {
  const question = props.question
  const item = props.item
  if (!question || !item) return
  emit('decide', {
    question_id: question.question_id,
    excluded: props.decision?.excluded ?? false,
    bank_match: bankMatch,
    bank_question_id: bankMatch === 'same' ? item.matched_question_id : null,
  })
}

function decideBankAnswer(): void {
  const question = props.question
  const item = props.item
  if (!question || !item || !item.suggested_answer_override) return
  emit('decide', {
    question_id: question.question_id,
    excluded: props.decision?.excluded ?? false,
    answer_confirmed: true,
    answer_override: item.suggested_answer_override,
  })
}
</script>

<template>
  <DialogRoot :open="item !== null" @update:open="!$event && emit('close')">
    <DialogPortal>
      <DialogOverlay class="dup-compare__overlay" />
      <DialogContent
        class="dup-compare"
        data-testid="dup-compare-dialog"
        :aria-describedby="undefined"
        @keydown="onKeydown"
      >
        <template v-if="item && question">
          <header class="dup-compare__head">
            <span
              v-if="kindTag"
              class="question-review__flag"
              :class="`question-review__flag--${kindTag.tone ?? 'warning'}`"
            >{{ kindTag.label }}</span>
            <DialogTitle class="dup-compare__title">{{ heading }}</DialogTitle>
            <span class="dup-compare__reason" :title="item.reason">{{ item.reason }}</span>
            <span class="dup-compare__counter">{{ index + 1 }}/{{ total }}</span><DialogClose as-child><AppIconButton label="关闭对照" icon="close" /></DialogClose>
          </header>

          <div class="dup-compare__panes">
            <section class="dup-compare__pane">
              <header><strong>本卷 · {{ question.question_id }}</strong></header>
              <QuestionContentRenderer
                :blocks="questionBlocks"
                :fallback="question.question_preview"
                empty-label="题目文字未提供预览。"
                media-mode="review"
                paper-media-flow
              />
              <div
                v-if="questionAssets.length || answerAssets.length"
                class="dup-compare__assets"
              >
                <img
                  v-for="(asset, indexOf) in questionAssets"
                  :key="asset.asset_id"
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 题目图 ${indexOf + 1}`"
                >
                <img
                  v-for="(asset, indexOf) in answerAssets"
                  :key="asset.asset_id"
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 答案图 ${indexOf + 1}`"
                >
              </div>
              <details
                v-if="answerBlocks.length"
                class="dup-compare__bank-answer"
              >
                <summary>本卷答案</summary>
                <QuestionContentRenderer
                  :blocks="answerBlocks"
                  :fallback="question.answer_preview"
                  empty-label="答案内容暂未识别。"
                  media-mode="review"
                />
              </details>
            </section>

            <section class="dup-compare__pane">
              <header><strong>题库 · #{{ item.matched_question_id }}</strong></header>
              <p v-if="bankState === 'loading'" class="dup-compare__hint">正在读取题库题目…</p>
              <p v-else-if="bankState === 'failed'" class="dup-compare__hint" role="alert">
                题库题目暂时读取失败，可稍后重试。
              </p>
              <template v-else-if="bankDetail">
                <QuestionContentRenderer
                  :blocks="bankDetail.rich_content.question_blocks"
                  :fallback="bankDetail.question_text"
                  empty-label="题库题目暂无题干。"
                  image-alt="题目配图"
                  media-mode="detail"
                />
                <details
                  class="dup-compare__bank-answer"
                  :open="answerOpen"
                  @toggle="answerOpen = ($event.target as HTMLDetailsElement).open"
                >
                  <summary>题库答案</summary>
                  <QuestionContentRenderer
                    :blocks="bankDetail.rich_content.answer_blocks"
                    :fallback="bankDetail.answer_text"
                    empty-label="暂未录入答案或解析"
                    image-alt="答案配图"
                    media-mode="detail"
                  />
                </details>
              </template>
            </section>
          </div>

          <div
            v-if="item.kind === 'answer_conflict'"
            class="dup-compare__answers"
          >
            <div>
              <strong>本卷答案</strong>
              <p>{{ question.answer_preview || '未识别' }}</p>
            </div>
            <div>
              <strong>题库答案</strong>
              <p>{{ item.bank_answer_text || '未提供' }}</p>
            </div>
          </div>

          <footer class="dup-compare__foot">
            <div class="dup-compare__nav">
              <button
                type="button"
                class="dup-compare__nav-button"
                :disabled="index <= 0"
                aria-label="上一题"
                @click="emit('prev')"
              >← 上一题</button>
              <button
                type="button"
                class="dup-compare__nav-button"
                :disabled="index >= total - 1"
                aria-label="下一题"
                @click="emit('next')"
              >下一题 →</button>
            </div>

            <div class="dup-compare__actions">
              <template v-if="decided">
                <span class="dup-compare__decided" data-testid="dup-compare-decided">
                  {{ decidedLabel }}
                </span>
                <button
                  type="button"
                  class="dup-compare__undo"
                  @click="emit('reset')"
                >撤销</button>
              </template>
              <template v-else>
                <template v-if="item.kind === 'exact_needs_analysis'">
                  <button
                    type="button"
                    class="dup-compare__action"
                    @click="decideBankMatch('reanalyze')"
                  >重新分析这道题</button>
                </template>
                <template v-else-if="item.kind === 'image_uncertain'">
                  <button
                    type="button"
                    class="dup-compare__action"
                    @click="decideBankMatch('same')"
                  >图片一致，是同一题</button>
                  <button
                    type="button"
                    class="dup-compare__action dup-compare__action--quiet"
                    @click="decideBankMatch('different')"
                  >不是同一题</button>
                </template>
                <template v-else-if="item.kind === 'suspected'">
                  <button
                    type="button"
                    class="dup-compare__action"
                    @click="decideBankMatch('same')"
                  >是同一题</button>
                  <button
                    type="button"
                    class="dup-compare__action dup-compare__action--quiet"
                    @click="decideBankMatch('different')"
                  >不是同一题</button>
                </template>
                <template v-else-if="item.kind === 'answer_conflict'">
                  <button
                    type="button"
                    class="dup-compare__action"
                    @click="decideBankMatch('different')"
                  >本卷答案正确</button>
                  <span class="dup-compare__action-note">
                    <button
                      type="button"
                      class="dup-compare__action"
                      :disabled="!item.suggested_answer_override"
                      @click="decideBankAnswer"
                    >题库答案正确</button>
                    <small>本卷将改用题库答案</small>
                  </span>
                </template>
              </template>
            </div>
          </footer>
        </template>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.dup-compare__overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: var(--color-overlay-mask);
}

.dup-compare {
  position: fixed;
  inset: 6vh 6vw;
  z-index: 61;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-overlay);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-overlay);
}

.dup-compare__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding-block-end: var(--space-2);
  border-block-end: var(--border-width) solid var(--color-border-subtle);
}

.dup-compare__title {
  margin: 0;
  overflow: hidden;
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dup-compare__reason {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dup-compare__counter {
  flex: 0 0 auto;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-variant-numeric: tabular-nums;
}


.dup-compare__panes {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
  min-height: 0;
  padding-block: var(--space-3);
}

.dup-compare__pane {
  min-width: 0;
  padding: var(--space-3);
  overflow: auto;
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  background: var(--secondary);
}

.dup-compare__pane > header {
  margin-block-end: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.dup-compare__assets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-block-start: var(--space-2);
}

.dup-compare__assets img {
  max-width: min(100%, 360px);
  max-height: min(220px, 34vh);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-sm);
}

.dup-compare__hint {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.dup-compare__bank-answer {
  margin-block-start: var(--space-2);
}

.dup-compare__bank-answer summary {
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.dup-compare__answers {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-2);
  padding-block-end: var(--space-2);
}

.dup-compare__answers > div {
  min-width: 0;
  padding: var(--space-2);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-sm);
}

.dup-compare__answers strong { font-size: var(--font-size-caption); }
.dup-compare__answers p {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-caption);
}

.dup-compare__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-block-start: var(--space-2);
  border-block-start: var(--border-width) solid var(--color-border-subtle);
}

.dup-compare__nav {
  display: flex;
  gap: var(--space-2);
}

.dup-compare__nav-button {
  padding: var(--space-1) var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.dup-compare__nav-button:disabled { opacity: 0.45; cursor: default; }

.dup-compare__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.dup-compare__action {
  padding: var(--space-1) var(--space-3);
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-sm);
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.dup-compare__action--quiet {
  border-color: var(--color-border-default);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
}

.dup-compare__action:disabled { opacity: 0.45; cursor: default; }

.dup-compare__action-note {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.dup-compare__action-note small {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.dup-compare__decided {
  padding: 1px var(--space-2);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-tag);
  background: var(--secondary);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.dup-compare__undo {
  border: none;
  background: none;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.dup-compare__undo:hover { text-decoration: underline; }

.dup-compare__nav-button:focus-visible,
.dup-compare__action:focus-visible,

.dup-compare__undo:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}
</style>
