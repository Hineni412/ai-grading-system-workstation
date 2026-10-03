<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { jobApi, type JobResponse } from '../../api/jobs'
import { exportsApi } from '../../api/exports'

import {
  trainingApi,
  type PersonalizedPaperInstance,
  type PersonalizedPaperBatch,
  type PersonalizedRecommendationDraft,
  type PersonalizedRecommendationItem,
  type TrainingDiagnosis,
  type TrainingEvidenceReference,
  type TrainingExamScopeRequest,
  type TrainingStage,
  type TrainingStudentScopeRequest,
} from '../../api/training'
import { knowledgeLeafLabel } from '../../api/question-bank'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { difficultyLevel } from '../../lib/utils'
import AppButton from '../design-system/AppButton.vue'
import StepProgress, { type StepProgressStep } from '../design-system/StepProgress.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import TrainingScanBatchPanel from './TrainingScanBatchPanel.vue'
import QuestionPreviewDialog from './QuestionPreviewDialog.vue'
import {
  clearPaperDraftSession,
  loadPaperDraftSession,
  savePaperDraftSession,
} from '../../features/training/paper-draft-session'

const props = defineProps<{
  initialDraftId?: string
  diagnosis: TrainingDiagnosis | null
  scope: TrainingStudentScopeRequest
  examScope: TrainingExamScopeRequest
  purpose?: 'training' | 'handout'
  maxQuestionsPerSkill?: number
  maxWrittenQuestions?: number
  recentActivityCount?: number
  questionCount: number
  excludeCurrentExamOriginals: boolean
  paperMode?: 'individual' | 'shared'
  targetKeys?: string[]
  scopeKeys?: string[]
  disabled?: boolean
  externalSetup?: boolean
  expectedMinutes?: number
  difficultyMin?: number
  difficultyMax?: number
  curriculumVolumeId?: string | null
  groupScopeKeys?: string[]
  groupSourceVersion?: string
  trainingIntent?: 'remediation' | 'challenge'
  teachingProgressChapterId?: string
}>()
const emit = defineEmits<{
  stageChange: [stage: 'diagnosis' | 'draft' | 'wps' | 'scan']
  stateChange: [state: RequestState]
  recoveryChange: [pending: boolean]
  contextChange: [context: { mode: 'individual' | 'shared'; studentCount: number; questionCount: number; difficultyMax: number; purpose: 'training' | 'handout' } | null]
}>()

type RequestState = 'idle' | 'loading' | 'ready' | 'error' | 'editing'

const difficultyMax = ref(props.difficultyMax ?? 8)
const selectedTargets = ref<string[]>([])
const editReason = ref('教师根据课堂安排调整推荐草稿')
const state = ref<RequestState>('idle')
const draft = ref<PersonalizedRecommendationDraft | null>(null)
const pendingRequestToken = ref('')
const selectedDraftStudentId = ref('')
const confirmDiscard = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')
const paperInstances = ref<PersonalizedPaperInstance[]>([])
const handoutJob = ref<JobResponse | null>(null)
const handoutRequestToken = ref('')
const viewStep = ref<'review' | 'print' | 'scan'>('review')
const scanProgress = ref({ received: 0, total: 0, completed: false })
const effectivePaperMode = computed(() => (draft.value?.config.paper_mode ?? props.paperMode ?? 'individual') === 'shared' ? 'shared' : 'individual')
const eligiblePaperCount = computed(() => draft.value?.students.filter(student => student.items.length > 0 && student.selection_mode !== 'maintenance_fallback').length ?? 0)
const hasPrintablePapers = computed(() => paperInstances.value.some(instance => instance.status === 'frozen'))
const allPapersReady = computed(() => {
  const current = draft.value
  if (!current?.students.length) return false
  return current.students.every(student => paperInstances.value.some(instance => instance.student_id === student.student_id && instance.status === 'frozen' && instance.draft_revision === current.revision))
})
const workspaceSteps = computed<StepProgressStep[]>(() => [
  { id: 'review', label: '审核题目', status: paperInstances.value.length || handoutJob.value?.status === 'succeeded' ? 'done' : 'in_progress', available: true },
  { id: 'print', label: isHandout.value ? '导出讲义' : '打印试卷', status: allPapersReady.value || handoutJob.value?.status === 'succeeded' ? 'done' : paperBatch.value || viewStep.value === 'print' ? 'in_progress' : 'todo', available: Boolean(draft.value) },
  ...(!isHandout.value ? [{ id: 'scan', label: '回收批改', status: scanProgress.value.completed ? 'done' as const : scanProgress.value.received ? 'in_progress' as const : 'todo' as const, available: hasPrintablePapers.value }] : []),
])
function selectWorkspaceStep(id: string): void {
  if (id === 'review' || id === 'print' || (id === 'scan' && hasPrintablePapers.value)) viewStep.value = id
}
watch(() => draft.value?.draft_id, () => { viewStep.value = 'review'; scanProgress.value = { received: 0, total: 0, completed: false }; confirmDiscard.value = false })
watch(draft, value => emit('contextChange', value ? {
  mode: effectivePaperMode.value, studentCount: value.students.length,
  questionCount: Number(value.config.question_count ?? props.questionCount), difficultyMax: Number(value.config.difficulty_max ?? difficultyMax.value),
  purpose: value.config.purpose === 'handout' ? 'handout' : 'training',
} : null))
const isHandout = computed(() => (draft.value?.config.purpose ?? props.purpose ?? 'training') === 'handout')
let handoutPollGeneration = 0
let componentActive = true
onBeforeUnmount(() => { componentActive = false; handoutPollGeneration += 1; paperBatchPollGeneration += 1 })
const paperBusy = ref('')
// 整卷体积上限对教师不可见：固定取最大档，硬上限（题量/判定点/图片/页数）仍会拦截。
const PAPER_CONTEXT_WINDOW = 128000
const paperBatch = ref<PersonalizedPaperBatch | null>(null)
const paperCancelBusy = ref(false)
let paperBatchPollGeneration = 0

const targetOptions = computed(() => {
  const options = new Map<string, string>()
  for (const weak of (props.diagnosis?.students ?? []).flatMap((student) => student.weak_points)) {
    if (weak.knowledge_key && weak.knowledge_point.trim()) {
      options.set(weak.knowledge_key, weak.knowledge_point.trim())
    }
  }
  return [...options].map(([key, label]) => ({ key, label })).sort((left, right) => (
    left.label.localeCompare(right.label, 'zh-CN')
  ))
})
const selectedDraftStudent = computed(() => draft.value?.students.find(
  (student) => student.student_id === selectedDraftStudentId.value,
) ?? draft.value?.students[0] ?? null)
const selectedTargetLabels = computed(() => {
  const labels = new Map(targetOptions.value.map((item) => [item.key, item.label]))
  return selectedTargets.value.map((key) => labels.get(key) ?? key)
})
const selectedTargetsAreGoverned = computed(() => selectedTargets.value.every((key) => (
  key.startsWith('kp_') || key.startsWith('ki_')
)))

