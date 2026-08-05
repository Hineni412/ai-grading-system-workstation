import type { WorkspaceManifest } from '../contracts'

const manifest: WorkspaceManifest = {
  moduleId: 'teaching-prep',
  displayName: '备课工作台',
  navigationGroup: 'teacher-workspaces',
  navigationOrder: 10,
  routePrefix: '/teaching-prep',
  page: () => import('./views/TeachingPrepHomeView.vue'),
  icon: 'teaching-prep',
  enabled: () => import.meta.env.VITE_TEACHING_PREP_ENABLED !== '0',
  dataClassifications: ['internal', 'confidential'],
  featureFlags: ['teaching-prep-shell'],
  title: '备课工作台',
  description: '按课时整理资料、证据和课件改编版本',
  breadcrumb: '备课工作台',
  topbarContext: 'workspace',
  subNavigation: [
    {
      destinationKey: 'teaching_prep.overview',
      label: '备课首页',
      order: 10,
      query: { view: 'overview' },
    },
    {
      destinationKey: 'teaching_prep.library',
      label: '资料库',
      order: 20,
      query: { view: 'library' },
    },
  ],
}

export default manifest
