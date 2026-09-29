<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DialogClose, DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

import {
  classAnalysisApi,
  type CauseCategory,
  type ClassAnalysisQuestion,
  type ClassAnalysisResponse,
  type ClassCauseEvidence,
  type ClassCause,
} from '../../api/class-analysis'
import type { ResultsCenterStudent } from '../../api/results-center'
import { ApiError } from '../../api/errors'
import { jobApi, TERMINAL_JOB_STATUSES } from '../../api/jobs'
import {
  modelProfilesApi,
  type ModelTaskBinding,
} from '../../api/model-profiles'
import { useJobStore } from '../../stores/jobs'
import { useResultsCenterStore } from '../../stores/results-center'
import AppButton from '../design-system/AppButton.vue'
import ClassAnalysisGenerateConfirm from './ClassAnalysisGenerateConfirm.vue'
import ReviewAnswerPanel from '../review/ReviewAnswerPanel.vue'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import { scoreStructureFor, subQuestionLabelFor } from './results-overview'
import {
  cachedClassAnalysis,
  invalidateClassAnalysis,
  rememberClassAnalysis,
} from './class-analysis-cache'

const props = defineProps<{
  sessionId: number | null
  focusQuestion?: string | null
  initialClass?: string | null
}>()

type QuestionRecord = ClassAnalysisQuestion['records'][number]

const route = useRoute()
const router = useRouter()
const jobStore = useJobStore()
const resultsStore = useResultsCenterStore()
const analysis = ref<ClassAnalysisResponse | null>(null)
const selectedClass = ref(props.initialClass ?? '')
const listRoot = ref<HTMLElement | null>(null)
const sortMode = ref<'rate' | 'number'>('rate')
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
const confirmOpen = ref(false)
const confirmLoading = ref(false)
const confirmBinding = ref<ModelTaskBinding | null>(null)
const confirmError = ref('')
const regenerating = ref(false)
let loadGeneration = 0
let loadController: AbortController | null = null
const expandedBands = ref(new Set<string>())
const expandedCauses = ref(new Set<string>())
// 打开的 <details> 键；证据内容只在打开时渲染，减少长卷面的常驻 DOM。
const openDetails = ref(new Set<string>())
const patternEdit = ref<{
  questionId: string
  kind: string
  category: CauseCategory | ''
  reason: string
  newReason: string
} | null>(null)
const patternEditLoading = ref(false)
const patternEditError = ref('')
const EDITABLE_KINDS = new Set(['error', 'process'])
const KIND_CATEGORIES: Record<string, readonly CauseCategory[]> = {
  error: ['概念理解', '计算与化简', '审题与条件', '方法与思路'],
  process: ['过程与依据', '书写与规范'],
}

const activeJobId = computed(() => analysis.value?.active_job_id ?? null)

const activeJob = computed(() => (
  activeJobId.value === null ? null : jobStore.jobs[activeJobId.value] ?? null
))

const generating = computed(() => {
  if (analysis.value?.status === 'generating') return true
  return activeJob.value !== null
    && !TERMINAL_JOB_STATUSES.has(activeJob.value.status)
})

const canRegenerate = computed(() => (
  analysis.value !== null
  && !generating.value
  && !regenerating.value
))

