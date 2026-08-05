<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type { GraphQueryInput } from '../../api/graph'
import type { SessionSummary } from '../../api/sessions'
import type { StudentSummary } from '../../api/students'

type StudentDecision = 'filter' | 'include' | 'exclude'

const props = withDefaults(defineProps<{
  sessions: SessionSummary[]
  currentSessionId: number | null
  students: StudentSummary[]
  modelValue: GraphQueryInput | null
  applying: boolean
  applyLabel?: string
  scoreProfiles?: Record<string, Record<string, unknown>>
}>(), { applyLabel: '立即更新', scoreProfiles: () => ({}) })

const emit = defineEmits<{
  apply: [query: GraphQueryInput]
}>()

const examMode = ref<'current' | 'manual' | 'cross_exam'>('current')
const manualSessionIds = ref<number[]>([])
const classIds = ref<string[]>([])
const scoreMin = ref<string>('')
const scoreMax = ref<string>('')
const useHistory = ref(true)
const includeStudentIds = ref<string[]>([])
const excludeStudentIds = ref<string[]>([])
const search = ref('')
const rosterOpen = ref(false)
const validationMessage = ref('')
let synchronizing = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

const classes = computed(() => [...new Set(
  props.students.flatMap((student) => student.class_name ? [student.class_name] : []),
)].sort((left, right) => left.localeCompare(right, 'zh-CN')))

const visibleStudents = computed(() => {
  const query = search.value.trim().toLocaleLowerCase('zh-CN')
  return props.students.filter((student) => {
    if (classIds.value.length && (!student.class_name || !classIds.value.includes(student.class_name))) return false
    return !query || `${student.student_code} ${student.name}`
      .toLocaleLowerCase('zh-CN').includes(query)
  })
})

const currentSessionName = computed(() => props.sessions.find(
  (item) => item.id === props.currentSessionId,
)?.name ?? '尚未选择考试')

const snapshotText = computed(() => {
  const exam = examMode.value === 'current'
    ? currentSessionName.value
    : examMode.value === 'manual'
      ? `${manualSessionIds.value.length} 场指定考试`
      : '全部可用考试'
  const roster = classIds.value.length ? `${classIds.value.length} 个班级` : '全部班级'
  const range = scoreMin.value || scoreMax.value
    ? `得分率 ${scoreMin.value || '0'}%–${scoreMax.value || '100'}%`
    : '全部得分率'
  const manual = includeStudentIds.value.length || excludeStudentIds.value.length
    ? `手动 +${includeStudentIds.value.length} / −${excludeStudentIds.value.length}`
    : '未手动调整'
  return `${exam} · ${roster} · ${range} · ${manual} · ${useHistory.value ? '缺考参考历史' : '仅本次成绩'}`
})

watch(
  () => props.modelValue,
  (query) => {
    if (!query) return
    synchronizing = true
    examMode.value = query.exam_scope.mode
    manualSessionIds.value = query.exam_scope.mode === 'manual'
      ? [...query.exam_scope.session_ids]
      : []
    classIds.value = query.scope.mode === 'class'
      ? [...(query.scope.class_ids ?? (query.scope.class_id ? [query.scope.class_id] : []))]
      : []
    scoreMin.value = query.scope.score_rate_min === null || query.scope.score_rate_min === undefined
      ? '' : String(Math.round(query.scope.score_rate_min * 1000) / 10)
    scoreMax.value = query.scope.score_rate_max === null || query.scope.score_rate_max === undefined
      ? '' : String(Math.round(query.scope.score_rate_max * 1000) / 10)
    includeStudentIds.value = [...(query.scope.include_student_ids ?? [])]
    excludeStudentIds.value = [...(query.scope.exclude_student_ids ?? [])]
    useHistory.value = query.scope.use_historical_fallback !== false
    void nextTick(() => { synchronizing = false })
  },
  { immediate: true, deep: true },
)

function rate(value: string): number | undefined {
  if (!value.trim()) return undefined
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return Number.NaN
  return parsed / 100
}

