<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  QUESTION_TYPES,
  type ConfigQuestionPreview,
  type ConfigAmbiguousAssetDecision,
  type ConfigSource,
  type ConfigSourceAsset,
  type QuestionDecision,
  type QuestionType,
} from '../../api/config-workspace'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import type { ConfigQuestionGenerationState } from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  source: ConfigSource
  decisions?: QuestionDecision[]
  assetDecisions?: ConfigAmbiguousAssetDecision[]
  questionStates?: ConfigQuestionGenerationState[]
}>(), {
  decisions: () => [],
  assetDecisions: () => [],
  questionStates: () => [],
})

const emit = defineEmits<{
  'update:decisions': [decisions: QuestionDecision[]]
  'update:asset-decisions': [decisions: ConfigAmbiguousAssetDecision[]]
}>()
const emptyRichContent: NonNullable<ConfigQuestionPreview['rich_content']> = {
  available: false,
  question_block_count: 0,
  answer_block_count: 0,
  question_blocks: [],
  answer_blocks: [],
}

const sourceAssets = computed<ConfigSourceAsset[]>(() => {
  if (props.source.assets !== undefined) return props.source.assets
  let automaticIndex = 0
  const automatic = props.source.questions.flatMap((question) => (
    (['question', 'answer'] as const).flatMap((assetKind) => (
      imageUrls(question, assetKind).map((assetUrl) => ({
        asset_id: `P${++automaticIndex}`,
        asset_url: assetUrl,
        assignment_state: 'automatic' as const,
        question_id: question.question_id,
        asset_kind: assetKind,
        candidate_question_ids: [],
      }))
    ))
  ))
  return [
    ...automatic,
    ...(props.source.ambiguous_assets ?? []).map((item) => ({
      asset_id: item.candidate_id,
      asset_url: item.asset_url,
      assignment_state: 'uncertain' as const,
      question_id: null,
      asset_kind: item.source_section,
      candidate_question_ids: [item.previous_question_id, item.next_question_id],
    })),
  ]
})

const uncertainAssets = computed(() => sourceAssets.value
  .filter((item) => item.assignment_state === 'uncertain'))
const expandedAnswerQuestionId = ref<string | null>(null)

function richContent(question: ConfigQuestionPreview): NonNullable<ConfigQuestionPreview['rich_content']> {
  return question.rich_content ?? emptyRichContent
}

function textBlocks(
  question: ConfigQuestionPreview,
  kind: 'question' | 'answer',
): NonNullable<ConfigQuestionPreview['rich_content']>['question_blocks'] {
  const blocks = kind === 'question'
    ? richContent(question).question_blocks
    : richContent(question).answer_blocks
  return blocks.map((block) => ({ ...block, asset_indexes: [], asset_urls: [] }))
}

function assetDecision(candidateId: string): ConfigAmbiguousAssetDecision | undefined {
  return props.assetDecisions.find((item) => item.candidate_id === candidateId)
}

function bindAsset(
  assetId: string,
  questionId: string,
  assetKind: 'question' | 'answer',
): void {
  const retained = props.assetDecisions.filter(
    (item) => item.candidate_id !== assetId,
  )
  emit('update:asset-decisions', [
    ...retained,
    {
      candidate_id: assetId,
      action: 'bind',
      question_id: questionId,
      asset_kind: assetKind,
    },
  ])
}

function ignoreAsset(assetId: string): void {
  const current = assetDecision(assetId)
  emit('update:asset-decisions', [
    ...props.assetDecisions.filter((item) => item.candidate_id !== assetId),
    ...(current?.action === 'ignore'
      ? []
      : [{ candidate_id: assetId, action: 'ignore' as const }]),
  ])
}

function isIgnored(assetId: string): boolean {
  return assetDecision(assetId)?.action === 'ignore'
}

