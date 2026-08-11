import {
  computed,
  onBeforeUnmount,
  ref,
  shallowRef,
  watch,
} from 'vue'
import { useRouter } from 'vue-router'

import { useTeachingPrepCatalogStore } from '../stores/catalog'
import {
  teachingPrepWorkbenchApi,
  type ReferenceSelectionPreflight,
  type TrustedPptxVersion,
} from '../api/workbench'
import { useLessonStatusFeed } from './lessonStatus'
import { useTeachingPrepRouteStateContext } from './routeContext'

/**
 * 课时页的课时级工作上下文：准备状态、资料预检、PPTX 版本与未保存守护。
 * 由 LessonPage 创建并 provide 给三个步骤组件；替代旧六维导航机的数据面。
 */
export function useTeachingPrepLessonWorkbench() {
  const routeState = useTeachingPrepRouteStateContext()
  const router = useRouter()
  const catalog = useTeachingPrepCatalogStore()
  const statusFeed = useLessonStatusFeed()
  const referencePreflight = shallowRef<ReferenceSelectionPreflight | null>(null)
  const pptxVersions = ref<TrustedPptxVersion[]>([])
  const dirtyReason = ref<string | null>(null)
  const workbenchError = ref('')
  const loading = ref(false)
  let contextGeneration = 0
  let disposed = false
  let executionTimer: ReturnType<typeof setTimeout> | null = null

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
    const generation = ++contextGeneration
    workbenchError.value = ''
    try {
      await statusFeed.reload()
      const lessonId = routeState.currentLessonId.value
      if (!lessonId) {
        referencePreflight.value = null
        pptxVersions.value = []
        return
      }
      const step = routeState.currentStep.value
      if (step === 1) {
        const next = await teachingPrepWorkbenchApi.referencePreflight(lessonId)
        if (generation === contextGeneration) referencePreflight.value = next
      }
      if (step === 3) {
        const next = await teachingPrepWorkbenchApi.pptxVersions(lessonId)
        if (generation === contextGeneration) pptxVersions.value = next
        scheduleExecutionRefresh()
      }
    } catch {
      if (generation === contextGeneration) {
        workbenchError.value = '当前工作面状态暂时无法刷新，已保留已保存内容。'
      }
    }
  }

  function scheduleExecutionRefresh(): void {
    if (executionTimer) clearTimeout(executionTimer)
    const running = catalog.pptxExecutions.some(
      item => ['running', 'verifying', 'publishing'].includes(item.status),
    )
    if (!running || !catalog.selectedSlidePlanId) return
    executionTimer = setTimeout(async () => {
      const plan = catalog.slidePlans.find(
        item => item.id === catalog.selectedSlidePlanId,
      )
      if (plan) await catalog.selectSlidePlan(plan)
      await refresh()
    }, 1200)
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
    () => { void load() },
  )

  onBeforeUnmount(() => {
    disposed = true
    globalThis.removeEventListener('beforeunload', beforeUnload)
    if (executionTimer) clearTimeout(executionTimer)
    removeNavigationGuard()
  })

  return {
    catalog,
    routeState,
    lessonStatuses,
    selectedLesson,
    selectedStatus,
    referencePreflight,
    pptxVersions,
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
