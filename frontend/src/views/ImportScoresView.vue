<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '../api/errors'
import {
  confirmScoreImport,
  fetchScoreImportState,
  previewScoreImport,
  uploadScoreImportFile,
  type ScoreImportConfirmResult,
  type ScoreImportPlanRequest,
  type ScoreImportPreview,
  type ScoreImportSheetInfo,
  type ScoreImportState,
  type ScoreImportUploadMeta,
} from '../api/score-import'
import { fetchStudents, type StudentSummary } from '../api/students'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import StatePanel from '../components/design-system/StatePanel.vue'

const route = useRoute()
const sessionId = computed(() => Number(route.params.sessionId))

const state = ref<ScoreImportState | null>(null)
const uploads = ref<ScoreImportUploadMeta[]>([])
const preview = ref<ScoreImportPreview | null>(null)
const roster = ref<StudentSummary[]>([])
const loadState = ref<'loading' | 'ready' | 'error' | 'not-import'>('loading')
const loadError = ref('')
const uploadBusy = ref(false)
const uploadMessage = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const confirming = ref(false)
const confirmError = ref('')
const confirmResult = ref<ScoreImportConfirmResult | null>(null)
const staleNotice = ref('')
const replaceExisting = ref(false)

const sheetClasses = ref<Record<string, Record<string, string>>>({})
const partChoices = ref<Record<string, 'parts' | 'whole'>>({})
const itemMaxInputs = ref<Record<string, string>>({})
const studentOverrides = ref<Record<string, number | null>>({})
const includedUploadIds = ref<Set<string>>(new Set())
const fileInput = ref<HTMLInputElement | null>(null)
const showAllMapping = ref(false)

const SHEET_KIND_LABELS: Record<ScoreImportSheetInfo['kind'], string> = {
  scores: '小题得分明细',
  options: '客观题作答明细',
  unrecognized: '未识别',
}
const CHOICE_LABELS: Record<string, string> = {
  single: '按列导入',
  parts: '按小问导入',
  whole: '按整题导入',
  derive_options: '由作答明细推算',
  combined: '合并给分',
}

const rosterClasses = computed(() => [...new Set(
  roster.value.map((student) => student.class_name ?? '').filter(Boolean),
)].sort((left, right) => left.localeCompare(right, 'zh-CN', { numeric: true })))
const studentNameOf = computed(() => new Map(
  roster.value.map((student) => [
    student.id,
    `${student.name}${student.class_name ? `（${student.class_name}班）` : ''}`,
  ]),
))
const uploadIds = computed(() => uploads.value
  .map((upload) => upload.upload_id)
  .filter((id) => includedUploadIds.value.has(id)))
const sheetNeedsClass = (sheet: ScoreImportSheetInfo) =>
  sheet.kind === 'scores' && !sheet.class_hint

const itemMax = computed(() => Object.fromEntries(
  Object.entries(itemMaxInputs.value)
    .map(([key, raw]) => [key, Number(raw)] as const)
    .filter(([, value]) => Number.isFinite(value) && value > 0)
))

function buildRequest(): ScoreImportPlanRequest {
  return {
    upload_ids: uploadIds.value,
    sheet_classes: sheetClasses.value,
    part_choices: partChoices.value,
    item_max: itemMax.value,
    student_overrides: studentOverrides.value,
  }
}

const blockIssues = computed(() => (preview.value?.issues ?? []).filter((issue) => issue.level === 'block'))
const warnIssues = computed(() => (preview.value?.issues ?? []).filter((issue) => issue.level === 'warn'))
const unresolvedRows = computed(() => preview.value?.students.unresolved ?? [])
const confirmBlockedReason = computed(() => {
  if (previewLoading.value) return '正在生成预览…'
  if (!preview.value) return '请先上传成绩表'
  if (blockIssues.value.length > 0) return `还有 ${blockIssues.value.length} 个阻断问题`
  if (unresolvedRows.value.length > 0) return `还有 ${unresolvedRows.value.length} 行学生未对应`
  if (preview.value.has_results && !replaceExisting.value) return '请勾选「覆盖已有成绩」'
  return ''
})
const canConfirm = computed(() => !confirming.value && confirmBlockedReason.value === '')
const observedTotal = computed(() => (preview.value?.questions ?? []).reduce(
  (total, question) => total + question.items.reduce(
    (sum, item) => sum + (item.observed_max ?? 0), 0,
  ), 0,
))

