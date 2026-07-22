export type WorkspaceRouteId =
  | 'workbench'
  | 'sessions'
  | 'students'
  | 'question-bank'
  | 'question-assembly'
  | 'training'
  | 'knowledge-graph'
  | 'files'
  | 'grading'
  | 'settings'

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

export const studentsRouteDefinition: WorkspaceRouteDefinition = {
  id: 'students',
  label: '学生名单',
  path: '/students',
  title: '学生名单',
  description: '导入、核对和维护参与阅卷的学生名单',
  breadcrumb: '学生名单',
}

export const questionBankRouteDefinition: WorkspaceRouteDefinition = {
  id: 'question-bank',
  label: '题库管理',
  path: '/question-bank',
  title: '题库管理',
  description: '筛选、核对、导入并安全维护可用于阅卷与分析的题目',
  breadcrumb: '题库管理',
}

export const questionAssemblyRouteDefinition: WorkspaceRouteDefinition = {
  id: 'question-assembly',
  label: '组卷工作台',
  path: '/question-assembly',
  title: '组卷工作台',
  description: '从题库选择试题，整理顺序和分节，并导出练习试卷',
  breadcrumb: '组卷工作台',
}

export const trainingRouteDefinition: WorkspaceRouteDefinition = {
  id: 'training',
  label: '训练推荐',
  path: '/training',
  title: '训练推荐',
  description: '核对薄弱证据、确认精确标签训练计划并生成训练材料',
  breadcrumb: '训练推荐',
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

export const filesRouteDefinition: WorkspaceRouteDefinition = {
  id: 'files',
  label: '文件中心',
  path: '/files',
  title: '文件中心',
  description: '生成、查看并安全下载成绩表、批注原卷和训练材料',
  breadcrumb: '文件中心',
}

export const settingsRouteDefinition: WorkspaceRouteDefinition = {
  id: 'settings',
  label: '设置与运维',
  path: '/settings',
  title: '设置与运维',
  description: '查看系统状态，并通过安全预检执行备份、恢复、迁移和数据转移',
  breadcrumb: '设置与运维',
}

export const navigationItems = [
  workbenchRouteDefinition,
  sessionRouteDefinition,
  studentsRouteDefinition,
  questionBankRouteDefinition,
  questionAssemblyRouteDefinition,
  trainingRouteDefinition,
  knowledgeGraphRouteDefinition,
  filesRouteDefinition,
  reviewRouteDefinition,
  settingsRouteDefinition,
] as const
