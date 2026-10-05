<script setup lang="ts">
import AppIconButton from '../design-system/AppIconButton.vue'
import AppButton from '../design-system/AppButton.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ResultsCenterStudent } from '../../api/results-center'
import type { SessionSummary } from '../../api/sessions'
import { matchesReportStudent, personalReportsApi, type PersonalReportState } from '../../api/personal-reports'
import { useJobStore } from '../../stores/jobs'

const props = defineProps<{ students: ResultsCenterStudent[]; sessions: SessionSummary[]; sessionId: number; volumeLabel?: string }>()
const emit = defineEmits<{ close: []; submitted: [] }>()
const jobs = useJobStore()
const mode = ref('all')
const classes = ref<string[]>([])
const picked = ref<number[]>([])
const exams = ref<number[]>([props.sessionId])
const query = ref('')
const states = ref<Record<number, PersonalReportState[]>>({})
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const dialog = ref<HTMLElement | null>(null)
const controller = new AbortController()
let previousFocus: HTMLElement | null = null
const classNames = computed(() => [...new Set(props.students.map(s => s.class_name || '未分班'))])
const selected = computed(() => props.students.filter(s => mode.value === 'all' ||
  (mode.value === 'class' ? classes.value.includes(s.class_name || '未分班') : picked.value.includes(s.student_id))))
const groups = computed(() => classNames.value.map(name => ({ name,
  students: props.students.filter(s => (s.class_name || '未分班') === name && matchesReportStudent(s, query.value)) })).filter(g => g.students.length))
const chosenExams = computed(() => props.sessions.filter(s => exams.value.includes(s.id)))
const available = (sid: number) => selected.value.filter(s => states.value[sid]?.some(item =>
  item.student_id === s.student_id && ['current', 'stale'].includes(item.status))).length
const missingCount = computed(() => exams.value.reduce((sum, sid) => sum + selected.value.length - available(sid), 0))
const scopeLabel = computed(() => mode.value === 'all' ? `全部_${selected.value.length}人`
  : mode.value === 'class' ? `${classes.value.join('、')}_${selected.value.length}人` : `指定${selected.value.length}人`)
const filename = computed(() => selected.value.length === 1 && chosenExams.value.length === 1 && available(chosenExams.value[0]!.id) === 1
  ? `${selected.value[0]!.student_code || selected.value[0]!.student_id}_${selected.value[0]!.student_name}_${chosenExams.value[0]!.name}_个人报告.html`
  : chosenExams.value.length === 1 ? `${chosenExams.value[0]!.name}_个人报告_${scopeLabel.value}.zip`
    : `${props.volumeLabel || '本学期'}_个人报告_${exams.value.length}场_${selected.value.length}人.zip`)
function toggleClass(group: {students: ResultsCenterStudent[]}) {
  const ids = group.students.map(s => s.student_id)
  const all = ids.every(id => picked.value.includes(id))
  picked.value = all ? picked.value.filter(id => !ids.includes(id)) : [...new Set([...picked.value, ...ids])]
}
async function submit() {
  if (!selected.value.length || !exams.value.length || submitting.value || loading.value) return
  submitting.value = true
  try {
    const job = await personalReportsApi.bundle(exams.value, selected.value.map(s => s.student_id), scopeLabel.value)
    await jobs.track(job)
    emit('submitted')
  } catch { error.value = '导出请求未能提交，请稍后重试。' }
  finally { submitting.value = false }
}
function keyboard(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); emit('close') }
  if (event.key !== 'Tab' || !dialog.value) return
  const list = [...dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')]
  if (event.shiftKey && document.activeElement === list[0]) { event.preventDefault(); list[list.length - 1]?.focus() }
  else if (!event.shiftKey && document.activeElement === list[list.length - 1]) { event.preventDefault(); list[0]?.focus() }
}
onMounted(async () => {
  previousFocus = document.activeElement as HTMLElement | null
  document.addEventListener('keydown', keyboard, true)
  await nextTick()
  dialog.value?.querySelector<HTMLElement>('button')?.focus()
  const results = await Promise.allSettled(props.sessions.map(async session => {
    const value = await personalReportsApi.states(session.id, controller.signal)
    if (!controller.signal.aborted) states.value[session.id] = value
  }))
  if (!controller.signal.aborted) {
    if (results.some(r => r.status === 'rejected')) error.value = '部分考试的报告状态无法读取，请关闭后重试。'
    loading.value = false
  }
})
onBeforeUnmount(() => {
  controller.abort()
  document.removeEventListener('keydown', keyboard, true)
  void nextTick(() => { if (previousFocus?.isConnected) previousFocus.focus() })
})
</script>

