<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  trainingApi,
  type PersonalizedPaperInstance,
  type TrainingScanBatch,
  type TrainingScanCandidate,
  type TrainingScanIssue,
  type TrainingScanPage,
} from '../../api/training'
import { ApiError } from '../../api/errors'
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

const frozenInstances = computed(() => props.instances.filter(
  (instance) => instance.status === 'frozen',
))

const reviewPages = computed(() => (
  batch.value?.pages.filter((page) => (
    page.issue_code
    && !['replaced', 'dismissed'].includes(page.state)
  )) ?? []
))

watch(
  frozenInstances,
  (instances) => {
    if (batch.value) return
    const latest = new Map<string, PersonalizedPaperInstance>()
    for (const instance of instances) {
      const current = latest.get(instance.student_id)
      if (!current || instance.series_version > current.series_version) {
        latest.set(instance.student_id, instance)
      }
    }
    selectedIds.value = [...latest.values()].map(
      (instance) => instance.paper_instance_id,
    )
  },
  { immediate: true },
)

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
      <span v-if="batch" :class="['scan-status', `is-${batch.status}`]">
        {{ batch.status === 'ready' ? '归组完成' : '需要检查' }}
      </span>
    </header>

    <template v-if="!batch">
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

    <template v-else>
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
  border: 1px solid #c8d7d1;
  border-radius: 12px;
  background: #f4f8f6;
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
  color: #5d6864;
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
  padding: 0.3rem 0.6rem;
  border-radius: 999px;
  background: #fff0cc;
  color: #785000;
}

.scan-status.is-ready {
  background: #dff3e8;
  color: #205d3d;
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
  border: 1px solid #d5dfdb;
  border-radius: 9px;
  background: #fff;
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
  border: 1px solid #e1c9bd;
  border-radius: 9px;
  background: #fffaf7;
}

.scan-review img {
  width: 100%;
  max-height: 220px;
  object-fit: contain;
  border: 1px solid #d8d4cf;
  background: #fff;
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
