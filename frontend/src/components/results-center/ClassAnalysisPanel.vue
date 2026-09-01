<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  classAnalysisApi,
  type ClassAnalysisQuestion,
  type ClassAnalysisRecord,
  type ClassAnalysisResponse,
} from '../../api/class-analysis'
import { ApiError } from '../../api/errors'
import { jobApi, TERMINAL_JOB_STATUSES } from '../../api/jobs'
import {
  modelProfilesApi,
  type ModelTaskBinding,
} from '../../api/model-profiles'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  sessionId: number | null
}>()

const jobStore = useJobStore()
const analysis = ref<ClassAnalysisResponse | null>(null)
const loadState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
const actionError = ref('')
const settingsSaving = ref(false)
const confirmOpen = ref(false)
const confirmLoading = ref(false)
const confirmBinding = ref<ModelTaskBinding | null>(null)
const confirmError = ref('')
const regenerating = ref(false)
let loadGeneration = 0

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
const narrative = computed(() => analysis.value?.narrative ?? null)

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
    actionError.value = ''
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
  if (sessionId === null) {
    analysis.value = null
    loadState.value = 'idle'
    return
  }
  if (analysis.value === null) loadState.value = 'loading'
  errorMessage.value = ''
  try {
    const next = await classAnalysisApi.getClassAnalysis(sessionId)
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    analysis.value = next
    loadState.value = 'ready'
    void ensureTracked(next.active_job_id)
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    loadState.value = 'error'
    errorMessage.value = '班级分析暂时无法读取，请稍后重试。'
  }
}

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

async function toggleAutoGenerate(event: Event): Promise<void> {
  const input = event.target instanceof HTMLInputElement ? event.target : null
  const sessionId = props.sessionId
  if (input === null || sessionId === null || settingsSaving.value) return
  const checked = input.checked
  settingsSaving.value = true
  actionError.value = ''
  try {
    const saved = await classAnalysisApi.updateSettings(sessionId, checked)
    if (analysis.value !== null && props.sessionId === sessionId) {
      analysis.value = { ...analysis.value, auto_generate: saved.auto_generate }
    }
  } catch {
    input.checked = !checked
    actionError.value = '自动生成设置未能保存，请稍后重试。'
  } finally {
    settingsSaving.value = false
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
    const job = await classAnalysisApi.regenerate(sessionId)
    if (props.sessionId !== sessionId) return
    jobStore.track(job)
    closeConfirm()
    await load()
  } catch (error) {
    if (props.sessionId !== sessionId) return
    confirmError.value = error instanceof ApiError
      && error.code === 'content_generation_model_not_configured'
      ? '未配置内容生成模型，请前往 设置→模型配置 绑定后重试。'
      : '重新生成请求未能提交，请稍后重试。'
  } finally {
    regenerating.value = false
  }
}

function studentNameForAlias(alias: string): string {
  const match = /^S(\d+)$/i.exec(alias.trim())
  if (!match) return alias
  const index = Number(match[1]) - 1
  const student = data.value?.students[index]
  return student?.student_name ?? alias
}

function formatScore(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(1).replace(/\.0$/, '')
}

