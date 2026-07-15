<script setup lang="ts">
import { ref } from 'vue'

import {
  uploadConfigSource,
  type ConfigSource,
} from '../../api/config-workspace'

const MAX_SOURCE_BYTES = 200 * 1024 * 1024

const props = withDefaults(defineProps<{
  sessionId: number
  source?: ConfigSource | null
  uploader?: (sessionId: number, file: File) => Promise<ConfigSource>
}>(), {
  source: null,
  uploader: uploadConfigSource,
})

const emit = defineEmits<{
  uploaded: [source: ConfigSource]
}>()

const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const errorMessage = ref('')

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

async function submit(): Promise<void> {
  const file = selectedFile.value
  if (file === null || uploading.value) return
  const validation = validateFile(file)
  if (validation) {
    errorMessage.value = validation
    return
  }
  uploading.value = true
  errorMessage.value = ''
  try {
    const accepted = await props.uploader(props.sessionId, file)
    emit('uploaded', accepted)
    selectedFile.value = null
  } catch {
    errorMessage.value = props.source === null
      ? '文件未接收成功。请选择文件后重新上传。'
      : '新文件未接收成功，当前试卷来源已保留。'
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
      <label class="config-source__picker">
        <span>选择 DOCX 或 PDF</span>
        <input
          type="file"
          accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          :disabled="uploading"
          @change="onFileChange"
        >
      </label>
      <button type="submit" :disabled="uploading || selectedFile === null || errorMessage !== ''">
        上传并拆题
      </button>
    </form>

    <div v-if="uploading" class="config-source__progress" role="status" aria-live="polite">
      <progress aria-label="上传并拆题进度" />
      <span>正在上传并拆题，请保持页面打开…</span>
    </div>
    <p v-if="errorMessage" class="config-source__error" role="alert">{{ errorMessage }}</p>

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
