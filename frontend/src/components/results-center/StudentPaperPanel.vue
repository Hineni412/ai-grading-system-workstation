<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError } from '../../api/errors'
import type {
  ResultsCenterItem,
  ResultsCenterResponse,
  ResultsCenterStudent,
} from '../../api/results-center'
import {
  confirmReviewItem,
  type ResolvedReviewItem,
  type ReviewConfirmInput,
  type ReviewRubricPoint,
  type ReviewRubricSection,
} from '../../api/review'
import type { PageRole, RegionWorkspace } from '../../api/template-regions'
import {
  canReviewSteps,
  reviewDraftIssue,
  reviewDraftKey,
  reviewDraftRevision,
  stepNeedsReview,
  stepNumber,
  submittedSteps,
  useReviewDraftStore,
  type ReviewStepDraft,
} from '../../stores/review-drafts'
import { dedupeAcceptedAnswers } from '../../utils/answer-dedupe'
import AppButton from '../design-system/AppButton.vue'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import PaperMarksLayer from './PaperMarksLayer.vue'
import {
  formatScore,
  isCompleteStudent,
  resolvedItemScore,
} from './results-overview'
import {
  paperMarks,
  regionWorkspaceFor,
  resultIdOf,
  scoreStatusLabel,
  type PaperMark,
} from './student-paper'
import { unexpectedLosses, walkthroughDataFor } from './paper-walkthrough'

const props = defineProps<{
  sessionId: number
  results: ResultsCenterResponse
  student: ResultsCenterStudent
  items: ResultsCenterItem[]
  editable?: boolean
}>()

const emit = defineEmits<{
  review: [item: ResultsCenterItem]
  saved: []
}>()

const draftStore = useReviewDraftStore()

const page = ref<PageRole>('front')
const scale = ref(1)
const selectedItemKey = ref<string | null>(null)

const workspace = ref<RegionWorkspace | null>(null)
const workspaceState = ref<'loading' | 'ready' | 'error'>('loading')
let workspaceRequest = 0

function loadWorkspace(): void {
  workspaceRequest += 1
  const request = workspaceRequest
  workspaceState.value = 'loading'
  regionWorkspaceFor(props.sessionId)
    .then((data) => {
      if (request !== workspaceRequest) return
      workspace.value = data
      workspaceState.value = 'ready'
    })
    .catch(() => {
      if (request === workspaceRequest) workspaceState.value = 'error'
    })
}
loadWorkspace()

const resultId = computed(() => resultIdOf(props.student))
const imageFailed = ref<Record<PageRole, boolean>>({ front: false, back: false })

const pageUrl = computed(() => resultId.value === null
  ? null
  : `/api/sessions/${props.sessionId}/results/${resultId.value}/pages/${page.value}`)

const imageAvailable = computed(() => (
  pageUrl.value !== null && !imageFailed.value[page.value]
))

const marks = computed<PaperMark[]>(() => {
  if (workspaceState.value !== 'ready' || workspace.value === null) return []
  return paperMarks(
    workspace.value.formal_regions,
    workspace.value.template.pages,
    props.items,
  )
})

const marksOnPage = computed(() => (
  marks.value.filter((mark) => mark.page === page.value)
))

function pageTotal(role: PageRole): number | null {
  const onPage = marks.value.filter((mark) => mark.page === role)
  if (onPage.some((mark) => mark.state === 'pending')) return null
  return onPage.reduce((total, mark) => total + (mark.score ?? 0), 0)
}

function pageLabel(role: PageRole, label: string): string {
  const total = pageTotal(role)
  return total === null ? label : `${label} · ${formatScore(total)} 分`
}

const markByItem = computed(() => {
  const map = new Map<string, PaperMark>()
  for (const mark of marks.value) {
    for (const item of mark.items) map.set(item.review_item_id, mark)
  }
  return map
})

// 导入的考试没有样卷题框，任何题都没有框选标记时不逐题提示。
const hasAnyMark = computed(() => props.items.some(
  (item) => markByItem.value.has(item.review_item_id),
))

const unexpectedIds = computed(() => {
  const peers = props.results.students.filter(
    (peer) => (peer.class_name ?? '') === (props.student.class_name ?? '')
      && isCompleteStudent(peer),
  )
  return new Set(
    unexpectedLosses(props.student, peers).map((loss) => loss.questionId),
  )
})

