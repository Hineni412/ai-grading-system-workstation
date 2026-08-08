<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  QuestionBankPaper,
  QuestionBankPaperMetadataInput,
  QuestionBankPaperPermanentDeleteImpact,
} from '../../api/question-bank'
import { questionBankApi } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import { useJobStore } from '../../stores/jobs'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'

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
  open: [paper: QuestionBankPaper]
  import: []
  reviewTaxonomy: []
}>()

const store = useQuestionBankStore()
const jobStore = useJobStore()
const keyword = ref('')
const year = ref('')
const examType = ref('')
const sourceType = ref('')
const progressStatus = ref('')
const editingPaperId = ref<number | null>(null)
const pendingDeletePaper = ref<QuestionBankPaper | null>(null)
const permanentDeleteImpact = ref<QuestionBankPaperPermanentDeleteImpact | null>(null)
const permanentDeleteState = ref<'idle' | 'loading' | 'working' | 'error'>('idle')
const permanentDeleteMessage = ref('')
const permanentDeleteRequestToken = ref('')
const formError = ref('')
const retagBusyPaperId = ref<number | null>(null)
const retagAllBusy = ref(false)
const taggingMode = ref<'fill' | 'retag' | null>(null)
const retagMessage = ref('')
const deleteNotice = ref('')
const refreshedTerminalJobs = new Set<string>()
const collapsedFolderKeys = ref(new Set<string>())
const paperDraft = ref({
  title: '',
  year: '',
  province: '',
  city: '',
  district: '',
  exam_type: '',
  grade: '',
  semester: '',
  folder_name: '',
  textbook_version: '',
})

const years = computed(() => uniqueValues(store.papers.map((paper) => paper.year)))
const examTypes = computed(() => uniqueValues(store.papers.map((paper) => paper.exam_type)))
const canConfirmPermanentDelete = computed(() => (
  permanentDeleteImpact.value !== null
  && pendingDeletePaper.value !== null
  && permanentDeleteState.value !== 'working'
))

const filteredPapers = computed(() => {
  const search = keyword.value.trim().toLocaleLowerCase()
  return [...store.papers]
    .filter((paper) => {
      if (year.value && paper.year !== year.value) return false
      if (examType.value && paper.exam_type !== examType.value) return false
      if (sourceType.value && paper.source_type !== sourceType.value) return false
      if (
        progressStatus.value === 'complete' &&
        paper.complete_analysis_count < paper.question_count
      ) return false
      if (
        progressStatus.value === 'pending' &&
        paper.complete_analysis_count >= paper.question_count
      ) return false
      if (!search) return true
      return [
        paper.title,
        paper.year,
        paper.exam_type,
        paper.grade,
        paper.semester,
        paper.folder_name,
        paper.textbook_version,
        paper.province,
        paper.city,
        paper.district,
      ].some((value) => value?.toLocaleLowerCase().includes(search))
    })
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
})

interface PaperFolder {
  key: string
  label: string
  manual: boolean
  papers: QuestionBankPaper[]
}

const paperFolders = computed<PaperFolder[]>(() => {
  const groups = new Map<string, PaperFolder>()
  for (const paper of filteredPapers.value) {
    const manualName = paper.folder_name?.trim() ?? ''
    const automaticName = [paper.year, paper.semester].filter(Boolean).join(' · ')
      || paper.semester
      || paper.year
      || '未归类'
    const label = manualName || automaticName
    const key = `${manualName ? 'manual' : 'semester'}:${label}`
    const group = groups.get(key) ?? {
      key,
      label,
      manual: Boolean(manualName),
      papers: [],
    }
    group.papers.push(paper)
    groups.set(key, group)
  }
  return [...groups.values()]
})

function toggleFolder(key: string): void {
  const next = new Set(collapsedFolderKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedFolderKeys.value = next
}

const totalQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.question_count,
  0,
))
const completeQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.complete_analysis_count,
  0,
))
const analysisJobs = computed(() => {
  const matching = Object.values(jobStore.jobs)
    .filter((job) => job.job_type === 'question_import' || job.job_type === 'tagging_sync')
    .sort((left, right) => right.id - left.id)
  const active = matching.filter((job) => !TERMINAL_JOB_STATUSES.has(job.status))
  const latestFinished = matching.find((job) => TERMINAL_JOB_STATUSES.has(job.status))
  return latestFinished ? [...active, latestFinished] : active
})

function analysisJobState(job: JobResponse): string {
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return job.detail || job.stage || '正在处理'
  if (job.status === 'cancelled') return '任务已取消；已保存的结果不会被撤销。'
  if (job.status === 'failed') {
    return job.job_type === 'question_import'
      ? '试卷入库失败，请检查文件后重试。'
      : '分析失败；已保存的结果保留，可补齐未完成项目。'
  }
  if (job.job_type === 'question_import') {
    return '试卷已入库，等待标签与判定点分析。'
  }
  const outcome = String(job.result.outcome ?? '')
  const reviewCount = jobResultCount(job, 'criteria_needs_review_count')
  if (reviewCount > 0) return `${reviewCount} 道题需要审核，禁止按分析成功展示。`
  if (outcome === 'complete') return '标签、解题证据和训练判定点均已完成。'
  if (outcome === 'partial') return '部分题目已完成，其余项目待补齐或审核。'
  if (outcome === 'failed') return '任务已结束，但标签或训练判定点没有保存成功。'
  return '分析任务已结束，请核对标签与判定点状态。'
}

function jobResultCount(job: JobResponse, key: string): number {
  const value = Number(job.result[key] ?? 0)
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0
}

function analysisJobMetrics(job: JobResponse): string {
  if (job.job_type === 'question_import') {
    const count = jobResultCount(job, 'question_count')
      || jobResultCount(job, 'imported_question_count')
    return count > 0 ? `已入库 ${count} 道题` : '仅执行本地入库'
  }
  const tagged = jobResultCount(job, 'complete_tagged_count')
  const evidence = jobResultCount(job, 'evidence_count')
  const criteria = jobResultCount(job, 'criteria_count')
  const review = jobResultCount(job, 'criteria_needs_review_count')
  const failed = jobResultCount(job, 'failed_count')
  return [
    `标签 ${tagged}`,
    `证据 ${evidence}`,
    `判定点 ${criteria}`,
    review > 0 ? `待审核 ${review}` : '',
    failed > 0 ? `未完成 ${failed}` : '',
  ].filter(Boolean).join(' · ')
}

