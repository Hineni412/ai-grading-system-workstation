export interface WorkspaceRouteDefinition {
  id: 'sessions' | 'grading'
  label: '考试配置' | '评分复核'
  path: '/sessions' | '/grading'
  title: '考试配置' | '评分复核'
  description: '创建考试并准备评分依据' | '按题号批量比较并确认评分结果'
  breadcrumb: '考试配置' | '评分复核'
}

export const sessionRouteDefinition: WorkspaceRouteDefinition = {
  id: 'sessions',
  label: '考试配置',
  path: '/sessions',
  title: '考试配置',
  description: '创建考试并准备评分依据',
  breadcrumb: '考试配置',
}

export const reviewRouteDefinition: WorkspaceRouteDefinition = {
  id: 'grading',
  label: '评分复核',
  path: '/grading',
  title: '评分复核',
  description: '按题号批量比较并确认评分结果',
  breadcrumb: '评分复核',
}

export const navigationItems = [sessionRouteDefinition, reviewRouteDefinition] as const
