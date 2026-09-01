import { apiClient } from './client'

export type TimetableSlotKind = 'lesson' | 'custom'
export type TimetableCellSource = 'regular' | 'override' | 'empty'
export type OverrideAction = 'set' | 'clear'

export interface TimetableSlot {
  slot_key: string
  kind: TimetableSlotKind
  custom_slot_id: string | null
  label: string
  start_text: string | null
  end_text: string | null
  position: number | null
  lesson_no: number | null
}

export interface TimetableDay {
  day_of_week: number
  date: string
}

export interface TimetableCell {
  day_of_week: number
  slot_key: string
  course_text: string
  class_label: string
  source: TimetableCellSource
  override_id: string | null
  note: string | null
}

export interface TimetableToday {
  date: string
  day_of_week: number
  in_week: boolean
}

export interface TimetableWeek {
  week_start: string
  week_no: number | null
  days: TimetableDay[]
  slots: TimetableSlot[]
  cells: TimetableCell[]
  today: TimetableToday
}

export interface WeekAnchor {
  anchor_monday: string
  week_no: number
  updated_at: string
}

export interface OverrideOperation {
  day_of_week: number
  slot_key: string
  action: OverrideAction
  course_text?: string
  class_label?: string
  note?: string
}

export interface AppliedOverride {
  override_id: string
  week_start: string
  day_of_week: number
  slot_key: string
  action: OverrideAction
  course_text: string
  class_label: string
  note: string | null
  created_at: string
}

export interface LessonNote {
  id: string
  note_date: string
  slot_key: string
  class_label: string
  content_text: string
  homework_text: string | null
  created_at: string
}

export interface RecentNoteClassGroup {
  class_label: string
  notes: LessonNote[]
}

export type DailyTableStatus = 'active' | 'archived'
export type DailyTableColumnType = 'text' | 'number' | 'check' | 'select' | 'date'

export interface RosterClass {
  class_label: string
  student_count: number
}

export interface DailyTableSummary {
  id: string
  title: string
  status: DailyTableStatus
  column_count: number
  row_count: number
  created_at: string
  updated_at: string
}

export interface DailyTableColumn {
  id: string
  name: string
  col_type: DailyTableColumnType
  options: string[]
  position: number
}

export interface DailyTableRow {
  id: string
  student_ref: string
  display_name: string
  class_label: string
  position: number
}

/** 稀疏单元格：row_id → column_id → 文本值。 */
export type DailyTableCells = Record<string, Record<string, string>>

export interface DailyTableDetail {
  id: string
  title: string
  status: DailyTableStatus
  created_at: string
  updated_at: string
  columns: DailyTableColumn[]
  rows: DailyTableRow[]
  cells: DailyTableCells
}

export interface DailyTableCreateResult extends DailyTableDetail {
  /** 所选班级中名单为空的班（这些班没有生成学生行）。 */
  empty_class_labels: string[]
}

export interface DailyTableColumnInput {
  name: string
  col_type: DailyTableColumnType
  options?: string[]
  position?: number
}

export interface DailyTableColumnPatch {
  name?: string
  col_type?: DailyTableColumnType
  options?: string[]
  position?: number
}

export interface DailyTableCellUpdate {
  row_id: string
  column_id: string
  value_text: string
}

const slotKinds = new Set<TimetableSlotKind>(['lesson', 'custom'])
const cellSources = new Set<TimetableCellSource>(['regular', 'override', 'empty'])
const overrideActions = new Set<OverrideAction>(['set', 'clear'])
const tableStatuses = new Set<DailyTableStatus>(['active', 'archived'])
const tableColumnTypes = new Set<DailyTableColumnType>(['text', 'number', 'check', 'select', 'date'])

function invalid(): never {
  throw new Error('日常管理接口返回了无法识别的数据')
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

function nullableText(value: unknown): string | null {
  if (value === null) return null
  return text(value)
}

function nullableInteger(value: unknown): number | null {
  if (value === null) return null
  return integer(value)
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) return invalid()
  return value
}

