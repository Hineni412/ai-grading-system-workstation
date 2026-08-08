import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { WorkspaceManifest } from './contracts'
import {
  createWorkspaceRegistry,
  WorkspaceManifestError,
} from './registry'

function manifest(
  moduleId: 'teaching-prep' | 'class-teacher',
  changes: Partial<WorkspaceManifest> = {},
): WorkspaceManifest {
  return {
    moduleId,
    displayName: moduleId === 'teaching-prep' ? '备课工作台' : '班主任工作台',
    navigationGroup: 'teacher-workspaces',
    navigationOrder: moduleId === 'teaching-prep' ? 10 : 20,
    routePrefix: `/${moduleId}`,
    page: async () => ({ default: defineComponent({ name: `${moduleId}-page` }) }),
    icon: moduleId,
    enabled: true,
    dataClassifications: ['internal'],
    featureFlags: [`${moduleId}-shell`],
    title: moduleId === 'teaching-prep' ? '备课工作台' : '班主任工作台',
    description: '模块说明',
    breadcrumb: moduleId === 'teaching-prep' ? '备课工作台' : '班主任工作台',
    topbarContext: 'workspace',
    curriculumScope: moduleId === 'teaching-prep',
    ...changes,
  } as WorkspaceManifest
}

describe('workspace registry', () => {
  it('sorts enabled workspaces and projects navigation metadata', () => {
    const registry = createWorkspaceRegistry([
      manifest('class-teacher'),
      manifest('teaching-prep'),
    ])

    expect(registry.navigationItems.map(({ id, path }) => [id, path])).toEqual([
      ['teaching-prep', '/teaching-prep'],
      ['class-teacher', '/class-teacher'],
    ])
    expect(registry.modules.map(({ route }) => route.topbarContext)).toEqual([
      'workspace',
      'workspace',
    ])
  })

  it('validates disabled manifests but creates no enabled entry', () => {
    const enabled = vi.fn(() => false)
    const registry = createWorkspaceRegistry([
      manifest('teaching-prep', { enabled }),
    ])

    expect(enabled).toHaveBeenCalledTimes(1)
    expect(registry.modules).toEqual([])
  })

  it('rejects duplicate IDs, routes and navigation orders', () => {
    expect(() => createWorkspaceRegistry([
      manifest('teaching-prep'),
      manifest('teaching-prep', { navigationOrder: 30 }),
    ])).toThrow(/Duplicate workspace module ID/)

    expect(() => createWorkspaceRegistry([
      manifest('teaching-prep'),
      manifest('class-teacher', { navigationOrder: 10 }),
    ])).toThrow(/Duplicate workspace navigation order/)
  })

  it('reports the affected module when a manifest is malformed', () => {
    expect(() => createWorkspaceRegistry([
      manifest('class-teacher', { featureFlags: ['not valid'] }),
    ])).toThrowError(
      new WorkspaceManifestError(
        'Workspace class-teacher feature flags are invalid',
      ),
    )
  })

  it('rejects an undeclared topbar context', () => {
    expect(() => createWorkspaceRegistry([
      manifest('teaching-prep', { topbarContext: 'unknown' as 'workspace' }),
    ])).toThrow(/Workspace teaching-prep topbar context is invalid/)
  })

  it('rejects conflicts with existing application routes', () => {
    const registry = createWorkspaceRegistry([manifest('teaching-prep')])

    expect(() => registry.assertNoCoreConflicts([
      { id: 'teaching-prep', path: '/existing' },
    ])).toThrow(/core route ID/)
    expect(() => registry.assertNoCoreConflicts([
      { id: 'existing', path: '/teaching-prep' },
    ])).toThrow(/core route prefix/)
  })

  it('fails closed when enablement throws', () => {
    expect(() => createWorkspaceRegistry([
      manifest('teaching-prep', {
        enabled: () => {
          throw new Error('broken flag source')
        },
      }),
    ])).toThrow(/Workspace teaching-prep enablement failed/)
  })

  it('validates ordered destination-key sub-navigation without URLs', () => {
    const registry = createWorkspaceRegistry([
      manifest('class-teacher', {
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
      manifest('teaching-prep', {
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
