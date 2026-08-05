import {
  computed,
  onBeforeUnmount,
  readonly,
  ref,
  shallowRef,
  watch,
  type Ref,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useTeachingPrepCatalogStore } from '../stores/catalog'
import {
  teachingPrepWorkbenchApi,
  type ExerciseSuggestionRun,
  type LessonPreparationStatus,
  type ReferenceSelectionPreflight,
  type TeachingPrepStage,
  type TeachingPrepWorkspace,
  type TrustedPptxVersion,
} from '../api/workbench'

export interface WorkbenchStageState {
  id: TeachingPrepStage
  label: string
  workspace: TeachingPrepWorkspace
  state: 'current' | 'complete' | 'available' | 'blocked' | 'invalidated'
  explanation: string
}

export interface NavigationTarget {
  workspace: TeachingPrepWorkspace
  stage: TeachingPrepStage
  lessonId?: string | null
}

export interface NavigationDecision {
  allowed: boolean
  reason?: string
}

const STAGES: Array<Pick<WorkbenchStageState, 'id' | 'label' | 'workspace'>> = [
  { id: 'select', label: '选课时', workspace: 'lesson-tree' },
  { id: 'materials', label: '核资料', workspace: 'materials' },
  { id: 'plan', label: '定方案', workspace: 'lesson-prep' },
  { id: 'slides', label: '审课件', workspace: 'versions' },
  { id: 'package', label: '上课包', workspace: 'versions' },
]

const STAGE_ORDER = Object.fromEntries(
  STAGES.map((stage, index) => [stage.id, index]),
) as Record<TeachingPrepStage, number>

export function hasFormalLessonTree(
  nodes: Array<{ node_type: string; is_active: boolean }>,
): boolean {
  return nodes.some(node => node.node_type === 'lesson' && node.is_active)
}

