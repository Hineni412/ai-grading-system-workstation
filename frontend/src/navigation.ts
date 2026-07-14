export interface WorkspaceRouteDefinition {
  id: 'grading'
  label: '评分复核'
  path: '/grading'
  title: '评分复核'
  description: '按题号批量比较并确认评分结果'
  breadcrumb: '评分复核'
}

export const reviewRouteDefinition: WorkspaceRouteDefinition = {
  id: 'grading',
  label: '评分复核',
  path: '/grading',
  title: '评分复核',
  description: '按题号批量比较并确认评分结果',
  breadcrumb: '评分复核',
}

export const navigationItems = [reviewRouteDefinition] as const