function questionNeedsAttention(question: ScoreImportPreview['questions'][number]): boolean {
  if (question.status === 'needs_confirm') return true
  if (question.choice === 'whole' || question.choice === 'combined') return true
  return question.items.some((item) => (
    itemMaxInputs.value[item.item_id] !== undefined
    || (item.observed_max !== null
      && item.observed_max > (itemMax.value[item.item_id] ?? item.max_score))
  ))
}

const mappingQuestions = computed(() => preview.value?.questions ?? [])
const attentionQuestions = computed(() => mappingQuestions.value.filter(questionNeedsAttention))
const visibleQuestions = computed(() => (
  showAllMapping.value ? mappingQuestions.value : attentionQuestions.value
))

function issueWhere(issue: { sheet?: string; row?: number; column?: string }): string {
  const parts = [
    issue.sheet ? `工作表「${issue.sheet}」` : '',
    issue.row !== undefined ? `第 ${issue.row} 行` : '',
    issue.column ? `列「${issue.column}」` : '',
  ].filter(Boolean)
  return parts.length ? `${parts.join(' ')}：` : ''
}

function setIncluded(uploadId: string, checked: boolean): void {
  const next = new Set(includedUploadIds.value)
  if (checked) next.add(uploadId)
  else next.delete(uploadId)
  includedUploadIds.value = next
}

function sheetClassValue(uploadId: string, sheetName: string): string {
  return sheetClasses.value[uploadId]?.[sheetName] ?? ''
}

function setSheetClass(uploadId: string, sheetName: string, value: string): void {
  const next = { ...sheetClasses.value }
  const sheets = { ...(next[uploadId] ?? {}) }
  if (value) sheets[sheetName] = value
  else delete sheets[sheetName]
  next[uploadId] = sheets
  sheetClasses.value = next
}

function overrideValue(rowKey: string): string {
  const value = studentOverrides.value[rowKey]
  if (value === undefined) return ''
  return value === null ? '__skip__' : String(value)
}

function setOverride(rowKey: string, value: string): void {
  const next = { ...studentOverrides.value }
  if (value === '') delete next[rowKey]
  else next[rowKey] = value === '__skip__' ? null : Number(value)
  studentOverrides.value = next
}

function canChooseParts(columns: string[]): boolean {
  return columns.some((column) => /[(（]\d+[)）]/.test(column))
}

function questionNumber(questionId: string): string {
  return questionId.replace(/^Q/i, '')
}

// 「Q14(P3)」→「（3）」，整题评分项显示「整题」，页面上不出现内部题号。
function partLabel(itemId: string): string {
  const part = /^Q?\d+\s*[（(]\s*P?(\d+)\s*[)）]$/i.exec(itemId)
  return part ? `（${part[1]}）` : '整题'
}

function itemLabel(itemId: string): string {
  const part = /^Q?(\d+)\s*[（(]\s*P?(\d+)\s*[)）]$/i.exec(itemId)
  return part ? `${part[1]}（${part[2]}）` : questionNumber(itemId)
}

function diffItemLabel(itemId: string): string {
  const group = preview.value?.groups.find((entry) => entry.group_id === itemId)
  return group ? `${group.label}（合并给分）` : itemLabel(itemId)
}

