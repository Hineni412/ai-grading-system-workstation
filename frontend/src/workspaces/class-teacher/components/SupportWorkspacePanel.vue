<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError } from '../../../api/errors'
import { actionApi, type WorkPlan } from '../api/actions'
import { sopApi, type SopTemplate } from '../api/sop'
import { mergeQuickFragmentWithNeighbor } from '../quickFragments'
import {
  supportApi,
  type AssessmentEvidence,
  type AttentionCard,
  type QuickInboxItem,
  type SpreadsheetPreview,
  type SubjectDeletionPreview,
  type SupportPlan,
  type SupportRecord,
  type SupportSummary,
  type SupportSubject,
} from '../api/support'

const props = defineProps<{
  sessionToken: string
  actionPlans?: WorkPlan[] | null
}>()
const emit = defineEmits<{
  activity: []
  confirmed: [result: 'action_created' | 'no_action' | 'subject_deleted']
  error: [message: string]
  locked: [reason?: string]
}>()

const loading = ref(true)
const saving = ref(false)
const recordSaving = ref(false)
const attentionSavingId = ref('')
const subjects = ref<SupportSubject[]>([])
const records = ref<SupportRecord[]>([])
const quickItems = ref<QuickInboxItem[]>([])
const evidenceItems = ref<AssessmentEvidence[]>([])
const attentionCards = ref<AttentionCard[]>([])
const plans = ref<WorkPlan[]>([])
const sopTemplates = ref<SopTemplate[]>([])
const currentSummary = ref<SupportSummary | null>(null)
const supportPlans = ref<SupportPlan[]>([])
const selectedSubjectId = ref('')
const localMessage = ref('')

const sourceStudentId = ref('')
const displayName = ref('')
const classLabel = ref('')
const quickText = ref('')
const quickKinds = ref<Record<string, string>>({})
const quickTargets = ref<Record<string, 'support_record' | 'action' | 'sop'>>({})
const quickPlanIds = ref<Record<string, string>>({})
const quickDueAts = ref<Record<string, string>>({})
const quickReviewAts = ref<Record<string, string>>({})
const quickExpiresAts = ref<Record<string, string>>({})
const quickTemplateIds = ref<Record<string, string>>({})
const recordKind = ref('fact')
const recordContent = ref('')
const recordScene = ref('')
const recordSource = ref('教师当场记录')
const recordCounterexample = ref('')
const recordReviewAt = ref('')
const recordExpiresAt = ref('')
const revisingRecordId = ref('')
const revisedContent = ref('')
const revisionReason = ref('')
const supportGoal = ref('')
const supportActions = ref('')
const supportReviewAt = ref('')
const activeSupportPlan = ref<SupportPlan | null>(null)
const supportResult = ref('')
const projectionAffairId = ref('')

const assessmentTitle = ref('')
const assessmentSubject = ref('数学')
const assessmentDate = ref('')
const assessmentNature = ref('unit')
const resultState = ref('normal')
const score = ref<number | null>(null)
const maxScore = ref<number | null>(100)
const rank = ref<number | null>(null)
const participantCount = ref<number | null>(null)
const spreadsheetPreview = ref<SpreadsheetPreview | null>(null)
const spreadsheetIdHeader = ref('')
const spreadsheetScoreHeader = ref('')
const spreadsheetFileName = ref('')
const spreadsheetContentBase64 = ref('')

const deletePhrase = ref('')
const backupDeletePhrase = ref('')
const deletionPreview = ref<SubjectDeletionPreview | null>(null)
const attentionPlanId = ref('')
const attentionReviewAt = ref('')
const noActionReasons = ref<Record<string, string>>({})

const selectedSubject = computed(() => (
  subjects.value.find((item) => item.subject_id === selectedSubjectId.value) ?? null
))
const expiringRecord = computed(() => (
  recordKind.value === 'teacher_observation'
  || recordKind.value === 'provisional_judgment'
))
const matchedSpreadsheetRows = computed(() => {
  if (!spreadsheetPreview.value || !spreadsheetIdHeader.value || !spreadsheetScoreHeader.value) {
    return []
  }
  return spreadsheetPreview.value.rows.flatMap((row) => {
    const sourceId = String(row[spreadsheetIdHeader.value] ?? '').trim()
    const subject = subjects.value.find((item) => item.source_student_id === sourceId)
    if (!subject) return []
    const rawResult = String(row[spreadsheetScoreHeader.value] ?? '').trim()
    const normalized = normalizeSpreadsheetResult(rawResult)
    return [{ subject, rawResult, ...normalized }]
  })
})
const spreadsheetDuplicateIds = computed(() => {
  const counts = new Map<string, number>()
  for (const row of matchedSpreadsheetRows.value) {
    counts.set(row.subject.subject_id, (counts.get(row.subject.subject_id) ?? 0) + 1)
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([id]) => id)
})
const spreadsheetUnmatchedCount = computed(() => {
  if (!spreadsheetPreview.value || !spreadsheetIdHeader.value) return 0
  return spreadsheetPreview.value.rows.filter((row) => {
    const sourceId = String(row[spreadsheetIdHeader.value] ?? '').trim()
    return !subjects.value.some((subject) => subject.source_student_id === sourceId)
  }).length
})

function toIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null
}

function normalizeSpreadsheetResult(raw: string): {
  result_state: string
  score: number | null
  valid: boolean
} {
  const labels: Record<string, string> = {
    缺考: 'absent',
    免考: 'exempt',
    缺失: 'missing',
    未完成: 'incomplete',
    补考: 'makeup',
  }
  if (!raw) return { result_state: 'missing', score: null, valid: true }
  if (labels[raw] && labels[raw] !== 'makeup') {
    return { result_state: labels[raw], score: null, valid: true }
  }
  if (raw === '补考') return { result_state: 'makeup', score: null, valid: false }
  const parsed = Number(raw.replace(/^补考[:：]?/, ''))
  if (!Number.isFinite(parsed)) return { result_state: 'missing', score: null, valid: false }
  return {
    result_state: raw.startsWith('补考') ? 'makeup' : 'normal',
    score: parsed,
    valid: true,
  }
}

function arrayBufferBase64(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value)
  let binary = ''
  const blockSize = 0x8000
  for (let index = 0; index < bytes.length; index += blockSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + blockSize))
  }
  return btoa(binary)
}

function report(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked')
    return
  }
  emit(
    'error',
    error instanceof ApiError
      ? error.message
      : '学生支持区没有完成本次操作，现有记录没有改变。',
  )
}

function reportPostRecordSummaryRefresh(error: unknown): void {
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit(
      'locked',
      '支持记录已经保存，但当前有效摘要暂时无法重新读取；保险箱已锁定，请重新解锁后核对。',
    )
    return
  }
  emit(
    'error',
    '支持记录已经保存，但当前有效摘要暂时无法重新读取，请稍后重试。',
  )
}

