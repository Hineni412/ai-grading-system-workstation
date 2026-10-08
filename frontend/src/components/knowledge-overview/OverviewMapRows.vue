<script setup lang="ts">
import { computed } from 'vue'
import type { TrainingOverviewNode } from '../../api/training'
import { shortNodeName } from './model'
import { heatLevel, weakRate } from './metrics'
import OverviewTierBar from './OverviewTierBar.vue'
const props = defineProps<{ nodes: TrainingOverviewNode[]; catalog: TrainingOverviewNode[]; selectedKey: string; activeKeys: Set<string>; hasActive: boolean }>()
const emit = defineEmits<{ select: [node: TrainingOverviewNode, event: MouseEvent]; hover: [key: string] }>()
// Type mode (typed release active): one "题型" column, types without exam
// evidence stay hidden; chapters and sections aggregate as before.
const rows = computed(() => {
  const groups = new Map<string, TrainingOverviewNode[]>()
  for (const node of props.nodes) {
    const key = node.section_key || node.display_name.split(/[|｜]/).slice(0, -1).join('｜')
    groups.set(key, [...(groups.get(key) ?? []), node])
  }
  return [...groups].map(([key, nodes]) => ({ key, nodes,
    label: props.catalog.find(n => n.knowledge_key === key)?.display_name.split(/[|｜]/).pop()
      || nodes[0]?.display_name.split(/[|｜]/).slice(-2, -1)[0] || '未提供小节' }))
    .filter(row => row.nodes.some(node => node.kind !== 'type' || node.evidence_student_count > 0))
})
function columns(row: { nodes: TrainingOverviewNode[] }) {
  if (row.nodes.some(node => node.target_kind === 'type' || node.kind === 'type')) return [{ kind: 'type', label: '题型 · 考什么' }]
  if (row.nodes.some(node => node.target_kind === 'knowledge')) return [{ kind: 'topic', label: '知识点 · 学什么' }]
  return [{ kind: 'topic', label: '知识点 · 学什么' }, { kind: 'skill', label: '技能 · 会做什么' }]
}
function cellNodes(row: { nodes: TrainingOverviewNode[] }, kind: string) {
  return row.nodes.filter(node => node.kind === kind && (node.kind !== 'type' || node.evidence_student_count > 0))
}
function label(node: TrainingOverviewNode) {
  const rate = weakRate(node)
  const kindLabel = node.kind === 'skill' ? '技能' : node.kind === 'type' ? '题型' : '知识点'
  return `${node.display_name}，${kindLabel}，${rate === null ? '无证据'
    : `明显薄弱 ${node.distribution.weak} 人 / 有证据 ${node.evidence_student_count} 人，占 ${Math.round(rate * 100)}%`}`
}
</script>
<template>
  <div v-for="row in rows" :key="row.key" class="mastery-map-row">
    <h3>{{ row.label }}</h3>
    <div class="mastery-map-columns">
      <div v-for="column in columns(row)" :key="column.kind" class="mastery-map-column" :data-kind="column.kind">
        <h4>{{ column.label }}</h4>
        <div class="mastery-map-cells">
          <button v-for="node in cellNodes(row, column.kind)" :key="node.knowledge_key" type="button"
            class="mastery-map-node" :class="[`is-${column.kind}`, `heat-${heatLevel(node)}`, { 'is-selected': selectedKey === node.knowledge_key,
              'is-related': hasActive && activeKeys.has(node.knowledge_key), 'is-dimmed': hasActive && !activeKeys.has(node.knowledge_key) }]"
            :data-knowledge="node.knowledge_key" :title="label(node)" :aria-label="label(node)" :aria-pressed="selectedKey === node.knowledge_key"
            @click="emit('select', node, $event)" @mouseenter="emit('hover', node.knowledge_key)" @mouseleave="emit('hover', '')">
            <span class="mastery-map-node-name">{{ shortNodeName(node) }}</span>
            <b v-if="node.distribution.weak > 0" class="mastery-map-badge">{{ node.distribution.weak }}</b>
            <OverviewTierBar v-if="node.evidence_student_count > 0" :distribution="node.distribution" />
          </button>
          <span v-if="!cellNodes(row, column.kind).length" class="mastery-map-empty">—</span>
        </div>
      </div>
    </div>
  </div>
</template>