async function refresh(): Promise<void> {
  const id = sessionId.value
  if (!Number.isSafeInteger(id) || id <= 0) return
  loadState.value = 'loading'
  loadError.value = ''
  try {
    const next = await fetchScoreImportState(id)
    if (sessionId.value !== id) return
    if (next.kind !== 'zhixue_import') {
      loadState.value = 'not-import'
      return
    }
    state.value = next
    uploads.value = next.uploads
    const usedIds = new Set(next.receipt?.upload_ids ?? [])
    includedUploadIds.value = new Set(
      next.uploads
        .map((upload) => upload.upload_id)
        .filter((id) => next.receipt === null || usedIds.has(id)),
    )
    if (next.receipt) {
      sheetClasses.value = { ...(next.receipt.parameters.sheet_classes ?? {}) }
      partChoices.value = { ...(next.receipt.parameters.part_choices ?? {}) }
      studentOverrides.value = { ...(next.receipt.parameters.student_overrides ?? {}) }
      const maxInputs: Record<string, string> = {}
      for (const [itemId, value] of Object.entries(next.receipt.parameters.item_max ?? {})) {
        maxInputs[itemId] = String(value)
      }
      itemMaxInputs.value = maxInputs
    }
    loadState.value = 'ready'
  } catch (error) {
    if (sessionId.value !== id) return
    loadState.value = error instanceof ApiError && error.status === 404
      ? 'not-import' : 'error'
    loadError.value = '导入状态暂时无法读取，请重试。'
  }
}

async function uploadFiles(fileList: FileList | null): Promise<void> {
  if (!fileList || uploadBusy.value) return
  const id = sessionId.value
  uploadBusy.value = true
  uploadMessage.value = ''
  const failures: string[] = []
  for (const file of Array.from(fileList)) {
    try {
      const meta = await uploadScoreImportFile(id, file)
      if (sessionId.value !== id) return
      const index = uploads.value.findIndex((upload) => upload.upload_id === meta.upload_id)
      if (index >= 0) uploads.value[index] = meta
      else uploads.value.push(meta)
      includedUploadIds.value = new Set([...includedUploadIds.value, meta.upload_id])
    } catch (error) {
      failures.push(error instanceof ApiError ? `${file.name}：${error.message}` : `${file.name}：上传失败`)
    }
  }
  uploadBusy.value = false
  uploadMessage.value = failures.join('；')
}

let previewGeneration = 0
let previewTimer: ReturnType<typeof setTimeout> | undefined
async function runPreview(): Promise<void> {
  const generation = ++previewGeneration
  if (uploadIds.value.length === 0) {
    preview.value = null
    previewError.value = ''
    return
  }
  previewLoading.value = true
  previewError.value = ''
  try {
    const result = await previewScoreImport(sessionId.value, buildRequest())
    if (generation !== previewGeneration) return
    preview.value = result
  } catch (error) {
    if (generation !== previewGeneration) return
    preview.value = null
    previewError.value = error instanceof ApiError
      ? error.message
      : '预览暂时无法生成，请重试。'
  } finally {
    if (generation === previewGeneration) previewLoading.value = false
  }
}

function schedulePreview(): void {
  if (previewTimer !== undefined) clearTimeout(previewTimer)
  previewTimer = setTimeout(() => void runPreview(), 400)
}

watch(
  [uploadIds, sheetClasses, partChoices, itemMax, studentOverrides],
  schedulePreview,
  { deep: true },
)

async function confirm(): Promise<void> {
  if (!canConfirm.value || !preview.value) return
  const id = sessionId.value
  confirming.value = true
  confirmError.value = ''
  staleNotice.value = ''
  try {
    const result = await confirmScoreImport(id, {
      ...buildRequest(),
      preview_revision: preview.value.preview_revision,
      replace_existing: replaceExisting.value,
    })
    if (sessionId.value !== id) return
    confirmResult.value = result
    replaceExisting.value = false
    const refreshed = await fetchScoreImportState(id)
    if (sessionId.value !== id) return
    state.value = refreshed
    await runPreview()
  } catch (error) {
    if (sessionId.value !== id) return
    if (error instanceof ApiError && error.code === 'score_import_preview_stale') {
      staleNotice.value = '文件或评分结构有变化，预览已刷新，请重新核对后再确认。'
      await runPreview()
    } else {
      confirmError.value = error instanceof ApiError
        ? error.message
        : '导入未能完成，请检查数据后重试。'
    }
  } finally {
    confirming.value = false
  }
}

