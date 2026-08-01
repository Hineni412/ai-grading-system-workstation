import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface StudentCardEntry {
  entry_id: string
  subject_id?: string
  teacher_quote: string
  portrait: {
    summary: string
    strengths: string[]
    needs: string[]
    open_questions: string[]
  }
  sop: {
    title: string
    steps: string[]
    review_date: string | null
  }
  model_draft: string
  projection_state: 'pending' | 'applied'
  created_at: string
}

export interface StudentCard {
  subject: {
    subject_id: string
    source_student_id: string
    display_name: string
    class_label: string | null
  }
  entries: StudentCardEntry[]
  existing_records: Array<Record<string, unknown>>
  support_plans: Array<Record<string, unknown>>
}

export interface FinalStructure {
  portrait: StudentCardEntry['portrait']
  sop: StudentCardEntry['sop']
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

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('contract')
  return value.map(string)
}

function entry(value: unknown): StudentCardEntry {
  const item = record(value)
  const portrait = record(item.portrait)
  const sop = record(item.sop)
  return {
    entry_id: string(item.entry_id),
    subject_id: item.subject_id === undefined ? undefined : string(item.subject_id),
    teacher_quote: string(item.teacher_quote),
    portrait: {
      summary: string(portrait.summary),
      strengths: stringList(portrait.strengths),
      needs: stringList(portrait.needs),
      open_questions: stringList(portrait.open_questions),
    },
    sop: {
      title: string(sop.title),
      steps: stringList(sop.steps),
      review_date: nullableString(sop.review_date),
    },
    model_draft: string(item.model_draft),
    projection_state: string(item.projection_state) as StudentCardEntry['projection_state'],
    created_at: string(item.created_at),
  }
}

function card(value: unknown): StudentCard {
  const item = record(value)
  const subject = record(item.subject)
  if (
    !Array.isArray(item.entries)
    || !Array.isArray(item.existing_records)
    || !Array.isArray(item.support_plans)
  ) throw new Error('contract')
  return {
    subject: {
      subject_id: string(subject.subject_id),
      source_student_id: string(subject.source_student_id),
      display_name: string(subject.display_name),
      class_label: nullableString(subject.class_label),
    },
    entries: item.entries.map(entry),
    existing_records: item.existing_records.map(record),
    support_plans: item.support_plans.map(record),
  }
}

function cards(value: unknown): StudentCard[] {
  const item = record(value)
  if (!Array.isArray(item.items)) throw new Error('contract')
  return item.items.map(card)
}

function headers(token: string): Record<string, string> {
  return {
    'x-class-teacher-client': CLIENT_HEADER,
    'x-class-teacher-session': token,
  }
}

export const studentCardApi = {
  list(token: string) {
    return apiClient.request('/api/class-teacher/student-cards', {
      headers: { 'x-class-teacher-session': token },
      decode: cards,
    })
  },
  confirm(
    token: string,
    subjectId: string,
    modelOperationId: string,
    value: FinalStructure,
    operationId: string,
  ) {
    return apiClient.request(
      `/api/class-teacher/student-cards/${subjectId}/entries/confirm`,
      {
        method: 'POST',
        headers: headers(token),
        body: {
          model_operation_id: modelOperationId,
          operation_id: operationId,
          portrait: value.portrait,
          sop: value.sop,
        },
        decode: entry,
      },
    )
  },
}
