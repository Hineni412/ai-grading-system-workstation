<script setup lang="ts">
import { GraphChart } from 'echarts/charts'
import { AriaComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type {
  GraphV2Edge,
  GraphV2Node,
  GraphV2RelationType,
} from '../../api/graph-v2'
import type { GraphCoverage } from '../../api/graph'
import {
  buildGraphV2DisplayNodes,
  connectedNodeKeys,
  findGraphV2Path,
  relationTypeLabel,
  relationVerb,
} from '../../features/knowledge-graph/v2-model'

use([GraphChart, AriaComponent, TooltipComponent, CanvasRenderer])

export interface ChartLike {
  setOption(option: unknown, notMerge?: boolean): void
  on(eventName: string, handler: (event: ChartClickEvent) => void): void
  off(eventName: string, handler: (event: ChartClickEvent) => void): void
  resize(): void
  dispose(): void
  dispatchAction(payload: unknown): void
}

interface ChartClickEvent {
  data?: { stableKey?: unknown }
}

type GraphViewMode = 'overview' | 'focus' | 'path'

const props = withDefaults(defineProps<{
  nodes: GraphV2Node[]
  edges: GraphV2Edge[]
  selectedKey: string | null
  scopeLabel: string
  coverage: GraphCoverage
  masteryMode: 'v1' | 'v2'
  chartFactory?: (element: HTMLElement) => ChartLike
}>(), {
  chartFactory: undefined,
  masteryMode: 'v1',
})

const emit = defineEmits<{ selectNode: [stableKey: string] }>()
const chartElement = ref<HTMLElement | null>(null)
const chartError = ref('')
const viewMode = ref<GraphViewMode>('overview')
const pathTargetKey = ref('')
const enabledTypes = ref<GraphV2RelationType[]>(['parent', 'prerequisite', 'related'])
let chart: ChartLike | null = null
let observer: ResizeObserver | null = null
let resizeFrame: number | null = null
const chartUnavailableMessage = '关系图暂时无法显示，文字目录和节点详情仍可使用。'

const stateTokens = {
  missing: { fill: '--color-bg-subtle', border: '--color-border-strong', symbol: 'emptyRect' },
  weak: { fill: '--color-danger-subtle', border: '--color-danger', symbol: 'diamond' },
  review: { fill: '--color-warning-subtle', border: '--color-warning', symbol: 'rect' },
  slight: { fill: '--color-info-subtle', border: '--color-info', symbol: 'roundRect' },
  stable: { fill: '--color-success-subtle', border: '--color-success', symbol: 'circle' },
} as const

const relationTokens: Record<GraphV2RelationType, {
  colour: string
  type: 'solid' | 'dashed' | 'dotted'
}> = {
  parent: { colour: '--color-info', type: 'solid' },
  prerequisite: { colour: '--color-warning', type: 'dashed' },
  related: { colour: '--color-text-secondary', type: 'dotted' },
}

const typeSet = computed(() => new Set(enabledTypes.value))
const filteredEdges = computed(() => props.edges.filter(
  (edge) => typeSet.value.has(edge.relation_type),
))
const path = computed(() => (
  viewMode.value === 'path' && props.selectedKey && pathTargetKey.value
    ? findGraphV2Path(props.edges, props.selectedKey, pathTargetKey.value, typeSet.value)
    : null
))
const visibleNodeKeys = computed(() => {
  if (viewMode.value === 'focus' && props.selectedKey) {
    return connectedNodeKeys(props.edges, props.selectedKey, typeSet.value)
  }
  if (viewMode.value === 'path' && props.selectedKey && pathTargetKey.value) {
    return new Set(path.value?.nodeKeys ?? [props.selectedKey, pathTargetKey.value])
  }
  return new Set(props.nodes.map((node) => node.stable_key))
})
const visibleNodes = computed(() => props.nodes.filter(
  (node) => visibleNodeKeys.value.has(node.stable_key),
))
const visibleEdges = computed(() => filteredEdges.value.filter((edge) => (
  visibleNodeKeys.value.has(edge.source_key) &&
  visibleNodeKeys.value.has(edge.target_key) &&
  (viewMode.value !== 'path' || path.value?.edgeIds.includes(edge.relation_id))
)))
const selectedLabel = computed(() => props.nodes.find(
  (node) => node.stable_key === props.selectedKey,
)?.display_name ?? '未选择')
const pathDescription = computed(() => {
  if (viewMode.value !== 'path') return ''
  if (!props.selectedKey) return '请先选择路径起点。'
  if (!pathTargetKey.value) return '请选择路径终点。'
  if (!path.value) return '按当前关系筛选未找到有向路径，可调整关系类型或终点。'
  const names = new Map(props.nodes.map((node) => [node.stable_key, node.display_name]))
  return path.value.nodeKeys.map((key) => names.get(key) ?? key).join(' → ')
})

function themeColour(token: string): string {
  const element = chartElement.value ?? document.documentElement
  return getComputedStyle(element).getPropertyValue(token).trim()
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function graphOption(): Record<string, unknown> {
  const displayNodes = buildGraphV2DisplayNodes(visibleNodes.value, props.masteryMode)
  const names = new Map(props.nodes.map((node) => [node.stable_key, node.display_name]))
  const pathEdgeIds = new Set(path.value?.edgeIds ?? [])
  return {
    animation: !prefersReducedMotion() && visibleNodes.value.length <= 200,
    aria: {
      enabled: true,
      description: `已确认知识关系图，共 ${visibleNodes.value.length} 个知识点、${visibleEdges.value.length} 条关系。关系方向和类型另有文字图例。`,
    },
    tooltip: {
      trigger: 'item',
      formatter: (params: { dataType?: string; data?: Record<string, unknown> }) => {
        const data = params.data ?? {}
        if (params.dataType === 'edge') {
          const type = data.relationType as GraphV2RelationType
          return `${names.get(String(data.source)) ?? data.source} ${relationVerb(type)} ${names.get(String(data.target)) ?? data.target}<br>${String(data.rationale ?? '')}`
        }
        return `${String(data.name ?? '')}<br>${String(data.stateLabel ?? '')} · ${String(data.masteryLabel ?? '')}`
      },
    },
    series: [{
      type: 'graph',
      layout: 'none',
      roam: true,
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: 8,
      data: displayNodes.map((node) => {
        const tokens = stateTokens[node.state]
        return {
          id: node.stableKey,
          name: node.label,
          stableKey: node.stableKey,
          stateLabel: node.stateLabel,
          masteryLabel: node.masteryLabel,
          value: node.mastery,
          x: node.x,
          y: node.y,
          symbol: tokens.symbol,
          symbolSize: node.size,
          category: node.stateIndex,
          itemStyle: {
            color: themeColour(tokens.fill),
            borderColor: themeColour(tokens.border),
            borderWidth: props.selectedKey === node.stableKey ? 3 : 1,
          },
          label: {
            show: visibleNodes.value.length <= 100,
            color: themeColour('--color-text-primary'),
            formatter: `${node.label}\n${node.masteryLabel} · ${node.stateLabel}`,
            fontSize: 12,
            lineHeight: 16,
          },
        }
      }),
      links: visibleEdges.value.map((edge) => {
        const tokens = relationTokens[edge.relation_type]
        return {
          id: edge.relation_id,
          source: edge.source_key,
          target: edge.target_key,
          relationType: edge.relation_type,
          rationale: edge.rationale,
          symbol: edge.relation_type === 'related' ? ['none', 'none'] : ['none', 'arrow'],
          lineStyle: {
            color: themeColour(tokens.colour),
            type: tokens.type,
            width: pathEdgeIds.has(edge.relation_id) ? 4 : 2,
            opacity: viewMode.value === 'path' && pathEdgeIds.has(edge.relation_id) ? 1 : 0.72,
            curveness: edge.relation_type === 'related' ? 0.12 : 0.04,
          },
        }
      }),
      emphasis: {
        focus: 'adjacency',
        itemStyle: {
          borderColor: themeColour('--color-accent'),
          borderWidth: 3,
        },
        lineStyle: { width: 4, opacity: 1 },
      },
    }],
  }
}

function renderChart(): void {
  if (!chart) return
  try {
    chart.setOption(graphOption(), true)
    chartError.value = ''
    updateChartSelection()
  } catch {
    chartError.value = chartUnavailableMessage
  }
}

function updateChartSelection(): void {
  if (!chart) return
  try {
    chart.dispatchAction({ type: 'downplay', seriesIndex: 0 })
    if (props.selectedKey) {
      chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataId: props.selectedKey })
    }
  } catch {
    chartError.value = chartUnavailableMessage
  }
}

