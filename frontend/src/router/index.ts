import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'

const placeholderRouteDefinitions: ReadonlyArray<readonly [string, string, string]> = [
  ['/workbench', 'workbench', '工作台'],
  ['/grading', 'grading', '阅卷'],
  ['/exams', 'exams', '考试'],
  ['/students', 'students', '学生'],
  ['/analytics', 'analytics', '分析'],
  ['/question-bank', 'question-bank', '题库与训练'],
  ['/settings', 'settings', '设置'],
]

const placeholderRoutes: RouteRecordRaw[] = placeholderRouteDefinitions.map(([path, name, title]) => ({
  path,
  name,
  component: () => import('../views/RoutePlaceholderView.vue'),
  meta: {
    title,
    description: `${title}工作区尚未迁移`,
    breadcrumb: title,
  },
}))

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/workbench',
    meta: {
      title: '工作台',
      description: '进入工作台',
      breadcrumb: '工作台',
    },
  },
  ...placeholderRoutes,
  {
    path: '/design-system',
    name: 'design-system',
    component: ComponentShowcase,
    meta: {
      title: '设计系统展示',
      description: '查看基础控件、状态和视觉规范',
      breadcrumb: '设计系统展示',
    },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFoundView.vue'),
    meta: {
      title: '页面未找到',
      description: '请求的页面不存在，当前数据没有改变',
      breadcrumb: '页面未找到',
    },
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({ history, routes })
}