function reportPostAttentionRefresh(
  error: unknown,
  decision: 'follow_up' | 'observe' | 'no_action',
): void {
  const savedFact = decision === 'no_action'
    ? '关注卡处置已经保存，没有创建行动'
    : '关注卡处置和对应行动已经保存'
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit(
      'locked',
      `${savedFact}，但支持区最新状态暂时无法重新读取；保险箱已锁定，请重新解锁后核对。`,
    )
    return
  }
  emit(
    'error',
    `${savedFact}，但支持区最新状态暂时无法重新读取，请稍后重试。`,
  )
}

function reportPostSubjectDeletionRefresh(error: unknown): void {
  const completedFact = '学生支持数据及受影响旧备份已经删除，但支持区最新状态暂时无法重新读取'
  if (error instanceof ApiError && error.code === 'vault_locked') {
    emit('locked', `${completedFact}；保险箱已锁定，请重新解锁后核对。`)
    return
  }
  emit('error', `${completedFact}，请稍后重试。`)
}

async function refreshSubjects(): Promise<void> {
  subjects.value = await supportApi.listSubjects(props.sessionToken)
  if (
    selectedSubjectId.value
    && !subjects.value.some((item) => item.subject_id === selectedSubjectId.value)
  ) selectedSubjectId.value = ''
  if (!selectedSubjectId.value && subjects.value[0]) {
    selectedSubjectId.value = subjects.value[0].subject_id
  }
}

function replaceActionPlans(loadedPlans: WorkPlan[]): void {
  plans.value = loadedPlans.map((item) => ({ ...item }))
  if (
    attentionPlanId.value
    && !plans.value.some((item) => item.plan_id === attentionPlanId.value)
  ) attentionPlanId.value = ''
  if (!attentionPlanId.value && plans.value[0]) {
    attentionPlanId.value = plans.value[0].plan_id
  }
}

async function refreshSubjectDetails(): Promise<void> {
  if (!selectedSubjectId.value) {
    records.value = []
    evidenceItems.value = []
    attentionCards.value = []
    currentSummary.value = null
    supportPlans.value = []
    activeSupportPlan.value = null
    deletionPreview.value = null
    return
  }
  const [loadedRecords, loadedEvidence, loadedAttention, summary, loadedSupportPlans] = await Promise.all([
    supportApi.listRecords(props.sessionToken, selectedSubjectId.value),
    supportApi.listEvidence(props.sessionToken, selectedSubjectId.value),
    supportApi.listAttention(props.sessionToken, selectedSubjectId.value),
    supportApi.getSummary(props.sessionToken, selectedSubjectId.value),
    supportApi.listSupportPlans(props.sessionToken, selectedSubjectId.value),
  ])
  records.value = loadedRecords
  evidenceItems.value = loadedEvidence
  attentionCards.value = loadedAttention
  currentSummary.value = summary
  supportPlans.value = loadedSupportPlans
  activeSupportPlan.value = loadedSupportPlans.find((item) => item.state === 'active') ?? null
  deletionPreview.value = null
  deletePhrase.value = ''
  backupDeletePhrase.value = ''
}

async function refreshCurrentSummaryAfterRecordWrite(subjectId: string): Promise<boolean> {
  try {
    const summary = await supportApi.getSummary(props.sessionToken, subjectId)
    if (selectedSubjectId.value === subjectId) currentSummary.value = summary
    return true
  } catch (error) {
    reportPostRecordSummaryRefresh(error)
    return false
  }
}

async function refresh(): Promise<void> {
  try {
    await refreshSubjects()
    const [inboxes, loadedPlans, loadedTemplates] = await Promise.all([
      supportApi.listQuickInbox(props.sessionToken),
      actionApi.listPlans(props.sessionToken),
      sopApi.listTemplates(props.sessionToken),
    ])
    quickItems.value = inboxes
    replaceActionPlans(props.actionPlans ?? loadedPlans)
    sopTemplates.value = loadedTemplates
    await refreshSubjectDetails()
  } catch (error) {
    report(error)
  } finally {
    loading.value = false
  }
}

async function selectSubject(): Promise<void> {
  try {
    await refreshSubjectDetails()
  } catch (error) {
    report(error)
  }
}

