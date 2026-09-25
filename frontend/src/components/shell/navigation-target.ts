import type { WorkspaceRouteDefinition } from '../../navigation'

/* 批改入口：已选考试时直达当前考试的批改执行页 */
export function resolveNavigationTarget(
  item: WorkspaceRouteDefinition,
  selectedSessionId: number | null,
): string {
  if (item.id === 'grading' && selectedSessionId !== null) {
    return `/sessions/${selectedSessionId}/grading-run`
  }
  return item.path
}
