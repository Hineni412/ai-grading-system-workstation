<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type { JobResponse } from '../api/jobs'
import { fetchStudents, type StudentSummary } from '../api/students'
import type {
  TrainingPlanItem,
  TrainingStage,
  TrainingWeakPoint,
} from '../api/training'
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
const examMode = ref<'current' | 'manual' | 'cross_exam'>('current')
const manualSessionIds = ref<number[]>([])
const studentMode = ref<'student' | 'selected' | 'class'>('student')
const singleStudentId = ref('')
const selectedStudentIds = ref<string[]>([])
const selectedClass = ref('')
const selectedWeakKey = ref('')
const variantMode = ref<'individual' | 'auto_group'>('individual')
const questionCount = ref(10)
const excludeCurrentOriginals = ref(true)
const exportMode = ref<ExportMode>('bundle')
const exportFormat = ref<'docx' | 'markdown'>('docx')
const exportAudience = ref<'student' | 'teacher'>('student')
const exportVariantId = ref<number | null>(null)
let studentsController: AbortController | null = null

const classes = computed(() => [...new Set(
  students.value
    .map((student) => student.class_name?.trim() ?? '')
    .filter(Boolean),
)].sort())

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

const trainingJobs = computed(() => Object.values(jobs.jobs)
  .filter((job) => job.job_type === 'training_export')
  .sort((left, right) => right.id - left.id))

const coverageText = computed(() => {
  const coverage = training.diagnosis?.coverage
  if (!coverage) return ''
  return `已覆盖 ${coverage.covered_items} / ${coverage.total_items} 个评分题`
})

const selectedStudentCount = computed(() => {
  if (studentMode.value === 'student') return singleStudentId.value ? 1 : 0
  if (studentMode.value === 'selected') return selectedStudentIds.value.length
  return students.value.filter(
    (student) => student.class_name === selectedClass.value,
  ).length
})

const selectedExamCount = computed(() => {
  if (examMode.value === 'current') return sessionStore.selectedSessionId ? 1 : 0
  if (examMode.value === 'manual') return manualSessionIds.value.length
  return sessionStore.sessions.filter((session) => !session.is_deleted).length
})

const canAnalyze = computed(
  () => selectedStudentCount.value > 0
    && selectedExamCount.value > 0
    && training.analysisState !== 'loading',
)

const canConfirm = computed(
  () => training.hasCurrentPlan
    && training.planState === 'ready'
    && training.confirmState !== 'submitting'
    && training.confirmState !== 'conflict',
)

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

function scopeStudentIds(): string[] {
  if (studentMode.value === 'student') {
    return singleStudentId.value ? [singleStudentId.value] : []
  }
  if (studentMode.value === 'selected') return selectedStudentIds.value
  return students.value
    .filter((student) => student.class_name === selectedClass.value)
    .map((student) => String(student.id))
}

function scopeSessionIds(): number[] {
  if (examMode.value === 'current') {
    return sessionStore.selectedSessionId ? [sessionStore.selectedSessionId] : []
  }
  if (examMode.value === 'manual') return manualSessionIds.value
  return sessionStore.sessions
    .filter((session) => !session.is_deleted)
    .map((session) => session.id)
}

function applyScope(): void {
  training.setStudentScope({
    mode: studentMode.value,
    studentIds: scopeStudentIds(),
    classId: studentMode.value === 'class' ? selectedClass.value : '',
  })
  training.setExamScope({
    mode: examMode.value,
    sessionIds: scopeSessionIds(),
  })
  selectedWeakKey.value = ''
}

async function loadStudents(): Promise<void> {
  studentsController?.abort()
  const controller = new AbortController()
  studentsController = controller
  referenceState.value = 'loading'
  try {
    students.value = await fetchStudents(controller.signal)
    referenceState.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    students.value = []
    referenceState.value = 'error'
  } finally {
    if (studentsController === controller) studentsController = null
  }
}

