<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import GraphNodeInspector from '../components/knowledge-graph/GraphNodeInspector.vue'
import GraphScopeFilters from '../components/knowledge-graph/GraphScopeFilters.vue'
import GraphTextDirectory from '../components/knowledge-graph/GraphTextDirectory.vue'
import KnowledgeGraphCanvas, {
  type GraphDisplayMode,
} from '../components/knowledge-graph/KnowledgeGraphCanvas.vue'
import { summarizeGraph } from '../features/knowledge-graph/model'
import {
  parseGraphRouteScope,
  serializeGraphRouteScope,
} from '../features/knowledge-graph/route'
import { useKnowledgeGraphStore } from '../stores/knowledge-graph'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const graphStore = useKnowledgeGraphStore()

const students = ref<StudentSummary[]>([])
const studentsState = ref<'loading' | 'ready' | 'error'>('loading')
const activeQuery = ref<GraphQueryInput | null>(null)
const routeNotice = ref('')
const mode = ref<GraphDisplayMode>('graph')
let studentsController: AbortController | null = null
let routeInitialized = false

const selectedNode = computed(() => graphStore.graph?.nodes.find(
  (node) => node.knowledge_key === graphStore.selectedNodeKey,
) ?? null)
const summary = computed(() => summarizeGraph(graphStore.graph?.nodes ?? []))
const scopeLabel = computed(() => {
  const graph = graphStore.graph
  if (!graph) return '尚未应用查看范围'
  const exams = graph.exam_scope.sessions.map((session) => session.session_name).join('、') || '全部可用考试'
  if (graph.scope.mode === 'class') return `${exams} · ${graph.scope.class_id ?? '班级暂不可用'}`
  return `${exams} · ${graph.scope.student_ids.length} 名学生`
})

async function initializeFromRoute(): Promise<void> {
  if (routeInitialized || sessionStore.loadState !== 'ready' || studentsState.value === 'loading') return
  routeInitialized = true
  const parsed = parseGraphRouteScope(route.query, sessionStore.sessions, students.value)
  if (parsed.query === null) {
    routeNotice.value = parsed.notice || '请选择班级并应用范围'
    if (Object.keys(route.query).length > 0) {
      await router.replace({ name: 'knowledge-graph', query: parsed.canonical })
    }
    return
  }
  if (studentsState.value === 'error') {
    routeNotice.value = '班级和学生列表暂时不可用，请重新加载后选择范围'
    await router.replace({ name: 'knowledge-graph', query: {} })
    return
  }
  if (
    parsed.query.exam_scope.mode === 'current' &&
    sessionStore.selectedSessionId !== parsed.query.exam_scope.session_ids[0]
  ) {
    sessionStore.selectSession(parsed.query.exam_scope.session_ids[0])
  }
  activeQuery.value = parsed.query
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
  routeNotice.value = ''
  mode.value = 'graph'
  await router.replace({ name: 'knowledge-graph', query: serializeGraphRouteScope(query) })
  await graphStore.loadGraph(query)
}

function selectNode(knowledgeKey: string): void {
  const node = graphStore.graph?.nodes.find((candidate) => candidate.knowledge_key === knowledgeKey)
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
    graphStore.clearScope()
    activeQuery.value = null
    routeNotice.value = '当前考试已更改，请选择班级并应用范围'
    void router.replace({ name: 'knowledge-graph', query: {} })
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
        <p>只读知识标签证据</p>
        <h1 id="knowledge-graph-title" tabindex="-1">知识图谱</h1>
        <p>按已有题库标签查看得分率和原始证据，不推断知识点关系。</p>
      </div>
      <dl v-if="graphStore.graph" class="knowledge-graph-summary" aria-label="知识标签分档汇总">
        <div><dt>全部</dt><dd>{{ summary.total }}</dd></div>
        <div><dt>重点薄弱</dt><dd>{{ summary.weak }}</dd></div>
        <div><dt>需要讲评</dt><dd>{{ summary.review }}</dd></div>
        <div><dt>轻微欠缺</dt><dd>{{ summary.slight }}</dd></div>
        <div><dt>稳定</dt><dd>{{ summary.stable }}</dd></div>
      </dl>
    </header>

    <div v-if="studentsState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>班级和学生列表暂时无法读取</p>
      <button type="button" @click="retryStudents">重新加载筛选项</button>
    </div>
    <GraphScopeFilters
      :sessions="sessionStore.sessions"
      :current-session-id="sessionStore.selectedSessionId"
      :students="students"
      :model-value="activeQuery"
      :applying="graphStore.graphState === 'loading'"
      @apply="applyQuery"
    />

    <p v-if="routeNotice" class="knowledge-graph-scope-notice" role="status">
      {{ routeNotice }}
    </p>
    <p
      v-if="graphStore.graphState === 'loading' && graphStore.graph === null"
      class="knowledge-graph-state-copy"
      role="status"
    >
      正在读取知识图谱…
    </p>
    <div v-else-if="graphStore.graphState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>{{ graphStore.graphError }}</p>
      <button type="button" @click="retryGraph">重新加载知识图谱</button>
    </div>

    <template v-if="graphStore.graph">
      <div v-if="graphStore.graphState === 'stale-error'" class="knowledge-graph-stale" role="alert">
        <p>当前显示上次成功读取的知识图谱，最新内容暂时无法确认。</p>
        <button type="button" @click="retryGraph">重新加载知识图谱</button>
      </div>
      <section class="knowledge-graph-coverage" aria-label="标签覆盖情况">
        <strong>已覆盖 {{ graphStore.graph.coverage.covered_items }} / {{ graphStore.graph.coverage.total_items }} 份作答</strong>
        <span>未覆盖 {{ graphStore.graph.coverage.total_items - graphStore.graph.coverage.covered_items }} 份</span>
      </section>
      <ul v-if="graphStore.graph.warnings.length" class="knowledge-graph-warnings" aria-label="知识图谱说明">
        <li v-for="warning in graphStore.graph.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <p v-if="graphStore.graph.nodes.length === 0" class="knowledge-graph-scope-notice">
        当前范围没有可显示的知识标签
      </p>
      <template v-else>
        <div class="knowledge-graph-workspace">
          <KnowledgeGraphCanvas
            :nodes="graphStore.graph.nodes"
            :rows="graphStore.graph.rows"
            :mode="mode"
            :selected-key="graphStore.selectedNodeKey"
            :scope-label="scopeLabel"
            :coverage="graphStore.graph.coverage"
            @change-mode="mode = $event"
            @select-node="selectNode"
          />
          <GraphNodeInspector
            :node="selectedNode"
            :evidence="graphStore.evidence"
            :evidence-state="graphStore.evidenceState"
            :evidence-error="graphStore.evidenceError"
            @load-more-evidence="graphStore.loadMoreEvidence()"
            @retry-evidence="graphStore.retryEvidence()"
          />
        </div>
        <GraphTextDirectory
          :nodes="graphStore.graph.nodes"
          :selected-key="graphStore.selectedNodeKey"
          @select-node="selectNode"
        />
      </template>
    </template>
  </section>
</template>
