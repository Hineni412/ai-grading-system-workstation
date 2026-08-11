<script setup lang="ts">
import AppButton from '../../../../components/design-system/AppButton.vue'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { MATERIAL_ROLES } from './libraryShared'
import type { MaterialImportQueue } from './importQueue'

const props = defineProps<{ queue: MaterialImportQueue }>()
const catalog = useTeachingPrepCatalogStore()
const queue = props.queue
</script>

<template>
  <section class="tp-panel" aria-label="导入资料">
    <div class="tp-panel__head">
      <h2>① 导入资料</h2>
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
        <article
          v-for="item in queue.pendingImports.value"
          :key="item.id"
          class="tp-import-row"
          :class="`is-${item.state}`"
        >
          <div class="tp-import-row__identity">
            <strong>{{ item.file.name }}</strong>
            <small v-if="item.folderBatchId">{{ item.relativePath }}</small>
            <small>{{ queue.fileSizeLabel(item.file.size) }} · {{ queue.importStateLabel(item) }}</small>
            <small v-if="item.error" class="tp-error-text">{{ item.error }}</small>
            <div v-if="item.state !== 'pending'" class="tp-import-row__progress">
              <progress
                :value="queue.progressPercent(item)"
                max="100"
                :aria-label="`${item.file.name}：${queue.importStateLabel(item)}`"
              />
              <span>{{ queue.progressPercent(item) }}%</span>
            </div>
          </div>
          <label class="tp-field">
            资料角色
            <select
              v-model="item.role"
              :disabled="item.state === 'uploading' || item.state === 'processing' || item.state === 'done'"
            >
              <option v-for="role in MATERIAL_ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
          </label>
          <template v-if="item.role === 'homework_workbook'">
            <label class="tp-field">
              教辅套组
              <input v-model="item.workbookSeries" type="text" placeholder="例如：全品学练考">
            </label>
            <label class="tp-field">
              分册
              <select v-model="item.workbookVolume">
                <option value="A">A 本</option>
                <option value="B">B 本</option>
              </select>
            </label>
          </template>
          <div class="tp-import-row__actions">
            <AppButton
              v-if="item.state === 'processing'"
              variant="ghost"
              :disabled="queue.parseJobFor(item)?.cancel_requested"
              @click="queue.cancelImport(item)"
            >
              {{ queue.parseJobFor(item)?.cancel_requested ? '正在停止…' : '取消导入' }}
            </AppButton>
            <AppButton
              v-else
              variant="ghost"
              :disabled="item.state === 'uploading'"
              @click="queue.removePendingImport(item.id)"
            >
              移除
            </AppButton>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>