const selectedItem = computed(() => (
  props.items.find(
    (item) => item.review_item_id === selectedItemKey.value,
  ) ?? null
))

const selectedMark = computed(() => (
  selectedItemKey.value === null
    ? null
    : (markByItem.value.get(selectedItemKey.value) ?? null)
))

const highlightedItemKeys = computed(() => new Set(
  (selectedMark.value?.items ?? []).map((item) => item.review_item_id),
))

const deduction = ref<{
  key: string | null
  state: 'idle' | 'loading' | 'ready' | 'error'
  text: string | null
}>({ key: null, state: 'idle', text: null })
let deductionRequest = 0

function ensureDeduction(item: ResultsCenterItem): void {
  const resolved = resolvedItemScore(item)
  if (resolved !== null && resolved >= item.max_score) {
    deduction.value = { key: item.review_item_id, state: 'ready', text: '满分' }
    return
  }
  deductionRequest += 1
  const request = deductionRequest
  deduction.value = { key: item.review_item_id, state: 'loading', text: null }
  walkthroughDataFor(props.results).items(item.question_id)
    .then((items) => {
      if (request !== deductionRequest) return
      const match = items.find(
        (entry) => entry.review_item_id === item.review_item_id,
      ) ?? items.find((entry) => entry.student_id === props.student.student_id)
      deduction.value = {
        key: item.review_item_id,
        state: 'ready',
        text: match?.deduction_reason ?? match?.error_summary ?? null,
      }
    })
    .catch(() => {
      if (request === deductionRequest) {
        deduction.value = { key: item.review_item_id, state: 'error', text: null }
      }
    })
}

const deductionText = computed(() => {
  const item = selectedItem.value
  if (item === null || deduction.value.key !== item.review_item_id) return ''
  if (deduction.value.state !== 'ready') {
    return deduction.value.state === 'error'
      ? '扣分理由暂时无法读取'
      : '正在读取扣分理由…'
  }
  return deduction.value.text ?? '无扣分说明'
})

// 展开行的复核上下文：复核条目按 review_item_id 严格对应
// （student_id 兜底只用于上方扣分理由），与成绩快照同源缓存。
const reviewContext = ref<{
  key: string | null
  state: 'idle' | 'loading' | 'ready'
  item: ResolvedReviewItem | null
  rubric: ReviewRubricSection | null
  rubricFailed: boolean
}>({ key: null, state: 'idle', item: null, rubric: null, rubricFailed: false })
let reviewContextRequest = 0

const rubricParts = computed(() => {
  const parts = new Map<string, {
    partId: string
    label: string
    reference: ReviewRubricPoint
    points: { point: ReviewRubricPoint; index: number }[]
  }>()
  ;(reviewContext.value.rubric?.points ?? []).forEach((point, index) => {
    const part = parts.get(point.part_id)
    if (part) {
      part.points.push({ point, index })
    } else {
      parts.set(point.part_id, {
        partId: point.part_id,
        label: point.part_label,
        reference: point,
        points: [{ point, index }],
      })
    }
  })
  return [...parts.values()]
})

const contextEditable = computed(() => {
  const context = reviewContext.value
  const row = selectedItem.value
  return props.editable === true
    && context.state === 'ready'
    && context.item !== null
    && row !== null
    && row.combined_group !== true
})

const currentDraft = computed(() => {
  const item = reviewContext.value.item
  if (!contextEditable.value || item === null) return null
  return draftStore.drafts[reviewDraftKey(item)] ?? null
})

const draftIssue = computed(() => {
  const draft = currentDraft.value
  const item = reviewContext.value.item
  if (draft === null || item === null) return null
  return reviewDraftIssue(draft, item.max_score)
})

const submitting = ref(false)
const submitFailed = ref<'' | 'conflict' | 'error'>('')

const saveVisible = computed(() => {
  const draft = currentDraft.value
  const item = reviewContext.value.item
  return draft !== null && item !== null
    && (draft.dirty || item.score_status === 'ai_review')
})

const saveDisabled = computed(() => draftIssue.value !== null || submitting.value)

function hasUnsavedDraft(item: ResultsCenterItem): boolean {
  return draftStore.drafts[`review-item:${item.review_item_id}`]?.dirty === true
}

