<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ResultsCenterStudent } from '../../api/results-center'
import { personalReportsApi, reportStatusText, type PersonalReportExam } from '../../api/personal-reports'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { useJobStore } from '../../stores/jobs'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
import StatePanel from '../design-system/StatePanel.vue'

const props = defineProps<{
  students: ResultsCenterStudent[]
  studentId: number
  sessionId: number
  reportSessionId?: number
  volumeId?: string | null
  initiallyDataOnly?: boolean
}>()
const emit = defineEmits<{
  close: [studentId: number]
  review: [studentId: number, questionId: string, reportSessionId: number]
  refreshed: []
}>()
const jobs = useJobStore()
const currentStudentId = ref(props.studentId)
const reportSession = ref(props.reportSessionId ?? props.sessionId)
const exams = ref<PersonalReportExam[]>([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const dataOnly = ref(props.initiallyDataOnly ?? false)
const iframe = ref<HTMLIFrameElement | null>(null)
const back = ref<{ focus?: () => void } | null>(null)
const ready = ref(false)
const student = computed(() => props.students.find(s => s.student_id === currentStudentId.value))
const position = computed(() => props.students.findIndex(s => s.student_id === currentStudentId.value))
const exam = computed(() => exams.value.find(e => e.session_id === reportSession.value))
const canRead = computed(() => exam.value && ['current', 'stale', 'old_prompt', 'missing'].includes(exam.value.status))
const effectiveDataOnly = computed(() => dataOnly.value || exam.value?.status === 'missing')
const src = computed(() => student.value && canRead.value ? personalReportsApi.htmlUrl(reportSession.value,
  student.value.student_id, effectiveDataOnly.value, reportSession.value === props.sessionId, exam.value?.generated_at ?? '') : '')
let controller: AbortController | null = null
let previousOverflow = ''
async function load() {
  controller?.abort()
  const next = new AbortController()
  controller = next
  loading.value = true
  error.value = ''
  exams.value = []
  try {
    const value = await personalReportsApi.exams(currentStudentId.value, props.volumeId, next.signal)
    if (!next.signal.aborted) {
      exams.value = value
      if (['current', 'stale', 'old_prompt'].includes(exam.value?.status ?? '')) dataOnly.value = false
    }
  } catch { if (!next.signal.aborted) error.value = '本学期报告暂时无法读取，请重试。' }
  finally { if (controller === next) loading.value = false }
}
watch(currentStudentId, (_id, previous) => {
  if (previous !== undefined) dataOnly.value = false
  void load()
}, { immediate: true })
watch(reportSession, () => { dataOnly.value = false; message.value = '' })
watch(src, () => { ready.value = false })
const watchedJobs = new Set<number>()
function affectsReport(job: { job_type: string; payload: Record<string, unknown> }): boolean {
  if (job.job_type === 'report_export' && job.payload.report_type === 'personal_analysis_html') return true
  return job.job_type === 'class_analysis_generate' && job.payload.session_id === props.sessionId
}
watch(() => Object.values(jobs.jobs).filter(affectsReport).map(j => `${j.id}:${j.status}`).join('|'), () => {
  for (const job of Object.values(jobs.jobs)) {
    if (!affectsReport(job)) continue
    if (!TERMINAL_JOB_STATUSES.has(job.status)) watchedJobs.add(job.id)
    else if (watchedJobs.delete(job.id)) { void load(); emit('refreshed') }
  }
}, { immediate: true })
function move(delta: number) {
  const next = props.students[position.value + delta]
  if (next) currentStudentId.value = next.student_id
}
function handleKey(key: string, editing = false): boolean {
  if (key === 'Escape') { emit('close', currentStudentId.value); return true }
  if (editing) return false
  if (key === 'ArrowLeft') { move(-1); return true }
  if (key === 'ArrowRight') { move(1); return true }
  return false
}
function keydown(event: KeyboardEvent) {
  const target = event.target as Element | null
  const editing = typeof target?.closest === 'function' && Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
  if (handleKey(event.key, editing)) { event.preventDefault(); event.stopImmediatePropagation() }
}
function receive(event: MessageEvent) {
  if (event.origin !== location.origin || event.source !== iframe.value?.contentWindow) return
  if (event.data?.type === 'personal-report:key') {
    if (['Escape', 'ArrowLeft', 'ArrowRight'].includes(event.data.key)) handleKey(event.data.key)
    return
  }
  if (reportSession.value !== props.sessionId || event.data?.type !== 'personal-report:open-review'
    || typeof event.data.questionId !== 'string') return
  emit('review', currentStudentId.value, event.data.questionId, reportSession.value)
}
async function exportHtml() {
  try {
    const job = await personalReportsApi.bundle([reportSession.value], [currentStudentId.value], '指定1人')
    await jobs.track(job)
    message.value = '已加入任务中心，完成后在任务中心下载。'
  } catch { error.value = '导出请求未能提交，请稍后重试。' }
}
onMounted(() => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', keydown)
  window.addEventListener('message', receive)
  void nextTick(() => back.value?.focus?.())
})
onBeforeUnmount(() => {
  controller?.abort()
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', keydown)
  window.removeEventListener('message', receive)
})
</script>

<template>
  <AppDialog :open="true" title="学生个人报告" layout="fullscreen" hide-header :dismissible="false">
    <section class="personal-reader" data-testid="personal-report-reader">
      <header class="personal-reader__header">
        <AppButton ref="back" variant="ghost" size="small" @click="emit('close', currentStudentId)">← 返回成绩明细（Esc）</AppButton>
        <div class="personal-reader__identity"><b>{{ student?.student_name }}</b><span>{{ student?.student_code }} · {{ student?.class_name }}</span></div>
        <span class="personal-reader__position">第 {{ position + 1 }} / {{ students.length }} 人<span>按当前排序和筛选</span></span>
        <AppButton :disabled="position <= 0" variant="ghost" size="small" @click="move(-1)">← 上一位</AppButton>
        <AppButton :disabled="position >= students.length - 1" variant="ghost" size="small" @click="move(1)">下一位 →</AppButton>
        <div class="personal-reader__actions">
          <AppButton :disabled="!exam || !['current', 'stale', 'old_prompt'].includes(exam.status)" variant="ghost" size="small" @click="exportHtml">导出本场 HTML</AppButton>
          <AppButton :disabled="!ready || !canRead" variant="ghost" size="small" @click="iframe?.contentWindow?.print()">打印</AppButton>
        </div>
      </header>
      <nav class="personal-reader__exams" aria-label="切换考试">
        <button v-for="item in exams" :key="item.session_id" :class="['personal-reader__exam', {active: reportSession === item.session_id}]"
          :aria-pressed="reportSession === item.session_id" @click="reportSession = item.session_id">
          <b>{{ item.session_name }}</b><span>{{ item.score ?? '—' }} / {{ item.max_score }} · {{ reportStatusText[item.status] }}</span>
        </button>
      </nav>
      <FeedbackBanner v-if="exam?.status === 'stale'" tone="info" description="成绩已变化，显示的是上次生成的 AI 分析；分数和班级数据为当前值。在成绩中心顶部点「AI 整理」可更新。" />
      <FeedbackBanner v-else-if="exam?.status === 'missing'" tone="info" description="AI 分析部分尚未整理：复核完成后自动整理，或在成绩中心顶部点「AI 整理」。" />
      <FeedbackBanner v-if="message" role="status" tone="success" :description="message" />
      <FeedbackBanner v-if="error" role="alert" tone="info" :description="error" />
      <StatePanel v-if="loading" kind="loading" title="正在读取个人报告…" />
      <iframe v-else-if="canRead" ref="iframe" :key="src" :src="src" class="personal-reader__body" title="个人报告正文" @load="ready = true" />
      <StatePanel
        v-else
        :kind="error ? 'error' : 'empty'"
        :title="error ? '报告暂不可用' : exam?.status === 'unavailable' ? (exam.reason ?? '本场报告暂不可用') : '本场报告尚未生成'"
        :description="exam ? '成绩与教师批语保留在成绩明细中。' : '该生在所选考试没有可查看的报告。'"
      >
        <template #actions>
          <AppButton v-if="error" variant="ghost" size="small" @click="load">重新读取</AppButton>
        </template>
      </StatePanel>
    </section>
  </AppDialog>
</template>

<style scoped>
.personal-reader{flex:1;min-height:0;background:#f1f4f6;display:flex;flex-direction:column;color:var(--ink,#24364a)}
.personal-reader__header{display:flex;gap:12px;align-items:center;padding:12px 20px;background:white;border-bottom:1px solid #dce3e8;flex-wrap:wrap}
.personal-reader__identity{display:flex;gap:8px;align-items:baseline}.personal-reader__identity b{font-size:var(--font-size-h2)}.personal-reader__identity span,.personal-reader__position{font-size:var(--font-size-caption);color:#687988}
.personal-reader__position span{display:block}.personal-reader__actions{margin-left:auto;display:flex;gap:8px}
.personal-reader__exams{display:flex;gap:8px;overflow-x:auto;padding:12px 24px;background:#fff}.personal-reader__exam{flex-shrink:0;border:1px solid #dce3e8;border-radius:7px;background:white;padding:8px 14px;text-align:left;font-size:var(--font-size-dense);cursor:pointer}.personal-reader__exam span{display:block;font-size:var(--font-size-caption);color:#73808c;margin-top:4px}.personal-reader__exam.active{border-color:#3d788f;background:#eaf3f5;color:#23596c}

.personal-reader__body{flex:1;min-height:0;border:0;width:100%}.personal-reader .state-panel{flex:1;justify-content:center;margin:24px}
</style>
