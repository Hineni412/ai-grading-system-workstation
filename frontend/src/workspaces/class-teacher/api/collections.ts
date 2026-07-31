import { apiClient } from '../../../api/client'
import type { PlanningAction } from './planning'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface MeetingDraft {
  meeting_draft_id: string
  title: string
  objective: string
  deadline_date: string | null
  final_deadline: string | null
  deadline_source: string
  actions: PlanningAction[]
  unknowns: string[]
  sensitive_findings: string[]
  is_late: boolean
  template_kind: string
}

export interface MeetingInbox {
  inbox_id: string
  revision: number
  status: 'draft' | 'cancelled' | 'confirmed'
  raw_text: string | null
  source_deleted: boolean
  delete_source_after_confirm: boolean
  model_enabled: boolean
  physical_request_count: number
  drafts: MeetingDraft[]
  confirmed_plan_ids: string[]
  confirmed_action_ids: string[]
  created_at: string
  updated_at: string
}

export type CollectionStatus =
  | 'pending_notice'
  | 'pending_submission'
  | 'submitted'
  | 'needs_review'
  | 'completed'

export interface CollectionItem {
  collection_item_id: string
  participant_ref: string
  status: CollectionStatus
  updated_at: string
}

interface Reminder {
  content: string
  basis: string
  unknowns: string[]
  status: 'unsent'
  sent_at: null
}

export interface CollectionBoard {
  board_id: string
  revision: number
  action_id: string
  title: string
  items: CollectionItem[]
  created_at: string
  updated_at: string
  total_count: number
  counts: Record<CollectionStatus, number>
  group_reminder: Reminder & { audience: string }
}

export interface IndividualReminder extends Reminder {
  board_id: string
  collection_item_id: string
  audience_ref: string
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as Record<string, unknown>
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('contract')
  return value
}

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value)
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('contract')
  return value
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('contract')
  return value
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error('contract')
  }
  return [...value]
}

function action(payload: unknown): PlanningAction {
  const value = record(payload)
  return {
    draft_action_id: string(value.draft_action_id),
    title: string(value.title),
    details: nullableString(value.details),
    due_at: nullableString(value.due_at),
    depends_on_draft_action_ids: stringArray(value.depends_on_draft_action_ids),
  }
}

function meetingDraft(payload: unknown): MeetingDraft {
  const value = record(payload)
  if (!Array.isArray(value.actions)) throw new Error('contract')
  return {
    meeting_draft_id: string(value.meeting_draft_id),
    title: string(value.title),
    objective: string(value.objective),
    deadline_date: nullableString(value.deadline_date),
    final_deadline: nullableString(value.final_deadline),
    deadline_source: string(value.deadline_source),
    actions: value.actions.map(action),
    unknowns: stringArray(value.unknowns),
    sensitive_findings: stringArray(value.sensitive_findings),
    is_late: boolean(value.is_late),
    template_kind: string(value.template_kind),
  }
}

function inbox(payload: unknown): MeetingInbox {
  const value = record(payload)
  if (!Array.isArray(value.drafts)) throw new Error('contract')
  return {
    inbox_id: string(value.inbox_id),
    revision: number(value.revision),
    status: string(value.status) as MeetingInbox['status'],
    raw_text: nullableString(value.raw_text),
    source_deleted: boolean(value.source_deleted),
    delete_source_after_confirm: boolean(value.delete_source_after_confirm),
    model_enabled: boolean(value.model_enabled),
    physical_request_count: number(value.physical_request_count),
    drafts: value.drafts.map(meetingDraft),
    confirmed_plan_ids: stringArray(value.confirmed_plan_ids),
    confirmed_action_ids: stringArray(value.confirmed_action_ids),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
  }
}

function reminder(payload: unknown): Reminder {
  const value = record(payload)
  return {
    content: string(value.content),
    basis: string(value.basis),
    unknowns: stringArray(value.unknowns),
    status: string(value.status) as 'unsent',
    sent_at: null,
  }
}

