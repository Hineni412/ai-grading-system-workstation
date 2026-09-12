<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import AppButton from '../components/design-system/AppButton.vue'
import GraphScopeFilters from '../components/knowledge-graph/GraphScopeFilters.vue'
import KnowledgeStructureBrowser from '../components/knowledge-training/KnowledgeStructureBrowser.vue'
import KnowledgeTrainingTabs from '../components/knowledge-training/KnowledgeTrainingTabs.vue'
import { summarizeGraph } from '../features/knowledge-graph/model'
import { loadEvidenceScope, saveEvidenceScope } from '../features/evidence-scope/session'
import {
  parseGraphRouteScope,
  serializeGraphRouteScope,
} from '../features/knowledge-graph/route'
import { useKnowledgeGraphStore } from '../stores/knowledge-graph'
import { useSessionStore } from '../stores/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import '../styles/knowledge-graph.css'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const graphStore = useKnowledgeGraphStore()

const students = ref<StudentSummary[]>([])
const studentsState = ref<'loading' | 'ready' | 'error'>('loading')
const activeQuery = ref<GraphQueryInput | null>(null)
const routeNotice = ref('')
let studentsController: AbortController | null = null
let routeInitialized = false

const summary = computed(() => summarizeGraph(
  graphStore.graph?.nodes ?? [],
  graphStore.graph?.edges ?? [],
))
const visibleWarnings = computed(() => [...new Set(graphStore.graph?.warnings ?? [])])

async function initializeFromRoute(): Promise<void> {
  if (routeInitialized || sessionStore.loadState !== 'ready' || studentsState.value !== 'ready') return
  routeInitialized = true
  const parsed = parseGraphRouteScope(route.query, sessionStore.sessions, students.value)
  if (parsed.query === null) {
    if (sessionStore.selectedSessionId === null) {
      routeNotice.value = '请先在顶部选择当前考试'
      return
    }
    const savedQuery = loadEvidenceScope()
    const knownSessionIds = new Set(sessionStore.sessions.map((item) => item.id))
    const compatibleSavedQuery = savedQuery?.exam_scope.mode === 'cross_exam'
      || savedQuery?.exam_scope.session_ids.every((id) => knownSessionIds.has(id))
      ? savedQuery
      : null
    const defaultQuery: GraphQueryInput = compatibleSavedQuery ?? {
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
    }
    activeQuery.value = defaultQuery
    if (
      defaultQuery.exam_scope.mode === 'current'
      && sessionStore.selectedSessionId !== defaultQuery.exam_scope.session_ids[0]
    ) {
      sessionStore.selectSession(defaultQuery.exam_scope.session_ids[0])
    }
    saveEvidenceScope(defaultQuery)
    routeNotice.value = parsed.notice
    await router.replace({ name: 'knowledge-graph', query: serializeGraphRouteScope(defaultQuery) })
    await graphStore.loadGraph(defaultQuery)
    return
  }
  if (
    parsed.query.exam_scope.mode === 'current' &&
    sessionStore.selectedSessionId !== parsed.query.exam_scope.session_ids[0]
  ) {
    sessionStore.selectSession(parsed.query.exam_scope.session_ids[0])
  }
  activeQuery.value = parsed.query
  saveEvidenceScope(parsed.query)
  routeNotice.value = parsed.notice
  await router.replace({ name: 'knowledge-graph', query: parsed.canonical })
  await graphStore.loadGraph(parsed.query)
}

async function loadStudentOptions(): Promise<void> {
  studentsController?.abort()
  const controller = new AbortController()
  studentsController = controller
  studentsState.value = 'loading'
  try {
    students.value = await fetchStudents(controller.signal)
    studentsState.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    students.value = []
    studentsState.value = 'error'
  } finally {
    if (studentsController === controller) studentsController = null
  }
  await initializeFromRoute()
}

async function applyQuery(query: GraphQueryInput): Promise<void> {
  activeQuery.value = query
  saveEvidenceScope(query)
  routeNotice.value = ''
  await router.replace({ name: 'knowledge-graph', query: serializeGraphRouteScope(query) })
  await graphStore.loadGraph(query)
}

function selectNode(knowledgeKey: string): void {
  const node = graphStore.graph?.nodes.find((candidate) => candidate.stable_key === knowledgeKey)
  if (node) void graphStore.selectNode(node)
}

function retryGraph(): void {
  void graphStore.retryGraph()
}

function retryStudents(): void {
  void loadStudentOptions()
}

