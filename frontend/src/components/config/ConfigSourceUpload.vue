<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  fetchActiveConfigSource,
  uploadConfigSource,
  type ConfigSource,
} from '../../api/config-workspace'
import { isAmbiguousWriteError } from '../../api/errors'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useJobStore } from '../../stores/jobs'
import { formatBytes } from '../../lib/format'

const MAX_SOURCE_BYTES = 200 * 1024 * 1024

const props = withDefaults(defineProps<{
  sessionId: number
  source?: ConfigSource | null
  uploader?: (sessionId: number, file: File) => Promise<ConfigSource>
  beforeUpload?: () => boolean | Promise<boolean>
  activeSourceLoader?: (sessionId: number) => Promise<ConfigSource>
}>(), {
  source: null,
  uploader: uploadConfigSource,
  beforeUpload: () => true,
  activeSourceLoader: fetchActiveConfigSource,
})

const emit = defineEmits<{
  uploaded: [source: ConfigSource]
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const errorMessage = ref('')
const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const selectedFileInvalid = computed(() => selectedFile.value === null
  || validateFile(selectedFile.value) !== '')
const selectedFileDescription = computed(() => selectedFile.value === null
  ? '尚未选择文件'
  : `${selectedFile.value.name} · ${formatBytes(selectedFile.value.size)}`)
const activeGeneration = computed(() => {
  const current = configStore.jobId === null ? null : jobStore.jobs[configStore.jobId]
  return current?.job_type === 'config_generation'
    && current.payload.session_id === props.sessionId
    && !['succeeded', 'failed', 'cancelled'].includes(current.status)
})
const workspaceLocked = computed(() => configStore.hasPendingSubmission || activeGeneration.value)

function acceptSource(source: ConfigSource): void {
  emit('uploaded', source)
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  errorMessage.value = ''
}

function validateFile(file: File): string {
  if (!/\.(docx|pdf)$/i.test(file.name)) return '只支持 DOCX 或 PDF 文件。'
  if (file.size > MAX_SOURCE_BYTES) return '文件不能超过 200 MiB。'
  return ''
}

function onFileChange(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0] ?? null
  selectedFile.value = file
  errorMessage.value = file === null ? '' : validateFile(file)
}

function chooseFile(): void {
  if (uploading.value || workspaceLocked.value) return
  fileInput.value?.click()
}

function clearFile(): void {
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  errorMessage.value = ''
}

async function submit(): Promise<void> {
  const file = selectedFile.value
  if (file === null || uploading.value || workspaceLocked.value) return
  const validation = validateFile(file)
  if (validation) {
    errorMessage.value = validation
    return
  }
  if (!await props.beforeUpload()) return
  uploading.value = true
  errorMessage.value = ''
  try {
    const accepted = await props.uploader(props.sessionId, file)
    acceptSource(accepted)
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      try {
        const active = await props.activeSourceLoader(props.sessionId)
        if (active.source_id !== props.source?.source_id
          || active.source_revision !== props.source?.source_revision) {
          acceptSource(active)
          return
        }
      } catch {
        // Fall through to the unconfirmed-result message below.
      }
      errorMessage.value = '上传结果尚未确认，请刷新页面核对当前试卷来源后再决定是否重新上传。'
    } else {
      errorMessage.value = props.source === null
        ? '文件未接收成功。请选择文件后重新上传。'
        : '新文件未接收成功，当前试卷来源已保留。'
    }
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <section class="config-source" aria-label="上传与拆题">
    <div class="config-source__compact">
      <span
        v-if="source"
        class="config-source__summary"
        aria-label="当前试卷来源"
        :title="source.safe_filename"
      >{{ source.safe_filename }} · {{ formatBytes(source.size_bytes) }} · {{ source.questions.length }} 题</span>
      <form class="config-source__form" @submit.prevent="submit">
        <div class="config-source__picker">
          <span id="config-source-file-label" class="sr-only">选择 DOCX 或 PDF</span>
          <input
            ref="fileInput"
            class="config-source__native-input"
            type="file"
            accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            aria-labelledby="config-source-file-label"
            :disabled="uploading || workspaceLocked"
            @change="onFileChange"
          >
          <div class="config-source__file-control">
            <button
              type="button"
              class="config-source__choose"
              :disabled="uploading || workspaceLocked"
              @click="chooseFile"
            >{{ source ? '更换试卷' : '选择试卷' }}</button>
            <template v-if="selectedFile !== null">
              <span
                class="config-source__file-name has-file"
                :title="selectedFile.name"
              >{{ selectedFileDescription }}</span>
              <button
                type="submit"
                class="config-source__submit"
                :disabled="uploading || workspaceLocked || selectedFileInvalid"
              >上传并拆题</button>
              <button
                type="button"
                class="config-source__cancel"
                :disabled="uploading"
                @click="clearFile"
              >取消</button>
            </template>
          </div>
        </div>
      </form>
    </div>

    <div v-if="uploading" class="config-source__progress" role="status" aria-live="polite">
      <progress aria-label="上传并拆题进度" />
      <span>正在上传并拆题，请保持页面打开…</span>
    </div>
    <p v-if="errorMessage" class="config-source__error" role="alert">{{ errorMessage }}</p>

    <p v-if="source && source.questions.length === 0" class="config-source__empty" role="status">
      未识别到题目。当前来源已接收，可以选择更换文件。
    </p>
  </section>
</template>
