import { describe, expect, it } from 'vitest'

import type { DailyTableSummary } from '../api/daily'
import {
  buildCellUpdate,
  cellInputError,
  csvFilenameFromDisposition,
  formatTableTime,
  parseOptionsInput,
  partitionTables,
  savedCellValue,
} from '../views/daily/tables/tableModel'

function summary(partial: Partial<DailyTableSummary> & Pick<DailyTableSummary, 'id' | 'status'>): DailyTableSummary {
  return {
    title: `表格${partial.id}`,
    column_count: 2,
    row_count: 30,
    created_at: '2026-09-01T08:00:00+00:00',
    updated_at: '2026-09-01T09:00:00+00:00',
    ...partial,
  }
}

describe('daily tables list partition', () => {
  it('splits tables into active and archived groups preserving order', () => {
    const groups = partitionTables([
      summary({ id: 'a', status: 'active' }),
      summary({ id: 'b', status: 'archived' }),
      summary({ id: 'c', status: 'active' }),
    ])
    expect(groups.active.map((table) => table.id)).toEqual(['a', 'c'])
    expect(groups.archived.map((table) => table.id)).toEqual(['b'])
  })
})

describe('daily table cell updates', () => {
  it('trims text input and treats empty text as clearing the cell', () => {
    expect(buildCellUpdate({ id: 'c1', col_type: 'text' }, 'r1', '  已通知家长  ')).toEqual({
      row_id: 'r1',
      column_id: 'c1',
      value_text: '已通知家长',
    })
    expect(buildCellUpdate({ id: 'c1', col_type: 'text' }, 'r1', '   ')).toEqual({
      row_id: 'r1',
      column_id: 'c1',
      value_text: '',
    })
  })

  it('passes number input through as trimmed text', () => {
    expect(buildCellUpdate({ id: 'c2', col_type: 'number' }, 'r1', ' 12.5 ')).toEqual({
      row_id: 'r1',
      column_id: 'c2',
      value_text: '12.5',
    })
  })

  it('normalizes check input to 1 or 0', () => {
    expect(buildCellUpdate({ id: 'c3', col_type: 'check' }, 'r1', true).value_text).toBe('1')
    expect(buildCellUpdate({ id: 'c3', col_type: 'check' }, 'r1', false).value_text).toBe('0')
  })

  it('keeps select and date values as-is (empty string clears)', () => {
    expect(buildCellUpdate({ id: 'c4', col_type: 'select' }, 'r1', '已交').value_text).toBe('已交')
    expect(buildCellUpdate({ id: 'c4', col_type: 'select' }, 'r1', '').value_text).toBe('')
    expect(buildCellUpdate({ id: 'c5', col_type: 'date' }, 'r1', '2026-09-05').value_text).toBe(
      '2026-09-05',
    )
  })

  it('flags non-numeric input for number columns before saving', () => {
    expect(cellInputError({ col_type: 'number' }, 'abc')).toBe('数字列只能填写数字。')
    expect(cellInputError({ col_type: 'number' }, '12')).toBeNull()
    expect(cellInputError({ col_type: 'text' }, '任意内容')).toBeNull()
    expect(cellInputError({ col_type: 'text' }, '')).toBeNull()
  })

  it('reads saved values from the sparse cell map', () => {
    const cells = { r1: { c1: '1' } }
    expect(savedCellValue(cells, 'r1', 'c1')).toBe('1')
    expect(savedCellValue(cells, 'r1', 'c2')).toBe('')
    expect(savedCellValue(cells, 'r2', 'c1')).toBe('')
  })
})

describe('daily table select options parsing', () => {
  it('splits on commas, Chinese commas, dunhao and newlines, dropping blanks and duplicates', () => {
    expect(parseOptionsInput('已交, 未交，补交、已交\n免试')).toEqual(['已交', '未交', '补交', '免试'])
    expect(parseOptionsInput('  ')).toEqual([])
  })
})

describe('daily table display helpers', () => {
  it('formats the updated time as month-day and local clock time', () => {
    expect(formatTableTime('2026-09-01T08:30:00')).toBe('9月1日 08:30')
    expect(formatTableTime('不是日期')).toBe('不是日期')
  })

  it('prefers the RFC 5987 filename from Content-Disposition', () => {
    const disposition =
      "attachment; filename=\"daily-table.csv\"; filename*=UTF-8''%E7%A0%94%E5%AD%A6%E5%9B%9E%E6%89%A7.csv"
    expect(csvFilenameFromDisposition(disposition, '兜底')).toBe('研学回执.csv')
  })

  it('falls back to the plain filename and then to the table title', () => {
    expect(csvFilenameFromDisposition('attachment; filename="table.csv"', '兜底')).toBe('table.csv')
    expect(csvFilenameFromDisposition(null, '研学回执统计')).toBe('研学回执统计.csv')
    expect(csvFilenameFromDisposition(null, '  ')).toBe('表格.csv')
  })
})