function decision(studentId: string): StudentDecision {
  if (includeStudentIds.value.includes(studentId)) return 'include'
  if (excludeStudentIds.value.includes(studentId)) return 'exclude'
  return 'filter'
}

function setDecision(studentId: string, next: StudentDecision): void {
  includeStudentIds.value = includeStudentIds.value.filter((item) => item !== studentId)
  excludeStudentIds.value = excludeStudentIds.value.filter((item) => item !== studentId)
  if (next === 'include') includeStudentIds.value.push(studentId)
  if (next === 'exclude') excludeStudentIds.value.push(studentId)
}

function toggleClass(className: string): void {
  classIds.value = classIds.value.includes(className)
    ? classIds.value.filter((item) => item !== className)
    : [...classIds.value, className].sort((left, right) => left.localeCompare(right, 'zh-CN'))
  const allowed = new Set(props.students.filter((student) => (
    !classIds.value.length || (student.class_name && classIds.value.includes(student.class_name))
  )).map((student) => String(student.id)))
  includeStudentIds.value = includeStudentIds.value.filter((id) => allowed.has(id))
  excludeStudentIds.value = excludeStudentIds.value.filter((id) => allowed.has(id))
}

function sourceLabel(studentId: string): string {
  const source = props.scoreProfiles[studentId]?.score_rate_source
  if (source === 'current_exam') return '本次成绩'
  if (source === 'historical_fallback') return '历史参考'
  return '无成绩'
}

const groupedStudents = computed(() => {
  const groups = new Map<string, StudentSummary[]>()
  for (const student of visibleStudents.value) {
    const manual = decision(String(student.id))
    const label = manual === 'include'
      ? '手动增加'
      : manual === 'exclude'
        ? '手动取消'
        : sourceLabel(String(student.id))
    groups.set(label, [...(groups.get(label) ?? []), student])
  }
  return [...groups.entries()]
})

function toggleSession(sessionId: number): void {
  manualSessionIds.value = manualSessionIds.value.includes(sessionId)
    ? manualSessionIds.value.filter((item) => item !== sessionId)
    : [...manualSessionIds.value, sessionId]
}

function apply(): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  validationMessage.value = ''
  const minimum = rate(scoreMin.value)
  const maximum = rate(scoreMax.value)
  if (Number.isNaN(minimum) || Number.isNaN(maximum)) {
    validationMessage.value = '得分率请输入 0–100 之间的数字'
    return
  }
  if (minimum !== undefined && maximum !== undefined && minimum > maximum) {
    validationMessage.value = '得分率下限不能大于上限'
    return
  }
  let examScope: GraphQueryInput['exam_scope']
  if (examMode.value === 'current') {
    if (props.currentSessionId === null) {
      validationMessage.value = '请先在顶部选择当前考试'
      return
    }
    examScope = { mode: 'current', session_ids: [props.currentSessionId] }
  } else if (examMode.value === 'manual') {
    if (!manualSessionIds.value.length) {
      validationMessage.value = '请至少选择一场考试'
      return
    }
    examScope = { mode: 'manual', session_ids: [...manualSessionIds.value] }
  } else {
    examScope = { mode: 'cross_exam' }
  }
  emit('apply', {
    exam_scope: examScope,
    scope: {
      mode: classIds.value.length ? 'class' : 'all',
      ...(classIds.value.length ? { class_ids: [...classIds.value] } : {}),
      ...(minimum !== undefined ? { score_rate_min: minimum } : {}),
      ...(maximum !== undefined ? { score_rate_max: maximum } : {}),
      include_student_ids: [...includeStudentIds.value],
      exclude_student_ids: [...excludeStudentIds.value],
      use_historical_fallback: useHistory.value,
    },
  })
}

watch(
  [examMode, manualSessionIds, classIds, scoreMin, scoreMax, useHistory, includeStudentIds, excludeStudentIds],
  () => {
    if (synchronizing) return
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(apply, 420)
  },
  { deep: true },
)

onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <section class="evidence-scope" aria-labelledby="evidence-scope-title">
    <div class="evidence-scope__snapshot">
      <div>
        <span>当前证据范围</span>
        <strong>{{ snapshotText }}</strong>
      </div>
      <button type="button" class="quiet-button" @click="rosterOpen = !rosterOpen">
        {{ rosterOpen ? '收起学生调整' : '精细调整学生' }}
      </button>
    </div>

    <div class="evidence-scope__controls">
      <label>
        <span id="evidence-scope-title">考试</span>
        <select v-model="examMode">
          <option value="current">当前考试</option>
          <option value="manual">指定考试</option>
          <option value="cross_exam">全部可用考试</option>
        </select>
      </label>
      <details class="evidence-scope__classes">
        <summary>班级 · {{ classIds.length ? `${classIds.length} 个` : '全部' }}</summary>
        <div>
          <label v-for="name in classes" :key="name">
            <input type="checkbox" :checked="classIds.includes(name)" @change="toggleClass(name)">
            <span>{{ name }}</span>
          </label>
        </div>
      </details>
      <fieldset class="evidence-scope__range">
        <legend>得分率区间</legend>
        <label><span>最低</span><input v-model="scoreMin" inputmode="decimal" placeholder="0" aria-label="最低得分率"><i>%</i></label>
        <b>—</b>
        <label><span>最高</span><input v-model="scoreMax" inputmode="decimal" placeholder="100" aria-label="最高得分率"><i>%</i></label>
      </fieldset>
      <label class="evidence-scope__history">
        <input v-model="useHistory" type="checkbox">
        <span><strong>缺考时参考历史</strong><small>仅使用目标考试之前的成绩与相似知识点证据</small></span>
      </label>
      <button type="button" class="primary-button" data-testid="apply-evidence-scope" @click="apply">
        {{ applying ? '正在更新' : applyLabel }}
      </button>
    </div>

    <div v-if="examMode === 'manual'" class="evidence-scope__sessions">
      <button v-for="session in sessions" :key="session.id" type="button" :class="{ active: manualSessionIds.includes(session.id) }" @click="toggleSession(session.id)">
        {{ session.name }}
      </button>
    </div>

    <div v-if="rosterOpen" class="evidence-scope__roster">
      <header>
        <div><strong>学生精细调整</strong><p>筛选后仍可强制增加或取消某些学生；“按条件”会恢复自动判断。</p></div>
        <input v-model="search" type="search" placeholder="搜索姓名或学号" aria-label="搜索学生">
      </header>
      <div class="evidence-scope__students">
        <section v-for="[group, members] in groupedStudents" :key="group">
          <h3>{{ group }} · {{ members.length }} 人</h3>
          <article v-for="student in members" :key="student.id">
            <div><strong>{{ student.name }}</strong><span>{{ student.student_code }} · {{ student.class_name || '未分班' }} · {{ sourceLabel(String(student.id)) }}</span></div>
            <select :value="decision(String(student.id))" :aria-label="`${student.name}的范围决定`" @change="setDecision(String(student.id), ($event.target as HTMLSelectElement).value as StudentDecision)">
              <option value="filter">按条件</option>
              <option value="include">强制纳入</option>
              <option value="exclude">排除</option>
            </select>
          </article>
        </section>
      </div>
    </div>
    <p v-if="validationMessage" class="evidence-scope__error" role="alert">{{ validationMessage }}</p>
  </section>
</template>

