<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import {
  trainingApi,
  type TrainingDiagnosis, type TrainingReadDiagnosis, type TrainingReadStudent,
  type TrainingGroup,
  type TrainingGroupingRequest,
  type TrainingExamScopeRequest,
  type TrainingStudentScopeRequest,
} from '../api/training'
import TrainingScopeBar from '../components/knowledge-training/TrainingScopeBar.vue'
import StudentPicker from '../components/knowledge-training/StudentPicker.vue'
import KnowledgeRangeList from '../components/knowledge-training/KnowledgeRangeList.vue'
import StudentQuickView from '../components/knowledge-training/StudentQuickView.vue'
import TrainingGroupRecommendations from '../components/knowledge-training/TrainingGroupRecommendations.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import PaperSettingsPanel from '../components/knowledge-training/PaperSettingsPanel.vue'
import PersonalizedRecommendationDraft from '../components/training/PersonalizedRecommendationDraft.vue'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import BackButton from '../components/design-system/BackButton.vue'
import StepProgress, { type StepProgressStep } from '../components/design-system/StepProgress.vue'
import { loadEvidenceScope, saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { loadPaperSelectionSession, savePaperSelectionSession, resolvePaperScope, DEFAULT_TRAINING_RULES, DEFAULT_HANDOUT_RULES, type PracticeRules, type AdoptedChapterGroup, type ChapterGroupEditor, type ChapterGroupSort } from '../features/training/paper-selection-session'
import '../styles/training-recommendations.css'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useTrainingStore } from '../stores/training'

type ReferenceState = 'loading' | 'ready' | 'error'
type TrainingMode = 'chapter' | 'student' | 'paper'

const route = useRoute()
const router = useRouter()
const curriculumScope = useCurriculumScopeStore()
let initialVolumePending = curriculumScope.loadState !== 'ready'
const training = useTrainingStore()

// 出卷勾选与设置随会话暂存：切页签回来后无需重新勾选，
// 草稿指纹才能对上并自动恢复上次生成的草稿。
const savedPaperSelection = loadPaperSelectionSession()
const students = ref<StudentSummary[]>([])
const referenceState = ref<ReferenceState>('loading')
const selectedTargetKeys = ref<string[]>(savedPaperSelection?.targetKeys ?? [])
const selectedRangeKeys = ref<string[]>(savedPaperSelection?.rangeKeys ?? [])
const purpose = ref<'training' | 'handout' | 'wrong_book'>(savedPaperSelection?.purpose ?? 'training')
const selectedStudentIds = ref<string[]>(savedPaperSelection?.selectedStudentIds ?? [])
const trainingRules = ref<PracticeRules>({ ...DEFAULT_TRAINING_RULES, ...savedPaperSelection?.trainingRules })
const handoutRules = ref<PracticeRules>({ ...DEFAULT_HANDOUT_RULES, ...savedPaperSelection?.handoutRules })
const initialRules = purpose.value === 'handout' ? handoutRules.value : trainingRules.value
const maxConsolidationQuestions = ref(initialRules.maxConsolidationQuestions)
const maxUnmeasuredQuestions = ref(initialRules.maxUnmeasuredQuestions)
const wrongBookSessionIds = ref<number[] | null>(savedPaperSelection?.wrongBook?.sessionIds ?? null)
const includeSourceLabel = ref(savedPaperSelection?.wrongBook?.includeSourceLabel !== false)
const includeAnswerSpace = ref(savedPaperSelection?.wrongBook?.includeAnswerSpace !== false)
const quickStudent = shallowRef<TrainingReadStudent | null>(null)
const quickOpen = ref(false)
function showStudent(student: TrainingReadStudent) { quickStudent.value = student; quickOpen.value = true }
function chooseWrongBook(studentId: string) { selectedStudentIds.value = [studentId]; purpose.value = 'wrong_book'; quickOpen.value = false }
const maxQuestionsPerSkill = ref(initialRules.maxQuestionsPerSkill)
const maxWrittenQuestions = ref(initialRules.maxWrittenQuestions)
const recentActivityCount = ref(initialRules.recentActivityCount)
const questionCount = ref(initialRules.questionCount)
const teachingProgressChapterId = ref(savedPaperSelection?.teachingProgressChapterId ?? '')
const scopeMode = ref<'comprehensive' | 'focused'>(savedPaperSelection?.scopeMode ?? 'comprehensive')
const individualScope = computed(() => resolvePaperScope(curriculumScope.selectedVolume, selectionDiagnosis.value,
  selectedRangeKeys.value, teachingProgressChapterId.value, scopeMode.value))
const difficultyMax = ref(initialRules.difficultyMax)
const progressChapters = computed(() => (curriculumScope.selectedVolume ? [curriculumScope.selectedVolume] : curriculumScope.volumes)
  .flatMap(volume => volume.chapters.map(chapter => ({ id: chapter.id, label: `${volume.label} · ${chapter.label}` }))))
watch(progressChapters, (chapters) => {
  if (curriculumScope.loadState === 'ready' && teachingProgressChapterId.value
    && !chapters.some(chapter => chapter.id === teachingProgressChapterId.value)) teachingProgressChapterId.value = ''
})
const excludeCurrentOriginals = ref(savedPaperSelection?.excludeCurrentOriginals ?? true)
const paperMode = ref<'individual' | 'shared'>(savedPaperSelection?.paperMode ?? 'individual')
const chapterKey = ref(savedPaperSelection?.chapterKey ?? '')
const sectionKey = ref(savedPaperSelection?.sectionKey ?? '')
const chapterScope = ref<GraphQueryInput | undefined>(savedPaperSelection?.chapterScope)
const groupSort = ref<ChapterGroupSort>(savedPaperSelection?.groupSort ?? 'size')
const groupEditor = ref<ChapterGroupEditor | null>(savedPaperSelection?.groupEditor ?? null)
const adoptedGroup = ref<AdoptedChapterGroup | null>(savedPaperSelection?.adoptedGroup ?? null)
const arrangements = ref<AdoptedChapterGroup[]>(savedPaperSelection?.arrangements ?? [])
const groupMessage = ref('')
const groupChecking = ref(false)
type PaperSource = { kind: 'idle' } | { kind: 'loading'; key: string }
  | { kind: 'ready'; key: string; basis: TrainingReadDiagnosis; diagnosis: TrainingDiagnosis } | { kind: 'error'; key: string }
