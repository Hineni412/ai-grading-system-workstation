import { computed, inject, onBeforeUnmount, onMounted, ref, type ComputedRef } from 'vue'
import { routeLocationKey, routerKey } from 'vue-router'

export type ClassTeacherSurface = 'today' | 'calendar' | 'affairs' | 'students'
export type StudentPanel = 'directory' | 'support' | 'academic' | 'security'
export type WorkRange = 'today' | 'week' | 'timeline' | 'all'

export interface ClassTeacherRouteState {
  surface: ClassTeacherSurface
  panel: StudentPanel
  range: WorkRange
  week: string | null
}

const surfaces = new Set<ClassTeacherSurface>(['today', 'calendar', 'affairs', 'students'])
const panels = new Set<StudentPanel>(['directory', 'support', 'academic', 'security'])
const ranges = new Set<WorkRange>(['today', 'week', 'timeline', 'all'])
const isoDate = /^\d{4}-\d{2}-\d{2}$/

function first(value: unknown): string | null {
  if (Array.isArray(value)) return typeof value[0] === 'string' ? value[0] : null
  return typeof value === 'string' ? value : null
}

function normalize(query: Record<string, unknown>): { state: ClassTeacherRouteState; valid: boolean } {
  const rawSurface = first(query.surface)
  const rawPanel = first(query.panel)
  const rawRange = first(query.range)
  const rawWeek = first(query.week)
  const surface = surfaces.has(rawSurface as ClassTeacherSurface)
    ? rawSurface as ClassTeacherSurface
    : 'today'
  const panel = panels.has(rawPanel as StudentPanel)
    ? rawPanel as StudentPanel
    : 'directory'
  const range = ranges.has(rawRange as WorkRange)
    ? rawRange as WorkRange
    : 'week'
  const week = rawWeek && isoDate.test(rawWeek) ? rawWeek : null
  const allowed = new Set(['surface', 'panel', 'range', 'week'])
  const unknown = Object.keys(query).some((key) => !allowed.has(key))
  const valid = !unknown
    && rawSurface === surface
    && (surface !== 'students' || rawPanel === panel)
    && (surface !== 'calendar' || rawRange === range)
    && (!rawWeek || week === rawWeek)
  return { state: { surface, panel, range, week }, valid }
}

function browserQuery(): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(window.location.search))
}

export function useClassTeacherRouteState(): {
  state: ComputedRef<ClassTeacherRouteState>
  navigate: (next: Partial<ClassTeacherRouteState>) => Promise<void>
  canonicalize: () => Promise<void>
} {
  const router = inject(routerKey, null)
  const route = inject(routeLocationKey, null)
  const fallbackQuery = ref<Record<string, string>>(browserQuery())
  const query = computed<Record<string, unknown>>(() => (
    route ? route.query as Record<string, unknown> : fallbackQuery.value
  ))
  const normalized = computed(() => normalize(query.value))

  function locationFor(next: ClassTeacherRouteState): { path: string; query: Record<string, string> } {
    const target: Record<string, string> = { surface: next.surface }
    if (next.surface === 'calendar') {
      target.range = next.range
      if (next.week) target.week = next.week
    }
    if (next.surface === 'students') target.panel = next.panel
    return { path: '/class-teacher', query: target }
  }

  async function commit(next: ClassTeacherRouteState, replace: boolean): Promise<void> {
    const location = locationFor(next)
    if (router) {
      await (replace ? router.replace(location) : router.push(location))
      return
    }
    const search = new URLSearchParams(location.query).toString()
    window.history[replace ? 'replaceState' : 'pushState']({}, '', `${location.path}?${search}`)
    fallbackQuery.value = browserQuery()
  }

  async function canonicalize(): Promise<void> {
    if (!normalized.value.valid) {
      await commit({ surface: 'today', panel: 'directory', range: 'week', week: null }, true)
    }
  }

  async function navigate(next: Partial<ClassTeacherRouteState>): Promise<void> {
    await commit({ ...normalized.value.state, ...next }, false)
  }

  function onPopState(): void {
    fallbackQuery.value = browserQuery()
  }

  onMounted(() => {
    if (!router) window.addEventListener('popstate', onPopState)
    void canonicalize()
  })
  onBeforeUnmount(() => {
    if (!router) window.removeEventListener('popstate', onPopState)
  })

  return {
    state: computed(() => normalized.value.state),
    navigate,
    canonicalize,
  }
}
