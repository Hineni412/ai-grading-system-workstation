import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../../../api/errors'
import {
  jobApi,
  TERMINAL_JOB_STATUSES,
  type JobResponse,
} from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import {
  teachingPrepCatalogApi,
  type AssessmentChoice,
  type ClassVariant,
  type CreateCurriculumInput,
  type CreateLessonNodeInput,
  type CurriculumEdition,
  type CreateSemesterInput,
  type ExerciseCandidate,
  type ExerciseCandidateInput,
  type FreezeResourcePackInput,
  type LessonNode,
  type LessonDraft,
  type LessonDraftPayload,
  type LessonDraftPreflight,
  type LessonGenerationPerformance,
  type SlideOperationReviewInput,
  type SlidePlan,
  type SlidePlanPreview,
  type MaterialLink,
  type MaterialLinkPurpose,
  type MaterialUnit,
  type MaterialVersion,
  type PptxExecution,
  type PptxVersion,
  type PostLessonReview,
  type QuestionEvidenceChoice,
  type ResourcePack,
  type ResourcePackStatus,
  type SemesterLessonProgress,
  type SemesterLessonProgressStatus,
  type SemesterMaterialMappingStatus,
  type SemesterMaterialRecord,
  type SemesterMaterialRole,
  type SemesterMappingPreflight,
  type SemesterMappingProposal,
  type SemesterStatus,
  type TeachingSemester,
  type TeachingPrepModuleStatus,
  type TeachingPreferences,
  type TeachingPreferencesPayload,
  type UpClassPackage,
} from '../api/catalog'

export type CatalogLoadState = 'idle' | 'loading' | 'ready' | 'error'

const SEMESTER_MAPPING_JOB_TYPE = 'teaching_prep.semester_mapping'

interface SemesterMappingPreflightContext {
  semesterId: string
  materialRecordId: string
  materialVersionId: string
  sourceStateSha256: string
  generation: number
}

function safeMessage(error: unknown): string {
  if (
    error instanceof ApiError
    && error.code === 'semester_mapping_retry_available'
  ) {
    return '本次目录整理没有完成。请确认资料后，再次点击生成新的建议。'
  }
  if (error instanceof ApiError && error.kind === 'conflict') {
    return '内容已在其他页面更新，请刷新后继续。'
  }
  if (error instanceof Error && !(error instanceof ApiError) && error.message.trim()) {
    return error.message
  }
  return '备课资料暂时无法读取，请保留当前内容后重试。'
}

function waitForNextJobPoll(): Promise<void> {
  return new Promise(resolve => globalThis.setTimeout(resolve, 350))
}

function mappingJobMaterialRecordId(job: JobResponse): string | null {
  const value = job.payload.material_record_id
  return typeof value === 'string' && value ? value : null
}

function mappingJobSourceState(job: JobResponse): string | null {
  const payloadValue = job.payload.source_state_sha256
  if (typeof payloadValue === 'string' && payloadValue) return payloadValue
  const resultValue = job.result.source_state_sha256
  return typeof resultValue === 'string' && resultValue ? resultValue : null
}

function mappingJobOperationId(job: JobResponse): string | null {
  const payloadValue = job.payload.operation_id
  if (typeof payloadValue === 'string' && payloadValue) return payloadValue
  const resultValue = job.result.operation_id
  return typeof resultValue === 'string' && resultValue ? resultValue : null
}