const paperSource = shallowRef<PaperSource>({ kind: 'idle' })
let paperSourceController: AbortController | null = null
const draftContext = ref<{ mode: 'individual' | 'shared'; studentCount: number; questionCount: number; difficultyMax: number; purpose: 'training' | 'handout' } | null>(null)
const workflowStage = ref<'diagnosis' | 'draft' | 'wps' | 'scan'>('diagnosis')
const draftRequestState = ref<'idle' | 'loading' | 'ready' | 'error' | 'editing'>('idle')
const draftNeedsCheck = ref(false)
const paperDraft = ref<{ generate: () => Promise<void>; selectWorkspaceStep: (id: string) => void; focusReturnPoint: () => void } | null>(null)
const draftWorkspace = ref<{ steps: StepProgressStep[]; current: string; revision: number } | null>(null)
const draftOpen = computed(() => trainingMode.value === 'paper' && draftContext.value !== null)
const returnFocus = computed(() => route.query.focus === 'scan' || route.query.focus === 'review' || route.query.focus === 'publish' ? route.query.focus : undefined)
async function consumeReturnFocus() {
  const query = { ...route.query }
  delete query.focus
  await router.replace({ query })
  await nextTick()
  paperDraft.value?.focusReturnPoint()
}
const draftBack = computed(() => route.query.from === 'workbench'
  ? { to: '/workbench', label: '工作台' }
  : { to: paperBackTarget.value, label: paperBackTarget.value.query.mode === 'chapter' ? '按章节训练' : '按学生训练' })
let studentsController: AbortController | null = null

const trainingMode = computed<TrainingMode>(() => {
  if (route.query.mode === 'student') return 'student'
  if (route.query.mode === 'paper') return 'paper'
  return 'chapter'
})
const targetLabel = computed(() => (paperDiagnosis.value ?? training.diagnosis)?.target_kind === 'type' ? '题型' : (paperDiagnosis.value ?? training.diagnosis)?.target_kind === 'knowledge' ? '知识点' : (paperDiagnosis.value ?? training.diagnosis)?.target_kind === 'mixed' ? '训练目标' : '技能')
const pageCopy = computed(() => ({
  chapter: {
    title: '按章节训练',
    description: `选择学生范围和章／小节，按同${targetLabel.value}作答所支持的适合难度与练习需要分组；核对名单后采用同卷训练。`,
  },
  student: {
    title: '按学生训练',
    description: '选择学生并确认已学进度，默认综合训练覆盖已学章节；需要集中练某章或小节时，切换专项训练。',
  },
  paper: {
    title: '出卷与回收',
    description: '在这里审核草稿、生成 PDF 试卷并扫描归卷；出卷设置在按章节/按学生训练页完成。',
  },
}[trainingMode.value]))

const selectedStudentCount = computed(() => training.diagnosis?.students.length ?? 0)
const sharedStudentCount = computed(() => adoptedGroup.value?.memberIds.length ?? selectedStudentIds.value.length)
const paperStudentCount = computed(() => paperMode.value === 'shared' ? sharedStudentCount.value : selectedStudentIds.value.length)
const selectionDiagnosis = computed<TrainingReadDiagnosis | null>(() => {
  const diagnosis = training.diagnosis
  if (!diagnosis) return null
  const ids = paperMode.value === 'shared' && adoptedGroup.value ? adoptedGroup.value.memberIds : selectedStudentIds.value
  const selected = new Set(ids)
  if ('response_mode' in diagnosis) return { ...diagnosis, scope: { mode: 'selected', student_ids: [...ids] },
    students: diagnosis.students.filter(student => selected.has(student.student_id)) }
  return { ...diagnosis, scope: { mode: 'selected', student_ids: [...ids] },
    students: diagnosis.students.filter(student => selected.has(student.student_id)) }
})
const paperDiagnosis = computed<TrainingDiagnosis | null>(() => {
  const source = paperSource.value
  if (source.kind !== 'ready' || source.key !== paperSourceKey.value || source.basis !== training.diagnosis
    || !training.hasCurrentDiagnosis) return null
  const ids = paperMode.value === 'shared' && adoptedGroup.value ? adoptedGroup.value.memberIds : selectedStudentIds.value
  const selected = new Set(ids)
  return { ...source.diagnosis, scope: { mode: 'selected', student_ids: [...ids] },
    students: source.diagnosis.students.filter(student => selected.has(student.student_id)) }
})
const panelStudentIds = computed(() => trainingMode.value === 'chapter' ? adoptedGroup.value?.memberIds ?? [] : selectedStudentIds.value)
const classes = computed(() => [...new Set(students.value.map(student => student.class_name).filter((name): name is string => Boolean(name)))].sort((a,b) => a.localeCompare(b, 'zh-CN', { numeric: true })))
const selectedClass = computed(() => training.studentScope.mode === 'class' ? training.studentScope.classIds[0] || training.studentScope.classId : '')
const wrongBookScopeKeys = computed(() => trainingMode.value === 'chapter' ? adoptedGroup.value?.scopeKeys ?? [] : scopeMode.value === 'focused' ? selectedRangeKeys.value : [])
async function setScopeClass(name: string) {
  await applyEvidenceScope(semesterEvidenceQuery({ mode: name ? 'class' : 'all', class_ids: name ? [name] : [], score_rate_min: training.studentScope.scoreRateMin }, curriculumScope.selectedVolumeId))
}
async function setScoreFloor(value: number | null) {
  await applyEvidenceScope(semesterEvidenceQuery({ mode: selectedClass.value ? 'class' : 'all', class_ids: selectedClass.value ? [selectedClass.value] : [], score_rate_min: value }, curriculumScope.selectedVolumeId))
}
const paperNumericSettingsValid = computed(() => (
  Number.isInteger(questionCount.value)
  && (purpose.value === 'training' ? questionCount.value >= 8 && questionCount.value <= 12 : questionCount.value >= 1)
  && Number.isInteger(maxQuestionsPerSkill.value) && maxQuestionsPerSkill.value >= 1 && maxQuestionsPerSkill.value <= questionCount.value
  && Number.isInteger(maxWrittenQuestions.value) && maxWrittenQuestions.value >= 0 && maxWrittenQuestions.value <= questionCount.value
  && Number.isInteger(recentActivityCount.value) && recentActivityCount.value >= 0
  && (purpose.value !== 'handout' || paperMode.value !== 'individual' || [maxConsolidationQuestions.value, maxUnmeasuredQuestions.value].every(value => Number.isInteger(value) && value >= 0 && value <= questionCount.value))
  && Number.isInteger(difficultyMax.value)
  && difficultyMax.value >= 1
  && difficultyMax.value <= 10
))
// 多人同一套卷用成员需求并集出题；一人一卷用章/节范围出题。
// 出卷页按最后编辑页记录的模式（paperMode）取对应的校验。
const sharedSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && (adoptedGroup.value ? adoptedGroup.value.memberIds.length > 0 : selectedStudentIds.value.length > 0)
  && (adoptedGroup.value ? selectedTargetKeys.value.length > 0 : individualScope.value.keys.length > 0)
  && paperNumericSettingsValid.value
  && (!adoptedGroup.value || adoptedGroup.value.memberIds.length >= 2)
))
const individualSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && selectedStudentIds.value.length > 0
  && individualScope.value.keys.length > 0
  && paperNumericSettingsValid.value
))
const paperSettingsValid = computed(() => (
  paperMode.value === 'shared'
    ? sharedSettingsValid.value
    : individualSettingsValid.value
))
const expectedPaperCount = computed(() => paperMode.value === 'shared' ? 1 : paperStudentCount.value)