const canGenerate = computed(() => (
  Boolean(props.diagnosis)
  && !props.disabled
  && state.value !== 'loading'
  && state.value !== 'editing'
  && Number.isInteger(props.questionCount)
  && (props.purpose === 'handout' ? props.questionCount >= 1 : props.questionCount >= 8 && props.questionCount <= 12)
  && Number.isInteger(props.maxQuestionsPerSkill ?? 1) && (props.maxQuestionsPerSkill ?? 1) >= 1 && (props.maxQuestionsPerSkill ?? 1) <= props.questionCount
  && Number.isInteger(props.maxWrittenQuestions ?? 2) && (props.maxWrittenQuestions ?? 2) >= 0 && (props.maxWrittenQuestions ?? 2) <= props.questionCount
  && Number.isInteger(props.recentActivityCount ?? 3) && (props.recentActivityCount ?? 3) >= 0
  && Number.isInteger(difficultyMax.value)
  && difficultyMax.value >= 1
  && difficultyMax.value <= 10
  && (
    (props.scopeKeys?.length ?? 0) > 0
    || !targetOptions.value.length
    || selectedTargets.value.length > 0
  )
))
const workflowStage = computed<'diagnosis' | 'draft' | 'wps' | 'scan'>(() => {
  if (!draft.value) return 'diagnosis'
  if (paperInstances.value.some((item) => item.status === 'frozen')) return 'scan'
  if (paperBatch.value || paperInstances.value.length) return 'wps'
  return 'draft'
})
// 已生成训练卷或批次后不允许放弃草稿，避免丢失已生成卷的返回入口。
const canDiscardDraft = computed(() => (
  state.value !== 'loading'
  && state.value !== 'editing'
  && !paperInstances.value.length
  && !paperBatch.value
  && !paperBusy.value
  && (!handoutRequestToken.value || Boolean(handoutJob.value && ['succeeded', 'failed', 'cancelled'].includes(handoutJob.value.status)))
))

// 出卷设置指纹：设置一致时才恢复上次草稿，设置变了必须重新生成。
// rulesVersion 随选题规则升级递增，避免恢复规则升级前的旧草稿。
const settingsFingerprint = computed(() => JSON.stringify({
  rulesVersion: props.paperMode === 'shared' ? 15 : 16,
  maxUnmeasuredQuestions: props.paperMode === 'shared' ? 0 : 4,
  purpose: props.purpose ?? 'training',
  maxQuestionsPerSkill: props.maxQuestionsPerSkill ?? 1,
  maxWrittenQuestions: props.maxWrittenQuestions ?? 2,
  recentActivityCount: props.recentActivityCount ?? 3,
  scope: props.scope,
  examScope: props.examScope,
  questionCount: props.questionCount,
  difficultyMax: difficultyMax.value,
  paperMode: props.paperMode ?? 'individual',
  targetKeys: [...(props.targetKeys ?? [])].sort(),
  scopeKeys: [...(props.scopeKeys ?? [])].sort(),
  excludeCurrentExamOriginals: props.excludeCurrentExamOriginals,
  curriculumVolumeId: props.curriculumVolumeId ?? null,
  groupScopeKeys: props.groupScopeKeys ?? [],
  teachingProgressChapterId: props.teachingProgressChapterId ?? '',
}))

function rememberDraft(draftId: string): void {
  savePaperDraftSession({
    fingerprint: settingsFingerprint.value,
    draftId,
    handoutRequestToken: handoutRequestToken.value || undefined,
  })
}

let restoring = false
watch(() => props.initialDraftId, id => {
  if (id && draft.value && id !== draft.value.draft_id) {
    draft.value = null
    paperInstances.value = []
    paperBatch.value = null
    state.value = 'idle'
  }
})
async function restoreDraft(): Promise<void> {
  if (restoring || draft.value || state.value !== 'idle' || (!props.diagnosis && !props.initialDraftId)) return
  const stored = props.initialDraftId ? { draftId: props.initialDraftId, fingerprint: settingsFingerprint.value, requestToken: '', handoutRequestToken: '' } : loadPaperDraftSession()
  if (!stored) return
  let previousRules = false
  if (stored.fingerprint !== settingsFingerprint.value) {
    try {
      const previous = JSON.parse(stored.fingerprint)
      previousRules = Number(previous.rulesVersion) < 11
      if (!previousRules) return
      previous.rulesVersion = 11
      previous.purpose ??= 'training'
      previous.maxQuestionsPerSkill ??= 1
      previous.maxWrittenQuestions ??= 2
      previous.recentActivityCount ??= 3
      for (const key of ['trainingIntent', 'expectedMinutes', 'difficultyMin', 'stageRatios']) delete previous[key]
      previous.teachingProgressChapterId ??= ''
      previous.difficultyMax ??= difficultyMax.value
      const current = JSON.parse(settingsFingerprint.value)
      if (Object.keys(current).some(key => JSON.stringify(current[key]) !== JSON.stringify(previous[key]))) return
    } catch { return }
  }
  restoring = true
  if (stored.requestToken && !stored.draftId) {
    pendingRequestToken.value = stored.requestToken
    await recoverPendingDraft()
    restoring = false
    return
  }
  state.value = 'loading'
  try {
    const restored = await trainingApi.getPersonalizedDraft(stored.draftId)
    const [instances, batches] = await Promise.all([
      trainingApi.listPaperInstances(stored.draftId),
      trainingApi.listPaperBatches(stored.draftId),
    ])
    draft.value = restored
    paperInstances.value = instances
    paperBatch.value = batches[0] ?? null
    selectedDraftStudentId.value = restored.students[0]?.student_id ?? ''
    handoutRequestToken.value = stored.handoutRequestToken ?? ''
    state.value = 'ready'
    await Promise.resolve()
    viewStep.value = instances.some(instance => instance.status === 'frozen') ? 'scan' : 'review'
    actionMessage.value = previousRules
      ? '已恢复原草稿，沿用保存时的设置、来源和限制；已生成训练卷仍可查看。'
      : '已恢复上次生成的草稿，可继续审核。'
    if (handoutRequestToken.value) void recoverHandoutExport()
  } catch {
    clearPaperDraftSession()
    state.value = 'idle'
    actionMessage.value = '上次暂存的草稿已无法打开，已清除本地暂存；请重新生成草稿。'
  } finally {
    restoring = false
  }
}

function discardDraft(): void {
  if (!canDiscardDraft.value) return
  draft.value = null
  selectedDraftStudentId.value = ''
  confirmDiscard.value = false
  paperInstances.value = []
  paperBusy.value = ''
  paperBatch.value = null
  handoutJob.value = null
  handoutRequestToken.value = ''
  handoutPollGeneration += 1
  paperBatchPollGeneration += 1
  state.value = 'idle'
  errorMessage.value = ''
  actionMessage.value = '已放弃该草稿，回到出卷设置；勾选与设置保持不变，可调整后重新生成。'
  clearPaperDraftSession()
}

watch(workflowStage, (stage) => emit('stageChange', stage), { immediate: true })
watch(state, (nextState) => emit('stateChange', nextState), { immediate: true })
watch(pendingRequestToken, value => emit('recoveryChange', Boolean(value)), { immediate: true })
watch(() => props.difficultyMax, (value) => {
  if (value !== undefined) difficultyMax.value = value
})

watch(
  [settingsFingerprint, () => JSON.stringify(props.diagnosis?.students.map(student => student.student_id).sort() ?? [])],
  () => {
    // 草稿自带生成时的证据快照；重读同一批学生不清空草稿。
    if (props.initialDraftId && draft.value?.draft_id === props.initialDraftId) return
    // 设置或成员变化才重置，生成与出卷仍由后端核对来源。
    draft.value = null
    pendingRequestToken.value = ''
    selectedDraftStudentId.value = ''
    state.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
    paperInstances.value = []
    paperBusy.value = ''
    paperBatch.value = null
    handoutJob.value = null
    handoutRequestToken.value = ''
    handoutPollGeneration += 1
    paperBatchPollGeneration += 1
    selectedTargets.value = [...(props.targetKeys ?? [])]
  },
  { immediate: true },
)