function prepareDraft(): void {
  const item = reviewContext.value.item
  if (!contextEditable.value || item === null) return
  const draft = draftStore.ensureDraft(item)
  if (canReviewSteps(item, reviewContext.value.rubric) && !draft.stepScores) {
    draftStore.ensureSteps(item, reviewContext.value.rubric)
  }
}

function loadReviewContext(item: ResultsCenterItem): void {
  const request = ++reviewContextRequest
  reviewContext.value = {
    key: item.review_item_id,
    state: 'loading',
    item: null,
    rubric: null,
    rubricFailed: false,
  }
  const data = walkthroughDataFor(props.results)
  void Promise.all([
    data.items(item.question_id).then(
      (items) => items,
      () => null,
    ),
    data.rubric(item.question_id).then(
      (rubric) => ({ rubric, failed: false }),
      () => ({ rubric: null, failed: true }),
    ),
  ]).then(([items, rubricResult]) => {
    if (request !== reviewContextRequest) return
    reviewContext.value = {
      key: item.review_item_id,
      state: 'ready',
      item: items?.find(
        (entry) => entry.review_item_id === item.review_item_id,
      ) ?? null,
      rubric: rubricResult.rubric,
      rubricFailed: rubricResult.failed,
    }
    prepareDraft()
  })
}

async function submitReviewContext(): Promise<void> {
  const item = reviewContext.value.item
  const draft = currentDraft.value
  if (item === null || draft === null || saveDisabled.value) return
  const note = draft.note.trim()
  const input: ReviewConfirmInput = {
    review_item_id: item.review_item_id,
    expected_revision: reviewDraftRevision(draft),
    student_id: item.student_id,
    result_id: item.result_id,
    detail_id: item.detail_id,
    score_awarded: Number(draft.scoreText.trim()),
    ...(note ? { deduction_reason: note } : {}),
    ...(draft.stepScores ? { step_scores: submittedSteps(draft) } : {}),
  }
  submitting.value = true
  submitFailed.value = ''
  try {
    await confirmReviewItem(item.session_id, item.question_id, input)
    draftStore.markConfirmed(draft.key)
    emit('saved')
  } catch (error) {
    submitFailed.value = error instanceof ApiError && error.kind === 'conflict'
      ? 'conflict'
      : 'error'
  } finally {
    submitting.value = false
  }
}

function resetDraft(): void {
  const item = reviewContext.value.item
  if (item === null) return
  draftStore.markConfirmed(reviewDraftKey(item))
  submitFailed.value = ''
  prepareDraft()
}

function discardDraft(): void {
  const item = reviewContext.value.item
  if (item !== null) draftStore.markConfirmed(reviewDraftKey(item))
  emit('saved')
}

function pointStep(point: ReviewRubricPoint): ReviewStepDraft | undefined {
  return currentDraft.value?.stepScores?.find(
    (step) => step.partId === point.part_id && step.stepId === point.step_id,
  )
}

function updatePointScore(point: ReviewRubricPoint, event: Event): void {
  const draft = currentDraft.value
  if (!draft?.stepScores) return
  const input = event.target as HTMLInputElement
  const raw = input.value.trim()
  const normalized = /^\d+$/.test(raw) ? String(Number(raw)) : raw
  const index = draft.stepScores.findIndex(
    (step) => step.partId === point.part_id && step.stepId === point.step_id,
  )
  if (index < 0) return
  draftStore.updateStep(draft.key, index, { scoreText: normalized })
  input.value = normalized
}

function updateTotalScore(event: Event): void {
  const draft = currentDraft.value
  if (draft === null || draft.stepScores) return
  draftStore.updateScore(draft.key, (event.target as HTMLInputElement).value)
}

function selectScore(event: Event): void {
  ;(event.currentTarget as HTMLInputElement).select()
}

function pointTag(point: ReviewRubricPoint): { red: boolean; label: string } {
  const item = reviewContext.value.item
  return item === null
    ? { red: false, label: '' }
    : stepNeedsReview(item, pointStep(point))
}

function dedupedAnswers(point: ReviewRubricPoint): string[] {
  return dedupeAcceptedAnswers(point.accepted_answers, point.standard_answer)
}