const sourceStudentScope = computed<TrainingStudentScopeRequest>(() => ({
  mode: training.studentScope.mode,
  student_ids: [...training.studentScope.studentIds],
  ...(training.studentScope.classId ? { class_id: training.studentScope.classId } : {}),
  ...(training.studentScope.classIds.length ? { class_ids: [...training.studentScope.classIds] } : {}),
  score_rate_min: training.studentScope.scoreRateMin,
  score_rate_max: training.studentScope.scoreRateMax,
  include_student_ids: [...training.studentScope.includeStudentIds],
  exclude_student_ids: [...training.studentScope.excludeStudentIds],
  use_historical_fallback: training.studentScope.useHistoricalFallback,
}))
const personalizedScope = computed<TrainingStudentScopeRequest>(() => ({ mode: 'selected',
  student_ids: [...(paperMode.value === 'shared' && adoptedGroup.value ? adoptedGroup.value.memberIds : selectedStudentIds.value)], use_historical_fallback: false }))
const chapterGroupBlockedReason = computed(() => {
  if (trainingMode.value !== 'chapter' || !adoptedGroup.value) return ''
  if (training.analysisState === 'loading') return '正在更新学生范围，请稍候。'
  const allowed = new Set(training.diagnosis?.students.map(student => student.student_id) ?? [])
  return adoptedGroup.value.memberIds.some(id => !allowed.has(id))
    ? '来源范围已变化，所采用小组含范围外学生。请恢复原范围或重新选择候选小组。' : ''
})
const panelValid = computed(() => panelStudentIds.value.length > 0 && Boolean(curriculumScope.selectedVolumeId)
  && (purpose.value === 'wrong_book' ? trainingMode.value === 'chapter' || scopeMode.value === 'comprehensive' || selectedRangeKeys.value.length > 0
    : trainingMode.value === 'chapter' ? Boolean(adoptedGroup.value) && paperNumericSettingsValid.value && !chapterGroupBlockedReason.value : paperSettingsValid.value))
const blockedReason = computed(() => !panelStudentIds.value.length ? trainingMode.value === 'chapter' ? '先在中间采用一个小组' : '请先勾选学生'
  : purpose.value === 'wrong_book' && scopeMode.value === 'focused' && !selectedRangeKeys.value.length ? '请勾选章节范围'
  : purpose.value !== 'wrong_book' && chapterGroupBlockedReason.value ? chapterGroupBlockedReason.value
  : !paperNumericSettingsValid.value ? '请检查题数与各项上限' : '请确认已学进度或专项范围')
const personalizedExamScope = computed<TrainingExamScopeRequest>(() => ({
  mode: 'semester',
  session_ids: [],
  curriculum_volume_id: curriculumScope.selectedVolumeId ?? '',
}))
const paperSourceKey = computed(() => JSON.stringify({ scope: sourceStudentScope.value, exam_scope: personalizedExamScope.value }))
async function loadPaperDiagnosis(): Promise<void> {
  if (trainingMode.value !== 'paper' || !training.hasCurrentDiagnosis) return
  const basis = training.diagnosis
  if (!basis) return
  const key = paperSourceKey.value
  if (paperSource.value.kind === 'ready' && paperSource.value.key === key && paperSource.value.basis === basis) return
  paperSourceController?.abort()
  const controller = new AbortController()
  paperSourceController = controller
  paperSource.value = { kind: 'loading', key }
  try {
    const diagnosis = await trainingApi.diagnose({ scope: sourceStudentScope.value, exam_scope: personalizedExamScope.value }, controller.signal)
    if (controller.signal.aborted || key !== paperSourceKey.value || basis !== training.diagnosis || !training.hasCurrentDiagnosis) return
    paperSource.value = { kind: 'ready', key, basis, diagnosis }
  } catch {
    if (controller.signal.aborted || key !== paperSourceKey.value) return
    paperSource.value = { kind: 'error', key }
  } finally {
    if (paperSourceController === controller) paperSourceController = null
  }
}
const groupingSettings = computed<TrainingGroupingRequest>(() => ({
  scope_keys: sectionKey.value || chapterKey.value ? [sectionKey.value || chapterKey.value] : [],
  question_count: questionCount.value,
  purpose: purpose.value === 'wrong_book' ? 'training' : purpose.value, max_questions_per_skill: maxQuestionsPerSkill.value,
  max_written_questions: maxWrittenQuestions.value, recent_activity_count: recentActivityCount.value,
  difficulty_max: difficultyMax.value,
  exclude_current_exam_originals: excludeCurrentOriginals.value, curriculum_volume_id: curriculumScope.selectedVolumeId ?? '',
  teaching_progress_chapter_id: teachingProgressChapterId.value,
}))
const activeEvidenceQuery = computed<GraphQueryInput | null>(() => {
  return semesterEvidenceQuery({
      mode: training.studentScope.mode,
      ...(training.studentScope.classId ? { class_id: training.studentScope.classId } : {}),
      ...(training.studentScope.classIds.length ? { class_ids: [...training.studentScope.classIds] } : {}),
      ...(training.studentScope.studentIds.length ? { student_ids: [...training.studentScope.studentIds] } : {}),
      score_rate_min: training.studentScope.scoreRateMin,
      score_rate_max: training.studentScope.scoreRateMax,
      include_student_ids: [...training.studentScope.includeStudentIds],
      exclude_student_ids: [...training.studentScope.excludeStudentIds],
      use_historical_fallback: training.studentScope.useHistoricalFallback,
    }, curriculumScope.selectedVolumeId)
})

