<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import type {
  TrainingExamScopeRequest,
  TrainingStudentScopeRequest,
} from '../api/training'
import EvidenceScopeFilters from '../components/evidence/EvidenceScopeFilters.vue'
import ChapterTrainingMatrix from '../components/knowledge-training/ChapterTrainingMatrix.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import PaperSettingsPanel from '../components/knowledge-training/PaperSettingsPanel.vue'
import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
import PersonalizedRecommendationDraft from '../components/training/PersonalizedRecommendationDraft.vue'
import AppButton from '../components/design-system/AppButton.vue'
import { loadEvidenceScope, saveEvidenceScope } from '../features/evidence-scope/session'
import { loadPaperSelectionSession, savePaperSelectionSession } from '../features/training/paper-selection-session'
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
const expectedMinutes = ref(savedPaperSelection?.expectedMinutes ?? 40)
const difficultyMin = ref(savedPaperSelection?.difficultyMin ?? 2)
const difficultyMax = ref(savedPaperSelection?.difficultyMax ?? 8)
const excludeCurrentOriginals = ref(savedPaperSelection?.excludeCurrentOriginals ?? true)
const directRatio = ref(savedPaperSelection?.directRatio ?? 60)
const prerequisiteRatio = ref(savedPaperSelection?.prerequisiteRatio ?? 30)
const transferRatio = ref(savedPaperSelection?.transferRatio ?? 10)
const paperMode = ref<'individual' | 'shared'>(savedPaperSelection?.paperMode ?? 'individual')
const workflowStage = ref<'diagnosis' | 'draft' | 'wps' | 'scan'>('diagnosis')
const draftRequestState = ref<'idle' | 'loading' | 'ready' | 'error' | 'editing'>('idle')
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
    description: '先选章与小节，在学生 × 知识点热力图中核对差异，再把需要训练的知识点加入清单。',
  },
  student: {
    title: '按学生训练',
    description: '用顶部筛选确定学生群体，勾选章或小节作为训练范围；生成一人一卷时按每名学生的细知识点掌握情况配题。',
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
const scoreSourceSummary = computed(() => {
  const profiles = training.diagnosis?.students ?? []
  const current = profiles.filter((student) => student.score_rate_source === 'current_exam').length
  const historical = profiles.filter((student) => student.score_rate_source === 'historical_fallback').length
  const none = profiles.length - current - historical
  return `所选 ${profiles.length} 人 · 本次成绩 ${current} 人 · 历史参考 ${historical} 人 · 无成绩 ${none} 人`
})
const stageRatioTotal = computed(() => directRatio.value + prerequisiteRatio.value + transferRatio.value)
const groupScopeLabel = computed(() => {
  if (training.studentScope.mode === 'selected') return `当前筛选 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length === 1) return `${training.studentScope.classIds[0]} · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length > 1) return `${training.studentScope.classIds.length} 个班级 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classId) return `${training.studentScope.classId} · ${selectedStudentCount.value} 人`
  return `全部班级 · ${selectedStudentCount.value} 人`
})
const paperNumericSettingsValid = computed(() => (
  stageRatioTotal.value === 100
  && questionCount.value >= 8
  && questionCount.value <= 12
  && expectedMinutes.value >= 10
  && expectedMinutes.value <= 180
  && difficultyMin.value >= 1
  && difficultyMax.value <= 10
  && difficultyMin.value <= difficultyMax.value
))
// 多人同一套卷用共同细点出题；一人一卷用章/节范围出题。
// 出卷页按最后编辑页记录的模式（paperMode）取对应的校验。
const sharedSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && selectedTargetKeys.value.length > 0
  && paperNumericSettingsValid.value
))
const individualSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && selectedRangeKeys.value.length > 0
  && paperNumericSettingsValid.value
))
const paperSettingsValid = computed(() => (
  paperMode.value === 'shared'
    ? sharedSettingsValid.value
    : individualSettingsValid.value
))
const expectedPaperCount = computed(() => selectedStudentCount.value)

const personalizedScope = computed<TrainingStudentScopeRequest>(() => ({
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
const personalizedExamScope = computed<TrainingExamScopeRequest>(() => ({
  mode: training.examScope.mode,
  session_ids: [...training.examScope.sessionIds],
}))
const activeEvidenceQuery = computed<GraphQueryInput | null>(() => {
  const sessionIds = training.examScope.sessionIds
  if (training.examScope.mode !== 'cross_exam' && !sessionIds.length) return null
  return {
    scope: {
      mode: training.studentScope.mode,
      ...(training.studentScope.classId ? { class_id: training.studentScope.classId } : {}),
      ...(training.studentScope.classIds.length ? { class_ids: [...training.studentScope.classIds] } : {}),
      ...(training.studentScope.studentIds.length ? { student_ids: [...training.studentScope.studentIds] } : {}),
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

async function analyze(): Promise<void> {
  try {
    await training.analyze()
  } catch {
    // The store publishes a safe user-facing recovery message.
  }
}

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

function openScopeFilters(): void {
  const filters = scopeFilters.value
  if (!filters) return
  filters.openMoreFilters()
  ;(filters.$el as HTMLElement | undefined)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

function generatePaperDraft(): void {
  void paperDraft.value?.generate()
}

// 上游页（按章节/按学生训练）完成设置后跳转到出卷页：
// 以最后编辑的页为准记录出卷模式。
function goPaper(mode: 'individual' | 'shared'): void {
  paperMode.value = mode
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
        exam_scope: { mode: 'current', session_ids: [sessionStore.selectedSessionId] },
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

watch(
  [
    selectedTargetKeys,
    selectedRangeKeys,
    questionCount,
    expectedMinutes,
    difficultyMin,
    difficultyMax,
    excludeCurrentOriginals,
    directRatio,
    prerequisiteRatio,
    transferRatio,
    paperMode,
  ],
  () => {
    savePaperSelectionSession({
      targetKeys: [...selectedTargetKeys.value],
      rangeKeys: [...selectedRangeKeys.value],
      questionCount: questionCount.value,
      expectedMinutes: expectedMinutes.value,
      difficultyMin: difficultyMin.value,
      difficultyMax: difficultyMax.value,
      excludeCurrentOriginals: excludeCurrentOriginals.value,
      directRatio: directRatio.value,
      prerequisiteRatio: prerequisiteRatio.value,
      transferRatio: transferRatio.value,
      paperMode: paperMode.value,
    })
  },
)

watch(() => sessionStore.selectedSessionId, (sessionId) => {
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
})

onMounted(() => void loadStudents())
onBeforeUnmount(() => studentsController?.abort())
</script>

<template>
  <section class="training-workspace" aria-labelledby="training-title">
    <header class="training-heading training-heading--compact">
      <div>
        <p class="training-eyebrow">知识与训练 · {{ curriculumScope.selectedVolume?.label ?? '全部学期' }}</p>
        <h1 id="training-title">{{ pageCopy.title }}</h1>
        <p>{{ pageCopy.description }}</p>
      </div>
      <div v-if="training.diagnosis" class="training-basis">
        <strong>{{ selectedStudentCount }} 名学生</strong>
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
      @apply="applyEvidenceScope"
    />

    <p v-if="referenceState === 'error'" class="status-card error">
      学生名单暂时无法读取。请检查服务后重试；当前筛选没有被清空。
    </p>
    <p v-if="training.errorMessage" class="status-card error">{{ training.errorMessage }}</p>

    <section v-if="trainingMode !== 'paper'" class="training-mode-panel">
      <header class="training-mode-panel__heading">
        <div>
          <p class="training-eyebrow">{{ trainingMode === 'chapter' ? '01 · 章节视角' : '02 · 学生视角' }}</p>
          <h2>{{ trainingMode === 'chapter' ? '章节学生热力图' : '群体知识结构' }}</h2>
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
          @adjust-scope="openScopeFilters"
        />

        <TrainingKnowledgeStructure
          v-else-if="trainingMode !== 'chapter' && training.analysisState !== 'empty'"
          v-model="selectedRangeKeys"
          selection-kind="range"
          :diagnosis="training.diagnosis"
          title="所选学生的加权知识结构"
          description="勾选章或小节作为训练范围；细知识点只作掌握情况参考，一人一卷时按每名学生自动配题。"
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
          :student-count="selectedStudentCount"
          :selection-text="`${selectedTargetKeys.length} 项细点`"
          :valid="sharedSettingsValid"
          :generating="draftRequestState === 'loading'"
          v-model:question-count="questionCount"
          v-model:expected-minutes="expectedMinutes"
          v-model:difficulty-min="difficultyMin"
          v-model:difficulty-max="difficultyMax"
          v-model:direct-ratio="directRatio"
          v-model:prerequisite-ratio="prerequisiteRatio"
          v-model:transfer-ratio="transferRatio"
          v-model:exclude-current-originals="excludeCurrentOriginals"
          @go-paper="goPaper('shared')"
        />
        <PaperSettingsPanel
          v-else-if="trainingMode === 'student' && training.analysisState !== 'empty'"
          mode="individual"
          :student-count="selectedStudentCount"
          :selection-text="`${selectedRangeKeys.length} 个范围`"
          :valid="individualSettingsValid"
          :generating="draftRequestState === 'loading'"
          v-model:question-count="questionCount"
          v-model:expected-minutes="expectedMinutes"
          v-model:difficulty-min="difficultyMin"
          v-model:difficulty-max="difficultyMax"
          v-model:direct-ratio="directRatio"
          v-model:prerequisite-ratio="prerequisiteRatio"
          v-model:transfer-ratio="transferRatio"
          v-model:exclude-current-originals="excludeCurrentOriginals"
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
          <strong>{{ selectedStudentCount }} 人</strong>
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
              <span><b>{{ selectedStudentCount }}</b> 名学生</span>
              <span v-if="paperMode === 'shared'"><b>{{ selectedTargetKeys.length }}</b> 项细点</span>
              <span v-else><b>{{ selectedRangeKeys.length }}</b> 个范围</span>
              <span>每卷 <b>{{ questionCount }}</b> 题</span>
              <span>约 <b>{{ expectedMinutes }}</b> 分钟</span>
              <span>难度 <b>{{ difficultyMin }}–{{ difficultyMax }}</b></span>
              <span>针对 <b>{{ directRatio }}</b>% · 基础 <b>{{ prerequisiteRatio }}</b>% · 提升 <b>{{ transferRatio }}</b>%</span>
              <span v-if="excludeCurrentOriginals">排除本次考试原题</span>
              <RouterLink class="paper-review-bar__back" :to="paperBackTarget">{{ paperBackLabel }}</RouterLink>
              <AppButton
                v-if="workflowStage === 'diagnosis'"
                variant="primary"
                data-testid="generate-paper-draft"
                :disabled="!paperSettingsValid || draftRequestState === 'loading'"
                @click="generatePaperDraft"
              >
                {{ draftRequestState === 'loading' ? '正在生成…' : `生成 ${expectedPaperCount} 份草稿` }}
              </AppButton>
            </div>
            <p v-if="workflowStage === 'diagnosis' && !paperSettingsValid" class="paper-review-hint">
              {{ paperMode === 'shared'
                ? '多人同一套卷：请回到“按章节训练”勾选至少一个细知识点并完成出卷设置。'
                : '一人一卷：请回到“按学生训练”勾选至少一个章/节范围并完成出卷设置。' }}
            </p>

            <PersonalizedRecommendationDraft
              ref="paperDraft"
              external-setup
              :diagnosis="training.diagnosis"
              :scope="personalizedScope"
              :exam-scope="personalizedExamScope"
              :question-count="questionCount"
              :expected-minutes="expectedMinutes"
              :difficulty-min="difficultyMin"
              :difficulty-max="difficultyMax"
              :stage-ratios="{
                direct: directRatio / 100,
                prerequisite: prerequisiteRatio / 100,
                transfer: transferRatio / 100,
              }"
              :exclude-current-exam-originals="excludeCurrentOriginals"
              :paper-mode="paperMode"
              :target-keys="selectedTargetKeys"
              :scope-keys="selectedRangeKeys"
              :curriculum-volume-id="curriculumScope.selectedVolumeId"
              :disabled="!paperSettingsValid"
              @stage-change="onPaperStageChange"
              @state-change="draftRequestState = $event"
            />
          </div>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
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
</style>
