import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'
import { reviewRouteDefinition, sessionRouteDefinition } from '../navigation'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: sessionRouteDefinition.path,
    meta: {
      title: sessionRouteDefinition.title,
      description: sessionRouteDefinition.description,
      breadcrumb: sessionRouteDefinition.breadcrumb,
    },
  },
  {
    path: sessionRouteDefinition.path,
    name: sessionRouteDefinition.id,
    component: () => import('../views/SessionConfigView.vue'),
    meta: {
      title: sessionRouteDefinition.title,
      description: sessionRouteDefinition.description,
      breadcrumb: sessionRouteDefinition.breadcrumb,
    },
  },
  {
    path: reviewRouteDefinition.path,
    name: reviewRouteDefinition.id,
    component: () => import('../views/ReviewQueueView.vue'),
    meta: {
      title: reviewRouteDefinition.title,
      description: reviewRouteDefinition.description,
      breadcrumb: reviewRouteDefinition.breadcrumb,
    },
  },
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