watch(
  () => sessionStore.loadState,
  () => { void initializeFromRoute() },
)

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    const applied = activeQuery.value
    if (
      !routeInitialized ||
      applied?.exam_scope.mode !== 'current' ||
      applied.exam_scope.session_ids[0] === sessionId
    ) return
    if (sessionId === null) return
    const next: GraphQueryInput = {
      ...applied,
      exam_scope: { mode: 'current', session_ids: [sessionId] },
    }
    activeQuery.value = next
    saveEvidenceScope(next)
    routeNotice.value = '已按新的当前考试更新范围'
    void router.replace({ name: 'knowledge-graph', query: serializeGraphRouteScope(next) })
    void graphStore.loadGraph(next)
  },
)

onMounted(() => { void loadStudentOptions() })
onBeforeUnmount(() => {
  studentsController?.abort()
  graphStore.clearScope()
})
</script>

<template>
  <section class="knowledge-graph-view" aria-labelledby="knowledge-graph-title">
    <header class="knowledge-graph-page-heading">
      <div>
        <p>知识点热力图 · 自身证据</p>
        <h1 id="knowledge-graph-title" tabindex="-1">知识结构</h1>
        <p>按教材章、节、知识点查看掌握状况；每个知识点独立计算，无证据显示证据不足。</p>
      </div>
      <div class="knowledge-graph-heading-scope">
        <dl v-if="graphStore.graph" class="knowledge-graph-summary" aria-label="知识图谱汇总">
          <div><dt>知识点</dt><dd>{{ summary.total }}</dd></div>
          <div><dt>证据不足</dt><dd>{{ summary.missing }}</dd></div>
          <div><dt>重点薄弱</dt><dd>{{ summary.weak }}</dd></div>
          <div><dt>需要讲评</dt><dd>{{ summary.review }}</dd></div>
        </dl>
      </div>
    </header>

    <KnowledgeTrainingTabs />

    <GraphScopeFilters
      class="knowledge-graph-scope-filters"
      :sessions="sessionStore.sessions"
      :current-session-id="sessionStore.selectedSessionId"
      :students="students"
      :model-value="activeQuery"
      :applying="graphStore.graphState === 'loading'"
      :score-profiles="graphStore.graph?.scope.student_score_profiles ?? {}"
      :curriculum-volume-id="curriculumScope.selectedVolumeId"
      evidence-from="chapter"
      @apply="applyQuery"
    />

    <div v-if="studentsState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>班级和学生列表暂时无法读取</p>
      <AppButton type="button" @click="retryStudents">重新加载筛选项</AppButton>
    </div>
    <p v-if="routeNotice" class="knowledge-graph-scope-notice" role="status">
      {{ routeNotice }}
    </p>
    <div
      v-if="graphStore.graphState === 'loading' && graphStore.graph === null"
      class="knowledge-graph-loading-skeleton"
      role="status"
      aria-busy="true"
      aria-label="正在读取知识结构"
    >
      正在按章节整理知识结构…
    </div>
    <div v-else-if="graphStore.graphState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>{{ graphStore.graphError }}</p>
      <AppButton type="button" @click="retryGraph">重新加载知识图谱</AppButton>
    </div>

    <template v-if="graphStore.graph">
      <div v-if="graphStore.graphState === 'stale-error'" class="knowledge-graph-stale" role="alert">
        <p>当前显示上次成功读取的知识图谱，最新内容暂时无法确认。</p>
        <AppButton type="button" @click="retryGraph">重新加载知识图谱</AppButton>
      </div>
      <details
        v-if="visibleWarnings.length || graphStore.graph.missing.length"
        class="knowledge-graph-secondary-panel"
      >
        <summary>
          数据说明（{{ visibleWarnings.length + graphStore.graph.missing.length }}）
        </summary>
        <ul v-if="visibleWarnings.length" class="knowledge-graph-warnings" aria-label="知识图谱说明">
          <li v-for="warning in visibleWarnings" :key="warning">{{ warning }}</li>
        </ul>
        <ul v-if="graphStore.graph.missing.length" class="knowledge-graph-missing-list" aria-label="未纳入图谱的项目">
          <li v-for="(item, index) in graphStore.graph.missing" :key="`${String(item.stable_key ?? item.label ?? 'missing')}:${index}`">
            {{ String(item.label ?? item.stable_key ?? '未识别知识项') }} 未纳入当前知识图谱。
          </li>
        </ul>
      </details>
      <p v-if="graphStore.graph.nodes.length === 0" class="knowledge-graph-scope-notice">
        当前范围没有可显示的已治理知识点。可调整考试或学生范围；未治理标签不用于推断掌握情况。
      </p>
      <template v-if="graphStore.graph.nodes.length > 0">
        <KnowledgeStructureBrowser
          :nodes="graphStore.graph.nodes"
          :edges="graphStore.graph.edges"
          :selected-key="graphStore.selectedNodeKey ?? ''"
          @select="selectNode"
        />
      </template>
    </template>
  </section>
</template>

<style scoped>
.knowledge-graph-scope-filters { display: block; margin-top: var(--space-2); }
</style>
