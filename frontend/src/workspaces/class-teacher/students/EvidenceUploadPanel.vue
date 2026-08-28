<script lang="ts">
export const TERM_OPTIONS = [
  { value: '七上', grade: '七年级', term: '上学期' },
  { value: '七下', grade: '七年级', term: '下学期' },
  { value: '八上', grade: '八年级', term: '上学期' },
  { value: '八下', grade: '八年级', term: '下学期' },
  { value: '九上', grade: '九年级', term: '上学期' },
  { value: '九下', grade: '九年级', term: '下学期' },
] as const
export type TermValue = (typeof TERM_OPTIONS)[number]['value']

// 学年按 8 月分界：8 月及以后属于当年起始的学年
export function academicYearOf(dateText: string): string {
  const year = Number(dateText.slice(0, 4))
  const month = Number(dateText.slice(5, 7))
  if (!Number.isFinite(year) || !Number.isFinite(month)) return ''
  return month >= 8 ? `${year}-${year + 1}` : `${year - 1}-${year}`
}

export const EXAM_NATURE_OPTIONS = ['期中考试', '期末考试', '其他'] as const
export type ExamNature = (typeof EXAM_NATURE_OPTIONS)[number]

// 登记日期只作参考：表内有「考试日期」列取第一条非空值，否则用上传当天
export function examDateOf(headers: string[], rows: Array<Record<string, string>>): string {
  const column = headers.find((header) => /考试日期/.test(header))
  if (!column) return ''
  for (const row of rows) {
    const raw = String(row[column] ?? '').trim()
    const match = raw.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/)
    if (match) {
      const [, year, month, day] = match
      return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    }
  }
  return ''
}

// 考试性质按文件名/标题推断，推不出归为「其他」
export function guessExamNature(source: string): ExamNature {
  if (/期中/.test(source)) return '期中考试'
  if (/期末/.test(source)) return '期末考试'
  return '其他'
}
</script>

<script setup lang="ts">
import { computed, ref } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import { ApiError } from '@/api/errors'
import { supportApi, type SpreadsheetPreview, type SupportSubject } from '../api/support'

const MAX_FILE_BYTES = 10 * 1024 * 1024
const PREVIEW_TABLE_LIMIT = 50

interface SubjectColumn {
  name: string
  scoreColumn: string
  classRankColumn: string
  gradeRankColumn: string
  gradeLevelColumn: string
  isTotal: boolean
}

interface RowCheck {
  row: Record<string, string>
  name: string
  studentCode: string
  subject: SupportSubject | null
  scores: Record<string, number | null>
  classRanks: Record<string, number | null>
  gradeRanks: Record<string, number | null>
  gradeLevels: Record<string, string>
  status: 'matched' | 'create' | 'empty' | 'no_score'
}

const open = ref(false)
const busy = ref(false)
const message = ref('')
const errorText = ref('')
const roster = ref<SupportSubject[]>([])
const fileName = ref('')
const preview = ref<SpreadsheetPreview | null>(null)
const sheetName = ref('')
const confirmed = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
// 多选排队：一份解析或登记失败不影响其余文件
interface QueueItem { name: string; status: 'waiting' | 'active' | 'done' | 'failed'; error: string }
const queue = ref<QueueItem[]>([])
const queueFiles = new Map<string, File>()
const currentFile = ref<File | null>(null)

const title = ref('')
const subjectName = ref('')
const occurredOn = ref('')
const maxScore = ref('')
const termLabel = ref<TermValue | ''>('')
const examNature = ref<ExamNature>('其他')
const gradeSize = ref('')
const nameColumn = ref('')
const studentCodeColumn = ref('')
const subjects = ref<SubjectColumn[]>([])
const singleFallback = ref(false)

