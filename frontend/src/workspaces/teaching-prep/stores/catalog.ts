import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../../../api/errors'
import {
  jobApi,
  TERMINAL_JOB_STATUSES,
  type JobResponse,
} from '../../../api/jobs'
import {
  DEFAULT_JOB_POLL_INTERVAL_MS,
  DEFAULT_JOB_MAX_BACKOFF_MS,
  jobPollDelay,
} from '../../../stores/jobs'
import {
  teachingPrepCatalogApi,
  type CreateCurriculumInput,
  type CreateLessonNodeInput,
  type CurriculumEdition,
  type CreateSemesterInput,
  type FreezeResourcePackInput,
  type LessonNode,
  type LessonDraft,
  type LessonDraftPayload,
  type LessonDraftPreflight,
  type SlideOperationReviewInput,
  type SlidePlan,
  type SlidePlanPreview,
  type MaterialLink,
  type MaterialDeletionPreview,
  type MaterialUnit,
  type MaterialVersion,
  type DeleteMaterialSourceResult,
  type ReferencePptCollection,
  type ReferencePptCollectionMember,
  type ResourcePack,
  type ResourcePackStatus,
  type SemesterLessonProgress,
  type SemesterLessonProgressStatus,
  type SemesterMaterialMappingStatus,
  type SemesterMaterialRecord,
  type SemesterMaterialRole,
  type SemesterStatus,
  type TeachingSemester,
  type TeachingPrepModuleStatus,
  type TeachingPreferences,
} from '../api/catalog'

export type CatalogLoadState = 'idle' | 'loading' | 'ready' | 'error'
export type CatalogLoadScope = 'overview' | 'materials'

/* ===== 资料库（方案 C）货架与进度模型 ===== */

export interface LibraryChapterFile {
  recordId: string
  materialVersionId: string
  name: string
  relativePath: string
  unitCount: number
  parsing: boolean
  needsContinue: boolean
}

export interface LibraryChapterFolder {
  key: string
  name: string
  collectionId: string
  collectionName: string
  collectionActive: boolean
  files: LibraryChapterFile[]
  totalPages: number
}

export type LessonTreeShelfState = 'active' | 'empty'

export interface LibraryProgressStep {
  key: 'import' | 'parse' | 'tree' | 'ready'
  label: string
  hint: string
  done: boolean
}



function safeMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === 'conflict') {
    return '内容已在其他页面更新，请刷新后继续。'
  }
  if (error instanceof Error && !(error instanceof ApiError) && error.message.trim()) {
    return error.message
  }
  return '备课资料暂时无法读取，请保留当前内容后重试。'
}

function waitForNextJobPoll(delay: number): Promise<void> {
  return new Promise(resolve => globalThis.setTimeout(resolve, delay))
}

function isRetryableJobPollError(error: unknown): boolean {
  return error instanceof ApiError
    && error.retryable
    && (error.kind === 'network' || error.kind === 'server')
}


