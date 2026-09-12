<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  trainingApi,
  type PersonalizedPaperInstance,
  type TrainingScanBatch,
  type TrainingScanBatchSummary,
  type TrainingScanCandidate,
  type TrainingScanIssue,
  type TrainingScanPage,
} from '../../api/training'
import { ApiError } from '../../api/errors'
import StatusBadge from '../design-system/StatusBadge.vue'
import TrainingAssessmentPanel from './TrainingAssessmentPanel.vue'

const props = defineProps<{
  instances: PersonalizedPaperInstance[]
}>()

const emit = defineEmits<{
  openDraft: [draftId: string]
}>()

const batch = ref<TrainingScanBatch | null>(null)
const selectedIds = ref<string[]>([])
const files = ref<File[]>([])
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')
const targets = ref<Record<string, string>>({})
const targetPages = ref<Record<string, number>>({})
const batches = ref<TrainingScanBatchSummary[]>([])
const historyLoaded = ref(false)
const paperBatchIds = computed(() => [...new Set(
  props.instances.map((instance) => instance.paper_batch_id),
)].sort())
let loadVersion = 0

const frozenInstances = computed(() => props.instances.filter(
  (instance) => instance.status === 'frozen',
))

const reviewPages = computed(() => (
  batch.value?.pages.filter((page) => (
    page.issue_code
    && !['replaced', 'dismissed'].includes(page.state)
  )) ?? []
))

function selectLatestInstances(): void {
  const latest = new Map<string, PersonalizedPaperInstance>()
  for (const instance of frozenInstances.value) {
    const current = latest.get(instance.student_id)
    if (!current || instance.series_version > current.series_version) {
      latest.set(instance.student_id, instance)
    }
  }
  selectedIds.value = [...latest.values()].map(
    (instance) => instance.paper_instance_id,
  )
}

watch(
  frozenInstances,
  () => {
    if (!batch.value) selectLatestInstances()
  },
  { immediate: true },
)

function beginNewBatch(): void {
  batch.value = null
  files.value = []
  targets.value = {}
  targetPages.value = {}
  message.value = ''
  errorMessage.value = ''
  selectLatestInstances()
}

async function restoreBatches(): Promise<void> {
  const version = ++loadVersion
  beginNewBatch()
  batches.value = []
  historyLoaded.value = false
  busy.value = false
  if (!paperBatchIds.value.length) return
  busy.value = true
  try {
    const groups = await Promise.all(paperBatchIds.value.map(
      (id) => trainingApi.listTrainingScanBatches(id),
    ))
    if (version !== loadVersion) return
    const items = groups.flat().sort((left, right) => right.created_at.localeCompare(left.created_at))
    batches.value = items
    const latest = items[0]
    if (latest) {
      const restored = await trainingApi.getTrainingScanBatch(latest.batch_id)
      if (version !== loadVersion) return
      batch.value = restored
      message.value = '已恢复最近一次扫描批次，可以继续归卷和复核。'
    }
    historyLoaded.value = true
  } catch {
    if (version === loadVersion) errorMessage.value = '扫描批次读取未完成，请重新读取后继续。'
  } finally {
    if (version === loadVersion) busy.value = false
  }
}

async function openBatch(batchId: string): Promise<void> {
  if (!batchId || busy.value) return
  const version = ++loadVersion
  busy.value = true
  errorMessage.value = ''
  try {
    const restored = await trainingApi.getTrainingScanBatch(batchId)
    if (version !== loadVersion) return
    beginNewBatch()
    batch.value = restored
  } catch {
    if (version === loadVersion) errorMessage.value = '扫描批次读取未完成，请重试。'
  } finally {
    if (version === loadVersion) busy.value = false
  }
}

function changeBatch(event: Event): void {
  void openBatch((event.target as HTMLSelectElement).value)
}

function batchLabel(item: TrainingScanBatchSummary): string {
  const date = new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })
  const value = batch.value?.batch_id === item.batch_id ? batch.value.status : item.status
  const status = value === 'ready' ? '归组完成' : value === 'cancelled' ? '已取消' : '需要检查'
  return `${date} · ${item.submission_count} 人 · ${status}`
}

