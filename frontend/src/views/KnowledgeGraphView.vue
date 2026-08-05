<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { GraphQueryInput } from '../api/graph'
import { fetchStudents, type StudentSummary } from '../api/students'
import GraphRelationReviewShortcut from '../components/knowledge-graph/GraphRelationReviewShortcut.vue'
import GraphScopeFilters from '../components/knowledge-graph/GraphScopeFilters.vue'
import GraphNodeInspector from '../components/knowledge-graph/GraphNodeInspector.vue'
import GraphTextDirectory from '../components/knowledge-graph/GraphTextDirectory.vue'
import KnowledgeGraphCanvas from '../components/knowledge-graph/KnowledgeGraphCanvas.vue'
import { summarizeGraph } from '../features/knowledge-graph/model'
import { loadEvidenceScope, saveEvidenceScope } from '../features/evidence-scope/session'
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
const mobilePane = ref<'directory' | 'graph' | 'detail'>('directory')
let studentsController: AbortController | null = null
let routeInitialized = false

const selectedNode = computed(() => graphStore.graph?.nodes.find(
  (node) => node.stable_key === graphStore.selectedNodeKey,
) ?? null)
const summary = computed(() => summarizeGraph(
  graphStore.graph?.nodes ?? [],
  graphStore.graph?.edges ?? [],
))
const scopeLabel = computed(() => {
  const graph = graphStore.graph
  if (!graph) return '尚未应用查看范围'
  const exams = graph.exam_scope.sessions.map((session) => session.session_name).join('、') || '全部可用考试'
  if (graph.scope.mode === 'class') return `${exams} · ${(graph.scope.class_ids ?? []).join('、') || graph.scope.class_id || '班级暂不可用'}`
  return `${exams} · ${graph.scope.student_ids.length} 名学生`
})
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

function selectDirectoryNode(knowledgeKey: string): void {
  selectNode(knowledgeKey)
  mobilePane.value = 'graph'
}

