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

export type TeachingPrepView = 'overview' | 'lesson' | 'library'
export type TeachingPrepPanel = 'sources' | 'exercises' | 'plan' | 'slides' | 'package'
export type TeachingPrepPane = 'source' | 'preview' | 'review'

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
  { id: 'materials', label: '核资料', workspace: 'lesson-prep' },
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
  const view = ref<TeachingPrepView>('overview')
  const workspace = ref<TeachingPrepWorkspace>('lesson-tree')
  const stage = ref<TeachingPrepStage>('select')
  const panel = ref<TeachingPrepPanel>('sources')
  const focusRef = ref<string | null>(null)
  const pane = ref<TeachingPrepPane>('preview')
  const lessonStatuses = ref<LessonPreparationStatus[]>([])
  const referencePreflight = shallowRef<ReferenceSelectionPreflight | null>(null)
  const activeSuggestionRun = shallowRef<ExerciseSuggestionRun | null>(null)
  const pptxVersions = ref<TrustedPptxVersion[]>([])
  const dirtyReason = ref<string | null>(null)
  const workbenchError = ref('')
  const loading = ref(false)
  let contextGeneration = 0
  let disposed = false
  let executionTimer: ReturnType<typeof setTimeout> | null = null
  let applyingRoute = false
  let routeApplicationQueued = false

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
      const linkedSemesterId = typeof route.query.semester === 'string'
        ? route.query.semester
        : null
      if (
        linkedSemesterId
        && catalog.semesters.some(item => item.id === linkedSemesterId)
        && catalog.selectedSemester?.id !== linkedSemesterId
      ) {
        await catalog.selectSemester(linkedSemesterId)
      }
      await loadSemesterStatuses()
      const restoreLibrary = route.query.view === 'library'
      if (restoreLibrary) {
        view.value = 'library'
        workspace.value = 'materials'
        stage.value = 'materials'
      } else if (route.query.view === 'lesson') {
        await restoreLessonDeepLink()
      } else if (isWorkspace(route.query.workspace)) {
        if (route.query.workspace === 'materials') {
          view.value = 'library'
          restoreRouteContext()
        } else if (route.query.workspace === 'lesson-tree') {
          view.value = 'overview'
          restoreRouteContext()
        } else {
          await restoreLessonDeepLink()
        }
      } else if (!isWorkspace(route.query.workspace) && !isStage(route.query.stage)) {
        view.value = 'overview'
        workspace.value = 'lesson-tree'
        stage.value = 'select'
      }
      const routeNotice = workbenchError.value
      await refreshCurrentWorkspace()
      if (routeNotice) workbenchError.value = routeNotice
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
    if (isPanel(route.query.panel)) panel.value = route.query.panel
    focusRef.value = safeFocusRef(route.query.focus_ref)
  }

  async function restoreLessonDeepLink(): Promise<void> {
    const semester = route.query.semester
    if (typeof semester === 'string' && semester) {
      const targetSemester = catalog.semesters.find(item => item.id === semester)
      if (!targetSemester) {
        returnToOverviewForInvalidDeepLink()
        return
      }
      if (catalog.selectedSemester?.id !== targetSemester.id) {
        await catalog.selectSemester(targetSemester.id)
        await loadSemesterStatuses()
      }
    }
    const lessonId = route.query.lesson
    const lesson = typeof lessonId === 'string'
      ? catalog.lessonNodes.find(item => item.id === lessonId && item.is_active)
      : null
    if (
      !lesson
    ) {
      returnToOverviewForInvalidDeepLink()
      return
    }
    view.value = 'lesson'
    restoreRouteContext()
    if (stage.value === 'select') stage.value = 'materials'
    const definition = STAGES.find(item => item.id === stage.value)
    workspace.value = definition?.workspace ?? 'lesson-prep'
    if (catalog.selectedLessonId !== lesson.id) await catalog.selectLesson(lesson)
  }

  function returnToOverviewForInvalidDeepLink(): void {
    view.value = 'overview'
    workspace.value = 'lesson-tree'
    stage.value = 'select'
    panel.value = 'sources'
    focusRef.value = null
    workbenchError.value = '原链接中的学期或课时已不存在，已返回备课首页；没有改选其他课时。'
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
    const lessonStatus = lessonStatuses.value.find(item => item.lesson_node_id === lessonId)
    const nextStage = lessonStatus?.preparation_stage === 'select'
      ? 'materials'
      : lessonStatus?.preparation_stage ?? 'materials'
    view.value = 'lesson'
    const definition = STAGES.find(item => item.id === nextStage)
    if (definition) {
      workspace.value = definition.workspace
      stage.value = nextStage
    }
    await refreshCurrentWorkspace()
    await syncRoute()
  }

  async function openStage(
    nextStage: TeachingPrepStage,
    options: { panel?: TeachingPrepPanel; focusRef?: string | null } = {},
  ): Promise<void> {
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
    view.value = nextStage === 'select' ? 'overview' : 'lesson'
    panel.value = options.panel ?? defaultPanel(nextStage)
    focusRef.value = options.focusRef ?? null
    await refreshCurrentWorkspace()
    await syncRoute()
    requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('[data-workbench-title]')?.focus()
    })
  }

  async function openWorkspace(nextWorkspace: TeachingPrepWorkspace): Promise<void> {
    if (nextWorkspace === 'materials') {
      const decision = await requestNavigation({ workspace: 'materials', stage: 'materials' })
      if (!decision.allowed) return
      view.value = 'library'
      workspace.value = 'materials'
      stage.value = 'materials'
      panel.value = 'sources'
      focusRef.value = null
      await syncRoute()
      return
    }
    const fallbackStage: Record<TeachingPrepWorkspace, TeachingPrepStage> = {
      'lesson-tree': 'select',
      materials: 'materials',
      'lesson-prep': 'plan',
      versions: stage.value === 'package' ? 'package' : 'slides',
    }
    await openStage(fallbackStage[nextWorkspace])
  }

  async function openPanel(nextPanel: TeachingPrepPanel, nextFocusRef: string | null = null): Promise<void> {
    const nextStage: TeachingPrepStage = nextPanel === 'exercises' || nextPanel === 'sources'
      ? 'materials'
      : nextPanel
    await openStage(nextStage, { panel: nextPanel, focusRef: nextFocusRef })
  }

  function setPane(nextPane: TeachingPrepPane): void {
    pane.value = nextPane
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
    if (disposed) return
    await router.replace({
      query: {
        ...route.query,
        view: view.value,
        workspace: workspace.value,
        stage: stage.value,
        semester: catalog.selectedSemester?.id ?? undefined,
        lesson: view.value === 'lesson' ? catalog.selectedLessonId ?? undefined : undefined,
        panel: view.value === 'lesson' ? panel.value : undefined,
        focus_ref: view.value === 'lesson' ? focusRef.value ?? undefined : undefined,
      },
    })
  }

  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!dirtyReason.value) return
    event.preventDefault()
  }
  globalThis.addEventListener('beforeunload', beforeUnload)

  const removeNavigationGuard = router.beforeEach(async (to, from) => {
    if (disposed || !dirtyReason.value || to.fullPath === from.fullPath) return true
    return (await requestNavigation({
      workspace: workspace.value,
      stage: stage.value,
      lessonId: catalog.selectedLessonId,
    })).allowed
  })

  async function selectRouteSemester(): Promise<void> {
    const semesterId = route.query.semester
    if (typeof semesterId !== 'string' || !semesterId) return
    const targetSemester = catalog.semesters.find(item => item.id === semesterId)
    if (!targetSemester || catalog.selectedSemester?.id === targetSemester.id) return
    await catalog.selectSemester(targetSemester.id)
    await loadSemesterStatuses()
  }

  async function applyCurrentRoute(): Promise<void> {
    const requestedPath = route.fullPath
    if (route.query.view === 'library' || route.query.view === 'overview') {
      await selectRouteSemester()
      if (route.fullPath !== requestedPath) {
        routeApplicationQueued = true
        return
      }
    }
    if (route.query.view === 'library') {
      view.value = 'library'
      workspace.value = 'materials'
      stage.value = 'materials'
      panel.value = 'sources'
      focusRef.value = null
      if (route.query.workspace !== 'materials' || route.query.stage !== 'materials') await syncRoute()
      return
    }
    if (route.query.view === 'overview') {
      view.value = 'overview'
      workspace.value = 'lesson-tree'
      stage.value = 'select'
      panel.value = 'sources'
      focusRef.value = null
      if (route.query.workspace !== 'lesson-tree' || route.query.stage !== 'select') await syncRoute()
      return
    }
    if (route.query.view === 'lesson') {
      await restoreLessonDeepLink()
      await refreshCurrentWorkspace()
    } else restoreRouteContext()
  }

  async function applyRoute(): Promise<void> {
    if (applyingRoute) {
      routeApplicationQueued = true
      return
    }
    applyingRoute = true
    try {
      do {
        routeApplicationQueued = false
        await applyCurrentRoute()
      } while (routeApplicationQueued && !disposed)
    } finally {
      applyingRoute = false
    }
  }

  watch(
    () => [route.query.workspace, route.query.stage, route.query.view, route.query.semester, route.query.lesson, route.query.panel, route.query.focus_ref],
    () => {
      void applyRoute()
    },
  )

  onBeforeUnmount(() => {
    disposed = true
    globalThis.removeEventListener('beforeunload', beforeUnload)
    if (executionTimer) clearTimeout(executionTimer)
    removeNavigationGuard()
  })

  return {
    catalog,
    view: readonly(view) as Readonly<Ref<TeachingPrepView>>,
    workspace: readonly(workspace) as Readonly<Ref<TeachingPrepWorkspace>>,
    stage: readonly(stage) as Readonly<Ref<TeachingPrepStage>>,
    panel: readonly(panel) as Readonly<Ref<TeachingPrepPanel>>,
    focusRef: readonly(focusRef) as Readonly<Ref<string | null>>,
    pane: readonly(pane) as Readonly<Ref<TeachingPrepPane>>,
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
    openPanel,
    setPane,
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

function isPanel(value: unknown): value is TeachingPrepPanel {
  return ['sources', 'exercises', 'plan', 'slides', 'package'].includes(String(value))
}

function safeFocusRef(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z0-9._:-]{1,160}$/.test(value) ? value : null
}

function defaultPanel(stage: TeachingPrepStage): TeachingPrepPanel {
  return stage === 'materials' ? 'sources'
    : stage === 'select' ? 'sources'
      : stage
}
