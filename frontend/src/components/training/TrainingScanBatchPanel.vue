<script setup lang="ts">
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  trainingApi,
  type PersonalizedPaperInstance,
  type TrainingScanBatch,
  type TrainingScanBatchSummary,
  type TrainingScanCandidate,
  type TrainingScanIssue,
  type TrainingScanPage,
  type TrainingSubmission,
} from '../../api/training'
import { ApiError } from '../../api/errors'
import StatusBadge from '../design-system/StatusBadge.vue'
import TrainingAssessmentPanel from './TrainingAssessmentPanel.vue'

const props = defineProps<{
  instances: PersonalizedPaperInstance[]
}>()

const emit = defineEmits<{
  openDraft: [draftId: string]
  progressChange: [progress: { received: number; total: number; completed: boolean }]
}>()

const batch = ref<TrainingScanBatch | null>(null)
const selectedSubmissionId = ref('')
const summaries = ref<Record<string, { label: string; tone: 'info' | 'success' | 'warning' | 'danger'; met: number; total: number; published: boolean }>>({})
function summaryKey(submission: TrainingSubmission): string { return `${submission.submission_id}:${submission.revision}` }
function submissionSummary(submission: TrainingSubmission) { return summaries.value[summaryKey(submission)] }
function updateSummary(submission: TrainingSubmission, summary: typeof summaries.value[string]): void {
  summaries.value = { ...summaries.value, [summaryKey(submission)]: summary }
}
function submissionLabel(submission: TrainingSubmission): string {
  if (submission.status === 'cancelled') return '已取消'
  if (submission.status !== 'ready') return submission.missing_pages.length ? `缺 ${submission.missing_pages.length} 页` : '待核对'
  return submissionSummary(submission)?.label ?? '待批改'
}
watch(() => batch.value?.submissions, submissions => {
  if (!submissions?.some(item => item.submission_id === selectedSubmissionId.value)) selectedSubmissionId.value = submissions?.[0]?.submission_id ?? ''
}, { immediate: true })
watch([batch, summaries], () => {
  const submissions = batch.value?.submissions ?? []
  emit('progressChange', { received: submissions.filter(item => item.status === 'ready').length, total: submissions.length,
    completed: submissions.length > 0 && submissions.every(item => submissionSummary(item)?.published === true) })
}, { deep: true })
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
    <header><div><h3 id="training-scan-title">答卷回收与批改</h3><span v-if="batch">{{ batch.submissions.filter(item => item.status === 'ready').length }}/{{ batch.submissions.length }} 份答卷页面齐全<span v-if="reviewPages.length"> · {{ reviewPages.length }} 页待核对</span></span></div><StatusBadge v-if="batch" :tone="batch.status === 'ready' ? 'success' : 'warning'" :label="batch.status === 'ready' ? '归组完成' : '需要检查'" /></header>
    <div v-if="batches.length" class="scan-history">
      <label>扫描批次<select class="app-input" :value="batch?.batch_id || ''" :disabled="busy" @change="changeBatch"><option v-if="!batch" value="">新批次</option><option v-for="item in batches" :key="item.batch_id" :value="item.batch_id">{{ batchLabel(item) }}</option></select></label>
      <AppButton v-if="batch" variant="ghost" :disabled="busy" @click="openBatch(batch.batch_id)">刷新批次</AppButton><AppButton v-if="batch" variant="ghost" :disabled="busy" @click="beginNewBatch">建立另一批次</AppButton>
    </div>
    <p v-if="!historyLoaded && busy" class="scan-note">正在恢复扫描批次…</p>
    <AppButton v-else-if="!historyLoaded && paperBatchIds.length" variant="secondary" @click="restoreBatches">重新读取批次</AppButton>
    <template v-if="!batch && historyLoaded">
      <p v-if="!frozenInstances.length" class="training-empty is-compact">先生成 PDF 训练卷，再回收答卷。</p>
      <fieldset v-else><legend>本次回收 {{ selectedIds.length }} 份训练卷</legend><label v-for="instance in frozenInstances" :key="instance.paper_instance_id"><input v-model="selectedIds" type="checkbox" :value="instance.paper_instance_id">{{ instance.student_name || instance.student_code || instance.student_id }}<span>V{{ instance.series_version }} · {{ instance.pages.length }} 页</span></label></fieldset>
      <AppButton v-if="frozenInstances.length" variant="primary" :disabled="busy || !selectedIds.length" @click="createBatch">{{ busy ? '正在建立…' : '建立扫描批次' }}</AppButton>
    </template>
    <template v-else-if="batch">
      <details class="scan-upload-options" :open="batch.status !== 'ready' || busy"><summary v-if="batch.status === 'ready'">继续导入或补传答卷</summary><div class="scan-upload"><label>导入扫描文件 <span>PDF / JPG / PNG，可多选</span><input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" :disabled="busy" @change="chooseFiles"></label><AppButton variant="primary" :disabled="busy || !files.length" @click="upload">{{ busy ? '正在识别归组…' : `导入并归组${files.length ? `（${files.length} 个）` : ''}` }}</AppButton></div></details>

      <section v-if="reviewPages.length" class="scan-review"><h4>先核对 {{ reviewPages.length }} 页异常</h4><p>缺页、重页或身份冲突解决后，才能批改对应答卷。</p><article v-for="page in reviewPages" :key="page.scan_page_id"><a :href="page.preview_url" target="_blank" rel="noopener"><img :src="page.preview_url" alt="待人工核对的完整扫描页"></a><div><strong>{{ issueLabel(page.issue_code) }}</strong><small>上传文件第 {{ page.upload_page_number }} 页</small><label>匹配到<select class="app-input" v-model="targets[page.scan_page_id]"><option value="">请选择学生训练卷</option><option v-for="candidate in batch.candidates" :key="candidate.paper_instance_id" :value="candidate.paper_instance_id">{{ candidateLabel(candidate) }}</option></select></label><label>页码<input class="app-input" v-model.number="targetPages[page.scan_page_id]" type="number" min="1" :max="batch.candidates.find(item => item.paper_instance_id === targets[page.scan_page_id])?.total_pages || 100"></label><div class="scan-review-actions"><AppButton variant="secondary" :disabled="busy" @click="resolve(page, 'match')">人工匹配</AppButton><AppButton variant="secondary" :disabled="busy" @click="resolve(page, 'replace')">明确替换该页</AppButton><AppButton variant="ghost" :disabled="busy" @click="resolve(page, 'dismiss')">忽略此页</AppButton></div></div></article></section>

      <div v-if="batch.submissions.length" class="scan-workspace">
        <nav class="scan-student-list" aria-label="回收答卷学生列表"><header>学生 <span>{{ batch.submissions.length }} 人</span></header><button v-for="submission in batch.submissions" :key="submission.submission_id" type="button" :aria-pressed="selectedSubmissionId === submission.submission_id" @click="selectedSubmissionId = submission.submission_id"><strong>{{ submission.student_name || submission.student_code || submission.student_id }}</strong><StatusBadge :tone="submission.status !== 'ready' ? 'warning' : submissionSummary(submission)?.tone ?? 'info'" :label="submissionLabel(submission)" /><small>{{ submission.class_id }} · {{ submission.student_code || '' }}</small><span v-if="submissionSummary(submission)?.total" class="scan-student-result">达成 {{ submissionSummary(submission)?.met }}/{{ submissionSummary(submission)?.total }} 点</span></button></nav>
        <div class="scan-submissions">
          <article v-for="submission in batch.submissions" v-show="submission.submission_id === selectedSubmissionId" :key="submission.submission_id">
            <header><strong>{{ submission.student_name || submission.student_code || submission.student_id }}</strong><span>V{{ submission.series_version }} · {{ submission.expected_total_pages }} 页</span></header>
            <div v-if="submission.status !== 'ready'" class="scan-submission-issues"><p v-if="submission.missing_pages.length">缺少第 {{ submission.missing_pages.join('、') }} 页</p><p v-if="submission.issue_codes.length">{{ submission.issue_codes.map(issueLabel).join('；') }}</p><p v-if="submission.status === 'cancelled'">这份提交已取消。</p><AppButton v-if="submission.status === 'manual_review'" variant="ghost" :disabled="busy" @click="cancelSubmission(submission.submission_id)">取消这份提交</AppButton></div>
            <TrainingAssessmentPanel v-if="submission.status === 'ready'" :key="`${submission.submission_id}:${submission.revision}`" :submission="submission" :pages="batch.pages.filter(page => page.submission_id === submission.submission_id)" @summary-change="updateSummary(submission, $event)" @open-draft="emit('openDraft', $event)" />
          </article>
        </div>
      </div>
    </template>
    <p v-if="message" class="training-notice" role="status">{{ message }}</p><p v-if="errorMessage" class="training-error" role="alert">{{ errorMessage }}</p>
  </section>