function scoreInputMaxLength(maxScore: number): number {
  return Math.max(1, Math.min(3, String(Math.max(0, maxScore)).length + 1))
}

function selectItem(item: ResultsCenterItem): void {
  selectedItemKey.value = item.review_item_id
  const mark = markByItem.value.get(item.review_item_id)
  if (mark) {
    page.value = mark.page
    void nextTick(() => {
      document.getElementById(`paper-mark-${mark.key}`)
        ?.scrollIntoView?.({ block: 'center', inline: 'center' })
    })
  }
}

watch(
  [() => props.results, selectedItemKey],
  ([, key]) => {
    reviewContextRequest += 1
    submitFailed.value = ''
    const row = props.items.find(
      (entry) => entry.review_item_id === key,
    ) ?? null
    if (row === null) {
      deduction.value = { key: null, state: 'idle', text: null }
      reviewContext.value = {
        key: null, state: 'idle', item: null, rubric: null, rubricFailed: false,
      }
      return
    }
    ensureDeduction(row)
    loadReviewContext(row)
  },
)

watch(() => props.student.student_id, () => {
  page.value = 'front'
  scale.value = 1
  selectedItemKey.value = null
  deduction.value = { key: null, state: 'idle', text: null }
  submitFailed.value = ''
  imageFailed.value = { front: false, back: false }
})
</script>

