<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  onScopeDispose,
  ref,
  watch,
} from 'vue'
import { storeToRefs } from 'pinia'
import { useDebounceFn } from '@vueuse/core'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import StatePanel from '../design-system/StatePanel.vue'
import AppDialog from '../design-system/AppDialog.vue'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../ui/sheet'

import type {
  QuestionBankPaper,
  QuestionBankPaperMetadataInput,
  QuestionBankPaperPermanentDeleteImpact,
} from '../../api/question-bank'
import { questionBankApi } from '../../api/question-bank'
import { useConfirm } from '../../composables/useConfirm'
import { useQuestionBankStore } from '../../stores/question-bank'
import { useJobStore } from '../../stores/jobs'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import AppButton from '../design-system/AppButton.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import {
  isQuestionBankLibraryJob,
  jobBelongsToPaper,
  paperCurrentStatusLines,
  paperLiveAnalysisLine,
  type PaperQuestionRef,
} from './paper-analysis-status'

withDefaults(defineProps<{
  pendingTaxonomyCount?: number
  pendingTaxonomyState?: 'idle' | 'loading' | 'ready' | 'empty' | 'error'
  headerTarget?: string
  activePaperId?: number
}>(), {
  pendingTaxonomyCount: 0,
  pendingTaxonomyState: 'ready',
})

const emit = defineEmits<{
  open: [paper: QuestionBankPaper]
  import: []
  reviewTaxonomy: []
  reviewCriteria: []
}>()

const store = useQuestionBankStore()
const { confirm } = useConfirm()
const jobStore = useJobStore()
const curriculumScope = useCurriculumScopeStore()
const keyword = ref('')
const category = ref('')
const onlyIssues = ref(false)
async function continuePaper(paper: QuestionBankPaper) {
  const previous = selectedPaperIds.value
  selectedPaperIds.value = new Set([paper.id])
  try { await fillSelectedPapers() } finally { selectedPaperIds.value = previous }
}
async function answerPaper(paper: QuestionBankPaper) {
  const previous = selectedPaperIds.value
  selectedPaperIds.value = new Set([paper.id])
  try { await requestAnswerDraft() } finally { selectedPaperIds.value = previous }
}
function paperCategory(paper: QuestionBankPaper) {
  const text = `${paper.exam_type || ''} ${paper.title || ''}`
  return /练习|训练|习题|作业/.test(text) ? '练习' : /校本|自编|自命题/.test(text) ? '校本' : '真卷'
}
// 搜索输入防抖：逐键全量筛选 + 分组重算在试卷多时明显卡顿。
const debouncedKeyword = ref('')
const flushDebouncedKeyword = useDebounceFn((value: string) => {
  debouncedKeyword.value = value
}, 300)

watch(keyword, (value) => {
  void flushDebouncedKeyword(value)
})
const year = ref('')
const examType = ref('')
const sourceType = ref('')
const progressStatus = ref('')
const editingPaperId = ref<number | null>(null)
let editorReturnTarget: HTMLElement | null = null
function returnEditorFocus(event: Event) { event.preventDefault(); editorReturnTarget?.focus({ preventScroll: true }) }
const pendingDeletePapers = ref<QuestionBankPaper[]>([])
const selectedPaperIds = ref(new Set<number>())
const openMenuPaperId = ref<number | null>(null)
const permanentDeleteImpact = ref<QuestionBankPaperPermanentDeleteImpact | null>(null)
const permanentDeleteState = ref<'idle' | 'loading' | 'working' | 'error'>('idle')
const permanentDeleteMessage = ref('')
const permanentDeleteRequestToken = ref('')
const formError = ref('')
const retagBusyPaperId = ref<number | null>(null)
const retagAllBusy = ref(false)
defineExpose({ editPaper, retagPaper, continuePaper, answerPaper })
const taggingMode = ref<'fill' | 'retag' | null>(null)
const retagMessage = ref('')
const answerDraftBusy = ref(false)
const pendingAnswerDraft = ref<{ paperCount: number; questionIds: number[] } | null>(null)
const deleteNotice = ref('')
const refreshedTerminalJobs = new Set<string>()
const collapsedFolderKeys = ref(new Set<string>())
// 这三个表持久化在 store 里：打开试卷再返回时组件重建，
// 旧的任务状态和缺题提示可以立即显示，再由后台请求校对刷新。
const {
  paperQuestionRefs: questionRefs,
  paperJobLinks: jobPaperLinks,
  paperIncompleteNumbers: incompleteQuestionNumbers,
} = storeToRefs(store)
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
  && pendingDeletePapers.value.length > 0
  && permanentDeleteState.value !== 'working'
))

const filteredPapers = computed(() => {
  const search = debouncedKeyword.value.trim().toLocaleLowerCase()
  return [...store.papers]
    .filter((paper) => {
      if (category.value && paperCategory(paper) !== category.value) return false
      if (onlyIssues.value && !paper.criteria_needs_review_count && !paper.skill_unlinked_question_count && paper.complete_analysis_count >= paper.question_count) return false
      if (year.value && paper.year !== year.value) return false
      if (examType.value && paper.exam_type !== examType.value) return false
      if (sourceType.value && paper.source_type !== sourceType.value) return false
      if (
        progressStatus.value === 'complete' &&
        (
          paper.complete_analysis_count < paper.question_count
          || paper.criteria_needs_review_count > 0
        )
      ) return false
      if (
        progressStatus.value === 'pending' &&
        paper.complete_analysis_count >= paper.question_count
        && paper.criteria_needs_review_count === 0
      ) return false
      if (
        progressStatus.value === 'review' &&
        paper.criteria_needs_review_count === 0
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
  kindLabel: string
  papers: QuestionBankPaper[]
}

const paperFolders = computed<PaperFolder[]>(() => {
  const selectedVolumeId = curriculumScope.selectedVolumeId
  const groups = new Map<string, PaperFolder>()
  const otherPapers: QuestionBankPaper[] = []
  const unclassifiedPapers: QuestionBankPaper[] = []
  for (const paper of filteredPapers.value) {
    if (selectedVolumeId && paper.curriculum_volume_id !== selectedVolumeId) {
      if (paper.curriculum_volume_id) otherPapers.push(paper)
      else unclassifiedPapers.push(paper)
      continue
    }
    const manualName = paper.folder_name?.trim() ?? ''
    const automaticName = [paper.grade, paper.semester].filter(Boolean).join(' · ')
      || '未归类'
    const label = manualName || automaticName
    const key = `${manualName ? 'manual' : 'semester'}:${label}`
    const group = groups.get(key) ?? {
      key,
      label,
      manual: Boolean(manualName),
      kindLabel: manualName ? '自定义文件夹' : '按年级学期自动归类',
      papers: [],
    }
    group.papers.push(paper)
    groups.set(key, group)
  }
  // 组内按年份最新在前，同年按更新时间最新在前；自动分组之间同样按最新年份排序。
  for (const group of groups.values()) {
    group.papers.sort(comparePapersByYearDesc)
  }
  const manualGroups = [...groups.values()].filter((group) => group.manual)
  const automaticGroups = [...groups.values()].filter((group) => !group.manual)
  automaticGroups.sort((left, right) => {
    const leftYear = newestYearValue(left)
    const rightYear = newestYearValue(right)
    if (leftYear !== rightYear) return rightYear - leftYear
    return left.label.localeCompare(right.label, 'zh-CN')
  })
  const result = [...manualGroups, ...automaticGroups]
  if (selectedVolumeId && otherPapers.length) {
    result.push({
      key: 'scope:other-semesters',
      label: '其他学期',
      manual: false,
      kindLabel: '不属于当前教学学期，仍可操作',
      papers: otherPapers,
    })
  }
  if (selectedVolumeId && unclassifiedPapers.length) {
    result.push({
      key: 'scope:unclassified',
      label: '未归类',
      manual: false,
      kindLabel: '尚未匹配教学学期，仍可操作',
      papers: unclassifiedPapers,
    })
  }
  return result
})

const paperPageSize = 24
const paperPage = ref(1)
const libraryElement = ref<HTMLElement | null>(null)
async function changePaperPage(step: number): Promise<void> {
  paperPage.value = Math.max(1, Math.min(paperPageCount.value, paperPage.value + step))
  await nextTick()
  libraryElement.value?.scrollIntoView?.({ block: 'start' })
}
const paperPageCount = computed(() => Math.max(1, Math.ceil(filteredPapers.value.length / paperPageSize)))
const pageFolders = computed(() => {
  let offset = (paperPage.value - 1) * paperPageSize
  let remaining = paperPageSize
  return paperFolders.value.flatMap((folder) => {
    if (offset >= folder.papers.length) {
      offset -= folder.papers.length
      return []
    }
    const visiblePapers = folder.papers.slice(offset, offset + remaining)
    remaining -= visiblePapers.length
    offset = 0
    return visiblePapers.length ? [{ ...folder, visiblePapers }] : []
  })
})
watch([debouncedKeyword, year, examType, sourceType, progressStatus, category, onlyIssues, () => curriculumScope.selectedVolumeId], () => {
  paperPage.value = 1
})
watch(paperPageCount, (count) => { paperPage.value = Math.min(paperPage.value, count) })

watch(() => curriculumScope.selectedVolumeId, () => {
  const next = new Set(collapsedFolderKeys.value)
  next.delete('scope:other-semesters')
  next.delete('scope:unclassified')
  collapsedFolderKeys.value = next
}, { immediate: true })

function toggleFolder(key: string): void {
  const next = new Set(collapsedFolderKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedFolderKeys.value = next
}

const selectedCount = computed(() => selectedPaperIds.value.size)
const selectedPapers = computed(() => store.papers.filter(
  (paper) => selectedPaperIds.value.has(paper.id),
))
const filteredPaperIds = computed(() => filteredPapers.value.map((paper) => paper.id))
const allFilteredSelected = computed(() => (
  filteredPaperIds.value.length > 0
  && filteredPaperIds.value.every((id) => selectedPaperIds.value.has(id))
))
const someFilteredSelected = computed(() => (
  filteredPaperIds.value.some((id) => selectedPaperIds.value.has(id))
))
const batchActionsDisabled = computed(() => (
  selectedCount.value === 0
  || retagAllBusy.value
  || retagBusyPaperId.value !== null
  || answerDraftBusy.value
))

// Papers disappear from the store after deletion or external refresh; prune the
// selection so the batch bar never acts on stale ids. Filter/search changes keep
// the selection on purpose: teachers often filter one batch, select it, then
// filter the next.
watch(() => store.papers, (papers) => {
  const existing = new Set(papers.map((paper) => paper.id))
  const next = new Set([...selectedPaperIds.value].filter((id) => existing.has(id)))
  if (next.size !== selectedPaperIds.value.size) selectedPaperIds.value = next
})

function isPaperSelected(paperId: number): boolean {
  return selectedPaperIds.value.has(paperId)
}

function onPaperSelectChange(paperId: number, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedPaperIds.value)
  if (checked) next.add(paperId)
  else next.delete(paperId)
  selectedPaperIds.value = next
}

function onSelectAllChange(event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedPaperIds.value)
  for (const id of filteredPaperIds.value) {
    if (checked) next.add(id)
    else next.delete(id)
  }
  selectedPaperIds.value = next
}

function folderSelectedCount(folder: PaperFolder): number {
  return folder.papers.filter((paper) => selectedPaperIds.value.has(paper.id)).length
}

function onFolderSelectChange(folder: PaperFolder, event: Event): void {
  const checked = (event.target as HTMLInputElement).checked
  const next = new Set(selectedPaperIds.value)
  for (const paper of folder.papers) {
    if (checked) next.add(paper.id)
    else next.delete(paper.id)
  }
  selectedPaperIds.value = next
}

function clearPaperSelection(): void {
  selectedPaperIds.value = new Set()
}

function togglePaperMenu(paperId: number): void {
  openMenuPaperId.value = openMenuPaperId.value === paperId ? null : paperId
}

function closePaperMenu(): void {
  openMenuPaperId.value = null
}

onMounted(() => document.addEventListener('click', closePaperMenu))
onBeforeUnmount(() => {
  document.removeEventListener('click', closePaperMenu)
  if (papersRefreshTimer !== null) clearTimeout(papersRefreshTimer)
})

const totalQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.question_count,
  0,
))
const completeQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.complete_analysis_count,
  0,
))
const reviewQuestions = computed(() => store.papers.reduce(
  (total, paper) => total + paper.criteria_needs_review_count,
  0,
))
const libraryJobs = computed(() => Object.values(jobStore.jobs)
  .filter(isQuestionBankLibraryJob)
  .sort((left, right) => right.id - left.id))

