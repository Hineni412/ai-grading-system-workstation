import { describe, expect, it } from 'vitest'

import type { TimetableCell, TimetableSlot } from '../api/daily'
import {
  buildClassPalette,
  buildDragOps,
  buildGridRows,
  classColorFor,
  distinctClassLabels,
  isCollapsibleRow,
  isoAddDays,
  mondayOf,
  NEUTRAL_CLASS_COLOR,
  pickDefaultCompareClasses,
  slotLabelFor,
  slotLabelMap,
  todayEntries,
  weekTitle,
} from '../views/daily/timetable/timetableModel'
import type { TimetableGridRow } from '../views/daily/timetable/timetableModel'

function lessonSlot(lessonNo: number): TimetableSlot {
  return {
    slot_key: `lesson:${lessonNo}`,
    kind: 'lesson',
    custom_slot_id: null,
    label: `第${lessonNo}节`,
    start_text: null,
    end_text: null,
    position: null,
    lesson_no: lessonNo,
  }
}

function customSlot(id: string, label: string, position: number): TimetableSlot {
  return {
    slot_key: `custom:${id}`,
    kind: 'custom',
    custom_slot_id: id,
    label,
    start_text: '12:30',
    end_text: '13:10',
    position,
    lesson_no: null,
  }
}

function cell(partial: Partial<TimetableCell> & Pick<TimetableCell, 'day_of_week' | 'slot_key'>): TimetableCell {
  return {
    course_text: '',
    class_label: '',
    source: 'empty',
    override_id: null,
    note: null,
    has_note: false,
    has_homework: false,
    ...partial,
  }
}

describe('daily timetable week navigation', () => {
  it('formats the week title with week number and date range', () => {
    expect(weekTitle('2026-08-31', 3)).toBe('第 3 周（8月31日–9月4日）')
  })

  it('marks the title as unset when week number is missing', () => {
    expect(weekTitle('2026-08-31', null)).toBe('周次未设定（8月31日–9月4日）')
  })

  it('computes week range across month boundaries', () => {
    expect(weekTitle('2026-09-28', 7)).toBe('第 7 周（9月28日–10月2日）')
  })

  it('normalizes any day to the monday of its week', () => {
    expect(mondayOf('2026-09-01')).toBe('2026-08-31') // 周二
    expect(mondayOf('2026-09-06')).toBe('2026-08-31') // 周日
    expect(mondayOf('2026-08-31')).toBe('2026-08-31') // 周一
  })

  it('adds days without timezone drift', () => {
    expect(isoAddDays('2026-08-31', 7)).toBe('2026-09-07')
    expect(isoAddDays('2026-08-31', -7)).toBe('2026-08-24')
  })
})

describe('daily timetable grid model', () => {
  const slots = [lessonSlot(1), lessonSlot(2), customSlot('a'.repeat(32), '午练', 4)]

  it('renders merged cells by slot order with empty fallbacks', () => {
    const rows = buildGridRows({
      slots,
      cells: [
        cell({
          day_of_week: 2,
          slot_key: 'lesson:1',
          course_text: '数学',
          class_label: '七（2）班',
          source: 'regular',
        }),
        cell({
          day_of_week: 4,
          slot_key: 'lesson:2',
          course_text: '自习',
          source: 'override',
          override_id: 'ov-1',
        }),
      ],
    })

    expect(rows).toHaveLength(3)
    expect(rows[0]?.slot.slot_key).toBe('lesson:1')
    expect(rows[0]?.cells).toHaveLength(5)
    expect(rows[0]?.cells[1]).toMatchObject({ course_text: '数学', source: 'regular' })
    expect(rows[0]?.cells[0]).toMatchObject({ course_text: '', source: 'empty' })
    expect(rows[1]?.cells[3]).toMatchObject({ course_text: '自习', source: 'override', override_id: 'ov-1' })
    expect(rows[2]?.slot.label).toBe('午练')
    expect(rows[2]?.cells.every((entry) => entry.source === 'empty')).toBe(true)
  })

  it('builds structured today entries with slot time and note flags', () => {
    const entries = todayEntries({
      week_start: '2026-08-31',
      week_no: 1,
      days: [1, 2, 3, 4, 5].map((day) => ({ day_of_week: day, date: `2026-08-3${day}` })),
      slots,
      cells: [
        cell({
          day_of_week: 2,
          slot_key: 'lesson:1',
          course_text: '数学',
          class_label: '七（2）班',
          source: 'regular',
          has_note: true,
          has_homework: true,
        }),
        cell({
          day_of_week: 2,
          slot_key: `custom:${'a'.repeat(32)}`,
          course_text: '午练',
          source: 'regular',
        }),
        cell({ day_of_week: 3, slot_key: 'lesson:1', course_text: '语文', source: 'regular' }),
      ],
      today: { date: '2026-09-01', day_of_week: 2, in_week: true },
    })

    expect(entries).toEqual([
      {
        slot_key: 'lesson:1',
        slot_label: '第1节',
        time: '',
        course: '数学',
        class_label: '七（2）班',
        has_note: true,
        has_homework: true,
      },
      {
        slot_key: `custom:${'a'.repeat(32)}`,
        slot_label: '午练',
        time: '12:30–13:10',
        course: '午练',
        class_label: '',
        has_note: false,
        has_homework: false,
      },
    ])
  })

  it('returns no today entries outside the viewed week', () => {
    const entries = todayEntries({
      week_start: '2026-08-31',
      week_no: null,
      days: [],
      slots,
      cells: [cell({ day_of_week: 2, slot_key: 'lesson:1', course_text: '数学', source: 'regular' })],
      today: { date: '2026-09-08', day_of_week: 2, in_week: false },
    })
    expect(entries).toEqual([])
  })

  it('assigns class palette colors by first-seen order with a neutral fallback', () => {
    const palette = buildClassPalette(['9班', '10班'])
    expect(palette.get('9班')).toEqual({ fg: '#2f7a6b', bg: '#e3f1ef' })
    expect(palette.get('10班')).toEqual({ fg: '#5e628d', bg: '#eceef8' })
    expect(classColorFor(palette, '')).toEqual(NEUTRAL_CLASS_COLOR)
    expect(classColorFor(palette, '未标注班')).toEqual(NEUTRAL_CLASS_COLOR)
  })

  it('marks a row collapsible only when all five cells are plain empty', () => {
    const rowOf = (cells: TimetableCell[]): TimetableGridRow => ({ slot: lessonSlot(3), cells })
    const allEmpty = [1, 2, 3, 4, 5].map((day) => cell({ day_of_week: day, slot_key: 'lesson:3' }))
    expect(isCollapsibleRow(rowOf(allEmpty))).toBe(true)

    const withClearedOverride = allEmpty.map((entry, index) =>
      index === 0
        ? cell({ day_of_week: 1, slot_key: 'lesson:3', source: 'override', override_id: 'ov-1' })
        : entry,
    )
    expect(isCollapsibleRow(rowOf(withClearedOverride))).toBe(false)

    const withCourse = allEmpty.map((entry, index) =>
      index === 4
        ? cell({ day_of_week: 5, slot_key: 'lesson:3', course_text: '数学', source: 'regular' })
        : entry,
    )
    expect(isCollapsibleRow(rowOf(withCourse))).toBe(false)
  })
})