<template>
  <div class="paper-wrap">
  <div class="paper-panel">
    <div class="paper-viewer">
      <div class="paper-viewer__toolbar">
        <div class="paper-viewer__pages" role="group" aria-label="卷面">
          <button
            type="button"
            :class="{ 'is-active': page === 'front' }"
            :aria-pressed="page === 'front'"
            @click="page = 'front'"
          >{{ pageLabel('front', '正面') }}</button>
          <button
            type="button"
            :class="{ 'is-active': page === 'back' }"
            :aria-pressed="page === 'back'"
            @click="page = 'back'"
          >{{ pageLabel('back', '反面') }}</button>
        </div>
        <div class="paper-viewer__zoom" role="group" aria-label="缩放">
          <button type="button" @click="scale = 1">适应宽度</button>
          <button
            type="button"
            :disabled="scale <= 1"
            @click="scale = Math.max(1, scale - 0.25)"
          >缩小</button>
          <button
            type="button"
            :disabled="scale >= 3"
            @click="scale = Math.min(3, scale + 0.25)"
          >放大</button>
          <output>{{ Math.round(scale * 100) }}%</output>
        </div>
        <p class="paper-viewer__legend">
          <i class="is-full"></i>满分
          <i class="is-partial"></i>部分得分
          <i class="is-zero"></i>0 分
          <i class="is-pending"></i>未出分
          <b class="paper-mark__review">待复核</b>
        </p>
      </div>
      <div class="paper-viewer__scroll">
        <div
          v-if="imageAvailable"
          class="paper-viewer__image"
          :style="{ width: `${scale * 100}%` }"
        >
          <img
            :src="pageUrl!"
            :alt="`原卷${page === 'front' ? '正面' : '反面'}`"
            @error="imageFailed[page] = true"
          >
          <PaperMarksLayer
            :marks="marksOnPage"
            :selected-item-key="selectedItemKey"
            @select-item="selectItem"
          />
        </div>
        <p v-else class="paper-panel__note">
          原卷页暂时无法读取；原卷清理后只保留分数和作答记录。
        </p>
        <p v-if="workspaceState === 'error'" class="paper-panel__note">
          题框读取失败，仅显示原卷
        </p>
      </div>
    </div>
    <ol class="paper-list">
      <li v-for="item in items" :key="item.review_item_id">
        <button
          type="button"
          class="paper-list__row"
          :class="{ 'is-selected': highlightedItemKeys.has(item.review_item_id) }"
          :data-status="item.score_status"
          @click="selectItem(item)"
        >
          <span class="paper-list__qid">{{ item.question_id }}</span>
          <span class="paper-list__score">
            <strong>{{ formatScore(item.score_awarded) }}</strong>
            / {{ formatScore(item.max_score) }}
          </span>
          <span class="results-status" :data-status="item.score_status">
            {{ scoreStatusLabel(item.score_status) }}
          </span>
          <span v-if="hasUnsavedDraft(item)" class="paper-list__tag">未保存</span>
          <span
            v-if="item.combined_group"
            class="paper-list__tag"
            title="这几题合起来给一个总分"
          >合并给分</span>
          <span v-if="unexpectedIds.has(item.question_id)" class="paper-list__tag">
            意外失分
          </span>
          <span
            v-if="!markByItem.has(item.review_item_id) && hasAnyMark"
            class="paper-list__tag is-muted"
          >卷面未框选</span>
        </button>
        <div
          v-if="selectedItemKey === item.review_item_id"
          class="paper-list__detail"
        >
          <p>{{ deductionText }}</p>
          <p v-if="reviewContext.state !== 'ready'" class="paper-list__muted">
            正在读取评分标准…
          </p>
          <template v-else>
            <p v-if="reviewContext.rubricFailed" class="paper-list__warning">
              评分标准暂时无法读取
            </p>
            <template v-else-if="reviewContext.rubric && rubricParts.length">
              <section
                v-for="part in rubricParts"
                :key="part.partId"
                class="paper-rubric-part"
              >
                <h4 v-if="rubricParts.length > 1" class="paper-rubric-part__label">
                  {{ part.label || part.partId }}
                </h4>
                <details
                  v-if="part.reference.standard_answer || dedupedAnswers(part.reference).length"
                  class="paper-rubric-details"
                >
                  <summary>参考解答</summary>
                  <dl class="paper-rubric-fields">
                    <template v-if="part.reference.standard_answer">
                      <dt>{{ part.points.some((entry) => entry.point.answer_kind === 'conditions') ? '参考解答与示例答案' : '参考解答' }}</dt>
                      <dd>
                        <QuestionHtmlBlock :text="part.reference.standard_answer" typeset-text />
                      </dd>
                    </template>
                    <template v-if="dedupedAnswers(part.reference).length">
                      <dt>等价答案</dt>
                      <dd>
                        <QuestionHtmlBlock :text="dedupedAnswers(part.reference).join('；')" typeset-text />
                      </dd>
                    </template>
                  </dl>
                </details>
                <div
                  v-for="entry in part.points"
                  :key="`${entry.point.part_id}:${entry.point.step_id}`"
                  class="paper-rubric-point"
                >
                  <div class="paper-rubric-point__row">
                    <span class="paper-rubric-point__goal">
                      {{ stepNumber(entry.index) }}
                      <QuestionHtmlBlock
                        :text="entry.point.core_goal || entry.point.part_label"
                        inline
                        typeset-text
                      />
                      <i v-if="pointTag(entry.point).red" class="paper-list__red-tag">{{ pointTag(entry.point).label }}</i>
                    </span>
                    <span
                      v-if="currentDraft?.stepScores && pointStep(entry.point)"
                      class="paper-rubric-point__input"
                    >
                      <input
                        :value="pointStep(entry.point)!.scoreText"
                        type="text"
                        inputmode="numeric"
                        :maxlength="scoreInputMaxLength(entry.point.score)"
                        :aria-label="`第${entry.index + 1}步给分，满分${formatScore(entry.point.score)}`"
                        :disabled="submitting"
                        @input="updatePointScore(entry.point, $event)"
                        @keydown.enter.prevent="submitReviewContext"
                        @focus="selectScore"
                        @click="selectScore"
                      >
                      <i>/ {{ formatScore(entry.point.score) }}</i>
                    </span>
                    <span v-else class="paper-rubric-point__max">{{ formatScore(entry.point.score) }} 分</span>
                  </div>
                  <p
                    v-if="pointStep(entry.point)?.recheck"
                    class="paper-list__warning paper-rubric-point__recheck"
                  >{{ pointStep(entry.point)!.recheck }}</p>
                  <details
                    v-if="entry.point.required_elements.length || entry.point.deduction_rules.length"
                    class="paper-rubric-details"
                  >
                    <summary>评分要点</summary>
                    <dl class="paper-rubric-fields">
                      <template v-if="entry.point.required_elements.length">
                        <dt>{{ entry.point.answer_kind === 'conditions' ? '判对条件' : '得分条件' }}</dt>
                        <dd>
                          <QuestionHtmlBlock :text="entry.point.required_elements.join('；')" typeset-text />
                        </dd>
                      </template>
                      <template v-if="entry.point.deduction_rules.length">
                        <dt>扣分例外</dt>
                        <dd>
                          <QuestionHtmlBlock :text="entry.point.deduction_rules.join('；')" typeset-text />
                        </dd>
                      </template>
                    </dl>
                  </details>
                </div>
              </section>
            </template>
            <p
              v-else-if="!reviewContext.rubric"
              class="paper-list__muted"
            >当前题没有可展示的评分标准</p>
          </template>
          <template v-if="currentDraft">
            <p v-if="currentDraft.stepNotice" class="paper-list__warning">{{ currentDraft.stepNotice }}</p>
            <p v-if="currentDraft.stepScores" class="paper-list__total">
              合计 {{ currentDraft.scoreText || '—' }} / {{ formatScore(item.max_score) }}
            </p>
            <p v-else class="paper-list__total">
              <input
                :value="currentDraft.scoreText"
                type="text"
                inputmode="numeric"
                :maxlength="scoreInputMaxLength(item.max_score)"
                aria-label="教师最终分"
                :aria-invalid="draftIssue !== null ? 'true' : 'false'"
                :disabled="submitting"
                @input="updateTotalScore"
                @keydown.enter.prevent="submitReviewContext"
                @focus="selectScore"
                @click="selectScore"
              >
              <span>/ {{ formatScore(item.max_score) }} 分</span>
            </p>
            <p v-if="draftIssue" class="paper-list__warning">{{ draftIssue }}</p>
            <p v-if="submitFailed === 'conflict'" class="paper-list__warning" role="alert">
              这道题已在别处更新，草稿已保留。
            </p>
            <p v-else-if="submitFailed === 'error'" class="paper-list__warning" role="alert">
              保存失败，草稿已保留，请重试。
            </p>
            <div class="paper-list__actions">
              <AppButton
                v-if="saveVisible"
                variant="primary"
                size="small"
                :disabled="saveDisabled"
                @click="submitReviewContext"
              >{{ submitting ? '正在保存…' : currentDraft.dirty ? '保存' : '确认原分' }}</AppButton>
              <AppButton
                v-if="currentDraft.dirty"
                variant="ghost"
                size="small"
                :disabled="submitting"
                @click="resetDraft"
              >撤销修改</AppButton>
              <AppButton
                v-if="submitFailed === 'conflict'"
                variant="ghost"
                size="small"
                @click="discardDraft"
              >放弃草稿并重新读取</AppButton>
            </div>
          </template>
          <AppButton
            variant="ghost"
            size="small"
            @click="emit('review', item)"
          >去复核页细看</AppButton>
        </div>
      </li>
    </ol>
  </div>
  </div>