export const useTeachingPrepCatalogStore = defineStore(
  'teaching-prep-catalog',
  () => {
    const jobStore = useJobStore()
    const curricula = ref<CurriculumEdition[]>([])
    const semesters = ref<TeachingSemester[]>([])
    const selectedCurriculumId = ref<string | null>(null)
    const selectedSemesterId = ref<string | null>(null)
    const lessonNodes = ref<LessonNode[]>([])
    const materials = ref<MaterialVersion[]>([])
    const semesterLessonProgress = ref<SemesterLessonProgress[]>([])
    const semesterMaterials = ref<SemesterMaterialRecord[]>([])
    const semesterMappingPreflight = ref<SemesterMappingPreflight | null>(null)
    const semesterMappingPreflightContext = ref<SemesterMappingPreflightContext | null>(null)
    const semesterMappingProposals = ref<SemesterMappingProposal[]>([])
    const semesterMappingJobIds = ref<number[]>([])
    const recoveredSemesterMappingJobIds = ref<number[]>([])
    const refreshingSemesterMappingJobs = new Map<number, Promise<void>>()
    const selectedLessonId = ref<string | null>(null)
    const selectedMaterialId = ref<string | null>(null)
    const materialUnits = ref<MaterialUnit[]>([])
    const materialParseJobs = ref<Record<string, JobResponse>>({})
    const materialLinks = ref<MaterialLink[]>([])
    const exerciseCandidates = ref<ExerciseCandidate[]>([])
    const availableAssessments = ref<AssessmentChoice[]>([])
    const availableQuestions = ref<QuestionEvidenceChoice[]>([])
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
    const pptxExecutions = ref<PptxExecution[]>([])
    const lessonGenerationPerformance = ref<LessonGenerationPerformance | null>(
      null,
    )
    const latestPptxVersion = ref<PptxVersion | null>(null)
    const classVariants = ref<ClassVariant[]>([])
    const upClassPackages = ref<UpClassPackage[]>([])
    const postLessonReviews = ref<PostLessonReview[]>([])
    const loadState = ref<CatalogLoadState>('idle')
    const saveState = ref<'idle' | 'saving'>('idle')
    const errorMessage = ref('')
    let loadController: AbortController | null = null
    let lessonFlowGeneration = 0
    let materialFlowGeneration = 0
    let semesterMappingFlowGeneration = 0
    const materialParsePolls = new Map<number, Promise<JobResponse>>()
    const refreshedSemesterMappingJobs = new Set<number>()

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
    const currentSemesterMappingPreflight = computed(() => {
      const semester = selectedSemester.value
      const record = selectedSemesterMaterial.value
      const context = semesterMappingPreflightContext.value
      if (
        !semester
        || !record
        || !semesterMappingPreflight.value
        || !context
        || context.semesterId !== semester.id
        || context.materialRecordId !== record.id
        || context.materialVersionId !== record.current_material_version_id
        || context.sourceStateSha256 !== semesterMappingPreflight.value.source_state_sha256
      ) return null
      return semesterMappingPreflight.value
    })
    const currentSemesterMappingJob = computed(() => {
      const semester = selectedSemester.value
      const record = selectedSemesterMaterial.value
      if (!semester || !record) return null
      const jobs = semesterMappingJobIds.value
        .map(id => jobStore.jobs[id])
        .filter((job): job is JobResponse => Boolean(
          job
          && job.job_type === SEMESTER_MAPPING_JOB_TYPE
          && job.payload.semester_id === semester.id
          && mappingJobMaterialRecordId(job) === record.id,
        ))
        .sort((left, right) => (
          Date.parse(right.updated_at) - Date.parse(left.updated_at)
          || right.id - left.id
        ))
      const sourceState = currentSemesterMappingPreflight.value?.source_state_sha256
      return sourceState
        ? jobs.find(job => mappingJobSourceState(job) === sourceState) ?? null
        : jobs[0] ?? null
    })
    const currentSemesterMappingProposal = computed(() => {
      const record = selectedSemesterMaterial.value
      const sourceState = currentSemesterMappingPreflight.value?.source_state_sha256
      const latestLocalCollection = semesterMappingProposals.value
        .filter(item => (
          item.status === 'proposed'
          && item.payload.generation_source === 'local_reference_ppt_names'
          && (!record || item.payload.source_material_record_ids.includes(record.id))
        ))
        .sort((left, right) => (
          Date.parse(right.updated_at) - Date.parse(left.updated_at)
        ))[0] ?? null
      if (!record || !sourceState) return latestLocalCollection
      const candidates = semesterMappingProposals.value.filter(item => (
        item.status === 'proposed'
        && item.payload.source_material_record_ids.length === 1
        && item.payload.source_material_record_ids[0] === record.id
        && item.source_state_sha256 === sourceState
      ))
      const job = currentSemesterMappingJob.value
      if (job) {
        const proposalId = typeof job.result.proposal_id === 'string'
          ? job.result.proposal_id
          : null
        const operationId = mappingJobOperationId(job)
        const sourceState = mappingJobSourceState(job)
        const exact = candidates.find(item => (
          (proposalId !== null && item.id === proposalId)
          || (
            operationId !== null
            && sourceState !== null
            && item.operation_id === operationId
            && item.source_state_sha256 === sourceState
          )
        ))
        return exact ?? null
      }
      return candidates.sort((left, right) => (
        Date.parse(right.updated_at) - Date.parse(left.updated_at)
      ))[0] ?? latestLocalCollection
    })
    const currentSemesterMappingJobRecovered = computed(() => (
      currentSemesterMappingJob.value !== null
      && recoveredSemesterMappingJobIds.value.includes(
        currentSemesterMappingJob.value.id,
      )
    ))
    const currentSemesterMappingJobSyncError = computed(() => (
      currentSemesterMappingJob.value
        ? jobStore.syncErrors[currentSemesterMappingJob.value.id] ?? null
        : null
    ))

    function invalidateSemesterMappingPreflight(): void {
      semesterMappingFlowGeneration += 1
      semesterMappingPreflight.value = null
      semesterMappingPreflightContext.value = null
    }

    function setSemesterMappingJobs(
      jobs: JobResponse[],
      recovered: boolean,
    ): void {
      const valid = jobs.filter(job => job.job_type === SEMESTER_MAPPING_JOB_TYPE)
      for (const job of valid) jobStore.track(job)
      semesterMappingJobIds.value = [
        ...new Set([
          ...semesterMappingJobIds.value,
          ...valid.map(job => job.id),
        ]),
      ]
      if (recovered) {
        recoveredSemesterMappingJobIds.value = [
          ...new Set([
            ...recoveredSemesterMappingJobIds.value,
            ...valid.map(job => job.id),
          ]),
        ]
      }
    }

    async function refreshProposalsForMappingJob(
      job: JobResponse,
      force = false,
    ): Promise<void> {
      if (!force && refreshedSemesterMappingJobs.has(job.id)) return
      const inFlight = refreshingSemesterMappingJobs.get(job.id)
      if (inFlight) return inFlight
      const refresh = refreshProposalsForMappingJobOnce(job, force)
      refreshingSemesterMappingJobs.set(job.id, refresh)
      try {
        await refresh
      } finally {
        if (refreshingSemesterMappingJobs.get(job.id) === refresh) {
          refreshingSemesterMappingJobs.delete(job.id)
        }
      }
    }

    async function refreshProposalsForMappingJobOnce(
      job: JobResponse,
      force: boolean,
    ): Promise<void> {
      const semester = selectedSemester.value
      const record = selectedSemesterMaterial.value
      const sourceState = mappingJobSourceState(job)
      if (
        !semester
        || !record
        || !sourceState
        || job.payload.semester_id !== semester.id
        || mappingJobMaterialRecordId(job) !== record.id
      ) return
      const proposalId = typeof job.result.proposal_id === 'string'
        ? job.result.proposal_id
        : null
      const operationId = mappingJobOperationId(job)
      const attempts = force ? 1 : 2
      let lastError: unknown = null
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        try {
          const next = await teachingPrepCatalogApi.listSemesterMappingProposals(
            semester.id,
          )
          if (
            selectedSemester.value?.id !== semester.id
            || selectedSemesterMaterial.value?.id !== record.id
            || currentSemesterMappingPreflight.value?.source_state_sha256 !== sourceState
          ) return
          const durableProposalFound = next.some(item => (
            item.status === 'proposed'
            && item.payload.source_material_record_ids.length === 1
            && item.payload.source_material_record_ids[0] === record.id
            && item.source_state_sha256 === sourceState
            && (
              (proposalId !== null && item.id === proposalId)
              || (operationId !== null && item.operation_id === operationId)
            )
          ))
          if (durableProposalFound) {
            semesterMappingProposals.value = next
            refreshedSemesterMappingJobs.add(job.id)
            return
          }
        } catch (error) {
          lastError = error
        }
      }
      refreshedSemesterMappingJobs.delete(job.id)
      errorMessage.value = lastError
        ? safeMessage(lastError)
        : '后台任务已完成，但待确认建议尚未同步；重新检查后会继续恢复，不会再次调用模型。'
    }

    watch(
      currentSemesterMappingJob,
      (job) => {
        if (job && TERMINAL_JOB_STATUSES.has(job.status)) {
          void refreshProposalsForMappingJob(job)
        }
      },
      { immediate: true },
    )

    async function load(): Promise<void> {
      loadController?.abort()
      lessonFlowGeneration += 1
      materialFlowGeneration += 1
      invalidateSemesterMappingPreflight()
      materialUnits.value = []
      const controller = new AbortController()
      loadController = controller
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const initial = await Promise.allSettled([
          teachingPrepCatalogApi.status(controller.signal),
          teachingPrepCatalogApi.getTeachingPreferences(controller.signal),
          teachingPrepCatalogApi.listCurricula(controller.signal),
          teachingPrepCatalogApi.listSemesters(controller.signal),
          teachingPrepCatalogApi.listMaterials(controller.signal, true),
          teachingPrepCatalogApi.listMaterialParseJobs(controller.signal),
        ] as const)
        if (controller.signal.aborted) return
        const issues: string[] = []
        const [statusResult, preferencesResult, curriculaResult, semestersResult, materialsResult, jobsResult] = initial
        if (statusResult?.status === 'fulfilled') moduleStatus.value = statusResult.value
        else issues.push('模块状态')
        if (preferencesResult?.status === 'fulfilled') teachingPreferences.value = preferencesResult.value
        else issues.push('备课偏好')
        if (curriculaResult?.status === 'fulfilled') curricula.value = curriculaResult.value
        else issues.push('教材目录')
        if (semestersResult?.status === 'fulfilled') semesters.value = semestersResult.value
        else issues.push('学期目录')
        if (materialsResult?.status === 'fulfilled') materials.value = materialsResult.value
        else issues.push('资料列表')
        const nextCurricula = curricula.value
        const nextSemesters = semesters.value
        const nextMaterials = materials.value
        const nextMaterialParseJobs = jobsResult?.status === 'fulfilled' ? jobsResult.value : []
        if (jobsResult?.status === 'rejected') issues.push('资料处理进度')
        materialParseJobs.value = Object.fromEntries(
          nextMaterialParseJobs.map(job => [
            String(job.payload.material_version_id),
            job,
          ]),
        )
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
            const semesterResults = await Promise.allSettled([
              teachingPrepCatalogApi.listSemesterLessonProgress(
                semester.id,
                controller.signal,
              ),
              teachingPrepCatalogApi.listSemesterMaterials(
                semester.id,
                controller.signal,
              ),
              teachingPrepCatalogApi.listSemesterMappingProposals(
                semester.id,
                controller.signal,
              ),
              teachingPrepCatalogApi.listSemesterMappingProposalJobs(
                semester.id,
                controller.signal,
              ),
            ] as const)
            const [progressResult, semesterMaterialsResult, proposalsResult, mappingJobsResult] = semesterResults
            if (progressResult?.status === 'fulfilled') semesterLessonProgress.value = progressResult.value
            else issues.push('课时进度')
            if (semesterMaterialsResult?.status === 'fulfilled') semesterMaterials.value = semesterMaterialsResult.value
            else issues.push('学期资料')
            if (proposalsResult?.status === 'fulfilled') semesterMappingProposals.value = proposalsResult.value
            else issues.push('目录建议')
            if (mappingJobsResult?.status === 'fulfilled') setSemesterMappingJobs(mappingJobsResult.value, true)
            else issues.push('目录任务')
          } else {
            semesterLessonProgress.value = []
            semesterMaterials.value = []
            semesterMappingProposals.value = []
            semesterMappingJobIds.value = []
          }
        } else {
          selectedSemesterId.value = null
          lessonNodes.value = []
          semesterLessonProgress.value = []
          semesterMaterials.value = []
          semesterMappingProposals.value = []
          semesterMappingJobIds.value = []
        }
        if (!controller.signal.aborted) {
          loadState.value = materialsResult?.status === 'rejected' && materials.value.length === 0
            ? 'error'
            : 'ready'
          errorMessage.value = issues.length
            ? `以下信息暂时未更新：${[...new Set(issues)].join('、')}。已经读取的资料仍可查看和管理，可稍后重新检查。`
            : ''
          for (const job of nextMaterialParseJobs) {
            const materialId = String(job.payload.material_version_id ?? '')
            if (
              materialId
              && nextMaterials.some(item => item.id === materialId)
              && !TERMINAL_JOB_STATUSES.has(job.status)
            ) {
              void monitorMaterialParseJob(materialId, job).catch(() => {
                // The persisted Job remains visible and can be recovered on reload.
              })
            }
          }
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
    ): Promise<void> {
      const semester = semesters.value.find(item => (
        item.curriculum_id === curriculumId
        && (semesterId === undefined || item.id === semesterId)
      )) ?? semesters.value.find(item => item.curriculum_id === curriculumId) ?? null
      if (
        curriculumId === selectedCurriculumId.value
        && semester?.id === selectedSemester.value?.id
      ) return
      const generation = ++lessonFlowGeneration
      materialFlowGeneration += 1
      invalidateSemesterMappingPreflight()
      selectedCurriculumId.value = curriculumId
      selectedSemesterId.value = semester?.id ?? null
      selectedLessonId.value = null
      selectedMaterialId.value = null
      materialUnits.value = []
      materialLinks.value = []
      exerciseCandidates.value = []
      resourcePacks.value = []
      resourcePackStatus.value = null
      classVariants.value = []
      upClassPackages.value = []
      postLessonReviews.value = []
      semesterLessonProgress.value = []
      semesterMaterials.value = []
      semesterMappingProposals.value = []
      semesterMappingJobIds.value = []
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
          const [nextProgress, nextSemesterMaterials, nextProposals, nextMappingJobs] = await Promise.all([
            teachingPrepCatalogApi.listSemesterLessonProgress(semester.id),
            teachingPrepCatalogApi.listSemesterMaterials(semester.id),
            teachingPrepCatalogApi.listSemesterMappingProposals(semester.id),
            teachingPrepCatalogApi.listSemesterMappingProposalJobs(semester.id),
          ])
          if (
            generation !== lessonFlowGeneration
            || selectedCurriculumId.value !== curriculumId
          ) return
          semesterLessonProgress.value = nextProgress
          semesterMaterials.value = nextSemesterMaterials
          semesterMappingProposals.value = nextProposals
          setSemesterMappingJobs(nextMappingJobs, true)
        }
        loadState.value = 'ready'
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedCurriculumId.value !== curriculumId
        ) return
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
      }
    }

    async function selectSemester(semesterId: string): Promise<void> {
      const semester = semesters.value.find(item => item.id === semesterId)
      if (!semester) return
      await selectCurriculum(semester.curriculum_id, semester.id)
    }

    async function selectLesson(node: LessonNode): Promise<void> {
      if (node.node_type !== 'lesson') return
      const generation = ++lessonFlowGeneration
      selectedLessonId.value = node.id
      materialLinks.value = []
      exerciseCandidates.value = []
      resourcePacks.value = []
      resourcePackStatus.value = null
      selectedResourcePackId.value = null
      lessonDrafts.value = []
      lessonDraftPreflight.value = null
      selectedLessonDraftId.value = null
      slidePlans.value = []
      selectedSlidePlanId.value = null
      slidePlanPreview.value = null
      pptxExecutions.value = []
      lessonGenerationPerformance.value = null
      latestPptxVersion.value = null
      classVariants.value = []
      upClassPackages.value = []
      postLessonReviews.value = []
      errorMessage.value = ''
      try {
        const [
          nextLinks,
          nextExercises,
          nextPacks,
          nextPackStatus,
          nextVariants,
          nextPackages,
          nextReviews,
        ] = await Promise.all([
          teachingPrepCatalogApi.listMaterialLinks(node.id),
          teachingPrepCatalogApi.listExerciseCandidates(node.id),
          teachingPrepCatalogApi.listResourcePacks(node.id),
          teachingPrepCatalogApi.getResourcePackStatus(node.id),
          teachingPrepCatalogApi.listClassVariants(node.id),
          teachingPrepCatalogApi.listUpClassPackages(node.id),
          teachingPrepCatalogApi.listPostLessonReviews(node.id),
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
        const nextExecutions = nextPlans[0]
          ? await teachingPrepCatalogApi.listPptxExecutions(
            nextPlans[0].id,
          )
          : []
        if (
          generation !== lessonFlowGeneration
          || selectedLessonId.value !== node.id
        ) return
        materialLinks.value = nextLinks
        exerciseCandidates.value = nextExercises
        resourcePacks.value = nextPacks
        resourcePackStatus.value = nextPackStatus
        classVariants.value = nextVariants
        upClassPackages.value = nextPackages
        postLessonReviews.value = nextReviews
        selectedResourcePackId.value = nextPacks[0]?.id ?? null
        lessonDrafts.value = nextDrafts
        selectedLessonDraftId.value = confirmedDraft?.id ?? null
        slidePlans.value = nextPlans
        selectedSlidePlanId.value = nextPlans[0]?.id ?? null
        slidePlanPreview.value = nextPreview
        pptxExecutions.value = nextExecutions
        lessonGenerationPerformance.value = nextExecutions[0]
          ? await teachingPrepCatalogApi.getLessonGenerationPerformance(
              nextExecutions[0].id,
            )
          : null
        latestPptxVersion.value = null
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
        ;[semesterMaterials.value, semesters.value] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(
            selectedSemester.value.id,
          ),
          teachingPrepCatalogApi.listSemesters(),
        ])
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
        materialParseJobs.value = {
          ...materialParseJobs.value,
          [materialId]: job,
        }
        while (!TERMINAL_JOB_STATUSES.has(job.status)) {
          await waitForNextJobPoll()
          job = await jobApi.getJob(job.id)
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
      invalidateSemesterMappingPreflight()
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
      invalidateSemesterMappingPreflight()
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
          semesterMaterials.value = await (
            teachingPrepCatalogApi.listSemesterMaterials(
              selectedSemester.value.id,
            )
          )
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

    async function confirmMaterialRanges(
      ranges: Array<{ start: number; end: number }>,
      purpose: MaterialLinkPurpose,
      teacherNote: string | null,
    ): Promise<void> {
      const lessonId = selectedLessonId.value
      const materialId = selectedMaterialId.value
      if (lessonId === null || materialId === null) {
        throw new Error('请先选择课时和资料')
      }
      saveState.value = 'saving'
      errorMessage.value = ''
      let completed = 0
      try {
        for (const [index, range] of ranges.entries()) {
          await teachingPrepCatalogApi.createMaterialLink(lessonId, {
            request_token: `link-${globalThis.crypto.randomUUID().replaceAll('-', '')}-${index}`,
            material_version_id: materialId,
            start_unit: range.start,
            end_unit: range.end,
            crop: null,
            purpose,
            teacher_note: teacherNote,
            confirmation_status: 'confirmed',
          })
          completed += 1
        }
        materialLinks.value = await teachingPrepCatalogApi.listMaterialLinks(
          lessonId,
        )
      } catch (error) {
        materialLinks.value = await teachingPrepCatalogApi.listMaterialLinks(
          lessonId,
        ).catch(() => materialLinks.value)
        errorMessage.value = completed > 0
          ? `已保存 ${completed} 段，其余范围未能保存；请核对列表后重试。`
          : safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function deactivateMaterialLink(link: MaterialLink): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const updated = await teachingPrepCatalogApi.updateMaterialLink(
          link,
          { is_active: false },
        )
        materialLinks.value = materialLinks.value.map(
          (item) => item.id === updated.id ? updated : item,
        )
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function saveExerciseCandidate(
      candidate: ExerciseCandidate | null,
      input: ExerciseCandidateInput,
    ): Promise<ExerciseCandidate> {
      const lessonId = selectedLessonId.value
      if (lessonId === null) throw new Error('请先选择课时')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const saved = candidate === null
          ? await teachingPrepCatalogApi.createExerciseCandidate(
            lessonId,
            {
              ...input,
              request_token: `exercise-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
            },
          )
          : await teachingPrepCatalogApi.updateExerciseCandidate(
            candidate,
            input,
          )
        exerciseCandidates.value = await teachingPrepCatalogApi
          .listExerciseCandidates(lessonId)
        return saved
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function updateExerciseCandidate(
      candidate: ExerciseCandidate,
      changes: Partial<ExerciseCandidateInput> & { is_active?: boolean },
    ): Promise<void> {
      const lessonId = selectedLessonId.value
      if (lessonId === null) return
      const current: ExerciseCandidateInput = {
        question_number: candidate.question_number,
        content_label: candidate.content_label,
        difficulty: candidate.difficulty,
        classroom_use: candidate.classroom_use,
        estimated_minutes: candidate.estimated_minutes,
        teaching_focus: candidate.teaching_focus,
        teacher_note: candidate.teacher_note,
        selection_status: candidate.selection_status,
        answer_status: candidate.answer_status,
        question_regions: candidate.question_regions.map(
          ({ material_unit_id: materialUnitId, crop }) => ({
            material_unit_id: materialUnitId,
            crop,
          }),
        ),
        answer_regions: candidate.answer_regions.map(
          ({ material_unit_id: materialUnitId, crop }) => ({
            material_unit_id: materialUnitId,
            crop,
          }),
        ),
      }
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.updateExerciseCandidate(
          candidate,
          {
            ...current,
            ...changes,
            is_active: changes.is_active,
          },
        )
        exerciseCandidates.value = await teachingPrepCatalogApi
          .listExerciseCandidates(lessonId)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function loadAvailableAssessments(): Promise<void> {
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        availableAssessments.value = await teachingPrepCatalogApi
          .listAvailableAssessments()
        loadState.value = 'ready'
      } catch (error) {
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function loadAvailableQuestions(): Promise<void> {
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        availableQuestions.value = await teachingPrepCatalogApi
          .listAvailableQuestions()
        loadState.value = 'ready'
      } catch (error) {
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
        throw error
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

    async function saveTeachingPreferences(
      payload: TeachingPreferencesPayload,
    ): Promise<void> {
      const current = teachingPreferences.value
      if (current === null) {
        throw new Error('个人备课偏好尚未加载')
      }
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        teachingPreferences.value = await teachingPrepCatalogApi
          .updateTeachingPreferences(current, payload)
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
      pptxExecutions.value = []
      lessonGenerationPerformance.value = null
      latestPptxVersion.value = null
      errorMessage.value = ''
      try {
        const nextPlans = await teachingPrepCatalogApi.listSlidePlans(
          draft.id,
        )
        const nextPreview = nextPlans[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(nextPlans[0].id)
          : null
        const nextExecutions = nextPlans[0]
          ? await teachingPrepCatalogApi.listPptxExecutions(nextPlans[0].id)
          : []
        if (
          generation !== lessonFlowGeneration
          || selectedLessonDraftId.value !== draft.id
        ) return
        slidePlans.value = nextPlans
        selectedSlidePlanId.value = nextPlans[0]?.id ?? null
        slidePlanPreview.value = nextPreview
        pptxExecutions.value = nextExecutions
        lessonGenerationPerformance.value = nextExecutions[0]
          ? await teachingPrepCatalogApi.getLessonGenerationPerformance(
              nextExecutions[0].id,
            )
          : null
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedLessonDraftId.value !== draft.id
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function createSlidePlan(draft: LessonDraft): Promise<void> {
      if (draft.status !== 'confirmed') throw new Error('请先确认课堂草稿')
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.createSlidePlan(
          draft.id,
          `slide-plan-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        )
        await selectLessonDraft(draft)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function selectSlidePlan(plan: SlidePlan): Promise<void> {
      const generation = ++lessonFlowGeneration
      selectedSlidePlanId.value = plan.id
      errorMessage.value = ''
      try {
        const [preview, executions] = await Promise.all([
          teachingPrepCatalogApi.getSlidePlanPreview(plan.id),
          teachingPrepCatalogApi.listPptxExecutions(plan.id),
        ])
        if (
          generation !== lessonFlowGeneration
          || selectedSlidePlanId.value !== plan.id
        ) return
        slidePlanPreview.value = preview
        pptxExecutions.value = executions
        lessonGenerationPerformance.value = executions[0]
          ? await teachingPrepCatalogApi.getLessonGenerationPerformance(
              executions[0].id,
            )
          : null
        latestPptxVersion.value = null
      } catch (error) {
        if (
          generation !== lessonFlowGeneration
          || selectedSlidePlanId.value !== plan.id
        ) return
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function executePptx(plan: SlidePlan): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const result = await teachingPrepCatalogApi.executeSlidePlan(
          plan.id,
          `pptx-execution-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        )
        latestPptxVersion.value = result.version
        pptxExecutions.value = await teachingPrepCatalogApi
          .listPptxExecutions(plan.id)
        lessonGenerationPerformance.value = pptxExecutions.value[0]
          ? await teachingPrepCatalogApi.getLessonGenerationPerformance(
              pptxExecutions.value[0].id,
            )
          : null
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function cancelPptxExecution(run: PptxExecution): Promise<void> {
      await teachingPrepCatalogApi.cancelPptxExecution(run.id)
      pptxExecutions.value = await teachingPrepCatalogApi
        .listPptxExecutions(run.slide_plan_id)
      lessonGenerationPerformance.value = pptxExecutions.value[0]
        ? await teachingPrepCatalogApi.getLessonGenerationPerformance(
            pptxExecutions.value[0].id,
          )
        : null
    }

    async function recoverPptxExecution(run: PptxExecution): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const result = await teachingPrepCatalogApi
          .recoverPptxExecution(run.id)
        latestPptxVersion.value = result.version
        pptxExecutions.value = await teachingPrepCatalogApi
          .listPptxExecutions(run.slide_plan_id)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function discardPptxStaging(run: PptxExecution): Promise<void> {
      await teachingPrepCatalogApi.discardPptxStaging(run.id)
      pptxExecutions.value = await teachingPrepCatalogApi
        .listPptxExecutions(run.slide_plan_id)
    }

    async function createUpClassPackage(pptxVersionId: string): Promise<void> {
      const lessonId = selectedLessonId.value
      if (lessonId === null) return
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.createUpClassPackage(
          pptxVersionId,
          `up-class-package-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        )
        upClassPackages.value = await teachingPrepCatalogApi
          .listUpClassPackages(lessonId)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function activateUpClassPackage(item: UpClassPackage): Promise<void> {
      await teachingPrepCatalogApi.activateUpClassPackage(
        item.id,
        `package-selection-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
      )
      upClassPackages.value = await teachingPrepCatalogApi
        .listUpClassPackages(item.lesson_node_id)
    }

    async function recoverUpClassPackage(item: UpClassPackage): Promise<void> {
      await teachingPrepCatalogApi.recoverUpClassPackage(item.id)
      upClassPackages.value = await teachingPrepCatalogApi
        .listUpClassPackages(item.lesson_node_id)
    }

    async function discardUpClassPackageStaging(
      item: UpClassPackage,
    ): Promise<void> {
      await teachingPrepCatalogApi.discardUpClassPackageStaging(item.id)
      upClassPackages.value = await teachingPrepCatalogApi
        .listUpClassPackages(item.lesson_node_id)
    }

    async function savePostLessonReview(
      item: UpClassPackage,
      input: {
        timing: PostLessonReview['payload']['timing']
        question_outcome: PostLessonReview['payload']['question_outcome']
        reteach_points: string[]
        next_action: PostLessonReview['payload']['next_action']
        note: string | null
        use_in_next_version: boolean
      },
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.createPostLessonReview(item.id, {
          request_token: `post-review-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
          ...input,
        })
        postLessonReviews.value = await teachingPrepCatalogApi
          .listPostLessonReviews(item.lesson_node_id)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function deriveClassVariant(
      basePack: ResourcePack,
      input: {
        class_name: string
        teacher_context: string | null
        assessment_ids: number[]
        knowledge_scope: string[]
        prior_review_ids: string[]
      },
    ): Promise<void> {
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        const result = await teachingPrepCatalogApi.deriveClassVariant(
          basePack.id,
          {
            request_token: `class-variant-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
            ...input,
          },
        )
        resourcePacks.value = [
          result.resource_pack,
          ...resourcePacks.value.filter(
            ({ id }) => id !== result.resource_pack.id,
          ),
        ]
        classVariants.value = await teachingPrepCatalogApi
          .listClassVariants(basePack.lesson_node_id)
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
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
        invalidateSemesterMappingPreflight()
        semesterMappingProposals.value = []
        semesterMappingJobIds.value = []
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
        invalidateSemesterMappingPreflight()
        semesterMappingProposals.value = []
        semesterMappingJobIds.value = []
        semesters.value = await teachingPrepCatalogApi.listSemesters()
        ;[
          semesterLessonProgress.value,
          semesterMaterials.value,
          semesterMappingProposals.value,
        ] = await Promise.all([
          teachingPrepCatalogApi.listSemesterLessonProgress(workspace.semester.id),
          teachingPrepCatalogApi.listSemesterMaterials(workspace.semester.id),
          teachingPrepCatalogApi.listSemesterMappingProposals(workspace.semester.id),
        ])
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
          ;[
            semesterLessonProgress.value,
            semesterMaterials.value,
            semesterMappingProposals.value,
          ] = await Promise.all([
            teachingPrepCatalogApi.listSemesterLessonProgress(semester.id),
            teachingPrepCatalogApi.listSemesterMaterials(semester.id),
            teachingPrepCatalogApi.listSemesterMappingProposals(semester.id),
          ])
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
      invalidateSemesterMappingPreflight()
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
      invalidateSemesterMappingPreflight()
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
        ;[
          semesterMaterials.value,
          semesters.value,
        ] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
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
      invalidateSemesterMappingPreflight()
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
        ;[
          semesterMaterials.value,
          semesters.value,
        ] = await Promise.all([
          teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
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
      invalidateSemesterMappingPreflight()
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

    function requireCurrentSemesterMaterial(
      materialRecordIds: string[],
    ): { semester: TeachingSemester; record: SemesterMaterialRecord } {
      const semester = selectedSemester.value
      const record = selectedSemesterMaterial.value
      if (!semester) throw new Error('请先建立学期状态')
      if (
        !record
        || materialRecordIds.length !== 1
        || materialRecordIds[0] !== record.id
        || !record.is_active
        || record.parse_status !== 'parsed'
        || record.has_unparsed_update
      ) {
        throw new Error('当前资料已变化，请重新选择并检查发送范围')
      }
      return { semester, record }
    }

    async function prepareSemesterMapping(
      materialRecordIds: string[],
    ): Promise<void> {
      const { semester, record } = requireCurrentSemesterMaterial(materialRecordIds)
      const generation = ++semesterMappingFlowGeneration
      semesterMappingPreflight.value = null
      semesterMappingPreflightContext.value = null
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const next = await teachingPrepCatalogApi.semesterMappingPreflight(
          semester.id,
          [record.id],
        )
        if (
          generation !== semesterMappingFlowGeneration
          || selectedSemester.value?.id !== semester.id
          || selectedMaterialId.value !== record.current_material_version_id
          || selectedSemesterMaterial.value?.id !== record.id
        ) return
        semesterMappingPreflight.value = next
        semesterMappingPreflightContext.value = {
          semesterId: semester.id,
          materialRecordId: record.id,
          materialVersionId: record.current_material_version_id,
          sourceStateSha256: next.source_state_sha256,
          generation,
        }
        loadState.value = 'ready'
        const job = currentSemesterMappingJob.value
        if (
          job
          && TERMINAL_JOB_STATUSES.has(job.status)
          && currentSemesterMappingProposal.value === null
        ) {
          await refreshProposalsForMappingJob(job, true)
        }
      } catch (error) {
        if (
          generation !== semesterMappingFlowGeneration
          || selectedSemester.value?.id !== semester.id
          || selectedMaterialId.value !== record.current_material_version_id
        ) return
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function generateSemesterMapping(
      materialRecordIds: string[],
    ): Promise<void> {
      const { semester, record } = requireCurrentSemesterMaterial(materialRecordIds)
      const preflight = currentSemesterMappingPreflight.value
      const context = semesterMappingPreflightContext.value
      if (
        !preflight
        || !context
        || context.materialRecordId !== record.id
        || context.semesterId !== semester.id
      ) throw new Error('发送范围已失效，请先重新检查')
      if (!preflight.model_available) {
        throw new Error('请先在模型配置中启用可用模型')
      }

      const existing = semesterMappingJobIds.value
        .map(id => jobStore.jobs[id])
        .find(job => (
          job?.job_type === SEMESTER_MAPPING_JOB_TYPE
          && job.payload.semester_id === semester.id
          && mappingJobMaterialRecordId(job) === record.id
          && mappingJobSourceState(job) === preflight.source_state_sha256
          && !['failed', 'cancelled'].includes(job.status)
        ))
      if (existing) {
        jobStore.track(existing)
        return
      }

      saveState.value = 'saving'
      errorMessage.value = ''
      const input = {
        operation_id: `semester-mapping-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
        material_record_id: record.id,
        expected_source_state_sha256: preflight.source_state_sha256,
      }
      try {
        const job = await teachingPrepCatalogApi.startSemesterMappingProposalJob(
          semester.id,
          input,
        )
        if (
          selectedSemester.value?.id !== semester.id
          || selectedMaterialId.value !== record.current_material_version_id
        ) {
          setSemesterMappingJobs([job], false)
          return
        }
        setSemesterMappingJobs([job], false)
        recoveredSemesterMappingJobIds.value = recoveredSemesterMappingJobIds.value.filter(
          id => id !== job.id,
        )
      } catch (error) {
        try {
          const recovered = await teachingPrepCatalogApi.listSemesterMappingProposalJobs(
            semester.id,
          )
          setSemesterMappingJobs(recovered, true)
          const matching = recovered.find(job => (
            mappingJobMaterialRecordId(job) === record.id
            && mappingJobSourceState(job) === preflight.source_state_sha256
          ))
          if (matching) return
        } catch {
          // A failed recovery GET must never cause another model POST.
        }
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
      }
    }

    async function applySemesterMapping(
      proposal: SemesterMappingProposal,
    ): Promise<void> {
      const semester = selectedSemester.value
      const curriculumId = selectedCurriculumId.value
      if (!semester || !curriculumId) {
        throw new Error('请先选择当前学期')
      }
      if (currentSemesterMappingProposal.value?.id !== proposal.id) {
        throw new Error('该建议不属于当前资料，不能审核或应用')
      }
      saveState.value = 'saving'
      errorMessage.value = ''
      try {
        await teachingPrepCatalogApi.applySemesterMappingProposal(proposal)
        ;[
          lessonNodes.value,
          semesterMaterials.value,
          semesterMappingProposals.value,
          semesters.value,
        ] = await Promise.all([
          teachingPrepCatalogApi.listLessons(curriculumId),
          teachingPrepCatalogApi.listSemesterMaterials(semester.id),
          teachingPrepCatalogApi.listSemesterMappingProposals(semester.id),
          teachingPrepCatalogApi.listSemesters(),
        ])
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      } finally {
        saveState.value = 'idle'
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
      selectedSemesterMaterial,
      semesterMappingPreflight,
      currentSemesterMappingPreflight,
      semesterMappingProposals,
      currentSemesterMappingProposal,
      currentSemesterMappingJob,
      currentSemesterMappingJobRecovered,
      currentSemesterMappingJobSyncError,
      selectedLessonId,
      selectedLesson,
      selectedMaterialId,
      materialUnits,
      materialParseJobs,
      materialLinks,
      exerciseCandidates,
      availableAssessments,
      availableQuestions,
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
      pptxExecutions,
      lessonGenerationPerformance,
      latestPptxVersion,
      classVariants,
      upClassPackages,
      postLessonReviews,
      loadState,
      saveState,
      errorMessage,
      load,
      selectCurriculum,
      selectSemester,
      createCurriculum,
      createSemesterWorkspace,
      createSemester,
      updateSemester,
      setSemesterLessonProgress,
      attachSemesterMaterial,
      updateSemesterMaterial,
      updateMaterialSource,
      prepareSemesterMapping,
      generateSemesterMapping,
      applySemesterMapping,
      createLesson,
      updateLesson,
      moveLesson,
      selectLesson,
      openMaterial,
      parseMaterialInBackground,
      cancelMaterialParse,
      importMaterialCopy,
      relocateMaterialCopy,
      saveMaterialUnitLabel,
      confirmMaterialRanges,
      deactivateMaterialLink,
      saveExerciseCandidate,
      updateExerciseCandidate,
      loadAvailableAssessments,
      loadAvailableQuestions,
      freezeResourcePack,
      saveTeachingPreferences,
      selectResourcePack,
      prepareLessonDraft,
      generateLessonDraft,
      reviseLessonDraft,
      selectLessonDraft,
      createSlidePlan,
      selectSlidePlan,
      reviewSlidePlan,
      executePptx,
      cancelPptxExecution,
      recoverPptxExecution,
      discardPptxStaging,
      createUpClassPackage,
      activateUpClassPackage,
      recoverUpClassPackage,
      discardUpClassPackageStaging,
      savePostLessonReview,
      deriveClassVariant,
    }
  },
)