watch(() => paperBatchIds.value.join(','), restoreBatches, { immediate: true })
onBeforeUnmount(() => { loadVersion += 1 })

function requestToken(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return [...bytes]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

function safeError(error: unknown): string {
  if (
    error instanceof ApiError
    && error.code === 'training_submission_revision_conflict'
  ) {
    return '归卷状态已在另一处变化，请刷新该批次后再操作。'
  }
  return '扫描件处理未完成，已有归组结果保持不变，请检查文件后重试。'
}

function issueLabel(issue: TrainingScanIssue | null | undefined): string {
  if (!issue) return ''
  return {
    identity_unreadable: '页面身份无法读取',
    invalid_identity: '页面身份或签名无效',
    unexpected_paper: '不属于本扫描批次',
    duplicate_page: '检测到重复页面',
    page_content_conflict: '同一页码出现不同图像',
    image_blurry: '页面疑似明显模糊',
    severe_crop: '页面疑似严重裁切',
  }[issue]
}

function candidateLabel(candidate: TrainingScanCandidate): string {
  return `${candidate.student_name || candidate.student_code || candidate.student_id}`
    + ` · V${candidate.series_version}`
}

function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  files.value = [...(input.files ?? [])]
}

async function createBatch(): Promise<void> {
  if (!selectedIds.value.length || busy.value) return
  busy.value = true
  errorMessage.value = ''
  message.value = ''
  try {
    batch.value = await trainingApi.createTrainingScanBatch(
      selectedIds.value,
      requestToken(),
    )
    batches.value.unshift({
      batch_id: batch.value.batch_id,
      paper_batch_id: batch.value.paper_batch_id,
      status: batch.value.status,
      submission_count: batch.value.submissions.length,
      created_at: batch.value.created_at,
      updated_at: batch.value.updated_at,
    })
    message.value = '扫描批次已建立；只有选中的冻结卷会被自动归组。'
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = false
  }
}

async function upload(): Promise<void> {
  if (!batch.value || !files.value.length || busy.value) return
  busy.value = true
  errorMessage.value = ''
  message.value = ''
  try {
    for (const file of files.value) {
      batch.value = await trainingApi.uploadTrainingScan(
        batch.value,
        file,
        requestToken(),
      )
    }
    message.value = batch.value.status === 'ready'
      ? '全部页面已按身份归组，可以进入后续训练判定。'
      : '扫描件已归组；异常页和缺页需要人工处理后才能进入判定。'
    files.value = []
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = false
  }
}

async function resolve(
  page: TrainingScanPage,
  action: 'match' | 'replace' | 'dismiss',
): Promise<void> {
  if (!batch.value || busy.value) return
  const paperInstanceId = targets.value[page.scan_page_id]
  const pageNumber = targetPages.value[page.scan_page_id]
  if (action !== 'dismiss' && (!paperInstanceId || !pageNumber)) {
    errorMessage.value = '请先选择准确的学生训练卷和页码。'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    batch.value = await trainingApi.resolveTrainingScanPage(
      batch.value,
      page,
      {
        operation_token: requestToken(),
        action,
        paper_instance_id: action === 'dismiss' ? undefined : paperInstanceId,
        page_number: action === 'dismiss' ? undefined : pageNumber,
      },
    )
    message.value = action === 'replace'
      ? '已只替换明确选择的页，旧页仍保留在历史中。'
      : action === 'dismiss'
        ? '该扫描页已忽略，不再阻塞本批次。'
        : '该扫描页已人工匹配并留下审计记录。'
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = false
  }
}

