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
})
