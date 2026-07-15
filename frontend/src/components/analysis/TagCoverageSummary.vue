<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import {
  fetchGraphEvidence,
  type GraphEvidenceResponse,
  type GraphNode,
  type GraphRowsResponse,
} from '../../api/graph'
import type { ResourceState } from '../../stores/workbench'

const props = defineProps<{
  sessionId: number | null
  className: string | null
  graph: GraphRowsResponse | null
  state: ResourceState
  updatedAt: string | null
}>()

defineEmits<{
  retry: []
}>()

const selectedNodeKey = ref<string | null>(null)
const evidence = ref<GraphEvidenceResponse | null>(null)
const evidenceState = ref<'idle' | 'loading' | 'ready' | 'empty' | 'stale-error' | 'error'>('idle')
let controller: AbortController | null = null
let generation = 0

watch(
  () => [props.sessionId, props.className, props.graph] as const,
  () => {
    controller?.abort()
    controller = null
    generation += 1
    selectedNodeKey.value = null
    evidence.value = null
    evidenceState.value = 'idle'
  },
)

onBeforeUnmount(() => controller?.abort())

async function selectNode(node: GraphNode): Promise<void> {
  if (props.sessionId === null || props.className === null) return
  const keepPrevious = selectedNodeKey.value === node.knowledge_key && evidence.value !== null
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  const requestGeneration = ++generation
  selectedNodeKey.value = node.knowledge_key
  if (!keepPrevious) evidence.value = null
  evidenceState.value = 'loading'
  try {
    const loaded = await fetchGraphEvidence(
      props.sessionId,
      props.className,
      node.knowledge_key,
      requestController.signal,
    )
    if (
      requestGeneration !== generation ||
      loaded.knowledge_key !== node.knowledge_key ||
      loaded.scope.mode !== 'class' ||
      loaded.scope.class_id !== props.className ||
      loaded.exam_scope.mode !== 'current' ||
      loaded.exam_scope.session_ids.length !== 1 ||
      loaded.exam_scope.session_ids[0] !== props.sessionId
    ) return
    evidence.value = loaded
    evidenceState.value = loaded.items.length === 0 ? 'empty' : 'ready'
  } catch {
    if (requestGeneration !== generation || requestController.signal.aborted) return
    evidenceState.value = keepPrevious ? 'stale-error' : 'error'
  } finally {
    if (controller === requestController) controller = null
  }
}

function retryEvidence(): void {
  const node = props.graph?.nodes.find((item) => item.knowledge_key === selectedNodeKey.value)
  if (node) void selectNode(node)
}

function displayTime(value: string | null): string {
  return value?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'
}
</script>

<template>
  <section class="workbench-section tag-coverage" aria-labelledby="tag-coverage-title">
    <header class="workbench-section__heading">
      <div>
        <p class="workbench-eyebrow">已有题库标签证据</p>
        <h2 id="tag-coverage-title">知识标签覆盖</h2>
      </div>
    </header>
    <p v-if="className === null" class="workbench-empty-copy">选择班级后查看知识标签覆盖</p>
    <p
      v-else-if="state === 'loading' && (graph === null || graph.nodes.length === 0)"
      class="workbench-state-copy"
      role="status"
    >
      正在读取标签覆盖…
    </p>
    <div v-else-if="state === 'error'" class="workbench-inline-error" role="alert">
      <p>标签覆盖暂时无法读取</p>
      <button type="button" class="workbench-secondary-button" @click="$emit('retry')">
        重新加载标签覆盖
      </button>
    </div>
    <p v-else-if="graph === null || graph.nodes.length === 0" class="workbench-empty-copy">当前班级没有知识标签记录</p>
    <template v-else>
      <p v-if="state === 'loading'" class="workbench-state-copy" role="status">正在更新标签覆盖…</p>
      <div v-if="state === 'stale-error'" class="workbench-stale" role="alert">
        <span>数据可能不是最新 · 上次更新 {{ displayTime(updatedAt) }}</span>
        <button type="button" class="workbench-link-button" @click="$emit('retry')">重新加载标签覆盖</button>
      </div>
      <p class="tag-coverage__summary">
        已覆盖 {{ graph.coverage.covered_items }} / {{ graph.coverage.total_items }} 份
      </p>
      <ul v-if="graph.warnings.length" class="tag-coverage__warnings" aria-label="标签覆盖说明">
        <li v-for="warning in graph.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <ul class="tag-node-list">
        <li v-for="node in graph.nodes" :key="node.knowledge_key">
          <button
            type="button"
            class="tag-node"
            :aria-pressed="node.knowledge_key === selectedNodeKey"
            @click="selectNode(node)"
          >
            <strong>{{ node.knowledge_label }}</strong>
            <span>{{ node.item_count }} 份作答 · {{ node.deduction_count }} 条失分记录</span>
          </button>
        </li>
      </ul>
      <p v-if="evidenceState === 'loading'" class="workbench-state-copy" role="status">
        {{ evidence === null ? '正在读取标签证据…' : '正在更新标签证据…' }}
      </p>
      <div v-else-if="evidenceState === 'error'" class="workbench-inline-error" role="alert">
        <p>标签证据暂时无法读取</p>
        <button type="button" class="workbench-secondary-button" @click="retryEvidence">重新加载标签证据</button>
      </div>
      <div v-else-if="evidenceState === 'stale-error'" class="workbench-stale" role="alert">
        <span>标签证据可能不是最新</span>
        <button type="button" class="workbench-link-button" @click="retryEvidence">重新加载标签证据</button>
      </div>
      <p v-else-if="evidenceState === 'empty'" class="workbench-empty-copy">当前标签没有可显示的证据</p>
      <ol v-if="evidence" class="tag-evidence-list" aria-label="标签证据">
        <li v-for="item in evidence.items" :key="`${item.student_id}:${item.question_id}`">
          <strong>{{ item.student_name }} · {{ item.question_id }}</strong>
          <span>{{ item.score_awarded }} / {{ item.full_score }} 分</span>
          <span>{{ item.actionable_reasons.join('；') || '未记录扣分原因' }}</span>
        </li>
      </ol>
    </template>
  </section>
</template>