function formatPercent(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${Math.round(percent)}%`
}

function formatTime(value: string | null): string {
  if (!value) return '时间未记录'
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}

function recordSummary(record: ClassAnalysisRecord): string {
  const reason = record.error_summary
    ?? record.deduction_reason
    ?? record.error_category
    ?? '有扣分'
  return `${record.student_name}：${reason}`
}

function questionRecordSummary(question: ClassAnalysisQuestion): string {
  if (question.records.length === 0) return '暂无典型扣分记录'
  return question.records.map(recordSummary).join('；')
}

const SEVERITY_LABELS: Record<string, string> = {
  high: '重点关注',
  medium: '需要留意',
  low: '参考',
}

function severityLabel(severity: string): string {
  return SEVERITY_LABELS[severity] ?? severity
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
          <input
            type="checkbox"
            :checked="analysis.auto_generate"
            :disabled="settingsSaving"
            data-testid="auto-generate-toggle"
            @change="toggleAutoGenerate"
          >
          <span>阅卷完成后自动生成班级分析</span>
        </label>
        <div class="class-analysis__toolbar-actions">
          <span v-if="analysis.generated_at">
            生成于 {{ formatTime(analysis.generated_at) }}
          </span>
          <AppButton
            variant="secondary"
            data-testid="regenerate-open"
            :disabled="!canRegenerate"
            @click="openRegenerateConfirm"
          >
            重新生成
          </AppButton>
        </div>
      </div>

      <p v-if="actionError" class="class-analysis__error" role="alert">
        {{ actionError }}
      </p>

      <div
        v-if="analysis?.stale"
        class="class-analysis__banner"
        role="status"
        data-testid="stale-banner"
      >
        <span>成绩已更新，分析可能不是最新。</span>
        <button
          type="button"
          :disabled="!canRegenerate"
          @click="openRegenerateConfirm"
        >
          重新生成
        </button>
      </div>

      <p
        v-if="analysis?.small_sample"
        class="class-analysis__note"
        data-testid="small-sample-note"
      >
        本场参考人数较少，比率指标解读需谨慎。
      </p>

      <div
        v-if="loadState === 'loading'"
        class="results-state-panel"
        role="status"
      >
        <strong>正在读取班级分析</strong>
        <span>首次生成后，这里会展示班级整体表现。</span>
      </div>

      <div
        v-else-if="loadState === 'error'"
        class="results-state-panel results-state-panel--error"
        role="alert"
      >
        <strong>班级分析暂时无法读取</strong>
        <span>{{ errorMessage }}</span>
        <AppButton variant="secondary" @click="load">重新加载</AppButton>
      </div>

      <div
        v-else-if="analysis?.status === 'no_data'"
        class="results-state-panel"
        data-testid="class-analysis-empty"
      >
        <strong>本场次尚无已批改成绩</strong>
        <span>完成批改后，班级分析会在这里生成。</span>
      </div>

      <div
        v-else-if="generating"
        class="results-state-panel"
        role="status"
        data-testid="class-analysis-generating"
      >
        <strong>班级分析生成中…</strong>
        <span>完成后页面会自动更新，无需手动刷新。</span>
      </div>

      <template v-else-if="analysis && data">
        <section class="class-analysis__section" aria-labelledby="class-analysis-overview-title">
          <div class="class-analysis__section-heading">
            <div>
              <p class="results-center__eyebrow">成绩概览</p>
              <h2 id="class-analysis-overview-title">{{ data.exam.title }}</h2>
              <p>
                <template v-if="data.exam.subject">{{ data.exam.subject }} · </template>
                满分 {{ formatScore(data.exam.full_score) }} 分 ·
                实际参考 {{ data.present }} 人
                <template v-if="data.roster_absent.length > 0">
                  · 名册另有 {{ data.roster_absent.length }} 人未检测到答卷
                </template>
              </p>
              <p v-if="data.exam.graded_at">批改完成 {{ formatTime(data.exam.graded_at) }}</p>
            </div>
          </div>

          <div class="class-analysis__stats">
            <div class="class-analysis__stat">
              <strong>{{ formatScore(data.score_distribution.avg) }}</strong>
              <span>平均分</span>
            </div>
            <div class="class-analysis__stat">
              <strong>{{ formatScore(data.score_distribution.median) }}</strong>
              <span>中位数</span>
            </div>
            <div class="class-analysis__stat">
              <strong>
                {{ formatScore(data.score_distribution.max) }}
                <small>/ {{ formatScore(data.score_distribution.min) }}</small>
              </strong>
              <span>最高 / 最低</span>
            </div>
            <div class="class-analysis__stat">
              <strong>{{ formatPercent(data.score_distribution.pass_rate) }}</strong>
              <span>及格率</span>
            </div>
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
              <p>得分率偏低的题目排在前面，建议作为讲评顺序。</p>
            </div>
            <span class="class-analysis__privacy">含学生姓名，请勿直接外发</span>
          </div>
          <div class="results-table-wrap">
            <table class="class-analysis__questions">
              <thead>
                <tr>
                  <th scope="col">题号</th>
                  <th scope="col">满分</th>
                  <th scope="col">班级得分率</th>
                  <th scope="col">得分情况与主要错因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="question in data.questions" :key="question.question_id">
                  <th scope="row">
                    <strong>{{ question.question_id }}</strong>
                    <span v-if="question.stem_summary" class="class-analysis__stem">
                      {{ question.stem_summary }}
                    </span>
                  </th>
                  <td>{{ formatScore(question.max_score) }}</td>
                  <td>
                    <span
                      class="class-analysis__ratebar"
                      :class="`class-analysis__ratebar--${rateTone(question.class_rate)}`"
                      aria-hidden="true"
                    >
                      <i :style="{ width: formatPercent(question.class_rate) }"></i>
                    </span>
                    {{ formatPercent(question.class_rate) }}
                  </td>
                  <td class="class-analysis__why">{{ questionRecordSummary(question) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="class-analysis__section" aria-labelledby="class-analysis-students-title">
          <div class="class-analysis__section-heading">
            <div>
              <p class="results-center__eyebrow">学生丢分清单</p>
              <h2 id="class-analysis-students-title">逐人失分点</h2>
            </div>
            <span class="class-analysis__privacy">含学生姓名，请勿直接外发</span>
          </div>
          <div class="class-analysis__students">
            <article
              v-for="student in data.students"
              :key="student.student_name"
              class="class-analysis__student"
            >
              <div class="class-analysis__student-head">
                <span>
                  <strong>{{ student.student_name }}</strong>
                  <template v-if="student.student_code">　{{ student.student_code }}</template>
                  　{{ formatScore(student.total_score) }} 分
                  <template v-if="student.rank !== null"> · 第 {{ student.rank }} 名</template>
                  <span v-if="student.needs_review" class="class-analysis__badge">含待复核</span>
                </span>
              </div>
              <ul v-if="student.lost.length > 0">
                <li v-for="lost in student.lost" :key="lost.question_id">
                  第 {{ lost.question_id }} 题丢 {{ formatScore(lost.lost_points) }} 分
                  <template v-if="lost.record">
                    ——{{ lost.record.error_summary ?? lost.record.deduction_reason ?? lost.record.error_category ?? '' }}
                  </template>
                </li>
              </ul>
              <p v-else>本场没有失分。</p>
            </article>
          </div>
        </section>

        <section class="class-analysis__section" aria-labelledby="class-analysis-narrative-title">
          <div class="class-analysis__section-heading">
            <div>
              <p class="results-center__eyebrow">整体解读</p>
              <h2 id="class-analysis-narrative-title">
                AI 班级分析
                <span class="class-analysis__ai-badge">AI 分析 · 仅供参考</span>
              </h2>
            </div>
          </div>

          <div
            v-if="!narrative"
            class="class-analysis__narrative-missing"
            data-testid="narrative-missing"
          >
            <p>{{ analysis.narrative_failed ? 'AI 分析生成失败，可重新生成。' : 'AI 分析未生成，可重新生成。' }}</p>
            <AppButton
              variant="secondary"
              data-testid="narrative-regenerate"
              :disabled="!canRegenerate"
              @click="openRegenerateConfirm"
            >
              重新生成
            </AppButton>
          </div>

          <template v-else>
            <div v-if="narrative.key_findings.length > 0" class="class-analysis__findings">
              <article
                v-for="finding in narrative.key_findings"
                :key="finding.title"
                class="class-analysis__finding"
                :data-severity="finding.severity"
              >
                <strong>
                  {{ finding.title }}
                  <span class="class-analysis__severity">{{ severityLabel(finding.severity) }}</span>
                </strong>
                <p>{{ finding.detail }}</p>
              </article>
            </div>

            <ol v-if="narrative.common_issues.length > 0" class="class-analysis__issues">
              <li v-for="issue in narrative.common_issues" :key="issue.title">
                <strong>{{ issue.title }}</strong>
                <p class="class-analysis__evidence">{{ issue.evidence }}</p>
                <p class="class-analysis__action"><b>讲评动作：</b>{{ issue.teaching_action }}</p>
              </li>
            </ol>

            <div v-if="narrative.student_notes.length > 0" class="class-analysis__notes">
              <p class="class-analysis__privacy">含学生姓名，请勿直接外发</p>
              <article
                v-for="note in narrative.student_notes"
                :key="note.alias"
                class="class-analysis__note-card"
              >
                <div class="class-analysis__student-head">
                  <span>
                    <strong>{{ studentNameForAlias(note.alias) }}</strong>
                    <span
                      v-for="flag in note.flags"
                      :key="flag"
                      class="class-analysis__badge"
                    >{{ flag }}</span>
                  </span>
                </div>
                <p>{{ note.note }}</p>
                <p class="class-analysis__suggestion"><b>建议（AI 草稿）：</b>{{ note.suggestion }}</p>
              </article>
            </div>

            <div v-if="narrative.grouping_advice" class="class-analysis__grouping">
              <strong>分组教学建议</strong>
              <p>{{ narrative.grouping_advice }}</p>
            </div>
          </template>
        </section>
      </template>
    </template>

    <div
      v-if="confirmOpen"
      class="class-analysis__dialog-backdrop"
      data-testid="regenerate-confirm-backdrop"
      @click.self="closeConfirm"
    >
      <form
        class="class-analysis__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="regenerate-confirm-title"
        data-testid="regenerate-confirm-dialog"
        @submit.prevent="confirmRegenerate"
      >
        <div class="class-analysis__dialog-heading">
          <div>
            <p class="results-center__eyebrow">AI 内容生成确认</p>
            <h3 id="regenerate-confirm-title">重新生成班级分析</h3>
          </div>
          <button type="button" class="class-analysis__link" @click="closeConfirm">关闭</button>
        </div>

        <p v-if="confirmLoading" role="status">正在读取模型配置…</p>

        <template v-else>
          <p>
            将调用 1 次内容生成模型（{{
              confirmBinding?.profile_name ?? '未配置'
            }}<template v-if="confirmBinding?.model"> · {{ confirmBinding.model }}</template>），
            实际费用取决于服务商定价。AI 分析内容仅供参考，建议抽查后再使用。
          </p>
          <p
            v-if="confirmBinding && !confirmBinding.profile_name"
            class="class-analysis__error"
            role="alert"
            data-testid="regenerate-not-configured"
          >
            未配置内容生成模型，请前往 设置→模型配置 绑定后重试。
          </p>
          <p v-if="confirmError" class="class-analysis__error" role="alert" data-testid="regenerate-error">
            {{ confirmError }}
          </p>
        </template>

        <div class="class-analysis__dialog-actions">
          <span>确认后才会发起模型调用并产生费用。</span>
          <div>
            <AppButton variant="secondary" @click="closeConfirm">取消</AppButton>
            <AppButton
              variant="primary"
              type="submit"
              data-testid="regenerate-confirm"
              :disabled="
                confirmLoading
                  || regenerating
                  || confirmBinding?.profile_name == null
                  || confirmBinding?.profile_name === ''
              "
              :loading="regenerating"
              loading-label="正在提交"
            >
              确认重新生成
            </AppButton>
          </div>
        </div>
      </form>
    </div>
  </section>
</template>