async function analyze(): Promise<void> {
  try {
    const volume = curriculumScope.selectedVolume
    const chapter = volume?.chapters.find(item => item.knowledge_id === chapterKey.value)
      ?? volume?.chapters[0]
    const section = chapter?.sections.find(item => item.knowledge_id === sectionKey.value)
    const scopeKey = section?.knowledge_id ?? chapter?.knowledge_id
    if (trainingMode.value !== 'chapter' || !scopeKey) {
      await training.analyze()
      return
    }
    const initialGroupBody = {
      scope: sourceStudentScope.value, exam_scope: personalizedExamScope.value,
      grouping: { ...groupingSettings.value, scope_keys: [scopeKey] },
    }
    // Reuse the existing grouped endpoint for the initial full read. A
    // grouping failure retains the original base-diagnosis recovery path.
    await training.analyze({ diagnoseDisplay: async (body, signal) => {
      try { return await trainingApi.diagnoseDisplay(body, signal) }
      catch (error) {
        if (signal?.aborted) throw error
        const base = { ...body }
        delete base.grouping
        return trainingApi.diagnoseDisplay(base, signal)
      }
    } }, initialGroupBody)
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

async function applyEvidenceScope(query: GraphQueryInput, reuseCurrent = false): Promise<void> {
  query = semesterEvidenceQuery(query.scope, curriculumScope.selectedVolumeId)
  if (trainingMode.value === 'chapter') chapterScope.value = query
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
    mode: 'semester', sessionIds: [], curriculumVolumeId: curriculumScope.selectedVolumeId ?? '',
  })
  if (!reuseCurrent || !training.hasCurrentDiagnosis) await analyze()
}

async function generatePaperDraft(): Promise<void> {
  if (groupChecking.value) return
  groupMessage.value = ''
  if (draftNeedsCheck.value) { await paperDraft.value?.generate(); return }
  if (!paperDiagnosis.value) return
  if (paperMode.value === 'shared' && adoptedGroup.value) {
    groupChecking.value = true
    const key = paperSourceKey.value
    const basis = training.diagnosis
    const selectionKey = JSON.stringify([adoptedGroup.value.memberIds, adoptedGroup.value.targetKeys, adoptedGroup.value.scopeKeys])
    try {
      const response = await trainingApi.diagnose({ scope: sourceStudentScope.value, exam_scope: personalizedExamScope.value,
        grouping: { ...groupingSettings.value, scope_keys: adoptedGroup.value.scopeKeys,
          member_ids: adoptedGroup.value.memberIds, target_keys: adoptedGroup.value.targetKeys } })
      if (key !== paperSourceKey.value || basis !== training.diagnosis || !adoptedGroup.value
        || selectionKey !== JSON.stringify([adoptedGroup.value.memberIds, adoptedGroup.value.targetKeys, adoptedGroup.value.scopeKeys])) {
        groupMessage.value = '来源范围已变化，已保留当前成员与出卷设置，请重新核对。'
        return
      }
      const checked = response.grouping?.selection
      if (!checked?.ready) {
        groupMessage.value = checked?.issues.join('；') || '当前小组需要重新核对，请回到按章节训练调整。'
        return
      }
      adoptedGroup.value = { ...adoptedGroup.value, sourceVersion: checked.source_version }
      if (!training.diagnosis) return
      paperSource.value = { kind: 'ready', key: paperSourceKey.value, basis: training.diagnosis, diagnosis: response }
      await nextTick()
    } catch {
      groupMessage.value = '小组依据暂时无法核对，已保留成员与出卷设置，请重试。'
      return
    } finally { groupChecking.value = false }
  }
  await paperDraft.value?.generate()
}

function adoptGroup(group: TrainingGroup, diagnosis: TrainingDiagnosis): void {
  selectedTargetKeys.value = group.targets.map(target => target.knowledge_key)
  adoptedGroup.value = { groupId: group.group_id, memberIds: group.members.map(member => member.student_id),
    targetKeys: [...selectedTargetKeys.value], scopeKeys: [...groupingSettings.value.scope_keys], sourceVersion: group.source_version }
  if (training.diagnosis) paperSource.value = { kind: 'ready', key: paperSourceKey.value, basis: training.diagnosis, diagnosis }
  paperMode.value = 'shared'
  selectedStudentIds.value = [...adoptedGroup.value.memberIds]
  groupMessage.value = `已选择 ${group.members.length} 人小组，请核对出卷设置。`
  void nextTick(() => document.querySelector('.paper-settings-panel')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }))
}

function clearGroupAdoption(): void {
  adoptedGroup.value = null
  groupEditor.value = null
  groupMessage.value = '小组目标已改变，请重新采用推荐小组。'
}

// 上游页（按章节/按学生训练）完成设置后跳转到出卷页：
// 以最后编辑的页为准记录出卷模式。
function goPaper(mode: 'individual' | 'shared'): void {
  if (purpose.value === 'wrong_book' || !panelValid.value) return
  paperMode.value = mode
  if (trainingMode.value === 'student') { adoptedGroup.value = null; if (mode === 'shared') selectedTargetKeys.value = [] }
  groupMessage.value = ''
  void router.push({ name: 'training', query: { mode: 'paper' } })
}

