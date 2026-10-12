<script setup lang="ts">
import { computed, ref } from 'vue'
import { useResizeObserver } from '@vueuse/core'
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
  presentation?: 'regions' | 'scores'
}>(), {
  interactive: true,
  presentation: 'regions',
})

const emit = defineEmits<{
  'select-item': [item: ResultsCenterItem]
}>()

const selectedMarkKeys = computed(() => new Set(props.marks
  .filter((mark) => mark.items.some((item) => item.review_item_id === props.selectedItemKey))
  .map((mark) => mark.key)))

const layer = ref<HTMLElement | null>(null)
const pageHeight = ref(0)
const scoreLabelHeight = 26
useResizeObserver(layer, ([entry]) => {
  if (entry) pageHeight.value = entry.contentRect.height
})

const scoreLabels = computed(() => {
  if (props.presentation !== 'scores') return []
  return props.marks.filter((mark) => !hideOverlapMark(mark))
    .sort((left, right) => left.top - right.top || left.left - right.left)
    .map((mark) => ({
      mark,
      top: (mark.top + mark.height / 2) / 100 * pageHeight.value - scoreLabelHeight / 2,
    }))
})

function scoreLabel(mark: PaperMark): string {
  const selected = mark.items.find((item) => item.review_item_id === props.selectedItemKey)
  if (selected && mark.items.length > 1) {
    const score = resolvedItemScore(selected)
    return `${selected.question_id.replace('(P', '(')} ${score === null ? '未出分' : `${formatScore(score)}/${formatScore(selected.max_score)}`}`
  }
  return `${mark.questionId} ${markLabel(mark)}`
}

function nearSameRegion(left: PaperMark, right: PaperMark): boolean {
  if (left.page !== right.page) return false
  const width = Math.max(0, Math.min(left.left + left.width, right.left + right.width) - Math.max(left.left, right.left))
  const height = Math.max(0, Math.min(left.top + left.height, right.top + right.height) - Math.max(left.top, right.top))
  const intersection = width * height
  const union = left.width * left.height + right.width * right.height - intersection
  return union > 0 && intersection / union >= 0.8
}

const overlapGroups = computed(() => {
  const remaining = new Set(props.marks)
  const groups: { key: string; anchor: PaperMark; marks: PaperMark[]; items: ResultsCenterItem[] }[] = []
  for (const first of props.marks) {
    if (!remaining.delete(first)) continue
    const marks = [first]
    for (let index = 0; index < marks.length; index += 1) {
      const current = marks[index]
      if (!current) continue
      for (const candidate of remaining) {
        if (!nearSameRegion(current, candidate)) continue
        remaining.delete(candidate)
        marks.push(candidate)
      }
    }
    if (marks.length < 2) continue
    const items = [...new Map(marks.flatMap((mark) => mark.items)
      .map((item) => [item.review_item_id, item])).values()]
    groups.push({ key: first.key, anchor: first, marks, items })
  }
  return groups
})
const overlapByMark = computed(() => new Map(overlapGroups.value
  .flatMap((group) => group.marks.map((mark) => [mark.key, group] as const))))

function hideOverlapMark(mark: PaperMark): boolean {
  const group = overlapByMark.value.get(mark.key)
  if (!group) return false
  const selected = group.marks.find((entry) => selectedMarkKeys.value.has(entry.key))
  return mark.key !== (selected ?? group.anchor).key
}

