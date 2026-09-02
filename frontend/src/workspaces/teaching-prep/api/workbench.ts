import { apiClient } from '../../../api/client'
import { assertNoPathLikeKeys, isRecord } from '../../../api/validation'
import type { LessonDraftPayload } from './catalog'

export type TeachingPrepStage =
  | 'select'
  | 'materials'
  | 'plan'
  | 'slides'
  | 'package'

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

/* ===== 题库自动选题 ===== */

export interface QuestionBankSection {
  section_id: string
  section_name: string
  chapter_id: string
  chapter_name: string
  question_count: number
}

export interface QuestionSelectionPreviewInput {
  volume_id: string
  section_ids: string[]
  difficulty_max?: number
  stem_max_chars?: number
  limit?: number
  max_per_method?: number
  exclude_question_ids?: number[]
}

export interface QuestionSelectionItem {
  question_id: number
  question_type: string
  stem: string
  difficulty: number
  frequency_score: number
  frequency: { midterm: number; final: number; zhongkao: number }
  method: string
  knowledge_points: string[]
  has_images: boolean
  selection_reason: {
    frequency: string
    difficulty: string
    method: string
  }
}

export interface QuestionSelectionPreview {
  request: {
    volume_id: string
    section_ids: string[]
    difficulty_max: number
    stem_max_chars: number
    limit: number
    max_per_method: number
    exclude_question_ids: number[]
  }
  stats: {
    candidate_total: number
    dropped_stem_length: number
    dropped_excluded: number
    dropped_duplicate: number
    dropped_method_balance: number
    selected: number
  }
  items: QuestionSelectionItem[]
  method_distribution: Record<string, number>
}

/* ===== 本机改编产物与审计 ===== */

export interface SuperscriptFinding {
  page: number
  baseline: number
  kind: 'superscript' | 'subscript' | string
  text: string
}

export interface PptxOutputAudit {
  superscript_subscript: {
    scanned_runs: number
    finding_count: number
    findings: SuperscriptFinding[]
    passed: boolean
  }
  page_budget: {
    final_page_count: number
    limit: number
    over_limit: boolean
    suggestion: string
  }
  question_pages: {
    checked: number
    pages: Record<string, number>
    problem_count: number
    problems: string[]
    passed: boolean
  }
  passed: boolean
}

export interface InsertedQuestionPage {
  question_id: number | null
  final_position: number | null
}

export interface PptxLocalOutput {
  id: string
  lesson_node_id: string
  version_number: number
  status: string
  output_filename: string
  source_file_name: string
  source_page_count: number
  final_page_count: number
  lesson_kind: string
  audit: PptxOutputAudit
  inserted_question_pages: InsertedQuestionPage[]
  worksheet_filename: string | null
  created_at: string
}

/* ===== AI 改编任务采纳 ===== */

