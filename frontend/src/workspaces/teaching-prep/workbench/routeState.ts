import { computed, type ComputedRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export type TeachingPrepView = 'overview' | 'library' | 'lesson'
export type TeachingPrepLessonStep = 1 | 2

export interface TeachingPrepRouteState {
  currentView: ComputedRef<TeachingPrepView>
  currentLessonId: ComputedRef<string | null>
  currentStep: ComputedRef<TeachingPrepLessonStep>
  openOverview(): Promise<void>
  openLibrary(): Promise<void>
  openLesson(lessonId: string, step?: TeachingPrepLessonStep): Promise<void>
  setStep(step: TeachingPrepLessonStep): Promise<void>
}

function normalizeView(value: unknown): TeachingPrepView {
  return value === 'library' || value === 'lesson' ? value : 'overview'
}

function normalizeStep(value: unknown): TeachingPrepLessonStep {
  return Number(value) === 2 ? 2 : 1
}

/**
 * 备课工作台的唯一导航状态：路由 query 中的 view / lesson / step 三维。
 * 页面切换一律通过改写 query 完成，不再维护额外的内存导航机。
 */
export function useTeachingPrepRouteState(): TeachingPrepRouteState {
  const route = useRoute()
  const router = useRouter()

  const currentView = computed(() => normalizeView(route.query.view))
  const currentLessonId = computed(() => (
    currentView.value === 'lesson' && typeof route.query.lesson === 'string' && route.query.lesson
      ? route.query.lesson
      : null
  ))
  const currentStep = computed(() => normalizeStep(route.query.step))

  async function replaceQuery(patch: Record<string, string | undefined>): Promise<void> {
    await router.replace({
      query: {
        ...route.query,
        ...patch,
      },
    })
  }

  async function openOverview(): Promise<void> {
    await replaceQuery({ view: 'overview', lesson: undefined, step: undefined })
  }

  async function openLibrary(): Promise<void> {
    await replaceQuery({ view: 'library', lesson: undefined, step: undefined })
  }

  async function openLesson(lessonId: string, step: TeachingPrepLessonStep = 1): Promise<void> {
    await replaceQuery({ view: 'lesson', lesson: lessonId, step: String(step) })
  }

  async function setStep(step: TeachingPrepLessonStep): Promise<void> {
    if (currentView.value !== 'lesson') return
    await replaceQuery({ step: String(step) })
  }

  return {
    currentView,
    currentLessonId,
    currentStep,
    openOverview,
    openLibrary,
    openLesson,
    setStep,
  }
}
