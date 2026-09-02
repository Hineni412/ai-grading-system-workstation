import {
  computed,
  onBeforeUnmount,
  ref,
  watch,
} from 'vue'
import { useRouter } from 'vue-router'

import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { useLessonStatusFeed } from './lessonStatus'
import { useTeachingPrepRouteStateContext } from './routeContext'

/**
 * 课时页的课时级工作上下文：准备状态与未保存守护。
 * 由 LessonPage 创建并 provide 给两个步骤组件（课件与选题、对照与导出）。
 */
export function useTeachingPrepLessonWorkbench() {
  const routeState = useTeachingPrepRouteStateContext()
  const router = useRouter()
  const catalog = useTeachingPrepCatalogStore()
  const statusFeed = useLessonStatusFeed()
  const worksheetRequested = ref(false)
  const dirtyReason = ref<string | null>(null)
  const workbenchError = ref('')
  const loading = ref(false)
  let disposed = false

  const lessonStatuses = statusFeed.lessonStatuses

  const selectedLesson = computed(() => {
    const lessonId = routeState.currentLessonId.value
    return catalog.lessonNodes.find(item => item.id === lessonId) ?? null
  })
  const selectedStatus = computed(() => {
    const lessonId = routeState.currentLessonId.value
    return lessonId ? statusFeed.statusFor(lessonId) : null
  })

  async function load(): Promise<void> {
    const lessonId = routeState.currentLessonId.value
    if (!lessonId) return
    loading.value = true
    workbenchError.value = ''
    try {
      const lesson = catalog.lessonNodes.find(item => item.id === lessonId && item.is_active)
      if (!lesson) {
        workbenchError.value = '原链接中的课时已不存在或已停用，已保留在备课首页；没有改选其他课时。'
        await routeState.openOverview()
        return
      }
      if (catalog.selectedLessonId !== lesson.id) await catalog.selectLesson(lesson)
      await refresh()
    } catch {
      workbenchError.value = catalog.errorMessage || '课时工作面暂时无法载入。'
    } finally {
      loading.value = false
    }
  }

  async function refresh(): Promise<void> {
    workbenchError.value = ''
    try {
      await statusFeed.reload()
    } catch {
      workbenchError.value = '当前工作面状态暂时无法刷新，已保留已保存内容。'
    }
  }

  function setDirty(reason: string | null): void {
    dirtyReason.value = reason
  }

  async function requestLeave(): Promise<boolean> {
    if (!dirtyReason.value) return true
    const allowed = globalThis.confirm(
      `${dirtyReason.value}尚未保存。离开后本次编辑不会写入版本，是否继续？`,
    )
    if (allowed) dirtyReason.value = null
    return allowed
  }

  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!dirtyReason.value) return
    event.preventDefault()
  }
  globalThis.addEventListener('beforeunload', beforeUnload)

  const removeNavigationGuard = router.beforeEach(async (to, from) => {
    if (disposed || !dirtyReason.value || to.fullPath === from.fullPath) return true
    return requestLeave()
  })

  watch(
    () => [routeState.currentLessonId.value, routeState.currentStep.value] as const,
    (next, prev) => {
      if (next[0] !== prev?.[0]) worksheetRequested.value = false
      void load()
    },
  )

  onBeforeUnmount(() => {
    disposed = true
    globalThis.removeEventListener('beforeunload', beforeUnload)
    removeNavigationGuard()
  })

  return {
    catalog,
    routeState,
    lessonStatuses,
    selectedLesson,
    selectedStatus,
    worksheetRequested,
    dirtyReason,
    workbenchError,
    loading,
    load,
    refresh,
    setDirty,
    requestLeave,
  }
}

export type TeachingPrepLessonWorkbench = ReturnType<typeof useTeachingPrepLessonWorkbench>