export type TeachingPrepAdoptionCommand =
  | { kind: 'confirm_lesson_draft'; payload: LessonDraftPayload }
  | {
      kind: 'review_slide_plan'
      operation_reviews: Array<{
        operation_id: string
        decision: string
        reason: string
        planned_minutes: number
        teacher_note: string | null
        target_slide_number?: number
        position?: { x: number; y: number; width: number; height: number }
        text?: string
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

/* ===== 课堂动画 ===== */

export interface SlideAnimationScene {
  title: string
  narration: string
  duration_ms: number
  source_page: number
  highlight?: string | null
}

export interface SlideAnimationStoryboard {
  title: string
  scenes: SlideAnimationScene[]
}

export interface SlideAnimationRun {
  id: string
  lesson_node_id: string
  material_version_id: string
  material_link_id: string
  operation_id: string
  page_indexes: number[]
  storyboard: SlideAnimationStoryboard | null
  status:
    | 'running'
    | 'succeeded'
    | 'accepted'
    | 'discarded'
    | 'failed'
    | 'cancelled'
    | 'result_unknown'
  teacher_decision: 'pending' | 'accepted' | 'discarded'
  error_code: string | null
  model_call_count: number
  revision: number
  can_preview: boolean
  can_download: boolean
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface SlideAnimationRunList {
  lesson_node_id: string
  items: SlideAnimationRun[]
  billed_count: number
  billed_limit: number
  page_limit: number
}

/* ===== 改编过程观察 ===== */

export interface AdaptationTraceTool {
  name: string
  purpose: string | null
  page: number | null
  source_ref: string
}

export interface AdaptationTraceResult {
  ok: boolean
  label: string
  preview_url: string | null
}

export interface AdaptationTraceFinding {
  finding: string
  category: string
  pages: number[]
}

export interface AdaptationTraceEvent {
  round: number
  phase: 'started' | 'thinking' | 'tool_call' | 'tool_result' | 'round_done' | 'final_accepted' | 'failed' | 'findings_ready' | string
  summary: string
  thinking_excerpt: string | null
  tool: AdaptationTraceTool | null
  result: AdaptationTraceResult | null
  model_calls_used: number
  model_calls_max: number
  findings?: AdaptationTraceFinding[]
}

export interface AdaptationTrace {
  operation_id: string
  lesson_node_id: string
  status: 'running' | 'succeeded' | 'failed' | string
  model_calls_used: number
  model_calls_max: number
  events: AdaptationTraceEvent[]
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

  questionBankSections(volumeId: string, signal?: AbortSignal): Promise<QuestionBankSection[]> {
    const query = new URLSearchParams({ volume_id: volumeId })
    return apiClient.request(
      `/api/teaching-prep/question-bank-sections?${query}`,
      {
        signal,
        decode: payload => itemList(payload, questionBankSection),
      },
    )
  },

  questionSelectionPreview(
    input: QuestionSelectionPreviewInput,
    signal?: AbortSignal,
  ): Promise<QuestionSelectionPreview> {
    return apiClient.request(
      '/api/teaching-prep/question-selection/preview',
      {
        method: 'POST',
        body: input,
        signal,
        decode: questionSelectionPreview,
      },
    )
  },

  executeSlidePlanLocal(planId: string, requestToken: string): Promise<PptxLocalOutput> {
    return apiClient.request(
      `/api/teaching-prep/slide-plans/${encodeURIComponent(planId)}/execute-local`,
      {
        method: 'POST',
        body: { request_token: requestToken, confirmed: true },
        decode: pptxLocalOutput,
      },
    )
  },

  listPptxOutputs(lessonId: string, signal?: AbortSignal): Promise<PptxLocalOutput[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/pptx-outputs`,
      {
        signal,
        decode: (payload) => {
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid pptx local output list')
          }
          return payload.items.map(pptxLocalOutput)
        },
      },
    )
  },

  createWorksheet(outputId: string, requestToken: string): Promise<PptxLocalOutput> {
    return apiClient.request(
      `/api/teaching-prep/pptx-outputs/${encodeURIComponent(outputId)}/worksheet`,
      {
        method: 'POST',
        body: { request_token: requestToken, confirmed: true },
        decode: pptxLocalOutput,
      },
    )
  },

  downloadPptxOutput(outputId: string): Promise<{ blob: Blob; contentDisposition: string | null }> {
    return apiClient.download(
      `/api/teaching-prep/pptx-outputs/${encodeURIComponent(outputId)}/download`,
    )
  },

  downloadWorksheet(outputId: string): Promise<{ blob: Blob; contentDisposition: string | null }> {
    return apiClient.download(
      `/api/teaching-prep/pptx-outputs/${encodeURIComponent(outputId)}/worksheet/download`,
    )
  },

  listSlideAnimationRuns(lessonId: string, signal?: AbortSignal): Promise<SlideAnimationRunList> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/slide-animation-runs`,
      { signal, decode: slideAnimationRunList },
    )
  },

  startSlideAnimationRun(
    lessonId: string,
    input: {
      operationId: string
      materialLinkId: string
      pageIndexes: number[]
    },
  ): Promise<SlideAnimationRun> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/slide-animation-runs`,
      {
        method: 'POST',
        body: {
          operation_id: input.operationId,
          confirmed: true,
          material_link_id: input.materialLinkId,
          page_indexes: input.pageIndexes,
        },
        decode: slideAnimationRun,
      },
    )
  },

  slideAnimationRun(runId: string, signal?: AbortSignal): Promise<SlideAnimationRun> {
    return apiClient.request(
      `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}`,
      { signal, decode: slideAnimationRun },
    )
  },

  cancelSlideAnimationRun(runId: string): Promise<SlideAnimationRun> {
    return apiClient.request(
      `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST', decode: slideAnimationRun },
    )
  },

  acceptSlideAnimationRun(runId: string, expectedRevision: number): Promise<SlideAnimationRun> {
    return apiClient.request(
      `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}/accept`,
      {
        method: 'POST',
        body: { expected_revision: expectedRevision },
        decode: slideAnimationRun,
      },
    )
  },

  discardSlideAnimationRun(runId: string, expectedRevision: number): Promise<SlideAnimationRun> {
    return apiClient.request(
      `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}/discard`,
      {
        method: 'POST',
        body: { expected_revision: expectedRevision },
        decode: slideAnimationRun,
      },
    )
  },

  downloadSlideAnimation(runId: string): Promise<{ blob: Blob; contentDisposition: string | null }> {
    return apiClient.download(
      `/api/teaching-prep/slide-animation-runs/${encodeURIComponent(runId)}/download`,
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

  getAdaptationTrace(
    lessonId: string,
    operationId?: string | null,
    signal?: AbortSignal,
  ): Promise<AdaptationTrace> {
    const query = operationId
      ? `?operation_id=${encodeURIComponent(operationId)}`
      : ''
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonId)}/adaptation-trace${query}`,
      { signal, decode: adaptationTrace },
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

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function questionBankSection(payload: unknown): QuestionBankSection {
  if (
    !isRecord(payload)
    || typeof payload.section_id !== 'string' || !payload.section_id
    || typeof payload.section_name !== 'string'
    || typeof payload.chapter_id !== 'string'
    || typeof payload.chapter_name !== 'string'
    || !Number.isSafeInteger(payload.question_count)
  ) throw new Error('Invalid question bank section')
  return {
    section_id: payload.section_id,
    section_name: payload.section_name,
    chapter_id: payload.chapter_id,
    chapter_name: payload.chapter_name,
    question_count: Number(payload.question_count),
  }
}

function questionSelectionItem(payload: unknown): QuestionSelectionItem {
  if (
    !isRecord(payload)
    || !Number.isSafeInteger(payload.question_id)
    || typeof payload.question_type !== 'string'
    || typeof payload.stem !== 'string'
    || !Number.isSafeInteger(payload.difficulty)
    || !finiteNumber(payload.frequency_score)
    || !isRecord(payload.frequency)
    || !finiteNumber(payload.frequency.midterm)
    || !finiteNumber(payload.frequency.final)
    || !finiteNumber(payload.frequency.zhongkao)
    || typeof payload.method !== 'string'
    || !Array.isArray(payload.knowledge_points)
    || !payload.knowledge_points.every(item => typeof item === 'string')
    || typeof payload.has_images !== 'boolean'
    || !isRecord(payload.selection_reason)
    || typeof payload.selection_reason.frequency !== 'string'
    || typeof payload.selection_reason.difficulty !== 'string'
    || typeof payload.selection_reason.method !== 'string'
  ) throw new Error('Invalid question selection item')
  return {
    question_id: Number(payload.question_id),
    question_type: payload.question_type,
    stem: payload.stem,
    difficulty: Number(payload.difficulty),
    frequency_score: payload.frequency_score,
    frequency: {
      midterm: payload.frequency.midterm,
      final: payload.frequency.final,
      zhongkao: payload.frequency.zhongkao,
    },
    method: payload.method,
    knowledge_points: payload.knowledge_points,
    has_images: payload.has_images,
    selection_reason: {
      frequency: payload.selection_reason.frequency,
      difficulty: payload.selection_reason.difficulty,
      method: payload.selection_reason.method,
    },
  }
}

function questionSelectionPreview(payload: unknown): QuestionSelectionPreview {
  if (
    !isRecord(payload)
    || !isRecord(payload.request)
    || typeof payload.request.volume_id !== 'string'
    || !Array.isArray(payload.request.section_ids)
    || !isRecord(payload.stats)
    || !Array.isArray(payload.items)
    || !isRecord(payload.method_distribution)
  ) throw new Error('Invalid question selection preview')
  const stats = payload.stats
  if (
    !Number.isSafeInteger(stats.candidate_total)
    || !Number.isSafeInteger(stats.dropped_stem_length)
    || !Number.isSafeInteger(stats.dropped_excluded)
    || !Number.isSafeInteger(stats.dropped_duplicate)
    || !Number.isSafeInteger(stats.dropped_method_balance)
    || !Number.isSafeInteger(stats.selected)
  ) throw new Error('Invalid question selection preview')
  return {
    request: {
      volume_id: payload.request.volume_id,
      section_ids: payload.request.section_ids.map(String),
      difficulty_max: Number(payload.request.difficulty_max),
      stem_max_chars: Number(payload.request.stem_max_chars),
      limit: Number(payload.request.limit),
      max_per_method: Number(payload.request.max_per_method),
      exclude_question_ids: Array.isArray(payload.request.exclude_question_ids)
        ? payload.request.exclude_question_ids.map(Number)
        : [],
    },
    stats: {
      candidate_total: Number(stats.candidate_total),
      dropped_stem_length: Number(stats.dropped_stem_length),
      dropped_excluded: Number(stats.dropped_excluded),
      dropped_duplicate: Number(stats.dropped_duplicate),
      dropped_method_balance: Number(stats.dropped_method_balance),
      selected: Number(stats.selected),
    },
    items: payload.items.map(questionSelectionItem),
    method_distribution: Object.fromEntries(
      Object.entries(payload.method_distribution).map(([key, value]) => [key, Number(value)]),
    ),
  }
}

function superscriptFinding(payload: unknown): SuperscriptFinding {
  if (
    !isRecord(payload)
    || !Number.isSafeInteger(payload.page)
    || !Number.isSafeInteger(payload.baseline)
    || typeof payload.kind !== 'string'
    || typeof payload.text !== 'string'
  ) throw new Error('Invalid superscript finding')
  return {
    page: Number(payload.page),
    baseline: Number(payload.baseline),
    kind: payload.kind,
    text: payload.text,
  }
}

function pptxOutputAudit(payload: unknown): PptxOutputAudit {
  if (!isRecord(payload)) throw new Error('Invalid pptx output audit')
  const superscript = isRecord(payload.superscript_subscript) ? payload.superscript_subscript : {}
  const budget = isRecord(payload.page_budget) ? payload.page_budget : {}
  const questionPages = isRecord(payload.question_pages) ? payload.question_pages : {}
  return {
    superscript_subscript: {
      scanned_runs: Number(superscript.scanned_runs ?? 0),
      finding_count: Number(superscript.finding_count ?? 0),
      findings: Array.isArray(superscript.findings)
        ? superscript.findings.map(superscriptFinding)
        : [],
      passed: superscript.passed !== false,
    },
    page_budget: {
      final_page_count: Number(budget.final_page_count ?? 0),
      limit: Number(budget.limit ?? 0),
      over_limit: budget.over_limit === true,
      suggestion: typeof budget.suggestion === 'string' ? budget.suggestion : '',
    },
    question_pages: {
      checked: Number(questionPages.checked ?? 0),
      pages: isRecord(questionPages.pages)
        ? Object.fromEntries(
            Object.entries(questionPages.pages).map(([key, value]) => [key, Number(value)]),
          )
        : {},
      problem_count: Number(questionPages.problem_count ?? 0),
      problems: Array.isArray(questionPages.problems)
        ? questionPages.problems.map(String)
        : [],
      passed: questionPages.passed !== false,
    },
    passed: payload.passed !== false,
  }
}

function insertedQuestionPage(payload: unknown): InsertedQuestionPage {
  if (!isRecord(payload)) throw new Error('Invalid inserted question page')
  return {
    question_id: Number.isSafeInteger(payload.question_id) ? Number(payload.question_id) : null,
    final_position: Number.isSafeInteger(payload.final_position) ? Number(payload.final_position) : null,
  }
}

function pptxLocalOutput(payload: unknown): PptxLocalOutput {
  if (
    !isRecord(payload)
    || typeof payload.id !== 'string' || !payload.id
    || typeof payload.lesson_node_id !== 'string'
    || !Number.isSafeInteger(payload.version_number)
    || typeof payload.status !== 'string'
    || typeof payload.output_filename !== 'string'
    || typeof payload.source_file_name !== 'string'
    || !Number.isSafeInteger(payload.source_page_count)
    || !Number.isSafeInteger(payload.final_page_count)
    || typeof payload.lesson_kind !== 'string'
    || typeof payload.created_at !== 'string'
    || !(payload.worksheet_filename === null || typeof payload.worksheet_filename === 'string')
  ) throw new Error('Invalid pptx local output')
  // source_file_name 是后端脱敏后的展示名；键名含 file 令牌，先摘出再做路径键扫描。
  const { source_file_name: sourceFileName, ...rest } = payload
  assertNoPathLikeKeys(rest)
  return {
    id: payload.id,
    lesson_node_id: payload.lesson_node_id,
    version_number: Number(payload.version_number),
    status: payload.status,
    output_filename: payload.output_filename,
    source_file_name: sourceFileName,
    source_page_count: Number(payload.source_page_count),
    final_page_count: Number(payload.final_page_count),
    lesson_kind: payload.lesson_kind,
    audit: pptxOutputAudit(payload.audit ?? {}),
    inserted_question_pages: Array.isArray(payload.inserted_question_pages)
      ? payload.inserted_question_pages.map(insertedQuestionPage)
      : [],
    worksheet_filename: payload.worksheet_filename ?? null,
    created_at: payload.created_at,
  }
}

function slideAnimationRun(payload: unknown): SlideAnimationRun {
  return recordPayload(payload) as unknown as SlideAnimationRun
}

function slideAnimationRunList(payload: unknown): SlideAnimationRunList {
  const record = recordPayload(payload)
  if (!Array.isArray(record.items)) {
    throw new Error('Invalid slide animation list')
  }
  return {
    lesson_node_id: String(record.lesson_node_id ?? ''),
    items: record.items.map(slideAnimationRun),
    billed_count: Number(record.billed_count ?? 0),
    billed_limit: Number(record.billed_limit ?? 3),
    page_limit: Number(record.page_limit ?? 4),
  }
}

function adaptationTrace(payload: unknown): AdaptationTrace {
  assertNoPathLikeKeys(payload)
  if (
    !isRecord(payload)
    || typeof payload.operation_id !== 'string'
    || typeof payload.lesson_node_id !== 'string'
    || typeof payload.status !== 'string'
    || typeof payload.model_calls_used !== 'number'
    || typeof payload.model_calls_max !== 'number'
    || !Array.isArray(payload.events)
  ) {
    throw new Error('Invalid adaptation trace')
  }
  return {
    operation_id: payload.operation_id,
    lesson_node_id: payload.lesson_node_id,
    status: payload.status,
    model_calls_used: payload.model_calls_used,
    model_calls_max: payload.model_calls_max,
    events: payload.events.map(adaptationTraceEvent),
  }
}

function adaptationTraceEvent(payload: unknown): AdaptationTraceEvent {
  if (
    !isRecord(payload)
    || typeof payload.round !== 'number'
    || typeof payload.phase !== 'string'
    || typeof payload.summary !== 'string'
    || (payload.thinking_excerpt !== null && typeof payload.thinking_excerpt !== 'string')
    || typeof payload.model_calls_used !== 'number'
    || typeof payload.model_calls_max !== 'number'
  ) {
    throw new Error('Invalid adaptation trace event')
  }
  return {
    round: payload.round,
    phase: payload.phase,
    summary: payload.summary,
    thinking_excerpt: payload.thinking_excerpt,
    tool: payload.tool == null ? null : adaptationTraceTool(payload.tool),
    result: payload.result == null ? null : adaptationTraceResult(payload.result),
    model_calls_used: payload.model_calls_used,
    model_calls_max: payload.model_calls_max,
    findings: adaptationTraceFindings(payload.findings),
  }
}

function adaptationTraceFindings(payload: unknown): AdaptationTraceFinding[] | undefined {
  if (payload == null) return undefined
  if (!Array.isArray(payload)) throw new Error('Invalid adaptation trace event')
  return payload.map((item) => {
    if (
      !isRecord(item)
      || typeof item.finding !== 'string'
      || typeof item.category !== 'string'
      || !Array.isArray(item.pages)
      || item.pages.some(page => typeof page !== 'number')
    ) {
      throw new Error('Invalid adaptation trace event')
    }
    return { finding: item.finding, category: item.category, pages: item.pages }
  })
}

function adaptationTraceTool(payload: unknown): AdaptationTraceTool {
  if (
    !isRecord(payload)
    || typeof payload.name !== 'string'
    || (payload.purpose !== null && typeof payload.purpose !== 'string')
    || (payload.page !== null && typeof payload.page !== 'number')
    || typeof payload.source_ref !== 'string'
  ) {
    throw new Error('Invalid adaptation trace tool')
  }
  return {
    name: payload.name,
    purpose: payload.purpose,
    page: payload.page,
    source_ref: payload.source_ref,
  }
}

function adaptationTraceResult(payload: unknown): AdaptationTraceResult {
  if (
    !isRecord(payload)
    || typeof payload.ok !== 'boolean'
    || typeof payload.label !== 'string'
    || (payload.preview_url !== null && typeof payload.preview_url !== 'string')
  ) {
    throw new Error('Invalid adaptation trace result')
  }
  return {
    ok: payload.ok,
    label: payload.label,
    preview_url: payload.preview_url,
  }
}

function teachingPrepAIAdoption(payload: unknown): TeachingPrepAIAdoption {
  return recordPayload(payload) as unknown as TeachingPrepAIAdoption
}
