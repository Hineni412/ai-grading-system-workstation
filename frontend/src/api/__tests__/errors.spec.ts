import { describe, expect, it } from 'vitest'

import {
  ApiError,
  isAuthoritativeNotFoundError,
  parseErrorResponse,
  toNotification,
} from '../errors'

describe('API error contract', () => {
  it('keeps only the validated backend error and matching request id', () => {
    const error = parseErrorResponse(
      {
        error: {
          code: 'validation_error',
          message: 'Invalid request',
          details: { errors: [] },
          request_id: 'req-7',
        },
      },
      'req-7',
      422,
    )

    expect(error).toMatchObject({
      kind: 'validation',
      status: 422,
      code: 'validation_error',
      requestId: 'req-7',
      retryable: false,
    })
  })

  it('rejects request-id disagreement as a contract error', () => {
    const error = parseErrorResponse(
      {
        error: {
          code: 'job_not_found',
          message: 'Job not found',
          details: {},
          request_id: 'body-id',
        },
      },
      'header-id',
      404,
    )

    expect(error).toMatchObject({
      kind: 'contract',
      code: 'invalid_error_contract',
      requestId: 'header-id',
    })
  })

  it('never places details in the user notification', () => {
    const error = new ApiError({
      kind: 'server',
      status: 500,
      code: 'server_error',
      message: 'Request failed',
      details: { path: 'private' },
      requestId: 'req-9',
      retryable: true,
    })

    expect(toNotification(error, '考试列表保持上次内容')).toEqual({
      message: 'Request failed',
      impact: '考试列表保持上次内容',
      retryable: true,
      requestId: 'req-9',
    })
  })

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
