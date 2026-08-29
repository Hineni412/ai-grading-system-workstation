import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConfigEditorResponse,
  ConfigGenerationRequest,
  ConfigSource,
} from '../../../api/config-workspace'
import type { JobResponse } from '../../../api/jobs'
import { ApiError } from '../../../api/errors'
import type { CurriculumCatalog } from '../../../api/question-bank'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import { useJobStore } from '../../../stores/jobs'
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

function twoVolumeCurriculum(): CurriculumCatalog {
  const base = curriculum()
  return {
    ...base,
    volumes: [
      ...base.volumes,
      {
        id: 'bnu24-math-g8-upper',
        order: 2,
        label: '八年级数学上册',
        grade: '八年级',
        semester: '上册',
        textbook_version: '北师大版',
        source: { provider: '组卷网' },
        statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
        chapters: [],
      },
    ],
  }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
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
  it('does not duplicate the question-bank tagging entry in generation', async () => {
    const mounted = await mountPanel()
    const option = mounted.host.querySelector<HTMLInputElement>(
      '.config-generation__bank-sync input[type="checkbox"]',
    )

    expect(option).toBeNull()
    expect(mounted.host.textContent).not.toContain('评分标准生成后，将这份试卷入库并打标签')
    expect(mounted.host.textContent).toContain('选择教材册别')
    expect(mounted.host.textContent).not.toContain('确认教材范围')
  })

  it('automatically continues into question-bank analysis after generation', async () => {
    const submitter = vi.fn(async (
      _sessionId: number,
      _request: ConfigGenerationRequest,
    ) => {
      void _sessionId
      void _request
      return job({ status: 'queued', progress: 0 })
    })
    const mounted = await mountPanel({ submitter })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(submitter.mock.calls[0]?.[1]).toMatchObject({
      sync_to_question_bank: true,
      curriculum_volume_id: 'bnu24-math-g7-upper',
    })
  })

  it('offers one evidence-first flow and submits a write only once while disabled', async () => {
    const pending = deferred<JobResponse>()
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return pending.promise
    })
    const mounted = await mountPanel({ submitter })

    expect(mounted.host.textContent).toContain('先分析并完整入库，再为本场赋分')
    expect(mounted.host.textContent).toContain('系统会按题分析标签和详细判定点并写入题库')
    expect(mounted.host.textContent).toContain('只有全部题目入库成功后，才会给本场考试挂分')
    expect(mounted.host.textContent).toContain('入库失败时会留下失败类别和题号')
    expect(mounted.host.querySelectorAll('input[type="radio"]')).toHaveLength(0)
    expect(mounted.host.textContent).not.toContain('整卷生成评分标准')
    const submit = mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!
    submit.click()
    submit.click()
    await nextTick()

    expect(submitter).toHaveBeenCalledOnce()
    expect(submitter.mock.calls[0]?.[1]).toMatchObject({ generation_mode: 'batched' })
    expect(submit.disabled).toBe(true)
    pending.resolve(job({ payload: { ...job().payload, generation_mode: 'batched' } }))
    await settle()
    expect(useConfigWorkspaceStore().jobId).toBe(31)
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

  it('unlocks generation when the authoritative token lookup confirms 404', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const requestAbandoner = vi.fn(async () => undefined)
    const mounted = await mountPanel({
      submitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner,
    })

    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(requestAbandoner).toHaveBeenCalledWith(7, expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(useConfigWorkspaceStore().pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')?.disabled).toBe(false)
  })

  it('keeps generation locked when the missing token cannot be atomically abandoned', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const conflict = new ApiError({ kind: 'conflict', status: 409,
      code: 'config_request_token_conflict', message: 'late request', details: {},
      requestId: 'safe-409', retryable: false })
    const mounted = await mountPanel({
      submitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner: vi.fn(async () => { throw conflict }),
    })

    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingJobRequestToken).not.toBeNull()
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')?.disabled).toBe(true)
  })

  it('reconciles only the exact pending token restored after a refresh', async () => {
    const token = '3'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.markJobSubmissionPending(token, 'refine')
    const generationLoader = vi.fn(async () => job({ id: 45, status: 'queued', progress: 0 }))
    const mounted = await mountPanel({ generationLoader })

    mounted.host.querySelector<HTMLButtonElement>('button[name="重新核对生成任务"]')!.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledExactlyOnceWith(7, token)
    expect(configStore.jobId).toBe(45)
    expect(configStore.pendingJobRequestToken).toBeNull()
  })

  it('shows and reconciles a refine recovery without requiring a P2 source', async () => {
    const token = '4'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.selectSource(null)
    configStore.setEditor(editor())
    configStore.markJobSubmissionPending(token, 'refine')
    const generationLoader = vi.fn(async () => job({
      id: 46,
      status: 'queued',
      progress: 0,
      payload: { session_id: 7, mode: 'refine' },
    }))
    const mounted = await mountPanel({ generationLoader })

    expect(configStore.canGenerate).toBe(false)
    const reconcile = mounted.host.querySelector<HTMLButtonElement>(
      'button[name="重新核对生成任务"]',
    )!
    expect(reconcile).not.toBeNull()
    reconcile.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledExactlyOnceWith(7, token)
    expect(configStore.jobId).toBe(46)
    expect(configStore.pendingJobRequestToken).toBeNull()
  })

  it('unlocks a restored refine request after an authoritative 404', async () => {
    const token = '6'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.selectSource(null)
    configStore.setEditor(editor())
    configStore.markJobSubmissionPending(token, 'refine')
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const mounted = await mountPanel({
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner: vi.fn(async () => undefined),
    })

    mounted.host.querySelector<HTMLButtonElement>('button[name="重新核对生成任务"]')!.click()
    await settle()

    expect(configStore.pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
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
    expect(mounted.host.querySelector('button[name="重新进行 AI 统一配分"]')).toBeNull()
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

  it.each(['failed', 'cancelled'] as const)(
    'keeps the failed-batch retry entry for a %s checkpointed job',
    async (status) => {
      const retryer = vi.fn(async () => job({ id: 33, status: 'queued', progress: 0 }))
      const configStore = useConfigWorkspaceStore()
      useJobStore().track(job({
        status, progress: 1, result: {
          outcome: 'partial', total_questions: 3, generated_questions: 2,
          failed_count: 1, failed_question_ids: ['Q3'],
          failed_batches: [{ batch_id: 'B001', question_ids: ['Q3'] }],
        }, finished_at: '2026-07-15T00:01:00Z',
      }))
      configStore.attachJob(31, configStore.captureGenerationContext())
      const mounted = await mountPanel({ retryer })

      expect(mounted.host.querySelector('button[name="重新分批生成"]')).toBeNull()
      mounted.host.querySelector<HTMLInputElement>('[aria-label="选择失败批次 B001"]')!.click()
      await nextTick()
      mounted.host.querySelector<HTMLButtonElement>('button[name="重试所选批次"]')!.click()
      await settle()

      expect(retryer).toHaveBeenCalledWith(
        7, 31, ['Q3'], expect.stringMatching(/^[0-9a-f]{32}$/),
      )
    },
  )

  it.each(['failed', 'cancelled'] as const)(
    'resumes AI score allocation without question retries for a %s complete checkpoint',
    async (status) => {
      const retryer = vi.fn(async () => job({ id: 34, status: 'queued', progress: 0 }))
      const configStore = useConfigWorkspaceStore()
      useJobStore().track(job({
        status, progress: 1, result: {
          outcome: 'complete', total_questions: 3, generated_questions: 3,
          failed_count: 0, failed_question_ids: [], failed_batches: [],
        }, finished_at: '2026-07-15T00:01:00Z',
      }))
      configStore.attachJob(31, configStore.captureGenerationContext())
      const mounted = await mountPanel({ retryer })

      expect(mounted.host.textContent).toContain('将调用模型一次')
      mounted.host.querySelector<HTMLButtonElement>('button[name="继续 AI 统一配分"]')!.click()
      await settle()

      expect(retryer).toHaveBeenCalledWith(
        7, 31, [], expect.stringMatching(/^[0-9a-f]{32}$/),
      )
    },
  )

  it('resumes only AI score allocation after all batches were retained', async () => {
    const retryer = vi.fn(async () => job({ id: 35, status: 'queued', progress: 0 }))
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 12, generated_questions: 12,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        score_allocation_pending: true, score_allocation_failed: true,
        retryable: true,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ retryer })

    expect(mounted.host.textContent).toMatch(/12 道题\s+的生成结果已经保存在本机/)
    expect(mounted.host.textContent).toContain('没有使用本地分数替代')
    expect(mounted.host.querySelectorAll('input[type="checkbox"]')).toHaveLength(0)
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新进行 AI 统一配分"]')!.click()
    await settle()

    expect(retryer).toHaveBeenCalledWith(
      7, 31, [], expect.stringMatching(/^[0-9a-f]{32}$/),
    )
  })

  it('shows batches repaired locally without exposing the model response', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        local_json_repairs: [
          { batch_id: 'B001', question_ids: ['Q1', 'Q2', 'Q3'], operations: ['remove_trailing_comma'] },
        ],
        local_structure_repairs: [
          { batch_id: 'B001', question_ids: ['Q1', 'Q2', 'Q3'], operations: ['part_id P1 -> Q1'] },
        ],
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('本地程序已修复 1 个批次的 JSON（B001）')
    expect(mounted.host.textContent).toContain('本地程序已统一 1 个批次的题号、小问号或步骤号')
    expect(mounted.host.textContent).toContain('未产生额外模型请求')
  })

  it('keeps scoring success when taxonomy labels need later review', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        taxonomy_review_count: 2,
        taxonomy_review_question_ids: ['Q1', 'Q2'],
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })

    expect(mounted.host.textContent).toContain('评分标准生成成功')
    expect(mounted.host.textContent).toContain('2 道题的知识标签需要稍后重试或人工归并')
    expect(mounted.host.textContent).toContain('未知词尚未写入正式标签')
    expect(mounted.host.textContent).not.toContain('失败 2 道题')
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

  it('reports that a fully analysed paper entered the question bank before score allocation succeeds', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 12, generated_questions: 12,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        retryable: true, score_allocation_pending: true,
        question_bank_sync_requested: true,
        question_bank_sync_state: 'ready_for_config_link',
        question_bank_imported_count: 12,
        question_bank_tagged_count: 12,
      }, finished_at: '2026-08-02T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('试卷已先收入题库')
    expect(mounted.host.textContent).toContain('共入库 12 题、已有完整标签 12 题')
    expect(mounted.host.textContent).toContain('不会重复建卷或重复打标签')
  })

  it('shows imported and tagged counts separately when only some questions analysed successfully', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 12, generated_questions: 9,
        failed_count: 3, failed_question_ids: ['Q10', 'Q11', 'Q12'],
        failed_batches: [], retryable: true,
        question_bank_sync_requested: true,
        question_bank_sync_state: 'partial',
        question_bank_imported_count: 12,
        question_bank_tagged_count: 9,
      }, finished_at: '2026-08-02T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('题库入库未完整成功，当前不能赋分')
    expect(mounted.host.textContent).toContain('当前入库 12 题、已有完整标签 9 题')
    expect(mounted.host.textContent).toContain('只可继续处理缺失项')
  })

  it('explains a recoverable question-bank intake failure without claiming reanalysis', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 12, generated_questions: 12,
        failed_count: 0, failed_question_ids: [], failed_batches: [],
        retryable: true, score_allocation_pending: true,
        question_bank_sync_requested: true,
        question_bank_sync_state: 'intake_failed',
      }, finished_at: '2026-08-02T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('试卷暂时未能写入题库')
    expect(mounted.host.textContent).toContain('不会重新分析已完成题目')
    expect(mounted.host.textContent).not.toContain('标签已保存在本机')
  })

  it('unlocks an ambiguous retry when its exact lookup confirms 404', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 5, generated_questions: 3,
        failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
        failed_batches: [
          { batch_id: 'B001', question_ids: ['Q2'] },
          { batch_id: 'B002', question_ids: ['Q5'] },
        ],
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({
      retryer: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner: vi.fn(async () => undefined),
    })
    const failedInputs = mounted.host.querySelectorAll<HTMLInputElement>(
      '.config-generation__partial input[type="checkbox"]',
    )
    failedInputs[1]!.click()
    await nextTick()

    mounted.host.querySelector<HTMLButtonElement>('.config-generation__partial button')!.click()
    await settle()

    expect(configStore.pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
    expect(mounted.host.querySelector<HTMLButtonElement>('.config-generation__partial button')?.disabled).toBe(false)
  })

  it('offers a whole-document restart after a legacy whole mode failure', async () => {
    const submitter = vi.fn(async (_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return job({ id: 32, status: 'queued', progress: 0 })
    })
    const retryer = vi.fn()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'failed', payload: { ...job().payload, generation_mode: 'whole_document' },
      detail: '应用重启后，本次生成已停止。', error: 'private failure',
      finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ submitter, retryer })

    expect(mounted.host.textContent).toContain('评分标准生成失败')
    expect(mounted.host.textContent).not.toContain('private failure')
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新生成"]')!.click()
    await settle()
    expect(submitter.mock.calls[0]?.[1]).toMatchObject({ generation_mode: 'whole_document' })
    expect(retryer).not.toHaveBeenCalled()
  })

  it('does not expose private failure details when restarting a truncated job', async () => {
    const submitter = vi.fn(async (_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return job({ id: 32, status: 'queued', progress: 0 })
    })
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'failed', payload: { ...job().payload, generation_mode: 'whole_document' },
      error: '模型因输出长度上限停止，返回结果不完整（响应摘要: private-hash）。',
      finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ submitter })

    expect(mounted.host.textContent).toContain('评分标准生成失败')
    expect(mounted.host.textContent).not.toContain('private-hash')
    expect(mounted.host.querySelector('button[name="重新生成"]')?.textContent)
      .toContain('重新整卷生成')
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新生成"]')!.click()
    await settle()

    expect(submitter).toHaveBeenCalledOnce()
    expect(submitter.mock.calls[0]?.[1]).toMatchObject({ generation_mode: 'whole_document' })
  })

  it('keeps refine failures in the editor workflow without offering generation retry', async () => {
    const submitter = vi.fn()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ status: 'failed', payload: { session_id: 7, mode: 'refine' },
      finished_at: '2026-07-15T00:01:00Z' }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ submitter })

    expect(mounted.host.textContent).toContain('重新细化')
    expect(mounted.host.textContent).toContain('返回编辑器')
    expect(mounted.host.textContent).not.toContain('重新逐题生成')
    mounted.host.querySelector<HTMLButtonElement>('button[name="返回编辑器"]')!.click()
    await settle()
    expect(submitter).not.toHaveBeenCalled()
    expect(configStore.jobId).toBeNull()
  })

  it('does not relabel cancel-requested as cancelled before the terminal state', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ cancel_requested: true }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('正在等待已经发出的模型请求返回')
    expect(mounted.host.textContent).not.toContain('已取消')
  })

  it.each([
    ['failed', {}, '生成失败'],
    ['succeeded', { outcome: 'complete' }, '评分标准生成成功'],
    ['succeeded', {
      outcome: 'partial', total_questions: 5, generated_questions: 3,
      failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
    }, '评分标准生成失败'],
  ] as const)('uses terminal %s status even when cancel_requested remains true', async (
    status, result, expected,
  ) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status, result, cancel_requested: true, progress: 1,
      finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })

    expect(mounted.host.textContent).toContain(expected)
    expect(mounted.host.textContent).not.toContain('正在等待当前模型请求返回')
  })

  it('keeps the last Job visible on sync error and offers explicit refresh', async () => {
    const configStore = useConfigWorkspaceStore()
    const jobStore = useJobStore()
    jobStore.track(job())
    configStore.attachJob(31, configStore.captureGenerationContext())
    jobStore.syncErrors[31] = {
      kind: 'network', message: '任务状态暂时无法更新', retryable: true, requestId: 'safe-id',
    }
    const refresh = vi.spyOn(jobStore, 'refresh').mockResolvedValue()
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('已保留上次进度')
    expect(mounted.host.querySelector<HTMLProgressElement>('progress')?.value).toBe(0.4)
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新同步"]')!.click()
    await settle()
    expect(refresh).toHaveBeenCalledWith(31)
  })

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

  it('replaces the workspace with the authoritative editor after complete success', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    await settle()

    expect(configStore.editor).toEqual(editor())
    expect(configStore.phase).toBe('editor')
  })

  it.each([
    ['refreshed', '样卷映射已刷新'],
    ['reconfirm_required', '样卷映射需要回到旧入口重新确认'],
    ['not_present', '当前考试没有样卷映射'],
  ] as const)('shows the %s mapping outcome after complete generation', async (mappingStatus, copy) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
        mapping_status: mappingStatus,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    await settle()

    expect(mounted.host.textContent).toContain(copy)
  })

  it('blocks a session change until an in-flight submission is attached', async () => {
    const pending = deferred<JobResponse>()
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return pending.promise
    })
    const configStore = useConfigWorkspaceStore()
    const mounted = await mountPanel({ submitter })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await nextTick()
    expect(configStore.selectSession(8)).toBe(false)
    pending.resolve(job())
    await settle()

    expect(configStore.sessionId).toBe(7)
    expect(configStore.jobId).toBe(31)
    expect(useJobStore().jobs[31]).toEqual(job())
  })

  it.each([
    ['queued', '等待开始'],
    ['running', '正在生成'],
    ['paused', '生成已暂停'],
    ['succeeded', '评分标准生成成功'],
    ['failed', '生成失败'],
    ['cancelled', '已取消'],
  ] as const)('shows the %s terminal state truthfully', async (status, copy) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ status, finished_at: '2026-07-15T00:01:00Z' }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    expect(mounted.host.textContent).toContain(copy)
  })

  it('follows the global teaching semester until the teacher picks another volume', async () => {
    const scope = useCurriculumScopeStore()
    const mounted = await mountPanel({ curriculumLoader: async () => twoVolumeCurriculum() })
    const select = mounted.host.querySelector<HTMLSelectElement>(
      '.config-generation__console-volume select',
    )!

    expect(select.value).toBe('bnu24-math-g7-upper')

    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    await settle()
    expect(select.value).toBe('bnu24-math-g8-upper')

    scope.selectedVolumeId = null
    await settle()
    expect(select.value).toBe('')

    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    await settle()
    select.value = 'bnu24-math-g7-upper'
    select.dispatchEvent(new Event('change'))
    await settle()
    scope.selectedVolumeId = 'bnu24-math-g7-lower-missing'
    await settle()
    expect(select.value).toBe('bnu24-math-g7-upper')
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    await settle()
    expect(select.value).toBe('bnu24-math-g7-upper')
    mounted.unmount()
  })

  it('prefers the global teaching semester even when the exam name infers another volume', async () => {
    const scope = useCurriculumScopeStore()
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    const mounted = await mountPanel({
      sessionName: '七年级数学上册测试',
      curriculumLoader: async () => twoVolumeCurriculum(),
    })
    const select = mounted.host.querySelector<HTMLSelectElement>(
      '.config-generation__console-volume select',
    )!

    // 考试名能推断出七年级上册，但顶部全局学期优先
    expect(select.value).toBe('bnu24-math-g8-upper')
    mounted.unmount()
  })

  it('falls back to the global teaching semester when the exam name matches no volume', async () => {
    const scope = useCurriculumScopeStore()
    scope.selectedVolumeId = 'bnu24-math-g8-upper'
    const mounted = await mountPanel({
      sessionName: '0526test',
      curriculumLoader: async () => twoVolumeCurriculum(),
    })
    const select = mounted.host.querySelector<HTMLSelectElement>(
      '.config-generation__console-volume select',
    )!

    expect(select.value).toBe('bnu24-math-g8-upper')

    // 换一个也推断不出册别的考试名，仍跟随全局学期而不是清空
    await mounted.unmount()
    const remounted = await mountPanel({
      sessionName: '另一场推断不出的考试',
      curriculumLoader: async () => twoVolumeCurriculum(),
    })
    const reselect = remounted.host.querySelector<HTMLSelectElement>(
      '.config-generation__console-volume select',
    )!
    expect(reselect.value).toBe('bnu24-math-g8-upper')
    remounted.unmount()
  })
})
