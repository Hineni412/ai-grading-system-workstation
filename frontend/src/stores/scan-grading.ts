import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import {
  beginScanReplacement,
  cancelGrading,
  cancelScanReplacement,
  clearScans,
  commitScanReplacement,
  controlGrading,
  fetchGradingPlan,
  fetchGradingWorkspace,
  fetchPreflight,
  fetchScanStudentOptions,
  freezeScans,
  removeScan,
  saveScanDecisions,
  startGrading,
  startNewScanBatch,
  startPreflight,
  supplementGrading,
  uploadScan,
  type AutomatedGradingMode,
  type GradingPlan,
  type GradingMode,
  type GradingWorkspace,
  type ScanDecision,
  type ScanPreflight,
  type ScanStudentMatchOption,
} from '../api/scan-grading'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../api/jobs'
import { ApiError, isAmbiguousWriteError } from '../api/errors'
import { useJobStore } from './jobs'

export const useScanGradingStore = defineStore('scan-grading', () => {
  const jobStore = useJobStore()
  const sessionId = ref<number | null>(null)
  const workspace = ref<GradingWorkspace | null>(null)
  const preflight = ref<ScanPreflight | null>(null)
  const students = ref<ScanStudentMatchOption[]>([])
  const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const busyAction = ref('')
  const errorMessage = ref('')
  const activeJobId = ref<number | null>(null)
  const preflightJobId = ref<number | null>(null)
  const selectedMode = ref<GradingMode | null>(null)
  const gradingPlan = ref<GradingPlan | null>(null)
  const planState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const planErrorMessage = ref('')
  let generation = 0
  let planGeneration = 0
  let jobsInitialized = false
  let workspaceRefresh: Promise<void> | null = null
  let workspaceRefreshQueued = false

  const uploadBatch = computed(() => workspace.value?.upload_batch ?? null)
  const replacementBatch = computed(() => workspace.value?.replacement_batch ?? null)
  const gradingRun = computed(() => workspace.value?.grading_run ?? null)
  const preflightJob = computed(() => (
    preflightJobId.value === null ? null : jobStore.jobs[preflightJobId.value] ?? null
  ))
  const gradingJob = computed(() => (
    activeJobId.value === null ? null : jobStore.jobs[activeJobId.value] ?? null
  ))
  const planRevisionMarker = computed(() => [
    sessionId.value ?? '',
    uploadBatch.value?.batch_id ?? '',
    uploadBatch.value?.revision ?? -1,
    preflight.value?.revision ?? -1,
  ].join(':'))

  function safeMessage(error: unknown): string {
    if (error instanceof ApiError && error.code === 'scan_decision_revision_conflict') {
      return '匹配结果已在其他页面更新，本次选择尚未保存。已尝试刷新最新结果，请核对后再提交。'
    }
    if (error instanceof ApiError && error.code === 'grading_preflight_stale') {
      return '评分依据或样卷题框已经更新，请重新运行扫描预检后再预览批改计划。'
    }
    if (error instanceof ApiError && error.code === 'scan_replacement_training_snapshot_exists') {
      return '这场考试已经生成了独立训练任务。为避免训练材料失去来源，当前不能清空旧答卷；请先处理对应训练任务。'
    }
    if (error instanceof ApiError && (
      error.code === 'scan_replacement_active_work'
      || error.code === 'scan_analysis_still_active'
      || error.code === 'grading_run_still_active'
    )) {
      return '这场考试仍有预检、批改或报告任务正在运行，请先完成或取消任务后再重新上传。'
    }
    return error instanceof Error && error.message ? error.message : '操作没有完成，请稍后重试。'
  }

  function isCurrent(id: number, expectedGeneration: number): boolean {
    return sessionId.value === id && generation === expectedGeneration
  }

  function invalidatePlan(): void {
    planGeneration += 1
    gradingPlan.value = null
    planState.value = 'idle'
    planErrorMessage.value = ''
  }

  function resetPlanSelection(): void {
    selectedMode.value = null
    invalidatePlan()
  }

  function projectedScanJob(next: GradingWorkspace): JobResponse | null {
    const job = next.scan_analysis_job
    if (!job) return null
    const terminal = TERMINAL_JOB_STATUSES.has(job.status)
    return {
      id: job.id,
      job_type: 'scan_analysis',
      payload: { session_id: next.session_id, scan_batch_id: job.scan_batch_id },
      result: {},
      status: job.status,
      progress: job.progress,
      stage: job.status,
      detail: '',
      error: null,
      cancel_requested: job.cancel_requested,
      created_at: job.updated_at,
      started_at: null,
      updated_at: job.updated_at,
      finished_at: terminal ? job.updated_at : null,
    }
  }

  function projectedGradingJob(next: GradingWorkspace): JobResponse | null {
    const job = next.grading_job
    if (!job) return null
    const terminal = TERMINAL_JOB_STATUSES.has(job.status)
    return {
      id: job.id,
      job_type: 'grading_run',
      payload: { session_id: next.session_id, scan_batch_id: job.scan_batch_id },
      result: {},
      status: job.status,
      progress: job.progress,
      stage: job.status,
      detail: '',
      error: null,
      cancel_requested: job.cancel_requested,
      created_at: job.updated_at,
      started_at: null,
      updated_at: job.updated_at,
      finished_at: terminal ? job.updated_at : null,
    }
  }

  function applyWorkspaceSnapshot(next: GradingWorkspace): void {
    workspace.value = next
    const gradingJob = projectedGradingJob(next)
    if (next.grading_run === null) {
      if (gradingJob) jobStore.track(gradingJob)
      if (gradingJob && !TERMINAL_JOB_STATUSES.has(gradingJob.status)) {
        activeJobId.value = gradingJob.id
      } else {
        activeJobId.value = null
        if (gradingJob && gradingJob.status !== 'succeeded' && !errorMessage.value) {
          errorMessage.value = '批改任务未能建立运行记录，可以重新提交。'
        }
      }
    } else if (next.grading_run.job_id) {
      activeJobId.value = next.grading_run.job_id
      if (next.grading_run.job_status && next.grading_run.updated_at
        && !jobStore.jobs[next.grading_run.job_id]) {
        jobStore.track({
          id: next.grading_run.job_id,
          job_type: 'grading_run',
          payload: { session_id: next.session_id },
          result: {},
          status: next.grading_run.job_status,
          progress: next.grading_run.progress ?? 0,
          stage: next.grading_run.job_status,
          detail: '',
          error: null,
          cancel_requested: next.grading_run.state === 'cancel_requested',
          created_at: next.grading_run.started_at ?? next.grading_run.updated_at,
          started_at: next.grading_run.started_at ?? null,
          updated_at: next.grading_run.updated_at,
          finished_at: TERMINAL_JOB_STATUSES.has(next.grading_run.job_status)
            ? next.grading_run.updated_at : null,
        })
      }
    }
    const scanJob = projectedScanJob(next)
    if (scanJob && !TERMINAL_JOB_STATUSES.has(scanJob.status)) {
      jobStore.track(scanJob)
      preflightJobId.value = scanJob.id
    } else {
      preflightJobId.value = null
    }
  }

  async function refreshWorkspaceSnapshot(): Promise<void> {
    if (workspaceRefresh) {
      workspaceRefreshQueued = true
      return workspaceRefresh
    }
    const id = sessionId.value; const current = generation
    if (!id) return
    const refresh = (async () => {
      try {
        const next = await fetchGradingWorkspace(id)
        if (isCurrent(id, current)) applyWorkspaceSnapshot(next)
      } catch (error) {
        if (isCurrent(id, current)) errorMessage.value = safeMessage(error)
      } finally {
        workspaceRefresh = null
        if (workspaceRefreshQueued) {
          workspaceRefreshQueued = false
          void refreshWorkspaceSnapshot()
        }
      }
    })()
    workspaceRefresh = refresh
    return refresh
  }

  async function load(id: number): Promise<void> {
    const current = ++generation
    sessionId.value = id
    workspace.value = null
    preflight.value = null
    students.value = []
    activeJobId.value = null
    preflightJobId.value = null
    resetPlanSelection()
    workspaceRefreshQueued = false
    busyAction.value = ''
    errorMessage.value = ''
    loadState.value = 'loading'
    try {
      if (!jobsInitialized) {
        jobsInitialized = true
        await jobStore.initialize()
      }
      const [next, studentList] = await Promise.all([
        fetchGradingWorkspace(id),
        fetchScanStudentOptions(id).catch(() => []),
      ])
      if (!isCurrent(id, current)) return
      applyWorkspaceSnapshot(next)
      students.value = studentList
      loadState.value = 'ready'
      if (next.upload_batch.state === 'frozen') {
        try {
          const result = await fetchPreflight(id)
          if (isCurrent(id, current)) preflight.value = result
        } catch {
          // A frozen batch can still be waiting for its first successful preflight.
        }
      }
      if (!isCurrent(id, current)) return
      const jobs = Object.values(jobStore.jobs).sort((left, right) => right.id - left.id)
      const gradingJob = jobs.find((job) => job.job_type === 'grading_run'
        && Number(job.payload.session_id) === id && !['succeeded', 'failed', 'cancelled'].includes(job.status))
      const scanJob = jobs.find((job) => job.job_type === 'scan_analysis'
        && Number(job.payload.session_id) === id && !TERMINAL_JOB_STATUSES.has(job.status))
      activeJobId.value = activeJobId.value ?? gradingJob?.id ?? null
      if (next.scan_analysis_job === undefined) {
        preflightJobId.value = preflightJobId.value ?? scanJob?.id ?? null
      }
    } catch (error) {
      if (!isCurrent(id, current)) return
      loadState.value = 'error'
      errorMessage.value = safeMessage(error)
    }
  }

  async function runAction(
    name: string,
    id: number,
    expectedGeneration: number,
    action: () => Promise<void>,
  ): Promise<void> {
    if (busyAction.value || !isCurrent(id, expectedGeneration)) return
    busyAction.value = name
    errorMessage.value = ''
    try {
      await action()
    } catch (error) {
      if (isCurrent(id, expectedGeneration)) errorMessage.value = safeMessage(error)
    } finally {
      if (isCurrent(id, expectedGeneration)) busyAction.value = ''
    }
  }

  async function addFiles(files: File[]): Promise<void> {
    const id = sessionId.value; const current = generation
    if (!id || busyAction.value) return
    busyAction.value = 'upload'
    errorMessage.value = ''
    let failed = 0
    let replacement = replacementBatch.value !== null
    if (uploadBatch.value?.state === 'frozen' && !replacement) {
      try {
        const batch = await beginScanReplacement(id)
        if (!isCurrent(id, current) || !workspace.value) return
        workspace.value.replacement_batch = batch
        replacement = true
      } catch (error) {
        if (isCurrent(id, current)) errorMessage.value = safeMessage(error)
        busyAction.value = ''
        return
      }
    }
    for (const file of files) {
      if (!isCurrent(id, current)) return
      try { await uploadScan(id, file, replacement) } catch { failed += 1 }
    }
    if (!isCurrent(id, current)) return
    await load(id)
    if (sessionId.value === id && failed > 0) {
      errorMessage.value = `${failed} 个文件未加入队列；其他成功文件已经保留。`
    }
  }

  async function remove(uploadId: string): Promise<void> {
    const id = sessionId.value
    const replacement = replacementBatch.value !== null
    const batch = replacementBatch.value ?? uploadBatch.value
    const current = generation
    if (!id || !batch) return
    await runAction('remove', id, current, async () => {
      const next = await removeScan(id, uploadId, batch.revision, replacement)
      if (isCurrent(id, current) && workspace.value) {
        if (replacement) workspace.value.replacement_batch = next
        else workspace.value.upload_batch = next
      }
    })
  }

  async function clear(): Promise<void> {
    const id = sessionId.value
    const replacement = replacementBatch.value !== null
    const batch = replacementBatch.value ?? uploadBatch.value
    const current = generation
    if (!id || !batch) return
    await runAction('clear', id, current, async () => {
      const next = await clearScans(id, batch.revision, replacement)
      if (isCurrent(id, current) && workspace.value) {
        if (replacement) workspace.value.replacement_batch = next
        else workspace.value.upload_batch = next
      }
    })
  }

  async function analyze(): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value; const current = generation
    if (!id || !batch) return
    await runAction('preflight', id, current, async () => {
      if (batch.state === 'draft') {
        const frozen = await freezeScans(id, batch.revision)
        if (!isCurrent(id, current)) return
        if (workspace.value) workspace.value.upload_batch = frozen
      }
      const job = await startPreflight(id)
      if (!isCurrent(id, current)) return
      jobStore.track(job)
      preflightJobId.value = job.id
    })
    const trackedStatus = preflightJobId.value === null ? null : jobStore.jobs[preflightJobId.value]?.status
    if (trackedStatus === 'succeeded' && isCurrent(id, current)) await refreshPreflight()
  }

  async function refreshPreflight(): Promise<void> {
    const id = sessionId.value; const current = generation
    if (!id) return
    await runAction('refresh-preflight', id, current, async () => {
      const result = await fetchPreflight(id)
      if (isCurrent(id, current)) preflight.value = result
    })
  }

  watch(
    () => preflightJobId.value === null ? null : jobStore.jobs[preflightJobId.value]?.status,
    (status) => {
      if (status === 'succeeded') void refreshPreflight()
      else if ((status === 'failed' || status === 'cancelled') && sessionId.value !== null) {
        preflightJobId.value = null
        errorMessage.value = '预检没有发布新结果；已上传文件和上一次成功结果仍然保留。'
        void refreshWorkspaceSnapshot()
      }
    },
  )

  watch(
    () => {
      const job = activeJobId.value === null ? null : jobStore.jobs[activeJobId.value]
      return job == null
        ? null
        : `${job.id}:${job.status}:${job.progress}:${job.updated_at}`
    },
    (marker) => {
      if (marker !== null) void refreshWorkspaceSnapshot()
    },
  )

  async function saveDecisions(decisions: ScanDecision[]): Promise<boolean> {
    const id = sessionId.value; const check = preflight.value; const current = generation
    if (!id || !check) return false
    let succeeded = false
    await runAction('decisions', id, current, async () => {
      let saved
      try {
        saved = await saveScanDecisions(id, check.revision, decisions)
      } catch (error) {
        if (error instanceof ApiError && error.code === 'scan_decision_revision_conflict') {
          try {
            const latest = await fetchPreflight(id)
            if (isCurrent(id, current)) preflight.value = latest
          } catch { /* Keep the original conflict and the user's unsaved selections. */ }
        }
        throw error
      }
      if (!isCurrent(id, current) || preflight.value !== check) return
      check.revision = saved.revision
      check.decisions = saved.decisions
      check.pending_issue_count = saved.pending_issue_count
      check.summary = saved.summary ?? { ...check.summary, ready_to_grade: saved.ready_to_grade }
      if (saved.absent_students) check.absent_students = saved.absent_students
      if (saved.match_conflicts) check.match_conflicts = saved.match_conflicts
      succeeded = true
    })
    return succeeded
  }

  async function previewPlan(mode: GradingMode): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value; const check = preflight.value
    const current = generation
    if (!id || !batch || !check || busyAction.value) return
    selectedMode.value = mode
    invalidatePlan()
    const currentPlanGeneration = planGeneration
    const expectedMarker = planRevisionMarker.value
    planState.value = 'loading'
    try {
      const result = await fetchGradingPlan(id, mode)
      if (!isCurrent(id, current)
        || planGeneration !== currentPlanGeneration
        || planRevisionMarker.value !== expectedMarker
        || selectedMode.value !== mode) return
      if (result.mode !== mode) throw new Error('计划返回的批改方式与当前选择不一致，请重新预览。')
      gradingPlan.value = result
      planState.value = 'ready'
    } catch (error) {
      if (!isCurrent(id, current)
        || planGeneration !== currentPlanGeneration
        || planRevisionMarker.value !== expectedMarker
        || selectedMode.value !== mode) return
      planState.value = 'error'
      planErrorMessage.value = safeMessage(error)
    }
  }

  watch(planRevisionMarker, (next, previous) => {
    if (next !== previous) invalidatePlan()
  })

  async function begin(mode: AutomatedGradingMode, confirmPending: boolean): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value; const check = preflight.value
    const current = generation
    if (!id || !batch || !check) return
    invalidatePlan()
    await runAction('grading', id, current, async () => {
      const job = await startGrading(id, mode, batch.revision, check.revision, confirmPending)
      if (!isCurrent(id, current)) return
      jobStore.track(job)
      activeJobId.value = job.id
      await load(id)
      if (sessionId.value === id) activeJobId.value = job.id
    })
  }

  async function control(action: 'pause' | 'resume' | 'retry-failed'): Promise<void> {
    const id = sessionId.value; const run = gradingRun.value; const current = generation
    if (!id || !run) return
    await runAction(action, id, current, async () => {
      const result = await controlGrading(id, run.run_id, action)
      if (!isCurrent(id, current)) return
      if ('id' in result) { jobStore.track(result); activeJobId.value = result.id }
      await refreshWorkspaceSnapshot()
    })
  }

  async function cancel(): Promise<void> {
    const id = sessionId.value; const run = gradingRun.value; const current = generation
    if (!id || !run) return
    await runAction('cancel', id, current, async () => {
      const result = await cancelGrading(id, run.run_id, activeJobId.value)
      if (isCurrent(id, current) && workspace.value) workspace.value.grading_run = result
    })
  }

  async function supplement(): Promise<void> {
    const id = sessionId.value; const run = gradingRun.value; const current = generation
    if (!id || !run) return
    await runAction('supplement', id, current, async () => {
      const job = await supplementGrading(id, run.run_id)
      if (!isCurrent(id, current)) return
      jobStore.track(job)
      activeJobId.value = job.id
      await refreshWorkspaceSnapshot()
    })
  }

  async function newBatch(): Promise<void> {
    const id = sessionId.value; const current = generation
    if (!id) return
    await runAction('new-batch', id, current, async () => {
      const batch = await startNewScanBatch(id)
      if (!isCurrent(id, current) || !workspace.value) return
      workspace.value.upload_batch = batch
      workspace.value.grading_run = null
      preflight.value = null
      activeJobId.value = null
      preflightJobId.value = null
    })
  }

  async function cancelReplacement(): Promise<void> {
    const id = sessionId.value; const current = generation
    if (!id || !replacementBatch.value) return
    await runAction('cancel-replacement', id, current, async () => {
      await cancelScanReplacement(id)
      if (isCurrent(id, current) && workspace.value) workspace.value.replacement_batch = null
    })
  }

  async function commitReplacement(): Promise<void> {
    const id = sessionId.value; const candidate = replacementBatch.value; const current = generation
    if (!id || !candidate || !candidate.file_count) return
    await runAction('commit-replacement', id, current, async () => {
      let batch
      try {
        batch = await commitScanReplacement(id, candidate.revision)
      } catch (error) {
        if (!isAmbiguousWriteError(error)) throw error
        const reconciled = await fetchGradingWorkspace(id)
        if (
          reconciled.upload_batch.batch_id !== candidate.batch_id
          || reconciled.replacement_batch
        ) throw error
        applyWorkspaceSnapshot(reconciled)
        batch = reconciled.upload_batch
      }
      if (!isCurrent(id, current) || !workspace.value) return
      workspace.value.upload_batch = batch
      workspace.value.replacement_batch = null
      workspace.value.grading_run = null
      workspace.value.grading_job = null
      preflight.value = null
      activeJobId.value = null
      preflightJobId.value = null
      resetPlanSelection()
      let job: JobResponse
      try {
        job = await startPreflight(id)
      } catch {
        throw new Error('最新答卷已经替换成功，但预检没有启动。旧数据已按确认永久清除；请点击“运行或重新运行预检”。')
      }
      if (!isCurrent(id, current)) return
      jobStore.track(job)
      preflightJobId.value = job.id
    })
  }

  return { sessionId, workspace, preflight, students, loadState, busyAction, errorMessage,
    activeJobId, preflightJobId, selectedMode, gradingPlan, planState, planErrorMessage,
    uploadBatch, replacementBatch, gradingRun, preflightJob, gradingJob,
    load, addFiles, remove, clear, analyze, refreshPreflight,
    saveDecisions, previewPlan, begin, control, cancel, supplement, newBatch,
    cancelReplacement, commitReplacement }
})
