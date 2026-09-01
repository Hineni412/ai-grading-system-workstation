<script setup lang="ts">
import { ref, watch } from 'vue'

import type { TimetableCell } from '../../../api/daily'

export interface RegularEditContext {
  cell: TimetableCell
  dayLabel: string
  slotLabel: string
}

const props = withDefaults(defineProps<{
  context: RegularEditContext | null
  busy?: boolean
}>(), {
  busy: false,
})

const emit = defineEmits<{
  save: [payload: { course_text: string; class_label: string }]
  clear: []
  close: []
}>()

const course = ref('')
const classLabel = ref('')
const formError = ref('')

watch(
  () => props.context,
  (context) => {
    course.value = context?.cell.course_text ?? ''
    classLabel.value = context?.cell.class_label ?? ''
    formError.value = ''
  },
  { immediate: true },
)

function submit(): void {
  if (!course.value.trim()) {
    formError.value = '课程内容不能为空；要清空这一格请用「清空此常规格」。'
    return
  }
  formError.value = ''
  emit('save', { course_text: course.value.trim(), class_label: classLabel.value.trim() })
}
</script>

<template>
  <section class="regular-edit" aria-label="常规课表编辑">
    <template v-if="context">
      <header class="regular-edit__header">
        <h3>编辑常规格：{{ context.dayLabel }} · {{ context.slotLabel }}</h3>
        <button type="button" :disabled="busy" @click="emit('close')">收起</button>
      </header>
      <form class="regular-edit__form" @submit.prevent="submit">
        <label>
          课程
          <input v-model="course" type="text" maxlength="50" :disabled="busy">
        </label>
        <label>
          班级（可留空）
          <input v-model="classLabel" type="text" maxlength="50" :disabled="busy">
        </label>
        <div class="regular-edit__actions">
          <button type="submit" :disabled="busy">保存常规格</button>
          <button
            v-if="context.cell.course_text"
            type="button"
            :disabled="busy"
            @click="emit('clear')"
          >
            清空此常规格
          </button>
        </div>
        <p v-if="context.cell.source === 'override'" class="regular-edit__hint">
          这一格本周已被临时调整覆盖；保存常规课表不会改变本周的临时内容。
        </p>
        <p v-if="formError" class="regular-edit__error" role="alert">{{ formError }}</p>
      </form>
    </template>
    <p v-else class="regular-edit__placeholder">点击课格开始编辑常规课表；修改对所有周生效。</p>
  </section>
</template>

<style scoped>
.regular-edit {
  display: grid;
  gap: var(--space-2);
  padding: 12px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.regular-edit__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.regular-edit__header h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.regular-edit__header button,
.regular-edit__actions button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
  cursor: pointer;
}

.regular-edit__actions button[type='submit'] {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  font-weight: var(--font-weight-medium);
}

.regular-edit__form {
  display: grid;
  gap: var(--space-2);
  max-width: 420px;
}

.regular-edit__form label {
  display: grid;
  gap: 4px;
  color: var(--color-text-secondary);
}

.regular-edit__form input {
  padding: 6px 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.regular-edit__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.regular-edit__hint,
.regular-edit__placeholder {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.regular-edit__error {
  margin: 0;
  color: var(--color-danger);
}
</style>
