import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeStudentImportPreview,
  decodeStudentWorkspace,
  studentRosterApi,
} from '../students'

const student = {
  id: 12,
  student_code: 'S012',
  name: '测试学生',
  class_name: '七年级一班',
  created_at: '2026-07-18T08:00:00Z',
}

const workspace = {
  items: [student],
  total: 1,
  page: 1,
  page_size: 50,
  total_pages: 1,
  class_names: ['七年级一班'],
  roster_revision: 'a'.repeat(64),
}

const preview = {
  filename: 'students.csv',
  columns: ['学号', '姓名', '班级'],
  mapping: { student_code: '学号', name: '姓名', class_name: '班级' },
  counts: { insert: 1, update: 0, unchanged: 0, invalid: 0, duplicate: 0 },
  rows: [{
    source_row: 2,
    student_code: 'S013',
    name: '新学生',
    class_name: '七年级一班',
    operation: 'insert',
    selectable: true,
    issues: [],
    existing: null,
  }],
  roster_revision: 'a'.repeat(64),
  issues: [],
}

afterEach(() => vi.restoreAllMocks())

describe('student roster API', () => {
  it('accepts the exact paged workspace contract and rejects private fields', () => {
    expect(decodeStudentWorkspace(workspace)).toEqual(workspace)
    expect(() => decodeStudentWorkspace({
      ...workspace,
      items: [{ ...student, private_path: 'internal' }],
    })).toThrow('Invalid student workspace')
  })

  it('accepts only classified import rows from the preview contract', () => {
    expect(decodeStudentImportPreview(preview)).toEqual(preview)
    expect(() => decodeStudentImportPreview({
      ...preview,
      rows: [{ ...preview.rows[0], operation: 'overwrite' }],
    })).toThrow('Invalid student import preview')
  })

  it('uploads file bytes with encoded mapping choices and no client path', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(preview),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))
    const file = new File(['学号,姓名\nS013,新学生\n'], 'students.csv', {
      type: 'text/csv',
    })

    await studentRosterApi.previewImport(file, {
      student_code: '学号',
      name: '姓名',
      class_name: null,
    })

    const [path, init] = fetchMock.mock.calls[0]!
    expect(String(path)).toContain('/api/students/import/preview?')
    expect(String(path)).toContain('filename=students.csv')
    expect(String(path)).toContain('student_code_column=%E5%AD%A6%E5%8F%B7')
    expect(String(path)).not.toContain('fakepath')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(file)
  })

  it('commits and deletes only with the preview revision', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        inserted: 1,
        updated: 0,
        unchanged: 0,
        total: 1,
        roster_revision: 'b'.repeat(64),
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        deleted_students: 1,
        deleted_results: 0,
        deleted_details: 0,
        deleted_annotations: 0,
        deleted_attendance: 0,
        unlinked_papers: 0,
        backup_created: true,
        roster_revision: 'c'.repeat(64),
      }), { status: 200, headers: { 'content-type': 'application/json' } }))

    await studentRosterApi.commitImport('a'.repeat(64), [{
      student_code: 'S013',
      name: '新学生',
      class_name: '七年级一班',
    }])
    await studentRosterApi.deleteStudent(12, 'b'.repeat(64))

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      expected_revision: 'a'.repeat(64),
      items: [{
        student_code: 'S013',
        name: '新学生',
        class_name: '七年级一班',
      }],
    })
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      `/api/students/12?expected_revision=${'b'.repeat(64)}&confirmed=true`,
    )
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe('DELETE')
  })
})
