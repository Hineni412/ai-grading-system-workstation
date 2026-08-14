import { createPinia, setActivePinia } from 'pinia'
import { createApp, defineComponent, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ReferencePptCollection,
  SemesterMaterialRecord,
  MaterialVersion,
} from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import type { LibrarySelection } from './libraryShared'
import ShelfRail from './ShelfRail.vue'

const semesterId = 's'.repeat(32)

function material(id: string, name: string, unitCount: number): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pptx',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pptx`, size_bytes: 20,
    modified_ns: null, unit_count: unitCount, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function record(id: string, item: MaterialVersion): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: item.source_id,
    display_name: item.display_name, material_role: 'reference_ppt', parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: item.id,
    safe_filename: item.safe_filename, current_inspection_status: 'ready',
    current_unit_count: item.unit_count, last_parsed_version_id: item.id,
    has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function member(collectionId: string, recordId: string, relativePath: string, index: number) {
  return {
    id: `${collectionId}-m${index}`, collection_id: collectionId,
    material_record_id: recordId, relative_path: relativePath,
    kind: 'lesson' as const, confidence: 'high' as const,
    chapter_number: null, section_number: null, subsection_number: null,
    lesson_number: null, normalized_title: `课件${index}`,
    evidence: [], issues: [], created_at: '2026-08-03T00:00:00Z',
  }
}

function collection(
  id: string,
  name: string,
  members: ReturnType<typeof member>[],
  isActive = true,
) {
  const result: ReferencePptCollection = {
    id, semester_id: semesterId, display_name: name,
    mapping_proposal_id: 'p'.repeat(32), ignored_file_count: 0, is_active: isActive,
    revision: 1, created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
    members,
  }
  return result
}

async function mountRail() {
  const catalog = useTeachingPrepCatalogStore()
  const ppt1 = material('a'.repeat(32), '1.1 认识勾股定理.pptx', 26)
  const ppt2 = material('b'.repeat(32), '1.2 直角三角形.pptx', 27)
  const ppt3 = material('c'.repeat(32), '2.1 认识无理数.pptx', 24)
  const ppt4 = material('d'.repeat(32), '3.1 旧版实数.pptx', 20)
  const records = [
    record('r'.repeat(32), ppt1),
    record('t'.repeat(32), ppt2),
    record('u'.repeat(32), ppt3),
    record('v'.repeat(32), ppt4),
  ]
  const collections = [
    collection('9'.repeat(32), '八上课件', [
      member('9'.repeat(32), records[0]!.id, '第一章 勾股定理/1.1.pptx', 0),
      member('9'.repeat(32), records[1]!.id, '第一章 勾股定理/1.2.pptx', 1),
      member('9'.repeat(32), records[2]!.id, '第二章 实数/2.1.pptx', 2),
    ]),
    collection('8'.repeat(32), '旧版课件', [
      member('8'.repeat(32), records[3]!.id, '第三章 旧版/3.1.pptx', 0),
    ], false),
  ]
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue(collections)
  catalog.materials = [ppt1, ppt2, ppt3, ppt4]
  catalog.semesterMaterials = records
  catalog.selectedCurriculumId = 'c'.repeat(32)
  catalog.selectedSemesterId = semesterId
  catalog.semesters = [{
    id: semesterId,
    curriculum_id: 'c'.repeat(32),
    curriculum_title: '八年级上册',
    school_year: '2026-2027',
    term: 'first',
    planned_new_lesson_count: 60,
    status: 'active',
    active_lesson_count: 0,
    not_started_lesson_count: 0,
    preparing_lesson_count: 0,
    ready_lesson_count: 0,
    taught_lesson_count: 0,
    skipped_lesson_count: 0,
    material_count: 4,
    parsed_material_count: 4,
    mapped_material_count: 0,
    revision: 1,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
  }]
  catalog.referencePptCollections = collections

  const selected = ref<LibrarySelection>({ kind: 'tree' })
  const Harness = defineComponent({
    components: { ShelfRail },
    setup: () => ({ selected }),
    template: '<ShelfRail :selection="selected" @select="selected = $event" />',
  })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(Harness)
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, selected }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('ShelfRail', () => {
  it('groups collection members into chapter folders with file-count badges', async () => {
    const { app, host } = await mountRail()

    expect(host.querySelector('[data-testid="shelf-import-entry"]')?.textContent).toContain('导入资料')
    expect(host.textContent).toContain('课件 · 3 份')
    expect(host.textContent).toContain('第一章 勾股定理')
    expect(host.textContent).toContain('第二章 实数')
    const items = [...host.querySelectorAll('.tp-rail__item')]
    const firstChapter = items.find(item => item.textContent?.includes('第一章 勾股定理'))
    expect(firstChapter?.querySelector('.tp-rail__count')?.textContent?.trim()).toBe('2')
    const secondChapter = items.find(item => item.textContent?.includes('第二章 实数'))
    expect(secondChapter?.querySelector('.tp-rail__count')?.textContent?.trim()).toBe('1')
    app.unmount()
  })

  it('已停用合集的章文件夹不出现在资料柜', async () => {
    const { app, host, catalog } = await mountRail()

    expect(host.textContent).toContain('课件 · 3 份')
    expect(host.textContent).not.toContain('第三章 旧版')
    expect(host.textContent).not.toContain('已停用')
    // store 全量列表仍包含已停用合集，供 ChapterGrid 停用/恢复管理使用
    expect(catalog.libraryChapterFolders.some(folder => !folder.collectionActive)).toBe(true)
    app.unmount()
  })

  it('shows the lesson-tree entry state and selects entries on click', async () => {
    const { app, host, selected } = await mountRail()

    expect(host.querySelector('[data-testid="shelf-tree-entry"]')?.textContent).toContain('未开始')
    const chapterButton = [...host.querySelectorAll('.tp-rail__item')]
      .find(item => item.textContent?.includes('第二章 实数'))
    chapterButton?.dispatchEvent(new Event('click'))
    await nextTick()
    expect(selected.value).toEqual({
      kind: 'chapter',
      folderKey: `${'9'.repeat(32)}:第二章 实数`,
    })
    app.unmount()
  })

  it('keeps other-semester books out of the current 书 list', async () => {
    const { app, host, catalog } = await mountRail()
    const otherBook = material('e'.repeat(32), '验收学期教材.pdf', 100)
    catalog.materials = [...catalog.materials, otherBook]
    catalog.allSemesterMaterialRecords = [
      ...catalog.semesterMaterials,
      {
        id: 'w'.repeat(32),
        semester_id: 'o'.repeat(32),
        material_source_id: otherBook.source_id,
        display_name: otherBook.display_name,
        material_role: 'textbook',
        parse_status: 'parsed',
        mapping_status: 'unmapped',
        current_material_version_id: otherBook.id,
        safe_filename: otherBook.safe_filename,
        current_inspection_status: 'ready',
        current_unit_count: otherBook.unit_count,
        last_parsed_version_id: otherBook.id,
        has_unparsed_update: false,
        parsed_at: '2026-08-03T00:00:00Z',
        is_active: true,
        revision: 1,
        created_at: '2026-08-03T00:00:00Z',
        updated_at: '2026-08-03T00:00:00Z',
      },
    ]
    await nextTick()

    expect(host.textContent).not.toContain('教材 · 验收学期教材.pdf')
    const otherEntry = [...host.querySelectorAll('.tp-rail__item')]
      .find(item => item.textContent?.includes('验收学期教材.pdf'))
    expect(otherEntry).toBeTruthy()
    expect(otherEntry?.textContent).toContain('未加入本学期')
    app.unmount()
  })
})
