<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type {
  ResultsCenterItem,
  ResultsCenterResponse,
  ResultsCenterStudent,
} from '../../api/results-center'
import type { PageRole, RegionWorkspace } from '../../api/template-regions'
import AppButton from '../design-system/AppButton.vue'
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
}>()

const emit = defineEmits<{
  review: [item: ResultsCenterItem]
}>()

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

function selectItem(item: ResultsCenterItem): void {
  selectedItemKey.value = item.review_item_id
  ensureDeduction(item)
  const mark = markByItem.value.get(item.review_item_id)
  if (mark) {
    page.value = mark.page
    void nextTick(() => {
      document.getElementById(`paper-mark-${mark.key}`)
        ?.scrollIntoView?.({ block: 'center', inline: 'center' })
    })
  }
}

watch(() => props.student.student_id, () => {
  page.value = 'front'
  scale.value = 1
  selectedItemKey.value = null
  deduction.value = { key: null, state: 'idle', text: null }
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
          <span v-if="unexpectedIds.has(item.question_id)" class="paper-list__tag">
            意外失分
          </span>
          <span
            v-if="!markByItem.has(item.review_item_id)"
            class="paper-list__tag is-muted"
          >卷面未框选</span>
        </button>
        <div
          v-if="selectedItemKey === item.review_item_id"
          class="paper-list__detail"
        >
          <p>{{ deductionText }}</p>
          <AppButton
            variant="ghost"
            size="small"
            @click="emit('review', item)"
          >去复核这题</AppButton>
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
  grid-template-columns: minmax(0, 1fr) 340px;
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

@container (max-width: 760px) {
  .paper-panel {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 3fr) minmax(0, 2fr);
  }
}
</style>