watch(
  () => Object.values(jobStore.jobs)
    .filter(isQuestionBankLibraryJob)
    .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
    .sort()
    .join('|'),
  (signature) => {
    if (!signature) return
    const terminalJob = Object.values(jobStore.jobs).find((job) => (
      isQuestionBankLibraryJob(job)
      && TERMINAL_JOB_STATUSES.has(job.status)
      && !refreshedTerminalJobs.has(`${job.id}:${job.status}:${job.updated_at}`)
    ))
    if (!terminalJob) return
    refreshedTerminalJobs.add(`${terminalJob.id}:${terminalJob.status}:${terminalJob.updated_at}`)
    schedulePapersRefresh()
  },
)

// 批量打标会连续终结多个任务，合并成一次静默刷新，避免列表反复重拉。
let papersRefreshTimer: ReturnType<typeof setTimeout> | null = null

function schedulePapersRefresh(): void {
  if (papersRefreshTimer !== null) clearTimeout(papersRefreshTimer)
  papersRefreshTimer = setTimeout(() => {
    papersRefreshTimer = null
    void store.loadPapers()
  }, 400)
}

// 批量打标/导入运行期间，试卷计数与任务状态高频变化；两张题号表只依赖
// 轻量的 id/题号引用接口，且 800ms 防抖合并，避免反复全量拉取。
let refRefreshTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => [
    libraryJobs.value.map((job) => `${job.id}:${job.status}`).join('|'),
    store.papers.map((paper) => `${paper.id}:${paper.complete_analysis_count}:${paper.criteria_needs_review_count}:${paper.question_count}`).join('|'),
    [...jobPaperLinks.value.entries()].map(([jobId, paperId]) => `${jobId}:${paperId}`).join('|'),
  ].join('/'),
  () => {
    if (refRefreshTimer !== null) clearTimeout(refRefreshTimer)
    refRefreshTimer = setTimeout(() => {
      refRefreshTimer = null
      void refreshQuestionRefs()
      void refreshIncompleteNumbers()
    }, 800)
  },
  { immediate: true },
)

onScopeDispose(() => {
  if (refRefreshTimer !== null) clearTimeout(refRefreshTimer)
})

function rememberJobPaper(jobId: number, paperId: number): void {
  const next = new Map(jobPaperLinks.value)
  next.set(jobId, paperId)
  jobPaperLinks.value = next
}

async function refreshQuestionRefs(): Promise<void> {
  if (libraryJobs.value.length === 0) return
  const linkedPaperIds = [...jobPaperLinks.value.values()]
  const paperIds = [...new Set(
    store.papers
      .filter((paper) => (
        paper.question_count > 0
        && (
          paper.complete_analysis_count < paper.question_count
          || paper.criteria_needs_review_count > 0
          || linkedPaperIds.includes(paper.id)
        )
      ))
      .map((paper) => paper.id),
  )]
  if (paperIds.length === 0) return
  const next = new Map<number, PaperQuestionRef>()
  let page = 1
  try {
    while (true) {
      const result = await questionBankApi.listQuestionRefs({
        page,
        pageSize: 500,
        paperIds,
      })
      for (const item of result.items) {
        next.set(item.id, {
          id: item.id,
          paperId: item.paper_id,
          number: item.question_number,
        })
      }
      if (page >= result.total_pages) break
      page += 1
    }
    questionRefs.value = next
  } catch {
    // Keep the last successful mapping; live analysis matching can still use paper links.
  }
}

// 与卡片进度同一口径：直接按试卷字段拉取分析未完成的题号，
// 不依赖 localStorage 里追踪过的任务，刷新后提示保持稳定。
async function refreshIncompleteNumbers(): Promise<void> {
  const paperIds = store.papers
    .filter((paper) => (
      paper.question_count > 0
      && paper.complete_analysis_count < paper.question_count
    ))
    .map((paper) => paper.id)
  if (paperIds.length === 0) {
    incompleteQuestionNumbers.value = new Map()
    return
  }
  const next = new Map<number, string[]>()
  let page = 1
  try {
    while (true) {
      const result = await questionBankApi.listQuestionRefs({
        page,
        pageSize: 500,
        paperIds,
        analysisStatus: 'incomplete',
      })
      for (const item of result.items) {
        const numbers = next.get(item.paper_id) ?? []
        numbers.push(item.question_number)
        next.set(item.paper_id, numbers)
      }
      if (page >= result.total_pages) break
      page += 1
    }
    incompleteQuestionNumbers.value = next
  } catch {
    // Keep the last successful numbers; the progress counts still show.
  }
}

function analysisJobForPaper(paper: QuestionBankPaper): JobResponse | undefined {
  const matching = libraryJobs.value.filter((job) => jobBelongsToPaper(
    job,
    paper.id,
    questionRefs.value,
    jobPaperLinks.value.get(job.id),
  ))
  return matching.find((job) => !TERMINAL_JOB_STATUSES.has(job.status))
    ?? matching.find((job) => TERMINAL_JOB_STATUSES.has(job.status))
}

