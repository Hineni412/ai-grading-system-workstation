import { apiClient } from '../../../api/client'

export type IntakeDomain = 'student_growth' | 'student_support' | 'conflict_safety' | 'class_operations' | 'activities_culture' | 'school_coordination'
export type HandlingMode = 'record' | 'plan_calendar' | 'sop'

export interface HomeroomPreference {
  homeroom_class: string | null
  revision: number
  classes: string[]
  source_revision: string
  updated_at?: string
}

export interface IntakeTurn {
  turn_id: string
  conversation_id: string
  sequence: number
  operation_id: string
  teacher_message: string
  assistant_message: string | null
  clarification_questions: string[]
  task_id: string | null
  task_state: string
  created_at: string
  updated_at: string
}

export interface IntakeHandoffSummary {
  handoff_id: string
  draft_id: string
  work_item_id: string
  turn_id: string
  domain: IntakeDomain
  handling_mode: HandlingMode
  intent: string
  destination_key: string
  draft_revision: number
  adoption_state: 'pending' | 'opened' | 'adoption_started' | 'adopted' | 'reverted' | 'discarded' | 'stale'
  missing_fields: string[]
  subject_ref_count: number
  subject_id: string | null
  auto_open_allowed: boolean
}

export interface IntakeConversation {
  conversation_id: string
  revision: number
  state: string
  homeroom_class: string | null
  focused_subject_id?: string | null
  focused_subject_revision?: string | null
  created_at: string
  updated_at: string
  turns: IntakeTurn[]
  handoffs: IntakeHandoffSummary[]
}

export interface IntakeConversationSummary {
  conversation_id: string
  revision: number
  state: string
  homeroom_class: string | null
  focused_subject_id?: string | null
  focused_subject_revision?: string | null
  first_message: string | null
  pending_count: number
  updated_at: string
}

export interface SpeechCapabilities {
  available: boolean
  status: 'ready' | 'dependency_missing' | 'model_missing'
  engine: string
  offline: true
  sample_rate: 16000
  max_duration_seconds: number
  max_audio_bytes: number
  accepted_content_type: 'audio/wav'
  cloud_audio: CloudAudioCapabilities
}

export interface CloudAudioCapabilities {
  available: boolean
  status: 'ready' | 'profile_missing'
  provider: 'configured_model'
  model: string | null
  destination_fingerprint: string
}

export interface SpeechTranscription {
  text: string
  duration_seconds: number
  engine: string
  audio_retained: false
}

export interface HandoffDraft {
  contract_version: 'teacher_workspace_handoff.v1'
  handoff_id: string
  work_item_id: string
  conversation_id: string
  turn_id: string
  draft_id: string
  draft_revision: number
  domain: IntakeDomain
  handling_mode: HandlingMode
  intent: string
  destination_key: string
  adoption_id: string
  adoption_state: string
  content: Record<string, unknown>
  subject_refs: Array<{ kind: string; id: string; revision: string }>
  missing_fields: string[]
  return_context: { destination_key: string; focus_ref: string }
}

export interface DraftRevisionSnapshot {
  request_id: string
  handoff_id: string
  source_draft_revision: number
  task_id: string | null
  task_state: string
  created_at: string
  updated_at: string
}

const domains = new Set<IntakeDomain>(['student_growth', 'student_support', 'conflict_safety', 'class_operations', 'activities_culture', 'school_coordination'])
const modes = new Set<HandlingMode>(['record', 'plan_calendar', 'sop'])
const destinations = new Set([
  'class_teacher.student.record',
  'class_teacher.affair.record',
  'class_teacher.plan.calendar',
  'class_teacher.affair.sop',
])
const adoptionStates = new Set(['pending', 'opened', 'adoption_started', 'adopted', 'reverted', 'discarded', 'stale'])

function invalid(): never {
  throw new Error('班主任工作台返回了无法识别的数据')
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string') return invalid()
  return value
}

function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) return invalid()
  return value
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) return invalid()
  return value
}

function domain(value: unknown): IntakeDomain {
  const candidate = text(value) as IntakeDomain
  if (!domains.has(candidate)) return invalid()
  return candidate
}

function mode(value: unknown): HandlingMode {
  const candidate = text(value) as HandlingMode
  if (!modes.has(candidate)) return invalid()
  return candidate
}

