<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import {
  questionBankApi,
  type QuestionBankDetail,
} from '../../api/question-bank'
import {
  TAXONOMY_DIMENSIONS,
  type TaxonomyDimension,
  type TaxonomyBatchManualDecision,
  type TaxonomyProposal,
  type TaxonomySuggestion,
  type TaxonomyTerm,
} from '../../api/question-bank-taxonomy'
import { useTaxonomyReviewStore } from '../../stores/taxonomy-review'
import QuestionContentRenderer from './QuestionContentRenderer.vue'

interface CandidateDraft {
  editedName: string
  mergeSearch: string
  targetTermIds: string[]
  selectedQuestionIds: number[]
  adoptedSuggestion: TaxonomySuggestion['relation_kind'] | null
  approvalExpanded: boolean
}

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const store = useTaxonomyReviewStore()
const closeButton = ref<HTMLButtonElement | null>(null)
const activeDimension = ref<'all' | TaxonomyDimension>('all')
const drafts = reactive<Record<string, CandidateDraft>>({})
const previewQuestion = ref<QuestionBankDetail | null>(null)
const previewQuestionId = ref<number | null>(null)
const previewState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const previewReturnFocus = ref<HTMLElement | null>(null)
const previewCloseButton = ref<HTMLButtonElement | null>(null)
let previewController: AbortController | null = null
let suggestionTimer: ReturnType<typeof setTimeout> | null = null

const dimensionLabels: Record<TaxonomyDimension, string> = {
  curriculum: '教材归属',
  knowledge: '知识点',
  ability: '能力',
  method: '解题方法',
  thought: '数学思想',
  model: '数学模型',
  special_type: '特殊题型/考法',
}

const pendingProposals = computed(() => (
  store.proposals.filter(({ status, actionable }) => (
    status === 'pending' && actionable
  ))
))

const batchDrafts = reactive<Record<string, { decision: TaxonomyBatchManualDecision['decision']; targetTermId: string }>>({})
const manualBatchItems = computed(() => store.batchPreview?.items.filter((item) => !item.automatic) ?? [])
const automaticBatchItems = computed(() => store.batchPreview?.items.filter((item) => item.automatic) ?? [])

const suggestionIsActive = computed(() => (
  store.suggestionRun !== null
  && ['queued', 'running', 'cancelling'].includes(store.suggestionRun.status)
))

const suggestionProgressLabel = computed(() => {
  const run = store.suggestionRun
  if (!run) return ''
  const {
    total,
    processed,
    completed,
    failed,
    pending,
    cancelled,
  } = run.progress
  if (run.stale) return '词表已变化，这批建议已失效'
  const details = [
    `已生成建议 ${completed}`,
    failed ? `失败 ${failed}` : '',
    pending ? `待处理 ${pending}` : '',
    cancelled ? `未继续 ${cancelled}` : '',
  ].filter(Boolean).join('，')
  return `已处理 ${processed}/${total}，${details}`
})

const dimensionCounts = computed<Record<TaxonomyDimension, number>>(() => (
  Object.fromEntries(TAXONOMY_DIMENSIONS.map((key) => [
    key,
    pendingProposals.value.filter(({ dimension }) => dimension === key).length,
  ])) as Record<TaxonomyDimension, number>
))

const visibleGroups = computed(() => TAXONOMY_DIMENSIONS
  .filter((dimension) => (
    activeDimension.value === 'all' || activeDimension.value === dimension
  ))
  .map((dimension) => ({
    dimension,
    label: dimensionLabels[dimension],
    items: pendingProposals.value.filter((item) => item.dimension === dimension),
  }))
  .filter(({ items }) => items.length > 0))

watch(
  () => store.proposals,
  (items) => {
    for (const proposal of items) {
      if (!drafts[proposal.id]) {
        drafts[proposal.id] = {
          editedName: proposal.edited_name || proposal.proposed_name,
          mergeSearch: '',
          targetTermIds: proposal.nearest_id ? [proposal.nearest_id] : [],
          selectedQuestionIds: [...proposal.question_refs],
          adoptedSuggestion: null,
          approvalExpanded: false,
        }
      }
    }
  },
  { deep: true, immediate: true },
)

