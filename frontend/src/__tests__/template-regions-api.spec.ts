import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import {
  assignTemplatePageRole,
  fetchRegionWorkspace,
  uploadTemplate,
} from '../api/template-regions'

const template = {
  session_id: 7, template_id: 3, template_fingerprint: 'a'.repeat(64), first_page_role: 'front',
  pages: {
    front: { url: '/api/sessions/7/template/pages/front', width: 1000, height: 1400 },
    back: { url: '/api/sessions/7/template/pages/back', width: 2480, height: 3508 },
  },
  is_confirmed: false, regions_snapshot_pending: false,
}

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200, headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('template region API contract', () => {
  it('rejects any internal path-shaped field in a workspace response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      session_id: 7, template, formal_regions: [],
      draft: { status: 'missing', revision: 0, regions: [], draft_path: 'C:\\private' },
      automatic_candidates: [], manual_question_options: [], issues: [], template_ready: false,
    })))

    await expect(fetchRegionWorkspace(7)).rejects.toMatchObject({
      kind: 'contract', code: 'invalid_success_contract',
    } satisfies Partial<ApiError>)
  })

  it('sends a PDF upload once with its explicit role and recovery token', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response(template))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['%PDF'], '匿名样卷.pdf', { type: 'application/pdf' })

    await uploadTemplate(7, file, 'back', 'b'.repeat(32))

    expect(fetchMock).toHaveBeenCalledOnce()
    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/sessions/7/template?first_page_role=back')
    expect(init).toMatchObject({ method: 'POST', body: file })
    expect((init as RequestInit).headers).toMatchObject({
      'content-type': 'application/pdf', 'x-client-request-token': 'b'.repeat(32),
      'x-content-sha256': '315d429b7714cedb6ad04ac31240145257692630457f3c88253c5beceac76027',
    })
  })

  it('sets the current page role as a target state and versions page images', async () => {
    const reassigned = {
      ...template,
      template_fingerprint: 'b'.repeat(64),
      first_page_role: 'back',
      pages: {
        front: { url: '/api/sessions/7/template/pages/front', width: 2480, height: 3508 },
        back: { url: '/api/sessions/7/template/pages/back', width: 1000, height: 1400 },
      },
    }
    const fetchMock = vi.fn<typeof fetch>(async () => response({
      changed: true,
      draft_sync_pending: false,
      template: reassigned,
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await assignTemplatePageRole(7, 'back', 'a'.repeat(64))

    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/sessions/7/template/page-assignment')
    expect(init).toMatchObject({ method: 'PUT' })
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      first_page_role: 'back',
      expected_template_fingerprint: 'a'.repeat(64),
    })
    expect(result.template.pages.front.url).toBe(
      `/api/sessions/7/template/pages/front?v=${'b'.repeat(64)}`,
    )
    expect(result.template.pages.back.url).toBe(
      `/api/sessions/7/template/pages/back?v=${'b'.repeat(64)}`,
    )
  })
})
