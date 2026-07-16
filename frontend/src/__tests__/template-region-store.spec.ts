import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import * as api from '../api/template-regions'
import { useTemplateRegionStore } from '../stores/template-regions'

vi.mock('../api/template-regions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/template-regions')>(),
  fetchRegionReadiness: vi.fn(), fetchRegionWorkspace: vi.fn(), saveRegionDraft: vi.fn(), uploadTemplate: vi.fn(),
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
  vi.mocked(api.fetchRegionReadiness).mockResolvedValue({
    session_id: 7, scoring_configured: true, template_present: true, template_ready: false,
  })
})

describe('template region store', () => {
  it('ignores a late workspace response from a previously selected session', async () => {
    let release!: (value: api.RegionWorkspace) => void
    vi.mocked(api.fetchRegionWorkspace)
      .mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
      .mockResolvedValueOnce(workspace(8))
    vi.mocked(api.fetchRegionReadiness).mockImplementation(async (sessionId) => ({
      session_id: sessionId, scoring_configured: true, template_present: true,
      template_ready: false,
    }))
    const store = useTemplateRegionStore()

    const oldLoad = store.load(7)
    await Promise.resolve()
    await Promise.resolve()
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

  it('requires an explicit choice before restoring a compatible draft', async () => {
    const value = workspace()
    value.draft = { status: 'compatible', revision: 4, regions: [{
      region_uuid: 'draft-region', page: 'front', region_order: 1,
      x: 10, y: 20, w: 100, h: 80, mapped_question_id: 'Q1',
      mapping_status: 'manual', is_confirmed: false, multi_region_confirmed: false,
    }] }
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(value)
    const store = useTemplateRegionStore()

    await store.load(7)

    expect(store.draftChoiceRequired).toBe(true)
    expect(store.editorReady).toBe(false)
    store.continueDraft()
    expect(store.editorReady).toBe(true)
    expect(store.editorState.regions[0]?.region_uuid).toBe('draft-region')
  })

  it('opens the issue drawer when formal validation rejects the regions', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.saveRegionDraft).mockResolvedValue({
      status: 'compatible', session_id: 7, template_id: 3,
      template_fingerprint: 'a'.repeat(64), draft: { revision: 1, regions: [] },
    })
    vi.mocked(api.commitRegions).mockResolvedValue({ committed: false,
      snapshot_pending: false, error: 'validation_failed', region_count: 0,
      issues: [{ code: 'missing_region', message: 'missing', region_uuid: null,
        question_id: 'Q1' }] })
    const store = useTemplateRegionStore()
    await store.load(7)
    store.updateEditor({ revision: 1, regions: [] })

    await store.commit()

    expect(store.editorState.drawer_open).toBe(true)
    expect(store.workspace?.issues[0]?.code).toBe('missing_region')
  })

  it('creates a new editable draft before re-editing a confirmed version', async () => {
    const value = workspace()
    value.template.is_confirmed = true
    value.template_ready = true
    value.formal_regions = [{ region_uuid: 'formal', page: 'front', region_order: 1,
      x: 10, y: 20, w: 100, h: 80, mapped_question_id: 'Q1', mapping_status: 'manual',
      is_confirmed: true, multi_region_confirmed: false }]
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(value)
    vi.mocked(api.saveRegionDraft).mockResolvedValue({ status: 'compatible', session_id: 7,
      template_id: 3, template_fingerprint: 'a'.repeat(64),
      draft: { revision: 1, regions: [] } })
    const store = useTemplateRegionStore()
    await store.load(7)

    await store.startEditingConfirmed()

    expect(store.readOnly).toBe(false)
    expect(store.editorState.regions[0]?.is_confirmed).toBe(false)
    expect(api.saveRegionDraft).toHaveBeenCalledOnce()
  })

  it('turns a commit lock timeout into a safe retry message', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.commitRegions).mockRejectedValue(new ApiError({ kind: 'server', status: 503,
      code: 'answer_region_lock_timeout', message: 'busy', details: {},
      requestId: 'rid-lock', retryable: true }))
    const store = useTemplateRegionStore()
    await store.load(7)

    const result = await store.commit()

    expect(result).toBeNull()
    expect(store.errorMessage).toBe('当前考试正在被另一项操作使用，请稍后重试完成标定。')
  })
})