watch(
  () => props.open,
  (open) => {
    if (!open) {
      closeQuestionPreview()
      clearSuggestionTimer()
      return
    }
    if (store.loadState !== 'loading' && store.writeState !== 'saving') void store.load()
    scheduleSuggestionRefresh()
    void nextTick(() => closeButton.value?.focus())
  },
)

watch(
  () => store.suggestionRun?.status,
  () => scheduleSuggestionRefresh(),
)

function draftFor(proposal: TaxonomyProposal): CandidateDraft {
  const existing = drafts[proposal.id]
  if (existing) return existing
  const created = {
    editedName: proposal.edited_name || proposal.proposed_name,
    mergeSearch: '',
    targetTermIds: proposal.nearest_id ? [proposal.nearest_id] : [],
    selectedQuestionIds: [...proposal.question_refs],
    adoptedSuggestion: null,
    approvalExpanded: false,
  }
  drafts[proposal.id] = created
  return created
}

function termFor(proposal: TaxonomyProposal, termId: string | null): TaxonomyTerm | null {
  if (!termId) return null
  return store.termsFor(proposal.dimension).find(({ id }) => id === termId) ?? null
}

function matchingTerms(proposal: TaxonomyProposal): TaxonomyTerm[] {
  const draft = draftFor(proposal)
  const search = draft.mergeSearch.trim().toLocaleLowerCase()
  const selected = draft.targetTermIds
    .map((termId) => termFor(proposal, termId))
    .filter((term): term is TaxonomyTerm => term !== null)
  const matches = store.termsFor(proposal.dimension)
    .filter((term) => {
      if (!search) return true
      return [term.name, ...term.aliases].some(
        (value) => value.toLocaleLowerCase().includes(search),
      )
    })
    .slice(0, 30)
  for (const term of selected.reverse()) {
    if (!matches.some(({ id }) => id === term.id)) matches.unshift(term)
  }
  return matches
}

function selectedTerms(proposal: TaxonomyProposal): TaxonomyTerm[] {
  return draftFor(proposal).targetTermIds
    .map((termId) => termFor(proposal, termId))
    .filter((term): term is TaxonomyTerm => term !== null)
}

function formatDate(value: string | null): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

async function approve(proposal: TaxonomyProposal): Promise<void> {
  if (await store.review(proposal.id, {
    decision: 'approve',
    question_ids: draftFor(proposal).selectedQuestionIds,
  })) {
    delete drafts[proposal.id]
  }
}

async function approveEdited(proposal: TaxonomyProposal): Promise<void> {
  const editedName = draftFor(proposal).editedName.trim()
  if (!editedName) return
  if (await store.review(proposal.id, {
    decision: 'edit',
    edited_name: editedName,
    question_ids: draftFor(proposal).selectedQuestionIds,
  })) {
    delete drafts[proposal.id]
  }
}

async function merge(proposal: TaxonomyProposal): Promise<void> {
  const draft = draftFor(proposal)
  const targetTermIds = draft.targetTermIds.filter(
    (termId) => termFor(proposal, termId) !== null,
  )
  if (targetTermIds.length === 0) return
  if (await store.review(proposal.id, {
    decision: 'merge',
    target_term_id: targetTermIds.length === 1 ? targetTermIds[0] : undefined,
    target_term_ids: targetTermIds,
    question_ids: draft.selectedQuestionIds,
  })) {
    delete drafts[proposal.id]
  }
}

async function reject(proposal: TaxonomyProposal): Promise<void> {
  const confirmed = window.confirm(
    `确认拒绝“${proposal.proposed_name}”吗？拒绝后它不会进入正式词表。`,
  )
  if (!confirmed) return
  if (await store.review(proposal.id, {
    decision: 'reject',
    question_ids: draftFor(proposal).selectedQuestionIds,
  })) {
    delete drafts[proposal.id]
  }
}

