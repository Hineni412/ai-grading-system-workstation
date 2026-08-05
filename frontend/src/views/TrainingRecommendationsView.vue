<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type { TrainingVariant } from '../api/exports'
import type { GraphQueryInput } from '../api/graph'
import type { JobResponse } from '../api/jobs'
import { fetchStudents, type StudentSummary } from '../api/students'
import type {
  TrainingExamScopeRequest,
  TrainingDiagnosis,
  TrainingPlanItem,
  TrainingStage,
  TrainingStudentScopeRequest,
  TrainingWeakPoint,
} from '../api/training'
import PersonalizedRecommendationDraft from '../components/training/PersonalizedRecommendationDraft.vue'
import EvidenceScopeFilters from '../components/evidence/EvidenceScopeFilters.vue'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import { useTrainingStore } from '../stores/training'
import { loadEvidenceScope, saveEvidenceScope } from '../features/evidence-scope/session'

type ReferenceState = 'loading' | 'ready' | 'error'
type ExportMode = 'bundle' | 'variant'

const router = useRouter()
const sessionStore = useSessionStore()
const training = useTrainingStore()
const jobs = useJobStore()

const students = ref<StudentSummary[]>([])
const referenceState = ref<ReferenceState>('loading')
const selectedWeakKey = ref('')
const selectedGroupKey = ref('')
const expandedKnowledgeKeys = ref<string[]>([])
const showNoEvidenceStudents = ref(false)
const showNoEvidenceKnowledge = ref(false)
const variantMode = ref<'individual' | 'auto_group'>('individual')
const questionCount = ref(10)
const excludeCurrentOriginals = ref(true)
const directRatio = ref(60)
const prerequisiteRatio = ref(30)
const transferRatio = ref(10)
const exportMode = ref<ExportMode>('bundle')
const exportFormat = ref<'docx' | 'markdown'>('docx')
const exportAudience = ref<'student' | 'teacher'>('student')
const exportVariantId = ref<number | null>(null)
const workflowStage = ref<'diagnosis' | 'draft' | 'wps' | 'scan'>('diagnosis')
let studentsController: AbortController | null = null

const availableSessions = computed(() => sessionStore.sessions.filter((item) => !item.is_deleted))

const selectedWeakPoint = computed<{
  studentName: string
  weak: TrainingWeakPoint
} | null>(() => {
  for (const student of training.diagnosis?.students ?? []) {
    const weak = student.weak_points.find(
      (candidate) => weakKey(student.student_id, candidate.knowledge_key) === selectedWeakKey.value,
    )
    if (weak) return { studentName: student.student_name, weak }
  }
  return null
})

const matrixStudents = computed(() => (
  training.diagnosis?.students.filter((student) => student.weak_points.length > 0) ?? []
))
const hiddenStudentCount = computed(() => (
  (training.diagnosis?.students.length ?? 0) - matrixStudents.value.length
))
const scoreSourceSummary = computed(() => {
  const students = training.diagnosis?.students ?? []
  const current = students.filter((student) => student.score_rate_source === 'current_exam').length
  const historical = students.filter(
    (student) => student.score_rate_source === 'historical_fallback',
  ).length
  const none = students.length - current - historical
  return `有知识证据 ${matrixStudents.value.length} / 所选 ${students.length} 人 · 本次成绩 ${current} 人 · 历史参考 ${historical} 人 · 无成绩 ${none} 人`
})
const displayMatrixStudents = computed(() => (
  showNoEvidenceStudents.value ? training.diagnosis?.students ?? [] : matrixStudents.value
))

const knowledgeColumns = computed(() => {
  const columns = new Map<string, {
    key: string
    label: string
    parentLabel: string | null
  }>()
  for (const student of matrixStudents.value) {
    for (const weak of student.weak_points) {
      if (!columns.has(weak.knowledge_key)) {
        columns.set(weak.knowledge_key, {
          key: weak.knowledge_key,
          label: weak.knowledge_point,
          parentLabel: weak.parent_knowledge_point ?? null,
        })
      }
    }
  }
  if (showNoEvidenceKnowledge.value) {
    for (const item of training.diagnosis?.knowledge_catalog ?? []) {
      if (!columns.has(item.knowledge_key)) {
        columns.set(item.knowledge_key, {
          key: item.knowledge_key,
          label: item.knowledge_point,
          parentLabel: item.parent_knowledge_point ?? null,
        })
      }
    }
  }
  const parentByKey = new Map((training.diagnosis?.knowledge_catalog ?? []).map((item) => [
    item.knowledge_key,
    item.parent_knowledge_key ?? null,
  ]))
  const belongsToFocus = (key: string): boolean => {
    if (!selectedGroupKey.value) return false
    if (key === selectedGroupKey.value) return true
    let parent = parentByKey.get(key) ?? null
    while (parent) {
      if (parent === selectedGroupKey.value) return true
      if (!expandedKnowledgeKeys.value.includes(parent)) return false
      parent = parentByKey.get(parent) ?? null
    }
    return false
  }
  return [...columns.values()].filter((item) => belongsToFocus(item.key)).sort((left, right) => (
    (left.parentLabel ?? left.label).localeCompare(right.parentLabel ?? right.label, 'zh-CN')
    || left.label.localeCompare(right.label, 'zh-CN')
  ))
})