interface PaperStatusLine {
  live: string
  leftovers: string[]
}

// 一次重算替代"每卡片每次渲染 3 次调用"，且只在任务/试卷/映射真正变化时重算。
const paperStatusLines = computed(() => {
  const map = new Map<number, PaperStatusLine>()
  for (const paper of store.papers) {
    map.set(paper.id, {
      live: liveAnalysisLine(paper),
      leftovers: leftoverLines(paper),
    })
  }
  return map
})

function liveAnalysisLine(paper: QuestionBankPaper): string {
  const current = analysisJobForPaper(paper)
  if (!current || TERMINAL_JOB_STATUSES.has(current.status)) return ''
  return paperLiveAnalysisLine(current)
}

function leftoverLines(paper: QuestionBankPaper): string[] {
  return paperCurrentStatusLines(
    paper,
    incompleteQuestionNumbers.value.get(paper.id) ?? [],
  )
}

function uniqueValues(values: Array<string | null>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value?.trim())))]
    .sort((left, right) => right.localeCompare(left, 'zh-CN'))
}

function paperYearValue(paper: QuestionBankPaper): number {
  if (!paper.year?.trim()) return Number.NEGATIVE_INFINITY
  const value = Number(paper.year)
  return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY
}

function comparePapersByYearDesc(left: QuestionBankPaper, right: QuestionBankPaper): number {
  const leftYear = paperYearValue(left)
  const rightYear = paperYearValue(right)
  if (leftYear !== rightYear) return rightYear - leftYear
  return right.updated_at.localeCompare(left.updated_at)
}