function useNearest(proposal: TaxonomyProposal): void {
  if (!proposal.nearest_id) return
  const draft = draftFor(proposal)
  draft.targetTermIds = [proposal.nearest_id]
  const term = termFor(proposal, proposal.nearest_id)
  if (term) draft.mergeSearch = term.name
}

function toggleQuestion(
  proposal: TaxonomyProposal,
  questionId: number,
  selected: boolean,
): void {
  const draft = draftFor(proposal)
  draft.selectedQuestionIds = selected
    ? [...new Set([...draft.selectedQuestionIds, questionId])]
    : draft.selectedQuestionIds.filter((item) => item !== questionId)
}

function adoptSuggestion(proposal: TaxonomyProposal): void {
  const item = store.suggestionFor(proposal.id)
  const suggestion = item?.suggestion
  if (!suggestion || store.suggestionRun?.stale) return
  if (!['exact', 'new_core_candidate'].includes(suggestion.relation_kind)) return
  const draft = draftFor(proposal)
  draft.adoptedSuggestion = suggestion.relation_kind
  if (suggestion.relation_kind === 'exact') {
    draft.targetTermIds = suggestion.target_term_ids.filter(
      (termId) => termFor(proposal, termId) !== null,
    )
    draft.mergeSearch = ''
  } else if (suggestion.relation_kind === 'new_core_candidate') {
    draft.editedName = proposal.proposed_name
    draft.approvalExpanded = true
  }
}

function suggestionLabel(suggestion: TaxonomySuggestion): string {
  return {
    exact: '严格同义',
    broader: '候选范围更宽',
    narrower: '候选范围更细',
    related: '相关但不同义',
    new_core_candidate: '可能是缺失的新词',
    wrong_dimension: '标签维度不合适',
    reject: '不纳入规范词表',
    uncertain: '证据不足，建议人工判断',
  }[suggestion.relation_kind]
}

function suggestionCanPrefill(suggestion: TaxonomySuggestion): boolean {
  return ['exact', 'new_core_candidate'].includes(suggestion.relation_kind)
}

function suggestionActionLabel(proposal: TaxonomyProposal): string {
  const draft = draftFor(proposal)
  if (!draft.adoptedSuggestion) return '采用并预填'
  return draft.adoptedSuggestion === 'new_core_candidate'
    ? '已预填规范名，仍需确认'
    : '已预填归并目标，仍需确认'
}

function suggestionManualHint(suggestion: TaxonomySuggestion): string {
  return suggestion.relation_kind === 'reject'
    ? '如你认同，请在卡片底部人工点击“拒绝这个新词”。'
    : '这项没有可自动预填的结论，请结合题目预览人工判断。'
}

async function startSuggestions(): Promise<void> {
  const started = await store.startSuggestions(
    pendingProposals.value.slice(0, 200).map(({ id }) => id),
  )
  if (started) scheduleSuggestionRefresh()
}

function batchDraft(proposalId: string, targetIds: string[]) {
  batchDrafts[proposalId] ??= {
    decision: 'defer',
    targetTermId: targetIds[0] ?? '',
  }
  return batchDrafts[proposalId]!
}

function batchTerms(dimension: TaxonomyDimension): TaxonomyTerm[] {
  return store.termsFor(dimension)
}

async function saveBatch(): Promise<void> {
  const decisions: TaxonomyBatchManualDecision[] = manualBatchItems.value.map((item) => {
    const draft = batchDraft(item.proposal_id, item.suggestion.target_term_ids)
    return {
      proposal_id: item.proposal_id,
      decision: draft.decision,
      target_term_ids: draft.decision === 'merge' && draft.targetTermId
        ? [draft.targetTermId]
        : [],
    }
  })
  await store.applySuggestionBatch(decisions)
}

function clearSuggestionTimer(): void {
  if (suggestionTimer !== null) {
    clearTimeout(suggestionTimer)
    suggestionTimer = null
  }
}

function scheduleSuggestionRefresh(): void {
  clearSuggestionTimer()
  if (!props.open || !suggestionIsActive.value) return
  suggestionTimer = setTimeout(async () => {
    suggestionTimer = null
    await store.refreshSuggestions()
    scheduleSuggestionRefresh()
  }, 1200)
}

