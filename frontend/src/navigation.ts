export type WorkspaceRouteId = 'workbench' | 'sessions' | 'knowledge-graph' | 'grading'

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

export const sessionRouteDefinition: WorkspaceRouteDefinition = {
  id: 'sessions',
  label: '考试配置',
  path: '/sessions',
  title: '考试配置',
  description: '创建考试并准备评分依据',
  breadcrumb: '考试配置',
}

export const templateRegionRouteDefinition = {
  id: 'template-regions',
  path: '/sessions/:sessionId/regions',
  title: '样卷题框标定',
  description: '上传样卷并标定每道题的作答区域',
  breadcrumb: '考试配置 / 样卷题框标定',
} as const

export const gradingRunRouteDefinition = {
  id: 'grading-run',
  path: '/sessions/:sessionId/grading-run',
  title: '批改执行',
  description: '上传整班答卷、完成扫描预检并控制批改运行',
  breadcrumb: '考试配置 / 批改执行',
} as const

export const reviewRouteDefinition: WorkspaceRouteDefinition = {
  id: 'grading',
  label: '评分复核',
  path: '/grading',
  title: '评分复核',
  description: '按题号批量比较并确认评分结果',
  breadcrumb: '评分复核',
}

export const knowledgeGraphRouteDefinition = {
  id: 'knowledge-graph',
  label: '知识图谱',
  path: '/knowledge-graph',
  title: '知识图谱',
  description: '按考试、班级和学生查看知识标签证据',
  breadcrumb: '知识图谱',
} as const satisfies WorkspaceRouteDefinition

export const navigationItems = [
  workbenchRouteDefinition,
  sessionRouteDefinition,
  knowledgeGraphRouteDefinition,
  reviewRouteDefinition,
] as const
