<script setup lang="ts">
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, ref } from 'vue'

import type { StudentImportOperation } from '../../api/students'
import { useStudentRosterStore } from '../../stores/students'
import StatusBadge from '../design-system/StatusBadge.vue'

const roster = useStudentRosterStore()
const selectedFile = ref<File | null>(null)

const stage = computed(() => {
  if (roster.importState === 'committed') return 3
  if (roster.preview) return 2
  return 1
})

const operationLabels: Record<StudentImportOperation, string> = {
  insert: '新增',
  update: '更新',
  unchanged: '无变化',
  invalid: '无效',
  duplicate: '重复',
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
    await roster.load({ page: 1 })
  }
}
</script>

<template>
  <section
    class="student-import"
    :class="{ 'is-compact': !roster.preview }"
    aria-labelledby="student-import-title"
  >
    <template v-if="!roster.preview">
      <h2 id="student-import-title" class="sr-only">导入学生名单</h2>
      <label class="student-file-picker student-file-picker--inline">
        <input
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          @change="chooseFile"
        >
        <strong>{{ selectedFile ? '更换文件' : '导入学生名单' }}</strong>
      </label>
      <span class="student-import__hint">CSV / XLSX，不超过 10 MB</span>
      <span v-if="roster.importState === 'previewing'" role="status">正在读取并比对名单…</span>
    </template>

    <template v-else>
      <div class="student-section-heading">
        <div>
          <p class="student-eyebrow">批量登记</p>
          <h2 id="student-import-title">导入学生名单</h2>
        </div>
        <span>CSV / XLSX，不超过 10 MB</span>
      </div>

      <ol class="student-import__rail" aria-label="名单导入步骤">
        <li :aria-current="stage === 1 ? 'step' : undefined" :class="{ 'is-complete': stage > 1 }">
          <span>1</span>
          <strong>选择文件</strong>
        </li>
        <li :aria-current="stage === 2 ? 'step' : undefined" :class="{ 'is-complete': stage > 2 }">
          <span>2</span>
          <strong>核对变化</strong>
        </li>
        <li :aria-current="stage === 3 ? 'step' : undefined">
          <span>3</span>
          <strong>确认写入</strong>
        </li>
      </ol>

      <div class="student-import__file">
        <label class="student-file-picker">
          <span>{{ selectedFile?.name ?? '选择 CSV 或 XLSX 名单' }}</span>
          <input
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            @change="chooseFile"
          >
          <strong>{{ selectedFile ? '更换文件' : '选择文件' }}</strong>
        </label>
        <span v-if="roster.importState === 'previewing'" role="status">正在读取并比对名单…</span>
      </div>

      <div class="student-import__mapping">
        <label>
          <span>学号列</span>
          <select class="app-input" v-model="roster.importMapping.student_code">
            <option :value="null">请选择</option>
            <option v-for="column in roster.preview.columns" :key="column" :value="column">
              {{ column }}
            </option>
          </select>
        </label>
        <label>
          <span>姓名列</span>
          <select class="app-input" v-model="roster.importMapping.name">
            <option :value="null">请选择</option>
            <option v-for="column in roster.preview.columns" :key="column" :value="column">
              {{ column }}
            </option>
          </select>
        </label>
        <label>
          <span>班级列（可选）</span>
          <select class="app-input" v-model="roster.importMapping.class_name">
            <option :value="null">不导入班级</option>
            <option v-for="column in roster.preview.columns" :key="column" :value="column">
              {{ column }}
            </option>
          </select>
        </label>
        <AppButton variant="secondary" type="button" class="student-button student-button--secondary" @click="applyMapping">
          应用列对应
        </AppButton>
      </div>

      <div class="student-import__counts" aria-label="导入变化统计">
        <span class="is-insert">新增 {{ roster.preview.counts.insert }}</span>
        <span class="is-update">更新 {{ roster.preview.counts.update }}</span>
        <span>无变化 {{ roster.preview.counts.unchanged }}</span>
        <span class="is-invalid">无效 {{ roster.preview.counts.invalid }}</span>
        <span class="is-duplicate">重复 {{ roster.preview.counts.duplicate }}</span>
      </div>

      <div class="student-import__preview">
        <table>
          <thead>
            <tr>
              <th scope="col">写入</th>
              <th scope="col">源行</th>
              <th scope="col">学号</th>
              <th scope="col">姓名</th>
              <th scope="col">班级</th>
              <th scope="col">变化</th>
              <th scope="col">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in roster.preview.rows"
              :key="row.source_row"
              data-testid="import-preview-row"
            >
              <td>
                <input
                  type="checkbox"
                  :aria-label="`选择源文件第 ${row.source_row} 行`"
                  :checked="roster.selectedSourceRows.includes(row.source_row)"
                  :disabled="!row.selectable"
                  @change="roster.toggleImportRow(
                    row.source_row,
                    ($event.currentTarget as HTMLInputElement).checked,
                  )"
                >
              </td>
              <td>{{ row.source_row }}</td>
              <td>{{ row.student_code || '—' }}</td>
              <td>{{ row.name || '—' }}</td>
              <td>{{ row.class_name || '未分班' }}</td>
              <td>
                <StatusBadge
                  class="student-operation"
                  :tone="operationTones[row.operation]"
                  :label="operationLabels[row.operation]"
                />
              </td>
              <td>{{ row.issues.join('；') || '可写入' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="student-import__actions">
        <span>已选择 {{ roster.selectedImportRows.length }} 行；确认后才会写入名单。</span>
        <AppButton variant="primary"
          type="button"
          class="student-button student-button--primary"
          data-action="commit-import"
          :disabled="
            roster.selectedImportRows.length === 0
            || roster.importState === 'committing'
            || roster.importState === 'committed'
          "
          @click="commit"
        >
          {{ roster.importState === 'committing' ? '正在写入…' : '确认写入名单' }}
        </AppButton>
      </div>
    </template>
  </section>
</template>
