import { inject, type InjectionKey } from 'vue'

import type { TeachingPrepRouteState } from './routeState'
import type { TeachingPrepLessonWorkbench } from './lessonWorkbench'

export const teachingPrepRouteStateKey: InjectionKey<TeachingPrepRouteState> =
  Symbol('teaching-prep-route-state')

export const teachingPrepLessonWorkbenchKey: InjectionKey<TeachingPrepLessonWorkbench> =
  Symbol('teaching-prep-lesson-workbench')

export function useTeachingPrepRouteStateContext(): TeachingPrepRouteState {
  const routeState = inject(teachingPrepRouteStateKey)
  if (!routeState) throw new Error('Teaching prep route state is unavailable')
  return routeState
}

export function useTeachingPrepLessonWorkbenchContext(): TeachingPrepLessonWorkbench {
  const workbench = inject(teachingPrepLessonWorkbenchKey)
  if (!workbench) throw new Error('Teaching prep lesson workbench is unavailable')
  return workbench
}