const groupSummaryPoints = computed(() => (
  [...(training.diagnosis?.group_weak_points ?? [])]
    .filter((weak) => weak.hierarchy_kind === 'parent_summary' || weak.hierarchy_kind === 'root')
    .sort((left, right) => left.mastery - right.mastery || left.knowledge_point.localeCompare(right.knowledge_point, 'zh-CN'))
))

function selectGroup(knowledgeKey: string): void {
  selectedGroupKey.value = knowledgeKey
  expandedKnowledgeKeys.value = [knowledgeKey]
  selectedWeakKey.value = ''
}

function toggleKnowledgeExpansion(knowledgeKey: string): void {
  expandedKnowledgeKeys.value = expandedKnowledgeKeys.value.includes(knowledgeKey)
    ? expandedKnowledgeKeys.value.filter((item) => item !== knowledgeKey)
    : [...expandedKnowledgeKeys.value, knowledgeKey]
}

const groupWeakPoints = computed(() => {
  return new Map((training.diagnosis?.group_weak_points ?? []).map((weak) => [
    weak.knowledge_key,
    {
      mastery: weak.mastery,
      evidenceCount: weak.evidence_count,
      contributorCount: matrixStudents.value.filter((student) => (
        student.weak_points.some((point) => point.knowledge_key === weak.knowledge_key)
      )).length,
      effectiveWeight: weak.effective_weight ?? 0,
    },
  ]))
})

function scoreSourceLabel(student: TrainingDiagnosis['students'][number]): string {
  if (student.score_rate_source === 'historical_fallback') {
    const latest = student.historical_latest_exam_at
      ? new Date(student.historical_latest_exam_at).toLocaleDateString('zh-CN')
      : '时间未知'
    return `历史参考 · ${student.historical_exam_count ?? 0} 场 · 最近 ${latest}`
  }
  if (student.score_rate_source === 'current_exam') return '本次成绩'
  return '暂无成绩'
}

const selectedTask = computed(
  () => training.selectedTask ?? training.confirmedTask,
)

const selectedTaskStudentNames = computed(() => {
  const snapshot = selectedTask.value?.diagnosis_snapshot
  const profiles = snapshot && Array.isArray(snapshot.students) ? snapshot.students : []
  return new Map(profiles.flatMap((profile) => {
    if (
      typeof profile !== 'object'
      || profile === null
      || Array.isArray(profile)
    ) return []
    const studentId = profile.student_id
    const studentName = profile.student_name
    return typeof studentId === 'string' && typeof studentName === 'string'
      ? [[studentId, studentName] as const]
      : []
  }))
})

const trainingJobs = computed(() => Object.values(jobs.jobs)
  .filter((job) => job.job_type === 'training_export')
  .sort((left, right) => right.id - left.id))

const coverageText = computed(() => {
  const coverage = training.diagnosis?.coverage
  if (!coverage) return ''
  return `已覆盖 ${coverage.covered_items} / ${coverage.total_items} 个评分题`
})

const coverageEntries = computed(() => Object.entries(
  training.diagnosis?.coverage.missing_items ?? {},
).sort(([left], [right]) => left.localeCompare(right, 'zh-CN')))

const stageRatioTotal = computed<number | null>(() => {
  const values = [directRatio.value, prerequisiteRatio.value, transferRatio.value]
  if (values.some((value) => !Number.isFinite(value) || value < 0 || value > 100)) {
    return null
  }
  return values.reduce((total, value) => total + value, 0)
})

const selectedStudentCount = computed(() => {
  if (training.diagnosis) return training.diagnosis.scope.matched_student_count
    ?? training.diagnosis.students.length
  if (training.studentScope.mode === 'all') return students.value.length
  if (training.studentScope.mode === 'selected') return training.studentScope.studentIds.length
  const selectedClasses = new Set(training.studentScope.classIds)
  return students.value.filter((student) => (
    !selectedClasses.size || (student.class_name && selectedClasses.has(student.class_name))
  )).length
})

const selectedExamCount = computed(() => {
  if (training.examScope.mode === 'cross_exam') return availableSessions.value.length
  return training.examScope.sessionIds.length
})

const hasAnalyzableScope = computed(
  () => selectedStudentCount.value > 0
    && selectedExamCount.value > 0,
)

const canAnalyze = computed(
  () => hasAnalyzableScope.value && training.analysisState !== 'loading',
)

const canConfirm = computed(
  () => training.hasCurrentPlan
    && training.planState === 'ready'
    && training.confirmState !== 'submitting'
    && training.confirmState !== 'conflict',
)

const canPreview = computed(
  () => training.hasCurrentDiagnosis
    && training.planState !== 'loading'
    && stageRatioTotal.value === 100,
)

