import { apiClient } from '../../../api/client'
import { assertNoPathLikeKeys, isRecord } from '../../../api/validation'
import type {
  LessonDraftPayload,
  PptxExecution,
  PptxVersion,
  SemesterMappingProposal,
  TeachingPreferencesPayload,
} from './catalog'

export type TeachingPrepStage =
  | 'select'
  | 'materials'
  | 'plan'
  | 'slides'
  | 'package'
export type TeachingPrepWorkspace =
  | 'lesson-tree'
  | 'materials'
  | 'lesson-prep'
  | 'versions'

export interface LessonPreparationStatus {
  lesson_node_id: string
  title: string
  sort_order: number
  duration_minutes: number
  manual_progress: 'not_started' | 'preparing' | 'ready' | 'taught' | 'skipped'
  manual_progress_revision: number | null
  preparation_stage: TeachingPrepStage
  next_action: string
  blockers: string[]
  cells: Record<'materials' | 'plan' | 'exercises' | 'slides', {
    status: 'not_started' | 'in_progress' | 'needs_teacher' | 'ready' | 'stale' | 'failed' | 'not_applicable'
    summary: string
    target_panel: string
  }>
  ai_tasks: Array<{
    task_id: string
    task_kind: string
    status: string
    proposal_ref_id: string | null
    proposal_revision: string | null
  }>
  summary_revision: string
  latest: {
    resource_pack_id: string | null
    lesson_draft_id: string | null
    slide_plan_id: string | null
    pptx_version_id: string | null
    pptx_revision: number | null
    up_class_package_id: string | null
  }
}

export interface ReferenceMaterialUnit {
  unit_id: string
  unit_index: number
  unit_kind: string
  title: string | null
  preview_url: string
  text_status: string
  formula_review_required: boolean
}

export interface ReferenceMaterialLink {
  link_id: string
  link_revision: number
  purpose: string
  material_version_id: string
  material_name: string
  material_type: string
  content_sha256: string
  start_unit: number
  end_unit: number
  units: ReferenceMaterialUnit[]
}

export interface ReferenceSelectionPayload {
  material_selections: Array<{
    link_id: string
    material_version_id?: string
    purpose?: string
    start_unit: number
    end_unit: number
    ppt_intent?: 'keep' | 'candidate_delete'
  }>
  exercise_candidate_ids: string[]
  question_ids: number[]
  assessment_ids: number[]
  knowledge_scope: string[]
  preparation_preferences: TeachingPreferencesPayload
  class_name: string | null
  teacher_context: string | null
}

export interface ReferenceSelectionDraft {
  lesson_node_id: string
  payload: ReferenceSelectionPayload
  source_state_sha256: string
  revision: number
  created_at: string
  updated_at: string
}

export interface ReferenceSelectionPreflight {
  lesson_node_id: string
  source_state_sha256: string
  catalog: {
    lesson: Record<string, unknown>
    material_links: ReferenceMaterialLink[]
  }
  draft: ReferenceSelectionDraft | null
  model_available: boolean
  model_label: string | null
  model_destination_fingerprint: string
  will_call_model: false
}

export interface ReferenceSelectionSnapshot {
  id: string
  lesson_node_id: string
  source_state_sha256: string
  payload: Record<string, unknown>
  created_at: string
}

export type TeachingPrepAdoptionCommand =
  | { kind: 'apply_semester_mapping'; proposal_revision: number }
  | { kind: 'confirm_lesson_draft'; payload: LessonDraftPayload }
  | { kind: 'finalize_exercise_suggestions' }
  | {
      kind: 'review_slide_plan'
      operation_reviews: Array<{
        operation_id: string
        decision: string
        reason: string
        planned_minutes: number
        teacher_note: string | null
      }>
      approve_low_risk_deletions: boolean
      review_note: string | null
    }

export interface TeachingPrepAIAdoption {
  adoption_id: string
  handoff_id: string
  task_kind: string
  proposal_ref_id: string
  object_kind: string
  object_id: string
  object_ref: string
  object_status: string
  draft_revision: string
  target_revision: string
  receipt_revision: string
  adopted_at: string
}

export interface ExerciseSuggestionRegion {
  material_unit_id: string
  sequence: number
  crop: { x0: number; y0: number; x1: number; y1: number }
}

