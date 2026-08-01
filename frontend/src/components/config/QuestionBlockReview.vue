<script setup lang="ts">
import { computed, watch } from 'vue'

import {
  type ConfigQuestionPreview,
  type ConfigAmbiguousAssetDecision,
  type ConfigSource,
  type ConfigSourceAsset,
  type QuestionDecision,
} from '../../api/config-workspace'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const props = withDefaults(defineProps<{
  source: ConfigSource
  decisions?: QuestionDecision[]
  assetDecisions?: ConfigAmbiguousAssetDecision[]
}>(), {
  decisions: () => [],
  assetDecisions: () => [],
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

const unresolvedAssetCount = computed(() => {
  const resolved = new Set(props.assetDecisions.map((item) => item.candidate_id))
  return uncertainAssets.value.filter((item) => !resolved.has(item.asset_id)).length
})

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

function assetChoice(asset: ConfigSourceAsset): string {
  const decision = assetDecision(asset.asset_id)
  if (decision === undefined) {
    return asset.question_id === null
      ? ''
      : `${asset.question_id}:${asset.asset_kind}`
  }
  if (decision.action === 'ignore') return 'ignore'
  return `${decision.question_id}:${decision.asset_kind}`
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

function updateAssetChoice(asset: ConfigSourceAsset, event: Event): void {
  const value = (event.currentTarget as HTMLSelectElement).value
  const retained = props.assetDecisions.filter(
    (item) => item.candidate_id !== asset.asset_id,
  )
  if (!value) {
    emit('update:asset-decisions', retained)
    return
  }
  if (value === 'ignore') {
    emit('update:asset-decisions', [
      ...retained,
      { candidate_id: asset.asset_id, action: 'ignore' },
    ])
    return
  }
  const [questionId, assetKind] = value.split(':')
  if (!props.source.questions.some((item) => item.question_id === questionId)
    || !['question', 'answer'].includes(assetKind ?? '')) return
  bindAsset(asset.asset_id, questionId!, assetKind as 'question' | 'answer')
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
  if (decision?.action === 'ignore') return null
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

function candidatePlacement(asset: ConfigSourceAsset): string {
  const decision = assetDecision(asset.asset_id)
  if (decision === undefined) return '待归属'
  if (decision.action === 'ignore') return '已忽略'
  return `${decision.question_id} · ${decision.asset_kind === 'question' ? '题目' : '答案'}`
}

function answerStatus(question: ConfigQuestionPreview): string {
  if (question.local_answer_trusted) return '答案已匹配'
  if (question.answer_present) return '识别到答案，完整预览'
  return '未识别到答案'
}

watch(() => props.source.source_revision, (_revision, previous) => {
  if (previous !== undefined || props.decisions.length > 0) emit('update:decisions', [])
}, { immediate: true })
</script>

<template>
  <section class="question-review" aria-labelledby="question-review-title">
    <header class="config-section-heading">
      <div>
        <h2 id="question-review-title">核对拆题结果</h2>
        <p>题目与完整答案并排呈现。黄色图片请拖到对应区域；题型、小问和作答方式由 AI 在后续分析中判断。</p>
      </div>
      <span v-if="source.questions.length" class="question-review__count">
        共 {{ source.questions.length }} 题
      </span>
    </header>

    <aside
      v-if="uncertainAssets.length"
      class="question-review__asset-tray"
      :class="{ 'is-complete': unresolvedAssetCount === 0 }"
      aria-label="待归属图片"
    >
      <div class="question-review__asset-tray-heading">
        <div>
          <strong>{{ unresolvedAssetCount ? `${unresolvedAssetCount} 张图片待归属` : '图片归属已完成' }}</strong>
          <span>本地程序不猜测，只提供相邻题作为建议；你也可以拖到其他题目的题目区或答案区。</span>
        </div>
      </div>
      <div class="question-review__asset-tray-list">
        <article
          v-for="candidate in uncertainAssets"
          :key="candidate.asset_id"
          class="question-review__asset-candidate"
          :class="{ 'is-resolved': assetDecision(candidate.asset_id) !== undefined }"
          draggable="true"
          :data-asset-id="candidate.asset_id"
          @dragstart="startAssetDrag(candidate.asset_id, $event)"
        >
          <img :src="candidate.asset_url" :alt="`${candidate.asset_id} 待归属图片`">
          <div>
            <strong>{{ candidate.asset_id }}</strong>
            <span>{{ candidate.candidate_question_ids.join(' / ') }}</span>
            <small>{{ candidatePlacement(candidate) }}</small>
          </div>
          <label>
            <span class="sr-only">放到</span>
            <select
              :aria-label="`${candidate.asset_id} 图片归属`"
              :value="assetChoice(candidate)"
              @change="updateAssetChoice(candidate, $event)"
            >
              <option value="">暂不确定</option>
              <template v-for="question in source.questions" :key="question.question_id">
                <option :value="`${question.question_id}:question`">{{ question.question_id }} 题目</option>
                <option :value="`${question.question_id}:answer`">{{ question.question_id }} 答案</option>
              </template>
              <option value="ignore">忽略这张图片</option>
            </select>
          </label>
        </article>
      </div>
    </aside>

    <p v-if="source.questions.length === 0" class="question-review__empty" role="status">
      当前来源没有可核对的题目，请更换文件后重试。
    </p>
    <ol v-else class="question-review__list">
      <li
        v-for="question in source.questions"
        :key="question.question_id"
        class="question-review__row"
      >
        <header class="question-review__identity">
          <strong class="question-review__id">{{ question.question_id }}</strong>
          <span v-if="question.needs_review" class="question-review__warning">建议留意预览</span>
        </header>

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
            <div class="question-review__image-well" :aria-label="`${question.question_id} 题目图片放置区`">
              <div
                v-for="(asset, index) in placedAssets(question.question_id, 'question')"
                :key="asset.asset_id"
                class="question-review__placed-asset"
              >
                <img
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 题目图 ${index + 1}`"
                  draggable="true"
                  :data-asset-id="asset.asset_id"
                  @dragstart="startAssetDrag(asset.asset_id, $event)"
                >
                <label>
                  <span class="sr-only">移动{{ question.question_id }}题目图{{ index + 1 }}</span>
                  <select :value="assetChoice(asset)" @change="updateAssetChoice(asset, $event)">
                    <option value="">选择图片归属</option>
                    <template v-for="target in source.questions" :key="target.question_id">
                      <option :value="`${target.question_id}:question`">{{ target.question_id }} 题目</option>
                      <option :value="`${target.question_id}:answer`">{{ target.question_id }} 答案</option>
                    </template>
                    <option value="ignore">忽略这张图片</option>
                  </select>
                </label>
              </div>
              <span v-if="placedAssets(question.question_id, 'question').length === 0">可将图片拖到这里</span>
            </div>
          </section>

          <section
            class="question-review__paper-panel question-review__paper-panel--answer"
            :data-answer-panel="question.question_id"
            @dragover.prevent
            @drop.prevent="dropCandidate(question.question_id, 'answer', $event)"
          >
            <header><strong>完整答案</strong><span>{{ answerStatus(question) }}</span></header>
            <div :data-answer-content="question.question_id">
              <QuestionContentRenderer
                :blocks="textBlocks(question, 'answer')"
                :fallback="question.answer_preview"
                empty-label="答案内容暂未识别。"
                media-mode="review"
                dense
              />
            </div>
            <div class="question-review__image-well" :aria-label="`${question.question_id} 答案图片放置区`">
              <div
                v-for="(asset, index) in placedAssets(question.question_id, 'answer')"
                :key="asset.asset_id"
                class="question-review__placed-asset"
              >
                <img
                  :src="asset.asset_url"
                  :alt="`${question.question_id} 答案图 ${index + 1}`"
                  draggable="true"
                  :data-asset-id="asset.asset_id"
                  @dragstart="startAssetDrag(asset.asset_id, $event)"
                >
                <label>
                  <span class="sr-only">移动{{ question.question_id }}答案图{{ index + 1 }}</span>
                  <select :value="assetChoice(asset)" @change="updateAssetChoice(asset, $event)">
                    <option value="">选择图片归属</option>
                    <template v-for="target in source.questions" :key="target.question_id">
                      <option :value="`${target.question_id}:question`">{{ target.question_id }} 题目</option>
                      <option :value="`${target.question_id}:answer`">{{ target.question_id }} 答案</option>
                    </template>
                    <option value="ignore">忽略这张图片</option>
                  </select>
                </label>
              </div>
              <span v-if="placedAssets(question.question_id, 'answer').length === 0">可将图片拖到这里</span>
            </div>
          </section>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.question-review__asset-tray {
  display: grid;
  gap: var(--space-3);
  margin: 0 var(--space-4) var(--space-4);
  padding: var(--space-4);
  border: 1px solid #d7a94a;
  border-left-width: 4px;
  border-radius: var(--radius-md);
  background: #fff8e8;
}

.question-review__asset-tray.is-complete {
  border-color: var(--color-success, #2f7d60);
  background: #f4faf7;
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
  border: 1px solid #e4c476;
  border-radius: var(--radius-sm);
  background: #fff;
  cursor: grab;
}

.question-review__asset-candidate.is-resolved { border-color: #8dbba7; }
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

.question-review__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-2);
  padding: 0 0 var(--space-3);
  border-bottom: 1px solid var(--color-border-subtle);
}

.question-review__identity {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.question-review__pair {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr);
  gap: var(--space-3);
  min-width: 0;
}

.question-review__paper-panel {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: #fff;
}

.question-review__paper-panel--answer { background: #f8fbfb; }

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
  border: 1px dashed #9eb4ba;
  border-radius: var(--radius-sm);
  background: #f7fafb;
}

.question-review__placed-asset {
  display: grid;
  gap: var(--space-1);
  width: min(100%, 240px);
}

.question-review__placed-asset > img {
  max-width: min(100%, 240px);
  max-height: 180px;
  object-fit: contain;
  cursor: grab;
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
  .question-review__pair { grid-template-columns: minmax(0, 1fr); }
}
</style>