async function openQuestionPreview(
  questionId: number,
  event: MouseEvent,
): Promise<void> {
  previewReturnFocus.value = event.currentTarget as HTMLElement
  previewQuestionId.value = questionId
  await loadQuestionPreview(questionId)
}

async function loadQuestionPreview(questionId: number): Promise<void> {
  previewController?.abort()
  const controller = new AbortController()
  previewController = controller
  previewQuestion.value = null
  previewState.value = 'loading'
  void nextTick(() => previewCloseButton.value?.focus())
  try {
    previewQuestion.value = await questionBankApi.getQuestion(
      questionId,
      controller.signal,
    )
    if (previewController !== controller) return
    previewState.value = 'ready'
  } catch {
    if (!controller.signal.aborted && previewController === controller) {
      previewState.value = 'error'
    }
  }
}

async function retryQuestionPreview(): Promise<void> {
  if (previewQuestionId.value) {
    await loadQuestionPreview(previewQuestionId.value)
  }
}

function closeQuestionPreview(): void {
  const returnTarget = previewReturnFocus.value
  previewController?.abort()
  previewController = null
  previewQuestion.value = null
  previewQuestionId.value = null
  previewState.value = 'idle'
  previewReturnFocus.value = null
  if (returnTarget) void nextTick(() => returnTarget.focus())
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open || event.key !== 'Escape') return
  if (previewState.value !== 'idle') closeQuestionPreview()
  else emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  previewController?.abort()
  clearSuggestionTimer()
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="qb-drawer-layer taxonomy-review-layer"
      role="presentation"
      @click.self="emit('close')"
    >
      <aside
        class="taxonomy-review"
        role="dialog"
        aria-modal="true"
        aria-labelledby="taxonomy-review-title"
      >
        <header class="taxonomy-review__header">
          <div>
            <p class="qb-eyebrow">CONTROLLED VOCABULARY</p>
            <h2 id="taxonomy-review-title">审核 AI 新造词</h2>
            <p>这些词尚未进入正式词表，也不会自动写入题目。请逐个批准、修改、合并或拒绝。</p>
          </div>
          <button
            ref="closeButton"
            type="button"
            class="qb-drawer-close"
            aria-label="关闭新词审核"
            @click="emit('close')"
          >
            ×
          </button>
        </header>

        <div class="taxonomy-review__toolbar">
          <div class="taxonomy-review__summary">
            <strong>{{ store.pendingCount }}</strong>
            <span>个词等待教师确认</span>
            <small>词表版本 {{ store.revision }}</small>
            <small v-if="store.pendingCount < 30">
              建议积累到 30—50 个候选或月底再集中请 AI 判断
            </small>
            <small v-else>
              已达到集中治理建议数量；单批最多处理 200 个
            </small>
          </div>
          <div class="taxonomy-review__toolbar-actions">
            <button
              v-if="!suggestionIsActive"
              type="button"
              class="qb-button is-primary"
              :disabled="
                pendingProposals.length === 0
                || store.suggestionState === 'starting'
              "
              @click="startSuggestions"
            >
              {{ store.suggestionState === 'starting' ? '正在启动…' : '请 AI 一键判断' }}
            </button>
            <button
              v-else
              type="button"
              class="qb-button"
              @click="store.cancelSuggestions()"
            >
              停止未开始项
            </button>
            <button
              v-if="store.suggestionRun?.retryable"
              type="button"
              class="qb-button"
              :disabled="store.suggestionRun.stale"
              @click="store.retrySuggestions()"
            >
              继续未完成项
            </button>
            <button
              type="button"
              class="qb-button is-quiet"
              :disabled="store.loadState === 'loading' || store.writeState === 'saving'"
              @click="store.load()"
            >
              {{ store.loadState === 'loading' ? '正在读取…' : '刷新候选' }}
            </button>
          </div>
        </div>

        <p class="taxonomy-review__standard-summary" role="status">
          当前知识标准 · 词表修订 {{ store.revision }} ·
          {{ Object.values(store.dimensions).reduce((total, terms) => total + terms.length, 0) }} 个活动规范词
        </p>

        <section
          v-if="store.suggestionRun || store.suggestionMessage"
          class="taxonomy-review__suggestion-progress"
          aria-live="polite"
        >
          <div>
            <strong>AI 归并建议</strong>
            <span>{{ suggestionProgressLabel || store.suggestionMessage }}</span>
          </div>
          <progress
            v-if="store.suggestionRun"
            :max="Math.max(1, store.suggestionRun.progress.total)"
            :value="store.suggestionRun.progress.processed"
          />
          <p>{{ store.suggestionMessage }}</p>
        </section>

        <section v-if="store.batchPreview" class="taxonomy-ai-results" aria-labelledby="taxonomy-ai-results-title">
          <header>
            <div>
              <p class="qb-eyebrow">AI 归并结果</p>
              <h3 id="taxonomy-ai-results-title">先核对疑难项，再一次保存</h3>
            </div>
            <p>
              自动归并 {{ store.batchPreview.counts.automatic }} ｜
              需人工 {{ store.batchPreview.counts.manual }}
            </p>
          </header>

          <div class="taxonomy-ai-results__manual">
            <h4>需人工确认</h4>
            <article v-for="item in manualBatchItems" :key="item.proposal_id">
              <div>
                <strong>{{ item.proposed_name }}</strong>
                <span>{{ suggestionLabel(item.suggestion) }} · {{ Math.round(item.suggestion.confidence * 100) }}%</span>
                <p>{{ item.suggestion.reason }}</p>
              </div>
              <label>
                <span>本批决定</span>
                <select v-model="batchDraft(item.proposal_id, item.suggestion.target_term_ids).decision">
                  <option value="defer">暂缓</option>
                  <option value="merge">归并到现有词</option>
                  <option value="approve">保留为新词</option>
                  <option value="reject">拒绝</option>
                </select>
              </label>
              <label v-if="batchDraft(item.proposal_id, item.suggestion.target_term_ids).decision === 'merge'">
                <span>目标词</span>
                <select v-model="batchDraft(item.proposal_id, item.suggestion.target_term_ids).targetTermId">
                  <option value="">请选择</option>
                  <option v-for="term in batchTerms(item.dimension)" :key="term.id" :value="term.id">
                    {{ term.name }}
                  </option>
                </select>
              </label>
              <details>
                <summary>查看证据</summary>
                <button
                  v-for="questionId in item.question_ids"
                  :key="questionId"
                  type="button"
                  class="taxonomy-question-link"
                  @click="openQuestionPreview(questionId, $event)"
                >
                  题目 #{{ questionId }}
                </button>
              </details>
            </article>
          </div>

          <details class="taxonomy-ai-results__automatic">
            <summary>已自动处理（{{ automaticBatchItems.length }}，默认折叠）</summary>
            <article v-for="item in automaticBatchItems" :key="item.proposal_id">
              <strong>{{ item.proposed_name }}</strong>
              <span>{{ suggestionLabel(item.suggestion) }} · {{ Math.round(item.suggestion.confidence * 100) }}%</span>
              <p>{{ item.suggestion.reason }}</p>
            </article>
          </details>

          <footer>
            <p aria-live="polite">{{ store.batchMessage }}</p>
            <button type="button" class="qb-button" @click="store.batchPreview = null">返回候选</button>
            <button
              v-if="store.reviewOperation?.undo_status === 'available'"
              type="button"
              class="qb-button"
              @click="store.undoLastBatch()"
            >
              撤销上一批
            </button>
            <button
              type="button"
              class="qb-button is-primary"
              :disabled="store.batchState === 'saving'"
              @click="saveBatch"
            >
              {{ store.batchState === 'saving' ? '正在保存…' : '保存本批决定' }}
            </button>
          </footer>
        </section>

        <nav v-if="!store.batchPreview" class="taxonomy-review__dimensions" aria-label="按标签维度筛选">
          <button
            type="button"
            :class="{ 'is-active': activeDimension === 'all' }"
            @click="activeDimension = 'all'"
          >
            全部 <span>{{ store.pendingCount }}</span>
          </button>
          <button
            v-for="dimension in TAXONOMY_DIMENSIONS"
            :key="dimension"
            type="button"
            :class="{ 'is-active': activeDimension === dimension }"
            @click="activeDimension = dimension"
          >
            {{ dimensionLabels[dimension] }}
            <span>{{ dimensionCounts[dimension] }}</span>
          </button>
        </nav>

        <p
          v-if="store.message"
          class="qb-feedback taxonomy-review__feedback"
          :class="{
            'is-warning': store.writeState === 'conflict',
            'is-error': store.writeState === 'error',
          }"
          role="status"
        >
          {{ store.message }}
        </p>

        <div v-if="!store.batchPreview && store.loadState === 'loading' && !store.proposals.length" class="taxonomy-review__state">
          正在读取新词候选…
        </div>
        <div v-else-if="!store.batchPreview && store.loadState === 'error' && !store.proposals.length" class="taxonomy-review__state is-error">
          <strong>候选清单暂时无法读取</strong>
          <p>可以保留当前窗口，稍后重新读取。</p>
          <button type="button" class="qb-button" @click="store.load()">重新读取</button>
        </div>
        <div v-else-if="!store.batchPreview && (store.loadState === 'empty' || pendingProposals.length === 0)" class="taxonomy-review__state">
          <strong>当前没有待审核新词</strong>
          <p>AI 继续使用现有规范词；以后出现新候选时会在这里集中显示。</p>
        </div>
        <div v-else-if="!store.batchPreview && visibleGroups.length === 0" class="taxonomy-review__state">
          <strong>这个维度暂时没有候选</strong>
          <button type="button" class="qb-link" @click="activeDimension = 'all'">查看全部候选</button>
        </div>

        <div v-else-if="!store.batchPreview" class="taxonomy-review__groups">
          <section
            v-for="group in visibleGroups"
            :key="group.dimension"
            class="taxonomy-review__group"
            :aria-labelledby="`taxonomy-group-${group.dimension}`"
          >
            <header>
              <h3 :id="`taxonomy-group-${group.dimension}`">{{ group.label }}</h3>
              <span>{{ group.items.length }} 个待确认</span>
            </header>

            <article
              v-for="proposal in group.items"
              :key="proposal.id"
              class="taxonomy-candidate"
              :class="{ 'is-busy': store.busyProposalId === proposal.id }"
            >
              <div class="taxonomy-candidate__identity">
                <span class="taxonomy-candidate__flag">AI 新造词</span>
                <h4>{{ proposal.proposed_name }}</h4>
                <time>{{ formatDate(proposal.created_at) }}</time>
              </div>

              <div
                v-if="store.proposalErrors[proposal.id]"
                class="taxonomy-candidate__review-error"
                role="alert"
              >
                <strong>本次审核未保存</strong>
                <span>{{ store.proposalErrors[proposal.id]?.message }}</span>
                <small v-if="store.proposalErrors[proposal.id]?.requestId">
                  请求编号：{{ store.proposalErrors[proposal.id]?.requestId }}
                </small>
              </div>

              <dl class="taxonomy-candidate__evidence">
                <div v-if="proposal.reason">
                  <dt>提出理由</dt>
                  <dd>{{ proposal.reason }}</dd>
                </div>
                <div v-if="proposal.why_not_reuse">
                  <dt>未复用现有词</dt>
                  <dd>{{ proposal.why_not_reuse }}</dd>
                </div>
                <div v-if="proposal.question_refs.length">
                  <dt>关联题目</dt>
                  <dd class="taxonomy-candidate__questions">
                    <label v-for="questionId in proposal.question_refs" :key="questionId">
                      <input
                        type="checkbox"
                        :checked="draftFor(proposal).selectedQuestionIds.includes(questionId)"
                        :disabled="store.busyProposalId === proposal.id"
                        :aria-label="`把题目 #${questionId} 纳入本次标签更新`"
                        @change="toggleQuestion(
                          proposal,
                          questionId,
                          ($event.currentTarget as HTMLInputElement).checked,
                        )"
                      >
                      <button
                        type="button"
                        class="taxonomy-question-link"
                        :aria-label="`预览题目 #${questionId}`"
                        @click="openQuestionPreview(questionId, $event)"
                      >
                        #{{ questionId }}
                      </button>
                    </label>
                  </dd>
                </div>
              </dl>

              <div
                v-if="store.suggestionFor(proposal.id)?.suggestion"
                class="taxonomy-candidate__ai-suggestion"
                :class="{ 'is-stale': store.suggestionRun?.stale }"
              >
                <div>
                  <span>AI 建议</span>
                  <strong>
                    {{ suggestionLabel(store.suggestionFor(proposal.id)!.suggestion!) }}
                  </strong>
                  <p>{{ store.suggestionFor(proposal.id)?.suggestion?.reason }}</p>
                </div>
                <button
                  v-if="suggestionCanPrefill(
                    store.suggestionFor(proposal.id)!.suggestion!,
                  )"
                  type="button"
                  class="qb-button"
                  :disabled="store.suggestionRun?.stale"
                  @click="adoptSuggestion(proposal)"
                >
                  {{ suggestionActionLabel(proposal) }}
                </button>
                <span v-else class="taxonomy-candidate__suggestion-hint">
                  {{ suggestionManualHint(store.suggestionFor(proposal.id)!.suggestion!) }}
                </span>
              </div>
              <div
                v-else-if="
                  store.suggestionFor(proposal.id)?.status === 'failed'
                  && store.suggestionFor(proposal.id)?.error
                "
                class="taxonomy-candidate__suggestion-error"
                role="status"
              >
                <strong>本项 AI 建议未完成</strong>
                <span>{{ store.suggestionFor(proposal.id)?.error?.message }}</span>
              </div>

              <div
                v-if="termFor(proposal, proposal.nearest_id)"
                class="taxonomy-candidate__nearest"
              >
                <span>系统找到近似规范词</span>
                <strong>{{ termFor(proposal, proposal.nearest_id)?.name }}</strong>
                <button type="button" class="qb-link" @click="useNearest(proposal)">
                  选为合并目标
                </button>
              </div>

              <details open class="taxonomy-candidate__merge">
                <summary>搜索并归并到已有规范词（推荐）</summary>
                <div class="taxonomy-candidate__merge-body">
                  <label>
                    <span>搜索已有词</span>
                    <input
                      v-model="draftFor(proposal).mergeSearch"
                      type="search"
                      placeholder="输入名称或别名"
                      autocomplete="off"
                    >
                  </label>
                  <fieldset class="taxonomy-candidate__term-options">
                    <legend>归并目标（可多选）</legend>
                    <label
                      v-for="term in matchingTerms(proposal)"
                      :key="term.id"
                    >
                      <input
                        v-model="draftFor(proposal).targetTermIds"
                        type="checkbox"
                        :value="term.id"
                        :disabled="store.busyProposalId === proposal.id"
                      >
                      <span>{{ term.name }}</span>
                    </label>
                  </fieldset>
                  <div class="taxonomy-candidate__merge-actions">
                    <span>
                      已选择 {{ selectedTerms(proposal).length }} 个规范词
                      <template v-if="draftFor(proposal).selectedQuestionIds.length">
                        ，将更新 {{ draftFor(proposal).selectedQuestionIds.length }} 道关联题
                      </template>
                      <template v-else>
                        ；未选择题目，本次只处理词表
                      </template>
                    </span>
                    <button
                      type="button"
                      class="qb-button is-primary"
                      :disabled="
                        selectedTerms(proposal).length === 0
                        || store.busyProposalId === proposal.id
                      "
                      @click="merge(proposal)"
                    >
                      确认归并
                    </button>
                  </div>
                </div>
              </details>

              <details
                class="taxonomy-candidate__approve"
                :open="draftFor(proposal).approvalExpanded"
                @toggle="draftFor(proposal).approvalExpanded =
                  ($event.currentTarget as HTMLDetailsElement).open"
              >
                <summary>现有词确实不合适，批准为新规范词</summary>
                <div class="taxonomy-candidate__decision">
                  <label>
                    <span>规范名称</span>
                    <input
                      v-model="draftFor(proposal).editedName"
                      type="text"
                      maxlength="36"
                      autocomplete="off"
                      :disabled="store.busyProposalId === proposal.id"
                    >
                  </label>
                  <div class="taxonomy-candidate__primary-actions">
                    <button
                      type="button"
                      class="qb-button"
                      :disabled="store.busyProposalId === proposal.id"
                      @click="approve(proposal)"
                    >
                      按原名批准
                    </button>
                    <button
                      type="button"
                      class="qb-button"
                      :disabled="
                        !draftFor(proposal).editedName.trim()
                        || store.busyProposalId === proposal.id
                      "
                      @click="approveEdited(proposal)"
                    >
                      按修改名批准
                    </button>
                  </div>
                </div>
              </details>

              <footer>
                <span>批准后才会成为 AI 今后可选的规范词。</span>
                <button
                  type="button"
                  class="qb-link is-danger"
                  :disabled="store.busyProposalId === proposal.id"
                  @click="reject(proposal)"
                >
                  拒绝这个新词
                </button>
              </footer>
            </article>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>

  <Teleport to="body">
    <div
      v-if="previewState !== 'idle'"
      class="qb-drawer-layer taxonomy-question-preview-layer"
      role="presentation"
      @click.self="closeQuestionPreview"
    >
      <aside
        class="taxonomy-question-preview"
        role="dialog"
        aria-modal="true"
        aria-labelledby="taxonomy-question-preview-title"
      >
        <header class="qb-inspector__heading">
          <div>
            <p class="qb-eyebrow">QUESTION PREVIEW</p>
            <h2 id="taxonomy-question-preview-title">
              题目 #{{ previewQuestionId }}
            </h2>
            <p>只读预览，用于判断新词应归并到哪些现有标签。</p>
          </div>
          <button
            ref="previewCloseButton"
            type="button"
            class="qb-drawer-close"
            aria-label="关闭题目预览"
            @click="closeQuestionPreview"
          >
            ×
          </button>
        </header>

        <div
          v-if="previewState === 'loading'"
          class="qb-inspector__empty"
          role="status"
        >
          正在读取题目…
        </div>
        <div
          v-else-if="previewState === 'error'"
          class="qb-inspector__empty"
          role="alert"
        >
          <span>题目暂时无法读取，候选词草稿不受影响。</span>
          <button type="button" class="qb-button" @click="retryQuestionPreview">
            重新读取
          </button>
        </div>
        <template v-else-if="previewQuestion">
          <dl class="qb-facts">
            <div><dt>原题号</dt><dd>{{ previewQuestion.question_number || previewQuestion.id }}</dd></div>
            <div><dt>题型</dt><dd>{{ previewQuestion.question_type || '未分类' }}</dd></div>
            <div><dt>试卷</dt><dd>{{ previewQuestion.paper_title || '未命名试卷' }}</dd></div>
            <div><dt>页码</dt><dd>{{ previewQuestion.page_range || '未记录' }}</dd></div>
          </dl>
          <section class="qb-paper-section">
            <h3>题干</h3>
            <QuestionContentRenderer
              :blocks="previewQuestion.rich_content.question_blocks"
              :fallback="previewQuestion.question_text"
              image-alt="题目配图"
              media-mode="detail"
            />
          </section>
          <details class="qb-paper-section qb-answer-section">
            <summary>展开答案与解析</summary>
            <QuestionContentRenderer
              :blocks="previewQuestion.rich_content.answer_blocks"
              :fallback="previewQuestion.answer_text"
              empty-label="暂未录入答案或解析"
              image-alt="答案配图"
              media-mode="detail"
            />
          </details>
        </template>
      </aside>
    </div>
  </Teleport>
</template>
