<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { GraphStudentScopeInput } from '../api/graph-query'
import { fetchStudents, type StudentSummary } from '../api/students'
import { trainingApi, type TrainingOverview } from '../api/training'
import AppButton from '../components/design-system/AppButton.vue'
import OverviewChapters, {
  type ChapterFilter,
} from '../components/knowledge-overview/OverviewChapters.vue'
import OverviewFocusColumns from '../components/knowledge-overview/OverviewFocusColumns.vue'
import OverviewStudentTable from '../components/knowledge-overview/OverviewStudentTable.vue'
import { formatPercent, shortNodeName } from '../components/knowledge-overview/model'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import {
  loadEvidenceScope,
  saveEvidenceScope,
  semesterEvidenceQuery,
} from '../features/evidence-scope/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import '../styles/knowledge-overview.css'

const curriculumScope = useCurriculumScopeStore()

const students = ref<StudentSummary[]>([])
const overview = ref<TrainingOverview | null>(null)
const loadState = ref<'idle' | 'loading' | 'ready' | 'error' | 'stale-error'>('idle')
const errorMessage = ref('')
const scopeSelection = ref<'all' | string>('all')
const chapterFilter = ref<ChapterFilter>({ type: 'all' })
let controller: AbortController | null = null
let studentsController: AbortController | null = null
let initialized = false

const classOptions = computed(() => [
  ...new Set(
    students.value
      .map(student => student.class_name?.trim() ?? '')
      .filter(name => name !== ''),
  ),
].sort((left, right) => left.localeCompare(right, 'zh')))

function classLabel(name: string): string {
  return /^\d+$/.test(name) ? `${name} 班` : name
}

const currentScope = computed<GraphStudentScopeInput>(() => (
  scopeSelection.value === 'all'
    ? { mode: 'all' }
    : { mode: 'class', class_id: scopeSelection.value, class_ids: [scopeSelection.value] }
))

const chapters = computed(() => overview.value?.nodes ?? [])
const sectionNames = computed(() => {
  const names: Record<string, string> = {}
  for (const node of overview.value?.nodes ?? []) {
    if (node.kind === 'section') names[node.knowledge_key] = shortNodeName(node)
  }
  return names
})
const studentsById = computed(() => Object.fromEntries(
  (overview.value?.students ?? []).map(student => [student.student_id, student]),
))
const focusTopics = computed(() => filterByChapter(
  chapters.value.filter(node => node.kind === 'topic'),
))
const focusSkills = computed(() => filterByChapter(
  chapters.value.filter(node => node.kind === 'skill'),
))

function filterByChapter(nodes: TrainingOverview['nodes']): TrainingOverview['nodes'] {
  const filter = chapterFilter.value
  if (filter.type === 'chapter') {
    return nodes.filter(node => node.chapter_key === filter.key)
  }
  if (filter.type === 'section') {
    return nodes.filter(node => node.section_key === filter.key)
  }
  return nodes
}

async function requestOverview(scope: GraphStudentScopeInput): Promise<void> {
  const volumeId = curriculumScope.selectedVolumeId
  if (!volumeId) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  const hadResult = overview.value !== null
  loadState.value = hadResult ? loadState.value : 'loading'
  try {
    const query = semesterEvidenceQuery(scope, volumeId)
    const result = await trainingApi.overview(
      {
        scope: { student_ids: [], ...query.scope },
        exam_scope: { session_ids: [], ...query.exam_scope },
      },
      current.signal,
    )
    overview.value = result
    loadState.value = 'ready'
    errorMessage.value = ''
  } catch {
    if (current.signal.aborted) return
    loadState.value = hadResult ? 'stale-error' : 'error'
    errorMessage.value = '学情总览暂时无法读取，请稍后重试。'
  } finally {
    if (controller === current) controller = null
  }
}

function applyScope(selection: 'all' | string): void {
  scopeSelection.value = selection
  chapterFilter.value = { type: 'all' }
  const query = semesterEvidenceQuery(currentScope.value, curriculumScope.selectedVolumeId)
  saveEvidenceScope(query)
  void requestOverview(currentScope.value)
}

function retry(): void {
  void requestOverview(currentScope.value)
}