async function cancelSubmission(submissionId: string): Promise<void> {
  if (!batch.value || busy.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    batch.value = await trainingApi.cancelTrainingSubmission(
      batch.value,
      submissionId,
      requestToken(),
    )
    message.value = '已取消这一个学生的本次提交，其他训练卷不受影响。'
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="training-scan-panel" aria-labelledby="training-scan-title">
    <header>
      <div>
        <h4 id="training-scan-title">扫描归卷</h4>
        <p>逐页核验冻结身份；缺页、重页或身份冲突不会进入自动判定。</p>
      </div>
      <StatusBadge
        v-if="batch"
        class="scan-status"
        :tone="batch.status === 'ready' ? 'success' : 'warning'"
        :label="batch.status === 'ready' ? '归组完成' : '需要检查'"
      />
    </header>

    <div v-if="batches.length" class="scan-history">
      <label>
        扫描批次
        <select :value="batch?.batch_id || ''" :disabled="busy" @change="changeBatch">
          <option v-if="!batch" value="">正在建立新批次</option>
          <option v-for="item in batches" :key="item.batch_id" :value="item.batch_id">
            {{ batchLabel(item) }}
          </option>
        </select>
      </label>
      <div>
        <button v-if="batch" type="button" class="training-link" :disabled="busy" @click="openBatch(batch.batch_id)">刷新批次</button>
        <button v-if="batch" type="button" class="training-link" :disabled="busy" @click="beginNewBatch">建立另一批次</button>
      </div>
    </div>

    <p v-if="!historyLoaded && busy" class="scan-note">正在恢复扫描批次…</p>
    <button v-else-if="!historyLoaded && paperBatchIds.length" type="button" class="training-link" @click="restoreBatches">重新读取批次</button>

    <template v-if="!batch && historyLoaded">
      <p v-if="!frozenInstances.length" class="training-empty is-compact">
        至少冻结一份训练卷后，才能建立扫描批次。
      </p>
      <fieldset v-else>
        <legend>本次预期收到的冻结卷</legend>
        <label
          v-for="instance in frozenInstances"
          :key="instance.paper_instance_id"
        >
          <input
            v-model="selectedIds"
            type="checkbox"
            :value="instance.paper_instance_id"
          >
          {{ instance.student_name || instance.student_code || instance.student_id }}
          · V{{ instance.series_version }} · {{ instance.pages.length }} 页
        </label>
      </fieldset>
      <button
        v-if="frozenInstances.length"
        type="button"
        class="training-button"
        :disabled="busy || !selectedIds.length"
        @click="createBatch"
      >
        {{ busy ? '正在建立…' : '建立扫描批次' }}
      </button>
    </template>

    <template v-else-if="batch">
      <div class="scan-upload">
        <label>
          导入扫描文件（PDF、JPG 或 PNG，可多选）
          <input
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="busy"
            @change="chooseFiles"
          >
        </label>
        <button
          type="button"
          class="training-button"
          :disabled="busy || !files.length"
          @click="upload"
        >
          {{ busy ? '正在识别归组…' : `导入并归组${files.length ? `（${files.length} 个）` : ''}` }}
        </button>
      </div>

      <div class="scan-submissions">
        <article
          v-for="submission in batch.submissions"
          :key="submission.submission_id"
        >
          <div>
            <strong>
              {{ submission.student_name || submission.student_code || submission.student_id }}
              · V{{ submission.series_version }}
            </strong>
            <span>{{ submission.status === 'ready' ? '页面齐全' : submission.status === 'cancelled' ? '已取消' : '等待处理' }}</span>
          </div>
          <p v-if="submission.missing_pages.length">
            缺少第 {{ submission.missing_pages.join('、') }} 页
          </p>
          <p v-if="submission.issue_codes.length">
            {{ submission.issue_codes.map(issueLabel).join('；') }}
          </p>
          <button
            v-if="submission.status === 'manual_review'"
            type="button"
            class="training-link"
            :disabled="busy"
            @click="cancelSubmission(submission.submission_id)"
          >
            取消这份提交
          </button>
          <TrainingAssessmentPanel
            v-if="submission.status === 'ready'"
            :key="`${submission.submission_id}:${submission.revision}`"
            :submission="submission"
            @open-draft="emit('openDraft', $event)"
          />
        </article>
      </div>

      <section v-if="reviewPages.length" class="scan-review">
        <h5>异常页面</h5>
        <article v-for="page in reviewPages" :key="page.scan_page_id">
          <a :href="page.preview_url" target="_blank" rel="noopener">
            <img :src="page.preview_url" alt="待人工核对的完整扫描页">
          </a>
          <div>
            <strong>{{ issueLabel(page.issue_code) }}</strong>
            <small>上传文件第 {{ page.upload_page_number }} 页</small>
            <label>
              匹配到
              <select v-model="targets[page.scan_page_id]">
                <option value="">请选择学生训练卷</option>
                <option
                  v-for="candidate in batch.candidates"
                  :key="candidate.paper_instance_id"
                  :value="candidate.paper_instance_id"
                >
                  {{ candidateLabel(candidate) }}
                </option>
              </select>
            </label>
            <label>
              页码
              <input
                v-model.number="targetPages[page.scan_page_id]"
                type="number"
                min="1"
                :max="batch.candidates.find((item) => item.paper_instance_id === targets[page.scan_page_id])?.total_pages || 100"
              >
            </label>
            <div class="scan-review-actions">
              <button
                type="button"
                class="training-link"
                :disabled="busy"
                @click="resolve(page, 'match')"
              >
                人工匹配
              </button>
              <button
                type="button"
                class="training-link"
                :disabled="busy"
                @click="resolve(page, 'replace')"
              >
                明确替换该页
              </button>
              <button
                type="button"
                class="training-link"
                :disabled="busy"
                @click="resolve(page, 'dismiss')"
              >
                忽略此页
              </button>
            </div>
          </div>
        </article>
      </section>

      <p v-else-if="batch.pages.length" class="scan-clear">
        当前没有未处理的异常页。
      </p>
    </template>

    <p v-if="message" class="training-notice">{{ message }}</p>
    <p v-if="errorMessage" class="training-error" role="alert">{{ errorMessage }}</p>
    <p class="scan-note">
      页面姓名只用于人工核对，不会根据 OCR 自动确认；本步骤不会调用模型。
    </p>
  </section>
</template>

<style scoped>
.training-scan-panel {
  margin-top: 1rem;
  padding: 1rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.training-scan-panel > header,
.scan-upload,
.scan-submissions article > div,
.scan-review-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  justify-content: space-between;
}

.training-scan-panel h4,
.training-scan-panel h5,
.training-scan-panel p {
  margin: 0;
}

.training-scan-panel header p,
.scan-note,
.scan-submissions p,
.scan-review small {
  color: var(--color-text-secondary);
  font-size: 0.86rem;
}

.training-scan-panel fieldset {
  display: grid;
  gap: 0.45rem;
  margin: 0.9rem 0;
  border: 0;
  padding: 0;
}

.scan-status {
  flex: 0 0 auto;
}

.scan-history {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.75rem;
  margin-top: 0.9rem;
}

.scan-history label {
  display: grid;
  gap: 0.3rem;
  flex: 1 1 260px;
  min-width: 0;
}

.scan-history select {
  width: 100%;
  min-width: 0;
  padding: 0.45rem;
}

.scan-history > div {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.scan-upload {
  margin-top: 0.9rem;
  align-items: end;
}

.scan-upload label,
.scan-review label {
  display: grid;
  gap: 0.3rem;
}

.scan-submissions {
  display: grid;
  gap: 0.55rem;
  margin-top: 1rem;
}

.scan-submissions article {
  padding: 0.7rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.scan-review {
  margin-top: 1rem;
}

.scan-review > article {
  display: grid;
  grid-template-columns: minmax(120px, 180px) 1fr;
  gap: 0.85rem;
  margin-top: 0.65rem;
  padding: 0.7rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-danger-subtle);
}

.scan-review img {
  width: 100%;
  max-height: 220px;
  object-fit: contain;
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.scan-review > article > div {
  display: grid;
  gap: 0.55rem;
}

.scan-review select,
.scan-review input {
  min-width: 0;
  padding: 0.45rem;
}

.scan-clear,
.scan-note,
.training-notice,
.training-error {
  margin-top: 0.75rem !important;
}

@media (max-width: 720px) {
  .training-scan-panel > header,
  .scan-upload,
  .scan-submissions article > div,
  .scan-review-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .scan-review > article {
    grid-template-columns: 1fr;
  }
}
</style>
