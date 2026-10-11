<script setup lang="ts">
import { computed } from 'vue'
import type { ResultsCenterItem } from '../../api/results-center'
import {
  formatScore,
  resolvedItemScore,
  subQuestionOf,
} from './results-overview'
import {
  itemMarkState,
  scoreStatusLabel,
  type PaperMark,
} from './student-paper'

const props = withDefaults(defineProps<{
  marks: PaperMark[]
  selectedItemKey: string | null
  interactive?: boolean
}>(), {
  interactive: true,
})

const emit = defineEmits<{
  'select-item': [item: ResultsCenterItem]
}>()

const selectedMarkKey = computed(() => {
  if (props.selectedItemKey === null) return null
  for (const mark of props.marks) {
    if (mark.items.some((item) => item.review_item_id === props.selectedItemKey)) {
      return mark.key
    }
  }
  return null
})

const multiMarks = computed(() => (
  props.marks.filter((mark) => mark.items.length > 1)
))

function selectMark(mark: PaperMark): void {
  const first = mark.items[0]
  if (first) emit('select-item', first)
}

function markLabel(mark: PaperMark): string {
  return mark.score === null
    ? '未出分'
    : `${formatScore(mark.score)}/${formatScore(mark.maxScore)}`
}

function itemNeedsReview(item: ResultsCenterItem): boolean {
  return item.needs_review || item.score_status === 'ai_review'
}

function subChipTag(item: ResultsCenterItem): string {
  const sub = subQuestionOf(item.question_id)
  return sub === null ? item.question_id : `(${sub.index})`
}

function subChipLabel(item: ResultsCenterItem): string {
  const score = resolvedItemScore(item)
  return `${subChipTag(item)} ${score === null ? '—' : formatScore(score)}/${formatScore(item.max_score)}`
}

function subChipTitle(item: ResultsCenterItem): string {
  const parts = [subChipLabel(item), scoreStatusLabel(item.score_status)]
  if (itemNeedsReview(item)) parts.push('待复核')
  return parts.join(' · ')
}

function markTitle(mark: PaperMark): string {
  const detail = mark.items.map((item) => {
    const sub = subQuestionOf(item.question_id)
    const label = sub === null ? item.question_id : `(P${sub.index})`
    const score = resolvedItemScore(item)
    return `${label} ${score === null ? '未出分' : formatScore(score)}/${formatScore(item.max_score)}`
  }).join(' · ')
  return `${mark.questionId} ${markLabel(mark)}：${detail}`
}
</script>

<template>
  <div
    class="paper-marks-layer"
    :class="{ 'is-static': !interactive }"
  >
    <component
      :is="interactive ? 'button' : 'span'"
      v-for="mark in marks"
      :id="`paper-mark-${mark.key}`"
      :key="mark.key"
      :type="interactive ? 'button' : undefined"
      class="paper-mark"
      :class="[`is-${mark.state}`, { 'is-selected': selectedMarkKey === mark.key }]"
      :style="{
        left: `${mark.left}%`,
        top: `${mark.top}%`,
        width: `${mark.width}%`,
        height: `${mark.height}%`,
      }"
      :aria-label="interactive ? markTitle(mark) : undefined"
      :title="markTitle(mark)"
      @click="selectMark(mark)"
    >
      <span class="paper-mark__label">{{ mark.questionId }} {{ markLabel(mark) }}</span>
      <span v-if="mark.needsReview" class="paper-mark__review">待复核</span>
    </component>
    <div
      v-for="mark in multiMarks"
      :key="`subs-${mark.key}`"
      class="paper-mark__subs"
      :style="{
        left: `${mark.left}%`,
        top: `${mark.top}%`,
        width: `${mark.width}%`,
      }"
    >
      <component
        :is="interactive ? 'button' : 'span'"
        v-for="item in mark.items"
        :key="item.review_item_id"
        :type="interactive ? 'button' : undefined"
        class="paper-mark__sub"
        :class="[`is-${itemMarkState(item)}`, {
          'is-selected': selectedItemKey === item.review_item_id,
        }]"
        :title="subChipTitle(item)"
        @click.stop="emit('select-item', item)"
      >{{ subChipLabel(item) }}<i
        v-if="itemNeedsReview(item)"
        class="paper-mark__sub-dot"
      ></i></component>
    </div>
  </div>
</template>

<style scoped>
.paper-marks-layer {
  position: absolute;
  inset: 0;
}

.paper-marks-layer.is-static,
.paper-marks-layer.is-static * {
  pointer-events: none;
}

.paper-mark {
  position: absolute;
  box-sizing: border-box;
  display: block;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 2px;
  background: transparent;
  cursor: pointer;
  text-align: start;
}

.paper-mark.is-full {
  border-color: var(--color-success);
  background: color-mix(in srgb, var(--color-success) 18%, transparent);
}

.paper-mark.is-partial {
  border-color: var(--color-warning);
  background: color-mix(in srgb, var(--color-warning) 22%, transparent);
}

.paper-mark.is-zero {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 18%, transparent);
}

.paper-mark.is-pending {
  border-color: var(--muted-foreground);
  border-style: dashed;
  background: color-mix(in srgb, var(--muted-foreground) 12%, transparent);
}

.paper-mark.is-selected {
  outline: 3px solid var(--primary);
  outline-offset: 1px;
}

.paper-mark__label {
  position: absolute;
  top: 0;
  right: 0;
  padding: 1px 4px;
  border-radius: 0 0 0 4px;
  background: var(--card);
  color: var(--foreground);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
  white-space: nowrap;
}

.paper-mark__review {
  display: inline-block;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--color-warning);
  color: var(--card);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
}

.paper-mark .paper-mark__review {
  position: absolute;
  top: 0;
  left: 0;
}

.paper-mark__subs {
  position: absolute;
  display: flex;
  align-content: flex-start;
  flex-wrap: wrap;
  gap: 2px;
  padding: 2px;
  padding-inline-end: 60px;
  pointer-events: none;
}

.paper-mark__sub {
  position: relative;
  padding: 0 4px;
  border: 1px solid transparent;
  border-radius: 3px;
  font-size: var(--font-size-caption);
  line-height: 1.5;
  white-space: nowrap;
  cursor: pointer;
  pointer-events: auto;
}

.paper-mark__sub.is-full {
  border-color: var(--color-success);
  background: color-mix(in srgb, var(--color-success) 22%, var(--card));
}

.paper-mark__sub.is-partial {
  border-color: var(--color-warning);
  background: color-mix(in srgb, var(--color-warning) 26%, var(--card));
}

.paper-mark__sub.is-zero {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 22%, var(--card));
}

.paper-mark__sub.is-pending {
  border-color: var(--muted-foreground);
  border-style: dashed;
  background: color-mix(in srgb, var(--muted-foreground) 15%, var(--card));
}

.paper-mark__sub.is-selected {
  outline: 2px solid var(--primary);
  outline-offset: 0;
}

.paper-mark__sub-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-inline-start: 3px;
  border-radius: 50%;
  background: var(--color-warning);
  vertical-align: 1px;
}
</style>
