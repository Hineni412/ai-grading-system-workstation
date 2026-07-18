import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'
import {
  sessionRouteDefinition,
  studentsRouteDefinition,
  filesRouteDefinition,
  templateRegionRouteDefinition,
  gradingRunRouteDefinition,
  knowledgeGraphRouteDefinition,
  reviewRouteDefinition,
  workbenchRouteDefinition,
} from '../navigation'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: workbenchRouteDefinition.path,
    meta: {
      title: workbenchRouteDefinition.title,
      description: workbenchRouteDefinition.description,
      breadcrumb: workbenchRouteDefinition.breadcrumb,
    },
  },
  {
    path: workbenchRouteDefinition.path,
    name: workbenchRouteDefinition.id,
    component: () => import('../views/WorkbenchView.vue'),
    meta: {
      title: workbenchRouteDefinition.title,
      description: workbenchRouteDefinition.description,
      breadcrumb: workbenchRouteDefinition.breadcrumb,
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
    path: studentsRouteDefinition.path,
    name: studentsRouteDefinition.id,
    component: () => import('../views/StudentsView.vue'),
    meta: {
      title: studentsRouteDefinition.title,
      description: studentsRouteDefinition.description,
      breadcrumb: studentsRouteDefinition.breadcrumb,
    },
  },
  {
    path: templateRegionRouteDefinition.path,
    name: templateRegionRouteDefinition.id,
    component: () => import('../views/TemplateRegionView.vue'),
    meta: {
      title: templateRegionRouteDefinition.title,
      description: templateRegionRouteDefinition.description,
      breadcrumb: templateRegionRouteDefinition.breadcrumb,
    },
  },
  {
    path: gradingRunRouteDefinition.path,
    name: gradingRunRouteDefinition.id,
    component: () => import('../views/ScanGradingView.vue'),
    meta: {
      title: gradingRunRouteDefinition.title,
      description: gradingRunRouteDefinition.description,
      breadcrumb: gradingRunRouteDefinition.breadcrumb,
    },
  },
  {
    path: knowledgeGraphRouteDefinition.path,
    name: knowledgeGraphRouteDefinition.id,
    component: () => import('../views/KnowledgeGraphView.vue'),
    meta: {
      title: knowledgeGraphRouteDefinition.title,
      description: knowledgeGraphRouteDefinition.description,
      breadcrumb: knowledgeGraphRouteDefinition.breadcrumb,
    },
  },
  {
    path: filesRouteDefinition.path,
    name: filesRouteDefinition.id,
    component: () => import('../views/FileCenterView.vue'),
    meta: {
      title: filesRouteDefinition.title,
      description: filesRouteDefinition.description,
      breadcrumb: filesRouteDefinition.breadcrumb,
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