const personalizedScope = computed<TrainingStudentScopeRequest>(() => ({
  mode: training.studentScope.mode,
  student_ids: [...training.studentScope.studentIds],
  ...(training.studentScope.classId
    ? { class_id: training.studentScope.classId }
    : {}),
  ...(training.studentScope.classIds.length
    ? { class_ids: [...training.studentScope.classIds] }
    : {}),
  score_rate_min: training.studentScope.scoreRateMin,
  score_rate_max: training.studentScope.scoreRateMax,
  include_student_ids: [...training.studentScope.includeStudentIds],
  exclude_student_ids: [...training.studentScope.excludeStudentIds],
  use_historical_fallback: training.studentScope.useHistoricalFallback,
}))

const activeEvidenceQuery = computed<GraphQueryInput | null>(() => {
  const sessionIds = training.examScope.sessionIds
  if (training.examScope.mode !== 'cross_exam' && !sessionIds.length) return null
  return {
    scope: {
      mode: training.studentScope.mode,
      ...(training.studentScope.classId ? { class_id: training.studentScope.classId } : {}),
      ...(training.studentScope.classIds.length
        ? { class_ids: [...training.studentScope.classIds] }
        : {}),
      ...(training.studentScope.studentIds.length
        ? { student_ids: [...training.studentScope.studentIds] }
        : {}),
      score_rate_min: training.studentScope.scoreRateMin,
      score_rate_max: training.studentScope.scoreRateMax,
      include_student_ids: [...training.studentScope.includeStudentIds],
      exclude_student_ids: [...training.studentScope.excludeStudentIds],
      use_historical_fallback: training.studentScope.useHistoricalFallback,
    },
    exam_scope: training.examScope.mode === 'cross_exam'
      ? { mode: 'cross_exam' }
      : training.examScope.mode === 'current'
        ? { mode: 'current', session_ids: [sessionIds[0]!] }
        : { mode: 'manual', session_ids: [...sessionIds] },
  }
})

async function applyEvidenceScope(query: GraphQueryInput): Promise<void> {
  saveEvidenceScope(query)
  training.setStudentScope({
    mode: query.scope.mode,
    studentIds: [...(query.scope.student_ids ?? [])],
    classId: query.scope.class_id ?? '',
    classIds: [...(query.scope.class_ids ?? (query.scope.class_id ? [query.scope.class_id] : []))],
    scoreRateMin: query.scope.score_rate_min ?? null,
    scoreRateMax: query.scope.score_rate_max ?? null,
    includeStudentIds: [...(query.scope.include_student_ids ?? [])],
    excludeStudentIds: [...(query.scope.exclude_student_ids ?? [])],
    useHistoricalFallback: query.scope.use_historical_fallback !== false,
  })
  training.setExamScope({
    mode: query.exam_scope.mode,
    sessionIds: query.exam_scope.mode === 'cross_exam'
      ? availableSessions.value.map((item) => item.id)
      : [...query.exam_scope.session_ids],
  })
  await analyze()
}

const personalizedExamScope = computed<TrainingExamScopeRequest>(() => ({
  mode: training.examScope.mode,
  session_ids: [...training.examScope.sessionIds],
}))

function weakKey(studentId: string, knowledgeKey: string): string {
  return `${studentId}\u0000${knowledgeKey}`
}

function findWeak(studentId: string, knowledgeKey: string): TrainingWeakPoint | null {
  return training.diagnosis?.students
    .find((student) => student.student_id === studentId)
    ?.weak_points.find((weak) => weak.knowledge_key === knowledgeKey) ?? null
}

function masteryLabel(mastery: number): string {
  return `${Math.round(mastery * 100)}%`
}

function stageLabel(stage: TrainingStage): string {
  return {
    direct: '针对训练',
    prerequisite: '基础巩固',
    transfer: '提升应用',
  }[stage]
}

function localizedWarning(warning: string): string {
  return warning
    .replace(/\bprerequisite\b\s*/g, '基础巩固')
    .replace(/\btransfer\b\s*/g, '提升应用')
    .replace(/\bdirect\b\s*/g, '针对训练')
}

function statusLabel(status: JobResponse['status']): string {
  return {
    queued: '排队中',
    running: '生成中',
    paused: '已暂停',
    succeeded: '可下载',
    failed: '生成失败',
    cancelled: '已取消',
  }[status]
}

function taskStatusLabel(status: NonNullable<typeof selectedTask.value>['status']): string {
  return {
    draft: '草稿',
    ready: '可出件',
    exporting: '生成材料中',
    completed: '已完成',
    cancelled: '已取消',
    failed: '失败',
  }[status]
}

function variantStudentNames(variant: TrainingVariant): string {
  const names = variant.students
    .map((student) => {
      if (typeof student.student_name === 'string') return student.student_name.trim()
      const studentId = typeof student.student_id === 'string' ? student.student_id : ''
      return selectedTaskStudentNames.value.get(studentId) ?? ''
    })
    .filter(Boolean)
  return names.length ? names.join('、') : `${variant.students.length} 名学生`
}

