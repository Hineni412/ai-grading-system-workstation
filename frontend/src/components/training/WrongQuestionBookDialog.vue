<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { isAmbiguousWriteError } from '../../api/errors'
import { exportsApi } from '../../api/exports'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import {
  findWrongQuestionBookRequest, previewWrongQuestionBook, submitWrongQuestionBooks,
  type StudentSummary, type WrongQuestionBookPreview,
} from '../../api/students'
import { isRecord } from '../../api/validation'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'
import AppField from '../design-system/AppField.vue'

const props = defineProps<{ student: StudentSummary; volumeId: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const includeClass = ref(false)
const preview = ref<WrongQuestionBookPreview | null>(null)
const selectedSessions = ref<number[]>([])
const loading = ref(false)
const submitting = ref(false)
const downloading = ref(false)
const downloaded = ref(false)
const message = ref('')
const token = ref('')
const jobId = ref<number | null>(null)
const jobs = useJobStore()
let controller: AbortController | null = null
const storageKey = `ai-grading:wrong-question-book:${props.student.id}:${props.volumeId}`
const job = computed(() => jobId.value === null ? null : jobs.jobs[jobId.value] ?? null)
const busy = computed(() => submitting.value || Boolean(job.value && !TERMINAL_JOB_STATUSES.has(job.value.status)))
const result = computed(() => job.value?.result ?? {})
const missing = computed(() => job.value?.status === 'succeeded'
  ? (Array.isArray(result.value.missing_items) ? result.value.missing_items.filter(isRecord) : [])
  : preview.value?.missing_items ?? [])
const emptyStudents = computed(() => Array.isArray(result.value.empty_students) ? result.value.empty_students.filter(isRecord) : [])
const failedStudents = computed(() => Array.isArray(result.value.failed_students) ? result.value.failed_students.filter(isRecord) : [])
const canSelect = computed(() => !token.value && !busy.value)

function saveRequest(): void {
  localStorage.setItem(storageKey, JSON.stringify({ token: token.value, downloaded: downloaded.value }))
}

function acceptJob(value: JobResponse): void {
  jobs.track(value)
  jobId.value = value.id
  message.value = ''
}

async function loadPreview(reset = false): Promise<void> {
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  message.value = ''
  try {
    const response = await previewWrongQuestionBook(props.student.id, {
      curriculum_volume_id: props.volumeId, include_class: includeClass.value,
      ...(reset ? {} : { session_ids: [...selectedSessions.value] }),
    }, active.signal)
    if (active.signal.aborted) return
    preview.value = response
    selectedSessions.value = response.session_ids
  } catch {
    if (active.signal.aborted) return
    preview.value = null
    message.value = '导出范围暂时无法读取，请重新加载。'
  } finally {
    if (controller === active) {
      controller = null
      loading.value = false
    }
  }
}

async function recover(): Promise<void> {
  if (!token.value) return
  submitting.value = true
  try {
    acceptJob(await findWrongQuestionBookRequest(token.value))
  } catch {
    message.value = '尚未确认此次提交的结果，请稍后查询；不会自动重复导出。'
  } finally {
    submitting.value = false
  }
}

async function submit(): Promise<void> {
  if (!preview.value || loading.value || busy.value || token.value || !selectedSessions.value.length) return
  token.value = crypto.randomUUID().replace(/-/g, '')
  saveRequest()
  submitting.value = true
  message.value = ''
  try {
    acceptJob(await submitWrongQuestionBooks({
      curriculum_volume_id: props.volumeId,
      student_ids: preview.value.students.map(student => student.id),
      session_ids: [...selectedSessions.value], client_request_token: token.value,
    }))
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      submitting.value = false
      await recover()
    } else {
      token.value = ''
      localStorage.removeItem(storageKey)
      message.value = '导出未提交成功，请刷新范围后重试。'
    }
  } finally {
    submitting.value = false
  }
}