const data = computed(() => analysis.value?.data ?? null)
const causeAnalysis = computed(() => analysis.value?.cause_analysis ?? null)
const causeStatusText = computed(() => {
  const state = causeAnalysis.value
  if (state?.legacy_questions) return `${state.legacy_questions} 题保留既有归并；更新整理后，将结合真实作答分别整理错因、过程缺项和待核对事项。`
  if (state?.outdated_questions) return `${state.outdated_questions} 题为旧版整理，暂无错误大类；重新整理后自动升级并补充大类。`
  if (!state || state.status === 'not_generated') return state?.failed_questions
    ? '错因整理未完成，保留原始理由；可重新整理。'
    : state?.stale ? '作答、批语或题目依据已变化，建议更新整理。' : '当前按原始表述合并，可结合真实作答整理。'
  if (state.status === 'partial') return `部分题目已整理，${state.pending_questions} 题仍显示原始理由，可继续整理。`
  return '已结合现有作答整理；展开可核对本题表现、作答与批语。同一学生同类只计一次，不同类可重复出现。'
})
const diagnosticQuestions = computed(() => {
  const questions = data.value?.questions ?? []
  return [...questions].sort((a, b) => a.class_rate - b.class_rate)
    .map((question) => {
      const sub = subQuestionLabelFor(question.question_id, question.stem_summary, questions)
      const limits = [0, Number((question.max_score / 3).toFixed(2)), Number((question.max_score * 2 / 3).toFixed(2)), question.max_score]
      const records = [...question.records].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
      const bands = [2, 1, 0].map((index) => ({
        key: `${question.question_id}:${index}`,
        label: `${formatScore(limits[index]!)}–${formatScore(limits[index + 1]!)} 分`,
        description: `${formatScore(limits[index]!)} 分及以上、不足 ${formatScore(limits[index + 1]!)} 分`,
        records: records.filter((record) => record.score !== null && record.score >= limits[index]! && record.score < limits[index + 1]!),
      }))
      const unscored = records.filter((record) => record.score === null)
      if (unscored.length) bands.push({ key: `${question.question_id}:unscored`, label: '未记分', description: '尚未记录本题得分', records: unscored })
      const structuredCauses = question.causes_legacy === false || question.causes?.some((cause) => cause.kind !== undefined)
      const groupedCauses: ClassCause[] = [...(question.causes ?? [])]
      if (structuredCauses && question.cause_review?.uncertain.length) groupedCauses.push({
        kind: 'review', reason: '错因待明确', count: evidenceStudentCount(question.cause_review.uncertain),
        evidence: question.cause_review.uncertain,
      })
      const kinds = [
        ['legacy', question.causes_legacy ? '既有归并 · 待升级' : '原始归并'],
        ['error', '数学与单位错误'], ['process', '过程与表达缺项'],
        ['response_state', '作答状态'], ['carry_forward', '前问错误延续'], ['review', '需要核对'],
      ] as const
      const causeSections = kinds.map(([kind, label]) => {
        const causes = groupedCauses.filter((cause) => (cause.kind ?? 'legacy') === kind).sort((a, b) => b.count - a.count)
        const count = causes.every((cause) => cause.evidence?.length)
          ? evidenceStudentCount(causes.flatMap((cause) => cause.evidence ?? [])) : null
        const byCategory = new Map<string, ClassCause[]>()
        for (const cause of causes) {
          if (!cause.category) continue
          byCategory.set(cause.category, [...(byCategory.get(cause.category) ?? []), cause])
        }
        const categoryText = byCategory.size ? [...byCategory.entries()]
          .map(([category, entries]) => `${category} ${
            entries.every((cause) => cause.evidence?.length)
              ? evidenceStudentCount(entries.flatMap((cause) => cause.evidence ?? []))
              : entries.reduce((total, cause) => total + cause.count, 0)
          }`).join(' · ') : null
        return { kind, label, key: `${question.question_id}:${kind}`, causes, count, categoryText }
      }).filter((section) => section.causes.length)
      return {
        ...question,
        subIndex: sub?.index ?? null,
        subParentStem: sub?.parentStem ?? null,
        causeSections,
        structuredCauses,
        scoreBands: bands.map((band) => ({ ...band, share: records.length ? Number((band.records.length / records.length * 100).toFixed(1)) : 0 })),
        causes: [...(question.causes ?? [])].sort((a, b) => b.count - a.count),
      }
    })
})

const sortedQuestions = computed(() => {
  const questions = diagnosticQuestions.value
  if (sortMode.value === 'rate') return questions
  return [...questions].sort(
    (a, b) => a.question_id.localeCompare(b.question_id, 'zh-CN', { numeric: true }),
  )
})

const requestedQuestionId = computed(() => {
  const query = route.query.question
  return (typeof query === 'string' && query.length > 0 ? query : null) ?? props.focusQuestion ?? null
})

const selectedQuestionId = computed(() => {
  const list = sortedQuestions.value
  const requested = requestedQuestionId.value
  if (requested !== null && list.some((question) => question.question_id === requested)) return requested
  return list[0]?.question_id ?? null
})

const selectedQuestion = computed(() => (
  sortedQuestions.value.find((question) => question.question_id === selectedQuestionId.value) ?? null
))

function selectQuestion(questionId: string): void {
  if (questionId === selectedQuestionId.value && route.query.question === questionId) return
  void router.replace({ query: { ...route.query, question: questionId } })
}

function listRowFor(questionId: string): HTMLElement | null {
  return [...(listRoot.value?.querySelectorAll<HTMLElement>('[data-question-id]') ?? [])]
    .find((element) => element.dataset.questionId === questionId) ?? null
}

watch(selectedQuestionId, async (questionId) => {
  if (questionId === null) return
  await nextTick()
  listRowFor(questionId)?.scrollIntoView?.({ block: 'nearest' })
})

function onListKeydown(event: KeyboardEvent): void {
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  const list = sortedQuestions.value
  if (!list.length) return
  event.preventDefault()
  const index = list.findIndex((question) => question.question_id === selectedQuestionId.value)
  const nextIndex = event.key === 'ArrowDown'
    ? Math.min(index + 1, list.length - 1)
    : Math.max(index - 1, 0)
  const target = list[nextIndex]
  if (!target) return
  selectQuestion(target.question_id)
  void nextTick(() => listRowFor(target.question_id)?.focus())
}

