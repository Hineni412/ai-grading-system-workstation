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
  featureFlags: ['conversation-intake'],
  title: '班主任工作台',
  description: '持续对话整理班级事务，正式保存始终由教师确认',
  breadcrumb: '班主任工作台',
  topbarContext: 'workspace',
  curriculumScope: false,
}

export default manifest
