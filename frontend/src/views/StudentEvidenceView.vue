<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { fetchGraphEvidence, type GraphEvidenceItem, type GraphQueryInput } from '../api/graph'
import {
  knowledgeLeafLabel,
  questionBankApi,
  type QuestionBankDetail,
  type QuestionBankTagType,
} from '../api/question-bank'
import {
  fetchStudentExamResults,
  type StudentExamResultItem,
  type StudentExamResultSession,
  type StudentSummary,
} from '../api/students'
import AppButton from '../components/design-system/AppButton.vue'
import QuestionContentRenderer from '../components/question-bank/QuestionContentRenderer.vue'
import { loadEvidenceScope, semesterEvidenceQuery } from '../features/evidence-scope/session'
import { useSessionStore } from '../stores/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'

interface KnowledgeRow {
  key: string
  sessionId: number
  studentId: number
  studentName: string
  studentCode: string
  questionId: string
  bankQuestionId: number | null
  scoreAwarded: number
  fullScore: number
  reason: string
  evidenceUrl: string
  assessment: string
}

interface KnowledgeSessionGroup {
  sessionId: number
  sessionName: string
  items: KnowledgeRow[]
}

interface QuestionGroup {
  questionId: string
  bankQuestionId: number | null
  rows: KnowledgeRow[]
}

interface QuestionSessionGroup {
  sessionId: number
  sessionName: string
  questions: QuestionGroup[]
}

const route = useRoute()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()

const studentId = computed(() => String(route.params.studentId ?? ''))
const knowledgeKey = computed(() => typeof route.query.knowledge === 'string'
  ? route.query.knowledge.trim()
  : '')
const knowledgeLabel = computed(() => typeof route.query.klabel === 'string'
  ? route.query.klabel
  : '')
const groupMode = computed(() => route.query.mode === 'questions' && knowledgeKey.value !== '')
const knowledgeMode = computed(() => knowledgeKey.value !== '' && !groupMode.value)
const backTarget = computed(() => ({
  name: 'training',
  query: { mode: route.query.from === 'student' ? 'student' : 'chapter' },
}))
const backLabel = computed(() => route.query.from === 'student' ? '返回按学生训练' : '返回按章节训练')

const student = ref<StudentSummary | null>(null)
const sessions = ref<StudentExamResultSession[]>([])
const totalSessions = ref(0)
const page = ref(1)
const totalPages = ref(0)
const onlyDeducted = ref(true)
const loadState = ref<'loading' | 'ready' | 'error'>('loading')
const loadingMore = ref(false)
const previewUrl = ref('')
const previewDialog = ref<HTMLDialogElement | null>(null)
const knowledgeSessions = ref<KnowledgeSessionGroup[]>([])
const questionSessions = ref<QuestionSessionGroup[]>([])
const knowledgeStudentName = ref('')
const groupStudentCount = ref(0)
const expandedQuestions = ref<string[]>([])
let controller: AbortController | null = null

function formatDate(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('zh-CN')
}