onMounted(() => {
  void refresh()
  void fetchStudents().then((students) => { roster.value = students }).catch(() => {})
})
onBeforeUnmount(() => { if (previewTimer !== undefined) clearTimeout(previewTimer) })
watch(sessionId, () => {
  uploads.value = []
  preview.value = null
  confirmResult.value = null
  sheetClasses.value = {}
  partChoices.value = {}
  itemMaxInputs.value = {}
  studentOverrides.value = {}
  replaceExisting.value = false
  staleNotice.value = ''
  void refresh()
})
</script>

<template>
  <article class="import-scores" aria-labelledby="import-scores-title">
    <PageHeader title="导入成绩" title-id="import-scores-title">
      <template #meta>
        <span v-if="state?.exam_date">考试日期 {{ state.exam_date }}</span>
        <span v-if="state?.receipt">
          上次导入 {{ state.receipt.imported_at.replace('T', ' ') }} ·
          {{ state.receipt.counts.students }} 名学生 · {{ state.receipt.counts.items }} 个评分项
        </span>
      </template>
    </PageHeader>

    <StatePanel v-if="loadState === 'loading'" kind="loading" title="正在读取导入状态…" />
    <StatePanel v-else-if="loadState === 'not-import'" kind="empty"
      title="这场考试不是智学网成绩导入考试"
      description="扫描批改的考试请上传答卷，不走成绩导入。" />
    <StatePanel v-else-if="loadState === 'error'" kind="error"
      title="导入状态暂时无法读取" :description="loadError"
      retry-label="重新加载" @retry="refresh" />

    <template v-else>
      <section class="import-section" aria-labelledby="import-upload-title">
        <h2 id="import-upload-title">上传成绩表</h2>
        <p class="import-hint">
          使用智学网导出的「小题得分明细」和「客观题作答明细」（.xlsx，可按班一个文件或用年级总表）。
        </p>
        <div class="import-upload-bar">
          <input
            ref="fileInput"
            type="file"
            multiple
            accept=".xlsx"
            class="import-file-input"
            :disabled="uploadBusy"
            aria-label="选择智学网成绩表"
            @change="uploadFiles(($event.target as HTMLInputElement).files); ($event.target as HTMLInputElement).value = ''"
          >
          <AppButton variant="secondary" :disabled="uploadBusy" @click="fileInput?.click()">
            {{ uploadBusy ? '正在上传…' : '选择成绩表' }}
          </AppButton>
        </div>
        <p v-if="uploadMessage" class="import-warn" role="alert">{{ uploadMessage }}</p>

        <table v-if="uploads.length" class="app-table">
          <thead>
            <tr><th>使用</th><th>文件 / 工作表</th><th>识别类型</th><th>行数</th><th>班级</th></tr>
          </thead>
          <tbody>
            <template v-for="upload in uploads" :key="upload.upload_id">
              <tr v-for="(sheet, index) in upload.sheets" :key="sheet.name">
                <td v-if="index === 0" :rowspan="upload.sheets.length">
                  <input
                    type="checkbox"
                    :aria-label="`本次导入使用 ${upload.filename}`"
                    :checked="includedUploadIds.has(upload.upload_id)"
                    @change="setIncluded(upload.upload_id, ($event.target as HTMLInputElement).checked)"
                  >
                </td>
                <th scope="row">
                  <strong v-if="index === 0">{{ upload.filename }}</strong>
                  <div>{{ sheet.name }}</div>
                </th>
                <td>{{ SHEET_KIND_LABELS[sheet.kind] }}</td>
                <td class="is-num">{{ sheet.row_count }}</td>
                <td>
                  <span v-if="sheet.class_hint">{{ sheet.class_hint }}</span>
                  <select
                    v-else-if="sheetNeedsClass(sheet)"
                    class="app-input"
                    :aria-label="`${sheet.name} 对应班级`"
                    :value="sheetClassValue(upload.upload_id, sheet.name)"
                    @change="setSheetClass(upload.upload_id, sheet.name, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="">未选择班级</option>
                    <option v-for="className in rosterClasses" :key="className" :value="className">
                      {{ className }} 班
                    </option>
                  </select>
                  <span v-else-if="sheet.kind === 'options'" class="import-hint">按准考证号/姓名对应</span>
                  <span v-else>—</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>

      <section class="import-section" aria-labelledby="import-preview-title">
        <h2 id="import-preview-title">导入预览</h2>
        <p v-if="previewLoading" role="status" class="import-hint">正在核对成绩表…</p>
        <p v-else-if="previewError" class="import-warn" role="alert">{{ previewError }}</p>
        <template v-else-if="preview">
          <template v-if="blockIssues.length || warnIssues.length">
            <h3>问题</h3>
            <div class="import-issues">
            <p v-for="(issue, index) in blockIssues" :key="`b${index}`" class="import-issue import-issue--block" role="alert">
              {{ issueWhere(issue) }}{{ issue.message }}
            </p>
            <p v-for="(issue, index) in warnIssues" :key="`w${index}`" class="import-issue import-issue--warn">
              {{ issueWhere(issue) }}{{ issue.message }}
            </p>
            </div>
          </template>

          <template v-if="preview.diff && preview.diff.length">
            <h3>重新导入的变化</h3>
            <table class="app-table">
              <thead>
                <tr><th>学生</th><th>题</th><th>原分 → 新分</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="(entry, index) in preview.diff" :key="index">
                  <td>{{ studentNameOf.get(entry.student_id) ?? `学生 ${entry.student_id}` }}</td>
                  <td>{{ diffItemLabel(entry.item_id) }}</td>
                  <td class="is-num">{{ entry.old_score ?? '—' }} → {{ entry.new_score }}</td>
                  <td><span v-if="entry.teacher_edited" class="import-tag">导入后教师改过</span></td>
                </tr>
              </tbody>
            </table>
          </template>
          <label v-if="preview.has_results" class="import-radio import-replace">
            <input v-model="replaceExisting" type="checkbox">
            覆盖已有成绩（重新导入会替换本场上次导入的全部成绩）
          </label>

          <h3>学生对应</h3>
          <p class="import-hint">
            已对应 {{ preview.students.matched }} 人 ·
            缺考 {{ preview.students.absent.length }} 人 ·
            按班级跳过 {{ Object.values(preview.students.skipped_by_class).reduce((a, b) => a + b, 0) }} 行<template v-if="preview.students.skipped_rows">
              · 手动跳过 {{ preview.students.skipped_rows }} 行</template>
          </p>
          <p v-if="preview.students.absent.length" class="import-note">
            缺考：{{ preview.students.absent.map((entry) => entry.name).join('、') }}
          </p>
          <table v-if="unresolvedRows.length" class="app-table">
            <thead>
              <tr><th>表中姓名</th><th>工作表</th><th>行</th><th>对应学生</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in unresolvedRows" :key="row.row_key">
                <th scope="row">{{ row.name || '（空）' }}<small v-if="row.class"> · {{ row.class }} 班</small></th>
                <td>{{ row.sheet }}</td>
                <td class="is-num">{{ row.row }}</td>
                <td>
                  <select
                    class="app-input"
                    :aria-label="`${row.name} 对应学生`"
                    :value="overrideValue(row.row_key)"
                    @change="setOverride(row.row_key, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="">未选择</option>
                    <option
                      v-for="candidate in row.candidates"
                      :key="candidate.student_id"
                      :value="String(candidate.student_id)"
                    >{{ candidate.name }}（{{ candidate.class_name }}班）</option>
                    <option value="__skip__">跳过此行</option>
                  </select>
                </td>
              </tr>
            </tbody>
          </table>

          <template v-if="preview.groups.length">
            <h3>合并给分</h3>
            <p class="import-note">满分和0分的学生计入掌握度，中间分只计入总分和排名。</p>
            <table class="app-table">
              <thead>
                <tr><th>组</th><th>包含题目</th><th>满分</th><th>满分人数</th><th>0分人数</th><th>中间分人数</th></tr>
              </thead>
              <tbody>
                <tr v-for="group in preview.groups" :key="group.group_id">
                  <th scope="row">{{ group.label }}</th>
                  <td>{{ group.question_ids.map(questionNumber).join('、') }}</td>
                  <td class="is-num">{{ group.max_score }}</td>
                  <td class="is-num">{{ group.counts.full }}</td>
                  <td class="is-num">{{ group.counts.zero }}</td>
                  <td class="is-num">{{ group.counts.intermediate + group.counts.derived }}</td>
                </tr>
              </tbody>
            </table>
          </template>

          <h3>题目对应</h3>
          <p v-if="!attentionQuestions.length && !showAllMapping" class="import-note">
            {{ mappingQuestions.length }} 道题均按列导入，无需处理。
            <button type="button" class="import-link" @click="showAllMapping = true">显示全部 {{ mappingQuestions.length }} 题</button>
          </p>
          <template v-else>
            <p v-if="!showAllMapping && attentionQuestions.length" class="import-note">
              只显示需要处理的 {{ attentionQuestions.length }} 题。
              <button type="button" class="import-link" @click="showAllMapping = true">显示全部 {{ mappingQuestions.length }} 题</button>
            </p>
            <p v-else-if="showAllMapping && attentionQuestions.length" class="import-note">
              <button type="button" class="import-link" @click="showAllMapping = false">只看需要处理的 {{ attentionQuestions.length }} 题</button>
            </p>
          </template>
          <table v-if="visibleQuestions.length" class="app-table">
            <thead>
              <tr><th>题号</th><th>本场评分项</th><th>表中列</th><th>处理方式</th><th>满分</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="question in visibleQuestions"
                :key="question.question_id"
                :class="{ 'import-row--confirm': question.status === 'needs_confirm' }"
              >
                <th scope="row">{{ questionNumber(question.question_id) }}</th>
                <td>
                  <template v-if="question.choice === 'combined'">
                    并入 {{ question.group_label }}（合并给分）
                  </template>
                  <template v-else-if="question.items.length === 1">
                    整题
                  </template>
                  <template v-else>
                    {{ question.items.map((item) => partLabel(item.item_id)).join(' ') }}
                  </template>
                </td>
                <td>{{ question.columns.join('、') || '—' }}</td>
                <td>
                  <template v-if="question.choice === 'parts' || (question.choice === 'whole' && canChooseParts(question.columns))">
                    <label class="import-radio">
                      <input
                        type="radio"
                        :name="`part-${question.question_id}`"
                        :checked="question.choice === 'parts'"
                        @change="partChoices = { ...partChoices, [question.question_id]: 'parts' }"
                      >按小问
                    </label>
                    <label class="import-radio">
                      <input
                        type="radio"
                        :name="`part-${question.question_id}`"
                        :checked="question.choice === 'whole'"
                        @change="partChoices = { ...partChoices, [question.question_id]: 'whole' }"
                      >按整题
                    </label>
                    <p v-if="question.status === 'needs_confirm'" class="import-note">
                      表中列数与小问一致，已默认按小问；这些列也可能是老师自设的步骤分，请核对。
                    </p>
                  </template>
                  <template v-else>{{ CHOICE_LABELS[question.choice] ?? question.choice }}</template>
                </td>
                <td>
                  <div v-for="item in question.items" :key="item.item_id" class="import-max">
                    <span v-if="question.items.length > 1" class="import-part-tag">{{ partLabel(item.item_id) }}</span>
                    <input
                      v-if="question.choice !== 'combined'"
                      class="app-input import-max__input"
                      type="number"
                      min="0.5"
                      step="0.5"
                      :aria-label="`第 ${questionNumber(question.question_id)} 题${partLabel(item.item_id) === '整题' ? '' : partLabel(item.item_id)} 满分`"
                      :value="itemMaxInputs[item.item_id] ?? String(item.max_score)"
                      @change="itemMaxInputs = { ...itemMaxInputs, [item.item_id]: ($event.target as HTMLInputElement).value }"
                    >
                    <span v-else>{{ item.max_score }}</span>
                    <span
                      v-if="item.observed_max !== null"
                      class="import-observed"
                      :class="{ 'import-observed--over': item.observed_max > (itemMax[item.item_id] ?? item.max_score) }"
                    >表中最高 {{ item.observed_max }}</span>
                  </div>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="4">评分依据满分合计 / 表中最高分合计</td>
                <td class="is-num">{{ preview.proposed_rubric_total }} / {{ observedTotal }}</td>
              </tr>
            </tfoot>
          </table>
        </template>
      </section>

      <section class="import-section import-confirm" aria-labelledby="import-confirm-title">
        <h2 id="import-confirm-title">确认导入</h2>
        <FeedbackBanner v-if="confirmResult" tone="success"
          :description="`已导入 ${confirmResult.imported_students} 名学生、${confirmResult.teacher_final_items} 个评分项${confirmResult.absent_students ? `，缺考 ${confirmResult.absent_students} 人` : ''}。`" />
        <div v-if="confirmResult" class="import-links">
          <a href="/results">去成绩中心查看 →</a>
          <a href="/knowledge-overview">去学情总览 →</a>
        </div>
        <FeedbackBanner v-if="staleNotice" tone="warning" :description="staleNotice" />
        <FeedbackBanner v-if="confirmError" tone="error" :description="confirmError" />
        <div class="import-confirm__bar">
          <AppButton
            variant="primary"
            :disabled="!canConfirm"
            @click="confirm"
          >{{ confirming ? '正在导入…' : '确认导入' }}</AppButton>
          <span v-if="confirmBlockedReason && !confirming" class="import-note">{{ confirmBlockedReason }}</span>
        </div>
      </section>
    </template>
  </article>
