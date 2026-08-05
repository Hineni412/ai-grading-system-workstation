import type {
  WorkspaceManifest,
  WorkspaceManifestModule,
  WorkspaceModuleIcon,
  WorkspaceModuleId,
  WorkspaceTopbarContext,
} from './contracts'

const MODULE_IDS = new Set<WorkspaceModuleId>(['teaching-prep', 'class-teacher'])
const FEATURE_FLAG = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/
const DATA_CLASSIFICATIONS = new Set([
  'public',
  'internal',
  'confidential',
  'restricted',
])
const TOPBAR_CONTEXTS = new Set<WorkspaceTopbarContext>([
  'current-exam',
  'workspace',
])
const DESTINATION_KEYS = new Set([
  'teaching_prep.overview',
  'teaching_prep.library',
  'teaching_prep.lesson.materials',
  'teaching_prep.lesson.plan',
  'teaching_prep.lesson.exercises',
  'teaching_prep.lesson.slides',
  'teaching_prep.lesson.package',
  'class_teacher.home',
  'class_teacher.student.record',
  'class_teacher.affair.record',
  'class_teacher.plan.calendar',
  'class_teacher.affair.sop',
])
const QUERY_KEY = /^[a-z][a-z0-9_]{0,63}$/
const QUERY_VALUE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/

export interface WorkspaceModuleRouteDefinition {
  id: WorkspaceModuleId
  label: string
  path: `/${WorkspaceModuleId}`
  title: string
  description: string
  breadcrumb: string
  icon: WorkspaceModuleIcon
  topbarContext: WorkspaceTopbarContext
}

export interface RegisteredWorkspaceModule {
  manifest: WorkspaceManifest
  route: WorkspaceModuleRouteDefinition
}

interface ManifestCandidate {
  source: string
  manifest: WorkspaceManifest
}

export class WorkspaceManifestError extends Error {}

export class WorkspaceRegistry {
  readonly modules: readonly RegisteredWorkspaceModule[]
  readonly navigationItems: readonly WorkspaceModuleRouteDefinition[]

  constructor(modules: readonly RegisteredWorkspaceModule[]) {
    this.modules = modules
    this.navigationItems = modules.map(({ route }) => route)
  }

  assertNoCoreConflicts(
    coreRoutes: readonly { id: string; path: string }[],
  ): void {
    const coreIds = new Set(coreRoutes.map(({ id }) => id))
    const corePaths = new Set(coreRoutes.map(({ path }) => path))
    for (const { manifest } of this.modules) {
      if (coreIds.has(manifest.moduleId)) {
        throw new WorkspaceManifestError(
          `Workspace ${manifest.moduleId} conflicts with a core route ID`,
        )
      }
      if (corePaths.has(manifest.routePrefix)) {
        throw new WorkspaceManifestError(
          `Workspace ${manifest.moduleId} conflicts with a core route prefix`,
        )
      }
    }
  }
}

export function createWorkspaceRegistry(
  manifests: readonly WorkspaceManifest[],
): WorkspaceRegistry {
  return createWorkspaceRegistryFromCandidates(
    manifests.map((manifest, index) => ({
      source: `manifest[${index}]`,
      manifest,
    })),
  )
}

function createWorkspaceRegistryFromCandidates(
  candidates: readonly ManifestCandidate[],
): WorkspaceRegistry {
  const seenIds = new Set<string>()
  const seenRoutes = new Set<string>()
  const seenOrders = new Set<number>()

  for (const candidate of candidates) {
    validateManifest(candidate.source, candidate.manifest)
    const { moduleId, routePrefix, navigationOrder } = candidate.manifest
    if (seenIds.has(moduleId)) {
      throw new WorkspaceManifestError(`Duplicate workspace module ID: ${moduleId}`)
    }
    if (seenRoutes.has(routePrefix)) {
      throw new WorkspaceManifestError(
        `Duplicate workspace route prefix: ${routePrefix}`,
      )
    }
    if (seenOrders.has(navigationOrder)) {
      throw new WorkspaceManifestError(
        `Duplicate workspace navigation order: ${navigationOrder}`,
      )
    }
    seenIds.add(moduleId)
    seenRoutes.add(routePrefix)
    seenOrders.add(navigationOrder)
  }

  const enabled = candidates
    .filter(({ source, manifest }) => resolveEnabled(source, manifest))
    .sort((left, right) => (
      left.manifest.navigationOrder - right.manifest.navigationOrder
      || left.manifest.moduleId.localeCompare(right.manifest.moduleId)
    ))
    .map(({ manifest }) => ({
      manifest,
      route: {
        id: manifest.moduleId,
        label: manifest.displayName,
        path: manifest.routePrefix,
        title: manifest.title,
        description: manifest.description,
        breadcrumb: manifest.breadcrumb,
        icon: manifest.icon,
        topbarContext: manifest.topbarContext,
      },
    }))

  return new WorkspaceRegistry(enabled)
}