export const useTeachingPrepCatalogStore = defineStore(
  'teaching-prep-catalog',
  () => {
    const curricula = ref<CurriculumEdition[]>([])
    const semesters = ref<TeachingSemester[]>([])
    const selectedCurriculumId = ref<string | null>(null)
    const selectedSemesterId = ref<string | null>(null)
    const lessonNodes = ref<LessonNode[]>([])
    const materials = ref<MaterialVersion[]>([])
    const semesterLessonProgress = ref<SemesterLessonProgress[]>([])
    const semesterMaterials = ref<SemesterMaterialRecord[]>([])
    const allSemesterMaterialRecords = ref<SemesterMaterialRecord[]>([])
    const referencePptCollections = ref<ReferencePptCollection[]>([])
    const selectedLessonId = ref<string | null>(null)
    const selectedMaterialId = ref<string | null>(null)
    const materialUnits = ref<MaterialUnit[]>([])
    const materialParseJobs = ref<Record<string, JobResponse>>({})
    const materialLinks = ref<MaterialLink[]>([])
    const resourcePacks = ref<ResourcePack[]>([])
    const resourcePackStatus = ref<ResourcePackStatus | null>(null)
    const selectedResourcePackId = ref<string | null>(null)
    const lessonDrafts = ref<LessonDraft[]>([])
    const lessonDraftPreflight = ref<LessonDraftPreflight | null>(null)
    const selectedLessonDraftId = ref<string | null>(null)
    const slidePlans = ref<SlidePlan[]>([])
    const selectedSlidePlanId = ref<string | null>(null)
    const slidePlanPreview = ref<SlidePlanPreview | null>(null)
    const moduleStatus = ref<TeachingPrepModuleStatus | null>(null)
    const teachingPreferences = ref<TeachingPreferences | null>(null)
    const loadState = ref<CatalogLoadState>('idle')
    const saveState = ref<'idle' | 'saving'>('idle')
    const errorMessage = ref('')
    let loadController: AbortController | null = null
    let lessonFlowGeneration = 0
    let materialFlowGeneration = 0
    let materialContextGeneration = 0
    let globalMaterialDataLoaded = false
    let loadedMaterialContextKey: string | null = null
    let materialContextLoad: { key: string; promise: Promise<void> } | null = null
    const materialParsePolls = new Map<number, Promise<JobResponse>>()

    const selectedCurriculum = computed(
      () => curricula.value.find(
        ({ id }) => id === selectedCurriculumId.value,
      ) ?? null,
    )
    const selectedSemester = computed(
      () => semesters.value.find(item => (
        item.id === selectedSemesterId.value
        && item.curriculum_id === selectedCurriculumId.value
      )) ?? semesters.value.find(
        ({ curriculum_id: curriculumId }) => curriculumId === selectedCurriculumId.value,
      ) ?? null,
    )
    const selectedLesson = computed(
      () => lessonNodes.value.find(
        ({ id }) => id === selectedLessonId.value,
      ) ?? null,
    )
    const selectedSemesterMaterial = computed(
      () => semesterMaterials.value.find(item => (
        item.current_material_version_id === selectedMaterialId.value
      )) ?? null,
    )
    const libraryMaterialRecords = computed(() => (
      selectedSemester.value
        ? semesterMaterials.value
        : allSemesterMaterialRecords.value
    ))

    /* ===== 资料库（方案 C）：合集、章文件夹、课时树状态与进度 ===== */

    // 本学期资料记录 id → 所属课件文件夹（合集）；散装资料与已停用合集不在映射中
    const collectionByRecordId = computed(() => {
      const map = new Map<string, ReferencePptCollection>()
      for (const collection of referencePptCollections.value) {
        if (collection.is_active === false) continue
        for (const member of collection.members) {
          if (!map.has(member.material_record_id)) {
            map.set(member.material_record_id, collection)
          }
        }
      }
      return map
    })

    // 章文件夹组：合集成员按 relative_path 首段分组，份数/页数经记录与版本回填
    const libraryChapterFolders = computed<LibraryChapterFolder[]>(() => {
      const folders: LibraryChapterFolder[] = []
      for (const collection of referencePptCollections.value) {
        const grouped = new Map<string, ReferencePptCollectionMember[]>()
        for (const member of collection.members) {
          const parts = member.relative_path.split('/').filter(Boolean)
          const name = parts.length > 1 ? parts[0]! : '未分章课件'
          grouped.set(name, [...(grouped.get(name) ?? []), member])
        }
        for (const [name, members] of grouped) {
          const files = members.map((member) => {
            const record = semesterMaterials.value.find(
              item => item.id === member.material_record_id,
            )
            const version = record
              ? materials.value.find(
                  item => item.id === record.current_material_version_id,
                ) ?? null
              : null
            const job = version ? materialParseJobs.value[version.id] : null
            return {
              recordId: member.material_record_id,
              materialVersionId: record?.current_material_version_id ?? '',
              name: version?.display_name ?? record?.display_name ?? member.normalized_title,
              relativePath: member.relative_path,
              unitCount: version?.unit_count ?? record?.current_unit_count ?? 0,
              parsing: Boolean(job && ['queued', 'running'].includes(job.status)),
              needsContinue: Boolean(record) && (
                record!.parse_status !== 'parsed' || record!.has_unparsed_update
              ),
            }
          })
          folders.push({
            key: `${collection.id}:${name}`,
            name,
            collectionId: collection.id,
            collectionName: collection.display_name,
            collectionActive: collection.is_active !== false,
            files,
            totalPages: files.reduce((total, file) => total + file.unitCount, 0),
          })
        }
      }
      return folders
    })


    const activeLessonCount = computed(() => lessonNodes.value.filter(
      item => item.node_type === 'lesson' && item.is_active,
    ).length)

    const lessonTreeStatus = computed<{
      state: LessonTreeShelfState
      activeLessonCount: number
    }>(() => ({
      state: activeLessonCount.value > 0 ? 'active' : 'empty',
      activeLessonCount: activeLessonCount.value,
    }))


    // 五步进度条：可备课为近似组合判断（资料库视角：导入、解析、课时树、
    // 教材教辅对应都完成后即认为可以开始备课，课时级内容以备课页为准）
    const libraryProgressSteps = computed<LibraryProgressStep[]>(() => {
      const activeRecords = semesterMaterials.value.filter(item => item.is_active)
      const imported = activeRecords.length > 0
      const parseRunning = Object.values(materialParseJobs.value).some(
        job => ['queued', 'running'].includes(job.status),
      )
      const parsed = imported && !parseRunning && activeRecords.every(
        item => item.parse_status === 'parsed' && !item.has_unparsed_update,
      )
      const treeDone = activeLessonCount.value > 0
      const ready = imported && parsed && treeDone
      return [
        {
          key: 'import',
          label: '导入资料',
          hint: imported ? `${activeRecords.length} 份资料已入库` : '还没有资料',
          done: imported,
        },
        {
          key: 'parse',
          label: '本机处理',
          hint: parsed
            ? '全部处理完成'
            : parseRunning
              ? '正在逐页处理'
              : imported
                ? '有资料未处理完'
                : '等待导入',
          done: parsed,
        },
        {
          key: 'tree',
          label: '课时树',
          hint: treeDone
            ? `${activeLessonCount.value} 个课时已生效`
            : '还没有课时，可回备课首页新建',
          done: treeDone,
        },
        {
          key: 'ready',
          label: '可备课',
          hint: ready ? '资料已备好' : '完成前面的步骤后可备课',
          done: ready,
        },
      ]
    })


    let collectionsGeneration = 0
    async function refreshReferencePptCollections(): Promise<void> {
      const generation = ++collectionsGeneration
      const semester = selectedSemester.value
      if (!semester) {
        referencePptCollections.value = []
        return
      }
      try {
        const items = await teachingPrepCatalogApi.listReferencePptCollections(
          semester.id,
          { includeInactive: true },
        )
        if (generation === collectionsGeneration) referencePptCollections.value = items
      } catch {
        // 合集刷新失败不阻塞其余资料操作，界面退化为无章文件夹分组
      }
    }

    watch(
      () => selectedSemester.value?.id ?? '',
      () => { void refreshReferencePptCollections() },
      { immediate: true },
    )



    function currentMaterialContextKey(): string {
      return `${selectedCurriculumId.value ?? 'no-curriculum'}:${selectedSemester.value?.id ?? 'no-semester'}`
    }

    function applyCurrentSemesterMaterialsFromAll(): void {
      const semesterId = selectedSemester.value?.id
      semesterMaterials.value = semesterId
        ? allSemesterMaterialRecords.value.filter(item => item.semester_id === semesterId)
        : []
    }

    function replaceCurrentSemesterMaterials(next: SemesterMaterialRecord[]): void {
      const semesterId = selectedSemester.value?.id ?? selectedSemesterId.value
      semesterMaterials.value = next
      if (!semesterId) return
      allSemesterMaterialRecords.value = [
        ...allSemesterMaterialRecords.value.filter(item => item.semester_id !== semesterId),
        ...next,
      ]
    }

    async function refreshAllSemesterMaterialRecords(): Promise<{ failed: boolean }> {
      if (semesters.value.length === 0) {
        allSemesterMaterialRecords.value = []
        applyCurrentSemesterMaterialsFromAll()
        return { failed: false }
      }
      const results = await Promise.allSettled(
        semesters.value.map(item => teachingPrepCatalogApi.listSemesterMaterials(item.id)),
      )
      const records: SemesterMaterialRecord[] = []
      let failed = false
      let fulfilled = false
      for (const result of results) {
        if (result.status === 'fulfilled') {
          records.push(...result.value)
          fulfilled = true
        } else failed = true
      }
      if (!fulfilled) return { failed: true }
      allSemesterMaterialRecords.value = records
      applyCurrentSemesterMaterialsFromAll()
      return { failed }
    }

    function invalidateLoadedMaterialContext(): void {
      materialContextGeneration += 1
      loadedMaterialContextKey = null
      materialContextLoad = null
    }

    function applyMaterialParseJobs(jobs: JobResponse[]): void {
      materialParseJobs.value = Object.fromEntries(
        jobs.map(job => [String(job.payload.material_version_id), job]),
      )
    }

    function resumeMaterialParseJobs(
      materialItems: MaterialVersion[],
      jobs: JobResponse[],
    ): void {
      for (const job of jobs) {
        const materialId = String(job.payload.material_version_id ?? '')
        if (
          materialId
          && materialItems.some(item => item.id === materialId)
          && !TERMINAL_JOB_STATUSES.has(job.status)
        ) {
          void monitorMaterialParseJob(materialId, job).catch(() => {
            // The persisted Job remains visible and can be recovered on reload.
          })
        }
      }
    }

    async function ensureMaterialData(): Promise<void> {
      const key = currentMaterialContextKey()
      if (loadedMaterialContextKey === key) return
      if (materialContextLoad?.key === key) return materialContextLoad.promise

      const generation = ++materialContextGeneration
      loadState.value = 'loading'
      errorMessage.value = ''

      const promise = (async () => {
        const [globalResults, allSemesterResult] = await Promise.all([
          globalMaterialDataLoaded
            ? Promise.resolve(null)
            : Promise.allSettled([
                teachingPrepCatalogApi.listMaterials(undefined, true),
                teachingPrepCatalogApi.listMaterialParseJobs(),
              ] as const),
          refreshAllSemesterMaterialRecords(),
        ])
        if (
          generation !== materialContextGeneration
          || key !== currentMaterialContextKey()
        ) return

        const issues: string[] = []
        const materialsResult = globalResults?.[0]
        const jobsResult = globalResults?.[1]
        const nextMaterials = materialsResult?.status === 'fulfilled'
          ? materialsResult.value
          : materials.value
        const nextJobs = jobsResult?.status === 'fulfilled' ? jobsResult.value : []
        if (materialsResult?.status === 'fulfilled') materials.value = nextMaterials
        else if (globalResults) issues.push('资料列表')
        if (jobsResult?.status === 'fulfilled') applyMaterialParseJobs(nextJobs)
        else if (globalResults) issues.push('资料处理进度')
        if (globalResults) {
          globalMaterialDataLoaded = globalResults.every(
            result => result.status === 'fulfilled',
          )
        }

        if (allSemesterResult.failed) issues.push('学期资料')

        const complete = globalMaterialDataLoaded && !allSemesterResult.failed
        loadedMaterialContextKey = complete ? key : null
        loadState.value = materialsResult?.status === 'rejected' && materials.value.length === 0
          ? 'error'
          : 'ready'
        errorMessage.value = issues.length
          ? `以下信息暂时未更新：${issues.join('、')}。已经读取的资料仍可查看和管理，可稍后重新检查。`
          : ''
        resumeMaterialParseJobs(nextMaterials, nextJobs)
      })().finally(() => {
        if (materialContextLoad?.promise === promise) materialContextLoad = null
      })
      materialContextLoad = { key, promise }
      return promise
    }

    async function load(scope: CatalogLoadScope = 'materials'): Promise<void> {
      loadController?.abort()
      lessonFlowGeneration += 1
      materialFlowGeneration += 1
      invalidateLoadedMaterialContext()
      globalMaterialDataLoaded = false
      materialUnits.value = []
      const controller = new AbortController()
      loadController = controller
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const includeMaterials = scope === 'materials'
        const [initial, initialMaterialResults] = await Promise.all([
          Promise.allSettled([
            teachingPrepCatalogApi.status(controller.signal),
            teachingPrepCatalogApi.getTeachingPreferences(controller.signal),
            teachingPrepCatalogApi.listCurricula(controller.signal),
            teachingPrepCatalogApi.listSemesters(controller.signal),
          ] as const),
          includeMaterials
            ? Promise.allSettled([
                teachingPrepCatalogApi.listMaterials(controller.signal, true),
                teachingPrepCatalogApi.listMaterialParseJobs(controller.signal),
              ] as const)
            : Promise.resolve(null),
        ])
        if (controller.signal.aborted) return
        const issues: string[] = []
        const [statusResult, preferencesResult, curriculaResult, semestersResult] = initial
        if (statusResult?.status === 'fulfilled') moduleStatus.value = statusResult.value
        else issues.push('模块状态')
        if (preferencesResult?.status === 'fulfilled') teachingPreferences.value = preferencesResult.value
        else issues.push('备课偏好')
        if (curriculaResult?.status === 'fulfilled') curricula.value = curriculaResult.value
        else issues.push('教材目录')
        if (semestersResult?.status === 'fulfilled') semesters.value = semestersResult.value
        else issues.push('学期目录')
        const materialsResult = initialMaterialResults?.[0]
        const jobsResult = initialMaterialResults?.[1]
        globalMaterialDataLoaded = Boolean(
          initialMaterialResults?.every(result => result.status === 'fulfilled'),
        )
        if (materialsResult?.status === 'fulfilled') materials.value = materialsResult.value
        else if (includeMaterials) issues.push('资料列表')
        const nextCurricula = curricula.value
        const nextSemesters = semesters.value
        const nextMaterials = materials.value
        const nextMaterialParseJobs = jobsResult?.status === 'fulfilled' ? jobsResult.value : []
        if (jobsResult?.status === 'rejected') issues.push('资料处理进度')
        if (jobsResult?.status === 'fulfilled') applyMaterialParseJobs(nextMaterialParseJobs)
        const currentStillExists = nextCurricula.some(
          ({ id }) => id === selectedCurriculumId.value,
        )
        selectedCurriculumId.value = currentStillExists
          ? selectedCurriculumId.value
          : nextCurricula[0]?.id ?? null
        if (selectedCurriculumId.value !== null) {
          try {
            lessonNodes.value = await teachingPrepCatalogApi.listLessons(
              selectedCurriculumId.value,
              controller.signal,
            )
          } catch {
            issues.push('课时目录')
          }
          const semester = nextSemesters.find(item => (
            item.id === selectedSemesterId.value
            && item.curriculum_id === selectedCurriculumId.value
          )) ?? nextSemesters.find(
            ({ curriculum_id: curriculumId }) => (
              curriculumId === selectedCurriculumId.value
            ),
          )
          selectedSemesterId.value = semester?.id ?? null
          if (semester) {
            const [progressResults, allSemesterResult] = await Promise.all([
              Promise.allSettled([
                teachingPrepCatalogApi.listSemesterLessonProgress(
                  semester.id,
                  controller.signal,
                ),
              ] as const),
              includeMaterials
                ? refreshAllSemesterMaterialRecords()
                : Promise.resolve({ failed: false }),
            ])
            const [progressResult] = progressResults
            if (progressResult?.status === 'fulfilled') semesterLessonProgress.value = progressResult.value
            else issues.push('课时进度')
            if (includeMaterials && allSemesterResult.failed) issues.push('学期资料')
            if (includeMaterials) {
              if (
                initialMaterialResults?.every(result => result.status === 'fulfilled')
                && !allSemesterResult.failed
              ) loadedMaterialContextKey = currentMaterialContextKey()
            } else {
              semesterMaterials.value = []
            }
          } else {
            semesterLessonProgress.value = []
            if (includeMaterials) {
              const allSemesterResult = await refreshAllSemesterMaterialRecords()
              if (allSemesterResult.failed) issues.push('学期资料')
              if (
                initialMaterialResults?.every(result => result.status === 'fulfilled')
                && !allSemesterResult.failed
              ) loadedMaterialContextKey = currentMaterialContextKey()
            } else {
              semesterMaterials.value = []
              allSemesterMaterialRecords.value = []
            }
          }
        } else {
          selectedSemesterId.value = null
          lessonNodes.value = []
          semesterLessonProgress.value = []
          if (includeMaterials) {
            const allSemesterResult = await refreshAllSemesterMaterialRecords()
            if (allSemesterResult.failed) issues.push('学期资料')
            if (
              initialMaterialResults?.every(result => result.status === 'fulfilled')
              && !allSemesterResult.failed
            ) loadedMaterialContextKey = currentMaterialContextKey()
          } else {
            semesterMaterials.value = []
            allSemesterMaterialRecords.value = []
          }
        }
        if (!controller.signal.aborted) {
          loadState.value = materialsResult?.status === 'rejected' && materials.value.length === 0
            ? 'error'
            : 'ready'
          errorMessage.value = issues.length
            ? `以下信息暂时未更新：${[...new Set(issues)].join('、')}。已经读取的资料仍可查看和管理，可稍后重新检查。`
            : ''
          if (includeMaterials) resumeMaterialParseJobs(nextMaterials, nextMaterialParseJobs)
        }
      } catch (error) {
        if (controller.signal.aborted) return
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
      } finally {
        if (loadController === controller) loadController = null
      }
    }

    async function selectCurriculum(
      curriculumId: string,
      semesterId?: string,
      scope: CatalogLoadScope = 'materials',
    ): Promise<void> {
      const semester = semesters.value.find(item => (
        item.curriculum_id === curriculumId
        && (semesterId === undefined || item.id === semesterId)
      )) ?? semesters.value.find(item => item.curriculum_id === curriculumId) ?? null
      if (
        curriculumId === selectedCurriculumId.value
        && semester?.id === selectedSemester.value?.id
      ) {
        if (scope === 'materials') await ensureMaterialData()
        return
      }
      const generation = ++lessonFlowGeneration
      materialFlowGeneration += 1
      invalidateLoadedMaterialContext()
      selectedCurriculumId.value = curriculumId
      selectedSemesterId.value = semester?.id ?? null
      selectedLessonId.value = null
      selectedMaterialId.value = null
      materialUnits.value = []
      materialLinks.value = []
      resourcePacks.value = []
      resourcePackStatus.value = null
      semesterLessonProgress.value = []
      semesterMaterials.value = []
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const nextLessons = await teachingPrepCatalogApi.listLessons(curriculumId)
        if (
          generation !== lessonFlowGeneration
          || selectedCurriculumId.value !== curriculumId
        ) return
        lessonNodes.value = nextLessons
        if (semester) {
          const [nextProgress] = await Promise.all([
            teachingPrepCatalogApi.listSemesterLessonProgress(semester.id),
            scope === 'materials' ? ensureMaterialData() : Promise.resolve(),
          ])
          if (
            generation !== lessonFlowGeneration
            || selectedCurriculumId.value !== curriculumId
          ) return
          semesterLessonProgress.value = nextProgress
        } else if (scope === 'materials') {
          await ensureMaterialData()
        }
        if (scope === 'overview') loadState.value = 'ready'
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedCurriculumId.value !== curriculumId
        ) return
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
      }
    }

    async function selectSemester(
      semesterId: string,
      scope: CatalogLoadScope = 'materials',
    ): Promise<void> {
      const semester = semesters.value.find(item => item.id === semesterId)
      if (!semester) return
      await selectCurriculum(semester.curriculum_id, semester.id, scope)
    }

    function clearSemesterSelection(): void {
      lessonFlowGeneration += 1
      materialFlowGeneration += 1
      invalidateLoadedMaterialContext()
      selectedCurriculumId.value = null
      selectedSemesterId.value = null
      selectedLessonId.value = null
      selectedMaterialId.value = null
      lessonNodes.value = []
      materialUnits.value = []
      semesterLessonProgress.value = []
      semesterMaterials.value = []
    }


    async function selectLesson(node: LessonNode): Promise<void> {
      if (node.node_type !== 'lesson') return
      await ensureMaterialData()
      const generation = ++lessonFlowGeneration
      selectedLessonId.value = node.id
      materialLinks.value = []
      resourcePacks.value = []
      resourcePackStatus.value = null
      selectedResourcePackId.value = null
      lessonDrafts.value = []
      lessonDraftPreflight.value = null
      selectedLessonDraftId.value = null
      slidePlans.value = []
      selectedSlidePlanId.value = null
      slidePlanPreview.value = null
      errorMessage.value = ''
      try {
        const [nextLinks, nextPacks, nextPackStatus] = await Promise.all([
          teachingPrepCatalogApi.listMaterialLinks(node.id),
          teachingPrepCatalogApi.listResourcePacks(node.id),
          teachingPrepCatalogApi.getResourcePackStatus(node.id),
        ])
        const nextDrafts = nextPacks[0]
          ? await teachingPrepCatalogApi.listLessonDrafts(nextPacks[0].id)
          : []
        const confirmedDraft = nextDrafts.find(
          ({ status }) => status === 'confirmed',
        )
        const nextPlans = confirmedDraft
          ? await teachingPrepCatalogApi.listSlidePlans(confirmedDraft.id)
          : []
        const nextPreview = nextPlans[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(
            nextPlans[0].id,
          )
          : null
        if (
          generation !== lessonFlowGeneration
          || selectedLessonId.value !== node.id
        ) return
        materialLinks.value = nextLinks
        resourcePacks.value = nextPacks
        resourcePackStatus.value = nextPackStatus
        selectedResourcePackId.value = nextPacks[0]?.id ?? null
        lessonDrafts.value = nextDrafts
        selectedLessonDraftId.value = confirmedDraft?.id ?? null
        slidePlans.value = nextPlans
        selectedSlidePlanId.value = nextPlans[0]?.id ?? null
        slidePlanPreview.value = nextPreview
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedLessonId.value !== node.id
        ) return
        errorMessage.value = safeMessage(error)
      }
    }

    async function refreshMaterialCollections(): Promise<void> {
      materials.value = await teachingPrepCatalogApi.listMaterials(undefined, true)
      if (selectedSemester.value) {
        const [nextMaterials, nextSemesters] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(
            selectedSemester.value.id,
          ),
          teachingPrepCatalogApi.listSemesters(),
        ])
        replaceCurrentSemesterMaterials(nextMaterials)
        semesters.value = nextSemesters
      }
    }

    function monitorMaterialParseJob(
      materialId: string,
      initialJob: JobResponse,
    ): Promise<JobResponse> {
      const existing = materialParsePolls.get(initialJob.id)
      if (existing) return existing
      const polling = (async () => {
        let job = initialJob
        let lastPreviewRefresh = job.progress
        let retryCount = 0
        materialParseJobs.value = {
          ...materialParseJobs.value,
          [materialId]: job,
        }
        while (!TERMINAL_JOB_STATUSES.has(job.status)) {
          await waitForNextJobPoll(jobPollDelay(
            retryCount,
            DEFAULT_JOB_POLL_INTERVAL_MS,
            DEFAULT_JOB_MAX_BACKOFF_MS,
          ))
          try {
            job = await jobApi.getJob(job.id)
            retryCount = 0
          } catch (error) {
            if (!isRetryableJobPollError(error)) throw error
            retryCount += 1
            continue
          }
          materialParseJobs.value = {
            ...materialParseJobs.value,
            [materialId]: job,
          }
          if (
            selectedMaterialId.value === materialId
            && job.stage === 'preview'
            && job.progress - lastPreviewRefresh >= 0.03
          ) {
            const nextUnits = await teachingPrepCatalogApi.listMaterialUnits(
              materialId,
            )
            if (selectedMaterialId.value !== materialId) continue
            materialUnits.value = nextUnits
            lastPreviewRefresh = job.progress
          }
        }
        await refreshMaterialCollections()
        if (job.status !== 'succeeded') {
          throw new Error(
            job.status === 'cancelled'
              ? '资料解析已取消，可保留进度后重试。'
              : '资料解析没有完成，可从已生成的预览继续重试。',
          )
        }
        return job
      })().finally(() => {
        materialParsePolls.delete(initialJob.id)
      })
      materialParsePolls.set(initialJob.id, polling)
      return polling
    }

    async function parseMaterialInBackground(
      material: MaterialVersion,
      selectResult = false,
    ): Promise<JobResponse> {
      const initialJob = await teachingPrepCatalogApi.startMaterialParse(
        material.id,
      )
      materialParseJobs.value = {
        ...materialParseJobs.value,
        [material.id]: initialJob,
      }
      const job = await monitorMaterialParseJob(material.id, initialJob)
      if (selectResult && selectedMaterialId.value === material.id) {
        materialUnits.value = await teachingPrepCatalogApi.listMaterialUnits(
          material.id,
        )
      }
      return job
    }

    async function cancelMaterialParse(materialId: string): Promise<void> {
      const current = materialParseJobs.value[materialId]
      if (!current || TERMINAL_JOB_STATUSES.has(current.status)) return
      const job = await jobApi.cancelJob(current.id)
      materialParseJobs.value = {
        ...materialParseJobs.value,
        [materialId]: job,
      }
    }

    async function openMaterial(
      material: MaterialVersion,
    ): Promise<'loaded' | 'stale'> {
      const generation = ++materialFlowGeneration
      selectedMaterialId.value = material.id
      materialUnits.value = []
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const nextUnits = await teachingPrepCatalogApi.listMaterialUnits(material.id)
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return 'stale'
        materialUnits.value = nextUnits
        loadState.value = nextUnits.length > 0 ? 'ready' : 'loading'
        await refreshMaterialCollections()
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return 'stale'
        loadState.value = 'ready'
        return 'loaded'
      } catch (error) {
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return 'stale'
        materialUnits.value = []
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function refreshCurrentMaterialUnits(materialId: string): Promise<boolean> {
      if (selectedMaterialId.value !== materialId) return false
      const nextUnits = await teachingPrepCatalogApi.listMaterialUnits(materialId)
      if (selectedMaterialId.value !== materialId) return false
      materialUnits.value = nextUnits
      return true
    }

    async function importMaterialCopy(
      file: File,
      materialRole?: SemesterMaterialRole,
      workbook?: { series: string; volume: 'A' | 'B' },
    ): Promise<MaterialVersion> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const item = await teachingPrepCatalogApi.importMaterialCopy(
          file,
          `material-import-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        )
        materials.value = await teachingPrepCatalogApi.listMaterials(undefined, true)
        if (selectedSemester.value && materialRole) {
          await teachingPrepCatalogApi.attachSemesterMaterial(
            selectedSemester.value.id,
            {
              request_token: `semester-material-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
              material_version_id: item.id,
              material_role: materialRole,
              workbook_series: workbook?.series ?? null,
              workbook_volume: workbook?.volume ?? null,
            },
          )
          replaceCurrentSemesterMaterials(await (
            teachingPrepCatalogApi.listSemesterMaterials(
              selectedSemester.value.id,
            )
          ))
          semesters.value = await teachingPrepCatalogApi.listSemesters()
        }
        return item
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function relocateMaterialCopy(
      material: MaterialVersion,
      file: File,
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const item = await teachingPrepCatalogApi.relocateMaterialCopy(
          material.id,
          file,
          `material-relocate-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        )
        materials.value = await teachingPrepCatalogApi.listMaterials(undefined, true)
        await openMaterial(item)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function saveMaterialUnitLabel(
      unit: MaterialUnit,
      input: {
        title: string | null
        manualText: string
        formulaReviewRequired: boolean
      },
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const updated = await teachingPrepCatalogApi.updateMaterialUnit(
          unit.id,
          {
            expected_revision: unit.revision,
            title: input.title,
            manual_text: input.manualText,
            formula_review_required: input.formulaReviewRequired,
          },
        )
        materialUnits.value = materialUnits.value.map(
          (item) => item.id === updated.id ? updated : item,
        )
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }


    async function freezeResourcePack(
      input: Omit<FreezeResourcePackInput, 'request_token'>,
    ): Promise<void> {
      const lessonId = selectedLessonId.value
      if (lessonId === null) throw new Error('请先选择课时')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.freezeResourcePack(
          lessonId,
          {
            ...input,
            request_token: `resource-pack-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
          },
        )
        const [nextPacks, nextStatus] = await Promise.all([
          teachingPrepCatalogApi.listResourcePacks(lessonId),
          teachingPrepCatalogApi.getResourcePackStatus(lessonId),
        ])
        resourcePacks.value = nextPacks
        resourcePackStatus.value = nextStatus
        if (nextPacks[0]) {
          await selectResourcePack(nextPacks[0])
        }
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }


    async function selectResourcePack(pack: ResourcePack): Promise<void> {
      const generation = ++lessonFlowGeneration
      selectedResourcePackId.value = pack.id
      lessonDraftPreflight.value = null
      errorMessage.value = ''
      try {
        const nextDrafts = await teachingPrepCatalogApi.listLessonDrafts(
          pack.id,
        )
        const confirmedDraft = nextDrafts.find(
          ({ status }) => status === 'confirmed',
        )
        const nextPlans = confirmedDraft
          ? await teachingPrepCatalogApi.listSlidePlans(confirmedDraft.id)
          : []
        const nextPreview = nextPlans[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(
            nextPlans[0].id,
          )
          : null
        if (
          generation !== lessonFlowGeneration
          || selectedResourcePackId.value !== pack.id
        ) return
        lessonDrafts.value = nextDrafts
        selectedLessonDraftId.value = confirmedDraft?.id ?? null
        slidePlans.value = nextPlans
        selectedSlidePlanId.value = nextPlans[0]?.id ?? null
        slidePlanPreview.value = nextPreview
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedResourcePackId.value !== pack.id
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function prepareLessonDraft(
      mode: 'local_template' | 'model' = 'local_template',
    ): Promise<void> {
      const packId = selectedResourcePackId.value
      if (packId === null) throw new Error('请先选择资源包')
      const generation = lessonFlowGeneration
      errorMessage.value = ''
      try {
        const nextPreflight = await teachingPrepCatalogApi
          .getLessonDraftPreflight(packId, mode)
        if (
          generation !== lessonFlowGeneration
          || selectedResourcePackId.value !== packId
        ) return
        if (nextPreflight.resource_pack_id !== packId) {
          throw new Error('草稿生成范围与当前资源包不一致')
        }
        lessonDraftPreflight.value = nextPreflight
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedResourcePackId.value !== packId
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function generateLessonDraft(
      mode: 'local_template' | 'model' = 'local_template',
    ): Promise<void> {
      const packId = selectedResourcePackId.value
      if (packId === null) throw new Error('请先选择资源包')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.generateLessonDraft(packId, {
          operation_id: `lesson-draft-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
          mode,
          confirmed: true,
        })
        lessonDrafts.value = await teachingPrepCatalogApi.listLessonDrafts(
          packId,
        )
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function reviseLessonDraft(
      draft: LessonDraft,
      payload: LessonDraftPayload,
      confirmed: boolean,
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.reviseLessonDraft(draft.id, {
          request_token: `draft-revision-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
          payload,
          confirmed,
        })
        lessonDrafts.value = await teachingPrepCatalogApi.listLessonDrafts(
          draft.resource_pack_id,
        )
        const confirmedDraft = lessonDrafts.value.find(
          ({ status }) => status === 'confirmed',
        )
        if (confirmedDraft) {
          await selectLessonDraft(confirmedDraft)
        }
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function selectLessonDraft(draft: LessonDraft): Promise<void> {
      const generation = ++lessonFlowGeneration
      selectedLessonDraftId.value = draft.id
      selectedSlidePlanId.value = null
      slidePlanPreview.value = null
      errorMessage.value = ''
      try {
        const nextPlans = await teachingPrepCatalogApi.listSlidePlans(
          draft.id,
        )
        const nextPreview = nextPlans[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(nextPlans[0].id)
          : null
        if (
          generation !== lessonFlowGeneration
          || selectedLessonDraftId.value !== draft.id
        ) return
        slidePlans.value = nextPlans
        selectedSlidePlanId.value = nextPlans[0]?.id ?? null
        slidePlanPreview.value = nextPreview
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedLessonDraftId.value !== draft.id
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }



    async function selectSlidePlan(plan: SlidePlan): Promise<void> {
      const generation = ++lessonFlowGeneration
      selectedSlidePlanId.value = plan.id
      errorMessage.value = ''
      try {
        const preview = await teachingPrepCatalogApi.getSlidePlanPreview(plan.id)
        if (
          generation !== lessonFlowGeneration
          || selectedSlidePlanId.value !== plan.id
        ) return
        slidePlanPreview.value = preview
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedSlidePlanId.value !== plan.id
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }


    async function reviewSlidePlan(
      plan: SlidePlan,
      operationReviews: SlideOperationReviewInput[],
      options: {
        approveLowRiskDeletions?: boolean
        reviewNote?: string | null
      } = {},
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const revised = await teachingPrepCatalogApi.reviseSlidePlan(
          plan.id,
          {
            request_token: `slide-review-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
            operation_reviews: operationReviews,
            approve_low_risk_deletions:
              options.approveLowRiskDeletions ?? false,
            review_note: options.reviewNote?.trim() || null,
          },
        )
        slidePlans.value = await teachingPrepCatalogApi.listSlidePlans(
          revised.lesson_draft_id,
        )
        await selectSlidePlan(revised)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function createCurriculum(
      input: CreateCurriculumInput,
    ): Promise<CurriculumEdition> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const created = await teachingPrepCatalogApi.createCurriculum(input)
        curricula.value = [
          created,
          ...curricula.value.filter(({ id }) => id !== created.id),
        ]
        selectedCurriculumId.value = created.id
        lessonNodes.value = []
        semesterLessonProgress.value = []
        semesterMaterials.value = []
        return created
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function createSemesterWorkspace(input: {
      curriculum: Omit<CreateCurriculumInput, 'request_token'>
      semester: Omit<CreateSemesterInput, 'request_token' | 'curriculum_id'>
    }): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const workspace = await teachingPrepCatalogApi.createSemesterWorkspace({
          request_token: (
            `semester-workspace-${globalThis.crypto.randomUUID().replaceAll('-', '')}`
          ),
          curriculum: input.curriculum,
          ...input.semester,
        })
        const created = workspace.curriculum
        curricula.value = [
          created,
          ...curricula.value.filter(({ id }) => id !== created.id),
        ]
        selectedCurriculumId.value = created.id
        selectedSemesterId.value = workspace.semester.id
        lessonNodes.value = []
        semesterLessonProgress.value = []
        semesterMaterials.value = []
        semesters.value = await teachingPrepCatalogApi.listSemesters()
        const [nextLessons, nextProgress, nextMaterials] = await Promise.all([
          teachingPrepCatalogApi.listLessons(created.id),
          teachingPrepCatalogApi.listSemesterLessonProgress(workspace.semester.id),
          teachingPrepCatalogApi.listSemesterMaterials(workspace.semester.id),
        ])
        lessonNodes.value = nextLessons
        semesterLessonProgress.value = nextProgress
        replaceCurrentSemesterMaterials(nextMaterials)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function createSemester(
      input: Omit<CreateSemesterInput, 'curriculum_id'>,
    ): Promise<void> {
      const curriculumId = selectedCurriculumId.value
      if (curriculumId === null) throw new Error('请先选择教材版本')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const created = await teachingPrepCatalogApi.createSemester({
          ...input,
          curriculum_id: curriculumId,
        })
        semesters.value = await teachingPrepCatalogApi.listSemesters()
        selectedSemesterId.value = created.id
        const semester = created
        if (semester) {
          const [nextProgress, nextMaterials] = await Promise.all([
            teachingPrepCatalogApi.listSemesterLessonProgress(semester.id),
            teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          ])
          semesterLessonProgress.value = nextProgress
          replaceCurrentSemesterMaterials(nextMaterials)
        }
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function updateSemester(
      input: {
        plannedNewLessonCount: number
        status: SemesterStatus
      },
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.updateSemester(semester, {
          planned_new_lesson_count: input.plannedNewLessonCount,
          status: input.status,
        })
        semesters.value = await teachingPrepCatalogApi.listSemesters()
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function setSemesterLessonProgress(
      lessonNodeId: string,
      status: SemesterLessonProgressStatus,
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      const current = semesterLessonProgress.value.find(
        ({ lesson_node_id: nodeId }) => nodeId === lessonNodeId,
      )
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.setSemesterLessonProgress(
          semester.id,
          lessonNodeId,
          {
            status,
            expected_revision: current?.revision ?? null,
          },
        )
        ;[
          semesterLessonProgress.value,
          semesters.value,
        ] = await Promise.all([
          teachingPrepCatalogApi.listSemesterLessonProgress(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function attachSemesterMaterial(
      material: MaterialVersion,
      materialRole: SemesterMaterialRole,
      workbook?: { series: string; volume: 'A' | 'B' },
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.attachSemesterMaterial(
          semester.id,
          {
            request_token: `semester-material-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
            material_version_id: material.id,
            material_role: materialRole,
            workbook_series: workbook?.series ?? null,
            workbook_volume: workbook?.volume ?? null,
          },
        )
        const [nextMaterials, nextSemesters] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
        replaceCurrentSemesterMaterials(nextMaterials)
        semesters.value = nextSemesters
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function updateSemesterMaterial(
      record: SemesterMaterialRecord,
      input: {
        materialRole?: SemesterMaterialRole
        mappingStatus?: SemesterMaterialMappingStatus
        isActive?: boolean
        workbookSeries?: string | null
        workbookVolume?: 'A' | 'B' | null
      },
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.updateSemesterMaterial(record, {
          material_role: input.materialRole ?? record.material_role,
          mapping_status: input.mappingStatus ?? record.mapping_status,
          is_active: input.isActive ?? record.is_active,
          workbook_series: input.workbookSeries === undefined
            ? record.workbook_series ?? null
            : input.workbookSeries,
          workbook_volume: input.workbookVolume === undefined
            ? record.workbook_volume ?? null
            : input.workbookVolume,
        })
        const [nextMaterials, nextSemesters] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
        replaceCurrentSemesterMaterials(nextMaterials)
        semesters.value = nextSemesters
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function updateMaterialSource(
      material: MaterialVersion,
      input: { displayName?: string; archived?: boolean },
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.updateMaterialSource(material, {
          ...(input.displayName !== undefined ? { display_name: input.displayName } : {}),
          ...(input.archived !== undefined ? { archived: input.archived } : {}),
        })
        materials.value = await teachingPrepCatalogApi.listMaterials(undefined, true)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function getMaterialDeletionPreview(
      material: MaterialVersion,
    ): Promise<MaterialDeletionPreview> {
      errorMessage.value = ''
      try {
        return await teachingPrepCatalogApi.getMaterialDeletionPreview(material)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function refreshAfterMaterialDeletion(material: MaterialVersion): Promise<void> {
      if (selectedMaterialId.value === material.id) {
        selectedMaterialId.value = null
        materialUnits.value = []
      }
      const [nextMaterialsList, nextSemesterMaterials, nextSemesters] = await Promise.all([
        teachingPrepCatalogApi.listMaterials(undefined, true),
        selectedSemester.value
          ? teachingPrepCatalogApi.listSemesterMaterials(selectedSemester.value.id)
          : Promise.resolve([]),
        teachingPrepCatalogApi.listSemesters(),
      ])
      materials.value = nextMaterialsList
      replaceCurrentSemesterMaterials(nextSemesterMaterials)
      semesters.value = nextSemesters
      if (!selectedSemester.value) await refreshAllSemesterMaterialRecords()
    }

    async function deleteMaterialSource(
      material: MaterialVersion,
      input: {
        operation_id: string
        preview_version: string
        confirmation_phrase: string
      },
    ): Promise<DeleteMaterialSourceResult> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const result = await teachingPrepCatalogApi.deleteMaterialSource(material, input)
        if (result.status === 'succeeded') await refreshAfterMaterialDeletion(material)
        return result
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function getMaterialDeletionStatus(
      operationId: string,
      material: MaterialVersion,
    ): Promise<DeleteMaterialSourceResult> {
      errorMessage.value = ''
      try {
        const result = await teachingPrepCatalogApi.getMaterialDeletionStatus(operationId)
        if (result.status === 'succeeded') await refreshAfterMaterialDeletion(material)
        return result
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      }
    }


    async function createLesson(input: CreateLessonNodeInput): Promise<void> {
      const curriculumId = selectedCurriculumId.value
      if (curriculumId === null) throw new Error('请先选择教材版本')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.createLesson(curriculumId, input)
        lessonNodes.value = await teachingPrepCatalogApi.listLessons(curriculumId)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function updateLesson(
      node: LessonNode,
      changes: {
        title?: string
        durationMinutes?: number | null
        isActive?: boolean
      },
    ): Promise<void> {
      const curriculumId = selectedCurriculumId.value
      if (curriculumId === null) return
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.updateLesson(node.id, {
          expected_revision: node.revision,
          title: changes.title ?? node.title,
          duration_minutes: changes.durationMinutes ?? node.duration_minutes,
          is_active: changes.isActive ?? node.is_active,
        })
        lessonNodes.value = await teachingPrepCatalogApi.listLessons(curriculumId)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function moveLesson(
      node: LessonNode,
      direction: -1 | 1,
    ): Promise<void> {
      const curriculumId = selectedCurriculumId.value
      if (curriculumId === null) return
      const siblings = lessonNodes.value
        .filter(({ parent_id: parentId }) => parentId === node.parent_id)
        .sort((left, right) => left.sort_order - right.sort_order)
      const currentIndex = siblings.findIndex(({ id }) => id === node.id)
      const targetIndex = currentIndex + direction
      if (currentIndex < 0 || targetIndex < 0 || targetIndex >= siblings.length) {
        return
      }
      const ordered = siblings.map(({ id }) => id)
      ;[ordered[currentIndex], ordered[targetIndex]] = [
        ordered[targetIndex]!,
        ordered[currentIndex]!,
      ]
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.reorderLessons(curriculumId, {
          parent_id: node.parent_id,
          ordered_ids: ordered,
          expected_revisions: Object.fromEntries(
            siblings.map(({ id, revision }) => [id, revision]),
          ),
        })
        lessonNodes.value = await teachingPrepCatalogApi.listLessons(curriculumId)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    return {
      curricula,
      semesters,
      selectedCurriculumId,
      selectedSemesterId,
      selectedCurriculum,
      selectedSemester,
      lessonNodes,
      materials,
      semesterLessonProgress,
      semesterMaterials,
      allSemesterMaterialRecords,
      libraryMaterialRecords,
      selectedSemesterMaterial,
      referencePptCollections,
      collectionByRecordId,
      libraryChapterFolders,
      activeLessonCount,
      lessonTreeStatus,
      libraryProgressSteps,
      refreshReferencePptCollections,
      selectedLessonId,
      selectedLesson,
      selectedMaterialId,
      materialUnits,
      materialParseJobs,
      materialLinks,
      resourcePacks,
      resourcePackStatus,
      selectedResourcePackId,
      lessonDrafts,
      lessonDraftPreflight,
      selectedLessonDraftId,
      slidePlans,
      selectedSlidePlanId,
      slidePlanPreview,
      moduleStatus,
      teachingPreferences,
      loadState,
      saveState,
      errorMessage,
      load,
      ensureMaterialData,
      selectCurriculum,
      selectSemester,
      clearSemesterSelection,
      createCurriculum,
      createSemesterWorkspace,
      createSemester,
      updateSemester,
      setSemesterLessonProgress,
      attachSemesterMaterial,
      updateSemesterMaterial,
      updateMaterialSource,
      getMaterialDeletionPreview,
      deleteMaterialSource,
      getMaterialDeletionStatus,
      createLesson,
      updateLesson,
      moveLesson,
      selectLesson,
      openMaterial,
      refreshCurrentMaterialUnits,
      parseMaterialInBackground,
      cancelMaterialParse,
      importMaterialCopy,
      relocateMaterialCopy,
      saveMaterialUnitLabel,
      freezeResourcePack,
      selectResourcePack,
      prepareLessonDraft,
      generateLessonDraft,
      reviseLessonDraft,
      selectLessonDraft,
      selectSlidePlan,
      reviewSlidePlan,
    }
  },
)
