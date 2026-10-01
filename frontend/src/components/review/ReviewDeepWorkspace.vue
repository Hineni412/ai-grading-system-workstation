<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  resolveReviewItem,
  type ReviewMediaLinks,
  type ReviewConfirmInput,
  type ReviewItemLike,
} from '../../api/review'
import type { EvidenceSource } from '../../composables/use-evidence-viewer'
import type { ScanStudentMatchOption } from '../../api/scan-grading'
import AppIcon from '../shell/AppIcon.vue'
import StudentMatchSelect from '../scan-grading/StudentMatchSelect.vue'
import ReviewAnswerPanel from './ReviewAnswerPanel.vue'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'
import ReviewImageDialog from './ReviewImageDialog.vue'
import ReviewScoringInspector from './ReviewScoringInspector.vue'

interface StudentNav {
  previousName: string | null
  nextName: string | null
  position: string | null
}

const props = defineProps<{
  item: ReviewItemLike
  previousItem: ReviewItemLike | null
  nextItem: ReviewItemLike | null
  backLabel?: string
  sessionId: number | null
  questionId: string | null
  studentNav: StudentNav | null
  studentOptions?: ScanStudentMatchOption[]
  activeStudentId?: number | null
  studentSearchNotice?: string
  answerPanelOpen?: boolean
  switching?: boolean
  stayAfterConfirm?: boolean
  registerAnnotationRetry: (entry: {
    input: ReviewConfirmInput
    item: ReviewItemLike
  }) => void
}>()

const imageExpanded = ref(false)

const sourceOptions: readonly {
  value: EvidenceSource
  label: string
  mediaKey: keyof ReviewMediaLinks
}[] = [
  { value: 'crop', label: '裁剪证据', mediaKey: 'crop_url' },
  { value: 'original_front', label: '原卷正面', mediaKey: 'original_front_url' },
  { value: 'original_back', label: '原卷反面', mediaKey: 'original_back_url' },
]
const selectedSource = ref<EvidenceSource>('crop')
const resolvedItem = computed(() => resolveReviewItem(props.item))
const availableSources = computed(() =>
  sourceOptions.filter((option) => Boolean(resolvedItem.value.media[option.mediaKey])),
)

function selectSource(source: EvidenceSource): void {
  selectedSource.value = source
}

watch(
  () => resolvedItem.value.review_item_id,
  () => {
    selectedSource.value = 'crop'
  },
  { immediate: true, flush: 'sync' },
)

// 题目与答案面板宽度：拖动分隔条调整（300px ~ 主体一半），双击复位 420px。
const ANSWER_PANEL_WIDTH_KEY = 'ai-grading:review-answer-panel-width:v1'
const ANSWER_PANEL_DEFAULT_WIDTH = 420
const ANSWER_PANEL_MIN_WIDTH = 300
const ANSWER_PANEL_KEY_STEP = 24

const workspaceBody = ref<HTMLElement | null>(null)
const bodyWidth = ref(0)

function loadAnswerPanelWidth(): number {
  try {
    const raw = localStorage.getItem(ANSWER_PANEL_WIDTH_KEY)
    const value = raw === null ? Number.NaN : Number(raw)
    if (Number.isFinite(value) && value >= ANSWER_PANEL_MIN_WIDTH) return value
  } catch { /* 存储不可用时用默认宽度 */ }
  return ANSWER_PANEL_DEFAULT_WIDTH
}

const answerPanelWidth = ref(loadAnswerPanelWidth())
const answerPanelMaxWidth = computed(() =>
  Math.max(ANSWER_PANEL_MIN_WIDTH, Math.floor((bodyWidth.value || 1200) / 2)),
)

function applyAnswerPanelWidth(width: number): void {
  answerPanelWidth.value = Math.min(
    Math.max(width, ANSWER_PANEL_MIN_WIDTH),
    answerPanelMaxWidth.value,
  )
  try {
    localStorage.setItem(ANSWER_PANEL_WIDTH_KEY, String(answerPanelWidth.value))
  } catch { /* 存储不可用时保持当次宽度 */ }
}

function onDividerPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  event.preventDefault()
  const divider = event.currentTarget as HTMLElement
  bodyWidth.value = workspaceBody.value?.clientWidth ?? bodyWidth.value
  const startX = event.clientX
  const startWidth = (divider.nextElementSibling as HTMLElement | null)?.offsetWidth
    ?? answerPanelWidth.value
  divider.setPointerCapture(event.pointerId)
  const onMove = (move: PointerEvent): void => {
    applyAnswerPanelWidth(startWidth + (startX - move.clientX))
  }
  const stop = (): void => divider.removeEventListener('pointermove', onMove)
  divider.addEventListener('pointermove', onMove)
  divider.addEventListener('pointerup', stop, { once: true })
  divider.addEventListener('pointercancel', stop, { once: true })
}

function onDividerKeydown(event: KeyboardEvent): void {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  event.preventDefault()
  bodyWidth.value = workspaceBody.value?.clientWidth ?? bodyWidth.value
  applyAnswerPanelWidth(
    answerPanelWidth.value + (event.key === 'ArrowRight' ? ANSWER_PANEL_KEY_STEP : -ANSWER_PANEL_KEY_STEP),
  )
}