const paperBackTarget = computed(() => (
  adoptedGroup.value
    ? { name: 'training', query: { mode: 'chapter' } }
    : { name: 'training', query: { mode: 'student' } }
))

function onPaperStageChange(stage: 'diagnosis' | 'draft' | 'wps' | 'scan'): void {
  workflowStage.value = stage
  if (stage !== 'diagnosis' && adoptedGroup.value && !arrangements.value.some(group => group.groupId === adoptedGroup.value?.groupId)) {
    arrangements.value = [...arrangements.value, { ...adoptedGroup.value }]
  }
}

async function loadStudents(): Promise<void> {
  studentsController?.abort()
  const controller = new AbortController()
  studentsController = controller
  referenceState.value = 'loading'
  try {
    students.value = await fetchStudents(controller.signal)
    referenceState.value = 'ready'
    if (curriculumScope.loadState === 'ready') {
      const generalQuery = loadEvidenceScope()
      const savedQuery = trainingMode.value === 'chapter' || (trainingMode.value === 'paper' && adoptedGroup.value)
        ? chapterScope.value ?? (generalQuery ? { ...generalQuery, scope: { ...generalQuery.scope, score_rate_min: null, score_rate_max: null } } : null)
        : generalQuery
      let scope = savedQuery?.scope ?? { mode: 'all' as const }
      if (scope.mode === 'selected' || scope.mode === 'student') {
        const ids = scope.student_ids ?? []
        selectedStudentIds.value = [...ids]
        const names = new Set(students.value.filter(student => ids.includes(String(student.id))).map(student => student.class_name).filter(Boolean))
        const name = names.size === 1 ? [...names][0] : null
        scope = name ? { mode: 'class', class_ids: [name] } : { mode: 'all' }
      }
      await applyEvidenceScope(semesterEvidenceQuery(scope, curriculumScope.selectedVolumeId), true)
    }
  } catch {
    if (controller.signal.aborted) return
    students.value = []
    referenceState.value = 'error'
  } finally {
    if (studentsController === controller) studentsController = null
  }
}

watch(() => training.diagnosis, (diagnosis) => {
  // 诊断尚未就绪时不做清理：暂存的勾选要等到知识目录到达后再校验，
  // 否则会在目录为空时被误清空，导致草稿指纹对不上。
  if (!diagnosis) return
  const validStudents = new Set(diagnosis.students.map(student => student.student_id))
  const nextStudents = selectedStudentIds.value.filter(id => validStudents.has(id))
  if (nextStudents.length !== selectedStudentIds.value.length) selectedStudentIds.value = nextStudents
  const validKeys = new Set((diagnosis.knowledge_catalog ?? []).map((item) => item.knowledge_key))
  // filter 总是返回新数组；内容没变就不赋值，避免触发下游草稿重置。
  const nextTargets = selectedTargetKeys.value.filter((key) => validKeys.has(key))
  if (nextTargets.length !== selectedTargetKeys.value.length || nextTargets.some((key, index) => key !== selectedTargetKeys.value[index])) {
    selectedTargetKeys.value = nextTargets
  }
  const nextRanges = selectedRangeKeys.value.filter((key) => validKeys.has(key))
  if (nextRanges.length !== selectedRangeKeys.value.length || nextRanges.some((key, index) => key !== selectedRangeKeys.value[index])) {
    selectedRangeKeys.value = nextRanges
  }
}, { immediate: true })

function currentRules(): PracticeRules { return { questionCount: questionCount.value, difficultyMax: difficultyMax.value,
  maxQuestionsPerSkill: maxQuestionsPerSkill.value, maxWrittenQuestions: maxWrittenQuestions.value,
  recentActivityCount: recentActivityCount.value, maxConsolidationQuestions: maxConsolidationQuestions.value, maxUnmeasuredQuestions: maxUnmeasuredQuestions.value } }
watch(purpose, (next, previous) => {
  if (previous === 'training') trainingRules.value = currentRules()
  if (previous === 'handout') handoutRules.value = currentRules()
  if (next === 'wrong_book') return
  const rules = next === 'handout' ? handoutRules.value : trainingRules.value
  questionCount.value = rules.questionCount; difficultyMax.value = rules.difficultyMax
  maxQuestionsPerSkill.value = rules.maxQuestionsPerSkill; maxWrittenQuestions.value = rules.maxWrittenQuestions
  recentActivityCount.value = rules.recentActivityCount; maxConsolidationQuestions.value = rules.maxConsolidationQuestions
  maxUnmeasuredQuestions.value = rules.maxUnmeasuredQuestions
}, { flush: 'sync' })
watch([questionCount, difficultyMax, maxQuestionsPerSkill, maxWrittenQuestions, recentActivityCount, maxConsolidationQuestions, maxUnmeasuredQuestions], () => {
  if (purpose.value === 'training') trainingRules.value = currentRules()
  if (purpose.value === 'handout') handoutRules.value = currentRules()
})
watch(selectedTargetKeys, keys => {
  if (adoptedGroup.value && JSON.stringify([...keys].sort()) !== JSON.stringify([...adoptedGroup.value.targetKeys].sort())) {
    clearGroupAdoption()
  }
})

watch(
  [
    selectedTargetKeys,
    selectedRangeKeys,
    questionCount, purpose, maxQuestionsPerSkill, maxWrittenQuestions, recentActivityCount, selectedStudentIds,
    maxConsolidationQuestions, maxUnmeasuredQuestions, wrongBookSessionIds, includeSourceLabel, includeAnswerSpace,
    difficultyMax,
    teachingProgressChapterId,
    scopeMode,
    excludeCurrentOriginals,
    paperMode,
    chapterKey, sectionKey, chapterScope, groupSort, groupEditor, adoptedGroup, arrangements,
  ],
  () => {
    savePaperSelectionSession({
      targetKeys: [...selectedTargetKeys.value],
      rangeKeys: [...selectedRangeKeys.value],
      questionCount: questionCount.value,
      purpose: purpose.value, selectedStudentIds: [...selectedStudentIds.value], trainingRules: { ...trainingRules.value }, handoutRules: { ...handoutRules.value },
      wrongBook: { sessionIds: wrongBookSessionIds.value, includeSourceLabel: includeSourceLabel.value, includeAnswerSpace: includeAnswerSpace.value }, maxQuestionsPerSkill: maxQuestionsPerSkill.value,
      maxWrittenQuestions: maxWrittenQuestions.value, recentActivityCount: recentActivityCount.value,
      difficultyMax: difficultyMax.value,
      teachingProgressChapterId: teachingProgressChapterId.value,
      scopeMode: scopeMode.value,
      excludeCurrentOriginals: excludeCurrentOriginals.value,
      paperMode: paperMode.value,
      chapterKey: chapterKey.value, sectionKey: sectionKey.value, chapterScope: chapterScope.value,
      groupSort: groupSort.value,
      groupEditor: groupEditor.value, adoptedGroup: adoptedGroup.value, arrangements: arrangements.value,
    })
  },
)

