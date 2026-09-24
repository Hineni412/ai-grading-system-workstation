import type {
  TrainingOverviewNode,
  TrainingOverviewStudent,
} from '../../api/training'

export type MasteryTier = 'weak' | 'review' | 'stable'

export const TIER_LABELS: Record<MasteryTier | 'missing', string> = {
  weak: '待补强',
  review: '需巩固',
  stable: '较稳定',
  missing: '证据不足',
}

export function tierOf(mastery: number | null | undefined): MasteryTier | null {
  if (mastery === null || mastery === undefined) return null
  if (mastery < 0.6) return 'weak'
  if (mastery < 0.75) return 'review'
  return 'stable'
}

function pathParts(value: string): string[] {
  return value.split(/[|｜]/).map(part => part.trim()).filter(Boolean)
}

// 目录标签是"册｜章｜小节｜细分点"全路径；章名取第二段，其余取末段，
// 技能再剥掉"技能·/技能："前缀（与知识结构页一致）。
export function shortNodeName(node: Pick<TrainingOverviewNode, 'display_name' | 'kind'>): string {
  const parts = pathParts(node.display_name)
  if (node.kind === 'chapter' && parts.length >= 2) return parts[1] ?? node.display_name
  const leaf = parts.length ? parts[parts.length - 1]! : node.display_name
  return node.kind === 'skill' ? leaf.replace(/^技能[·：:]/, '') : leaf
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

// 最需关注：待补强人数降序 → 群体掌握度升序 → 有证据人数降序 → 稳定键。
export function compareFocusNodes(left: TrainingOverviewNode, right: TrainingOverviewNode): number {
  return (
    right.distribution.weak - left.distribution.weak
    || (left.group_mastery ?? Number.POSITIVE_INFINITY) - (right.group_mastery ?? Number.POSITIVE_INFINITY)
    || right.evidence_student_count - left.evidence_student_count
    || left.knowledge_key.localeCompare(right.knowledge_key)
  )
}

export function defaultStudentSort(
  students: readonly TrainingOverviewStudent[],
): TrainingOverviewStudent[] {
  return [...students].sort((left, right) => (
    (right.topics.weak + right.skills.weak) - (left.topics.weak + left.skills.weak)
    || (left.score_rate ?? Number.POSITIVE_INFINITY) - (right.score_rate ?? Number.POSITIVE_INFINITY)
    || left.student_code.localeCompare(right.student_code, 'zh')
  ))
}
