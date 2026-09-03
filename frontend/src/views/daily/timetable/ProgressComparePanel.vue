<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError } from '../../../api/errors'
import { dailyApi, type LessonNote, type TimetableSlot } from '../../../api/daily'
import {
  buildClassPalette,
  classColorFor,
  formatMonthDay,
  pickDefaultCompareClasses,
  slotLabelFor,
  slotLabelMap,
} from './timetableModel'

const props = defineProps<{
  /** 进度对照的候选班级（来自课表课程标注）。 */
  candidates: string[]
  slots: TimetableSlot[]
}>()

const RECENT_LIMIT = 10

const collapsed = ref(false)
const classA = ref('')
const classB = ref('')
const groups = ref<Record<string, LessonNote[]>>({})
const loading = ref(false)
const errorMessage = ref('')

const labels = computed(() => slotLabelMap(props.slots))

/** 与课表格子一致的班级配色（按候选顺序取色）。 */
const palette = computed(() => buildClassPalette(props.candidates))

/** 候选班级 ∪ 已有记录班级（保持课表顺序在前）。 */
const classOptions = computed(() => {
  const options = [...props.candidates]
  for (const label of Object.keys(groups.value)) {
    if (!options.includes(label)) options.push(label)
  }
  return options
})

const notesA = computed(() => groups.value[classA.value] ?? [])
const notesB = computed(() => groups.value[classB.value] ?? [])

const sides = computed(() => [
  { key: 'a' as const, selected: classA.value, notes: notesA.value },
  { key: 'b' as const, selected: classB.value, notes: notesB.value },
])

function selectClass(side: 'a' | 'b', event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (side === 'a') classA.value = value
  else classB.value = value
}

async function load(classLabels: string[]): Promise<void> {
  const wanted = [...new Set(classLabels.filter((label) => label.trim()))]
  if (!wanted.length) return
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await dailyApi.recentNotes(wanted, RECENT_LIMIT)
    const next = { ...groups.value }
    for (const group of result) next[group.class_label] = group.notes
    groups.value = next
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '进度记录暂时无法读取。'
  } finally {
    loading.value = false
  }
}

function ensureDefaults(): void {
  const options = classOptions.value
  if (!options.includes(classA.value) || !options.includes(classB.value) || classA.value === classB.value) {
    const picked = pickDefaultCompareClasses(
      options.map((label) => ({ class_label: label, notes: groups.value[label] ?? [] })),
    )
    classA.value = picked[0] ?? ''
    classB.value = picked[1] ?? ''
  }
}

async function refresh(): Promise<void> {
  await load(props.candidates)
  ensureDefaults()
}

watch(() => props.candidates, () => void refresh())

watch([classA, classB], ([a, b]) => {
  void load([a, b])
})

onMounted(refresh)
</script>

<template>
  <section class="progress-compare" aria-label="双班进度对照">
    <header class="progress-compare__header">
      <h3>双班进度对照</h3>
      <button
        type="button"
        :aria-expanded="!collapsed"
        @click="collapsed = !collapsed"
      >
        {{ collapsed ? '展开' : '收起' }}
      </button>
    </header>

    <template v-if="!collapsed">
      <p v-if="errorMessage" class="progress-compare__error" role="alert">{{ errorMessage }}</p>
      <p v-if="!classOptions.length" class="progress-compare__empty">
        课表中还没有班级信息：先在常规课表里为课程标注班级，或在课格里「记一笔」时填写班级。
      </p>
      <div v-else class="progress-compare__columns">
        <div
          v-for="side in sides"
          :key="side.key"
          class="progress-compare__column"
        >
          <label>
            班级
            <span class="progress-compare__class-select">
              <i
                class="progress-compare__class-dot"
                :style="{
                  background: classColorFor(palette, side.selected).fg,
                  opacity: side.selected ? 1 : 0.3,
                }"
                aria-hidden="true"
              ></i>
              <select :value="side.selected" @change="selectClass(side.key, $event)">
                <option value="" disabled>选择班级</option>
                <option v-for="option in classOptions" :key="option" :value="option">{{ option }}</option>
              </select>
            </span>
          </label>
          <p v-if="loading && !side.notes.length" class="progress-compare__empty">正在读取…</p>
          <p v-else-if="side.selected && !side.notes.length" class="progress-compare__empty">
            这个班还没有进度记录。
          </p>
          <ul v-if="side.notes.length" class="progress-compare__notes">
            <li v-for="note in side.notes" :key="note.id">
              <span class="progress-compare__note-meta">
                {{ formatMonthDay(note.note_date) }} · {{ slotLabelFor(labels, note.slot_key) }}
              </span>
              <span class="progress-compare__note-content">{{ note.content_text }}</span>
              <span v-if="note.homework_text" class="progress-compare__note-homework">
                作业：{{ note.homework_text }}
              </span>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.progress-compare {
  display: grid;
  gap: var(--space-2);
  padding: 12px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.progress-compare__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.progress-compare__header h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.progress-compare__header button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  cursor: pointer;
}

.progress-compare__columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}

.progress-compare__column {
  display: grid;
  align-content: start;
  gap: var(--space-2);
}

.progress-compare__column label {
  display: grid;
  gap: 4px;
  color: var(--color-text-secondary);
}

.progress-compare__class-select {
  position: relative;
  display: block;
}

.progress-compare__class-dot {
  position: absolute;
  top: 50%;
  left: 10px;
  z-index: 1;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transform: translateY(-50%);
}

.progress-compare__column select {
  min-height: var(--control-height-default);
  /* 左侧留出班级色点位置（色点绝对定位在下拉框上）。 */
  padding: 0 10px 0 26px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.progress-compare__notes {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.progress-compare__notes li {
  display: grid;
  gap: 2px;
  padding: 6px 8px;
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-control);
}

.progress-compare__note-meta {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.progress-compare__note-content {
  color: var(--color-text-primary);
}

.progress-compare__note-homework {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.progress-compare__empty {
  margin: 0;
  color: var(--color-text-muted);
}

.progress-compare__error {
  margin: 0;
  color: var(--color-danger);
}
</style>
