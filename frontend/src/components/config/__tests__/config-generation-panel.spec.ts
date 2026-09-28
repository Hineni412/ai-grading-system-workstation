import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { ConfigEditorResponse, ConfigGenerationRequest, ConfigSource } from '../../../api/config-workspace';
import { ApiError } from '../../../api/errors';
import type { JobResponse } from '../../../api/jobs';
import type { CurriculumCatalog } from '../../../api/question-bank';
import { useConfigWorkspaceStore } from '../../../stores/config-workspace';

import { useJobStore } from '../../../stores/jobs';
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import ConfigGenerationPanel from '../ConfigGenerationPanel.vue'

function source(): ConfigSource {
  return {
    session_id: 7, source_id: 'a'.repeat(32), source_revision: 'b'.repeat(64),
    safe_filename: '数学卷.pdf', suffix: '.pdf', size_bytes: 4096,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready',
    questions: [
      { question_id: 'Q1', question_type: 'calculation', question_preview: '1',
        answer_preview: '1', answer_present: true, needs_review: false,
        local_answer_trusted: true, has_question_asset: false, has_answer_asset: false },
      { question_id: 'Q2', question_type: 'proof', question_preview: '2',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
      { question_id: 'Q5', question_type: 'comprehensive', question_preview: '5',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
    ],
  }
}

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 31, job_type: 'config_generation',
    payload: { session_id: 7, mode: 'generate', generation_mode: 'batched',
      source_id: 'a'.repeat(32), source_revision: 'b'.repeat(64) },
    result: {}, status: 'running', progress: 0.4, stage: 'generating', detail: '',
    error: null, cancel_requested: false, created_at: '2026-07-15T00:00:00Z',
    started_at: '2026-07-15T00:00:01Z', updated_at: '2026-07-15T00:00:02Z',
    finished_at: null, ...overrides,
  }
}

function editor(): ConfigEditorResponse {
  return {
    session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [],
    total_score: 100, issues: [], source: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function curriculum(): CurriculumCatalog {
  return {
    schema_version: 2,
    catalog_id: 'test-catalog',
    knowledge_standard_id: 'test-standard',
    publisher: '北京师范大学出版社',
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
      label: '七年级数学上册',
      grade: '七年级',
      semester: '上册',
      textbook_version: '北师大版',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [],
    }],
  }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

async function mountPanel(options: {
  sessionName?: string
  submitter?: (sessionId: number, request: ConfigGenerationRequest) => Promise<JobResponse>
  retryer?: (
    sessionId: number,
    jobId: number,
    questionIds: string[],
    requestToken: string,
    confirmUncertainRetry?: boolean,
  ) => Promise<JobResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
  requestAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
  curriculumLoader?: () => Promise<CurriculumCatalog>
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConfigGenerationPanel, {
    sessionName: '七年级数学上册测试',
    curriculumLoader: async () => curriculum(),
    ...options,
  })
  app.mount(host)
  await settle()
  return { host, unmount: () => app.unmount() }
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  setActivePinia(createPinia())
  const configStore = useConfigWorkspaceStore()
  configStore.selectSession(7)
  configStore.setSource(source())
  await useJobStore().initialize({
    api: { getJob: vi.fn(), cancelJob: vi.fn(), getJobStatusBatch: vi.fn(async () => []) }, now: () => new Date(0),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>), cancelScheduled: vi.fn(),
    pollIntervalMs: 2_000, maxBackoffMs: 30_000,
  })
})

