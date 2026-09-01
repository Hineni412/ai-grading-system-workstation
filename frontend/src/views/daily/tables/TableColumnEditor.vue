<script setup lang="ts">
import { computed, ref } from 'vue'

import type {
  DailyTableColumn,
  DailyTableColumnInput,
  DailyTableColumnPatch,
  DailyTableColumnType,
} from '../../../api/daily'
import { COLUMN_TYPE_LABELS, COLUMN_TYPE_OPTIONS, parseOptionsInput } from './tableModel'

const props = withDefaults(
  defineProps<{
    columns: DailyTableColumn[]
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{
  add: [payload: DailyTableColumnInput]
  update: [payload: { columnId: string; patch: DailyTableColumnPatch }]
  move: [payload: { columnId: string; position: number }]
  remove: [column: DailyTableColumn]
}>()

const columnTypeOptions = COLUMN_TYPE_OPTIONS

const sorted = computed(() => [...props.columns].sort((a, b) => a.position - b.position))

const newName = ref('')
const newType = ref<DailyTableColumnType>('text')
const newOptions = ref('')

const editingId = ref<string | null>(null)
const editName = ref('')
const editType = ref<DailyTableColumnType>('text')
const editOptions = ref('')

const formError = ref('')

function buildPayload(
  name: string,
  colType: DailyTableColumnType,
  optionsRaw: string,
): DailyTableColumnInput | null {
  const trimmed = name.trim()
  if (!trimmed) {
    formError.value = '列名不能为空。'
    return null
  }
  if (colType === 'select') {
    const options = parseOptionsInput(optionsRaw)
    if (!options.length) {
      formError.value = '单选列需要至少一个选项（用逗号分隔）。'
      return null
    }
    return { name: trimmed, col_type: 'select', options }
  }
  return { name: trimmed, col_type: colType }
}

function submitAdd(): void {
  const payload = buildPayload(newName.value, newType.value, newOptions.value)
  if (!payload) return
  formError.value = ''
  emit('add', payload)
  newName.value = ''
  newOptions.value = ''
}

function startEdit(column: DailyTableColumn): void {
  editingId.value = column.id
  editName.value = column.name
  editType.value = column.col_type
  editOptions.value = column.options.join('，')
  formError.value = ''
}

function submitEdit(column: DailyTableColumn): void {
  const payload = buildPayload(editName.value, editType.value, editOptions.value)
  if (!payload) return
  const patch: DailyTableColumnPatch = {}
  if (payload.name !== column.name) patch.name = payload.name
  if (payload.col_type !== column.col_type) patch.col_type = payload.col_type
  if (payload.col_type === 'select') {
    const next = payload.options ?? []
    if (payload.col_type !== column.col_type || next.join('|') !== column.options.join('|')) {
      patch.options = next
    }
  }
  formError.value = ''
  editingId.value = null
  if (Object.keys(patch).length) emit('update', { columnId: column.id, patch })
}
</script>

<template>
  <section class="column-editor" aria-label="列管理">
    <h3>列管理</h3>
    <ul v-if="sorted.length" class="column-editor__list">
      <li v-for="(column, index) in sorted" :key="column.id">
        <template v-if="editingId !== column.id">
          <span class="column-editor__name">{{ column.name }}</span>
          <span class="column-editor__type">{{ COLUMN_TYPE_LABELS[column.col_type] }}</span>
          <span v-if="column.options.length" class="column-editor__options">
            {{ column.options.join(' / ') }}
          </span>
          <span class="column-editor__ops">
            <button
              type="button"
              :disabled="busy || index === 0"
              :aria-label="`左移 ${column.name}`"
              @click="emit('move', { columnId: column.id, position: index - 1 })"
            >
              ←
            </button>
            <button
              type="button"
              :disabled="busy || index === sorted.length - 1"
              :aria-label="`右移 ${column.name}`"
              @click="emit('move', { columnId: column.id, position: index + 1 })"
            >
              →
            </button>
            <button type="button" :disabled="busy" @click="startEdit(column)">编辑</button>
            <button
              type="button"
              class="column-editor__danger"
              :disabled="busy"
              @click="emit('remove', column)"
            >
              删除
            </button>
          </span>
        </template>
        <form v-else class="column-editor__edit" @submit.prevent="submitEdit(column)">
          <input
            v-model="editName"
            type="text"
            maxlength="30"
            aria-label="列名"
            :disabled="busy"
          >
          <select v-model="editType" aria-label="列类型" :disabled="busy">
            <option v-for="option in columnTypeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
          <input
            v-if="editType === 'select'"
            v-model="editOptions"
            type="text"
            placeholder="选项，用逗号分隔"
            aria-label="列选项"
            :disabled="busy"
          >
          <button type="submit" :disabled="busy">保存</button>
          <button type="button" :disabled="busy" @click="editingId = null">取消</button>
        </form>
      </li>
    </ul>
    <p v-else class="column-editor__empty">还没有列，请在下方添加。</p>

    <form class="column-editor__add" @submit.prevent="submitAdd">
      <input
        v-model="newName"
        type="text"
        maxlength="30"
        placeholder="列名，如：已交"
        aria-label="新列列名"
        :disabled="busy"
      >
      <select v-model="newType" aria-label="新列类型" :disabled="busy">
        <option v-for="option in columnTypeOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
      <input
        v-if="newType === 'select'"
        v-model="newOptions"
        type="text"
        placeholder="选项，用逗号分隔，如：已交，未交"
        aria-label="新列选项"
        :disabled="busy"
      >
      <button type="submit" :disabled="busy">添加列</button>
    </form>
    <p class="column-editor__hint">修改列类型不会清空该列已填内容；删除列才会连带清空。</p>
    <p v-if="formError" class="column-editor__error" role="alert">{{ formError }}</p>
  </section>
</template>

<style scoped>
.column-editor {
  display: grid;
  gap: var(--space-2);
  padding: 12px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.column-editor h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.column-editor button {
  font: inherit;
  cursor: pointer;
}

.column-editor__list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.column-editor__list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.column-editor__name {
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.column-editor__type,
.column-editor__options,
.column-editor__empty,
.column-editor__hint {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.column-editor__empty,
.column-editor__hint {
  margin: 0;
}

.column-editor__ops {
  display: inline-flex;
  gap: 4px;
}

.column-editor__ops button,
.column-editor__edit button,
.column-editor__add button {
  min-height: var(--control-height-small);
  padding: 0 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.column-editor__ops .column-editor__danger {
  color: var(--color-danger);
}

.column-editor__edit,
.column-editor__add {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.column-editor__edit input,
.column-editor__edit select,
.column-editor__add input,
.column-editor__add select {
  min-height: var(--control-height-default);
  padding: 0 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.column-editor__error {
  margin: 0;
  color: var(--color-danger);
}
</style>
