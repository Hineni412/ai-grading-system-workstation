import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  MaterialVersion,
  ReferencePptCollection,
  SemesterMappingProposal,
  SemesterMaterialRecord,
} from '../../api/catalog'
import { teachingPrepCatalogApi } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import ChapterGrid from './ChapterGrid.vue'

const semesterId = 's'.repeat(32)
const collectionId = '9'.repeat(32)

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

function treeProposal(recordIds: string[]): SemesterMappingProposal {
  return {
    id: 'p'.repeat(32), semester_id: semesterId, operation_id: 'operation-tree',
    source_state_sha256: 'f'.repeat(64), status: 'proposed',
    payload: {
      tree: [{
        key: 'ppt-chapter-1', title: '第一章 勾股定理',
        sections: [{
          key: 'ppt-section-1-1', title: '1.1',
          lessons: [
            { key: 'ppt-lesson-1-1-1', title: '1.1 第1课时 认识勾股定理', duration_minutes: 45 },
            { key: 'ppt-lesson-1-1-2', title: '1.1 第2课时 验证勾股定理', duration_minutes: 45 },
          ],
        }],
      }],
      mappings: [], uncertainties: [], source_material_record_ids: recordIds,
      generation_source: 'local_reference_ppt_names',
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  }
}

function textbookProposal(recordId: string): SemesterMappingProposal {
  return {
    id: 'q'.repeat(32), semester_id: semesterId, operation_id: 'operation-book',
    source_state_sha256: 'e'.repeat(64), status: 'proposed',
    payload: {
      tree: [{
        key: 't-chapter-1', title: '第一章 勾股定理',
        sections: [{
          key: 't-section-1-1', title: '1.1',
          lessons: [{ key: 't-lesson-1-1-1', title: '1.1 探索勾股定理', duration_minutes: 45 }],
        }],
      }],
      mappings: [{
        mapping_id: 'mapping-t1', material_record_id: recordId,
        lesson_ref: 'proposal:t-lesson-1-1-1', start_unit: 2, end_unit: 9,
        purpose: 'textbook', decision: 'accepted',
        teacher_revision: null, decision_reason: null,
      }],
      uncertainties: [], source_material_record_ids: [recordId],
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  }
}

const ppt1 = material('a'.repeat(32), '1.1 认识勾股定理.pptx', 26)
const ppt2 = material('b'.repeat(32), '1.2 直角三角形.pptx', 27)
const record1 = record('r'.repeat(32), ppt1)
const record2 = record('t'.repeat(32), ppt2)

function folderCollection(): ReferencePptCollection {
  return {
    id: collectionId, semester_id: semesterId, display_name: '八上课件',
    mapping_proposal_id: 'p'.repeat(32), ignored_file_count: 0, is_active: true,
    revision: 1, created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
    members: [record1, record2].map((item, index) => ({
      id: `${collectionId}-m${index}`, collection_id: collectionId,
      material_record_id: item.id,
      relative_path: `第一章 勾股定理/${index}.pptx`,
      kind: 'lesson' as const, confidence: 'high' as const,
      chapter_number: null, section_number: null, subsection_number: null,
      lesson_number: null, normalized_title: item.display_name,
      evidence: [], issues: [], created_at: '2026-08-03T00:00:00Z',
    })),
  }
}

async function mountGrid(options: { withValueHint?: boolean } = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.materials = [ppt1, ppt2]
  catalog.semesterMaterials = [record1, record2]
  catalog.referencePptCollections = [folderCollection()]
  catalog.semesterMappingProposals = [
    treeProposal([record1.id, record2.id]),
    ...(options.withValueHint ? [textbookProposal('x'.repeat(32))] : []),
  ]
  vi.spyOn(teachingPrepCatalogApi, 'listReferencePptCollections').mockResolvedValue([])

  const selections: unknown[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(ChapterGrid, {
    folderKey: `${collectionId}:第一章 勾股定理`,
    onSelect: (selection: unknown) => selections.push(selection),
  })
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, selections }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('ChapterGrid', () => {
  it('renders the compact file grid with aggregated page counts', async () => {
    const { app, host } = await mountGrid()

    expect(host.textContent).toContain('2 份课件 · 共 53 页')
    const cells = host.querySelectorAll('[data-testid="chapter-file-grid"] .tp-file-cell')
    expect(cells).toHaveLength(2)
    expect(cells[0]?.textContent).toContain('1.1 认识勾股定理.pptx')
    expect(cells[0]?.textContent).toContain('26 页 · 已处理好')
    app.unmount()
  })

  it('shows the chapter value hint with lesson count and textbook range', async () => {
    const { app, host } = await mountGrid({ withValueHint: true })

    const hint = host.querySelector('.tp-value-hint')
    expect(hint?.textContent).toContain('已对应 2 个课时')
    expect(hint?.textContent).toContain('教材 P2–9')
    app.unmount()
  })

  it('selects a material when a file cell is clicked', async () => {
    const { app, host, selections } = await mountGrid()

    host.querySelector<HTMLButtonElement>('[data-testid="chapter-file-grid"] .tp-file-cell')?.click()
    await nextTick()
    expect(selections[0]).toEqual({ kind: 'material', materialId: ppt1.id })
    app.unmount()
  })
})
