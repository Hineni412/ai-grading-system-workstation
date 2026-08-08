import type { Component } from 'vue'

export type WorkspaceModuleId = 'teaching-prep' | 'class-teacher'
export type WorkspaceModuleIcon = WorkspaceModuleId
export type WorkspaceTopbarContext = 'current-exam' | 'workspace'
export type WorkspaceDataClassification =
  | 'public'
  | 'internal'
  | 'confidential'
  | 'restricted'

export interface WorkspaceSubNavigationItem {
  destinationKey: string
  label: string
  order: number
  query: Readonly<Record<string, string>>
}

export interface WorkspaceManifest {
  moduleId: WorkspaceModuleId
  displayName: string
  navigationGroup: 'teacher-workspaces'
  navigationOrder: number
  routePrefix: `/${WorkspaceModuleId}`
  page: () => Promise<{ default: Component }>
  icon: WorkspaceModuleIcon
  enabled: boolean | (() => boolean)
  dataClassifications: readonly WorkspaceDataClassification[]
  featureFlags: readonly string[]
  title: string
  description: string
  breadcrumb: string
  topbarContext: WorkspaceTopbarContext
  curriculumScope: boolean
  subNavigation?: readonly WorkspaceSubNavigationItem[]
}

export interface WorkspaceManifestModule {
  default: WorkspaceManifest
}
