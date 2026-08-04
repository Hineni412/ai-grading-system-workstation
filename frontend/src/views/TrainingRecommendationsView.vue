<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type { TrainingVariant } from '../api/exports'
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
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import { useTrainingStore } from '../stores/training'

type ReferenceState = 'loading' | 'ready' | 'error'
type ExportMode = 'bundle' | 'variant'

const router = useRouter()
const sessionStore = useSessionStore()
const training = useTrainingStore()
const jobs = useJobStore()

const students = ref<StudentSummary[]>([])
const referenceState = ref<ReferenceState>('loading')
const examMode = ref<'current' | 'recent' | 'manual' | 'cross_exam'>('current')
const manualSessionIds = ref<number[]>([])
const studentMode = ref<'class' | 'score_band' | 'selected'>('class')
const selectedStudentIds = ref<string[]>([])
const selectedClass = ref('')
const studentSearch = ref('')
const scoreBand = ref<'low' | 'middle' | 'high'>('low')
const scoreBandMatchCount = ref<number | null>(null)
const selectedWeakKey = ref('')
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
const scopeEditorOpen = ref(true)
let studentsController: AbortController | null = null
let autoAnalyzeHandle: ReturnType<typeof setTimeout> | null = null
let autoAnalyzeGeneration = 0

const classes = computed(() => [...new Set(
  students.value
    .map((student) => student.class_name?.trim() ?? '')
    .filter(Boolean),
)].sort())

const availableSessions = computed(() => sessionStore.sessions.filter((item) => !item.is_deleted))
const recentSessions = computed(() => {
  const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000
  return availableSessions.value.filter((session) => {
    const timestamp = Date.parse(session.created_at ?? '')
    return Number.isFinite(timestamp) && timestamp >= cutoff
  })
})
const classStudents = computed(() => students.value.filter(
  (student) => !selectedClass.value || student.class_name === selectedClass.value,
))
const visibleStudents = computed(() => {
  const query = studentSearch.value.trim().toLocaleLowerCase('zh-CN')
  return classStudents.value.filter((student) => (
    !query || `${student.name} ${student.student_code}`.toLocaleLowerCase('zh-CN').includes(query)
  ))
})

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
  if (studentMode.value === 'selected') {
    const classIds = new Set(classStudents.value.map((student) => String(student.id)))
    return selectedStudentIds.value.filter((id) => classIds.has(id)).length
  }
  if (studentMode.value === 'score_band' && scoreBandMatchCount.value !== null) {
    return scoreBandMatchCount.value
  }
  return students.value.filter(
    (student) => student.class_name === selectedClass.value,
  ).length
})

const selectedExamCount = computed(() => {
  if (examMode.value === 'current') return sessionStore.selectedSessionId ? 1 : 0
  if (examMode.value === 'recent') return recentSessions.value.length
  if (examMode.value === 'manual') return manualSessionIds.value.length
  return availableSessions.value.length
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
}))

const personalizedExamScope = computed<TrainingExamScopeRequest>(() => ({
  mode: training.examScope.mode,
  session_ids: [...training.examScope.sessionIds],
}))

function weakKey(studentId: string, knowledgeKey: string): string {
  return `${studentId}\u0000${knowledgeKey}`
}

