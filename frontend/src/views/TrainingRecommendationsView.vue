<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import {
  trainingApi,
  type TrainingDiagnosis,
  type TrainingGroup,
  type TrainingGroupingRequest,
  type TrainingExamScopeRequest,
  type TrainingStudentScopeRequest,
} from '../api/training'
import EvidenceScopeFilters from '../components/evidence/EvidenceScopeFilters.vue'
import ChapterTrainingMatrix from '../components/knowledge-training/ChapterTrainingMatrix.vue'
import TrainingGroupRecommendations from '../components/knowledge-training/TrainingGroupRecommendations.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import PaperSettingsPanel from '../components/knowledge-training/PaperSettingsPanel.vue'
import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
import PersonalizedRecommendationDraft from '../components/training/PersonalizedRecommendationDraft.vue'
import AppButton from '../components/design-system/AppButton.vue'
import { loadEvidenceScope, saveEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { loadPaperSelectionSession, savePaperSelectionSession, resolvePaperScope, type AdoptedChapterGroup, type ChapterGroupEditor, type ChapterGroupSort } from '../features/training/paper-selection-session'
import { useSessionStore } from '../stores/session'
import '../styles/training-recommendations.css'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useTrainingStore } from '../stores/training'

type ReferenceState = 'loading' | 'ready' | 'error'
type TrainingMode = 'chapter' | 'student' | 'paper'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const training = useTrainingStore()

// 出卷勾选与设置随会话暂存：切页签回来后无需重新勾选，
// 草稿指纹才能对上并自动恢复上次生成的草稿。
const savedPaperSelection = loadPaperSelectionSession()
const students = ref<StudentSummary[]>([])
const referenceState = ref<ReferenceState>('loading')
const selectedTargetKeys = ref<string[]>(savedPaperSelection?.targetKeys ?? [])
const selectedRangeKeys = ref<string[]>(savedPaperSelection?.rangeKeys ?? [])
const questionCount = ref(savedPaperSelection?.questionCount ?? 10)
const teachingProgressChapterId = ref(savedPaperSelection?.teachingProgressChapterId ?? '')
const scopeMode = ref<'comprehensive' | 'focused'>(savedPaperSelection?.scopeMode ?? 'comprehensive')
const individualScope = computed(() => resolvePaperScope(curriculumScope.selectedVolume, training.diagnosis,
  selectedRangeKeys.value, teachingProgressChapterId.value, scopeMode.value))
const difficultyMax = ref(savedPaperSelection?.difficultyMax ?? 8)
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
const latestGroupDiagnosis = ref<TrainingDiagnosis | null>(null)
const workflowStage = ref<'diagnosis' | 'draft' | 'wps' | 'scan'>('diagnosis')
const draftRequestState = ref<'idle' | 'loading' | 'ready' | 'error' | 'editing'>('idle')
const draftNeedsCheck = ref(false)
const scopeFilters = ref<InstanceType<typeof EvidenceScopeFilters> | null>(null)
const paperDraft = ref<{ generate: () => Promise<void> } | null>(null)
let studentsController: AbortController | null = null

const trainingMode = computed<TrainingMode>(() => {
  if (route.query.mode === 'student') return 'student'
  if (route.query.mode === 'paper') return 'paper'
  return 'chapter'
})
const pageCopy = computed(() => ({
  chapter: {
    title: '按章节训练',
    description: '选择多个班和章／小节，按同技能作答所支持的适合难度与练习需要分组；核对名单后采用同卷训练。',
  },
  student: {
    title: '按学生训练',
    description: '选择学生并确认已学进度，默认综合训练覆盖已学章节；需要集中练某章或小节时，切换专项训练。',
  },
  paper: {
    title: '生成试卷',
    description: '在这里审核草稿、生成 PDF 试卷并扫描归卷；出卷设置在按章节/按学生训练页完成。',
  },
}[trainingMode.value]))

const availableSessions = computed(() => sessionStore.sessions.filter((item) => !item.is_deleted))

