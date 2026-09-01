<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  dailyApi,
  type DailyTableCells,
  type DailyTableColumn,
  type DailyTableColumnInput,
  type DailyTableColumnPatch,
  type DailyTableDetail,
  type DailyTableRow,
} from '../../../api/daily'
import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import TableColumnEditor from './TableColumnEditor.vue'
import {
  buildCellUpdate,
  cellInputError,
  cellKey,
  COLUMN_TYPE_LABELS,
  csvFilenameFromDisposition,
  savedCellValue,
} from './tableModel'

const props = withDefaults(
  defineProps<{
    tableId: string
    /** 新建表格后带进来的提示（如部分班级名单为空）。 */
    initialNotice?: string
  }>(),
  { initialNotice: '' },
)

const emit = defineEmits<{
  close: []
}>()

const detail = ref<DailyTableDetail | null>(null)
const cells = ref<DailyTableCells>({})
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref(props.initialNotice)

const showColumns = ref(false)
const renaming = ref(false)
const renameTitle = ref('')

/** 文本/数字格的本地草稿；保存失败后保留，不清空。 */
const drafts = reactive<Record<string, string>>({})
/** 每格保存状态：saving = 请求中，error = 上次保存失败。 */
const cellStates = reactive<Record<string, 'saving' | 'error'>>({})

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

async function loadDetail(): Promise<void> {
  loadState.value = detail.value ? loadState.value : 'loading'
  try {
    const next = await dailyApi.table(props.tableId)
    detail.value = next
    cells.value = { ...next.cells }
    loadState.value = 'ready'
  } catch (error) {
    loadState.value = 'error'
    errorMessage.value = errorText(error, '表格暂时无法读取，请稍后重试。')
  }
}

async function refreshDetail(): Promise<void> {
  const next = await dailyApi.table(props.tableId)
  detail.value = next
  cells.value = { ...next.cells }
}

function savedValue(row: DailyTableRow, column: DailyTableColumn): string {
  return savedCellValue(cells.value, row.id, column.id)
}

function draftValue(row: DailyTableRow, column: DailyTableColumn): string {
  return drafts[cellKey(row.id, column.id)] ?? savedValue(row, column)
}

function onDraftInput(row: DailyTableRow, column: DailyTableColumn, event: Event): void {
  const key = cellKey(row.id, column.id)
  drafts[key] = (event.target as HTMLInputElement).value
  delete cellStates[key]
}

function onDraftBlur(row: DailyTableRow, column: DailyTableColumn): void {
  const key = cellKey(row.id, column.id)
  const draft = drafts[key]
  if (draft === undefined) return
  if (draft.trim() === savedValue(row, column)) {
    delete drafts[key]
    return
  }
  void saveCell(row, column, draft)
}

async function saveCell(
  row: DailyTableRow,
  column: DailyTableColumn,
  input: string | boolean,
): Promise<void> {
  const current = detail.value
  if (!current) return
  const key = cellKey(row.id, column.id)
  const update = buildCellUpdate(column, row.id, input)
  if (update.value_text === savedValue(row, column)) {
    delete drafts[key]
    delete cellStates[key]
    return
  }
  const validation = cellInputError(column, update.value_text)
  if (validation) {
    cellStates[key] = 'error'
    errorMessage.value = `「${column.name}」${validation}`
    return
  }
  cellStates[key] = 'saving'
  errorMessage.value = ''
  try {
    await dailyApi.putTableCells(current.id, [update])
    const rowCells = { ...(cells.value[row.id] ?? {}) }
    if (update.value_text === '') delete rowCells[column.id]
    else rowCells[column.id] = update.value_text
    cells.value = { ...cells.value, [row.id]: rowCells }
    delete drafts[key]
    delete cellStates[key]
  } catch (error) {
    // 失败不清本地编辑值：草稿保留，格子标记为未保存。
    cellStates[key] = 'error'
    errorMessage.value = errorText(error, '这一格没有保存，请检查内容后再试。')
  }
}

function onCheckChange(row: DailyTableRow, column: DailyTableColumn, event: Event): void {
  void saveCell(row, column, (event.target as HTMLInputElement).checked)
}

function onValueChange(row: DailyTableRow, column: DailyTableColumn, event: Event): void {
  void saveCell(row, column, (event.target as HTMLInputElement | HTMLSelectElement).value)
}

async function runHeaderMutation(action: () => Promise<unknown>, notice: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    await action()
    await refreshDetail()
    noticeMessage.value = notice
  } catch (error) {
    errorMessage.value = errorText(error, '操作没有保存，请重试。')
  } finally {
    busy.value = false
  }
}

function startRename(): void {
  renameTitle.value = detail.value?.title ?? ''
  renaming.value = true
}

function submitRename(): void {
  const current = detail.value
  const title = renameTitle.value.trim()
  if (!current || busy.value) return
  if (!title) {
    errorMessage.value = '表格名称不能为空。'
    return
  }
  if (title === current.title) {
    renaming.value = false
    return
  }
  void runHeaderMutation(async () => {
    await dailyApi.updateTable(current.id, { title })
    renaming.value = false
  }, '已重命名表格。')
}