onMounted(() => {
  bodyWidth.value = workspaceBody.value?.clientWidth ?? 0
})

const emit = defineEmits<{
  back: []
  'student-nav': [direction: -1 | 1]
  'student-pick': [studentId: number | undefined]
  'toggle-answer-panel': []
  confirmed: [payload: {
    reviewItemId: string
    annotationRetry: boolean
  }]
}>()
</script>

<template>
  <section
    class="review-deep-workspace"
    :class="{ 'review-deep-workspace--switching': switching === true }"
    data-testid="review-deep-workspace"
    aria-labelledby="review-deep-title"
  >
    <header class="review-deep-workspace__header">
      <button type="button" data-testid="back-to-batch" @click="emit('back')">
        {{ backLabel || `返回 ${item.question_id} 批量复核` }}
      </button>
      <div class="review-deep-workspace__identity">
        <h2 id="review-deep-title">{{ item.student_name }}</h2>
        <span>{{ item.student_code || '学号未提供' }} · {{ item.class_name || '班级未提供' }}</span>
      </div>
      <div
        v-if="studentNav"
        class="review-deep-workspace__student-nav"
        role="group"
        aria-label="切换学生"
      >
        <button
          type="button"
          :disabled="studentNav.previousName === null"
          :title="studentNav.previousName
            ? `上一名：${studentNav.previousName}（快捷键 ↑）`
            : '已是第一个学生'"
          :aria-label="studentNav.previousName
            ? `上一名学生：${studentNav.previousName}`
            : '上一名学生（已是第一个）'"
          @click="emit('student-nav', -1)"
        >
          ‹ 上一名 ↑
        </button>
        <span v-if="studentNav.position" class="review-deep-workspace__position">
          {{ studentNav.position }}
        </span>
        <button
          type="button"
          :disabled="studentNav.nextName === null"
          :title="studentNav.nextName
            ? `下一名：${studentNav.nextName}（快捷键 ↓）`
            : '已是最后一个学生'"
          :aria-label="studentNav.nextName
            ? `下一名学生：${studentNav.nextName}`
            : '下一名学生（已是最后一个）'"
          @click="emit('student-nav', 1)"
        >
          下一名 ↓ ›
        </button>
        <div v-if="studentOptions?.length" class="review-deep-workspace__student-search">
          <AppIcon name="search" :size="14" class="review-deep-workspace__search-icon" />
          <StudentMatchSelect
            :model-value="activeStudentId ?? undefined"
            :students="studentOptions"
            placeholder="搜索姓名/学号/拼音首字母"
            aria-label="搜索并跳转到学生"
            @update:model-value="emit('student-pick', $event)"
          />
          <span v-if="studentSearchNotice" class="review-deep-workspace__search-notice" role="status">
            {{ studentSearchNotice }}
          </span>
        </div>
      </div>
      <div class="review-deep-workspace__tools">
        <button
          type="button"
          class="review-deep-workspace__panel-toggle"
          :aria-pressed="answerPanelOpen === true"
          title="显示或隐藏原题与参考答案"
          @click="emit('toggle-answer-panel')"
        >
          <AppIcon name="book-open" :size="14" />
          题目与答案
        </button>
        <div
          v-if="resolvedItem.media.originals_available !== false"
          class="review-deep-workspace__sources"
          role="group"
          aria-label="证据来源"
        >
          <button
            v-for="option in availableSources"
            :key="option.value"
            type="button"
            :aria-pressed="selectedSource === option.value"
            @click="selectSource(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </header>

    <slot name="strip" />

    <div
      ref="workspaceBody"
      class="review-deep-workspace__body"
      :class="{ 'review-deep-workspace__body--answers': answerPanelOpen === true }"
      :style="answerPanelOpen === true
        ? { '--review-answer-panel-width': `${answerPanelWidth}px` }
        : undefined"
    >
      <div class="review-deep-workspace__evidence">
        <ReviewEvidenceViewer
          :item="item"
          :previous-item="previousItem"
          :next-item="nextItem"
          :source="selectedSource"
          @expand="imageExpanded = true"
        />
      </div>
      <template v-if="answerPanelOpen === true && sessionId !== null && questionId !== null">
        <div
          class="review-deep-workspace__divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整题目与答案面板宽度"
          :aria-valuenow="answerPanelWidth"
          :aria-valuemin="ANSWER_PANEL_MIN_WIDTH"
          :aria-valuemax="answerPanelMaxWidth"
          tabindex="0"
          title="拖动调整宽度；双击恢复默认 420px；左右方向键微调"
          @pointerdown="onDividerPointerDown"
          @dblclick="applyAnswerPanelWidth(ANSWER_PANEL_DEFAULT_WIDTH)"
          @keydown="onDividerKeydown"
        ></div>
        <ReviewAnswerPanel
          :session-id="sessionId"
          :question-id="questionId"
          @close="emit('toggle-answer-panel')"
        />
      </template>
      <div class="review-deep-workspace__scoring">
        <ReviewScoringInspector
          :register-annotation-retry="registerAnnotationRetry"
          :stay-after-confirm="stayAfterConfirm === true"
          @confirmed="emit('confirmed', $event)"
        />
      </div>
    </div>
    <ReviewImageDialog v-if="imageExpanded" :item="item" :source="selectedSource" @close="imageExpanded = false" />
  </section>
</template>
