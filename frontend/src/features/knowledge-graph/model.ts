import type { GraphNode, GraphRow } from '../../api/graph'

export type MasteryBand = 'stable' | 'slight' | 'review' | 'weak'

export interface EvidenceLaneNode {
  knowledgeKey: string
  label: string
  mastery: number
  percentLabel: string
  band: MasteryBand
  bandLabel: string
  bandIndex: number
  orderInBand: number
  size: number
  studentCount: number
  itemCount: number
  deductionCount: number
}

export interface GroupingTreeNode {
  name: string
  knowledgeKey?: string
  children: GroupingTreeNode[]
}

export interface GraphSummary {
  total: number
  stable: number
  slight: number
  review: number
  weak: number
}

const BAND_ORDER: MasteryBand[] = ['weak', 'review', 'slight', 'stable']

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

function percentLabel(rate: number): string {
  const percent = Math.round(normalizedMastery(rate) * 1000) / 10
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}

export function buildEvidenceLaneModel(nodes: GraphNode[]): EvidenceLaneNode[] {
  const ordered = [...nodes].sort((left, right) => {
    const leftBand = masteryBand(left.average_mastery)
    const rightBand = masteryBand(right.average_mastery)
    return (
      BAND_ORDER.indexOf(leftBand) - BAND_ORDER.indexOf(rightBand) ||
      left.average_mastery - right.average_mastery ||
      left.knowledge_label.localeCompare(right.knowledge_label, 'zh-CN') ||
      left.knowledge_key.localeCompare(right.knowledge_key)
    )
  })
  const bandCounts = new Map<MasteryBand, number>()
  return ordered.map((node) => {
    const band = masteryBand(node.average_mastery)
    const orderInBand = bandCounts.get(band) ?? 0
    bandCounts.set(band, orderInBand + 1)
    return {
      knowledgeKey: node.knowledge_key,
      label: node.knowledge_label,
      mastery: normalizedMastery(node.average_mastery),
      percentLabel: percentLabel(node.average_mastery),
      band,
      bandLabel: masteryBandLabel(band),
      bandIndex: BAND_ORDER.indexOf(band),
      orderInBand,
      size: nodeEvidenceSize(node.item_count),
      studentCount: node.student_count,
      itemCount: node.item_count,
      deductionCount: node.deduction_count,
    }
  })
}

export function buildGroupingTree(rows: GraphRow[], scopeLabel: string): GroupingTreeNode {
  const students = new Map<string, {
    studentId: number
    name: string
    tags: Map<string, string>
  }>()
  for (const row of rows) {
    const key = String(row.student_id)
    const student = students.get(key) ?? {
      studentId: row.student_id,
      name: `${row.student_code} ${row.student_name}`.trim(),
      tags: new Map<string, string>(),
    }
    student.tags.set(row.knowledge_key, row.knowledge_label)
    students.set(key, student)
  }

  const children = [...students.values()]
    .sort((left, right) => (
      left.name.localeCompare(right.name, 'zh-CN') || left.studentId - right.studentId
    ))
    .map((student) => ({
      name: student.name,
      children: [...student.tags.entries()]
        .sort(([leftKey, leftLabel], [rightKey, rightLabel]) => (
          leftLabel.localeCompare(rightLabel, 'zh-CN') || leftKey.localeCompare(rightKey)
        ))
        .map(([knowledgeKey, name]) => ({ name, knowledgeKey, children: [] })),
    }))

  return { name: scopeLabel.trim() || '当前筛选范围', children }
}

export function summarizeGraph(nodes: GraphNode[]): GraphSummary {
  const summary: GraphSummary = { total: nodes.length, stable: 0, slight: 0, review: 0, weak: 0 }
  for (const node of nodes) summary[masteryBand(node.average_mastery)] += 1
  return summary
}