function startAssetDrag(assetId: string, event: DragEvent): void {
  event.dataTransfer?.setData('application/x-config-asset', assetId)
  event.dataTransfer?.setData('text/plain', assetId)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function dropCandidate(
  questionId: string,
  assetKind: 'question' | 'answer',
  event: DragEvent,
): void {
  const assetId = event.dataTransfer?.getData('application/x-config-asset')
    || event.dataTransfer?.getData('text/plain')
  if (!assetId) return
  if (!sourceAssets.value.some((item) => item.asset_id === assetId)) return
  bindAsset(assetId, questionId, assetKind)
}

function assetUrl(questionId: string, assetKind: 'question' | 'answer'): string {
  return `/api/sessions/${props.source.session_id}/config/sources/${encodeURIComponent(props.source.source_id)}`
    + `/questions/${encodeURIComponent(questionId)}/assets/${assetKind}`
}

function imageUrls(question: ConfigQuestionPreview, assetKind: 'question' | 'answer'): string[] {
  const blocks = assetKind === 'question'
    ? richContent(question).question_blocks
    : richContent(question).answer_blocks
  const urls = blocks.flatMap((block) => block.asset_urls)
  if (urls.length) return urls
  const hasAsset = assetKind === 'question'
    ? question.has_question_asset
    : question.has_answer_asset
  return hasAsset ? [assetUrl(question.question_id, assetKind)] : []
}

function assetPlacement(asset: ConfigSourceAsset): { questionId: string | null; assetKind: 'question' | 'answer' } | null {
  const decision = assetDecision(asset.asset_id)
  if (decision?.action === 'ignore') {
    return { questionId: asset.question_id, assetKind: asset.asset_kind }
  }
  if (decision?.action === 'bind') {
    return { questionId: decision.question_id, assetKind: decision.asset_kind }
  }
  return { questionId: asset.question_id, assetKind: asset.asset_kind }
}

function placedAssets(questionId: string, assetKind: 'question' | 'answer'): ConfigSourceAsset[] {
  return sourceAssets.value.filter((asset) => {
    const placement = assetPlacement(asset)
    return placement?.questionId === questionId && placement.assetKind === assetKind
  })
}

function candidatesBefore(questionId: string): ConfigSourceAsset[] {
  return uncertainAssets.value.filter((item) => (
    item.candidate_question_ids[1] === questionId
    && assetDecision(item.asset_id) === undefined
  ))
}

const ignoredAssets = computed(() => uncertainAssets.value
  .filter((item) => assetDecision(item.asset_id)?.action === 'ignore'))
const ignoredExpanded = ref(false)

function restoreAsset(assetId: string): void {
  emit('update:asset-decisions', props.assetDecisions.filter(
    (item) => item.candidate_id !== assetId,
  ))
}

function stateOf(questionId: string): 'pending' | 'running' | 'passed' | 'blocked' | 'failed' | '' {
  return props.questionStates.find((item) => item.question_id === questionId)?.state ?? ''
}

function answerStatus(question: ConfigQuestionPreview): string {
  if (question.local_answer_trusted) return '答案已匹配'
  if (question.answer_present) return '识别到答案，完整预览'
  return '未识别到答案'
}

const questionTypeLabels: Record<QuestionType, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  calculation: '计算题',
  proof: '证明题',
  comprehensive: '综合解答题',
}

function knownQuestionTypeLabel(questionType: string): string | null {
  if (questionType === 'single_choice' || questionType === 'multi_choice') {
    return questionTypeLabels.choice
  }
  if ((QUESTION_TYPES as readonly string[]).includes(questionType)) {
    return questionTypeLabels[questionType as QuestionType]
  }
  return null
}

function localTypeNote(question: ConfigQuestionPreview): string {
  const confirmed = decisionFor(question.question_id)?.question_type
  if (confirmed) {
    const confirmedLabel = knownQuestionTypeLabel(confirmed)
    return confirmedLabel ? `您已确认为${confirmedLabel}` : ''
  }
  const label = knownQuestionTypeLabel(question.question_type)
  if (!label) return ''
  const basis = question.question_type_basis?.trim()
  return basis
    ? `本地判为${label}（${basis} · 未经您确认）`
    : `本地判为${label}（未经您确认）`
}