function decodeSlot(value: unknown): TimetableSlot {
  const item = record(value)
  const kind = text(item.kind) as TimetableSlotKind
  if (!slotKinds.has(kind)) return invalid()
  return {
    slot_key: text(item.slot_key),
    kind,
    custom_slot_id: nullableText(item.custom_slot_id),
    label: text(item.label),
    start_text: nullableText(item.start_text),
    end_text: nullableText(item.end_text),
    position: nullableInteger(item.position),
    lesson_no: nullableInteger(item.lesson_no),
  }
}

function decodeDay(value: unknown): TimetableDay {
  const item = record(value)
  return { day_of_week: integer(item.day_of_week), date: text(item.date) }
}

function decodeCell(value: unknown): TimetableCell {
  const item = record(value)
  const source = text(item.source) as TimetableCellSource
  if (!cellSources.has(source)) return invalid()
  return {
    day_of_week: integer(item.day_of_week),
    slot_key: text(item.slot_key),
    course_text: text(item.course_text),
    class_label: text(item.class_label),
    source,
    override_id: nullableText(item.override_id),
    note: nullableText(item.note),
  }
}

function decodeWeek(value: unknown): TimetableWeek {
  const item = record(value)
  const today = record(item.today)
  if (typeof today.in_week !== 'boolean') return invalid()
  return {
    week_start: text(item.week_start),
    week_no: nullableInteger(item.week_no),
    days: list(item.days).map(decodeDay),
    slots: list(item.slots).map(decodeSlot),
    cells: list(item.cells).map(decodeCell),
    today: {
      date: text(today.date),
      day_of_week: integer(today.day_of_week),
      in_week: today.in_week,
    },
  }
}

function decodeWeekAnchor(value: unknown): WeekAnchor | null {
  if (value === null) return null
  const item = record(value)
  return {
    anchor_monday: text(item.anchor_monday),
    week_no: integer(item.week_no),
    updated_at: text(item.updated_at),
  }
}

function decodeAppliedOverride(value: unknown): AppliedOverride {
  const item = record(value)
  const action = text(item.action) as OverrideAction
  if (!overrideActions.has(action)) return invalid()
  return {
    override_id: text(item.override_id),
    week_start: text(item.week_start),
    day_of_week: integer(item.day_of_week),
    slot_key: text(item.slot_key),
    action,
    course_text: text(item.course_text),
    class_label: text(item.class_label),
    note: nullableText(item.note),
    created_at: text(item.created_at),
  }
}

function decodeNote(value: unknown): LessonNote {
  const item = record(value)
  return {
    id: text(item.id),
    note_date: text(item.note_date),
    slot_key: text(item.slot_key),
    class_label: text(item.class_label),
    content_text: text(item.content_text),
    homework_text: nullableText(item.homework_text),
    created_at: text(item.created_at),
  }
}

function decodeRecentNotes(value: unknown): RecentNoteClassGroup[] {
  const item = record(value)
  return list(item.classes).map((group) => {
    const entry = record(group)
    return {
      class_label: text(entry.class_label),
      notes: list(entry.notes).map(decodeNote),
    }
  })
}

function decodeTableStatus(value: unknown): DailyTableStatus {
  const status = text(value) as DailyTableStatus
  if (!tableStatuses.has(status)) return invalid()
  return status
}

function decodeColumnType(value: unknown): DailyTableColumnType {
  const colType = text(value) as DailyTableColumnType
  if (!tableColumnTypes.has(colType)) return invalid()
  return colType
}

function decodeRosterClasses(value: unknown): RosterClass[] {
  const item = record(value)
  return list(item.classes).map((entry) => {
    const klass = record(entry)
    return {
      class_label: text(klass.class_label),
      student_count: integer(klass.student_count),
    }
  })
}