watch(
  () => Object.values(jobStore.jobs)
    .filter((job) => job.job_type === 'question_import' || job.job_type === 'tagging_sync')
    .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
    .sort()
    .join('|'),
  (signature) => {
    if (!signature) return
    const terminalJob = Object.values(jobStore.jobs).find((job) => (
      (job.job_type === 'question_import' || job.job_type === 'tagging_sync')
      && TERMINAL_JOB_STATUSES.has(job.status)
      && !refreshedTerminalJobs.has(`${job.id}:${job.status}:${job.updated_at}`)
    ))
    if (!terminalJob) return
    refreshedTerminalJobs.add(`${terminalJob.id}:${terminalJob.status}:${terminalJob.updated_at}`)
    void store.loadPapers()
  },
)

function uniqueValues(values: Array<string | null>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value?.trim())))]
    .sort((left, right) => right.localeCompare(left, 'zh-CN'))
}

function progressFor(paper: QuestionBankPaper): number {
  if (paper.question_count === 0) return 0
  return Math.round((paper.complete_analysis_count / paper.question_count) * 100)
}

function sourceLabel(source: QuestionBankPaper['source_type']): string {
  if (source === 'docx') return 'Word'
  if (source === 'pdf') return 'PDF'
  return '文件'
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '日期未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function resetFilters(): void {
  keyword.value = ''
  year.value = ''
  examType.value = ''
  sourceType.value = ''
  progressStatus.value = ''
}

async function loadQuestionIds(
  paperIds?: number[],
  analysisStatus: 'all' | 'incomplete' = 'all',
): Promise<number[]> {
  const ids: number[] = []
  let page = 1
  while (true) {
    const result = await questionBankApi.listQuestions({
      page,
      pageSize: 100,
      paperIds,
      analysisStatus,
      sort: 'paper_order',
    })
    ids.push(...result.items.map((item) => item.id))
    if (page >= result.total_pages) break
    page += 1
  }
  return [...new Set(ids)]
}

