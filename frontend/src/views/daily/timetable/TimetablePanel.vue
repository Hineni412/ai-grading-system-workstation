<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import { dailyApi, type TimetableCell, type TimetableWeek } from '../../../api/daily'
import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import CustomSlotManager from './CustomSlotManager.vue'
import ProgressComparePanel from './ProgressComparePanel.vue'
import RegularEditPanel from './RegularEditPanel.vue'
import TimetableCellPopover from './TimetableCellPopover.vue'
import TimetableGrid from './TimetableGrid.vue'
import TodayPanel from './TodayPanel.vue'
import WeekNavigator from './WeekNavigator.vue'
import {
  buildDragOps,
  dayLabel,
  distinctClassLabels,
  isoAddDays,
  mondayOf,
  slotLabelFor,
  slotLabelMap,
  todayEntries,
  todayIso,
  type TimetableCellRef,
  type TimetableMode,
} from './timetableModel'

defineProps<{
  /** 自定义时段面板开关（开关按钮在页头一行，状态由父组件持有）。 */
  customOpen: boolean
}>()

const weekStart = ref(mondayOf(todayIso()))
const week = ref<TimetableWeek | null>(null)
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const mode = ref<TimetableMode>('view')
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')
const popoverCell = ref<TimetableCell | null>(null)
const popoverInitialSection = ref<'menu' | 'adjust'>('menu')
const regularEditCell = ref<TimetableCell | null>(null)

const modeOptions: Array<{ id: TimetableMode; label: string }> = [
  { id: 'view', label: '查看' },
  { id: 'adjust', label: '临时调整' },
  { id: 'regular', label: '编辑常规课表' },
]

const modeHints: Record<TimetableMode, string> = {
  view: '点击课格可以记一笔、发起临时调整或清除临时调换。',
  adjust: '临时调整模式：拖拽课格移动或交换，点击空白格新增临时课程；只对本周生效。',
  regular: '编辑常规课表：点击课格修改固定课表；本周已被临时调整的格子仍显示临时内容。',
}

const slotLabels = computed(() => slotLabelMap(week.value?.slots ?? []))
const customSlots = computed(() => (week.value?.slots ?? []).filter((slot) => slot.kind === 'custom'))
const candidateClassLabels = computed(() => distinctClassLabels(week.value?.cells ?? []))
const todayItems = computed(() => (week.value ? todayEntries(week.value) : []))

const popoverContext = computed(() => {
  const cell = popoverCell.value
  const current = week.value
  if (!cell || !current) return null
  const day = current.days.find((entry) => entry.day_of_week === cell.day_of_week)
  return {
    cell,
    dayLabel: dayLabel(cell.day_of_week),
    slotLabel: slotLabelFor(slotLabels.value, cell.slot_key),
    date: day?.date ?? current.week_start,
  }
})

const regularEditContext = computed(() => {
  const cell = regularEditCell.value
  if (!cell) return null
  return {
    cell,
    dayLabel: dayLabel(cell.day_of_week),
    slotLabel: slotLabelFor(slotLabels.value, cell.slot_key),
  }
})

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

async function loadWeek(): Promise<void> {
  loadState.value = week.value ? loadState.value : 'loading'
  try {
    week.value = await dailyApi.timetable(weekStart.value)
    loadState.value = 'ready'
  } catch (error) {
    loadState.value = 'error'
    errorMessage.value = errorText(error, '课表暂时无法读取，请稍后重试。')
  }
}

async function runMutation(action: () => Promise<unknown>, notice: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    await action()
    await loadWeek()
    noticeMessage.value = notice
  } catch (error) {
    errorMessage.value = errorText(error, '操作没有保存，请刷新后重试。')
  } finally {
    busy.value = false
  }
}

function navigate(direction: 'prev' | 'today' | 'next'): void {
  if (direction === 'today') weekStart.value = mondayOf(todayIso())
  else weekStart.value = isoAddDays(weekStart.value, direction === 'prev' ? -7 : 7)
  popoverCell.value = null
  regularEditCell.value = null
  void loadWeek()
}

