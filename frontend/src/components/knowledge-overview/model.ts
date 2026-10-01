import type {
  TrainingOverviewNode,
  TrainingOverviewStudent,
} from '../../api/training'

export type MasteryTier = 'weak' | 'unsteady' | 'stable' | 'insufficient'

export const TIER_LABELS: Record<MasteryTier, string> = {
  weak: '明显薄弱',
  unsteady: '还不稳',
  stable: '较稳定',
  insufficient: '证据不足',
}

export function tierOf(tier: string | null | undefined): MasteryTier {
  return tier === 'stable' || tier === 'unsteady' || tier === 'weak' ? tier : 'insufficient'
}

export function tierClass(tier: string | null | undefined): string {
  return { stable: 'is-good', unsteady: 'is-mid', weak: 'is-low', insufficient: 'is-empty' }[tierOf(tier)]
}

export function masteryDetail(item: {
  mastery?: number | null; value?: number | null; interval_low?: number | null; interval_high?: number | null
  observation_count?: number; full_correct_count?: number; recent_trend?: string | null; tier?: string
} | null | undefined): string {
  if (!item) return '证据不足'
  const range = item.interval_low != null && item.interval_high != null
    ? `（${formatPercent(item.interval_low)}–${formatPercent(item.interval_high)}）` : ''
  const counts = item.observation_count !== undefined
    ? ` · 作答 ${item.observation_count} 处、全对 ${item.full_correct_count ?? 0} 处` : ''
  return `${TIER_LABELS[tierOf(item.tier)]} · 掌握度 ${formatPercent(item.mastery ?? item.value)}${range}${counts}${item.recent_trend ? ` · ${item.recent_trend}` : ''}`
}

export function parentMasteryDetails(
  key: string,
  items: Array<{ knowledge_key: string; mastery?: number | null; tier?: string }>,
  catalog: Array<{ knowledge_key: string; knowledge_point: string; parent_knowledge_key?: string | null }>,
): string[] {
  const references: string[] = []
  const seen = new Set([key])
  let parent = catalog.find(item => item.knowledge_key === key)?.parent_knowledge_key
  while (parent && !seen.has(parent)) {
    seen.add(parent)
    const node = catalog.find(item => item.knowledge_key === parent)
    const value = items.find(item => item.knowledge_key === parent)
    if (node && value?.mastery != null) {
      const parts = node.knowledge_point.split(/[|｜]/)
      references.push(`${parts[parts.length - 1]?.trim()}：${masteryDetail(value)}`)
    }
    parent = node?.parent_knowledge_key
  }
  return references
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

// 最需关注：明显薄弱人数 → 还不稳人数 → 群体掌握度 → 有证据人数。
export function compareFocusNodes(left: TrainingOverviewNode, right: TrainingOverviewNode): number {
  return (
    right.distribution.weak - left.distribution.weak
    || right.distribution.unsteady - left.distribution.unsteady
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
