<script setup lang="ts">
import { computed, reactive } from 'vue'

import AppButton from '../../../../components/design-system/AppButton.vue'
import type { SemesterMaterialRole } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { MATERIAL_ROLES } from './libraryShared'
import type {
  MaterialImportQueue,
  PendingImportFolderGroup,
  PendingMaterialImport,
} from './importQueue'

const props = defineProps<{ queue: MaterialImportQueue }>()
const catalog = useTeachingPrepCatalogStore()
const queue = props.queue

type ImportQueueRow =
  | { kind: 'folder'; key: string; group: PendingImportFolderGroup }
  | { kind: 'item'; key: string; item: PendingMaterialImport; nested: boolean }

// 教师手动切换过的展开状态；未切换时按 folderGroupNeedsAttention 决定默认展开/收起
const folderExpandedOverrides = reactive<Record<string, boolean>>({})

function isFolderExpanded(group: PendingImportFolderGroup): boolean {
  return folderExpandedOverrides[group.key] ?? queue.folderGroupNeedsAttention(group)
}

function toggleFolder(group: PendingImportFolderGroup): void {
  folderExpandedOverrides[group.key] = !isFolderExpanded(group)
}

// 队列按原始顺序渲染：文件夹导入的项聚合成一行（可展开明细），散装文件保持逐份行
const importRows = computed<ImportQueueRow[]>(() => {
  const rows: ImportQueueRow[] = []
  const emittedFolderKeys = new Set<string>()
  for (const item of queue.pendingImports.value) {
    if (!item.folderBatchId) {
      rows.push({ kind: 'item', key: item.id, item, nested: false })
      continue
    }
    if (emittedFolderKeys.has(item.folderBatchId)) continue
    const group = queue.importFolderGroups.value.find(
      candidate => candidate.key === item.folderBatchId,
    )
    if (!group) continue
    emittedFolderKeys.add(group.key)
    rows.push({ kind: 'folder', key: `folder-${group.key}`, group })
    if (isFolderExpanded(group)) {
      for (const member of group.items) {
        rows.push({ kind: 'item', key: member.id, item: member, nested: true })
      }
    }
  }
  return rows
})

function folderRoleEditable(group: PendingImportFolderGroup): boolean {
  return group.items.some(item => (
    item.state === 'pending' || item.state === 'failed' || item.state === 'cancelled'
  ))
}

function onFolderRoleChange(group: PendingImportFolderGroup, event: Event): void {
  queue.setFolderGroupRole(
    group,
    (event.target as HTMLSelectElement).value as SemesterMaterialRole,
  )
}
</script>