function decisionFor(questionId: string): QuestionDecision | undefined {
  return props.decisions.find((item) => item.question_id === questionId)
}

function confirmQuestionType(questionId: string, event: Event): void {
  const questionType = (event.currentTarget as HTMLSelectElement).value as QuestionType | ''
  const current = decisionFor(questionId)
  const retained = props.decisions.filter((item) => item.question_id !== questionId)
  if (!questionType) {
    emit('update:decisions', current
      ? [...retained, { ...current, question_type: undefined }]
      : retained)
    return
  }
  emit('update:decisions', [
    ...retained,
    {
      question_id: questionId,
      excluded: current?.excluded ?? false,
      ...(current ?? {}),
      question_type: questionType,
    },
  ])
}

function openFullAnswer(questionId: string): void {
  expandedAnswerQuestionId.value = questionId
}

function closeFullAnswer(): void {
  expandedAnswerQuestionId.value = null
}

const expandedAnswer = computed(() => (
  props.source.questions.find((question) => question.question_id === expandedAnswerQuestionId.value)
  ?? null
))

watch(() => props.source.source_revision, (_revision, previous) => {
  if (previous !== undefined || props.decisions.length > 0) emit('update:decisions', [])
}, { immediate: true })
</script>

<template>
  <section class="question-review" aria-labelledby="question-review-title">
    <header class="config-section-heading">
      <div>
        <h2 id="question-review-title">核对拆题结果</h2>
        <p>题目与答案摘要并排呈现，完整解析可从右侧打开。每题会显示本地判型；拿不准时会出现黄条，请您确认。未确认前不会当作最终题型。</p>
      </div>
      <span v-if="source.questions.length" class="question-review__count">
        共 {{ source.questions.length }} 题
      </span>
    </header>

    <div v-if="ignoredAssets.length" class="question-review__ignored-tray">
      <button
        type="button"
        class="question-review__ignored-toggle"
        :aria-expanded="ignoredExpanded"
        @click="ignoredExpanded = !ignoredExpanded"
      >
        已忽略 {{ ignoredAssets.length }} 张<small>点开可查看并恢复</small>
      </button>
      <ul v-if="ignoredExpanded" class="question-review__ignored-list">
        <li
          v-for="asset in ignoredAssets"
          :key="asset.asset_id"
          class="question-review__ignored-item"
        >
          <img :src="asset.asset_url" :alt="`${asset.asset_id} 已忽略图片`">
          <div><strong>疑难图片 · {{ asset.candidate_question_ids.join(' / ') }}</strong><small>已忽略，不会发送给AI</small></div>
          <button
            type="button"
            class="question-review__restore-button"
            :aria-label="`恢复疑难图片 ${asset.candidate_question_ids.join(' / ')} 到待归属提醒区`"
            @click="restoreAsset(asset.asset_id)"
          >恢复</button>
        </li>
      </ul>
    </div>

    <p v-if="source.questions.length === 0" class="question-review__empty" role="status">
      当前来源没有可核对的题目，请更换文件后重试。
    </p>
    <ol v-else class="question-review__list">
      <template v-for="question in source.questions" :key="question.question_id">
      <li
        v-for="candidate in candidatesBefore(question.question_id)"
        :key="candidate.asset_id"
        class="question-review__asset-between"
      >
        <img :src="candidate.asset_url" :alt="`${candidate.asset_id} 待归属图片`" draggable="true" @dragstart="startAssetDrag(candidate.asset_id, $event)">
        <div><strong>疑难图片 · {{ candidate.candidate_question_ids.join(' / ') }}</strong><small>待归属</small></div>
        <button
          type="button"
          class="question-review__ignore-button"
          aria-label="忽略这张图片，不发送给AI"
          :aria-pressed="false"
          @click="ignoreAsset(candidate.asset_id)"
        ><span aria-hidden="true">×</span></button>
      </li>
      <li
        class="question-review__row"
        :class="{ 'is-exception': ['blocked', 'failed'].includes(stateOf(question.question_id)) }"
        :data-question-row="question.question_id"
      >
        <header class="question-review__identity">
          <div class="question-review__toggle">
            <span :class="`is-${stateOf(question.question_id) || 'idle'}`" aria-hidden="true">●</span>
            <strong class="question-review__id">{{ question.question_id }}</strong>
            <span>题目与答案摘要</span>
          </div>
          <span v-if="question.needs_review" class="question-review__warning">建议留意预览</span>
        </header>
        <p v-if="localTypeNote(question)" class="question-review__local-type">{{ localTypeNote(question) }}</p>

        <label v-if="question.question_type_review_required" class="question-review__type-check">
          <span><strong>题型建议（可选修改）</strong>{{ question.question_type_review_reason || '题面形式与解析内容存在冲突。' }} 不修改时将沿用系统建议，不会阻塞 AI 生成。</span>
          <select :value="decisionFor(question.question_id)?.question_type ?? ''" @change="confirmQuestionType(question.question_id, $event)">
            <option value="">沿用系统建议：{{ questionTypeLabels[question.question_type as QuestionType] ?? question.question_type }}</option>
            <option v-for="type in QUESTION_TYPES" :key="type" :value="type">{{ questionTypeLabels[type] }}</option>
          </select>
        </label>

        <div class="question-review__pair">
          <section
            class="question-review__paper-panel"
            :data-question-panel="question.question_id"
            @dragover.prevent
            @drop.prevent="dropCandidate(question.question_id, 'question', $event)"
          >
            <header><strong>题目预览</strong><span>题目图片</span></header>
            <div :data-question-content="question.question_id">
              <QuestionContentRenderer
                :blocks="textBlocks(question, 'question')"
                :fallback="question.question_preview"
                empty-label="题目文字未提供预览。"
                media-mode="review"
                paper-media-flow
                dense
              />
            </div>
            <div v-if="placedAssets(question.question_id, 'question').length" class="question-review__image-well" :data-image-count="placedAssets(question.question_id, 'question').length" :aria-label="`${question.question_id} 题目图片放置区`">
              <div
                v-for="(asset, index) in placedAssets(question.question_id, 'question')"
                :key="asset.asset_id"
                class="question-review__placed-asset"
                :class="{ 'is-ignored': isIgnored(asset.asset_id) }"
              >
                <img
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 题目图 ${index + 1}`"
                  draggable="true"
                  :data-asset-id="asset.asset_id"
                  @dragstart="startAssetDrag(asset.asset_id, $event)"
                >
                <button
                  type="button"
                  class="question-review__ignore-button"
                  :aria-label="isIgnored(asset.asset_id) ? '撤销忽略这张图片' : '忽略这张图片，不发送给AI'"
                  :aria-pressed="isIgnored(asset.asset_id)"
                  @click="ignoreAsset(asset.asset_id)"
                ><span aria-hidden="true">×</span></button>
              </div>
            </div>
          </section>

          <section
            class="question-review__paper-panel question-review__paper-panel--answer"
            :data-answer-panel="question.question_id"
            @dragover.prevent
            @drop.prevent="dropCandidate(question.question_id, 'answer', $event)"
          >
            <header><strong>答案摘要</strong><span>{{ answerStatus(question) }}</span></header>
            <div class="question-review__answer-preview" :data-answer-content="question.question_id">
              <QuestionContentRenderer
                :blocks="[]"
                :fallback="question.answer_preview"
                empty-label="答案内容暂未识别。"
                media-mode="review"
                dense
              />
            </div>
            <button
              type="button"
              class="question-review__answer-expand"
              @click="openFullAnswer(question.question_id)"
            >
              <span aria-hidden="true">⋯</span> 查看完整答案
            </button>
            <div v-if="placedAssets(question.question_id, 'answer').length" class="question-review__image-well" :data-image-count="placedAssets(question.question_id, 'answer').length" :aria-label="`${question.question_id} 答案图片放置区`">
              <div
                v-for="(asset, index) in placedAssets(question.question_id, 'answer')"
                :key="asset.asset_id"
                class="question-review__placed-asset"
                :class="{ 'is-ignored': isIgnored(asset.asset_id) }"
              >
                <img
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 答案图 ${index + 1}`"
                  draggable="true"
                  :data-asset-id="asset.asset_id"
                  @dragstart="startAssetDrag(asset.asset_id, $event)"
                >
                <button
                  type="button"
                  class="question-review__ignore-button"
                  :aria-label="isIgnored(asset.asset_id) ? '撤销忽略这张图片' : '忽略这张图片，不发送给AI'"
                  :aria-pressed="isIgnored(asset.asset_id)"
                  @click="ignoreAsset(asset.asset_id)"
                ><span aria-hidden="true">×</span></button>
              </div>
            </div>
          </section>
        </div>
      </li>
      </template>
    </ol>

    <Teleport to="body">
      <div
        v-if="expandedAnswer"
        class="question-review__answer-layer"
        @click.self="closeFullAnswer"
      >
        <aside
          class="question-review__answer-drawer"
          role="dialog"
          aria-modal="true"
          :aria-label="`${expandedAnswer.question_id} 完整答案`"
        >
          <header>
            <div>
              <small>{{ expandedAnswer.question_id }}</small>
              <h3>完整答案</h3>
            </div>
            <button type="button" aria-label="关闭完整答案" @click="closeFullAnswer">×</button>
          </header>
          <QuestionContentRenderer
            :blocks="textBlocks(expandedAnswer, 'answer')"
            :fallback="expandedAnswer.answer_preview"
            empty-label="答案内容暂未识别。"
            media-mode="review"
          />
        </aside>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.question-review__asset-tray {
  display: grid;
  gap: var(--space-3);
  margin: 0 var(--space-4) var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--color-warning);
  border-left-width: 4px;
  border-radius: var(--radius-md);
  background: var(--color-warning-subtle);
}