async function download(): Promise<void> {
  if (!job.value || downloading.value || downloaded.value) return
  downloading.value = true
  message.value = ''
  try {
    const file = await exportsApi.downloadJobFile(job.value.id)
    const url = URL.createObjectURL(file.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    downloaded.value = true
    saveRequest()
  } catch {
    message.value = '下载未完成或文件已过期，请查询任务后重试；需要时可重新导出。'
  } finally {
    downloading.value = false
  }
}

function reset(): void {
  token.value = ''
  jobId.value = null
  downloaded.value = false
  localStorage.removeItem(storageKey)
  void loadPreview(true)
}

watch(includeClass, () => { if (canSelect.value) void loadPreview(true) })
onMounted(() => {
  dialog.value?.showModal?.()
  void loadPreview(true)
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    if (isRecord(saved) && typeof saved.token === 'string' && /^[0-9a-f]{32}$/.test(saved.token)) {
      token.value = saved.token
      downloaded.value = saved.downloaded === true
      void recover()
    }
  } catch { localStorage.removeItem(storageKey) }
})
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <dialog ref="dialog" class="wrong-book-dialog" aria-labelledby="wrong-book-title" @cancel.prevent="emit('close')">
    <header class="wrong-book-dialog__heading">
      <div><h2 id="wrong-book-title">导出错题本</h2><p>每人一份 Word，答案解析集中放在末尾。</p></div>
      <AppButton variant="ghost" :disabled="submitting || downloading" @click="emit('close')">关闭</AppButton>
    </header>
    <template v-if="canSelect">
      <AppField id="wrong-book-students" label="学生范围">
        <select id="wrong-book-students" v-model="includeClass" class="app-select app-input" :disabled="loading">
          <option :value="false">当前学生：{{ student.name }}</option>
          <option :value="true" :disabled="!student.class_name">{{ student.class_name || '尚未分班' }}全部学生</option>
        </select>
      </AppField>
      <fieldset class="wrong-book-dialog__exams" :disabled="loading">
        <legend>考试范围 · {{ preview?.semester_label || '当前教学学期' }}</legend>
        <p v-if="preview && !preview.sessions.length">当前学期没有该范围学生的考试成绩。</p>
        <label v-for="exam in preview?.sessions ?? []" :key="exam.session_id">
          <input v-model="selectedSessions" type="checkbox" :value="exam.session_id" @change="loadPreview()">
          <span>{{ exam.session_name }}</span>
        </label>
      </fieldset>
      <p v-if="loading" role="status">正在统计错题……</p>
      <p v-else-if="preview" class="wrong-book-dialog__estimate" role="status">预计 {{ preview.students.length }} 名学生 · 可导出 {{ preview.question_count }} 道错题 · 缺少题库原题 {{ preview.missing_items.length }} 道</p>
    </template>
    <div v-if="job" class="wrong-book-dialog__progress" aria-live="polite">
      <template v-if="busy"><p>{{ job.detail || '正在生成错题本……' }}</p><progress :value="job.progress" max="1" /></template>
      <p v-else-if="job.status === 'succeeded'">已完成 · {{ result.question_count || 0 }} 道错题</p>
      <p v-else>本次导出未完成，请重新选择后导出。</p>
      <p v-if="jobs.syncErrors[job.id]">任务状态暂时无法更新。<AppButton @click="jobs.refresh(job.id)">查询任务</AppButton></p>
    </div>
    <details v-if="missing.length" class="wrong-book-dialog__list">
      <summary>无法导出的原题（{{ missing.length }} 道）</summary>
      <ul><li v-for="(item, index) in missing" :key="index">{{ item.student_name }} · {{ item.session_name }} · {{ item.question_id }}</li></ul>
    </details>
    <p v-for="(item, index) in emptyStudents" :key="`empty-${index}`">未生成：{{ item.student_name }}（{{ item.reason }}）</p>
    <p v-for="(item, index) in failedStudents" :key="`failed-${index}`" role="alert">失败：{{ item.student_name }}（{{ item.reason }}）</p>
    <p v-if="message" role="alert">{{ message }}</p>
    <p v-if="downloaded" role="status">下载已完成，本机临时导出文件已清除。</p>
    <footer>
      <AppButton v-if="canSelect && !preview && !loading" @click="loadPreview(true)">重新加载</AppButton>
      <AppButton v-if="canSelect" variant="primary" :disabled="loading || !selectedSessions.length || !preview" :loading="submitting" @click="submit">导出</AppButton>
      <AppButton v-else-if="!job" :loading="submitting" @click="recover">查询此次提交</AppButton>
      <AppButton v-if="job?.status === 'succeeded' && result.download_url && !downloaded" variant="primary" :loading="downloading" @click="download">下载{{ String(result.filename).endsWith('.zip') ? '全部错题本' : '错题本' }}</AppButton>
      <AppButton v-if="job && !busy" :disabled="downloading" @click="reset">重新选择导出</AppButton>
    </footer>
  </dialog>
</template>

<style scoped>
.wrong-book-dialog { margin: auto; width: min(620px, calc(100vw - 32px)); max-height: 85vh; overflow-y: auto; padding: 24px; border: 1px solid var(--color-border-default); border-radius: 16px; background: var(--color-bg-surface); color: var(--color-text-primary); box-shadow: 0 16px 60px #0003; }
.wrong-book-dialog::backdrop { background: #0006; }
.wrong-book-dialog__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
h2 { margin: 0; font-size: 20px; }
p { line-height: 1.6; }
.wrong-book-dialog__heading p { margin-bottom: 0; color: var(--color-text-secondary); }
select { width: 100%; padding: 9px 12px; }
.wrong-book-dialog__exams { border: 0; padding: 0; margin: 22px 0 16px; }
legend { font-weight: 600; margin-bottom: 10px; }
.wrong-book-dialog__exams label { display: flex; align-items: center; gap: 10px; padding: 8px 0; }
.wrong-book-dialog__estimate { background: var(--color-bg-subtle); padding: 12px; border-radius: 8px; }
.wrong-book-dialog__list ul { padding-left: 20px; }
progress { width: 100%; }
footer { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; margin-top: 20px; }
</style>
