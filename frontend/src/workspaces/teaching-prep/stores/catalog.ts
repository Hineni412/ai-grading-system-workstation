import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../../../api/errors'
import {
  teachingPrepCatalogApi,
  type AssessmentChoice,
  type ClassVariant,
  type CreateCurriculumInput,
  type CreateLessonNodeInput,
  type CurriculumEdition,
  type ExerciseCandidate,
  type ExerciseCandidateInput,
  type FreezeResourcePackInput,
  type LessonNode,
  type LessonDraft,
  type LessonDraftPayload,
  type LessonDraftPreflight,
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
  type TeachingPrepModuleStatus,
  type UpClassPackage,
} from '../api/catalog'

export type CatalogLoadState = 'idle' | 'loading' | 'ready' | 'error'

function safeMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === 'conflict') {
    return '内容已在其他页面更新，请刷新后继续。'
  }
  return '备课资料暂时无法读取，请保留当前内容后重试。'
}

export const useTeachingPrepCatalogStore = defineStore(
  'teaching-prep-catalog',
  () => {
    const curricula = ref<CurriculumEdition[]>([])
    const selectedCurriculumId = ref<string | null>(null)
    const lessonNodes = ref<LessonNode[]>([])
    const materials = ref<MaterialVersion[]>([])
    const selectedLessonId = ref<string | null>(null)
    const selectedMaterialId = ref<string | null>(null)
    const materialUnits = ref<MaterialUnit[]>([])
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
    const pptxExecutions = ref<PptxExecution[]>([])
    const latestPptxVersion = ref<PptxVersion | null>(null)
    const classVariants = ref<ClassVariant[]>([])
    const upClassPackages = ref<UpClassPackage[]>([])
    const postLessonReviews = ref<PostLessonReview[]>([])
    const loadState = ref<CatalogLoadState>('idle')
    const saveState = ref<'idle' | 'saving'>('idle')
    const errorMessage = ref('')
    let loadController: AbortController | null = null

    const selectedCurriculum = computed(
      () => curricula.value.find(
        ({ id }) => id === selectedCurriculumId.value,
      ) ?? null,
    )
    const selectedLesson = computed(
      () => lessonNodes.value.find(
        ({ id }) => id === selectedLessonId.value,
      ) ?? null,
    )

    async function load(): Promise<void> {
      loadController?.abort()
      const controller = new AbortController()
      loadController = controller
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        const [nextStatus, nextCurricula, nextMaterials] = await Promise.all([
          teachingPrepCatalogApi.status(controller.signal),
          teachingPrepCatalogApi.listCurricula(controller.signal),
          teachingPrepCatalogApi.listMaterials(controller.signal),
        ])
        if (controller.signal.aborted) return
        moduleStatus.value = nextStatus
        curricula.value = nextCurricula
        materials.value = nextMaterials
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
        } else {
          lessonNodes.value = []
        }
        if (!controller.signal.aborted) loadState.value = 'ready'
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
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        lessonNodes.value = await teachingPrepCatalogApi.listLessons(curriculumId)
        loadState.value = 'ready'
      } catch (error) {
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
      }
    }

    async function selectLesson(node: LessonNode): Promise<void> {
      if (node.node_type !== 'lesson') return
      selectedLessonId.value = node.id
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
        materialLinks.value = nextLinks
        exerciseCandidates.value = nextExercises
        resourcePacks.value = nextPacks
        resourcePackStatus.value = nextPackStatus
        classVariants.value = nextVariants
        upClassPackages.value = nextPackages
        postLessonReviews.value = nextReviews
        selectedResourcePackId.value = nextPacks[0]?.id ?? null
        lessonDraftPreflight.value = null
        lessonDrafts.value = nextPacks[0]
          ? await teachingPrepCatalogApi.listLessonDrafts(nextPacks[0].id)
          : []
        const confirmedDraft = lessonDrafts.value.find(
          ({ status }) => status === 'confirmed',
        )
        selectedLessonDraftId.value = confirmedDraft?.id ?? null
        slidePlans.value = confirmedDraft
          ? await teachingPrepCatalogApi.listSlidePlans(confirmedDraft.id)
          : []
        selectedSlidePlanId.value = slidePlans.value[0]?.id ?? null
        slidePlanPreview.value = slidePlans.value[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(
            slidePlans.value[0].id,
          )
          : null
        pptxExecutions.value = slidePlans.value[0]
          ? await teachingPrepCatalogApi.listPptxExecutions(
            slidePlans.value[0].id,
          )
          : []
        latestPptxVersion.value = null
      } catch (error) {
        errorMessage.value = safeMessage(error)
      }
    }

    async function openMaterial(material: MaterialVersion): Promise<void> {
      selectedMaterialId.value = material.id
      loadState.value = 'loading'
      errorMessage.value = ''
      try {
        materialUnits.value = await teachingPrepCatalogApi.parseMaterial(
          material.id,
        )
        loadState.value = 'ready'
      } catch (error) {
        loadState.value = 'error'
        errorMessage.value = safeMessage(error)
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

    async function selectResourcePack(pack: ResourcePack): Promise<void> {
      selectedResourcePackId.value = pack.id
      lessonDraftPreflight.value = null
      errorMessage.value = ''
      try {
        lessonDrafts.value = await teachingPrepCatalogApi.listLessonDrafts(
          pack.id,
        )
        const confirmedDraft = lessonDrafts.value.find(
          ({ status }) => status === 'confirmed',
        )
        selectedLessonDraftId.value = confirmedDraft?.id ?? null
        slidePlans.value = confirmedDraft
          ? await teachingPrepCatalogApi.listSlidePlans(confirmedDraft.id)
          : []
        selectedSlidePlanId.value = slidePlans.value[0]?.id ?? null
        slidePlanPreview.value = slidePlans.value[0]
          ? await teachingPrepCatalogApi.getSlidePlanPreview(
            slidePlans.value[0].id,
          )
          : null
      } catch (error) {
        errorMessage.value = safeMessage(error)
        throw error
      }
    }

    async function prepareLessonDraft(
      mode: 'local_template' | 'model' = 'local_template',
    ): Promise<void> {
      const packId = selectedResourcePackId.value
      if (packId === null) throw new Error('请先选择资源包')
      errorMessage.value = ''
      try {
        lessonDraftPreflight.value = await teachingPrepCatalogApi
          .getLessonDraftPreflight(packId, mode)
      } catch (error) {
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
      selectedLessonDraftId.value = draft.id
      selectedSlidePlanId.value = null
      slidePlanPreview.value = null
      pptxExecutions.value = []
      latestPptxVersion.value = null
      errorMessage.value = ''
      try {
        slidePlans.value = await teachingPrepCatalogApi.listSlidePlans(
          draft.id,
        )
        if (slidePlans.value[0]) {
          await selectSlidePlan(slidePlans.value[0])
        }
      } catch (error) {
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
      selectedSlidePlanId.value = plan.id
      errorMessage.value = ''
      try {
        const [preview, executions] = await Promise.all([
          teachingPrepCatalogApi.getSlidePlanPreview(plan.id),
          teachingPrepCatalogApi.listPptxExecutions(plan.id),
        ])
        slidePlanPreview.value = preview
        pptxExecutions.value = executions
        latestPptxVersion.value = null
      } catch (error) {
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
    ): Promise<void> {
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
      selectedCurriculumId,
      selectedCurriculum,
      lessonNodes,
      materials,
      selectedLessonId,
      selectedLesson,
      selectedMaterialId,
      materialUnits,
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
      pptxExecutions,
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
      createLesson,
      updateLesson,
      moveLesson,
      selectLesson,
      openMaterial,
      saveMaterialUnitLabel,
      confirmMaterialRanges,
      deactivateMaterialLink,
      saveExerciseCandidate,
      updateExerciseCandidate,
      loadAvailableAssessments,
      loadAvailableQuestions,
      freezeResourcePack,
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
