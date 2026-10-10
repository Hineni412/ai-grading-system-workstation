import type { TrainingOverviewNode } from '../../api/training'

/**
 * 明显薄弱占比＝明显薄弱 ÷（明显薄弱＋不稳＋掌握），证据不足的学生不进分母。
 * share 为 null 表示无证据；<0 表示有学情人数但三档之和为 0（全部证据不足）。
 */
export interface WeakShare {
  share: number | null
  weak: number
  denominator: number
}

export function weakShareOf(node: TrainingOverviewNode | undefined): WeakShare {
  if (!node || node.evidence_student_count <= 0) {
    return { share: null, weak: 0, denominator: 0 }
  }
  const distribution = node.distribution
  const denominator = distribution.weak + distribution.unsteady + distribution.stable
  if (denominator === 0) {
    return { share: -1, weak: distribution.weak, denominator: 0 }
  }
  return {
    share: distribution.weak / denominator,
    weak: distribution.weak,
    denominator,
  }
}

export function weakShareText(node: TrainingOverviewNode | undefined): string {
  const result = weakShareOf(node)
  if (result.share === null) return '无证据'
  if (result.share < 0) return '证据不足'
  return `${Math.round(result.share * 100)}%（${result.weak}/${result.denominator} 人）`
}

export function isWeakAndFrequent(
  total: number,
  node: TrainingOverviewNode | undefined,
): boolean {
  const { share } = weakShareOf(node)
  return share !== null && share >= 0.3 && total >= 3
}