function findWeak(studentId: string, knowledgePoint: string): TrainingWeakPoint | null {
  return training.diagnosis?.students
    .find((student) => student.student_id === studentId)
    ?.weak_points.find((weak) => weak.knowledge_point === knowledgePoint) ?? null
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

function scopeStudentIds(): string[] {
  if (studentMode.value === 'selected') {
    const classIds = new Set(classStudents.value.map((student) => String(student.id)))
    return selectedStudentIds.value.filter((id) => classIds.has(id))
  }
  return students.value
    .filter((student) => student.class_name === selectedClass.value)
    .map((student) => String(student.id))
}

function scopeSessionIds(): number[] {
  if (examMode.value === 'current') {
    return sessionStore.selectedSessionId ? [sessionStore.selectedSessionId] : []
  }
  if (examMode.value === 'recent') return recentSessions.value.map((session) => session.id)
  if (examMode.value === 'manual') return manualSessionIds.value
  return availableSessions.value.map((session) => session.id)
}

function applyScope(studentIds = scopeStudentIds(), forceClass = false): void {
  const apiStudentMode = forceClass || studentMode.value === 'class'
    ? 'class'
    : 'selected'
  training.setStudentScope({
    mode: apiStudentMode,
    studentIds,
    classId: apiStudentMode === 'class' ? selectedClass.value : '',
  })
  training.setExamScope({
    mode: examMode.value === 'current'
      ? 'current'
      : examMode.value === 'cross_exam' ? 'cross_exam' : 'manual',
    sessionIds: scopeSessionIds(),
  })
  selectedWeakKey.value = ''
}

function toggleSession(sessionId: number): void {
  manualSessionIds.value = manualSessionIds.value.includes(sessionId)
    ? manualSessionIds.value.filter((id) => id !== sessionId)
    : [...manualSessionIds.value, sessionId]
}

function toggleStudent(studentId: string): void {
  selectedStudentIds.value = selectedStudentIds.value.includes(studentId)
    ? selectedStudentIds.value.filter((id) => id !== studentId)
    : [...selectedStudentIds.value, studentId]
}

function selectVisibleStudents(): void {
  selectedStudentIds.value = [...new Set([
    ...selectedStudentIds.value,
    ...visibleStudents.value.map((student) => String(student.id)),
  ])]
}

function clearSelectedStudents(): void {
  selectedStudentIds.value = []
}

function scoreBandMatches(value: number | null | undefined): boolean {
  if (value === null || value === undefined || !Number.isFinite(value)) return false
  if (scoreBand.value === 'low') return value < 40
  if (scoreBand.value === 'middle') return value >= 40 && value < 70
  return value >= 70
}

async function loadStudents(): Promise<void> {
  studentsController?.abort()
  const controller = new AbortController()
  studentsController = controller
  referenceState.value = 'loading'
  try {
    students.value = await fetchStudents(controller.signal)
    if (!selectedClass.value) selectedClass.value = classes.value[0] ?? ''
    referenceState.value = 'ready'
    scheduleAnalysis()
  } catch {
    if (controller.signal.aborted) return
    students.value = []
    referenceState.value = 'error'
  } finally {
    if (studentsController === controller) studentsController = null
  }
}

async function analyze(): Promise<void> {
  const request = ++autoAnalyzeGeneration
  try {
    let result: TrainingDiagnosis | null
    if (studentMode.value === 'score_band') {
      const classIds = students.value
        .filter((student) => student.class_name === selectedClass.value)
        .map((student) => String(student.id))
      applyScope(classIds, true)
      const classDiagnosis = await training.analyze()
      if (request !== autoAnalyzeGeneration || !classDiagnosis) return
      const matchedIds = classDiagnosis.students
        .filter((student) => scoreBandMatches(student.score_rate))
        .map((student) => student.student_id)
      scoreBandMatchCount.value = matchedIds.length
      if (!matchedIds.length) {
        training.setStudentScope({ mode: 'selected', studentIds: [], classId: '' })
        return
      }
      applyScope(matchedIds)
      result = await training.analyze()
    } else {
      applyScope()
      result = await training.analyze()
    }
    if (request !== autoAnalyzeGeneration) return
    const first = result?.students
      .flatMap((student) => student.weak_points.map((weak) => ({
        studentId: student.student_id,
        knowledgeKey: weak.knowledge_key,
      })))[0]
    selectedWeakKey.value = first ? weakKey(first.studentId, first.knowledgeKey) : ''
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

function scheduleAnalysis(): void {
  if (autoAnalyzeHandle) clearTimeout(autoAnalyzeHandle)
  autoAnalyzeGeneration += 1
  if (studentMode.value === 'score_band') scoreBandMatchCount.value = null
  if (referenceState.value !== 'ready') return
  if (studentMode.value === 'score_band') {
    const classIds = students.value
      .filter((student) => student.class_name === selectedClass.value)
      .map((student) => String(student.id))
    applyScope(classIds, true)
  } else {
    applyScope()
  }
  if (!hasAnalyzableScope.value) return
  autoAnalyzeHandle = setTimeout(() => {
    autoAnalyzeHandle = null
    void analyze()
  }, 260)
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
  () => sessionStore.selectedSessionId,
  () => {
    if (examMode.value === 'current') scheduleAnalysis()
  },
)

watch(
  [
    examMode,
    () => manualSessionIds.value.join(','),
    studentMode,
    selectedClass,
    () => selectedStudentIds.value.join(','),
    scoreBand,
  ],
  scheduleAnalysis,
)

watch(selectedClass, () => {
  const currentClassIds = new Set(
    students.value
      .filter((student) => student.class_name === selectedClass.value)
      .map((student) => String(student.id)),
  )
  selectedStudentIds.value = selectedStudentIds.value.filter((id) => currentClassIds.has(id))
})

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

onMounted(() => {
  void loadStudents()
  void training.loadTasks().catch(() => undefined)
  void jobs.initialize().catch(() => undefined)
})

onBeforeUnmount(() => {
  studentsController?.abort()
  if (autoAnalyzeHandle) clearTimeout(autoAnalyzeHandle)
  autoAnalyzeGeneration += 1
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
      <div class="training-basis" aria-label="推荐口径">
        <strong>精确标签口径</strong>
        <span>不使用近义标签补题，不调用 AI 二次匹配</span>
      </div>
    </header>

    <section class="training-scope" aria-labelledby="training-scope-title">
      <div class="training-section-heading">
        <div>
          <p class="training-eyebrow">01 · 明确范围</p>
          <h2 id="training-scope-title">选择考试与学生</h2>
        </div>
        <div class="training-scope-summary">
          <span>{{ selectedExamCount }} 场考试 · {{ selectedStudentCount }} 名学生</span>
          <button
            type="button"
            class="training-button is-secondary"
            :aria-expanded="scopeEditorOpen"
            @click="scopeEditorOpen = !scopeEditorOpen"
          >{{ scopeEditorOpen ? '收起范围' : '更改范围' }}</button>
        </div>
      </div>

      <template v-if="scopeEditorOpen">
        <div v-if="referenceState === 'error'" class="training-feedback is-error" role="alert">
          学生名单暂时无法读取。当前未选择任何学生。
          <button type="button" class="training-link" @click="loadStudents">重新读取</button>
        </div>

        <div class="training-scope-grid">
        <fieldset class="training-filter-group">
          <legend>考试</legend>
          <div class="training-filter-modes" data-testid="training-exam-mode">
            <button type="button" :class="{ 'is-active': examMode === 'current' }" @click="examMode = 'current'">
              <strong>当前考试</strong><span>{{ sessionStore.currentSession?.name || '尚未选择' }}</span>
            </button>
            <button type="button" :class="{ 'is-active': examMode === 'recent' }" @click="examMode = 'recent'">
              <strong>最近一个月</strong><span>{{ recentSessions.length }} 场</span>
            </button>
            <button type="button" :class="{ 'is-active': examMode === 'manual' }" @click="examMode = 'manual'">
              <strong>指定考试</strong><span>{{ manualSessionIds.length }} 场</span>
            </button>
            <button type="button" :class="{ 'is-active': examMode === 'cross_exam' }" @click="examMode = 'cross_exam'">
              <strong>全部考试</strong><span>{{ availableSessions.length }} 场</span>
            </button>
          </div>
          <div v-if="examMode === 'manual'" class="training-choice-grid" aria-label="选择考试">
            <label v-for="session in availableSessions" :key="session.id" :class="{ 'is-selected': manualSessionIds.includes(session.id) }">
              <input
                type="checkbox"
                :checked="manualSessionIds.includes(session.id)"
                @change="toggleSession(session.id)"
              >
              <span><strong>{{ session.name }}</strong><small>{{ session.created_at ? new Date(session.created_at).toLocaleDateString('zh-CN') : '日期未记录' }}</small></span>
            </label>
          </div>
        </fieldset>

        <fieldset class="training-filter-group training-filter-group--students">
          <legend>班级与学生</legend>
          <div class="training-filter-inline">
            <label>
              <span>班级</span>
              <select v-model="selectedClass">
                <option value="">请选择班级</option>
                <option v-for="className in classes" :key="className" :value="className">{{ className }}</option>
              </select>
            </label>
            <div class="training-filter-modes training-filter-modes--students">
              <button type="button" :class="{ 'is-active': studentMode === 'class' }" @click="studentMode = 'class'">整班</button>
              <button type="button" :class="{ 'is-active': studentMode === 'score_band' }" @click="studentMode = 'score_band'">按得分率</button>
              <button type="button" :class="{ 'is-active': studentMode === 'selected' }" @click="studentMode = 'selected'">指定学生</button>
            </div>
          </div>

          <div v-if="studentMode === 'score_band'" class="training-score-bands" aria-label="按所选考试得分率筛选">
            <button type="button" :class="{ 'is-active': scoreBand === 'low' }" @click="scoreBand = 'low'"><strong>0–39%</strong><span>优先补缺</span></button>
            <button type="button" :class="{ 'is-active': scoreBand === 'middle' }" @click="scoreBand = 'middle'"><strong>40–69%</strong><span>基础巩固</span></button>
            <button type="button" :class="{ 'is-active': scoreBand === 'high' }" @click="scoreBand = 'high'"><strong>70–100%</strong><span>提升训练</span></button>
            <p>系统先读取所选考试的实际得分率，再自动缩小学生范围；缺失成绩不会按 0 分处理。</p>
          </div>

          <template v-if="studentMode === 'selected'">
            <div class="training-student-tools">
              <input v-model="studentSearch" type="search" placeholder="搜索姓名或学号" aria-label="搜索学生">
              <button type="button" @click="selectVisibleStudents">选择当前结果</button>
              <button type="button" @click="clearSelectedStudents">清空</button>
            </div>
            <div class="training-choice-grid training-choice-grid--students" data-testid="training-student">
              <label v-for="student in visibleStudents" :key="student.id" :class="{ 'is-selected': selectedStudentIds.includes(String(student.id)) }">
                <input
                  type="checkbox"
                  :checked="selectedStudentIds.includes(String(student.id))"
                  @change="toggleStudent(String(student.id))"
                >
                <span><strong>{{ student.name }}</strong><small>{{ student.student_code }}</small></span>
              </label>
            </div>
          </template>
        </fieldset>
        </div>

        <div class="training-action-row">
          <p>筛选条件有效后会自动刷新下方热力图，不调用外部 AI，也不会产生模型费用。</p>
          <button
            v-if="training.analysisState === 'error'"
            type="button"
            class="training-button is-secondary"
            data-testid="retry-training-analysis"
            :disabled="!canAnalyze"
            @click="analyze"
          >
            重新读取热力图
          </button>
          <span v-else class="training-auto-state" role="status">
            {{ training.analysisState === 'loading' ? '正在自动刷新…' : '范围变化后自动刷新' }}
          </span>
        </div>
      </template>
    </section>

    <p v-if="training.errorMessage" class="training-feedback is-error" role="alert">
      {{ training.errorMessage }}
    </p>
    <p v-if="training.actionMessage" class="training-feedback is-success" role="status">
      {{ training.actionMessage }}
    </p>

    <div class="training-main-grid">
      <main class="training-primary">
        <section class="training-ledger" aria-labelledby="diagnosis-title">
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
            <div v-if="training.diagnosis.students.length" class="training-ledger-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">学生</th>
                    <th v-for="point in training.knowledgePoints" :key="point" scope="col">
                      {{ point }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="student in training.diagnosis.students" :key="student.student_id">
                    <th scope="row">
                      <strong>{{ student.student_name }}</strong>
                      <span>{{ student.student_code }}</span>
                    </th>
                    <td v-for="point in training.knowledgePoints" :key="point">
                      <button
                        v-if="findWeak(student.student_id, point)"
                        type="button"
                        class="training-heat-cell"
                        :class="{
                          'is-selected': selectedWeakKey === weakKey(
                            student.student_id,
                            findWeak(student.student_id, point)!.knowledge_key,
                          ),
                        }"
                        :data-testid="`weak-point-${student.student_id}-${findWeak(student.student_id, point)!.knowledge_key}`"
                        :aria-label="`${student.student_name}，${point}，掌握率 ${masteryLabel(findWeak(student.student_id, point)!.mastery)}，${findWeak(student.student_id, point)!.evidence_count} 条证据`"
                        @click="selectedWeakKey = weakKey(
                          student.student_id,
                          findWeak(student.student_id, point)!.knowledge_key,
                        )"
                      >
                        <strong>{{ masteryLabel(findWeak(student.student_id, point)!.mastery) }}</strong>
                        <span>· {{ findWeak(student.student_id, point)!.evidence_count }} 条证据</span>
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

        <section class="training-evidence" aria-labelledby="evidence-title">
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
                <strong>{{ evidence.score_rate == null ? '得分率未知' : masteryLabel(evidence.score_rate) }}</strong>
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
          />

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
      </main>

      <aside class="training-sidebar" aria-labelledby="training-history-title">
        <nav class="training-stage-rail" aria-label="训练推荐步骤">
          <strong>当前工作</strong>
          <ol>
            <li :class="{ 'is-complete': selectedExamCount > 0 && selectedStudentCount > 0 }">确定范围</li>
            <li :class="{ 'is-complete': Boolean(training.diagnosis) }">核对证据</li>
            <li :class="{ 'is-complete': Boolean(training.plan) }">确认计划</li>
            <li :class="{ 'is-complete': Boolean(selectedTask) }">保存与出件</li>
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

        <section v-if="selectedTask" class="training-export-panel">
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
