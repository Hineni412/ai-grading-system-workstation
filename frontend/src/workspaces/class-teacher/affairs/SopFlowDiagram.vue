<script setup lang="ts">
import { computed } from 'vue'

import type { AffairStep } from '../api/r1'

const props = defineProps<{
  steps: AffairStep[]
  selectedId: string | null
}>()
const emit = defineEmits<{ select: [stepInstanceId: string] }>()

// —— 动态布局：由 depends_on / activation 推导 DAG，纵向主轴 + 分支横排 + 汇合 ——
const NODE_W = 190
const NODE_H = 64
const DIAMOND_W = 210
const DIAMOND_H = 96
const ROW_H = 108
const COL_W = 230
const MARGIN = 48

interface LayoutNode {
  step: AffairStep
  key: string
  x: number
  y: number
  w: number
  h: number
  shape: 'rect' | 'diamond'
  lane: string | null
}

interface LayoutEdge {
  d: string
  label: string
  lx: number
  ly: number
  state: string
}

function stepKey(step: AffairStep): string {
  return step.key || step.step_instance_id
}

function isDecision(step: AffairStep): boolean {
  return Boolean(step.decision_key && step.decision_options?.length)
}

function nodeKind(step: AffairStep): { label: string; tone: string } {
  if (step.safety_required) return { label: '安全必做', tone: 'safety' }
  if (isDecision(step)) return { label: '判断分支', tone: 'branch' }
  if (step.activation) return { label: '分支步骤', tone: 'branch' }
  if (step.communication_templates?.length) return { label: '沟通', tone: 'communicate' }
  return { label: '步骤', tone: 'plain' }
}

function wrapTitle(title: string, per = 12): string[] {
  const lines: string[] = []
  for (let index = 0; index < title.length; index += per) lines.push(title.slice(index, index + per))
  return lines.slice(0, 2)
}

