import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import * as api from '../api/template-regions'
import {
  TEMPLATE_REGION_UPLOAD_STORAGE_KEY,
  useTemplateRegionStore,
} from '../stores/template-regions'

vi.mock('../api/template-regions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/template-regions')>(),
  fetchRegionReadiness: vi.fn(), fetchRegionWorkspace: vi.fn(), fetchRegionAutoProposal: vi.fn(), saveRegionDraft: vi.fn(), uploadTemplate: vi.fn(),
  assignTemplatePageRole: vi.fn(),
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
  localStorage.clear()
  vi.mocked(api.fetchRegionReadiness).mockResolvedValue({
    session_id: 7, scoring_configured: true, template_present: true, template_ready: false,
  })
  vi.mocked(api.fetchRegionAutoProposal).mockReset().mockResolvedValue({
    regions: [], missing_question_ids: ['Q1', '__student_name__'], template_fingerprint: 'a'.repeat(64),
  })
  vi.mocked(api.saveRegionDraft).mockReset().mockImplementation(async (_id, request) => ({
    status: 'compatible', session_id: 7, template_id: 3, template_fingerprint: 'a'.repeat(64),
    draft: { revision: request.revision, regions: request.regions },
  }))
})

function autoRegion(id = 'Q1'): api.Region {
  return { region_uuid: `auto-${id}`, page: 'front', region_order: 1,
    x: 30, y: 100, w: 800, h: 300, mapped_question_id: id, mapping_status: 'auto',
    is_confirmed: false, multi_region_confirmed: false, detected_question_id: id, confidence: 0.95 }
}

function proposal(regions = [autoRegion(), autoRegion('__student_name__')]): api.RegionAutoProposal {
  return { regions, missing_question_ids: [], template_fingerprint: 'a'.repeat(64) }
}