<style scoped>
.evidence-scope { border: 1px solid var(--color-border-default); border-radius: 14px; background: var(--color-bg-surface); overflow: hidden; }
.evidence-scope__snapshot { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-left: 4px solid var(--color-accent); background: var(--color-bg-subtle); }
.evidence-scope__snapshot div { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.evidence-scope__snapshot span { color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.evidence-scope__snapshot strong { color: var(--color-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evidence-scope__controls { display: grid; grid-template-columns: minmax(130px,.7fr) minmax(150px,.8fr) minmax(250px,1.2fr) minmax(250px,1.25fr) auto; gap: 14px; align-items: end; padding: 16px; }
label > span, legend { display: block; margin-bottom: 6px; color: var(--color-text-secondary); font-size: 12px; }
select, input { width: 100%; min-height: 40px; border: 1px solid var(--color-border-default); border-radius: 9px; background: var(--color-bg-surface); color: var(--color-text-primary); padding: 8px 10px; }
.evidence-scope__range { display: flex; align-items: end; gap: 7px; min-width: 0; border: 0; padding: 0; margin: 0; }
.evidence-scope__range label { position: relative; flex: 1; }
.evidence-scope__range label span { position: absolute; width: 1px; height: 1px; overflow: hidden; }
.evidence-scope__range input { padding-right: 28px; }
.evidence-scope__range i { position: absolute; right: 10px; bottom: 11px; color: var(--color-text-muted); font-style: normal; }
.evidence-scope__range b { padding-bottom: 11px; color: var(--color-text-muted); }
.evidence-scope__history { display: flex; gap: 9px; align-items: center; min-height: 40px; }
.evidence-scope__history input { width: 18px; min-height: auto; }
.evidence-scope__history span { margin: 0; }
.evidence-scope__history strong, .evidence-scope__history small { display: block; }
.evidence-scope__history small { margin-top: 2px; color: var(--color-text-muted); }
.evidence-scope__classes { position: relative; }
.evidence-scope__classes summary { min-height: 40px; padding: 10px; border: 1px solid var(--color-border-default); border-radius: 9px; cursor: pointer; list-style: none; }
.evidence-scope__classes > div { position: absolute; z-index: 8; top: 44px; left: 0; display: grid; min-width: 220px; max-height: 240px; overflow: auto; padding: 8px; border: 1px solid var(--color-border-default); border-radius: 9px; background: var(--color-bg-surface); box-shadow: var(--shadow-overlay); }
.evidence-scope__classes label { display: flex; align-items: center; gap: 8px; padding: 7px; }
.evidence-scope__classes input { width: 16px; min-height: auto; }
.evidence-scope__classes span { margin: 0; }
.primary-button, .quiet-button, .evidence-scope__sessions button { border-radius: 9px; min-height: 40px; padding: 8px 14px; cursor: pointer; }
.primary-button { border: 1px solid var(--color-accent); background: var(--color-accent); color: var(--color-bg-surface); font-weight: 700; }
.quiet-button { border: 1px solid var(--color-border-default); background: var(--color-bg-surface); color: var(--color-text-primary); }
.evidence-scope__sessions { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 16px; }
.evidence-scope__sessions button { min-height: 34px; border: 1px solid var(--color-border-default); background: var(--color-bg-surface); }
.evidence-scope__sessions button.active { border-color: var(--color-accent); background: var(--color-bg-selected); color: var(--color-accent); }
.evidence-scope__roster { border-top: 1px solid var(--color-border-subtle); padding: 16px; }
.evidence-scope__roster header { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.evidence-scope__roster p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 13px; }
.evidence-scope__roster header input { max-width: 260px; }
.evidence-scope__students { display: grid; grid-template-columns: repeat(auto-fit,minmax(280px,1fr)); gap: 12px; max-height: 340px; overflow: auto; }
.evidence-scope__students section { display: grid; align-content: start; gap: 6px; }
.evidence-scope__students h3 { margin: 0; color: var(--color-text-secondary); font-size: 12px; }
.evidence-scope__students article { display: flex; align-items: center; justify-content: space-between; gap: 10px; border: 1px solid var(--color-border-default); border-radius: 9px; padding: 9px 10px; }
.evidence-scope__students article span { display: block; margin-top: 2px; color: var(--color-text-muted); font-size: 12px; }
.evidence-scope__students article select { width: 104px; min-height: 34px; }
.evidence-scope__error { margin: 0; padding: 0 16px 14px; color: var(--color-danger); }
@media (max-width: 1100px) { .evidence-scope__controls { grid-template-columns: repeat(2,minmax(0,1fr)); } .primary-button { width: 100%; } }
</style>