function newestYearValue(folder: PaperFolder): number {
  return folder.papers.reduce(
    (newest, paper) => Math.max(newest, paperYearValue(paper)),
    Number.NEGATIVE_INFINITY,
  )
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

const paperDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '日期未知'
  return paperDateFormatter.format(date)
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
    const result = await questionBankApi.listQuestionRefs({
      page,
      pageSize: 500,
      paperIds,
      analysisStatus,
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
  options: { forceRetag: boolean; scope: string; volumeId: string; paperId?: number },
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
    if (options.paperId) rememberJobPaper(job.id, options.paperId)
    window.localStorage.removeItem(storageKey)
    jobs += 1
  }
  return jobs
}

interface PaperTaggingPlan {
  paper: QuestionBankPaper
  ids: number[]
}

async function submitTaggingPlans(
  plans: PaperTaggingPlan[],
  forceRetag: boolean,
): Promise<number> {
  let jobs = 0
  for (const { paper, ids } of plans) {
    if (!paper.curriculum_volume_id) continue
    jobs += await submitTaggingBatches(ids, {
      forceRetag,
      scope: `paper-${paper.id}-${forceRetag ? 'retag' : 'fill'}`,
      volumeId: paper.curriculum_volume_id,
      paperId: paper.id,
    })
  }
  return jobs
}

// Batch entry points confirm once for the whole selection, then reuse the exact
// per-paper submission path (same scopes, same localStorage idempotency tokens).
async function collectTaggingPlans(
  papers: QuestionBankPaper[],
): Promise<{ plans: PaperTaggingPlan[]; skippedWithoutVolume: number }> {
  const plans: PaperTaggingPlan[] = []
  let skippedWithoutVolume = 0
  for (const paper of papers) {
    if (!paper.curriculum_volume_id) {
      skippedWithoutVolume += 1
      continue
    }
    const ids = await loadQuestionIds([paper.id])
    if (ids.length > 0) plans.push({ paper, ids })
  }
  return { plans, skippedWithoutVolume }
}

function skippedVolumeNote(skippedWithoutVolume: number): string {
  return skippedWithoutVolume > 0
    ? `另有 ${skippedWithoutVolume} 份试卷缺少年级、学期或教材版本，已跳过。`
    : ''
}

async function fillSelectedPapers(): Promise<void> {
  const papers = selectedPapers.value
  if (papers.length === 0 || retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagAllBusy.value = true
  taggingMode.value = 'fill'
  try {
    const { plans, skippedWithoutVolume } = await collectTaggingPlans(papers)
    const total = plans.reduce((sum, plan) => sum + plan.ids.length, 0)
    if (total === 0) {
      retagMessage.value = skippedWithoutVolume > 0
        ? '选中的试卷还没有可补齐的题目；部分试卷请先在“编辑资料”中补全年级、学期和教材版本。'
        : '选中的试卷没有可补齐的题目。'
      return
    }
    // The list endpoint only has a coarse saved-row view. Submit whole papers so
    // the job can compare evidence/criteria with the current source hash and
    // still skip every projection that is genuinely complete. The dialog still
    // leads with the visible not-yet-complete count so teachers know what to expect.
    const incomplete = plans.reduce(
      (sum, plan) => sum + Math.max(
        0,
        plan.paper.question_count - plan.paper.complete_analysis_count,
      ),
      0,
    )
    if (!await confirm({
      title: '补齐所选试卷的标签？',
      message: `选中的 ${plans.length} 份试卷还有 ${incomplete} 道题未打全标签。将把共 ${total} 道题提交后端逐题核对：只补齐缺失、失败或已过期的标签和判定点，真正完整的题和人工修改不会重做。需要补齐时可能产生模型费用。${skippedVolumeNote(skippedWithoutVolume)}`,
      confirmLabel: '继续',
    })) return
    const count = await submitTaggingPlans(plans, false)
    retagMessage.value = `已提交 ${plans.length} 份试卷等待后端核对（其中 ${incomplete} 道题未打全标签），共 ${count} 个任务；只会补齐缺失、失败或已过期的内容。${skippedVolumeNote(skippedWithoutVolume)}`
  } catch {
    retagMessage.value = '补齐标签任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagAllBusy.value = false
    taggingMode.value = null
  }
}

async function retagSelectedPapers(): Promise<void> {
  const papers = selectedPapers.value
  if (papers.length === 0 || retagBusyPaperId.value !== null || retagAllBusy.value) return
  retagMessage.value = ''
  retagAllBusy.value = true
  taggingMode.value = 'retag'
  try {
    const { plans, skippedWithoutVolume } = await collectTaggingPlans(papers)
    const total = plans.reduce((sum, plan) => sum + plan.ids.length, 0)
    if (total === 0) {
      retagMessage.value = skippedWithoutVolume > 0
        ? '选中的试卷还没有可重新标注的题目；部分试卷请先在“编辑资料”中补全年级、学期和教材版本。'
        : '选中的试卷没有可重新标注的题目。'
      return
    }
    if (!await confirm({
      title: '重新标注所选试卷？',
      message: `将重新分析选中的 ${plans.length} 份试卷共 ${total} 道题，可能产生模型费用；人工修改的标签会保留。${skippedVolumeNote(skippedWithoutVolume)}`,
      confirmLabel: '继续',
    })) return
    const count = await submitTaggingPlans(plans, true)
    retagMessage.value = `已提交 ${total} 道题，共 ${count} 个重新标注任务。${skippedVolumeNote(skippedWithoutVolume)}`
  } catch {
    retagMessage.value = '重新标注任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagAllBusy.value = false
    taggingMode.value = null
  }
}

// AI 补答案与彻底删除一样用弹层确认：先读出选中试卷的题数，
// 教师确认会产生模型调用费用、结果标记为待复核后才提交任务。
async function requestAnswerDraft(): Promise<void> {
  const papers = selectedPapers.value
  if (
    papers.length === 0
    || retagBusyPaperId.value !== null
    || retagAllBusy.value
    || answerDraftBusy.value
  ) return
  retagMessage.value = ''
  answerDraftBusy.value = true
  try {
    const questionIds = await loadQuestionIds(papers.map((paper) => paper.id))
    if (questionIds.length === 0) {
      retagMessage.value = '选中的试卷没有题目。'
      return
    }
    pendingAnswerDraft.value = {
      paperCount: papers.length,
      questionIds,
    }
  } catch {
    retagMessage.value = '题目列表读取失败，请刷新试卷库后重试。'
  } finally {
    answerDraftBusy.value = false
  }
}

function cancelAnswerDraft(): void {
  if (answerDraftBusy.value) return
  pendingAnswerDraft.value = null
}

async function confirmAnswerDraft(): Promise<void> {
  const pending = pendingAnswerDraft.value
  if (!pending || answerDraftBusy.value) return
  answerDraftBusy.value = true
  retagMessage.value = ''
  try {
    let count = 0
    for (let index = 0; index < pending.questionIds.length; index += 500) {
      const batch = pending.questionIds.slice(index, index + 500)
      if (batch.length === 0) continue
      const job = await questionBankApi.submitAnswerDraft(batch)
      jobStore.track(job)
      count += 1
    }
    pendingAnswerDraft.value = null
    retagMessage.value = `已提交 ${pending.questionIds.length} 道题的 AI 补答案任务（共 ${count} 个任务）；只处理还没有答案的题，生成结果会标记为待复核。`
  } catch {
    retagMessage.value = 'AI 补答案任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    answerDraftBusy.value = false
  }
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
    if (!await confirm({
      title: '重新标注这份试卷？',
      message: `将重新分析“${paper.title || `试卷 #${paper.id}`}”的 ${ids.length} 道题，可能产生模型费用；人工修改的标签会保留。`,
      confirmLabel: '继续',
    })) return
    const count = await submitTaggingPlans([{ paper, ids }], true)
    retagMessage.value = `已提交 ${ids.length} 道题，共 ${count} 个重新标注任务。`
  } catch {
    retagMessage.value = '重新标注任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagBusyPaperId.value = null
    taggingMode.value = null
  }
}

function editPaper(paper: QuestionBankPaper): void {
  const active = document.activeElement as HTMLElement | null
  editorReturnTarget = active?.closest('.paper-card')?.querySelector<HTMLElement>('.paper-card__more-toggle') || active?.closest('details')?.querySelector<HTMLElement>('summary') || active
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

// The preview and delete endpoints both accept a selections array, so a batch
// delete is one preview + one confirmed request for the whole selection.
async function requestPermanentDelete(
  target: QuestionBankPaper | QuestionBankPaper[],
): Promise<void> {
  const papers = (Array.isArray(target) ? target : [target])
    .filter((paper, index, all) => all.findIndex((item) => item.id === paper.id) === index)
  if (papers.length === 0) return
  pendingDeletePapers.value = papers
  permanentDeleteState.value = 'loading'
  permanentDeleteMessage.value = ''
  deleteNotice.value = ''
  permanentDeleteRequestToken.value = ''
  try {
    permanentDeleteImpact.value = await questionBankApi.previewPaperPermanentDelete(
      papers.map((paper) => ({
        id: paper.id,
        expected_updated_at: paper.updated_at,
      })),
    )
    permanentDeleteState.value = 'idle'
  } catch (error) {
    permanentDeleteState.value = 'error'
    deleteNotice.value = error instanceof ApiError
      ? `${error.message}（请求编号：${error.requestId}）`
      : '删除影响读取失败，没有删除任何内容。请刷新试卷库后重试。'
  }
}

function cancelPermanentDelete(): void {
  if (permanentDeleteState.value === 'working') return
  pendingDeletePapers.value = []
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
  const papers = pendingDeletePapers.value
  if (!impact || papers.length === 0 || !canConfirmPermanentDelete.value) return
  permanentDeleteState.value = 'working'
  permanentDeleteMessage.value = ''
  if (!permanentDeleteRequestToken.value) {
    permanentDeleteRequestToken.value = requestToken()
  }
  try {
    const result = await questionBankApi.permanentlyDeletePapers(
      papers.map((paper) => ({
        id: paper.id,
        expected_updated_at: paper.updated_at,
      })),
      impact.permanent_delete_phrase,
      permanentDeleteRequestToken.value,
    )
    await store.loadPapers()
    pendingDeletePapers.value = []
    permanentDeleteImpact.value = null
    permanentDeleteRequestToken.value = ''
    permanentDeleteState.value = 'idle'
    deleteNotice.value = `已删除 ${result.deleted_paper_ids.length} 份试卷、${result.deleted_question_count} 道题、${result.deleted_tag_count} 个标签和 ${result.deleted_analysis_record_count} 条分析记录。`
  } catch (error) {
    await store.loadPapers()
    const refreshConfirmedDeletion = (
      store.papersState === 'ready'
      && papers.every((paper) => !store.papers.some(item => item.id === paper.id))
    )
    if (refreshConfirmedDeletion) {
      pendingDeletePapers.value = []
      permanentDeleteImpact.value = null
      permanentDeleteRequestToken.value = ''
      permanentDeleteState.value = 'idle'
      deleteNotice.value = '服务器响应中断，但刷新后已确认试卷删除成功。'
      return
    }
    permanentDeleteState.value = 'error'
    if (!isAmbiguousWriteError(error)) {
      permanentDeleteMessage.value = error instanceof ApiError
        ? `${error.message}（请求编号：${error.requestId}）`
        : '删除未完成，服务器没有接受本次请求；本次没有删除任何内容。请刷新后重试。'
      return
    }
    permanentDeleteMessage.value = store.papersState === 'ready'
      ? '删除结果尚不确定：未收到服务器确认，刷新核对后试卷仍在。请点击重试，系统会使用同一请求编号，不会重复删除。'
      : '删除结果尚不确定：未收到服务器确认，刷新核对也暂时失败。请保留此窗口并点击重试；系统会使用同一请求编号，不会重复删除。'
  }
}
</script>

<template>
  <section ref="libraryElement" class="paper-library" aria-labelledby="paper-library-title">
    <header class="paper-library__header" :class="{ 'is-relocated': headerTarget }">
      <div v-if="!headerTarget">
        <h2 id="paper-library-title">试卷库</h2>
        
      </div>
      <div class="paper-library__stats" aria-label="试卷库概况">
        <span><strong>{{ store.papers.length }}</strong> 份试卷</span>
        <span><strong>{{ totalQuestions }}</strong> 道题</span>
        <span><strong>{{ completeQuestions }}</strong> 道联合分析完整</span>
        <AppButton variant="secondary"
          v-if="reviewQuestions > 0"
          type="button"
          class="paper-button is-review"
          @click="emit('reviewCriteria')"
        >
          判定点需核对
          <strong>{{ reviewQuestions }}</strong>
        </AppButton>
        <Teleport :to="headerTarget || 'body'" :disabled="!headerTarget" defer>
        <AppButton variant="primary" @click="emit('import')">
          上传试卷
        </AppButton>
        </Teleport>
      </div>
    </header>

    <FeedbackBanner v-if="retagMessage" role="status" tone="info" :description="retagMessage" />
    <FeedbackBanner v-if="deleteNotice" role="status" tone="info" :description="deleteNotice" />

    <div class="paper-library__tools">
      <label class="paper-search"><span class="sr-only">搜索试卷</span><input class="app-input" v-model="keyword" type="search" placeholder="搜索试卷名称、地区或教材"></label>
      <div class="paper-category-chips"><button v-for="value in ['', '真卷', '校本', '练习']" :key="value" class="qb-filter-chip" :class="{ 'is-active': category === value }" :aria-pressed="category === value" @click="category = value">{{ value || '全部' }}</button></div>
      <div class="paper-library__tool-line"><label><input v-model="onlyIssues" type="checkbox">只看有问题</label><details class="paper-library__extra-filters"><summary>更多筛选</summary><div class="paper-library__filters">
      <label>
        <span>年份</span>
        <select class="app-input" v-model="year">
          <option value="">全部年份</option>
          <option v-for="item in years" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>试卷类型</span>
        <select class="app-input" v-model="examType">
          <option value="">全部类型</option>
          <option v-for="item in examTypes" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>文件</span>
        <select class="app-input" v-model="sourceType">
          <option value="">全部文件</option>
          <option value="docx">Word</option>
          <option value="pdf">PDF</option>
          <option value="other">其他</option>
        </select>
      </label>
      <label>
        <span>标注</span>
        <select class="app-input" v-model="progressStatus">
          <option value="">全部进度</option>
          <option value="complete">已完成</option>
          <option value="pending">待完善</option>
          <option value="review">判定点待审核</option>
        </select>
      </label>
      <AppButton variant="ghost" @click="resetFilters">清除</AppButton>
    </div></details></div></div>

    <div class="paper-batch-bar" :class="{ 'is-active': selectedCount > 0 }">
      <label class="paper-batch-bar__select-all">
        <input
          type="checkbox"
          :checked="allFilteredSelected"
          :indeterminate="someFilteredSelected && !allFilteredSelected"
          :disabled="filteredPaperIds.length === 0"
          @change="onSelectAllChange"
        >
        <span title="包含其他页中符合当前筛选条件的试卷">全选筛选结果</span>
      </label>
      <span class="paper-batch-bar__info">
        {{ selectedCount > 0 ? `已选 ${selectedCount} 份试卷` : '未选中试卷' }}
      </span>
      <span class="paper-batch-bar__spacer" />
      <template v-if="selectedCount"><AppButton
        variant="primary"
        :disabled="batchActionsDisabled"
        @click="fillSelectedPapers"
      >{{ retagAllBusy && taggingMode === 'fill' ? '正在检查未完成题…' : '继续完成未完成题目' }}</AppButton>
      <AppButton
        variant="secondary"
        :disabled="batchActionsDisabled"
        @click="retagSelectedPapers"
      >{{ retagAllBusy && taggingMode === 'retag' ? '正在准备…' : '重新打标签' }}</AppButton>
      <AppButton
        variant="secondary"
        :disabled="batchActionsDisabled"
        @click="requestAnswerDraft"
      >{{ answerDraftBusy ? '正在处理…' : 'AI 补答案' }}</AppButton>
      <AppButton
        variant="danger"
        :disabled="batchActionsDisabled || permanentDeleteState === 'loading' || permanentDeleteState === 'working'"
        @click="requestPermanentDelete(selectedPapers)"
      >删除</AppButton>
      <AppButton
        variant="secondary"
        :disabled="selectedCount === 0"
        @click="clearPaperSelection"
      >取消选择</AppButton></template>
    </div>

    <StatePanel v-if="store.papersState === 'loading'" kind="loading" title="正在读取试卷库…" />
    <StatePanel
      v-else-if="store.papersState === 'error'"
      kind="error"
      title="试卷库暂时无法读取。"
     
      retry-label="重新加载"
      @retry="store.loadPapers()"
    />
    <StatePanel
      v-else-if="filteredPapers.length === 0"
      kind="empty"
      :title="store.papers.length ? '当前筛选下没有试卷' : '还没有导入试卷'"
      :description="store.papers.length ? '可以清除筛选后再查看。' : undefined"
    />

    <div v-else class="paper-folders">
      <section v-for="folder in pageFolders" :key="folder.key" class="paper-folder">
        <div class="paper-folder__head">
          <label class="paper-folder__check" @click.stop>
            <input
              type="checkbox"
              :checked="folder.papers.length > 0 && folderSelectedCount(folder) === folder.papers.length"
              :indeterminate="folderSelectedCount(folder) > 0 && folderSelectedCount(folder) < folder.papers.length"
              @change="onFolderSelectChange(folder, $event)"
            >
            <span class="sr-only">全选「{{ folder.label }}」的试卷</span>
          </label>
          <button
            type="button"
            class="paper-folder__header"
            :aria-expanded="!collapsedFolderKeys.has(folder.key) || Boolean(keyword.trim())"
            @click="toggleFolder(folder.key)"
          >
            <svg
              class="paper-folder__chevron"
              :class="{ 'is-collapsed': collapsedFolderKeys.has(folder.key) && !keyword.trim() }"
              viewBox="0 0 16 16"
              aria-hidden="true"
            ><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
            <strong>{{ folder.label }}</strong>
            <span class="paper-folder__kind">{{ folder.kindLabel }}</span>
            <span class="paper-folder__count">{{ folder.papers.length }} 份</span>
          </button>
        </div>
        <div v-if="!collapsedFolderKeys.has(folder.key) || keyword.trim()" class="paper-library__grid">
          <article
            v-for="paper in folder.visiblePapers"
            :key="paper.id"
            class="paper-card"
            :class="{ 'is-selected': isPaperSelected(paper.id), 'is-current': activePaperId === paper.id }"
          >
        <div class="paper-card__body">
          <div class="paper-card__row">
            <label class="paper-card__check" @click.stop><input type="checkbox" :checked="isPaperSelected(paper.id)" :aria-label="`选择${paper.title || `试卷 ${paper.id}`}`" @change="onPaperSelectChange(paper.id, $event)"></label>
            <div class="paper-card__text"><h2><button type="button" class="paper-card__title" :title="paper.title || `未命名试卷 #${paper.id}`" @click="emit('open', paper)">{{ paper.title || `未命名试卷 #${paper.id}` }}</button></h2>
              <div class="paper-card__compact-meta"><span>{{ paper.question_count }} 题</span><span>{{ sourceLabel(paper.source_type) }}</span><span>{{ formatDate(paper.updated_at) }}</span><span class="paper-card__compact-progress" role="progressbar" aria-label="联合分析完整度" :aria-valuenow="progressFor(paper)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${progressFor(paper)}%` }" /></span></div>
            </div>
            <div class="paper-card__statuses"><StatusBadge v-if="paper.criteria_needs_review_count" tone="warning" :label="`${paper.criteria_needs_review_count} 待审`" /><StatusBadge v-if="paper.skill_unlinked_question_count" tone="warning" :label="`${paper.skill_unlinked_question_count} 未挂`" /><span v-if="paper.question_count > 0 && paper.complete_analysis_count === paper.question_count && !paper.criteria_needs_review_count && !paper.skill_unlinked_question_count" class="paper-card__complete">✓ 完整</span><StatusBadge v-else-if="!paper.criteria_needs_review_count && !paper.skill_unlinked_question_count" tone="neutral" :label="paper.question_count ? `${paper.question_count - paper.complete_analysis_count} 未完成` : '暂无题'" /></div>
          </div>
          <p v-if="paperStatusLines.get(paper.id)?.live" class="paper-card__live" role="status">{{ paperStatusLines.get(paper.id)?.live }}</p>
          <ul v-if="paperStatusLines.get(paper.id)?.leftovers.length" class="paper-card__leftovers"><li v-for="line in paperStatusLines.get(paper.id)?.leftovers" :key="line">{{ line }}</li></ul>
          <footer>
            <div class="paper-card__more">
              <button
                type="button"
                class="paper-card__more-toggle"
                :aria-expanded="openMenuPaperId === paper.id"
                :aria-label="`${paper.title || `试卷 ${paper.id}`} 的更多操作`"
                @click.stop="togglePaperMenu(paper.id)"
              >⋯</button>
              <div
                v-if="openMenuPaperId === paper.id"
                class="paper-card__more-menu"
                role="menu"
              >
                <AppButton
                  type="button"
                  role="menuitem"
                  variant="ghost" size="small" @click="closePaperMenu(); editPaper(paper)"
                >编辑资料</AppButton>
                <details class="paper-card__metadata"><summary>来源与分析资料</summary><p>{{ [paper.province, paper.city, paper.district, paper.grade, paper.semester, paper.textbook_version].filter(Boolean).join(' · ') || '来源信息待补充' }}</p><p>标签 {{ paper.tagged_question_count }}/{{ paper.question_count }} · 判定点 {{ paper.criteria_question_count }}/{{ paper.question_count }} · 完整 {{ paper.complete_analysis_count }}/{{ paper.question_count }}</p></details>

                <button role="menuitem" :disabled="batchActionsDisabled && retagAllBusy" @click="closePaperMenu(); continuePaper(paper)">继续分析</button>
                <button role="menuitem" :disabled="answerDraftBusy" @click="closePaperMenu(); answerPaper(paper)">生成答案草稿</button>
                <button
                  type="button"
                  role="menuitem"
                  :disabled="retagBusyPaperId !== null || retagAllBusy"
                  @click="closePaperMenu(); retagPaper(paper)"
                >{{
                  retagBusyPaperId === paper.id && taggingMode === 'retag'
                    ? '准备中…'
                    : '重新打标签'
                }}</button>
                <button
                  type="button"
                  role="menuitem"
                  class="is-danger"
                  :disabled="permanentDeleteState === 'loading' || permanentDeleteState === 'working'"
                  :aria-label="`永久删除${paper.title || `试卷 ${paper.id}`}`"
                  @click="closePaperMenu(); requestPermanentDelete(paper)"
                >删除</button>
              </div>
            </div>
          </footer>
        </div>
          </article>
        </div>
      </section>
    </div>


    <nav v-if="paperPageCount > 1" class="paper-library__pagination" aria-label="试卷分页">
      <AppButton variant="secondary" :disabled="paperPage === 1" @click="changePaperPage(-1)">上一页</AppButton>
      <span role="status">第 {{ paperPage }} / {{ paperPageCount }} 页 · 共 {{ filteredPapers.length }} 份试卷</span>
      <AppButton variant="secondary" :disabled="paperPage === paperPageCount" @click="changePaperPage(1)">下一页</AppButton>
    </nav>

    <Sheet :open="editingPaperId !== null" @update:open="(value: boolean) => { if (!value) closePaperEditor() }">
      <SheetContent
          class="paper-editor overflow-y-auto gap-0"
          :aria-describedby="undefined"
          @close-auto-focus="returnEditorFocus"
        >
          <SheetHeader class="paper-editor__header">
            <div>
              <p>试卷标签</p>
              <SheetTitle as="h2">编辑试卷资料</SheetTitle>
              <span>这里的内容会显示在试卷卡片，并用于题库筛选。</span>
            </div>
          </SheetHeader>

          <form class="paper-editor__form" @submit.prevent="savePaperMetadata">
            <label class="is-wide">
              <span>试卷名称 <strong aria-hidden="true">*</strong></span>
              <input class="app-input"
                v-model="paperDraft.title"
                name="paper-title"
                maxlength="255"
                autocomplete="off"
                :aria-invalid="Boolean(formError)"
              >
              
            </label>

            <label>
              <span>年份</span>
              <input class="app-input"
                v-model="paperDraft.year"
                name="paper-year"
                maxlength="24"
                inputmode="numeric"
                placeholder="如：2026"
              >
            </label>
            <label>
              <span>试卷类型</span>
              <input class="app-input"
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
              <input class="app-input"
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
              <input class="app-input"
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
              <input class="app-input"
                v-model="paperDraft.folder_name"
                name="paper-folder-name"
                maxlength="80"
                placeholder="如：中考专题卷"
              >
              <small>留空时，系统会按上面的年份和学期自动归类。</small>
            </label>

            <label class="is-wide">
              <span>教材版本</span>
              <input class="app-input"
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
                <input class="app-input" v-model="paperDraft.province" name="paper-province" maxlength="48">
              </label>
              <label>
                <span>城市</span>
                <input class="app-input" v-model="paperDraft.city" name="paper-city" maxlength="48">
              </label>
              <label>
                <span>区县</span>
                <input class="app-input" v-model="paperDraft.district" name="paper-district" maxlength="48">
              </label>
            </fieldset>

            <FeedbackBanner v-if="formError" role="alert" tone="error" :description="formError" />
            <FeedbackBanner v-else-if="store.paperWriteMessage" :class="{ 'is-error': store.paperWriteState === 'error' || store.paperWriteState === 'conflict' }" :role="store.paperWriteState === 'error' || store.paperWriteState === 'conflict' ? 'alert' : 'status'" tone="info" :description="store.paperWriteMessage" />

            <footer class="paper-editor__actions is-wide">
              <AppButton
                variant="secondary"
                :disabled="store.paperWriteState === 'saving'"
                @click="closePaperEditor"
              >
                取消
              </AppButton>
              <AppButton
                variant="primary"
                type="submit"
                :disabled="store.paperWriteState === 'saving'"
              >
                {{ store.paperWriteState === 'saving' ? '正在保存…' : '保存资料' }}
              </AppButton>
            </footer>
          </form>
      </SheetContent>
    </Sheet>
    <AppDialog :open="Boolean(permanentDeleteImpact)" title="确认彻底删除？" class="paper-trash-confirm" @update:open="(value: boolean) => { if (!value) cancelPermanentDelete() }">
        <template v-if="permanentDeleteImpact">
          <strong v-if="pendingDeletePapers.length <= 1">
            {{ pendingDeletePapers[0]?.title || `未命名试卷 #${pendingDeletePapers[0]?.id}` }}
          </strong>
          <template v-else>
            <strong>选中的 {{ pendingDeletePapers.length }} 份试卷</strong>
            <ul class="paper-trash-confirm__list">
              <li v-for="paper in pendingDeletePapers" :key="paper.id">
                {{ paper.title || `未命名试卷 #${paper.id}` }}
              </li>
            </ul>
          </template>
          <p class="paper-permanent-impact">
            <span><b>{{ permanentDeleteImpact.paper_count }}</b> 份试卷</span>
            <span><b>{{ permanentDeleteImpact.question_count }}</b> 道题</span>
            <span><b>{{ permanentDeleteImpact.tag_count }}</b> 个标签</span>
            <span><b>{{ permanentDeleteImpact.analysis_record_count }}</b> 条分析记录</span>
            <span><b>{{ permanentDeleteImpact.owned_file_count }}</b> 个本地文件</span>
            <span><b>{{ permanentDeleteImpact.training_link_count }}</b> 条训练关联</span>
            <span><b>{{ permanentDeleteImpact.knowledge_graph_link_count }}</b> 条知识图谱计数来源</span>
            <span v-if="permanentDeleteImpact.taxonomy_proposal_count">
              将清除 <b>{{ permanentDeleteImpact.taxonomy_proposal_count }}</b> 条待审新词
            </span>
          </p>
          <p>
            本地 Word/PDF、题目和标签会删除；训练材料快照保留，但不再计入这些题；
            已完成考试的答卷与成绩不受影响。
            <template v-if="permanentDeleteImpact.shared_file_count">
              另有 {{ permanentDeleteImpact.shared_file_count }} 个共享文件仍被其他试卷使用，将保留。
            </template>
          </p>
          <FeedbackBanner v-if="permanentDeleteMessage" role="alert" tone="error" :description="permanentDeleteMessage" />
          <footer>
            <AppButton
              variant="secondary"
              :disabled="permanentDeleteState === 'working'"
              @click="cancelPermanentDelete"
            >取消</AppButton>
            <AppButton
              variant="danger"
              :disabled="!canConfirmPermanentDelete"
              @click="confirmPermanentDelete"
            >{{ permanentDeleteState === 'working' ? '正在彻底删除…' : '确认彻底删除' }}</AppButton>
          </footer>
        </template>
    </AppDialog>
    <AppDialog :open="Boolean(pendingAnswerDraft)" title="确认生成答案草稿？" class="paper-trash-confirm" @update:open="(value: boolean) => { if (!value) cancelAnswerDraft() }">
        <template v-if="pendingAnswerDraft">
          <strong>
            选中的 {{ pendingAnswerDraft.paperCount }} 份试卷、共 {{ pendingAnswerDraft.questionIds.length }} 道题
          </strong>
          <p>
            仅对其中还没有答案的题调用 AI 生成答案和解析草稿，已有答案的题目不会改动。
            会产生模型调用费用；生成结果一律标记为待复核，请教师核对后再使用。
          </p>
          <footer>
            <AppButton
              variant="secondary"
              :disabled="answerDraftBusy"
              @click="cancelAnswerDraft"
            >取消</AppButton>
            <AppButton
              variant="primary"
              :disabled="answerDraftBusy"
              @click="confirmAnswerDraft"
            >{{ answerDraftBusy ? '正在提交…' : '确认生成' }}</AppButton>
          </footer>
        </template>
    </AppDialog>
  </section>
</template>

<style scoped>
.paper-library__pagination { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 12px; }
.paper-library__maintenance { position: relative; }
.paper-library__maintenance summary { cursor: pointer; }
.paper-library__maintenance-items { position: absolute; right: 0; top: 100%; z-index: 31; display: grid; gap: 8px; width: min(290px, 85vw); padding: 16px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 8px 24px #18283f20; }
.paper-library__standard { padding: 18px; border: 1px solid #d8dee8; border-radius: 12px; overflow-wrap: anywhere; }
.paper-library__standard-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.paper-library__standard h2 { font-size: var(--font-size-h3); }
.paper-library__standard p, .paper-library__standard li { line-height: 1.7; }
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

.paper-library__header h2 {
  color: var(--color-text-primary);
  font-size: var(--font-size-display);
  letter-spacing: -.04em;
  margin: 0;
}

.paper-library__header p {
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
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
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.paper-library__stats strong {
  color: var(--color-text-primary);
  font-size: var(--font-size-h2);
  margin-right: 2px;
}

.paper-library__filters {
  align-items: flex-end;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  display: grid;
  gap: 10px;
  grid-template-columns: minmax(240px, 1fr) repeat(4, minmax(118px, 150px)) auto;
  padding: 14px;
}

.paper-library__filters label {
  color: var(--color-text-secondary);
  display: grid;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  gap: 5px;
}

.paper-library__filters input,
.paper-library__filters select {
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-dense);
  height: var(--control-height-large);
  min-width: 0;
  padding: 0 10px;
}

.paper-library__filters input:focus,
.paper-library__filters select:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-accent) 10%, transparent);
  outline: none;
}

.paper-library__grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr));
}

