import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'
import { workspaceRouteDefinitions } from '../navigation'

const placeholderRoutes: RouteRecordRaw[] = workspaceRouteDefinitions.map((definition) => ({
  path: definition.path,
  name: definition.id,
  component: () => import('../views/RoutePlaceholderView.vue'),
  meta: {
    title: definition.title,
    description: definition.description,
    breadcrumb: definition.breadcrumb,
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