function handleChartClick(event: ChartClickEvent): void {
  const stableKey = event.data?.stableKey
  if (typeof stableKey === 'string') emit('selectNode', stableKey)
}

function queueResize(): void {
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = null
    try {
      chart?.resize()
    } catch {
      chartError.value = chartUnavailableMessage
    }
  })
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
    observer = new ResizeObserver(queueResize)
    observer.observe(element)
  } catch {
    try { observer?.disconnect() } catch { /* Keep failures inside this component. */ }
    observer = null
    try { chart?.dispose() } catch { /* Keep failures inside this component. */ }
    chart = null
    chartError.value = chartUnavailableMessage
  }
}

function retryChart(): void {
  if (!chart) {
    initializeChart()
    return
  }
  try {
    chart.resize()
    renderChart()
  } catch {
    chartError.value = chartUnavailableMessage
  }
}

function restoreView(): void {
  try {
    chart?.dispatchAction({ type: 'restore' })
    renderChart()
  } catch {
    chartError.value = chartUnavailableMessage
  }
}

function toggleRelation(type: GraphV2RelationType): void {
  enabledTypes.value = enabledTypes.value.includes(type)
    ? enabledTypes.value.filter((value) => value !== type)
    : [...enabledTypes.value, type]
}

watch(
  () => [props.nodes, props.edges, props.scopeLabel, props.coverage, props.masteryMode],
  renderChart,
  { deep: true },
)
watch([viewMode, pathTargetKey, enabledTypes], renderChart, { deep: true })
watch(() => props.selectedKey, () => {
  if (viewMode.value === 'path' && pathTargetKey.value === props.selectedKey) pathTargetKey.value = ''
  renderChart()
})