watch(
  [settingsFingerprint, () => props.diagnosis, () => props.initialDraftId],
  () => {
    void restoreDraft()
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

function stageLabel(stage: TrainingStage): string {
  return {
    direct: '直接巩固',
    prerequisite: '先修补强',
    transfer: '迁移应用',
  }[stage]
}

function itemLabel(item: PersonalizedRecommendationItem): string {
  if (item.practice_purpose === 'new') return '新练习'
  if (item.practice_purpose === 'consolidation') return '巩固练习'
  if (item.selection_kind === 'task_matched') return item.match_label || '原小问任务匹配'
  if (item.match_level && item.match_label) return `匹配${item.match_level}级 · ${item.match_label}`
  return item.selection_kind === 'supplement' ? '补充练习' : stageLabel(item.stage)
}

function difficultySummary(items: PersonalizedRecommendationItem[]): string {
  const levels = items.map(item => difficultyLevel(item.difficulty) ?? 0)
  const basic = levels.filter(level => level >= 1 && level <= 5).length
  const six = levels.filter(level => level === 6).length
  const challenge = levels.filter(level => level >= 7).length
  return `1–5级 ${basic}题 · 6级 ${six}题 · 7级及以上 ${challenge}题 · 最高 ${Math.max(0, ...items.map(item => item.difficulty))}级`
}

function shortageStage(shortage: Record<string, unknown>): TrainingStage {
  const stage = String(shortage.stage ?? '')
  return stage === 'prerequisite' || stage === 'transfer' ? stage : 'direct'
}

function evidenceRefsFor(
  studentId: string,
  item: PersonalizedRecommendationItem,
): TrainingEvidenceReference[] {
  if (item.selection_kind === 'supplement') return []
  const stableKey = typeof item.target?.stable_key === 'string'
    ? item.target.stable_key
    : ''
  if (!stableKey) return []
  const profile = props.diagnosis?.students.find(
    (student) => student.student_id === studentId,
  )
  return profile?.weak_points.find(
    (weak) => weak.knowledge_key === stableKey,
  )?.source_question_refs ?? []
}

function evidenceRefLabel(evidence: TrainingEvidenceReference): string {
  const question = String(evidence.question_id ?? '').trim()
  const questionLabel = /第|题/.test(question) ? question : `第 ${question} 题`
  return `${evidence.session_name} · ${questionLabel} · 得 ${evidence.score_awarded}/${evidence.full_score} 分`
}

// 代表错题：优先本次考试，再按失分最重排序的第一条；
// 其余依据仍可在“全部依据”里逐条查看。
function representativeEvidence(
  refs: TrainingEvidenceReference[],
): TrainingEvidenceReference | null {
  if (!refs.length) return null
  return [...refs].sort((left, right) => {
    const leftCurrent = left.source_kind === 'current_exam' ? 0 : 1
    const rightCurrent = right.source_kind === 'current_exam' ? 0 : 1
    if (leftCurrent !== rightCurrent) return leftCurrent - rightCurrent
    return (right.full_score - right.score_awarded)
      - (left.full_score - left.score_awarded)
  })[0] ?? null
}

// 小题（如 Q10(P1)/Q10(P2)）聚合到题目层级展示：合计得分，
// 预览打开失分最重的那个小问对应的题库原题。
function aggregateEvidenceRefs(
  refs: TrainingEvidenceReference[],
): TrainingEvidenceReference[] {
  const byQuestion = new Map<string, TrainingEvidenceReference>()
  const worstPartLoss = new Map<string, number>()
  for (const ref of refs) {
    const baseQuestion = String(ref.question_id ?? '').replace(/\(P\d+\)\s*$/i, '')
    const key = `${ref.session_id}:${baseQuestion}`
    const loss = ref.full_score - ref.score_awarded
    const existing = byQuestion.get(key)
    if (!existing) {
      byQuestion.set(key, { ...ref, question_id: baseQuestion })
      worstPartLoss.set(key, loss)
      continue
    }
    existing.score_awarded += ref.score_awarded
    existing.full_score += ref.full_score
    if (ref.source_kind === 'current_exam') existing.source_kind = 'current_exam'
    if (loss > (worstPartLoss.get(key) ?? -1)) {
      existing.bank_question_id = ref.bank_question_id
      worstPartLoss.set(key, loss)
    }
  }
  return [...byQuestion.values()]
}

const previewQuestionId = ref<number | null>(null)
const previewTitle = ref('')

// 每个推荐题的错题依据展示：一道代表错题 + 可展开的完整列表。
const evidenceDisplay = computed(() => {
  const map = new Map<string, {
    representative: TrainingEvidenceReference | null
    refs: TrainingEvidenceReference[]
  }>()
  for (const student of draft.value?.students ?? []) {
    for (const item of student.items) {
      const refs = aggregateEvidenceRefs(evidenceRefsFor(student.student_id, item))
      map.set(`${student.student_id}:${item.item_id}`, {
        representative: representativeEvidence(refs),
        refs,
      })
    }
  }
  return map
})

function evidenceDisplayFor(
  studentId: string,
  item: PersonalizedRecommendationItem,
): { representative: TrainingEvidenceReference | null; refs: TrainingEvidenceReference[] } {
  return evidenceDisplay.value.get(`${studentId}:${item.item_id}`) ?? {
    representative: null,
    refs: [],
  }
}

function openPreview(questionId: number, title: string): void {
  previewTitle.value = title
  previewQuestionId.value = questionId
}

function closePreview(): void {
  previewQuestionId.value = null
  previewTitle.value = ''
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.code === 'personalized_recommendation_revision_conflict') {
      return '草稿已在另一处发生变化，请重新生成后再调整。'
    }
    if (error.code === 'personalized_recommendation_source_changed') {
      return '题目、关系或判定点已经变化，请重新生成草稿。'
    }
    if (error.code === 'personalized_paper_revision_conflict') {
      return '这份训练卷已在另一处发生变化，请刷新后再继续。'
    }
    if (error.code === 'personalized_paper_source_changed') {
      return '题目、关系或判定点已经变化，请重新生成草稿和训练卷。'
    }
    if (error.code === 'personalized_paper_invalid') {
      return '上传的 WPS 审核稿与本卷题目或顺序不一致，请使用本版本下载的审核稿。'
    }
  }
  return fallback
}

function acceptGeneratedDraft(value: PersonalizedRecommendationDraft): void {
  draft.value = value
  pendingRequestToken.value = ''
  paperInstances.value = []
  selectedDraftStudentId.value = value.students[0]?.student_id ?? ''
  state.value = 'ready'
  errorMessage.value = ''
  actionMessage.value = isHandout.value
    ? '讲义草稿已生成并自动暂存，切页后返回会自动恢复；请核对题目后导出打印。'
    : '草稿已生成并自动暂存，切页后返回会自动恢复；尚未形成正式训练卷。'
  rememberDraft(value.draft_id)
}

async function recoverPendingDraft(): Promise<void> {
  const token = pendingRequestToken.value
  const fingerprint = settingsFingerprint.value
  if (!token) return
  state.value = 'loading'
  errorMessage.value = ''
  try {
    const recovered = await trainingApi.getPersonalizedDraftByRequest(token)
    if (fingerprint !== settingsFingerprint.value || token !== pendingRequestToken.value) return
    acceptGeneratedDraft(recovered)
    actionMessage.value = '已取回上次请求生成的草稿，没有重复创建。'
  } catch {
    if (fingerprint !== settingsFingerprint.value || token !== pendingRequestToken.value) return
    state.value = 'error'
    errorMessage.value = '暂未取得生成结果，后台可能仍在处理。请稍后核对生成结果；当前设置已保留，不会重复创建草稿。'
  }
}

