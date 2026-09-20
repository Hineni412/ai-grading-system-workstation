<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { DialogClose, DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

import {
  classAnalysisApi,
  type ClassAnalysisQuestion,
  type ClassQuestionPreview,
  type ClassAnalysisResponse,
  type ClassCauseEvidence,
  type ClassCause,
} from '../../api/class-analysis'
import { ApiError } from '../../api/errors'
import { jobApi, TERMINAL_JOB_STATUSES } from '../../api/jobs'
import {
  modelProfilesApi,
  type ModelTaskBinding,
} from '../../api/model-profiles'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'
import ClassAnalysisGenerateConfirm from './ClassAnalysisGenerateConfirm.vue'
import QuestionContentRenderer from '../question-bank/QuestionContentRenderer.vue'

const props = defineProps<{
  sessionId: number | null
}>()

const router = useRouter()
const jobStore = useJobStore()
const analysis = ref<ClassAnalysisResponse | null>(null)
const selectedClass = ref('')
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
const confirmOpen = ref(false)
const confirmLoading = ref(false)
const confirmBinding = ref<ModelTaskBinding | null>(null)
const confirmError = ref('')
const regenerating = ref(false)
let loadGeneration = 0
let loadController: AbortController | null = null
let previewController: AbortController | null = null
const previewQuestion = ref<ClassAnalysisQuestion | null>(null)
const preview = ref<ClassQuestionPreview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
let previewTrigger: HTMLElement | null = null
const previewCache = new Map<string, ClassQuestionPreview>()
const expandedBands = ref(new Set<string>())
const expandedCauses = ref(new Set<string>())

const activeJobId = computed(() => analysis.value?.active_job_id ?? null)

const activeJob = computed(() => (
  activeJobId.value === null ? null : jobStore.jobs[activeJobId.value] ?? null
))

const generating = computed(() => {
  if (analysis.value?.status === 'generating') return true
  return activeJob.value !== null
    && !TERMINAL_JOB_STATUSES.has(activeJob.value.status)
})

const canRegenerate = computed(() => (
  analysis.value !== null
  && !generating.value
  && !regenerating.value
))

const data = computed(() => analysis.value?.data ?? null)
const causeAnalysis = computed(() => analysis.value?.cause_analysis ?? null)
const causeStatusText = computed(() => {
  const state = causeAnalysis.value
  if (state?.legacy_questions) return `${state.legacy_questions} 题保留既有归并；更新整理后，将结合真实作答分别整理错因、过程缺项和待核对事项。`
  if (!state || state.status === 'not_generated') return state?.failed_questions
    ? '错因整理未完成，保留原始理由；可重新整理。'
    : state?.stale ? '作答、批语或题目依据已变化，建议更新整理。' : '当前按原始表述合并，可结合真实作答整理。'
  if (state.status === 'partial') return `部分题目已整理，${state.pending_questions} 题仍显示原始理由，可继续整理。`
  return '已结合现有作答整理；展开可核对本题表现、作答与批语。同一学生同类只计一次，不同类可重复出现。'
})
const diagnosticQuestions = computed(() => [...(data.value?.questions ?? [])]
  .sort((a, b) => a.class_rate - b.class_rate)
  .map((question) => {
    const limits = [0, Number((question.max_score / 3).toFixed(2)), Number((question.max_score * 2 / 3).toFixed(2)), question.max_score]
    const records = [...question.records].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
    const bands = [2, 1, 0].map((index) => ({
      key: `${question.question_id}:${index}`,
      label: `${formatScore(limits[index]!)}–${formatScore(limits[index + 1]!)} 分`,
      description: `${formatScore(limits[index]!)} 分及以上、不足 ${formatScore(limits[index + 1]!)} 分`,
      records: records.filter((record) => record.score !== null && record.score >= limits[index]! && record.score < limits[index + 1]!),
    }))
    const unscored = records.filter((record) => record.score === null)
    if (unscored.length) bands.push({ key: `${question.question_id}:unscored`, label: '未记分', description: '尚未记录本题得分', records: unscored })
    const structuredCauses = question.causes_legacy === false || question.causes?.some((cause) => cause.kind !== undefined)
    const groupedCauses: ClassCause[] = [...(question.causes ?? [])]
    if (structuredCauses && question.cause_review?.uncertain.length) groupedCauses.push({
      kind: 'review', reason: '错因待明确', count: evidenceStudentCount(question.cause_review.uncertain),
      evidence: question.cause_review.uncertain,
    })
    const kinds = [
      ['legacy', question.causes_legacy ? '既有归并 · 待升级' : '原始归并'],
      ['error', '数学与单位错误'], ['process', '过程与表达缺项'],
      ['response_state', '作答状态'], ['carry_forward', '前问错误延续'], ['review', '需要核对'],
    ] as const
    const causeSections = kinds.map(([kind, label]) => {
      const causes = groupedCauses.filter((cause) => (cause.kind ?? 'legacy') === kind).sort((a, b) => b.count - a.count)
      return { kind, label, key: `${question.question_id}:${kind}`, causes,
        count: causes.every((cause) => cause.evidence?.length) ? evidenceStudentCount(causes.flatMap((cause) => cause.evidence ?? [])) : null }
    }).filter((section) => section.causes.length)
    return {
      ...question,
      causeSections,
      structuredCauses,
      scoreBands: bands.map((band) => ({ ...band, share: records.length ? Number((band.records.length / records.length * 100).toFixed(1)) : 0 })),
      causes: [...(question.causes ?? [])].sort((a, b) => b.count - a.count),
    }
  }))

const bandEntries = computed(() => {
  const bands = data.value?.score_distribution.bands ?? {}
  const entries = Object.entries(bands)
  const largest = Math.max(1, ...entries.map(([, count]) => count))
  return entries.map(([label, count]) => ({
    label,
    count,
    width: `${Math.round((count / largest) * 100)}%`,
  }))
})

watch(
  () => props.sessionId,
  () => {
    closeConfirm()
    closePreview()
    previewCache.clear()
    selectedClass.value = ''
    analysis.value = null
    void load()
  },
  { immediate: true },
)

watch(
  () => activeJob.value?.status,
  (status) => {
    if (status !== undefined && TERMINAL_JOB_STATUSES.has(status)) void load()
  },
)

async function load(): Promise<void> {
  const sessionId = props.sessionId
  const generation = ++loadGeneration
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  if (sessionId === null) {
    analysis.value = null
    loadState.value = 'idle'
    return
  }
  loadState.value = 'loading'
  expandedBands.value = new Set()
  expandedCauses.value = new Set()
  errorMessage.value = ''
  try {
    const next = await classAnalysisApi.getClassAnalysis(sessionId, controller.signal, selectedClass.value, 'summary')
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    analysis.value = next
    selectedClass.value = next.selected_class ?? ''
    loadState.value = 'ready'
    void ensureTracked(next.active_job_id)
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    loadState.value = 'error'
    errorMessage.value = '班级分析暂时无法读取，请稍后重试。'
  }
}

function openReport(): void {
  if (props.sessionId === null) return
  void router.push({
    path: '/class-report',
    query: {
      session: String(props.sessionId),
      ...(selectedClass.value ? { class: selectedClass.value } : {}),
    },
  })
}

function closePreview(): void {
  previewController?.abort()
  previewQuestion.value = null
  preview.value = null
}

function restorePreviewFocus(event: Event): void {
  event.preventDefault()
  if (previewTrigger?.isConnected) previewTrigger.focus()
}

async function openPreview(question: ClassAnalysisQuestion, event?: Event): Promise<void> {
  if (event?.currentTarget instanceof HTMLElement) previewTrigger = event.currentTarget
  previewController?.abort()
  const sessionId = props.sessionId
  if (sessionId === null) return
  const controller = new AbortController()
  previewController = controller
  previewQuestion.value = question
  preview.value = previewCache.get(question.question_id) ?? null
  previewError.value = ''
  previewLoading.value = !preview.value
  if (preview.value) return
  try {
    const result = await classAnalysisApi.getQuestionPreview(sessionId, question.question_id, controller.signal)
    if (controller.signal.aborted || props.sessionId !== sessionId) return
    previewCache.set(question.question_id, result)
    preview.value = result
  } catch {
    if (!controller.signal.aborted) previewError.value = '原题暂时无法读取，请重试。'
  } finally {
    if (!controller.signal.aborted) previewLoading.value = false
  }
}

onBeforeUnmount(() => {
  loadGeneration += 1
  loadController?.abort()
  previewController?.abort()
})

async function ensureTracked(id: number | null): Promise<void> {
  if (id === null) return
  const existing = jobStore.jobs[id]
  if (existing !== undefined && !TERMINAL_JOB_STATUSES.has(existing.status)) return
  try {
    const job = await jobApi.getJob(id)
    // await 期间页面可能已通过其他链路拿到更新状态，不覆盖。
    if (jobStore.jobs[id] !== undefined) return
    if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
  } catch {
    // 任务可能已结束或被清理；下次刷新时按最新状态展示。
  }
}

async function openRegenerateConfirm(): Promise<void> {
  if (!canRegenerate.value) return
  confirmOpen.value = true
  confirmBinding.value = null
  confirmError.value = ''
  confirmLoading.value = true
  try {
    const state = await modelProfilesApi.getState()
    if (!confirmOpen.value) return
    confirmBinding.value = state.task_bindings.content_generation
  } catch {
    if (!confirmOpen.value) return
    confirmError.value = '模型配置暂时无法读取，请稍后重试。'
  } finally {
    if (confirmOpen.value) confirmLoading.value = false
  }
}

function closeConfirm(): void {
  confirmOpen.value = false
  confirmBinding.value = null
  confirmError.value = ''
  confirmLoading.value = false
}

async function confirmRegenerate(): Promise<void> {
  const sessionId = props.sessionId
  if (sessionId === null || regenerating.value || confirmLoading.value) return
  regenerating.value = true
  confirmError.value = ''
  try {
    const job = await classAnalysisApi.regenerate(sessionId, undefined, 'causes')
    if (props.sessionId !== sessionId) return
    jobStore.track(job)
    closeConfirm()
    await load()
  } catch (error) {
    if (props.sessionId !== sessionId) return
    confirmError.value = error instanceof ApiError
      && error.code === 'content_generation_model_not_configured'
      ? '未配置内容生成模型，请前往 设置→模型配置 绑定后重试。'
      : '整理请求未能提交，请稍后重试。'
  } finally {
    regenerating.value = false
  }
}

function formatScore(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? String(value)
    : String(Number(value.toFixed(2)))
}

function causeStudents(question: ClassAnalysisQuestion, ids: number[]): string {
  return question.records.filter((record) => record.student_id !== undefined && ids.includes(record.student_id))
    .map((record) => `${record.student_name}（${!selectedClass.value && record.class_name ? `${record.class_name}，` : ''}${formatScore(record.score)} 分）`).join('、')
}

function evidenceStudentCount(items: ClassCauseEvidence[]): number {
  return new Set(items.flatMap((item) => item.student_ids)).size
}

function toggleBand(key: string): void {
  const next = new Set(expandedBands.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedBands.value = next
}

function toggleCauses(questionId: string): void {
  const next = new Set(expandedCauses.value)
  if (next.has(questionId)) next.delete(questionId)
  else next.add(questionId)
  expandedCauses.value = next
}

function formatPercent(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${Math.round(percent)}%`
}

function formatTime(value: string | null): string {
  if (!value) return '时间未记录'
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}

function rateTone(rate: number): 'low' | 'mid' | 'high' {
  const normalized = rate <= 1 ? rate : rate / 100
  if (normalized < 0.4) return 'low'
  if (normalized < 0.7) return 'mid'
  return 'high'
}
</script>


<template>
  <section class="class-analysis" aria-label="班级分析">
      <div v-if="props.sessionId === null" class="results-state-panel">
        <strong>请先在顶部选择考试</strong>
        <span>选择后，这里会显示该考试的班级整体分析。</span>
      </div>
      <template v-else>
        <div v-if="analysis" class="class-analysis__toolbar">
          <label class="class-analysis__toggle">
            <span>查看范围</span>
            <select v-model="selectedClass" aria-label="查看班级" @change="load">
              <option value="">全部班级（合并）</option>
              <option v-for="name in analysis.class_names" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>
          <AppButton variant="secondary" data-testid="ai-analysis-open" @click="openReport">
            AI 班级分析<span v-if="generating"> · 生成中</span>
          </AppButton>
        </div>
        <div v-if="loadState === 'loading'" class="results-state-panel" role="status">
          <strong>正在读取班级分析</strong>
          <span>正在统计当前范围的成绩与试题。</span>
        </div>
        <div v-else-if="loadState === 'error'" class="results-state-panel results-state-panel--error" role="alert">
          <strong>班级分析暂时无法读取</strong>
          <span>{{ errorMessage }}</span>
          <AppButton variant="secondary" @click="load">重新加载</AppButton>
        </div>
        <div v-else-if="analysis?.status === 'no_data'" class="results-state-panel" data-testid="class-analysis-empty">
          <strong>当前范围尚无已批改成绩</strong>
          <span>完成批改后，这里会显示统计结果。</span>
        </div>
        <div v-else-if="generating && !data" class="results-state-panel" role="status" data-testid="class-analysis-generating">
          <strong>班级分析生成中…</strong>
          <span>完成后页面会自动更新，无需手动刷新。</span>
        </div>
        <template v-else-if="analysis && data">
          <section class="class-analysis__section" aria-labelledby="class-analysis-overview-title">
            <div class="class-analysis__section-heading">
              <div>
                <p class="results-center__eyebrow">成绩概览 · {{ selectedClass || '全部班级合并' }}</p>
                <h2 id="class-analysis-overview-title">{{ data.exam.title }}</h2>
                <p>
                  <template v-if="data.exam.subject">{{ data.exam.subject }} · </template>
                  满分 {{ formatScore(data.exam.full_score) }} 分 · 实际参考 {{ data.present }} 人
                  <template v-if="data.roster_absent.length > 0"> · 名册另有 {{ data.roster_absent.length }} 人未检测到答卷</template>
                </p>
                <p v-if="data.exam.graded_at">批改完成 {{ formatTime(data.exam.graded_at) }}</p>
              </div>
            </div>
            <p v-if="analysis.small_sample" class="class-analysis__note" data-testid="small-sample-note">
              当前范围参考人数较少，比率指标解读需谨慎。
            </p>
            <div class="class-analysis__stats">
              <div class="class-analysis__stat"><strong>{{ formatScore(data.score_distribution.avg) }}</strong><span>平均分</span></div>
              <div class="class-analysis__stat"><strong>{{ formatScore(data.score_distribution.median) }}</strong><span>中位数</span></div>
              <div class="class-analysis__stat">
                <strong>{{ formatScore(data.score_distribution.max) }} <small>/ {{ formatScore(data.score_distribution.min) }}</small></strong>
                <span>最高 / 最低</span>
              </div>
              <div class="class-analysis__stat"><strong>{{ formatPercent(data.score_distribution.pass_rate) }}</strong><span>及格率</span></div>
            </div>
            <div class="class-analysis__bands" aria-label="分数段分布">
              <div v-for="band in bandEntries" :key="band.label" class="class-analysis__band">
                <span>{{ band.label }}</span>
                <div aria-hidden="true"><i :style="{ width: band.width }"></i></div>
                <strong>{{ band.count }} 人</strong>
              </div>
            </div>
          </section>
          <section class="class-analysis__section" aria-labelledby="class-analysis-questions-title">
            <div class="class-analysis__section-heading">
              <div>
                <p class="results-center__eyebrow">每题得分率</p>
                <h2 id="class-analysis-questions-title">试题诊断</h2>
                <p>得分率低的题目在前；错因按人数从多到少排列。</p>
                <p class="class-analysis__cause-status" data-testid="cause-analysis-status">{{ causeStatusText }}</p>
                <p>失分名单按本题满分分为三档，临界分归入较高档；点击档位查看名单。</p>
              </div>
              <div class="class-analysis__heading-actions">
                <AppButton
                  variant="secondary"
                  data-testid="group-causes-open"
                  :disabled="!canRegenerate || causeAnalysis?.pending_questions === 0"
                  @click="openRegenerateConfirm()"
                >
                  {{ causeAnalysis?.status === 'ready' ? '已整理' : causeAnalysis?.status === 'partial' || causeAnalysis?.stale ? '继续整理 / 更新' : '整理错因' }}
                </AppButton>
                <span class="class-analysis__privacy">含学生姓名，请勿直接外发</span>
              </div>
            </div>
            <div class="results-table-wrap class-analysis__table-wrap">
              <table class="class-analysis__questions">
                <colgroup><col class="class-analysis__col-question"><col class="class-analysis__col-score"><col class="class-analysis__col-rate"><col class="class-analysis__col-students"><col></colgroup>
                <thead>
                  <tr><th scope="col">题目</th><th scope="col">满分</th><th scope="col">得分率</th><th scope="col">失分同学 · 本题得分</th><th scope="col">错因与作答情况</th></tr>
                </thead>
                <tbody>
                  <tr v-for="question in diagnosticQuestions" :key="question.question_id">
                    <th scope="row">
                      <strong>{{ question.question_id }}</strong>
                      <span v-if="question.stem_summary" class="class-analysis__stem">{{ question.stem_summary }}</span>
                      <button type="button" class="class-analysis__preview-button" :aria-label="`预览 ${question.question_id} 原题`" @click="openPreview(question, $event)">查看原题</button>
                    </th>
                    <td>{{ formatScore(question.max_score) }}</td>
                    <td>
                      <span class="class-analysis__ratebar" :class="`class-analysis__ratebar--${rateTone(question.class_rate)}`" aria-hidden="true"><i :style="{ width: formatPercent(question.class_rate) }"></i></span>
                      <strong>{{ formatPercent(question.class_rate) }}</strong>
                    </td>
                    <td>
                      <div v-if="question.records.length" class="class-analysis__score-bands">
                        <div v-for="band in question.scoreBands" :key="band.key" class="class-analysis__score-band">
                          <button type="button" class="class-analysis__score-band-toggle" :aria-expanded="expandedBands.has(band.key)" :disabled="!band.records.length" :title="`${band.description}；占本题失分人数 ${band.share}%`" @click="toggleBand(band.key)">
                            <span class="class-analysis__score-band-arrow" :class="{ 'is-expanded': expandedBands.has(band.key) }" aria-hidden="true">▶</span>
                            <strong>{{ band.label }}</strong>
                            <span class="class-analysis__score-band-count">{{ band.records.length }} 人 · {{ band.share }}%</span>
                            <span class="class-analysis__score-band-track" aria-hidden="true"><i :style="{ width: `${band.share}%` }"></i></span>
                          </button>
                          <div v-if="expandedBands.has(band.key)" class="class-analysis__score-list">
                            <span v-for="(record, index) in band.records" :key="record.student_id ?? index" class="class-analysis__score-chip" :title="[record.class_name, record.student_code].filter(Boolean).join(' · ')">
                              <span>{{ record.student_name }}<small v-if="!selectedClass && question.records.some(other => other.student_name === record.student_name && other.student_id !== record.student_id)">（{{ record.class_name }} {{ record.student_code }}）</small></span>
                              <b>{{ formatScore(record.score) }} 分</b>
                            </span>
                          </div>
                        </div>
                        <span class="class-analysis__note">共 {{ question.records.length }} 人失分，档内按得分从高到低排列</span>
                      </div>
                      <span v-if="!question.records.length" class="class-analysis__note">无人失分</span>
                    </td>
                    <td>
                      <details v-for="section in question.causeSections" :key="section.kind" class="class-analysis__cause-category" :data-kind="section.kind" :open="section.kind === 'error' || section.kind === 'legacy'">
                        <summary>{{ section.label }}<span v-if="section.count !== null"> · {{ section.count }} 人</span></summary>
                        <ol class="class-analysis__causes">
                          <li v-for="cause in (expandedCauses.has(section.key) ? section.causes : section.causes.slice(0, 5))" :key="cause.reason">
                            <details v-if="cause.evidence?.length" class="class-analysis__cause-detail">
                              <summary><span>{{ cause.reason }}</span><b>{{ cause.count }} 人</b></summary>
                              <div v-for="(variant, variantIndex) in (cause.manifestations ?? [{ description: '', source_question_id: null, evidence: cause.evidence }])" :key="variantIndex" class="class-analysis__cause-manifestation">
                                <p v-if="variant.description" class="class-analysis__cause-observation">本题表现：{{ variant.description }}</p>
                                <p v-if="variant.source_question_id" class="class-analysis__note">延续自 {{ variant.source_question_id }}</p>
                                <div v-for="(item, index) in variant.evidence" :key="index" class="class-analysis__cause-evidence">
                                  <p v-if="item.student_answer"><strong>作答记录：</strong>{{ item.student_answer }}</p>
                                  <p v-else-if="cause.kind" class="class-analysis__note">未保存作答文字，具体原因以已有证据为限。</p>
                                  <p v-if="item.evidence_steps?.length"><strong>已有步骤记录：</strong>{{ item.evidence_steps.join('；') }}</p>
                                  <p><strong v-if="cause.kind">原始批语：</strong>{{ item.text }}</p>
                                  <p v-for="previous in item.previous_answers" :key="previous.question_id" class="class-analysis__note">{{ previous.question_id }} 作答：{{ previous.student_answer || '未记录作答文字' }}</p>
                                  <small>{{ causeStudents(question, item.student_ids) }}</small>
                                </div>
                              </div>
                            </details>
                            <template v-else><span>{{ cause.reason }}</span><b>{{ cause.count }} 人</b></template>
                          </li>
                        </ol>
                        <button v-if="section.causes.length > 5" type="button" class="class-analysis__preview-button class-analysis__causes-toggle" :aria-expanded="expandedCauses.has(section.key)" @click="toggleCauses(section.key)">
                          {{ expandedCauses.has(section.key) ? '收起错因' : `展开其余 ${section.causes.length - 5} 条错因` }} · 共 {{ section.causes.length }} 条
                        </button>
                      </details>
                      <span v-if="!question.causeSections.length" class="class-analysis__note">{{ question.records.length ? (question.causes_grouped ? '没有可确认的共同错因，见下方原始证据' : '未记录具体错因') : '—' }}</span>
                      <details v-if="!question.structuredCauses && question.cause_review?.uncertain.length" class="class-analysis__cause-review">
                        <summary>错因待明确 · {{ evidenceStudentCount(question.cause_review.uncertain) }} 人</summary>
                        <div v-for="(item, index) in question.cause_review.uncertain" :key="index" class="class-analysis__cause-evidence">
                          <p v-if="item.student_answer">作答记录：{{ item.student_answer }}</p>
                          <p>{{ item.text }}</p><small>{{ causeStudents(question, item.student_ids) }}</small>
                        </div>
                      </details>
                      <details v-if="question.cause_review?.positive.length" class="class-analysis__cause-review">
                        <summary>未归为错因的批语 · {{ evidenceStudentCount(question.cause_review.positive) }} 人</summary>
                        <div v-for="(item, index) in question.cause_review.positive" :key="index" class="class-analysis__cause-evidence">
                          <p v-if="item.student_answer">作答记录：{{ item.student_answer }}</p>
                          <p>{{ item.text }}</p><small>{{ causeStudents(question, item.student_ids) }}</small>
                        </div>
                      </details>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>
      </template>
    </section>

    <ClassAnalysisGenerateConfirm
      v-if="confirmOpen"
      kind="causes"
      :loading="confirmLoading"
      :binding="confirmBinding"
      :error="confirmError"
      :submitting="regenerating"
      :call-count="causeAnalysis?.pending_questions ?? data?.questions.filter((q) => q.records.length).length ?? 0"
      @close="closeConfirm"
      @confirm="confirmRegenerate"
    />

  <DialogRoot :open="previewQuestion !== null" @update:open="!$event && closePreview()">
    <DialogPortal>
      <DialogOverlay class="class-analysis__overlay" />
      <DialogContent class="class-analysis__preview" data-testid="question-preview-dialog" :aria-describedby="undefined" @close-auto-focus="restorePreviewFocus">
        <div class="class-analysis__dialog-heading">
          <DialogTitle>{{ previewQuestion?.question_id }} · 原题预览</DialogTitle>
          <DialogClose class="class-analysis__link" aria-label="关闭原题预览">关闭</DialogClose>
        </div>
        <p v-if="previewLoading" role="status">正在读取原题…</p>
        <p v-else-if="previewError" class="class-analysis__error" role="alert">{{ previewError }} <button class="class-analysis__link" @click="previewQuestion && openPreview(previewQuestion)">重试</button></p>
        <template v-else-if="preview">
          <p v-if="preview.notice" class="class-analysis__note">{{ preview.notice }}</p>
          <QuestionContentRenderer :blocks="preview.rich_content.question_blocks" :fallback="preview.text" empty-label="此题尚无可预览的原题内容" image-alt="原题配图" media-mode="detail" />
        </template>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