// 选定范围下 diagnosis.students 只含所选学生，卡片得分率会丢成 "—"；
// 这里把每次诊断回来的得分率并入只增缓存。后端若在 scope 上直接给出全量
// student_score_profiles 则优先使用。
const profileCache = ref<Record<string, Record<string, unknown>>>({})
const scoreProfiles = computed(() => {
  const scopeProfiles = training.diagnosis?.scope.student_score_profiles
  if (scopeProfiles && Object.keys(scopeProfiles).length) return scopeProfiles
  return profileCache.value
})

const selectedStudentCount = computed(() => training.diagnosis?.students.length ?? 0)
const groupingAvailable = computed(() => training.diagnosis?.knowledge_catalog?.some(node => !node.parent_knowledge_key && /^(kp_|sk_|ki_)/.test(node.knowledge_key)) ?? false)
const sharedStudentCount = computed(() => adoptedGroup.value?.memberIds.length ?? selectedStudentCount.value)
const paperStudentCount = computed(() => paperMode.value === 'shared' ? sharedStudentCount.value : selectedStudentCount.value)
const paperDiagnosis = computed<TrainingDiagnosis | null>(() => {
  if (paperMode.value !== 'shared' || !adoptedGroup.value) return training.diagnosis
  const diagnosis = latestGroupDiagnosis.value ?? training.diagnosis
  if (!diagnosis) return null
  const ids = adoptedGroup.value.memberIds
  return { ...diagnosis, scope: { mode: 'selected', student_ids: [...ids] },
    students: diagnosis.students.filter(student => ids.includes(student.student_id)) }
})
const scoreSourceSummary = computed(() => {
  const profiles = training.diagnosis?.students ?? []
  const current = profiles.filter((student) => student.score_rate_source === 'current_exam').length
  const none = profiles.length - current
  return `所选 ${profiles.length} 人 · 本学期有成绩 ${current} 人 · 无成绩 ${none} 人`
})
const groupScopeLabel = computed(() => {
  if (training.studentScope.mode === 'selected') return `当前筛选 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length === 1) return `${training.studentScope.classIds[0]} · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length > 1) return `${training.studentScope.classIds.length} 个班级 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classId) return `${training.studentScope.classId} · ${selectedStudentCount.value} 人`
  return `全部班级 · ${selectedStudentCount.value} 人`
})
const paperNumericSettingsValid = computed(() => (
  questionCount.value >= 8
  && questionCount.value <= 12
  && difficultyMax.value >= 1
  && difficultyMax.value <= 8
))
// 多人同一套卷用成员需求并集出题；一人一卷用章/节范围出题。
// 出卷页按最后编辑页记录的模式（paperMode）取对应的校验。
const sharedSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && selectedTargetKeys.value.length > 0
  && paperNumericSettingsValid.value
  && (!adoptedGroup.value || adoptedGroup.value.memberIds.length >= 2)
))
const individualSettingsValid = computed(() => (
  Boolean(training.diagnosis)
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
const personalizedScope = computed<TrainingStudentScopeRequest>(() => paperMode.value === 'shared' && adoptedGroup.value
  ? { mode: 'selected', student_ids: [...adoptedGroup.value.memberIds], use_historical_fallback: training.studentScope.useHistoricalFallback }
  : sourceStudentScope.value)
const personalizedExamScope = computed<TrainingExamScopeRequest>(() => ({
  mode: 'semester',
  session_ids: [],
  curriculum_volume_id: curriculumScope.selectedVolumeId ?? '',
}))
const groupingSettings = computed<TrainingGroupingRequest>(() => ({
  scope_keys: sectionKey.value || chapterKey.value ? [sectionKey.value || chapterKey.value] : [],
  question_count: questionCount.value,
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
    await training.analyze()
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

function openScopeFilters(): void {
  const filters = scopeFilters.value
  if (!filters) return
  filters.openMoreFilters()
  ;(filters.$el as HTMLElement | undefined)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

async function generatePaperDraft(): Promise<void> {
  if (groupChecking.value) return
  groupMessage.value = ''
  if (draftNeedsCheck.value) { await paperDraft.value?.generate(); return }
  if (paperMode.value === 'shared' && adoptedGroup.value) {
    groupChecking.value = true
    try {
      const response = await trainingApi.diagnose({ scope: sourceStudentScope.value, exam_scope: personalizedExamScope.value,
        grouping: { ...groupingSettings.value, scope_keys: adoptedGroup.value.scopeKeys,
          member_ids: adoptedGroup.value.memberIds, target_keys: adoptedGroup.value.targetKeys } })
      const checked = response.grouping?.selection
      if (!checked?.ready) {
        groupMessage.value = checked?.issues.join('；') || '当前小组需要重新核对，请回到按章节训练调整。'
        return
      }
      adoptedGroup.value = { ...adoptedGroup.value, sourceVersion: checked.source_version }
      latestGroupDiagnosis.value = response
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
  latestGroupDiagnosis.value = diagnosis
  paperMode.value = 'shared'
  groupMessage.value = `已采用 ${group.members.length} 人的小组。请在下方核对出卷设置；原跨班推荐范围保留。`
  void nextTick(() => document.querySelector('.paper-settings-panel')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }))
}

function useManualSelection(): void {
  adoptedGroup.value = null
  groupEditor.value = null
  latestGroupDiagnosis.value = null
  groupMessage.value = '已返回手动同卷：出卷使用顶部范围内的学生，请在学生明细中核对目标。'
}

function selectChapterScope(scope: { chapterKey: string; sectionKey: string }): void {
  chapterKey.value = scope.chapterKey
  sectionKey.value = scope.sectionKey
}

// 上游页（按章节/按学生训练）完成设置后跳转到出卷页：
// 以最后编辑的页为准记录出卷模式。
function goPaper(mode: 'individual' | 'shared'): void {
  paperMode.value = mode
  groupMessage.value = ''
  void router.push({ name: 'training', query: { mode: 'paper' } })
}

const paperBackTarget = computed(() => (
  paperMode.value === 'shared'
    ? { name: 'training', query: { mode: 'chapter' } }
    : { name: 'training', query: { mode: 'student' } }
))
const paperBackLabel = computed(() => (
  paperMode.value === 'shared' ? '回到按章节训练调整设置' : '回到按学生训练调整设置'
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
      const savedQuery = trainingMode.value === 'chapter' || (trainingMode.value === 'paper' && paperMode.value === 'shared')
        ? chapterScope.value ?? (generalQuery ? { ...generalQuery, scope: { ...generalQuery.scope, score_rate_min: null, score_rate_max: null } } : null)
        : generalQuery
      await applyEvidenceScope(semesterEvidenceQuery(savedQuery?.scope ?? { mode: 'all' }, curriculumScope.selectedVolumeId), true)
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
  for (const student of diagnosis.students) {
    profileCache.value[student.student_id] = {
      score_rate: student.score_rate ?? null,
      score_rate_source: student.score_rate_source ?? 'none',
    }
  }
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

watch(selectedTargetKeys, keys => {
  if (adoptedGroup.value && JSON.stringify([...keys].sort()) !== JSON.stringify([...adoptedGroup.value.targetKeys].sort())) {
    useManualSelection()
  }
})

watch(
  [
    selectedTargetKeys,
    selectedRangeKeys,
    questionCount,
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

watch(trainingMode, (mode, previous) => {
  if (mode !== 'chapter' || previous !== 'student') return
  const active = activeEvidenceQuery.value
  const query = chapterScope.value ?? (active ? { ...active, scope: { ...active.scope, score_rate_min: null, score_rate_max: null } } : null)
  if (query) void applyEvidenceScope(query, true)
})

watch(() => curriculumScope.selectedVolumeId, () => {
  profileCache.value = {}
  adoptedGroup.value = null
  groupEditor.value = null
  latestGroupDiagnosis.value = null
  chapterKey.value = ''
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
onBeforeUnmount(() => studentsController?.abort())
</script>

<template>
  <section class="training-workspace" aria-labelledby="training-title">
    <header class="training-heading training-heading--compact">
      <div>
        <p class="training-eyebrow">知识与训练 · {{ curriculumScope.selectedVolume?.label ?? '未选择教学学期' }}</p>
        <h1 id="training-title">{{ pageCopy.title }}</h1>
        <p>{{ pageCopy.description }}</p>
      </div>
      <div v-if="training.diagnosis" class="training-basis">
        <strong>{{ trainingMode === 'paper' ? paperStudentCount : selectedStudentCount }} 名{{ trainingMode === 'paper' ? '出卷' : '' }}学生</strong>
        <span v-if="trainingMode === 'paper' && adoptedGroup && paperMode === 'shared'">推荐来源 {{ selectedStudentCount }} 人</span>
        <span>{{ training.diagnosis.knowledge_catalog?.length ?? 0 }} 个结构节点</span>
        <span>{{ selectedRangeKeys.length }} 个范围 · {{ selectedTargetKeys.length }} 项细点</span>
      </div>
    </header>

    <KnowledgeTrainingTabs />

    <EvidenceScopeFilters
      v-if="referenceState !== 'error' && trainingMode !== 'paper'"
      ref="scopeFilters"
      class="training-scope-filters"
      :model-value="activeEvidenceQuery"
      :students="students"
      :sessions="availableSessions"
      :current-session-id="sessionStore.selectedSessionId"
      :curriculum-volume-id="curriculumScope.selectedVolumeId"
      :applying="training.analysisState === 'loading'"
      :score-profiles="scoreProfiles"
      :evidence-from="trainingMode === 'student' ? 'student' : 'chapter'"
      :compact-roster="trainingMode === 'chapter' && groupingAvailable"
      show-score-floor
      @apply="applyEvidenceScope"
    />

    <p v-if="referenceState === 'error'" class="status-card error">
      学生名单暂时无法读取。请检查服务后重试；当前筛选没有被清空。
    </p>
    <p v-if="training.errorMessage" class="status-card error">{{ training.errorMessage }}</p>
    <p v-if="groupMessage" class="status-card" role="status">{{ groupMessage }}</p>

    <section v-if="trainingMode !== 'paper'" class="training-mode-panel">
      <header class="training-mode-panel__heading">
        <div>
          <p class="training-eyebrow">{{ trainingMode === 'chapter' ? '01 · 章节视角' : '02 · 学生视角' }}</p>
          <h2>{{ trainingMode === 'chapter' ? '推荐共同训练小组' : '群体知识结构' }}</h2>
        </div>
        <span>{{ scoreSourceSummary }}</span>
      </header>

      <p v-if="training.analysisState === 'loading' && !training.diagnosis" class="status-card">正在汇总学生与知识点……</p>
      <div v-else-if="training.analysisState === 'idle'" class="status-card empty-state">
        <p>选择学生和考试范围后，开始汇总掌握度。</p>
        <AppButton variant="primary" @click="analyze">开始汇总</AppButton>
      </div>
      <div v-else-if="training.analysisState === 'empty' && !training.diagnosis" class="status-card empty-state">
        当前范围没有可用知识证据，请调整学生或考试范围。
      </div>

      <template v-else-if="training.diagnosis">
        <p v-if="training.analysisState === 'loading'" class="training-updating">正在更新掌握汇总…</p>
        <ChapterTrainingMatrix
          v-if="trainingMode === 'chapter' && training.analysisState !== 'empty'"
          v-model="selectedTargetKeys"
          :diagnosis="training.diagnosis"
          :group-scope-label="groupScopeLabel"
          :initial-chapter-key="chapterKey"
          :initial-section-key="sectionKey"
          @adjust-scope="openScopeFilters"
          @scope-change="selectChapterScope"
        >
          <template v-if="groupingAvailable" #recommendations="{ scopeKey }">
            <TrainingGroupRecommendations
              v-model:sort-mode="groupSort"
              :diagnosis="training.diagnosis"
              :scope="sourceStudentScope"
              :exam-scope="personalizedExamScope"
              :settings="{ ...groupingSettings, scope_keys: scopeKey ? [scopeKey] : [] }"
              :editor="groupEditor"
              :adopted="adoptedGroup"
              :arrangements="arrangements"
              :disabled="training.analysisState === 'loading' || !paperNumericSettingsValid"
              @edit="groupEditor = $event"
              @adopt="adoptGroup"
            />
          </template>
        </ChapterTrainingMatrix>

        <TrainingKnowledgeStructure
          v-else-if="trainingMode !== 'chapter' && training.analysisState !== 'empty'"
          v-model="selectedRangeKeys"
          selection-kind="range"
          :diagnosis="training.diagnosis"
          title="所选学生的知识与技能关联"
          description="综合训练覆盖已学章节；勾选的章或小节仅在切换专项训练后限定选题范围。点击知识主题或技能可查看关联。"
          @focus="() => undefined"
        />
        <div v-else-if="training.analysisState === 'empty'" class="status-card empty-state">
          当前范围没有可用知识证据，请调整学生或考试范围。
        </div>

        <details v-if="training.diagnosis.warnings.length" class="training-data-note">
          <summary>数据说明（{{ training.diagnosis.warnings.length }}）</summary>
          <ul><li v-for="warning in training.diagnosis.warnings" :key="warning">{{ warning }}</li></ul>
        </details>

        <PaperSettingsPanel
          v-if="trainingMode === 'chapter' && training.analysisState !== 'empty'"
          mode="shared"
          :student-count="sharedStudentCount"
          :selection-text="`${selectedTargetKeys.length} 项细点`"
          :valid="sharedSettingsValid"
          :generating="draftRequestState === 'loading'"
          v-model:question-count="questionCount"
          v-model:difficulty-max="difficultyMax"
          v-model:teaching-progress-chapter-id="teachingProgressChapterId"
          :progress-chapters="progressChapters"
          @go-paper="goPaper('shared')"
        />
        <div v-if="trainingMode === 'chapter' && adoptedGroup" class="training-adopted-group">
          <span>已采用 {{ sharedStudentCount }} 人、{{ adoptedGroup.targetKeys.length }} 个共同目标；顶部仍保留 {{ selectedStudentCount }} 人的推荐范围。</span>
          <AppButton @click="useManualSelection">返回手动同卷</AppButton>
        </div>
        <PaperSettingsPanel
          v-if="trainingMode === 'student' && training.analysisState !== 'empty'"
          mode="individual"
          :student-count="selectedStudentCount"
          :selection-text="individualScope.label"
          :scope-summary="individualScope.label"
          v-model:scope-mode="scopeMode"
          :valid="individualSettingsValid"
          :generating="draftRequestState === 'loading'"
          v-model:question-count="questionCount"
          v-model:difficulty-max="difficultyMax"
          v-model:teaching-progress-chapter-id="teachingProgressChapterId"
          :progress-chapters="progressChapters"
          @go-paper="goPaper('individual')"
        />
      </template>
    </section>

    <section v-else class="paper-workspace">
      <header class="paper-workspace__heading">
        <div v-if="workflowStage === 'diagnosis'">
          <p class="training-eyebrow">03 · 出卷审核与导出</p>
          <h2>生成草稿与出卷</h2>
          <p>出卷设置在按章节/按学生训练页完成；本页只负责审核草稿、生成 PDF 试卷与扫描归卷。</p>
        </div>
        <div v-else>
          <p class="training-eyebrow">04 · 草稿审核与匹配预览</p>
          <h2>逐题核对错题依据与推荐题</h2>
          <p>设置已锁定；回到“按学生训练”或“按章节训练”调整后会重新生成草稿。</p>
        </div>
        <div class="paper-workspace__scope">
          <strong>{{ paperStudentCount }} 人</strong>
          <span>{{ selectedRangeKeys.length }} 个范围 · {{ selectedTargetKeys.length }} 个细点</span>
        </div>
      </header>

      <div v-if="!training.diagnosis" class="status-card empty-state">
        请先回到“按章节训练”勾选细知识点，或回到“按学生训练”勾选章/节范围。
      </div>
      <template v-else>
        <div class="paper-console is-reviewing">
          <div class="paper-console__main">
            <div class="paper-review-bar">
              <span><b>{{ paperMode === 'individual' ? '一人一卷' : '多人同一套卷' }}</b></span>
              <span v-if="paperMode === 'shared'">供 <b>{{ paperStudentCount }}</b> 名学生共同练习</span>
              <span v-else><b>{{ paperStudentCount }}</b> 名学生</span>
              <span v-if="paperMode === 'shared'"><b>{{ selectedTargetKeys.length }}</b> 项细点</span>
              <span v-else>{{ individualScope.label }}</span>
              <span>每卷 <b>{{ questionCount }}</b> 题</span>
              <span>难度上限 <b>{{ difficultyMax }}</b> 级</span>
              <span>已学到 {{ progressChapters.find(chapter => chapter.id === (paperMode === 'individual' ? individualScope.progressId : teachingProgressChapterId))?.label ?? '所选目标最晚章节' }}</span>
              <span>排除最近3次已批改活动原题</span>
              <RouterLink class="paper-review-bar__back" :to="paperBackTarget">{{ paperBackLabel }}</RouterLink>
              <AppButton
                v-if="workflowStage === 'diagnosis'"
                variant="primary"
                data-testid="generate-paper-draft"
                :disabled="!paperSettingsValid || draftRequestState === 'loading' || groupChecking"
                @click="generatePaperDraft"
              >
                {{ groupChecking ? '正在核对小组…' : draftRequestState === 'loading' ? '正在生成并核对…' : draftNeedsCheck ? '核对生成结果' : `生成 ${expectedPaperCount} 份草稿` }}
              </AppButton>
            </div>
            <p v-if="workflowStage === 'diagnosis' && !paperSettingsValid" class="paper-review-hint">
              {{ paperMode === 'shared'
                ? '多人同一套卷：请回到“按章节训练”勾选至少一个细知识点并完成出卷设置。'
                : '一人一卷：请回到“按学生训练”确认已学进度，或选择专项范围并完成出卷设置。' }}
            </p>

            <PersonalizedRecommendationDraft
              ref="paperDraft"
              external-setup
              :diagnosis="paperDiagnosis"
              :scope="personalizedScope"
              :exam-scope="personalizedExamScope"
              :question-count="questionCount"
              :difficulty-max="difficultyMax"
              :teaching-progress-chapter-id="paperMode === 'individual' ? individualScope.progressId : teachingProgressChapterId"

              :exclude-current-exam-originals="excludeCurrentOriginals"
              :paper-mode="paperMode"
              :target-keys="paperMode === 'shared' ? selectedTargetKeys : []"
              :scope-keys="paperMode === 'shared' ? [] : individualScope.keys"
              :group-scope-keys="paperMode === 'shared' ? adoptedGroup?.scopeKeys : undefined"
              :group-source-version="paperMode === 'shared' ? adoptedGroup?.sourceVersion : undefined"
              :curriculum-volume-id="curriculumScope.selectedVolumeId"
              :disabled="!paperSettingsValid"
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
.training-workspace { grid-template-columns:minmax(0,1fr);min-width:0 }
.training-adopted-group { display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.8rem 0;font-size:.85rem;color:var(--color-text-secondary) }
.training-heading--compact { align-items: end; min-height: 96px; }
.training-scope-filters { margin-top: var(--space-2); }
.training-mode-panel,
.paper-workspace { margin-top: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); overflow: hidden; }
.training-mode-panel__heading,
.paper-workspace__heading { display: flex; justify-content: space-between; gap: var(--space-6); align-items: end; padding: var(--space-5) var(--space-6); border-bottom: 1px solid var(--border); }
.training-mode-panel__heading h2,
.paper-workspace__heading h2 { margin: var(--space-1) 0 0; }
.training-mode-panel__heading > span { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.training-data-note { margin: var(--space-3) var(--space-6) var(--space-6); color: var(--color-text-muted); }
.training-updating { margin: 0; padding: 8px var(--space-6); color: var(--color-text-muted); font-size: var(--font-size-caption); line-height: var(--line-height-body); }
.paper-workspace__heading p { margin: var(--space-1) 0 0; color: var(--color-text-muted); }
.paper-workspace__scope { display: grid; text-align: right; }
.paper-console { display: grid; grid-template-columns: 1fr; gap: var(--space-5); padding: var(--space-5); background: var(--color-bg-subtle); }
.paper-review-bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-4); padding: var(--space-3) var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.paper-review-bar span { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-review-bar b { color: var(--color-text-primary); font-weight: 600; }
.paper-review-bar__back { margin-left: auto; color: var(--color-accent-active); font-size: var(--font-size-dense); }
.paper-review-bar button { margin-left: auto; }
.paper-review-bar__back + button { margin-left: 0; }
.paper-review-hint { margin: 0; color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-console__main { display: grid; min-width: 0; gap: var(--space-4); align-content: start; }
@media(max-width:760px){.training-mode-panel__heading,.paper-workspace__heading,.training-adopted-group{align-items:flex-start;flex-direction:column}.paper-console{padding:var(--space-3)}.paper-workspace__scope{text-align:left}}
</style>