const layout = computed(() => {
  const steps = props.steps
  const byKey = new Map(steps.map((step) => [stepKey(step), step]))

  // 深度 = 最长依赖链（带成环保护）
  const depthMemo = new Map<string, number>()
  const depthOf = (step: AffairStep, seen: Set<string>): number => {
    const key = stepKey(step)
    const memo = depthMemo.get(key)
    if (memo !== undefined) return memo
    if (seen.has(key)) return 0
    seen.add(key)
    const deps = (step.depends_on ?? []).map((dep) => byKey.get(dep)).filter((dep): dep is AffairStep => Boolean(dep))
    const depth = deps.length ? 1 + Math.max(...deps.map((dep) => depthOf(dep, new Set(seen)))) : 0
    depthMemo.set(key, depth)
    return depth
  }

  // 泳道：activation 节点自带泳道；单一依赖且在泳道内则沿泳道延伸；多依赖汇合回主轴
  const laneMemo = new Map<string, string | null>()
  const laneOf = (step: AffairStep, seen: Set<string>): string | null => {
    const key = stepKey(step)
    if (laneMemo.has(key)) return laneMemo.get(key) ?? null
    if (seen.has(key)) return null
    seen.add(key)
    let lane: string | null = null
    if (step.activation) {
      lane = `${step.activation.decision_key}::${(step.activation.allowed_values ?? []).join('|')}`
    } else {
      const deps = (step.depends_on ?? []).map((dep) => byKey.get(dep)).filter((dep): dep is AffairStep => Boolean(dep))
      if (deps.length === 1) {
        const depLane = laneOf(deps[0]!, seen)
        // 判断节点的直接后继不算泳道延伸（除非它自己带 activation）
        if (depLane && !isDecision(deps[0]!)) lane = depLane
      }
    }
    laneMemo.set(key, lane)
    return lane
  }

  for (const step of steps) depthOf(step, new Set())
  for (const step of steps) laneOf(step, new Set())

  // 泳道列：按所属判断节点的选项顺序，左右交替排布
  const lanes = [...new Set(steps.map((step) => laneMemo.get(stepKey(step)) ?? null).filter((lane): lane is string => Boolean(lane)))]
  const laneOrder = (lane: string): number => {
    const [decisionKey, values] = lane.split('::')
    const decision = steps.find((step) => step.decision_key === decisionKey)
    const firstValue = (values ?? '').split('|')[0] ?? ''
    const optionIndex = decision?.decision_options?.findIndex((option) => option.value === firstValue) ?? -1
    return optionIndex >= 0 ? optionIndex : 99
  }
  lanes.sort((a, b) => laneOrder(a) - laneOrder(b))
  const laneOffset = new Map<string, number>()
  lanes.forEach((lane, index) => {
    const rank = Math.floor(index / 2) + 1
    laneOffset.set(lane, index % 2 === 0 ? -rank : rank)
  })

  // 行 = 深度；主轴同行多节点时水平铺开
  const rows = new Map<number, AffairStep[]>()
  for (const step of steps) {
    const depth = depthMemo.get(stepKey(step)) ?? 0
    rows.set(depth, [...(rows.get(depth) ?? []), step])
  }

  const nodes: LayoutNode[] = []
  for (const [depth, rowSteps] of rows) {
    const mainSteps = rowSteps.filter((step) => !(laneMemo.get(stepKey(step)) ?? null))
    const laneSteps = rowSteps.filter((step) => Boolean(laneMemo.get(stepKey(step)) ?? null))
    mainSteps.forEach((step, index) => {
      const offset = index - (mainSteps.length - 1) / 2
      nodes.push({
        step, key: stepKey(step), lane: null,
        x: offset * COL_W, y: depth * ROW_H,
        w: isDecision(step) ? DIAMOND_W : NODE_W,
        h: isDecision(step) ? DIAMOND_H : NODE_H,
        shape: isDecision(step) ? 'diamond' : 'rect',
      })
    })
    for (const step of laneSteps) {
      const lane = laneMemo.get(stepKey(step)) ?? null
      nodes.push({
        step, key: stepKey(step), lane,
        x: (laneOffset.get(lane ?? '') ?? 0) * COL_W, y: depth * ROW_H,
        w: isDecision(step) ? DIAMOND_W : NODE_W,
        h: isDecision(step) ? DIAMOND_H : NODE_H,
        shape: isDecision(step) ? 'diamond' : 'rect',
      })
    }
  }

  const nodeByKey = new Map(nodes.map((node) => [node.key, node]))
  const edges: LayoutEdge[] = []
  for (const node of nodes) {
    for (const depKey of node.step.depends_on ?? []) {
      const from = nodeByKey.get(depKey)
      if (!from) continue
      const x1 = from.x
      const y1 = from.y + from.h / 2
      const x2 = node.x
      const y2 = node.y - node.h / 2
      const midY = y1 + Math.max(18, (y2 - y1) / 2)
      const d = x1 === x2
        ? `M${x1},${y1} V${y2}`
        : `M${x1},${y1} V${midY} H${x2} V${y2}`
      let label = ''
      if (node.step.activation && from.step.decision_key === node.step.activation.decision_key) {
        const options = from.step.decision_options ?? []
        label = (node.step.activation.allowed_values ?? [])
          .map((value) => options.find((option) => option.value === value)?.label ?? value)
          .join(' / ')
      }
      edges.push({ d, label, lx: (x1 + x2) / 2, ly: midY - 6, state: node.step.state })
    }
  }

  const minX = Math.min(...nodes.map((node) => node.x - node.w / 2), 0) - MARGIN
  const maxX = Math.max(...nodes.map((node) => node.x + node.w / 2), 0) + MARGIN
  const maxY = Math.max(...nodes.map((node) => node.y + node.h / 2), 0) + MARGIN
  return { nodes, edges, viewBox: `${minX} ${-MARGIN} ${maxX - minX} ${maxY + MARGIN}` }
})

function diamondPoints(node: LayoutNode): string {
  return `${node.x - node.w / 2},${node.y} ${node.x},${node.y - node.h / 2} ${node.x + node.w / 2},${node.y} ${node.x},${node.y + node.h / 2}`
}

function stateClass(step: AffairStep): string {
  if (step.state === 'completed' || step.state === 'waived') return 'is-completed'
  if (step.state === 'superseded') return 'is-pruned'
  if (step.state === 'blocked') return 'is-locked'
  if (step.state === 'in_progress') return 'is-in_progress'
  return 'is-pending'
}
</script>