.question-review__asset-tray.is-complete {
  border-color: var(--color-success);
  background: var(--color-success-subtle);
}

.question-review__asset-tray-heading span,
.question-review__asset-candidate span,
.question-review__asset-candidate small {
  display: block;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.question-review__asset-tray-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-2);
}

.question-review__asset-candidate {
  grid-column: auto;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: var(--space-2);
  align-items: center;
  margin: 0;
  padding: var(--space-2);
  border: 1px solid color-mix(in srgb, var(--color-warning) 40%, var(--card));
  border-radius: var(--radius-sm);
  background: var(--card);
  cursor: grab;
}

.question-review__asset-candidate.is-resolved { border-color: color-mix(in srgb, var(--color-success) 35%, var(--card)); }
.question-review__asset-candidate:active { cursor: grabbing; }
.question-review__asset-candidate img { width: 64px; height: 64px; object-fit: contain; }
.question-review__asset-candidate label { grid-column: 1 / -1; }
.question-review__asset-candidate select { width: 100%; min-height: 36px; }

.question-review__list {
  display: grid;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  border: 0;
  list-style: none;
}
.question-review__ignored-tray {
  display: grid;
  gap: var(--space-2);
  margin: 0 0 var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--secondary);
}
.question-review__ignored-toggle {
  display: flex;
  align-items: baseline;
  justify-self: start;
  gap: var(--space-2);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
  cursor: pointer;
}
.question-review__ignored-toggle small { color: var(--color-text-secondary); }
.question-review__ignored-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
.question-review__ignored-item {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--card);
}
.question-review__ignored-item img { width: 64px; height: 64px; object-fit: contain; filter: grayscale(1); opacity: .55; }
.question-review__ignored-item small { display: block; color: var(--color-text-secondary); font-size: var(--font-size-caption); }
.question-review__restore-button {
  min-height: 32px;
  padding-inline: var(--space-3);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  cursor: pointer;
}
.question-review__restore-button:hover,
.question-review__restore-button:focus-visible { border-color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 8%, transparent); }
.question-review__asset-between { position: relative; display: grid; grid-template-columns: 88px minmax(0, 1fr); align-items: center; gap: var(--space-3); padding: var(--space-3) 52px var(--space-3) var(--space-3); border: 1px solid var(--color-warning); border-left-width: 4px; border-radius: var(--radius-sm); background: var(--color-warning-subtle); }
.question-review__asset-between img { width: 88px; height: 72px; object-fit: contain; }
.question-review__asset-between small { display: block; color: var(--color-text-secondary); }