const multiMarks = computed(() => (
  props.marks.filter((mark) => mark.items.length > 1 && !overlapByMark.value.has(mark.key))
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

function overlapChipLabel(item: ResultsCenterItem): string {
  const score = resolvedItemScore(item)
  return `${item.question_id} ${score === null ? '—' : formatScore(score)}/${formatScore(item.max_score)}`
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
    ref="layer"
    class="paper-marks-layer"
    :class="{ 'is-static': !interactive, 'is-scores': presentation === 'scores' }"
  >
    <template v-if="presentation === 'scores'">
      <span v-for="entry in scoreLabels" :key="entry.mark.key" class="paper-score"
        :data-region-key="entry.mark.key"
        :class="[`is-${entry.mark.state}`, { 'is-current': selectedMarkKeys.has(entry.mark.key) }]"
        :style="{ top: `${entry.top}px`, height: `${scoreLabelHeight}px` }"
        :title="markTitle(entry.mark)"
        :aria-current="selectedMarkKeys.has(entry.mark.key) ? 'true' : undefined">
        {{ scoreLabel(entry.mark) }}<small v-if="selectedMarkKeys.has(entry.mark.key)">当前</small>
      </span>
    </template>
    <template v-else>
      <component
        :is="interactive ? 'button' : 'span'"
        v-for="mark in marks"
        :id="`paper-mark-${mark.key}`"
        :key="mark.key"
        :type="interactive ? 'button' : undefined"
        class="paper-mark"
        :class="[`is-${mark.state}`, {
          'is-selected': selectedMarkKeys.has(mark.key),
          'is-overlap-hidden': hideOverlapMark(mark),
          'is-overlap': overlapByMark.has(mark.key),
        }]"
        :style="{
          left: `${mark.left}%`,
          top: `${mark.top}%`,
          width: `${mark.width}%`,
          height: `${mark.height}%`,
        }"
        :aria-label="interactive ? markTitle(mark) : undefined"
        :aria-hidden="hideOverlapMark(mark) ? 'true' : undefined"
        :tabindex="interactive && hideOverlapMark(mark) ? -1 : undefined"
        :title="markTitle(mark)"
        @click="selectMark(mark)"
      >
        <span v-if="!overlapByMark.has(mark.key) || selectedMarkKeys.has(mark.key)" class="paper-mark__label">{{ mark.questionId }} {{ markLabel(mark) }}</span>
        <span v-if="mark.needsReview && (!overlapByMark.has(mark.key) || selectedMarkKeys.has(mark.key))" class="paper-mark__review">待复核</span>
      </component>
      <div v-for="group in interactive ? overlapGroups : []" :key="`overlap-${group.key}`" class="paper-mark__overlap-items"
        :style="{ left: `${group.anchor.left}%`, top: `${group.anchor.top + group.anchor.height}%`, width: `${group.anchor.width}%` }"
        :aria-label="interactive ? '同一区域的题目，可逐题选择' : undefined">
        <component :is="interactive ? 'button' : 'span'" v-for="item in group.items" :key="item.review_item_id"
          :type="interactive ? 'button' : undefined" class="paper-mark__sub"
          :class="[`is-${itemMarkState(item)}`, { 'is-selected': selectedItemKey === item.review_item_id }]"
          :title="`${item.question_id} · ${subChipTitle(item)}`" :aria-current="selectedItemKey === item.review_item_id ? 'true' : undefined"
          @click.stop="emit('select-item', item)">{{ overlapChipLabel(item) }}<i v-if="itemNeedsReview(item)" class="paper-mark__sub-dot"></i></component>
      </div>
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
    </template>
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

.paper-score {
  position: absolute;
  inset-inline-start: calc(-1 * var(--paper-score-gutter));
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: var(--space-1);
  width: var(--paper-score-gutter);
  padding-inline: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
  white-space: nowrap;
  pointer-events: none;
}

.paper-score.is-full { color: var(--color-success); }
.paper-score.is-partial { color: var(--color-warning); }
.paper-score.is-zero { color: var(--color-danger); }
.paper-score.is-pending { color: var(--color-text-muted); }
.paper-score.is-current { color: var(--color-accent); font-weight: var(--font-weight-semibold); }
.paper-score small { font-size: inherit; font-weight: inherit; }

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

.paper-mark.is-overlap-hidden {
  opacity: 0;
  pointer-events: none;
}

.paper-mark.is-overlap:not(.is-selected) {
  border-color: var(--color-border-strong);
  background: transparent;
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

.paper-mark__overlap-items {
  position: absolute;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
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
