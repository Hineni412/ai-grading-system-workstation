import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  cancelGrading,
  clearScans,
  controlGrading,
  fetchGradingWorkspace,
  fetchPreflight,
  freezeScans,
  removeScan,
  saveScanDecisions,
  startGrading,
  startPreflight,
  uploadScan,
  type GradingMode,
  type GradingWorkspace,
  type ScanDecision,
  type ScanPreflight,
} from '../api/scan-grading'
import { useJobStore } from './jobs'

export const useScanGradingStore = defineStore('scan-grading', () => {
  const sessionId = ref<number | null>(null)
  const workspace = ref<GradingWorkspace | null>(null)
  const preflight = ref<ScanPreflight | null>(null)
  const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const busyAction = ref('')
  const errorMessage = ref('')
  const activeJobId = ref<number | null>(null)
  let generation = 0

  const uploadBatch = computed(() => workspace.value?.upload_batch ?? null)
  const gradingRun = computed(() => workspace.value?.grading_run ?? null)

  function safeMessage(error: unknown): string {
    return error instanceof Error && error.message ? error.message : '操作没有完成，请稍后重试。'
  }

  async function load(id: number): Promise<void> {
    const current = ++generation
    sessionId.value = id
    workspace.value = null
    preflight.value = null
    errorMessage.value = ''
    loadState.value = 'loading'
    try {
      const next = await fetchGradingWorkspace(id)
      if (current !== generation) return
      workspace.value = next
      activeJobId.value = next.grading_run?.job_id ?? null
      loadState.value = 'ready'
      if (next.upload_batch.state === 'frozen') {
        try {
          const result = await fetchPreflight(id)
          if (current === generation) preflight.value = result
        } catch {
          // A frozen upload batch can legitimately still be waiting for its first preflight.
        }
      }
      const jobs = useJobStore().jobs
      const active = Object.values(jobs).find((job) => job.job_type === 'grading_run'
        && Number(job.payload.session_id) === id && !['succeeded', 'failed', 'cancelled'].includes(job.status))
      activeJobId.value = activeJobId.value ?? active?.id ?? null
    } catch (error) {
      if (current !== generation) return
      loadState.value = 'error'
      errorMessage.value = safeMessage(error)
    }
  }

  async function runAction(name: string, action: () => Promise<void>): Promise<void> {
    if (busyAction.value) return
    busyAction.value = name
    errorMessage.value = ''
    try { await action() } catch (error) { errorMessage.value = safeMessage(error) } finally { busyAction.value = '' }
  }

  async function addFiles(files: File[]): Promise<void> {
    const id = sessionId.value
    if (!id) return
    await runAction('upload', async () => {
      for (const file of files) await uploadScan(id, file)
      await load(id)
    })
  }

  async function remove(uploadId: string): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value
    if (!id || !batch) return
    await runAction('remove', async () => {
      const next = await removeScan(id, uploadId, batch.revision)
      if (workspace.value) workspace.value.upload_batch = next
    })
  }

  async function clear(): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value
    if (!id || !batch) return
    await runAction('clear', async () => {
      const next = await clearScans(id, batch.revision)
      if (workspace.value) workspace.value.upload_batch = next
    })
  }

  async function analyze(): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value
    if (!id || !batch) return
    await runAction('preflight', async () => {
      if (batch.state === 'draft') {
        const frozen = await freezeScans(id, batch.revision)
        if (workspace.value) workspace.value.upload_batch = frozen
      }
      const job = await startPreflight(id)
      useJobStore().track(job)
      activeJobId.value = job.id
    })
  }

  async function refreshPreflight(): Promise<void> {
    const id = sessionId.value
    if (!id) return
    await runAction('refresh-preflight', async () => { preflight.value = await fetchPreflight(id) })
  }

  async function saveDecisions(decisions: ScanDecision[]): Promise<void> {
    const id = sessionId.value; const current = preflight.value
    if (!id || !current) return
    await runAction('decisions', async () => {
      const saved = await saveScanDecisions(id, current.revision, decisions)
      current.revision = saved.revision
      current.decisions = saved.decisions
      current.pending_issue_count = saved.pending_issue_count
    })
  }

  async function begin(mode: GradingMode, confirmPending: boolean): Promise<void> {
    const id = sessionId.value; const batch = uploadBatch.value; const check = preflight.value
    if (!id || !batch || !check) return
    await runAction('grading', async () => {
      const job = await startGrading(id, mode, batch.revision, check.revision, confirmPending)
      useJobStore().track(job)
      activeJobId.value = job.id
      await load(id)
      activeJobId.value = job.id
    })
  }

  async function control(action: 'pause' | 'resume' | 'retry-failed'): Promise<void> {
    const id = sessionId.value; const run = gradingRun.value
    if (!id || !run) return
    await runAction(action, async () => {
      const result = await controlGrading(id, run.run_id, action)
      if ('id' in result) { useJobStore().track(result); activeJobId.value = result.id }
      await load(id)
    })
  }

  async function cancel(): Promise<void> {
    const id = sessionId.value; const run = gradingRun.value; const jobId = activeJobId.value
    if (!id || !run || !jobId) throw new Error('请先刷新当前批改任务，再取消。')
    await runAction('cancel', async () => {
      const result = await cancelGrading(id, run.run_id, jobId)
      if (workspace.value) workspace.value.grading_run = result
    })
  }

  return { sessionId, workspace, preflight, loadState, busyAction, errorMessage, activeJobId,
    uploadBatch, gradingRun, load, addFiles, remove, clear, analyze, refreshPreflight,
    saveDecisions, begin, control, cancel }
})
