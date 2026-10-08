<script setup lang="ts">
import { computed, ref } from 'vue'
import { trainingTargetNodeKinds, type TrainingReadDiagnosis, type TrainingReadStudent } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
const props = defineProps<{ diagnosis: TrainingReadDiagnosis }>()
const selected = defineModel<string[]>({ required: true })
const emit = defineEmits<{ 'show-student': [student: TrainingReadStudent] }>()
const search = ref('')
const onlyWeak = ref(false)
const collapsed = ref<string[]>([])
const counts = computed(() => {
  const kinds = new Map((props.diagnosis.knowledge_catalog ?? []).map(node => [node.knowledge_key, node.node_kind]))
  // Typed releases count weak question types; legacy releases count skills/topics.
  const listed = trainingTargetNodeKinds(props.diagnosis.target_kind)
  return new Map(props.diagnosis.students.map(student => [student.student_id, new Set(student.weak_points
    .filter(point => point.tier === 'weak' && listed.includes(kinds.get(point.knowledge_key) ?? ''))
    .map(point => point.knowledge_key)).size]))
})
const groups = computed(() => {
  const classes = new Map<string, TrainingReadStudent[]>()
  for (const student of props.diagnosis.students) {
    if (!`${student.student_name} ${student.student_code}`.includes(search.value.trim()) || (onlyWeak.value && !counts.value.get(student.student_id))) continue
    const name = student.class_id || '未分班'
    const rows = classes.get(name) ?? []
    rows.push(student); classes.set(name, rows)
  }
  return [...classes].sort(([a], [b]) => a.localeCompare(b, 'zh-CN', { numeric: true }))
})
function toggle(id: string) { selected.value = selected.value.includes(id) ? selected.value.filter(key => key !== id) : [...selected.value, id] }
function toggleClass(name: string) {
  const ids = props.diagnosis.students.filter(student => (student.class_id || '未分班') === name).map(student => student.student_id)
  selected.value = ids.every(id => selected.value.includes(id)) ? selected.value.filter(id => !ids.includes(id)) : [...new Set([...selected.value, ...ids])]
}
function allSelected(name: string) { return props.diagnosis.students.filter(student => (student.class_id || '未分班') === name).every(student => selected.value.includes(student.student_id)) }
function fold(name: string) { collapsed.value = collapsed.value.includes(name) ? collapsed.value.filter(item => item !== name) : [...collapsed.value, name] }
function score(student: TrainingReadStudent) { return student.score_rate_source === 'current_exam' && typeof student.score_rate === 'number' ? `${Math.round(student.score_rate * 100)}%` : '无成绩' }
function scoreClass(student: TrainingReadStudent) { return score(student) === '无成绩' ? 'none' : (student.score_rate ?? 0) >= .8 ? 'high' : (student.score_rate ?? 0) >= .6 ? 'middle' : 'low' }
</script>
<template>
  <section class="practice-box student-picker" aria-label="学生名单">
    <header class="practice-box-heading"><strong>学生名单</strong><span>已选 {{ selected.length }} 人</span><AppButton variant="ghost" :disabled="!selected.length" @click="selected = []">清空</AppButton></header>
    <div class="student-picker-tools"><input v-model="search" class="app-input" aria-label="检索姓名或学号" placeholder="检索姓名／学号"><label><input v-model="onlyWeak" type="checkbox">只看有明显薄弱点</label></div>
    <div class="student-picker-roster">
      <section v-for="[name, rows] in groups" :key="name" class="student-picker-class">
        <header><button type="button" :aria-expanded="!collapsed.includes(name)" @click="fold(name)">{{ collapsed.includes(name) ? '▸' : '▾' }} {{ /^\d+$/.test(name) ? `${name}班` : name }} <small>（{{ rows.length }} 人）</small></button><AppButton type="button" variant="ghost" size="small" @click="toggleClass(name)">{{ allSelected(name) ? '取消本班' : '全选本班' }}</AppButton></header>
        <div v-if="!collapsed.includes(name)">
          <div v-for="student in rows" :key="student.student_id" class="student-picker-row">
            <input type="checkbox" :aria-label="`选择${student.student_name}`" :checked="selected.includes(student.student_id)" @change="toggle(student.student_id)">
            <div class="student-picker-identity"><button type="button" @click="emit('show-student', student)">{{ student.student_name }}</button><small>{{ student.student_code }}</small></div>
            <span class="student-picker-score" :class="scoreClass(student)">{{ score(student) }}</span><span class="student-picker-weak">薄弱 {{ counts.get(student.student_id) ?? 0 }}</span>
          </div>
        </div>
      </section>
      <StatePanel v-if="!groups.length" kind="empty" compact title="没有符合检索条件的学生。" />
    </div>
  </section>
</template>
<style scoped>
.student-picker-tools{display:grid;gap:var(--space-2);padding:var(--space-3);border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-caption)}.student-picker-tools>input{width:100%}.student-picker-tools label{display:flex;gap:var(--space-2);align-items:center;color:var(--color-text-secondary)}
.student-picker-class{border-bottom:1px solid var(--color-border-subtle)}.student-picker-class>header{display:flex;align-items:center;justify-content:space-between;padding:var(--space-2) var(--space-3);gap:var(--space-1);background:var(--color-bg-subtle)}button{border:0;background:transparent;cursor:pointer;color:inherit;padding:0;font:inherit}.student-picker-class header>button:first-child{font-weight:var(--font-weight-semibold);font-size:var(--font-size-dense)}small{font-weight:var(--font-weight-regular);color:var(--color-text-muted)}
.student-picker-roster{max-height:calc(100vh - 290px);overflow:auto}.student-picker-row{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-2) var(--space-3)}.student-picker-row:hover{background:var(--color-bg-subtle)}input[type=checkbox]{accent-color:var(--color-accent);flex:none}.student-picker-identity{display:flex;flex-direction:column;gap:2px;min-width:0}.student-picker-identity button{text-align:left;font-size:var(--font-size-dense);font-weight:var(--font-weight-semibold)}.student-picker-identity small{font-size:var(--font-size-caption)}.student-picker-score{margin-left:auto;border-radius:var(--radius-control);padding:2px 5px;font-size:var(--font-size-caption);white-space:nowrap}.high{background:var(--color-success-subtle);color:var(--color-success)}.middle{background:var(--color-warning-subtle);color:var(--color-warning)}.low{background:var(--color-danger-subtle);color:var(--color-danger)}.none{background:var(--color-bg-subtle);color:var(--color-text-muted)}.student-picker-weak{font-size:var(--font-size-caption);color:var(--color-danger);white-space:nowrap}
@media(max-width:1199px){.student-picker-roster{max-height:420px}}
</style>