function board(payload: unknown): CollectionBoard {
  const value = record(payload)
  const rawCounts = record(value.counts)
  if (!Array.isArray(value.items)) throw new Error('contract')
  const statuses: CollectionStatus[] = [
    'pending_notice',
    'pending_submission',
    'submitted',
    'needs_review',
    'completed',
  ]
  const counts = Object.fromEntries(
    statuses.map((status) => [status, number(rawCounts[status])]),
  ) as Record<CollectionStatus, number>
  const group = record(value.group_reminder)
  return {
    board_id: string(value.board_id),
    revision: number(value.revision),
    action_id: string(value.action_id),
    title: string(value.title),
    items: value.items.map((item) => {
      const entry = record(item)
      return {
        collection_item_id: string(entry.collection_item_id),
        participant_ref: string(entry.participant_ref),
        status: string(entry.status) as CollectionStatus,
        updated_at: string(entry.updated_at),
      }
    }),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
    total_count: number(value.total_count),
    counts,
    group_reminder: {
      ...reminder(group),
      audience: string(group.audience),
    },
  }
}

function headers(token: string): Record<string, string> {
  return {
    'x-class-teacher-session': token,
    'x-class-teacher-client': CLIENT_HEADER,
  }
}

function operationId(): string {
  return globalThis.crypto.randomUUID()
}

export const collectionApi = {
  listInboxes(token: string) {
    return apiClient.request('/api/class-teacher/meeting-inboxes', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(inbox)
      },
    })
  },
  importMeeting(token: string, rawText: string) {
    return apiClient.request('/api/class-teacher/meeting-inboxes', {
      method: 'POST',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        raw_text: rawText,
        reference_at: new Date().toISOString(),
      },
      decode: inbox,
    })
  },
  updateInbox(token: string, value: MeetingInbox) {
    return apiClient.request(`/api/class-teacher/meeting-inboxes/${value.inbox_id}`, {
      method: 'PUT',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        revision: value.revision,
        drafts: value.drafts,
        delete_source_after_confirm: value.delete_source_after_confirm,
      },
      decode: inbox,
    })
  },
  confirmInbox(token: string, value: MeetingInbox) {
    return apiClient.request(`/api/class-teacher/meeting-inboxes/${value.inbox_id}/confirm`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), revision: value.revision },
      decode: record,
    })
  },
  cancelInbox(token: string, value: MeetingInbox) {
    return apiClient.request(`/api/class-teacher/meeting-inboxes/${value.inbox_id}/cancel`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), revision: value.revision },
      decode: inbox,
    })
  },
  listBoards(token: string) {
    return apiClient.request('/api/class-teacher/collection-boards', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(board)
      },
    })
  },
  createBoard(
    token: string,
    input: { action_id: string; title: string; participant_refs: string[] },
  ) {
    return apiClient.request('/api/class-teacher/collection-boards', {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: board,
    })
  },
  updateItem(
    token: string,
    value: CollectionBoard,
    itemId: string,
    status: CollectionStatus,
  ) {
    return apiClient.request(
      `/api/class-teacher/collection-boards/${value.board_id}/items/${itemId}`,
      {
        method: 'PATCH',
        headers: headers(token),
        body: { operation_id: operationId(), revision: value.revision, status },
        decode: board,
      },
    )
  },
  individualReminder(token: string, boardId: string, itemId: string) {
    return apiClient.request(
      `/api/class-teacher/collection-boards/${boardId}/items/${itemId}/reminder`,
      {
        headers: { 'x-class-teacher-session': token },
        decode: (payload) => {
          const value = record(payload)
          return {
            ...reminder(value),
            board_id: string(value.board_id),
            collection_item_id: string(value.collection_item_id),
            audience_ref: string(value.audience_ref),
          }
        },
      },
    )
  },
}
