import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory } from 'vue-router'

import { createAppRouter } from '../router'
import SettingsStudentsPanel from '../components/settings/SettingsStudentsPanel.vue'
import { useStudentRosterStore } from '../stores/students'

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

const apiMock = vi.hoisted(() => ({
  getWorkspace: vi.fn(),
  previewImport: vi.fn(),
  commitImport: vi.fn(),
  updateStudent: vi.fn(),
  getDeletionImpact: vi.fn(),
  deleteStudent: vi.fn(),
}))

const homeroomMock = vi.hoisted(() => ({
  homeroom: vi.fn(),
  setHomeroom: vi.fn(),
}))

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  studentRosterApi: apiMock,
}))


const mounted: App[] = []

async function settle() {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/students')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  expect(router.currentRoute.value.fullPath).toBe('/settings?section=students')
  const app = createApp(SettingsStudentsPanel)
  app.use(createPinia())
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return document.body
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  homeroomMock.homeroom.mockResolvedValue({
    homeroom_class: null,
    revision: 0,
    classes: ['七年级一班'],
    source_revision: 'a'.repeat(64),
  })
  apiMock.getWorkspace.mockImplementation(async () => structuredClone(workspace))
  apiMock.previewImport.mockImplementation(async () => structuredClone(preview))
  apiMock.commitImport.mockResolvedValue({
    inserted: 1,
    updated: 0,
    unchanged: 0,
    total: 1,
    roster_revision: 'b'.repeat(64),
  })
  apiMock.updateStudent.mockResolvedValue({
    student: { ...student, name: '修改后学生' },
    roster_revision: 'b'.repeat(64),
  })
  apiMock.getDeletionImpact.mockResolvedValue({
    student,
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
  apiMock.deleteStudent.mockResolvedValue({
    deleted_students: 1,
    deleted_results: 2,
    deleted_details: 10,
    deleted_annotations: 2,
    deleted_attendance: 1,
    unlinked_papers: 2,
    backup_created: true,
    roster_revision: 'c'.repeat(64),
  })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
})

describe('SettingsStudentsPanel', () => {

  it('classifies file rows before enabling the confirmation write', async () => {
    const host = await mountView()
    const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
    const file = new File(['学号,姓名\nS013,新学生'], 'students.csv', {
      type: 'text/csv',
    })
    Object.defineProperty(input, 'files', { configurable: true, value: [file] })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()

    expect(apiMock.previewImport).toHaveBeenCalled()
    expect(host.textContent).toContain('新增 1')
    expect(host.textContent).toContain('有问题 1')
    expect(host.textContent).toContain('只看有变化的行')
    expect(host.textContent).toContain('写入名单（1 人）')
    const checkboxes = host.querySelectorAll<HTMLInputElement>(
      '[data-testid="import-preview-row"] input[type="checkbox"]',
    )
    expect(checkboxes).toHaveLength(2)
    expect(checkboxes[0]?.checked).toBe(true)
    expect(checkboxes[1]?.disabled).toBe(true)

    host.querySelector<HTMLButtonElement>('[data-action="commit-import"]')!.click()
    await settle()
    expect(apiMock.commitImport).toHaveBeenCalledWith(
      'a'.repeat(64),
      [expect.objectContaining({ student_code: 'S013' })],
    )
  })

  it('edits one student and requires impact review plus typed confirmation for deletion', async () => {
    const host = await mountView()
    host.querySelector<HTMLButtonElement>('[data-student-id="12"]')!.click()
    await nextTick()

    const name = host.querySelector<HTMLInputElement>('input[name="student-name"]')!
    name.value = '修改后学生'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    host.querySelector<HTMLButtonElement>('[data-action="save-student"]')!.click()
    await settle()

    expect(apiMock.updateStudent).toHaveBeenCalledWith(
      12,
      'a'.repeat(64),
      {
        student_code: 'S012',
        name: '修改后学生',
        class_name: '七年级一班',
      },
    )

    host.querySelector<HTMLButtonElement>('[data-student-id="12"]')!.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[data-action="review-deletion"]')!.click()
    await settle()
    expect(host.textContent).toContain('将删除 2 份成绩')
    const deleteButton = host.querySelector<HTMLButtonElement>(
      '[data-action="delete-student"]',
    )!
    expect(deleteButton.disabled).toBe(true)

    const confirmation = host.querySelector<HTMLInputElement>(
      'input[name="delete-confirmation"]',
    )!
    confirmation.value = 'S012'
    confirmation.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(deleteButton.disabled).toBe(false)
    deleteButton.click()
    await settle()
    expect(apiMock.deleteStudent).toHaveBeenCalled()
  })

  it('searches after typing and immediately filters class', async () => {
    const host = await mountView()
    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!
    search.value = 'S012'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await vi.waitFor(() => expect(apiMock.getWorkspace).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'S012', page: 1 }), expect.any(AbortSignal)))
  })

  it('keeps the edited name when a pending roster refresh finishes', async () => {
    const host = await mountView()
    host.querySelector<HTMLButtonElement>('[data-student-id="12"]')!.click()
    await nextTick()
    const name = host.querySelector<HTMLInputElement>('[name="student-name"]')!
    name.value = '正在编辑的名字'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    await useStudentRosterStore().load()
    await settle()
    expect(name.value).toBe('正在编辑的名字')
    host.querySelector<HTMLButtonElement>('[data-action="save-student"]')!.click()
    await settle()
    expect(apiMock.updateStudent).toHaveBeenCalledWith(12, 'a'.repeat(64), expect.objectContaining({ name: '正在编辑的名字' }))
  })
})