const rosterByName = computed(() => {
  const map = new Map<string, SupportSubject>()
  for (const subject of roster.value) {
    const key = normalizeName(subject.display_name)
    if (!map.has(key)) map.set(key, subject)
  }
  return map
})
const rowChecks = computed<RowCheck[]>(() => {
  if (!preview.value || !nameColumn.value || !subjects.value.length) return []
  return preview.value.rows.map((row) => {
    const name = String(row[nameColumn.value] ?? '').trim()
    const studentCode = studentCodeColumn.value ? String(row[studentCodeColumn.value] ?? '').trim() : ''
    const subject = name ? (rosterByName.value.get(normalizeName(name)) ?? null) : null
    const scores: Record<string, number | null> = {}
    const classRanks: Record<string, number | null> = {}
    const gradeRanks: Record<string, number | null> = {}
    const gradeLevels: Record<string, string> = {}
    for (const sub of subjects.value) {
      scores[sub.scoreColumn] = parseScore(row[sub.scoreColumn])
      classRanks[sub.scoreColumn] = sub.classRankColumn ? parseRank(row[sub.classRankColumn]) : null
      gradeRanks[sub.scoreColumn] = sub.gradeRankColumn ? parseRank(row[sub.gradeRankColumn]) : null
      gradeLevels[sub.scoreColumn] = sub.gradeLevelColumn ? String(row[sub.gradeLevelColumn] ?? '').trim() : ''
    }
    const anyScore = Object.values(scores).some((value) => value !== null)
    const status: RowCheck['status'] = !name ? 'empty' : !anyScore ? 'no_score' : subject ? 'matched' : 'create'
    return { row, name, studentCode, subject, scores, classRanks, gradeRanks, gradeLevels, status }
  })
})
const readyRows = computed(() => rowChecks.value.filter((item) => item.status === 'matched' || item.status === 'create'))
const createCount = computed(() => rowChecks.value.filter((item) => item.status === 'create').length)
const noScoreCount = computed(() => rowChecks.value.filter((item) => item.status === 'no_score' || item.status === 'empty').length)
const hasGradeRank = computed(() => subjects.value.some((sub) => sub.gradeRankColumn))
const parsedGradeSize = computed(() => {
  const parsed = Number(gradeSize.value.trim())
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
})
// 新档案的班级归属：优先取已建档学生中最多的班级，再从文件名解析
const newProfileClass = computed(() => {
  const counts = new Map<string, number>()
  for (const subject of roster.value) {
    if (subject.class_label) counts.set(subject.class_label, (counts.get(subject.class_label) ?? 0) + 1)
  }
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]
  return top?.[0] ?? guessClassLabel(fileName.value)
})
const canConfirm = computed(() => Boolean(
  preview.value && readyRows.value.length && subjects.value.length
  && title.value.trim() && occurredOn.value
  && termLabel.value
  && (!singleFallback.value || subjectName.value.trim()),
))

