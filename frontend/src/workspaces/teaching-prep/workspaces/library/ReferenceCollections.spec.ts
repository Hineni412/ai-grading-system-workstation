import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  teachingPrepCatalogApi,
  type ReferencePptCollection,
  type TeachingSemester,
} from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import type { MaterialImportQueue } from './importQueue'
import ReferenceCollections from './ReferenceCollections.vue'

const semester: TeachingSemester = {
  id: 's'.repeat(32),
  curriculum_id: 'c'.repeat(32),
  curriculum_title: '八年级上册',
  school_year: '2026-2027',
  term: 'first',
  planned_new_lesson_count: 48,
  status: 'active',
  active_lesson_count: 1,
  not_started_lesson_count: 1,
  preparing_lesson_count: 0,
  ready_lesson_count: 0,
  taught_lesson_count: 0,
  skipped_lesson_count: 0,
  material_count: 0,
  parsed_material_count: 0,
  mapped_material_count: 0,
  revision: 1,
  created_at: '2026-08-03T00:00:00Z',
  updated_at: '2026-08-03T00:00:00Z',
}

function collection(id: string, name: string, isActive: boolean): ReferencePptCollection {
  return {
    id,
    semester_id: semester.id,
    display_name: name,
    mapping_proposal_id: 'p'.repeat(32),
    ignored_file_count: 0,
    is_active: isActive,
    revision: 1,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    members: [{
      id: `${id}-m1`,
      collection_id: id,
      material_record_id: 'r'.repeat(32),
      relative_path: '第一章/一次函数.pptx',
      kind: 'lesson',
      confidence: 'high',
      chapter_number: 1,
      section_number: null,
      subsection_number: null,
      lesson_number: 1,
      normalized_title: '一次函数',
      evidence: [],
      issues: [],
      created_at: '2026-08-03T00:00:00Z',
    }],
  }
}

const active = collection('a'.repeat(32), '可用课件合集', true)
const inactive = collection('b'.repeat(32), '旧课件合集', false)

function fakeQueue(): MaterialImportQueue {
  return {
    pendingPptFolders: ref([]),
    retryPptCollection: vi.fn(async () => {}),
  } as unknown as MaterialImportQueue
}

async function mountPanel(collections: ReferencePptCollection[]) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.semesters = [semester]
  catalog.selectedCurriculumId = semester.curriculum_id
  catalog.selectedSemesterId = semester.id
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections')
    .mockResolvedValue(collections)

  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(ReferenceCollections, { queue: fakeQueue() })
  app.mount(host)
  await vi.waitFor(() => {
    expect(teachingPrepCatalogApi.listReferencePptCollections)
      .toHaveBeenCalledWith(semester.id, { includeInactive: true })
  })
  await nextTick()
  return { app, host, catalog }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('ReferenceCollections', () => {
  it('shows only active collections by default', async () => {
    const { app, host } = await mountPanel([active, inactive])

    expect(host.textContent).toContain('可用课件合集')
    expect(host.textContent).not.toContain('旧课件合集')
    expect(host.textContent).toContain('查看已停用合集（1）')
    app.unmount()
  })

  it('deactivates a collection after confirmation', async () => {
    const { app, host } = await mountPanel([active])
    const update = vi.spyOn(teachingPrepCatalogApi, 'updateReferencePptCollection')
      .mockResolvedValue({ ...active, is_active: false })
    vi.stubGlobal('confirm', vi.fn(() => true))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '停用')?.click()
    await vi.waitFor(() => expect(update).toHaveBeenCalled())
    expect(update).toHaveBeenCalledWith(active.id, { is_active: false })
    app.unmount()
  })

  it('does not deactivate when the teacher cancels the confirmation', async () => {
    const { app, host } = await mountPanel([active])
    const update = vi.spyOn(teachingPrepCatalogApi, 'updateReferencePptCollection')
    vi.stubGlobal('confirm', vi.fn(() => false))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '停用')?.click()
    await nextTick()
    expect(update).not.toHaveBeenCalled()
    app.unmount()
  })

  it('reveals and restores inactive collections without confirmation', async () => {
    const { app, host } = await mountPanel([active, inactive])
    const update = vi.spyOn(teachingPrepCatalogApi, 'updateReferencePptCollection')
      .mockResolvedValue({ ...inactive, is_active: true })
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim().startsWith('查看已停用合集'))?.click()
    await nextTick()
    expect(host.textContent).toContain('旧课件合集')

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '恢复')?.click()
    await vi.waitFor(() => expect(update).toHaveBeenCalledWith(inactive.id, { is_active: true }))
    expect(confirmSpy).not.toHaveBeenCalled()
    app.unmount()
  })

  it('shows the empty hint when there are no collections', async () => {
    const { app, host } = await mountPanel([])

    expect(host.textContent).toContain('还没有参考课件合集')
    app.unmount()
  })
})