async function analyze(): Promise<void> {
  applyScope()
  try {
    const result = await training.analyze()
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

async function previewPlan(): Promise<void> {
  try {
    await training.previewPlan({
      variantMode: variantMode.value,
      questionCount: questionCount.value,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
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
    if (exportMode.value === 'variant' && exportVariantId.value) {
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
    if (examMode.value === 'current') applyScope()
  },
)

onMounted(() => {
  void loadStudents()
  void training.loadTasks().catch(() => undefined)
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
        <span>{{ selectedExamCount }} 场考试 · {{ selectedStudentCount }} 名学生</span>
      </div>

      <div v-if="referenceState === 'error'" class="training-feedback is-error" role="alert">
        学生名单暂时无法读取。当前未选择任何学生。
        <button type="button" class="training-link" @click="loadStudents">重新读取</button>
      </div>

      <div class="training-scope-grid">
        <fieldset>
          <legend>考试范围</legend>
          <label>
            范围方式
            <select v-model="examMode" data-testid="training-exam-mode" @change="applyScope">
              <option value="current">顶部当前考试</option>
              <option value="manual">手动选择考试</option>
              <option value="cross_exam">全部可用考试</option>
            </select>
          </label>
          <label v-if="examMode === 'manual'">
            选择考试（可多选）
            <select v-model="manualSessionIds" multiple @change="applyScope">
              <option
                v-for="session in sessionStore.sessions.filter((item) => !item.is_deleted)"
                :key="session.id"
                :value="session.id"
              >
                {{ session.name }}
              </option>
            </select>
          </label>
          <p v-else-if="examMode === 'current'" class="training-field-note">
            {{ sessionStore.currentSession?.name || '请先在顶部选择考试' }}
          </p>
          <p v-else class="training-field-note">
            将读取 {{ selectedExamCount }} 场未删除考试。
          </p>
        </fieldset>

        <fieldset>
          <legend>学生范围</legend>
          <label>
            范围方式
            <select v-model="studentMode" @change="applyScope">
              <option value="student">单个学生</option>
              <option value="selected">明确多选</option>
              <option value="class">整班</option>
            </select>
          </label>
          <label v-if="studentMode === 'student'">
            学生
            <select
              v-model="singleStudentId"
              data-testid="training-student"
              @change="applyScope"
            >
              <option value="">请明确选择要训练的学生</option>
              <option v-for="student in students" :key="student.id" :value="String(student.id)">
                {{ student.name }} · {{ student.student_code }}
              </option>
            </select>
          </label>
          <label v-else-if="studentMode === 'selected'">
            学生（可多选）
            <select v-model="selectedStudentIds" multiple @change="applyScope">
              <option v-for="student in students" :key="student.id" :value="String(student.id)">
                {{ student.name }} · {{ student.student_code }}
              </option>
            </select>
          </label>
          <label v-else>
            班级
            <select v-model="selectedClass" @change="applyScope">
              <option value="">请选择班级</option>
              <option v-for="className in classes" :key="className" :value="className">
                {{ className }}
              </option>
            </select>
          </label>
        </fieldset>
      </div>

      <div class="training-action-row">
        <p>筛选结果不会自动成为训练范围；只有上方明确选择的学生会进入诊断。</p>
        <button
          type="button"
          class="training-button is-primary"
          data-testid="analyze-training"
          :disabled="!canAnalyze"
          @click="analyze"
        >
          {{ training.analysisState === 'loading' ? '正在分析…' : '分析薄弱知识点' }}
        </button>
      </div>
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
            先明确考试和学生范围，再分析薄弱知识点。
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
            <span>针对训练 60% · 基础巩固 30% · 提升应用 10%</span>
          </div>

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
              <select v-model.number="questionCount">
                <option v-for="count in [8, 9, 10, 11, 12]" :key="count" :value="count">
                  {{ count }} 题
                </option>
              </select>
            </label>
            <label class="training-checkbox">
              <input v-model="excludeCurrentOriginals" type="checkbox">
              排除当前考试原题
            </label>
            <button
              type="button"
              class="training-button is-secondary"
              data-testid="preview-training"
              :disabled="!training.hasCurrentDiagnosis || training.planState === 'loading'"
              @click="previewPlan"
            >
              {{ training.planState === 'loading' ? '正在生成…' : '生成计划预览' }}
            </button>
          </div>

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

        <section v-if="selectedTask" class="training-export-panel">
          <div class="training-section-heading">
            <div>
              <p class="training-eyebrow">04 · 训练出件</p>
              <h2>生成训练材料</h2>
            </div>
          </div>
          <label>
            出件范围
            <select v-model="exportMode">
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
            :disabled="Boolean(training.submittingExportKey)"
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
