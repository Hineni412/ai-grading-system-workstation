<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  fetchActiveConfigSource,
  uploadConfigSource,
  type ConfigSource,
} from '../../api/config-workspace'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useConfigWorkspaceStore } from '../../stores/config-workspace'
import { useJobStore } from '../../stores/jobs'
import { formatBytes } from '../../lib/format'

const MAX_SOURCE_BYTES = 200 * 1024 * 1024

const props = withDefaults(defineProps<{
  sessionId: number
  source?: ConfigSource | null
  uploader?: (sessionId: number, file: File, answerFile?: File) => Promise<ConfigSource>
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

const uploadMode = ref<'combined' | 'separate'>('combined')
const fileInput = ref<HTMLInputElement | null>(null)
const answerInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const selectedAnswerFile = ref<File | null>(null)
const uploading = ref(false)
const errorMessage = ref('')
const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const isSeparate = computed(() => uploadMode.value === 'separate')
const fileAccept = computed(() => isSeparate.value
  ? '.pdf,application/pdf'
  : '.docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document')
const selectedFileInvalid = computed(() => selectedFile.value === null
  || validateFile(selectedFile.value) !== '')
const submitDisabled = computed(() => uploading.value || workspaceLocked.value
  || selectedFileInvalid.value
  || (isSeparate.value
    && (selectedAnswerFile.value === null || validateFile(selectedAnswerFile.value) !== '')))
const selectedFileDescription = computed(() => selectedFile.value === null
  ? (isSeparate.value ? '尚未选择试卷文件' : '尚未选择文件')
  : `${selectedFile.value.name} · ${formatBytes(selectedFile.value.size)}`)
const selectedAnswerFileDescription = computed(() => selectedAnswerFile.value === null
  ? '尚未选择答案文件'
  : `${selectedAnswerFile.value.name} · ${formatBytes(selectedAnswerFile.value.size)}`)
const sourceAnsweredCount = computed(() => (props.source?.questions ?? [])
  .filter((question) => question.answer_present).length)
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
  selectedAnswerFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  if (answerInput.value) answerInput.value.value = ''
  errorMessage.value = ''
}

function validateFile(file: File): string {
  if (isSeparate.value ? !/\.pdf$/i.test(file.name) : !/\.(docx|pdf)$/i.test(file.name)) {
    return isSeparate.value ? '分文件上传时试卷和答案都必须是 PDF 文件。' : '只支持 DOCX 或 PDF 文件。'
  }
  if (file.size > MAX_SOURCE_BYTES) return '文件不能超过 200 MiB。'
  return ''
}

function onFileChange(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0] ?? null
  selectedFile.value = file
  errorMessage.value = file === null ? '' : validateFile(file)
}

function onAnswerFileChange(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  const file = input.files?.[0] ?? null
  selectedAnswerFile.value = file
  errorMessage.value = file === null ? '' : validateFile(file)
}

function setUploadMode(mode: 'combined' | 'separate'): void {
  if (uploadMode.value === mode) return
  uploadMode.value = mode
  clearFile()
}

function chooseFile(): void {
  if (uploading.value || workspaceLocked.value) return
  fileInput.value?.click()
}

function chooseAnswerFile(): void {
  if (uploading.value || workspaceLocked.value) return
  answerInput.value?.click()
}

function clearFile(): void {
  selectedFile.value = null
  selectedAnswerFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  if (answerInput.value) answerInput.value.value = ''
  errorMessage.value = ''
}