watch([() => curriculumScope.selectedVolume, trainingMode], ([volume, mode]) => {
  if (!chapterKey.value && volume?.chapters[0]) chapterKey.value = volume.chapters[0].knowledge_id
  if (mode === 'chapter') paperMode.value = 'shared'
}, { immediate: true })

watch(trainingMode, (mode, previous) => {
  if (mode !== 'chapter' || previous !== 'student') return
  const active = activeEvidenceQuery.value
  const query = chapterScope.value ?? (active ? { ...active, scope: { ...active.scope, score_rate_min: null, score_rate_max: null } } : null)
  if (query) void applyEvidenceScope(query, true)
})

watch([trainingMode, () => training.diagnosis, paperSourceKey], () => {
  paperSourceController?.abort()
  if (paperSource.value.kind !== 'idle' && paperSource.value.key !== paperSourceKey.value) paperSource.value = { kind: 'idle' }
  if (trainingMode.value === 'paper' && training.hasCurrentDiagnosis) void loadPaperDiagnosis()
}, { immediate: true })

watch(() => curriculumScope.selectedVolumeId, (_next, previous) => {
  if (initialVolumePending && previous === null) { initialVolumePending = false; return }
  adoptedGroup.value = null
  groupEditor.value = null
  chapterKey.value = curriculumScope.selectedVolume?.chapters[0]?.knowledge_id ?? ''
  sectionKey.value = ''
  selectedTargetKeys.value = []
  selectedRangeKeys.value = []
  if (referenceState.value === 'ready' && activeEvidenceQuery.value) void applyEvidenceScope(activeEvidenceQuery.value)
})
watch(() => curriculumScope.loadState, state => {
  if (state === 'ready' && referenceState.value === 'ready' && training.analysisState === 'idle' && activeEvidenceQuery.value) {
    void applyEvidenceScope(activeEvidenceQuery.value)
  }
})

onMounted(() => void loadStudents())
onBeforeUnmount(() => { studentsController?.abort(); paperSourceController?.abort() })
</script>

