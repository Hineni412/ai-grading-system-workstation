<script setup lang="ts">
import { computed } from 'vue'

import type { TimetableCell, TimetableWeek } from '../../../api/daily'
import {
  buildGridRows,
  dayLabel,
  formatMonthDay,
  slotTime,
  type TimetableCellRef,
  type TimetableMode,
} from './timetableModel'

const props = defineProps<{
  week: TimetableWeek
  mode: TimetableMode
}>()

const emit = defineEmits<{
  cellClick: [cell: TimetableCell]
  cellDrop: [payload: { source: TimetableCellRef; target: TimetableCellRef }]
}>()

const DRAG_MIME = 'application/x-daily-timetable-cell'

const rows = computed(() => buildGridRows(props.week))

function isToday(dayOfWeek: number): boolean {
  return props.week.today.in_week && props.week.today.day_of_week === dayOfWeek
}

function draggable(cell: TimetableCell): boolean {
  return props.mode === 'adjust' && Boolean(cell.course_text)
}

function onDragStart(event: DragEvent, cell: TimetableCell): void {
  if (!draggable(cell) || !event.dataTransfer) return
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData(
    DRAG_MIME,
    JSON.stringify({ day_of_week: cell.day_of_week, slot_key: cell.slot_key }),
  )
}

function onDragOver(event: DragEvent): void {
  if (props.mode !== 'adjust') return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

function onDrop(event: DragEvent, target: TimetableCell): void {
  if (props.mode !== 'adjust' || !event.dataTransfer) return
  event.preventDefault()
  const raw = event.dataTransfer.getData(DRAG_MIME)
  if (!raw) return
  try {
    const source = JSON.parse(raw) as TimetableCellRef
    emit('cellDrop', {
      source,
      target: { day_of_week: target.day_of_week, slot_key: target.slot_key },
    })
  } catch {
    // 非本课表拖入的数据直接忽略。
  }
}
</script>

<template>
  <div class="timetable-grid__scroll">
    <table class="timetable-grid" :data-mode="mode">
      <thead>
        <tr>
          <th class="timetable-grid__slot-head" scope="col">时段</th>
          <th
            v-for="day in week.days"
            :key="day.day_of_week"
            scope="col"
            :class="{ 'is-today': isToday(day.day_of_week) }"
          >
            <span class="timetable-grid__day-label">{{ dayLabel(day.day_of_week) }}</span>
            <span class="timetable-grid__day-date">{{ formatMonthDay(day.date) }}</span>
            <span v-if="isToday(day.day_of_week)" class="timetable-grid__today-badge">今天</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.slot.slot_key">
          <th scope="row" class="timetable-grid__slot" :data-kind="row.slot.kind">
            <span>{{ row.slot.label }}</span>
            <span v-if="slotTime(row.slot)" class="timetable-grid__slot-time">{{ slotTime(row.slot) }}</span>
          </th>
          <td
            v-for="cell in row.cells"
            :key="`${cell.day_of_week}|${cell.slot_key}`"
            class="timetable-grid__cell"
            :class="{
              'is-today': isToday(cell.day_of_week),
              'is-override': cell.source === 'override',
              'is-empty': !cell.course_text,
              'is-draggable': draggable(cell),
            }"
            :draggable="draggable(cell) || undefined"
            role="button"
            tabindex="0"
            @click="emit('cellClick', cell)"
            @keyup.enter="emit('cellClick', cell)"
            @dragstart="onDragStart($event, cell)"
            @dragover="onDragOver"
            @drop="onDrop($event, cell)"
          >
            <template v-if="cell.course_text">
              <span class="timetable-grid__course">{{ cell.course_text }}</span>
              <span v-if="cell.class_label" class="timetable-grid__class">{{ cell.class_label }}</span>
              <span v-if="cell.source === 'override'" class="timetable-grid__override-badge">临时</span>
            </template>
            <span v-else-if="cell.source === 'override'" class="timetable-grid__cleared-badge">留空</span>
            <span v-else-if="mode === 'adjust'" class="timetable-grid__empty-hint" aria-hidden="true">+</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.timetable-grid__scroll {
  overflow-x: auto;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.timetable-grid {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
  table-layout: fixed;
}

.timetable-grid th,
.timetable-grid td {
  border: var(--border-width) solid var(--color-border-subtle);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.timetable-grid thead th {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.timetable-grid__slot-head {
  width: 120px;
}

.timetable-grid__day-label {
  margin-right: 6px;
}

.timetable-grid__day-date {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular);
}

.timetable-grid__today-badge {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: var(--radius-tag);
  background: var(--color-accent);
  color: var(--color-bg-surface);
  font-size: var(--font-size-caption);
}

.timetable-grid__slot {
  color: var(--color-text-secondary);
  font-weight: var(--font-weight-medium);
}

.timetable-grid__slot[data-kind='custom'] {
  background: var(--color-bg-subtle);
}

.timetable-grid__slot-time {
  display: block;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular);
}

.timetable-grid__cell {
  min-height: 44px;
  cursor: pointer;
}

.timetable-grid__cell.is-draggable {
  cursor: grab;
}

.timetable-grid__cell.is-today,
.timetable-grid thead th.is-today {
  background: var(--color-accent-subtle);
}

.timetable-grid__cell.is-override {
  background: var(--color-warning-subtle);
}

.timetable-grid__cell.is-today.is-override {
  background: var(--color-accent-subtle);
  box-shadow: inset 0 0 0 2px var(--color-warning);
}

.timetable-grid__course {
  display: block;
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.timetable-grid__class {
  display: inline-block;
  margin-top: 2px;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.timetable-grid__override-badge {
  display: inline-block;
  margin-top: 2px;
  margin-left: 4px;
  padding: 0 6px;
  border-radius: var(--radius-tag);
  background: var(--color-warning);
  color: var(--color-bg-surface);
  font-size: var(--font-size-caption);
}

/* 「本周留空」的 clear 覆盖格：描边徽章与实心「临时」徽章区分，不只靠底色识别。 */
.timetable-grid__cleared-badge {
  display: inline-block;
  padding: 0 6px;
  border: var(--border-width) solid var(--color-warning);
  border-radius: var(--radius-tag);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.timetable-grid__empty-hint {
  color: var(--color-text-muted);
}
</style>