function formatScore(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function sessionDate(session: StudentExamResultSession): string {
  return formatDate(session.graded_at ?? session.exam_created_at)
}

function reasonText(item: StudentExamResultItem): string {
  return item.deduction_reason || item.error_summary || '—'
}

async function load(nextPage: number): Promise<void> {
  controller?.abort()
  const active = new AbortController()
  controller = active
  if (nextPage === 1) loadState.value = 'loading'
  else loadingMore.value = true
  try {
    const response = await fetchStudentExamResults(studentId.value, {
      onlyDeducted: onlyDeducted.value,
      page: nextPage,
      pageSize: 10,
      curriculumVolumeId: curriculumScope.selectedVolumeId ?? '',
    }, active.signal)
    student.value = response.student
    sessions.value = nextPage === 1 ? response.sessions : [...sessions.value, ...response.sessions]
    totalSessions.value = response.total_sessions
    page.value = response.page
    totalPages.value = response.total_pages
    loadState.value = 'ready'
  } catch {
    if (active.signal.aborted) return
    if (nextPage === 1) loadState.value = 'error'
  } finally {
    if (controller === active) controller = null
    loadingMore.value = false
  }
}

// 场次排序：按考试列表的创建时间由新到旧，列表外的场次数值大的（通常是新的）排前。
function sessionOrderIndex(): Map<number, number> {
  const order = new Map<number, number>()
  ;[...sessionStore.sessions]
    .sort((left, right) => (right.created_at ?? '').localeCompare(left.created_at ?? ''))
    .forEach((session, index) => order.set(session.id, index))
  return order
}

function toRow(item: GraphEvidenceItem): KnowledgeRow {
  return {
    key: `${item.session_id}:${item.student_id}:${item.question_id}`,
    sessionId: item.session_id,
    studentId: item.student_id,
    studentName: item.student_name,
    studentCode: item.student_code,
    questionId: item.question_id,
    bankQuestionId: item.bank_question_id ?? null,
    scoreAwarded: item.score_awarded,
    fullScore: item.full_score,
    reason: item.deduction_reason ?? '',
    evidenceUrl: item.evidence_url ?? '',
    assessment: item.assessment?.reason === 'blank_or_no_valid_work'
      ? '空白或无有效作答：未据此判定具体知识失败'
      : item.assessment?.reason === 'assessment_needs_review'
        ? '作答待核查：暂不计入掌握度'
      : item.assessment?.granularity === 'part'
      ? `小问综合表现，内部归因有限 · 难度 ${item.assessment.part_difficulty ?? '尚未细分'}${item.assessment.part_difficulty == null ? '' : '/10（估计）'}`
      : '整题标签关联，细点归属尚未整理',
  }
}

function sortSessions<T extends { sessionId: number }>(groups: T[]): T[] {
  const order = sessionOrderIndex()
  return groups.sort((left, right) => {
    const leftIndex = order.get(left.sessionId)
    const rightIndex = order.get(right.sessionId)
    if (leftIndex !== undefined || rightIndex !== undefined) {
      return (leftIndex ?? Number.MAX_SAFE_INTEGER) - (rightIndex ?? Number.MAX_SAFE_INTEGER)
    }
    return right.sessionId - left.sessionId
  })
}

async function loadKnowledgeEvidence(): Promise<void> {
  controller?.abort()
  const active = new AbortController()
  controller = active
  loadState.value = 'loading'
  try {
    const saved = loadEvidenceScope()
    const query: GraphQueryInput = semesterEvidenceQuery(groupMode.value
        ? (saved?.scope ?? { mode: 'all' })
        : { mode: 'selected', student_ids: [studentId.value] }, curriculumScope.selectedVolumeId)
    const first = await fetchGraphEvidence(query, knowledgeKey.value, active.signal, 1)
    const items = [...first.items]
    for (let nextPage = 2; nextPage <= first.total_pages; nextPage += 1) {
      const response = await fetchGraphEvidence(query, knowledgeKey.value, active.signal, nextPage)
      items.push(...response.items)
    }
    // 只保留答错项。
    const wrong = items.filter((item) => item.score_awarded < item.full_score).map(toRow)
    if (groupMode.value) {
      groupStudentCount.value = new Set(wrong.map((row) => row.studentId)).size
      const bySessionMap = new Map<number, QuestionSessionGroup>()
      for (const row of wrong) {
        let group = bySessionMap.get(row.sessionId)
        if (!group) {
          group = { sessionId: row.sessionId, sessionName: '', questions: [] }
          bySessionMap.set(row.sessionId, group)
        }
        let question = group.questions.find((entry) => entry.questionId === row.questionId)
        if (!question) {
          question = { questionId: row.questionId, bankQuestionId: row.bankQuestionId, rows: [] }
          group.questions.push(question)
        }
        question.rows.push(row)
      }
      const names = new Map(items.map((item) => [item.session_id, item.session_name]))
      for (const group of bySessionMap.values()) {
        group.sessionName = names.get(group.sessionId) ?? `考试 ${group.sessionId}`
      }
      questionSessions.value = sortSessions([...bySessionMap.values()])
      knowledgeSessions.value = []
    } else {
      knowledgeStudentName.value = wrong[0]?.studentName ?? ''
      const bySessionMap = new Map<number, KnowledgeSessionGroup>()
      for (const row of wrong) {
        const group = bySessionMap.get(row.sessionId)
        if (group) group.items.push(row)
        else bySessionMap.set(row.sessionId, { sessionId: row.sessionId, sessionName: '', items: [row] })
      }
      const names = new Map(items.map((item) => [item.session_id, item.session_name]))
      for (const group of bySessionMap.values()) {
        group.sessionName = names.get(group.sessionId) ?? `考试 ${group.sessionId}`
      }
      knowledgeSessions.value = sortSessions([...bySessionMap.values()])
      questionSessions.value = []
    }
    loadState.value = 'ready'
  } catch {
    if (active.signal.aborted) return
    loadState.value = 'error'
  } finally {
    if (controller === active) controller = null
  }
}

function toggleDeducted(next: boolean): void {
  if (onlyDeducted.value === next) return
  onlyDeducted.value = next
  void load(1)
}

function loadMore(): void {
  if (loadingMore.value || page.value >= totalPages.value) return
  void load(page.value + 1)
}

function retry(): void {
  if (knowledgeMode.value || groupMode.value) void loadKnowledgeEvidence()
  else void load(1)
}

function toggleQuestion(key: string): void {
  expandedQuestions.value = expandedQuestions.value.includes(key)
    ? expandedQuestions.value.filter((item) => item !== key)
    : [...expandedQuestions.value, key]
}

function openPreview(url: string): void {
  previewUrl.value = url
  void nextTick(() => previewDialog.value?.showModal?.())
}

function closePreview(): void {
  previewDialog.value?.close?.()
  previewUrl.value = ''
}

// 「原题」右侧面板：复用题库详情接口与富文本渲染，只读展示，不跳路由。
const questionPanelId = ref<number | null>(null)
const questionPanelState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const questionPanelDetail = ref<QuestionBankDetail | null>(null)
let questionPanelController: AbortController | null = null

// 面板标签只展示可中文解释的维度；内部编码维度（curriculum_section、canonical_knowledge_id
// 等原始编码）与无法中文解读的值一律不显示。
interface QuestionPanelTagView {
  value: string
  display: string
}

interface QuestionPanelTagGroupView {
  key: string
  label: string
  tone: string
  tags: QuestionPanelTagView[]
}

const questionPanelTagGroupDefs: { type: QuestionBankTagType; label: string; tone: string }[] = [
  { type: 'knowledge_point', label: '知识点', tone: 'accent' },
  { type: 'skill', label: '技能', tone: 'accent' },
  { type: 'method', label: '解题方法', tone: 'info' },
  { type: 'thought', label: '数学思想', tone: 'success' },
  { type: 'ability', label: '能力', tone: 'warning' },
  { type: 'model', label: '模型', tone: 'purple' },
  { type: 'special_type', label: '特殊题型/考法', tone: 'orange' },
  { type: 'error_type', label: '错误类型', tone: 'danger' },
  { type: 'exam_scope', label: '教材章节/考试范围', tone: 'teal' },
  { type: 'prerequisite', label: '前置知识', tone: 'accent' },
  { type: 'sub_skill', label: '子技能', tone: 'teal' },
  { type: 'teaching_stage', label: '教学阶段', tone: 'purple' },
]

// 纯 ASCII 机器编码（如 bnu24-math-g7-lower-c04-s03）不适合直接展示，视为无法识别。
function isReadableTagValue(value: string): boolean {
  return /[一-鿿]/.test(value)
}

const questionPanelTagGroups = computed<QuestionPanelTagGroupView[]>(() => {
  const detail = questionPanelDetail.value
  if (!detail) return []
  return questionPanelTagGroupDefs
    .map((def) => ({
      key: def.type,
      label: def.label,
      tone: def.tone,
      tags: detail.tags
        .filter((tag) => tag.tag_type === def.type)
        .map((tag) => ({
          value: tag.tag_value,
          display: def.type === 'knowledge_point' || def.type === 'skill' ? knowledgeLeafLabel(tag.tag_value) : tag.tag_value,
        }))
        .filter((tag) => isReadableTagValue(tag.display)),
    }))
    .filter((group) => group.tags.length > 0)
})

// 页码取值 document 表示整份文档而非具体页码，按未记录处理。
function pageRangeText(value: string | null): string {
  return value && value !== 'document' ? value : '未记录'
}

async function openQuestionPanel(bankQuestionId: number): Promise<void> {
  questionPanelController?.abort()
  const active = new AbortController()
  questionPanelController = active
  questionPanelId.value = bankQuestionId
  questionPanelDetail.value = null
  questionPanelState.value = 'loading'
  try {
    const detail = await questionBankApi.getQuestion(bankQuestionId, active.signal)
    if (active.signal.aborted) return
    questionPanelDetail.value = detail
    questionPanelState.value = 'ready'
  } catch {
    if (active.signal.aborted) return
    questionPanelState.value = 'error'
  } finally {
    if (questionPanelController === active) questionPanelController = null
  }
}

function retryQuestionPanel(): void {
  if (questionPanelId.value !== null) void openQuestionPanel(questionPanelId.value)
}

function closeQuestionPanel(): void {
  questionPanelController?.abort()
  questionPanelController = null
  questionPanelId.value = null
  questionPanelDetail.value = null
  questionPanelState.value = 'idle'
}

function onPanelKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && questionPanelState.value !== 'idle') closeQuestionPanel()
}