<template>
  <section class="training-workspace knowledge-training-page" aria-labelledby="training-title">
    <PageHeader :title="draftOpen ? draftContext?.purpose === 'handout' ? '刷题讲义' : '训练卷' : pageCopy.title" title-id="training-title" class="knowledge-training-header" :class="{ 'training-draft-header': draftOpen }">
      <template v-if="draftOpen" #back><BackButton :label="draftBack.label" :to="draftBack.to" /></template>
      <template #meta>
        <span v-if="draftOpen && draftContext">{{ draftContext.mode === 'individual' ? '一人一卷' : '多人同一套卷' }} · {{ draftContext.studentCount }} 名学生 · 每卷 {{ draftContext.questionCount }} 题 · 难度 ≤ {{ draftContext.difficultyMax }} 级 · 自动保存 V{{ draftWorkspace?.revision }}</span>
        <template v-else><span>{{ curriculumScope.selectedVolume?.label ?? '未选择教学学期' }}</span><span v-if="training.diagnosis">{{ trainingMode === 'paper' ? paperStudentCount : selectedStudentCount }} 名学生</span></template>
      </template>
      <template v-if="!draftOpen" #navigation><KnowledgeTrainingTabs /></template>
      <template v-if="draftOpen && draftWorkspace" #actions><StepProgress :steps="draftWorkspace.steps" :current="draftWorkspace.current" aria-label="草稿步骤" @select="paperDraft?.selectWorkspaceStep($event)" /></template>
    </PageHeader>

    <TrainingScopeBar v-if="referenceState !== 'error' && trainingMode !== 'paper'"
      :volume-label="curriculumScope.selectedVolume?.label ?? '未选择教学学期'"
      :session-count="training.diagnosis?.exam_scope.sessions?.length ?? 0" :classes="classes" :selected-class="selectedClass"
      :score-floor="training.studentScope.scoreRateMin" :has-volume="!!curriculumScope.selectedVolumeId" @select-class="setScopeClass" @update-score-floor="setScoreFloor" />

    <FeedbackBanner
      v-if="referenceState === 'error'"
      tone="error"
      title="学生名单暂时无法读取。请检查服务后重试；当前筛选没有被清空。"
     
    />
    <FeedbackBanner v-if="training.errorMessage" tone="error" :title="training.errorMessage" />
    <FeedbackBanner v-if="groupMessage" tone="info" :title="groupMessage" />

    <section v-if="trainingMode !== 'paper'" class="practice-workspace">
      <StatePanel v-if="!curriculumScope.selectedVolumeId" kind="empty" title="请先选择教学学期" description="在左侧栏“当前考试”中选择教学学期。" />
      <StatePanel v-else-if="!training.diagnosis && training.analysisState === 'loading'" kind="loading" title="正在汇总学生与知识点…" />
      <div v-else-if="training.diagnosis" class="practice-layout">
        <StudentPicker v-if="trainingMode === 'student'" v-model="selectedStudentIds" :diagnosis="training.diagnosis" @show-student="showStudent" />
        <KnowledgeRangeList v-else mode="select-one" :volume="curriculumScope.selectedVolume" :diagnosis="training.diagnosis" :purpose="purpose"
          v-model:chapter-key="chapterKey" v-model:section-key="sectionKey" />
        <KnowledgeRangeList v-if="trainingMode === 'student'" mode="range" :volume="curriculumScope.selectedVolume" :diagnosis="training.diagnosis"
          :student-ids="selectedStudentIds" :purpose="purpose" :progress-id="individualScope.progressId"
          v-model:range-keys="selectedRangeKeys" v-model:scope-mode="scopeMode" v-model:teaching-progress-chapter-id="teachingProgressChapterId" />
        <TrainingGroupRecommendations v-else v-model:sort-mode="groupSort" :diagnosis="training.diagnosis"
          :scope="sourceStudentScope" :exam-scope="personalizedExamScope" :settings="groupingSettings"
          :editor="groupEditor" :adopted="adoptedGroup" :arrangements="arrangements"
          :disabled="training.analysisState === 'loading' || !paperNumericSettingsValid"
          @edit="groupEditor = $event" @adopt="adoptGroup" />
        <PaperSettingsPanel :context="trainingMode === 'student' ? 'student' : 'group'" :student-ids="panelStudentIds"
          :volume-id="curriculumScope.selectedVolumeId ?? ''" :scope-keys="wrongBookScopeKeys" :valid="panelValid" :blocked-reason="blockedReason"
          :generating="draftRequestState === 'loading'" :target-kind="paperDiagnosis?.target_kind ?? training.diagnosis?.target_kind" v-model:purpose="purpose" v-model:paper-mode="paperMode"
          v-model:question-count="questionCount" v-model:difficulty-max="difficultyMax" v-model:max-questions-per-skill="maxQuestionsPerSkill"
          v-model:max-written-questions="maxWrittenQuestions" v-model:recent-activity-count="recentActivityCount"
          v-model:max-consolidation-questions="maxConsolidationQuestions" v-model:max-unmeasured-questions="maxUnmeasuredQuestions"
          v-model:wrong-book-session-ids="wrongBookSessionIds" v-model:include-source-label="includeSourceLabel" v-model:include-answer-space="includeAnswerSpace"
          @go-paper="goPaper(trainingMode === 'chapter' ? 'shared' : paperMode)" />
      </div>
      <StatePanel v-else kind="empty" title="当前范围尚未汇总掌握度。">
        <template #actions><AppButton @click="analyze">重新加载</AppButton></template>
      </StatePanel>
      <details v-if="curriculumScope.selectedVolumeId && training.diagnosis?.warnings.length" class="training-data-note"><summary>数据说明（{{ training.diagnosis.warnings.length }}）</summary><ul><li v-for="warning in training.diagnosis.warnings" :key="warning">{{ warning }}</li></ul></details>
      <StudentQuickView v-if="training.diagnosis" v-model:open="quickOpen" :student="quickStudent" :diagnosis="training.diagnosis" @wrong-book="chooseWrongBook" />
    </section>

    <section v-else class="paper-workspace">

      <StatePanel
        v-if="!training.diagnosis && typeof route.query.draft !== 'string'"
        kind="empty"
        title="请先回到“按章节训练”勾选细知识点，或回到“按学生训练”勾选章/节范围。"
      />
      <template v-else>
        <StatePanel v-if="paperSource.kind === 'loading'" kind="loading" compact title="正在读取完整出卷依据…" />
        <FeedbackBanner v-if="paperSource.kind === 'error'" tone="error" title="完整出卷依据暂时无法读取，已保留成员与出卷设置；已有草稿仍可恢复。" />
        <AppButton v-if="paperSource.kind === 'error'" @click="loadPaperDiagnosis">重新加载出卷依据</AppButton>
        <div class="paper-console is-reviewing">
          <div class="paper-console__main">
            <div v-if="!draftOpen" class="paper-review-bar">
              <strong>{{ (draftContext?.mode ?? paperMode) === 'individual' ? '一人一卷' : '多人同一套卷' }}</strong>
              <span v-if="(draftContext?.mode ?? paperMode) === 'shared'">供 <b>{{ draftContext?.studentCount ?? paperStudentCount }}</b> 名学生共同练习</span>
              <span v-else>{{ draftContext?.studentCount ?? paperStudentCount }} 名学生</span>
              <span>每卷 {{ draftContext?.questionCount ?? questionCount }} 题 · 难度 ≤ {{ draftContext?.difficultyMax ?? difficultyMax }} 级</span>
              <span>{{ (draftContext?.purpose ?? purpose) === 'handout' ? '讲义 · 只打印' : '训练卷 · 可回收' }}</span>
              <details v-if="!draftContext" class="paper-settings-summary"><summary>选题细则</summary><p>同{{ targetLabel }}最多 {{ maxQuestionsPerSkill }} 道 · 解答题最多 {{ maxWrittenQuestions }} 道</p><p>{{ recentActivityCount === 0 ? '不排除近期原题' : purpose === 'handout' ? `排除最近 ${recentActivityCount} 次已批改考试原题 · 可复用历史训练题` : `排除最近 ${recentActivityCount} 次已批改考试与训练原题` }}</p></details>
              <RouterLink class="paper-review-bar__back" :to="paperBackTarget">调整出卷设置</RouterLink>
              <AppButton v-if="workflowStage === 'diagnosis'" variant="primary" data-testid="generate-paper-draft" :disabled="(!draftNeedsCheck && (!paperSettingsValid || !paperDiagnosis)) || draftRequestState === 'loading' || groupChecking" @click="generatePaperDraft">{{ groupChecking ? '正在核对小组…' : draftRequestState === 'loading' ? '正在生成并核对…' : draftNeedsCheck ? '核对生成结果' : `生成 ${expectedPaperCount} 份草稿` }}</AppButton>
            </div>
            <p v-if="workflowStage === 'diagnosis' && !paperSettingsValid" class="paper-review-hint">
              {{ paperMode === 'shared'
                ? '多人同卷：请回到训练页选择学生和章节范围，或采用一个小组。'
                : '一人一卷：请回到“按学生训练”确认已学进度，或选择专项范围并完成出卷设置。' }}
            </p>

            <PersonalizedRecommendationDraft
              ref="paperDraft"
              :initial-draft-id="typeof route.query.draft === 'string' ? route.query.draft : undefined"
              :initial-focus="returnFocus"
              external-setup
              :diagnosis="paperDiagnosis"
              :scope="personalizedScope"
              :exam-scope="personalizedExamScope"
              :purpose="purpose === 'wrong_book' ? 'training' : purpose"
              :max-consolidation-questions="maxConsolidationQuestions"
              :max-unmeasured-questions="maxUnmeasuredQuestions"
              :max-questions-per-skill="maxQuestionsPerSkill"
              :max-written-questions="maxWrittenQuestions"
              :recent-activity-count="recentActivityCount"
              :question-count="questionCount"
              :difficulty-max="difficultyMax"
              :teaching-progress-chapter-id="paperMode === 'individual' ? individualScope.progressId : teachingProgressChapterId"

              :exclude-current-exam-originals="excludeCurrentOriginals"
              :paper-mode="paperMode"
              :target-keys="paperMode === 'shared' && adoptedGroup || paperMode === 'individual' && scopeMode === 'focused' ? selectedTargetKeys : []"
              :scope-keys="paperMode === 'shared' && adoptedGroup ? [] : individualScope.keys"
              :group-scope-keys="paperMode === 'shared' ? adoptedGroup?.scopeKeys : undefined"
              :group-source-version="paperMode === 'shared' ? adoptedGroup?.sourceVersion : undefined"
              :curriculum-volume-id="curriculumScope.selectedVolumeId"
              :disabled="!paperSettingsValid || !paperDiagnosis"
              @context-change="draftContext = $event"
              @workspace-change="draftWorkspace = $event"
              @focus-consumed="consumeReturnFocus"
              @stage-change="onPaperStageChange"
              @state-change="draftRequestState = $event"
              @recovery-change="draftNeedsCheck = $event"
            />
          </div>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