export interface ExerciseSuggestionPayload {
  material_version_id: string
  question_number: string | null
  content_label: string | null
  difficulty: 'unrated' | 'easy' | 'medium' | 'hard'
  classroom_use: string
  estimated_minutes: number | null
  teaching_focus: string | null
  reason: string
  uncertainties: string[]
  question_regions: ExerciseSuggestionRegion[]
  answer_regions: ExerciseSuggestionRegion[]
}

export interface ExerciseSuggestion {
  id: string
  run_id: string
  lesson_node_id: string
  source_state_sha256: string
  decision: 'pending' | 'accepted' | 'modified' | 'rejected'
  original_payload: ExerciseSuggestionPayload
  teacher_payload: ExerciseSuggestionPayload | null
  rejection_reason: string | null
  exercise_candidate_id: string | null
  revision: number
  created_at: string
  updated_at: string
}

export interface ExerciseSuggestionRun {
  id: string
  snapshot_id: string
  operation_id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled' | 'result_unknown'
  error_code: string | null
  model_call_count: number
  created_at: string
  updated_at: string
  finished_at: string | null
  suggestions: ExerciseSuggestion[]
}

export interface TrustedPptxVersion extends PptxVersion {
  is_current: boolean
  current_revision: number | null
  preview_url: string
  file_verified: boolean
}

