import type { TrainingOverview, TrainingOverviewNode, TrainingOverviewDistribution } from '../../api/training'
import { shortNodeName } from './model'

// Display thresholds only. Backend tiers are never inferred from these rates.
export const WEAK_HEAT_THRESHOLDS = [0, .1, .2, .3, .4] as const
export const WEAK_HEAT_LABELS = ['0', '≤10%', '≤20%', '≤30%', '≤40%', '>40%'] as const
export const isItem = (node: TrainingOverviewNode) => node.kind === 'topic' || node.kind === 'skill' || node.kind === 'type'
export const isTypeMode = (data: Pick<TrainingOverview, 'target_kind'>) => data.target_kind === 'type'
export const itemWeakCount = (student: TrainingOverview['students'][number]) =>
  student.types ? student.types.weak : student.topics.weak + student.skills.weak
export const volumeItems = (nodes: TrainingOverviewNode[]) => nodes.filter(node => node.in_volume !== false && isItem(node))
export function weakRate(node: TrainingOverviewNode): number | null {
  return node.evidence_student_count > 0 ? node.distribution.weak / node.evidence_student_count : null
}
export function heatLevel(node: TrainingOverviewNode): number {
  const rate = weakRate(node)
  if (rate === null) return -1
  const level = WEAK_HEAT_THRESHOLDS.findIndex(limit => rate <= limit)
  return level === -1 ? WEAK_HEAT_THRESHOLDS.length : level
}
export function overviewMetrics(data: TrainingOverview) {
  const items = volumeItems(data.nodes)
  return { ...data.summary, total: items.length,
    evidence: items.filter(node => node.evidence_student_count > 0).length,
    weak: items.filter(node => node.distribution.weak > 0).length,
    weakStudents: data.students.filter(s => itemWeakCount(s) > 0).length }
}
export function chapterMetrics(nodes: TrainingOverviewNode[]) {
  return nodes.filter(node => node.kind === 'chapter' && node.in_volume !== false).map(chapter => {
    const items = volumeItems(nodes).filter(node => node.chapter_key === chapter.knowledge_key)
    return { chapter, items, evidence: items.filter(node => node.evidence_student_count > 0).length,
      weak: items.filter(node => node.distribution.weak > 0).length }
  })
}
export function nodeLocation(node: TrainingOverviewNode, nodes: TrainingOverviewNode[]): string {
  const names = [node.section_key, node.chapter_key].map(key => nodes.find(n => n.knowledge_key === key))
    .filter((n): n is TrainingOverviewNode => !!n).map(shortNodeName)
  if (names.length) return names.join(' · ')
  return node.display_name.split(/[|｜]/).slice(0, -1).reverse().join(' · ') || '未提供教材位置'
}
export function relatedNodes(data: TrainingOverview, key: string) {
  return (data.associations ?? []).filter(a => a.topic_key === key || a.skill_key === key)
    .map(association => ({ association, node: data.nodes.find(n => n.knowledge_key ===
      (association.topic_key === key ? association.skill_key : association.topic_key)) }))
    .filter((item): item is { association: NonNullable<TrainingOverview['associations']>[number]; node: TrainingOverviewNode } => !!item.node)
    .sort((a, b) => Number(b.association.basis === 'same_part') - Number(a.association.basis === 'same_part')
      || b.association.question_count - a.association.question_count || a.node.knowledge_key.localeCompare(b.node.knowledge_key))
}
export function studentDistribution(student: TrainingOverview['students'][number]): TrainingOverviewDistribution {
  if (student.types) {
    const { weak, unsteady, stable, insufficient } = student.types
    return { weak, unsteady, stable, insufficient }
  }
  const a = student.topics, b = student.skills
  return { weak: a.weak + b.weak, unsteady: a.unsteady + b.unsteady,
    stable: a.stable + b.stable, insufficient: a.insufficient + b.insufficient }
}