</template>

<style scoped>
.training-scan-panel{min-width:0;background:var(--color-bg-surface)}
.training-scan-panel>header{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);margin-bottom:var(--space-4)}
.training-scan-panel>header>div{display:flex;align-items:baseline;flex-wrap:wrap;gap:var(--space-3)}
.training-scan-panel h3,.training-scan-panel h4,.training-scan-panel p{margin:0}
.training-scan-panel h3{font-size:var(--font-size-h3)}
.training-scan-panel header span,.scan-note,.scan-review p,.scan-review small,.scan-upload label>span{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.scan-history{display:flex;align-items:end;gap:var(--space-2);margin-bottom:var(--space-3);flex-wrap:wrap}
.scan-history label{display:flex;align-items:center;gap:var(--space-3);font-size:var(--font-size-dense);flex:1;min-width:0}
.scan-history select{flex:1;min-width:0;max-width:420px}
.training-scan-panel fieldset{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--space-2);padding:var(--space-4);border:1px solid var(--color-border-default);border-radius:var(--radius-control);margin:0 0 var(--space-4)}
.training-scan-panel legend{font-weight:600;font-size:var(--font-size-dense);padding:0 var(--space-2)}
.training-scan-panel fieldset label{display:flex;align-items:center;gap:var(--space-2);font-size:var(--font-size-dense)}
.training-scan-panel fieldset span{margin-left:auto;color:var(--color-text-muted);font-size:var(--font-size-caption)}
.scan-upload{display:flex;align-items:end;justify-content:space-between;gap:var(--space-4);padding:var(--space-4);border:1px dashed var(--color-border-strong);border-radius:var(--radius-control);margin-bottom:var(--space-4);background:var(--color-bg-subtle)}
.scan-upload label{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2);font-size:var(--font-size-dense);min-width:0}
.scan-upload input{flex-basis:100%;min-width:0;max-width:100%;font:inherit}
.scan-upload-options{margin-bottom:var(--space-4);font-size:var(--font-size-dense);color:var(--color-text-secondary)}.scan-upload-options>summary{cursor:pointer;margin-bottom:var(--space-2)}.scan-upload-options .scan-upload{margin-bottom:0}
.scan-workspace{display:grid;grid-template-columns:220px minmax(0,1fr);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);overflow:clip;align-items:start}
.scan-student-list{background:var(--color-bg-subtle);min-width:0;position:sticky;top:var(--space-4)}
.scan-student-list>header{display:flex;justify-content:space-between;padding:var(--space-3) var(--space-4);font-size:var(--font-size-dense)}
.scan-student-list button{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--space-2);text-align:left;width:100%;padding:var(--space-3) var(--space-4);border:0;border-top:1px solid var(--color-border-subtle);background:transparent;font:inherit;cursor:pointer}
.scan-student-list button[aria-pressed=true]{background:var(--color-bg-surface);box-shadow:inset 3px 0 var(--color-accent)}
.scan-student-list strong{font-size:var(--font-size-dense);align-self:center;overflow-wrap:anywhere}
.scan-student-list small{grid-column:1/-1;font-size:var(--font-size-caption);color:var(--color-text-muted)}
.scan-student-result{grid-column:1/-1;font-size:var(--font-size-caption);color:var(--color-text-secondary);font-variant-numeric:tabular-nums}
.scan-submissions{min-width:0;border-left:1px solid var(--color-border-default)}
.scan-submissions article>header{display:flex;justify-content:space-between;align-items:center;padding:var(--space-3) var(--space-5);border-bottom:1px solid var(--color-border-subtle)}
.scan-submission-issues{padding:var(--space-5);display:grid;gap:var(--space-3);font-size:var(--font-size-dense);color:var(--color-warning)}
.scan-review{margin-bottom:var(--space-4);padding:var(--space-4);background:var(--color-warning-subtle);border-radius:var(--radius-control)}
.scan-review>article{display:grid;grid-template-columns:140px minmax(0,1fr);gap:var(--space-4);margin-top:var(--space-3)}
.scan-review img{width:100%;max-height:220px;object-fit:contain}
.scan-review article>div{display:grid;gap:var(--space-2)}
.scan-review label{display:flex;align-items:center;gap:var(--space-2);font-size:var(--font-size-dense)}
.scan-review select{min-width:0;flex:1}.scan-review input{width:80px}
.scan-review-actions{display:flex;flex-wrap:wrap;gap:var(--space-2)}
.training-notice,.training-error{margin-top:var(--space-3)!important;font-size:var(--font-size-dense)}.training-error{color:var(--color-danger)}
@media(max-width:1050px){.scan-workspace{grid-template-columns:180px minmax(0,1fr)}}
@media(max-width:760px){.scan-workspace{grid-template-columns:minmax(0,1fr)}.scan-student-list{position:static;display:flex;overflow:auto}.scan-student-list>header{display:none}.scan-student-list button{flex:0 0 185px;border-top:0;border-right:1px solid var(--color-border-default)}.scan-student-list button[aria-pressed=true]{box-shadow:inset 0 -3px var(--color-accent)}.scan-submissions{border-left:0;border-top:1px solid var(--color-border-default)}.scan-upload{flex-wrap:wrap}.scan-history label{flex-basis:100%}.scan-review>article{grid-template-columns:minmax(0,1fr)}.scan-review img{max-height:200px}.scan-submissions article>header{padding:var(--space-3) var(--space-4)}}
@media(max-width:760px){.scan-student-list button{grid-template-columns:minmax(0,1fr)}.scan-student-list strong{grid-column:1;grid-row:1}.scan-student-list small{grid-row:2}.scan-student-list :deep(.status-badge){grid-column:1;grid-row:3;justify-self:start}.scan-student-result{grid-column:1;grid-row:3;justify-self:end;align-self:center}}
</style>