async function createSubject(): Promise<void> {
  if (!sourceStudentId.value.trim() || !displayName.value.trim()) return
  saving.value = true
  try {
    const created = await supportApi.createSubject(props.sessionToken, {
      source_student_id: sourceStudentId.value,
      display_name: displayName.value,
      class_label: classLabel.value.trim() || null,
    })
    subjects.value = [...subjects.value, created]
    selectedSubjectId.value = created.subject_id
    sourceStudentId.value = ''
    displayName.value = ''
    classLabel.value = ''
    localMessage.value = '已建立加密身份快照；来源编号不会以明文写入普通数据表。'
    await refreshSubjectDetails()
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createQuickText(): Promise<void> {
  if (!quickText.value.trim()) return
  saving.value = true
  try {
    const created = await supportApi.createQuickText(
      props.sessionToken,
      quickText.value,
      selectedSubjectId.value || null,
    )
    quickItems.value = [created, ...quickItems.value]
    quickText.value = ''
    localMessage.value = '文字已放入待确认口袋，尚未进入正式档案。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function confirmQuick(item: QuickInboxItem, fragment = item.fragments[0]): Promise<void> {
  if (!fragment || !selectedSubjectId.value) return
  const targetKind = quickTargets.value[fragment.fragment_id] || 'support_record'
  const recordKind = quickKinds.value[fragment.fragment_id] || 'fact'
  if (
    targetKind === 'support_record'
    && ['teacher_observation', 'provisional_judgment'].includes(recordKind)
    && (
      !quickReviewAts.value[fragment.fragment_id]
      || !quickExpiresAts.value[fragment.fragment_id]
    )
  ) return
  if (targetKind === 'action' && !quickPlanIds.value[fragment.fragment_id]) return
  if (targetKind === 'sop' && !quickTemplateIds.value[fragment.fragment_id]) return
  saving.value = true
  try {
    const options: Record<string, unknown> = targetKind === 'support_record'
      ? {
          subject_id: selectedSubjectId.value,
          record_kind: recordKind,
          scene: '教师文字速记',
          observed_at: new Date().toISOString(),
          review_at: toIso(quickReviewAts.value[fragment.fragment_id] || ''),
          expires_at: toIso(quickExpiresAts.value[fragment.fragment_id] || ''),
        }
      : targetKind === 'action'
        ? {
            plan_id: quickPlanIds.value[fragment.fragment_id],
            title: fragment.text,
            due_at: toIso(quickDueAts.value[fragment.fragment_id] || ''),
          }
        : {
            template_version_id: quickTemplateIds.value[fragment.fragment_id],
            title: fragment.text,
            participant_refs: [selectedSubjectId.value],
          }
    await supportApi.confirmQuickRecord(
      props.sessionToken,
      item,
      fragment,
      targetKind,
      options,
    )
    quickItems.value = await supportApi.listQuickInbox(props.sessionToken)
    await refreshSubjectDetails()
    localMessage.value = targetKind === 'support_record'
      ? '教师已确认一条片段，并进入支持时间线。'
      : targetKind === 'action'
        ? '教师已确认一条片段，并加入行动账本。'
        : '教师已确认一条片段，并建立了一项 SOP 事务。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function addQuickFragment(item: QuickInboxItem): void {
  item.fragments.push({
    fragment_id: globalThis.crypto.randomUUID(),
    text: '',
    suggested_kind: 'unclassified',
  })
}

function mergeQuickFragment(item: QuickInboxItem, fragmentIndex: number): void {
  item.fragments = mergeQuickFragmentWithNeighbor(item.fragments, fragmentIndex)
}

async function saveQuickFragments(item: QuickInboxItem): Promise<void> {
  const fragments = item.fragments
    .map((fragment) => ({ ...fragment, text: fragment.text.trim() }))
    .filter((fragment) => fragment.text)
  if (!fragments.length) return
  saving.value = true
  try {
    const updated = await supportApi.updateQuickFragments(props.sessionToken, item, fragments)
    quickItems.value = quickItems.value.map((value) => (
      value.inbox_item_id === updated.inbox_item_id ? updated : value
    ))
    localMessage.value = '片段拆分、合并或文字修订已保存，仍在待确认区。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function cancelQuick(item: QuickInboxItem): Promise<void> {
  saving.value = true
  try {
    await supportApi.cancelQuick(props.sessionToken, item.inbox_item_id)
    quickItems.value = quickItems.value.filter(
      (value) => value.inbox_item_id !== item.inbox_item_id,
    )
    localMessage.value = '速记已取消，待确认正文已清除。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createRecord(): Promise<void> {
  if (recordSaving.value) return
  if (
    !selectedSubjectId.value
    || !recordContent.value.trim()
    || !recordScene.value.trim()
  ) return
  if (expiringRecord.value && (!recordReviewAt.value || !recordExpiresAt.value)) return
  recordSaving.value = true
  saving.value = true
  try {
    const subjectId = selectedSubjectId.value
    const created = await supportApi.createRecord(
      props.sessionToken,
      subjectId,
      {
        record_kind: recordKind.value,
        content: recordContent.value,
        scene: recordScene.value,
        source: recordSource.value,
        basis: null,
        counterexample: recordCounterexample.value.trim() || null,
        category: 'general',
        observed_at: new Date().toISOString(),
        review_at: toIso(recordReviewAt.value),
        expires_at: toIso(recordExpiresAt.value),
      },
    )
    if (selectedSubjectId.value === subjectId) {
      records.value = [created, ...records.value]
    }
    recordContent.value = ''
    recordScene.value = ''
    recordCounterexample.value = ''
    localMessage.value = '支持记录已保存；观察与阶段性判断到期后会自动退出当前摘要。'
    emit('activity')
    await refreshCurrentSummaryAfterRecordWrite(subjectId)
  } catch (error) {
    report(error)
  } finally {
    recordSaving.value = false
    saving.value = false
  }
}

function beginRevision(item: SupportRecord): void {
  if (recordSaving.value) return
  revisingRecordId.value = item.record_id
  revisedContent.value = item.content
  revisionReason.value = ''
}

async function reviseRecord(): Promise<void> {
  if (recordSaving.value) return
  const current = records.value.find((item) => item.record_id === revisingRecordId.value)
  if (!current || !revisedContent.value.trim() || !revisionReason.value.trim()) return
  recordSaving.value = true
  saving.value = true
  try {
    const subjectId = current.subject_id
    const updated = await supportApi.reviseRecord(
      props.sessionToken,
      current,
      revisedContent.value,
      revisionReason.value,
    )
    if (selectedSubjectId.value === subjectId) {
      records.value = records.value.map((item) => (
        item.record_id === updated.record_id ? updated : item
      ))
    }
    revisingRecordId.value = ''
    localMessage.value = '修订已保存，旧版本仍可追溯。'
    emit('activity')
    await refreshCurrentSummaryAfterRecordWrite(subjectId)
  } catch (error) {
    report(error)
  } finally {
    recordSaving.value = false
    saving.value = false
  }
}

async function createSupportPlan(): Promise<void> {
  if (!selectedSubjectId.value || !supportGoal.value.trim() || !supportReviewAt.value) return
  const actions = supportActions.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
  if (!actions.length) return
  saving.value = true
  try {
    const created = await supportApi.createSupportPlan(
      props.sessionToken,
      selectedSubjectId.value,
      {
        goal: supportGoal.value,
        support_actions: actions,
        review_at: toIso(supportReviewAt.value) as string,
      },
    )
    activeSupportPlan.value = created
    supportPlans.value = [created, ...supportPlans.value]
    localMessage.value = '支持计划已建立，等待复查结果。'
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function completeSupportPlan(): Promise<void> {
  if (!activeSupportPlan.value || !supportResult.value.trim()) return
  saving.value = true
  try {
    const completed = await supportApi.completeSupportPlan(
      props.sessionToken,
      activeSupportPlan.value.support_plan_id,
      activeSupportPlan.value.revision,
      supportResult.value,
    )
    activeSupportPlan.value = completed
    supportPlans.value = supportPlans.value.map((item) => (
      item.support_plan_id === completed.support_plan_id
        ? completed
        : item
    ))
    localMessage.value = '支持结果已记录。'
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function projectAffair(): Promise<void> {
  if (!selectedSubjectId.value || !projectionAffairId.value.trim()) return
  saving.value = true
  try {
    const projected = await supportApi.projectAffair(
      props.sessionToken,
      selectedSubjectId.value,
      projectionAffairId.value,
    )
    records.value = [projected, ...records.value]
    projectionAffairId.value = ''
    localMessage.value = '教师已确认结案的 SOP 结果已进入支持时间线。'
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function confirmEvidence(): Promise<void> {
  if (
    !selectedSubjectId.value
    || !assessmentTitle.value.trim()
    || !assessmentDate.value
    || !assessmentSubject.value.trim()
  ) return
  if (['normal', 'makeup'].includes(resultState.value) && score.value === null) return
  saving.value = true
  try {
    await supportApi.confirmEvidence(props.sessionToken, {
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      source_label: '教师在页面确认的单条证据',
      assessments: [{
        title: assessmentTitle.value,
        subject_name: assessmentSubject.value,
        occurred_on: assessmentDate.value,
        max_score: maxScore.value,
        rank_scope: rank.value === null ? null : 'class',
        participant_count: participantCount.value,
        assessment_nature: assessmentNature.value,
        results: [{
          subject_id: selectedSubjectId.value,
          result_state: resultState.value,
          score: ['normal', 'makeup'].includes(resultState.value) ? score.value : null,
          rank: rank.value,
        }],
      }],
    })
    assessmentTitle.value = ''
    score.value = null
    rank.value = null
    await refreshSubjectDetails()
    localMessage.value = '教师确认的证据快照已加密保存；没有保留原始文件。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function previewSpreadsheet(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  saving.value = true
  try {
    spreadsheetFileName.value = file.name
    spreadsheetContentBase64.value = arrayBufferBase64(await file.arrayBuffer())
    spreadsheetPreview.value = await supportApi.previewSpreadsheet(
      props.sessionToken,
      spreadsheetFileName.value,
      spreadsheetContentBase64.value,
    )
    spreadsheetIdHeader.value = spreadsheetPreview.value.headers.find(
      (value) => /学号|编号|student.?id/i.test(value),
    ) ?? spreadsheetPreview.value.headers[0] ?? ''
    spreadsheetScoreHeader.value = spreadsheetPreview.value.headers.find(
      (value) => /成绩|分数|score/i.test(value),
    ) ?? spreadsheetPreview.value.headers[1] ?? ''
    localMessage.value = '文件只在内存中预览，没有建立临时原始文件。请核对学生对应关系后再确认。'
    emit('activity')
  } catch (error) {
    spreadsheetPreview.value = null
    report(error)
  } finally {
    input.value = ''
    saving.value = false
  }
}

async function changeSpreadsheetSheet(): Promise<void> {
  if (!spreadsheetPreview.value || !spreadsheetContentBase64.value) return
  saving.value = true
  try {
    spreadsheetPreview.value = await supportApi.previewSpreadsheet(
      props.sessionToken,
      spreadsheetFileName.value,
      spreadsheetContentBase64.value,
      spreadsheetPreview.value.selected_sheet,
    )
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function confirmSpreadsheetEvidence(): Promise<void> {
  if (
    !spreadsheetPreview.value
    || !matchedSpreadsheetRows.value.length
    || spreadsheetDuplicateIds.value.length > 0
    || matchedSpreadsheetRows.value.some((item) => !item.valid)
    || spreadsheetPreview.value.truncated
    || !assessmentTitle.value.trim()
    || !assessmentDate.value
  ) return
  saving.value = true
  try {
    await supportApi.confirmEvidence(props.sessionToken, {
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      source_label: `${spreadsheetPreview.value.file_name} · ${spreadsheetPreview.value.selected_sheet}`,
      assessments: [{
        title: assessmentTitle.value,
        subject_name: assessmentSubject.value,
        occurred_on: assessmentDate.value,
        max_score: maxScore.value,
        rank_scope: null,
        participant_count: matchedSpreadsheetRows.value.length,
        assessment_nature: assessmentNature.value,
        results: matchedSpreadsheetRows.value.map((item) => ({
          subject_id: item.subject.subject_id,
          result_state: item.result_state,
          score: item.score,
          rank: null,
        })),
      }],
    })
    spreadsheetPreview.value = null
    spreadsheetContentBase64.value = ''
    spreadsheetFileName.value = ''
    spreadsheetIdHeader.value = ''
    spreadsheetScoreHeader.value = ''
    await refreshSubjectDetails()
    localMessage.value = '教师确认的表格映射已形成加密证据；原始文件没有留在班主任数据库中。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function createAttention(item: AssessmentEvidence): Promise<void> {
  saving.value = true
  try {
    const created = await supportApi.createAttention(props.sessionToken, {
      evidence_version_id: item.evidence_version_id,
      observed_fact: `${item.title}记录为${resultStateLabel(item.result_state)}${
        item.score === null ? '' : ` ${item.score} 分`
      }。`,
      comparability: 'insufficient_information',
      limitations: ['当前只引用这一条证据，不能形成稳定结论'],
      verification_question: '近期学习安排或考试条件是否发生变化？',
      low_risk_next_step: '建议教师先了解近期情况',
      evidence_sufficiency: '单条证据，仅能提示进一步了解',
      review_suggestion: '结合后续同口径证据复查',
    })
    attentionCards.value = [created, ...attentionCards.value]
    localMessage.value = '已形成待教师处置的关注卡；没有风险分，也没有自动创建任务。'
    emit('activity')
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function resolveAttention(
  card: AttentionCard,
  decision: 'follow_up' | 'observe' | 'no_action',
): Promise<void> {
  if (saving.value || attentionSavingId.value) return
  if (
    decision !== 'no_action'
    && (!attentionPlanId.value || (decision === 'observe' && !attentionReviewAt.value))
  ) return
  const reason = noActionReasons.value[card.attention_card_id]?.trim() || null
  if (decision === 'no_action' && !reason) return
  attentionSavingId.value = card.attention_card_id
  saving.value = true
  try {
    const resolved = await supportApi.resolveAttention(props.sessionToken, card, {
      decision,
      reason,
      plan_id: decision === 'no_action' ? null : attentionPlanId.value,
      review_at: decision === 'no_action' ? null : toIso(attentionReviewAt.value),
    })
    attentionCards.value = attentionCards.value.map((item) => (
      item.attention_card_id === card.attention_card_id
        ? {
            ...item,
            revision: item.revision + 1,
            state: 'resolved',
            decision,
            action_id: typeof resolved.action_id === 'string' ? resolved.action_id : null,
          }
        : item
    ))
    localMessage.value = decision === 'no_action'
      ? '教师的“无需处理”决定已留痕，没有创建行动。'
      : '教师决定已进入行动账本，且只创建了一份行动。'
    emit('activity')
    emit('confirmed', decision === 'no_action' ? 'no_action' : 'action_created')
    try {
      await refreshSubjectDetails()
    } catch (error) {
      reportPostAttentionRefresh(error, decision)
    }
  } catch (error) {
    report(error)
  } finally {
    if (attentionSavingId.value === card.attention_card_id) attentionSavingId.value = ''
    saving.value = false
  }
}

async function deleteSubject(): Promise<void> {
  if (!selectedSubjectId.value || deletePhrase.value !== '确认完整删除学生支持数据') return
  if (
    (deletionPreview.value?.affected_backup_count ?? 0) > 0
    && backupDeletePhrase.value !== '确认销毁受影响的班主任专用备份'
  ) return
  saving.value = true
  try {
    const deletedSubjectId = selectedSubjectId.value
    await supportApi.deleteSubject(
      props.sessionToken,
      deletedSubjectId,
      deletePhrase.value,
      deletionPreview.value?.affected_backup_count
        ? backupDeletePhrase.value
        : null,
    )
    subjects.value = subjects.value.filter((item) => item.subject_id !== deletedSubjectId)
    selectedSubjectId.value = subjects.value[0]?.subject_id ?? ''
    records.value = []
    evidenceItems.value = []
    attentionCards.value = []
    currentSummary.value = null
    supportPlans.value = []
    activeSupportPlan.value = null
    deletePhrase.value = ''
    backupDeletePhrase.value = ''
    deletionPreview.value = null
    localMessage.value = '该学生的支持数据及旧专用备份已处理完成。建议现在建立一份删除后的干净专用备份。'
    emit('activity')
    emit('confirmed', 'subject_deleted')
    try {
      await refreshSubjects()
      await refreshSubjectDetails()
    } catch (error) {
      reportPostSubjectDeletionRefresh(error)
    }
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

async function loadDeletionPreview(): Promise<void> {
  if (!selectedSubjectId.value) return
  saving.value = true
  try {
    deletionPreview.value = await supportApi.previewSubjectDeletion(
      props.sessionToken,
      selectedSubjectId.value,
    )
  } catch (error) {
    report(error)
  } finally {
    saving.value = false
  }
}

function kindLabel(value: string): string {
  return ({
    fact: '事实',
    student_statement: '学生自述',
    reported_statement: '他人转述',
    teacher_observation: '教师观察',
    provisional_judgment: '阶段性判断',
    professional_conclusion: '专业结论',
    ai_draft: 'AI 草稿',
  } as Record<string, string>)[value] ?? value
}

function resultStateLabel(value: string): string {
  return ({
    normal: '正常成绩',
    absent: '缺考',
    exempt: '免考',
    missing: '缺失',
    incomplete: '未完成',
    makeup: '补考',
  } as Record<string, string>)[value] ?? value
}

watch(
  () => props.actionPlans,
  (value) => {
    if (value === undefined || value === null) return
    replaceActionPlans(value)
  },
)

onMounted(() => { void refresh() })
</script>

<template>
  <section class="support-workspace">
    <header class="support-heading">
      <div>
        <p class="support-kicker">学生支持档案 · 受保护纸夹</p>
        <h2>记录可追溯的事实，不给学生贴标签</h2>
        <p>速记、证据和关注卡都要经过教师确认；外部模型与语音转写保持关闭。</p>
      </div>
      <span class="safety-stamp">AI 请求 0 次</span>
    </header>

    <p v-if="localMessage" class="local-message" role="status">{{ localMessage }}</p>
    <p v-if="loading" class="empty-note">正在打开加密纸夹…</p>

    <template v-else>
      <div class="subject-rail">
        <label>
          <span>当前学生支持对象</span>
          <select
            v-model="selectedSubjectId"
            :disabled="saving || recordSaving"
            @change="selectSubject"
          >
            <option value="">尚未选择</option>
            <option v-for="item in subjects" :key="item.subject_id" :value="item.subject_id">
              {{ item.display_name }}{{ item.class_label ? ` · ${item.class_label}` : '' }}
            </option>
          </select>
        </label>
        <details>
          <summary>建立新的加密身份快照</summary>
          <form class="compact-form" @submit.prevent="createSubject">
            <input v-model="sourceStudentId" placeholder="来源学生编号（仅加密保存）">
            <input v-model="displayName" placeholder="页面显示名称">
            <input v-model="classLabel" placeholder="班级（可不填）">
            <button type="submit" :disabled="saving">建立支持对象</button>
          </form>
        </details>
      </div>

      <div class="workspace-grid">
        <article class="paper-panel paper-panel--inbox">
          <div class="panel-heading">
            <div>
              <span class="panel-number">B08</span>
              <h3>文字速记口袋</h3>
            </div>
            <span class="muted-stamp">语音未启用</span>
          </div>
          <p>随手输入只会进入待确认区，不会自动进入学生档案。</p>
          <form @submit.prevent="createQuickText">
            <textarea v-model="quickText" rows="3" placeholder="输入一段待整理的文字…"></textarea>
            <button type="submit" :disabled="saving || !quickText.trim()">放入待确认口袋</button>
          </form>
          <ul v-if="quickItems.length" class="pocket-list">
            <li v-for="item in quickItems" :key="item.inbox_item_id">
              <div
                v-for="(fragment, fragmentIndex) in item.fragments"
                :key="fragment.fragment_id"
                class="fragment-row"
              >
                <textarea v-model="fragment.text" rows="2" aria-label="待确认片段"></textarea>
                <select v-model="quickTargets[fragment.fragment_id]">
                  <option value="support_record">进入支持记录</option>
                  <option value="action">进入行动账本</option>
                  <option value="sop">建立 SOP 事务</option>
                </select>
                <template v-if="(quickTargets[fragment.fragment_id] || 'support_record') === 'support_record'">
                  <select v-model="quickKinds[fragment.fragment_id]">
                    <option value="fact">事实</option>
                    <option value="student_statement">学生自述</option>
                    <option value="reported_statement">他人转述</option>
                    <option value="teacher_observation">教师观察（会到期）</option>
                    <option value="provisional_judgment">阶段性判断（会到期）</option>
                    <option value="professional_conclusion">专业结论</option>
                  </select>
                  <div
                    v-if="['teacher_observation', 'provisional_judgment'].includes(quickKinds[fragment.fragment_id] || '')"
                    class="date-pair"
                  >
                    <label>
                      <span>复核时间</span>
                      <input v-model="quickReviewAts[fragment.fragment_id]" type="datetime-local">
                    </label>
                    <label>
                      <span>到期时间</span>
                      <input v-model="quickExpiresAts[fragment.fragment_id]" type="datetime-local">
                    </label>
                  </div>
                </template>
                <template v-else-if="quickTargets[fragment.fragment_id] === 'action'">
                  <select v-model="quickPlanIds[fragment.fragment_id]">
                    <option value="">选择工作目标</option>
                    <option v-for="plan in plans" :key="plan.plan_id" :value="plan.plan_id">
                      {{ plan.title }}
                    </option>
                  </select>
                  <label>
                    <span>完成时间（可不填）</span>
                    <input v-model="quickDueAts[fragment.fragment_id]" type="datetime-local">
                  </label>
                </template>
                <template v-else>
                  <select v-model="quickTemplateIds[fragment.fragment_id]">
                    <option value="">选择 SOP 模板</option>
                    <option
                      v-for="template in sopTemplates"
                      :key="template.template_version_id"
                      :value="template.template_version_id"
                    >
                      {{ template.title }}
                    </option>
                  </select>
                </template>
                <div class="fragment-actions">
                  <button
                    type="button"
                    class="text-button"
                    :disabled="item.fragments.length <= 1"
                    @click="mergeQuickFragment(item, fragmentIndex)"
                  >
                    {{ fragmentIndex === 0 ? '与下一片段合并' : '与上一片段合并' }}
                  </button>
                </div>
                <button
                  type="button"
                  :disabled="saving || !selectedSubjectId || !fragment.text.trim()"
                  @click="confirmQuick(item, fragment)"
                >
                  确认这一片段
                </button>
              </div>
              <div class="fragment-actions">
                <button type="button" class="text-button" @click="addQuickFragment(item)">
                  新增拆分片段
                </button>
                <button type="button" class="text-button" @click="saveQuickFragments(item)">
                  保存片段编辑
                </button>
              </div>
              <button type="button" class="text-button" @click="cancelQuick(item)">取消整条并清除</button>
            </li>
          </ul>
          <p v-else class="empty-note">待确认口袋为空。</p>
        </article>

        <article class="paper-panel">
          <div class="panel-heading">
            <div>
              <span class="panel-number">B07</span>
              <h3>支持时间线</h3>
            </div>
            <span class="muted-stamp">旧版本保留</span>
          </div>
          <section v-if="currentSummary" class="current-summary" aria-label="当前有效摘要">
            <h4>当前有效摘要</h4>
            <ul v-if="currentSummary.items.length">
              <li v-for="item in currentSummary.items" :key="item.record_id">
                <strong>{{ kindLabel(item.record_kind) }}</strong>
                <span>{{ item.content }}</span>
              </li>
            </ul>
            <p v-else class="empty-note">当前没有仍然有效的摘要内容。</p>
          </section>
          <form class="stack-form" @submit.prevent="createRecord">
            <select v-model="recordKind">
              <option value="fact">事实</option>
              <option value="student_statement">学生自述</option>
              <option value="reported_statement">他人转述</option>
              <option value="teacher_observation">教师观察（会到期）</option>
              <option value="provisional_judgment">阶段性判断（会到期）</option>
              <option value="professional_conclusion">专业结论</option>
            </select>
            <textarea v-model="recordContent" rows="3" placeholder="具体发生了什么"></textarea>
            <input v-model="recordScene" placeholder="时间与场景">
            <input v-model="recordSource" placeholder="信息来源">
            <textarea v-model="recordCounterexample" rows="2" placeholder="反例或不同情形（可不填）"></textarea>
            <div v-if="expiringRecord" class="date-pair">
              <label><span>复核时间</span><input v-model="recordReviewAt" type="datetime-local"></label>
              <label><span>到期时间</span><input v-model="recordExpiresAt" type="datetime-local"></label>
            </div>
            <button type="submit" :disabled="saving || !selectedSubjectId">保存分层记录</button>
          </form>
          <ul v-if="records.length" class="timeline">
            <li v-for="item in records" :key="item.record_id" :data-state="item.state">
              <div><span>{{ kindLabel(item.record_kind) }}</span><small>第 {{ item.current_revision }} 版</small></div>
              <p>{{ item.content }}</p>
              <small>{{ item.scene }} · 来源：{{ item.source }}</small>
              <small v-if="item.counterexample">反例：{{ item.counterexample }}</small>
              <button
                type="button"
                class="text-button"
                :disabled="saving || recordSaving"
                @click="beginRevision(item)"
              >
                修订并保留旧版
              </button>
            </li>
          </ul>
          <p v-else class="empty-note">当前学生还没有支持记录。</p>
          <form v-if="revisingRecordId" class="stack-form revision-form" @submit.prevent="reviseRecord">
            <textarea v-model="revisedContent" rows="3"></textarea>
            <input v-model="revisionReason" placeholder="本次修订原因">
            <button type="submit" :disabled="saving || recordSaving">保存新版本</button>
          </form>
          <details class="support-plan-pocket">
            <summary>建立支持计划、记录结果或投影 SOP 结案</summary>
            <form class="stack-form" @submit.prevent="createSupportPlan">
              <input v-model="supportGoal" placeholder="支持目标">
              <textarea v-model="supportActions" rows="2" placeholder="每行一项支持行动"></textarea>
              <label><span>复查时间</span><input v-model="supportReviewAt" type="datetime-local"></label>
              <button type="submit">建立支持计划</button>
            </form>
            <form v-if="activeSupportPlan" class="stack-form" @submit.prevent="completeSupportPlan">
              <p>当前计划：{{ activeSupportPlan.goal }}</p>
              <textarea v-model="supportResult" rows="2" placeholder="复查后的支持结果"></textarea>
              <button type="submit">记录支持结果</button>
            </form>
            <ul v-if="supportPlans.length" class="support-plan-list">
              <li v-for="plan in supportPlans" :key="plan.support_plan_id">
                <strong>{{ plan.goal }}</strong>
                <span>{{ plan.state === 'active' ? '待复查' : '已完成' }}</span>
                <small>复查时间：{{ new Date(plan.review_at).toLocaleString() }}</small>
                <p>{{ plan.support_actions.join('；') }}</p>
                <p v-if="plan.result">结果：{{ plan.result }}</p>
              </li>
            </ul>
            <form class="stack-form" @submit.prevent="projectAffair">
              <input v-model="projectionAffairId" placeholder="已结案 SOP 事务编号">
              <button type="submit">投影教师确认的结案结果</button>
            </form>
          </details>
        </article>

        <article class="paper-panel">
          <div class="panel-heading">
            <div>
              <span class="panel-number">B09</span>
              <h3>教师确认的学业证据</h3>
            </div>
            <span class="muted-stamp">不是成绩大屏</span>
          </div>
          <p>这里保存确认后的加密快照；0 分与缺考、免考、缺失分别记录。</p>
          <div class="spreadsheet-pocket">
            <label class="file-button">
              <span>选择 CSV / XLSX 在本机内存中预览</span>
              <input type="file" accept=".csv,.xlsx" @change="previewSpreadsheet">
            </label>
            <template v-if="spreadsheetPreview">
              <label v-if="spreadsheetPreview.sheet_names.length > 1">
                <span>工作表</span>
                <select v-model="spreadsheetPreview.selected_sheet" @change="changeSpreadsheetSheet">
                  <option v-for="sheet in spreadsheetPreview.sheet_names" :key="sheet">{{ sheet }}</option>
                </select>
              </label>
              <div class="date-pair">
                <label>
                  <span>学生编号列</span>
                  <select v-model="spreadsheetIdHeader">
                    <option v-for="header in spreadsheetPreview.headers" :key="header">{{ header }}</option>
                  </select>
                </label>
                <label>
                  <span>成绩/状态列</span>
                  <select v-model="spreadsheetScoreHeader">
                    <option v-for="header in spreadsheetPreview.headers" :key="header">{{ header }}</option>
                  </select>
                </label>
              </div>
              <p>
                已预览 {{ spreadsheetPreview.preview_row_count }} 行；
                按来源编号匹配 {{ matchedSpreadsheetRows.length }} 名已建档学生。
                原始文件未保留。
              </p>
              <p v-if="spreadsheetUnmatchedCount || spreadsheetDuplicateIds.length || spreadsheetPreview.truncated" class="import-warning">
                未匹配 {{ spreadsheetUnmatchedCount }} 行；
                重复学生 {{ spreadsheetDuplicateIds.length }} 名；
                {{ spreadsheetPreview.truncated ? '文件超过 5000 行，已阻止确认。' : '请核对后再确认。' }}
              </p>
              <p v-if="matchedSpreadsheetRows.some((item) => !item.valid)" class="import-warning">
                “补考”必须同时填写分数（例如“补考：68”），当前已阻止确认。
              </p>
              <ul class="mapping-preview">
                <li v-for="item in matchedSpreadsheetRows.slice(0, 8)" :key="item.subject.subject_id">
                  <span>{{ item.subject.display_name }}</span>
                  <strong>{{ item.rawResult || '空白（缺失）' }}</strong>
                </li>
              </ul>
              <button
                type="button"
                :disabled="saving || !matchedSpreadsheetRows.length || !assessmentTitle || !assessmentDate || spreadsheetDuplicateIds.length > 0 || matchedSpreadsheetRows.some((item) => !item.valid) || spreadsheetPreview.truncated"
                @click="confirmSpreadsheetEvidence"
              >
                确认对应关系并导入匹配行
              </button>
            </template>
          </div>
          <p class="manual-divider"><span>或手工确认一条证据</span></p>
          <form class="stack-form" @submit.prevent="confirmEvidence">
            <div class="date-pair">
              <input v-model="assessmentTitle" placeholder="考试名称">
              <input v-model="assessmentSubject" placeholder="学科">
            </div>
            <div class="date-pair">
              <label><span>考试日期</span><input v-model="assessmentDate" type="date"></label>
              <label>
                <span>考试性质</span>
                <select v-model="assessmentNature">
                  <option value="unit">单元检查</option>
                  <option value="midterm">期中</option>
                  <option value="final">期末</option>
                  <option value="practice">练习</option>
                </select>
              </label>
            </div>
            <div class="score-grid">
              <label>
                <span>结果状态</span>
                <select v-model="resultState">
                  <option value="normal">正常成绩</option>
                  <option value="absent">缺考</option>
                  <option value="exempt">免考</option>
                  <option value="missing">缺失</option>
                  <option value="incomplete">未完成</option>
                  <option value="makeup">补考</option>
                </select>
              </label>
              <label><span>分数</span><input v-model.number="score" type="number" :disabled="!['normal', 'makeup'].includes(resultState)"></label>
              <label><span>满分</span><input v-model.number="maxScore" type="number"></label>
              <label><span>班内名次</span><input v-model.number="rank" type="number"></label>
              <label><span>参考人数</span><input v-model.number="participantCount" type="number"></label>
            </div>
            <button type="submit" :disabled="saving || !selectedSubjectId">确认并形成证据快照</button>
          </form>
          <ul v-if="evidenceItems.length" class="evidence-list">
            <li v-for="item in evidenceItems" :key="item.evidence_version_id">
              <div>
                <strong>{{ item.title }}</strong>
                <span>{{ item.subject_name }} · {{ item.occurred_on }}</span>
              </div>
              <b>{{ resultStateLabel(item.result_state) }}{{ item.score === null ? '' : ` ${item.score}` }}</b>
              <button type="button" @click="createAttention(item)">形成待处置关注卡</button>
            </li>
          </ul>
        </article>

        <article class="paper-panel paper-panel--attention">
          <div class="panel-heading">
            <div>
              <span class="panel-number">B10</span>
              <h3>关注与教师决定</h3>
            </div>
            <span class="safety-stamp">无风险评分</span>
          </div>
          <div class="decision-settings">
            <label>
              <span>跟进行动放入</span>
              <select v-model="attentionPlanId">
                <option value="">请先在行动账本建立目标</option>
                <option v-for="plan in plans" :key="plan.plan_id" :value="plan.plan_id">
                  {{ plan.title }}
                </option>
              </select>
            </label>
            <label><span>暂时观察复查时间</span><input v-model="attentionReviewAt" type="datetime-local"></label>
          </div>
          <ul v-if="attentionCards.length" class="attention-list">
            <li v-for="card in attentionCards" :key="card.attention_card_id" :data-state="card.state">
              <div class="card-state">{{ card.state === 'draft' ? '待教师决定' : card.state === 'invalidated' ? '证据变化，已失效' : '已处置' }}</div>
              <dl>
                <div><dt>已观察事实</dt><dd>{{ card.observed_fact }}</dd></div>
                <div><dt>证据来源</dt><dd>{{ card.evidence_source.assessment_title }} · {{ card.evidence_source.occurred_on }}</dd></div>
                <div><dt>可比性与局限</dt><dd>{{ card.comparability }}；{{ card.limitations.join('；') }}</dd></div>
                <div><dt>需要核实</dt><dd>{{ card.verification_question }}</dd></div>
                <div><dt>低风险建议</dt><dd>{{ card.low_risk_next_step }}</dd></div>
                <div><dt>证据充分程度</dt><dd>{{ card.evidence_sufficiency }}</dd></div>
                <div><dt>复查建议</dt><dd>{{ card.review_suggestion }}</dd></div>
              </dl>
              <template v-if="card.state === 'draft'">
                <div class="decision-buttons">
                  <button
                    type="button"
                    :disabled="saving || attentionSavingId === card.attention_card_id"
                    @click="resolveAttention(card, 'follow_up')"
                  >
                    需要跟进
                  </button>
                  <button
                    type="button"
                    :disabled="saving || attentionSavingId === card.attention_card_id"
                    @click="resolveAttention(card, 'observe')"
                  >
                    暂时观察
                  </button>
                </div>
                <div class="no-action-row">
                  <input v-model="noActionReasons[card.attention_card_id]" placeholder="无需处理的教师原因">
                  <button
                    type="button"
                    class="text-button"
                    :disabled="saving || attentionSavingId === card.attention_card_id"
                    @click="resolveAttention(card, 'no_action')"
                  >
                    无需处理并留痕
                  </button>
                </div>
              </template>
            </li>
          </ul>
          <p v-else class="empty-note">没有待处置关注卡。系统不会生成全班风险榜。</p>
        </article>
      </div>

      <details v-if="selectedSubject" class="delete-pocket">
        <summary>完整删除 {{ selectedSubject.display_name }} 的支持数据</summary>
        <p>删除前必须先查看影响清单；含该学生的班主任专用备份也必须另行确认销毁。</p>
        <button type="button" :disabled="saving" @click="loadDeletionPreview">查看删除影响</button>
        <div v-if="deletionPreview">
          <p>
            将处理 {{ deletionPreview.shared_object_count }} 个共享事务内容，
            并销毁 {{ deletionPreview.affected_backup_count }} 份班主任专用备份。
          </p>
          <ul v-if="deletionPreview.affected_backups.length">
            <li v-for="backup in deletionPreview.affected_backups" :key="backup.file_name">
              {{ backup.file_name }}（{{ Math.ceil(backup.size_bytes / 1024) }} KB）
            </li>
          </ul>
          <input v-model="deletePhrase" placeholder="输入：确认完整删除学生支持数据">
          <input
            v-if="deletionPreview.affected_backup_count > 0"
            v-model="backupDeletePhrase"
            placeholder="输入：确认销毁受影响的班主任专用备份"
          >
          <button
            type="button"
            :disabled="
              saving
              || deletePhrase !== '确认完整删除学生支持数据'
              || (
                deletionPreview.affected_backup_count > 0
                && backupDeletePhrase !== '确认销毁受影响的班主任专用备份'
              )
            "
            @click="deleteSubject"
          >
            完整删除
          </button>
        </div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.support-workspace {
  margin: var(--space-7) 0;
  padding: var(--space-6);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background:
    linear-gradient(90deg, transparent 31px, color-mix(in srgb, var(--color-danger) 16%, transparent) 32px, transparent 33px),
    var(--color-bg-subtle);
}

.support-heading,
.panel-heading,
.subject-rail,
.decision-settings {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.support-heading h2,
.panel-heading h3 {
  margin: 0;
}

.support-heading p {
  max-width: 760px;
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
}

.support-kicker,
.panel-number {
  color: var(--color-accent-active);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
  letter-spacing: .08em;
}

.safety-stamp,
.muted-stamp,
.card-state {
  flex: 0 0 auto;
  padding: var(--space-1) var(--space-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-tag);
  color: var(--color-success);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-semibold);
}

.muted-stamp {
  color: var(--color-text-muted);
}

.local-message {
  margin: var(--space-4) 0;
  padding: var(--space-3);
  border-inline-start: 3px solid var(--color-success);
  background: var(--color-success-subtle);
}

.subject-rail {
  align-items: end;
  margin: var(--space-5) 0;
  padding: var(--space-4);
  border: 1px solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.subject-rail > label {
  flex: 1;
}

details summary {
  color: var(--color-accent);
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

.compact-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(150px, 1fr)) auto;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.workspace-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.paper-panel {
  padding: var(--space-5);
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-panel);
  background: var(--color-bg-surface);
  box-shadow: 0 2px 0 color-mix(in srgb, var(--color-border-default) 55%, transparent);
}

.paper-panel > p {
  color: var(--color-text-secondary);
  line-height: var(--line-height-body);
}

.paper-panel--attention {
  grid-column: 1 / -1;
}

.stack-form,
.paper-panel--inbox form {
  display: grid;
  gap: var(--space-2);
}

.spreadsheet-pocket {
  display: grid;
  gap: var(--space-3);
  margin: var(--space-3) 0;
  padding: var(--space-3);
  border: 1px dashed var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent-subtle);
}

.file-button {
  display: block;
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-accent);
  text-align: center;
  cursor: pointer;
}

.file-button input {
  position: absolute;
  inline-size: 1px;
  block-size: 1px;
  overflow: hidden;
  opacity: 0;
}

.mapping-preview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-1) var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.mapping-preview li {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
}

.manual-divider {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-caption);
}

.import-warning {
  color: var(--color-danger) !important;
  font-weight: var(--font-weight-medium);
}

.manual-divider::before,
.manual-divider::after {
  flex: 1;
  border-top: 1px solid var(--color-border-default);
  content: "";
}

textarea {
  resize: vertical;
}

button {
  min-height: var(--control-height-default);
  padding: 0 var(--space-3);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent);
  color: var(--color-bg-surface);
  font: inherit;
  cursor: pointer;
}

button:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

.text-button {
  border-color: transparent;
  background: transparent;
  color: var(--color-text-secondary);
}

.pocket-list,
.timeline,
.evidence-list,
.attention-list {
  display: grid;
  gap: var(--space-3);
  margin: var(--space-4) 0 0;
  padding: 0;
  list-style: none;
}

.pocket-list li,
.evidence-list li,
.attention-list li {
  padding: var(--space-3);
  border: 1px dashed var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.pocket-list p {
  margin: 0 0 var(--space-2);
}

.fragment-row {
  display: grid !important;
  grid-template-columns: minmax(0, 1fr) 120px auto;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-subtle);
}

.pocket-list li > div,
.decision-buttons,
.no-action-row {
  display: flex;
  gap: var(--space-2);
}

.timeline li {
  display: grid;
  gap: var(--space-1);
  padding-inline-start: var(--space-4);
  border-inline-start: 3px solid var(--color-accent);
}

.timeline li[data-state="archived"],
.timeline li[data-state="withdrawn"],
.attention-list li[data-state="invalidated"] {
  opacity: .65;
}

.timeline li > div,
.evidence-list li,
.decision-settings {
  align-items: center;
}

.timeline li > div {
  display: flex;
  justify-content: space-between;
}

.timeline p {
  margin: 0;
}

.timeline small,
.evidence-list span {
  color: var(--color-text-muted);
}

.revision-form,
.support-plan-pocket {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px dashed var(--color-border-default);
}

.support-plan-pocket form {
  margin-top: var(--space-3);
}

.date-pair,
.score-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.score-grid {
  grid-template-columns: repeat(5, minmax(90px, 1fr));
}

.evidence-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: var(--space-3);
}

.evidence-list li > div {
  display: grid;
  gap: var(--space-1);
}

.decision-settings {
  margin: var(--space-3) 0;
}

.decision-settings label {
  flex: 1;
}

.attention-list dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.attention-list dl div {
  padding-inline-start: var(--space-3);
  border-inline-start: 2px solid var(--color-border-strong);
}

.attention-list dt {
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
}

.attention-list dd {
  margin: var(--space-1) 0 0;
  line-height: var(--line-height-body);
}

.no-action-row {
  margin-top: var(--space-2);
}

.no-action-row input {
  flex: 1;
}

.delete-pocket {
  margin-top: var(--space-5);
  padding: var(--space-4);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-panel);
}

.delete-pocket p {
  color: var(--color-text-secondary);
}

.delete-pocket > div {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--space-2);
}

.delete-pocket button {
  border-color: var(--color-danger);
  background: var(--color-danger);
}

.empty-note {
  color: var(--color-text-muted);
}

@media (max-width: 960px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }

  .paper-panel--attention {
    grid-column: auto;
  }

  .compact-form,
  .score-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 680px) {
  .support-workspace {
    padding: var(--space-4);
  }

  .support-heading,
  .subject-rail,
  .decision-settings,
  .pocket-list li > div,
  .decision-buttons,
  .no-action-row {
    flex-direction: column;
  }

  .compact-form,
  .date-pair,
  .score-grid,
  .attention-list dl,
  .delete-pocket > div {
    grid-template-columns: 1fr;
  }
}
</style>