function initialize(): void {
  if (initialized || curriculumScope.loadState !== 'ready') return
  initialized = true
  const saved = loadEvidenceScope()?.scope
  if (saved?.mode === 'class' && saved.class_ids?.length === 1 && saved.class_ids[0]) {
    scopeSelection.value = saved.class_ids[0]
  } else if (saved?.mode === 'class' && saved.class_id) {
    scopeSelection.value = saved.class_id
  } else {
    scopeSelection.value = 'all'
  }
  saveEvidenceScope(semesterEvidenceQuery(currentScope.value, curriculumScope.selectedVolumeId))
  void requestOverview(currentScope.value)
}

async function loadStudents(): Promise<void> {
  studentsController?.abort()
  const current = new AbortController()
  studentsController = current
  try {
    students.value = await fetchStudents(current.signal)
  } catch {
    if (current.signal.aborted) return
    students.value = []
  } finally {
    if (studentsController === current) studentsController = null
  }
}

watch(
  () => curriculumScope.loadState,
  () => initialize(),
)

watch(
  () => curriculumScope.selectedVolumeId,
  () => {
    if (!initialized) return
    chapterFilter.value = { type: 'all' }
    void requestOverview(currentScope.value)
  },
)

onMounted(() => {
  void loadStudents()
  initialize()
})
onBeforeUnmount(() => {
  controller?.abort()
  studentsController?.abort()
})
</script>

<template>
  <section class="knowledge-overview-view" aria-labelledby="knowledge-overview-title">
    <header class="knowledge-overview-heading">
      <p>学期掌握度 · 知识点与技能并列</p>
      <h1 id="knowledge-overview-title" tabindex="-1">学情总览</h1>
      <p>按当前教学学期汇总知识点、技能与学生的掌握分布；证据不足不计为 0。</p>
    </header>

    <KnowledgeTrainingTabs />

    <div class="knowledge-overview-scope-bar" role="group" aria-label="学生范围">
      <span>学生范围</span>
      <button
        type="button"
        :class="{ 'is-active': scopeSelection === 'all' }"
        :aria-pressed="scopeSelection === 'all'"
        @click="applyScope('all')"
      >
        全部学生
      </button>
      <button
        v-for="className in classOptions"
        :key="className"
        type="button"
        :class="{ 'is-active': scopeSelection === className }"
        :aria-pressed="scopeSelection === className"
        @click="applyScope(className)"
      >
        {{ classLabel(className) }}
      </button>
    </div>

    <p v-if="!curriculumScope.selectedVolumeId" class="knowledge-overview-notice" role="status">
      请先在顶部选择教学学期
    </p>

    <div
      v-else-if="loadState === 'loading' && overview === null"
      class="knowledge-overview-skeleton"
      role="status"
      aria-busy="true"
      aria-label="正在读取学情总览"
    >
      正在汇总本学期掌握度…
    </div>

    <div
      v-else-if="loadState === 'error'"
      class="knowledge-overview-error"
      role="alert"
    >
      <p>{{ errorMessage }}</p>
      <AppButton type="button" @click="retry">重新加载</AppButton>
    </div>

    <template v-if="overview">
      <div
        v-if="loadState === 'stale-error'"
        class="knowledge-overview-error"
        role="alert"
      >
        <p>当前显示上次成功读取的结果，最新内容暂时无法确认。</p>
        <AppButton type="button" @click="retry">重新加载</AppButton>
      </div>

      <dl class="overview-summary-strip" aria-label="学期汇总">
        <div>
          <dt>学生</dt>
          <dd>
            {{ overview.summary.evidence_student_count }}
            <small>有证据 / 共 {{ overview.summary.student_count }}</small>
          </dd>
        </div>
        <div>
          <dt>平均考试得分率</dt>
          <dd>
            {{ formatPercent(overview.summary.exam_score_rate) }}
            <small v-if="overview.summary.exam_score_rate !== null">
              {{ overview.summary.exam_student_count }} 人参与
            </small>
          </dd>
        </div>
        <div>
          <dt>明显薄弱知识点</dt>
          <dd>{{ overview.summary.weak_topic_count }}</dd>
        </div>
        <div>
          <dt>明显薄弱技能</dt>
          <dd>{{ overview.summary.weak_skill_count }}</dd>
        </div>
      </dl>

      <OverviewChapters
        :nodes="overview.nodes"
        :filter="chapterFilter"
        @select="chapterFilter = $event"
      />

      <OverviewFocusColumns
        :topic-nodes="focusTopics"
        :skill-nodes="focusSkills"
        :section-names="sectionNames"
        :students-by-id="studentsById"
      />

      <OverviewStudentTable :students="overview.students" />
    </template>
  </section>
</template>