function reloadSemester(): void {
  if (curriculumScope.loadState !== 'ready') return
  sessions.value = []
  knowledgeSessions.value = []
  questionSessions.value = []
  if (knowledgeMode.value || groupMode.value) void loadKnowledgeEvidence()
  else void load(1)
}
watch([() => curriculumScope.loadState, () => curriculumScope.selectedVolumeId], reloadSemester)
onMounted(() => {
  window.addEventListener('keydown', onPanelKeydown)
  reloadSemester()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onPanelKeydown)
  controller?.abort()
  questionPanelController?.abort()
})
</script>

<template>
  <section class="student-evidence" aria-labelledby="student-evidence-title">
    <header class="student-evidence__heading">
      <div>
        <p class="student-evidence__eyebrow">知识与训练 · 学生作答证据</p>
        <h1 id="student-evidence-title">
          {{ groupMode ? (knowledgeLabel || '群体错题') : knowledgeMode ? (knowledgeStudentName || '学生作答证据') : (student ? student.name : '学生作答证据') }}
        </h1>
        <p v-if="groupMode">群体 {{ groupStudentCount }} 人答错 · 按考试与题目归类</p>
        <p v-else-if="knowledgeMode">{{ knowledgeLabel || knowledgeKey }} · 只看答错记录</p>
        <p v-else-if="student">{{ student.student_code }} · {{ student.class_name || '未分班' }}</p>
      </div>
      <RouterLink class="student-evidence__back" :to="backTarget">{{ backLabel }}</RouterLink>
    </header>

    <div v-if="!knowledgeMode && !groupMode" class="student-evidence__toolbar">
      <div class="student-evidence__modes" role="group" aria-label="作答范围">
        <button type="button" :class="{ 'is-active': onlyDeducted }" @click="toggleDeducted(true)">只看错题</button>
        <button type="button" :class="{ 'is-active': !onlyDeducted }" @click="toggleDeducted(false)">全部作答</button>
      </div>
      <span v-if="loadState === 'ready'">共 {{ totalSessions }} 场考试</span>
    </div>

    <p v-if="loadState === 'loading'" class="status-card">正在读取学生的作答证据……</p>
    <div v-else-if="loadState === 'error'" class="status-card error" role="alert">
      <p>作答证据暂时无法读取。请检查服务后重试。</p>
      <AppButton type="button" @click="retry">重新加载</AppButton>
    </div>

    <template v-else-if="groupMode">
      <p v-if="!questionSessions.length" class="status-card empty-state">当前群体在该知识点下没有答错记录。</p>
      <article v-for="session in questionSessions" :key="session.sessionId" class="student-evidence__session">
        <header>
          <div><strong>{{ session.sessionName }}</strong></div>
          <b>{{ session.questions.length }} 道题</b>
        </header>
        <div v-for="question in session.questions" :key="question.questionId" class="student-evidence__question">
          <div class="student-evidence__question-bar">
            <button
              type="button"
              class="student-evidence__question-heading"
              :aria-expanded="expandedQuestions.includes(`${session.sessionId}:${question.questionId}`)"
              @click="toggleQuestion(`${session.sessionId}:${question.questionId}`)"
            >
              <strong>{{ question.questionId }}</strong>
              <span>{{ question.rows.length }} 人答错</span>
            </button>
            <button
              v-if="question.bankQuestionId"
              type="button"
              class="student-evidence__original"
              :aria-label="`查看 ${question.questionId} 的题库原题`"
              @click="openQuestionPanel(question.bankQuestionId)"
            >原题</button>
          </div>
          <ul v-if="expandedQuestions.includes(`${session.sessionId}:${question.questionId}`)">
            <li v-for="row in question.rows" :key="row.key">
              <span class="student-evidence__question-student"><b>{{ row.studentName }}</b><small>{{ row.studentCode }}</small></span>
              <span>{{ formatScore(row.scoreAwarded) }} / {{ formatScore(row.fullScore) }}</span>
              <span class="student-evidence__question-reason">{{ row.reason || '—' }}<br><small>{{ row.assessment }}</small></span>
              <button
                v-if="row.evidenceUrl"
                type="button"
                class="student-evidence__thumb"
                :aria-label="`放大查看 ${row.studentName} ${row.questionId} 的作答图像`"
                @click="openPreview(row.evidenceUrl)"
              >
                <img :src="row.evidenceUrl" :alt="`${row.studentName} ${row.questionId} 作答图像`" loading="lazy">
              </button>
              <span v-else>—</span>
            </li>
          </ul>
        </div>
      </article>
    </template>

    <template v-else-if="knowledgeMode">
      <p v-if="!knowledgeSessions.length" class="status-card empty-state">该学生在该知识点下没有答错记录。</p>
      <article v-for="session in knowledgeSessions" :key="session.sessionId" class="student-evidence__session">
        <header>
          <div><strong>{{ session.sessionName }}</strong></div>
          <b>{{ session.items.length }} 道错题</b>
        </header>
        <table>
          <thead>
            <tr>
              <th scope="col">题号</th>
              <th scope="col">得分 / 满分</th>
              <th scope="col">扣分原因</th>
              <th scope="col">作答图像</th>
              <th scope="col">原题</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in session.items" :key="row.key">
              <td>{{ row.questionId }}</td>
              <td>{{ formatScore(row.scoreAwarded) }} / {{ formatScore(row.fullScore) }}</td>
              <td>{{ row.reason || '—' }}<br><small>{{ row.assessment }}</small></td>
              <td>
                <button
                  v-if="row.evidenceUrl"
                  type="button"
                  class="student-evidence__thumb"
                  :aria-label="`放大查看 ${row.questionId} 的作答图像`"
                  @click="openPreview(row.evidenceUrl)"
                >
                  <img :src="row.evidenceUrl" :alt="`${row.questionId} 作答图像`" loading="lazy">
                </button>
                <span v-else>—</span>
              </td>
              <td>
                <button
                  v-if="row.bankQuestionId"
                  type="button"
                  class="student-evidence__original"
                  :aria-label="`查看 ${row.questionId} 的题库原题`"
                  @click="openQuestionPanel(row.bankQuestionId)"
                >原题</button>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </article>
    </template>

    <template v-else>
      <p v-if="!sessions.length" class="status-card empty-state">该学生暂无考试作答记录。</p>
      <template v-else>
        <article v-for="session in sessions" :key="session.session_id" class="student-evidence__session">
          <header>
            <div>
              <strong>{{ session.session_name }}</strong>
              <span>{{ sessionDate(session) }}</span>
            </div>
            <b>{{ formatScore(session.student_score) }} / {{ formatScore(session.total_score) }}</b>
          </header>
          <table>
            <thead>
              <tr>
                <th scope="col">题号</th>
                <th scope="col">得分 / 满分</th>
                <th scope="col">扣分</th>
                <th scope="col">扣分原因</th>
                <th scope="col">作答图像</th>
                <th scope="col">原题</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in session.items" :key="item.detail_id">
                <td>{{ item.question_id }}</td>
                <td>{{ formatScore(item.score_awarded) }} / {{ item.max_score === null ? '—' : formatScore(item.max_score) }}</td>
                <td>{{ item.deduction_amount === null ? '—' : formatScore(item.deduction_amount) }}</td>
                <td>{{ reasonText(item) }}</td>
                <td>
                  <button
                    type="button"
                    class="student-evidence__thumb"
                    :aria-label="`放大查看 ${item.question_id} 的作答图像`"
                    @click="openPreview(item.evidence_url)"
                  >
                    <img :src="item.evidence_url" :alt="`${item.question_id} 作答图像`" loading="lazy">
                  </button>
                </td>
                <td>
                  <button
                    v-if="item.bank_question_id"
                    type="button"
                    class="student-evidence__original"
                    :aria-label="`查看 ${item.question_id} 的题库原题`"
                    @click="openQuestionPanel(item.bank_question_id)"
                  >原题</button>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </article>
        <AppButton
          v-if="page < totalPages"
          block
          class="student-evidence__more"
          :loading="loadingMore"
          loading-label="正在加载"
          @click="loadMore"
        >
          加载更多（{{ page }} / {{ totalPages }} 页）
        </AppButton>
      </template>
    </template>

    <dialog ref="previewDialog" class="student-evidence__preview" @click="closePreview">
      <img v-if="previewUrl" :src="previewUrl" alt="作答证据大图">
    </dialog>

    <Teleport to="body">
      <div
        v-if="questionPanelState !== 'idle'"
        class="question-panel-layer"
        role="presentation"
        @click.self="closeQuestionPanel"
      >
        <aside class="question-panel" role="dialog" aria-modal="true" aria-labelledby="question-panel-title">
          <div v-if="questionPanelState === 'loading'" class="question-panel__empty" role="status">
            正在打开题库原题…
          </div>
          <div v-else-if="questionPanelState === 'error'" class="question-panel__empty" role="alert">
            <span>题库原题暂时无法读取，作答证据不受影响。</span>
            <button type="button" class="question-panel__retry" @click="retryQuestionPanel">重试</button>
          </div>
          <template v-else-if="questionPanelDetail">
            <header class="question-panel__heading">
              <div>
                <h2 id="question-panel-title" tabindex="-1">
                  第 {{ questionPanelDetail.question_number || questionPanelDetail.id }} 题
                </h2>
                <p>{{ questionPanelDetail.paper_title || '未命名试卷' }}</p>
              </div>
              <button type="button" class="question-panel__close" aria-label="关闭原题预览" @click="closeQuestionPanel">×</button>
            </header>

            <dl class="question-panel__facts">
              <div><dt>题型</dt><dd>{{ questionPanelDetail.question_type || '未分类' }}</dd></div>
              <div><dt>难度</dt><dd>{{ questionPanelDetail.difficulty || '待定' }}</dd></div>
              <div><dt>页码</dt><dd>{{ pageRangeText(questionPanelDetail.page_range) }}</dd></div>
              <div><dt>图片</dt><dd>{{ questionPanelDetail.has_images ? '包含' : '无' }}</dd></div>
            </dl>

            <section class="question-panel__section">
              <h3>题干</h3>
              <QuestionContentRenderer
                :blocks="questionPanelDetail.rich_content.question_blocks"
                :fallback="questionPanelDetail.question_text"
                image-alt="题目配图"
                media-mode="detail"
              />
            </section>

            <details class="question-panel__section">
              <summary>答案与解析</summary>
              <QuestionContentRenderer
                :blocks="questionPanelDetail.rich_content.answer_blocks"
                :fallback="questionPanelDetail.answer_text"
                empty-label="暂未录入答案或解析"
                image-alt="答案配图"
                media-mode="detail"
              />
            </details>

            <section v-if="questionPanelDetail.previews.length" class="question-panel__section">
              <h3>原卷预览</h3>
              <div class="question-panel__previews">
                <template v-for="preview in questionPanelDetail.previews" :key="preview.preview_type">
                  <a v-if="preview.url" :href="preview.url" target="_blank" rel="noopener">
                    {{ preview.preview_type === 'question' ? '打开题目原卷' : '打开答案原卷' }}
                    <span v-if="preview.page_number"> · 第 {{ preview.page_number }} 页</span>
                  </a>
                  <span v-else>
                    {{ preview.preview_type === 'question' ? '题目原卷' : '答案原卷' }}暂不可用
                  </span>
                </template>
              </div>
            </section>

            <section v-if="questionPanelTagGroups.length" class="question-panel__section">
              <h3>标签</h3>
              <div class="question-panel__tag-groups">
                <section
                  v-for="group in questionPanelTagGroups"
                  :key="group.key"
                  class="question-panel__tag-group"
                  :data-tone="group.tone"
                >
                  <h4>{{ group.label }}</h4>
                  <ul>
                    <li
                      v-for="tag in group.tags"
                      :key="tag.value"
                      :title="tag.value === tag.display ? undefined : tag.value"
                    >{{ tag.display }}</li>
                  </ul>
                </section>
              </div>
            </section>
          </template>
        </aside>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.student-evidence__heading { display: flex; justify-content: space-between; align-items: end; gap: var(--space-4); }