function normalizeName(value: string): string {
  return value.replace(/\s+/g, '')
}
function parseScore(value: string | undefined): number | null {
  if (value === undefined || value === null || String(value).trim() === '') return null
  const parsed = Number(String(value).trim())
  return Number.isFinite(parsed) ? parsed : null
}
function parseRank(value: string | undefined): number | null {
  if (value === undefined || value === null || String(value).trim() === '') return null
  const parsed = Number(String(value).trim())
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : null
}
function guessTermLabel(source: string): TermValue | '' {
  let grade = ''
  let half = ''
  // 先试"年级+上/下"连写（如"七年级上期中""七下期中"），能同时确定年级与学期
  const joint = source.match(/(初一|初二|初三|[七八九])(?:年级)?(上|下)/)
  if (joint && joint[1] && joint[2]) {
    const gradeMap: Record<string, string> = { 初一: '七', 初二: '八', 初三: '九' }
    grade = gradeMap[joint[1]] ?? joint[1]
    half = joint[2]
  } else {
    if (/九|初三/.test(source)) grade = '九'
    else if (/八|初二/.test(source)) grade = '八'
    else if (/七|初一/.test(source)) grade = '七'
    if (/第二学期|下学期|下册/.test(source)) half = '下'
    else if (/第一学期|上学期|上册/.test(source)) half = '上'
  }
  const combined = `${grade}${half}`
  return (TERM_OPTIONS.some((option) => option.value === combined) ? combined : '') as TermValue | ''
}
// 文件名推不出学期时，看表内「年级」「学期」列的首条非空值
function guessTermFromTable(value: SpreadsheetPreview): TermValue | '' {
  const gradeColumn = value.headers.find((header) => /年级/.test(header))
  const termColumn = value.headers.find((header) => /学期/.test(header))
  if (!gradeColumn && !termColumn) return ''
  for (const row of value.rows) {
    const gradeText = gradeColumn ? String(row[gradeColumn] ?? '').trim() : ''
    const termText = termColumn ? String(row[termColumn] ?? '').trim() : ''
    const guessed = guessTermLabel(`${gradeText}${termText}`)
    if (guessed) return guessed
  }
  return ''
}
function guessRankColumn(headers: string[], scoreHeader: string, kind: '班次' | '校次'): string {
  const prefix = scoreHeader.replace(/[-—_\s]*(总?分|成绩|分数|得分)$/, '')
  if (prefix && prefix !== scoreHeader) {
    const grouped = headers.find((header) => header === `${prefix}-${kind}`)
    if (grouped) return grouped
  }
  return headers.find((header) => header.endsWith(kind)) ?? ''
}
// 等级列按原始科目名精确匹配：优先「科目-等级」，兼容「科目等级」；
// 在英语变体归一前绑定，隐藏列的等级不会误挂到「英语」上。
function guessGradeLevelColumn(headers: string[], baseName: string): string {
  if (!baseName) return ''
  const grouped = headers.find((header) => header === `${baseName}-等级`)
  if (grouped) return grouped
  return headers.find((header) => header === `${baseName}等级`) ?? ''
}
function guessClassLabel(source: string): string {
  const gradeMap: Record<string, string> = { 七: '七', 八: '八', 九: '九', 初一: '七', 初二: '八', 初三: '九' }
  const match = source.match(/(初一|初二|初三|[七八九])(?:年级)?(\d+)班/)
  if (!match || !match[1] || !match[2]) return ''
  return `${gradeMap[match[1]] ?? match[1]}${match[2]}班`
}
function numericRatio(header: string, rows: Array<Record<string, string>>): number {
  if (!rows.length) return 0
  return rows.filter((row) => parseScore(row[header]) !== null).length / rows.length
}
function detectColumns(value: SpreadsheetPreview): void {
  const headers = value.headers
  nameColumn.value = headers.find((header) => /姓名|名字/.test(header)) ?? ''
  studentCodeColumn.value = headers.find((header) => /准考证号|学号|考号/.test(header)) ?? ''
  // 多学科宽表：所有"科目-得分/成绩/分数"列各自成为一个科目，同组班次/校次自动绑定
  const found: SubjectColumn[] = []
  for (const header of headers) {
    const match = header.match(/^(.+?)[-—_](得分|成绩|分数)$/)
    if (!match) continue
    const name = (match[1] ?? '').trim()
    if (!name || numericRatio(header, value.rows) === 0) continue
    found.push({
      name,
      scoreColumn: header,
      classRankColumn: headers.includes(`${name}-班次`) ? `${name}-班次` : '',
      gradeRankColumn: headers.includes(`${name}-校次`) ? `${name}-校次` : '',
      gradeLevelColumn: guessGradeLevelColumn(headers, name),
      isTotal: name === '总分',
    })
  }
  // 「英语笔试加听力/听说」在同表没有单独「英语」列时归一为「英语」；已有「英语」列则保持原名，交给后端消歧
  const ENGLISH_ALIASES = ['英语笔试加听力', '英语笔试加听说']
  if (found.length && !found.some((item) => item.name === '英语')) {
    for (const item of found) {
      if (ENGLISH_ALIASES.includes(item.name)) item.name = '英语'
    }
  }
  if (found.length) {
    subjects.value = found
    singleFallback.value = false
    subjectName.value = ''
    return
  }
  // 回退：简单单列表（如"姓名,数学成绩"），学科名从列头剥后缀，剥不掉由教师填写
  singleFallback.value = true
  const candidates = headers.filter((header) => header !== nameColumn.value)
  let scoreHeader = candidates.find((header) => /成绩|得分|分数|分$/.test(header) && numericRatio(header, value.rows) > 0.5) ?? ''
  if (!scoreHeader) {
    let bestRatio = 0
    for (const header of candidates) {
      const ratio = numericRatio(header, value.rows)
      if (ratio > bestRatio) { scoreHeader = header; bestRatio = ratio }
    }
    if (bestRatio <= 0.5) scoreHeader = ''
  }
  if (!scoreHeader) {
    subjects.value = []
    subjectName.value = ''
    return
  }
  const name = scoreHeader.replace(/[-—_\s]*(总?分|成绩|分数|得分)$/, '')
  subjects.value = [{
    name,
    scoreColumn: scoreHeader,
    classRankColumn: guessRankColumn(headers, scoreHeader, '班次'),
    gradeRankColumn: guessRankColumn(headers, scoreHeader, '校次'),
    gradeLevelColumn: guessGradeLevelColumn(headers, name),
    isTotal: false,
  }]
  subjectName.value = name
}
function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

