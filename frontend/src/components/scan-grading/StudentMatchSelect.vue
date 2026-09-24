<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'

import type { ScanStudentMatchOption } from '../../api/scan-grading'

const props = defineProps<{
  modelValue?: number
  students: ScanStudentMatchOption[]
  placeholder?: string
  ariaLabel?: string
  // student_id -> labels of papers that already belong to the student.
  assigned?: Record<number, string>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: number | undefined]
}>()

const listId = useId()
const input = ref<HTMLInputElement | null>(null)
const query = ref('')
const isOpen = ref(false)
const activeIndex = ref(-1)

const selectedStudent = computed(() => (
  props.students.find((student) => student.id === props.modelValue)
))

function optionLabel(student: ScanStudentMatchOption): string {
  return [
    student.name,
    student.student_code,
    student.class_name,
  ].filter(Boolean).join(' · ')
}

function normalized(value: unknown): string {
  return String(value ?? '').trim().toLocaleLowerCase('zh-CN')
}

const filteredStudents = computed(() => {
  const term = normalized(query.value)
  const selectedLabel = selectedStudent.value ? optionLabel(selectedStudent.value) : ''
  const filtered = (!term || query.value === selectedLabel)
    ? props.students
    : props.students.filter((student) => [
      student.name,
      student.student_code,
      student.class_name,
      student.pinyin_initials,
      student.pinyin_full,
    ].some((value) => normalized(value).includes(term)))
  // Already-assigned students stay selectable but sink to the bottom group.
  return filtered
    .map((student, index) => ({ student, index }))
    .sort((a, b) => (
      Number(Boolean(props.assigned?.[a.student.id]))
      - Number(Boolean(props.assigned?.[b.student.id]))
      || a.index - b.index
    ))
    .map((entry) => entry.student)
    .slice(0, 80)
})

watch(
  () => [props.modelValue, props.students] as const,
  () => {
    if (!isOpen.value) query.value = selectedStudent.value ? optionLabel(selectedStudent.value) : ''
  },
  { immediate: true, deep: true },
)

function openList(): void {
  isOpen.value = true
  const selectedIndex = filteredStudents.value.findIndex(
    (student) => student.id === props.modelValue,
  )
  activeIndex.value = selectedIndex >= 0 ? selectedIndex : 0
  void nextTick(() => input.value?.select())
}

function onInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value
  isOpen.value = true
  activeIndex.value = filteredStudents.value.length ? 0 : -1
}

function choose(student: ScanStudentMatchOption): void {
  emit('update:modelValue', student.id)
  query.value = optionLabel(student)
  isOpen.value = false
  activeIndex.value = -1
  input.value?.focus()
}

function clearSelection(): void {
  emit('update:modelValue', undefined)
  query.value = ''
  isOpen.value = true
  activeIndex.value = filteredStudents.value.length ? 0 : -1
  input.value?.focus()
}

function closeList(): void {
  window.setTimeout(() => {
    isOpen.value = false
    activeIndex.value = -1
    query.value = selectedStudent.value ? optionLabel(selectedStudent.value) : ''
  }, 100)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    isOpen.value = false
    activeIndex.value = -1
    query.value = selectedStudent.value ? optionLabel(selectedStudent.value) : ''
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!isOpen.value) openList()
    if (!filteredStudents.value.length) return
    const delta = event.key === 'ArrowDown' ? 1 : -1
    activeIndex.value = (
      activeIndex.value + delta + filteredStudents.value.length
    ) % filteredStudents.value.length
    return
  }
  if (event.key === 'Enter' && isOpen.value && activeIndex.value >= 0) {
    event.preventDefault()
    const student = filteredStudents.value[activeIndex.value]
    if (student) choose(student)
  }
}
</script>

<template>
  <div class="student-match-select">
    <input
      ref="input"
      class="student-match-select__input"
      type="text"
      role="combobox"
      autocomplete="off"
      :aria-label="ariaLabel ?? placeholder ?? '选择学生'"
      :aria-expanded="isOpen"
      :aria-controls="listId"
      :aria-activedescendant="activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined"
      :placeholder="placeholder ?? '姓名、学号或拼音首字母'"
      :value="query"
      @focus="openList"
      @input="onInput"
      @blur="closeList"
      @keydown="onKeydown"
    >
    <button
      v-if="modelValue"
      type="button"
      class="student-match-select__clear"
      aria-label="清除已选学生"
      @mousedown.prevent
      @click="clearSelection"
    >
      ×
    </button>
    <div
      v-if="isOpen"
      :id="listId"
      class="student-match-select__menu"
      role="listbox"
    >
      <button
        v-for="(student, index) in filteredStudents"
        :id="`${listId}-${index}`"
        :key="student.id"
        type="button"
        role="option"
        :aria-selected="student.id === modelValue"
        :data-active="index === activeIndex"
        @mousedown.prevent="choose(student)"
        @mousemove="activeIndex = index"
      >
        <strong>{{ student.name }}</strong>
        <span>{{ student.student_code }}<template v-if="student.class_name"> · {{ student.class_name }}</template></span>
        <span v-if="assigned?.[student.id]" class="student-match-select__assigned">已归属：{{ assigned[student.id] }}</span>
      </button>
      <p v-if="!filteredStudents.length">没有匹配的学生</p>
    </div>
  </div>
</template>

<style scoped>
.student-match-select {
  position: relative;
  flex: 0 1 18rem;
  min-width: 13rem;
}

.student-match-select__input {
  width: 100%;
  min-height: var(--control-height-default);
  padding-inline-end: var(--space-7);
}

.student-match-select__clear {
  position: absolute;
  inset-block-start: 50%;
  inset-inline-end: var(--space-2);
  width: var(--space-6);
  min-width: 0;
  height: var(--space-6);
  padding: 0;
  border: 0;
  border-radius: var(--radius-circle);
  background: transparent;
  color: var(--color-text-muted);
  transform: translateY(-50%);
}

.student-match-select__clear:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
}

.student-match-select__menu {
  position: absolute;
  z-index: 20;
  inset-block-start: calc(100% + var(--space-1));
  inset-inline: 0;
  max-height: 18rem;
  overflow-y: auto;
  border: var(--border-width) solid var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-overlay);
}

.student-match-select__menu button {
  display: grid;
  width: 100%;
  min-height: auto;
  gap: 0;
  padding: var(--space-2) var(--space-3);
  border: 0;
  border-bottom: var(--border-width) solid var(--color-border-default);
  border-radius: 0;
  background: transparent;
  color: var(--color-text-primary);
  text-align: left;
}

.student-match-select__menu button:last-of-type {
  border-bottom: 0;
}

.student-match-select__menu button[data-active="true"],
.student-match-select__menu button[aria-selected="true"] {
  background: var(--color-accent-subtle);
}

.student-match-select__menu button span {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.student-match-select__menu button span.student-match-select__assigned {
  color: var(--color-warning);
}

.student-match-select__menu p {
  margin: 0;
  padding: var(--space-3);
  color: var(--color-text-muted);
}
</style>
