import { inject, type InjectionKey } from 'vue'

import type { TeachingPrepWorkbench } from './state'

export const teachingPrepWorkbenchKey: InjectionKey<TeachingPrepWorkbench> =
  Symbol('teaching-prep-workbench')

export function useTeachingPrepWorkbenchContext(): TeachingPrepWorkbench {
  const workbench = inject(teachingPrepWorkbenchKey)
  if (!workbench) throw new Error('Teaching prep workbench is unavailable')
  return workbench
}