describe('template region store', () => {

  it('automatically fills an empty workspace, saves metadata and restores the saved draft', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.fetchRegionAutoProposal).mockResolvedValue(proposal())
    const store = useTemplateRegionStore()
    await store.load(7)
    expect(api.fetchRegionAutoProposal).toHaveBeenCalledOnce()
    expect(store.editorState.regions).toEqual(proposal().regions)
    expect(store.saveState).toBe('saved')
    expect(store.missingQuestionIds).toEqual([])
    expect(api.saveRegionDraft).toHaveBeenCalledWith(7, expect.objectContaining({
      revision: 1, expected_revision: 0, regions: proposal().regions,
    }))
    const restored = workspace()
    restored.draft = { status: 'compatible', revision: 1, regions: proposal().regions }
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(restored)
    await store.load(7)
    expect(store.draftChoiceRequired).toBe(true)
    store.continueDraft()
    expect(store.editorState.regions).toEqual(proposal().regions)
    expect(api.fetchRegionAutoProposal).toHaveBeenCalledOnce()
  })

  it.each(['draft', 'formal'] as const)('does not automatically replace existing %s regions', async (kind) => {
    const current = workspace()
    if (kind === 'draft') current.draft = { status: 'compatible', revision: 3, regions: [autoRegion()] }
    else { current.formal_regions = [autoRegion()]; current.template_ready = true }
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(current)
    const store = useTemplateRegionStore()
    await store.load(7)
    expect(api.fetchRegionAutoProposal).not.toHaveBeenCalled()
    if (kind === 'draft') store.continueDraft()
    else await store.startEditingConfirmed()
    vi.mocked(api.fetchRegionAutoProposal).mockResolvedValue(proposal())
    await store.autoPropose()
    expect(store.editorState.regions).toEqual(proposal().regions)
    expect(store.saveState).toBe('saved')
  })

  it('discards a proposal whose template fingerprint differs', async () => {
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.fetchRegionAutoProposal).mockResolvedValue({ ...proposal(), template_fingerprint: 'b'.repeat(64) })
    const store = useTemplateRegionStore()
    await store.load(7)
    expect(store.editorState.regions).toEqual([])
    expect(api.saveRegionDraft).not.toHaveBeenCalled()
    expect(store.autoProposalMessage).toContain('已丢弃')
  })

  it.each(['cancel', 'edit', 'switch'] as const)('discards a late result after %s', async (action) => {
    let resolve: (value: api.RegionAutoProposal) => void = () => {}
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(workspace())
    vi.mocked(api.fetchRegionAutoProposal).mockImplementation(() => new Promise((done) => { resolve = done }))
    const store = useTemplateRegionStore()
    const loading = store.load(7)
    await vi.waitFor(() => expect(store.autoProposalState).toBe('loading'))
    if (action === 'edit') store.updateEditor({ revision: 1, regions: [autoRegion('Q2')] })
    else if (action === 'switch') {
      vi.mocked(api.fetchRegionReadiness).mockResolvedValue({ session_id: 8,
        scoring_configured: false, template_present: false, template_ready: false })
      await store.load(8)
    } else store.cancelAutoProposal()
    resolve(proposal())
    await loading
    expect(store.editorState.regions.map((item) => item.mapped_question_id)).toEqual(action === 'edit' ? ['Q2'] : [])
    expect(store.autoProposalState).toBe('idle')
  })

  it('keeps the current draft on OCR failure and updates missing detail IDs when a parent is bound', async () => {
    const current = workspace()
    current.automatic_candidates = ['Q1', 'Q2(P1)', 'Q2(P2)', 'Q3']
    current.draft = { status: 'compatible', revision: 3, regions: [autoRegion('Q2')] }
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(current)
    const store = useTemplateRegionStore()
    await store.load(7)
    store.continueDraft()
    vi.mocked(api.fetchRegionAutoProposal).mockRejectedValue(new Error('model missing'))
    await store.autoPropose()
    expect(store.editorState.regions).toEqual([autoRegion('Q2')])
    expect(store.missingQuestionIds).toEqual(['Q1', 'Q3', '__student_name__'])
    expect(store.autoProposalMessage).toContain('未能自动框题')
    expect(api.saveRegionDraft).not.toHaveBeenCalled()
  })

  it('restores editing for a compatible draft over an already confirmed template', async () => {
    const current = workspace()
    current.template_ready = true
    current.formal_regions = [autoRegion()]
    current.draft = { status: 'compatible', revision: 3, regions: [autoRegion('Q2')] }
    vi.mocked(api.fetchRegionWorkspace).mockResolvedValue(current)
    const store = useTemplateRegionStore()
    await store.load(7)
    store.continueDraft()
    expect(store.readOnly).toBe(false)
    expect(store.canAutoPropose).toBe(true)
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

  it('restores an unknown upload token after the page store is recreated', async () => {
    vi.mocked(api.fetchRegionReadiness).mockResolvedValue({ session_id: 7,
      scoring_configured: true, template_present: false, template_ready: false })
    vi.mocked(api.uploadTemplate).mockRejectedValue(new ApiError({ kind: 'server', status: 500,
      code: 'template_upload_failed', message: 'failed', details: {},
      requestId: 'rid-upload', retryable: true }))
    const firstStore = useTemplateRegionStore()
    await firstStore.load(7)
    await firstStore.upload(new File(['%PDF'], 'sample.pdf', { type: 'application/pdf' }), 'front')
    const token = firstStore.pendingUploadToken

    setActivePinia(createPinia())
    const restoredStore = useTemplateRegionStore()
    await restoredStore.load(7)

    expect(restoredStore.uploadState).toBe('unknown')
    expect(restoredStore.pendingUploadToken).toBe(token)
    expect(JSON.parse(localStorage.getItem(TEMPLATE_REGION_UPLOAD_STORAGE_KEY) ?? '{}'))
      .toEqual({ sessionId: 7, requestToken: token })
  })

})
