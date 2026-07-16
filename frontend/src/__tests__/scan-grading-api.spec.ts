import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import { fetchGradingWorkspace, uploadScan } from '../api/scan-grading'

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('scan grading API contract', () => {
  it('accepts a path-free empty workspace', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      session_id: 7,
      upload_batch: {
        batch_id: 'batch-1', revision: 0, state: 'draft', files: [], file_count: 0,
        total_bytes: 0, frozen_at: null,
      },
      grading_run: null,
    })))

    await expect(fetchGradingWorkspace(7)).resolves.toMatchObject({ session_id: 7 })
  })

  it('rejects a path-shaped field returned by the workspace endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({
      session_id: 7,
      upload_batch: {
        batch_id: 'batch-1', revision: 0, state: 'draft', files: [], file_count: 0,
        total_bytes: 0, frozen_at: null, storage_path: 'C:\\private',
      },
      grading_run: null,
    })))

    await expect(fetchGradingWorkspace(7)).rejects.toMatchObject({
      kind: 'contract', code: 'invalid_success_contract',
    } satisfies Partial<ApiError>)
  })

  it('uploads a scan with its encoded filename and content digest', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response({
      duplicate: false,
      file: {
        id: 'file-1', name: '答卷 1.jpg', media_type: 'image/jpeg', size_bytes: 4,
        sha256_prefix: '9f64a747e1b9', added_at: '2026-07-16T12:00:00Z',
      },
    }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File([new Uint8Array([1, 2, 3, 4])], '答卷 1.jpg', { type: 'image/jpeg' })

    await uploadScan(7, file)

    const [path, init] = fetchMock.mock.calls[0]!
    expect(path).toBe('/api/sessions/7/scan-uploads')
    expect(init).toMatchObject({ method: 'POST', body: file })
    expect((init as RequestInit).headers).toMatchObject({
      'content-type': 'image/jpeg',
      'x-upload-filename': encodeURIComponent('答卷 1.jpg'),
      'x-content-sha256': '9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a',
    })
  })
})