export function useTeachingPrepWorkbench() {
  const route = useRoute()
  const router = useRouter()
  const catalog = useTeachingPrepCatalogStore()
  const workspace = ref<TeachingPrepWorkspace>('lesson-tree')
  const stage = ref<TeachingPrepStage>('select')
  const lessonStatuses = ref<LessonPreparationStatus[]>([])
  const referencePreflight = shallowRef<ReferenceSelectionPreflight | null>(null)
  const activeSuggestionRun = shallowRef<ExerciseSuggestionRun | null>(null)
  const pptxVersions = ref<TrustedPptxVersion[]>([])
  const dirtyReason = ref<string | null>(null)
  const workbenchError = ref('')
  const loading = ref(false)
  let contextGeneration = 0
  let suggestionTimer: ReturnType<typeof setTimeout> | null = null
  let executionTimer: ReturnType<typeof setTimeout> | null = null

  const selectedStatus = computed(
    () => lessonStatuses.value.find(
      item => item.lesson_node_id === catalog.selectedLessonId,
    ) ?? null,
  )

  const stages = computed<WorkbenchStageState[]>(() => {
    const reached = selectedStatus.value?.preparation_stage ?? 'select'
    const reachedIndex = STAGE_ORDER[reached]
    return STAGES.map((item) => {
      const index = STAGE_ORDER[item.id]
      const current = stage.value === item.id
      const blocker = selectedStatus.value?.blockers[0]
      return {
        ...item,
        state: current
          ? 'current'
          : index < reachedIndex
            ? 'complete'
            : index === reachedIndex
              ? 'available'
              : 'blocked',
        explanation: current
          ? '当前工作面'
          : index <= reachedIndex
            ? '可以查看或创建新版本'
            : blocker ?? '可以查看说明，完成前置条件后再执行',
      }
    })
  })

  async function load(): Promise<void> {
    loading.value = true
    workbenchError.value = ''
    try {
      await catalog.load()
      const restoreLibrary = route.query.view === 'library'
      if (restoreLibrary) {
        workspace.value = 'materials'
        stage.value = 'materials'
      } else if (!isWorkspace(route.query.workspace) && !isStage(route.query.stage)) {
        if (hasFormalLessonTree(catalog.lessonNodes)) {
          workspace.value = 'lesson-tree'
          stage.value = 'select'
        } else {
          workspace.value = 'materials'
          stage.value = 'materials'
        }
      }
      if (!restoreLibrary) restoreRouteContext()
      await loadSemesterStatuses()
      const lessonId = route.query.lesson
      if (typeof lessonId === 'string' && lessonId) {
        const lesson = catalog.lessonNodes.find(item => item.id === lessonId)
        if (lesson) await catalog.selectLesson(lesson)
      }
      if (!catalog.selectedLessonId) {
        const firstLesson = catalog.lessonNodes.find(
          item => item.node_type === 'lesson' && item.is_active,
        )
        if (firstLesson) await catalog.selectLesson(firstLesson)
      }
      await refreshCurrentWorkspace()
      await syncRoute()
    } catch {
      workbenchError.value = catalog.errorMessage || '备课工作台暂时无法载入。'
    } finally {
      loading.value = false
    }
  }

  function restoreRouteContext(): void {
    const nextWorkspace = route.query.workspace
    const nextStage = route.query.stage
    if (isWorkspace(nextWorkspace)) workspace.value = nextWorkspace
    if (isStage(nextStage)) stage.value = nextStage
  }

  async function loadSemesterStatuses(): Promise<void> {
    const semesterId = catalog.selectedSemester?.id
    lessonStatuses.value = semesterId
      ? await teachingPrepWorkbenchApi.lessonStatuses(semesterId)
      : []
  }

  async function openLesson(lessonId: string): Promise<void> {
    const lesson = catalog.lessonNodes.find(item => item.id === lessonId)
    if (!lesson) {
      workbenchError.value = '该课时已不存在，已保留当前安全页面。'
      return
    }
    const decision = await requestNavigation({
      workspace: workspace.value,
      stage: stage.value,
      lessonId,
    })
    if (!decision.allowed) return
    contextGeneration += 1
    await catalog.selectLesson(lesson)
    await refreshCurrentWorkspace()
    await syncRoute()
  }

  async function openStage(nextStage: TeachingPrepStage): Promise<void> {
    const definition = STAGES.find(item => item.id === nextStage)
    if (!definition) return
    const decision = await requestNavigation({
      workspace: definition.workspace,
      stage: nextStage,
      lessonId: catalog.selectedLessonId,
    })
    if (!decision.allowed) return
    workspace.value = definition.workspace
    stage.value = nextStage
    await refreshCurrentWorkspace()
    await syncRoute()
    requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('[data-workbench-title]')?.focus()
    })
  }

  async function openWorkspace(nextWorkspace: TeachingPrepWorkspace): Promise<void> {
    const fallbackStage: Record<TeachingPrepWorkspace, TeachingPrepStage> = {
      'lesson-tree': 'select',
      materials: 'materials',
      'lesson-prep': 'plan',
      versions: stage.value === 'package' ? 'package' : 'slides',
    }
    await openStage(fallbackStage[nextWorkspace])
  }

  async function requestNavigation(
    _target: NavigationTarget,
  ): Promise<NavigationDecision> {
    void _target
    if (!dirtyReason.value) return { allowed: true }
    const allowed = globalThis.confirm(
      `${dirtyReason.value}尚未保存。离开后本次编辑不会写入版本，是否继续？`,
    )
    if (allowed) dirtyReason.value = null
    return allowed
      ? { allowed: true }
      : { allowed: false, reason: '存在未保存内容' }
  }

  async function refreshCurrentWorkspace(): Promise<void> {
    const generation = ++contextGeneration
    workbenchError.value = ''
    try {
      await loadSemesterStatuses()
      const lessonId = catalog.selectedLessonId
      if (!lessonId) {
        referencePreflight.value = null
        pptxVersions.value = []
        return
      }
      if (workspace.value === 'lesson-prep' || stage.value === 'materials') {
        const next = await teachingPrepWorkbenchApi.referencePreflight(lessonId)
        if (generation === contextGeneration) referencePreflight.value = next
      }
      if (workspace.value === 'versions') {
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

  function setDirty(reason: string | null): void {
    dirtyReason.value = reason
  }

  function watchSuggestionRun(run: ExerciseSuggestionRun | null): void {
    activeSuggestionRun.value = run
    if (suggestionTimer) clearTimeout(suggestionTimer)
    if (!run || run.status !== 'running') return
    suggestionTimer = setTimeout(async () => {
      try {
        const next = await teachingPrepWorkbenchApi.exerciseSuggestionRun(run.id)
        watchSuggestionRun(next)
      } catch {
        suggestionTimer = setTimeout(() => watchSuggestionRun(run), 2000)
      }
    }, 1000)
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
      await refreshCurrentWorkspace()
    }, 1200)
  }

  async function syncRoute(): Promise<void> {
    await router.replace({
      query: {
        ...route.query,
        workspace: workspace.value,
        stage: stage.value,
        lesson: catalog.selectedLessonId ?? undefined,
      },
    })
  }

  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!dirtyReason.value) return
    event.preventDefault()
  }
  globalThis.addEventListener('beforeunload', beforeUnload)

  watch(
    () => [route.query.workspace, route.query.stage, route.query.view],
    () => {
      if (route.query.view === 'library') {
        workspace.value = 'materials'
        stage.value = 'materials'
        return
      }
      restoreRouteContext()
    },
  )

  onBeforeUnmount(() => {
    globalThis.removeEventListener('beforeunload', beforeUnload)
    if (suggestionTimer) clearTimeout(suggestionTimer)
    if (executionTimer) clearTimeout(executionTimer)
  })

  return {
    catalog,
    workspace: readonly(workspace) as Readonly<Ref<TeachingPrepWorkspace>>,
    stage: readonly(stage) as Readonly<Ref<TeachingPrepStage>>,
    stages,
    lessonStatuses,
    selectedStatus,
    referencePreflight,
    activeSuggestionRun,
    pptxVersions,
    dirtyReason,
    workbenchError,
    loading,
    load,
    openLesson,
    openStage,
    openWorkspace,
    requestNavigation,
    refreshCurrentWorkspace,
    setDirty,
    watchSuggestionRun,
  }
}

export type TeachingPrepWorkbench = ReturnType<typeof useTeachingPrepWorkbench>

function isStage(value: unknown): value is TeachingPrepStage {
  return typeof value === 'string' && value in STAGE_ORDER
}

function isWorkspace(value: unknown): value is TeachingPrepWorkspace {
  return [
    'lesson-tree',
    'materials',
    'lesson-prep',
    'versions',
  ].includes(String(value))
}