async function submit(): Promise<void> {
  const file = selectedFile.value
  const answerFile = isSeparate.value ? selectedAnswerFile.value : null
  if (file === null || uploading.value || workspaceLocked.value) return
  if (isSeparate.value && answerFile === null) return
  const validation = validateFile(file)
    || (answerFile !== null ? validateFile(answerFile) : '')
  if (validation) {
    errorMessage.value = validation
    return
  }
  if (!await props.beforeUpload()) return
  uploading.value = true
  errorMessage.value = ''
  try {
    const accepted = answerFile === null
      ? await props.uploader(props.sessionId, file)
      : await props.uploader(props.sessionId, file, answerFile)
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
      // 已知的拆题失败（题号重复、缺裁图、答案文件对不上等）带可执行提示，
      // 直接展示给教师；其余失败保持原有通用文案。
      const parseMessage = error instanceof ApiError
        && error.code === 'config_source_invalid'
        && typeof error.details?.reason === 'string'
        ? error.message : ''
      const kept = props.source === null ? '' : '当前试卷来源已保留。'
      errorMessage.value = parseMessage
        ? `${parseMessage}${kept ? ` ${kept}` : ''}`
        : (props.source === null
          ? '文件未接收成功。请选择文件后重新上传。'
          : '新文件未接收成功，当前试卷来源已保留。')
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
        :title="source.answer_filename ? `${source.safe_filename} · ${source.answer_filename}` : source.safe_filename"
      ><template v-if="source.answer_filename">试卷 {{ source.safe_filename }} · 答案 {{ source.answer_filename }} · {{ source.questions.length }} 题 · 配上答案 {{ sourceAnsweredCount }} 题</template><template v-else>{{ source.safe_filename }} · {{ formatBytes(source.size_bytes) }} · {{ source.questions.length }} 题</template></span>
      <form class="config-source__form" @submit.prevent="submit">
        <div class="config-source__picker">
          <div class="app-segmented config-source__mode" role="group" aria-label="试卷和答案的文件形式">
            <button
              type="button"
              :class="{ 'is-active': uploadMode === 'combined' }"
              :aria-pressed="uploadMode === 'combined'"
              :disabled="uploading || workspaceLocked"
              @click="setUploadMode('combined')"
            >试卷和答案在一个文件</button>
            <button
              type="button"
              :class="{ 'is-active': uploadMode === 'separate' }"
              :aria-pressed="uploadMode === 'separate'"
              :disabled="uploading || workspaceLocked"
              @click="setUploadMode('separate')"
            >试卷、答案分两个文件</button>
          </div>
          <span id="config-source-file-label" class="sr-only">{{ isSeparate ? '选择试卷 PDF' : '选择 DOCX 或 PDF' }}</span>
          <input
            ref="fileInput"
            class="config-source__native-input"
            type="file"
            :accept="fileAccept"
            aria-labelledby="config-source-file-label"
            :disabled="uploading || workspaceLocked"
            @change="onFileChange"
          >
          <span id="config-source-answer-label" class="sr-only">选择答案 PDF</span>
          <input
            ref="answerInput"
            class="config-source__native-input"
            type="file"
            accept=".pdf,application/pdf"
            aria-labelledby="config-source-answer-label"
            :disabled="uploading || workspaceLocked"
            @change="onAnswerFileChange"
          >
          <div class="config-source__file-control">
            <button
              type="button"
              class="config-source__choose"
              :disabled="uploading || workspaceLocked"
              @click="chooseFile"
            >{{ source ? '更换试卷' : (isSeparate ? '选择试卷（PDF）' : '选择试卷') }}</button>
            <template v-if="selectedFile !== null">
              <span
                class="config-source__file-name has-file"
                :title="selectedFile.name"
              >{{ selectedFileDescription }}</span>
              <template v-if="!isSeparate">
                <button
                  type="submit"
                  class="config-source__submit"
                  :disabled="submitDisabled"
                >上传并拆题</button>
                <button
                  type="button"
                  class="config-source__cancel"
                  :disabled="uploading"
                  @click="clearFile"
                >取消</button>
              </template>
            </template>
          </div>
          <div v-if="isSeparate" class="config-source__file-control">
            <button
              type="button"
              class="config-source__choose"
              :disabled="uploading || workspaceLocked"
              @click="chooseAnswerFile"
            >选择答案（PDF）</button>
            <span
              class="config-source__file-name"
              :class="{ 'has-file': selectedAnswerFile !== null }"
              :title="selectedAnswerFile?.name"
            >{{ selectedAnswerFileDescription }}</span>
            <button
              type="submit"
              class="config-source__submit"
              :disabled="submitDisabled"
            >上传并拆题</button>
            <button
              type="button"
              class="config-source__cancel"
              :disabled="uploading"
              @click="clearFile"
            >取消</button>
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