function toggleArchive(): void {
  const current = detail.value
  if (!current) return
  const next = current.status === 'archived' ? 'active' : 'archived'
  void runHeaderMutation(
    () => dailyApi.updateTable(current.id, { status: next }),
    next === 'archived' ? '已归档，回到列表后在「已归档」分区查看。' : '已恢复为进行中。',
  )
}

async function removeTable(): Promise<void> {
  const current = detail.value
  if (!current || busy.value) return
  const confirmed = window.confirm(
    `删除表格「${current.title}」会连带删除其中的所有记录，且无法恢复，确定删除吗？`,
  )
  if (!confirmed) return
  busy.value = true
  errorMessage.value = ''
  try {
    await dailyApi.deleteTable(current.id)
    emit('close')
  } catch (error) {
    busy.value = false
    errorMessage.value = errorText(error, '删除失败，请重试。')
  }
}

async function exportCsv(): Promise<void> {
  const current = detail.value
  if (!current || busy.value) return
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await dailyApi.exportTableCsv(current.id)
    const url = URL.createObjectURL(response.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = csvFilenameFromDisposition(response.contentDisposition, current.title)
    link.click()
    URL.revokeObjectURL(url)
    noticeMessage.value = '已导出 CSV，可用 Excel 或 WPS 直接打开。'
  } catch (error) {
    errorMessage.value = errorText(error, '导出失败，请重试。')
  } finally {
    busy.value = false
  }
}

function addColumn(payload: DailyTableColumnInput): void {
  void runHeaderMutation(
    () => dailyApi.addTableColumn(props.tableId, payload),
    `已添加列「${payload.name}」。`,
  )
}

function updateColumn(payload: { columnId: string; patch: DailyTableColumnPatch }): void {
  void runHeaderMutation(
    () => dailyApi.updateTableColumn(props.tableId, payload.columnId, payload.patch),
    '列设置已保存。',
  )
}

function moveColumn(payload: { columnId: string; position: number }): void {
  void runHeaderMutation(
    () => dailyApi.updateTableColumn(props.tableId, payload.columnId, { position: payload.position }),
    '已调整列顺序。',
  )
}

function removeColumn(column: DailyTableColumn): void {
  const confirmed = window.confirm(
    `删除列「${column.name}」会连带清空该列已填写的所有内容，确定删除吗？`,
  )
  if (!confirmed) return
  void runHeaderMutation(
    () => dailyApi.deleteTableColumn(props.tableId, column.id),
    `已删除列「${column.name}」。`,
  )
}

onMounted(loadDetail)
</script>