</template>

<style scoped>
.paper-wrap {
  container-type: inline-size;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  height: 100%;
  min-height: 0;
}

.paper-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  grid-template-rows: minmax(0, 1fr);
  min-height: 0;
  gap: var(--space-4);
  overflow: hidden;
  border-top: var(--border-width) solid var(--border);
}

.paper-viewer {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.paper-viewer__toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-2) 0;
}

.paper-viewer__pages,
.paper-viewer__zoom {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-control);
  background: var(--muted);
}

.paper-viewer__pages button,
.paper-viewer__zoom button {
  min-height: var(--control-height-small);
  padding: 0 var(--space-2);
  border: 0;
  border-radius: calc(var(--radius-control) - 2px);
  background: transparent;
  color: var(--muted-foreground);
  font-size: var(--font-size-dense);
  cursor: pointer;
}

.paper-viewer__pages button.is-active {
  background: var(--card);
  color: var(--foreground);
  box-shadow: var(--shadow-subtle);
}

.paper-viewer__zoom button:disabled {
  opacity: 0.5;
  cursor: default;
}

.paper-viewer__zoom output {
  padding-inline: var(--space-1);
  color: var(--muted-foreground);
  font-size: var(--font-size-caption);
  font-variant-numeric: tabular-nums;
}

.paper-viewer__legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  margin: 0;
  color: var(--muted-foreground);
  font-size: var(--font-size-caption);
}

.paper-viewer__legend i {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-inline-start: var(--space-1);
  border-radius: 2px;
}

.paper-viewer__legend i.is-full {
  background: var(--color-success);
}

.paper-viewer__legend i.is-partial {
  background: var(--color-warning);
}