.question-review__ignore-button {
  position: absolute;
  inset-block-start: var(--space-2);
  inset-inline-end: var(--space-2);
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--color-text-secondary) 35%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, var(--card) 76%, transparent);
  color: var(--color-text-secondary);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  backdrop-filter: blur(3px);
}
.question-review__ignore-button:hover,
.question-review__ignore-button:focus-visible { border-color: var(--color-danger); color: var(--color-danger); }
.question-review__ignore-button:focus-visible { outline: 2px solid var(--color-danger); outline-offset: 2px; }
.question-review__ignore-button[aria-pressed="true"] { border-color: var(--color-danger); background: var(--color-danger); color: var(--destructive-foreground); }

.question-review__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-2);
  padding: 0 0 var(--space-3);
  border-bottom: 1px solid var(--color-border-subtle);
}
.question-review__row.is-exception { padding: var(--space-3); border: 1px solid color-mix(in srgb, var(--color-danger) 45%, var(--color-border-default)); border-left-width: 4px; background: color-mix(in srgb, var(--color-danger-subtle) 45%, var(--card)); }
.question-review__identity {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.question-review__local-type {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}
.question-review__type-check { display: grid; grid-template-columns: minmax(0, 1fr) minmax(180px, 240px); align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-3); border-inline-start: 4px solid var(--color-warning); background: var(--color-warning-subtle); }
.question-review__type-check > span { display: grid; gap: 2px; color: var(--color-text-secondary); font-size: var(--font-size-caption); }
.question-review__type-check strong { color: var(--color-warning); }
.question-review__type-check select { width: 100%; }
.question-review__toggle { display: flex; min-width: 0; flex: 1; align-items: center; gap: var(--space-2); padding: var(--space-1); color: var(--color-text-primary); text-align: left; }
.question-review__toggle > span:last-child { min-width: 0; overflow: hidden; color: var(--color-text-secondary); font-size: var(--font-size-caption); text-overflow: ellipsis; white-space: nowrap; }
.question-review__toggle .is-passed { color: var(--color-success); }
.question-review__toggle .is-blocked, .question-review__toggle .is-failed { color: var(--color-danger); }
.question-review__toggle .is-pending, .question-review__toggle .is-running, .question-review__toggle .is-idle { color: var(--color-text-muted); }
.question-review__pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: stretch;
  gap: var(--space-3);
  min-width: 0;
}

