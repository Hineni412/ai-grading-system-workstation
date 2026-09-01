<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '../../../api/errors'
import {
  dailyApi,
  type DailyTableColumnInput,
  type DailyTableColumnType,
  type DailyTableStatus,
  type DailyTableSummary,
  type RosterClass,
} from '../../../api/daily'
import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import TableGridEditor from './TableGridEditor.vue'
import {
  COLUMN_TYPE_LABELS,
  COLUMN_TYPE_OPTIONS,
  formatTableTime,
  parseOptionsInput,
  partitionTables,
} from './tableModel'

const tables = ref<DailyTableSummary[] | null>(null)
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')

const openTableId = ref<string | null>(null)
const editorNotice = ref('')

// 新建向导
const wizardOpen = ref(false)
const wizardTitle = ref('')
const roster = ref<RosterClass[] | null>(null)
const rosterState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const rosterError = ref('')
const selectedClasses = ref<string[]>([])
const draftColumns = ref<DailyTableColumnInput[]>([])
const newColumnName = ref('')
const newColumnType = ref<DailyTableColumnType>('check')
const newColumnOptions = ref('')
const wizardError = ref('')
const creating = ref(false)

const columnTypeOptions = COLUMN_TYPE_OPTIONS

const groups = computed(() => partitionTables(tables.value ?? []))

const selectedStudentCount = computed(() => {
  const counts = new Map((roster.value ?? []).map((klass) => [klass.class_label, klass.student_count]))
  return selectedClasses.value.reduce((sum, label) => sum + (counts.get(label) ?? 0), 0)
})

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

async function loadTables(): Promise<void> {
  loadState.value = tables.value ? loadState.value : 'loading'
  try {
    tables.value = await dailyApi.tables('all')
    loadState.value = 'ready'
  } catch (error) {
    loadState.value = 'error'
    errorMessage.value = errorText(error, '表格列表暂时无法读取，请稍后重试。')
  }
}

async function runMutation(action: () => Promise<unknown>, notice: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    await action()
    await loadTables()
    noticeMessage.value = notice
  } catch (error) {
    errorMessage.value = errorText(error, '操作没有保存，请刷新后重试。')
  } finally {
    busy.value = false
  }
}

function setTableStatus(table: DailyTableSummary, status: DailyTableStatus): void {
  void runMutation(
    () => dailyApi.updateTable(table.id, { status }),
    status === 'archived' ? `已归档「${table.title}」。` : `已恢复「${table.title}」。`,
  )
}

function removeTable(table: DailyTableSummary): void {
  const confirmed = window.confirm(
    `删除表格「${table.title}」会连带删除其中的所有记录，且无法恢复，确定删除吗？`,
  )
  if (!confirmed) return
  void runMutation(() => dailyApi.deleteTable(table.id), `已删除「${table.title}」。`)
}

async function loadRoster(): Promise<void> {
  rosterState.value = 'loading'
  rosterError.value = ''
  try {
    roster.value = await dailyApi.rosterClasses()
    rosterState.value = 'ready'
  } catch (error) {
    rosterState.value = 'error'
    rosterError.value = errorText(error, '名单暂时无法读取，请稍后重试。')
  }
}

function toggleWizard(): void {
  wizardOpen.value = !wizardOpen.value
  wizardError.value = ''
  if (wizardOpen.value && (rosterState.value === 'idle' || rosterState.value === 'error')) {
    void loadRoster()
  }
}

function resetWizard(): void {
  wizardTitle.value = ''
  selectedClasses.value = []
  draftColumns.value = []
  newColumnName.value = ''
  newColumnType.value = 'check'
  newColumnOptions.value = ''
  wizardError.value = ''
}

function toggleClass(label: string): void {
  selectedClasses.value = selectedClasses.value.includes(label)
    ? selectedClasses.value.filter((item) => item !== label)
    : [...selectedClasses.value, label]
}

function addDraftColumn(): void {
  const name = newColumnName.value.trim()
  if (!name) {
    wizardError.value = '列名不能为空。'
    return
  }
  if (newColumnType.value === 'select') {
    const options = parseOptionsInput(newColumnOptions.value)
    if (!options.length) {
      wizardError.value = '单选列需要至少一个选项（用逗号分隔）。'
      return
    }
    draftColumns.value.push({ name, col_type: 'select', options })
  } else {
    draftColumns.value.push({ name, col_type: newColumnType.value })
  }
  wizardError.value = ''
  newColumnName.value = ''
  newColumnOptions.value = ''
}

function removeDraftColumn(index: number): void {
  draftColumns.value = draftColumns.value.filter((_, itemIndex) => itemIndex !== index)
}