async function generate(): Promise<void> {
  if (!canGenerate.value) return
  if (pendingRequestToken.value) { await recoverPendingDraft(); return }
  const token = requestToken()
  const fingerprint = settingsFingerprint.value
  pendingRequestToken.value = token
  savePaperDraftSession({ fingerprint, draftId: '', requestToken: token })
  state.value = 'loading'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const generated = await trainingApi.createPersonalizedDraft({
      request_token: token,
      scope: props.scope,
      exam_scope: props.examScope,
      question_count: props.questionCount,
      purpose: props.purpose ?? 'training',
      max_questions_per_skill: props.maxQuestionsPerSkill ?? 1,
      max_written_questions: props.maxWrittenQuestions ?? 2,
      recent_activity_count: props.recentActivityCount ?? 3,
      difficulty_max: difficultyMax.value,
      paper_mode: props.paperMode ?? 'individual',
      remediation_only: props.paperMode !== 'shared',
      max_unmeasured_questions: props.paperMode !== 'shared' ? 4 : 0,
      target_keys: selectedTargetsAreGoverned.value ? selectedTargets.value : [],
      scope_keys: (props.scopeKeys ?? []).filter((key) => key.startsWith('kp_') || key.startsWith('ki_')),
      target_names: selectedTargetsAreGoverned.value ? [] : selectedTargetLabels.value,
      exclude_current_exam_originals: props.excludeCurrentExamOriginals,
      curriculum_volume_id: props.curriculumVolumeId ?? null,
      teaching_progress_chapter_id: props.teachingProgressChapterId ?? '',
      ...(props.groupScopeKeys?.length ? { group_scope_keys: props.groupScopeKeys, group_source_version: props.groupSourceVersion } : {}),
    })
    if (fingerprint !== settingsFingerprint.value || token !== pendingRequestToken.value) return
    acceptGeneratedDraft(generated)
  } catch (error) {
    if (fingerprint !== settingsFingerprint.value || token !== pendingRequestToken.value) return
    if (isAmbiguousWriteError(error) || (error instanceof ApiError && error.kind === 'contract')) {
      await recoverPendingDraft()
      return
    }
    pendingRequestToken.value = ''
    clearPaperDraftSession()
    state.value = 'error'
    errorMessage.value = safeError(
      error,
      '个性化草稿暂时无法生成，当前选择已保留，请稍后重试。',
    )
  }
}

defineExpose({ generate })

async function openNextDraft(draftId: string): Promise<void> {
  if (state.value === 'loading' || state.value === 'editing') return
  state.value = 'loading'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    draft.value = await trainingApi.getPersonalizedDraft(draftId)
    selectedDraftStudentId.value = draft.value.students[0]?.student_id ?? ''
    const [instances, batches] = await Promise.all([
      trainingApi.listPaperInstances(draftId),
      trainingApi.listPaperBatches(draftId),
    ])
    paperInstances.value = instances
    paperBatch.value = batches[0] ?? null
    state.value = 'ready'
    actionMessage.value = '已打开下一轮草稿；请先审核，系统不会自动生成正式训练卷。'
    rememberDraft(draft.value.draft_id)
  } catch (error) {
    state.value = 'error'
    errorMessage.value = safeError(
      error,
      '下一轮草稿暂时无法打开，已发布的训练证据不受影响。',
    )
  }
}

function instancesForStudent(studentId: string): PersonalizedPaperInstance[] {
  return paperInstances.value
    .filter((item) => item.student_id === studentId)
    .sort((left, right) => right.series_version - left.series_version)
}

async function trackHandoutJob(job: JobResponse): Promise<void> {
  if (!componentActive) return
  const generation = ++handoutPollGeneration
  handoutJob.value = job
  while (generation === handoutPollGeneration && ['queued', 'running'].includes(handoutJob.value.status)) {
    await new Promise(resolve => globalThis.setTimeout(resolve, 500))
    if (generation !== handoutPollGeneration) return
    handoutJob.value = await jobApi.getJob(job.id)
  }
  if (generation !== handoutPollGeneration) return
  if (handoutJob.value.status === 'succeeded') {
    const skipped = Number(handoutJob.value?.result.skipped_student_count) || 0
    actionMessage.value = skipped
      ? `讲义已导出；${skipped} 名学生暂无可配补弱题，已跳过空卷。请下载后打印。`
      : '讲义已导出，请下载后打印；不会产生训练证据。'
  } else {
    errorMessage.value = '讲义导出未完成，请重新核对草稿来源后再导出。'
    handoutRequestToken.value = ''
  }
  if (draft.value) rememberDraft(draft.value.draft_id)
}

async function recoverHandoutExport(): Promise<void> {
  if (!componentActive || !draft.value || !handoutRequestToken.value) return
  const draftId = draft.value.draft_id
  const token = handoutRequestToken.value
  const fingerprint = settingsFingerprint.value
  const stillCurrent = () => componentActive && token === handoutRequestToken.value && fingerprint === settingsFingerprint.value
  paperBusy.value = 'handout'
  try {
    const job = await trainingApi.getHandoutExportByRequest(draftId, token)
    if (!stillCurrent()) return
    await trackHandoutJob(job)
  } catch (error) {
    if (!stillCurrent()) return
    if (error instanceof ApiError && error.status === 404) {
      handoutRequestToken.value = ''
      rememberDraft(draft.value.draft_id)
      actionMessage.value = '未找到导出任务，可以重新导出讲义。'
    } else errorMessage.value = '暂时无法核对导出结果，请稍后再次核对。'
  } finally { if (stillCurrent()) paperBusy.value = '' }
}

async function exportHandout(): Promise<void> {
  if (!draft.value || !isHandout.value || paperBusy.value) return
  if (handoutRequestToken.value && (!handoutJob.value || ['queued', 'running'].includes(handoutJob.value.status))) {
    await recoverHandoutExport(); return
  }
  paperBusy.value = 'handout'
  errorMessage.value = ''
  actionMessage.value = ''
  handoutRequestToken.value = requestToken()
  const token = handoutRequestToken.value
  const fingerprint = settingsFingerprint.value
  handoutJob.value = null
  rememberDraft(draft.value.draft_id)
  try {
    const job = await trainingApi.exportHandout(draft.value.draft_id, {
      expected_revision: draft.value.revision, request_token: token,
    })
    if (!componentActive || token !== handoutRequestToken.value || fingerprint !== settingsFingerprint.value) return
    await trackHandoutJob(job)
  } catch (error) {
    if (!componentActive || token !== handoutRequestToken.value || fingerprint !== settingsFingerprint.value) return
    if (isAmbiguousWriteError(error) || (error instanceof ApiError && error.kind === 'contract') || handoutJob.value) errorMessage.value = '导出请求结果暂未确认，请核对导出结果。'
    else {
      handoutRequestToken.value = ''
      rememberDraft(draft.value.draft_id)
      errorMessage.value = safeError(error, '讲义暂时无法导出，请重新核对草稿来源。')
    }
  } finally { paperBusy.value = '' }
}

async function downloadHandout(): Promise<void> {
  if (!handoutJob.value?.result.download_url || paperBusy.value) return
  paperBusy.value = 'handout-download'
  try {
    const file = await exportsApi.downloadJobFile(handoutJob.value.id)
    const url = URL.createObjectURL(file.blob)
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = file.filename; anchor.click()
    URL.revokeObjectURL(url)
    handoutJob.value = await jobApi.getJob(handoutJob.value.id)
    actionMessage.value = '讲义已下载，本机副本已删除；需要时可以再次导出。'
  } catch { errorMessage.value = '讲义暂时无法下载，请稍后重试。' }
  finally { paperBusy.value = '' }
}

