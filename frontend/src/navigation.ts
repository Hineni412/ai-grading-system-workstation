import { workspaceRegistry } from './workspaces/registry'

export type WorkspaceRouteId =
  | 'workbench'
  | 'sessions'
  | 'students'
  | 'question-bank'
  | 'question-assembly'
  | 'training'
  | 'knowledge-graph'
  | 'results'
  | 'files'
  | 'grading'
  | 'model-profiles'
  | 'settings'
  | 'teaching-prep'
  | 'class-teacher'

export type WorkspaceNavigationIcon =
  | 'workbench'
  | 'exam'
  | 'review'
  | 'results'
  | 'files'
  | 'question-bank'
  | 'assembly'
  | 'graph'
  | 'training'
  | 'students'
  | 'model'
  | 'settings'
  | 'teaching-prep'
  | 'class-teacher'

export type AppIconName =
  | WorkspaceNavigationIcon
  | 'archive'
  | 'menu'
  | 'close'
  | 'chevron-down'

export interface WorkspaceRouteDefinition {
  id: WorkspaceRouteId
  label: string
  path: `/${string}`
  title: string
  description: string
  breadcrumb: string
  icon: WorkspaceNavigationIcon
}

export interface WorkspaceNavigationGroup {
  id: 'exam' | 'question-work' | 'analysis' | 'teacher-workspaces'
  label: string
  items: readonly WorkspaceRouteDefinition[]
}

export const workbenchRouteDefinition = {
  id: 'workbench',
  label: '工作台',
  path: '/workbench',
  title: '工作台',
  description: '查看当前考试进度、待处理事项与学情分析',
  breadcrumb: '工作台',
  icon: 'workbench',
} as const satisfies WorkspaceRouteDefinition

export const sessionRouteDefinition = {
  id: 'sessions',
  label: '考试配置',
  path: '/sessions',
  title: '考试配置',
  description: '创建考试并准备评分依据',
  breadcrumb: '考试配置',
  icon: 'exam',
} as const satisfies WorkspaceRouteDefinition

export const studentsRouteDefinition = {
  id: 'students',
  label: '学生管理',
  path: '/students',
  title: '学生名单',
  description: '导入、核对和维护参与阅卷的学生名单',
  breadcrumb: '学生名单',
  icon: 'students',
} as const satisfies WorkspaceRouteDefinition

export const questionBankRouteDefinition = {
  id: 'question-bank',
  label: '题库管理',
  path: '/question-bank',
  title: '题库管理',
  description: '筛选、核对、导入并安全维护可用于阅卷与分析的题目',
  breadcrumb: '题库管理',
  icon: 'question-bank',
} as const satisfies WorkspaceRouteDefinition

export const questionAssemblyRouteDefinition = {
  id: 'question-assembly',
  label: '组卷工作台',
  path: '/question-assembly',
  title: '组卷工作台',
  description: '从题库选择试题，整理顺序和分节，并导出练习试卷',
  breadcrumb: '组卷工作台',
  icon: 'assembly',
} as const satisfies WorkspaceRouteDefinition

export const trainingRouteDefinition = {
  id: 'training',
  label: '知识与训练',
  path: '/training',
  title: '知识与训练',
  description: '按知识结构核对群体证据，人工确定训练范围并生成训练材料',
  breadcrumb: '知识与训练',
  icon: 'training',
} as const satisfies WorkspaceRouteDefinition

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
  breadcrumb: '考试批改 / 批改执行',
} as const

export const reviewRouteDefinition = {
  id: 'grading',
  label: '考试批改',
  path: '/grading',
  title: '考试批改',
  description: '执行整班批改，并按题人工评分或复核 AI 结果',
  breadcrumb: '考试批改',
  icon: 'review',
} as const satisfies WorkspaceRouteDefinition

export const knowledgeGraphRouteDefinition = {
  id: 'knowledge-graph',
  label: '知识与训练',
  path: '/knowledge-graph',
  title: '知识与训练',
  description: '查看知识结构、章节学情并为所选学生安排训练',
  breadcrumb: '知识与训练',
  icon: 'graph',
} as const satisfies WorkspaceRouteDefinition

export const filesRouteDefinition = {
  id: 'files',
  label: '文件中心',
  path: '/files',
  title: '文件中心',
  description: '生成、查看并安全下载成绩表、批注原卷和训练材料',
  breadcrumb: '文件中心',
  icon: 'files',
} as const satisfies WorkspaceRouteDefinition

export const resultsRouteDefinition = {
  id: 'results',
  label: '成绩中心',
  path: '/results',
  title: '成绩中心',
  description: '查看当前考试成绩、定位需要处理的题目并导出正式文件',
  breadcrumb: '成绩中心',
  icon: 'results',
} as const satisfies WorkspaceRouteDefinition

export const modelProfilesRouteDefinition = {
  id: 'model-profiles',
  label: '大模型 API',
  path: '/model-profiles',
  title: '大模型 API',
  description: '管理本机模型服务、密钥与各任务使用的模型',
  breadcrumb: '设置 / 大模型 API',
  icon: 'model',
} as const satisfies WorkspaceRouteDefinition

export const settingsRouteDefinition = {
  id: 'settings',
  label: '设置',
  path: '/settings',
  title: '设置',
  description: '管理模型使用方式、备份与本机维护',
  breadcrumb: '设置',
  icon: 'settings',
} as const satisfies WorkspaceRouteDefinition

workspaceRegistry.assertNoCoreConflicts([
  workbenchRouteDefinition,
  sessionRouteDefinition,
  studentsRouteDefinition,
  questionBankRouteDefinition,
  questionAssemblyRouteDefinition,
  trainingRouteDefinition,
  reviewRouteDefinition,
  knowledgeGraphRouteDefinition,
  filesRouteDefinition,
  resultsRouteDefinition,
  modelProfilesRouteDefinition,
  settingsRouteDefinition,
])

export const navigationGroups: readonly WorkspaceNavigationGroup[] = [
  {
    id: 'exam',
    label: '考试与阅卷',
    items: [
      sessionRouteDefinition,
      reviewRouteDefinition,
      resultsRouteDefinition,
    ],
  },
  {
    id: 'question-work',
    label: '题库与组卷',
    items: [
      questionBankRouteDefinition,
      questionAssemblyRouteDefinition,
    ],
  },
  {
    id: 'analysis',
    label: '教学分析',
    items: [
      knowledgeGraphRouteDefinition,
    ],
  },
  ...(workspaceRegistry.navigationItems.length
    ? [{
        id: 'teacher-workspaces' as const,
        label: '教师工作台',
        items: workspaceRegistry.navigationItems,
      }]
    : []),
] as const

export const settingsNavigationItems: readonly WorkspaceRouteDefinition[] = [
  studentsRouteDefinition,
  settingsRouteDefinition,
] as const

export const navigationItems: readonly WorkspaceRouteDefinition[] = [
  workbenchRouteDefinition,
  ...navigationGroups.flatMap((group) => group.items),
  ...settingsNavigationItems,
] as const