.paper-viewer__legend i.is-zero {
  background: var(--color-danger);
}

.paper-viewer__legend i.is-pending {
  border: 1.5px dashed var(--muted-foreground);
}

.paper-viewer__scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-control);
  background: var(--muted);
}

.paper-viewer__image {
  position: relative;
  min-width: 100%;
}

.paper-viewer__image img {
  display: block;
  width: 100%;
}

.paper-viewer__legend b.paper-mark__review {
  display: inline-block;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--color-warning);
  color: var(--card);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.paper-panel__note {
  margin: var(--space-3);
  color: var(--muted-foreground);
  font-size: var(--font-size-dense);
}

.paper-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-content: start;
  min-height: 0;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.paper-list__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  width: 100%;
  min-height: 44px;
  padding: var(--space-2);
  border: 0;
  border-bottom: var(--border-width) solid var(--border);
  border-radius: 0;
  background: transparent;
  text-align: start;
  cursor: pointer;
}

.paper-list__row:hover {
  background: var(--muted);
}

.paper-list__row.is-selected {
  background: var(--accent);
  box-shadow: inset var(--border-selected-width) 0 0 var(--primary);
}

.paper-list__row[data-status='ai_review'] {
  box-shadow: inset var(--border-selected-width) 0 0 var(--color-warning);
}

.paper-list__row[data-status='failed'] {
  box-shadow: inset var(--border-selected-width) 0 0 var(--destructive);
}

.paper-list__qid {
  min-width: 56px;
  font-weight: var(--font-weight-semibold);
}

.paper-list__score {
  color: var(--muted-foreground);
  font-variant-numeric: tabular-nums;
}

.paper-list__score strong {
  color: var(--foreground);
}

.paper-list__tag {
  padding: 1px 5px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--color-warning) 20%, transparent);
  color: var(--foreground);
  font-size: var(--font-size-caption);
}

.paper-list__tag.is-muted {
  background: none;
  color: var(--muted-foreground);
}

.paper-list__detail {
  padding: var(--space-2) var(--space-3) var(--space-3);
  border-bottom: var(--border-width) solid var(--border);
  background: var(--accent);
}

.paper-list__detail p {
  margin: 0 0 var(--space-2);
  font-size: var(--font-size-dense);
  white-space: pre-wrap;
}

.paper-list__muted {
  color: var(--muted-foreground);
}

.paper-list__warning {
  color: var(--color-warning);
}

.paper-list__red-tag {
  display: inline-block;
  margin-inline-start: var(--space-1);
  padding: 0 4px;
  border: 1px solid var(--color-danger);
  border-radius: 4px;
  background: var(--color-danger-subtle);
  color: var(--color-danger);
  font-size: var(--font-size-caption);
  font-style: normal;
  line-height: 15px;
  white-space: nowrap;
}

.paper-rubric-part {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-1);
  margin-bottom: var(--space-2);
}

.paper-rubric-part__label {
  margin: 0;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
}

.paper-rubric-details > summary {
  color: var(--muted-foreground);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.paper-rubric-fields {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--space-1) var(--space-2);
  margin: var(--space-1) 0 var(--space-2);
  font-size: var(--font-size-dense);
}

.paper-rubric-fields dt {
  color: var(--muted-foreground);
}

.paper-rubric-fields dd {
  margin: 0;
  min-width: 0;
}

.paper-rubric-point {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 2px;
}

.paper-rubric-point__row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--font-size-dense);
}

.paper-rubric-point__goal {
  flex: 1;
  min-width: 0;
}

.paper-rubric-point__max,
.paper-rubric-point__input {
  flex: none;
  color: var(--muted-foreground);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.paper-rubric-point__input i {
  font-style: normal;
}

.paper-list__detail input {
  width: 3em;
  min-height: var(--control-height-small);
  padding: 0 var(--space-1);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--foreground);
  font-size: var(--font-size-dense);
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.paper-list__detail input:focus-visible {
  outline: var(--border-selected-width) solid var(--primary);
  outline-offset: 1px;
}

.paper-rubric-point__recheck {
  margin: 0;
}

.paper-list__total {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-variant-numeric: tabular-nums;
}

.paper-list__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

@container (max-width: 760px) {
  .paper-panel {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 3fr) minmax(0, 2fr);
  }
}
</style>