export const teachingPrepWorkbenchApi = {
  adoptAIHandoff(
    handoffId: string,
    draftRevision: string,
    targetRevision: string,
    command: TeachingPrepAdoptionCommand,
  ): Promise<TeachingPrepAIAdoption> {
    return apiClient.request(
      `/api/teaching-prep/ai-handoffs/${encodeURIComponent(handoffId)}/adopt`,
      {
        method: 'POST',
        body: {
          draft_revision: draftRevision,
          target_revision: targetRevision,
          command,
        },
        decode: teachingPrepAIAdoption,
      },
    )
  },
  lessonStatuses(semesterId: string, signal?: AbortSignal): Promise<LessonPreparationStatus[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/lesson-preparation-statuses`,
      {
        signal,
        decode: payload => itemList(payload, lessonPreparationStatus),
      },
    )
  },

  reviewMapping(
    proposalId: string,
    mappingId: string,
    input: {
      expected_revision: number
      decision: 'accepted' | 'modified' | 'rejected'
      lesson_ref?: string
      start_unit?: number
      end_unit?: number
      reason?: string | null
    },
  ): Promise<SemesterMappingProposal> {
    return apiClient.request(
      `/api/teaching-prep/semester-mapping-proposals/${encodeURIComponent(proposalId)}/mappings/${encodeURIComponent(mappingId)}`,
      { method: 'PATCH', body: input, decode: semesterMappingProposal },
    )
  },

  referencePreflight(lessonId: string, signal?: AbortSignal): Promise<ReferenceSelectionPreflight> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/reference-selection-preflight`,
      { method: 'POST', signal, decode: referencePreflight },
    )
  },

  saveReferenceDraft(
    lessonId: string,
    input: {
      expected_revision: number | null
      source_state_sha256: string
      selection: ReferenceSelectionPayload
    },
  ): Promise<ReferenceSelectionDraft> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/reference-selection-draft`,
      { method: 'PUT', body: input, decode: referenceDraft },
    )
  },

  freezeReferenceSnapshot(
    lessonId: string,
    requestToken: string,
    expectedDraftRevision: number,
  ): Promise<ReferenceSelectionSnapshot> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/reference-selection-snapshots`,
      {
        method: 'POST',
        body: {
          request_token: requestToken,
          expected_draft_revision: expectedDraftRevision,
        },
        decode: referenceSnapshot,
      },
    )
  },

  startExerciseSuggestions(
    snapshotId: string,
    operationId: string,
  ): Promise<ExerciseSuggestionRun> {
    return apiClient.request(
      `/api/teaching-prep/reference-selection-snapshots/${encodeURIComponent(snapshotId)}/exercise-suggestion-runs`,
      {
        method: 'POST',
        body: { operation_id: operationId, confirmed: true },
        decode: exerciseSuggestionRun,
      },
    )
  },

  exerciseSuggestionRun(runId: string, signal?: AbortSignal): Promise<ExerciseSuggestionRun> {
    return apiClient.request(
      `/api/teaching-prep/exercise-suggestion-runs/${encodeURIComponent(runId)}`,
      { signal, decode: exerciseSuggestionRun },
    )
  },

  cancelExerciseSuggestions(runId: string): Promise<ExerciseSuggestionRun> {
    return apiClient.request(
      `/api/teaching-prep/exercise-suggestion-runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST', decode: exerciseSuggestionRun },
    )
  },

  reviewExerciseSuggestion(
    suggestionId: string,
    input: {
      expected_revision: number
      decision: 'accepted' | 'modified' | 'rejected'
      teacher_payload?: ExerciseSuggestionPayload | null
      rejection_reason?: string | null
    },
  ): Promise<ExerciseSuggestion> {
    return apiClient.request(
      `/api/teaching-prep/exercise-suggestions/${encodeURIComponent(suggestionId)}`,
      { method: 'PATCH', body: input, decode: exerciseSuggestion },
    )
  },

  resourcePackPreflight(
    lessonId: string,
    input: {
      reference_ppt_intents: Record<string, 'keep' | 'candidate_delete'>
      selected_material_link_ids: string[]
      selected_exercise_candidate_ids: string[]
    },
  ): Promise<Record<string, unknown>> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/resource-pack-preflight`,
      { method: 'POST', body: input, decode: recordPayload },
    )
  },

  capacityPreview(draftId: string, payload: LessonDraftPayload): Promise<Record<string, unknown>> {
    return apiClient.request(
      `/api/teaching-prep/lesson-drafts/${encodeURIComponent(draftId)}/capacity-preview`,
      { method: 'POST', body: { payload }, decode: recordPayload },
    )
  },

  pptxVersions(lessonId: string, signal?: AbortSignal): Promise<TrustedPptxVersion[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/pptx-versions`,
      { signal, decode: payload => itemList(payload, trustedPptxVersion) },
    )
  },

  activatePptxVersion(
    versionId: string,
    expectedRevision: number | null,
  ): Promise<{ version: PptxVersion; current_revision: number; changed: boolean }> {
    return apiClient.request(
      `/api/teaching-prep/pptx-versions/${encodeURIComponent(versionId)}/activate`,
      { method: 'POST', body: { expected_revision: expectedRevision }, decode: activateResult },
    )
  },

  pptxExecution(executionId: string, signal?: AbortSignal): Promise<PptxExecution> {
    return apiClient.request(
      `/api/teaching-prep/pptx-executions/${encodeURIComponent(executionId)}`,
      { signal, decode: pptxExecution },
    )
  },
}

function itemList<T>(payload: unknown, decode: (value: unknown) => T): T[] {
  assertNoPathLikeKeys(payload)
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error('Invalid teaching-prep item list')
  }
  return payload.items.map(decode)
}

function recordPayload(payload: unknown): Record<string, unknown> {
  assertNoPathLikeKeys(payload)
  if (!isRecord(payload)) throw new Error('Invalid teaching-prep response')
  return payload
}

function lessonPreparationStatus(payload: unknown): LessonPreparationStatus {
  return recordPayload(payload) as unknown as LessonPreparationStatus
}

function semesterMappingProposal(payload: unknown): SemesterMappingProposal {
  return recordPayload(payload) as unknown as SemesterMappingProposal
}

function referencePreflight(payload: unknown): ReferenceSelectionPreflight {
  return recordPayload(payload) as unknown as ReferenceSelectionPreflight
}

function referenceDraft(payload: unknown): ReferenceSelectionDraft {
  return recordPayload(payload) as unknown as ReferenceSelectionDraft
}

function referenceSnapshot(payload: unknown): ReferenceSelectionSnapshot {
  return recordPayload(payload) as unknown as ReferenceSelectionSnapshot
}

function exerciseSuggestion(payload: unknown): ExerciseSuggestion {
  return recordPayload(payload) as unknown as ExerciseSuggestion
}

function exerciseSuggestionRun(payload: unknown): ExerciseSuggestionRun {
  return recordPayload(payload) as unknown as ExerciseSuggestionRun
}

function trustedPptxVersion(payload: unknown): TrustedPptxVersion {
  return recordPayload(payload) as unknown as TrustedPptxVersion
}

function activateResult(
  payload: unknown,
): { version: PptxVersion; current_revision: number; changed: boolean } {
  return recordPayload(payload) as unknown as {
    version: PptxVersion
    current_revision: number
    changed: boolean
  }
}

function pptxExecution(payload: unknown): PptxExecution {
  return recordPayload(payload) as unknown as PptxExecution
}

function teachingPrepAIAdoption(payload: unknown): TeachingPrepAIAdoption {
  return recordPayload(payload) as unknown as TeachingPrepAIAdoption
}
