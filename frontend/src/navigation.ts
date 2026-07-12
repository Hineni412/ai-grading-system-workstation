export type NavigationAvailability = 'available' | 'future'

export interface NavigationItem {
  id: string
  label: string
  symbol: string
  availability: NavigationAvailability
  path?: string
  futureReason?: string
}

export const navigationItems: readonly NavigationItem[] = [
  { id: 'workbench', label: '工作台', symbol: '工', availability: 'available', path: '/workbench' },
  { id: 'grading', label: '阅卷', symbol: '阅', availability: 'available', path: '/grading' },
  { id: 'exams', label: '考试', symbol: '考', availability: 'available', path: '/exams' },
  { id: 'students', label: '学生', symbol: '生', availability: 'available', path: '/students' },
  { id: 'analytics', label: '分析', symbol: '析', availability: 'available', path: '/analytics' },
  { id: 'question-bank', label: '题库与训练', symbol: '题', availability: 'available', path: '/question-bank' },
  {
    id: 'agents',
    label: '智能体与自动化',
    symbol: '智',
    availability: 'future',
    path: undefined,
    futureReason: '未来能力，本阶段暂不开放',
  },
] as const

export const settingsNavigationItem: NavigationItem = {
  id: 'settings', label: '设置', symbol: '设', availability: 'available', path: '/settings',
}