function decodeTableSummary(value: unknown): DailyTableSummary {
  const item = record(value)
  return {
    id: text(item.id),
    title: text(item.title),
    status: decodeTableStatus(item.status),
    column_count: integer(item.column_count),
    row_count: integer(item.row_count),
    created_at: text(item.created_at),
    updated_at: text(item.updated_at),
  }
}

function decodeTableColumn(value: unknown): DailyTableColumn {
  const item = record(value)
  return {
    id: text(item.id),
    name: text(item.name),
    col_type: decodeColumnType(item.col_type),
    options: list(item.options).map(text),
    position: integer(item.position),
  }
}

function decodeTableRow(value: unknown): DailyTableRow {
  const item = record(value)
  return {
    id: text(item.id),
    student_ref: text(item.student_ref),
    display_name: text(item.display_name),
    class_label: text(item.class_label),
    position: integer(item.position),
  }
}

function decodeTableCells(value: unknown): DailyTableCells {
  const item = record(value)
  const cells: DailyTableCells = {}
  for (const [rowId, rowCells] of Object.entries(item)) {
    const values: Record<string, string> = {}
    for (const [columnId, cellValue] of Object.entries(record(rowCells))) {
      values[columnId] = text(cellValue)
    }
    cells[rowId] = values
  }
  return cells
}

function decodeTableDetail(value: unknown): DailyTableDetail {
  const item = record(value)
  const table = record(item.table)
  return {
    id: text(table.id),
    title: text(table.title),
    status: decodeTableStatus(table.status),
    created_at: text(table.created_at),
    updated_at: text(table.updated_at),
    columns: list(item.columns).map(decodeTableColumn),
    rows: list(item.rows).map(decodeTableRow),
    cells: decodeTableCells(item.cells),
  }
}

function decodeTableCreateResult(value: unknown): DailyTableCreateResult {
  const item = record(value)
  return {
    ...decodeTableDetail(item),
    empty_class_labels: list(item.empty_class_labels).map(text),
  }
}

function headers(): Record<string, string> {
  return {
    'x-class-teacher-client': 'class-teacher-browser-v1',
  }
}

const BASE = '/api/class-teacher/daily'

