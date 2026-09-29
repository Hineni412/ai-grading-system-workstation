import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from 'vue-router'

import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'
import {
  sessionRouteDefinition,
  studentsRouteDefinition,
  questionBankRouteDefinition,
  questionAssemblyRouteDefinition,
  authoringRouteDefinition,
  trainingRouteDefinition,
  filesRouteDefinition,
  resultsRouteDefinition,
  templateRegionRouteDefinition,
  gradingRunRouteDefinition,
  knowledgeGraphRouteDefinition,
  knowledgeOverviewRouteDefinition,
  reviewRouteDefinition,
  workbenchRouteDefinition,
  modelProfilesRouteDefinition,
  settingsRouteDefinition,
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
    path: questionBankRouteDefinition.path,
    name: questionBankRouteDefinition.id,
    component: () => import('../views/QuestionBankView.vue'),
    meta: {
      title: questionBankRouteDefinition.title,
      description: questionBankRouteDefinition.description,
      breadcrumb: questionBankRouteDefinition.breadcrumb,
    },
  },
  {
    path: questionAssemblyRouteDefinition.path,
    name: questionAssemblyRouteDefinition.id,
    component: () => import('../views/QuestionAssemblyView.vue'),
    meta: {
      title: questionAssemblyRouteDefinition.title,
      description: questionAssemblyRouteDefinition.description,
      breadcrumb: questionAssemblyRouteDefinition.breadcrumb,
    },
  },
  {
    path: authoringRouteDefinition.path,
    name: authoringRouteDefinition.id,
    component: () => import('../views/AuthoringPracticeView.vue'),
    meta: {
      title: authoringRouteDefinition.title,
      description: authoringRouteDefinition.description,
      breadcrumb: authoringRouteDefinition.breadcrumb,
    },
  },
  {
    path: trainingRouteDefinition.path,
    name: trainingRouteDefinition.id,
    component: () => import('../views/TrainingRecommendationsView.vue'),
    meta: {
      title: trainingRouteDefinition.title,
      description: trainingRouteDefinition.description,
      breadcrumb: trainingRouteDefinition.breadcrumb,
    },
  },
  {
    path: '/training/evidence/:studentId',
    name: 'student-evidence',
    component: () => import('../views/StudentEvidenceView.vue'),
    meta: {
      title: '学生作答证据',
      description: '按考试场次查看学生的得分、扣分原因与作答图像证据',
      breadcrumb: '知识与训练 / 学生作答证据',
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
    path: knowledgeOverviewRouteDefinition.path,
    name: knowledgeOverviewRouteDefinition.id,
    component: () => import('../views/KnowledgeOverviewView.vue'),
    meta: {
      title: knowledgeOverviewRouteDefinition.title,
      description: knowledgeOverviewRouteDefinition.description,
      breadcrumb: knowledgeOverviewRouteDefinition.breadcrumb,
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
    path: resultsRouteDefinition.path,
    name: resultsRouteDefinition.id,
    component: () => import('../views/ResultsCenterView.vue'),
    meta: {
      title: resultsRouteDefinition.title,
      description: resultsRouteDefinition.description,
      breadcrumb: resultsRouteDefinition.breadcrumb,
    },
  },
  {
    path: '/class-report',
    name: 'class-report',
    component: () => import('../views/ClassReportView.vue'),
    meta: {
      title: '班级报告',
      description: '按班级生成的 AI 分析报告，内嵌于系统框架',
      breadcrumb: `${resultsRouteDefinition.breadcrumb} / 班级报告`,
    },
  },
  {
    path: filesRouteDefinition.path,
    redirect: (to) => ({
      path: resultsRouteDefinition.path,
      query: { ...to.query, tab: 'exports' },
    }),
    meta: {
      title: resultsRouteDefinition.title,
      description: resultsRouteDefinition.description,
      breadcrumb: `${resultsRouteDefinition.breadcrumb} / 导出文件`,
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
    path: modelProfilesRouteDefinition.path,
    redirect: (to) => ({
      path: settingsRouteDefinition.path,
      query: { ...to.query, section: 'models' },
    }),
    meta: {
      title: modelProfilesRouteDefinition.title,
      description: modelProfilesRouteDefinition.description,
      breadcrumb: modelProfilesRouteDefinition.breadcrumb,
    },
  },
  {
    path: settingsRouteDefinition.path,
    name: settingsRouteDefinition.id,
    component: () => import('../views/SettingsHubView.vue'),
    meta: {
      title: settingsRouteDefinition.title,
      description: settingsRouteDefinition.description,
      breadcrumb: settingsRouteDefinition.breadcrumb,
      curriculumScope: false,
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
      curriculumScope: false,
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
      curriculumScope: false,
    },
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({ history, routes })
}
