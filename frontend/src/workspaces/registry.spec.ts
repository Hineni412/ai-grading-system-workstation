import { describe, expect, it } from 'vitest'
import { workspaceRegistry } from './registry'
describe('registered workspaces', () => {
  it('does not register the retired standalone workbench', () => {
    expect(workspaceRegistry.modules).toEqual([])
    expect(workspaceRegistry.navigationItems).toEqual([])
  })
})
