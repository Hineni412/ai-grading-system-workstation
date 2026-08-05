import { computed, inject, onBeforeUnmount, onMounted, ref, type ComputedRef } from 'vue'
import { routeLocationKey, routerKey } from 'vue-router'

export type ClassTeacherSurface = 'home' | 'calendar' | 'affairs' | 'students'
export type StudentPanel = 'directory' | 'support' | 'academic'
export type WorkRange = 'today' | 'week' | 'timeline' | 'all'

export interface ClassTeacherRouteState {
  surface: ClassTeacherSurface
  panel: StudentPanel
  range: WorkRange
  week: string | null
  conversationId: string | null
  turnId: string | null
  workItemId: string | null
  handoffId: string | null
}

const surfaces = new Set<ClassTeacherSurface>(['home', 'calendar', 'affairs', 'students'])
const panels = new Set<StudentPanel>(['directory', 'support', 'academic'])
const ranges = new Set<WorkRange>(['today', 'week', 'timeline', 'all'])
const isoDate = /^\d{4}-\d{2}-\d{2}$/
const opaqueIdPattern = /^[A-Za-z0-9_-]{8,128}$/

function first(value: unknown): string | null {
  if (Array.isArray(value)) return typeof value[0] === 'string' ? value[0] : null
  return typeof value === 'string' ? value : null
}

function normalize(query: Record<string, unknown>): { state: ClassTeacherRouteState; valid: boolean; sharedReturn: boolean } {
  const rawSurface = first(query.surface)
  const rawPanel = first(query.panel)
  const rawRange = first(query.range)
  const rawWeek = first(query.week)
  const surface = surfaces.has(rawSurface as ClassTeacherSurface)
    ? rawSurface as ClassTeacherSurface
    : 'home'
  const panel = panels.has(rawPanel as StudentPanel)
    ? rawPanel as StudentPanel
    : 'directory'
  const range = ranges.has(rawRange as WorkRange)
    ? rawRange as WorkRange
    : 'week'
  const week = rawWeek && isoDate.test(rawWeek) ? rawWeek : null
  const rawConversation = first(query.conversation)
  const rawDestination = first(query.destination)
  const rawSourceTask = first(query.source_task_id)
  const rawSourceRef = first(query.source_ref)
  const rawTurn = first(query.turn)
  const rawWorkItem = first(query.work_item)
  const rawHandoff = first(query.handoff)
  const sharedReturn = rawDestination?.startsWith('class_teacher.')
    && Boolean(rawSourceTask && opaqueIdPattern.test(rawSourceTask))
    && Boolean(rawSourceRef && opaqueIdPattern.test(rawSourceRef))
  const conversationCandidate = rawConversation || (sharedReturn ? rawSourceRef : null)
  const conversationId = conversationCandidate && opaqueIdPattern.test(conversationCandidate) ? conversationCandidate : null
  const turnId = rawTurn && opaqueIdPattern.test(rawTurn) ? rawTurn : null
  const workItemId = rawWorkItem && opaqueIdPattern.test(rawWorkItem) ? rawWorkItem : null
  const handoffId = rawHandoff && opaqueIdPattern.test(rawHandoff) ? rawHandoff : null
  const allowed = new Set(['surface', 'panel', 'range', 'week', 'conversation', 'turn', 'work_item', 'handoff', 'destination', 'source_task_id', 'source_ref'])
  const unknown = Object.keys(query).some((key) => !allowed.has(key))
  const valid = !unknown
    && rawSurface === surface
    && (surface !== 'students' || rawPanel === panel)
    && (surface !== 'calendar' || rawRange === range)
    && (!rawWeek || week === rawWeek)
    && (!rawConversation || rawConversation === conversationId)
    && (!rawDestination || Boolean(sharedReturn))
    && (!rawSourceTask || Boolean(sharedReturn))
    && (!rawSourceRef || Boolean(sharedReturn))
    && (!rawTurn || rawTurn === turnId)
    && (!rawWorkItem || rawWorkItem === workItemId)
    && (!rawHandoff || rawHandoff === handoffId)
  return { state: { surface, panel, range, week, conversationId, turnId, workItemId, handoffId }, valid, sharedReturn: Boolean(sharedReturn) }
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
    if (next.conversationId) target.conversation = next.conversationId
    if (next.turnId) target.turn = next.turnId
    if (next.workItemId) target.work_item = next.workItemId
    if (next.handoffId) target.handoff = next.handoffId
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
      await commit(normalized.value.sharedReturn
        ? normalized.value.state
        : { surface: 'home', panel: 'directory', range: 'week', week: null, conversationId: null, turnId: null, workItemId: null, handoffId: null }, true)
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
