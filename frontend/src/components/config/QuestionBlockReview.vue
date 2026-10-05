<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  QUESTION_TYPES,
  type ConfigQuestionPreview,
  type ConfigAmbiguousAssetDecision,
  type ConfigSource,
  type ConfigSourceAsset,
  type ConfigSourceDuplicateItem,
  type QuestionDecision,
  type QuestionType,
} from '../../api/config-workspace'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import DuplicateCompareDialog from './DuplicateCompareDialog.vue'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'
import AppButton from '../design-system/AppButton.vue'
import type { ConfigQuestionGenerationState } from '../../api/config-workspace'
import { useConfigQuestionFocus } from '../../composables/useConfigQuestionFocus'
import {
  BANK_DUPLICATE_KINDS,
  REVIEW_DUPLICATE_KINDS,
  configQuestionFlags,
  configSourceDuplicateTag,
  duplicateDecisionRecorded,
  type ConfigQuestionFlag,
} from './config-question-flags'

const props = withDefaults(defineProps<{
  source: ConfigSource
  decisions?: QuestionDecision[]
  assetDecisions?: ConfigAmbiguousAssetDecision[]
  questionStates?: ConfigQuestionGenerationState[]
  duplicates?: ConfigSourceDuplicateItem[]
  duplicatesUnavailable?: boolean
}>(), {
  decisions: () => [],
  assetDecisions: () => [],
  questionStates: () => [],
  duplicates: () => [],
  duplicatesUnavailable: false,
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

function generationStateOf(questionId: string): ConfigQuestionGenerationState | undefined {
  return props.questionStates.find((item) => item.question_id === questionId)
}

function stateOf(questionId: string): 'pending' | 'running' | 'passed' | 'blocked' | 'failed' | '' {
  return generationStateOf(questionId)?.state ?? ''
}

function intakeFailed(questionId: string): boolean {
  return props.questionStates.some((item) => item.question_id === questionId && item.reason === 'question_bank_intake')
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

// 来源被替换（revision 变化）时清空题目决定；首次挂载不清空——store 从
// 持久化索引恢复的决定本就属于当前 revision，挂载时清空会让决定无法跨刷新保留。
watch(() => props.source.source_revision, (revision, previous) => {
  if (previous !== undefined && previous !== revision) emit('update:decisions', [])
})

// —— 左侧题目目录 + 右侧整卷连续预览 ——
const { focusedQuestionId, focusRequestToken } = useConfigQuestionFocus()
const selectedQuestionId = ref('')
const activeFilter = ref<'all' | 'attention' | 'failed' | 'bank'>('all')
const listRef = ref<HTMLElement | null>(null)
const streamRef = ref<HTMLElement | null>(null)
const expandedAnswerIds = ref(new Set<string>())
const compareQuestionId = ref<string | null>(null)

/** Questions with a visible duplicate tag, in paper order — the dialog's nav list. */
const compareItems = computed(() => (
  props.source.questions.filter((question) => duplicateById.value.has(question.question_id))
))
const compareIndex = computed(() => (
  compareItems.value.findIndex((question) => question.question_id === compareQuestionId.value)
))
const compareQuestion = computed(() => (
  compareItems.value[compareIndex.value] ?? null
))
const compareItem = computed(() => (
  compareQuestionId.value === null
    ? null
    : duplicateById.value.get(compareQuestionId.value) ?? null
))

const duplicateById = computed(() => {
  const map = new Map<string, ConfigSourceDuplicateItem>()
  for (const item of props.duplicates) {
    // same_session 是本卷自身入库的正常回链；variant 按产品要求不展示。
    if (item.kind !== 'same_session' && item.kind !== 'variant'
      && !map.has(item.question_id)) {
      map.set(item.question_id, item)
    }
  }
  return map
})

function duplicateFor(questionId: string): ConfigSourceDuplicateItem | undefined {
  return duplicateById.value.get(questionId)
}

function questionFlags(question: ConfigQuestionPreview) {
  const flags = [
    ...configQuestionFlags(question, generationStateOf(question.question_id)),
  ]
  const dup = duplicateFor(question.question_id)
  if (dup && REVIEW_DUPLICATE_KINDS.has(dup.kind)
    && !duplicateDecisionRecorded(decisionFor(question.question_id))) {
    const tag = configSourceDuplicateTag(dup)
    if (tag) flags.push(tag)
  }
  return flags
}

/** Display tags: review-worthy duplicates merge into flags; informational tags prepended. */
function questionTags(question: ConfigQuestionPreview): ConfigQuestionFlag[] {
  const dup = duplicateFor(question.question_id)
  const decision = decisionFor(question.question_id)
  const tag = dup && (duplicateDecisionRecorded(decision)
    || !REVIEW_DUPLICATE_KINDS.has(dup.kind))
    ? configSourceDuplicateTag(dup, decision)
    : null
  const flags = questionFlags(question)
  return tag ? [tag, ...flags] : flags
}

/** Card header flags: the duplicate line below carries its own tag. */
function cardFlags(question: ConfigQuestionPreview): ConfigQuestionFlag[] {
  return configQuestionFlags(question, generationStateOf(question.question_id))
}

function isBankDuplicate(question: ConfigQuestionPreview): boolean {
  const dup = duplicateFor(question.question_id)
  return dup !== undefined && BANK_DUPLICATE_KINDS.has(dup.kind)
}

function openDuplicateCompare(questionId: string): void {
  compareQuestionId.value = questionId
}

function closeDuplicateCompare(): void {
  compareQuestionId.value = null
}

function stepDuplicateCompare(delta: -1 | 1): void {
  const next = compareItems.value[compareIndex.value + delta]
  if (next) compareQuestionId.value = next.question_id
}

function applyDuplicateDecision(decision: QuestionDecision): void {
  const retained = props.decisions.filter(
    (item) => item.question_id !== decision.question_id,
  )
  const current = decisionFor(decision.question_id)
  const base: QuestionDecision = {
    ...(current ?? { question_id: decision.question_id, excluded: false }),
  }
  delete base.bank_match
  delete base.bank_question_id
  delete base.answer_confirmed
  delete base.answer_override
  emit('update:decisions', [...retained, { ...base, ...decision }])
}

function resetDuplicateDecision(questionId: string): void {
  const current = decisionFor(questionId)
  const retained = props.decisions.filter(
    (item) => item.question_id !== questionId,
  )
  if (!current) {
    if (props.decisions.length !== retained.length) emit('update:decisions', retained)
    return
  }
  const next: QuestionDecision = {
    question_id: questionId,
    excluded: current.excluded,
  }
  if (current.question_type !== undefined) next.question_type = current.question_type
  emit('update:decisions', [
    ...retained,
    ...(current.excluded || next.question_type !== undefined ? [next] : []),
  ])
}

function needsAttention(question: ConfigQuestionPreview): boolean {
  return questionFlags(question).length > 0
}

function isFailedState(question: ConfigQuestionPreview): boolean {
  return ['blocked', 'failed'].includes(stateOf(question.question_id))
    || intakeFailed(question.question_id)
}

const attentionCount = computed(() =>
  props.source.questions.filter(needsAttention).length)
const failedCount = computed(() =>
  props.source.questions.filter(isFailedState).length)
const bankCount = computed(() =>
  props.source.questions.filter(isBankDuplicate).length)

const visibleQuestions = computed(() => props.source.questions.filter((question) => {
  if (activeFilter.value === 'attention') return needsAttention(question)
  if (activeFilter.value === 'failed') return isFailedState(question)
  if (activeFilter.value === 'bank') return isBankDuplicate(question)
  return true
}))

watch(() => props.source.questions, (questions) => {
  if (!questions.some((question) => question.question_id === selectedQuestionId.value)) {
    selectedQuestionId.value = questions[0]?.question_id ?? ''
  }
}, { immediate: true })

function isAnswerExpanded(questionId: string): boolean {
  return expandedAnswerIds.value.has(questionId)
}

function toggleAnswerExpanded(questionId: string): void {
  const next = new Set(expandedAnswerIds.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedAnswerIds.value = next
}

function scrollRowIntoView(questionId: string): void {
  const rows = listRef.value?.querySelectorAll<HTMLElement>('[data-question-row]') ?? []
  for (const row of rows) {
    if (row.getAttribute('data-question-row') === questionId) {
      row.scrollIntoView({ block: 'nearest' })
      return
    }
  }
}

let spyMuteUntil = 0

function scrollCardIntoView(questionId: string, smooth = true): void {
  spyMuteUntil = Date.now() + 450
  const cards = streamRef.value?.querySelectorAll<HTMLElement>('[data-question-card]') ?? []
  for (const card of cards) {
    if (card.getAttribute('data-question-card') === questionId) {
      card.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' })
      return
    }
  }
}

function selectQuestion(questionId: string, { scroll = true } = {}): void {
  selectedQuestionId.value = questionId
  if (!scroll) return
  scrollRowIntoView(questionId)
  scrollCardIntoView(questionId)
}

watch([focusRequestToken], async () => {
  const target = focusedQuestionId.value
  if (!target) return
  if (!props.source.questions.some((question) => question.question_id === target)) return
  activeFilter.value = 'all'
  selectedQuestionId.value = target
  await nextTick()
  scrollRowIntoView(target)
  scrollCardIntoView(target)
})

function onListKeydown(event: KeyboardEvent): void {
  const key = event.key
  const forward = key === 'ArrowDown' || key === 'j' || key === 'J'
  const backward = key === 'ArrowUp' || key === 'k' || key === 'K'
  if (!forward && !backward) return
  const list = visibleQuestions.value
  if (list.length === 0) return
  event.preventDefault()
  const index = list.findIndex(
    (question) => question.question_id === selectedQuestionId.value,
  )
  const nextIndex = index < 0 ? 0
    : Math.min(list.length - 1, Math.max(0, index + (forward ? 1 : -1)))
  const next = list[nextIndex]
  if (!next) return
  selectedQuestionId.value = next.question_id
  void nextTick(() => {
    scrollRowIntoView(next.question_id)
    scrollCardIntoView(next.question_id)
  })
}

// —— 右侧连续预览的滚动联动 ——
let scrollspy: IntersectionObserver | null = null
const streamVisibleIds = new Set<string>()

function syncSelectionFromStream(): void {
  if (Date.now() < spyMuteUntil) return
  const topmost = visibleQuestions.value.find(
    (question) => streamVisibleIds.has(question.question_id),
  )
  if (topmost && topmost.question_id !== selectedQuestionId.value) {
    selectedQuestionId.value = topmost.question_id
    scrollRowIntoView(topmost.question_id)
  }
}

function observeStreamCards(): void {
  scrollspy?.disconnect()
  streamVisibleIds.clear()
  const stream = streamRef.value
  if (!stream || typeof IntersectionObserver !== 'function') return
  scrollspy = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const id = (entry.target as HTMLElement).getAttribute('data-question-card') ?? ''
      if (entry.isIntersecting) streamVisibleIds.add(id)
      else streamVisibleIds.delete(id)
    }
    syncSelectionFromStream()
  }, { root: stream, rootMargin: '-8% 0px -55% 0px' })
  stream.querySelectorAll('[data-question-card]').forEach((card) => {
    scrollspy?.observe(card)
  })
}

onMounted(() => { void nextTick(observeStreamCards) })
onBeforeUnmount(() => scrollspy?.disconnect())
watch(visibleQuestions, () => { void nextTick(observeStreamCards) })

function stemLine(question: ConfigQuestionPreview): string {
  return question.question_preview.replace(/\s+/g, ' ').trim()
}

const stemHtmlById = computed(() => new Map(props.source.questions.map(question => [
  question.question_id,
  question.rich_content?.question_blocks.find(block => block.kind !== 'table' && block.text.trim() && block.html)?.html ?? '',
])))

function assetCountFor(question: ConfigQuestionPreview): number {
  return placedAssets(question.question_id, 'question').length
    + placedAssets(question.question_id, 'answer').length
}
</script>

<template>
  <section class="question-review" aria-labelledby="question-review-title">
    <header class="question-review__bar">
      <h2 id="question-review-title">拆题结果</h2>
      <span v-if="source.questions.length" class="question-review__count">
        {{ source.questions.length }} 题
      </span>
      <div class="question-review__filters" role="group" aria-label="按问题筛选">
        <button
          type="button"
          :class="{ 'is-active': activeFilter === 'all' }"
          :aria-pressed="activeFilter === 'all'"
          @click="activeFilter = 'all'"
        >全部</button>
        <button
          type="button"
          :class="{ 'is-active': activeFilter === 'attention' }"
          :aria-pressed="activeFilter === 'attention'"
          @click="activeFilter = 'attention'"
        >需核对 {{ attentionCount }}</button>
        <button
          type="button"
          :class="{ 'is-active': activeFilter === 'failed' }"
          :aria-pressed="activeFilter === 'failed'"
          @click="activeFilter = 'failed'"
        >入库异常 {{ failedCount }}</button>
        <button
          type="button"
          :class="{ 'is-active': activeFilter === 'bank' }"
          :aria-pressed="activeFilter === 'bank'"
          @click="activeFilter = 'bank'"
        >题库已有 {{ bankCount }}</button>
      </div>
      <span
        v-if="duplicatesUnavailable"
        class="question-review__dup-offline"
      >查重暂不可用</span>
      <div v-if="ignoredAssets.length" class="question-review__ignored-tray">
        <button
          type="button"
          class="question-review__ignored-toggle"
          :aria-expanded="ignoredExpanded"
          @click="ignoredExpanded = !ignoredExpanded"
        >已忽略 {{ ignoredAssets.length }} 张</button>
      </div>
    </header>
    <ul
      v-if="ignoredAssets.length && ignoredExpanded"
      class="question-review__ignored-list"
    >
      <li
        v-for="asset in ignoredAssets"
        :key="asset.asset_id"
        class="question-review__ignored-item"
      >
        <img :src="asset.asset_url" :alt="`${asset.asset_id} 已忽略图片`">
        <div><strong>疑难图片 · {{ asset.candidate_question_ids.join(' / ') }}</strong><small>已忽略，不会发送给AI</small></div>
        <AppButton
          variant="secondary"
          class="question-review__restore-button"
          :aria-label="`恢复疑难图片 ${asset.candidate_question_ids.join(' / ')} 到待归属提醒区`"
          @click="restoreAsset(asset.asset_id)"
        >恢复</AppButton>
      </li>
    </ul>

    <p v-if="source.questions.length === 0" class="question-review__empty" role="status">
      当前来源没有可核对的题目，请更换文件后重试。
    </p>
    <div v-else class="question-review__split">
      <ol
        ref="listRef"
        class="question-review__list"
        role="listbox"
        aria-label="拆题结果列表"
        @keydown="onListKeydown"
      >
        <template v-for="question in visibleQuestions" :key="question.question_id">
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
            :class="{
              'is-exception': isFailedState(question),
              'is-selected': question.question_id === selectedQuestionId,
            }"
            :data-question-row="question.question_id"
            @dragover.prevent
            @drop.prevent="dropCandidate(question.question_id, 'question', $event)"
          >
            <button
              type="button"
              class="question-review__row-button"
              role="option"
              :aria-selected="question.question_id === selectedQuestionId"
              @click="selectQuestion(question.question_id)"
            >
              <span :class="`question-review__dot is-${stateOf(question.question_id) || 'idle'}`" aria-hidden="true">●</span>
              <strong class="question-review__id">{{ question.question_id }}</strong>
              <span
                class="question-review__type-chip"
                :class="{ 'question-review__type-chip--review': question.question_type_review_required }"
              >{{ questionTypeLabels[question.question_type as QuestionType] ?? question.question_type }}</span>
              <QuestionHtmlBlock class="question-review__stem" :title="stemLine(question)"
                :html="stemHtmlById.get(question.question_id)" :text="stemLine(question)" inline typeset-text />
              <QuestionHtmlBlock class="question-review__answer-mini" :title="question.answer_preview"
                :text="question.answer_preview" inline />
              <span
                v-for="flag in questionTags(question).slice(0, 1)"
                :key="flag.label"
                class="question-review__flag"
                :class="flag.tone ? `question-review__flag--${flag.tone}` : ''"
                :title="flag.title"
              >{{ flag.label }}</span>
              <span
                v-if="questionTags(question).length > 1"
                class="question-review__flag-more"
                :title="questionTags(question).slice(1).map((flag) => `${flag.label}：${flag.title}`).join('\n')"
              >+{{ questionTags(question).length - 1 }}</span>
              <span
                v-if="assetCountFor(question) > 0"
                class="question-review__asset-count"
                :title="`${assetCountFor(question)} 张图片`"
              >图{{ assetCountFor(question) }}</span>
            </button>
          </li>
        </template>
      </ol>

      <div ref="streamRef" class="question-review__stream" aria-label="整卷预览">
        <template v-for="question in visibleQuestions" :key="question.question_id">
          <div
            v-for="candidate in candidatesBefore(question.question_id)"
            :key="candidate.asset_id"
            class="question-review__asset-between question-review__asset-between--stream"
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
          </div>
          <article
            class="question-review__card"
            :class="{ 'is-focused': question.question_id === selectedQuestionId }"
            :data-question-card="question.question_id"
          >
            <header class="question-review__detail-head">
              <strong class="question-review__id">{{ question.question_id }}</strong>
              <span
                class="question-review__type-chip"
                :title="localTypeNote(question)"
              >{{ questionTypeLabels[question.question_type as QuestionType] ?? question.question_type }}</span>
              <span v-if="decisionFor(question.question_id)?.question_type" class="question-review__local-type">
                {{ localTypeNote(question) }}
              </span>
              <span
                v-for="flag in cardFlags(question).slice(0, 1)"
                :key="flag.label"
                class="question-review__flag"
                :class="flag.tone ? `question-review__flag--${flag.tone}` : ''"
                :title="flag.title"
              >{{ flag.label }}</span>
              <span
                v-if="cardFlags(question).length > 1"
                class="question-review__flag-more"
                :title="cardFlags(question).slice(1).map((flag) => `${flag.label}：${flag.title}`).join('\n')"
              >+{{ cardFlags(question).length - 1 }}</span>
            </header>
            <div
              v-if="duplicateFor(question.question_id)"
              class="question-review__dup-line"
            >
              <span
                class="question-review__flag"
                :class="`question-review__flag--${configSourceDuplicateTag(duplicateFor(question.question_id)!, decisionFor(question.question_id))?.tone ?? 'warning'}`"
                :title="configSourceDuplicateTag(duplicateFor(question.question_id)!, decisionFor(question.question_id))?.title"
              >{{ configSourceDuplicateTag(duplicateFor(question.question_id)!, decisionFor(question.question_id))?.label }}</span>
              <span
                class="question-review__dup-meta"
                :title="`《${duplicateFor(question.question_id)!.matched_paper_title}》${duplicateFor(question.question_id)!.matched_question_number ? `第${duplicateFor(question.question_id)!.matched_question_number}题` : ''}`"
              >《{{ duplicateFor(question.question_id)!.matched_paper_title }}》<template v-if="duplicateFor(question.question_id)!.matched_question_number">第{{ duplicateFor(question.question_id)!.matched_question_number }}题</template></span>
              <AppButton
                variant="ghost"
                class="question-review__dup-compare"
                @click="openDuplicateCompare(question.question_id)"
              >对照</AppButton>
            </div>
            <p v-if="intakeFailed(question.question_id)" class="question-review__type-check" role="alert">
              <strong>本题入库未完成：</strong>AI 分析已保留，请核对题目边界和配图归属；完整入库后才能赋分。
            </p>
            <p
              v-for="warning in question.parse_warnings ?? []"
              :key="warning"
              class="question-review__type-check"
              role="status"
            >
              <span><strong>来源需核对：</strong>{{ warning }}</span>
            </p>

            <label v-if="question.question_type_review_required" class="question-review__type-check">
              <span><strong>题型建议（可选修改）</strong>{{ question.question_type_review_reason || '题面形式与解析内容存在冲突。' }} 不修改时将沿用系统建议，不会阻塞 AI 生成。</span>
              <select class="app-input" :value="decisionFor(question.question_id)?.question_type ?? ''" @change="confirmQuestionType(question.question_id, $event)">
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
                <header><strong>题目</strong></header>
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
                <header><strong>答案</strong><span class="question-review__answer-status">{{ answerStatus(question) }}</span></header>
                <div
                  class="question-review__answer-flow"
                  :class="{ 'is-clamped': !isAnswerExpanded(question.question_id) }"
                  :data-answer-content="question.question_id"
                >
                  <QuestionContentRenderer
                    :blocks="textBlocks(question, 'answer')"
                    :fallback="question.answer_preview"
                    empty-label="答案内容暂未识别。"
                    media-mode="review"
                    dense
                  />
                </div>
                <div class="question-review__answer-actions">
                  <button
                    type="button"
                    class="question-review__answer-expand"
                    @click="toggleAnswerExpanded(question.question_id)"
                  >
                    {{ isAnswerExpanded(question.question_id) ? '收起' : '展开' }}
                  </button>
                  <button
                    type="button"
                    class="question-review__answer-expand"
                    @click="openFullAnswer(question.question_id)"
                  >
                    查看完整答案
                  </button>
                </div>
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
          </article>
        </template>
      </div>
    </div>

    <Sheet
      :open="expandedAnswer !== undefined"
      @update:open="(value: boolean) => { if (!value) closeFullAnswer() }"
    >
      <SheetContent
        class="question-review__answer-drawer"
        :aria-describedby="undefined"
      >
        <template v-if="expandedAnswer">
          <SheetHeader class="question-review__answer-head">
            <small>{{ expandedAnswer.question_id }}</small>
            <SheetTitle>完整答案</SheetTitle>
          </SheetHeader>
          <QuestionContentRenderer
            :blocks="textBlocks(expandedAnswer, 'answer')"
            :fallback="expandedAnswer.answer_preview"
            empty-label="答案内容暂未识别。"
            media-mode="review"
          />
        </template>
      </SheetContent>
    </Sheet>

    <DuplicateCompareDialog
      :item="compareItem"
      :question="compareQuestion"
      :question-blocks="compareQuestion ? textBlocks(compareQuestion, 'question') : []"
      :question-assets="compareQuestion ? placedAssets(compareQuestion.question_id, 'question') : []"
      :answer-assets="compareQuestion ? placedAssets(compareQuestion.question_id, 'answer') : []"
      :answer-blocks="compareQuestion ? textBlocks(compareQuestion, 'answer') : []"
      :decision="compareQuestion ? decisionFor(compareQuestion.question_id) : undefined"
      :index="Math.max(compareIndex, 0)"
      :total="compareItems.length"
      @close="closeDuplicateCompare"
      @prev="stepDuplicateCompare(-1)"
      @next="stepDuplicateCompare(1)"
      @decide="applyDuplicateDecision"
      @reset="compareQuestion && resetDuplicateDecision(compareQuestion.question_id)"
    />
  </section>