async function toggle(): Promise<void> {
  open.value = !open.value
  message.value = ''
  errorText.value = ''
  if (open.value && !roster.value.length) {
    try {
      roster.value = await supportApi.listSubjects()
    } catch (error) {
      errorText.value = errorMessage(error, '学生名单暂时无法读取，请稍后重试。')
    }
  }
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  if (!files.length || busy.value) return
  errorText.value = ''
  message.value = ''
  confirmed.value = false
  queueFiles.clear()
  // 逐个校验入队：不合格的文件只标记失败，不影响其余文件
  queue.value = files.map((file) => {
    if (!/\.(csv|xlsx)$/i.test(file.name)) {
      return { name: file.name, status: 'failed' as const, error: '只支持 CSV 或 XLSX 成绩文件' }
    }
    if (file.size > MAX_FILE_BYTES) {
      return { name: file.name, status: 'failed' as const, error: '成绩文件不能超过 10 MB' }
    }
    queueFiles.set(file.name, file)
    return { name: file.name, status: 'waiting' as const, error: '' }
  })
  startNextFile()
}

function queueItem(name: string): QueueItem | undefined {
  return queue.value.find((item) => item.name === name)
}

function startNextFile(): void {
  const next = queue.value.find((item) => item.status === 'waiting')
  if (!next) {
    if (queue.value.length && queue.value.every((item) => item.status === 'failed')) {
      errorText.value = queue.value[0]?.error ?? '成绩文件不可用。'
    }
    return
  }
  const file = queueFiles.get(next.name)
  if (!file) return
  next.status = 'active'
  preview.value = null
  const reader = new FileReader()
  reader.onload = () => {
    const base64 = String(reader.result ?? '').split(',')[1] ?? ''
    currentFile.value = file
    void loadPreview(file.name, base64, null)
  }
  reader.onerror = () => {
    next.status = 'failed'
    next.error = '成绩文件读取失败'
    startNextFile()
  }
  reader.readAsDataURL(file)
}

async function loadPreview(name: string, contentBase64: string, sheet: string | null): Promise<void> {
  busy.value = true
  errorText.value = ''
  try {
    const value = await supportApi.previewSpreadsheet(name, contentBase64, sheet)
    preview.value = value
    fileName.value = name
    sheetName.value = value.selected_sheet
    if (!sheet) {
      title.value = name.replace(/\.(csv|xlsx)$/i, '')
      // 表内有「考试日期」列取表内日期，否则登记为上传当天
      occurredOn.value = examDateOf(value.headers, value.rows) || new Date().toISOString().slice(0, 10)
      detectColumns(value)
      termLabel.value = guessTermLabel(name) || guessTermFromTable(value)
      examNature.value = guessExamNature(name)
      maxScore.value = ''
    }
  } catch (error) {
    preview.value = null
    // 解析失败只标记当前文件，继续处理队列中的下一份
    const item = queueItem(name)
    if (item) {
      item.status = 'failed'
      item.error = errorMessage(error, '成绩表解析失败')
    }
    errorText.value = `${name}：${errorMessage(error, '成绩表解析失败，请核对文件后重试。')}`
    startNextFile()
  } finally {
    busy.value = false
  }
}

async function changeSheet(): Promise<void> {
  if (!preview.value || sheetName.value === preview.value.selected_sheet) return
  const file = currentFile.value
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const base64 = String(reader.result ?? '').split(',')[1] ?? ''
    void loadPreview(file.name, base64, sheetName.value)
  }
  reader.onerror = () => { errorText.value = '成绩文件读取失败，请重新选择。' }
  reader.readAsDataURL(file)
}