// 明细成绩（结果中心 store）提供满分/部分/0分结构和逐题深评入口。
const scopeStudents = computed(() => (resultsStore.results?.students ?? [])
  .filter((student) => !selectedClass.value || (student.class_name ?? '') === selectedClass.value))

const selectedStructure = computed(() => {
  const question = selectedQuestion.value
  if (question === null) return null
  const structure = scoreStructureFor(scopeStudents.value, [question.question_id])
    .get(question.question_id)
  return structure !== undefined && structure.resolved > 0 ? structure : null
})

function structureWidth(count: number, resolved: number): string {
  return resolved > 0 ? `${(count / resolved) * 100}%` : '0%'
}

function reviewTargetFor(questionId: string, record: QuestionRecord): {
  student: ResultsCenterStudent
  reviewItemId: string
} | null {
  if (record.student_id === undefined) return null
  const student = resultsStore.results?.students
    .find((entry) => entry.student_id === record.student_id)
  const item = student?.items.find((entry) => entry.question_id === questionId)
  return student !== undefined && item !== undefined
    ? { student, reviewItemId: item.review_item_id }
    : null
}

function openStudentReview(questionId: string, record: QuestionRecord): void {
  const sessionId = props.sessionId
  const target = reviewTargetFor(questionId, record)
  const question = diagnosticQuestions.value.find((entry) => entry.question_id === questionId)
  if (sessionId === null || target === null || question === undefined) return
  resultsStore.setReviewNavigation({
    sessionId,
    studentIds: [...new Set(question.scoreBands
      .flatMap((band) => band.records)
      .map((entry) => entry.student_id)
      .filter((id): id is number => id !== undefined))],
  })
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId),
      scope: 'all',
      question: questionId,
      item: target.reviewItemId,
      student: String(target.student.student_id),
      entry: 'results',
    },
  })
}

watch(
  () => props.sessionId,
  () => {
    closeConfirm()
    selectedClass.value = props.initialClass ?? ''
    analysis.value = null
    void load()
  },
  { immediate: true },
)

watch(
  () => props.initialClass,
  (value) => {
    const next = value ?? ''
    if (next === selectedClass.value) return
    selectedClass.value = next
    void load()
  },
)

watch(
  () => activeJob.value?.status,
  (status) => {
    if (status !== undefined && TERMINAL_JOB_STATUSES.has(status)) {
      if (props.sessionId !== null) invalidateClassAnalysis(props.sessionId)
      void load()
    }
  },
)

async function load(): Promise<void> {
  const sessionId = props.sessionId
  const generation = ++loadGeneration
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  if (sessionId === null) {
    analysis.value = null
    loadState.value = 'idle'
    return
  }
  const requestedClass = selectedClass.value
  const cached = cachedClassAnalysis(sessionId, requestedClass)
  if (cached) {
    // 先复用总览侧缓存的数据立即出表，再静默重取最新结果。
    analysis.value = cached
    loadState.value = 'ready'
  } else {
    loadState.value = 'loading'
    expandedBands.value = new Set()
    expandedCauses.value = new Set()
    openDetails.value = new Set()
  }
  errorMessage.value = ''
  try {
    const next = await classAnalysisApi.getClassAnalysis(sessionId, controller.signal, requestedClass, 'summary')
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    rememberClassAnalysis(sessionId, requestedClass, next)
    analysis.value = next
    selectedClass.value = next.selected_class ?? ''
    loadState.value = 'ready'
    void ensureTracked(next.active_job_id)
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    if (cached) return
    loadState.value = 'error'
    errorMessage.value = '班级分析暂时无法读取，请稍后重试。'
  }
}

onBeforeUnmount(() => {
  loadGeneration += 1
  loadController?.abort()
})

async function ensureTracked(id: number | null): Promise<void> {
  if (id === null) return
  const existing = jobStore.jobs[id]
  if (existing !== undefined && !TERMINAL_JOB_STATUSES.has(existing.status)) return
  try {
    const job = await jobApi.getJob(id)
    // await 期间页面可能已通过其他链路拿到更新状态，不覆盖。
    if (jobStore.jobs[id] !== undefined) return
    if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
  } catch {
    // 任务可能已结束或被清理；下次刷新时按最新状态展示。
  }
}

