import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../../../api/errors'
import {
  jobApi,
  TERMINAL_JOB_STATUSES,
  type JobResponse,
} from '../../../api/jobs'
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

export const SEMESTER_MAPPING_COMMAND_STORAGE_KEY =
  'ai-grading:teaching-prep:semester-mapping-pending-command:v1'

interface PendingSemesterMappingCommand {
  fingerprint: string
  operationId: string
}

function pendingSemesterMappingCommand(
  fingerprint: string,
): PendingSemesterMappingCommand {
  try {
    const stored = globalThis.localStorage?.getItem(
      SEMESTER_MAPPING_COMMAND_STORAGE_KEY,
    )
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<PendingSemesterMappingCommand>
      if (
        parsed.fingerprint === fingerprint
        && typeof parsed.operationId === 'string'
        && /^semester-mapping-[0-9a-f]{32}$/i.test(parsed.operationId)
      ) {
        return {
          fingerprint,
          operationId: parsed.operationId,
        }
      }
    }
    const command = {
      fingerprint,
      operationId: `semester-mapping-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
    }
    globalThis.localStorage?.setItem(
      SEMESTER_MAPPING_COMMAND_STORAGE_KEY,
      JSON.stringify(command),
    )
    return command
  } catch {
    // The backend also de-duplicates the semantic mapping request. Browser
    // storage only preserves the original operation ID across a refresh.
    return {
      fingerprint,
      operationId: `semester-mapping-${globalThis.crypto.randomUUID().replaceAll('-', '')}`,
    }
  }
}

function clearPendingSemesterMappingCommand(fingerprint: string): void {
  try {
    const stored = globalThis.localStorage?.getItem(
      SEMESTER_MAPPING_COMMAND_STORAGE_KEY,
    )
    if (!stored) return
    const parsed = JSON.parse(stored) as Partial<PendingSemesterMappingCommand>
    if (parsed.fingerprint === fingerprint) {
      globalThis.localStorage?.removeItem(
        SEMESTER_MAPPING_COMMAND_STORAGE_KEY,
      )
    }
  } catch {
    // No browser state needs recovery when storage is unavailable.
  }
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
  return '备课资料暂时无法读取，请保留当前内容后重试。'
}

function waitForNextJobPoll(): Promise<void> {
  return new Promise(resolve => globalThis.setTimeout(resolve, 350))
}

export const useTeachingPrepCatalogStore = defineStore(
  'teaching-prep-catalog',
  () => {
    const curricula = ref<CurriculumEdition[]>([])
    const semesters = ref<TeachingSemester[]>([])
    const selectedCurriculumId = ref<string | null>(null)
    const lessonNodes = ref<LessonNode[]>([])
    const materials = ref<MaterialVersion[]>([])
    const semesterLessonProgress = ref<SemesterLessonProgress[]>([])
    const semesterMaterials = ref<SemesterMaterialRecord[]>([])
    const semesterMappingPreflight = ref<SemesterMappingPreflight | null>(null)
    const semesterMappingProposals = ref<SemesterMappingProposal[]>([])
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
    const materialParsePolls = new Map<number, Promise<JobResponse>>()
    let pendingSemesterMapping: PendingSemesterMappingCommand | null = null

    const selectedCurriculum = computed(
      () => curricula.value.find(
        ({ id }) => id === selectedCurriculumId.value,
      ) ?? null,
    )
    const selectedSemester = computed(
      () => semesters.value.find(
        ({ curriculum_id: curriculumId }) => (
          curriculumId === selectedCurriculumId.value
        ),
      ) ?? null,
    )
    const selectedLesson = computed(
      () => lessonNodes.value.find(
        ({ id }) => id === selectedLessonId.value,
      ) ?? null,
    )

    async function load(): Promise<void> {
      loadController?.abort()
      lessonFlowGeneration += 1
      materialFlowGeneration += 1
      const controller = new AbortController()
      loadController = controller
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const [
          nextStatus,
          nextPreferences,
          nextCurricula,
          nextSemesters,
          nextMaterials,
          nextMaterialParseJobs,
        ] = await Promise.all([
          teachingPrepCatalogApi.status(controller.signal),
          teachingPrepCatalogApi.getTeachingPreferences(controller.signal),
          teachingPrepCatalogApi.listCurricula(controller.signal),
          teachingPrepCatalogApi.listSemesters(controller.signal),
          teachingPrepCatalogApi.listMaterials(controller.signal, true),
          teachingPrepCatalogApi.listMaterialParseJobs(controller.signal),
        ])
        if (controller.signal.aborted) return
        moduleStatus.value = nextStatus
        teachingPreferences.value = nextPreferences
        curricula.value = nextCurricula
        semesters.value = nextSemesters
        materials.value = nextMaterials
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
          lessonNodes.value = await teachingPrepCatalogApi.listLessons(
            selectedCurriculumId.value,
            controller.signal,
          )
          const semester = nextSemesters.find(
            ({ curriculum_id: curriculumId }) => (
              curriculumId === selectedCurriculumId.value
            ),
          )
          if (semester) {
            ;[
              semesterLessonProgress.value,
              semesterMaterials.value,
              semesterMappingProposals.value,
            ] = await Promise.all([
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
            ])
          } else {
            semesterLessonProgress.value = []
            semesterMaterials.value = []
            semesterMappingProposals.value = []
          }
        } else {
          lessonNodes.value = []
          semesterLessonProgress.value = []
          semesterMaterials.value = []
          semesterMappingProposals.value = []
        }
        if (!controller.signal.aborted) {
          loadState.value = 'ready'
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

    async function selectCurriculum(curriculumId: string): Promise<void> {
      if (curriculumId === selectedCurriculumId.value) return
      const generation = ++lessonFlowGeneration
      materialFlowGeneration += 1
      selectedCurriculumId.value = curriculumId
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
      semesterMappingPreflight.value = null
      semesterMappingProposals.value = []
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const nextLessons = await teachingPrepCatalogApi.listLessons(curriculumId)
        if (
          generation !== lessonFlowGeneration
          || selectedCurriculumId.value !== curriculumId
        ) return
        lessonNodes.value = nextLessons
        const semester = semesters.value.find(
          ({ curriculum_id: selectedId }) => selectedId === curriculumId,
        )
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
            materialUnits.value = await teachingPrepCatalogApi.listMaterialUnits(
              materialId,
            )
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

    async function openMaterial(material: MaterialVersion): Promise<void> {
      const generation = ++materialFlowGeneration
      selectedMaterialId.value = material.id
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const nextUnits = await teachingPrepCatalogApi.listMaterialUnits(material.id)
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return
        materialUnits.value = nextUnits
        loadState.value = nextUnits.length > 0 ? 'ready' : 'loading'
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return
        await refreshMaterialCollections()
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return
        loadState.value = 'ready'
      } catch (error) {
        if (
          generation !== materialFlowGeneration
          || selectedMaterialId.value !== material.id
        ) return
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
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
        semesterMappingPreflight.value = null
        semesterMappingProposals.value = []
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
        lessonNodes.value = []
        semesterLessonProgress.value = []
        semesterMaterials.value = []
        semesterMappingPreflight.value = null
        semesterMappingProposals.value = []
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
        await teachingPrepCatalogApi.createSemester({
          ...input,
          curriculum_id: curriculumId,
        })
        semesters.value = await teachingPrepCatalogApi.listSemesters()
        const semester = selectedSemester.value
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

    async function prepareSemesterMapping(
      materialRecordIds: string[],
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        semesterMappingPreflight.value = await (
          teachingPrepCatalogApi.semesterMappingPreflight(
            semester.id,
            materialRecordIds,
          )
        )
        loadState.value = 'ready'
      } catch (error) {
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function generateSemesterMapping(
      materialRecordIds: string[],
    ): Promise<void> {
      const semester = selectedSemester.value
      if (!semester) throw new Error('请先建立学期状态')
      saveState.value = 'saving'
      errorMessage.value = ''
      let fingerprint: string | null = null
      try {
        const freshPreflight = await (
          teachingPrepCatalogApi.semesterMappingPreflight(
            semester.id,
            materialRecordIds,
          )
        )
        semesterMappingPreflight.value = freshPreflight
        if (!freshPreflight.model_available) {
          throw new Error('请先在模型配置中启用可用模型')
        }
        fingerprint = JSON.stringify({
          semesterId: semester.id,
          materialRecordIds: [...materialRecordIds].sort(),
          sourceStateSha256: freshPreflight.source_state_sha256,
        })
        if (pendingSemesterMapping?.fingerprint !== fingerprint) {
          pendingSemesterMapping = pendingSemesterMappingCommand(fingerprint)
        }
        await teachingPrepCatalogApi.generateSemesterMappingProposal(
          semester.id,
          {
            operation_id: pendingSemesterMapping.operationId,
            material_record_ids: materialRecordIds,
          },
        )
        semesterMappingProposals.value = await (
          teachingPrepCatalogApi.listSemesterMappingProposals(semester.id)
        )
      } catch (error) {
        if (
          error instanceof ApiError
          && error.code === 'semester_mapping_retry_available'
          && fingerprint !== null
        ) {
          clearPendingSemesterMappingCommand(fingerprint)
          pendingSemesterMapping = null
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
      selectedCurriculum,
      selectedSemester,
      lessonNodes,
      materials,
      semesterLessonProgress,
      semesterMaterials,
      semesterMappingPreflight,
      semesterMappingProposals,
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
