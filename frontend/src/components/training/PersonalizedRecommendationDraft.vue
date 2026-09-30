<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
import TrainingScanBatchPanel from './TrainingScanBatchPanel.vue'
import QuestionPreviewDialog from './QuestionPreviewDialog.vue'
import {
  clearPaperDraftSession,
  loadPaperDraftSession,
  savePaperDraftSession,
} from '../../features/training/paper-draft-session'

const props = defineProps<{
  diagnosis: TrainingDiagnosis | null
  scope: TrainingStudentScopeRequest
  examScope: TrainingExamScopeRequest
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
  && props.questionCount >= 8
  && props.questionCount <= 12
  && difficultyMax.value >= 1
  && difficultyMax.value <= 8
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
))

// 出卷设置指纹：设置一致时才恢复上次草稿，设置变了必须重新生成。
// rulesVersion 随选题规则升级递增，避免恢复规则升级前的旧草稿。
const settingsFingerprint = computed(() => JSON.stringify({
  rulesVersion: 10,
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
  })
}

let restoring = false
async function restoreDraft(): Promise<void> {
  if (restoring || draft.value || state.value !== 'idle' || !props.diagnosis) return
  const stored = loadPaperDraftSession()
  if (!stored) return
  let previousRules = false
  if (stored.fingerprint !== settingsFingerprint.value) {
    try {
      const previous = JSON.parse(stored.fingerprint)
      previousRules = Number(previous.rulesVersion) < 10
      if (!previousRules) return
      previous.rulesVersion = 10
      for (const key of ['trainingIntent', 'expectedMinutes', 'difficultyMin', 'stageRatios']) delete previous[key]
      previous.teachingProgressChapterId ??= ''
      previous.difficultyMax = difficultyMax.value
      if (JSON.stringify(previous) !== settingsFingerprint.value) return
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
    state.value = 'ready'
    actionMessage.value = previousRules
      ? '已恢复原草稿供查看；选题规则已更新，未冻结的草稿需按新规则重新生成，已生成训练卷仍可查看。'
      : '已恢复上次生成的草稿，可继续审核。'
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
    paperBatchPollGeneration += 1
    selectedTargets.value = [...(props.targetKeys ?? [])]
  },
  { immediate: true },
)

watch(
  [settingsFingerprint, () => props.diagnosis],
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
  if (item.match_level && item.match_label) return `${item.match_level}级 · ${item.match_label}`
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
  actionMessage.value = '草稿已生成并自动暂存，切页后返回会自动恢复；尚未形成正式训练卷。'
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
      difficulty_max: difficultyMax.value,
      paper_mode: props.paperMode ?? 'individual',
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

async function createPaperBatch(): Promise<void> {
  if (!draft.value || paperBusy.value) return
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
  <section
    :class="['personalized-draft', { 'is-external-setup': externalSetup }]"
    :aria-labelledby="externalSetup ? undefined : 'personalized-draft-title'"
    :aria-label="externalSetup ? '个性化训练草稿' : undefined"
  >
    <header v-if="!externalSetup">
      <div>
        <p class="training-eyebrow">P4 · 教师确认后出卷</p>
        <h3 id="personalized-draft-title">{{ paperMode === 'shared' ? '多人同题草稿' : '一人一卷草稿' }}</h3>
        <p>{{ paperMode === 'shared'
          ? '题目和顺序一致，但每名学生仍保留独立姓名、二维码与回收身份。'
          : '按每名学生证据分别选题；题目不足时会明确保留空缺。' }}</p>
      </div>
      <span v-if="draft">版本 {{ draft.revision }}</span>
    </header>

    <details v-if="!externalSetup" class="personalized-settings" :open="!draft">
      <summary>训练设置</summary>
      <section>
    <div class="personalized-controls">
      <label>
        难度上限
        <input v-model.number="difficultyMax" type="number" min="1" max="8">
      </label>
      <p>依据同技能多次作答匹配难度；允许巩固与新练习，每份训练同技能最多1道、解答题最多2道，最高8级。</p>
    </div>

    <fieldset v-if="targetOptions.length && targetKeys === undefined" class="personalized-targets">
      <legend>本次训练目标</legend>
      <label v-for="target in targetOptions" :key="target.key">
        <input v-model="selectedTargets" type="checkbox" :value="target.key">
        {{ target.label }}
      </label>
      <p v-if="!selectedTargets.length">请先人工勾选至少一个知识点；系统不会替教师默认决定训练重点。</p>
    </fieldset>
    <fieldset v-else-if="targetOptions.length" class="personalized-targets is-summary">
      <legend>从知识结构中已选目标</legend>
      <span v-for="target in selectedTargetLabels" :key="target">{{ target }}</span>
      <p v-if="!selectedTargets.length">请回到上方知识结构，人工勾选至少一个知识点。</p>
    </fieldset>
    <p v-else class="training-empty is-compact">
      当前没有可确认的薄弱目标；一人一卷需要范围内已有掌握证据才会配题。
    </p>

    <AppButton variant="secondary"
      data-testid="generate-personalized-draft"
      :disabled="!canGenerate"
      @click="generate"
    >
      {{ state === 'loading' ? '正在生成并核对…' : pendingRequestToken ? '核对生成结果' : '生成个性化草稿' }}
    </AppButton>
      </section>
    </details>

    <p v-if="actionMessage" class="training-feedback" role="status">
      {{ actionMessage }}
    </p>
    <p v-if="errorMessage" class="training-feedback is-warning" role="alert">
      {{ errorMessage }}
    </p>

    <template v-if="draft">
      <div class="personalized-draft-toolbar">
        <span class="personalized-draft-toolbar__status">
          草稿已自动暂存（版本 {{ draft.revision }}）；切到其他页面再回来会自动恢复，不需要重新勾选。
        </span>
        <template v-if="canDiscardDraft">
          <AppButton variant="ghost"
            v-if="!confirmDiscard"
            data-testid="discard-paper-draft"
            @click="confirmDiscard = true"
          >
            放弃草稿
          </AppButton>
          <template v-else>
            <span class="personalized-draft-toolbar__confirm">
              确认放弃？该草稿不再自动恢复；勾选与出卷设置会保留。
            </span>
            <AppButton variant="secondary"
              data-testid="confirm-discard-paper-draft"
              @click="discardDraft"
            >
              确认放弃
            </AppButton>
            <AppButton variant="ghost" @click="confirmDiscard = false">取消</AppButton>
          </template>
        </template>
        <small v-else class="personalized-draft-toolbar__locked">
          已生成训练卷后不能再放弃草稿；如需重来请调整设置后生成新草稿。
        </small>
      </div>
      <label class="personalized-edit-reason">
        调整原因
        <input v-model="editReason" maxlength="500">
      </label>

      <ul v-if="draft.warnings.length" class="training-warning-list">
        <li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li>
      </ul>

      <div class="personalized-workbench">
      <nav class="personalized-student-list" :aria-label="paperMode === 'shared' ? '同卷学生列表' : '一人一卷学生列表'">
        <strong>学生与状态</strong>
        <button
          v-for="student in draft.students"
          :key="student.student_id"
          type="button"
          :class="{ 'is-selected': selectedDraftStudent?.student_id === student.student_id }"
          @click="selectedDraftStudentId = student.student_id"
        >
          <span>{{ student.student_name || student.student_code || student.student_id }}</span>
          <small v-if="paperMode === 'shared'">{{ student.class_id }} · {{ student.student_code || student.student_id }}</small>
          <small>
            {{ instancesForStudent(student.student_id)[0]?.status === 'frozen'
              ? '已冻结'
              : instancesForStudent(student.student_id).length ? '待审核' : `${student.items.length} 题草稿` }}
          </small>
        </button>
      </nav>

      <template v-for="student in draft.students" :key="student.student_id">
      <article v-if="selectedDraftStudent?.student_id === student.student_id" class="personalized-student">
        <header>
          <div>
            <strong>{{ student.student_name || student.student_code || student.student_id }}</strong>
            <span v-if="paperMode === 'shared'">{{ student.class_id }} · {{ student.student_code || student.student_id }}</span>
            <span>
              {{ student.items.length }} 题
            </span>
            <span v-if="student.items.length">{{ difficultySummary(student.items) }}</span>
          </div>
          <small>
            {{ student.selection_mode === 'maintenance_fallback' ? '保守复习' : '按掌握证据推荐' }}
            <template v-if="!externalSetup && student.targets.length">
              · 细点 {{ student.targets.map((item) => String(item.display_name || item.stable_key || '')).filter(Boolean).join('、') }}
            </template>
          </small>
        </header>

        <template v-if="externalSetup">
          <ol class="personalized-match-list">
            <li v-for="item in student.items" :key="item.item_id" class="personalized-match">
              <div class="personalized-match__evidence">
                <strong>{{ item.practice_purpose === 'new' ? '新练习依据' : item.practice_purpose === 'consolidation' ? '巩固依据' : item.selection_kind === 'supplement' ? '补充依据' : '错题依据' }}</strong>
                <template v-if="evidenceDisplayFor(student.student_id, item).representative">
                  <span title="同目标作答依据">
                    {{ evidenceRefLabel(evidenceDisplayFor(student.student_id, item).representative!) }}
                  </span>
                  <AppButton variant="ghost"
                    v-if="evidenceDisplayFor(student.student_id, item).representative!.bank_question_id"
                    @click="openPreview(evidenceDisplayFor(student.student_id, item).representative!.bank_question_id, '作答原题')"
                  >
                    预览
                  </AppButton>
                  <details
                    v-if="evidenceDisplayFor(student.student_id, item).refs.length > 1"
                    class="personalized-match__evidence-all"
                  >
                    <summary>全部 {{ evidenceDisplayFor(student.student_id, item).refs.length }} 条依据</summary>
                    <span
                      v-for="evidence in evidenceDisplayFor(student.student_id, item).refs"
                      :key="`${evidence.session_id}-${evidence.question_id}-${evidence.bank_question_id}`"
                    >
                      {{ evidenceRefLabel(evidence) }}
                      <AppButton variant="ghost"
                        v-if="evidence.bank_question_id"
                        @click="openPreview(evidence.bank_question_id, '作答原题')"
                      >
                        预览
                      </AppButton>
                    </span>
                  </details>
                </template>
                <small v-else-if="item.selection_kind === 'supplement'">范围内的新练习，不认定为已证实薄弱；不设固定比例。</small>
                <small v-else>该细点在当前范围内暂无逐题失分记录</small>
              </div>
              <span class="personalized-match__arrow" aria-hidden="true">→</span>
              <div class="personalized-match__card">
                <div class="personalized-match__head">
                  <span class="personalized-match__order">第 {{ item.item_order }} 题</span>
                  <span class="personalized-stage-badge" :class="`is-${item.stage}`">{{ itemLabel(item) }}</span>
                  <strong :title="item.matched_name">{{ knowledgeLeafLabel(item.matched_name) }}</strong>
                  <small v-if="knowledgeLeafLabel(item.matched_name) !== item.matched_name">{{ item.matched_name }}</small>
                </div>
                <p
                  class="personalized-match__stem"
                  :class="{ 'is-empty': !item.question_text }"
                  :title="item.question_text || undefined"
                >
                  {{ item.question_text || '旧草稿未包含题干，重新生成后可见' }}
                </p>
                <small class="personalized-match__meta">
                  <template v-if="item.difficulty_band">{{ { starter: '起步练习', consolidation: '巩固练习', stretch: '少量突破' }[item.difficulty_band] }} · </template>
                  原卷第 {{ item.question_number }} 题 · {{ item.part_assessment ? '最难小问' : '整题难度' }} {{ item.difficulty }} ·
                  {{ item.criterion_point_count }} 个判定点 · 来源：{{ item.source_paper }}
                </small>
                <small v-if="item.part_assessment" class="personalized-match__meta">
                  小问公式难度（1–10）：{{ item.part_assessment.parts.map((part, index) => `${part.label || `(${index + 1})`} ${part.difficulty ?? '暂无'}${part.direct_keys.includes(item.matched_key) ? ' · 本次目标' : ''}`).join('；') }}。按整题出卷。
                </small>
                <small v-if="item.relation">
                  已确认{{ item.relation.relation_type === 'prerequisite' ? '先修' : '相关' }}关系：
                  {{ item.relation.rationale }}
                </small>
                <small class="personalized-match__reason">
                  推荐理由：{{ item.reason }}
                </small>
                <div v-if="paperMode !== 'shared'" class="personalized-item-actions">
                  <AppButton variant="ghost"
                    @click="openPreview(item.question_id, '推荐题预览')"
                  >
                    预览
                  </AppButton>
                  <AppButton variant="ghost"
                    :disabled="state === 'editing'"
                    @click="editItem(student.student_id, item, item.locked ? 'unlock' : 'lock')"
                  >
                    {{ item.locked ? '解锁' : '锁定' }}
                  </AppButton>
                  <AppButton variant="ghost"
                    :disabled="item.locked || state === 'editing'"
                    @click="editItem(student.student_id, item, 'replace')"
                  >
                    替换
                  </AppButton>
                  <AppButton variant="ghost"
                    :disabled="item.locked || state === 'editing'"
                    @click="editItem(student.student_id, item, 'exclude')"
                  >
                    排除
                  </AppButton>
                </div>
                <p v-else class="personalized-shared-note">同题模式不允许只改某一名学生；如需换题，请调整设置后重新生成整组草稿。</p>
              </div>
            </li>
          </ol>
          <ul v-if="student.shortages.length" class="personalized-shortages">
            <li
              v-for="shortage in student.shortages"
              :key="String(shortage.stage)"
              class="personalized-shortage"
            >
              <span class="personalized-stage-badge" :class="`is-${shortageStage(shortage)}`">
                {{ stageLabel(shortageStage(shortage)) }}
              </span>
              <strong>待配 {{ Number(shortage.missing_count) || 0 }} 题</strong>
            </li>
          </ul>
        </template>

        <ol v-else>
          <li v-for="item in student.items" :key="item.item_id">
            <div class="personalized-item-main">
              <span>第 {{ item.item_order }} 题 · {{ itemLabel(item) }}</span>
              <strong>{{ item.matched_name }}</strong>
              <p>推荐理由：{{ item.reason }}</p>
              <small>
                原卷第 {{ item.question_number }} 题 ·
                {{ item.part_assessment ? '最难小问' : '整题难度' }} {{ item.difficulty }} ·
                {{ item.criterion_point_count }} 个判定点
              </small>
              <small v-if="item.part_assessment">
                小问公式难度（1–10）：{{ item.part_assessment.parts.map((part, index) => `${part.label || `(${index + 1})`} ${part.difficulty ?? '暂无'}${part.direct_keys.includes(item.matched_key) ? ' · 本次目标' : ''}`).join('；') }}。按整题出卷。
              </small>
              <small v-if="item.relation">
                已确认{{ item.relation.relation_type === 'prerequisite' ? '先修' : '相关' }}关系：
                {{ item.relation.rationale }}
              </small>
            </div>
            <div v-if="paperMode !== 'shared'" class="personalized-item-actions">
              <AppButton variant="ghost"
                :disabled="state === 'editing'"
                @click="editItem(student.student_id, item, item.locked ? 'unlock' : 'lock')"
              >
                {{ item.locked ? '解锁' : '锁定' }}
              </AppButton>
              <AppButton variant="ghost"
                :disabled="item.locked || state === 'editing'"
                @click="editItem(student.student_id, item, 'replace')"
              >
                替换
              </AppButton>
              <AppButton variant="ghost"
                :disabled="item.locked || state === 'editing'"
                @click="editItem(student.student_id, item, 'exclude')"
              >
                排除
              </AppButton>
            </div>
            <p v-else class="personalized-shared-note">同题模式不允许只改某一名学生；如需换题，请调整设置后重新生成整组草稿。</p>
          </li>
        </ol>

        <ul v-if="student.warnings.length" class="training-warning-list">
          <li v-for="warning in student.warnings" :key="warning">{{ warning }}</li>
        </ul>

        <section class="personalized-paper-panel">
          <p v-if="!instancesForStudent(student.student_id).length" class="training-empty is-compact">
            尚未生成训练卷。先完成草稿调整，再使用页面底部的批量生成。
          </p>

          <article
            v-for="instance in instancesForStudent(student.student_id)"
            :key="instance.paper_instance_id"
            class="personalized-paper-version"
          >
            <div>
              <strong>V{{ instance.series_version }}</strong>
              <span>
                {{ instance.question_count }} 题 ·
                {{ instance.criterion_point_count }} 个判定点 ·
                {{ instance.status === 'frozen' ? `${instance.pages.length} 页冻结 PDF` : '待冻结 PDF' }}
              </span>
            </div>
            <small v-if="instance.formula_fallbacks?.length" class="training-feedback is-warning">
              {{ instance.formula_fallbacks.length }} 处公式无法转为可编辑公式，已保留原式或题图，打印前请预览核对。
            </small>
            <div class="personalized-paper-actions">
              <AppButton variant="ghost"
                v-if="instance.downloads.frozen_pdf"
                :disabled="Boolean(paperBusy)"
                @click="downloadPaper(instance, 'frozen_pdf')"
              >
                下载 PDF 试卷
              </AppButton>
              <AppButton variant="ghost"
                v-if="instance.downloads.review_docx"
                :disabled="Boolean(paperBusy)"
                @click="downloadPaper(instance, 'review_docx')"
              >
                下载 DOCX 版（可选精修）
              </AppButton>
            </div>
          </article>
          <p class="personalized-paper-note">
            PDF 试卷每页带身份码，用于打印后扫码归卷；系统不会自动打印。要改内容请调整草稿后重新生成。
          </p>
        </section>
      </article>
      </template>

      </div>

      <div class="personalized-aside">
      <section class="personalized-batch-panel" aria-labelledby="personalized-batch-title">
        <div>
          <strong id="personalized-batch-title">{{ paperMode === 'shared' ? '批量生成实名同题卷' : '批量生成实名一人一卷' }}</strong>
          <p>每名学生保持独立卷实例；成功卷不会因其他学生失败而丢失。生成的是可直接打印的 PDF 试卷。</p>
        </div>
        <AppButton variant="primary" :disabled="Boolean(paperBusy)" @click="createPaperBatch">
          {{ paperBusy === 'batch' ? '正在逐人生成 PDF…' : '生成全部 PDF 试卷（可直接打印）' }}
        </AppButton>
        <AppButton variant="secondary"
          v-if="paperBatch?.status === 'creating'"
          :disabled="paperCancelBusy"
          @click="cancelPaperBatch"
        >
          {{ paperCancelBusy ? '正在停止…' : '停止未开始学生' }}
        </AppButton>
        <div v-if="paperBatch" class="personalized-batch-result">
          <span>成功 {{ paperBatch.succeeded_count }} / {{ paperBatch.requested_count }} 人</span>
          <AppButton variant="ghost" v-if="paperBatch.downloads.frozen_bundle" @click="downloadBatch('frozen_bundle')">下载试卷 PDF ZIP</AppButton>
          <AppButton variant="ghost" v-if="paperBatch.downloads.bundle" @click="downloadBatch('bundle')">下载 DOCX 版 ZIP</AppButton>
          <AppButton variant="secondary" v-if="paperBatch.failures.length" :disabled="Boolean(paperBusy)" @click="retryFailedPaperBatch">
            {{ paperBusy === 'batch-retry' ? '正在重试失败学生…' : `只重试失败的 ${paperBatch.failures.length} 人` }}
          </AppButton>
          <ul v-if="paperBatch.failures.length">
            <li v-for="failure in paperBatch.failures" :key="failure.student_id">学生 {{ failure.student_id }}：生成失败，可单独重试</li>
          </ul>
        </div>
      </section>

      <TrainingScanBatchPanel
        :instances="paperInstances"
        @open-draft="openNextDraft"
      />
      </div>

      <p class="personalized-footnote">
        训练卷使用生成时的题目、推荐理由和判定点快照；以后来源变化不会改写旧卷。
      </p>
    </template>

    <QuestionPreviewDialog
      :question-id="previewQuestionId"
      :title="previewTitle"
      @close="closePreview"
    />
  </section>
</template>

<style scoped>
.personalized-draft {
  margin-top: 1.25rem;
  padding: 1rem;
  border: 1px solid var(--line, var(--color-border-default));
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.personalized-draft.is-external-setup {
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
}

.personalized-draft-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 1rem;
  margin-bottom: 1rem;
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.personalized-draft-toolbar__status {
  color: var(--color-text-secondary);
  font-size: 0.86rem;
}

.personalized-draft-toolbar__confirm {
  color: var(--color-warning);
  font-size: 0.86rem;
}

.personalized-draft-toolbar__locked {
  color: var(--color-text-muted);
  font-size: 0.84rem;
}

.personalized-draft-toolbar .training-button {
  margin: 0;
}

.personalized-draft > header,
.personalized-student > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.personalized-draft h3,
.personalized-draft p {
  margin: 0.2rem 0;
}

.personalized-draft > header > span {
  flex: 0 0 auto;
  white-space: nowrap;
}

.personalized-controls {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin: 1rem 0;
}

.personalized-settings {
  margin: 1rem 0;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.personalized-settings > summary {
  padding: 0.75rem 0.9rem;
  cursor: pointer;
  font-weight: 700;
}

.personalized-settings > section {
  padding: 0 0.9rem 0.9rem;
}

.personalized-workbench {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  align-items: start;
  gap: 1rem;
  margin-top: 1rem;
}

.personalized-aside {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
  align-items: stretch;
  margin-top: 1rem;
}

.personalized-student-list {
  display: grid;
  gap: 0.45rem;
  position: sticky;
  top: calc(var(--shell-topbar-height, 64px) + 1rem);
}

.personalized-student-list > strong {
  padding: 0.4rem 0.2rem;
}

.personalized-student-list button {
  display: grid;
  gap: 0.2rem;
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  text-align: left;
  cursor: pointer;
}

.personalized-student-list button.is-selected {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  box-shadow: inset 3px 0 0 var(--color-accent);
}

.personalized-student-list small {
  color: var(--color-text-secondary);
}

.personalized-controls label,
.personalized-edit-reason {
  display: grid;
  gap: 0.35rem;
  color: var(--color-text-primary);
  font-size: 0.9rem;
}

.personalized-controls input,
.personalized-edit-reason input {
  min-width: 0;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.personalized-targets {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 1rem;
  margin: 0 0 1rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
}

.personalized-targets legend {
  padding: 0 0.35rem;
  font-weight: 700;
}

.personalized-targets p {
  flex-basis: 100%;
  color: var(--color-warning);
}

.personalized-targets.is-summary > span {
  padding: .35rem .55rem;
  border-radius: 999px;
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  font-size: .84rem;
}

.personalized-edit-reason {
  margin: 1rem 0;
}

.personalized-student {
  grid-column: 2;
  margin-top: 0;
  padding: 0.85rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.personalized-student header div,
.personalized-item-main {
  display: grid;
  gap: 0.25rem;
}

.personalized-student ol {
  display: grid;
  gap: 0.65rem;
  margin: 0.85rem 0 0;
  padding-left: 1.4rem;
}

.personalized-student li {
  padding: 0.7rem;
  border-radius: 8px;
  background: var(--color-bg-subtle);
}

.personalized-item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 0.55rem;
}

.personalized-shared-note {
  margin-top: .55rem !important;
  color: var(--color-text-secondary);
  font-size: .84rem;
}

.personalized-student ol.personalized-match-list {
  padding-left: 0;
  list-style: none;
}

.personalized-student li.personalized-match {
  display: grid;
  grid-template-columns: minmax(180px, 0.85fr) 28px minmax(0, 1.65fr);
  align-items: stretch;
  gap: 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.personalized-match__evidence {
  display: grid;
  align-self: start;
  align-content: start;
  gap: 0.35rem;
  padding: 0.55rem 0.65rem;
  border-radius: 8px;
  background: var(--color-bg-subtle);
}

.personalized-match__evidence > strong {
  color: var(--color-text-secondary);
  font-size: 0.84rem;
  font-weight: 600;
}

.personalized-match__evidence > span {
  color: var(--color-text-primary);
  font-size: 0.86rem;
}

.personalized-match__evidence > small {
  color: var(--color-text-muted);
}

.personalized-match__arrow {
  align-self: center;
  justify-self: center;
  color: var(--color-accent);
  font-weight: 700;
}

.personalized-match__card {
  display: grid;
  align-content: start;
  gap: 0.3rem;
}

.personalized-match__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.personalized-match__head small {
  color: var(--color-text-muted);
}

.personalized-stage-badge {
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  white-space: nowrap;
}

.personalized-stage-badge.is-direct {
  background: var(--color-accent);
  color: #ffffff;
}

.personalized-stage-badge.is-prerequisite {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.personalized-stage-badge.is-transfer {
  background: var(--color-ai-subtle);
  color: var(--color-ai);
}

.personalized-match__stem {
  display: -webkit-box;
  margin: 0;
  padding: 0.5rem 0.65rem;
  border-radius: 8px;
  background: var(--color-bg-subtle);
  font-family: var(--font-family-document);
  font-size: 0.92rem;
  line-height: var(--line-height-body);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

.personalized-match__stem.is-empty {
  color: var(--color-text-muted);
  font-family: var(--font-family-sans);
}

.personalized-match__meta {
  color: var(--color-text-secondary);
}

.personalized-match__reason {
  color: var(--color-text-muted);
}

.personalized-student ul.personalized-shortages {
  display: grid;
  gap: 0.5rem;
  margin: 0.65rem 0 0;
  padding: 0;
  list-style: none;
}

.personalized-student li.personalized-shortage {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.75rem;
  border: 1px dashed var(--color-warning);
  background: var(--color-warning-subtle);
}

.personalized-paper-panel {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
  padding: 0.85rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-info-subtle);
}

.personalized-batch-panel {
  display: grid;
  grid-template-columns: 1fr;
  align-items: center;
  gap: 1rem;
  margin: 0;
  padding: 1rem;
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent-subtle);
}

.personalized-match__evidence-all {
  display: grid;
  gap: 0.35rem;
}

.personalized-match__evidence-all > summary {
  cursor: pointer;
  color: var(--color-text-secondary);
  font-size: 0.8rem;
}

.personalized-match__evidence-all > span {
  display: block;
  padding-top: 0.3rem;
  color: var(--color-text-secondary);
  font-size: 0.82rem;
}

.personalized-batch-panel p { margin: .25rem 0 0; color: var(--color-text-secondary); }
.personalized-batch-panel label { display: flex; align-items: center; gap: .5rem; }
.personalized-batch-result { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }
.personalized-batch-result ul { flex-basis: 100%; margin: 0; }

.personalized-paper-version > div:first-child {
  display: grid;
  gap: 0.25rem;
}

.personalized-paper-version {
  display: grid;
  gap: 0.45rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.personalized-paper-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.75rem;
}

.personalized-paper-note {
  color: var(--color-text-secondary);
  font-size: 0.84rem;
}

.personalized-footnote {
  margin-top: 1rem !important;
  color: var(--color-text-secondary);
  font-size: 0.88rem;
}

@media (max-width: 760px) {
  .personalized-controls {
    grid-template-columns: 1fr;
  }

  .personalized-student li.personalized-match {
    grid-template-columns: 1fr;
  }

  .personalized-match__arrow {
    justify-self: start;
    transform: rotate(90deg);
  }

  .personalized-workbench,
  .personalized-aside {
    grid-template-columns: 1fr;
  }

  .personalized-student-list,
  .personalized-student {
    grid-column: 1;
    grid-row: auto;
    position: static;
  }
}
</style>
