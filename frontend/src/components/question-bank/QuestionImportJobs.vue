<script setup lang="ts">
import FeedbackBanner from '@/components/design-system/FeedbackBanner.vue'
import AppButton from '@/components/design-system/AppButton.vue'
import StatePanel from '@/components/design-system/StatePanel.vue'

import { computed, onMounted, ref } from 'vue'

import {
  questionBankApi,
  questionJobFailures,
  questionJobFailuresCsv,
  questionJobRetryIds,
  type CurriculumVolume,
} from '../../api/question-bank'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import QuestionPreviewDialog from '../training/QuestionPreviewDialog.vue'
import { CURRICULUM_SCOPE_STORAGE_KEY } from '../../stores/curriculum-scope'
import { useJobStore } from '../../stores/jobs'
import { useQuestionBankStore } from '../../stores/question-bank'

const props = withDefaults(defineProps<{
  pendingTaxonomyCount?: number
  pendingTaxonomyState?: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
}>(), {
  pendingTaxonomyCount: 0,
  pendingTaxonomyState: 'ready',
})

const pendingTaxonomyLabel = computed(() => {
  if (props.pendingTaxonomyState === 'idle' || props.pendingTaxonomyState === 'loading') return '读取中'
  if (props.pendingTaxonomyState === 'error') return '读取失败'
  return String(props.pendingTaxonomyCount)
})

const emit = defineEmits<{
  reviewTaxonomy: []
}>()

const bank = useQuestionBankStore()
const jobStore = useJobStore()
const busy = ref(false)
const feedback = ref('')
const queuedFiles = ref<Array<{ name: string; state: string }>>([])

// 导入时可指定教材册别，用来补齐文件名推断不出的年级/学期/教材版本。
const volumeOptions = ref<CurriculumVolume[]>([])
const importVolumeId = ref('')

onMounted(async () => {
  try {
    const catalog = await questionBankApi.getCurriculum(undefined, false)
    volumeOptions.value = catalog.volumes
    const remembered = globalThis.localStorage
      ?.getItem(CURRICULUM_SCOPE_STORAGE_KEY)
      ?.trim()
    if (remembered && catalog.volumes.some((volume) => volume.id === remembered)) {
      importVolumeId.value = remembered
    }
  } catch {
    volumeOptions.value = []
  }
})

const jobs = computed(() => Object.values(jobStore.jobs)
  .filter((job) => job.job_type === 'question_import' || job.job_type === 'tagging_sync')
  .sort((left, right) => right.id - left.id))