.question-review__paper-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  align-content: start;
  gap: var(--space-3);
  min-width: 0;
  overflow: visible;
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--card);
}

.question-review__paper-panel > [data-question-content] {
  min-width: 0;
  max-width: 100%;
}

.question-review__paper-panel--answer { background: var(--secondary); }

.question-review__answer-preview {
  position: relative;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 7.2em;
  overflow: hidden;
}

.question-review__answer-expand {
  align-self: flex-start;
  min-height: 32px;
  padding-inline: 8px;
  border: 0;
  background: transparent;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
}

.question-review__answer-expand:hover { background: color-mix(in srgb, var(--color-accent) 8%, transparent); }

.question-review__answer-layer {
  position: fixed;
  z-index: 1300;
  inset: 0;
  display: flex;
  justify-content: flex-end;
  background: color-mix(in srgb, var(--color-text-primary) 38%, transparent);
}

.question-review__answer-drawer {
  display: flex;
  width: min(720px, 100%);
  max-width: 100%;
  height: 100%;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  overflow: auto;
  background: var(--color-bg-surface);
  box-shadow: -12px 0 32px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
}

.question-review__answer-drawer > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.question-review__answer-drawer h3,
.question-review__answer-drawer small { margin: 0; }
.question-review__answer-drawer small { color: var(--color-text-secondary); }
.question-review__answer-drawer button { font-size: 24px; line-height: 1; }

