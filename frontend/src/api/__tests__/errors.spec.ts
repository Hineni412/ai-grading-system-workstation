import { describe, expect, it } from 'vitest';

import { ApiError, isAuthoritativeNotFoundError } from '../errors';

describe('API error contract', () => {

  it('treats only the expected exact 404 code as authoritative absence', () => {
    const missingJob = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'req-10', retryable: false })
    const missingSession = new ApiError({ kind: 'not_found', status: 404,
      code: 'session_not_found', message: 'missing', details: {},
      requestId: 'req-11', retryable: false })

    expect(isAuthoritativeNotFoundError(
      missingJob,
      'config_generation_job_not_found',
    )).toBe(true)
    expect(isAuthoritativeNotFoundError(
      missingSession,
      'config_generation_job_not_found',
    )).toBe(false)
  })
})
