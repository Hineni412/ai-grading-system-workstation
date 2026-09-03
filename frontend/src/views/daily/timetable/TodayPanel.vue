<script setup lang="ts">
import { computed, ref } from 'vue'

import type { TimetableWeek } from '../../../api/daily'
import {
  buildClassPalette,
  classColorFor,
  dayLabel,
  distinctClassLabels,
  formatMonthDay,
  type TimetableTodayEntry,
} from './timetableModel'

const props = defineProps<{
  week: TimetableWeek
  entries: TimetableTodayEntry[]
}>()

const collapsed = ref(false)

const dateLabel = computed(() => {
  const week = props.week
  return `${formatMonthDay(week.today.date)} ${dayLabel(week.today.day_of_week)}`
})

const palette = computed(() => buildClassPalette(distinctClassLabels(props.week.cells)))

const notInWeek = computed(() => !props.week.today.in_week)
</script>

<template>
  <section class="today-panel" aria-label="今天的安排">
    <header class="today-panel__header">
      <h3>
        今天的安排
        <small v-if="!collapsed">（{{ dateLabel }} · {{ entries.length }} 节课）</small>
      </h3>
      <button type="button" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
        {{ collapsed ? '展开' : '收起' }}
      </button>
    </header>

    <div v-if="!collapsed" class="today-panel__body">
      <p v-if="notInWeek" class="today-panel__empty">当前查看的周不包含今天。</p>
      <p v-else-if="!entries.length" class="today-panel__empty">今天没有课。</p>
      <template v-else>
        <div v-for="entry in entries" :key="entry.slot_key" class="today-panel__item">
          <span v-if="entry.time" class="today-panel__time">{{ entry.time }}</span>
          <span
            class="today-panel__chip"
            :style="{
              '--cc': classColorFor(palette, entry.class_label).fg,
              '--cc-sub': classColorFor(palette, entry.class_label).bg,
            }"
          >
            {{ entry.course }}
            <em>{{ entry.class_label ? `${entry.class_label} · ${entry.slot_label}` : entry.slot_label }}</em>
          </span>
          <span class="today-panel__marks">
            <span v-if="entry.has_note" class="today-panel__dot" title="已记一笔"></span>
            <span v-if="entry.has_homework" class="today-panel__hw" title="有作业">作</span>
          </span>
        </div>
        <p class="today-panel__tip">点击课格可「记一笔」或发起临时调整。</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.today-panel {
  display: grid;
  gap: var(--space-2);
  padding: 10px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.today-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.today-panel__header h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.today-panel__header small {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular);
}

.today-panel__header button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  cursor: pointer;
}

.today-panel__body {
  display: grid;
  gap: var(--space-2);
}

.today-panel__empty {
  margin: 0;
  color: var(--color-text-muted);
}

.today-panel__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px 6px;
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-control);
}

.today-panel__time {
  flex: none;
  width: 88px;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.today-panel__chip {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 4px;
  min-height: 26px;
  padding: 2px 8px;
  border-left: 3px solid var(--cc, var(--color-accent));
  border-radius: 6px;
  background: var(--cc-sub, var(--color-accent-subtle));
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
}

.today-panel__chip em {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.today-panel__marks {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
}

.today-panel__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-accent);
}

.today-panel__hw {
  padding: 0 4px;
  border: var(--border-width) solid var(--color-accent);
  border-radius: 4px;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  line-height: 1.5;
}

.today-panel__tip {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}
</style>
