import type {
  GraphV2Edge,
  GraphV2Node,
  GraphV2RelationType,
} from '../../api/graph-v2'
import {
  masteryBand,
  masteryBandLabel,
  nodeEvidenceSize,
  type MasteryBand,
} from './model'

export type GraphV2NodeState = MasteryBand | 'missing'

export interface GraphV2DisplayNode {
  stableKey: string
  label: string
  mastery: number | null
  masteryLabel: string
  state: GraphV2NodeState
  stateLabel: string
  stateIndex: number
  x: number
  y: number
  size: number
  studentCount: number
  evidenceCount: number
  deductionCount: number
}

export interface GraphV2Summary {
  total: number
  relationTotal: number
  missing: number
  stable: number
  slight: number
  review: number
  weak: number
}

export interface GraphV2Path {
  nodeKeys: string[]
  edgeIds: string[]
}

const STATE_ORDER: GraphV2NodeState[] = ['missing', 'weak', 'review', 'slight', 'stable']

function masteryLabel(value: number): string {
  const percent = Math.round(value * 1000) / 10
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}

function activeMastery(node: GraphV2Node, mode: 'v1' | 'v2') {
  return mode === 'v2' ? node.mastery_v2 : node.mastery_v1
}

export function graphV2NodeState(
  node: GraphV2Node,
  mode: 'v1' | 'v2' = 'v1',
): GraphV2NodeState {
  const mastery = activeMastery(node, mode)
  return mastery.status === 'available' && mastery.value !== null
    ? masteryBand(mastery.value)
    : 'missing'
}

export function graphV2NodeStateLabel(state: GraphV2NodeState): string {
  return state === 'missing' ? '当前无证据' : masteryBandLabel(state)
}

export function buildGraphV2DisplayNodes(
  nodes: GraphV2Node[],
  mode: 'v1' | 'v2' = 'v1',
): GraphV2DisplayNode[] {
  const ordered = [...nodes].sort((left, right) => {
    const leftState = graphV2NodeState(left, mode)
    const rightState = graphV2NodeState(right, mode)
    const leftValue = activeMastery(left, mode).value ?? -1
    const rightValue = activeMastery(right, mode).value ?? -1
    return (
      STATE_ORDER.indexOf(leftState) - STATE_ORDER.indexOf(rightState) ||
      leftValue - rightValue ||
      left.display_name.localeCompare(right.display_name, 'zh-CN') ||
      left.stable_key.localeCompare(right.stable_key)
    )
  })
  const totals = new Map<GraphV2NodeState, number>()
  for (const node of ordered) {
    const state = graphV2NodeState(node, mode)
    totals.set(state, (totals.get(state) ?? 0) + 1)
  }
  const starts = new Map<GraphV2NodeState, number>()
  let nextStart = 64
  for (const state of STATE_ORDER) {
    starts.set(state, nextStart)
    nextStart += Math.max(1, Math.ceil((totals.get(state) ?? 0) / 9)) * 92 + 54
  }
  const counts = new Map<GraphV2NodeState, number>()
  return ordered.map((node) => {
    const state = graphV2NodeState(node, mode)
    const order = counts.get(state) ?? 0
    counts.set(state, order + 1)
    const selected = activeMastery(node, mode)
    const mastery = selected.status === 'available'
      ? selected.value
      : null
    return {
      stableKey: node.stable_key,
      label: node.display_name,
      mastery,
      masteryLabel: mastery === null ? '暂无' : masteryLabel(mastery),
      state,
      stateLabel: graphV2NodeStateLabel(state),
      stateIndex: STATE_ORDER.indexOf(state),
      x: 76 + (order % 9) * 126,
      y: (starts.get(state) ?? 64) + Math.floor(order / 9) * 92,
      size: nodeEvidenceSize(node.evidence.item_count),
      studentCount: node.evidence.student_count,
      evidenceCount: node.evidence.item_count,
      deductionCount: node.evidence.deduction_count,
    }
  })
}

export function summarizeGraphV2(
  nodes: GraphV2Node[],
  edges: GraphV2Edge[],
  mode: 'v1' | 'v2' = 'v1',
): GraphV2Summary {
  const summary: GraphV2Summary = {
    total: nodes.length,
    relationTotal: edges.length,
    missing: 0,
    stable: 0,
    slight: 0,
    review: 0,
    weak: 0,
  }
  for (const node of nodes) summary[graphV2NodeState(node, mode)] += 1
  return summary
}

export function relationTypeLabel(type: GraphV2RelationType): string {
  if (type === 'parent') return '子级 → 上位'
  if (type === 'prerequisite') return '目标 → 先修'
  return '相关（无方向）'
}

export function relationVerb(type: GraphV2RelationType): string {
  if (type === 'parent') return '属于'
  if (type === 'prerequisite') return '需要先掌握'
  return '相关'
}

export function connectedNodeKeys(
  edges: GraphV2Edge[],
  selectedKey: string,
  enabledTypes: Set<GraphV2RelationType>,
): Set<string> {
  const result = new Set([selectedKey])
  for (const edge of edges) {
    if (!enabledTypes.has(edge.relation_type)) continue
    if (edge.source_key === selectedKey) result.add(edge.target_key)
    if (edge.target_key === selectedKey) result.add(edge.source_key)
  }
  return result
}

export function findGraphV2Path(
  edges: GraphV2Edge[],
  sourceKey: string,
  targetKey: string,
  enabledTypes: Set<GraphV2RelationType>,
): GraphV2Path | null {
  if (sourceKey === targetKey) return { nodeKeys: [sourceKey], edgeIds: [] }
  const adjacency = new Map<string, Array<{ nodeKey: string; edgeId: string }>>()
  function add(from: string, to: string, edgeId: string): void {
    adjacency.set(from, [...(adjacency.get(from) ?? []), { nodeKey: to, edgeId }])
  }
  for (const edge of edges) {
    if (!enabledTypes.has(edge.relation_type)) continue
    add(edge.source_key, edge.target_key, edge.relation_id)
    if (edge.relation_type === 'related') add(edge.target_key, edge.source_key, edge.relation_id)
  }
  const queue = [sourceKey]
  const previous = new Map<string, { nodeKey: string; edgeId: string }>()
  const visited = new Set([sourceKey])
  while (queue.length > 0) {
    const current = queue.shift()!
    for (const next of adjacency.get(current) ?? []) {
      if (visited.has(next.nodeKey)) continue
      visited.add(next.nodeKey)
      previous.set(next.nodeKey, { nodeKey: current, edgeId: next.edgeId })
      if (next.nodeKey === targetKey) {
        const nodeKeys = [targetKey]
        const edgeIds: string[] = []
        let cursor = targetKey
        while (cursor !== sourceKey) {
          const step = previous.get(cursor)!
          edgeIds.unshift(step.edgeId)
          nodeKeys.unshift(step.nodeKey)
          cursor = step.nodeKey
        }
        return { nodeKeys, edgeIds }
      }
      queue.push(next.nodeKey)
    }
  }
  return null
}