</template>

<style scoped>
.import-scores { padding-bottom: var(--space-8); }
.import-section { margin: 0 var(--space-6) var(--space-5); }
.import-section h2 { margin: 0 0 var(--space-3); }
.import-section h3 { margin: var(--space-4) 0 var(--space-2); }
.import-hint { color: var(--color-text-secondary); margin: 0 0 var(--space-2); }
.import-note { color: var(--color-text-secondary); font-size: var(--font-size-caption); margin: 4px 0 0; }
.import-warn { color: #a05a12; margin: var(--space-2) 0 0; }
.import-upload-bar { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); }
.import-issues { margin-bottom: var(--space-3); }
.import-issue { margin: 0 0 4px; padding: 6px 10px; border-radius: 6px; font-size: var(--font-size-dense); }
.import-issue--block { background: #fdecec; color: #a33; }
.import-issue--warn { background: #fdf3df; color: #8a5a10; }
.import-radio { display: inline-flex; align-items: center; gap: 4px; margin-right: var(--space-3); white-space: nowrap; }
.import-replace { display: flex; margin-top: var(--space-3); }
.import-row--confirm { background: #fdf3df; }
.import-max { display: flex; align-items: center; gap: 6px; }
.import-max__input { width: 76px; }
.import-observed { font-size: var(--font-size-caption); color: var(--color-text-secondary); }
.import-observed--over { color: #a33; font-weight: var(--font-weight-semibold); }
.import-tag { background: #fdf3df; color: #8a5a10; border-radius: 4px; padding: 1px 6px; font-size: var(--font-size-caption); }
.import-links { display: flex; gap: var(--space-4); margin: var(--space-2) 0 var(--space-3); }
.import-file-input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.import-link { border: none; background: none; padding: 0; color: var(--color-primary); cursor: pointer; font: inherit; }
.import-link:hover { text-decoration: underline; }
.import-part-tag { align-self: center; min-width: 2.5em; color: var(--color-text-muted); }
.import-confirm {
  position: sticky;
  bottom: 0;
  z-index: 10;
  margin-bottom: 0;
  padding: var(--space-3) var(--space-4);
  border-top: var(--border-width-thin) solid var(--color-border-subtle);
  background: var(--color-bg-surface);
}
.import-confirm__bar { display: flex; align-items: center; gap: var(--space-3); margin-top: var(--space-2); }
</style>