<template>
  <section class="tp-panel" aria-label="导入资料">
    <div class="tp-panel__head">
      <h2>导入资料</h2>
      <span class="tp-panel__hint">支持 PPTX / PDF，逐份受控复制后由后台逐页解析</span>
    </div>
    <div class="tp-panel__body">
      <div class="tp-import-actions">
        <label
          class="tp-file-button"
          :class="{ 'is-disabled': queue.importBatchRunning.value || !catalog.selectedSemester }"
          title="选择整个文件夹；只收录其中的 PPTX"
        >
          导入课件文件夹
          <input
            type="file"
            multiple
            webkitdirectory
            directory
            :disabled="queue.importBatchRunning.value || !catalog.selectedSemester"
            @change="queue.queuePptFolder($event)"
          >
        </label>
        <label
          class="tp-file-button tp-file-button--primary"
          :class="{ 'is-disabled': queue.importBatchRunning.value }"
        >
          选择多份资料
          <input
            type="file"
            multiple
            accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"
            :disabled="queue.importBatchRunning.value"
            @change="queue.queueFiles($event)"
          >
        </label>
      </div>

      <p v-if="queue.message.value" class="tp-inline-message" role="status">{{ queue.message.value }}</p>

      <div v-if="queue.pendingImports.value.length" class="tp-import-queue">
        <div class="tp-import-queue__head">
          <span>{{ queue.pendingImports.value.length }} 份在队列中</span>
          <div class="tp-inline-actions">
            <AppButton
              variant="ghost"
              :disabled="queue.importBatchRunning.value || !queue.pendingImports.value.some(item => item.state === 'done')"
              @click="queue.clearFinishedImports"
            >
              清除已完成
            </AppButton>
            <AppButton
              variant="primary"
              :disabled="queue.importBatchRunning.value || queue.pendingImportCount.value === 0"
              @click="queue.importQueuedFiles"
            >
              {{ queue.importBatchRunning.value ? '正在复制资料…' : `开始导入 ${queue.pendingImportCount.value} 份` }}
            </AppButton>
          </div>
        </div>
        <template v-for="row in importRows" :key="row.key">
          <article
            v-if="row.kind === 'folder'"
            class="tp-import-row tp-import-row--folder"
          >
            <div class="tp-import-row__identity">
              <strong>{{ row.group.displayName }}</strong>
              <small>
                {{ row.group.items.length }} 份 PPTX
                · 已完成 {{ queue.folderGroupCompletedCount(row.group) }}/{{ row.group.items.length }}
                · {{ queue.folderGroupStateLabel(row.group) }}
              </small>
              <div
                v-if="row.group.items.some(item => item.state !== 'pending')"
                class="tp-import-row__progress"
              >
                <progress
                  :value="queue.folderGroupProgressPercent(row.group)"
                  max="100"
                  :aria-label="`${row.group.displayName}：${queue.folderGroupStateLabel(row.group)}`"
                />
                <span>{{ queue.folderGroupProgressPercent(row.group) }}%</span>
              </div>
            </div>
            <label class="tp-field">
              资料角色
              <select
                :value="queue.folderGroupRole(row.group)"
                :disabled="!folderRoleEditable(row.group)"
                @change="onFolderRoleChange(row.group, $event)"
              >
                <option v-if="!queue.folderGroupRole(row.group)" value="" disabled>多种角色</option>
                <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                  {{ role.label }}
                </option>
              </select>
            </label>
            <div class="tp-import-row__actions">
              <AppButton
                variant="ghost"
                :aria-expanded="isFolderExpanded(row.group)"
                @click="toggleFolder(row.group)"
              >
                {{ isFolderExpanded(row.group) ? '收起明细' : `展开明细（${row.group.items.length}）` }}
              </AppButton>
            </div>
          </article>
          <article
            v-else
            class="tp-import-row"
            :class="[`is-${row.item.state}`, { 'tp-import-row--nested': row.nested }]"
          >
            <div class="tp-import-row__identity">
              <strong>{{ row.item.file.name }}</strong>
              <small v-if="row.item.folderBatchId">{{ row.item.relativePath }}</small>
              <small>{{ queue.fileSizeLabel(row.item.file.size) }} · {{ queue.importStateLabel(row.item) }}</small>
              <small v-if="row.item.error" class="tp-error-text">{{ row.item.error }}</small>
              <div v-if="row.item.state !== 'pending'" class="tp-import-row__progress">
                <progress
                  :value="queue.progressPercent(row.item)"
                  max="100"
                  :aria-label="`${row.item.file.name}：${queue.importStateLabel(row.item)}`"
                />
                <span>{{ queue.progressPercent(row.item) }}%</span>
              </div>
            </div>
            <label class="tp-field">
              资料角色
              <select
                v-model="row.item.role"
                :disabled="row.item.state === 'uploading' || row.item.state === 'processing' || row.item.state === 'done'"
              >
                <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                  {{ role.label }}
                </option>
              </select>
            </label>
            <template v-if="row.item.role === 'homework_workbook'">
              <label class="tp-field">
                教辅套组
                <input v-model="row.item.workbookSeries" type="text" placeholder="例如：全品学练考">
              </label>
              <label class="tp-field">
                分册
                <select v-model="row.item.workbookVolume">
                  <option value="A">A 本</option>
                  <option value="B">B 本</option>
                </select>
              </label>
            </template>
            <div class="tp-import-row__actions">
              <AppButton
                v-if="row.item.state === 'processing'"
                variant="ghost"
                :disabled="queue.parseJobFor(row.item)?.cancel_requested"
                @click="queue.cancelImport(row.item)"
              >
                {{ queue.parseJobFor(row.item)?.cancel_requested ? '正在停止…' : '取消导入' }}
              </AppButton>
              <AppButton
                v-else
                variant="ghost"
                :disabled="row.item.state === 'uploading'"
                @click="queue.removePendingImport(row.item.id)"
              >
                移除
              </AppButton>
            </div>
          </article>
        </template>
      </div>
    </div>
  </section>
</template>