async function createTable(): Promise<void> {
  if (creating.value) return
  const title = wizardTitle.value.trim()
  if (!title) {
    wizardError.value = '请先填写表格名称。'
    return
  }
  if (!selectedClasses.value.length) {
    wizardError.value = '请至少选择一个班级来生成学生行。'
    return
  }
  wizardError.value = ''
  creating.value = true
  try {
    const created = await dailyApi.createTable({
      title,
      class_labels: [...selectedClasses.value],
      ...(draftColumns.value.length ? { columns: [...draftColumns.value] } : {}),
    })
    editorNotice.value = created.empty_class_labels.length
      ? `班级 ${created.empty_class_labels.join('、')} 的名单为空，没有为这些班生成学生行。`
      : ''
    wizardOpen.value = false
    resetWizard()
    openTableId.value = created.id
  } catch (error) {
    wizardError.value = errorText(error, '表格创建失败，请重试。')
  } finally {
    creating.value = false
  }
}

function closeEditor(): void {
  openTableId.value = null
  editorNotice.value = ''
  void loadTables()
}

onMounted(loadTables)
</script>

<template>
  <div class="tables-panel">
    <TableGridEditor
      v-if="openTableId"
      :table-id="openTableId"
      :initial-notice="editorNotice"
      @close="closeEditor"
    />
    <template v-else>
      <StatePanel
        v-if="loadState === 'loading'"
        kind="loading"
        title="正在读取表格"
        description="表格列表加载中。"
      />
      <StatePanel
        v-else-if="loadState === 'error'"
        kind="error"
        title="表格列表暂时无法读取"
        :description="errorMessage"
        retry-label="重新读取"
        @retry="loadTables"
      />
      <template v-else>
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

        <div class="tables-panel__toolbar">
          <p class="tables-panel__hint">
            表格按学生名单生成行，可自定义文本 / 数字 / 打勾 / 单选 / 日期列，随时导出 CSV。
          </p>
          <button
            type="button"
            class="tables-panel__create"
            :aria-expanded="wizardOpen"
            @click="toggleWizard"
          >
            {{ wizardOpen ? '收起新建' : '新建表格' }}
          </button>
        </div>

        <section v-if="wizardOpen" class="table-wizard" aria-label="新建表格">
          <h3>新建表格</h3>
          <label class="table-wizard__field">
            表格名称
            <input
              v-model="wizardTitle"
              type="text"
              maxlength="50"
              placeholder="如：研学回执统计"
              :disabled="creating"
            >
          </label>

          <div class="table-wizard__classes">
            <p class="table-wizard__label">选择班级（按名单生成学生行）</p>
            <p v-if="rosterState === 'loading'" class="table-wizard__note">正在读取名单…</p>
            <p v-else-if="rosterState === 'error'" class="table-wizard__error" role="alert">
              {{ rosterError }}
              <button type="button" @click="loadRoster">重试</button>
            </p>
            <p v-else-if="roster && !roster.length" class="table-wizard__note">
              名单为空：请先在学生管理中导入学生名单，再回来创建表格。
            </p>
            <ul v-else-if="roster" class="table-wizard__class-list">
              <li v-for="klass in roster" :key="klass.class_label">
                <label>
                  <input
                    type="checkbox"
                    :checked="selectedClasses.includes(klass.class_label)"
                    :disabled="creating"
                    @change="toggleClass(klass.class_label)"
                  >
                  {{ klass.class_label }}（{{ klass.student_count }} 人）
                </label>
              </li>
            </ul>
            <p v-if="roster?.length" class="table-wizard__note">
              已选 {{ selectedClasses.length }} 个班，共 {{ selectedStudentCount }} 人。
            </p>
          </div>

          <div class="table-wizard__columns">
            <p class="table-wizard__label">预建列（可选，创建后还能再改）</p>
            <ul v-if="draftColumns.length" class="table-wizard__column-list">
              <li v-for="(column, index) in draftColumns" :key="`${column.name}-${index}`">
                <span class="table-wizard__column-name">{{ column.name }}</span>
                <span class="table-wizard__column-type">{{ COLUMN_TYPE_LABELS[column.col_type] }}</span>
                <span v-if="column.options?.length" class="table-wizard__column-options">
                  {{ column.options.join(' / ') }}
                </span>
                <button type="button" :disabled="creating" @click="removeDraftColumn(index)">
                  移除
                </button>
              </li>
            </ul>
            <div class="table-wizard__column-form">
              <input
                v-model="newColumnName"
                type="text"
                maxlength="30"
                placeholder="列名，如：已交"
                :disabled="creating"
                aria-label="预建列列名"
              >
              <select v-model="newColumnType" :disabled="creating" aria-label="预建列类型">
                <option v-for="option in columnTypeOptions" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
              <input
                v-if="newColumnType === 'select'"
                v-model="newColumnOptions"
                type="text"
                placeholder="选项，用逗号分隔，如：已交，未交"
                :disabled="creating"
                aria-label="预建列选项"
              >
              <button type="button" :disabled="creating" @click="addDraftColumn">添加列</button>
            </div>
          </div>

          <p class="table-wizard__snapshot">
            创建时按当前名单快照生成学生行；之后名单变动不会自动同步到已建表格。
          </p>
          <p v-if="wizardError" class="table-wizard__error" role="alert">{{ wizardError }}</p>
          <button
            type="button"
            class="table-wizard__submit"
            :disabled="creating"
            @click="createTable"
          >
            {{ creating ? '正在创建…' : '创建表格' }}
          </button>
        </section>

        <section class="tables-section" aria-label="进行中的表格">
          <h3>进行中</h3>
          <ul v-if="groups.active.length" class="tables-section__list">
            <li v-for="table in groups.active" :key="table.id" class="table-item">
              <button type="button" class="table-item__main" @click="openTableId = table.id">
                <span class="table-item__title">{{ table.title }}</span>
                <span class="table-item__meta">
                  {{ table.row_count }} 行 × {{ table.column_count }} 列 · 更新于
                  {{ formatTableTime(table.updated_at) }}
                </span>
              </button>
              <div class="table-item__actions">
                <button type="button" :disabled="busy" @click="setTableStatus(table, 'archived')">
                  归档
                </button>
                <button
                  type="button"
                  class="table-item__danger"
                  :disabled="busy"
                  @click="removeTable(table)"
                >
                  删除
                </button>
              </div>
            </li>
          </ul>
          <p v-else class="tables-section__empty">暂无进行中的表格，点「新建表格」开始。</p>
        </section>

        <section class="tables-section" aria-label="已归档的表格">
          <h3>已归档</h3>
          <ul v-if="groups.archived.length" class="tables-section__list">
            <li v-for="table in groups.archived" :key="table.id" class="table-item">
              <button type="button" class="table-item__main" @click="openTableId = table.id">
                <span class="table-item__title">{{ table.title }}</span>
                <span class="table-item__meta">
                  {{ table.row_count }} 行 × {{ table.column_count }} 列 · 更新于
                  {{ formatTableTime(table.updated_at) }}
                </span>
              </button>
              <div class="table-item__actions">
                <button type="button" :disabled="busy" @click="setTableStatus(table, 'active')">
                  恢复
                </button>
                <button
                  type="button"
                  class="table-item__danger"
                  :disabled="busy"
                  @click="removeTable(table)"
                >
                  删除
                </button>
              </div>
            </li>
          </ul>
          <p v-else class="tables-section__empty">暂无已归档的表格。</p>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.tables-panel {
  display: grid;
  gap: var(--space-3);
}