.paper-folders {
  display: grid;
  gap: 18px;
}

.paper-folder {
  display: grid;
  gap: 10px;
}

.paper-folder__head {
  align-items: center;
  background: var(--secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  display: flex;
  min-height: 46px;
  padding-right: 13px;
}

.paper-folder__head:hover,
.paper-folder__head:focus-within {
  background: var(--color-accent-subtle);
  border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
}

.paper-folder__check {
  align-items: center;
  align-self: stretch;
  cursor: pointer;
  display: flex;
  padding: 0 5px 0 13px;
}

.paper-folder__header {
  align-items: center;
  background: none;
  border: none;
  border-radius: var(--radius-control);
  color: var(--color-text-primary);
  cursor: pointer;
  display: flex;
  flex: 1;
  font: inherit;
  gap: 9px;
  min-height: 44px;
  min-width: 0;
  padding: 9px 0;
  text-align: left;
}

.paper-folder__header:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.paper-folder__chevron {
  color: var(--color-accent);
  flex: none;
  height: 16px;
  transition: transform 150ms ease;
  width: 16px;
}

.paper-folder__chevron.is-collapsed {
  transform: rotate(-90deg);
}

.paper-folder__kind {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  padding: 3px 8px;
}

.paper-folder__count {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  margin-left: auto;
}

.paper-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  box-shadow: 0 1px 2px color-mix(in srgb, var(--color-text-primary) 5%, transparent);
  display: flex;
  min-width: 0;
  position: relative;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}