function applyAnchor(weekNo: number): void {
  void runMutation(
    () => dailyApi.setWeekAnchor(weekStart.value, weekNo),
    `已把当前查看的周设为第 ${weekNo} 周，其它周次会自动顺推。`,
  )
}

function selectMode(next: TimetableMode): void {
  mode.value = next
  popoverCell.value = null
  regularEditCell.value = null
}

function onCellClick(cell: TimetableCell): void {
  if (mode.value === 'regular') {
    regularEditCell.value = cell
    return
  }
  // 临时调整模式点空白格 = 直接新增临时课程内容。
  popoverInitialSection.value = mode.value === 'adjust' && !cell.course_text ? 'adjust' : 'menu'
  popoverCell.value = cell
}

function onCellDrop(payload: { source: TimetableCellRef; target: TimetableCellRef }): void {
  const current = week.value
  if (!current || mode.value !== 'adjust') return
  const ops = buildDragOps(payload.source, payload.target, current.cells)
  if (!ops.length) return
  const targetCell = current.cells.find(
    (cell) =>
      cell.day_of_week === payload.target.day_of_week && cell.slot_key === payload.target.slot_key,
  )
  const isSwap = Boolean(targetCell?.course_text)
  void runMutation(
    () => dailyApi.applyOverrides(current.week_start, ops),
    isSwap ? '已交换这两节课，仅本周生效。' : '已把这节课移到新位置，仅本周生效。',
  )
}

function saveNote(payload: {
  note_date: string
  class_label: string
  content_text: string
  homework_text?: string
}): void {
  const cell = popoverCell.value
  if (!cell) return
  void runMutation(async () => {
    await dailyApi.addNote({ ...payload, slot_key: cell.slot_key })
    popoverCell.value = null
  }, '已记下这节课的进度。')
}

function saveOverride(payload: { course_text: string; class_label: string; note?: string }): void {
  const current = week.value
  const cell = popoverCell.value
  if (!current || !cell) return
  void runMutation(async () => {
    await dailyApi.applyOverrides(current.week_start, [
      {
        day_of_week: cell.day_of_week,
        slot_key: cell.slot_key,
        action: 'set',
        course_text: payload.course_text,
        class_label: payload.class_label,
        ...(payload.note ? { note: payload.note } : {}),
      },
    ])
    popoverCell.value = null
  }, '已保存本周的临时调整。')
}

function clearWeekCell(): void {
  const current = week.value
  const cell = popoverCell.value
  if (!current || !cell) return
  void runMutation(async () => {
    await dailyApi.applyOverrides(current.week_start, [
      { day_of_week: cell.day_of_week, slot_key: cell.slot_key, action: 'clear' },
    ])
    popoverCell.value = null
  }, '本周这一格已留空，下周恢复常规。')
}

function clearOverride(): void {
  const cell = popoverCell.value
  if (!cell?.override_id) return
  const overrideId = cell.override_id
  void runMutation(async () => {
    await dailyApi.deleteOverride(overrideId)
    popoverCell.value = null
  }, '已清除这条临时调换，恢复为常规课程。')
}

function saveRegularCell(payload: { course_text: string; class_label: string }): void {
  const cell = regularEditCell.value
  if (!cell) return
  void runMutation(
    () =>
      dailyApi.setRegularCell({
        day_of_week: cell.day_of_week,
        slot_key: cell.slot_key,
        course_text: payload.course_text,
        class_label: payload.class_label,
      }),
    '常规课表已保存。本周已被临时调整的格子仍显示临时内容。',
  )
}

function clearRegularCell(): void {
  const cell = regularEditCell.value
  if (!cell) return
  void runMutation(
    () =>
      dailyApi.setRegularCell({
        day_of_week: cell.day_of_week,
        slot_key: cell.slot_key,
        course_text: '',
        class_label: '',
      }),
    '已清空这一格的常规课程。',
  )
}

function createCustomSlot(payload: {
  label: string
  start_text?: string
  end_text?: string
  position: number
}): void {
  void runMutation(() => dailyApi.createCustomSlot(payload), `已添加时段「${payload.label}」。`)
}

