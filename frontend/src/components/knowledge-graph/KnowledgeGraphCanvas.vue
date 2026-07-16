<script setup lang="ts">
import { init } from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { GraphNode, GraphRow } from '../../api/graph'
import {
  buildEvidenceLaneModel,
  buildGroupingTree,
  type GroupingTreeNode,
  type MasteryBand,
} from '../../features/knowledge-graph/model'

export type GraphDisplayMode = 'graph' | 'tree'

export interface ChartLike {
  setOption(option: unknown, notMerge?: boolean): void
  on(eventName: string, handler: (event: ChartClickEvent) => void): void
  off(eventName: string, handler: (event: ChartClickEvent) => void): void
  resize(): void
  dispose(): void
  dispatchAction(payload: unknown): void
}

interface ChartClickEvent {
  data?: { knowledgeKey?: unknown }
}

const props = withDefaults(defineProps<{
  nodes: GraphNode[]
  rows: GraphRow[]
  mode: GraphDisplayMode
  selectedKey: string | null
  scopeLabel: string
  chartFactory?: (element: HTMLElement) => ChartLike
}>(), {
  chartFactory: undefined,
})

const emit = defineEmits<{
  selectNode: [knowledgeKey: string]
  changeMode: [mode: GraphDisplayMode]
}>()

const chartElement = ref<HTMLElement | null>(null)
let chart: ChartLike | null = null
let observer: ResizeObserver | null = null
let resizeFrame: number | null = null

const bandColours: Record<MasteryBand, { fill: string; border: string }> = {
  weak: { fill: '#faecec', border: '#b04444' },
  review: { fill: '#fff4da', border: '#9a6718' },
  slight: { fill: '#edf3f7', border: '#496579' },
  stable: { fill: '#eaf5ef', border: '#2f7a55' },
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function graphOption(): Record<string, unknown> {
  const laneNodes = buildEvidenceLaneModel(props.nodes)
  return {
    animation: !prefersReducedMotion(),
    aria: { enabled: true, description: '按四档得分率排列的知识标签图，知识标签之间没有关系连线。' },
    tooltip: { show: false },
    series: [{
      type: 'graph',
      layout: 'none',
      roam: true,
      links: [],
      categories: [
        { name: '重点薄弱' },
        { name: '需要讲评' },
        { name: '轻微欠缺' },
        { name: '稳定' },
      ],
      data: laneNodes.map((node) => ({
        name: node.label,
        knowledgeKey: node.knowledgeKey,
        value: node.mastery,
        x: 72 + (node.orderInBand % 10) * 112,
        y: 64 + node.bandIndex * 148 + Math.floor(node.orderInBand / 10) * 44,
        symbolSize: node.size,
        category: node.bandIndex,
        itemStyle: {
          color: bandColours[node.band].fill,
          borderColor: props.selectedKey === node.knowledgeKey ? '#2563eb' : bandColours[node.band].border,
          borderWidth: props.selectedKey === node.knowledgeKey ? 3 : 1,
        },
        label: {
          show: props.nodes.length <= 120 || props.selectedKey === node.knowledgeKey,
          color: '#20242a',
          formatter: `${node.label}\n${node.percentLabel}`,
          fontSize: 12,
          lineHeight: 16,
        },
      })),
      emphasis: { focus: 'self' },
    }],
  }
}

function treeData(node: GroupingTreeNode): Record<string, unknown> {
  return {
    name: node.name,
    ...(node.knowledgeKey ? { knowledgeKey: node.knowledgeKey } : {}),
    children: node.children.map(treeData),
    ...(node.knowledgeKey === props.selectedKey
      ? { itemStyle: { color: '#eff6ff', borderColor: '#2563eb', borderWidth: 3 } }
      : {}),
  }
}

function treeOption(): Record<string, unknown> {
  return {
    animation: !prefersReducedMotion(),
    aria: {
      enabled: true,
      description: '筛选范围、学生与知识标签的临时分组树，不表示知识点父子、先修或相关关系。',
    },
    tooltip: { show: false },
    series: [{
      type: 'tree',
      data: [treeData(buildGroupingTree(props.rows, props.scopeLabel))],
      top: 24,
      left: 24,
      bottom: 24,
      right: 120,
      orient: 'LR',
      roam: true,
      symbol: 'circle',
      symbolSize: 12,
      expandAndCollapse: false,
      lineStyle: { color: '#b9c0c9', width: 1, type: 'dotted' },
      itemStyle: { color: '#ffffff', borderColor: '#5c6470', borderWidth: 1 },
      label: { color: '#20242a', fontSize: 12, position: 'left' },
      leaves: { label: { position: 'right', color: '#20242a' } },
      emphasis: { focus: 'descendant' },
    }],
  }
}

function renderChart(): void {
  chart?.setOption(props.mode === 'graph' ? graphOption() : treeOption(), true)
}

function handleChartClick(event: ChartClickEvent): void {
  const knowledgeKey = event.data?.knowledgeKey
  if (typeof knowledgeKey === 'string' && knowledgeKey.startsWith('knowledge_point:')) {
    emit('selectNode', knowledgeKey)
  }
}

function queueResize(): void {
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = null
    chart?.resize()
  })
}

