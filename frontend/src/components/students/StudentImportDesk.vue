<script setup lang="ts">
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, ref } from 'vue'
import AppDialog from '../design-system/AppDialog.vue'

import type { StudentImportOperation } from '../../api/students'
import { useStudentRosterStore } from '../../stores/students'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'

const roster = useStudentRosterStore()
const selectedFile = ref<File | null>(null)

const open = computed({ get: () => Boolean(roster.preview), set: value => { if (!value && roster.importState !== 'committing') roster.resetImport() } })
const onlyChanges = ref(true)
const visibleRows = computed(() => roster.preview?.rows.filter(row => !onlyChanges.value || row.operation !== 'unchanged') ?? [])
const picker = ref<HTMLInputElement | null>(null)
const operationLabels: Record<StudentImportOperation, string> = {
  insert: '新增',
  update: '更新',
  unchanged: '无变化',
  invalid: '有问题',
  duplicate: '重复行',
}

const operationTones: Record<StudentImportOperation, 'success' | 'info' | 'neutral' | 'danger' | 'warning'> = {
  insert: 'success',
  update: 'info',
  unchanged: 'neutral',
  invalid: 'danger',
  duplicate: 'warning',
}

function chooseFile(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0] ?? null
  selectedFile.value = file
  onlyChanges.value = true
  input.value = ''
  if (file) void roster.previewFile(file)
}

function applyMapping(): void {
  if (selectedFile.value) {
    void roster.previewFile(selectedFile.value, { ...roster.importMapping })
  }
}

async function commit(): Promise<void> {
  await roster.commitPreview()
  if (roster.importState === 'committed') {
    roster.resetImport()
    await roster.load({ page: 1 })
  }
}
</script>
<template>
  <div class="student-import-trigger">
    <span class="settings-note">CSV / Excel</span>
    <AppButton variant="secondary" :disabled="roster.importState === 'previewing'" @click="picker?.click()">{{ roster.importState === 'previewing' ? '正在比对…' : '导入名单' }}</AppButton>
    <input ref="picker" class="sr-only" type="file" aria-label="选择学生名单" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" @change="chooseFile">
  </div>
  <AppDialog
    :open="open"
    title="导入名单"
    class="student-import-dialog"
    :dismissible="roster.importState !== 'committing'"
    @update:open="(value: boolean) => { open = value }"
  >
    <template #header-extra>
      <span class="settings-note">· {{ selectedFile?.name }}</span>
      <AppButton variant="ghost" :disabled="roster.importState === 'committing' || roster.importState === 'previewing'" @click="picker?.click()">换一个文件</AppButton>
    </template>
        <template v-if="roster.preview">
          <div class="student-import__mapping">
            <span>表格里哪一列是：</span>
            <label v-for="field in (['student_code', 'name', 'class_name'] as const)" :key="field">
              <span>{{ { student_code: '学号', name: '姓名', class_name: '班级' }[field] }}</span>
              <select v-model="roster.importMapping[field]" class="app-input" :aria-label="`${{ student_code: '学号', name: '姓名', class_name: '班级' }[field]}列`" :disabled="roster.importState === 'committing'">
                <option :value="null">{{ field === 'class_name' ? '不导入班级' : '请选择' }}</option>
                <option v-for="column in roster.preview.columns" :key="column" :value="column">{{ column }}</option>
              </select>
            </label>
            <AppButton variant="ghost" :disabled="roster.importState === 'previewing' || roster.importState === 'committing'" @click="applyMapping">重新比对</AppButton>
          </div>
          <div class="student-import__counts">
            <StatusBadge v-for="operation in (['insert', 'update', 'unchanged', 'invalid', 'duplicate'] as const)" :key="operation" :tone="operationTones[operation]" :label="`${operationLabels[operation]} ${roster.preview.counts[operation]}`" />
          </div>
          <label class="settings-check"><input v-model="onlyChanges" type="checkbox">只看有变化的行</label>
          <div class="student-import__preview"><table class="settings-table">
            <thead><tr><th><span class="sr-only">选择写入行</span></th><th>行号</th><th>学号</th><th>姓名</th><th>班级</th><th>变化</th><th>说明</th></tr></thead>
            <tbody><tr v-for="row in visibleRows" :key="row.source_row" data-testid="import-preview-row">
              <td><input type="checkbox" :aria-label="`选择源文件第 ${row.source_row} 行`" :checked="roster.selectedSourceRows.includes(row.source_row)" :disabled="!row.selectable || roster.importState === 'committing'" @change="roster.toggleImportRow(row.source_row, ($event.currentTarget as HTMLInputElement).checked)"></td>
              <td>{{ row.source_row }}</td><td>{{ row.student_code || '—' }}</td><td>{{ row.name || '—' }}</td><td>{{ row.class_name || '—' }}</td>
              <td><StatusBadge :tone="operationTones[row.operation]" :label="operationLabels[row.operation]" /></td><td>{{ row.issues.join('；') || '—' }}</td>
            </tr></tbody>
          </table><StatePanel v-if="!visibleRows.length" kind="empty" compact title="没有有变化的行" /></div>
          <FeedbackBanner v-if="roster.errorMessage" role="alert" tone="error" :description="roster.errorMessage" />
          <footer class="settings-dialog__footer">
            <span class="settings-note">已选 {{ roster.selectedImportRows.length }} 行，确认后才会写入。</span>
            <AppButton variant="secondary" :disabled="roster.importState === 'committing'" @click="open = false">取消</AppButton>
            <AppButton variant="primary" data-action="commit-import" :disabled="roster.selectedImportRows.length === 0 || roster.importState === 'committing' || roster.importState === 'previewing'" @click="commit">{{ roster.importState === 'committing' ? '正在写入…' : `写入名单（${roster.selectedImportRows.length} 人）` }}</AppButton>
          </footer>
        </template>
  </AppDialog>
</template>