<template>
  <div class="sop-flow">
    <svg v-if="layout.nodes.length" :viewBox="layout.viewBox" class="sop-flow__svg" role="img" aria-label="处理流程图">
      <defs>
        <marker id="sop-flow-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L9,4.5 L0,9 z" class="arrowhead" />
        </marker>
      </defs>

      <g v-for="edge in layout.edges" :key="edge.d" class="edge" :class="`edge--${edge.state}`">
        <path :d="edge.d" class="edge__line" marker-end="url(#sop-flow-arrow)" />
        <text v-if="edge.label" :x="edge.lx" :y="edge.ly" class="edge__label" text-anchor="middle">{{ edge.label }}</text>
      </g>

      <g
        v-for="node in layout.nodes"
        :key="node.key"
        class="flow-node"
        :class="[stateClass(node.step), { selected: selectedId === node.step.step_instance_id }]"
        @click="emit('select', node.step.step_instance_id)"
      >
        <rect
          v-if="selectedId === node.step.step_instance_id"
          :x="node.x - node.w / 2 - 6"
          :y="node.y - node.h / 2 - 6"
          :width="node.w + 12"
          :height="node.h + 12"
          rx="12"
          class="node-ring"
        />
        <rect
          v-if="node.shape === 'rect'"
          :x="node.x - node.w / 2"
          :y="node.y - node.h / 2"
          :width="node.w"
          :height="node.h"
          rx="8"
          class="node-box"
        />
        <polygon v-else :points="diamondPoints(node)" class="node-box" />
        <text :x="node.x" :y="node.y - 14" class="node-kind" :class="`node-kind--${nodeKind(node.step).tone}`">{{ nodeKind(node.step).label }}</text>
        <text
          v-for="(line, lineIndex) in wrapTitle(node.step.title)"
          :key="lineIndex"
          :x="node.x"
          :y="node.y + 5 + lineIndex * 15"
          class="node-title"
        >{{ line }}</text>
        <g v-if="node.step.state === 'completed' || node.step.state === 'waived'" class="node-check">
          <circle :cx="node.x + node.w / 2" :cy="node.y - node.h / 2" r="9" />
          <text :x="node.x + node.w / 2" :y="node.y - node.h / 2 + 4">✓</text>
        </g>
        <text v-if="node.step.origin === 'ai_flow_revision'" :x="node.x + node.w / 2 - 6" :y="node.y + node.h / 2 - 5" class="node-ai">AI</text>
      </g>
    </svg>
    <p v-else class="sop-flow__empty">这个事务还没有可展示的步骤。</p>
    <p class="sop-flow__legend">点击节点查看/处理该步骤；菱形为判断分支，连线文字为分支去向。</p>
  </div>
</template>

<style scoped>
.sop-flow{padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card);overflow:auto}
.sop-flow__svg{display:block;width:100%;min-width:480px;margin:0 auto;height:auto}
.sop-flow__legend{margin:8px 0 0;font-size:12px;color:var(--muted-foreground);text-align:center}
.sop-flow__empty{margin:24px 0;text-align:center;color:var(--muted-foreground)}
.edge__line{fill:none;stroke:#9a94c4;stroke-width:1.5}
.edge--superseded{opacity:.35}
.edge--superseded .edge__line{stroke-dasharray:5 4}
.edge--completed .edge__line,.edge--waived .edge__line{stroke:#16a34a}
.arrowhead{fill:#9a94c4}
.edge__label{font-size:11px;fill:#6b6794;paint-order:stroke;stroke:var(--card);stroke-width:4}
.flow-node{cursor:pointer}
.node-box{fill:#ececf8;stroke:#9a94c4;stroke-width:1.5}
.flow-node.is-in_progress .node-box{stroke:#4f46e5;stroke-width:2.5}
.flow-node.is-completed .node-box{stroke:#16a34a;fill:#eaf6ee}
.flow-node.is-locked{opacity:.5}
.flow-node.is-pruned{opacity:.45}
.flow-node.is-pruned .node-title{text-decoration:line-through}
.node-ring{fill:none;stroke:var(--ring,#4f46e5);stroke-width:2;stroke-dasharray:5 4}
.node-kind{font-size:10px;text-anchor:middle;fill:#8a87a8}
.node-kind--safety{fill:#dc2626}
.node-kind--branch{fill:#b45309}
.node-kind--communicate{fill:#2563eb}
.node-title{font-size:12.5px;text-anchor:middle;fill:#2a2740}
.node-check circle{fill:#16a34a}
.node-check text{font-size:11px;text-anchor:middle;fill:#fff}
.node-ai{font-size:9px;text-anchor:end;fill:#4f46e5;font-weight:700}
</style>