</template>

<style scoped>
.question-review__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-block-end: var(--border-width) solid var(--color-border-default);
}

.question-review__bar h2 {
  margin: 0;
  font-size: var(--font-size-h3);
  line-height: var(--line-height-tight);
}

.question-review__filters {
  display: inline-flex;
  gap: var(--space-1);
  padding: 2px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.question-review__filters button {
  min-height: var(--control-height-small);
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-tag);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.question-review__filters button.is-active {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
  box-shadow: var(--shadow-raised);
}

.question-review__ignored-tray {
  margin-inline-start: auto;
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
  min-height: var(--control-height-default);
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
.question-review__split {
  display: grid;
  grid-template-columns: minmax(320px, 400px) minmax(0, 1fr);
  align-items: start;
  min-height: 0;
}

.question-review__list {
  position: sticky;
  top: 0;
  display: grid;
  align-content: start;
  gap: 0;
  max-height: calc(100vh - 120px);
  margin: 0;
  padding: 0;
  overflow: auto;
  border: 0;
  border-inline-end: var(--border-width) solid var(--color-border-default);
  list-style: none;
}

.question-review__row {
  position: relative;
  min-width: 0;
  border-block-end: var(--border-width) solid var(--color-border-subtle);
}

.question-review__row-button {
  display: grid;
  width: 100%;
  min-height: 40px;
  align-items: center;
  grid-template-columns: auto auto auto minmax(0, 1fr) auto auto;
  gap: var(--space-2);
  padding: 0 var(--space-3) 0 var(--space-2);
  border: 0;
  border-inline-start: 3px solid transparent;
  background: transparent;
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
  text-align: start;
  cursor: pointer;
}

.question-review__row-button:hover { background: var(--color-bg-subtle); }
.question-review__row-button:focus-visible {
  outline: none;
  box-shadow: inset var(--focus-ring);
}

.question-review__row.is-selected .question-review__row-button {
  border-inline-start-color: var(--color-accent);
  background: var(--color-accent-subtle);
}

.question-review__dot { font-size: var(--font-size-caption); }
.question-review__dot.is-passed { color: var(--color-success); }
.question-review__dot.is-blocked,
.question-review__dot.is-failed { color: var(--color-danger); }
.question-review__dot.is-pending,
.question-review__dot.is-running,
.question-review__dot.is-idle { color: var(--color-text-muted); }

.question-review__id { font-size: var(--font-size-dense); }

.question-review__type-chip {
  max-width: 88px;
  padding: 1px var(--space-2);
  overflow: hidden;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-tag);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.question-review__type-chip--review {
  border-color: var(--color-warning);
  color: var(--color-warning);
}

.question-review__stem {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.question-review__answer-mini {
  max-width: 72px;
  overflow: hidden;
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.question-review__warn { color: var(--color-warning); font-size: var(--font-size-caption); }
.question-review__asset-count { color: var(--color-text-muted); font-size: var(--font-size-caption); }

.question-review__flag {
  flex: 0 0 auto;
  max-width: 96px;
  padding: 1px var(--space-2);
  overflow: hidden;
  border: var(--border-width) solid color-mix(in srgb, var(--color-warning) 55%, transparent);
  border-radius: var(--radius-tag);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.question-review__flag-more {
  flex: 0 0 auto;
  color: var(--color-warning);
  font-size: var(--font-size-caption);
}

.question-review__dup-offline {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.question-review__flag--neutral {
  border-color: var(--color-border-default);
  background: var(--secondary);
  color: var(--color-text-secondary);
}

.question-review__flag--info {
  border-color: color-mix(in srgb, var(--color-accent) 45%, transparent);
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}

.question-review__dup-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-block-start: var(--space-1);
  font-size: var(--font-size-caption);
}

.question-review__dup-meta {
  overflow: hidden;
  color: var(--color-text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.question-review__dup-compare {
  border: none;
  background: none;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  cursor: pointer;
}
.question-review__dup-compare:hover { text-decoration: underline; }
.question-review__dup-compare:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.question-review__stream {
  position: sticky;
  top: 0;
  min-width: 0;
  max-height: calc(100vh - 120px);
  overflow: auto;
  padding: var(--space-3) var(--space-4);
  scroll-padding-top: var(--space-3);
}

.question-review__card {
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.question-review__card + .question-review__card,
.question-review__card + .question-review__asset-between--stream,
.question-review__asset-between--stream + .question-review__card {
  margin-block-start: var(--space-3);
}
.question-review__card.is-focused {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.question-review__detail-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-block-end: var(--space-2);
}

.question-review__detail-head .question-review__id {
  font-size: var(--font-size-h3);
}

.question-review__asset-between { position: relative; display: grid; grid-template-columns: 56px minmax(0, 1fr); align-items: center; gap: var(--space-2); padding: var(--space-2) 44px var(--space-2) var(--space-2); border-block-end: 1px solid var(--color-warning); background: var(--color-warning-subtle); }
.question-review__asset-between--stream {
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-control);
}
.question-review__asset-between img { width: 56px; height: 44px; object-fit: contain; cursor: grab; }
.question-review__asset-between small { display: block; color: var(--color-text-secondary); }

.question-review__ignore-button {
  position: absolute;
  inset-block-start: var(--space-2);
  inset-inline-end: var(--space-2);
  display: grid;
  width: 34px;
  height: var(--control-height-default);
  place-items: center;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--color-text-secondary) 35%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, var(--card) 76%, transparent);
  color: var(--color-text-secondary);
  font-size: var(--font-size-h1);
  line-height: 1;
  cursor: pointer;
  backdrop-filter: blur(3px);
}
.question-review__ignore-button:hover,
.question-review__ignore-button:focus-visible { border-color: var(--color-danger); color: var(--color-danger); }
.question-review__ignore-button:focus-visible { outline: 2px solid var(--color-danger); outline-offset: 2px; }
.question-review__ignore-button[aria-pressed="true"] { border-color: var(--color-danger); background: var(--color-danger); color: var(--destructive-foreground); }

.question-review__row.is-exception .question-review__row-button {
  background: color-mix(in srgb, var(--color-danger-subtle) 45%, var(--color-bg-surface));
}
.question-review__local-type {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}
.question-review__type-check { display: grid; grid-template-columns: minmax(0, 1fr) minmax(180px, 240px); align-items: center; gap: var(--space-3); margin-block: var(--space-2); padding: var(--space-2) var(--space-3); border-inline-start: 3px solid var(--color-warning); background: var(--color-warning-subtle); }
.question-review__type-check > span { display: grid; gap: 2px; color: var(--color-text-secondary); font-size: var(--font-size-caption); }
.question-review__type-check strong { color: var(--color-warning); }
.question-review__type-check select { width: 100%; }
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
  gap: var(--space-2);
  min-width: 0;
  overflow: visible;
  padding: var(--space-3);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.question-review__paper-panel > [data-question-content] {
  min-width: 0;
  max-width: 100%;
}

.question-review__paper-panel--answer { background: var(--secondary); }

.question-review__answer-flow {
  position: relative;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
}

.question-review__answer-flow.is-clamped {
  max-height: 11.2em;
  overflow: hidden;
}

.question-review__answer-flow.is-clamped::after {
  position: absolute;
  inset-inline: 0;
  inset-block-end: 0;
  height: 2.4em;
  background: linear-gradient(to bottom, transparent, var(--secondary));
  content: '';
  pointer-events: none;
}

.question-review__answer-actions {
  display: flex;
  gap: var(--space-2);
}

.question-review__answer-expand {
  align-self: flex-start;
  min-height: var(--control-height-default);
  padding-inline: 8px;
  border: 0;
  background: transparent;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
}

.question-review__answer-expand:hover { background: color-mix(in srgb, var(--color-accent) 8%, transparent); }

.question-review__answer-drawer {
  width: min(720px, 100%);
  max-width: none;
  overflow: auto;
}

.question-review__answer-head {
  gap: var(--space-1);
}

.question-review__answer-drawer small { margin: 0; color: var(--color-text-secondary); }

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
  min-height: var(--control-height-default);
}

.question-review__image-well > span {
  align-self: center;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

@media (max-width: 900px) {
  .question-review__type-check { grid-template-columns: minmax(0, 1fr); }
  .question-review__pair { grid-template-columns: minmax(0, 1fr); }
}

@media (max-width: 1100px) {
  .question-review__split { grid-template-columns: minmax(0, 1fr); }
  .question-review__list {
    position: static;
    max-height: 320px;
    border-inline-end: 0;
    border-block-end: var(--border-width) solid var(--color-border-default);
  }
  .question-review__stream {
    position: static;
    max-height: 60vh;
  }
}

@media (max-width: 520px) {
  .question-review__image-well[data-image-count="4"] {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