<template>
  <div class="table-editor">
    <StatePanel
      v-if="loadState === 'loading'"
      kind="loading"
      title="正在读取表格"
      description="表格内容与列设置加载中。"
    />
    <StatePanel
      v-else-if="loadState === 'error'"
      kind="error"
      title="表格暂时无法读取"
      :description="errorMessage"
      retry-label="重新读取"
      @retry="loadDetail"
    />
    <template v-else-if="detail">
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
        title="已完成"
        :description="noticeMessage"
        dismissible
        @dismiss="noticeMessage = ''"
      />

      <div class="table-editor__bar">
        <button type="button" class="table-editor__back" @click="emit('close')">← 返回列表</button>
        <div class="table-editor__title">
          <template v-if="!renaming">
            <h2>
              {{ detail.title }}
              <span v-if="detail.status === 'archived'" class="table-editor__badge">已归档</span>
            </h2>
            <button type="button" :disabled="busy" @click="startRename">重命名</button>
          </template>
          <form v-else class="table-editor__rename" @submit.prevent="submitRename">
            <input
              v-model="renameTitle"
              type="text"
              maxlength="50"
              aria-label="表格名称"
              :disabled="busy"
            >
            <button type="submit" :disabled="busy">保存</button>
            <button type="button" :disabled="busy" @click="renaming = false">取消</button>
          </form>
        </div>
        <div class="table-editor__actions">
          <button
            type="button"
            :aria-expanded="showColumns"
            :disabled="busy"
            @click="showColumns = !showColumns"
          >
            {{ showColumns ? '收起列管理' : '列管理' }}
          </button>
          <button type="button" :disabled="busy" @click="exportCsv">导出 CSV</button>
          <button type="button" :disabled="busy" @click="toggleArchive">
            {{ detail.status === 'archived' ? '恢复' : '归档' }}
          </button>
          <button
            type="button"
            class="table-editor__danger"
            :disabled="busy"
            @click="removeTable"
          >
            删除
          </button>
        </div>
      </div>
      <p class="table-editor__hint">
        学生行来自创建时的名单快照，之后名单变动不会自动同步；单元格编辑后自动保存。
      </p>

      <TableColumnEditor
        v-if="showColumns"
        :columns="detail.columns"
        :busy="busy"
        @add="addColumn"
        @update="updateColumn"
        @move="moveColumn"
        @remove="removeColumn"
      />

      <p v-if="!detail.columns.length" class="table-editor__empty">
        这个表格还没有列，点「列管理」添加文本 / 数字 / 打勾 / 单选 / 日期列。
      </p>
      <p v-else-if="!detail.rows.length" class="table-editor__empty">
        这个表格没有学生行（创建时所选班级的名单为空）。
      </p>
      <div v-else class="table-editor__scroll">
        <table class="table-grid">
          <thead>
            <tr>
              <th class="table-grid__student" scope="col">学生</th>
              <th v-for="column in detail.columns" :key="column.id" scope="col">
                <span class="table-grid__column-name">{{ column.name }}</span>
                <span class="table-grid__column-type">{{ COLUMN_TYPE_LABELS[column.col_type] }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in detail.rows" :key="row.id">
              <th class="table-grid__student" scope="row">
                <span class="table-grid__name">{{ row.display_name }}</span>
                <span class="table-grid__class">{{ row.class_label }}</span>
              </th>
              <td
                v-for="column in detail.columns"
                :key="column.id"
                :data-state="cellStates[cellKey(row.id, column.id)]"
              >
                <input
                  v-if="column.col_type === 'check'"
                  type="checkbox"
                  :checked="savedValue(row, column) === '1'"
                  :aria-label="`${row.display_name}·${column.name}`"
                  @change="onCheckChange(row, column, $event)"
                >
                <select
                  v-else-if="column.col_type === 'select'"
                  :value="savedValue(row, column)"
                  :aria-label="`${row.display_name}·${column.name}`"
                  @change="onValueChange(row, column, $event)"
                >
                  <option value="">（空）</option>
                  <option v-for="option in column.options" :key="option" :value="option">
                    {{ option }}
                  </option>
                </select>
                <input
                  v-else-if="column.col_type === 'date'"
                  type="date"
                  :value="savedValue(row, column)"
                  :aria-label="`${row.display_name}·${column.name}`"
                  @change="onValueChange(row, column, $event)"
                >
                <input
                  v-else-if="column.col_type === 'number'"
                  type="number"
                  :value="draftValue(row, column)"
                  :aria-label="`${row.display_name}·${column.name}`"
                  @input="onDraftInput(row, column, $event)"
                  @blur="onDraftBlur(row, column)"
                >
                <input
                  v-else
                  type="text"
                  maxlength="500"
                  :value="draftValue(row, column)"
                  :aria-label="`${row.display_name}·${column.name}`"
                  @input="onDraftInput(row, column, $event)"
                  @blur="onDraftBlur(row, column)"
                >
                <span
                  v-if="cellStates[cellKey(row.id, column.id)] === 'saving'"
                  class="table-grid__status"
                >保存中…</span>
                <span
                  v-else-if="cellStates[cellKey(row.id, column.id)] === 'error'"
                  class="table-grid__status table-grid__status--error"
                >未保存</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.table-editor {
  display: grid;
  gap: var(--space-3);
}

.table-editor button {
  font: inherit;
  cursor: pointer;
}

.table-editor__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.table-editor__back {
  min-height: var(--control-height-default);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.table-editor__title {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  min-width: 200px;
}

.table-editor__title h2 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.table-editor__badge {
  margin-left: 6px;
  padding: 2px 8px;
  border-radius: var(--radius-tag);
  background: var(--color-bg-selected);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-normal);
}

.table-editor__title > button,
.table-editor__rename button,
.table-editor__actions button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.table-editor__actions .table-editor__danger {
  color: var(--color-danger);
}

.table-editor__rename {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.table-editor__rename input {
  min-height: var(--control-height-default);
  padding: 0 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.table-editor__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.table-editor__hint,
.table-editor__empty {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-editor__scroll {
  overflow-x: auto;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.table-grid {
  min-width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.table-grid th,
.table-grid td {
  padding: 6px 8px;
  border-bottom: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-primary);
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}

.table-grid tbody tr:last-child th,
.table-grid tbody tr:last-child td {
  border-bottom: 0;
}

.table-grid__student {
  position: sticky;
  left: 0;
  z-index: 1;
  min-width: 120px;
  background: var(--color-bg-surface);
  font-weight: var(--font-weight-normal);
}

.table-grid__name {
  font-weight: var(--font-weight-medium);
}

.table-grid__class {
  margin-left: 6px;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-grid__column-type {
  margin-left: 6px;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-normal);
}

.table-grid td input[type='text'],
.table-grid td input[type='number'],
.table-grid td input[type='date'],
.table-grid td select {
  min-height: var(--control-height-small);
  min-width: 120px;
  padding: 0 8px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.table-grid td[data-state='error'] input,
.table-grid td[data-state='error'] select {
  border-color: var(--color-danger);
}

.table-grid__status {
  margin-left: 6px;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-grid__status--error {
  color: var(--color-danger);
}
</style>
