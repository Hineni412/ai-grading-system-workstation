export type WorkspaceRouteId = 'workbench' | 'grading'

export interface WorkspaceRouteDefinition {
  id: WorkspaceRouteId
  label: string
  path: `/${string}`
  title: string
  description: string
  breadcrumb: string
}

export const workbenchRouteDefinition = {
  id: 'workbench',
  label: '工作台',
  path: '/workbench',
  title: '工作台',
  description: '查看当前考试进度、待处理事项与学情分析',
  breadcrumb: '工作台',
} as const satisfies WorkspaceRouteDefinition

export const reviewRouteDefinition: WorkspaceRouteDefinition = {
  id: 'grading',
  label: '评分复核',
  path: '/grading',
  title: '评分复核',
  description: '按题号批量比较并确认评分结果',
  breadcrumb: '评分复核',
}

export const navigationItems = [workbenchRouteDefinition, reviewRouteDefinition] as const
