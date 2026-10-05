<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ResultsCenterStudent } from '../../api/results-center'
import { personalReportsApi, reportStatusText, type PersonalReportExam } from '../../api/personal-reports'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { useJobStore } from '../../stores/jobs'
import AnalysisConfirmDialog from '../file-center/AnalysisConfirmDialog.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { usePersonalReportGeneration } from './personal-report-generation'

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
const generation = usePersonalReportGeneration()
const currentStudentId = ref(props.studentId)
const reportSession = ref(props.reportSessionId ?? props.sessionId)
const exams = ref<PersonalReportExam[]>([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const dataOnly = ref(props.initiallyDataOnly ?? false)
const iframe = ref<HTMLIFrameElement | null>(null)
const back = ref<HTMLButtonElement | null>(null)
const ready = ref(false)
const student = computed(() => props.students.find(s => s.student_id === currentStudentId.value))
const position = computed(() => props.students.findIndex(s => s.student_id === currentStudentId.value))
const exam = computed(() => exams.value.find(e => e.session_id === reportSession.value))
const canRead = computed(() => exam.value && (['current', 'stale'].includes(exam.value.status) || (dataOnly.value && exam.value.status === 'missing')))
const src = computed(() => student.value && canRead.value ? personalReportsApi.htmlUrl(reportSession.value,
  student.value.student_id, dataOnly.value, reportSession.value === props.sessionId, exam.value?.generated_at ?? '') : '')
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
      if (['current', 'stale'].includes(exam.value?.status ?? '')) dataOnly.value = false
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
watch(() => Object.values(jobs.jobs).filter(j => j.job_type === 'report_export' &&
  j.payload.report_type === 'personal_analysis_html').map(j => `${j.id}:${j.status}`).join('|'), () => {
  for (const job of Object.values(jobs.jobs)) {
    if (job.job_type !== 'report_export' || job.payload.report_type !== 'personal_analysis_html') continue
    if (!TERMINAL_JOB_STATUSES.has(job.status)) watchedJobs.add(job.id)
    else if (watchedJobs.delete(job.id)) { void load(); emit('refreshed') }
  }
}, { immediate: true })
function move(delta: number) {
  const next = props.students[position.value + delta]
  if (next) currentStudentId.value = next.student_id
}
function handleKey(key: string, editing = false): boolean {
  if (generation.open.value) {
    if (key === 'Escape') { generation.close(); return true }
    return false
  }
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
async function prepare() { await generation.prepare(reportSession.value, [currentStudentId.value]) }
async function confirm() {
  const job = await generation.confirm()
  if (job) {
    message.value = '已加入任务中心，完成后自动更新报告。'
    if (TERMINAL_JOB_STATUSES.has(job.status)) { await load(); emit('refreshed') }
    else watchedJobs.add(job.id)
  }
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
  void nextTick(() => back.value?.focus())
})
onBeforeUnmount(() => {
  controller?.abort()
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', keydown)
  window.removeEventListener('message', receive)
})
</script>

<template>
  <Teleport to="body">
    <section class="personal-reader" role="dialog" aria-modal="true" aria-label="学生个人报告" data-testid="personal-report-reader">
      <header class="personal-reader__header">
        <button ref="back" class="pr-button" @click="emit('close', currentStudentId)">← 返回成绩明细（Esc）</button>
        <div class="personal-reader__identity"><b>{{ student?.student_name }}</b><span>{{ student?.student_code }} · {{ student?.class_name }}</span></div>
        <span class="personal-reader__position">第 {{ position + 1 }} / {{ students.length }} 人<span>按当前排序和筛选</span></span>
        <button class="pr-button" :disabled="position <= 0" @click="move(-1)">← 上一位</button>
        <button class="pr-button" :disabled="position >= students.length - 1" @click="move(1)">下一位 →</button>
        <div class="personal-reader__actions">
          <button v-if="exam?.status === 'stale'" class="pr-button" @click="prepare">重新生成（1 次叙述调用）</button>
          <button class="pr-button" :disabled="!exam || !['current', 'stale'].includes(exam.status)" @click="exportHtml">导出本场 HTML</button>
          <button class="pr-button" :disabled="!ready || !canRead" @click="iframe?.contentWindow?.print()">打印</button>
        </div>
      </header>
      <nav class="personal-reader__exams" aria-label="切换考试">
        <button v-for="item in exams" :key="item.session_id" :class="['personal-reader__exam', {active: reportSession === item.session_id}]"
          :aria-pressed="reportSession === item.session_id" @click="reportSession = item.session_id">
          <b>{{ item.session_name }}</b><span>{{ item.score ?? '—' }} / {{ item.max_score }} · {{ reportStatusText[item.status] }}</span>
        </button>
      </nav>
      <p v-if="exam?.status === 'stale'" class="personal-reader__notice">成绩已变化，显示的是上次生成的 AI 分析；分数和班级数据为当前值</p>
      <p v-if="message" class="personal-reader__message" role="status">{{ message }}</p>
      <p v-if="error || generation.error.value" class="personal-reader__notice" role="alert">{{ error || generation.error.value }}</p>
      <StatePanel v-if="loading" kind="loading" title="正在读取个人报告…" />
      <iframe v-else-if="canRead" ref="iframe" :key="src" :src="src" class="personal-reader__body" title="个人报告正文" @load="ready = true" />
      <StatePanel
        v-else
        :kind="error ? 'error' : 'empty'"
        :title="error ? '报告暂不可用' : exam?.status === 'unavailable' ? (exam.reason ?? '本场报告暂不可用') : '本场报告尚未生成'"
        :description="exam ? '成绩与教师批语保留在成绩明细中。' : '该生在所选考试没有可查看的报告。'"
      >
        <template #actions>
          <template v-if="exam?.status === 'missing'">
            <button class="pr-button pr-button--primary" @click="prepare">生成该生报告（预估后确认）</button>
            <button class="pr-button" @click="dataOnly = true">先看数据版</button>
          </template>
          <button v-if="error" class="pr-button" @click="load">重新读取</button>
        </template>
      </StatePanel>
      <AnalysisConfirmDialog v-if="generation.open.value" :preflight="generation.preflight.value" :loading="generation.loading.value"
        report-type="personal_analysis_html" :submitting="generation.submitting.value" @close="generation.close" @confirm="confirm" />
    </section>
  </Teleport>
</template>

<style scoped>
.personal-reader{position:fixed;inset:0;z-index:100;background:#f1f4f6;display:flex;flex-direction:column;color:var(--ink,#24364a)}
.personal-reader__header{display:flex;gap:12px;align-items:center;padding:12px 20px;background:white;border-bottom:1px solid #dce3e8;flex-wrap:wrap}
.personal-reader__identity{display:flex;gap:8px;align-items:baseline}.personal-reader__identity b{font-size:var(--font-size-h2)}.personal-reader__identity span,.personal-reader__position{font-size:var(--font-size-caption);color:#687988}
.personal-reader__position span{display:block}.personal-reader__actions{margin-left:auto;display:flex;gap:8px}
.pr-button{border:1px solid #d5dee6;border-radius:6px;background:white;color:inherit;padding:7px 12px;cursor:pointer;font:inherit;font-size:var(--font-size-dense)}.pr-button:hover{background:#f2f7fb}.pr-button:disabled{opacity:.45;cursor:default}.pr-button--primary{background:#244c65;color:white}
.personal-reader__exams{display:flex;gap:8px;overflow-x:auto;padding:12px 24px;background:#fff}.personal-reader__exam{flex-shrink:0;border:1px solid #dce3e8;border-radius:7px;background:white;padding:8px 14px;text-align:left;font-size:var(--font-size-dense);cursor:pointer}.personal-reader__exam span{display:block;font-size:var(--font-size-caption);color:#73808c;margin-top:4px}.personal-reader__exam.active{border-color:#3d788f;background:#eaf3f5;color:#23596c}
.personal-reader__notice,.personal-reader__message{margin:0;padding:9px 24px;background:#fff4d8;color:#866221;font-size:var(--font-size-dense)}.personal-reader__message{background:#eaf5ef;color:#32664a}
.personal-reader__body{flex:1;min-height:0;border:0;width:100%}.personal-reader .state-panel{flex:1;justify-content:center;margin:24px}.personal-reader .state-panel .pr-button{margin:0 6px}
</style>