async function confirm(): Promise<void> {
  if (!canConfirm.value || busy.value) return
  const parsedMax = maxScore.value.trim() === '' ? null : Number(maxScore.value)
  if (parsedMax !== null && !Number.isFinite(parsedMax)) {
    errorText.value = '满分必须是数字；不确定时可以留空。'
    return
  }
  const termOption = TERM_OPTIONS.find((option) => option.value === termLabel.value)
  if (!termOption) {
    errorText.value = '请先选择学期类别（如：七下），确认后再登记。'
    return
  }
  busy.value = true
  errorText.value = ''
  const trimmedTitle = title.value.trim()
  const examType = examNature.value
  const session = {
    title: trimmedTitle,
    academic_year: academicYearOf(occurredOn.value),
    term: termOption.term,
    grade: termOption.grade,
    exam_type: examType,
    comparison_series: 'class-regular',
    occurred_on: occurredOn.value,
    source_reference: fileName.value,
  }
  // 每个科目一个 assessment：校次列绑定的科目按年级口径，否则按班级口径；
  // 校次口径固定为"同一届年级"，跨年级（如七下期末→八上期中）才能按学期链条连续比较
  const assessments = subjects.value.map((sub) => {
    const primaryIsGrade = Boolean(sub.gradeRankColumn)
    const rows = readyRows.value.filter((item) => item.scores[sub.scoreColumn] !== null)
    return {
      title: trimmedTitle,
      subject_name: singleFallback.value ? (subjectName.value.trim() || sub.name) : sub.name,
      occurred_on: occurredOn.value,
      max_score: singleFallback.value ? parsedMax : null,
      measure_role: sub.isTotal ? 'total_score' : 'subject_score',
      rank_scope: primaryIsGrade ? 'grade' : 'class',
      participant_count: primaryIsGrade ? parsedGradeSize.value : (readyRows.value.length || null),
      assessment_nature: examType,
      cohort_key: primaryIsGrade ? 'same-grade-cohort' : (newProfileClass.value || '本班'),
      ranking_rule_version: 'school-export-v1',
      session,
      results: rows.map((item) => ({
        ...(item.subject
          ? { subject_id: item.subject.subject_id }
          : {
            subject_identity: {
              display_name: item.name,
              class_label: newProfileClass.value || null,
              student_code: item.studentCode || null,
            },
          }),
        result_state: 'normal',
        score: item.scores[sub.scoreColumn],
        rank: primaryIsGrade ? item.gradeRanks[sub.scoreColumn] : item.classRanks[sub.scoreColumn],
        class_rank: primaryIsGrade ? item.classRanks[sub.scoreColumn] : null,
        ...(item.gradeLevels[sub.scoreColumn] ? { grade_level: item.gradeLevels[sub.scoreColumn] } : {}),
      })),
    }
  }).filter((assessment) => assessment.results.length)
  if (!assessments.length) {
    busy.value = false
    errorText.value = '没有可登记的科目成绩，请核对文件。'
    return
  }
  try {
    const result = await supportApi.confirmEvidence({
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      source_label: fileName.value,
      assessments,
    })
    confirmed.value = true
    const item = queueItem(fileName.value)
    if (item) item.status = 'done'
    const remaining = queue.value.filter((entry) => entry.status === 'waiting').length
    if (result.duplicate === true) {
      message.value = '这份成绩表之前已经登记过，本次没有重复写入。'
    } else {
      const results = typeof result.created_results === 'number' ? result.created_results : readyRows.value.length
      const created = createCount.value ? `，其中新建 ${createCount.value} 个空档案` : ''
      message.value = `已登记 ${results} 条学生成绩（${assessments.length} 个科目，场次：${title.value.trim()}）${created}。打开任一学生的学业证据即可查看。`
    }
    if (remaining) message.value += ` 队列中还有 ${remaining} 份待登记。`
  } catch (error) {
    errorText.value = errorMessage(error, '成绩没有登记成功；现有学业证据未改变。若其他页面已更新，请刷新后再核对。')
  } finally {
    busy.value = false
  }
}

function resetUpload(): void {
  preview.value = null
  fileName.value = ''
  message.value = ''
  errorText.value = ''
  confirmed.value = false
  if (queue.value.some((item) => item.status === 'waiting')) {
    // 队列还有下一份：直接继续登记
    startNextFile()
    return
  }
  discardQueue()
}

