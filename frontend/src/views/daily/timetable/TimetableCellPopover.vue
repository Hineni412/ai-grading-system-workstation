<script setup lang="ts">
import { ref, watch } from 'vue'

import type { TimetableCell } from '../../../api/daily'

export interface TimetableCellContext {
  cell: TimetableCell
  dayLabel: string
  slotLabel: string
  date: string
}

const props = withDefaults(defineProps<{
  context: TimetableCellContext
  busy?: boolean
  /** 临时调整模式点空白格时直接进入新增临时课程表单。 */
  initialSection?: 'menu' | 'adjust'
}>(), {
  busy: false,
  initialSection: 'menu',
})

const emit = defineEmits<{
  close: []
  saveNote: [payload: { note_date: string; class_label: string; content_text: string; homework_text?: string }]
  saveOverride: [payload: { course_text: string; class_label: string; note?: string }]
  clearOverride: []
  clearWeekCell: []
}>()

type Section = 'menu' | 'note' | 'adjust'

const section = ref<Section>('menu')
const noteDate = ref('')
const noteClass = ref('')
const noteContent = ref('')
const noteHomework = ref('')
const adjustCourse = ref('')
const adjustClass = ref('')
const adjustRemark = ref('')
const formError = ref('')

watch(
  () => props.context,
  (context) => {
    section.value = props.initialSection
    formError.value = ''
    noteDate.value = context.date
    noteClass.value = context.cell.class_label
    noteContent.value = ''
    noteHomework.value = ''
    adjustCourse.value = context.cell.course_text
    adjustClass.value = context.cell.class_label
    adjustRemark.value = context.cell.note ?? ''
  },
  { immediate: true },
)

function submitNote(): void {
  if (!noteClass.value.trim()) {
    formError.value = '班级不能为空。'
    return
  }
  if (!noteContent.value.trim()) {
    formError.value = '内容不能为空。'
    return
  }
  formError.value = ''
  emit('saveNote', {
    note_date: noteDate.value,
    class_label: noteClass.value.trim(),
    content_text: noteContent.value.trim(),
    ...(noteHomework.value.trim() ? { homework_text: noteHomework.value.trim() } : {}),
  })
}

function submitOverride(): void {
  if (!adjustCourse.value.trim()) {
    formError.value = '临时课程内容不能为空。'
    return
  }
  formError.value = ''
  emit('saveOverride', {
    course_text: adjustCourse.value.trim(),
    class_label: adjustClass.value.trim(),
    ...(adjustRemark.value.trim() ? { note: adjustRemark.value.trim() } : {}),
  })
}
</script>

<template>
  <div class="cell-popover__backdrop" @click.self="emit('close')">
    <section
      class="cell-popover"
      role="dialog"
      aria-modal="true"
      :aria-label="`${context.dayLabel} ${context.slotLabel}`"
    >
      <header class="cell-popover__header">
        <h3>
          {{ context.dayLabel }} · {{ context.slotLabel }}
          <span class="cell-popover__date">{{ context.date }}</span>
        </h3>
        <button type="button" aria-label="关闭" :disabled="busy" @click="emit('close')">×</button>
      </header>

      <p v-if="context.cell.course_text" class="cell-popover__current">
        当前：{{ context.cell.course_text }}
        <template v-if="context.cell.class_label">（{{ context.cell.class_label }}）</template>
        <span v-if="context.cell.source === 'override'" class="cell-popover__badge">临时</span>
      </p>
      <p v-else class="cell-popover__current is-empty">当前这一格没有课程。</p>

      <div v-if="section === 'menu'" class="cell-popover__menu">
        <button type="button" :disabled="busy" @click="section = 'note'">记一笔（本节课内容/作业）</button>
        <button type="button" :disabled="busy" @click="section = 'adjust'">临时调整此格（仅本周）</button>
        <button
          v-if="context.cell.source === 'override'"
          type="button"
          :disabled="busy"
          @click="emit('clearOverride')"
        >
          清除临时调换，恢复常规
        </button>
      </div>

      <form v-else-if="section === 'note'" class="cell-popover__form" @submit.prevent="submitNote">
        <label>
          日期
          <input v-model="noteDate" type="date" required :disabled="busy">
        </label>
        <label>
          班级
          <input v-model="noteClass" type="text" maxlength="50" required :disabled="busy">
        </label>
        <label>
          本节课内容
          <textarea v-model="noteContent" rows="3" maxlength="500" required :disabled="busy" />
        </label>
        <label>
          作业（可留空）
          <input v-model="noteHomework" type="text" maxlength="500" :disabled="busy">
        </label>
        <div class="cell-popover__actions">
          <button type="submit" :disabled="busy">保存记录</button>
          <button type="button" :disabled="busy" @click="section = 'menu'">返回</button>
        </div>
      </form>

      <form v-else class="cell-popover__form" @submit.prevent="submitOverride">
        <label>
          临时课程
          <input v-model="adjustCourse" type="text" maxlength="50" required :disabled="busy">
        </label>
        <label>
          班级（可留空）
          <input v-model="adjustClass" type="text" maxlength="50" :disabled="busy">
        </label>
        <label>
          备注（可留空）
          <input v-model="adjustRemark" type="text" maxlength="500" :disabled="busy">
        </label>
        <div class="cell-popover__actions">
          <button type="submit" :disabled="busy">保存临时调整</button>
          <button
            v-if="context.cell.source === 'regular'"
            type="button"
            :disabled="busy"
            @click="emit('clearWeekCell')"
          >
            本周此格留空
          </button>
          <button type="button" :disabled="busy" @click="section = 'menu'">返回</button>
        </div>
        <p class="cell-popover__hint">临时调整只对当前查看的这一周生效，下周自动恢复常规。</p>
      </form>

      <p v-if="formError" class="cell-popover__error" role="alert">{{ formError }}</p>
    </section>
  </div>
</template>

<style scoped>
.cell-popover__backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 16px;
  background: var(--color-overlay-mask);
}

.cell-popover {
  display: grid;
  gap: var(--space-3);
  width: min(440px, 100%);
  max-height: 85vh;
  overflow-y: auto;
  padding: 16px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-overlay);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-overlay);
}

.cell-popover__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
}

.cell-popover__header h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.cell-popover__header button {
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 20px;
  cursor: pointer;
}

.cell-popover__date {
  margin-left: 6px;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular);
}

.cell-popover__current {
  margin: 0;
  color: var(--color-text-secondary);
}

.cell-popover__current.is-empty {
  color: var(--color-text-muted);
}

.cell-popover__badge {
  margin-left: 4px;
  padding: 0 6px;
  border-radius: var(--radius-tag);
  background: var(--color-warning);
  color: var(--color-bg-surface);
  font-size: var(--font-size-caption);
}

.cell-popover__menu {
  display: grid;
  gap: var(--space-2);
}

.cell-popover__menu button,
.cell-popover__actions button {
  min-height: var(--control-height-default);
  padding: 0 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.cell-popover__actions button[type='submit'] {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  font-weight: var(--font-weight-medium);
}

.cell-popover__menu button:disabled,
.cell-popover__actions button:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.cell-popover__form {
  display: grid;
  gap: var(--space-2);
}

.cell-popover__form label {
  display: grid;
  gap: 4px;
  color: var(--color-text-secondary);
}

.cell-popover__form input,
.cell-popover__form textarea {
  padding: 6px 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.cell-popover__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.cell-popover__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.cell-popover__error {
  margin: 0;
  color: var(--color-danger);
}
</style>