function resolveEnabled(source: string, manifest: WorkspaceManifest): boolean {
  try {
    const enabled = typeof manifest.enabled === 'function'
      ? manifest.enabled()
      : manifest.enabled
    if (typeof enabled !== 'boolean') {
      throw new TypeError('enabled condition must return boolean')
    }
    return enabled
  } catch (error) {
    throw new WorkspaceManifestError(
      `Workspace ${manifest.moduleId || source} enablement failed: ${String(error)}`,
    )
  }
}

function validateManifest(source: string, manifest: WorkspaceManifest): void {
  const label = String(manifest?.moduleId || source)
  if (!manifest || typeof manifest !== 'object') {
    throw new WorkspaceManifestError(`Workspace ${source} manifest is invalid`)
  }
  if (!MODULE_IDS.has(manifest.moduleId)) {
    throw new WorkspaceManifestError(`Workspace ${label} module ID is invalid`)
  }
  if (manifest.routePrefix !== `/${manifest.moduleId}`) {
    throw new WorkspaceManifestError(`Workspace ${label} route prefix is invalid`)
  }
  if (manifest.icon !== manifest.moduleId) {
    throw new WorkspaceManifestError(`Workspace ${label} icon key is invalid`)
  }
  if (manifest.navigationGroup !== 'teacher-workspaces') {
    throw new WorkspaceManifestError(
      `Workspace ${label} navigation group is invalid`,
    )
  }
  if (!TOPBAR_CONTEXTS.has(manifest.topbarContext)) {
    throw new WorkspaceManifestError(
      `Workspace ${label} topbar context is invalid`,
    )
  }
  if (
    !Number.isSafeInteger(manifest.navigationOrder)
    || manifest.navigationOrder <= 0
  ) {
    throw new WorkspaceManifestError(
      `Workspace ${label} navigation order is invalid`,
    )
  }
  for (const field of [
    manifest.displayName,
    manifest.title,
    manifest.description,
    manifest.breadcrumb,
  ]) {
    if (!String(field || '').trim()) {
      throw new WorkspaceManifestError(
        `Workspace ${label} display metadata is incomplete`,
      )
    }
  }
  if (typeof manifest.page !== 'function') {
    throw new WorkspaceManifestError(`Workspace ${label} page loader is invalid`)
  }
  if (
    !Array.isArray(manifest.dataClassifications)
    || manifest.dataClassifications.length === 0
    || manifest.dataClassifications.some(
      (value) => !DATA_CLASSIFICATIONS.has(value),
    )
  ) {
    throw new WorkspaceManifestError(
      `Workspace ${label} data classifications are invalid`,
    )
  }
  if (
    !Array.isArray(manifest.featureFlags)
    || manifest.featureFlags.some((value) => !FEATURE_FLAG.test(value))
    || new Set(manifest.featureFlags).size !== manifest.featureFlags.length
  ) {
    throw new WorkspaceManifestError(
      `Workspace ${label} feature flags are invalid`,
    )
  }
  validateSubNavigation(label, manifest)
}

function validateSubNavigation(label: string, manifest: WorkspaceManifest): void {
  if (manifest.subNavigation === undefined) return
  if (!Array.isArray(manifest.subNavigation)) {
    throw new WorkspaceManifestError(
      `Workspace ${label} sub-navigation is invalid`,
    )
  }
  const seenDestinations = new Set<string>()
  const seenOrders = new Set<number>()
  const prefix = manifest.moduleId === 'teaching-prep'
    ? 'teaching_prep.'
    : 'class_teacher.'
  for (const item of manifest.subNavigation) {
    if (
      !item
      || !DESTINATION_KEYS.has(item.destinationKey)
      || !item.destinationKey.startsWith(prefix)
      || typeof item.label !== 'string'
      || !item.label.trim()
      || !Number.isSafeInteger(item.order)
      || item.order <= 0
      || typeof item.query !== 'object'
      || item.query === null
      || Array.isArray(item.query)
      || Object.entries(item.query).some(
        ([key, value]) => typeof value !== 'string'
          || !QUERY_KEY.test(key)
          || !QUERY_VALUE.test(value),
      )
      || seenDestinations.has(item.destinationKey)
      || seenOrders.has(item.order)
    ) {
      throw new WorkspaceManifestError(
        `Workspace ${label} sub-navigation is invalid`,
      )
    }
    seenDestinations.add(item.destinationKey)
    seenOrders.add(item.order)
  }
}

const discoveredManifestModules = import.meta.glob<WorkspaceManifestModule>(
  './*/manifest.ts',
  { eager: true },
)

export const workspaceRegistry = createWorkspaceRegistryFromCandidates(
  Object.entries(discoveredManifestModules).map(([source, module]) => ({
    source,
    manifest: module.default,
  })),
)