.paper-card:hover {
  border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
  box-shadow: 0 2px 8px color-mix(in srgb, var(--color-text-primary) 6%, transparent);
}

.paper-card.is-selected {
  background: color-mix(in srgb, var(--color-accent-subtle) 55%, var(--card));
  border-color: var(--color-accent);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--color-accent) 14%, transparent);
}

.paper-card__check {
  align-items: center;
  cursor: pointer;
  display: flex;
  flex: 0 0 auto;
  padding: 3px 2px;
}

.paper-batch-bar__select-all input,
.paper-folder__check input {
  accent-color: var(--color-accent);
  cursor: pointer;
  height: 18px;
  margin: 0;
  width: 18px;
}

.paper-card__check input {
  accent-color: var(--color-accent);
  cursor: pointer;
  height: 18px;
  margin: 0;
  width: 18px;
}

.paper-card__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 16px 17px 14px;
}

.paper-card__topline {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.paper-card__detail,
.paper-card__question-count {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.paper-card__question-count {
  margin-left: auto;
  white-space: nowrap;
}

.paper-card__question-count strong {
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.paper-chip {
  background: var(--background);
  border-radius: 999px;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  padding: 3px 8px;
}

.paper-chip.is-format {
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
  font-weight: var(--font-weight-semibold);
}

.paper-card h2 {
  color: var(--color-text-primary);
  display: -webkit-box;
  font-size: var(--font-size-h3);
  line-height: 1.45;
  margin: 12px 0 6px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.paper-card__title {
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  display: inline;
  font: inherit;
  padding: 0;
  text-align: left;
  overflow-wrap: anywhere;
}

.paper-card__title:hover {
  color: var(--color-accent);
  text-decoration: underline;
}

.paper-card__meta {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  margin: 0;
  line-height: var(--line-height-body);
  overflow-wrap: anywhere;
}

.paper-card__progress-heading {
  align-items: center;
  color: var(--color-text-secondary);
  display: flex;
  flex-wrap: wrap;
  font-size: var(--font-size-caption);
  gap: 6px;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 14px;
}

.paper-card__progress-heading strong {
  color: var(--color-text-primary);
}

.paper-card__progress {
  background: var(--color-border-subtle);
  border-radius: 999px;
  height: 4px;
  margin-top: 7px;
  overflow: hidden;
}

.paper-card__progress span {
  background: var(--color-accent);
  border-radius: inherit;
  display: block;
  height: 100%;
}

.paper-card__progress-note {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  margin: 6px 0 0;
}

.paper-card__live {
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  margin: 8px 0 0;
}

.paper-card__leftovers {
  color: var(--color-warning);
  display: grid;
  font-size: var(--font-size-caption);
  gap: 2px;
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}

.paper-card footer {
  align-items: center;
  border-top: 1px solid var(--border);
  color: var(--color-text-muted);
  display: flex;
  font-size: var(--font-size-caption);
  gap: 10px;
  margin-top: 12px;
  padding-top: 11px;
}

.paper-card__foot-spacer {
  flex: 1;
}

.paper-card__open {
  background: none;
  border: none;
  color: var(--color-accent);
  cursor: pointer;
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  padding: 4px 2px;
  white-space: nowrap;
}

.paper-card__open:hover {
  text-decoration: underline;
}

.paper-card__more {
  flex: 0 0 auto;
  position: relative;
}

.paper-card__more-toggle {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-secondary);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: var(--font-size-body);
  height: var(--control-height-small);
  justify-content: center;
  line-height: 1;
  width: 30px;
}

.paper-card__more-toggle:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.paper-card__more-menu {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
  min-width: 150px;
  padding: 6px;
  position: absolute;
  right: 0;
  top: 36px;
  z-index: 30;
}

.paper-card__more-menu button {
  background: none;
  border: none;
  border-radius: 6px;
  color: var(--color-text-primary);
  cursor: pointer;
  display: block;
  font: inherit;
  font-size: var(--font-size-dense);
  padding: 9px 12px;
  text-align: left;
  width: 100%;
}

.paper-card__more-menu button:hover:not(:disabled) {
  background: var(--secondary);
}

.paper-card__more-menu button.is-danger {
  color: var(--color-danger);
}

.paper-card__more-menu button:disabled {
  cursor: not-allowed;
  opacity: .55;
}

.paper-batch-bar {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 14px;
  position: sticky;
  top: 0;
  transition: border-color 150ms ease, box-shadow 150ms ease;
  z-index: 20;
}

.paper-batch-bar.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 6px 18px color-mix(in srgb, var(--color-accent) 12%, transparent);
}

.paper-batch-bar__select-all {
  align-items: center;
  color: var(--color-text-primary);
  cursor: pointer;
  display: flex;
  font-size: var(--font-size-dense);
  gap: 8px;
}

.paper-batch-bar__info {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.paper-batch-bar.is-active .paper-batch-bar__info {
  color: var(--color-accent);
  font-weight: var(--font-weight-semibold);
}

.paper-batch-bar__spacer {
  flex: 1;
}

.paper-button {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-primary);
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-medium);
  min-height: var(--control-height-default);
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
  background: var(--secondary);
  border-color: var(--color-border-strong);
}

.paper-button.is-primary {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: var(--primary-foreground);
}

.paper-button.is-primary:hover {
  background: var(--color-accent-hover);
}

.paper-button.is-quiet {
  color: var(--color-text-secondary);
}

.paper-button.is-danger-quiet {
  border-color: color-mix(in srgb, var(--color-danger) 24%, var(--card));
  color: var(--color-danger);
}

.paper-button.is-danger-quiet:hover {
  background: var(--color-danger-subtle);
  border-color: color-mix(in srgb, var(--color-danger) 35%, var(--card));
}

.paper-button.is-danger {
  background: var(--color-danger);
  border-color: var(--color-danger);
  color: var(--primary-foreground);
}

.paper-button.is-danger:hover {
  background: var(--color-danger);
  border-color: var(--color-danger);
}

.paper-button.is-review {
  background: var(--color-warning-subtle);
  border-color: color-mix(in srgb, var(--color-warning) 30%, var(--card));
  color: var(--color-warning);
  gap: 6px;
}

.paper-button.is-review:hover {
  background: var(--color-warning-subtle);
  border-color: color-mix(in srgb, var(--color-warning) 38%, var(--card));
}

.paper-button.is-review strong {
  background: var(--color-warning);
  border-radius: 999px;
  color: var(--primary-foreground);
  font-size: var(--font-size-caption);
  line-height: 18px;
  margin: 0;
  min-width: 18px;
  padding: 0 5px;
}

.paper-button.paper-icon-button {
  width: 38px;
  min-width: 38px;
  padding-inline: 0;
  font-size: var(--font-size-h2);
  line-height: 1;
}

.paper-library__notice {
  margin: 0;
  padding: 9px 12px;
  border-inline-start: 3px solid var(--color-accent);
  border-radius: 6px;
  background: var(--color-accent-subtle);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}

.paper-editor {
  width: 580px;
  max-width: 100%;
}

.paper-trash-drawer {
  background: var(--card);
  border-left: 1px solid var(--border);
  box-shadow: var(--shadow-overlay);
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
  color: var(--color-text-secondary);
  display: grid;
  font-size: var(--font-size-caption);
  gap: 4px;
}

.paper-trash-filters input,
.paper-trash-filters select,
.paper-permanent-confirmation input {
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-primary);
  font: inherit;
  height: var(--control-height-large);
  min-width: 0;
  padding: 0 9px;
}