.student-evidence__eyebrow { margin: 0 0 var(--space-1); color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.student-evidence__heading h1 { margin: 0 0 var(--space-1); }
.student-evidence__heading p { margin: 0; color: var(--color-text-secondary); }
.student-evidence__back { padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border-default); border-radius: var(--radius-control); color: var(--color-accent-active); text-decoration: none; white-space: nowrap; }
.student-evidence__toolbar { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); margin: var(--space-4) 0; }
.student-evidence__toolbar > span { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.student-evidence__modes { display: flex; padding: var(--space-1); border-radius: var(--radius-control); background: var(--color-bg-subtle); }
.student-evidence__modes button { padding: .45rem .75rem; border: 0; border-radius: calc(var(--radius) - 2px); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.student-evidence__modes button.is-active { background: var(--color-bg-surface); color: var(--color-accent-active); font-weight: 700; }
.student-evidence__session { margin-top: var(--space-4); border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); overflow: hidden; }
.student-evidence__session > header { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-4); padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--color-border-default); background: var(--color-bg-subtle); }
.student-evidence__session > header span { margin-left: var(--space-2); color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.student-evidence__session > header b { font-size: 1.05rem; }
.student-evidence__session table { width: 100%; border-collapse: collapse; }
.student-evidence__session th, .student-evidence__session td { padding: .45rem var(--space-3); border-bottom: 1px solid var(--color-border-default); text-align: left; }
.student-evidence__session thead th { color: var(--color-text-secondary); font-size: var(--font-size-caption); font-weight: 500; }
.student-evidence__session tbody tr:last-child td { border-bottom: 0; }
.student-evidence__question { border-bottom: 1px solid var(--color-border-default); }
.student-evidence__question:last-child { border-bottom: 0; }
.student-evidence__question-heading { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); width: 100%; padding: var(--space-2) var(--space-4); border: 0; background: transparent; color: var(--color-text-primary); cursor: pointer; }
.student-evidence__question-heading span { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.student-evidence__question ul { display: grid; gap: var(--space-2); margin: 0; padding: 0 var(--space-4) var(--space-3); list-style: none; }
.student-evidence__question li { display: grid; grid-template-columns: minmax(120px, 1fr) auto minmax(140px, 1.2fr) auto; align-items: center; gap: var(--space-3); }
.student-evidence__question-student { display: grid; line-height: 1.2; }
.student-evidence__question-student small { color: var(--color-text-muted); }
.student-evidence__question-reason { color: var(--color-text-secondary); }
.student-evidence__thumb { padding: 0; border: 1px solid var(--color-border-default); border-radius: calc(var(--radius) - 2px); background: var(--color-bg-subtle); cursor: zoom-in; }
.student-evidence__thumb img { display: block; width: 96px; height: 64px; object-fit: cover; }
.student-evidence__more { margin-bottom: var(--space-4); }
.student-evidence__preview { max-width: min(90vw, 960px); padding: var(--space-3); border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); }
.student-evidence__preview::backdrop { background: rgb(0 0 0 / .45); }
.student-evidence__preview img { display: block; max-width: 100%; max-height: 80vh; }
.student-evidence__question-bar { display: flex; align-items: center; gap: var(--space-2); padding-right: var(--space-3); }
.student-evidence__question-bar .student-evidence__question-heading { flex: 1; min-width: 0; }
.student-evidence__original { flex: none; padding: 3px 10px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-accent-active); font-size: var(--font-size-caption); cursor: pointer; }
.student-evidence__original:hover { border-color: var(--color-accent); background: var(--color-bg-selected); }
.question-panel-layer { position: fixed; inset: 0; z-index: 1100; display: flex; justify-content: flex-end; width: 100vw; height: 100dvh; background: color-mix(in srgb, var(--color-text-primary) 32%, transparent); }
.question-panel { width: min(560px, 92vw); max-width: 100%; height: 100%; overflow-y: auto; padding: 20px; border-left: 1px solid var(--color-border-default); background: var(--color-bg-surface); box-shadow: -18px 0 44px color-mix(in srgb, var(--color-text-primary) 14%, transparent); }
.question-panel__empty { display: grid; gap: var(--space-3); justify-items: start; padding: var(--space-6) 0; color: var(--color-text-secondary); }
.question-panel__retry { padding: 6px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-accent-active); cursor: pointer; }
.question-panel__heading { display: flex; justify-content: space-between; align-items: start; gap: var(--space-3); }
.question-panel__heading h2 { margin: 0 0 var(--space-1); }
.question-panel__heading p { margin: 0; color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.question-panel__close { flex: none; width: 32px; height: 32px; border: 1px solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-secondary); font-size: 18px; line-height: 1; cursor: pointer; }
.question-panel__facts { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--space-2) var(--space-4); margin: var(--space-4) 0; padding: var(--space-3); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-control); background: var(--color-bg-subtle); }
.question-panel__facts div { display: flex; justify-content: space-between; gap: var(--space-3); }
.question-panel__facts dt { color: var(--color-text-muted); }
.question-panel__facts dd { margin: 0; }
.question-panel__section { margin-top: var(--space-4); }
.question-panel__section h3 { margin: 0 0 var(--space-2); }
.question-panel__section summary { cursor: pointer; font-weight: 700; }
.question-panel__section summary + * { margin-top: var(--space-2); }
.question-panel__previews { display: grid; gap: var(--space-2); }
.question-panel__previews a { color: var(--color-accent-active); }
.question-panel__tag-groups { display: grid; gap: var(--space-3); }
.question-panel__tag-group h4 { margin: 0 0 var(--space-1); color: var(--color-text-secondary); font-size: var(--font-size-caption); font-weight: 600; }
.question-panel__tag-group ul { display: flex; flex-wrap: wrap; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.question-panel__tag-group li { padding: 3px 10px; border: 1px solid color-mix(in srgb, var(--qp-tag-tone) 32%, transparent); border-radius: 999px; background: var(--qp-tag-tint); color: var(--qp-tag-tone); font-size: var(--font-size-caption); }
.question-panel__tag-group[data-tone='accent'] { --qp-tag-tone: var(--color-accent); --qp-tag-tint: var(--color-accent-subtle); }
.question-panel__tag-group[data-tone='info'] { --qp-tag-tone: var(--color-info); --qp-tag-tint: var(--color-info-subtle); }
.question-panel__tag-group[data-tone='success'] { --qp-tag-tone: var(--color-success); --qp-tag-tint: var(--color-success-subtle); }
.question-panel__tag-group[data-tone='warning'] { --qp-tag-tone: var(--color-warning); --qp-tag-tint: var(--color-warning-subtle); }
.question-panel__tag-group[data-tone='danger'] { --qp-tag-tone: var(--color-danger); --qp-tag-tint: var(--color-danger-subtle); }
.question-panel__tag-group[data-tone='purple'] { --qp-tag-tone: color-mix(in srgb, var(--color-accent) 55%, var(--color-danger)); --qp-tag-tint: color-mix(in srgb, var(--color-accent-subtle) 55%, var(--color-danger-subtle)); }
.question-panel__tag-group[data-tone='orange'] { --qp-tag-tone: color-mix(in srgb, var(--color-warning) 55%, var(--color-danger)); --qp-tag-tint: color-mix(in srgb, var(--color-warning-subtle) 55%, var(--color-danger-subtle)); }
.question-panel__tag-group[data-tone='teal'] { --qp-tag-tone: color-mix(in srgb, var(--color-info) 55%, var(--color-success)); --qp-tag-tint: color-mix(in srgb, var(--color-info-subtle) 55%, var(--color-success-subtle)); }
</style>
