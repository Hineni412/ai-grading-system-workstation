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
  const records = [
    record('r'.repeat(32), ppt1),
    record('t'.repeat(32), ppt2),
    record('u'.repeat(32), ppt3),
  ]
  const collections = [
    collection('9'.repeat(32), '八上课件', [
      member('9'.repeat(32), records[0]!.id, '第一章 勾股定理/1.1.pptx', 0),
      member('9'.repeat(32), records[1]!.id, '第一章 勾股定理/1.2.pptx', 1),
      member('9'.repeat(32), records[2]!.id, '第二章 实数/2.1.pptx', 2),
    ]),
  ]
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue(collections)
  catalog.materials = [ppt1, ppt2, ppt3]
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
    material_count: 3,
    parsed_material_count: 3,
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

function folderTrigger(host: HTMLElement, label: string): HTMLButtonElement {
  const trigger = [...host.querySelectorAll<HTMLButtonElement>('.file-tree-folder__trigger')]
    .find(item => item.textContent?.includes(label))
  if (!trigger) throw new Error(`folder trigger not found: ${label}`)
  return trigger
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('ShelfRail 可折叠文件树', () => {
  it('renders each group as an expanded folder while keeping rail hooks and count badges', async () => {
    const { app, host } = await mountRail()

    const folders = host.querySelectorAll('.file-tree-folder')
    expect(folders).toHaveLength(2)
    const triggers = [...host.querySelectorAll('.file-tree-folder__trigger')].map(item => item.textContent ?? '')
    expect(triggers.some(text => text.includes('课件 · 3 份'))).toBe(true)
    expect(triggers.some(text => text.includes('课时树'))).toBe(true)
    const chapterItem = [...host.querySelectorAll('.tp-rail__item')]
      .find(item => item.textContent?.includes('第一章 勾股定理'))
    expect(chapterItem?.querySelector('.tp-rail__count')?.textContent?.trim()).toBe('2')
    // 长文本省略号截断后，悬浮 title 仍能看到完整名称
    expect(chapterItem?.querySelector('.tp-rail__item-name')?.getAttribute('title')).toBe('第一章 勾股定理')
    const treeEntry = host.querySelector('[data-testid="shelf-tree-entry"]')
    expect(treeEntry?.classList.contains('is-active')).toBe(true)
    expect(treeEntry?.textContent).toContain('未开始')
    app.unmount()
  })

  it('collapses and re-expands a group from its folder header without losing selection', async () => {
    const { app, host, selected } = await mountRail()

    const chapterItem = () => [...host.querySelectorAll('.tp-rail__item')]
      .find(item => item.textContent?.includes('第二章 实数'))
    expect(chapterItem()).toBeTruthy()

    const trigger = folderTrigger(host, '课件')
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    trigger.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(chapterItem()).toBeUndefined()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(host.querySelector('[data-testid="shelf-tree-entry"]')).not.toBeNull()

    trigger.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(chapterItem()).toBeTruthy()

    chapterItem()?.dispatchEvent(new Event('click'))
    await nextTick()
    expect(selected.value).toEqual({
      kind: 'chapter',
      folderKey: `${'9'.repeat(32)}:第二章 实数`,
    })
    app.unmount()
  })
})
