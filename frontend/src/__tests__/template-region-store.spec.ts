import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import * as api from '../api/template-regions'
import { useTemplateRegionStore } from '../stores/template-regions'

vi.mock('../api/template-regions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/template-regions')>(),
  fetchRegionWorkspace: vi.fn(), saveRegionDraft: vi.fn(), uploadTemplate: vi.fn(),
  fetchTemplateSubmission: vi.fn(), abandonTemplateSubmission: vi.fn(),
  discardRegionDraft: vi.fn(), commitRegions: vi.fn(), retryRegionSnapshot: vi.fn(),
}))

function workspace(sessionId = 7): api.RegionWorkspace {
  return {
    session_id: sessionId,
    template: {
      session_id: sessionId, template_id: 3, template_fingerprint: 'a'.repeat(64),
      first_page_role: 'front', pages: {
        front: { url: `/api/sessions/${sessionId}/template/pages/front`, width: 1000, height: 1400 },
        back: { url: `/api/sessions/${sessionId}/template/pages/back`, width: 1000, height: 1400 },
      }, is_confirmed: false, regions_snapshot_pending: false,
    },
    formal_regions: [], draft: { status: 'missing', revision: 0, regions: [] },
    automatic_candidates: ['Q1'], manual_question_options: [], issues: [], template_ready: false,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('template region store', () => {
  it('ignores a late workspace response from a previously selected session', async () => {
    let release!: (value: api.RegionWorkspace) => void
    vi.mocked(api.fetchRegionWorkspace)
      .mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
      .mockResolvedValueOnce(workspace(8))
    const store = useTemplateRegionStore()

    const oldLoad = store.load(7)
    await store.load(8)
    release(workspace(7))
    await oldLoad

    expect(store.sessionId).toBe(8)
    expect(store.workspace?.session_id).toBe(8)
  })

  it('stops autosave on a 409 and keeps the current in-memory edit', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.saveRegionDraft).mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'region_draft_revision_conflict', message: 'conflict',
      details: {}, requestId: 'rid', retryable: false,
    }))
    const store = useTemplateRegionStore()
    await store.load(7)
    store.updateEditor({ revision: 1, regions: [{ region_uuid: 'local', page: 'front',
      region_order: 1, x: 1, y: 2, w: 30, h: 40, mapped_question_id: null,
      mapping_status: 'unbound', multi_region_confirmed: false }] })

    await store.flushDraft()

    expect(store.saveState).toBe('conflict')
    expect(store.editorState.regions[0]?.region_uuid).toBe('local')
    store.updateEditor({ revision: 2, regions: [] })
    expect(store.editorState.regions[0]?.region_uuid).toBe('local')
  })
})
