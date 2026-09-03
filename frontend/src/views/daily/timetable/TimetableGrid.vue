<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { TimetableCell, TimetableWeek } from '../../../api/daily'
import {
  buildClassPalette,
  buildGridRows,
  classColorFor,
  dayLabel,
  distinctClassLabels,
  formatMonthDay,
  isCollapsibleRow,
  slotTime,
  type TimetableCellRef,
  type TimetableGridRow,
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

const palette = computed(() => buildClassPalette(distinctClassLabels(props.week.cells)))

function chipStyle(cell: TimetableCell): Record<string, string> {
  const color = classColorFor(palette.value, cell.class_label)
  return { '--cc': color.fg, '--cc-sub': color.bg }
}

function isToday(dayOfWeek: number): boolean {
  return props.week.today.in_week && props.week.today.day_of_week === dayOfWeek
}

/* 空时段折叠：连续全空行归为一组，默认展开，可手动收成一条。 */
interface GridStrip {
  kind: 'strip'
  id: number
  labels: string[]
}

interface GridRowItem {
  kind: 'row'
  row: TimetableGridRow
  stripId: number | null
}

type GridItem = GridStrip | GridRowItem

const collapsedStrips = ref<Set<number>>(new Set())

// 换周时重置折叠状态；同一周内保存（临时调整等）不重置。
watch(
  () => props.week.week_start,
  () => {
    collapsedStrips.value = new Set()
  },
)

const items = computed<GridItem[]>(() => {
  const result: GridItem[] = []
  let nextStripId = 0
  let buffer: TimetableGridRow[] = []
  const flush = (): void => {
    if (!buffer.length) return
    const id = nextStripId
    nextStripId += 1
    result.push({ kind: 'strip', id, labels: buffer.map((row) => row.slot.label) })
    for (const row of buffer) result.push({ kind: 'row', row, stripId: id })
    buffer = []
  }
  for (const row of buildGridRows(props.week)) {
    if (isCollapsibleRow(row)) buffer.push(row)
    else {
      flush()
      result.push({ kind: 'row', row, stripId: null })
    }
  }
  flush()
  return result
})

function stripCollapsed(id: number): boolean {
  return collapsedStrips.value.has(id)
}

function toggleStrip(id: number): void {
  const next = new Set(collapsedStrips.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsedStrips.value = next
}

function stripText(strip: GridStrip): string {
  const joined = strip.labels.join(' · ')
  return stripCollapsed(strip.id) ? `${joined} 本周暂无课程，点击展开` : `收起空时段（${joined}）`
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
            :class="{ 'is-today': isToday(day.day_of_week), 'is-dim': !isToday(day.day_of_week) }"
          >
            <span class="timetable-grid__day-label">{{ dayLabel(day.day_of_week) }}</span>
            <span class="timetable-grid__day-date">{{ formatMonthDay(day.date) }}</span>
            <span v-if="isToday(day.day_of_week)" class="timetable-grid__today-badge">今天</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <template v-for="item in items" :key="item.kind === 'strip' ? `strip-${item.id}` : item.row.slot.slot_key">
          <tr v-if="item.kind === 'strip'" class="timetable-grid__strip-row">
            <td :colspan="6">
              <button
                type="button"
                :aria-expanded="!stripCollapsed(item.id)"
                @click="toggleStrip(item.id)"
              >
                <span aria-hidden="true">{{ stripCollapsed(item.id) ? '▸' : '▾' }}</span>
                {{ stripText(item) }}
              </button>
            </td>
          </tr>
          <tr
            v-else
            v-show="item.stripId === null || !stripCollapsed(item.stripId)"
          >
            <th scope="row" class="timetable-grid__slot" :data-kind="item.row.slot.kind">
              <span>{{ item.row.slot.label }}</span>
              <span v-if="slotTime(item.row.slot)" class="timetable-grid__slot-time">{{ slotTime(item.row.slot) }}</span>
            </th>
            <td
              v-for="cell in item.row.cells"
              :key="`${cell.day_of_week}|${cell.slot_key}`"
              class="timetable-grid__cell"
              :class="{
                'is-today': isToday(cell.day_of_week),
                'is-dim': !isToday(cell.day_of_week),
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
                <span class="timetable-grid__course-chip" :style="chipStyle(cell)">
                  <span class="timetable-grid__course-name">{{ cell.course_text }}</span>
                  <span v-if="cell.class_label" class="timetable-grid__course-class">{{ cell.class_label }}</span>
                  <span v-if="cell.source === 'override'" class="timetable-grid__override-badge">临时</span>
                </span>
                <span v-if="cell.has_note" class="timetable-grid__note-dot" title="记了一笔"></span>
                <span v-if="cell.has_homework" class="timetable-grid__hw-badge" title="有作业">作</span>
              </template>
              <span v-else-if="cell.source === 'override'" class="timetable-grid__cleared-badge">留空</span>
              <span v-else-if="mode === 'adjust'" class="timetable-grid__empty-hint" aria-hidden="true">+</span>
            </td>
          </tr>
        </template>
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
  min-width: 680px;
  border-collapse: collapse;
  table-layout: fixed;
}

.timetable-grid th,
.timetable-grid td {
  border: var(--border-width) solid var(--color-border-subtle);
  padding: 7px 10px;
  text-align: left;
  vertical-align: top;
}

.timetable-grid thead th {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.timetable-grid__slot-head {
  width: 76px;
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
  font-size: var(--font-size-caption);
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

/* 舒适密度：保留呼吸空间，格子仍是一屏读完的高度。 */
.timetable-grid__cell {
  position: relative;
  min-height: 50px;
  cursor: pointer;
}

.timetable-grid__cell.is-draggable {
  cursor: grab;
}

/* 今天列反向强调：今天列着色，其余列整体减淡去饱和。 */
.timetable-grid__cell.is-today {
  background: var(--color-accent-subtle);
}

.timetable-grid thead th.is-today {
  background: var(--color-accent-subtle);
}

.timetable-grid__cell.is-dim,
.timetable-grid thead th.is-dim {
  filter: saturate(0.3);
  opacity: 0.68;
}

/* 临时调整的警示黄不随减淡丢失：保持接近原色以示区分。 */
.timetable-grid__cell.is-override {
  background: var(--color-warning-subtle);
}

.timetable-grid__cell.is-override.is-dim {
  filter: saturate(0.85);
  opacity: 0.95;
}

.timetable-grid__cell.is-today.is-override {
  background: var(--color-accent-subtle);
  box-shadow: inset 0 0 0 2px var(--color-warning);
}

.timetable-grid__cell:hover {
  background: var(--color-accent-subtle);
}

.timetable-grid__cell.is-override:hover {
  background: var(--color-warning-subtle);
}

/* 课程色块：按班级配色，未标注班级的课程用中性色。 */
.timetable-grid__course-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 32px;
  padding: 2px 8px;
  border-left: 3px solid var(--cc, var(--color-accent));
  border-radius: 6px;
  background: var(--cc-sub, var(--color-accent-subtle));
}

.timetable-grid__course-name {
  overflow: hidden;
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timetable-grid__course-class {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timetable-grid__override-badge {
  flex: none;
  margin-left: auto;
  padding: 0 6px;
  border-radius: var(--radius-tag);
  background: var(--color-warning);
  color: var(--color-bg-surface);
  font-size: var(--font-size-caption);
}

/* 格子信息填充：右上角笔记圆点、右下角作业角标。 */
.timetable-grid__note-dot {
  position: absolute;
  top: 4px;
  right: 6px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-accent);
}

.timetable-grid__hw-badge {
  position: absolute;
  right: 5px;
  bottom: 3px;
  padding: 0 4px;
  border: var(--border-width) solid var(--color-accent);
  border-radius: 4px;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  line-height: 1.5;
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

/* 空时段折叠条：一条细行代表一组全空时段。 */
.timetable-grid__strip-row td {
  padding: 0;
}

.timetable-grid__strip-row button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 10px;
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  text-align: left;
  cursor: pointer;
}

.timetable-grid__strip-row button:hover {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}
</style>
