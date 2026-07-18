import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import type {
  StudentDeletionImpact,
  StudentImportPreview,
  StudentWorkspace,
} from '../api/students'
import { useStudentRosterStore } from '../stores/students'

function makeWorkspace(name = '测试学生', revision = 'a'.repeat(64)): StudentWorkspace {
  return {
    items: [{
      id: 12,
      student_code: 'S012',
      name,
      class_name: '七年级一班',
      created_at: '2026-07-18T08:00:00Z',
    }],
    total: 1,
    page: 1,
    page_size: 50,
    total_pages: 1,
    class_names: ['七年级一班'],
    roster_revision: revision,
  }
}

function makePreview(): StudentImportPreview {
  return {
    filename: 'students.csv',
    columns: ['学号', '姓名', '班级'],
    mapping: { student_code: '学号', name: '姓名', class_name: '班级' },
    counts: { insert: 1, update: 0, unchanged: 0, invalid: 1, duplicate: 0 },
    rows: [
      {
        source_row: 2,
        student_code: 'S013',
        name: '新学生',
        class_name: '七年级一班',
        operation: 'insert',
        selectable: true,
        issues: [],
        existing: null,
      },
      {
        source_row: 3,
        student_code: 'S014',
        name: '',
        class_name: null,
        operation: 'invalid',
        selectable: false,
        issues: ['学号和姓名不能为空'],
        existing: null,
      },
    ],
    roster_revision: 'a'.repeat(64),
    issues: [],
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function makeApi(overrides: Record<string, unknown> = {}) {
  return {
    getWorkspace: vi.fn(async () => makeWorkspace()),
    previewImport: vi.fn(async () => makePreview()),
    commitImport: vi.fn(async () => ({
      inserted: 1,
      updated: 0,
      unchanged: 0,
      total: 1,
      roster_revision: 'b'.repeat(64),
    })),
    updateStudent: vi.fn(),
    getDeletionImpact: vi.fn(),
    deleteStudent: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('student roster store', () => {
  it('ignores a late workspace response after filters change', async () => {
    const oldRequest = deferred<StudentWorkspace>()
    const api = makeApi({
      getWorkspace: vi.fn()
        .mockReturnValueOnce(oldRequest.promise)
        .mockResolvedValueOnce(makeWorkspace('新筛选结果', 'b'.repeat(64))),
    })
    const store = useStudentRosterStore()

    const oldLoad = store.load({ search: '旧条件' }, api)
    await Promise.resolve()
    await store.load({ search: '新条件' }, api)
    oldRequest.resolve(makeWorkspace('迟到结果'))
    await oldLoad

    expect(store.workspace?.items[0]?.name).toBe('新筛选结果')
    expect(store.search).toBe('新条件')
  })

  it('commits only selectable preview rows with the preview revision', async () => {
    const api = makeApi()
    const store = useStudentRosterStore()
    const file = new File(['学号,姓名\nS013,新学生'], 'students.csv')

    await store.previewFile(file, undefined, api)
    await store.commitPreview(api)

    expect(api.commitImport).toHaveBeenCalledWith(
      'a'.repeat(64),
      [{
        student_code: 'S013',
        name: '新学生',
        class_name: '七年级一班',
      }],
    )
    expect(store.importState).toBe('committed')
    expect(store.noticeMessage).toContain('已导入')
  })

  it('preserves the preview when the roster changed before commit', async () => {
    const api = makeApi({
      commitImport: vi.fn(async () => {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'student_roster_conflict',
          message: 'conflict',
          details: {},
          requestId: 'rid',
          retryable: false,
        })
      }),
    })
    const store = useStudentRosterStore()
    await store.previewFile(new File(['x'], 'students.csv'), undefined, api)

    await store.commitPreview(api)

    expect(store.importState).toBe('conflict')
    expect(store.preview?.filename).toBe('students.csv')
    expect(store.errorMessage).toContain('名单已经变化')
  })

  it('ignores a late deletion impact after the selected student changes', async () => {
    const oldRequest = deferred<StudentDeletionImpact>()
    const customWorkspace = makeWorkspace()
    customWorkspace.items.push({
      id: 13,
      student_code: 'S013',
      name: '另一名学生',
      class_name: '七年级一班',
      created_at: '2026-07-18T08:01:00Z',
    })
    customWorkspace.total = 2
    const api = makeApi({
      getWorkspace: vi.fn(async () => customWorkspace),
      getDeletionImpact: vi.fn(() => oldRequest.promise),
    })
    const store = useStudentRosterStore()
    await store.load({}, api)
    store.selectStudent(12)

    const oldLoad = store.loadDeletionImpact(api)
    store.selectStudent(13)
    oldRequest.resolve({
      student: customWorkspace.items[0]!,
      counts: {
        deleted_students: 1,
        deleted_results: 2,
        deleted_details: 10,
        deleted_annotations: 2,
        deleted_attendance: 1,
        unlinked_papers: 2,
      },
      roster_revision: 'a'.repeat(64),
    })
    await oldLoad

    expect(store.selectedStudent?.id).toBe(13)
    expect(store.deletionImpact).toBeNull()
    expect(store.deletionState).toBe('idle')
  })

  it('reports every cleanup count after a backed-up deletion', async () => {
    const api = makeApi({
      getDeletionImpact: vi.fn(async () => ({
        student: makeWorkspace().items[0]!,
        counts: {
          deleted_students: 1,
          deleted_results: 2,
          deleted_details: 10,
          deleted_annotations: 2,
          deleted_attendance: 1,
          unlinked_papers: 2,
        },
        roster_revision: 'a'.repeat(64),
      })),
      deleteStudent: vi.fn(async () => ({
        deleted_students: 1,
        deleted_results: 2,
        deleted_details: 10,
        deleted_annotations: 2,
        deleted_attendance: 1,
        unlinked_papers: 2,
        backup_created: true,
        roster_revision: 'b'.repeat(64),
      })),
    })
    const store = useStudentRosterStore()
    await store.load({}, api)
    store.selectStudent(12)
    await store.loadDeletionImpact(api)

    await store.deleteSelected(api)

    expect(store.noticeMessage).toContain('备份已完成')
    expect(store.noticeMessage).toContain('1 名学生')
    expect(store.noticeMessage).toContain('2 份成绩')
    expect(store.noticeMessage).toContain('10 条评分明细')
    expect(store.noticeMessage).toContain('2 条批注')
    expect(store.noticeMessage).toContain('1 条考勤')
    expect(store.noticeMessage).toContain('2 份答卷')
  })

  it('tells the teacher to wait when grading blocks deletion', async () => {
    const api = makeApi({
      getDeletionImpact: vi.fn(async () => ({
        student: makeWorkspace().items[0]!,
        counts: {
          deleted_students: 1,
          deleted_results: 0,
          deleted_details: 0,
          deleted_annotations: 0,
          deleted_attendance: 0,
          unlinked_papers: 0,
        },
        roster_revision: 'a'.repeat(64),
      })),
      deleteStudent: vi.fn(async () => {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'student_grading_active',
          message: 'Wait for grading to finish before deleting this student',
          details: {},
          requestId: 'rid-delete-active',
          retryable: false,
        })
      }),
    })
    const store = useStudentRosterStore()
    await store.load({}, api)
    store.selectStudent(12)
    await store.loadDeletionImpact(api)

    const deleted = await store.deleteSelected(api)

    expect(deleted).toBe(false)
    expect(store.errorMessage).toBe('批改仍在进行，请等待批改结束后再删除学生。')
    expect(store.selectedStudent?.id).toBe(12)
  })
})