function discardQueue(): void {
  preview.value = null
  fileName.value = ''
  message.value = ''
  errorText.value = ''
  confirmed.value = false
  queue.value = []
  queueFiles.clear()
  currentFile.value = null
  if (fileInput.value) fileInput.value.value = ''
}
</script>

<template>
  <section class="evidence-upload">
    <header>
      <div><p>全班成绩登记</p><h2>大考成绩表</h2></div>
      <AppButton variant="secondary" @click="toggle">{{ open ? '收起上传' : '上传大考成绩表' }}</AppButton>
    </header>
    <p class="intro">上传 CSV 或 Excel 成绩表，教师核对预览并确认后，才会写入学生的学业证据；原始文件不在服务器保留。</p>
    <div v-if="open" class="upload-body">
      <p v-if="errorText" class="error" role="alert">{{ errorText }}</p>
      <template v-if="!confirmed">
        <label class="file-picker"><span>选择成绩表（可多选，逐份核对登记）</span><input ref="fileInput" type="file" accept=".csv,.xlsx" multiple :disabled="busy" @change="onFileChange"></label>
        <p class="hint">支持 CSV / Excel（.xlsx），不超过 10 MB、5000 行数据。一次可选择多份，一份失败不影响其余。</p>
        <ul v-if="queue.length > 1" class="queue">
          <li v-for="item in queue" :key="item.name" :class="`queue__item queue__item--${item.status}`">
            {{ item.name }}<template v-if="item.status === 'waiting'">：等待中</template><template v-else-if="item.status === 'active'">：核对中</template><template v-else-if="item.status === 'done'">：已登记</template><template v-else>：失败（{{ item.error }}）</template>
          </li>
        </ul>
        <template v-if="preview">
          <div class="session-form">
            <label><span>考试名称</span><input v-model="title" maxlength="500"></label>
            <div class="readonly-field"><span>登记日期：{{ occurredOn }}</span><small>日期仅作登记参考，场次顺序按学期和期中期末排列。</small></div>
            <label><span>学期类别（必选）</span><select v-model="termLabel"><option value="" disabled>请选择</option><option v-for="option in TERM_OPTIONS" :key="option.value" :value="option.value">{{ option.value }}</option></select></label>
            <label><span>考试性质</span><select v-model="examNature"><option v-for="option in EXAM_NATURE_OPTIONS" :key="option" :value="option">{{ option }}</option></select></label>
            <label v-if="preview.sheet_names.length > 1"><span>工作表</span><select v-model="sheetName" @change="changeSheet"><option v-for="name in preview.sheet_names" :key="name" :value="name">{{ name }}</option></select></label>
            <label v-if="singleFallback"><span>学科名称</span><input v-model="subjectName" maxlength="120" placeholder="如：数学"></label>
            <label v-if="singleFallback"><span>满分（可留空）</span><input v-model="maxScore" inputmode="decimal"></label>
            <label v-if="hasGradeRank"><span>年级人数（用于校次进退步）</span><input v-model="gradeSize" inputmode="numeric" placeholder="如：320"></label>
          </div>
          <p v-if="subjects.length" class="stats">已自动识别 {{ subjects.length }} 个科目：{{ subjects.map((sub) => sub.name).join('、') }}；姓名列：{{ nameColumn || '未找到' }}；{{ hasGradeRank ? '班次、校次列已自动绑定。' : '未找到班次/校次列，只登记分数。' }}</p>
          <p v-else class="error">没有识别到"科目-得分"格式的分数列，请核对文件表头。</p>
          <p v-if="preview.truncated" class="error">表格超过 5000 行，只解析了前 5000 行；请拆分后再上传。</p>
          <p class="stats">共识别 {{ rowChecks.length }} 行：可登记 {{ readyRows.length }} 人<template v-if="createCount">（其中 {{ createCount }} 人没有档案，将新建空档案并登记）</template>；{{ noScoreCount }} 行没有有效分数，不登记。</p>
          <table>
            <thead><tr><th>姓名</th><th v-for="sub in subjects" :key="sub.scoreColumn">{{ sub.name }}</th><th>核对结果</th></tr></thead>
            <tbody>
              <tr v-for="(item, index) in rowChecks.slice(0, PREVIEW_TABLE_LIMIT)" :key="index" :class="item.status">
                <td>{{ item.name || '（空）' }}</td>
                <td v-for="sub in subjects" :key="sub.scoreColumn">{{ item.scores[sub.scoreColumn] ?? '' }}</td>
                <td v-if="item.status==='matched'">登记给 {{ item.subject!.display_name }}</td>
                <td v-else-if="item.status==='create'">新建空档案并登记</td>
                <td v-else-if="item.status==='empty'">姓名为空，不登记</td>
                <td v-else>没有有效分数，不登记</td>
              </tr>
            </tbody>
          </table>
          <p v-if="rowChecks.length > PREVIEW_TABLE_LIMIT" class="hint">仅展示前 {{ PREVIEW_TABLE_LIMIT }} 行用于核对；确认登记时按全部识别行处理。</p>
          <p v-if="!termLabel" class="error">请先选择学期类别（如：七下）；学期会用于相邻考试的排名变化对比。</p>
          <p v-else-if="hasGradeRank && !parsedGradeSize" class="hint">未填年级人数：校次只记录名次，相邻考试的进退步不会计算。</p>
          <footer><span>确认后写入学业证据；没有档案的学生会先建空档案再登记，请核对上方"新建档案"人数。缺考、免考等特殊情况请在学生档案中单独说明。</span><AppButton variant="primary" :disabled="!canConfirm" :loading="busy" loading-label="正在登记" @click="confirm">核对无误，确认登记</AppButton></footer>
        </template>
        <p v-else-if="busy" class="hint">正在解析成绩表…</p>
      </template>
      <template v-else>
        <p class="message" role="status">{{ message }}</p>
        <footer><AppButton variant="secondary" @click="resetUpload">{{ queue.some((item) => item.status === 'waiting') ? '登记下一份' : '继续上传下一份' }}</AppButton><AppButton variant="ghost" @click="discardQueue(); open = false">完成，收起</AppButton></footer>
      </template>
    </div>
  </section>