function decodeSubjectRef(value: unknown): { kind: string; id: string; revision: string } {
  const item = record(value)
  if (item.kind !== 'student') return invalid()
  return { kind: 'student', id: text(item.id), revision: text(item.revision) }
}

function decodeTurn(value: unknown): IntakeTurn {
  const item = record(value)
  return {
    turn_id: text(item.turn_id), conversation_id: text(item.conversation_id),
    sequence: integer(item.sequence), operation_id: text(item.operation_id),
    teacher_message: text(item.teacher_message),
    assistant_message: item.assistant_message === null ? null : text(item.assistant_message),
    clarification_questions: list(item.clarification_questions).map(text),
    task_id: item.task_id === null ? null : text(item.task_id), task_state: text(item.task_state),
    created_at: text(item.created_at), updated_at: text(item.updated_at),
  }
}

function decodeHandoffSummary(value: unknown): IntakeHandoffSummary {
  const item = record(value)
  const destination = text(item.destination_key)
  const adoptionState = text(item.adoption_state)
  if (!destinations.has(destination) || !adoptionStates.has(adoptionState)) return invalid()
  if (typeof item.auto_open_allowed !== 'boolean') return invalid()
  return {
    handoff_id: text(item.handoff_id), draft_id: text(item.draft_id),
    work_item_id: text(item.work_item_id), turn_id: text(item.turn_id),
    domain: domain(item.domain), handling_mode: mode(item.handling_mode), intent: text(item.intent),
    destination_key: destination, draft_revision: integer(item.draft_revision),
    adoption_state: adoptionState as IntakeHandoffSummary['adoption_state'],
    missing_fields: list(item.missing_fields).map(text), subject_ref_count: integer(item.subject_ref_count),
    subject_id: item.subject_id == null ? null : text(item.subject_id),
    auto_open_allowed: item.auto_open_allowed,
  }
}

export function decodeIntakeConversation(value: unknown): IntakeConversation {
  const item = record(value)
  return {
    conversation_id: text(item.conversation_id), revision: integer(item.revision), state: text(item.state),
    homeroom_class: item.homeroom_class === null ? null : text(item.homeroom_class),
    focused_subject_id: item.focused_subject_id == null ? null : text(item.focused_subject_id),
    focused_subject_revision: item.focused_subject_revision == null ? null : text(item.focused_subject_revision),
    created_at: text(item.created_at), updated_at: text(item.updated_at),
    turns: list(item.turns).map(decodeTurn), handoffs: list(item.handoffs).map(decodeHandoffSummary),
  }
}

export function decodeHandoffDraft(value: unknown): HandoffDraft {
  const item = record(value)
  const destination = text(item.destination_key)
  const adoptionState = text(item.adoption_state)
  if (item.contract_version !== 'teacher_workspace_handoff.v1' || !destinations.has(destination) || !adoptionStates.has(adoptionState)) return invalid()
  return {
    contract_version: 'teacher_workspace_handoff.v1',
    handoff_id: text(item.handoff_id), work_item_id: text(item.work_item_id),
    conversation_id: text(item.conversation_id), turn_id: text(item.turn_id),
    draft_id: text(item.draft_id), draft_revision: integer(item.draft_revision),
    domain: domain(item.domain), handling_mode: mode(item.handling_mode), intent: text(item.intent),
    destination_key: destination, adoption_id: text(item.adoption_id), adoption_state: adoptionState,
    content: record(item.content), subject_refs: list(item.subject_refs).map(decodeSubjectRef),
    missing_fields: list(item.missing_fields).map(text),
    return_context: (() => {
      const context = record(item.return_context)
      if (context.destination_key !== 'class_teacher.home') return invalid()
      return { destination_key: 'class_teacher.home', focus_ref: text(context.focus_ref) }
    })(),
  }
}

function decodePreference(value: unknown): HomeroomPreference {
  const item = record(value)
  return {
    homeroom_class: item.homeroom_class === null ? null : text(item.homeroom_class),
    revision: integer(item.revision), classes: list(item.classes).map(text), source_revision: text(item.source_revision),
    ...(item.updated_at === undefined ? {} : { updated_at: text(item.updated_at) }),
  }
}

