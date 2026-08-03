import type { GraphEdge, GraphNode, GraphRelationType } from '../../api/graph'

export type MasteryBand = 'stable' | 'slight' | 'review' | 'weak'
export type GraphNodeState = MasteryBand | 'missing'

export interface GraphDisplayNode {
  stableKey: string
  label: string
  mastery: number | null
  masteryLabel: string
  state: GraphNodeState
  stateLabel: string
  stateIndex: number
  x: number
  y: number
  size: number
  studentCount: number
  evidenceCount: number
  deductionCount: number
}

export interface GraphSummary {
  total: number
  relationTotal: number
  missing: number
  stable: number
  slight: number
  review: number
  weak: number
}

export interface GraphPath {
  nodeKeys: string[]
  edgeIds: string[]
}

const STATE_ORDER: GraphNodeState[] = ['missing', 'weak', 'review', 'slight', 'stable']
const BAND_LABELS: Record<MasteryBand, string> = {
  stable: '稳定',
  slight: '轻微欠缺',
  review: '需要讲评',
  weak: '重点薄弱',
}

function normalizedMastery(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

export function masteryBand(rate: number): MasteryBand {
  const value = normalizedMastery(rate)
  if (value >= 0.9) return 'stable'
  if (value >= 0.75) return 'slight'
  if (value >= 0.6) return 'review'
  return 'weak'
}

export function masteryBandLabel(band: MasteryBand): string {
  return BAND_LABELS[band]
}

export function nodeEvidenceSize(itemCount: number): number {
  const count = Number.isFinite(itemCount) ? Math.max(0, itemCount) : 0
  return Math.min(72, Math.max(28, 24 + Math.sqrt(count) * 6))
}

function masteryLabel(value: number): string {
  const percent = Math.round(value * 1000) / 10
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}

export function graphNodeState(node: GraphNode): GraphNodeState {
  return node.mastery.status === 'available' && node.mastery.value !== null
    ? masteryBand(node.mastery.value)
    : 'missing'
}

export function graphNodeStateLabel(state: GraphNodeState): string {
  return state === 'missing' ? '当前无证据' : masteryBandLabel(state)
}

export function buildGraphDisplayNodes(nodes: GraphNode[]): GraphDisplayNode[] {
  const ordered = [...nodes].sort((left, right) => {
    const leftState = graphNodeState(left)
    const rightState = graphNodeState(right)
    return STATE_ORDER.indexOf(leftState) - STATE_ORDER.indexOf(rightState)
      || (left.mastery.value ?? -1) - (right.mastery.value ?? -1)
      || left.display_name.localeCompare(right.display_name, 'zh-CN')
      || left.stable_key.localeCompare(right.stable_key)
  })
  const totals = new Map<GraphNodeState, number>()
  for (const node of ordered) {
    const state = graphNodeState(node)
    totals.set(state, (totals.get(state) ?? 0) + 1)
  }
  const starts = new Map<GraphNodeState, number>()
  let nextStart = 64
  for (const state of STATE_ORDER) {
    starts.set(state, nextStart)
    nextStart += Math.max(1, Math.ceil((totals.get(state) ?? 0) / 9)) * 92 + 54
  }
  const counts = new Map<GraphNodeState, number>()
  return ordered.map((node) => {
    const state = graphNodeState(node)
    const order = counts.get(state) ?? 0
    counts.set(state, order + 1)
    const mastery = node.mastery.status === 'available' ? node.mastery.value : null
    return {
      stableKey: node.stable_key,
      label: node.display_name,
      mastery,
      masteryLabel: mastery === null ? '暂无' : masteryLabel(mastery),
      state,
      stateLabel: graphNodeStateLabel(state),
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

export function summarizeGraph(nodes: GraphNode[], edges: GraphEdge[]): GraphSummary {
  const summary: GraphSummary = {
    total: nodes.length,
    relationTotal: edges.length,
    missing: 0,
    stable: 0,
    slight: 0,
    review: 0,
    weak: 0,
  }
  for (const node of nodes) summary[graphNodeState(node)] += 1
  return summary
}

export function relationTypeLabel(type: GraphRelationType): string {
  if (type === 'parent') return '子级 → 上位'
  if (type === 'prerequisite') return '目标 → 先修'
  return '相关（无方向）'
}

export function relationVerb(type: GraphRelationType): string {
  if (type === 'parent') return '属于'
  if (type === 'prerequisite') return '需要先掌握'
  return '相关'
}

export function connectedNodeKeys(
  edges: GraphEdge[],
  selectedKey: string,
  enabledTypes: Set<GraphRelationType>,
): Set<string> {
  const result = new Set([selectedKey])
  for (const edge of edges) {
    if (!enabledTypes.has(edge.relation_type)) continue
    if (edge.source_key === selectedKey) result.add(edge.target_key)
    if (edge.target_key === selectedKey) result.add(edge.source_key)
  }
  return result
}

export function findGraphPath(
  edges: GraphEdge[],
  sourceKey: string,
  targetKey: string,
  enabledTypes: Set<GraphRelationType>,
): GraphPath | null {
  if (sourceKey === targetKey) return { nodeKeys: [sourceKey], edgeIds: [] }
  const adjacency = new Map<string, Array<{ nodeKey: string; edgeId: string }>>()
  const add = (from: string, to: string, edgeId: string) => {
    adjacency.set(from, [...(adjacency.get(from) ?? []), { nodeKey: to, edgeId }])
  }
  for (const edge of edges) {
    if (!enabledTypes.has(edge.relation_type)) continue
    add(edge.source_key, edge.target_key, edge.relation_key)
    if (edge.relation_type === 'related') add(edge.target_key, edge.source_key, edge.relation_key)
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