<template>
  <Teleport to="body">
    <div class="personal-export-backdrop" @click.self="emit('close')">
      <form ref="dialog" class="personal-export-dialog" role="dialog" aria-modal="true" aria-labelledby="personal-export-title" @submit.prevent="submit">
        <header><h2 id="personal-export-title">批量导出个人报告</h2><AppIconButton label="关闭" @click="emit('close')" icon="close" /></header>
                <fieldset><legend>学生范围</legend>
          <label><input v-model="mode" type="radio" value="all"> 全部学生（{{ students.length }} 人）</label>
          <label><input v-model="mode" type="radio" value="class"> 按班级</label>
          <label><input v-model="mode" type="radio" value="pick"> 指定学生</label>
          <div v-if="mode === 'class'" class="pe-classes"><label v-for="name in classNames" :key="name"><input v-model="classes" type="checkbox" :value="name"> {{ name }}</label></div>
          <div v-if="mode === 'pick'" class="pe-pick">
            <input v-model="query" class="app-input" aria-label="搜索学生" placeholder="姓名、学号、班级或拼音首字母">
            <div class="pe-chips"><button v-for="s in students.filter(s => picked.includes(s.student_id))" :key="s.student_id" type="button" @click="picked = picked.filter(id => id !== s.student_id)">{{ s.student_name }} ×</button></div>
            <div class="pe-list"><div v-for="group in groups" :key="group.name"><div class="pe-group"><b>{{ group.name }}（{{ group.students.length }} 人）</b><AppButton type="button" variant="ghost" size="small" @click="toggleClass(group)">全选 / 取消本班</AppButton></div>
              <label v-for="s in group.students" :key="s.student_id"><input v-model="picked" type="checkbox" :value="s.student_id"> {{ s.student_name }} <span>{{ s.student_code }}</span></label>
            </div></div>
          </div>
        </fieldset>
        <fieldset><legend>考试（可多选）</legend><div class="pe-exams">
          <label v-for="session in sessions" :key="session.id"><input v-model="exams" type="checkbox" :value="session.id"> {{ session.name }}{{ session.id === sessionId ? '（本场）' : '' }}<span>{{ states[session.id] ? `可导出 ${available(session.id)} 人` : '读取中…' }}</span></label>
        </div></fieldset>
        <fieldset><legend>格式</legend><p>每人一份 HTML，多人打包为 ZIP。</p></fieldset>
        <div class="pe-summary" aria-live="polite">将导出 <b>{{ selected.length }} 人 × {{ exams.length }} 场</b>，其中 {{ loading ? '…' : missingCount }} 份未生成或不可生成，不导出，会列入清单。</div>
        <p class="pe-filename">文件名：{{ filename }}</p>
        <p v-if="error" role="alert" class="pe-error">{{ error }}</p>
        <footer><AppButton type="button" variant="secondary" @click="emit('close')">取消</AppButton><button class="pe-primary" type="submit" :disabled="!selected.length || !exams.length || loading || submitting || !!error">{{ submitting ? '正在提交…' : '确认导出' }}</button></footer>
      </form>
    </div>
  </Teleport>
</template>

<style scoped>
.personal-export-backdrop{position:fixed;inset:0;z-index:150;background:var(--color-overlay-mask);display:grid;place-items:center;padding:20px}
.personal-export-dialog{width:min(720px,100%);max-height:90vh;overflow:auto;background:var(--color-bg-surface);border-radius:var(--radius-overlay);padding:var(--space-5);color:var(--color-text-primary);box-shadow:var(--shadow-overlay);font-size:var(--font-size-body)}
header,footer{display:flex;align-items:center;justify-content:space-between;gap:10px}h2{font-size:var(--font-size-h3);font-weight:var(--font-weight-semibold);margin:0}
fieldset{border:0;border-top:1px solid #e2e8ed;padding:16px 0;margin:0}legend{float:left;width:100%;font-weight:var(--font-weight-semibold);margin-bottom:12px}label{display:inline-flex;gap:5px;align-items:center;margin:0 20px 10px 0}input{accent-color:#316982}.pe-classes{clear:both;padding-top:8px}.pe-pick{clear:both}.pe-pick>.app-input{width:100%;margin-bottom:8px}.pe-list{max-height:200px;overflow:auto;border:1px solid #e0e7ed;border-radius:6px}.pe-list label{margin:6px 10px;width:calc(50% - 20px);font-size:var(--font-size-dense)}.pe-list label span{color:#84919c}.pe-group{display:flex;justify-content:space-between;padding:6px 10px;background:#f5f8fa;font-size:var(--font-size-caption)}.pe-chips{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px}.pe-exams{clear:both;display:flex;flex-direction:column}.pe-exams label{display:flex;margin-right:0}.pe-exams span{margin-left:auto;font-size:var(--font-size-caption);color:#768b99}.pe-summary{background:#edf5f8;border-radius:6px;padding:12px;font-size:var(--font-size-dense)}.pe-filename{font-size:var(--font-size-caption);color:#71828e;overflow-wrap:anywhere}.pe-error{color:#a6462a}footer{justify-content:flex-end;padding-top:12px}
</style>
