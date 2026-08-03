<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { GraphEdge, GraphNode, GraphRelationType } from '../../api/graph'
import {
  buildGraphDisplayNodes,
  relationTypeLabel,
} from '../../features/knowledge-graph/model'

const props = withDefaults(defineProps<{
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedKey: string | null
  pageSize?: number
}>(), {
  pageSize: 50,
})

const emit = defineEmits<{ selectNode: [stableKey: string] }>()
const search = ref('')
const relationType = ref<GraphRelationType | 'all'>('all')
const page = ref(1)

const relationCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const edge of props.edges) {
    counts.set(edge.source_key, (counts.get(edge.source_key) ?? 0) + 1)
    counts.set(edge.target_key, (counts.get(edge.target_key) ?? 0) + 1)
  }
  return counts
})
const filteredNodes = computed(() => {
  const term = search.value.trim().toLocaleLowerCase('zh-CN')
  const allowed = relationType.value === 'all'
    ? null
    : new Set(props.edges.filter(
        (edge) => edge.relation_type === relationType.value,
      ).flatMap((edge) => [edge.source_key, edge.target_key]))
  return buildGraphDisplayNodes(props.nodes).filter((node) => (
    (!term || node.label.toLocaleLowerCase('zh-CN').includes(term) ||
      node.stableKey.toLocaleLowerCase('en-US').includes(term)) &&
    (allowed === null || allowed.has(node.stableKey))
  ))
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredNodes.value.length / props.pageSize)))
const visibleNodes = computed(() => {
  const start = (page.value - 1) * props.pageSize
  return filteredNodes.value.slice(start, start + props.pageSize)
})

watch([search, relationType, () => props.nodes, () => props.edges], () => {
  page.value = 1
}, { deep: true })
watch(totalPages, (value) => { page.value = Math.min(page.value, value) })

function onDirectoryKeydown(event: KeyboardEvent): void {
  const target = event.target
  if (!(target instanceof HTMLButtonElement) || !target.matches('[data-testid="graph-directory-item"]')) return
  const list = event.currentTarget
  if (!(list instanceof HTMLElement)) return
  const buttons = [...list.querySelectorAll<HTMLButtonElement>('[data-testid="graph-directory-item"]')]
  const index = buttons.indexOf(target)
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const direction = event.key === 'ArrowDown' ? 1 : -1
    buttons[(index + direction + buttons.length) % buttons.length]?.focus()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const key = target.dataset.stableKey
    if (key) emit('selectNode', key)
  }
}
</script>

<template>
  <section class="knowledge-graph-directory" aria-labelledby="knowledge-graph-directory-title">
    <header>
      <div>
        <h2 id="knowledge-graph-directory-title">知识点文字目录</h2>
        <p>大图的完整文字替代。使用上下方向键浏览，按回车查看关系和证据。</p>
      </div>
      <div class="knowledge-graph-directory-tools">
        <label>
          <span>按关系筛选</span>
          <select v-model="relationType">
            <option value="all">全部关系状态</option>
            <option value="parent">{{ relationTypeLabel('parent') }}</option>
            <option value="prerequisite">{{ relationTypeLabel('prerequisite') }}</option>
            <option value="related">{{ relationTypeLabel('related') }}</option>
          </select>
        </label>
        <label>
          <span>搜索知识点</span>
          <input v-model="search" type="search" autocomplete="off" placeholder="名称或稳定编号">
        </label>
      </div>
    </header>

    <p v-if="filteredNodes.length === 0" class="knowledge-graph-empty-copy">
      当前筛选没有匹配知识点。可清空搜索或改选关系类型。
    </p>
    <ul v-else class="knowledge-graph-directory-list" @keydown="onDirectoryKeydown">
      <li v-for="node in visibleNodes" :key="node.stableKey">
        <button
          type="button"
          data-testid="graph-directory-item"
          :data-stable-key="node.stableKey"
          :class="{ 'is-selected': selectedKey === node.stableKey }"
          :aria-current="selectedKey === node.stableKey ? 'true' : undefined"
          :aria-label="`${node.label}，${node.stateLabel}，掌握证据 ${node.masteryLabel}，${node.evidenceCount} 条证据，${relationCounts.get(node.stableKey) ?? 0} 条已确认关系`"
          @click="emit('selectNode', node.stableKey)"
        >
          <span class="knowledge-graph-directory-list__name">{{ node.label }}</span>
          <span>{{ node.masteryLabel }} · {{ node.stateLabel }}</span>
          <span>{{ node.evidenceCount }} 条证据 · {{ relationCounts.get(node.stableKey) ?? 0 }} 条关系</span>
        </button>
      </li>
    </ul>

    <footer v-if="totalPages > 1" class="knowledge-graph-directory-pagination">
      <button type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页，共 {{ filteredNodes.length }} 个知识点</span>
      <button type="button" :disabled="page >= totalPages" @click="page += 1">下一页</button>
    </footer>
  </section>
</template>