onMounted(initializeChart)
onBeforeUnmount(() => {
  try { observer?.disconnect() } catch { /* Keep failures inside this component. */ }
  observer = null
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = null
  try { chart?.off('click', handleChartClick) } catch { /* Keep cleanup local. */ }
  try { chart?.dispose() } catch { /* Keep cleanup local. */ }
  chart = null
})
</script>

<template>
  <section class="knowledge-graph-canvas-panel" aria-labelledby="knowledge-graph-canvas-title">
    <header class="knowledge-graph-canvas-toolbar">
      <div>
        <h2 id="knowledge-graph-canvas-title">已确认知识关系</h2>
        <p>{{ scopeLabel }}</p>
      </div>
      <div class="knowledge-graph-canvas-actions" aria-label="图谱查看方式">
        <button type="button" :class="{ 'is-active': viewMode === 'overview' }" :aria-pressed="viewMode === 'overview'" @click="viewMode = 'overview'">全部关系</button>
        <button type="button" :class="{ 'is-active': viewMode === 'focus' }" :aria-pressed="viewMode === 'focus'" :disabled="!selectedKey" @click="viewMode = 'focus'">聚焦当前节点</button>
        <button type="button" :class="{ 'is-active': viewMode === 'path' }" :aria-pressed="viewMode === 'path'" :disabled="!selectedKey" @click="viewMode = 'path'">查看路径</button>
        <button type="button" @click="restoreView">恢复视图</button>
      </div>
    </header>

    <div class="knowledge-graph-v2-controls">
      <fieldset>
        <legend>显示关系</legend>
        <label v-for="type in (['parent', 'prerequisite', 'related'] as GraphV2RelationType[])" :key="type" :data-relation="type">
          <input type="checkbox" :checked="enabledTypes.includes(type)" @change="toggleRelation(type)">
          <span aria-hidden="true" />
          <strong>{{ relationTypeLabel(type) }}</strong>
        </label>
      </fieldset>
      <label v-if="viewMode === 'path'" class="knowledge-graph-v2-path-target">
        <span>路径终点</span>
        <select v-model="pathTargetKey">
          <option value="">请选择知识点</option>
          <option v-for="node in nodes.filter((item) => item.stable_key !== selectedKey)" :key="node.stable_key" :value="node.stable_key">
            {{ node.display_name }}
          </option>
        </select>
      </label>
    </div>

    <ul class="knowledge-graph-v2-state-legend" aria-label="掌握证据状态">
      <li data-state="missing"><span aria-hidden="true">□</span><strong>当前无证据</strong></li>
      <li data-state="weak"><span aria-hidden="true">◆</span><strong>重点薄弱</strong></li>
      <li data-state="review"><span aria-hidden="true">■</span><strong>需要讲评</strong></li>
      <li data-state="slight"><span aria-hidden="true">▰</span><strong>轻微欠缺</strong></li>
      <li data-state="stable"><span aria-hidden="true">●</span><strong>稳定</strong></li>
    </ul>

    <p v-if="viewMode === 'path'" class="knowledge-graph-v2-path-copy" role="status">{{ pathDescription }}</p>
    <p class="knowledge-graph-state-copy" role="status">
      当前显示 {{ visibleNodes.length }} / {{ nodes.length }} 个知识点、
      {{ visibleEdges.length }} / {{ edges.length }} 条已确认关系；覆盖
      {{ coverage.covered_items }} / {{ coverage.total_items }} 份作答；当前选择：{{ selectedLabel }}。
    </p>

    <div v-if="chartError" class="knowledge-graph-inline-error" role="alert">
      <p>{{ chartError }}</p>
      <button type="button" data-testid="retry-knowledge-v2-chart" @click="retryChart">重新显示关系图</button>
    </div>
    <div
      ref="chartElement"
      class="knowledge-graph-canvas"
      role="img"
      aria-label="已确认知识关系图。关系类型、方向和掌握状态可在上方文字图例中读取；下方文字目录可使用键盘浏览全部节点。"
    />
  </section>
</template>
