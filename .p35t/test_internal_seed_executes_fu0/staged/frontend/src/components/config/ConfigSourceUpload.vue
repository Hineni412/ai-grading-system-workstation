<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  abandonConfigSourceSubmission,
  createClientRequestToken,
  fetchActiveConfigSource,
  fetchConfigSourceSubmission,
  uploadConfigSource,
  type ConfigSource,
  type ConfigSourceSubmission,
} from '../../api/config-workspace'
import { isAmbiguousWriteError, isAuthoritativeNotFoundError } from '../../api/errors'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useJobStore } from '../../stores/jobs'

const MAX_SOURCE_BYTES = 200 * 1024 * 1024

const props = withDefaults(defineProps<{
  sessionId: number
  source?: ConfigSource | null
  uploader?: (sessionId: number, file: File, requestToken: string) => Promise<ConfigSource>
  beforeUpload?: () => boolean
  submissionLoader?: (sessionId: number, requestToken: string) => Promise<ConfigSourceSubmission>
  submissionAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
  activeSourceLoader?: (sessionId: number) => Promise<ConfigSource>
}>(), {
  source: null,
  uploader: uploadConfigSource,
  beforeUpload: () => true,
  submissionLoader: fetchConfigSourceSubmission,
  submissionAbandoner: abandonConfigSourceSubmission,
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
const submissionUnknown = computed(() => configStore.pendingUploadRequestToken !== null)
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
  configStore.clearUploadSubmissionPending()
  emit('uploaded', source)
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  errorMessage.value = ''
}

async function reconcileUpload(): Promise<void> {
  const token = configStore.pendingUploadRequestToken
  if (token === null || uploading.value) return
  uploading.value = true
  errorMessage.value = '正在核对这次上传的结果…'
  try {
    const submission = await props.submissionLoader(props.sessionId, token)
    if (submission.status === 'succeeded' && submission.source !== null) {
      acceptSource(submission.source)
    } else if (submission.status === 'replaced') {
      const activeSource = await props.activeSourceLoader(props.sessionId)
      acceptSource(activeSource)
    } else if (submission.status === 'failed') {
      configStore.clearUploadSubmissionPending()
      errorMessage.value = '这次上传没有成功，可以重新选择文件上传。'
    } else {
      errorMessage.value = '这次上传仍在处理中，请稍后再次核对。'
    }
  } catch (error) {
    if (isAuthoritativeNotFoundError(error, 'config_source_not_found')) {
      try {
        await props.submissionAbandoner(props.sessionId, token)
        configStore.clearUploadSubmissionPending()
        errorMessage.value = '服务器确认未收到这次上传，可以重新提交。'
      } catch {
        errorMessage.value = '原上传可能仍在到达服务器，当前继续锁定。请稍后再次核对。'
      }
    } else {
      errorMessage.value = '暂时无法核对这次上传。为避免重复接收，请稍后再次核对。'
    }
  } finally {
    uploading.value = false
  }
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

async function submit(): Promise<void> {
  const file = selectedFile.value
  if (file === null || uploading.value || workspaceLocked.value) return
  const validation = validateFile(file)
  if (validation) {
    errorMessage.value = validation
    return
  }
  if (!props.beforeUpload()) return
  const requestToken = createClientRequestToken()
  if (!configStore.markUploadSubmissionPending(requestToken)) return
  uploading.value = true
  errorMessage.value = ''
  try {
    const accepted = await props.uploader(props.sessionId, file, requestToken)
    acceptSource(accepted)
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      uploading.value = false
      await reconcileUpload()
    } else {
      configStore.clearUploadSubmissionPending()
      errorMessage.value = props.source === null
        ? '文件未接收成功。请选择文件后重新上传。'
        : '新文件未接收成功，当前试卷来源已保留。'
    }
  } finally {
    uploading.value = false
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Number((bytes / 1024).toFixed(1))} KiB`
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`
}
</script>

<template>
  <section class="config-source" aria-labelledby="config-source-title">
    <header class="config-section-heading">
      <div>
        <h2 id="config-source-title">上传与拆题</h2>
        <p>接收 DOCX 或 PDF，并在本机拆分为可核对的题目。</p>
      </div>
    </header>

    <form class="config-source__form" @submit.prevent="submit">
      <div class="config-source__picker">
        <span id="config-source-file-label">选择 DOCX 或 PDF</span>
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
          >选择文件</button>
          <span
            class="config-source__file-name"
            :class="{ 'has-file': selectedFile !== null }"
            :title="selectedFile?.name ?? ''"
          >{{ selectedFileDescription }}</span>
        </div>
      </div>
      <button
        type="submit"
        class="config-source__submit"
        :disabled="uploading || workspaceLocked || selectedFileInvalid"
      >
        上传并拆题
      </button>
    </form>

    <div v-if="uploading" class="config-source__progress" role="status" aria-live="polite">
      <progress aria-label="上传并拆题进度" />
      <span>正在上传并拆题，请保持页面打开…</span>
    </div>
    <p v-if="errorMessage" class="config-source__error" role="alert">{{ errorMessage }}</p>
    <button
      v-if="submissionUnknown"
      type="button"
      :disabled="uploading"
      @click="reconcileUpload"
    >重新核对上传结果</button>

    <dl v-if="source" class="config-source__summary" aria-label="当前试卷来源">
      <div><dt>文件</dt><dd>{{ source.safe_filename }}</dd></div>
      <div><dt>大小</dt><dd>{{ formatBytes(source.size_bytes) }}</dd></div>
      <div><dt>内容指纹</dt><dd><code>{{ source.sha256_prefix }}</code></dd></div>
      <div><dt>来源版本</dt><dd><code>{{ source.source_revision }}</code></dd></div>
    </dl>
    <p v-if="source && source.questions.length === 0" class="config-source__empty" role="status">
      未识别到题目。当前来源已接收，可以选择更换文件。
    </p>
  </section>
</template>
