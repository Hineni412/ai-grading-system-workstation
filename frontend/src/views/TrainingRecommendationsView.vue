<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { knowledgeLeafLabel } from '../api/question-bank'
import { fetchStudents, type StudentSummary } from '../api/students'
import type {
  TrainingDiagnosis,
  TrainingExamScopeRequest,
  TrainingStudentScopeRequest,
} from '../api/training'
import EvidenceScopeFilters from '../components/evidence/EvidenceScopeFilters.vue'
import ChapterTrainingMatrix from '../components/knowledge-training/ChapterTrainingMatrix.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import TrainingKnowledgeStructure from '../components/knowledge-training/TrainingKnowledgeStructure.vue'
import PersonalizedRecommendationDraft from '../components/training/PersonalizedRecommendationDraft.vue'
import AppButton from '../components/design-system/AppButton.vue'
import { loadEvidenceScope, saveEvidenceScope } from '../features/evidence-scope/session'
import { useSessionStore } from '../stores/session'
import '../styles/training-recommendations.css'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useTrainingStore } from '../stores/training'

type ReferenceState = 'loading' | 'ready' | 'error'
type TrainingMode = 'chapter' | 'student' | 'paper'

const route = useRoute()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const training = useTrainingStore()

const students = ref<StudentSummary[]>([])
const referenceState = ref<ReferenceState>('loading')
const selectedTargetKeys = ref<string[]>([])
const selectedStudentIds = ref<string[]>([])
const masteryFilterMin = ref(0)
const masteryFilterMax = ref(100)
const studentMasteryCache = ref<Record<string, number | null>>({})
const questionCount = ref(10)
const expectedMinutes = ref(40)
const difficultyMin = ref(2)
const difficultyMax = ref(8)
const excludeCurrentOriginals = ref(true)
const directRatio = ref(60)
const prerequisiteRatio = ref(30)
const transferRatio = ref(10)
const paperMode = ref<'individual' | 'shared'>('individual')
const workflowStage = ref<'diagnosis' | 'draft' | 'wps' | 'scan'>('diagnosis')
const draftRequestState = ref<'idle' | 'loading' | 'ready' | 'error' | 'editing'>('idle')
const scopeDisclosure = ref<HTMLDetailsElement | null>(null)
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
    description: '按掌握度快速筛选并多选学生，查看所选群体的加权知识结构，再确定训练范围。',
  },
  paper: {
    title: '生成试卷',
    description: '集中设置出卷方式、题量和训练比例；生成后在本页完成草稿审核、WPS 输出和扫描回流。',
  },
}[trainingMode.value]))

const availableSessions = computed(() => sessionStore.sessions.filter((item) => !item.is_deleted))

function profileMastery(student: TrainingDiagnosis['students'][number]): number | null {
  const evidence = student.weak_points.reduce((total, point) => total + point.evidence_count, 0)
  const weighted = student.weak_points.reduce((total, point) => (
    total + point.mastery * point.evidence_count
  ), 0)
  return evidence ? weighted / evidence : null
}

