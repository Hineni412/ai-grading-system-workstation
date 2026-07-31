import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export type ActionStatus =
  | 'pending'
  | 'in_progress'
  | 'waiting'
  | 'partially_completed'
  | 'completed'
  | 'cancelled'
  | 'superseded'

export interface WorkPlan {
  plan_id: string
  revision: number
  title: string
  description: string | null
  final_deadline: string | null
  created_at: string
  updated_at: string
}

export interface ActionItem {
  action_id: string
  plan_id: string
  revision: number
  title: string
  details: string | null
  status: ActionStatus
  due_at: string | null
  waiting_for_kind: string | null
  review_at: string | null
  completion_result: string | null
  completed_at: string | null
  reopened_count: number
  transition_history: Array<Record<string, unknown>>
  depends_on_action_ids: string[]
  created_at: string
  updated_at: string
}

export interface ActionDashboard {
  as_of: string
  today: ActionItem[]
  overdue: ActionItem[]
  upcoming: ActionItem[]
  waiting: ActionItem[]
  unscheduled: ActionItem[]
}

export interface SchoolCalendar {
  configured: boolean
  revision: number
  school_day_end: string | null
  locked_dates: string[]
  working_weekdays: number[] | null
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

function plan(payload: unknown): WorkPlan {
  const value = record(payload)
  return {
    plan_id: string(value.plan_id),
    revision: number(value.revision),
    title: string(value.title),
    description: nullableString(value.description),
    final_deadline: nullableString(value.final_deadline),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
  }
}

function action(payload: unknown): ActionItem {
  const value = record(payload)
  if (!Array.isArray(value.transition_history)) throw new Error('contract')
  return {
    action_id: string(value.action_id),
    plan_id: string(value.plan_id),
    revision: number(value.revision),
    title: string(value.title),
    details: nullableString(value.details),
    status: string(value.status) as ActionStatus,
    due_at: nullableString(value.due_at),
    waiting_for_kind: nullableString(value.waiting_for_kind),
    review_at: nullableString(value.review_at),
    completion_result: nullableString(value.completion_result),
    completed_at: nullableString(value.completed_at),
    reopened_count: number(value.reopened_count),
    transition_history: value.transition_history.map(record),
    depends_on_action_ids: stringArray(value.depends_on_action_ids),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
  }
}

function list<T>(payload: unknown, decode: (item: unknown) => T): T[] {
  const value = record(payload)
  if (!Array.isArray(value.items)) throw new Error('contract')
  return value.items.map(decode)
}

function dashboard(payload: unknown): ActionDashboard {
  const value = record(payload)
  const actions = (key: string) => {
    const items = value[key]
    if (!Array.isArray(items)) throw new Error('contract')
    return items.map(action)
  }
  return {
    as_of: string(value.as_of),
    today: actions('today'),
    overdue: actions('overdue'),
    upcoming: actions('upcoming'),
    waiting: actions('waiting'),
    unscheduled: actions('unscheduled'),
  }
}

function calendar(payload: unknown): SchoolCalendar {
  const value = record(payload)
  const weekdays = value.working_weekdays
  if (
    weekdays !== null
    && (!Array.isArray(weekdays) || weekdays.some((item) => typeof item !== 'number'))
  ) throw new Error('contract')
  return {
    configured: boolean(value.configured),
    revision: number(value.revision),
    school_day_end: nullableString(value.school_day_end),
    locked_dates: stringArray(value.locked_dates),
    working_weekdays: weekdays === null ? null : [...weekdays] as number[],
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

export const actionApi = {
  listPlans(token: string) {
    return apiClient.request('/api/class-teacher/plans', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => list(payload, plan),
    })
  },
  createPlan(
    token: string,
    input: { title: string; description?: string; final_deadline?: string | null },
  ) {
    return apiClient.request('/api/class-teacher/plans', {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: plan,
    })
  },
  updatePlan(
    token: string,
    planId: string,
    input: {
      revision: number
      title: string
      description?: string
      final_deadline?: string | null
    },
  ) {
    return apiClient.request(`/api/class-teacher/plans/${planId}`, {
      method: 'PATCH',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: plan,
    })
  },
  createAction(
    token: string,
    input: {
      plan_id: string
      title: string
      details?: string
      due_at?: string | null
      depends_on_action_ids?: string[]
    },
  ) {
    return apiClient.request('/api/class-teacher/actions', {
      method: 'POST',
      headers: headers(token),
      body: {
        ...input,
        operation_id: operationId(),
        depends_on_action_ids: input.depends_on_action_ids ?? [],
      },
      decode: action,
    })
  },
  listActions(token: string) {
    return apiClient.request('/api/class-teacher/actions', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => list(payload, action),
    })
  },
  dashboard(token: string) {
    return apiClient.request('/api/class-teacher/dashboard', {
      headers: { 'x-class-teacher-session': token },
      decode: dashboard,
    })
  },
  updateAction(
    token: string,
    actionId: string,
    input: {
      revision: number
      status: ActionStatus
      due_at: string | null
      waiting_for_kind: string | null
      review_at: string | null
      completion_result: string | null
      reason: string | null
    },
  ) {
    return apiClient.request(`/api/class-teacher/actions/${actionId}`, {
      method: 'PATCH',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: action,
    })
  },
  getCalendar(token: string) {
    return apiClient.request('/api/class-teacher/calendar', {
      headers: { 'x-class-teacher-session': token },
      decode: calendar,
    })
  },
  saveCalendar(token: string, value: SchoolCalendar) {
    return apiClient.request('/api/class-teacher/calendar', {
      method: 'PUT',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        revision: value.revision,
        school_day_end: value.school_day_end,
        locked_dates: value.locked_dates,
        working_weekdays: value.working_weekdays,
      },
      decode: calendar,
    })
  },
}
