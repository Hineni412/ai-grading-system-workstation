import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { WorkspaceManifest } from './contracts'
import {
  createWorkspaceRegistry,
  WorkspaceManifestError,
} from './registry'

function manifest(
  changes: Partial<WorkspaceManifest> = {},
): WorkspaceManifest {
  return {
    moduleId: 'class-teacher',
    displayName: '班主任工作台',
    navigationGroup: 'teacher-workspaces',
    navigationOrder: 20,
    routePrefix: '/class-teacher',
    page: async () => ({ default: defineComponent({ name: 'class-teacher-page' }) }),
    icon: 'class-teacher',
    enabled: true,
    dataClassifications: ['internal'],
    featureFlags: ['class-teacher-shell'],
    title: '班主任工作台',
    description: '模块说明',
    breadcrumb: '班主任工作台',
    topbarContext: 'workspace',
    curriculumScope: false,
    ...changes,
  } as WorkspaceManifest
}

describe('workspace registry', () => {
  it('projects navigation metadata for enabled workspaces', () => {
    const registry = createWorkspaceRegistry([manifest()])

    expect(registry.navigationItems.map(({ id, path }) => [id, path])).toEqual([
      ['class-teacher', '/class-teacher'],
    ])
    expect(registry.modules.map(({ route }) => route.topbarContext)).toEqual([
      'workspace',
    ])
  })

  it('validates disabled manifests but creates no enabled entry', () => {
    const enabled = vi.fn(() => false)
    const registry = createWorkspaceRegistry([
      manifest({ enabled }),
    ])

    expect(enabled).toHaveBeenCalledTimes(1)
    expect(registry.modules).toEqual([])
  })

  it('rejects duplicate IDs and routes', () => {
    expect(() => createWorkspaceRegistry([
      manifest(),
      manifest({ navigationOrder: 30 }),
    ])).toThrow(/Duplicate workspace module ID/)
  })

  it('reports the affected module when a manifest is malformed', () => {
    expect(() => createWorkspaceRegistry([
      manifest({ featureFlags: ['not valid'] }),
    ])).toThrowError(
      new WorkspaceManifestError(
        'Workspace class-teacher feature flags are invalid',
      ),
    )
  })

  it('rejects an undeclared topbar context', () => {
    expect(() => createWorkspaceRegistry([
      manifest({ topbarContext: 'unknown' as 'workspace' }),
    ])).toThrow(/Workspace class-teacher topbar context is invalid/)
  })

  it('rejects conflicts with existing application routes', () => {
    const registry = createWorkspaceRegistry([manifest()])

    expect(() => registry.assertNoCoreConflicts([
      { id: 'class-teacher', path: '/existing' },
    ])).toThrow(/core route ID/)
    expect(() => registry.assertNoCoreConflicts([
      { id: 'existing', path: '/class-teacher' },
    ])).toThrow(/core route prefix/)
  })

  it('fails closed when enablement throws', () => {
    expect(() => createWorkspaceRegistry([
      manifest({
        enabled: () => {
          throw new Error('broken flag source')
        },
      }),
    ])).toThrow(/Workspace class-teacher enablement failed/)
  })

  it('validates ordered destination-key sub-navigation without URLs', () => {
    const registry = createWorkspaceRegistry([
      manifest({
        subNavigation: [
          {
            destinationKey: 'class_teacher.home',
            label: '首页',
            order: 10,
            query: { view: 'home' },
          },
          {
            destinationKey: 'class_teacher.plan.calendar',
            label: '日历',
            order: 20,
            query: { view: 'calendar' },
          },
        ],
      }),
    ])
    expect(registry.modules[0]?.manifest.subNavigation?.map(item => item.label)).toEqual([
      '首页',
      '日历',
    ])

    expect(() => createWorkspaceRegistry([
      manifest({
        subNavigation: [{
          destinationKey: 'class_teacher.home',
          label: '越界入口',
          order: 10,
          query: { url: 'https://example.invalid' },
        }],
      }),
    ])).toThrow(/sub-navigation is invalid/)
  })
})
