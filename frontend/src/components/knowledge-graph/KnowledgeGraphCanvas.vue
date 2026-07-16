<script setup lang="ts">
import { GraphChart, TreeChart } from 'echarts/charts'
import { AriaComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { GraphCoverage, GraphNode, GraphRow } from '../../api/graph'
import {
  buildEvidenceLaneModel,
  buildGroupingTree,
  summarizeGraph,
  type GroupingTreeNode,
  type MasteryBand,
} from '../../features/knowledge-graph/model'

use([GraphChart, TreeChart, AriaComponent, CanvasRenderer])

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
  coverage: GraphCoverage
  chartFactory?: (element: HTMLElement) => ChartLike
}>(), {
  chartFactory: undefined,
})

const emit = defineEmits<{
  selectNode: [knowledgeKey: string]
  changeMode: [mode: GraphDisplayMode]
}>()

const chartElement = ref<HTMLElement | null>(null)
const chartError = ref('')
let chart: ChartLike | null = null
let observer: ResizeObserver | null = null
let resizeFrame: number | null = null

const bandColourTokens: Record<MasteryBand, { fill: string; border: string }> = {
  weak: { fill: '--color-danger-subtle', border: '--color-danger' },
  review: { fill: '--color-warning-subtle', border: '--color-warning' },
  slight: { fill: '--color-info-subtle', border: '--color-info' },
  stable: { fill: '--color-success-subtle', border: '--color-success' },
}

const graphSummary = computed(() => summarizeGraph(props.nodes))
const selectedLabel = computed(() => props.nodes.find(
  (node) => node.knowledge_key === props.selectedKey,
)?.knowledge_label ?? '未选择')

function themeColour(token: string): string {
  const element = chartElement.value ?? document.documentElement
  return getComputedStyle(element).getPropertyValue(token).trim()
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function graphOption(): Record<string, unknown> {
  const laneNodes = buildEvidenceLaneModel(props.nodes)
  return {
    animation: !prefersReducedMotion(),
    aria: { enabled: true, description: '按四档得分率排列的知识标签图，知识标签之间没有关系连线。' },
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
        x: node.x,
        y: node.y,
        symbolSize: node.size,
        category: node.bandIndex,
        itemStyle: {
          color: themeColour(bandColourTokens[node.band].fill),
          borderColor: props.selectedKey === node.knowledgeKey
            ? themeColour('--color-accent')
            : themeColour(bandColourTokens[node.band].border),
          borderWidth: props.selectedKey === node.knowledgeKey ? 3 : 1,
        },
        label: {
          show: props.nodes.length <= 120 || props.selectedKey === node.knowledgeKey,
          color: themeColour('--color-text-primary'),
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
      ? { itemStyle: {
          color: themeColour('--color-accent-subtle'),
          borderColor: themeColour('--color-accent'),
          borderWidth: 3,
        } }
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
      expandAndCollapse: true,
      lineStyle: { color: themeColour('--color-border-strong'), width: 1, type: 'dotted' },
      itemStyle: {
        color: themeColour('--color-bg-surface'),
        borderColor: themeColour('--color-text-secondary'),
        borderWidth: 1,
      },
      label: { color: themeColour('--color-text-primary'), fontSize: 12, position: 'left' },
      leaves: { label: { position: 'right', color: themeColour('--color-text-primary') } },
      emphasis: { focus: 'descendant' },
    }],
  }
}

function renderChart(): void {
  if (!chart) return
  try {
    chart.setOption(props.mode === 'graph' ? graphOption() : treeOption(), true)
    chartError.value = ''
  } catch {
    chartError.value = '图表暂时无法显示，文字目录和节点详情仍可使用。'
  }
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
    resizeChart()
  })
}

function resizeChart(): boolean {
  try {
    chart?.resize()
    return true
  } catch {
    chartError.value = '图表暂时无法显示，文字目录和节点详情仍可使用。'
    return false
  }
}

function restoreView(): void {
  try {
    chart?.dispatchAction({ type: 'restore' })
  } catch {
    chartError.value = '图表暂时无法显示，文字目录和节点详情仍可使用。'
    return
  }
  renderChart()
}

function initializeChart(): void {
  const element = chartElement.value
  if (!element) return
  try {
    chart = props.chartFactory
      ? props.chartFactory(element)
      : init(element, undefined, { renderer: 'canvas' }) as unknown as ChartLike
    chart.on('click', handleChartClick)
    renderChart()
    if (observer === null) {
      observer = new ResizeObserver(queueResize)
      observer.observe(element)
    }
  } catch {
    try {
      observer?.disconnect()
    } catch {
      // Resize observation cleanup remains inside the chart boundary.
    }
    observer = null
    try {
      chart?.dispose()
    } catch {
      // A broken third-party chart must not escape this component boundary.
    }
    chart = null
    chartError.value = '图表暂时无法显示，文字目录和节点详情仍可使用。'
  }
}

function retryChart(): void {
  if (chart) {
    if (resizeChart()) renderChart()
  }
  else initializeChart()
}

watch(
  () => [props.nodes, props.rows, props.mode, props.selectedKey, props.scopeLabel, props.coverage],
  renderChart,
  { deep: true },
)

onMounted(() => {
  initializeChart()
})

onBeforeUnmount(() => {
  try {
    observer?.disconnect()
  } catch {
    // Resize cleanup must not prevent chart disposal.
  }
  observer = null
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = null
  try {
    chart?.off('click', handleChartClick)
  } catch {
    // Listener cleanup must not prevent chart disposal.
  }
  try {
    chart?.dispose()
  } catch {
    // Component teardown remains isolated from third-party chart failures.
  }
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

    <p class="knowledge-graph-state-copy" role="status">
      共 {{ graphSummary.total }} 个知识标签；重点薄弱 {{ graphSummary.weak }} 个，
      需要讲评 {{ graphSummary.review }} 个，轻微欠缺 {{ graphSummary.slight }} 个，
      稳定 {{ graphSummary.stable }} 个；覆盖 {{ coverage.covered_items }} / {{ coverage.total_items }} 份作答；
      当前选择：{{ selectedLabel }}。
    </p>

    <div v-if="chartError" class="knowledge-graph-inline-error" role="alert">
      <p>{{ chartError }}</p>
      <button type="button" data-testid="retry-knowledge-chart" @click="retryChart">重新显示图表</button>
    </div>

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