.tables-panel__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.tables-panel__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.tables-panel button {
  font: inherit;
  cursor: pointer;
}

.tables-panel__create,
.table-wizard__submit {
  min-height: var(--control-height-default);
  padding: 0 14px;
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent-subtle);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.table-wizard {
  display: grid;
  gap: var(--space-3);
  padding: 12px 14px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.table-wizard h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.table-wizard__field {
  display: grid;
  gap: 4px;
  max-width: 320px;
  color: var(--color-text-secondary);
}

.table-wizard__label {
  margin: 0 0 4px;
  color: var(--color-text-secondary);
}

.table-wizard__field input,
.table-wizard__column-form input,
.table-wizard__column-form select {
  min-height: var(--control-height-default);
  padding: 0 10px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font: inherit;
}

.table-wizard__class-list,
.table-wizard__column-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.table-wizard__class-list label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-primary);
  cursor: pointer;
}

.table-wizard__note,
.table-wizard__snapshot {
  margin: 4px 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-wizard__column-list li {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.table-wizard__column-name {
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}

.table-wizard__column-type,
.table-wizard__column-options {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-wizard__column-form {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: 6px;
}

.table-wizard__column-form button,
.table-wizard__error button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.table-wizard__error {
  margin: 0;
  color: var(--color-danger);
}

.table-wizard__submit {
  justify-self: start;
}

.tables-section {
  display: grid;
  gap: var(--space-2);
}

.tables-section h3 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.tables-section__list {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.tables-section__empty {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 10px 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
}

.table-item__main {
  display: grid;
  gap: 2px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-text-primary);
  text-align: left;
}

.table-item__title {
  font-weight: var(--font-weight-medium);
}

.table-item__meta {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.table-item__actions {
  display: flex;
  gap: var(--space-2);
}

.table-item__actions button {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.table-item__actions .table-item__danger {
  color: var(--color-danger);
}
</style>