describe('daily timetable drag overrides', () => {
  const cells = [
    cell({
      day_of_week: 2,
      slot_key: 'lesson:3',
      course_text: '数学',
      class_label: '七（1）班',
      source: 'regular',
    }),
    cell({
      day_of_week: 4,
      slot_key: 'lesson:5',
      course_text: '体育',
      class_label: '',
      source: 'regular',
    }),
  ]

  it('moves a course onto an empty cell as set + clear ops', () => {
    const ops = buildDragOps(
      { day_of_week: 2, slot_key: 'lesson:3' },
      { day_of_week: 5, slot_key: 'lesson:1' },
      cells,
    )
    expect(ops).toEqual([
      {
        day_of_week: 5,
        slot_key: 'lesson:1',
        action: 'set',
        course_text: '数学',
        class_label: '七（1）班',
      },
      { day_of_week: 2, slot_key: 'lesson:3', action: 'clear' },
    ])
  })

  it('swaps two courses as two set ops in one atomic batch', () => {
    const ops = buildDragOps(
      { day_of_week: 2, slot_key: 'lesson:3' },
      { day_of_week: 4, slot_key: 'lesson:5' },
      cells,
    )
    expect(ops).toEqual([
      {
        day_of_week: 4,
        slot_key: 'lesson:5',
        action: 'set',
        course_text: '数学',
        class_label: '七（1）班',
      },
      {
        day_of_week: 2,
        slot_key: 'lesson:3',
        action: 'set',
        course_text: '体育',
        class_label: '',
      },
    ])
  })

  it('ignores drops on the same cell or from an empty source', () => {
    expect(
      buildDragOps({ day_of_week: 2, slot_key: 'lesson:3' }, { day_of_week: 2, slot_key: 'lesson:3' }, cells),
    ).toEqual([])
    expect(
      buildDragOps({ day_of_week: 1, slot_key: 'lesson:1' }, { day_of_week: 2, slot_key: 'lesson:3' }, cells),
    ).toEqual([])
  })
})

describe('daily progress compare defaults', () => {
  it('picks the two classes with the most recent notes, stable on ties', () => {
    const groups = [
      { class_label: '七（1）班', notes: [{ id: 'n1' }] },
      { class_label: '七（2）班', notes: [{ id: 'n2' }, { id: 'n3' }, { id: 'n4' }] },
      { class_label: '七（3）班', notes: [{ id: 'n5' }] },
    ]
    expect(pickDefaultCompareClasses(groups as never)).toEqual(['七（2）班', '七（1）班'])
  })

  it('handles classes without notes', () => {
    const groups = [
      { class_label: '七（1）班', notes: [] },
      { class_label: '七（2）班', notes: [] },
    ]
    expect(pickDefaultCompareClasses(groups as never)).toEqual(['七（1）班', '七（2）班'])
  })

  it('collects distinct class labels from cells in first-seen order', () => {
    const labels = distinctClassLabels([
      cell({ day_of_week: 1, slot_key: 'lesson:1', class_label: '七（1）班' }),
      cell({ day_of_week: 2, slot_key: 'lesson:1', class_label: '七（2）班' }),
      cell({ day_of_week: 3, slot_key: 'lesson:1', class_label: '七（1）班' }),
      cell({ day_of_week: 4, slot_key: 'lesson:1', class_label: '  ' }),
    ])
    expect(labels).toEqual(['七（1）班', '七（2）班'])
  })

  it('labels deleted custom slots explicitly', () => {
    const map = slotLabelMap([lessonSlot(1)])
    expect(slotLabelFor(map, 'lesson:1')).toBe('第1节')
    expect(slotLabelFor(map, `custom:${'b'.repeat(32)}`)).toBe('已删除时段')
  })
})