const statusLabels: Record<string, string> = {
  queued: '等待中',
  running: '进行中',
  paused: '已暂停',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function safeCount(result: Record<string, unknown>, key: string): number {
  const value = result[key]
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
}

interface ImportMatchEntry {
  questionNumber: string
  matchedQuestionId: number
  matchedPaperTitle: string
  matchedQuestionNumber: string
  questionId: number | null
  matchKind: string
  reason: string
}

function toMatchEntry(item: Record<string, unknown>): ImportMatchEntry {
  const matchedId = Number(item.matched_question_id)
  const newId = Number(item.question_id)
  return {
    questionNumber: typeof item.question_number === 'string' ? item.question_number : '',
    matchedQuestionId: Number.isSafeInteger(matchedId) ? matchedId : 0,
    matchedPaperTitle: typeof item.matched_paper_title === 'string' ? item.matched_paper_title : '',
    matchedQuestionNumber: typeof item.matched_question_number === 'string' ? item.matched_question_number : '',
    questionId: Number.isSafeInteger(newId) && newId > 0 ? newId : null,
    matchKind: typeof item.match_kind === 'string' ? item.match_kind : 'suspected',
    reason: typeof item.reason === 'string' ? item.reason : '',
  }
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    : []
}

function exactDuplicates(result: Record<string, unknown>): ImportMatchEntry[] {
  return recordList(result.exact_duplicates).map((item) => ({
    ...toMatchEntry(item),
    matchKind: 'exact',
  }))
}

function nearDuplicateHints(result: Record<string, unknown>): ImportMatchEntry[] {
  return recordList(result.near_duplicate_hints).map(toMatchEntry)
}

function similarHints(result: Record<string, unknown>): ImportMatchEntry[] {
  return nearDuplicateHints(result).filter((hint) => hint.matchKind !== 'answer_conflict')
}

function conflictHints(result: Record<string, unknown>): ImportMatchEntry[] {
  return nearDuplicateHints(result).filter((hint) => hint.matchKind === 'answer_conflict')
}

interface DuplicatePaper {
  paperId: number
  title: string
}

function duplicatePapers(result: Record<string, unknown>): DuplicatePaper[] {
  return recordList(result.duplicate_papers).map((item) => ({
    paperId: Number.isSafeInteger(Number(item.paper_id)) ? Number(item.paper_id) : 0,
    title: typeof item.title === 'string' ? item.title : '',
  })).filter((item) => item.paperId > 0)
}

function hasImportDetail(result: Record<string, unknown>): boolean {
  return exactDuplicates(result).length > 0
    || nearDuplicateHints(result).length > 0
    || duplicatePapers(result).length > 0
}

function importDetailSummary(result: Record<string, unknown>): string {
  const parts: string[] = []
  const exact = exactDuplicates(result).length
  const similar = similarHints(result).length
  const conflicts = conflictHints(result).length
  if (exact > 0) parts.push(`完全相同已关联 ${exact}`)
  if (similar > 0) parts.push(`相似或变式 ${similar}`)
  if (conflicts > 0) parts.push(`答案不同 ${conflicts}`)
  return parts.length ? parts.join(' · ') : '查看导入明细'
}

function questionImportCompletionNote(result: Record<string, unknown>): string {
  const note = '试卷已入库，尚未执行标签与判定点分析。'
  return hasImportDetail(result) ? `${note}（明细见下方展开）` : note
}

const previewQuestionId = ref<number | null>(null)
const previewTitle = ref('')

function openPreview(questionId: number, title: string): void {
  previewTitle.value = title
  previewQuestionId.value = questionId
}

function closePreview(): void {
  previewQuestionId.value = null
}

function matchedLabel(entry: ImportMatchEntry): string {
  const paper = entry.matchedPaperTitle ? `《${entry.matchedPaperTitle}》` : '题库已有题目'
  return entry.matchedQuestionNumber ? `${paper}第 ${entry.matchedQuestionNumber} 题` : paper
}

function jobCompletionNote(job: JobResponse): string {
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return job.detail || job.stage || '任务等待服务处理'
  if (job.status === 'failed') return job.job_type === 'question_import'
    ? '试卷没有完成入库。'
    : '标签或判定点没有完成；已保存结果不会撤销。'
  if (job.status === 'cancelled') return '任务已取消；已保存结果不会撤销。'
  if (job.job_type === 'question_import') return questionImportCompletionNote(job.result)
  const reviewCount = safeCount(job.result, 'criteria_needs_review_count')
  if (reviewCount > 0) return `${reviewCount} 道题的判定点需要审核，本任务不计为分析成功。`
  if (job.result.outcome === 'complete') return '标签和判定点均已完成。'
  if (job.result.outcome === 'partial') return '部分完成，仍有未完成或待审核项目。'
  return '分析任务已结束，请核对各项结果。'
}

async function chooseFiles(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (files.length === 0 || busy.value) return
  busy.value = true
  feedback.value = ''
  queuedFiles.value = files.map(({ name }) => ({ name, state: '等待上传' }))
  for (const [index, file] of files.entries()) {
    const row = queuedFiles.value[index]!
    try {
      row.state = '正在安全上传'
      const upload = await questionBankApi.stageImport(file)
      row.state = '正在核对导入请求'
      const request = await questionBankApi.createImportRequest(upload.upload_id)
      row.state = '已提交任务'
      jobStore.track(await questionBankApi.submitImportJob(
        request.request_id,
        importVolumeId.value || undefined,
      ))
    } catch {
      row.state = '提交失败'
    }
  }
  busy.value = false
  feedback.value = '导入队列已处理。每个文件都有独立任务，可在下方继续跟踪。'
}

async function startTagging(): Promise<void> {
  const count = bank.selectedQuestionIds.length
  if (count === 0 || busy.value) return
  const confirmed = window.confirm(
    `将对已选 ${count} 道题调用 AI 标注，可能产生外部费用。确认现在启动吗？`,
  )
  if (!confirmed) return
  busy.value = true
  feedback.value = ''
  try {
    const selected = new Set(bank.selectedQuestionIds)
    const selectedItems = bank.questions.filter((item) => selected.has(item.id))
    const volumeIds = [...new Set(selectedItems.map((item) => (
      bank.papers.find((paper) => paper.id === item.paper_id)?.curriculum_volume_id ?? ''
    )).filter(Boolean))]
    if (selectedItems.length !== count || volumeIds.length !== 1) {
      feedback.value = '请选择同一份且教材资料完整的试卷题目，或从试卷卡片点击“继续完成未完成题目”。'
      return
    }
    const job = await questionBankApi.submitTagging(
      bank.selectedQuestionIds,
      volumeIds[0]!,
    )
    jobStore.track(job)
    feedback.value = `已提交 ${count} 道题的 AI 标注任务。`
  } catch {
    feedback.value = 'AI 标注任务没有提交，当前选择保持不变。'
  } finally {
    busy.value = false
  }
}

async function retry(job: JobResponse): Promise<void> {
  if (busy.value) return
  if (job.job_type === 'tagging_sync') {
    const ids = questionJobRetryIds(job)
    if (ids.length === 0) {
      feedback.value = '服务端没有提供可安全重试的题目范围，请先刷新任务。'
      return
    }
    const confirmed = window.confirm(
      `只重试 ${ids.length} 道失败题目，仍可能产生外部费用。确认继续吗？`,
    )
    if (!confirmed) return
    busy.value = true
    try {
      jobStore.track(await questionBankApi.retryTagging(job.id, ids))
      feedback.value = '失败题目已提交新的重试任务。'
    } catch {
      feedback.value = '重试任务没有提交，原任务记录保持不变。'
    } finally {
      busy.value = false
    }
    return
  }

  busy.value = true
  try {
    jobStore.track(await questionBankApi.retryImport(job.id))
    feedback.value = '导入重试已经提交。'
  } catch {
    feedback.value = '导入重试没有提交，原任务记录保持不变。'
  } finally {
    busy.value = false
  }
}

function downloadFailures(job: JobResponse): void {
  const csv = `\uFEFF${questionJobFailuresCsv(job.result)}`
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `题库任务-${job.id}-失败清单.csv`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="qb-jobs" aria-labelledby="qb-import-title">
    <header class="qb-jobs__heading">
      <div>
        <h2 id="qb-import-title">上传试卷与任务</h2>
        <p>Word/PDF 会先安全上传，再由后台生成试卷和题目。</p>
      </div>
      <div class="qb-jobs__heading-actions">
        <span>{{ jobs.length }} 个历史任务</span>
        <AppButton variant="secondary"
          type="button"
         
          @click="emit('reviewTaxonomy')"
        >
          待审核新词
          <span class="app-button__count">{{ pendingTaxonomyLabel }}</span>
        </AppButton>
      </div>
    </header>

    <label v-if="volumeOptions.length" class="qb-volume-picker">
      <span>教材册别</span>
      <select class="app-input" v-model="importVolumeId" :disabled="busy" aria-label="选择导入试卷的教材册别">
        <option value="">不指定（仅按文件名推断）</option>
        <option v-for="volume in volumeOptions" :key="volume.id" :value="volume.id">
          {{ volume.label }}
        </option>
      </select>
      <small>用于补齐试卷的年级、学期与教材版本</small>
    </label>

    <div class="qb-jobs__actions">
      <label class="qb-file-picker">
        <input type="file" accept=".docx,.pdf" multiple :disabled="busy" @change="chooseFiles">
        <span>
          <strong>{{ busy ? '正在处理…' : '选择 Word / PDF' }}</strong>
          <small>可一次选择多个文件，单个文件不超过 200 MB</small>
        </span>
      </label>
      <AppButton variant="primary"
        type="button"
       
        :disabled="bank.selectedCount === 0 || busy"
        @click="startTagging"
      >
        AI 标注已选 {{ bank.selectedCount }} 题
      </AppButton>
    </div>
    <p class="qb-help">不会自动调用 AI；只有确认题数和费用提示后才会提交。</p>
    <FeedbackBanner v-if="feedback" role="status" tone="info" :description="feedback" />

    <ul v-if="queuedFiles.length" class="qb-upload-queue" aria-label="本次文件队列">
      <li v-for="file in queuedFiles" :key="file.name">
        <span>{{ file.name }}</span><strong>{{ file.state }}</strong>
      </li>
    </ul>

    <details class="qb-job-history">
      <summary>查看任务记录（{{ jobs.length }}）</summary>
      <div v-if="jobs.length" class="qb-job-list">
        <article v-for="job in jobs" :key="job.id" class="qb-job">
          <header>
            <div>
              <strong>{{ job.job_type === 'tagging_sync' ? 'AI 标注' : '试卷导入' }} #{{ job.id }}</strong>
              <span>{{ statusLabels[job.status] || job.status }} · {{ Math.round(job.progress * 100) }}%</span>
            </div>
            <progress :value="job.progress" max="1">{{ Math.round(job.progress * 100) }}%</progress>
          </header>
          <p>{{ jobCompletionNote(job) }}</p>
          <template v-if="job.job_type === 'question_import' && TERMINAL_JOB_STATUSES.has(job.status)">
            <FeedbackBanner v-for="dup in duplicatePapers(job.result)" :key="`dup-${dup.paperId}`" tone="warning">
              这份试卷已在题库中（《{{ dup.title }}》），本次未重复入库。
            </FeedbackBanner>
            <details
              v-if="exactDuplicates(job.result).length || nearDuplicateHints(job.result).length"
              class="qb-import-detail"
            >
              <summary>{{ importDetailSummary(job.result) }}</summary>
              <div class="qb-import-detail__groups">
                <section v-if="exactDuplicates(job.result).length" class="qb-import-detail__group">
                  <h4>完全相同，已关联</h4>
                  <ul>
                    <li v-for="(entry, index) in exactDuplicates(job.result)" :key="`exact-${index}`">
                      <span>本卷第 {{ entry.questionNumber }} 题 ↔ {{ matchedLabel(entry) }}</span>
                      <AppButton
                        type="button"
                       
                        variant="ghost" size="small" @click="openPreview(entry.matchedQuestionId, matchedLabel(entry))"
                      >查看</AppButton>
                    </li>
                  </ul>
                </section>
                <section v-if="similarHints(job.result).length" class="qb-import-detail__group">
                  <h4>相似或变式，供核对</h4>
                  <ul>
                    <li v-for="(entry, index) in similarHints(job.result)" :key="`similar-${index}`">
                      <span>本卷第 {{ entry.questionNumber }} 题 ↔ {{ matchedLabel(entry) }}</span>
                      <small v-if="entry.reason">{{ entry.reason }}</small>
                      <AppButton
                        type="button"
                       
                        variant="ghost" size="small" @click="openPreview(entry.matchedQuestionId, matchedLabel(entry))"
                      >查看</AppButton>
                      <AppButton
                        v-if="entry.questionId !== null"
                        type="button"
                       
                        variant="ghost" size="small" @click="openPreview(entry.questionId, `本卷第 ${entry.questionNumber} 题`)"
                      >查看新题</AppButton>
                    </li>
                  </ul>
                </section>
                <section v-if="conflictHints(job.result).length" class="qb-import-detail__group is-danger">
                  <h4>答案不同，需核对</h4>
                  <p class="qb-import-detail__note">新入库的题已标记为待复核，请核对答案后确认。</p>
                  <ul>
                    <li v-for="(entry, index) in conflictHints(job.result)" :key="`conflict-${index}`">
                      <span>本卷第 {{ entry.questionNumber }} 题 ↔ {{ matchedLabel(entry) }}</span>
                      <small v-if="entry.reason">{{ entry.reason }}</small>
                      <AppButton
                        type="button"
                       
                        variant="ghost" size="small" @click="openPreview(entry.matchedQuestionId, matchedLabel(entry))"
                      >查看</AppButton>
                      <AppButton
                        v-if="entry.questionId !== null"
                        type="button"
                       
                        variant="ghost" size="small" @click="openPreview(entry.questionId, `本卷第 ${entry.questionNumber} 题`)"
                      >查看新题</AppButton>
                    </li>
                  </ul>
                </section>
              </div>
            </details>
          </template>
          <FeedbackBanner v-if="job.result.outcome === 'partial'" tone="warning">
            部分完成：成功
            {{ safeCount(job.result, 'tagged_count') || safeCount(job.result, 'question_count') }}，
            跳过 {{ safeCount(job.result, 'skipped_complete_count') }}，
            失败 {{ safeCount(job.result, 'failed_count') }}。
          </FeedbackBanner>
          <FeedbackBanner v-if="safeCount(job.result, 'criteria_needs_review_count') > 0" tone="warning">
            待审核判定点 {{ safeCount(job.result, 'criteria_needs_review_count') }} 道；
            审核通过前不会显示为成功。
          </FeedbackBanner>
          <FeedbackBanner v-if="job.job_type === 'question_import' && job.result.restore_required === true" tone="warning" description="这是旧版删除流程留下的任务记录。无需恢复旧试卷；重新上传时会按当前流程全新入库。" />
          <FeedbackBanner v-if="job.error" tone="error" :description="job.error" />
          <FeedbackBanner v-if="jobStore.syncErrors[job.id]" role="alert" tone="error"
            :description="jobStore.syncErrors[job.id]?.message + (jobStore.syncErrors[job.id]?.requestId ? ` 请求编号：${jobStore.syncErrors[job.id]?.requestId}` : '')" />
          <div class="qb-job__actions">
            <AppButton
              v-if="!TERMINAL_JOB_STATUSES.has(job.status)"
              type="button"
             
              variant="ghost" size="small" @click="jobStore.cancel(job.id)"
            >
              请求取消
            </AppButton>
            <AppButton type="button" variant="ghost" size="small" @click="jobStore.refresh(job.id)">刷新</AppButton>
            <AppButton
              v-if="questionJobFailures(job.result).length"
              type="button"
             
              variant="ghost" size="small" @click="downloadFailures(job)"
            >
              下载失败清单
            </AppButton>
            <AppButton
              v-if="
                TERMINAL_JOB_STATUSES.has(job.status) &&
                (
                  job.job_type === 'question_import' ||
                  questionJobRetryIds(job).length > 0
                ) &&
                (
                  job.status !== 'succeeded' ||
                  job.result.outcome === 'partial' ||
                  job.result.restore_required === true
                ) && job.result.restore_required !== true
              "
              type="button"
             
              :disabled="busy"
              variant="ghost" size="small" @click="retry(job)"
            >
              重试允许的失败项
            </AppButton>
          </div>
        </article>
      </div>
      <StatePanel v-else kind="empty" compact title="当前浏览器还没有记录题库任务。" />
    </details>
    <QuestionPreviewDialog
      :question-id="previewQuestionId"
      :title="previewTitle"
      @close="closePreview"
    />
  </section>
</template>