async function createPaperBatch(): Promise<void> {
  if (!draft.value || paperBusy.value || isHandout.value) return
  const studentIds = draft.value.students
    .filter((student) => (
      student.items.length > 0
      && student.selection_mode !== 'maintenance_fallback'
    ))
    .map((student) => student.student_id)
  if (!studentIds.length) {
    errorMessage.value = '当前没有带有效证据的学生可批量出卷。'
    return
  }
  paperBusy.value = 'batch'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const pollGeneration = ++paperBatchPollGeneration
    const createPromise = trainingApi.createPaperBatch(
      draft.value.draft_id,
      {
        operation_token: requestToken(),
        expected_draft_revision: draft.value.revision,
        student_ids: studentIds,
        context_window_tokens: PAPER_CONTEXT_WINDOW,
        direct_freeze: true,
      },
    )
    void pollCreatingBatch(draft.value.draft_id, pollGeneration)
    paperBatch.value = await createPromise
    paperBatchPollGeneration += 1
    paperInstances.value = [
      ...paperBatch.value.items,
      ...paperInstances.value.filter((existing) => !paperBatch.value?.items.some(
        (item) => item.paper_instance_id === existing.paper_instance_id,
      )),
    ]
    viewStep.value = 'print'
    actionMessage.value = paperBatch.value.status === 'cancelled'
      ? `批次已停止；已保留 ${paperBatch.value.succeeded_count} 份完成卷，未开始学生可另行重试。`
      : paperBatch.value.failed_count
        ? `已生成 ${paperBatch.value.succeeded_count} 份实名 PDF 试卷，${paperBatch.value.failed_count} 人失败，可保留成功卷后单独重试。`
        : `已生成 ${paperBatch.value.succeeded_count} 份实名 PDF 试卷，下载 ZIP 后即可打印。`
  } catch (error) {
    errorMessage.value = safeError(error, '批量 PDF 试卷暂时无法生成；已有卷和推荐草稿均未改变。')
  } finally {
    paperBusy.value = ''
  }
}

async function pollCreatingBatch(draftId: string, generation: number): Promise<void> {
  while (generation === paperBatchPollGeneration && paperBusy.value === 'batch') {
    await new Promise((resolve) => globalThis.setTimeout(resolve, 300))
    if (generation !== paperBatchPollGeneration || paperBusy.value !== 'batch') return
    try {
      const batches = await trainingApi.listPaperBatches(draftId)
      const creating = batches.find((item) => item.status === 'creating')
      if (creating) paperBatch.value = creating
    } catch {
      // The create request remains authoritative; polling only exposes cancellation state.
    }
  }
}

async function cancelPaperBatch(): Promise<void> {
  if (!paperBatch.value || paperBatch.value.status !== 'creating' || paperCancelBusy.value) return
  paperCancelBusy.value = true
  try {
    paperBatch.value = await trainingApi.cancelPaperBatch(
      paperBatch.value.batch_run_id,
      requestToken(),
    )
    actionMessage.value = '已停止尚未开始的学生；已经生成的单人卷仍然保留。'
  } catch {
    errorMessage.value = '批量停止请求暂时未生效；请稍后从批次记录重新检查。'
  } finally {
    paperCancelBusy.value = false
  }
}

async function retryFailedPaperBatch(): Promise<void> {
  if (!paperBatch.value || paperBusy.value || !paperBatch.value.failures.length) return
  paperBusy.value = 'batch-retry'
  errorMessage.value = ''
  try {
    paperBatch.value = await trainingApi.retryPaperBatch(
      paperBatch.value.batch_run_id,
      paperBatch.value.failures.map((item) => item.student_id),
    )
    paperInstances.value = [
      ...paperBatch.value.items,
      ...paperInstances.value.filter((existing) => !paperBatch.value?.items.some(
        (item) => item.paper_instance_id === existing.paper_instance_id,
      )),
    ]
    actionMessage.value = paperBatch.value.failed_count
      ? `已保留成功卷；仍有 ${paperBatch.value.failed_count} 名学生需要处理。`
      : '失败学生已补齐，成功卷没有重复生成。'
  } catch (error) {
    errorMessage.value = safeError(error, '失败学生暂时无法重试；已完成卷保持不变。')
  } finally {
    paperBusy.value = ''
  }
}