function decodeSpeechCapabilities(value: unknown): SpeechCapabilities {
  const item = record(value)
  if (typeof item.available !== 'boolean' || item.offline !== true || item.sample_rate !== 16000 || item.accepted_content_type !== 'audio/wav') return invalid()
  const status = text(item.status)
  const maxDuration = item.max_duration_seconds
  const maxBytes = item.max_audio_bytes
  if (!['ready', 'dependency_missing', 'model_missing'].includes(status)) return invalid()
  if (typeof maxDuration !== 'number' || !Number.isFinite(maxDuration) || maxDuration <= 0) return invalid()
  if (typeof maxBytes !== 'number' || !Number.isInteger(maxBytes) || maxBytes <= 0) return invalid()
  const cloudItem = record(item.cloud_audio)
  const cloudStatus = text(cloudItem.status)
  if (
    typeof cloudItem.available !== 'boolean'
    || cloudItem.provider !== 'configured_model'
    || !['ready', 'profile_missing'].includes(cloudStatus)
  ) return invalid()
  return {
    available: item.available,
    status: status as SpeechCapabilities['status'],
    engine: text(item.engine),
    offline: true,
    sample_rate: 16000,
    max_duration_seconds: maxDuration,
    max_audio_bytes: maxBytes,
    accepted_content_type: 'audio/wav',
    cloud_audio: {
      available: cloudItem.available,
      status: cloudStatus as CloudAudioCapabilities['status'],
      provider: 'configured_model',
      model: cloudItem.model === null ? null : text(cloudItem.model),
      destination_fingerprint: text(cloudItem.destination_fingerprint),
    },
  }
}

function decodeSpeechTranscription(value: unknown): SpeechTranscription {
  const item = record(value)
  const transcript = text(item.text)
  const duration = item.duration_seconds
  if (!transcript.trim() || typeof duration !== 'number' || !Number.isFinite(duration) || duration < 0 || item.audio_retained !== false) return invalid()
  return {
    text: transcript,
    duration_seconds: duration,
    engine: text(item.engine),
    audio_retained: false,
  }
}

function decodeConversationSummary(value: unknown): IntakeConversationSummary {
  const item = record(value)
  return {
    conversation_id: text(item.conversation_id), revision: integer(item.revision), state: text(item.state),
    homeroom_class: item.homeroom_class === null ? null : text(item.homeroom_class),
    focused_subject_id: item.focused_subject_id == null ? null : text(item.focused_subject_id),
    focused_subject_revision: item.focused_subject_revision == null ? null : text(item.focused_subject_revision),
    first_message: item.first_message === null ? null : text(item.first_message),
    pending_count: integer(item.pending_count), updated_at: text(item.updated_at),
  }
}

function decodeDraftRevision(value: unknown): DraftRevisionSnapshot {
  const item = record(value)
  return {
    request_id: text(item.request_id), handoff_id: text(item.handoff_id),
    source_draft_revision: integer(item.source_draft_revision),
    task_id: item.task_id === null ? null : text(item.task_id), task_state: text(item.task_state),
    created_at: text(item.created_at), updated_at: text(item.updated_at),
  }
}

function operationId(): string {
  return crypto.randomUUID()
}

function headers(): Record<string, string> {
  return {
    'x-class-teacher-client': 'class-teacher-browser-v1',
  }
}