const studentMasteryRows = computed(() => {
  const diagnosisProfiles = new Map((training.diagnosis?.students ?? []).map((student) => [
    student.student_id,
    student,
  ]))
  const roster = students.value.length
    ? students.value.map((student) => ({
        id: String(student.id),
        code: student.student_code,
        name: student.name,
      }))
    : [...diagnosisProfiles.values()].map((student) => ({
        id: student.student_id,
        code: student.student_code,
        name: student.student_name,
      }))
  return roster.map((student) => ({
    ...student,
    mastery: diagnosisProfiles.has(student.id)
      ? profileMastery(diagnosisProfiles.get(student.id)!)
      : studentMasteryCache.value[student.id] ?? null,
  }))
})
const filteredStudentMasteryRows = computed(() => studentMasteryRows.value.filter((student) => (
  student.mastery !== null
  && student.mastery * 100 >= masteryFilterMin.value
  && student.mastery * 100 <= masteryFilterMax.value
)))
const selectedStudentCount = computed(() => training.diagnosis?.students.length ?? 0)
const scoreSourceSummary = computed(() => {
  const profiles = training.diagnosis?.students ?? []
  const current = profiles.filter((student) => student.score_rate_source === 'current_exam').length
  const historical = profiles.filter((student) => student.score_rate_source === 'historical_fallback').length
  const none = profiles.length - current - historical
  return `所选 ${profiles.length} 人 · 本次成绩 ${current} 人 · 历史参考 ${historical} 人 · 无成绩 ${none} 人`
})
const selectedTargetLabels = computed(() => {
  const labels = new Map((training.diagnosis?.knowledge_catalog ?? []).map((item) => [
    item.knowledge_key,
    item.knowledge_point,
  ]))
  const aggregates = new Map((training.diagnosis?.group_weak_points ?? []).map((item) => [
    item.knowledge_key,
    item,
  ]))
  return selectedTargetKeys.value.map((key) => ({
    key,
    label: knowledgeLeafLabel(labels.get(key) ?? key),
    fullLabel: labels.get(key) ?? key,
    mastery: aggregates.get(key)?.mastery ?? null,
    evidenceCount: aggregates.get(key)?.evidence_count ?? 0,
  }))
})
const stageRatioTotal = computed(() => directRatio.value + prerequisiteRatio.value + transferRatio.value)
const groupScopeLabel = computed(() => {
  if (training.studentScope.mode === 'selected') return `当前筛选 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length === 1) return `${training.studentScope.classIds[0]} · ${selectedStudentCount.value} 人`
  if (training.studentScope.classIds.length > 1) return `${training.studentScope.classIds.length} 个班级 · ${selectedStudentCount.value} 人`
  if (training.studentScope.classId) return `${training.studentScope.classId} · ${selectedStudentCount.value} 人`
  return `全部班级 · ${selectedStudentCount.value} 人`
})
const paperSettingsValid = computed(() => (
  Boolean(training.diagnosis)
  && selectedTargetKeys.value.length > 0
  && stageRatioTotal.value === 100
  && questionCount.value >= 8
  && questionCount.value <= 12
  && expectedMinutes.value >= 10
  && expectedMinutes.value <= 180
  && difficultyMin.value >= 1
  && difficultyMax.value <= 10
  && difficultyMin.value <= difficultyMax.value
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

function selectFilteredStudents(): void {
  selectedStudentIds.value = filteredStudentMasteryRows.value.map((student) => student.id)
}

async function applySelectedStudents(): Promise<void> {
  const active = activeEvidenceQuery.value
  if (!active || !selectedStudentIds.value.length) return
  await applyEvidenceScope({
    ...active,
    scope: {
      ...active.scope,
      mode: 'selected',
      student_ids: [...selectedStudentIds.value],
      include_student_ids: [],
      exclude_student_ids: [],
    },
  })
}

function openScopeFilters(): void {
  if (!scopeDisclosure.value) return
  scopeDisclosure.value.open = true
  scopeDisclosure.value.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

function removeTarget(key: string): void {
  selectedTargetKeys.value = selectedTargetKeys.value.filter((item) => item !== key)
}

function generatePaperDraft(): void {
  void paperDraft.value?.generate()
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
  const nextCache = { ...studentMasteryCache.value }
  for (const student of diagnosis?.students ?? []) nextCache[student.student_id] = profileMastery(student)
  studentMasteryCache.value = nextCache
  selectedStudentIds.value = (diagnosis?.students ?? []).map((student) => student.student_id)
  const validKeys = new Set((diagnosis?.knowledge_catalog ?? []).map((item) => item.knowledge_key))
  selectedTargetKeys.value = selectedTargetKeys.value.filter((key) => validKeys.has(key))
}, { immediate: true })

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
        <span>{{ selectedTargetKeys.length }} 项已选</span>
      </div>
    </header>

    <KnowledgeTrainingTabs />

    <details ref="scopeDisclosure" v-if="referenceState !== 'error' && trainingMode !== 'paper'" class="scope-disclosure">
      <summary>调整学生与考试范围</summary>
      <EvidenceScopeFilters
        :model-value="activeEvidenceQuery"
        :students="students"
        :sessions="availableSessions"
        :current-session-id="sessionStore.selectedSessionId"
        :curriculum-volume-id="curriculumScope.selectedVolumeId"
        :applying="training.analysisState === 'loading'"
        @apply="applyEvidenceScope"
      />
    </details>

    <p v-if="referenceState === 'error'" class="status-card error">
      学生名单暂时无法读取。请检查服务后重试；当前筛选没有被清空。
    </p>
    <p v-if="training.errorMessage" class="status-card error">{{ training.errorMessage }}</p>

    <section v-if="trainingMode !== 'paper'" class="training-mode-panel">
      <header class="training-mode-panel__heading">
        <div>
          <p class="training-eyebrow">{{ trainingMode === 'chapter' ? '01 · 章节视角' : '02 · 学生视角' }}</p>
          <h2>{{ trainingMode === 'chapter' ? '章节学生热力图' : '学生筛选与群体知识结构' }}</h2>
        </div>
        <span>{{ scoreSourceSummary }}</span>
      </header>

      <p v-if="training.analysisState === 'loading'" class="status-card">正在汇总学生与知识点……</p>
      <div v-else-if="training.analysisState === 'idle'" class="status-card empty-state">
        <p>选择学生和考试范围后，开始汇总掌握度。</p>
        <AppButton variant="primary" @click="analyze">开始汇总</AppButton>
      </div>
      <div v-else-if="training.analysisState === 'empty'" class="status-card empty-state">
        当前范围没有可用知识证据，请调整学生或考试范围。
      </div>

      <template v-else-if="training.diagnosis">
        <ChapterTrainingMatrix
          v-if="trainingMode === 'chapter'"
          v-model="selectedTargetKeys"
          :diagnosis="training.diagnosis"
          :group-scope-label="groupScopeLabel"
          @adjust-scope="openScopeFilters"
        />

        <template v-else>
          <section class="student-filter-strip" aria-labelledby="student-filter-title">
            <header>
              <div>
                <h3 id="student-filter-title">多选学生</h3>
                <p>先按总体掌握度缩小名单，再选择具体学生。</p>
              </div>
              <strong>{{ selectedStudentIds.length }} / {{ studentMasteryRows.length }} 人</strong>
            </header>
            <div class="student-filter-strip__tools">
              <label>最低<input v-model.number="masteryFilterMin" type="number" min="0" max="100"><span>%</span></label>
              <label>最高<input v-model.number="masteryFilterMax" type="number" min="0" max="100"><span>%</span></label>
              <AppButton @click="selectFilteredStudents">选中筛选结果</AppButton>
              <AppButton variant="ghost" class="button-link" @click="selectedStudentIds = []">清空</AppButton>
            </div>
            <div class="student-filter-strip__roster">
              <label v-for="student in filteredStudentMasteryRows" :key="student.id">
                <input v-model="selectedStudentIds" type="checkbox" :value="student.id">
                <span><b>{{ student.name }}</b><small>{{ student.code }}</small></span>
                <strong>{{ student.mastery === null ? '—' : `${Math.round(student.mastery * 100)}%` }}</strong>
              </label>
            </div>
            <AppButton
              variant="primary"
              block
              class="student-filter-strip__apply"
              :disabled="!selectedStudentIds.length"
              @click="applySelectedStudents"
            >
              应用 {{ selectedStudentIds.length }} 名学生并重算群体掌握度
            </AppButton>
          </section>

          <TrainingKnowledgeStructure
            v-model="selectedTargetKeys"
            :diagnosis="training.diagnosis"
            title="所选学生的加权知识结构"
            description="按章、节逐层展开；横条表示所选学生的加权掌握度，灰底表示满量程，斜纹表示无证据。"
            @focus="() => undefined"
          />
        </template>

        <details v-if="training.diagnosis.warnings.length" class="training-data-note">
          <summary>数据说明（{{ training.diagnosis.warnings.length }}）</summary>
          <ul><li v-for="warning in training.diagnosis.warnings" :key="warning">{{ warning }}</li></ul>
        </details>
      </template>
    </section>

    <section v-else class="paper-workspace">
      <header class="paper-workspace__heading">
        <div>
          <p class="training-eyebrow">03 · 独立出卷页</p>
          <h2>出卷设置与草稿</h2>
          <p>这里不再重复诊断图表，只承接前两页人工选中的学生与知识点。</p>
        </div>
        <div class="paper-workspace__scope">
          <strong>{{ selectedStudentCount }} 人</strong>
          <span>{{ selectedTargetKeys.length }} 个训练知识点</span>
        </div>
      </header>

      <div v-if="!training.diagnosis" class="status-card empty-state">
        请先回到“按章节训练”或“按学生训练”，确定学生与训练知识点。
      </div>
      <template v-else>
        <div class="paper-console">
          <div class="paper-console__main">
            <section class="paper-settings" aria-labelledby="paper-settings-title">
              <header>
                <div>
                  <p class="training-eyebrow">设置</p>
                  <h3 id="paper-settings-title">出卷方式与训练强度</h3>
                </div>
                <span>所有设置集中在一个区域完成</span>
              </header>
              <div class="paper-mode-options">
                <label :class="{ 'is-selected': paperMode === 'individual' }">
                  <input v-model="paperMode" type="radio" value="individual" :disabled="workflowStage !== 'diagnosis'">
                  <span><b>一人一卷</b><small>按每名学生的掌握证据分别选题</small></span>
                </label>
                <label :class="{ 'is-selected': paperMode === 'shared' }">
                  <input v-model="paperMode" type="radio" value="shared" :disabled="workflowStage !== 'diagnosis'">
                  <span><b>多人同一套卷</b><small>使用群体加权结果统一选题，回收身份仍独立</small></span>
                </label>
              </div>
              <div class="paper-settings__grid">
                <label>每卷题数<input v-model.number="questionCount" type="number" min="8" max="12" :disabled="workflowStage !== 'diagnosis'"><small>8–12 题</small></label>
                <label>预计时长<input v-model.number="expectedMinutes" type="number" min="10" max="180" :disabled="workflowStage !== 'diagnosis'"><small>分钟</small></label>
                <label>最低难度<input v-model.number="difficultyMin" type="number" min="1" max="10" :disabled="workflowStage !== 'diagnosis'"><small>1–10</small></label>
                <label>最高难度<input v-model.number="difficultyMax" type="number" min="1" max="10" :disabled="workflowStage !== 'diagnosis'"><small>1–10</small></label>
              </div>
              <div class="paper-ratios" aria-label="训练题目比例">
                <label><span>针对训练</span><input v-model.number="directRatio" type="number" min="0" max="100" :disabled="workflowStage !== 'diagnosis'"><b>%</b></label>
                <label><span>基础巩固</span><input v-model.number="prerequisiteRatio" type="number" min="0" max="100" :disabled="workflowStage !== 'diagnosis'"><b>%</b></label>
                <label><span>提升应用</span><input v-model.number="transferRatio" type="number" min="0" max="100" :disabled="workflowStage !== 'diagnosis'"><b>%</b></label>
                <strong :class="{ 'is-error': stageRatioTotal !== 100 }">合计 {{ stageRatioTotal }}%</strong>
              </div>
              <p v-if="stageRatioTotal !== 100" class="field-error">三类训练比例合计需为 100%。</p>
              <p v-if="difficultyMin > difficultyMax" class="field-error">最低难度不能高于最高难度。</p>
            </section>

            <section class="paper-targets" aria-labelledby="paper-targets-title">
              <header>
                <div><p class="training-eyebrow">目标</p><h3 id="paper-targets-title">本次训练知识点</h3></div>
                <span>{{ selectedTargetKeys.length }} 项</span>
              </header>
              <div v-if="selectedTargetLabels.length" class="paper-targets__rows">
                <article v-for="target in selectedTargetLabels" :key="target.key">
                  <span class="paper-targets__status" :class="target.mastery === null ? 'is-empty' : target.mastery < .6 ? 'is-low' : target.mastery < .75 ? 'is-mid' : 'is-good'"></span>
                  <strong :title="target.fullLabel">{{ target.label }}</strong>
                  <span>{{ target.mastery === null ? '无证据' : `${Math.round(target.mastery * 100)}%` }}</span>
                  <small>{{ target.evidenceCount }} 条证据</small>
                  <button type="button" :disabled="workflowStage !== 'diagnosis'" :aria-label="`移除${target.label}`" @click="removeTarget(target.key)">移除</button>
                </article>
              </div>
              <div v-else class="paper-targets__empty">
                尚未选择知识点。请先在“按章节训练”或“按学生训练”中勾选。
              </div>
            </section>

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
              :disabled="!paperSettingsValid"
              @stage-change="workflowStage = $event"
              @state-change="draftRequestState = $event"
            />
          </div>

          <aside class="paper-console__aside" aria-labelledby="paper-summary-title">
            <section>
              <p class="training-eyebrow">本次出卷摘要</p>
              <h3 id="paper-summary-title">确认后生成草稿</h3>
              <dl>
                <div><dt>学生范围</dt><dd>{{ selectedStudentCount }} 人</dd></div>
                <div><dt>训练知识点</dt><dd>{{ selectedTargetKeys.length }} 项</dd></div>
                <div><dt>预计试卷</dt><dd>{{ expectedPaperCount }} 份</dd></div>
                <div><dt>每卷题量</dt><dd>{{ questionCount }} 题</dd></div>
                <div><dt>预计时长</dt><dd>{{ expectedMinutes }} 分钟</dd></div>
              </dl>
              <p class="paper-console__mode-note">{{ paperMode === 'individual' ? '每名学生按各自证据选题。' : '所有学生使用同一套题，姓名和回收身份独立。' }}</p>
              <label class="paper-console__exclude"><input v-model="excludeCurrentOriginals" type="checkbox" :disabled="workflowStage !== 'diagnosis'">排除本次考试原题</label>
            </section>
            <section class="paper-console__action">
              <template v-if="workflowStage === 'diagnosis'">
                <strong>{{ paperSettingsValid ? '设置完整，可以生成' : '还需完成必要设置' }}</strong>
                <span>{{ selectedTargetKeys.length ? '生成后先进入教师审核，不会直接形成正式试卷。' : '请先选择至少一个训练知识点。' }}</span>
                <AppButton variant="primary" block data-testid="generate-paper-draft" :disabled="!paperSettingsValid || draftRequestState === 'loading'" @click="generatePaperDraft">
                  {{ draftRequestState === 'loading' ? '正在生成…' : `生成 ${expectedPaperCount} 份草稿` }}
                </AppButton>
              </template>
              <template v-else>
                <strong>草稿已生成</strong>
                <span>请在左侧继续审核、输出 WPS 或处理扫描回流。</span>
              </template>
            </section>
          </aside>
        </div>
      </template>
    </section>
  </section>
</template>

<style scoped>
.training-heading--compact { align-items: end; }
.scope-disclosure { margin-top: var(--space-3); }
.scope-disclosure > summary { margin-left: auto; width: max-content; padding: var(--space-2) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--card); color: var(--color-text-secondary); cursor: pointer; list-style: none; }
.scope-disclosure > summary::after { content: ' ▾'; }
.scope-disclosure[open] > summary::after { content: ' ▴'; }
.scope-disclosure > :deep(.evidence-scope) { margin-top: var(--space-2); }
.training-mode-panel,
.paper-workspace { margin-top: var(--space-5); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); overflow: hidden; }
.training-mode-panel__heading,
.paper-workspace__heading { display: flex; justify-content: space-between; gap: var(--space-6); align-items: end; padding: var(--space-5) var(--space-6); border-bottom: 1px solid var(--border); }
.training-mode-panel__heading h2,
.paper-workspace__heading h2 { margin: var(--space-1) 0 0; }
.training-mode-panel__heading > span { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.student-filter-strip { margin: var(--space-6); padding: var(--space-4); border-radius: var(--radius); background: var(--color-bg-subtle); }
.student-filter-strip header,
.student-filter-strip__tools { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.student-filter-strip h3,
.student-filter-strip p { margin: 0; }
.student-filter-strip p { color: var(--color-text-muted); font-size: var(--font-size-dense); }
.student-filter-strip__tools { justify-content: flex-start; margin: var(--space-3) 0; }
.student-filter-strip__tools label { display: flex; align-items: center; gap: var(--space-2); color: var(--color-text-muted); }
.student-filter-strip__tools input { width: 66px; }
.student-filter-strip__roster { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-2); }
.student-filter-strip__roster > label { display: grid; grid-template-columns: auto 1fr auto; gap: var(--space-2); align-items: center; min-height: 46px; padding: var(--space-2) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--card); cursor: pointer; }
.student-filter-strip__roster span { display: grid; line-height: 1.15; }
.student-filter-strip__roster small { color: var(--color-text-muted); }
.student-filter-strip__apply { width: 100%; margin-top: var(--space-3); }
.training-data-note { margin: var(--space-3) var(--space-6) var(--space-6); color: var(--color-text-muted); }
.paper-workspace__heading p { margin: var(--space-1) 0 0; color: var(--color-text-muted); }
.paper-workspace__scope { display: grid; text-align: right; }
.paper-console { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: var(--space-5); padding: var(--space-5); background: var(--color-bg-subtle); }
.paper-console__main { display: grid; min-width: 0; gap: var(--space-4); align-content: start; }
.paper-settings,
.paper-targets,
.paper-console__aside > section { padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.paper-settings > header,
.paper-targets > header { display: flex; justify-content: space-between; align-items: end; gap: var(--space-4); }
.paper-settings > header > span,
.paper-targets > header > span { color: var(--color-text-muted); font-size: var(--font-size-caption); }
.paper-settings h3,
.paper-targets h3,
.paper-console__aside h3 { margin: var(--space-1) 0 0; }
.paper-mode-options { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); margin: var(--space-4) 0; }
.paper-mode-options label { display: flex; gap: var(--space-2); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-md); cursor: pointer; }
.paper-mode-options label.is-selected { border-color: var(--primary); background: var(--accent); }
.paper-mode-options span { display: grid; }
.paper-mode-options small { color: var(--color-text-muted); }
.paper-settings__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-2); }
.paper-settings__grid label { display: grid; grid-template-columns: 1fr auto; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--font-size-dense); }
.paper-settings__grid input { grid-column: 1 / -1; width: 100%; }
.paper-settings__grid small { color: var(--color-text-muted); }
.paper-ratios { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; gap: var(--space-2); align-items: end; margin-top: var(--space-3); padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-bg-subtle); }
.paper-ratios label { display: grid; grid-template-columns: 1fr 22px; gap: var(--space-1); align-items: center; }
.paper-ratios label span { grid-column: 1 / -1; color: var(--color-text-muted); font-size: var(--font-size-caption); }
.paper-ratios input { width: 100%; }
.paper-ratios b { font-weight: 500; }
.paper-ratios > strong { padding-bottom: var(--space-2); white-space: nowrap; color: var(--color-accent); }
.paper-ratios > strong.is-error { color: var(--destructive); }
.paper-targets__rows { display: grid; margin-top: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; }
.paper-targets__rows article { display: grid; grid-template-columns: 10px minmax(160px, 1fr) 52px 80px auto; gap: var(--space-2); align-items: center; min-height: 44px; padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border); }
.paper-targets__rows article:last-child { border-bottom: 0; }
.paper-targets__rows article > span:not(.paper-targets__status),
.paper-targets__rows small { text-align: right; color: var(--color-text-muted); }
.paper-targets__status { width: 9px; height: 28px; border-radius: 999px; }
.paper-targets__rows button { border: 0; background: transparent; color: var(--destructive); cursor: pointer; }
.paper-targets__empty { margin-top: var(--space-3); padding: var(--space-4); border: 1px dashed var(--input); border-radius: var(--radius-md); color: var(--color-text-muted); text-align: center; }
.paper-console__aside { position: sticky; top: calc(var(--shell-topbar-height, 64px) + 16px); display: grid; gap: var(--space-3); align-self: start; }
.paper-console__aside dl { margin: var(--space-3) 0; }
.paper-console__aside dl div { display: flex; justify-content: space-between; gap: var(--space-5); padding: var(--space-2) 0; border-bottom: 1px solid var(--border); }
.paper-console__aside dt { color: var(--color-text-muted); }
.paper-console__aside dd { margin: 0; font-weight: 750; }
.paper-console__mode-note { margin: var(--space-3) 0; padding: var(--space-2) var(--space-3); border-radius: var(--radius-md); background: var(--accent); color: var(--color-accent-active); font-size: var(--font-size-dense); }
.paper-console__exclude { display: flex; gap: var(--space-2); align-items: center; }
.paper-console__action { display: grid; gap: var(--space-2); border-color: var(--color-accent) !important; }
.paper-console__action span { color: var(--color-text-muted); font-size: var(--font-size-dense); line-height: 1.5; }
.paper-console__action button { width: 100%; margin-top: var(--space-2); }
.field-error { color: var(--destructive); }
@media (max-width: 1180px) {
  .student-filter-strip__roster { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .paper-console { grid-template-columns: 1fr; }
  .paper-console__aside { position: static; grid-template-columns: 1fr 1fr; }
}
</style>
