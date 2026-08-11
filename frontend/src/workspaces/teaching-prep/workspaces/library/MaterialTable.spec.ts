import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  MaterialDeletionPreview,
  MaterialVersion,
  SemesterMaterialRecord,
} from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import MaterialTable from './MaterialTable.vue'

const semesterId = 's'.repeat(32)

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pdf',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pdf`, size_bytes: 20,
    modified_ns: null, unit_count: 1, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function record(id: string, item: MaterialVersion, isActive = true): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: item.source_id,
    display_name: item.display_name, material_role: 'textbook', parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: item.id,
    safe_filename: item.safe_filename, current_inspection_status: 'ready',
    current_unit_count: 1, last_parsed_version_id: item.id, has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: isActive, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function deletionPreview(item: MaterialVersion): MaterialDeletionPreview {
  return {
    source_id: item.source_id,
    display_name: item.display_name,
    source_revision: 1,
    impact_counts: {
      material_sources: 1, material_versions: 1, material_units: item.unit_count ?? 0,
      lesson_material_links: 0, semester_material_records: 1,
      semester_mapping_proposals: 0, reference_ppt_collections: 0,
      exercise_regions: 0, exercise_candidates: 0,
    },
    affected_semesters: [{
      semester_id: semesterId, title: '八年级上册', school_year: '2026-2027', term: 'first',
    }],
    generation_history_count: 0,
    preserved_snapshot_count: 0,
    blocking_generation_count: 0,
    can_delete: true,
    blocker_code: null,
    preserved_history_note: '没有正式生成历史。',
    confirmation_phrase: '确认彻底删除资料',
    preview_version: `preview-${item.id[0]}`,
    owned_file_count: 2,
  }
}

const textbook = material('a'.repeat(32), '八年级数学教材.pdf')

async function mountTable(options: { recordActive?: boolean; withRecord?: boolean } = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.materials = [textbook]
  catalog.semesterMaterials = options.withRecord === false
    ? []
    : [record('r'.repeat(32), textbook, options.recordActive ?? true)]
  catalog.materialParseJobs = {}
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(MaterialTable)
  app.mount(host)
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

describe('MaterialTable', () => {
  it('renders the current-term material row with its role', async () => {
    const { app, host } = await mountTable()

    expect(host.textContent).toContain('八年级数学教材.pdf')
    const roleSelect = host.querySelector<HTMLSelectElement>('select[aria-label="资料角色"]')
    expect(roleSelect?.value).toBe('textbook')
    expect(host.textContent).toContain('删除本学期全部资料（1）')
    app.unmount()
  })

  it('moves a material out of the semester after confirmation', async () => {
    const { app, host, catalog } = await mountTable()
    const update = vi.spyOn(catalog, 'updateSemesterMaterial').mockResolvedValue(undefined)
    vi.stubGlobal('confirm', vi.fn(() => true))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '移出学期')?.click()
    await vi.waitFor(() => expect(update).toHaveBeenCalled())
    expect(update.mock.calls[0]?.[1]).toEqual({ isActive: false })
    app.unmount()
  })

  it('restores an inactive semester record without confirmation', async () => {
    const { app, host, catalog } = await mountTable({ recordActive: false })
    const update = vi.spyOn(catalog, 'updateSemesterMaterial').mockResolvedValue(undefined)
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '恢复')?.click()
    await vi.waitFor(() => expect(update).toHaveBeenCalled())
    expect(update.mock.calls[0]?.[1]).toEqual({ isActive: true })
    expect(confirmSpy).not.toHaveBeenCalled()
    app.unmount()
  })

  it('renames a material through the store', async () => {
    const { app, host, catalog } = await mountTable()
    const rename = vi.spyOn(catalog, 'updateMaterialSource').mockResolvedValue(undefined)
    vi.stubGlobal('prompt', vi.fn(() => '新教材名称'))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '重命名')?.click()
    await vi.waitFor(() => expect(rename).toHaveBeenCalled())
    expect(rename.mock.calls[0]?.[1]).toEqual({ displayName: '新教材名称' })
    app.unmount()
  })

  it('previews deletion impact before the final confirmation', async () => {
    const { app, host, catalog } = await mountTable()
    const getPreview = vi.spyOn(catalog, 'getMaterialDeletionPreview')
      .mockResolvedValue(deletionPreview(textbook))
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource').mockImplementation(
      async (_item, input) => ({
        operation_id: input.operation_id,
        status: 'succeeded' as const,
        preview_version: input.preview_version,
        deleted_source_id: textbook.source_id,
        deleted_file_count: 2,
        counts: deletionPreview(textbook).impact_counts,
        error_code: null,
      }),
    )
    vi.stubGlobal('confirm', vi.fn(() => true))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '删除')?.click()
    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="confirm-material-deletion"]')).toBeTruthy()
    })
    expect(getPreview).toHaveBeenCalledTimes(1)
    expect(deleteSource).not.toHaveBeenCalled()
    expect(host.textContent).toContain('彻底删除：先核对这一份资料')

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')?.click()
    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledTimes(1))
    expect(deleteSource.mock.calls[0]?.[1]).toMatchObject({
      preview_version: `preview-${textbook.id[0]}`,
      confirmation_phrase: '确认彻底删除资料',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('已确认成功'))
    app.unmount()
  })

  it('loads a batch deletion preview for every current-term material', async () => {
    const { app, host, catalog } = await mountTable()
    const getPreview = vi.spyOn(catalog, 'getMaterialDeletionPreview')
      .mockResolvedValue(deletionPreview(textbook))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim().startsWith('删除本学期全部资料'))?.click()
    await vi.waitFor(() => {
      expect(host.textContent).toContain('彻底删除：逐项核对 1 份资料')
    })
    expect(getPreview).toHaveBeenCalledTimes(1)
    app.unmount()
  })

  it('shows unattached materials in the other-term section', async () => {
    const { app, host } = await mountTable({ withRecord: false })

    expect(host.textContent).toContain('本学期还没有资料')
    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim().startsWith('展开其他学期'))?.click()
    await nextTick()
    expect(host.textContent).toContain('加入本学期并选中')
    app.unmount()
  })
})