async function openRegenerateConfirm(): Promise<void> {
  if (!canRegenerate.value) return
  confirmOpen.value = true
  confirmBinding.value = null
  confirmError.value = ''
  confirmLoading.value = true
  try {
    const state = await modelProfilesApi.getState()
    if (!confirmOpen.value) return
    confirmBinding.value = state.task_bindings.content_generation
  } catch {
    if (!confirmOpen.value) return
    confirmError.value = '模型配置暂时无法读取，请稍后重试。'
  } finally {
    if (confirmOpen.value) confirmLoading.value = false
  }
}

function closeConfirm(): void {
  confirmOpen.value = false
  confirmBinding.value = null
  confirmError.value = ''
  confirmLoading.value = false
}

async function confirmRegenerate(): Promise<void> {
  const sessionId = props.sessionId
  if (sessionId === null || regenerating.value || confirmLoading.value) return
  regenerating.value = true
  confirmError.value = ''
  try {
    const job = await classAnalysisApi.regenerate(sessionId, undefined, 'causes')
    if (props.sessionId !== sessionId) return
    jobStore.track(job)
    closeConfirm()
    invalidateClassAnalysis(sessionId)
    await load()
  } catch (error) {
    if (props.sessionId !== sessionId) return
    confirmError.value = error instanceof ApiError
      && error.code === 'content_generation_model_not_configured'
      ? '未配置内容生成模型，请前往 设置→模型配置 绑定后重试。'
      : '整理请求未能提交，请稍后重试。'
  } finally {
    regenerating.value = false
  }
}

function formatScore(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? String(value)
    : String(Number(value.toFixed(2)))
}

function causeStudents(question: ClassAnalysisQuestion, ids: number[]): string {
  return question.records.filter((record) => record.student_id !== undefined && ids.includes(record.student_id))
    .map((record) => `${record.student_name}（${!selectedClass.value && record.class_name ? `${record.class_name}，` : ''}${formatScore(record.score)} 分）`).join('、')
}

function evidenceStudentCount(items: ClassCauseEvidence[]): number {
  return new Set(items.flatMap((item) => item.student_ids)).size
}

function canEditPattern(cause: ClassCause): boolean {
  return !!cause.kind && EDITABLE_KINDS.has(cause.kind)
}

function openPatternEdit(question: ClassAnalysisQuestion, cause: ClassCause): void {
  if (!cause.kind) return
  const allowed = KIND_CATEGORIES[cause.kind] ?? []
  patternEdit.value = {
    questionId: question.question_id,
    kind: cause.kind,
    category: cause.category && allowed.includes(cause.category)
      ? cause.category
      : (allowed[0] ?? ''),
    reason: cause.reason,
    newReason: cause.reason,
  }
  patternEditError.value = ''
}

async function savePatternEdit(): Promise<void> {
  const sessionId = props.sessionId
  const draft = patternEdit.value
  if (sessionId === null || !draft || patternEditLoading.value) return
  patternEditLoading.value = true
  patternEditError.value = ''
  try {
    await classAnalysisApi.editCausePattern(sessionId, {
      question_id: draft.questionId,
      kind: draft.kind,
      reason: draft.reason,
      new_reason: draft.newReason.trim(),
      category: draft.category || null,
      operation_token: `${sessionId}:${draft.questionId}:${Date.now()}`,
    })
    if (props.sessionId !== sessionId) return
    patternEdit.value = null
    invalidateClassAnalysis(sessionId)
    await load()
  } catch (error) {
    if (props.sessionId !== sessionId) return
    patternEditError.value = error instanceof ApiError
      ? (error.code === 'cause_pattern_not_ready' || error.code === 'cause_pattern_group_missing'
        ? '错因整理结果已变化，请刷新页面后重试。'
        : error.code === 'cause_pattern_category_invalid' || error.code === 'cause_pattern_name_blank'
          ? '错法名称或错误大类无效，请检查后重试。'
          : '修改失败，请稍后重试。')
      : '修改失败，请稍后重试。'
  } finally {
    patternEditLoading.value = false
  }
}