export const dailyApi = {
  timetable(weekStart?: string) {
    const query = weekStart ? `?week_start=${encodeURIComponent(weekStart)}` : ''
    return apiClient.request(`${BASE}/timetable${query}`, { decode: decodeWeek })
  },
  setRegularCell(input: {
    day_of_week: number
    slot_key: string
    course_text: string
    class_label: string
  }) {
    return apiClient.request(`${BASE}/timetable/regular-cell`, {
      method: 'PUT',
      headers: headers(),
      body: input,
      decode: decodeCell,
    })
  },
  createCustomSlot(input: {
    label: string
    start_text?: string
    end_text?: string
    position: number
  }) {
    return apiClient.request(`${BASE}/timetable/custom-slots`, {
      method: 'POST',
      headers: headers(),
      body: input,
      decode: decodeSlot,
    })
  },
  deleteCustomSlot(customSlotId: string) {
    return apiClient.request(`${BASE}/timetable/custom-slots/${encodeURIComponent(customSlotId)}`, {
      method: 'DELETE',
      headers: headers(),
      decode: record,
    })
  },
  applyOverrides(weekStart: string, ops: OverrideOperation[]) {
    return apiClient.request(`${BASE}/timetable/overrides`, {
      method: 'POST',
      headers: headers(),
      body: { week_start: weekStart, ops },
      decode: (value) => list(record(value).overrides).map(decodeAppliedOverride),
    })
  },
  deleteOverride(overrideId: string) {
    return apiClient.request(`${BASE}/timetable/overrides/${encodeURIComponent(overrideId)}`, {
      method: 'DELETE',
      headers: headers(),
      decode: record,
    })
  },
  weekAnchor() {
    return apiClient.request(`${BASE}/timetable/week-anchor`, { decode: decodeWeekAnchor })
  },
  setWeekAnchor(date: string, weekNo: number) {
    return apiClient.request(`${BASE}/timetable/week-anchor`, {
      method: 'PUT',
      headers: headers(),
      body: { date, week_no: weekNo },
      decode: (value) => {
        const anchor = decodeWeekAnchor(value)
        if (anchor === null) return invalid()
        return anchor
      },
    })
  },
  addNote(input: {
    note_date: string
    slot_key: string
    class_label: string
    content_text: string
    homework_text?: string
  }) {
    return apiClient.request(`${BASE}/notes`, {
      method: 'POST',
      headers: headers(),
      body: input,
      decode: decodeNote,
    })
  },
  recentNotes(classLabels: string[], limit = 10) {
    const params = new URLSearchParams()
    if (classLabels.length) params.set('class_labels', classLabels.join(','))
    params.set('limit', String(limit))
    return apiClient.request(`${BASE}/notes/recent?${params.toString()}`, {
      decode: decodeRecentNotes,
    })
  },
  rosterClasses() {
    return apiClient.request(`${BASE}/roster-classes`, { decode: decodeRosterClasses })
  },
  tables(status: 'all' | DailyTableStatus = 'all') {
    return apiClient.request(`${BASE}/tables?status=${status}`, {
      decode: (value) => list(record(value).tables).map(decodeTableSummary),
    })
  },
  createTable(input: {
    title: string
    class_labels: string[]
    columns?: DailyTableColumnInput[]
  }) {
    return apiClient.request(`${BASE}/tables`, {
      method: 'POST',
      headers: headers(),
      body: input,
      decode: decodeTableCreateResult,
    })
  },
  table(tableId: string) {
    return apiClient.request(`${BASE}/tables/${encodeURIComponent(tableId)}`, {
      decode: decodeTableDetail,
    })
  },
  updateTable(tableId: string, patch: { title?: string; status?: DailyTableStatus }) {
    return apiClient.request(`${BASE}/tables/${encodeURIComponent(tableId)}`, {
      method: 'PATCH',
      headers: headers(),
      body: patch,
      decode: decodeTableDetail,
    })
  },
  deleteTable(tableId: string) {
    return apiClient.request(`${BASE}/tables/${encodeURIComponent(tableId)}`, {
      method: 'DELETE',
      headers: headers(),
      decode: (value) => text(record(value).deleted_table_id),
    })
  },
  addTableColumn(tableId: string, input: DailyTableColumnInput) {
    return apiClient.request(`${BASE}/tables/${encodeURIComponent(tableId)}/columns`, {
      method: 'POST',
      headers: headers(),
      body: input,
      decode: decodeTableColumn,
    })
  },
  updateTableColumn(tableId: string, columnId: string, patch: DailyTableColumnPatch) {
    return apiClient.request(
      `${BASE}/tables/${encodeURIComponent(tableId)}/columns/${encodeURIComponent(columnId)}`,
      {
        method: 'PATCH',
        headers: headers(),
        body: patch,
        decode: decodeTableColumn,
      },
    )
  },
  deleteTableColumn(tableId: string, columnId: string) {
    return apiClient.request(
      `${BASE}/tables/${encodeURIComponent(tableId)}/columns/${encodeURIComponent(columnId)}`,
      {
        method: 'DELETE',
        headers: headers(),
        decode: (value) => {
          const item = record(value)
          return {
            deleted_column_id: text(item.deleted_column_id),
            removed_cells: integer(item.removed_cells),
          }
        },
      },
    )
  },
  putTableCells(tableId: string, updates: DailyTableCellUpdate[]) {
    return apiClient.request(`${BASE}/tables/${encodeURIComponent(tableId)}/cells`, {
      method: 'PUT',
      headers: headers(),
      body: { updates },
      decode: (value) => integer(record(value).updated),
    })
  },
  exportTableCsv(tableId: string) {
    return apiClient.download(`${BASE}/tables/${encodeURIComponent(tableId)}/export.csv`)
  },
}