async function downloadBatch(kind: 'bundle' | 'manifest' | 'frozen_bundle'): Promise<void> {
  const path = paperBatch.value?.downloads[kind]
  if (!path || paperBusy.value) return
  paperBusy.value = `batch-download:${kind}`
  try {
    const response = await trainingApi.downloadPaperArtifact(path)
    const url = URL.createObjectURL(response.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = kind === 'bundle'
      ? '个性化训练卷-批量DOCX版.zip'
      : kind === 'frozen_bundle'
        ? '个性化训练卷-批量冻结PDF.zip'
        : '个性化训练卷-生成清单.json'
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    errorMessage.value = '批量文件暂时无法下载，单人卷仍可分别下载。'
  } finally {
    paperBusy.value = ''
  }
}

async function downloadPaper(
  instance: PersonalizedPaperInstance,
  kind: 'review_docx' | 'frozen_pdf',
): Promise<void> {
  const path = instance.downloads[kind]
  if (!path) return
  paperBusy.value = `download:${instance.paper_instance_id}:${kind}`
  errorMessage.value = ''
  try {
    const response = await trainingApi.downloadPaperArtifact(path)
    const extension = kind === 'frozen_pdf' ? 'pdf' : 'docx'
    const url = URL.createObjectURL(response.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `个性化训练卷-${instance.student_name || instance.student_code || instance.student_id}-V${instance.series_version}.${extension}`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    errorMessage.value = '文件暂时无法下载，请稍后重试；已保存的版本不会受影响。'
  } finally {
    paperBusy.value = ''
  }
}

async function editItem(
  studentId: string,
  item: PersonalizedRecommendationItem,
  action: 'lock' | 'unlock' | 'exclude' | 'replace',
): Promise<void> {
  if (!draft.value || state.value === 'editing') return
  state.value = 'editing'
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    draft.value = await trainingApi.editPersonalizedDraft(
      draft.value.draft_id,
      {
        request_token: requestToken(),
        expected_revision: draft.value.revision,
        action,
        student_id: studentId,
        item_id: item.item_id,
        reason: editReason.value.trim() || '教师调整推荐草稿',
      },
    )
    state.value = 'ready'
    actionMessage.value = {
      lock: '已锁定该题，替换或排除前需先解锁。',
      unlock: '已解锁该题。',
      exclude: '已排除该题；空缺会保留，不会模糊补题。',
      replace: '已替换为下一道满足同一规则的题目，并保留原题历史。',
    }[action]
  } catch (error) {
    state.value = 'error'
    errorMessage.value = safeError(
      error,
      '草稿调整未保存，原草稿保持不变。',
    )
  }
}
</script>

<template>
  <section :class="['personalized-draft', { 'is-external-setup': externalSetup }]" :aria-labelledby="externalSetup ? undefined : 'personalized-draft-title'" :aria-label="externalSetup ? '个性化训练草稿' : undefined">
    <header v-if="!externalSetup"><h3 id="personalized-draft-title">{{ effectivePaperMode === 'shared' ? '多人同题草稿' : '一人一卷草稿' }}</h3></header>
    <details v-if="!externalSetup" class="personalized-settings" :open="!draft"><summary>训练设置</summary><section>
      <label class="personalized-edit-reason">难度上限<input v-model.number="difficultyMax" class="app-input" type="number" min="1" max="10"></label>
      <fieldset v-if="targetOptions.length && targetKeys === undefined" class="personalized-targets"><legend>本次训练目标</legend><label v-for="target in targetOptions" :key="target.key"><input v-model="selectedTargets" type="checkbox" :value="target.key">{{ target.label }}</label><p v-if="!selectedTargets.length">请勾选至少一个知识点。</p></fieldset>
      <fieldset v-else-if="targetOptions.length" class="personalized-targets"><legend>已选目标</legend><span v-for="target in selectedTargetLabels" :key="target">{{ knowledgeLeafLabel(target) }}</span><p v-if="!selectedTargets.length">请在知识结构中选择目标。</p></fieldset>
      <p v-else class="training-empty is-compact">当前范围暂无可用掌握证据。</p>
      <AppButton variant="primary" data-testid="generate-personalized-draft" :disabled="!canGenerate" @click="generate">{{ state === 'loading' ? '正在生成并核对…' : pendingRequestToken ? '核对生成结果' : '生成个性化草稿' }}</AppButton>
    </section></details>
    <p v-if="errorMessage" class="training-feedback is-warning" role="alert">{{ errorMessage }}</p>

    <template v-if="draft">
      <header class="draft-workflow-heading"><StepProgress :steps="workspaceSteps" :current="viewStep" @select="selectWorkspaceStep" /><span>自动保存 · V{{ draft.revision }}</span></header>

      <section v-show="viewStep === 'review'" class="draft-review">
        <div class="personalized-draft-toolbar">
          <strong>{{ effectivePaperMode === 'shared' ? `共同试题 · ${draft.students.length} 人` : `${draft.students.length} 份个人草稿` }}</strong>
          <details class="draft-data-notes"><summary>选题设置</summary><p>每卷 {{ draft.config.question_count ?? questionCount }} 题 · 难度 ≤ {{ draft.config.difficulty_max ?? difficultyMax }} 级 · 同技能 ≤ {{ draft.config.max_questions_per_skill ?? maxQuestionsPerSkill ?? 1 }} 道 · 解答题 ≤ {{ draft.config.max_written_questions ?? maxWrittenQuestions ?? 2 }} 道 · 排除最近 {{ draft.config.recent_activity_count ?? recentActivityCount ?? 3 }} 次原题</p></details>
          <details v-if="draft.warnings.length" class="draft-data-notes"><summary>数据说明（{{ draft.warnings.length }}）</summary><ul><li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li></ul></details>
          <template v-if="canDiscardDraft"><AppButton v-if="!confirmDiscard" variant="ghost" data-testid="discard-paper-draft" @click="confirmDiscard = true">放弃草稿</AppButton><template v-else><span>放弃后不再自动恢复，出卷设置保留。</span><AppButton variant="secondary" data-testid="confirm-discard-paper-draft" @click="discardDraft">确认放弃</AppButton><AppButton variant="ghost" @click="confirmDiscard = false">取消</AppButton></template></template>
          <AppButton variant="primary" :disabled="!eligiblePaperCount || Boolean(paperBusy)" @click="viewStep = 'print'">{{ isHandout ? '去导出讲义' : hasPrintablePapers ? '查看打印试卷' : '审核完成，去出卷' }}</AppButton>
        </div>
        <details v-if="effectivePaperMode !== 'shared'" class="draft-edit-settings"><summary>调整原因</summary><label class="personalized-edit-reason">调整原因<input v-model="editReason" class="app-input" maxlength="500"></label></details>
        <div class="personalized-workbench" :class="{ 'is-shared': effectivePaperMode === 'shared' }">
          <nav class="personalized-student-list" :aria-label="effectivePaperMode === 'shared' ? '同卷学生列表' : '一人一卷学生列表'"><header>{{ effectivePaperMode === 'shared' ? '共同练习学生' : '学生草稿' }} <span>{{ draft.students.length }} 人</span></header><button v-for="student in draft.students" :key="student.student_id" type="button" :class="{ 'is-selected': selectedDraftStudent?.student_id === student.student_id }" :aria-pressed="selectedDraftStudent?.student_id === student.student_id" @click="selectedDraftStudentId = student.student_id"><strong>{{ student.student_name || student.student_code || student.student_id }}</strong><small>{{ student.class_id }} · {{ student.student_code || '' }}</small><span>{{ instancesForStudent(student.student_id)[0]?.status === 'frozen' ? '已出卷' : instancesForStudent(student.student_id).length ? '待审核' : `${student.items.length} 题草稿` }}</span></button></nav>
          <template v-for="student in draft.students" :key="student.student_id"><article v-if="selectedDraftStudent?.student_id === student.student_id" class="personalized-student">
            <header><div><strong>{{ effectivePaperMode === 'shared' ? '共同试题' : student.student_name || student.student_code || student.student_id }}</strong><span>{{ student.items.length }} 题<span v-if="student.items.length"> · {{ difficultySummary(student.items) }}</span></span></div><StatusBadge :tone="student.selection_mode === 'maintenance_fallback' ? 'warning' : 'info'" :label="student.selection_mode === 'teacher_fixed_class' ? '教师固定选题' : student.selection_mode === 'maintenance_fallback' ? '保守复习' : '按掌握证据推荐'" /></header>
            <p v-if="draft.config.assembly_source" class="personalized-shared-note">班级组卷 · 全班：{{ (draft.config.assembly_source as {title?:string}).title }}。全部学生使用同一套题与题序；换题请返回班级组卷调整后重新生成。</p>
            <p v-else-if="effectivePaperMode === 'shared'" class="personalized-shared-note">全组题目与题序相同；换题需调整设置后重新生成整组草稿。</p>
            <ol class="personalized-match-list">
              <li v-for="item in student.items" :key="item.item_id" class="personalized-match">
                <div class="personalized-match__head"><strong>第 {{ item.item_order }} 题</strong><span class="personalized-stage-badge" :class="`is-${item.stage}`">{{ itemLabel(item) }}</span><span>{{ knowledgeLeafLabel(item.matched_name) }}</span><span class="personalized-match__difficulty">难度 {{ item.difficulty }} · {{ item.criterion_point_count }} 个判定点</span></div>
                <p class="personalized-match__stem">{{ item.question_text || '旧草稿未包含题干，请预览原题。' }}</p>
                <div class="personalized-item-actions">
                  <AppButton variant="ghost" @click="openPreview(item.question_id, '推荐题预览')">预览</AppButton>
                  <template v-if="effectivePaperMode !== 'shared'"><AppButton variant="ghost" :disabled="state === 'editing'" @click="editItem(student.student_id, item, item.locked ? 'unlock' : 'lock')">{{ item.locked ? '解锁' : '锁定' }}</AppButton><AppButton variant="ghost" :disabled="item.locked || state === 'editing'" @click="editItem(student.student_id, item, 'replace')">替换</AppButton><AppButton variant="ghost" :disabled="item.locked || state === 'editing'" @click="editItem(student.student_id, item, 'exclude')">排除</AppButton></template>
                  <details class="personalized-match__evidence"><summary>推荐依据与来源</summary><p>{{ item.reason }}</p><p>{{ item.matched_name }}</p><p>{{ item.source_paper }} · 原卷第 {{ item.question_number }} 题<span v-if="item.difficulty_band"> · {{ { starter: '起步练习', consolidation: '巩固练习', stretch: '少量突破' }[item.difficulty_band] }}</span></p><p v-if="item.part_assessment">小问难度（1–10）：{{ item.part_assessment.parts.map((part, index) => `${part.label || `(${index + 1})`} ${part.difficulty ?? '暂无'}${part.direct_keys.includes(item.matched_key) ? ' · 本次目标' : ''}`).join('；') }}。按整题出卷。</p><p v-if="item.relation">已确认{{ item.relation.relation_type === 'prerequisite' ? '先修' : '相关' }}关系：{{ item.relation.rationale }}</p>
                    <strong>{{ item.practice_purpose === 'new' ? '新练习依据' : item.practice_purpose === 'consolidation' ? '巩固依据' : item.selection_kind === 'supplement' ? '补充依据' : '错题依据' }}</strong>
                    <template v-if="evidenceDisplayFor(student.student_id, item).refs.length"><div v-for="evidence in evidenceDisplayFor(student.student_id, item).refs" :key="`${evidence.session_id}-${evidence.question_id}-${evidence.bank_question_id}`"><span>{{ evidenceRefLabel(evidence) }}</span><AppButton v-if="evidence.bank_question_id" variant="ghost" @click="openPreview(evidence.bank_question_id, '作答原题')">预览原题</AppButton></div></template><small v-else>{{ item.selection_kind === 'supplement' ? '范围内的新练习，不认定为已证实薄弱。' : '当前范围暂无逐题失分记录。' }}</small>
                  </details>
                </div>
              </li>
            </ol>
            <div v-if="student.shortages.length" class="personalized-shortages"><strong>{{ draft.config.remediation_only && effectivePaperMode === 'individual' ? `当前可配训练题 ${student.items.length} 题` : `尚未配齐，还缺 ${student.shortages.reduce((sum, item) => sum + (Number(item.missing_count) || 0), 0)} 题` }}</strong><p>{{ draft.config.remediation_only && effectivePaperMode === 'individual' ? (Number(draft.config.max_unmeasured_questions ?? 0) > 0 ? `优先补弱，再加入最多 ${draft.config.max_unmeasured_questions} 道未测目标新练习；新练习不计作薄弱点覆盖。题量不足保留缺口。` : '只练已有失分需要；题量不足保留缺口，可展开选题说明核对原因。') : '可返回设置调整范围、题量或近期原题排除次数。' }}</p><details><summary>缺题详情</summary><p v-for="shortage in student.shortages" :key="String(shortage.stage)">{{ stageLabel(shortageStage(shortage)) }} · 距题量上限少 {{ Number(shortage.missing_count) || 0 }} 题</p></details></div>
            <details v-if="student.warnings.length" class="draft-data-notes"><summary>选题说明（{{ student.warnings.length }}）</summary><ul><li v-for="warning in student.warnings" :key="warning">{{ warning }}</li></ul></details>
          </article></template>
        </div>
      </section>

      <section v-show="viewStep === 'print'" class="personalized-print">
        <section v-if="isHandout" class="personalized-batch-panel" aria-label="讲义导出"><div><h3>{{ effectivePaperMode === 'shared' ? '小组讲义' : '一人一份讲义' }}</h3><p>Word 格式，答案解析在末尾；讲义只打印，不回收、不更新掌握度。</p></div><AppButton variant="primary" data-testid="export-handout" :disabled="Boolean(paperBusy) || !eligiblePaperCount" @click="exportHandout">{{ paperBusy === 'handout' ? '正在导出讲义…' : handoutRequestToken && (!handoutJob || ['queued', 'running'].includes(handoutJob.status)) ? '核对导出结果' : '导出讲义' }}</AppButton><p v-if="handoutJob && ['queued', 'running'].includes(handoutJob.status)" role="status">正在导出讲义 · {{ Math.round(handoutJob.progress * 100) }}%</p><AppButton v-if="handoutJob?.result.download_url" variant="secondary" :disabled="Boolean(paperBusy)" @click="downloadHandout">下载讲义 {{ effectivePaperMode === 'shared' ? 'Word' : 'ZIP' }}</AppButton></section>
        <section v-else class="personalized-batch-panel" aria-labelledby="personalized-batch-title">
          <div><h3 id="personalized-batch-title">{{ hasPrintablePapers ? `${paperBatch?.succeeded_count ?? paperInstances.filter(item => item.status === 'frozen').length} 份 PDF 试卷已就绪` : effectivePaperMode === 'shared' ? `生成 ${draft.students.length} 份实名同题卷` : `生成 ${draft.students.length} 份个人训练卷` }}</h3><p>每人一份 PDF，每页带回收身份码。</p></div>
          <AppButton v-if="!hasPrintablePapers" variant="primary" :disabled="Boolean(paperBusy) || !eligiblePaperCount" @click="createPaperBatch">{{ paperBusy === 'batch' ? '正在逐人生成 PDF…' : '生成全部 PDF 试卷（可直接打印）' }}</AppButton>
          <AppButton v-if="paperBatch?.downloads.frozen_bundle" variant="primary" :disabled="Boolean(paperBusy)" @click="downloadBatch('frozen_bundle')">下载试卷 PDF ZIP</AppButton>
          <AppButton v-if="hasPrintablePapers" variant="secondary" @click="viewStep = 'scan'">回收答卷并批改</AppButton>
          <AppButton v-if="paperBatch?.status === 'creating'" variant="secondary" :disabled="paperCancelBusy" @click="cancelPaperBatch">{{ paperCancelBusy ? '正在停止…' : '停止未开始学生' }}</AppButton>
          <div v-if="paperBatch" class="personalized-batch-result"><span>成功 {{ paperBatch.succeeded_count }} / {{ paperBatch.requested_count }} 人</span><AppButton v-if="paperBatch.failures.length" variant="secondary" :disabled="Boolean(paperBusy)" @click="retryFailedPaperBatch">{{ paperBusy === 'batch-retry' ? '正在重试失败学生…' : `只重试失败的 ${paperBatch.failures.length} 人` }}</AppButton><ul v-if="paperBatch.failures.length"><li v-for="failure in paperBatch.failures" :key="failure.student_id">{{ draft.students.find(student => student.student_id === failure.student_id)?.student_name || failure.student_id }}：生成失败</li></ul></div>
          <details v-if="hasPrintablePapers" class="print-more"><summary>其他导出与重新出卷</summary><AppButton v-if="paperBatch?.downloads.bundle" variant="secondary" :disabled="Boolean(paperBusy)" @click="downloadBatch('bundle')">下载 DOCX 版 ZIP</AppButton><AppButton variant="secondary" :disabled="Boolean(paperBusy) || !eligiblePaperCount" @click="createPaperBatch">{{ paperBusy === 'batch' ? '正在逐人生成 PDF…' : '重新生成全部 PDF' }}</AppButton></details>
        </section>
        <div v-if="!isHandout && paperInstances.length" class="paper-download-list"><header><strong>学生试卷</strong><span>保留各版本，可分别下载</span></header><template v-for="student in draft.students" :key="student.student_id"><article v-for="instance in instancesForStudent(student.student_id)" :key="instance.paper_instance_id"><div><strong>{{ student.student_name || student.student_code || student.student_id }}</strong><span>V{{ instance.series_version }} · {{ instance.question_count }} 题 · {{ instance.pages.length }} 页</span></div><StatusBadge :tone="instance.status === 'frozen' ? 'success' : 'warning'" :label="instance.status === 'frozen' ? '可打印' : '待生成 PDF'" /><AppButton v-if="instance.downloads.frozen_pdf" variant="ghost" :disabled="Boolean(paperBusy)" @click="downloadPaper(instance, 'frozen_pdf')">下载 PDF 试卷</AppButton><AppButton v-if="instance.downloads.review_docx" variant="ghost" :disabled="Boolean(paperBusy)" @click="downloadPaper(instance, 'review_docx')">下载 DOCX</AppButton><p v-if="instance.formula_fallbacks?.length" class="formula-note">{{ instance.formula_fallbacks.length }} 处公式已保留原式或题图，打印前请预览核对。</p></article></template></div>
        <p v-if="actionMessage" class="draft-action-message" role="status">{{ actionMessage }}</p>
      </section>
      <section v-if="!isHandout && hasPrintablePapers" v-show="viewStep === 'scan'" class="personalized-scan"><TrainingScanBatchPanel :instances="paperInstances" @progress-change="scanProgress = $event" @open-draft="openNextDraft" /></section>
    </template>
    <p v-else-if="actionMessage" class="draft-action-message" role="status">{{ actionMessage }}</p>
    <QuestionPreviewDialog :question-id="previewQuestionId" :title="previewTitle" @close="closePreview" />
  </section>
</template>

<style scoped>
.personalized-draft{min-width:0;background:var(--color-bg-surface);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);padding:var(--space-5)}
.personalized-draft.is-external-setup{border:0;padding:0;background:transparent}
.personalized-draft h3,.personalized-draft p{margin:0}
.draft-workflow-heading{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);padding:var(--space-3) 0 var(--space-4);border-bottom:1px solid var(--color-border-default);margin-bottom:var(--space-4)}
.draft-workflow-heading>span{font-size:var(--font-size-caption);color:var(--color-text-muted);white-space:nowrap}
.personalized-draft-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-3);margin-bottom:var(--space-4);font-size:var(--font-size-dense)}
.personalized-draft-toolbar>button:first-of-type{margin-left:auto}
.draft-data-notes,.draft-edit-settings{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.draft-data-notes summary,.draft-edit-settings summary{cursor:pointer}
.draft-data-notes ul{padding-left:var(--space-5);font-size:var(--font-size-caption);max-width:80ch}
.personalized-draft-toolbar .draft-data-notes{max-width:100%}
.draft-edit-settings{margin-bottom:var(--space-3)}
.personalized-edit-reason{display:grid;grid-template-columns:80px minmax(0,1fr);gap:var(--space-3);align-items:center;margin:var(--space-3) 0;font-size:var(--font-size-dense)}
.personalized-workbench{display:grid;grid-template-columns:200px minmax(0,1fr);align-items:start;gap:var(--space-5)}
.personalized-student-list{position:sticky;top:var(--space-4);min-width:0;border:1px solid var(--color-border-default);border-radius:var(--radius-control);overflow:hidden}
.personalized-student-list header{display:flex;justify-content:space-between;gap:var(--space-2);padding:var(--space-3);background:var(--color-bg-subtle);font-size:var(--font-size-dense)}
.personalized-student-list header span{color:var(--color-text-muted)}
.personalized-student-list button{display:grid;gap:var(--space-1);width:100%;padding:var(--space-3);border:0;border-top:1px solid var(--color-border-subtle);background:var(--color-bg-surface);text-align:left;color:var(--color-text-primary);font:inherit;font-size:var(--font-size-dense);cursor:pointer}
.personalized-student-list button.is-selected{box-shadow:inset 3px 0 var(--color-accent);background:var(--color-accent-subtle)}
.personalized-student-list small,.personalized-student-list button>span{font-size:var(--font-size-caption);color:var(--color-text-secondary)}
.personalized-student{min-width:0}
.personalized-student>header{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3);padding-bottom:var(--space-3)}
.personalized-student>header>div{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--space-3)}
.personalized-student>header span{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.personalized-shared-note{font-size:var(--font-size-caption);color:var(--color-text-muted);margin-bottom:var(--space-3)!important}
.personalized-match-list{list-style:none;padding:0;margin:0}
.personalized-match{padding:var(--space-4) 0;border-top:1px solid var(--color-border-default)}
.personalized-match__head{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-2) var(--space-3);font-size:var(--font-size-dense)}
.personalized-match__head>span:not(.personalized-stage-badge){color:var(--color-text-secondary)}
.personalized-match__difficulty{margin-left:auto;font-size:var(--font-size-caption)}
.personalized-stage-badge{padding:2px var(--space-2);border-radius:var(--radius-tag);font-size:var(--font-size-caption);background:var(--color-accent-subtle);color:var(--color-accent)}
.personalized-stage-badge.is-prerequisite{background:var(--color-warning-subtle);color:var(--color-warning)}
.personalized-stage-badge.is-transfer{background:var(--color-ai-subtle);color:var(--color-ai)}
.personalized-match__stem{padding:var(--space-3) 0;font:16px/1.7 var(--font-family-document);white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto}
.personalized-item-actions{display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:start}
.personalized-match__evidence{flex:1;min-width:200px;font-size:var(--font-size-caption);color:var(--color-text-secondary);margin-left:auto;padding:var(--space-2) 0}
.personalized-match__evidence summary{color:var(--color-accent);cursor:pointer;text-align:right}
.personalized-match__evidence[open]{flex-basis:100%;border-top:1px solid var(--color-border-subtle);padding-top:var(--space-3);line-height:var(--line-height-relaxed)}
.personalized-match__evidence p{margin:var(--space-2) 0}
.personalized-match__evidence>div{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-2)}
.personalized-shortages{margin:var(--space-4) 0;padding:var(--space-4);border-radius:var(--radius-control);background:var(--color-warning-subtle);color:var(--color-warning);font-size:var(--font-size-dense)}
.personalized-shortages p{margin-top:var(--space-2);color:var(--color-text-secondary)}
.personalized-shortages details{margin-top:var(--space-3)}.personalized-shortages summary{cursor:pointer}
.personalized-batch-panel{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-3);padding:var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);box-shadow:var(--shadow-raised)}
.personalized-batch-panel>div:first-child{flex:1 1 300px}
.personalized-batch-panel h3{font-size:var(--font-size-h2);margin-bottom:var(--space-2)}
.personalized-batch-panel p,.personalized-batch-result{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.personalized-batch-result{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-3);flex-basis:100%;padding-top:var(--space-3);border-top:1px solid var(--color-border-subtle)}
.print-more{flex-basis:100%;font-size:var(--font-size-dense);color:var(--color-text-muted)}.print-more summary{cursor:pointer}.print-more button{margin:var(--space-3) var(--space-3) 0 0}
.paper-download-list{margin-top:var(--space-5);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);overflow:hidden;background:var(--color-bg-surface)}
.paper-download-list>header{display:flex;justify-content:space-between;padding:var(--space-3) var(--space-4);border-bottom:1px solid var(--color-border-default);background:var(--color-bg-subtle);font-size:var(--font-size-dense)}
.paper-download-list header>span{font-size:var(--font-size-caption);color:var(--color-text-muted)}
.paper-download-list article{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-4);border-bottom:1px solid var(--color-border-subtle)}
.paper-download-list article>div{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--space-3);flex:1 1 260px;font-size:var(--font-size-dense)}
.paper-download-list article>div span{color:var(--color-text-muted);font-size:var(--font-size-caption)}
.formula-note{flex-basis:100%;font-size:var(--font-size-caption);color:var(--color-warning)}
.draft-action-message{font-size:var(--font-size-dense);color:var(--color-text-secondary);margin-top:var(--space-3)!important}
.personalized-settings{padding:var(--space-3);margin-bottom:var(--space-4)}.personalized-settings summary{cursor:pointer}
.personalized-targets{display:flex;flex-wrap:wrap;gap:var(--space-3);margin-bottom:var(--space-3);border:1px solid var(--color-border-default);font-size:var(--font-size-dense)}
@media(max-width:760px){.draft-workflow-heading{flex-wrap:wrap}.personalized-workbench{grid-template-columns:minmax(0,1fr)}.personalized-student-list{display:flex;overflow:auto;position:static}.personalized-student-list header{display:none}.personalized-student-list button{flex:0 0 175px;border-right:1px solid var(--color-border-default);border-top:0}.personalized-student-list button.is-selected{box-shadow:inset 0 -3px var(--color-accent)}.personalized-match__head{gap:var(--space-2)}.personalized-match__difficulty{margin-left:0;flex-basis:100%}.personalized-batch-panel{padding:var(--space-4)}.personalized-batch-panel>button{flex:1 1 auto}.personalized-draft-toolbar>button:first-of-type{margin-left:0}.personalized-match__evidence{min-width:150px}.draft-workflow-heading :deep(.step-progress__connector){flex-basis:10px;margin-inline:var(--space-1)}}
</style>
