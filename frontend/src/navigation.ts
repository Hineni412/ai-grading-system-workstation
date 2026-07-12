export type NavigationAvailability = 'available' | 'future'

export interface WorkspaceRouteDefinition {
  id: string
  label: string
  symbol: string
  availability: 'available'
  path: string
  title: string
  description: string
  breadcrumb: string
  futureReason?: undefined
}

export interface FutureNavigationItem {
  id: string
  label: string
  symbol: string
  availability: 'future'
  path?: undefined
  futureReason?: string
}

export type NavigationItem = WorkspaceRouteDefinition | FutureNavigationItem

function workspaceRoute(
  id: string,
  path: string,
  label: string,
  symbol: string,
): WorkspaceRouteDefinition {
  return {
    id,
    path,
    label,
    symbol,
    availability: 'available',
    title: label,
    description: `${label}工作区尚未迁移`,
    breadcrumb: label,
  }
}

export const workspaceRouteDefinitions: readonly WorkspaceRouteDefinition[] = [
  workspaceRoute('workbench', '/workbench', '工作台', '工'),
  workspaceRoute('grading', '/grading', '阅卷', '阅'),
  workspaceRoute('exams', '/exams', '考试', '考'),
  workspaceRoute('students', '/students', '学生', '生'),
  workspaceRoute('analytics', '/analytics', '分析', '析'),
  workspaceRoute('question-bank', '/question-bank', '题库与训练', '题'),
  workspaceRoute('settings', '/settings', '设置', '设'),
] as const

export const navigationItems: readonly NavigationItem[] = [
  ...workspaceRouteDefinitions.slice(0, 6),
  {
    id: 'agents',
    label: '智能体与自动化',
    symbol: '智',
    availability: 'future',
    path: undefined,
    futureReason: '未来能力，本阶段暂不开放',
  },
] as const

export const settingsNavigationItem = workspaceRouteDefinitions[6]!
