import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import QuestionBankSyncPanel from '../QuestionBankSyncPanel.vue'
import type { CurriculumCatalog } from '../../../api/question-bank'

const curriculum: CurriculumCatalog = {
  schema_version: 1,
  catalog_id: 'test',
  publisher: '北师大',
  subject: '数学',
  edition: '2024',
  volumes: [{
    id: 'bnu24-math-g7-upper',
    label: '七年级上册',
    grade: '七年级',
    semester: '上学期',
    textbook_version: '北师大版（2024）',
    chapters: [],
  }],
}

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 81,
    job_type: 'question_bank_sync',
    payload: { session_id: 7, mode: 'sync', config_revision: 'd'.repeat(64) },
    result: {},
    status: 'queued',
    progress: 0,
    stage: '',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-26T00:00:00Z',
    started_at: null,
    updated_at: '2026-07-26T00:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  setActivePinia(createPinia())
  await useJobStore().initialize({
    api: { getJob: vi.fn(), cancelJob: vi.fn() },
    now: () => new Date(0),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 2_000,
    maxBackoffMs: 30_000,
  })
})

describe('QuestionBankSyncPanel', () => {
  it('auto-starts the independent job once and exposes its own progress', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const submitter = vi.fn(async () => job())
    const consumed = vi.fn()
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'd'.repeat(64),
      autoStart: true,
      submitter,
      curriculumLoader: vi.fn(async () => curriculum),
      onAutoStartConsumed: consumed,
    })

    app.mount(host)
    await settle()

    expect(submitter).toHaveBeenCalledExactlyOnceWith(7, {
      config_revision: 'd'.repeat(64),
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
      curriculum_volume_id: 'bnu24-math-g7-upper',
    })
    expect(consumed).toHaveBeenCalledOnce()
    expect(useJobStore().jobs[81]?.job_type).toBe('question_bank_sync')
    expect(host.textContent).toContain('等待入库')
    expect(host.textContent).toContain('失败不会撤销评分标准')
    app.unmount()
  })

  it('retries only the failed downstream chain', async () => {
    const failed = job({
      status: 'succeeded',
      progress: 1,
      payload: {
        session_id: 7,
        mode: 'sync',
        config_revision: 'e'.repeat(64),
      },
      result: {
        outcome: 'partial',
        imported_count: 12,
        tagged_count: 10,
        linked_count: 12,
        failed_count: 2,
        retryable: true,
      },
    })
    useJobStore().track(failed)
    const retryer = vi.fn(async () => job({ id: 82, payload: {
      session_id: 7, mode: 'tag_retry', retry_of_job_id: 81,
    } }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'e'.repeat(64),
      retryer,
      curriculumLoader: vi.fn(async () => curriculum),
    })

    app.mount(host)
    await nextTick()
    expect(host.textContent).toContain('入库 12 题')
    host.querySelector<HTMLButtonElement>('button')!.click()
    await settle()

    expect(retryer).toHaveBeenCalledWith(
      7,
      81,
      {
        config_revision: 'e'.repeat(64),
        client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
        curriculum_volume_id: 'bnu24-math-g7-upper',
      },
    )
    expect(useJobStore().jobs[82]?.payload.mode).toBe('tag_retry')
    app.unmount()
  })

  it('requires restoring an identical trashed paper instead of retrying the sync', async () => {
    useJobStore().track(job({
      status: 'succeeded',
      progress: 1,
      result: {
        outcome: 'failed',
        imported_count: 0,
        tagged_count: 0,
        linked_count: 0,
        failed_count: 1,
        retryable: false,
        restore_required: true,
        restore_paper_id: 17,
      },
    }))
    const retryer = vi.fn(async () => job({ id: 85 }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'd'.repeat(64),
      retryer,
      curriculumLoader: vi.fn(async () => curriculum),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent).toContain('相同试卷已在题库回收站')
    expect(host.textContent).toContain('恢复原试卷')
    expect(host.textContent).not.toContain('只重试未完成的题库流程')
    expect(retryer).not.toHaveBeenCalled()
    app.unmount()
  })

  it('offers recovery when an interrupted job has no structured result', async () => {
    useJobStore().track(job({
      status: 'failed',
      payload: {
        session_id: 7,
        mode: 'sync',
        config_revision: 'f'.repeat(64),
      },
      error: 'Job failed; see local logs for details.',
      finished_at: '2026-07-26T00:01:00Z',
      result: {},
    }))
    const retryer = vi.fn(async () => job({
      id: 83,
      payload: {
        session_id: 7,
        mode: 'sync_retry',
        retry_of_job_id: 81,
        config_revision: 'f'.repeat(64),
      },
    }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'f'.repeat(64),
      retryer,
      curriculumLoader: vi.fn(async () => curriculum),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent).toContain('题库流程异常结束')
    const retryButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('只重试未完成的题库流程'))
    expect(retryButton).toBeDefined()
    retryButton!.click()
    await settle()
    expect(retryer).toHaveBeenCalledWith(
      7,
      81,
      {
        config_revision: 'f'.repeat(64),
        client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
        curriculum_volume_id: 'bnu24-math-g7-upper',
      },
    )
    app.unmount()
  })

  it('keeps taxonomy-only evidence work visible and locally retryable', async () => {
    useJobStore().track(job({
      status: 'succeeded',
      progress: 1,
      result: {
        outcome: 'complete',
        imported_count: 1,
        tagged_count: 1,
        linked_count: 1,
        failed_count: 0,
        review_count: 1,
        taxonomy_review_count: 1,
        taxonomy_review_question_ids: [101],
        taxonomy_review_source_refs: ['Q1'],
        retryable: true,
      },
    }))
    const retryer = vi.fn(async () => job({ id: 84 }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'd'.repeat(64),
      retryer,
      curriculumLoader: vi.fn(async () => curriculum),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent).toContain('1 题标签待补充或归并')
    expect(host.textContent).toContain('含 1 个新标签')
    expect(host.textContent).toContain('试卷已入库，标签仍待补充或归并')
    expect(host.textContent).not.toContain('试卷已入库并完成标签治理')
    expect(host.textContent).toContain('待处理：Q1')
    const retryButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('标签处理后重新本地校验'))
    expect(retryButton).toBeDefined()
    retryButton!.click()
    await settle()
    expect(retryer).toHaveBeenCalledOnce()
    app.unmount()
  })
})