describe('ConfigGenerationPanel', () => {
  it('reuses the global catalog, follows the term, and preserves a manual choice', async () => {
    const scope = useCurriculumScopeStore()
    const catalog = curriculum()
    catalog.volumes.push({ ...catalog.volumes[0]!, id: 'g8-upper', label: '八年级上册' })
    await scope.initialize(async () => catalog)
    scope.selectVolume('g8-upper')
    const loader = vi.fn(async () => catalog)
    const { host, unmount } = await mountPanel({ curriculumLoader: loader })
    const select = host.querySelector('select')!
    expect(loader).not.toHaveBeenCalled()
    expect(select.value).toBe('g8-upper')
    scope.selectVolume('bnu24-math-g7-upper')
    await settle()
    expect(select.value).toBe('bnu24-math-g7-upper')
    select.value = 'g8-upper'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    scope.selectVolume(null)
    await settle()
    expect(select.value).toBe('g8-upper')
    unmount()
  })

  it('shows a failed catalog load and recovers through the inline retry', async () => {
    const loader = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(curriculum())
    const { host, unmount } = await mountPanel({ curriculumLoader: loader })
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('教材目录暂时无法读取')
    const retry = [...host.querySelectorAll('button')].find(button => button.textContent?.includes('重试读取教材'))!
    retry.click()
    await settle()
    expect(host.querySelector('select')?.value).toBe('bnu24-math-g7-upper')
    expect(loader).toHaveBeenCalledTimes(2)
    expect(host.querySelector('[role="alert"]')).toBeNull()
    unmount()
  })

  it('shows incomplete intake distinctly and resumes stored analysis without a model retry', async () => {
    const retryer = vi.fn(async () => job({ id: 35, status: 'queued' }))
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ status: 'succeeded', progress: 1, result: {
      outcome: 'partial', total_questions: 14, generated_questions: 14,
      failed_count: 0, failed_batches: [], retryable: true,
      question_bank_sync_requested: true, question_bank_sync_state: 'partial',
      question_bank_imported_count: 13, question_bank_tagged_count: 13,
      exam_intake_complete: false, exam_intake_failed_question_ids: ['Q6'],
      exam_intake_error: 'Q6 未能对应到独立的题库记录',
      score_allocation_pending: true, score_allocation_failed: false,
    } }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ retryer })
    expect(mounted.host.textContent).toContain('入库未完成，尚未赋分')
    expect(mounted.host.textContent).toContain('已分析 14/14 题 · 已入库 13/14 题')
    expect(mounted.host.textContent).toContain('定位 Q6')
    expect(mounted.host.querySelector('button[name="重新进行统一配分"]')).toBeNull()
    expect(mounted.host.textContent).not.toContain('评分标准生成成功')
    mounted.host.querySelector<HTMLButtonElement>('button[name="继续入库并赋分"]')!.click()
    await settle()
    expect(retryer).toHaveBeenCalledWith(7, 31, [], expect.stringMatching(/^[0-9a-f]{32}$/))
    mounted.unmount()
  })

  it('reconciles a lost generation response from the authoritative server record', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const submitter = vi.fn(async () => { throw timeout })
    const recovered = job({ id: 44, status: 'queued', progress: 0 })
    const generationLoader = vi.fn(async () => recovered)
    const mounted = await mountPanel({ submitter, generationLoader })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledWith(7, expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(useConfigWorkspaceStore().jobId).toBe(44)
    expect(useConfigWorkspaceStore().pendingGenerationMode).toBeNull()
    expect(mounted.host.textContent).not.toContain('未提交成功')
  })

  it('persists an unknown generation outcome and blocks duplicate submission', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const mounted = await mountPanel({
      submitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw timeout }),
    })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingGenerationMode).toBe('batched')
    expect(localStorage.getItem('ai-grading:config-workspace:v1')).toContain('pendingGenerationMode')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')?.disabled).toBe(true)
    expect(mounted.host.querySelector('button[name="重新核对生成任务"]')).not.toBeNull()
  })

  it('retries only checked failed batches and preserves successful counts', async () => {
    const retryer = vi.fn(async () => job({ id: 32, status: 'queued', progress: 0 }))
    const configStore = useConfigWorkspaceStore()
    const jobStore = useJobStore()
    jobStore.track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 5, generated_questions: 3,
        failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
        failed_batches: [
          {
            batch_id: 'B001',
            question_ids: ['Q2'],
            category: 'model_transport',
            error: '模型服务暂时不可用。',
          },
          { batch_id: 'B002', question_ids: ['Q5'] },
        ],
        score_allocation_pending: true,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ retryer })

    expect(mounted.host.textContent).toContain('已成功 3 道题')
    expect(mounted.host.textContent).toContain('失败 2 道题')
    expect(mounted.host.textContent).toContain('模型服务或网络请求失败')
    expect(mounted.host.textContent).toContain('模型服务暂时不可用')
    expect(mounted.host.querySelectorAll('input[type="checkbox"]')).toHaveLength(2)
    expect(mounted.host.querySelector('button[name="重新进行统一配分"]')).toBeNull()
    mounted.host.querySelector<HTMLInputElement>('[aria-label="选择失败批次 B002"]')!.click()
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="重试所选批次"]')!.click()
    await settle()

    expect(retryer).toHaveBeenCalledExactlyOnceWith(
      7, 31, ['Q5'], expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(configStore.jobId).toBe(32)
    expect(mounted.host.textContent).toContain('上一轮已成功 1 题')
    expect(mounted.host.textContent).toContain('共 3 题')
    expect(mounted.host.textContent).toContain('当前恢复任务')
  })

  it.each(['succeeded', 'failed'] as const)(
    'explains an uncertain model outcome from a %s job and retries only those questions after teacher confirmation',
    async (terminalStatus) => {
    const pending = deferred<JobResponse>()
    const retryer = vi.fn((
      _sessionId: number,
      _jobId: number,
      _questionIds: string[],
      _requestToken: string,
      _confirmUncertainRetry?: boolean,
    ) => {
      void _sessionId
      void _jobId
      void _questionIds
      void _requestToken
      void _confirmUncertainRetry
      return pending.promise
    })
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: terminalStatus, progress: 1, result: {
        outcome: 'partial', total_questions: 12, generated_questions: 11,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        uncertain_question_ids: ['Q10'], needs_teacher_resolution: true,
        retryable: false, question_bank_sync_requested: true,
        question_bank_sync_state: 'waiting_for_config',
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ retryer })

    expect(mounted.host.textContent).toContain('部分题目等待处理')
    expect(mounted.host.textContent).toContain('已完成 11 / 12 道题')
    expect(mounted.host.textContent).toContain('Q10')
    expect(mounted.host.textContent).toContain('可能已经在模型服务端完成')
    expect(mounted.host.textContent).not.toContain('评分标准生成失败')

    const retry = mounted.host.querySelector<HTMLButtonElement>(
      'button[name="确认重新分析结果不确定题"]',
    )!
    retry.click()
    retry.click()
    await nextTick()

    expect(retryer).toHaveBeenCalledExactlyOnceWith(
      7,
      31,
      ['Q10'],
      expect.stringMatching(/^[0-9a-f]{32}$/),
      true,
    )
    expect(retry.disabled).toBe(true)

    pending.resolve(job({ id: 32, status: 'queued', progress: 0 }))
    await settle()
    },
  )

  it('reloads the authoritative editor after complete success and isolates an old session', async () => {
    const editorPending = deferred<ConfigEditorResponse>()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    await mountPanel({ editorLoader: vi.fn(() => editorPending.promise) })
    await nextTick()
    configStore.selectSession(8)
    editorPending.resolve(editor())
    await settle()

    expect(configStore.sessionId).toBe(8)
    expect(configStore.editor).toBeNull()
    expect(useJobStore().jobs[31]?.status).toBe('succeeded')
  })

})
