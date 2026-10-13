import type { WorkspaceRouteDefinition } from '../../navigation'

/* 批改/导分入口：已选考试时直达当前考试的对应执行页 */
export function resolveNavigationTarget(
  item: WorkspaceRouteDefinition,
  selectedSessionId: number | null,
): string {
  if (item.id === 'grading' && selectedSessionId !== null) {
    return `/sessions/${selectedSessionId}/grading-run`
  }
  if (item.id === 'score-import' && selectedSessionId !== null) {
    return `/sessions/${selectedSessionId}/import`
  }
  return item.path
}