export const intakeApi = {
  speechCapabilities() {
    return apiClient.request('/api/class-teacher/intake/speech/capabilities', {
      decode: decodeSpeechCapabilities,
    })
  },
  transcribeSpeech(wav: Blob, signal?: AbortSignal) {
    return apiClient.request('/api/class-teacher/intake/speech/transcriptions', {
      method: 'POST', rawBody: wav, signal, timeoutMs: 90_000,
      headers: { ...headers(), 'content-type': 'audio/wav' },
      decode: decodeSpeechTranscription,
    })
  },
  homeroom() {
    return apiClient.request('/api/class-teacher/intake/preferences/homeroom-class', {
      decode: decodePreference,
    })
  },
  setHomeroom(preference: HomeroomPreference, homeroomClass: string | null) {
    return apiClient.request('/api/class-teacher/intake/preferences/homeroom-class', {
      method: 'PUT',
      headers: headers(),
      body: {
        homeroom_class: homeroomClass || null,
        expected_revision: preference.revision,
        expected_source_revision: preference.source_revision,
        operation_id: operationId(),
      },
      decode: decodePreference,
    })
  },
  startConversation() {
    return apiClient.request('/api/class-teacher/intake/conversations', {
      method: 'POST', headers: headers(), decode: decodeIntakeConversation,
    })
  },
  startStudentConversation(subjectId: string) {
    return apiClient.request('/api/class-teacher/intake/conversations', {
      method: 'POST', headers: headers(), body: { subject_id: subjectId }, decode: decodeIntakeConversation,
    })
  },
  listConversations(limit = 5) {
    return apiClient.request(`/api/class-teacher/intake/conversations?limit=${limit}`, {
      decode: (value) => list(record(value).items).map(decodeConversationSummary),
    })
  },
  conversation(id: string) {
    return apiClient.request(`/api/class-teacher/intake/conversations/${encodeURIComponent(id)}`, {
      decode: decodeIntakeConversation,
    })
  },
  deleteConversation(id: string) {
    return apiClient.request(`/api/class-teacher/intake/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: headers(),
      decode: (value) => {
        const item = record(value)
        return { conversation_id: text(item.conversation_id), deleted: item.deleted === true }
      },
    })
  },
  appendTurn(conversation: IntakeConversation, message: string, operation = operationId()) {
    return apiClient.request(`/api/class-teacher/intake/conversations/${encodeURIComponent(conversation.conversation_id)}/turns`, {
      method: 'POST', headers: headers(),
      body: { expected_revision: conversation.revision, message, operation_id: operation },
      decode: decodeIntakeConversation,
    })
  },
  sendCloudAudio(
    conversation: IntakeConversation,
    wav: Blob,
    operation: string,
    modelFingerprint: string,
    signal?: AbortSignal,
  ) {
    return apiClient.request(`/api/class-teacher/intake/conversations/${encodeURIComponent(conversation.conversation_id)}/audio-turns`, {
      method: 'POST', rawBody: wav, signal, timeoutMs: 120_000,
      headers: {
        ...headers(),
        'content-type': 'audio/wav',
        'x-class-teacher-conversation-revision': String(conversation.revision),
        'x-class-teacher-operation-id': operation,
        'x-class-teacher-model-fingerprint': modelFingerprint,
      },
      decode: decodeIntakeConversation,
    })
  },
  manualRoute(turnId: string, mode: HandlingMode) {
    return apiClient.request(`/api/class-teacher/intake/turns/${encodeURIComponent(turnId)}/manual-route`, {
      method: 'POST', headers: headers(), body: { mode },
      decode: decodeIntakeConversation,
    })
  },
  handoff(id: string) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(id)}`, {
      decode: decodeHandoffDraft,
    })
  },
  updateDraft(draft: HandoffDraft, content: Record<string, unknown>, subjectRefs = draft.subject_refs) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(draft.handoff_id)}/draft`, {
      method: 'PUT', headers: headers(),
      body: { expected_revision: draft.draft_revision, content, subject_refs: subjectRefs },
      decode: decodeHandoffDraft,
    })
  },
  requestDraftRevision(draft: HandoffDraft, instruction: string) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(draft.handoff_id)}/ai-revisions`, {
      method: 'POST', headers: headers(),
      body: { expected_revision: draft.draft_revision, instruction, operation_id: operationId() },
      decode: decodeDraftRevision,
    })
  },
  draftRevision(id: string) {
    return apiClient.request(`/api/class-teacher/intake/draft-revisions/${encodeURIComponent(id)}`, {
      decode: decodeDraftRevision,
    })
  },
  discard(id: string) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(id)}/discard`, {
      method: 'POST', headers: headers(), decode: record,
    })
  },
  adopt(draft: HandoffDraft, targetRevision: string) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(draft.handoff_id)}/adopt`, {
      method: 'POST', headers: headers(),
      body: { draft_revision: draft.draft_revision, target_revision: targetRevision, operation_id: operationId() },
      decode: record,
    })
  },
  revertProfile(id: string) {
    return apiClient.request(`/api/class-teacher/intake/handoffs/${encodeURIComponent(id)}/revert-profile`, {
      method: 'POST', headers: headers(), decode: record,
    })
  },
}
