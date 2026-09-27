import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import QuestionBankSyncPanel from '../QuestionBankSyncPanel.vue'
import type { CurriculumCatalog } from '../../../api/question-bank'

const curriculum: CurriculumCatalog = {
  schema_version: 2,
  catalog_id: 'test',
  knowledge_standard_id: 'test-standard',
  publisher: '北师大',
  subject: '数学',
  edition: '2024',
  statistics: {
    raw_nodes: 1,
    excluded_nodes: 0,
    retained_nodes: 1,
    chapters: 0,
    sections: 0,
    knowledge_points: 0,
  },
  volumes: [{
    id: 'bnu24-math-g7-upper',
    order: 1,
    label: '七年级上册',
    grade: '七年级',
    semester: '上学期',
    textbook_version: '北师大版（2024）',
    source: { provider: '组卷网' },
    statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
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
    api: { getJob: vi.fn(), cancelJob: vi.fn(), getJobStatusBatch: vi.fn(async () => []) },
    now: () => new Date(0),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 2_000,
    maxBackoffMs: 30_000,
  })
})

describe('QuestionBankSyncPanel', () => {
  it('restores the saved server job after a page refresh', async () => {
    const restored = job({
      id: 91,
      status: 'succeeded',
      progress: 1,
      result: {
        outcome: 'partial',
        imported_count: 12,
        question_count: 12,
        tagged_count: 0,
        complete_tagged_count: 0,
        criteria_count: 0,
        failed_count: 12,
        retryable: true,
      },
    })
    const latestJobLoader = vi.fn(async () => restored)
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '七年级上册阶段练习',
      configRevision: 'd'.repeat(64),
      latestJobLoader,
      curriculumLoader: vi.fn(async () => curriculum),
    })

    app.mount(host)
    await settle()

    expect(latestJobLoader).toHaveBeenCalledExactlyOnceWith(7)
    expect(host.textContent).toContain('题目入库成功 12 题')
    expect(host.textContent).toContain('继续完成未完成题目')
    expect(host.textContent).not.toContain('将试卷入库并打标签')
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
    await settle()
    expect(host.textContent).toContain('题目入库成功 12 题')
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

  it('continues the current incomplete questions instead of replaying old failures', async () => {
    useJobStore().track(job({
      status: 'succeeded',
      progress: 1,
      payload: {
        session_id: 7,
        mode: 'sync',
        config_revision: 'd'.repeat(64),
        curriculum_volume_id: 'bnu24-math-g7-upper',
      },
      result: {
        outcome: 'complete',
        imported_count: 12,
        failed_count: 0,
        retryable: false,
      },
    }))
    const statusLoader = vi.fn(async () => ({
      question_count: 12,
      tagged_count: 9,
      evidence_count: 12,
      criteria_count: 12,
      complete_count: 9,
      pending_taxonomy_count: 9,
      incomplete_question_ids: [2, 3, 6],
      incomplete_source_refs: ['Q2', 'Q3', 'Q6'],
    }))
    const retryer = vi.fn()
    const continuationSubmitter = vi.fn(async () => job({
      id: 100,
      job_type: 'tagging_sync',
      payload: {
        question_ids: [2, 3, 6],
        curriculum_volume_id: 'bnu24-math-g7-upper',
      },
    }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(QuestionBankSyncPanel, {
      sessionId: 7,
      sessionName: '无法从标题推断册别',
      configRevision: 'd'.repeat(64),
      retryer,
      continuationSubmitter,
      curriculumLoader: vi.fn(async () => curriculum),
      statusLoader,
    })

    app.mount(host)
    await settle()
    const continueButton = [...host.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('继续完成未完成题目'))
    expect(continueButton).toBeDefined()
    continueButton!.click()
    await settle()

    expect(continuationSubmitter).toHaveBeenCalledWith(
      [2, 3, 6],
      'bnu24-math-g7-upper',
      expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(retryer).not.toHaveBeenCalled()
    app.unmount()
  })
})