</template>

<style scoped>
.evidence-upload{overflow:hidden;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}header{display:flex;justify-content:space-between;align-items:end;padding:var(--space-5);border-bottom:1px solid var(--border)}header p{margin:0 0 2px;color:var(--primary);font-size:var(--font-size-caption);font-weight:700;letter-spacing:.07em}h2{margin:0;font-size:var(--font-size-h2)}.intro{margin:0;padding:var(--space-3) var(--space-5);color:var(--color-text-secondary);font-size:var(--font-size-dense)}.upload-body{display:grid;gap:var(--space-3);padding:0 var(--space-5) var(--space-5)}.error{margin:0;color:var(--destructive,#b03a2e);font-size:var(--font-size-dense)}.message{margin:0;color:var(--primary);font-size:var(--font-size-dense)}.hint{margin:0;color:var(--muted-foreground);font-size:var(--font-size-dense)}.file-picker{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}.file-picker input{min-height:38px;padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}.session-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:var(--space-3);padding:var(--space-3);border:1px solid var(--color-border-subtle);border-radius:var(--radius);background:var(--muted)}.session-form label{display:grid;gap:var(--space-1);margin:0;font-size:var(--font-size-dense);font-weight:650}.session-form input,.session-form select{min-height:38px;padding:0 var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);font:inherit}.stats{margin:0;font-size:var(--font-size-dense)}table{width:100%;border-collapse:collapse;font-size:var(--font-size-dense)}th,td{padding:var(--space-1) var(--space-2);border-bottom:1px solid var(--color-border-subtle);text-align:left}tr.create td{background:var(--accent);color:#9b6a2b}tr.no_score td{color:var(--muted-foreground)}footer{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3)}footer>span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.queue{display:grid;gap:var(--space-1);margin:0;padding:0;list-style:none;font-size:var(--font-size-dense)}.queue__item--failed{color:var(--destructive,#b03a2e)}.queue__item--done{color:var(--primary)}.readonly-field{display:grid;gap:var(--space-1);font-size:var(--font-size-dense);font-weight:650}.readonly-field small{color:var(--color-text-secondary);font-weight:400}
</style>