function newRequestToken(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

function requestTokenFor(storageKey: string): string {
  const existing = window.localStorage.getItem(storageKey)
  if (existing && /^[0-9a-f]{32}$/.test(existing)) return existing
  const token = newRequestToken()
  window.localStorage.setItem(storageKey, token)
  return token
}

async function submitTaggingBatches(
  questionIds: number[],
  options: { forceRetag: boolean; scope: string; volumeId: string },
): Promise<number> {
  let jobs = 0
  for (let index = 0; index < questionIds.length; index += 500) {
    const batch = questionIds.slice(index, index + 500)
    if (batch.length === 0) continue
    const storageKey = `question-bank:tagging:${options.scope}:${index}:${batch.join('-')}`
    const requestToken = requestTokenFor(storageKey)
    const job = await questionBankApi.submitTagging(
      batch, options.volumeId, undefined, undefined, options.forceRetag, requestToken,
    )
    jobStore.track(job)
    window.localStorage.removeItem(storageKey)
    jobs += 1
  }
  return jobs
}

async function retagPaper(paper: QuestionBankPaper): Promise<void> {
  if (retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagBusyPaperId.value = paper.id
  taggingMode.value = 'retag'
  try {
    if (!paper.curriculum_volume_id) {
      retagMessage.value = '请先在“编辑资料”中补全年级、学期和教材版本。'
      return
    }
    const ids = await loadQuestionIds([paper.id])
    if (ids.length === 0) {
      retagMessage.value = '这份试卷没有可重新标注的题目。'
      return
    }
    if (!window.confirm(
      `将重新分析“${paper.title || `试卷 #${paper.id}`}”的 ${ids.length} 道题，可能产生模型费用；人工修改的标签会保留。确认继续吗？`,
    )) return
    const count = await submitTaggingBatches(ids, {
      forceRetag: true,
      scope: `paper-${paper.id}-retag`,
      volumeId: paper.curriculum_volume_id,
    })
    retagMessage.value = `已提交 ${ids.length} 道题，共 ${count} 个重新标注任务。`
  } catch {
    retagMessage.value = '重新标注任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagBusyPaperId.value = null
    taggingMode.value = null
  }
}

async function fillPaperTags(paper: QuestionBankPaper): Promise<void> {
  if (retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagBusyPaperId.value = paper.id
  taggingMode.value = 'fill'
  try {
    if (!paper.curriculum_volume_id) {
      retagMessage.value = '请先在“编辑资料”中补全年级、学期和教材版本。'
      return
    }
    const ids = await loadQuestionIds([paper.id], 'incomplete')
    if (ids.length === 0) {
      retagMessage.value = '这份试卷没有可补齐的题目。'
      return
    }
    if (!window.confirm(
      `已检查这份试卷：有 ${ids.length} 道题的标签、解题证据或训练判定点尚未完整。将只继续这些题，已经完整的题和人工修改不会重做；可能产生模型费用。确认继续吗？`,
    )) return
    const count = await submitTaggingBatches(ids, {
      forceRetag: false,
      scope: `paper-${paper.id}-fill`,
      volumeId: paper.curriculum_volume_id,
    })
    retagMessage.value = `已提交 ${ids.length} 道未完成题目，共 ${count} 个任务；已完成内容不会重做。`
  } catch {
    retagMessage.value = '补齐标签任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagBusyPaperId.value = null
    taggingMode.value = null
  }
}

async function retagAllPapers(): Promise<void> {
  if (retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagAllBusy.value = true
  taggingMode.value = 'retag'
  try {
    const scopes: Array<{ paper: QuestionBankPaper; ids: number[] }> = []
    for (const paper of store.papers) {
      if (!paper.curriculum_volume_id) continue
      const ids = await loadQuestionIds([paper.id])
      if (ids.length) scopes.push({ paper, ids })
    }
    const total = scopes.reduce((sum, scope) => sum + scope.ids.length, 0)
    if (total === 0) {
      retagMessage.value = '题库中没有可重新标注的题目。'
      return
    }
    if (!window.confirm(
      `将重新分析题库中的 ${total} 道题，可能产生模型费用；人工修改的标签会保留。确认继续吗？`,
    )) return
    let count = 0
    for (const { paper, ids } of scopes) {
      count += await submitTaggingBatches(ids, {
        forceRetag: true,
        scope: `paper-${paper.id}-retag`,
        volumeId: paper.curriculum_volume_id!,
      })
    }
    retagMessage.value = `已提交全库 ${total} 道题，共 ${count} 个重新标注任务。`
  } catch {
    retagMessage.value = '全库重新标注没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagAllBusy.value = false
    taggingMode.value = null
  }
}

async function fillAllTags(): Promise<void> {
  if (retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagAllBusy.value = true
  taggingMode.value = 'fill'
  try {
    const scopes: Array<{ paper: QuestionBankPaper; ids: number[] }> = []
    for (const paper of store.papers) {
      if (!paper.curriculum_volume_id) continue
      const ids = await loadQuestionIds([paper.id], 'incomplete')
      if (ids.length) scopes.push({ paper, ids })
    }
    const total = scopes.reduce((sum, scope) => sum + scope.ids.length, 0)
    if (total === 0) {
      retagMessage.value = '题库中没有可补齐的题目。'
      return
    }
    if (!window.confirm(
      `已检查全库：有 ${total} 道题尚未完成。将只提交这些题，完整题和人工修改不会重做；可能产生模型费用。确认继续吗？`,
    )) return
    let count = 0
    for (const { paper, ids } of scopes) {
      count += await submitTaggingBatches(ids, {
        forceRetag: false,
        scope: `paper-${paper.id}-fill`,
        volumeId: paper.curriculum_volume_id!,
      })
    }
    retagMessage.value = `已提交全库 ${total} 道未完成题目，共 ${count} 个任务。`
  } catch {
    retagMessage.value = '全库补齐标签没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagAllBusy.value = false
    taggingMode.value = null
  }
}

function editPaper(paper: QuestionBankPaper): void {
  editingPaperId.value = paper.id
  paperDraft.value = {
    title: paper.title ?? '',
    year: paper.year ?? '',
    province: paper.province ?? '',
    city: paper.city ?? '',
    district: paper.district ?? '',
    exam_type: paper.exam_type ?? '',
    grade: paper.grade ?? '',
    semester: paper.semester ?? '',
    folder_name: paper.folder_name ?? '',
    textbook_version: paper.textbook_version ?? '',
  }
  formError.value = ''
  store.resetPaperWriteStatus()
}

function closePaperEditor(): void {
  if (store.paperWriteState === 'saving') return
  editingPaperId.value = null
  formError.value = ''
  store.resetPaperWriteStatus()
}

function optional(value: string): string | null {
  return value.trim() || null
}

async function savePaperMetadata(): Promise<void> {
  if (editingPaperId.value === null || store.paperWriteState === 'saving') return
  const title = paperDraft.value.title.trim()
  if (!title) {
    formError.value = '请填写试卷名称。'
    return
  }
  formError.value = ''
  const metadata: QuestionBankPaperMetadataInput = {
    title,
    year: optional(paperDraft.value.year),
    province: optional(paperDraft.value.province),
    city: optional(paperDraft.value.city),
    district: optional(paperDraft.value.district),
    exam_type: optional(paperDraft.value.exam_type),
    grade: optional(paperDraft.value.grade),
    semester: optional(paperDraft.value.semester),
    folder_name: optional(paperDraft.value.folder_name),
    textbook_version: optional(paperDraft.value.textbook_version),
  }
  if (await store.updatePaperMetadata(editingPaperId.value, metadata)) {
    closePaperEditor()
  }
}

async function requestPermanentDelete(paper: QuestionBankPaper): Promise<void> {
  pendingDeletePaper.value = paper
  permanentDeleteState.value = 'loading'
  permanentDeleteMessage.value = ''
  deleteNotice.value = ''
  permanentDeleteRequestToken.value = ''
  try {
    permanentDeleteImpact.value = await questionBankApi.previewPaperPermanentDelete(
      [{
        id: paper.id,
        expected_updated_at: paper.updated_at,
      }],
    )
    permanentDeleteState.value = 'idle'
  } catch {
    permanentDeleteState.value = 'error'
    deleteNotice.value = '删除影响读取失败，没有删除任何内容。请刷新试卷库后重试。'
  }
}

function cancelPermanentDelete(): void {
  if (permanentDeleteState.value === 'working') return
  pendingDeletePaper.value = null
  permanentDeleteImpact.value = null
  permanentDeleteMessage.value = ''
  permanentDeleteRequestToken.value = ''
  permanentDeleteState.value = 'idle'
}

function requestToken(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
}

async function confirmPermanentDelete(): Promise<void> {
  const impact = permanentDeleteImpact.value
  const paper = pendingDeletePaper.value
  if (!impact || !paper || !canConfirmPermanentDelete.value) return
  permanentDeleteState.value = 'working'
  permanentDeleteMessage.value = ''
  if (!permanentDeleteRequestToken.value) {
    permanentDeleteRequestToken.value = requestToken()
  }
  try {
    const result = await questionBankApi.permanentlyDeletePapers(
      [{
        id: paper.id,
        expected_updated_at: paper.updated_at,
      }],
      impact.permanent_delete_phrase,
      permanentDeleteRequestToken.value,
    )
    await store.loadPapers()
    pendingDeletePaper.value = null
    permanentDeleteImpact.value = null
    permanentDeleteRequestToken.value = ''
    permanentDeleteState.value = 'idle'
    deleteNotice.value = `已删除 ${result.deleted_paper_ids.length} 份试卷、${result.deleted_question_count} 道题和 ${result.deleted_tag_count} 个标签。`
  } catch {
    permanentDeleteState.value = 'error'
    permanentDeleteMessage.value = '未收到服务器确认，删除结果尚不确定。请保留此窗口并点击重试；系统会使用同一请求编号核对，不会重复删除。'
  }
}
</script>

<template>
  <section class="paper-library" aria-labelledby="paper-library-title">
    <header class="paper-library__header">
      <div>
        <p class="paper-library__eyebrow">PAPER LIBRARY</p>
        <h1 id="paper-library-title">试卷库</h1>
        <p>以老师上传的 Word 或 PDF 试卷为入口，再进入试题核对与标注。</p>
      </div>
      <div class="paper-library__stats" aria-label="试卷库概况">
        <span><strong>{{ store.papers.length }}</strong> 份试卷</span>
        <span><strong>{{ totalQuestions }}</strong> 道题</span>
        <span><strong>{{ completeQuestions }}</strong> 道联合分析完整</span>
        <button
          type="button"
          class="paper-button is-quiet"
          :disabled="retagAllBusy || retagBusyPaperId !== null"
          @click="fillAllTags"
        >{{ retagAllBusy && taggingMode === 'fill' ? '正在检查未完成题…' : '继续完成未完成题目' }}</button>
        <button
          type="button"
          class="paper-button is-review"
          @click="emit('reviewTaxonomy')"
        >
          待审核新词
          <strong>{{ pendingTaxonomyLabel }}</strong>
        </button>
        <button
          type="button"
          class="paper-button is-quiet"
          :disabled="retagAllBusy || retagBusyPaperId !== null"
          @click="retagAllPapers"
        >{{ retagAllBusy && taggingMode === 'retag' ? '正在准备…' : '全库重新打标签' }}</button>
        <button type="button" class="paper-button is-primary" @click="emit('import')">
          上传试卷
        </button>
      </div>
    </header>

    <p v-if="retagMessage" class="paper-library__notice" role="status">{{ retagMessage }}</p>
    <p v-if="deleteNotice" class="paper-library__notice" role="status">{{ deleteNotice }}</p>

    <section v-if="analysisJobs.length" class="paper-library__task-strip" aria-live="polite">
      <div class="paper-library__task-strip-heading">
        <strong>AI 解析进度</strong>
        <span>自动刷新</span>
      </div>
      <div
        v-for="job in analysisJobs"
        :key="job.id"
        class="paper-library__task"
        :class="{ 'is-failed': job.status === 'failed' || job.result.outcome === 'failed' }"
      >
        <div class="paper-library__task-main">
          <strong>{{ job.job_type === 'question_import' ? '试卷入库' : '标签与训练点补齐' }} #{{ job.id }}</strong>
          <span>{{ analysisJobState(job) }}</span>
        </div>
        <div class="paper-library__task-progress">
          <progress :value="Math.round(job.progress * 100)" max="100">
            {{ Math.round(job.progress * 100) }}%
          </progress>
          <span>{{ Math.round(job.progress * 100) }}%</span>
        </div>
        <span class="paper-library__task-metrics">{{ analysisJobMetrics(job) }}</span>
      </div>
    </section>

    <div class="paper-library__filters">
      <label class="paper-search">
        <span class="sr-only">搜索试卷</span>
        <input v-model="keyword" type="search" placeholder="搜索试卷名称、地区或教材">
      </label>
      <label>
        <span>年份</span>
        <select v-model="year">
          <option value="">全部年份</option>
          <option v-for="item in years" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>试卷类型</span>
        <select v-model="examType">
          <option value="">全部类型</option>
          <option v-for="item in examTypes" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>文件</span>
        <select v-model="sourceType">
          <option value="">全部文件</option>
          <option value="docx">Word</option>
          <option value="pdf">PDF</option>
          <option value="other">其他</option>
        </select>
      </label>
      <label>
        <span>标注</span>
        <select v-model="progressStatus">
          <option value="">全部进度</option>
          <option value="complete">已完成</option>
          <option value="pending">待完善</option>
        </select>
      </label>
      <button type="button" class="paper-button is-quiet" @click="resetFilters">清除</button>
    </div>

    <p v-if="store.papersState === 'loading'" class="paper-library__state" role="status">
      正在读取试卷库…
    </p>
    <div v-else-if="store.papersState === 'error'" class="paper-library__state is-error" role="alert">
      <span>试卷库暂时无法读取。</span>
      <button type="button" class="paper-button is-quiet" @click="store.loadPapers()">重新读取</button>
    </div>
    <div v-else-if="filteredPapers.length === 0" class="paper-library__state">
      <strong>{{ store.papers.length ? '当前筛选下没有试卷' : '还没有导入试卷' }}</strong>
      <p>{{ store.papers.length ? '可以清除筛选后再查看。' : '上传 Word 或 PDF 后，会在这里生成一张试卷卡片。' }}</p>
    </div>

    <div v-else class="paper-folders">
      <section v-for="folder in paperFolders" :key="folder.key" class="paper-folder">
        <button
          type="button"
          class="paper-folder__header"
          :aria-expanded="!collapsedFolderKeys.has(folder.key)"
          @click="toggleFolder(folder.key)"
        >
          <span class="paper-folder__chevron" aria-hidden="true">{{ collapsedFolderKeys.has(folder.key) ? '›' : '⌄' }}</span>
          <strong>{{ folder.label }}</strong>
          <span class="paper-folder__kind">{{ folder.manual ? '自定义文件夹' : '按学期自动归类' }}</span>
          <span class="paper-folder__count">{{ folder.papers.length }} 份</span>
        </button>
        <div v-if="!collapsedFolderKeys.has(folder.key)" class="paper-library__grid">
          <article v-for="paper in folder.papers" :key="paper.id" class="paper-card">
        <div class="paper-card__cover" :class="`is-${paper.source_type}`" aria-hidden="true">
          <span>{{ sourceLabel(paper.source_type) }}</span>
          <strong>试卷</strong>
          <i />
          <i />
          <i />
        </div>
        <div class="paper-card__body">
          <div class="paper-card__topline">
            <span class="paper-chip is-format">{{ sourceLabel(paper.source_type) }}</span>
            <span v-if="paper.year" class="paper-chip">{{ paper.year }}</span>
            <span v-if="paper.exam_type" class="paper-chip">{{ paper.exam_type }}</span>
            <span v-if="paper.grade" class="paper-chip">{{ paper.grade }}</span>
          </div>
          <h2>{{ paper.title || `未命名试卷 #${paper.id}` }}</h2>
          <p class="paper-card__meta">
            {{
              [
                paper.province,
                paper.city,
                paper.district,
                paper.semester,
                paper.textbook_version,
              ].filter(Boolean).join(' · ') || '来源信息待补充'
            }}
          </p>
          <div class="paper-card__progress-heading">
            <span>联合分析完整度</span>
            <strong>{{ paper.complete_analysis_count }} / {{ paper.question_count }} 道</strong>
          </div>
          <div
            class="paper-card__progress"
            role="progressbar"
            aria-label="联合分析完整度"
            :aria-valuenow="progressFor(paper)"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span :style="{ width: `${progressFor(paper)}%` }" />
          </div>
          <p class="paper-card__progress-note">
            {{ progressFor(paper) }}% 完整
            · 标签 {{ paper.tagged_question_count }}/{{ paper.question_count }}
            · 解题证据 {{ paper.evidence_question_count }}/{{ paper.question_count }}
            · 训练判定点 {{ paper.criteria_question_count }}/{{ paper.question_count }}
          </p>
          <footer>
            <span>更新于 {{ formatDate(paper.updated_at) }}</span>
            <div class="paper-card__actions">
              <button
                type="button"
                class="paper-button is-quiet"
                :disabled="retagBusyPaperId !== null || retagAllBusy"
                @click="fillPaperTags(paper)"
              >{{
                retagBusyPaperId === paper.id && taggingMode === 'fill'
                  ? '准备中…'
                  : '继续完成未完成题目'
              }}</button>
              <button
                type="button"
                class="paper-button is-danger-quiet"
                :disabled="permanentDeleteState === 'loading' || permanentDeleteState === 'working'"
                :aria-label="`永久删除${paper.title || `试卷 ${paper.id}`}`"
                title="永久删除"
                @click="requestPermanentDelete(paper)"
              >
                删除
              </button>
              <button
                type="button"
                class="paper-button is-quiet"
                :disabled="retagBusyPaperId !== null || retagAllBusy"
                @click="retagPaper(paper)"
              >{{
                retagBusyPaperId === paper.id && taggingMode === 'retag'
                  ? '准备中…'
                  : '重新打标签'
              }}</button>
              <button type="button" class="paper-button is-quiet" @click="editPaper(paper)">
                编辑资料
              </button>
              <button type="button" class="paper-button is-primary" @click="emit('open', paper)">
                查看试题
              </button>
            </div>
          </footer>
        </div>
          </article>
        </div>
      </section>
    </div>

    <Teleport to="body">
      <div
        v-if="editingPaperId !== null"
        class="paper-editor-layer"
        @click.self="closePaperEditor"
      >
        <aside
          class="paper-editor"
          role="dialog"
          aria-modal="true"
          aria-labelledby="paper-editor-title"
        >
          <header class="paper-editor__header">
            <div>
              <p>试卷标签</p>
              <h2 id="paper-editor-title">编辑试卷资料</h2>
              <span>这里的内容会显示在试卷卡片，并用于题库筛选。</span>
            </div>
            <button
              type="button"
              class="paper-editor__close"
              aria-label="关闭试卷资料编辑"
              :disabled="store.paperWriteState === 'saving'"
              @click="closePaperEditor"
            >
              ×
            </button>
          </header>

          <form class="paper-editor__form" @submit.prevent="savePaperMetadata">
            <label class="is-wide">
              <span>试卷名称 <strong aria-hidden="true">*</strong></span>
              <input
                v-model="paperDraft.title"
                name="paper-title"
                maxlength="255"
                autocomplete="off"
                :aria-invalid="Boolean(formError)"
              >
              <small>同步批改任务时默认使用原始 Word 或 PDF 文件名。</small>
            </label>

            <label>
              <span>年份</span>
              <input
                v-model="paperDraft.year"
                name="paper-year"
                maxlength="24"
                inputmode="numeric"
                placeholder="如：2026"
              >
            </label>
            <label>
              <span>试卷类型</span>
              <input
                v-model="paperDraft.exam_type"
                name="paper-exam-type"
                list="paper-exam-type-options"
                maxlength="48"
                placeholder="如：阶段练习"
              >
              <datalist id="paper-exam-type-options">
                <option value="阶段练习" />
                <option value="单元测试" />
                <option value="期中" />
                <option value="期末" />
                <option value="模拟考试" />
              </datalist>
            </label>

            <label>
              <span>年级</span>
              <input
                v-model="paperDraft.grade"
                name="paper-grade"
                list="paper-grade-options"
                maxlength="48"
                placeholder="如：七年级"
              >
              <datalist id="paper-grade-options">
                <option value="七年级" />
                <option value="八年级" />
                <option value="九年级" />
              </datalist>
            </label>
            <label>
              <span>学期</span>
              <input
                v-model="paperDraft.semester"
                name="paper-semester"
                list="paper-semester-options"
                maxlength="48"
                placeholder="如：下学期"
              >
              <datalist id="paper-semester-options">
                <option value="上学期" />
                <option value="下学期" />
              </datalist>
            </label>

            <label class="is-wide">
              <span>自定义文件夹（可选）</span>
              <input
                v-model="paperDraft.folder_name"
                name="paper-folder-name"
                maxlength="80"
                placeholder="如：中考专题卷"
              >
              <small>留空时，系统会按上面的年份和学期自动归类。</small>
            </label>

            <label class="is-wide">
              <span>教材版本</span>
              <input
                v-model="paperDraft.textbook_version"
                name="paper-textbook-version"
                maxlength="100"
                placeholder="如：北师大版"
              >
            </label>

            <fieldset class="paper-editor__region is-wide">
              <legend>来源地区</legend>
              <label>
                <span>省份</span>
                <input v-model="paperDraft.province" name="paper-province" maxlength="48">
              </label>
              <label>
                <span>城市</span>
                <input v-model="paperDraft.city" name="paper-city" maxlength="48">
              </label>
              <label>
                <span>区县</span>
                <input v-model="paperDraft.district" name="paper-district" maxlength="48">
              </label>
            </fieldset>

            <p v-if="formError" class="paper-editor__message is-error" role="alert">
              {{ formError }}
            </p>
            <p
              v-else-if="store.paperWriteMessage"
              class="paper-editor__message"
              :class="{ 'is-error': store.paperWriteState === 'error' || store.paperWriteState === 'conflict' }"
              :role="store.paperWriteState === 'error' || store.paperWriteState === 'conflict' ? 'alert' : 'status'"
            >
              {{ store.paperWriteMessage }}
            </p>

            <footer class="paper-editor__actions is-wide">
              <button
                type="button"
                class="paper-button is-quiet"
                :disabled="store.paperWriteState === 'saving'"
                @click="closePaperEditor"
              >
                取消
              </button>
              <button
                type="submit"
                class="paper-button is-primary"
                :disabled="store.paperWriteState === 'saving'"
              >
                {{ store.paperWriteState === 'saving' ? '正在保存…' : '保存资料' }}
              </button>
            </footer>
          </form>
        </aside>
      </div>

      <div
        v-if="permanentDeleteImpact"
        class="paper-trash-confirm-layer"
        @click.self="cancelPermanentDelete"
      >
        <section
          class="paper-trash-confirm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="paper-permanent-delete-title"
        >
          <p class="paper-trash-confirm__eyebrow">不可恢复</p>
          <h2 id="paper-permanent-delete-title">确认彻底删除？</h2>
          <strong>{{ pendingDeletePaper?.title || `未命名试卷 #${pendingDeletePaper?.id}` }}</strong>
          <p class="paper-permanent-impact">
            <span><b>{{ permanentDeleteImpact.paper_count }}</b> 份试卷</span>
            <span><b>{{ permanentDeleteImpact.question_count }}</b> 道题</span>
            <span><b>{{ permanentDeleteImpact.tag_count }}</b> 个标签</span>
            <span><b>{{ permanentDeleteImpact.owned_file_count }}</b> 个本地文件</span>
            <span><b>{{ permanentDeleteImpact.training_link_count }}</b> 条训练关联</span>
            <span><b>{{ permanentDeleteImpact.knowledge_graph_link_count }}</b> 条知识图谱计数来源</span>
          </p>
          <p>
            本地 Word/PDF、题目和标签会删除；训练材料快照保留，但不再计入这些题；
            已完成考试的答卷与成绩不受影响。
            <template v-if="permanentDeleteImpact.shared_file_count">
              另有 {{ permanentDeleteImpact.shared_file_count }} 个共享文件仍被其他试卷使用，将保留。
            </template>
          </p>
          <p v-if="permanentDeleteMessage" class="paper-trash-confirm__message is-error" role="alert">
            {{ permanentDeleteMessage }}
          </p>
          <footer>
            <button
              type="button"
              class="paper-button is-quiet"
              :disabled="permanentDeleteState === 'working'"
              @click="cancelPermanentDelete"
            >取消</button>
            <button
              type="button"
              class="paper-button is-danger"
              :disabled="!canConfirmPermanentDelete"
              @click="confirmPermanentDelete"
            >{{ permanentDeleteState === 'working' ? '正在彻底删除…' : '确认彻底删除' }}</button>
          </footer>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.paper-library {
  display: grid;
  gap: 18px;
}

.paper-library__header {
  align-items: flex-end;
  display: flex;
  gap: 24px;
  justify-content: space-between;
}

.paper-library__eyebrow {
  color: var(--color-accent, #135e6b) !important;
  font-size: 11px !important;
  font-weight: 750;
  letter-spacing: .13em;
  margin: 0 0 5px !important;
}

.paper-library__header h1 {
  color: var(--color-text-primary, #1c2733);
  font-size: 30px;
  letter-spacing: -.04em;
  margin: 0;
}

.paper-library__header p {
  color: var(--color-text-secondary, #5c6672);
  font-size: 14px;
  margin: 7px 0 0;
}

.paper-library__stats {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  justify-content: flex-end;
}

.paper-library__stats span {
  color: var(--color-text-secondary, #5c6672);
  font-size: 12px;
}

.paper-library__stats strong {
  color: var(--color-text-primary, #1c2733);
  font-size: 19px;
  margin-right: 2px;
}

.paper-library__filters {
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 12px;
  display: grid;
  gap: 10px;
  grid-template-columns: minmax(240px, 1fr) repeat(4, minmax(118px, 150px)) auto;
  padding: 14px;
}

.paper-library__filters label {
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 11px;
  font-weight: 650;
  gap: 5px;
}

.paper-library__filters input,
.paper-library__filters select {
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  font: inherit;
  font-size: 13px;
  height: 36px;
  min-width: 0;
  padding: 0 10px;
}

.paper-library__filters input:focus,
.paper-library__filters select:focus {
  border-color: var(--color-accent, #135e6b);
  box-shadow: 0 0 0 3px rgb(19 94 107 / 10%);
  outline: none;
}

.paper-library__grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr));
}

.paper-folders {
  display: grid;
  gap: 18px;
}

.paper-folder {
  display: grid;
  gap: 10px;
}

.paper-folder__header {
  align-items: center;
  background: #f6f8f8;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 10px;
  color: var(--color-text-primary, #1c2733);
  cursor: pointer;
  display: flex;
  font: inherit;
  gap: 9px;
  min-height: 46px;
  padding: 9px 13px;
  text-align: left;
  width: 100%;
}

.paper-folder__header:hover,
.paper-folder__header:focus-visible {
  background: #edf4f3;
  border-color: rgb(19 94 107 / 35%);
  outline: none;
}

.paper-folder__chevron {
  color: var(--color-accent, #135e6b);
  font-size: 22px;
  line-height: 1;
  width: 16px;
}

.paper-folder__kind {
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 999px;
  color: var(--color-text-secondary, #64707d);
  font-size: 11px;
  padding: 3px 8px;
}

.paper-folder__count {
  color: var(--color-text-secondary, #64707d);
  font-size: 12px;
  margin-left: auto;
}

.paper-card {
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 12px;
  box-shadow: 0 1px 2px rgb(28 39 51 / 5%);
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  min-height: 230px;
  overflow: hidden;
  transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
}

.paper-card:hover {
  border-color: rgb(19 94 107 / 35%);
  box-shadow: 0 8px 26px rgb(28 39 51 / 8%);
  transform: translateY(-1px);
}

.paper-card__cover {
  align-items: center;
  background: #edf4f3;
  border-right: 1px solid var(--color-border, #e2e4e7);
  color: var(--color-accent, #135e6b);
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  padding: 16px;
  position: relative;
}

.paper-card__cover::before {
  background: currentColor;
  content: "";
  height: 100%;
  left: 0;
  opacity: .84;
  position: absolute;
  top: 0;
  width: 4px;
}

.paper-card__cover.is-pdf {
  background: #f8f0ed;
  color: #8a4b37;
}

.paper-card__cover.is-other {
  background: #f1f2f4;
  color: #5c6672;
}

.paper-card__cover span {
  font-size: 12px;
  font-weight: 750;
  letter-spacing: .08em;
}

.paper-card__cover strong {
  font-size: 25px;
  letter-spacing: .08em;
  margin: 9px 0 16px;
}

.paper-card__cover i {
  background: currentColor;
  height: 2px;
  margin: 3px 0;
  opacity: .18;
  width: 56px;
}

.paper-card__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px 17px 14px;
}

.paper-card__topline {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.paper-chip {
  background: #f4f5f6;
  border-radius: 999px;
  color: var(--color-text-secondary, #5c6672);
  font-size: 11px;
  padding: 3px 8px;
}

.paper-chip.is-format {
  background: rgb(19 94 107 / 10%);
  color: var(--color-accent, #135e6b);
  font-weight: 700;
}

.paper-card h2 {
  color: var(--color-text-primary, #1c2733);
  display: -webkit-box;
  font-size: 16px;
  line-height: 1.45;
  margin: 11px 0 5px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.paper-card__meta {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 12px;
  margin: 0;
}

.paper-card__progress-heading {
  color: var(--color-text-secondary, #5c6672);
  display: flex;
  font-size: 11px;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 15px;
}

.paper-card__progress-heading strong {
  color: var(--color-text-primary, #1c2733);
}

.paper-card__progress {
  background: #edf0f1;
  border-radius: 999px;
  height: 6px;
  margin-top: 7px;
  overflow: hidden;
}

.paper-card__progress span {
  background: var(--color-accent, #135e6b);
  border-radius: inherit;
  display: block;
  height: 100%;
  min-width: 2px;
}

.paper-card__progress-note {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 11px;
  margin: 6px 0 0;
}

.paper-card footer {
  align-items: center;
  border-top: 1px solid var(--color-border, #e2e4e7);
  color: var(--color-text-tertiary, #6b7684);
  display: flex;
  font-size: 11px;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 11px;
}

.paper-card__actions {
  align-items: center;
  display: flex;
  gap: 8px;
}

.paper-button {
  align-items: center;
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  min-height: 34px;
  justify-content: center;
  padding: 0 13px;
  white-space: nowrap;
  flex-shrink: 0;
}

.paper-button:disabled {
  cursor: wait;
  opacity: .58;
}

.paper-button:hover {
  background: #f6f7f7;
  border-color: #b9c0c5;
}

.paper-button.is-primary {
  background: var(--color-accent, #135e6b);
  border-color: var(--color-accent, #135e6b);
  color: #fff;
}

.paper-button.is-primary:hover {
  background: var(--color-accent-hover, #0e4a54);
}

.paper-button.is-quiet {
  color: var(--color-text-secondary, #5c6672);
}

.paper-button.is-danger-quiet {
  border-color: #e2c2bd;
  color: #9a4136;
}

.paper-button.is-danger-quiet:hover {
  background: #fff4f2;
  border-color: #cd8b82;
}

.paper-button.is-danger {
  background: #9a4136;
  border-color: #9a4136;
  color: #fff;
}

.paper-button.is-danger:hover {
  background: #7f332b;
  border-color: #7f332b;
}

.paper-button.is-review {
  background: #fff8e9;
  border-color: #efdcb0;
  color: #805a16;
  gap: 6px;
}

.paper-button.is-review:hover {
  background: #fff3d7;
  border-color: #e2c67f;
}

.paper-button.is-review strong {
  background: #805a16;
  border-radius: 999px;
  color: #fff;
  font-size: 10px;
  line-height: 18px;
  margin: 0;
  min-width: 18px;
  padding: 0 5px;
}

.paper-button.paper-icon-button {
  width: 38px;
  min-width: 38px;
  padding-inline: 0;
  font-size: 20px;
  line-height: 1;
}

.paper-library__state {
  align-items: center;
  background: #fff;
  border: 1px dashed var(--color-border-strong, #d8dbdf);
  border-radius: 12px;
  color: var(--color-text-secondary, #5c6672);
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: center;
  min-height: 180px;
  padding: 24px;
  text-align: center;
}

.paper-library__state p {
  margin: 0;
}

.paper-library__state.is-error {
  color: #9a4136;
}

.paper-library__notice {
  margin: 0;
  padding: 9px 12px;
  border-inline-start: 3px solid var(--color-accent, #135e6b);
  border-radius: 6px;
  background: #f4faf9;
  color: var(--color-text-secondary, #5c6672);
  font-size: 13px;
}

.paper-library__task-strip {
  display: grid;
  gap: 7px;
  padding: 10px 12px;
  border: 1px solid rgb(19 94 107 / 18%);
  border-radius: 12px;
  background: #f4faf9;
}

.paper-library__task-strip-heading,
.paper-library__task {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
}

.paper-library__task-strip-heading {
  justify-content: space-between;
}

.paper-library__task-strip-heading span,
.paper-library__task-main span {
  color: var(--color-text-secondary, #5c6672);
  font-size: 12px;
}

.paper-library__task-main {
  display: grid;
  flex: 1 1 300px;
  min-width: 220px;
  gap: 2px;
}

.paper-library__task-progress {
  display: flex;
  flex: 0 1 340px;
  align-items: center;
  gap: 8px;
}

.paper-library__task-progress progress {
  width: min(280px, 28vw);
  height: 8px;
  accent-color: var(--color-accent, #135e6b);
}

.paper-library__task-progress span,
.paper-library__task-metrics {
  color: var(--color-text-secondary, #5c6672);
  font-size: 12px;
  white-space: nowrap;
}

.paper-library__task.is-failed {
  margin-inline: -6px;
  padding: 8px 6px;
  border-inline-start: 4px solid #b7791f;
  border-radius: 8px;
  background: #fff8e6;
}

@media (max-width: 620px) {
  .paper-library__task-strip-heading,
  .paper-library__task {
    align-items: flex-start;
    flex-direction: column;
  }

  .paper-library__task-progress,
  .paper-library__task-progress progress {
    width: 100%;
  }

  .paper-library__task-metrics {
    white-space: normal;
  }
}

.paper-editor-layer {
  align-items: stretch;
  background: rgb(28 39 51 / 38%);
  display: flex;
  inset: 0;
  justify-content: flex-end;
  position: fixed;
  z-index: 1200;
}

.paper-editor {
  background: #fff;
  border-left: 1px solid var(--color-border, #e2e4e7);
  box-shadow: -12px 0 32px rgb(28 39 51 / 12%);
  display: flex;
  flex-direction: column;
  max-width: 100%;
  overflow: auto;
  width: 580px;
}

.paper-trash-drawer {
  background: #fff;
  border-left: 1px solid var(--color-border, #e2e4e7);
  box-shadow: -12px 0 32px rgb(28 39 51 / 12%);
  display: flex;
  flex-direction: column;
  max-width: 100%;
  overflow: auto;
  width: 620px;
}

.paper-trash-drawer__body {
  display: grid;
  gap: 16px;
  padding: 22px 24px 28px;
}

.paper-trash-filters {
  align-items: end;
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(150px, 1fr) repeat(3, minmax(92px, 110px));
}

.paper-trash-filters label {
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 10px;
  gap: 4px;
}

.paper-trash-filters input,
.paper-trash-filters select,
.paper-permanent-confirmation input {
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  font: inherit;
  height: 36px;
  min-width: 0;
  padding: 0 9px;
}

.paper-trash-list {
  display: grid;
  gap: 10px;
}

.paper-trash-selection-bar {
  align-items: center;
  background: #f4f7f7;
  border-radius: 9px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 9px 10px;
}

.paper-trash-selection-bar label {
  align-items: center;
  display: inline-flex;
  gap: 6px;
  margin-right: auto;
}

.paper-trash-selection-bar span {
  color: var(--color-text-secondary, #5c6672);
  font-size: 11px;
}

.paper-trash-list article {
  align-items: center;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 10px;
  display: flex;
  gap: 18px;
  justify-content: space-between;
  padding: 15px;
}

.paper-trash-item-check {
  align-items: center;
  align-self: stretch;
  display: flex;
}

.paper-trash-item-check input,
.paper-trash-selection-bar input {
  accent-color: var(--color-accent, #135e6b);
  height: 16px;
  margin: 0;
  width: 16px;
}

.paper-trash-list h3 {
  color: var(--color-text-primary, #1c2733);
  font-size: 15px;
  line-height: 1.45;
  margin: 7px 0 4px;
}

.paper-trash-list p {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 11px;
  margin: 0;
}

.paper-trash-list .paper-button {
  flex: 0 0 auto;
}

.paper-trash-drawer__message,
.paper-trash-confirm__message {
  background: #f2f7f6;
  border: 1px solid rgb(19 94 107 / 18%);
  border-radius: 8px;
  color: var(--color-accent, #135e6b);
  font-size: 12px;
  line-height: 1.55;
  margin: 0;
  padding: 10px 12px;
}

.paper-trash-drawer__message.is-error,
.paper-trash-confirm__message.is-error {
  background: #fff4f2;
  border-color: #edc8c3;
  color: #9a4136;
}

.paper-trash-confirm-layer {
  align-items: center;
  background: rgb(28 39 51 / 42%);
  display: flex;
  inset: 0;
  justify-content: center;
  padding: 20px;
  position: fixed;
  z-index: 1250;
}

.paper-trash-confirm {
  background: #fff;
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 14px;
  box-shadow: 0 18px 52px rgb(28 39 51 / 18%);
  max-width: 100%;
  padding: 24px;
  width: 470px;
}

.paper-trash-confirm__eyebrow {
  color: #9a4136 !important;
  font-size: 11px !important;
  font-weight: 750;
  letter-spacing: .12em;
  margin: 0 0 6px !important;
}

.paper-trash-confirm h2 {
  color: var(--color-text-primary, #1c2733);
  font-size: 21px;
  margin: 0 0 14px;
}

.paper-trash-confirm > strong {
  color: var(--color-text-primary, #1c2733);
  display: block;
  font-size: 14px;
}

.paper-trash-confirm > p:not(.paper-trash-confirm__eyebrow, .paper-trash-confirm__message) {
  color: var(--color-text-secondary, #5c6672);
  font-size: 13px;
  line-height: 1.7;
  margin: 8px 0 0;
}

.paper-trash-confirm footer {
  display: flex;
  gap: 9px;
  justify-content: flex-end;
  margin-top: 20px;
}

.paper-permanent-impact {
  display: grid;
  gap: 7px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-block: 14px !important;
}

.paper-permanent-impact span {
  background: #f5f6f6;
  border-radius: 7px;
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 10px;
  gap: 2px;
  padding: 8px;
}

.paper-permanent-impact b {
  color: var(--color-text-primary, #1c2733);
  font-size: 16px;
}

.paper-permanent-confirmation {
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 12px;
  gap: 6px;
  margin-top: 14px;
}

.paper-editor__header {
  align-items: flex-start;
  border-bottom: 1px solid var(--color-border, #e2e4e7);
  display: flex;
  gap: 16px;
  justify-content: space-between;
  padding: 24px;
}

.paper-editor__header p {
  color: var(--color-accent, #135e6b);
  font-size: 11px;
  font-weight: 750;
  letter-spacing: .13em;
  margin: 0 0 5px;
  text-transform: uppercase;
}

.paper-editor__header h2 {
  color: var(--color-text-primary, #1c2733);
  font-size: 22px;
  letter-spacing: -.02em;
  margin: 0;
}

.paper-editor__header span {
  color: var(--color-text-secondary, #5c6672);
  display: block;
  font-size: 13px;
  margin-top: 7px;
}

.paper-editor__close {
  align-items: center;
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-secondary, #5c6672);
  cursor: pointer;
  display: inline-flex;
  flex: 0 0 auto;
  font: inherit;
  font-size: 22px;
  height: 36px;
  justify-content: center;
  line-height: 1;
  width: 36px;
}

.paper-editor__form {
  display: grid;
  gap: 18px 14px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 24px;
}

.paper-editor__form > label,
.paper-editor__region label {
  color: var(--color-text-secondary, #5c6672);
  display: grid;
  font-size: 12px;
  font-weight: 650;
  gap: 7px;
}

.paper-editor__form label strong {
  color: #9a4136;
}

.paper-editor__form input {
  background: #fff;
  border: 1px solid var(--color-border-strong, #d8dbdf);
  border-radius: 8px;
  color: var(--color-text-primary, #1c2733);
  font: inherit;
  font-size: 14px;
  height: 40px;
  min-width: 0;
  padding: 0 11px;
}

.paper-editor__form input:focus {
  border-color: var(--color-accent, #135e6b);
  box-shadow: 0 0 0 3px rgb(19 94 107 / 10%);
  outline: none;
}

.paper-editor__form input[aria-invalid="true"] {
  border-color: #b84f43;
}

.paper-editor__form small {
  color: var(--color-text-tertiary, #6b7684);
  font-size: 11px;
  font-weight: 400;
  line-height: 1.5;
}

.paper-editor__form .is-wide {
  grid-column: 1 / -1;
}

.paper-editor__region {
  border: 1px solid var(--color-border, #e2e4e7);
  border-radius: 10px;
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  padding: 14px;
}

.paper-editor__region legend {
  color: var(--color-text-secondary, #5c6672);
  font-size: 12px;
  font-weight: 700;
  padding: 0 5px;
}

.paper-editor__message {
  background: #f2f7f6;
  border: 1px solid rgb(19 94 107 / 18%);
  border-radius: 8px;
  color: var(--color-accent, #135e6b);
  font-size: 12px;
  grid-column: 1 / -1;
  line-height: 1.55;
  margin: 0;
  padding: 10px 12px;
}

.paper-editor__message.is-error {
  background: #fff4f2;
  border-color: #edc8c3;
  color: #9a4136;
}

.paper-editor__actions {
  align-items: center;
  border-top: 1px solid var(--color-border, #e2e4e7);
  display: flex;
  gap: 9px;
  justify-content: flex-end;
  margin-top: 4px;
  padding-top: 18px;
}

@media (max-width: 1260px) {
  .paper-library__filters {
    grid-template-columns: minmax(220px, 1fr) repeat(2, minmax(130px, 1fr));
  }

  .paper-library__grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .paper-library__header {
    align-items: flex-start;
    flex-direction: column;
  }

  .paper-library__stats {
    justify-content: flex-start;
  }

  .paper-library__filters {
    grid-template-columns: 1fr 1fr;
  }

  .paper-search {
    grid-column: 1 / -1;
  }

  .paper-card {
    grid-template-columns: 78px minmax(0, 1fr);
  }

  .paper-card footer,
  .paper-trash-list article {
    align-items: flex-start;
    flex-direction: column;
  }

  .paper-trash-filters,
  .paper-permanent-impact {
    grid-template-columns: 1fr 1fr;
  }

  .paper-card__actions {
    flex-wrap: wrap;
  }

  .paper-editor__form,
  .paper-editor__region {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .paper-card {
    transition: none;
  }
}
</style>