function toggleBand(key: string): void {
  const next = new Set(expandedBands.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedBands.value = next
}

function toggleCauses(questionId: string): void {
  const next = new Set(expandedCauses.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedCauses.value = next
}

function syncDetailOpen(event: Event, key: string): void {
  const next = new Set(openDetails.value)
  if ((event.target as HTMLDetailsElement).open) next.add(key)
  else next.delete(key)
  openDetails.value = next
}

function formatPercent(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${Math.round(percent)}%`
}

function formatShortDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return value
  return match[1] === String(new Date().getFullYear())
    ? `${match[2]}-${match[3]}`
    : `${match[1]}-${match[2]}-${match[3]}`
}

function rateTone(rate: number): 'low' | 'mid' | 'high' {
  const normalized = rate <= 1 ? rate : rate / 100
  if (normalized < 0.4) return 'low'
  if (normalized < 0.7) return 'mid'
  return 'high'
}
</script>


<template>
  <section class="class-analysis" aria-label="试题诊断">
      <div v-if="props.sessionId === null" class="results-state-panel">
        <strong>请先在顶部选择考试</strong>
        <span>选择后，这里会显示该考试的班级整体分析。</span>
      </div>
      <template v-else>
        <div v-if="analysis" class="class-analysis__toolbar">
          <label class="class-analysis__toggle">
            <span>查看范围</span>
            <select v-model="selectedClass" aria-label="查看班级" @change="load">
              <option value="">全部班级（合并）</option>
              <option v-for="name in analysis.class_names" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>
          <p v-if="data" class="class-analysis__exam-meta">
            满分 {{ formatScore(data.exam.full_score) }} 分 · 参考 {{ data.present }} 人<template
              v-if="data.roster_absent.length"
            > · 名册另有 {{ data.roster_absent.length }} 人未检测到答卷</template><template
              v-if="data.exam.graded_at"
            > · 批改完成 {{ formatShortDate(data.exam.graded_at) }}</template>
          </p>
          <p class="class-analysis__cause-status" data-testid="cause-analysis-status">{{ causeStatusText }}</p>
          <div class="class-analysis__toolbar-actions">
            <AppButton
              variant="secondary"
              data-testid="group-causes-open"
              :disabled="!canRegenerate || causeAnalysis?.pending_questions === 0"
              @click="openRegenerateConfirm()"
            >
              {{ causeAnalysis?.status === 'ready' ? '已整理' : causeAnalysis?.status === 'partial' || causeAnalysis?.stale ? '继续整理 / 更新' : '整理错因' }}
            </AppButton>
            <span class="class-analysis__privacy">含学生姓名，请勿直接外发</span>
          </div>
        </div>
        <p v-if="analysis?.small_sample" class="class-analysis__note" data-testid="small-sample-note">
          当前范围参考人数较少，比率指标解读需谨慎。
        </p>
        <div v-if="loadState === 'loading'" class="results-state-panel" role="status">
          <strong>正在读取班级分析</strong>
          <span>正在统计当前范围的成绩与试题。</span>
        </div>
        <div v-else-if="loadState === 'error'" class="results-state-panel results-state-panel--error" role="alert">
          <strong>班级分析暂时无法读取</strong>
          <span>{{ errorMessage }}</span>
          <AppButton variant="secondary" @click="load">重新加载</AppButton>
        </div>
        <div v-else-if="analysis?.status === 'no_data'" class="results-state-panel" data-testid="class-analysis-empty">
          <strong>当前范围尚无已批改成绩</strong>
          <span>完成批改后，这里会显示统计结果。</span>
        </div>
        <div v-else-if="generating && !data" class="results-state-panel" role="status" data-testid="class-analysis-generating">
          <strong>班级分析生成中…</strong>
          <span>完成后页面会自动更新，无需手动刷新。</span>
        </div>
        <template v-else-if="analysis && data">
          <div class="class-analysis__body">
            <aside
              ref="listRoot"
              class="class-analysis__list"
              aria-label="题目列表"
              @keydown="onListKeydown"
            >
              <div class="class-analysis__sort" role="group" aria-label="题目排序">
                <button
                  type="button"
                  :aria-pressed="sortMode === 'rate'"
                  data-testid="sort-rate"
                  @click="sortMode = 'rate'"
                >按得分率</button>
                <button
                  type="button"
                  :aria-pressed="sortMode === 'number'"
                  data-testid="sort-number"
                  @click="sortMode = 'number'"
                >按题号</button>
              </div>
              <div class="class-analysis__list-items" role="listbox" aria-label="题目">
                <button
                  v-for="question in sortedQuestions"
                  :key="question.question_id"
                  type="button"
                  role="option"
                  class="class-analysis__list-row"
                  :class="{ 'is-selected': question.question_id === selectedQuestionId }"
                  :aria-selected="question.question_id === selectedQuestionId"
                  :data-question-id="question.question_id"
                  @click="selectQuestion(question.question_id)"
                >
                  <span class="class-analysis__list-head">
                    <strong>{{ question.question_id }}</strong>
                    <span class="class-analysis__list-rate">{{ formatPercent(question.class_rate) }}</span>
                  </span>
                  <span
                    class="class-analysis__ratebar"
                    :class="`class-analysis__ratebar--${rateTone(question.class_rate)}`"
                    aria-hidden="true"
                  ><i :style="{ width: formatPercent(question.class_rate) }"></i></span>
                  <span class="class-analysis__list-stem">
                    <template v-if="question.subIndex !== null">第 {{ question.subIndex }} 小问</template>
                    <QuestionHtmlBlock
                      v-else-if="question.stem_summary"
                      :text="question.stem_summary"
                      inline
                      typeset-text
                    />
                    <template v-else>—</template>
                  </span>
                </button>
              </div>
            </aside>

            <div v-if="selectedQuestion" class="class-analysis__detail" :data-question-id="selectedQuestion.question_id">
              <header class="class-analysis__detail-header">
                <strong>{{ selectedQuestion.question_id }}</strong>
                <span>满分 {{ formatScore(selectedQuestion.max_score) }} 分</span>
                <span>得分率 {{ formatPercent(selectedQuestion.class_rate) }}</span>
                <span v-if="selectedStructure" class="class-analysis__structure">
                  <span
                    class="overview__structure"
                    role="img"
                    :aria-label="`满分 ${selectedStructure.full} 人，部分得分 ${selectedStructure.partial} 人，0 分 ${selectedStructure.zero} 人`"
                  >
                    <i class="is-full" :style="{ width: structureWidth(selectedStructure.full, selectedStructure.resolved) }" :title="`满分 ${selectedStructure.full} 人`"></i>
                    <i class="is-partial" :style="{ width: structureWidth(selectedStructure.partial, selectedStructure.resolved) }" :title="`部分得分 ${selectedStructure.partial} 人`"></i>
                    <i class="is-zero" :style="{ width: structureWidth(selectedStructure.zero, selectedStructure.resolved) }" :title="`0 分 ${selectedStructure.zero} 人`"></i>
                  </span>
                  <span class="class-analysis__structure-counts">
                    满分 {{ selectedStructure.full }} · 部分 {{ selectedStructure.partial }} · 0 分 {{ selectedStructure.zero }}
                  </span>
                </span>
              </header>
              <div class="class-analysis__detail-columns">
                <ReviewAnswerPanel
                  v-if="props.sessionId !== null"
                  :key="selectedQuestion.question_id"
                  :session-id="props.sessionId"
                  :question-id="selectedQuestion.question_id"
                  embedded
                  class="class-analysis__embedded-panel"
                />
                <div class="class-analysis__outcome">
                  <section class="class-analysis__outcome-section" aria-label="失分情况">
                    <h3>失分情况</h3>
                    <div v-if="selectedQuestion.records.length" class="class-analysis__score-bands">
                      <div v-for="band in selectedQuestion.scoreBands" :key="band.key" class="class-analysis__score-band">
                        <button type="button" class="class-analysis__score-band-toggle" :aria-expanded="expandedBands.has(band.key)" :disabled="!band.records.length" :title="`${band.description}；占本题失分人数 ${band.share}%`" @click="toggleBand(band.key)">
                          <span class="class-analysis__score-band-arrow" :class="{ 'is-expanded': expandedBands.has(band.key) }" aria-hidden="true">▶</span>
                          <strong>{{ band.label }}</strong>
                          <span class="class-analysis__score-band-count">{{ band.records.length }} 人 · {{ band.share }}%</span>
                          <span class="class-analysis__score-band-track" aria-hidden="true"><i :style="{ width: `${band.share}%` }"></i></span>
                        </button>
                        <div v-if="expandedBands.has(band.key)" class="class-analysis__score-list">
                          <component
                            :is="reviewTargetFor(selectedQuestion.question_id, record) !== null ? 'button' : 'span'"
                            v-for="(record, index) in band.records"
                            :key="record.student_id ?? index"
                            :type="reviewTargetFor(selectedQuestion.question_id, record) !== null ? 'button' : undefined"
                            class="class-analysis__score-chip"
                            :class="{ 'class-analysis__score-chip--link': reviewTargetFor(selectedQuestion.question_id, record) !== null }"
                            :title="reviewTargetFor(selectedQuestion.question_id, record) !== null
                              ? '查看该生本题作答'
                              : [record.class_name, record.student_code].filter(Boolean).join(' · ')"
                            @click="openStudentReview(selectedQuestion.question_id, record)"
                          >
                            <span>{{ record.student_name }}<small v-if="!selectedClass && selectedQuestion.records.some(other => other.student_name === record.student_name && other.student_id !== record.student_id)">（{{ record.class_name }} {{ record.student_code }}）</small></span>
                            <b>{{ formatScore(record.score) }} 分</b>
                          </component>
                        </div>
                      </div>
                      <span class="class-analysis__note">共 {{ selectedQuestion.records.length }} 人失分，档内按得分从高到低排列</span>
                    </div>
                    <span v-if="!selectedQuestion.records.length" class="class-analysis__note">无人失分</span>
                  </section>
                  <section class="class-analysis__outcome-section" aria-label="错因">
                    <h3>错因</h3>
                    <details v-for="section in selectedQuestion.causeSections" :key="section.kind" class="class-analysis__cause-category" :data-kind="section.kind" :open="section.kind === 'error' || section.kind === 'legacy'">
                      <summary>{{ section.label }}<span v-if="section.count !== null"> · {{ section.count }} 人</span><span v-if="section.categoryText" class="class-analysis__cause-categories">（{{ section.categoryText }}）</span></summary>
                      <ol class="class-analysis__causes">
                        <li v-for="(cause, causeIndex) in (expandedCauses.has(section.key) ? section.causes : section.causes.slice(0, 5))" :key="cause.reason">
                          <details
                            v-if="cause.evidence?.length"
                            class="class-analysis__cause-detail"
                            @toggle="syncDetailOpen($event, `${section.key}#${causeIndex}`)"
                          >
                            <summary>
                              <span v-if="cause.category" class="class-analysis__cause-tag" data-testid="cause-category">{{ cause.category }}</span>
                              <span v-if="cause.pattern_status === 'candidate'" class="class-analysis__cause-tag class-analysis__cause-tag--new">新错法</span>
                              <span v-if="cause.teacher_edited" class="class-analysis__cause-tag class-analysis__cause-tag--edited" data-testid="cause-teacher-edited">老师改过</span>
                              <QuestionHtmlBlock :text="cause.reason" inline typeset-text /><b>{{ cause.count }} 人</b>
                              <button
                                v-if="canEditPattern(cause)"
                                type="button"
                                class="class-analysis__link class-analysis__cause-confirm"
                                data-testid="cause-edit"
                                @click.stop.prevent="openPatternEdit(selectedQuestion, cause)"
                              >修改</button>
                            </summary>
                            <template v-if="openDetails.has(`${section.key}#${causeIndex}`)">
                            <div v-for="(variant, variantIndex) in (cause.manifestations ?? [{ description: '', source_question_id: null, evidence: cause.evidence }])" :key="variantIndex" class="class-analysis__cause-manifestation">
                              <p v-if="variant.description" class="class-analysis__cause-observation">本题表现：<QuestionHtmlBlock :text="variant.description" inline typeset-text /></p>
                              <p v-if="variant.source_question_id" class="class-analysis__note">延续自 {{ variant.source_question_id }}</p>
                              <div v-for="(item, index) in variant.evidence" :key="index" class="class-analysis__cause-evidence">
                                <p v-if="item.student_answer"><strong>作答记录：</strong><QuestionHtmlBlock :text="item.student_answer" inline typeset-text /></p>
                                <p v-else-if="cause.kind" class="class-analysis__note">未保存作答文字，具体原因以已有证据为限。</p>
                                <p v-if="item.evidence_steps?.length"><strong>已有步骤记录：</strong><QuestionHtmlBlock :text="item.evidence_steps.join('；')" inline typeset-text /></p>
                                <p><strong v-if="cause.kind">原始批语：</strong><QuestionHtmlBlock :text="item.text" inline typeset-text /></p>
                                <p v-for="previous in item.previous_answers" :key="previous.question_id" class="class-analysis__note">{{ previous.question_id }} 作答：<QuestionHtmlBlock :text="previous.student_answer || '未记录作答文字'" inline typeset-text /></p>
                                <small>{{ causeStudents(selectedQuestion, item.student_ids) }}</small>
                              </div>
                            </div>
                            </template>
                          </details>
                          <template v-else>
                            <span v-if="cause.category" class="class-analysis__cause-tag">{{ cause.category }}</span>
                            <span v-if="cause.teacher_edited" class="class-analysis__cause-tag class-analysis__cause-tag--edited" data-testid="cause-teacher-edited">老师改过</span>
                            <QuestionHtmlBlock :text="cause.reason" inline typeset-text /><b>{{ cause.count }} 人</b>
                            <button
                              v-if="canEditPattern(cause)"
                              type="button"
                              class="class-analysis__link class-analysis__cause-confirm"
                              data-testid="cause-edit"
                              @click="openPatternEdit(selectedQuestion, cause)"
                            >修改</button>
                          </template>
                        </li>
                      </ol>
                      <button v-if="section.causes.length > 5" type="button" class="class-analysis__link class-analysis__causes-toggle" :aria-expanded="expandedCauses.has(section.key)" @click="toggleCauses(section.key)">
                        {{ expandedCauses.has(section.key) ? '收起错因' : `展开其余 ${section.causes.length - 5} 条错因` }} · 共 {{ section.causes.length }} 条
                      </button>
                    </details>
                    <span v-if="selectedQuestion.causes_outdated" class="class-analysis__note" data-testid="causes-outdated">旧版整理 · 待升级（暂无错误大类）</span>
                    <span v-if="!selectedQuestion.causeSections.length" class="class-analysis__note">{{ selectedQuestion.records.length ? (selectedQuestion.causes_grouped ? '没有可确认的共同错因，见下方原始证据' : '未记录具体错因') : '—' }}</span>
                    <details
                      v-if="!selectedQuestion.structuredCauses && selectedQuestion.cause_review?.uncertain.length"
                      class="class-analysis__cause-review"
                      @toggle="syncDetailOpen($event, `${selectedQuestion.question_id}:uncertain`)"
                    >
                      <summary>错因待明确 · {{ evidenceStudentCount(selectedQuestion.cause_review.uncertain) }} 人</summary>
                      <template v-if="openDetails.has(`${selectedQuestion.question_id}:uncertain`)">
                        <div v-for="(item, index) in selectedQuestion.cause_review.uncertain" :key="index" class="class-analysis__cause-evidence">
                          <p v-if="item.student_answer">作答记录：<QuestionHtmlBlock :text="item.student_answer" inline typeset-text /></p>
                          <p><QuestionHtmlBlock :text="item.text" inline typeset-text /></p><small>{{ causeStudents(selectedQuestion, item.student_ids) }}</small>
                        </div>
                      </template>
                    </details>
                    <details
                      v-if="selectedQuestion.cause_review?.positive.length"
                      class="class-analysis__cause-review"
                      @toggle="syncDetailOpen($event, `${selectedQuestion.question_id}:positive`)"
                    >
                      <summary>未归为错因的批语 · {{ evidenceStudentCount(selectedQuestion.cause_review.positive) }} 人</summary>
                      <template v-if="openDetails.has(`${selectedQuestion.question_id}:positive`)">
                        <div v-for="(item, index) in selectedQuestion.cause_review.positive" :key="index" class="class-analysis__cause-evidence">
                          <p v-if="item.student_answer">作答记录：<QuestionHtmlBlock :text="item.student_answer" inline typeset-text /></p>
                          <p><QuestionHtmlBlock :text="item.text" inline typeset-text /></p><small>{{ causeStudents(selectedQuestion, item.student_ids) }}</small>
                        </div>
                      </template>
                    </details>
                  </section>
                </div>
              </div>
            </div>
          </div>
        </template>
      </template>
    </section>

    <ClassAnalysisGenerateConfirm
      v-if="confirmOpen"
      kind="causes"
      :loading="confirmLoading"
      :binding="confirmBinding"
      :error="confirmError"
      :submitting="regenerating"
      :call-count="causeAnalysis?.pending_questions ?? data?.questions.filter((q) => q.records.length).length ?? 0"
      @close="closeConfirm"
      @confirm="confirmRegenerate"
    />

  <DialogRoot :open="patternEdit !== null" @update:open="!$event && (patternEdit = null)">
    <DialogPortal>
      <DialogOverlay class="class-analysis__overlay" />
      <DialogContent class="class-analysis__preview" data-testid="cause-edit-dialog" :aria-describedby="undefined">
        <div class="class-analysis__dialog-heading">
          <DialogTitle>{{ patternEdit?.questionId }} · 修改错法</DialogTitle>
          <DialogClose class="class-analysis__link" aria-label="关闭修改对话框">关闭</DialogClose>
        </div>
        <template v-if="patternEdit">
          <p class="class-analysis__note">错法已自动整理并回挂题库；这里可以按需修改名称和大类，修改后同题考试会沿用新名称。</p>
          <label class="class-analysis__field">
            <span>错法名称</span>
            <input v-model="patternEdit.newReason" class="class-analysis__input" data-testid="cause-edit-reason" maxlength="40" />
          </label>
          <label class="class-analysis__field">
            <span>错误大类</span>
            <select v-model="patternEdit.category" class="class-analysis__input" data-testid="cause-edit-category">
              <option v-for="category in KIND_CATEGORIES[patternEdit.kind] ?? []" :key="category" :value="category">{{ category }}</option>
            </select>
          </label>
          <p v-if="patternEditError" class="class-analysis__error" role="alert">{{ patternEditError }}</p>
          <div class="class-analysis__dialog-actions">
            <button type="button" class="class-analysis__link" :disabled="patternEditLoading" @click="patternEdit = null">取消</button>
            <AppButton variant="primary" :loading="patternEditLoading" loading-label="正在保存" :disabled="!patternEdit.newReason.trim()" data-testid="cause-edit-submit" @click="savePatternEdit">
              保存修改
            </AppButton>
          </div>
        </template>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
