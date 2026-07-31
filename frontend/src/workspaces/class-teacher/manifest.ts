import type { WorkspaceManifest } from '../contracts'

const manifest: WorkspaceManifest = {
  moduleId: 'class-teacher',
  displayName: '班主任工作台',
  navigationGroup: 'teacher-workspaces',
  navigationOrder: 20,
  routePrefix: '/class-teacher',
  page: () => import('./views/ClassTeacherWorkbenchView.vue'),
  icon: 'class-teacher',
  enabled: true,
  dataClassifications: ['restricted'],
  featureFlags: ['vault'],
  title: '班主任工作台',
  description: '默认锁定的班主任行动、事务与学生支持空间',
  breadcrumb: '班主任工作台',
}

export default manifest