function nestedRecord(
  item: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const value = item[key]
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function firstText(
  records: Record<string, unknown>[],
  keys: string[],
): string {
  for (const record of records) {
    for (const key of keys) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return ''
}

function variantItemLabel(item: Record<string, unknown>, index: number): string {
  const recommendation = nestedRecord(item, 'recommendation_snapshot')
  const question = nestedRecord(item, 'question_snapshot')
  const questionNumber = firstText(
    [item, question],
    ['question_number'],
  ) || String(index + 1)
  const knowledgePoint = firstText(
    [item, recommendation],
    ['knowledge_point'],
  ) || '未命名知识点'
  return `题 ${questionNumber} · ${knowledgePoint}`
}

function variantItemReason(item: Record<string, unknown>): string {
  return firstText(
    [item, nestedRecord(item, 'recommendation_snapshot')],
    ['reason'],
  )
}

async function loadStudents(): Promise<void> {
  studentsController?.abort()
  const controller = new AbortController()
  studentsController = controller
  referenceState.value = 'loading'
  try {
    students.value = await fetchStudents(controller.signal)
    referenceState.value = 'ready'
    if (sessionStore.selectedSessionId && training.analysisState === 'idle') {
      const savedQuery = loadEvidenceScope()
      const knownSessionIds = new Set(availableSessions.value.map((item) => item.id))
      const compatibleSavedQuery = savedQuery?.exam_scope.mode === 'cross_exam'
        || savedQuery?.exam_scope.session_ids.every((id) => knownSessionIds.has(id))
        ? savedQuery
        : null
      await applyEvidenceScope(compatibleSavedQuery ?? {
        scope: {
          mode: 'all',
          include_student_ids: [],
          exclude_student_ids: [],
          use_historical_fallback: true,
        },
        exam_scope: {
          mode: 'current',
          session_ids: [sessionStore.selectedSessionId],
        },
      })
    }
  } catch {
    if (controller.signal.aborted) return
    students.value = []
    referenceState.value = 'error'
  } finally {
    if (studentsController === controller) studentsController = null
  }
}

async function analyze(): Promise<void> {
  try {
    await training.analyze()
    selectedWeakKey.value = ''
    selectedGroupKey.value = ''
    expandedKnowledgeKeys.value = []
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

async function previewPlan(): Promise<void> {
  try {
    await training.previewPlan({
      variantMode: variantMode.value,
      questionCount: questionCount.value,
      stageRatios: {
        direct: directRatio.value / 100,
        prerequisite: prerequisiteRatio.value / 100,
        transfer: transferRatio.value / 100,
      },
      excludeCurrentExamOriginals: excludeCurrentOriginals.value,
    })
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

async function confirmPlan(): Promise<void> {
  try {
    await training.confirmPlan()
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

async function selectTask(taskId: number): Promise<void> {
  try {
    const detail = await training.selectTask(taskId)
    exportVariantId.value = detail?.variants[0]?.id ?? null
  } catch {
    // The existing history remains visible when detail refresh fails.
  }
}

async function submitExport(): Promise<void> {
  const task = selectedTask.value
  if (!task) return
  try {
    if (exportMode.value === 'variant') {
      if (!exportVariantId.value) return
      await training.submitExport({
        taskId: task.id,
        variant_id: exportVariantId.value,
        format: exportFormat.value,
        audience: exportAudience.value,
      })
    } else {
      await training.submitExport({
        taskId: task.id,
        format: exportFormat.value,
      })
    }
  } catch {
    // The action can be retried explicitly without an automatic write replay.
  }
}

async function retryExport(jobId: number): Promise<void> {
  try {
    await training.retryExport(jobId)
  } catch {
    // Preserve the failed job so the teacher can retry again.
  }
}

async function cancelExport(jobId: number): Promise<void> {
  await jobs.cancel(jobId)
}

async function downloadExport(jobId: number): Promise<void> {
  try {
    const file = await training.download(jobId)
    const url = URL.createObjectURL(file.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = file.filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    // The job stays in the ledger and remains recoverable.
  }
}

function itemsForStage(items: TrainingPlanItem[], stage: TrainingStage): TrainingPlanItem[] {
  return items.filter((item) => item.stage === stage)
}

watch(
  [
    variantMode,
    questionCount,
    excludeCurrentOriginals,
    directRatio,
    prerequisiteRatio,
    transferRatio,
  ],
  () => {
    if (
      training.plan
      || training.planState !== 'idle'
      || training.confirmState !== 'idle'
    ) {
      training.invalidatePlan()
    }
  },
)

watch(
  selectedTask,
  (task) => {
    exportVariantId.value = task?.variants[0]?.id ?? null
  },
  { immediate: true },
)

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    const active = activeEvidenceQuery.value
    if (
      sessionId === null
      || !active
      || active.exam_scope.mode !== 'current'
      || active.exam_scope.session_ids[0] === sessionId
    ) return
    void applyEvidenceScope({
      ...active,
      exam_scope: { mode: 'current', session_ids: [sessionId] },
    })
  },
)

onMounted(() => {
  void loadStudents()
  void training.loadTasks().catch(() => undefined)
  void jobs.initialize().catch(() => undefined)
})

onBeforeUnmount(() => {
  studentsController?.abort()
})
</script>

<template>
  <section class="training-workspace" aria-labelledby="training-title">
    <header class="training-heading">
      <div>
        <p class="training-eyebrow">证据驱动的练习安排</p>
        <h1 id="training-title">训练推荐</h1>
        <p>按当前题库的精确知识点标签核对薄弱证据、推荐原因和训练材料。</p>
      </div>
      <div class="training-heading-scope">
        <EvidenceScopeFilters
          v-if="referenceState !== 'error'"
          :sessions="availableSessions"
          :current-session-id="sessionStore.selectedSessionId"
          :students="students"
          :model-value="activeEvidenceQuery"
          :applying="training.analysisState === 'loading'"
          :score-profiles="training.diagnosis?.scope.student_score_profiles ?? {}"
          @apply="applyEvidenceScope"
        />
        <div class="training-basis" aria-label="推荐口径">
          <strong>当前范围 {{ training.diagnosis?.scope.matched_student_count ?? selectedStudentCount }} 人</strong>
          <span>精确知识标签 · 本地证据读取 · 不调用外部 AI</span>
        </div>
      </div>
    </header>

    <div v-if="referenceState === 'error'" class="training-feedback is-error" role="alert">
      学生名单暂时无法读取。
      <button type="button" class="training-link" @click="loadStudents">重新读取</button>
    </div>
    <div v-if="training.analysisState === 'error'" class="training-action-row">
      <p>新范围读取失败，已保留上一次成功结果。</p>
      <button type="button" class="training-button is-secondary" data-testid="retry-training-analysis" :disabled="!canAnalyze" @click="analyze">重新读取诊断</button>
    </div>

    <p v-if="training.errorMessage" class="training-feedback is-error" role="alert">
      {{ training.errorMessage }}
    </p>
    <p v-if="training.actionMessage" class="training-feedback is-success" role="status">
      {{ training.actionMessage }}
    </p>

    <div class="training-main-grid" :class="`is-${workflowStage}`">
      <main class="training-primary">
        <section v-if="workflowStage === 'diagnosis'" class="training-ledger" aria-labelledby="diagnosis-title">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">02 · 核对诊断</p>
              <h2 id="diagnosis-title">诊断热力账本</h2>
            </div>
            <span v-if="coverageText">{{ coverageText }}</span>
          </div>

          <div v-if="training.analysisState === 'loading'" class="training-empty" role="status">
            正在读取评分证据和当前精确标签…
          </div>
          <div v-else-if="training.analysisState === 'idle'" class="training-empty">
            请选择有效的考试、班级和学生范围；完成后这里会自动显示热力图。
          </div>
          <div v-else-if="training.analysisState === 'empty'" class="training-empty">
            当前范围没有可生成推荐的精确标签证据。请先到题库核对来源题和知识点标签。
          </div>

          <template v-if="training.diagnosis">
            <p class="training-side-note">{{ scoreSourceSummary }}</p>
            <div v-if="groupSummaryPoints.length" class="training-group-summary" aria-label="群体薄弱知识点摘要">
              <button
                v-for="weak in groupSummaryPoints"
                :key="weak.knowledge_key"
                type="button"
                :class="{ 'is-selected': selectedGroupKey === weak.knowledge_key }"
                @click="selectGroup(weak.knowledge_key)"
              >
                <span>{{ weak.hierarchy_kind === 'parent_summary' ? '核心知识点' : '知识点' }}</span>
                <strong>{{ weak.knowledge_point }}</strong>
                <b>{{ masteryLabel(weak.mastery) }}</b>
                <small>{{ weak.evidence_count }} 条唯一证据 · 有效权重 {{ (weak.effective_weight ?? 0).toFixed(2) }}</small>
              </button>
            </div>
            <p v-if="groupSummaryPoints.length && !selectedGroupKey" class="training-empty is-compact">
              选择一个核心知识点，再展开贡献学生、历史来源和完整分层矩阵。
            </p>
            <div v-if="hiddenStudentCount > 0" class="training-action-row">
              <p>已默认隐藏 {{ hiddenStudentCount }} 名完全无知识证据学生；这些学生不进入群体分母，也不会默认出卷。</p>
              <button type="button" class="training-link" @click="showNoEvidenceStudents = !showNoEvidenceStudents">
                {{ showNoEvidenceStudents ? '隐藏无证据学生' : '显示无证据学生' }}
              </button>
            </div>
            <div v-if="selectedGroupKey" class="training-matrix-controls">
              <button type="button" class="training-link" @click="showNoEvidenceKnowledge = !showNoEvidenceKnowledge">
                {{ showNoEvidenceKnowledge ? '隐藏无证据知识点' : '显示无证据知识点' }}
              </button>
              <span>当前展开 {{ knowledgeColumns.length }} 列；父级与子级使用唯一证据身份去重。</span>
            </div>
            <div v-if="matrixStudents.length && selectedGroupKey" class="training-ledger-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col" rowspan="2">学生</th>
                    <th :colspan="Math.max(knowledgeColumns.length, 1)" scope="colgroup">
                      {{ groupSummaryPoints.find((item) => item.knowledge_key === selectedGroupKey)?.knowledge_point ?? '当前知识点' }}
                    </th>
                  </tr>
                  <tr>
                    <th v-for="column in knowledgeColumns" :key="column.key" scope="col">
                      <small v-if="column.parentLabel">{{ column.parentLabel }} › 子知识点</small>
                      <strong>{{ column.label }}</strong>
                      <button type="button" class="training-column-expand" @click="toggleKnowledgeExpansion(column.key)">
                        {{ expandedKnowledgeKeys.includes(column.key) ? '收起下级' : '展开下级' }}
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="matrixStudents.length > 1" class="training-group-row">
                    <th scope="row">
                      <strong>群体加权结果</strong>
                      <span>仅统计有证据学生</span>
                    </th>
                    <td v-for="column in knowledgeColumns" :key="column.key">
                      <div v-if="groupWeakPoints.get(column.key)?.mastery != null" class="training-group-cell">
                        <strong>{{ masteryLabel(groupWeakPoints.get(column.key)!.mastery!) }}</strong>
                        <span>
                          {{ groupWeakPoints.get(column.key)!.contributorCount }} 人 ·
                          {{ groupWeakPoints.get(column.key)!.evidenceCount }} 条证据
                        </span>
                      </div>
                      <span v-else class="training-no-evidence">无群体证据</span>
                    </td>
                  </tr>
                  <tr v-for="student in displayMatrixStudents" :key="student.student_id">
                    <th scope="row">
                      <strong>{{ student.student_name }}</strong>
                      <span>{{ student.student_code }} · {{ scoreSourceLabel(student) }}</span>
                    </th>
                    <td v-for="column in knowledgeColumns" :key="column.key">
                      <button
                        v-if="findWeak(student.student_id, column.key)"
                        type="button"
                        class="training-heat-cell"
                        :class="{
                          'is-selected': selectedWeakKey === weakKey(
                            student.student_id,
                            findWeak(student.student_id, column.key)!.knowledge_key,
                          ),
                        }"
                        :data-testid="`weak-point-${student.student_id}-${findWeak(student.student_id, column.key)!.knowledge_key}`"
                        :aria-label="`${student.student_name}，${column.label}，掌握率 ${masteryLabel(findWeak(student.student_id, column.key)!.mastery)}，${findWeak(student.student_id, column.key)!.evidence_count} 条证据`"
                        @click="selectedWeakKey = weakKey(
                          student.student_id,
                          findWeak(student.student_id, column.key)!.knowledge_key,
                        )"
                      >
                        <strong>{{ masteryLabel(findWeak(student.student_id, column.key)!.mastery) }}</strong>
                        <span>· {{ findWeak(student.student_id, column.key)!.evidence_count }} 条证据</span>
                      </button>
                      <span v-else class="training-no-evidence">无证据</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <ul v-if="training.diagnosis.warnings.length" class="training-warning-list">
              <li v-for="warning in training.diagnosis.warnings" :key="warning">{{ warning }}</li>
            </ul>
            <div v-if="coverageEntries.length" class="training-coverage-gaps">
              <strong>需要补齐的评分题</strong>
              <ul>
                <li v-for="[questionId, reason] in coverageEntries" :key="questionId">
                  <span>{{ questionId }}：</span>{{ reason }}
                </li>
              </ul>
              <p>请到题库核对这些来源题的关联和知识点标签后，再重新分析。</p>
            </div>
          </template>
        </section>

        <section v-if="workflowStage === 'diagnosis'" class="training-evidence" aria-labelledby="evidence-title">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">证据—推荐联动</p>
              <h2 id="evidence-title">薄弱原因与评分证据</h2>
            </div>
          </div>
          <div v-if="!selectedWeakPoint" class="training-empty is-compact">
            选择账本中的掌握率，查看对应原始证据。
          </div>
          <template v-else>
            <p class="training-evidence-summary">
              <strong>{{ selectedWeakPoint.studentName }}</strong>
              的“{{ selectedWeakPoint.weak.knowledge_point }}”掌握率为
              {{ masteryLabel(selectedWeakPoint.weak.mastery) }}，来自
              {{ selectedWeakPoint.weak.exam_count }} 场考试。
            </p>
            <ul class="training-reason-list">
              <li v-for="reason in selectedWeakPoint.weak.actionable_reasons" :key="reason">
                {{ reason }}
              </li>
            </ul>
            <ol class="training-evidence-list">
              <li
                v-for="evidence in selectedWeakPoint.weak.source_question_refs"
                :key="`${evidence.session_id}-${evidence.question_id}`"
              >
                <span>{{ evidence.session_name }} · {{ evidence.question_id }} · {{ evidence.score_awarded }} / {{ evidence.full_score }}</span>
                <strong>{{ evidence.source_kind === 'historical_exam' ? '历史参考 · ' : '' }}{{ evidence.score_rate == null ? '得分率未知' : masteryLabel(evidence.score_rate) }}</strong>
              </li>
            </ol>
          </template>
        </section>

        <section class="training-plan" aria-labelledby="plan-title">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">03 · 形成计划</p>
              <h2 id="plan-title">推荐路径</h2>
            </div>
            <span>
              针对训练 {{ directRatio }}% · 基础巩固 {{ prerequisiteRatio }}% ·
              提升应用 {{ transferRatio }}%
            </span>
          </div>

          <PersonalizedRecommendationDraft
            v-if="training.diagnosis"
            :diagnosis="training.diagnosis"
            :scope="personalizedScope"
            :exam-scope="personalizedExamScope"
            :question-count="questionCount"
            :stage-ratios="{
              direct: directRatio / 100,
              prerequisite: prerequisiteRatio / 100,
              transfer: transferRatio / 100,
            }"
            :exclude-current-exam-originals="excludeCurrentOriginals"
            :disabled="stageRatioTotal !== 100"
            @stage-change="workflowStage = $event"
          />

          <details class="training-history-panel training-legacy-plan">
            <summary>
              <span>历史兼容：旧计划预览与任务确认</span>
              <strong>按需展开</strong>
            </summary>
            <section>
          <div class="training-plan-controls">
            <label>
              训练版本
              <select v-model="variantMode">
                <option value="individual">每人独立</option>
                <option value="auto_group">自动分组</option>
              </select>
            </label>
            <label>
              每个版本题量
              <select v-model.number="questionCount" data-testid="training-question-count">
                <option v-for="count in [8, 9, 10, 11, 12]" :key="count" :value="count">
                  {{ count }} 题
                </option>
              </select>
            </label>
            <fieldset class="training-ratio-controls">
              <legend>阶段比例（合计 100%）</legend>
              <label>
                针对训练
                <input
                  v-model.number="directRatio"
                  data-testid="training-ratio-direct"
                  type="number"
                  min="0"
                  max="100"
                  step="5"
                >
              </label>
              <label>
                基础巩固
                <input
                  v-model.number="prerequisiteRatio"
                  type="number"
                  min="0"
                  max="100"
                  step="5"
                >
              </label>
              <label>
                提升应用
                <input
                  v-model.number="transferRatio"
                  type="number"
                  min="0"
                  max="100"
                  step="5"
                >
              </label>
            </fieldset>
            <label class="training-checkbox">
              <input v-model="excludeCurrentOriginals" type="checkbox">
              排除当前考试原题
            </label>
            <button
              type="button"
              class="training-button is-secondary"
              data-testid="preview-training"
              :disabled="!canPreview"
              @click="previewPlan"
            >
              {{ training.planState === 'loading' ? '正在生成…' : '生成计划预览' }}
            </button>
          </div>
          <p v-if="stageRatioTotal !== 100" class="training-feedback is-warning" role="alert">
            三个阶段当前合计 {{ stageRatioTotal ?? '未完成' }}%，请调整为 100% 后再生成计划。
          </p>

          <div v-if="training.planState === 'empty'" class="training-empty">
            当前精确标签下没有可确认的推荐题。请到题库补充同一知识点候选题，或调整已明确的范围。
          </div>

          <div v-if="training.plan" class="training-plan-variants">
            <article
              v-for="variant in training.plan.plan.variants"
              :key="variant.variant_key"
              class="training-variant"
            >
              <header>
                <div>
                  <strong>{{ variant.variant_key }}</strong>
                  <span>{{ variant.student_ids.length }} 名学生 · {{ variant.items.length }} 道题</span>
                </div>
              </header>
              <div class="training-path">
                <section v-for="stage in (['direct', 'prerequisite', 'transfer'] as TrainingStage[])" :key="stage">
                  <h3>{{ stageLabel(stage) }}</h3>
                  <ol>
                    <li v-for="item in itemsForStage(variant.items, stage)" :key="item.question_id">
                      <span class="training-question-number">题 {{ item.question_number || item.item_order }}</span>
                      <div>
                        <strong>{{ item.knowledge_point }}</strong>
                        <p>{{ item.reason }}</p>
                      </div>
                    </li>
                  </ol>
                  <p v-if="!itemsForStage(variant.items, stage).length" class="training-stage-empty">
                    本阶段暂无精确标签候选题
                  </p>
                </section>
              </div>
              <ul v-if="variant.warnings.length" class="training-warning-list">
                <li v-for="warning in variant.warnings" :key="warning">{{ localizedWarning(warning) }}</li>
              </ul>
            </article>

            <div class="training-confirm-bar">
              <div>
                <strong>确认后将保存固定训练任务</strong>
                <span>版本校验可防止把已经变化的题目计划误保存。</span>
              </div>
              <button
                type="button"
                class="training-button is-primary"
                data-testid="confirm-training"
                :disabled="!canConfirm"
                @click="confirmPlan"
              >
                {{ training.confirmState === 'submitting' ? '正在保存…' : '确认并保存训练任务' }}
              </button>
            </div>
            <p v-if="training.confirmState === 'conflict'" class="training-feedback is-warning" role="alert">
              训练候选题已经变化。当前预览仅供核对，请重新分析并生成计划。
            </p>
          </div>
            </section>
          </details>
        </section>
      </main>

      <aside v-if="workflowStage === 'diagnosis'" class="training-sidebar" aria-labelledby="training-history-title">
        <nav class="training-stage-rail" aria-label="训练推荐步骤">
          <strong>当前工作</strong>
          <ol>
            <li :class="{ 'is-complete': selectedExamCount > 0 && selectedStudentCount > 0 }">确定范围</li>
            <li :class="{ 'is-complete': Boolean(training.diagnosis) }">核对证据</li>
            <li>草稿与 WPS 审核</li>
            <li>冻结、扫描与回流</li>
          </ol>
        </nav>

        <details class="training-history-panel">
          <summary>
            <span>最近训练任务</span>
            <strong>{{ training.tasks.length }}</strong>
          </summary>
          <section>
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">已保存</p>
              <h2 id="training-history-title">最近训练任务</h2>
            </div>
            <button type="button" class="training-link" @click="training.loadTasks()">
              刷新
            </button>
          </div>
          <p class="training-side-note">
            训练结果回流尚未启用；保存任务不会自动改写学生掌握度。
          </p>
          <div v-if="training.historyState === 'loading'" class="training-empty is-compact" role="status">
            正在读取任务记录…
          </div>
          <div v-else-if="!training.tasks.length" class="training-empty is-compact">
            暂无已保存训练任务。
          </div>
          <ul v-else class="training-task-list">
            <li v-for="task in training.tasks" :key="task.id">
              <button
                type="button"
                :data-testid="`task-${task.id}`"
                :class="{ 'is-selected': selectedTask?.id === task.id }"
                @click="selectTask(task.id)"
              >
                <strong>{{ task.task_code }}</strong>
                <span>{{ task.status }} · {{ new Date(task.updated_at).toLocaleString('zh-CN') }}</span>
              </button>
            </li>
          </ul>
          </section>
        </details>

        <details v-if="selectedTask" class="training-history-panel">
          <summary>
            <span>历史兼容：旧任务通用导出</span>
            <strong>{{ selectedTask.task_code }}</strong>
          </summary>
        <section class="training-export-panel">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">04 · 训练出件</p>
              <h2>生成训练材料</h2>
            </div>
          </div>
          <div class="training-task-detail" data-testid="training-task-detail">
            <p>
              <strong>{{ selectedTask.task_code }}</strong>
              · {{ taskStatusLabel(selectedTask.status) }}
            </p>
            <ul v-if="selectedTask.warnings.length" class="training-warning-list">
              <li v-for="warning in selectedTask.warnings" :key="warning">
                {{ localizedWarning(warning) }}
              </li>
            </ul>
            <article v-for="variant in selectedTask.variants" :key="variant.id">
              <header>
                <strong>{{ variant.variant_key }}</strong>
                <span>{{ variantStudentNames(variant) }}</span>
              </header>
              <ol>
                <li v-for="(item, index) in variant.items" :key="String(item.question_id ?? index)">
                  <strong>{{ variantItemLabel(item, index) }}</strong>
                  <span v-if="variantItemReason(item)">{{ variantItemReason(item) }}</span>
                </li>
              </ol>
            </article>
          </div>
          <label>
            出件范围
            <select v-model="exportMode" data-testid="training-export-mode">
              <option value="bundle">整任务文件包</option>
              <option value="variant">指定训练版本</option>
            </select>
          </label>
          <label v-if="exportMode === 'variant'">
            训练版本
            <select v-model="exportVariantId">
              <option v-for="variant in selectedTask.variants" :key="variant.id" :value="variant.id">
                {{ variant.variant_key }}
              </option>
            </select>
          </label>
          <label>
            格式
            <select v-model="exportFormat">
              <option value="docx">Word</option>
              <option value="markdown">Markdown</option>
            </select>
          </label>
          <label v-if="exportMode === 'variant'">
            内容版本
            <select v-model="exportAudience">
              <option value="student">学生版</option>
              <option value="teacher">教师版</option>
            </select>
          </label>
          <button
            type="button"
            class="training-button is-primary"
            data-testid="export-training-task"
            :disabled="Boolean(training.submittingExportKey) || (exportMode === 'variant' && !exportVariantId)"
            @click="submitExport"
          >
            {{ training.submittingExportKey ? '正在提交…' : '生成训练材料' }}
          </button>
          <button type="button" class="training-link" @click="router.push('/files')">
            进入文件中心查看完整登记簿
          </button>
        </section>
        </details>

        <section v-if="trainingJobs.length" class="training-jobs" aria-labelledby="training-jobs-title">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">本机恢复</p>
              <h2 id="training-jobs-title">活跃导出任务</h2>
            </div>
          </div>
          <ul>
            <li v-for="job in trainingJobs" :key="job.id">
              <div>
                <strong>{{ statusLabel(job.status) }}</strong>
                <span>任务 #{{ job.id }} · {{ Math.round(job.progress * 100) }}%</span>
              </div>
              <div class="training-job-actions">
                <button
                  v-if="job.status === 'queued' || job.status === 'running'"
                  type="button"
                  class="training-link"
                  @click="cancelExport(job.id)"
                >
                  取消
                </button>
                <button
                  v-if="job.status === 'failed' || job.status === 'cancelled'"
                  type="button"
                  class="training-link"
                  @click="retryExport(job.id)"
                >
                  重试
                </button>
                <button
                  v-if="job.status === 'succeeded'"
                  type="button"
                  class="training-link"
                  @click="downloadExport(job.id)"
                >
                  下载
                </button>
              </div>
            </li>
          </ul>
        </section>
      </aside>
    </div>
  </section>
</template>
