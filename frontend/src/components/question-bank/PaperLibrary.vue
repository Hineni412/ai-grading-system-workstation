<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import type {
  QuestionBankPaper,
  QuestionBankPaperMetadataInput,
  QuestionBankPaperPermanentDeleteImpact,
} from '../../api/question-bank'
import { questionBankApi } from '../../api/question-bank'
import { useQuestionBankStore } from '../../stores/question-bank'
import { useJobStore } from '../../stores/jobs'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import AppButton from '../design-system/AppButton.vue'
import {
  formatQuestionLabel,
  isQuestionBankLibraryJob,
  jobBelongsToPaper,
  paperLeftoverLines,
  paperLiveAnalysisLine,
  type PaperQuestionRef,
} from './paper-analysis-status'

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
  reviewCriteria: []
}>()

const store = useQuestionBankStore()
const jobStore = useJobStore()
const curriculumScope = useCurriculumScopeStore()
const keyword = ref('')
const year = ref('')
const examType = ref('')
const sourceType = ref('')
const progressStatus = ref('')
const editingPaperId = ref<number | null>(null)
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
const taggingMode = ref<'fill' | 'retag' | null>(null)
const retagMessage = ref('')
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
  const search = keyword.value.trim().toLocaleLowerCase()
  return [...store.papers]
    .filter((paper) => {
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

watch(
  () => [
    libraryJobs.value.map((job) => `${job.id}:${job.status}`).join('|'),
    store.papers.map((paper) => `${paper.id}:${paper.complete_analysis_count}:${paper.criteria_needs_review_count}:${paper.question_count}`).join('|'),
    [...jobPaperLinks.value.entries()].map(([jobId, paperId]) => `${jobId}:${paperId}`).join('|'),
  ].join('/'),
  () => {
    void refreshQuestionRefs()
    void refreshIncompleteNumbers()
  },
  { immediate: true },
)

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
      const result = await questionBankApi.listQuestions({
        page,
        pageSize: 100,
        paperIds,
        sort: 'paper_order',
      })
      for (const item of result.items) {
        if (item.paper_id) {
          next.set(item.id, {
            id: item.id,
            paperId: item.paper_id,
            number: item.question_number,
          })
        }
      }
      if (page >= result.total_pages) break
      page += 1
    }
    questionRefs.value = next
  } catch {
    // Keep the last successful mapping; leftover copy can still use counts.
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
      const result = await questionBankApi.listQuestions({
        page,
        pageSize: 100,
        paperIds,
        analysisStatus: 'incomplete',
        sort: 'paper_order',
      })
      for (const item of result.items) {
        if (!item.paper_id) continue
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

function liveAnalysisLine(paper: QuestionBankPaper): string {
  const current = analysisJobForPaper(paper)
  if (!current || TERMINAL_JOB_STATUSES.has(current.status)) return ''
  return paperLiveAnalysisLine(current)
}

function leftoverLines(paper: QuestionBankPaper): string[] {
  const current = analysisJobForPaper(paper)
  const lines = current
    ? paperLeftoverLines(current, questionRefs.value, paper.id)
    : []
  // 任务结果给不出提示时（映射丢失、任务进行中、浏览器任务记录已清），
  // 用与进度条同一口径的试卷字段兜底，保证刷新后提示不消失。
  if (
    lines.length === 0
    && paper.complete_analysis_count < paper.question_count
  ) {
    const numbers = incompleteQuestionNumbers.value.get(paper.id)
    if (numbers?.length) {
      lines.push(`${formatQuestionLabel(numbers)}分析未完成`)
    }
  }
  if (
    paper.criteria_needs_review_count > 0
    && !lines.some((line) => line.includes('判定点待您审核'))
  ) {
    lines.push(`${paper.criteria_needs_review_count} 道题判定点待您审核`)
  }
  return lines
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

interface CurriculumQuestionScope {
  volumeId: string
  ids: number[]
}

async function loadCurriculumQuestionScopes(
  papers: QuestionBankPaper[],
): Promise<CurriculumQuestionScope[]> {
  const paperIdsByVolume = new Map<string, number[]>()
  for (const paper of papers) {
    if (!paper.curriculum_volume_id) continue
    const paperIds = paperIdsByVolume.get(paper.curriculum_volume_id) ?? []
    paperIds.push(paper.id)
    paperIdsByVolume.set(paper.curriculum_volume_id, paperIds)
  }
  const scopes = await Promise.all(
    [...paperIdsByVolume.entries()].map(async ([volumeId, paperIds]) => ({
      volumeId,
      ids: await loadQuestionIds(paperIds),
    })),
  )
  return scopes.filter(scope => scope.ids.length > 0)
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
    if (!window.confirm(
      `选中的 ${plans.length} 份试卷还有 ${incomplete} 道题未打全标签。将把共 ${total} 道题提交后端逐题核对：只补齐缺失、失败或已过期的标签和判定点，真正完整的题和人工修改不会重做。需要补齐时可能产生模型费用。${skippedVolumeNote(skippedWithoutVolume)}确认继续吗？`,
    )) return
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
    if (!window.confirm(
      `将重新分析选中的 ${plans.length} 份试卷共 ${total} 道题，可能产生模型费用；人工修改的标签会保留。${skippedVolumeNote(skippedWithoutVolume)}确认继续吗？`,
    )) return
    const count = await submitTaggingPlans(plans, true)
    retagMessage.value = `已提交 ${total} 道题，共 ${count} 个重新标注任务。${skippedVolumeNote(skippedWithoutVolume)}`
  } catch {
    retagMessage.value = '重新标注任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
  } finally {
    retagAllBusy.value = false
    taggingMode.value = null
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
    if (!window.confirm(
      `将重新分析“${paper.title || `试卷 #${paper.id}`}”的 ${ids.length} 道题，可能产生模型费用；人工修改的标签会保留。确认继续吗？`,
    )) return
    const count = await submitTaggingPlans([{ paper, ids }], true)
    retagMessage.value = `已提交 ${ids.length} 道题，共 ${count} 个重新标注任务。`
  } catch {
    retagMessage.value = '重新标注任务没有完整提交；已提交的任务会保留，请先查看任务记录。'
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
    const scopes = await loadCurriculumQuestionScopes(store.papers)
    const total = scopes.reduce((sum, scope) => sum + scope.ids.length, 0)
    if (total === 0) {
      retagMessage.value = '题库中没有可重新标注的题目。'
      return
    }
    if (!window.confirm(
      `将重新分析题库中的 ${total} 道题，可能产生模型费用；人工修改的标签会保留。确认继续吗？`,
    )) return
    let count = 0
    for (const { volumeId, ids } of scopes) {
      count += await submitTaggingBatches(ids, {
        forceRetag: true,
        scope: `curriculum-${volumeId}-retag`,
        volumeId,
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
    const scopes = await loadCurriculumQuestionScopes(store.papers)
    const total = scopes.reduce((sum, scope) => sum + scope.ids.length, 0)
    if (total === 0) {
      retagMessage.value = '题库中没有可补齐的题目。'
      return
    }
    if (!window.confirm(
      `将核对题库中的 ${total} 道题，只补齐缺失、失败或已过期的标签和判定点；真正完整的题和人工修改不会重做。需要补齐时可能产生模型费用。确认继续吗？`,
    )) return
    let count = 0
    for (const { volumeId, ids } of scopes) {
      count += await submitTaggingBatches(ids, {
        forceRetag: false,
        scope: `curriculum-${volumeId}-fill`,
        volumeId,
      })
    }
    retagMessage.value = `已提交全库 ${total} 道题等待后端核对，共 ${count} 个任务；完整内容不会重做。`
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
          class="paper-button is-review"
          @click="emit('reviewCriteria')"
        >
          判定点待审核
          <strong>{{ reviewQuestions }}</strong>
        </button>
        <AppButton
          variant="secondary"
          :disabled="retagAllBusy || retagBusyPaperId !== null"
          @click="fillAllTags"
        >{{ retagAllBusy && taggingMode === 'fill' ? '正在检查未完成题…' : '继续完成未完成题目' }}</AppButton>
        <button
          type="button"
          class="paper-button is-review"
          @click="emit('reviewTaxonomy')"
        >
          待审核新词
          <strong>{{ pendingTaxonomyLabel }}</strong>
        </button>
        <AppButton
          variant="secondary"
          :disabled="retagAllBusy || retagBusyPaperId !== null"
          @click="retagAllPapers"
        >{{ retagAllBusy && taggingMode === 'retag' ? '正在准备…' : '全库重新打标签' }}</AppButton>
        <AppButton variant="primary" @click="emit('import')">
          上传试卷
        </AppButton>
      </div>
    </header>

    <p v-if="retagMessage" class="paper-library__notice" role="status">{{ retagMessage }}</p>
    <p v-if="deleteNotice" class="paper-library__notice" role="status">{{ deleteNotice }}</p>

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
          <option value="review">判定点待审核</option>
        </select>
      </label>
      <AppButton variant="ghost" @click="resetFilters">清除</AppButton>
    </div>

    <div class="paper-batch-bar" :class="{ 'is-active': selectedCount > 0 }">
      <label class="paper-batch-bar__select-all">
        <input
          type="checkbox"
          :checked="allFilteredSelected"
          :indeterminate="someFilteredSelected && !allFilteredSelected"
          :disabled="filteredPaperIds.length === 0"
          @change="onSelectAllChange"
        >
        <span>全选</span>
      </label>
      <span class="paper-batch-bar__info">
        {{ selectedCount > 0 ? `已选 ${selectedCount} 份试卷` : '未选中试卷' }}
      </span>
      <span class="paper-batch-bar__spacer" />
      <AppButton
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
        variant="danger"
        :disabled="batchActionsDisabled || permanentDeleteState === 'loading' || permanentDeleteState === 'working'"
        @click="requestPermanentDelete(selectedPapers)"
      >删除</AppButton>
      <AppButton
        variant="secondary"
        :disabled="selectedCount === 0"
        @click="clearPaperSelection"
      >取消选择</AppButton>
    </div>

    <p v-if="store.papersState === 'loading'" class="paper-library__state" role="status">
      正在读取试卷库…
    </p>
    <div v-else-if="store.papersState === 'error'" class="paper-library__state is-error" role="alert">
      <span>试卷库暂时无法读取。</span>
      <AppButton variant="secondary" @click="store.loadPapers()">重新读取</AppButton>
    </div>
    <div v-else-if="filteredPapers.length === 0" class="paper-library__state">
      <strong>{{ store.papers.length ? '当前筛选下没有试卷' : '还没有导入试卷' }}</strong>
      <p>{{ store.papers.length ? '可以清除筛选后再查看。' : '上传 Word 或 PDF 后，会在这里生成一张试卷卡片。' }}</p>
    </div>

    <div v-else class="paper-folders">
      <section v-for="folder in paperFolders" :key="folder.key" class="paper-folder">
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
            v-for="paper in folder.papers"
            :key="paper.id"
            class="paper-card"
            :class="{ 'is-selected': isPaperSelected(paper.id) }"
          >
        <label class="paper-card__check" @click.stop>
          <input
            type="checkbox"
            :checked="isPaperSelected(paper.id)"
            :aria-label="`选择${paper.title || `试卷 ${paper.id}`}`"
            @change="onPaperSelectChange(paper.id, $event)"
          >
        </label>
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
          <h2>
            <button
              type="button"
              class="paper-card__title"
              @click="emit('open', paper)"
            >{{ paper.title || `未命名试卷 #${paper.id}` }}</button>
          </h2>
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
          <p v-if="liveAnalysisLine(paper)" class="paper-card__live" role="status">
            {{ liveAnalysisLine(paper) }}
          </p>
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
            · 判定点 {{ paper.criteria_question_count }}/{{ paper.question_count }}
            <template v-if="paper.criteria_needs_review_count">
              · 待审核判定点 {{ paper.criteria_needs_review_count }}
            </template>
          </p>
          <ul v-if="leftoverLines(paper).length" class="paper-card__leftovers">
            <li v-for="line in leftoverLines(paper)" :key="line">{{ line }}</li>
          </ul>
          <footer>
            <span>更新于 {{ formatDate(paper.updated_at) }}</span>
            <span class="paper-card__foot-spacer" />
            <button
              type="button"
              class="paper-card__open"
              @click="emit('open', paper)"
            >查看试题 →</button>
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
                <button
                  type="button"
                  role="menuitem"
                  @click="closePaperMenu(); editPaper(paper)"
                >编辑资料</button>
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
          <p v-if="permanentDeleteMessage" class="paper-trash-confirm__message is-error" role="alert">
            {{ permanentDeleteMessage }}
          </p>
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
  color: var(--color-accent) !important;
  font-size: 11px !important;
  font-weight: 750;
  letter-spacing: .13em;
  margin: 0 0 5px !important;
}

.paper-library__header h1 {
  color: var(--color-text-primary);
  font-size: 30px;
  letter-spacing: -.04em;
  margin: 0;
}

.paper-library__header p {
  color: var(--color-text-secondary);
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
  color: var(--color-text-secondary);
  font-size: 12px;
}

.paper-library__stats strong {
  color: var(--color-text-primary);
  font-size: 19px;
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
  font-size: 11px;
  font-weight: 650;
  gap: 5px;
}

.paper-library__filters input,
.paper-library__filters select {
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-primary);
  font: inherit;
  font-size: 13px;
  height: 36px;
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
  font-size: 11px;
  padding: 3px 8px;
}

.paper-folder__count {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin-left: auto;
}

.paper-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  box-shadow: 0 1px 2px color-mix(in srgb, var(--color-text-primary) 5%, transparent);
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  min-height: 230px;
  position: relative;
  transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
}

.paper-card:hover {
  border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
  box-shadow: 0 8px 26px color-mix(in srgb, var(--color-text-primary) 8%, transparent);
  transform: translateY(-1px);
}

.paper-card.is-selected {
  background: color-mix(in srgb, var(--color-accent-subtle) 55%, var(--card));
  border-color: var(--color-accent);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--color-accent) 14%, transparent);
}

.paper-card__check {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 7px;
  box-shadow: 0 1px 3px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
  cursor: pointer;
  display: flex;
  inset-block-start: 10px;
  inset-inline-start: 10px;
  padding: 5px;
  position: absolute;
  z-index: 2;
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

.paper-card__cover {
  align-items: center;
  background: var(--color-accent-subtle);
  border-right: 1px solid var(--border);
  color: var(--color-accent);
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
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.paper-card__cover.is-other {
  background: var(--secondary);
  color: var(--color-text-secondary);
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
  background: var(--background);
  border-radius: 999px;
  color: var(--color-text-secondary);
  font-size: 11px;
  padding: 3px 8px;
}

.paper-chip.is-format {
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
  font-weight: 700;
}

.paper-card h2 {
  color: var(--color-text-primary);
  display: -webkit-box;
  font-size: 16px;
  line-height: 1.45;
  margin: 11px 0 5px;
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
}

.paper-card__title:hover {
  color: var(--color-accent);
  text-decoration: underline;
}

.paper-card__meta {
  color: var(--color-text-muted);
  font-size: 12px;
  margin: 0;
}

.paper-card__progress-heading {
  color: var(--color-text-secondary);
  display: flex;
  font-size: 11px;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 15px;
}

.paper-card__progress-heading strong {
  color: var(--color-text-primary);
}

.paper-card__progress {
  background: var(--color-border-subtle);
  border-radius: 999px;
  height: 6px;
  margin-top: 7px;
  overflow: hidden;
}

.paper-card__progress span {
  background: var(--color-accent);
  border-radius: inherit;
  display: block;
  height: 100%;
  min-width: 2px;
}

.paper-card__progress-note {
  color: var(--color-text-muted);
  font-size: 11px;
  margin: 6px 0 0;
}

.paper-card__live {
  color: var(--color-accent);
  font-size: 12px;
  font-weight: 600;
  margin: 8px 0 0;
}

.paper-card__leftovers {
  color: var(--color-warning);
  display: grid;
  font-size: 12px;
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
  font-size: 11px;
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
  font-size: 12px;
  font-weight: 650;
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
  font-size: 15px;
  height: 30px;
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
  font-size: 13px;
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
  font-size: 13px;
  gap: 8px;
}

.paper-batch-bar__info {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.paper-batch-bar.is-active .paper-batch-bar__info {
  color: var(--color-accent);
  font-weight: 650;
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
  background: var(--card);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-panel);
  color: var(--color-text-secondary);
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
  color: var(--color-danger);
}

.paper-library__notice {
  margin: 0;
  padding: 9px 12px;
  border-inline-start: 3px solid var(--color-accent);
  border-radius: 6px;
  background: var(--color-accent-subtle);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.paper-editor-layer {
  align-items: stretch;
  background: color-mix(in srgb, var(--color-text-primary) 38%, transparent);
  display: flex;
  inset: 0;
  justify-content: flex-end;
  position: fixed;
  z-index: 1200;
}

.paper-editor {
  background: var(--card);
  border-left: 1px solid var(--border);
  box-shadow: -12px 0 32px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
  display: flex;
  flex-direction: column;
  max-width: 100%;
  overflow: auto;
  width: 580px;
}

.paper-trash-drawer {
  background: var(--card);
  border-left: 1px solid var(--border);
  box-shadow: -12px 0 32px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
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
  font-size: 10px;
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
  font-size: 11px;
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
  font-size: 15px;
  line-height: 1.45;
  margin: 7px 0 4px;
}

.paper-trash-list p {
  color: var(--color-text-muted);
  font-size: 11px;
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
  font-size: 12px;
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

.paper-trash-confirm-layer {
  align-items: center;
  background: color-mix(in srgb, var(--color-text-primary) 42%, transparent);
  display: flex;
  inset: 0;
  justify-content: center;
  padding: 20px;
  position: fixed;
  z-index: 1250;
}

.paper-trash-confirm {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-overlay);
  box-shadow: 0 18px 52px color-mix(in srgb, var(--color-text-primary) 18%, transparent);
  max-width: 100%;
  padding: 24px;
  width: 470px;
}

.paper-trash-confirm__eyebrow {
  color: var(--color-danger) !important;
  font-size: 11px !important;
  font-weight: 750;
  letter-spacing: .12em;
  margin: 0 0 6px !important;
}

.paper-trash-confirm h2 {
  color: var(--color-text-primary);
  font-size: 21px;
  margin: 0 0 14px;
}

.paper-trash-confirm > strong {
  color: var(--color-text-primary);
  display: block;
  font-size: 14px;
}

.paper-trash-confirm > p:not(.paper-trash-confirm__eyebrow, .paper-trash-confirm__message) {
  color: var(--color-text-secondary);
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
  background: var(--secondary);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  display: grid;
  font-size: 10px;
  gap: 2px;
  padding: 8px;
}

.paper-permanent-impact b {
  color: var(--color-text-primary);
  font-size: 16px;
}

.paper-trash-confirm__list {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.7;
  margin: 8px 0 0;
  max-height: 140px;
  overflow: auto;
  padding-left: 18px;
}

.paper-permanent-confirmation {
  color: var(--color-text-secondary);
  display: grid;
  font-size: 12px;
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
  font-size: 11px;
  font-weight: 750;
  letter-spacing: .13em;
  margin: 0 0 5px;
  text-transform: uppercase;
}

.paper-editor__header h2 {
  color: var(--color-text-primary);
  font-size: 22px;
  letter-spacing: -.02em;
  margin: 0;
}

.paper-editor__header span {
  color: var(--color-text-secondary);
  display: block;
  font-size: 13px;
  margin-top: 7px;
}

.paper-editor__close {
  align-items: center;
  background: var(--card);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  color: var(--color-text-secondary);
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
  color: var(--color-text-secondary);
  display: grid;
  font-size: 12px;
  font-weight: 650;
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
  font-size: 14px;
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
  font-size: 11px;
  font-weight: 400;
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
  font-size: 12px;
  font-weight: 700;
  padding: 0 5px;
}

.paper-editor__message {
  background: var(--color-accent-subtle);
  border: 1px solid color-mix(in srgb, var(--color-accent) 18%, transparent);
  border-radius: 8px;
  color: var(--color-accent);
  font-size: 12px;
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

  .paper-card__more {
    align-self: flex-end;
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