.question-review__paper-panel > header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border);
}

.question-review__paper-panel > header span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.question-review__image-well {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-height: 54px;
  padding: var(--space-2);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--secondary);
}

.question-review__image-well[data-image-count="3"],
.question-review__image-well[data-image-count="4"] {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.question-review__image-well[data-image-count="3"] {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.question-review__placed-asset {
  position: relative;
  display: grid;
  gap: var(--space-1);
  width: min(100%, 240px);
  padding: var(--space-1);
}

.question-review__image-well[data-image-count="3"] .question-review__placed-asset,
.question-review__image-well[data-image-count="4"] .question-review__placed-asset {
  min-width: 0;
  width: auto;
}

.question-review__placed-asset > img {
  max-width: min(100%, 240px);
  max-height: 180px;
  object-fit: contain;
  cursor: grab;
}

.question-review__image-well[data-image-count="3"] .question-review__placed-asset > img,
.question-review__image-well[data-image-count="4"] .question-review__placed-asset > img {
  max-height: 96px;
  max-width: 100%;
  width: 100%;
}

.question-review__placed-asset.is-ignored,
.question-review__asset-between.is-ignored {
  overflow: hidden;
}

.question-review__placed-asset.is-ignored > img,
.question-review__asset-between.is-ignored > img {
  filter: grayscale(1);
  opacity: .28;
}

.question-review__placed-asset.is-ignored::before,
.question-review__placed-asset.is-ignored::after,
.question-review__asset-between.is-ignored::before,
.question-review__asset-between.is-ignored::after {
  position: absolute;
  z-index: 1;
  inset-block-start: 50%;
  inset-inline-start: 50%;
  width: min(74%, 170px);
  height: 4px;
  border-radius: 999px;
  background: var(--color-danger);
  content: '';
  pointer-events: none;
  transform-origin: center;
  animation: question-review-cross-in 180ms ease-out both;
}
.question-review__placed-asset.is-ignored::before,
.question-review__asset-between.is-ignored::before { transform: translate(-50%, -50%) rotate(38deg); }
.question-review__placed-asset.is-ignored::after,
.question-review__asset-between.is-ignored::after { transform: translate(-50%, -50%) rotate(-38deg); }
.question-review__placed-asset.is-ignored .question-review__ignore-button,
.question-review__asset-between.is-ignored .question-review__ignore-button { z-index: 2; }

@keyframes question-review-cross-in {
  from { opacity: 0; scale: .55 1; }
  to { opacity: 1; scale: 1 1; }
}

@media (prefers-reduced-motion: reduce) {
  .question-review__placed-asset.is-ignored::before,
  .question-review__placed-asset.is-ignored::after,
  .question-review__asset-between.is-ignored::before,
  .question-review__asset-between.is-ignored::after { animation: none; }
}

.question-review__placed-asset select {
  width: 100%;
  min-height: 34px;
}

.question-review__image-well > span {
  align-self: center;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

@media (max-width: 900px) {
  .question-review__type-check { grid-template-columns: minmax(0, 1fr); }
  .question-review__pair { grid-template-columns: minmax(0, 1fr); }
  .question-review__asset-between { grid-template-columns: 64px minmax(0, 1fr); }
  .question-review__asset-between img { width: 64px; height: 56px; }
}

@media (max-width: 520px) {
  .question-review__image-well[data-image-count="4"] {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
