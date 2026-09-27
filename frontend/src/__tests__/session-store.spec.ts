import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/errors';
import { type SessionSummary } from '../api/sessions';
import { SESSION_STORAGE_KEY, SessionDraftOutcomeUnknownError, useSessionStore } from '../stores/session';

const sessions: SessionSummary[] = [
  {
    id: 7,
    name: '七年级数学期末质量监测',
    status: 'configured',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  },
  {
    id: 9,
    name: '超长考试名称用于验证顶部栏不会遮挡主要操作与检查器入口',
    status: 'grading',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('session Store', () => {

  it('reconciles a lost create response from the authoritative session list', async () => {
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    const created = { ...sessions[0]!, id: 12, name: '新考试草稿', status: 'created' }
    const creator = vi.fn(async () => {
      throw new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
        message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    })
    const loader = vi.fn(async () => [...sessions, created])

    await expect(store.createDraft('新考试草稿', creator, loader)).resolves.toEqual(created)
    expect(store.currentSession?.id).toBe(12)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('12')
  })

  it('reports an unknown create outcome when the authoritative list cannot be read', async () => {
    const store = useSessionStore()
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    await expect(store.createDraft('新考试草稿', vi.fn(async () => { throw timeout }),
      vi.fn(async () => { throw new Error('offline') })))
      .rejects.toBeInstanceOf(SessionDraftOutcomeUnknownError)
  })

  it('clears a stale id and never selects the first session', async () => {
    localStorage.setItem(SESSION_STORAGE_KEY, '99')
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    expect(store.selectedSessionId).toBeNull()
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
  })

  /* 注意：store 会把列表原数组写回，这里用副本避免夹具被跨测试污染 */

})