.training-workspace{grid-template-columns:minmax(0,1fr);min-width:0;gap:var(--space-4)}
.training-heading--compact{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);padding:0;border:0;min-height:0}
.training-heading h1{font-size:var(--font-size-h1);margin:0}
.training-page-summary{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:var(--space-3);color:var(--color-text-muted);font-size:var(--font-size-dense)}
.training-scope-filters{margin-top:0}
.training-scope-filters :deep(.evidence-scope__quickbar){gap:var(--space-3);padding:var(--space-3) var(--space-4)}
.training-scope-filters :deep(.evidence-scope__snapshot-text){flex-direction:column;gap:2px;border-left-width:2px;padding-block:0}
.training-scope-filters :deep(.evidence-scope__snapshot-text strong){font-size:var(--font-size-caption);font-weight:var(--font-weight-medium)}
.training-scope-filters :deep(.evidence-scope__quickbar>label){flex-basis:112px;min-width:112px}
.training-mode-panel,.paper-workspace{min-width:0;background:var(--color-bg-surface);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);overflow:clip;box-shadow:var(--shadow-raised)}
.training-mode-panel__heading{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);padding:var(--space-4) var(--space-5);border-bottom:1px solid var(--color-border-default)}
.training-mode-panel__heading h2{margin:0;font-size:var(--font-size-h3)}
.training-mode-panel__heading>span{color:var(--color-text-muted);font-size:var(--font-size-caption)}
.training-selection-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;align-items:start;gap:var(--space-4);padding:var(--space-4)}
.training-selection-layout>.paper-settings-panel{margin:0;position:sticky;top:var(--space-4)}
.training-selection-layout>.training-data-note,.training-selection-layout>.training-adopted-group{grid-column:1/-1}
.training-selection-layout>.training-data-note{grid-row:3;margin:0}
.training-selection-layout>.training-adopted-group{grid-row:4}
.training-selection-layout>.chapter-training,.training-selection-layout>.knowledge-structure{grid-column:1;grid-row:1/3;min-width:0}
.training-selection-layout>.paper-settings-panel{grid-column:2;grid-row:1/3}
.training-adopted-group{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3);font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.training-data-note{font-size:var(--font-size-caption);color:var(--color-text-muted)}
.training-data-note summary{cursor:pointer}
.training-updating{margin:0;padding:var(--space-2) var(--space-5);color:var(--color-text-muted);font-size:var(--font-size-caption)}
.paper-console{padding:var(--space-5);min-width:0}
.paper-console__main{min-width:0;display:grid;gap:var(--space-3)}
.paper-review-bar{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-2) var(--space-4);padding-bottom:var(--space-3);font-size:var(--font-size-dense);border-bottom:1px solid var(--color-border-subtle)}
.paper-review-bar>span{color:var(--color-text-secondary)}
.paper-review-bar__back{margin-left:auto;color:var(--color-accent);font-size:var(--font-size-dense);text-decoration:none}
.paper-review-hint{margin:0;font-size:var(--font-size-dense);color:var(--color-warning)}
.training-workspace :deep(.training-draft-header){gap:var(--space-3);padding-block:var(--space-3)}
.training-workspace :deep(.training-draft-header .page-header__meta){overflow-wrap:anywhere}
.training-workspace :deep(.training-draft-header .page-header__lead){flex:1}
@media(max-width:1100px){.training-workspace :deep(.training-draft-header .page-header__actions){width:100%;margin-inline-start:0;justify-content:flex-start;padding-block:var(--space-3)}}
@media(max-width:760px){.training-workspace :deep(.training-draft-header .step-progress__connector){flex-basis:10px;margin-inline:var(--space-1)}}
.paper-settings-summary{font-size:var(--font-size-caption);color:var(--color-text-muted)}.paper-settings-summary summary{cursor:pointer}.paper-settings-summary p{margin:var(--space-2) 0 0}
.training-workspace :deep(.knowledge-training-tabs>span){display:none}
@media(max-width:1100px){.training-selection-layout{grid-template-columns:minmax(0,1fr)}.training-selection-layout>.paper-settings-panel{position:static;grid-column:1;grid-row:auto}.training-selection-layout>.chapter-training,.training-selection-layout>.knowledge-structure{grid-row:auto}.training-selection-layout>.training-data-note,.training-selection-layout>.training-adopted-group{grid-row:auto}}
@media(max-width:760px){.training-workspace{gap:var(--space-3)}.training-heading--compact{flex-wrap:wrap}.training-mode-panel__heading{padding:var(--space-3);flex-wrap:wrap}.training-selection-layout{padding:var(--space-3)}.paper-console{padding:var(--space-3)}.training-adopted-group{flex-wrap:wrap}.paper-review-bar__back{margin-left:0}}
</style>