function restoreView(): void {
  chart?.dispatchAction({ type: 'restore' })
  renderChart()
}

watch(
  () => [props.nodes, props.rows, props.mode, props.selectedKey, props.scopeLabel],
  renderChart,
  { deep: true },
)

onMounted(() => {
  const element = chartElement.value
  if (!element) return
  chart = props.chartFactory
    ? props.chartFactory(element)
    : init(element, undefined, { renderer: 'canvas' }) as unknown as ChartLike
  chart.on('click', handleChartClick)
  renderChart()
  observer = new ResizeObserver(queueResize)
  observer.observe(element)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = null
  chart?.off('click', handleChartClick)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <section class="knowledge-graph-canvas-panel" aria-labelledby="knowledge-graph-canvas-title">
    <header class="knowledge-graph-canvas-toolbar">
      <div>
        <h2 id="knowledge-graph-canvas-title">知识标签分布</h2>
        <p>{{ scopeLabel }}</p>
      </div>
      <div class="knowledge-graph-canvas-actions" aria-label="图谱显示方式">
        <button
          type="button"
          :class="{ 'is-active': mode === 'graph' }"
          :aria-pressed="mode === 'graph'"
          @click="emit('changeMode', 'graph')"
        >
          掌握度分区
        </button>
        <button
          type="button"
          :class="{ 'is-active': mode === 'tree' }"
          :aria-pressed="mode === 'tree'"
          @click="emit('changeMode', 'tree')"
        >
          学生分组树
        </button>
        <button type="button" @click="restoreView">恢复视图</button>
      </div>
    </header>

    <ul v-if="mode === 'graph'" class="knowledge-graph-lane-legend" aria-label="掌握度分档说明">
      <li data-band="weak"><strong>重点薄弱</strong><span>低于 60%</span></li>
      <li data-band="review"><strong>需要讲评</strong><span>60%–74.9%</span></li>
      <li data-band="slight"><strong>轻微欠缺</strong><span>75%–89.9%</span></li>
      <li data-band="stable"><strong>稳定</strong><span>90% 及以上</span></li>
    </ul>
    <p v-else class="knowledge-graph-grouping-note" role="note">
      虚线仅表示筛选范围、学生与知识标签的分组归属；不是知识点父子、先修或相关关系。
    </p>

    <div
      ref="chartElement"
      class="knowledge-graph-canvas"
      role="img"
      :aria-label="mode === 'graph'
        ? '知识标签掌握度分区图。下方文字目录提供全部节点信息。'
        : '学生与知识标签临时分组树。下方文字目录提供全部节点信息。'"
    />
  </section>
</template>