function selectCanvasNode(knowledgeKey: string): void {
  selectNode(knowledgeKey)
  mobilePane.value = 'detail'
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
        <p>已确认关系 · 可追溯证据</p>
        <h1 id="knowledge-graph-title" tabindex="-1">知识图谱</h1>
        <p>查看经过规则与 AI 治理的父子、先修和相关关系；教师只处理少量异常。</p>
      </div>
      <div class="knowledge-graph-heading-scope">
        <GraphScopeFilters
          :sessions="sessionStore.sessions"
          :current-session-id="sessionStore.selectedSessionId"
          :students="students"
          :model-value="activeQuery"
          :applying="graphStore.graphState === 'loading'"
          :score-profiles="graphStore.graph?.scope.student_score_profiles ?? {}"
          @apply="applyQuery"
        />
        <dl v-if="graphStore.graph" class="knowledge-graph-summary" aria-label="知识图谱汇总">
          <div><dt>知识点</dt><dd>{{ summary.total }}</dd></div>
          <div><dt>已确认关系</dt><dd>{{ summary.relationTotal }}</dd></div>
          <div><dt>当前无证据</dt><dd>{{ summary.missing }}</dd></div>
          <div><dt>重点薄弱</dt><dd>{{ summary.weak }}</dd></div>
          <div><dt>需要讲评</dt><dd>{{ summary.review }}</dd></div>
        </dl>
      </div>
    </header>

    <div v-if="studentsState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>班级和学生列表暂时无法读取</p>
      <button type="button" @click="retryStudents">重新加载筛选项</button>
    </div>
    <details class="knowledge-graph-secondary-panel">
      <summary>关系异常与审核</summary>
      <GraphRelationReviewShortcut />
    </details>

    <p v-if="routeNotice" class="knowledge-graph-scope-notice" role="status">
      {{ routeNotice }}
    </p>
    <div
      v-if="graphStore.graphState === 'loading' && graphStore.graph === null"
      class="knowledge-graph-workspace knowledge-graph-loading-skeleton"
      role="status"
      aria-busy="true"
      aria-label="正在读取知识图谱"
    >
      <section class="knowledge-graph-canvas-panel">
        <header class="knowledge-graph-canvas-toolbar">
          <div>
            <h2>已确认知识关系</h2>
            <p>正在准备当前范围的关系与证据…</p>
          </div>
        </header>
        <div class="knowledge-graph-canvas" aria-hidden="true" />
      </section>
      <aside class="knowledge-graph-inspector">
        <header>
          <p class="knowledge-graph-inspector__eyebrow">关系、事实与证据</p>
          <h2>知识点详情</h2>
        </header>
        <p class="knowledge-graph-state-copy">正在准备节点事实和题目证据…</p>
      </aside>
    </div>
    <div v-else-if="graphStore.graphState === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>{{ graphStore.graphError }}</p>
      <button type="button" @click="retryGraph">重新加载知识图谱</button>
    </div>

    <template v-if="graphStore.graph">
      <div v-if="graphStore.graphState === 'stale-error'" class="knowledge-graph-stale" role="alert">
        <p>当前显示上次成功读取的知识图谱，最新内容暂时无法确认。</p>
        <button type="button" @click="retryGraph">重新加载知识图谱</button>
      </div>
      <section class="knowledge-graph-coverage" aria-label="证据覆盖情况">
        <strong>已覆盖 {{ graphStore.graph.coverage.covered_items }} / {{ graphStore.graph.coverage.total_items }} 份作答</strong>
        <span>未覆盖 {{ graphStore.graph.coverage.total_items - graphStore.graph.coverage.covered_items }} 份</span>
        <span>已应用当前筛选范围</span>
      </section>
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
        当前范围没有可显示的已治理知识点。可调整考试或学生范围；未治理标签不会被伪装成关系节点。
      </p>
      <template v-if="graphStore.graph.nodes.length > 0">
        <nav class="knowledge-graph-mobile-nav" aria-label="知识图谱查看步骤">
          <button type="button" :class="{ 'is-active': mobilePane === 'directory' }" @click="mobilePane = 'directory'">1 目录</button>
          <button type="button" :class="{ 'is-active': mobilePane === 'graph' }" @click="mobilePane = 'graph'">2 图谱</button>
          <button type="button" :class="{ 'is-active': mobilePane === 'detail' }" :disabled="!selectedNode" @click="mobilePane = 'detail'">3 详情</button>
        </nav>
        <div class="knowledge-graph-workspace">
          <GraphTextDirectory
            :class="{ 'is-mobile-active': mobilePane === 'directory' }"
            :nodes="graphStore.graph.nodes"
            :edges="graphStore.graph.edges"
            :selected-key="graphStore.selectedNodeKey"
            @select-node="selectDirectoryNode"
          />
          <KnowledgeGraphCanvas
            :class="{ 'is-mobile-active': mobilePane === 'graph' }"
            :nodes="graphStore.graph.nodes"
            :edges="graphStore.graph.edges"
            :selected-key="graphStore.selectedNodeKey"
            :scope-label="scopeLabel"
            :coverage="graphStore.graph.coverage"
            @select-node="selectCanvasNode"
          />
          <GraphNodeInspector
            :class="{ 'is-mobile-active': mobilePane === 'detail' }"
            :node="selectedNode"
            :nodes="graphStore.graph.nodes"
            :edges="graphStore.graph.edges"
            :current-standard="graphStore.graph.current_standard"
            :evidence="graphStore.evidence"
            :evidence-state="graphStore.evidenceState"
            :evidence-error="graphStore.evidenceError"
            @select-node="selectNode"
            @load-more-evidence="graphStore.loadMoreEvidence()"
            @retry-evidence="graphStore.retryEvidence()"
          />
        </div>
      </template>
    </template>
  </section>
</template>