function removeCustomSlot(slot: { custom_slot_id: string | null; label: string }): void {
  if (!slot.custom_slot_id) return
  const confirmed = window.confirm(
    `删除时段「${slot.label}」会连带删除该时段所有课程内容（含各周的临时调整），确定删除吗？`,
  )
  if (!confirmed) return
  const id = slot.custom_slot_id
  void runMutation(() => dailyApi.deleteCustomSlot(id), `已删除时段「${slot.label}」。`)
}

onMounted(loadWeek)
</script>

<template>
  <div class="timetable-panel">
    <StatePanel
      v-if="loadState === 'loading'"
      kind="loading"
      title="正在读取课表"
      description="课表、临时调整和自定义时段加载中。"
    />
    <StatePanel
      v-else-if="loadState === 'error'"
      kind="error"
      title="课表暂时无法读取"
      :description="errorMessage"
      retry-label="重新读取"
      @retry="loadWeek"
    />
    <template v-else-if="week">
      <FeedbackBanner
        v-if="errorMessage"
        tone="error"
        title="操作没有保存"
        :description="errorMessage"
        dismissible
        @dismiss="errorMessage = ''"
      />
      <FeedbackBanner
        v-else-if="noticeMessage"
        tone="success"
        title="已保存"
        :description="noticeMessage"
        dismissible
        @dismiss="noticeMessage = ''"
      />

      <WeekNavigator
        :week-start="week.week_start"
        :week-no="week.week_no"
        :busy="busy"
        @navigate="navigate"
        @anchor="applyAnchor"
      >
        <template #end>
          <div class="timetable-panel__modes" role="group" aria-label="课表模式">
            <button
              v-for="option in modeOptions"
              :key="option.id"
              type="button"
              :class="{ 'is-active': mode === option.id }"
              :aria-pressed="mode === option.id"
              @click="selectMode(option.id)"
            >
              {{ option.label }}
            </button>
          </div>
          <p class="timetable-panel__mode-hint">{{ modeHints[mode] }}</p>
        </template>
      </WeekNavigator>

      <div class="timetable-panel__cols">
        <div class="timetable-panel__main">
          <CustomSlotManager v-if="customOpen" :slots="customSlots" :busy="busy" @create="createCustomSlot" @remove="removeCustomSlot" />

          <TimetableGrid
            :week="week"
            :mode="mode"
            @cell-click="onCellClick"
            @cell-drop="onCellDrop"
          />

          <RegularEditPanel
            v-if="mode === 'regular'"
            :context="regularEditContext"
            :busy="busy"
            @save="saveRegularCell"
            @clear="clearRegularCell"
            @close="regularEditCell = null"
          />
        </div>

        <aside class="timetable-panel__side">
          <TodayPanel :week="week" :entries="todayItems" />
          <ProgressComparePanel :candidates="candidateClassLabels" :slots="week.slots" />
        </aside>
      </div>

      <TimetableCellPopover
        v-if="popoverContext"
        :context="popoverContext"
        :busy="busy"
        :initial-section="popoverInitialSection"
        @close="popoverCell = null"
        @save-note="saveNote"
        @save-override="saveOverride"
        @clear-override="clearOverride"
        @clear-week-cell="clearWeekCell"
      />
    </template>
  </div>
</template>

<style scoped>
.timetable-panel {
  display: grid;
  gap: var(--space-3);
}

/* 宽屏双栏：课表在左，「今天的安排 + 双班进度对照」常驻右栏。 */
.timetable-panel__cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 336px;
  gap: var(--space-3);
  align-items: start;
}

.timetable-panel__main {
  display: grid;
  gap: var(--space-3);
  min-width: 0;
}

.timetable-panel__side {
  display: grid;
  gap: var(--space-3);
  min-width: 0;
}

@media (max-width: 1280px) {
  .timetable-panel__cols {
    grid-template-columns: minmax(0, 1fr);
  }

  .timetable-panel__side {
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  }

  .timetable-panel__mode-hint {
    display: none;
  }
}

.timetable-panel__modes {
  display: flex;
  gap: var(--space-2);
}

.timetable-panel__modes button {
  min-height: var(--control-height-small);
  padding: 0 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}

.timetable-panel__modes button.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.timetable-panel__mode-hint {
  display: block;
  max-width: 420px;
  margin: 0;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