.paper-trash-list {
  display: grid;
  gap: 10px;
}

.paper-trash-selection-bar {
  align-items: center;
  background: var(--secondary);
  border-radius: var(--radius-control);
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
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

.paper-trash-list article {
  align-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
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
  accent-color: var(--color-accent);
  height: 16px;
  margin: 0;
  width: 16px;
}

.paper-trash-list h3 {
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  line-height: 1.45;
  margin: 7px 0 4px;
}

.paper-trash-list p {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  margin: 0;
}

.paper-trash-list .paper-button {
  flex: 0 0 auto;
}

.paper-trash-drawer__message,
.paper-trash-confirm__message {
  background: var(--color-accent-subtle);
  border: 1px solid color-mix(in srgb, var(--color-accent) 18%, transparent);
  border-radius: 8px;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  line-height: 1.55;
  margin: 0;
  padding: 10px 12px;
}

.paper-trash-drawer__message.is-error,
.paper-trash-confirm__message.is-error {
  background: var(--color-danger-subtle);
  border-color: color-mix(in srgb, var(--color-danger) 26%, var(--card));
  color: var(--color-danger);
}

.paper-trash-confirm {
  width: 470px;
  max-width: 100%;
}

.paper-trash-confirm strong {
  color: var(--color-text-primary);
  display: block;
  font-size: var(--font-size-body);
}

.paper-trash-confirm p:not(.paper-trash-confirm__message) {
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
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
  background: var(--secondary);
  border-radius: var(--radius-control);
  color: var(--color-text-secondary);
  display: grid;
  font-size: var(--font-size-caption);
  gap: 2px;
  padding: 8px;
}

.paper-permanent-impact b {
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.paper-trash-confirm__list {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  line-height: 1.7;
  margin: 8px 0 0;
  max-height: 140px;
  overflow: auto;
  padding-left: 18px;
}

.paper-permanent-confirmation {
  color: var(--color-text-secondary);
  display: grid;
  font-size: var(--font-size-caption);
  gap: 6px;
  margin-top: 14px;
}

.paper-editor__header {
  align-items: flex-start;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 16px;
  justify-content: space-between;
  padding: 24px;
}

.paper-editor__header p {
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: .13em;
  margin: 0 0 5px;
  text-transform: uppercase;
}

.paper-editor__header h2 {
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
  letter-spacing: -.02em;
  margin: 0;
}

.paper-editor__header span {
  color: var(--color-text-secondary);
  display: block;
  font-size: var(--font-size-dense);
  margin-top: 7px;
}



.paper-editor__form {
  display: grid;
  gap: 18px 14px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 24px;
}

.paper-editor__form > label,
.paper-editor__region label {
  color: var(--color-text-secondary);
  display: grid;
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  gap: 7px;
}

.paper-editor__form label strong {
  color: var(--color-danger);
}

.paper-editor__form input {
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-body);
  height: 40px;
  min-width: 0;
  padding: 0 11px;
}

.paper-editor__form input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-accent) 10%, transparent);
  outline: none;
}

