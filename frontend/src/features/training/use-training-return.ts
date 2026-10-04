import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { trainingApi, type PersonalizedPaperInstance, type TrainingAssessmentOutcome, type TrainingAssessmentPoint, type TrainingAssessmentQuestion, type TrainingPointState, type TrainingScanBatch, type TrainingScanBatchSummary, type TrainingScanPage, type TrainingSubmission } from '../../api/training'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { buildQueue, initialSelection, nextPending, pointKey, requestToken, reviewBody, submissionKey, type InitialFocus, type ReturnRecord } from './training-return'

export function safeError(error: unknown): string {
  if (error instanceof ApiError && error.code === 'training_submission_revision_conflict') return '归卷状态已在另一处变化，请刷新该批次后再操作。'
  if (error instanceof ApiError && ['training_assessment_review_conflict', 'training_assessment_revision_conflict'].includes(error.code)) return '判定内容已在另一处变化，请刷新后再操作。'
  return '本次操作未完成，已有判定和证据保持不变，请按页面提示重试。'
}
function freshRecord(): ReturnRecord {
  return { assessment: null, feedback: null, loading: true, readError: false, waiting: false, queryPaused: false, busy: '', error: '', message: '', onlyPending: false, page: 1, view: 'points' }
}
export function useTrainingReturn(instances: Ref<PersonalizedPaperInstance[]>, focus: Ref<InitialFocus | undefined>) {
  const batch = ref<TrainingScanBatch | null>(null)
  const batches = ref<TrainingScanBatchSummary[]>([])
  const records = ref<Record<string, ReturnRecord>>({})
  const notes = ref<Record<string, string>>({})
  const selectedId = ref('')
  const busy = ref(false)
  const historyLoaded = ref(false)
  const message = ref('')
  const errorMessage = ref('')
  const bulk = ref<{ kind: 'assess' | 'publish'; current: number; total: number; stop: boolean } | null>(null)
  const rows = computed(() => buildQueue(batch.value, records.value))
  const selected = computed(() => rows.value.find(r => r.id === selectedId.value))
  const paperBatchIds = computed(() => [...new Set(instances.value.map(p => p.paper_batch_id))].sort())
  let version = 0
  let active = true
  let selectedInitially = false
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const failures = new Map<string, number>()
  let reading = 0
  const waiters: Array<() => void> = []
  async function read<T>(fn: () => Promise<T>): Promise<T> {
    if (reading >= 4) await new Promise<void>(resolve => waiters.push(resolve))
    else reading++
    try { return await fn() } finally {
      const next = waiters.shift()
      if (next) next()
      else reading--
    }
  }
  const valid = (v: number) => active && v === version
  function invalidate() {
    version++; selectedInitially = false
    if (bulk.value) bulk.value.stop = true
    bulk.value = null
    timers.forEach(clearTimeout); timers.clear(); failures.clear()
  }
  function newBatch() {
    invalidate(); batch.value = null; selectedId.value = ''; records.value = {}; notes.value = {}; message.value = ''; errorMessage.value = ''
  }
  function next() { selectedId.value = nextPending(rows.value, selectedId.value) || selectedId.value }
  function selectInitial() {
    if (!selectedInitially && batch.value && !Object.values(records.value).some(r => r.loading)) {
      selectedId.value = initialSelection(rows.value, focus.value); selectedInitially = true
    } else if (selectedInitially && !rows.value.some(r => r.id === selectedId.value)) selectedId.value = initialSelection(rows.value)
  }
  watch(rows, selectInitial)
  async function loadFeedback(s: TrainingSubmission, v: number) {
    const key = submissionKey(s)
    try {
      const f = await read(() => trainingApi.getTrainingFeedback(s.submission_id, s.revision))
      if (!valid(v)) return
      const r = records.value[key]!
      r.feedback = f
      if (f.status !== 'withdrawn' && f.source_review_revision === r.assessment?.review_revision) r.view = 'feedback'
    } catch (error) {
      if (!valid(v)) return
      if (error instanceof ApiError && error.status === 404) records.value[key]!.feedback = null
      else {
        records.value[key]!.readError = true; records.value[key]!.error = safeError(error)
      }
    }
  }
  function follow(s: TrainingSubmission, v: number) {
    if (!valid(v)) return
    const key = submissionKey(s), r = records.value[key]!
    clearTimeout(timers.get(key)); timers.delete(key)
    if ((r.waiting || r.assessment?.status === 'running') && !r.queryPaused) {
      timers.set(key, setTimeout(() => { void queryResult(s, v) }, 3000))
    }
  }
  function accept(s: TrainingSubmission, a: TrainingAssessmentOutcome, v: number) {
    if (!valid(v)) return
    const r = records.value[submissionKey(s)]!
    r.assessment = a; r.readError = false; r.waiting = a.status === 'running'; r.loading = false; r.error = ''; r.message = a.action_message || ''
    follow(s, v)
  }
  async function queryResult(s: TrainingSubmission, v = version): Promise<void> {
    if (!valid(v)) return
    const key = submissionKey(s), r = records.value[key]!
    r.queryPaused = false
    try {
      const a = await read(() => trainingApi.getTrainingAssessment(s.submission_id, s.revision))
      if (!valid(v)) return
      failures.set(key, 0); accept(s, a, v)
      if (a.status !== 'running') { r.loading = true; await loadFeedback(s, v); if (valid(v)) r.loading = false }
    } catch {
      if (!valid(v)) return
      const n = (failures.get(key) ?? 0) + 1; failures.set(key, n)
      if (n >= 3) { r.queryPaused = true; r.error = '暂时无法确认后台结果，请重新查询；不会自动追加模型请求。' }
      else follow(s, v)
    }
  }
  async function loadSubmission(s: TrainingSubmission, v = version) {
    const key = submissionKey(s)
    if (!valid(v)) return
    const r = records.value[key] ?? (records.value[key] = freshRecord())
    if (s.status !== 'ready') { r.loading = false; return }
    r.loading = true; r.readError = false; r.error = ''
    try {
      const a = await read(() => trainingApi.getTrainingAssessment(s.submission_id, s.revision))
      if (!valid(v)) return
      accept(s, a, v)
      if (a.status !== 'running') { r.loading = true; await loadFeedback(s, v) }
    } catch (error) {
      if (!valid(v)) return
      if (error instanceof ApiError && error.status === 404 && error.code === 'training_assessment_not_found') r.assessment = null
      else { r.readError = true; r.error = safeError(error) }
    } finally { if (valid(v)) r.loading = false }
  }
  async function acceptBatch(value: TrainingScanBatch, v: number) {
    if (!valid(v)) return
    const keys = new Set(value.submissions.map(submissionKey))
    for (const [key, timer] of timers) if (!keys.has(key)) { clearTimeout(timer); timers.delete(key) }
    batch.value = value
    for (const s of value.submissions) records.value[submissionKey(s)] ??= freshRecord()
    await Promise.all(value.submissions.map(s => loadSubmission(s, v)))
    if (valid(v)) selectInitial()
  }
  async function restoreBatches() {
    newBatch(); const v = version
    historyLoaded.value = false; batches.value = []; busy.value = true
    try {
      const groups = await Promise.all(paperBatchIds.value.map(id => trainingApi.listTrainingScanBatches(id)))
      if (!valid(v)) return
      batches.value = groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at))
      const latest = batches.value[0]
      if (latest) await acceptBatch(await trainingApi.getTrainingScanBatch(latest.batch_id), v)
      if (valid(v)) historyLoaded.value = true
    } catch { if (valid(v)) errorMessage.value = '扫描批次读取未完成，请重新读取后继续。' }
    finally { if (valid(v)) busy.value = false }
  }
  async function openBatch(id: string) {
    if (busy.value) return
    newBatch(); if (!id) return
    const v = version; busy.value = true
    try { await acceptBatch(await trainingApi.getTrainingScanBatch(id), v) }
    catch { if (valid(v)) errorMessage.value = '扫描批次读取未完成，请重试。' }
    finally { if (valid(v)) busy.value = false }
  }
  async function importFiles(files: File[], ids?: string[]) {
    if (busy.value || !files.length) return
    const v = version; busy.value = true; errorMessage.value = ''; message.value = ''
    try {
      if (!batch.value && ids?.length) {
        const created = await trainingApi.createTrainingScanBatch(ids, requestToken())
        if (!valid(v)) return
        batch.value = created
        batches.value.unshift({ ...created, submission_count: created.submissions.length })
      }
      for (const file of files) {
        if (!valid(v) || !batch.value) return
        const updated = await trainingApi.uploadTrainingScan(batch.value, file, requestToken())
        if (!valid(v)) return
        batch.value = updated
      }
      if (batch.value) await acceptBatch(batch.value, v)
      if (valid(v)) message.value = batch.value?.status === 'ready' ? '全部页面已按身份归组，可以进入后续训练判定。' : '扫描件已归组；异常页和缺页需要人工处理后才能进入判定。'
    } catch {
      if (valid(v)) { errorMessage.value = '扫描件处理未完成，已有归组结果保持不变，请检查文件后重试。'; if (batch.value) await acceptBatch(batch.value, v) }
    } finally { if (valid(v)) busy.value = false }
  }
  async function resolve(page: TrainingScanPage, action: 'match' | 'replace' | 'dismiss', paperId: string, pageNumber: number) {
    if (!batch.value || busy.value) return
    if (action !== 'dismiss' && (!paperId || !Number.isInteger(pageNumber) || pageNumber < 1)) {
      errorMessage.value = '请先选择准确的学生训练卷和页码。'; return
    }
    const v = version; busy.value = true; errorMessage.value = ''
    try {
      const updated = await trainingApi.resolveTrainingScanPage(batch.value, page, {
        operation_token: requestToken(), action,
        paper_instance_id: action === 'dismiss' ? undefined : paperId,
        page_number: action === 'dismiss' ? undefined : pageNumber,
      })
      await acceptBatch(updated, v)
      if (valid(v)) message.value = action === 'replace' ? '已只替换明确选择的页，旧页仍保留在历史中。' : action === 'dismiss' ? '该扫描页已忽略，不再阻塞本批次。' : '该扫描页已人工匹配并留下审计记录。'
    } catch (error) { if (valid(v)) errorMessage.value = safeError(error) }
    finally { if (valid(v)) busy.value = false }
  }
  async function cancelSubmission(s: TrainingSubmission) {
    if (!batch.value || busy.value) return
    const v = version; busy.value = true
    try {
      await acceptBatch(await trainingApi.cancelTrainingSubmission(batch.value, s.submission_id, requestToken()), v)
      if (valid(v)) message.value = '已取消这一个学生的本次提交，其他训练卷不受影响。'
    } catch (error) { if (valid(v)) errorMessage.value = safeError(error) }
    finally { if (valid(v)) busy.value = false }
  }
  async function lockPoint(s: TrainingSubmission, q: TrainingAssessmentQuestion, p: TrainingAssessmentPoint, final: TrainingPointState) {
    const r = records.value[submissionKey(s)]
    if (!r?.assessment || r.busy || r.waiting || r.loading || r.assessment.status === 'running' || (p.state === final && !(!p.teacher_locked && ['uncertain', 'unreadable'].includes(final)))) return
    const v = version, key = pointKey(s, q, p)
    const body = reviewBody(q, p, final, notes.value[key] ?? '', requestToken())
    r.busy = key; r.error = ''
    try {
      const a = await trainingApi.reviewTrainingPoint(r.assessment, body)
      if (!valid(v)) return
      accept(s, a, v); delete notes.value[key]
      r.message = `已锁定：${body.teacher_reason}`
      r.view = 'points'
    } catch (error) {
      if (!valid(v)) return
      if (error instanceof ApiError && ['training_assessment_review_conflict', 'training_assessment_revision_conflict'].includes(error.code)) {
        await loadSubmission(s, v)
        if (valid(v)) r.error = '判定内容已在另一处变化，已刷新，请重新确认这一点'
      } else r.error = safeError(error)
    } finally { if (valid(v)) r.busy = '' }
  }
  async function assess(s: TrainingSubmission, action: 'assess' | 'retry' | 'recover' = 'assess') {
    const r = records.value[submissionKey(s)]
    if (!r || r.busy || (action !== 'recover' && r.waiting)) return false
    const v = version; r.busy = action; r.error = ''; r.message = ''
    try {
      const a = action === 'assess' ? await trainingApi.startTrainingAssessment(s.submission_id, s.revision)
        : r.assessment ? await trainingApi.controlTrainingAssessment(r.assessment, {
          operation_token: requestToken(), action,
          reason: action === 'retry' ? '教师确认失败后显式重试一次' : '教师确认接管中断的判定运行',
        }) : null
      if (!a || !valid(v)) return false
      accept(s, a, v)
      if (a.status !== 'running') await loadFeedback(s, v)
      return !['failed', 'cancelled', 'running'].includes(a.status)
    } catch (error) {
      if (!valid(v)) return false
      if (isAmbiguousWriteError(error)) {
        r.waiting = true; r.readError = false
        r.message = '请求等待较久，正在查询后台结果；不会重新发送判定请求。'
        await queryResult(s, v)
      } else { r.error = safeError(error); follow(s, v) }
      return false
    } finally { if (valid(v)) r.busy = '' }
  }
  async function syncEvidence(s: TrainingSubmission, action: 'publish' | 'withdraw' = 'publish') {
    const r = records.value[submissionKey(s)]
    if (!r?.assessment || r.busy || r.waiting || r.assessment.status === 'running') return false
    const v = version, token = requestToken(), assessment = r.assessment
    r.busy = action; r.error = ''
    try {
      const f = await trainingApi.syncTrainingEvidence(assessment, action, token)
      if (!valid(v)) return false
      r.feedback = f; r.message = f.summary.message; r.view = action === 'publish' ? 'feedback' : 'points'
      return f.status === 'complete' && f.source_review_revision === assessment.review_revision
    } catch (error) {
      if (!valid(v)) return false
      if (isAmbiguousWriteError(error)) {
        await loadFeedback(s, v)
        if (!valid(v)) return false
        const f = r.feedback
        if (f?.source_review_revision === assessment.review_revision && (action === 'withdraw' ? f.status === 'withdrawn' : ['partial', 'complete', 'publication_pending'].includes(f.status))) {
          r.view = action === 'publish' ? 'feedback' : 'points'; r.message = f.summary.message
          return f.status === 'complete'
        }
      }
      r.error = safeError(error); return false
    } finally { if (valid(v)) r.busy = '' }
  }
  async function replayEvidence(s: TrainingSubmission) {
    const r = records.value[submissionKey(s)]
    if (!r || r.busy) return
    const v = version; r.busy = 'replay'; r.error = ''
    try { await trainingApi.replayTrainingEvidence(); if (valid(v)) await loadFeedback(s, v) }
    catch (error) { if (valid(v)) r.error = safeError(error) }
    finally { if (valid(v)) r.busy = '' }
  }
  async function runBulk(kind: 'assess' | 'publish', list: Array<{ submission_id: string; revision: number }>) {
    if (bulk.value || busy.value) return
    const v = version, run = { kind, current: 0, total: list.length, stop: false }
    bulk.value = run; message.value = ''
    let succeeded = 0, failed = 0, skipped = 0
    for (const item of list) {
      if (!valid(v) || run.stop) break
      const row = rows.value.find(r => r.id === item.submission_id)
      if (!row?.submission || row.submission.revision !== item.revision || row.stage !== (kind === 'assess' ? 'unjudged' : 'update') || row.record?.busy) { skipped++; continue }
      bulk.value!.current++
      const ok = kind === 'assess' ? await assess(row.submission) : await syncEvidence(row.submission)
      if (ok) succeeded++; else failed++
    }
    if (!valid(v)) return
    skipped += list.length - succeeded - failed - skipped
    if (kind === 'assess') message.value = [succeeded && `已判定 ${succeeded} 份`, failed && `${failed} 份未完成，不会自动重试`, skipped && `${skipped} 份已跳过`].filter(Boolean).join('；')
    else {
      const reviews = rows.value.filter(r => r.stage === 'review').length
      message.value = [succeeded && `已更新 ${succeeded} 份掌握度`, reviews && `另有 ${reviews} 份待复核，需逐份确认`, (failed + skipped) && `${failed + skipped} 份未完成，请重试`].filter(Boolean).join('；')
    }
    bulk.value = null
  }
  function stopRemaining() { if (bulk.value) bulk.value.stop = true }
  watch(() => paperBatchIds.value.join(','), restoreBatches, { immediate: true })
  onBeforeUnmount(() => { active = false; invalidate() })
  return { batch, batches, records, notes, rows, selectedId, selected, busy, historyLoaded, message, errorMessage, bulk,
    next, newBatch, restoreBatches, openBatch, importFiles, resolve, cancelSubmission, queryResult, loadSubmission,
    lockPoint, assess, syncEvidence, replayEvidence, runBulk, stopRemaining }
}