.paper-editor__form input[aria-invalid="true"] {
  border-color: var(--color-danger);
}

.paper-editor__form small {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-regular);
  line-height: 1.5;
}

.paper-editor__form .is-wide {
  grid-column: 1 / -1;
}

.paper-editor__region {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  padding: 14px;
}

.paper-editor__region legend {
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  padding: 0 5px;
}

.paper-editor__message {
  background: var(--color-accent-subtle);
  border: 1px solid color-mix(in srgb, var(--color-accent) 18%, transparent);
  border-radius: 8px;
  color: var(--color-accent);
  font-size: var(--font-size-caption);
  grid-column: 1 / -1;
  line-height: 1.55;
  margin: 0;
  padding: 10px 12px;
}

.paper-editor__message.is-error {
  background: var(--color-danger-subtle);
  border-color: color-mix(in srgb, var(--color-danger) 26%, var(--card));
  color: var(--color-danger);
}

.paper-editor__actions {
  align-items: center;
  border-top: 1px solid var(--border);
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

  .paper-trash-list article {
    align-items: flex-start;
    flex-direction: column;
  }

  .paper-card footer {
    flex-wrap: wrap;
  }

  .paper-trash-filters,
  .paper-permanent-impact {
    grid-template-columns: 1fr 1fr;
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

<style scoped>
.paper-library { display: flex; flex-direction: column; gap: 0; height: 100%; min-height: 0; }
.paper-library__tools { display: grid; gap: 8px; padding: 11px 12px 10px; border-bottom: 1px solid var(--color-border-subtle); }
.paper-search .app-input { width: 100%; height: var(--control-height-small); min-height: var(--control-height-small); font-size: var(--font-size-caption); }
.paper-category-chips, .paper-library__tool-line { display: flex; gap: 5px; align-items: center; font-size: var(--font-size-caption); }
.paper-library__tool-line { justify-content: space-between; }
.paper-library__tool-line > label { display: inline-flex; align-items: center; gap: 6px; }
.paper-library__extra-filters { position: relative; }
.paper-library__extra-filters summary { cursor: pointer; color: var(--color-text-secondary); font-size: var(--font-size-caption); }
.paper-library__filters { position: absolute; z-index: 10; top: 24px; right: 0; display: grid; gap: 8px; width: 250px; padding: 12px; border: 1px solid var(--color-border-default); border-radius: 10px; background: var(--card); box-shadow: var(--shadow-overlay); }
.paper-library__filters label { min-width: 0; }
.paper-batch-bar { flex-wrap: wrap; gap: 6px; padding: 6px 12px; border: 0; border-bottom: 1px solid var(--color-border-subtle); border-radius: 0; box-shadow: none; min-height: 0; position: static; font-size: var(--font-size-caption); }
.paper-batch-bar__info, .paper-batch-bar__select-all { font-size: var(--font-size-caption); }
.paper-batch-bar.is-active { box-shadow: none; }
.paper-folders { display: block; gap: 0; flex: 1; min-height: 0; overflow-y: auto; }
.paper-folder { display: block; gap: 0; }
.paper-folder__head { padding: 10px 12px 4px; gap: 6px; min-height: 0; border: 0; border-radius: 0; background: transparent; }
.paper-folder__check { padding: 0; align-self: auto; }
.paper-folder__header { gap: 6px; font-size: var(--font-size-caption); min-width: 0; min-height: 0; flex-wrap: nowrap; padding: 0; }
.paper-folder__header strong { font-weight: var(--font-weight-semibold); }
.paper-folder__kind { display: none; }
.paper-folder__count { font-size: var(--font-size-caption); margin-left: auto; }
.paper-folder__chevron { width: 12px; height: 12px; }
.paper-library__grid { display: grid; grid-template-columns: minmax(0,1fr); gap: 0; }
.paper-card { position: relative; display: block; padding: 8px 10px 8px 12px; border: 0; border-left: 3px solid transparent; border-radius: 0; background: transparent; box-shadow: none; }
.paper-card.is-current { border-left-color: var(--color-accent); background: var(--color-accent-subtle); }
.paper-card.is-selected { border-left-color: var(--color-accent); box-shadow: none; }
.paper-card:hover { background: var(--color-bg-subtle); transform: none; box-shadow: none; }
.paper-card__body { display: grid; grid-template-columns: minmax(0,1fr); min-width: 0; gap: 4px; padding: 0; }
.paper-card__row { display: flex; min-width: 0; gap: 7px; align-items: flex-start; padding-right: 20px; }
.paper-card__check { padding: 4px 0 0; }
.paper-card__check input, .paper-folder__check input { width: 12px; height: 12px; }
.paper-card__text { flex: 1; min-width: 0; }
.paper-card h2 { margin: 0; font-size: var(--font-size-dense); line-height: 1.5; }
.paper-card__title { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; width: 100%; min-width: 0; min-height: 0; padding: 0; white-space: normal; overflow: hidden; overflow-wrap: anywhere; font-weight: var(--font-weight-medium); }
.paper-card__compact-meta { display: flex; align-items: center; gap: 6px; margin-top: 3px; color: var(--color-text-muted); font-size: var(--font-size-caption); white-space: nowrap; }
.paper-card__compact-meta > span { overflow-wrap: normal; }
.paper-card__compact-progress { flex: none; width: 48px; height: 4px; border-radius: 2px; background: var(--color-border-subtle); overflow: hidden; }
.paper-card__compact-progress i { display: block; height: 100%; background: var(--color-accent); }
.paper-card__statuses { flex: none; display: grid; gap: 3px; justify-items: end; }
.paper-card__statuses :deep([data-slot='badge']) { font-size: var(--font-size-caption); padding: 1px 5px; }
.paper-card__complete { color: var(--color-success); font-size: var(--font-size-caption); }
.paper-card footer { position: absolute; right: 6px; top: 10px; display: flex; padding: 0; margin: 0; border: 0; }
.paper-card__more-toggle { width: 22px; height: 22px; border: 0; background: transparent; }
.paper-card__more-menu { min-width: 210px; }
.paper-card__metadata { padding: 7px 10px; font-size: var(--font-size-caption); }
.paper-card__metadata summary { cursor: pointer; }
.paper-card__metadata p { margin: 6px 0; }
.paper-library__filters { grid-template-columns: minmax(0,1fr); }
.paper-library__tools { --app-control-height: var(--control-height-small); }
.paper-card__compact-meta { gap: 5px; font-size: var(--font-size-caption); }
.paper-card__compact-progress { width: 32px; }
.paper-card__live, .paper-card__leftovers { font-size: var(--font-size-caption); margin: 2px 0 0 18px; }
.paper-library__pagination { padding: 8px 12px; border-top: 1px solid var(--color-border-subtle); font-size: var(--font-size-caption); flex-wrap: nowrap; gap: 8px; }
.paper-library__pagination .app-button { --app-control-height: var(--control-height-small); --app-control-font: var(--font-size-caption); --app-control-padding: 10px; }
.paper-trash-row { display: flex; gap: 12px; flex-wrap: wrap; padding: 12px; border-bottom: 1px solid var(--color-border-default); }
</style>
