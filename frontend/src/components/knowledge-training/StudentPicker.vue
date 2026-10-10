<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { trainingTargetNodeKinds, type TrainingReadDiagnosis, type TrainingReadStudent } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
const props = withDefaults(defineProps<{ diagnosis: TrainingReadDiagnosis | null; classes?: string[]; selectedClass?: string;
  scoreMin?: number | null; scoreMax?: number | null; showRoster?: boolean }>(), { selectedClass: '', scoreMin: null, scoreMax: null, showRoster: true })
const selected = defineModel<string[]>({ required: true })
const emit = defineEmits<{ 'show-student': [student: TrainingReadStudent]; 'select-class': [name: string];
  'score-range': [min: number | null, max: number | null] }>()
const search = ref('')
const onlyWeak = ref(false)
const sort = ref<'class' | 'scoreAsc' | 'scoreDesc' | 'weak'>('class')
const minimum = ref(''), maximum = ref(''), rangeError = ref('')
const anchor = ref<string | null>(null)
watch([() => props.scoreMin, () => props.scoreMax], ([min, max]) => {
  minimum.value = min == null ? '' : String(Math.round(min * 100))
  maximum.value = max == null ? '' : String(Math.round(max * 100))
}, { immediate: true })
const classes = computed(() => props.classes ?? [...new Set((props.diagnosis?.students ?? []).map(student => student.class_id).filter(Boolean))]
  .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true })))
const counts = computed(() => {
  const kinds = new Map((props.diagnosis?.knowledge_catalog ?? []).map(node => [node.knowledge_key, node.node_kind]))
  const listed = trainingTargetNodeKinds(props.diagnosis?.target_kind)
  return new Map((props.diagnosis?.students ?? []).map(student => [student.student_id, new Set(student.weak_points
    .filter(point => point.tier === 'weak' && listed.includes(kinds.get(point.knowledge_key) ?? ''))
    .map(point => point.knowledge_key)).size]))
})
function currentScore(student: TrainingReadStudent) {
  return student.score_rate_source === 'current_exam' && typeof student.score_rate === 'number' ? student.score_rate : null
}
function compareIdentity(left: TrainingReadStudent, right: TrainingReadStudent) {
  return left.class_id.localeCompare(right.class_id, 'zh-CN', { numeric: true })
    || left.student_code.localeCompare(right.student_code, 'zh-CN', { numeric: true })
    || left.student_id.localeCompare(right.student_id)
}
const filtered = computed(() => (props.diagnosis?.students ?? []).filter(student => {
  const score = currentScore(student)
  return (!props.selectedClass || student.class_id === props.selectedClass)
    && (!search.value.trim() || (student.student_name + ' ' + student.student_code).includes(search.value.trim()))
    && (!onlyWeak.value || (counts.value.get(student.student_id) ?? 0) > 0)
    && (props.scoreMin == null || (score != null && score >= props.scoreMin))
    && (props.scoreMax == null || (score != null && score <= props.scoreMax))
}).sort((left, right) => {
  if (sort.value === 'weak') return (counts.value.get(right.student_id) ?? 0) - (counts.value.get(left.student_id) ?? 0)
    || (currentScore(left) ?? 2) - (currentScore(right) ?? 2) || compareIdentity(left, right)
  if (sort.value === 'scoreAsc') return (currentScore(left) ?? 2) - (currentScore(right) ?? 2) || compareIdentity(left, right)
  if (sort.value === 'scoreDesc') return (currentScore(right) ?? -1) - (currentScore(left) ?? -1) || compareIdentity(left, right)
  return compareIdentity(left, right)
}))
const allFilteredSelected = computed(() => filtered.value.length > 0 && filtered.value.every(student => selected.value.includes(student.student_id)))
function toggle(id: string, event: MouseEvent | KeyboardEvent) {
  const ids = filtered.value.map(student => student.student_id)
  const at = ids.indexOf(id), from = anchor.value ? ids.indexOf(anchor.value) : -1
  const range = event.shiftKey && from >= 0 && at >= 0 ? ids.slice(Math.min(from, at), Math.max(from, at) + 1) : [id]
  const next = new Set(selected.value)
  const adding = !next.has(id)
  for (const key of range) { if (adding) next.add(key); else next.delete(key) }
  selected.value = [...next]
  anchor.value = id
}
function toggleFiltered() {
  const ids = new Set(filtered.value.map(student => student.student_id))
  selected.value = allFilteredSelected.value ? selected.value.filter(id => !ids.has(id)) : [...new Set([...selected.value, ...ids])]
}
function applyRange() {
  const min = String(minimum.value).trim() === '' ? null : Number(minimum.value) / 100
  const max = String(maximum.value).trim() === '' ? null : Number(maximum.value) / 100
  if ([min, max].some(value => value != null && (!Number.isFinite(value) || value < 0 || value > 1))) {
    rangeError.value = '考试得分率必须在 0% 至 100% 之间。'; return
  }
  if (min != null && max != null && min > max) { rangeError.value = '得分率下限不能高于上限。'; return }
  rangeError.value = ''
  if (min !== props.scoreMin || max !== props.scoreMax) emit('score-range', min, max)
}
function preset(min: string, max: string) { minimum.value = min; maximum.value = max; applyRange() }
function score(student: TrainingReadStudent) { const value = currentScore(student); return value == null ? '无成绩' : Math.round(value * 100) + '%' }
function scoreClass(student: TrainingReadStudent) { const value = currentScore(student); return value == null ? 'none' : value >= .8 ? 'high' : value >= .6 ? 'middle' : 'low' }
</script>
<template>
  <section class="practice-box student-picker" aria-label="选学生">
    <header class="practice-box-heading"><strong>选学生</strong><span>已选 {{ selected.length }} 人</span><AppButton variant="ghost" :disabled="!selected.length" @click="selected = []; anchor = null">清空</AppButton></header>
    <slot name="mode" />
    <div class="student-picker-tools">
      <input v-if="showRoster" v-model="search" class="app-input" aria-label="检索姓名或学号" placeholder="检索姓名／学号">
      <div class="student-picker-classes"><span>班级</span><button type="button" :aria-pressed="!selectedClass" @click="emit('select-class', '')">全部</button>
        <button v-for="name in classes" :key="name" type="button" :aria-pressed="selectedClass === name" @click="emit('select-class', name)">{{ /^\d+$/.test(name) ? name + '班' : name }}</button>
      </div>
      <div class="student-picker-range"><span>考试得分率</span><input v-model="minimum" class="app-input" type="number" min="0" max="100" aria-label="得分率下限" placeholder="不限" @blur="applyRange" @keydown.enter="applyRange"><span>% 至</span><input v-model="maximum" class="app-input" type="number" min="0" max="100" aria-label="得分率上限" placeholder="不限" @blur="applyRange" @keydown.enter="applyRange"><span>%</span></div>
      <div class="student-picker-presets"><button type="button" @click="preset('', '60')">0–60%</button><button type="button" @click="preset('60', '80')">60–80%</button><button type="button" @click="preset('80', '')">80% 以上</button><button type="button" @click="preset('', '')">不限</button></div>
      <p v-if="rangeError" role="alert">{{ rangeError }}</p>
      <template v-if="showRoster"><label><input v-model="onlyWeak" type="checkbox">只看有明显薄弱</label>
        <label>排序<select v-model="sort" class="app-input" aria-label="学生排序"><option value="class">班级与学号</option><option value="scoreAsc">得分率升序</option><option value="scoreDesc">得分率降序</option><option value="weak">明显薄弱项数</option></select></label>
      </template>
    </div>
    <template v-if="showRoster">
      <div class="student-picker-summary"><span>筛出 {{ filtered.length }} 人</span><AppButton variant="ghost" :disabled="!filtered.length" @click="toggleFiltered">{{ allFilteredSelected ? '取消勾选这 ' : '勾选这 ' }}{{ filtered.length }} 人</AppButton></div>
      <p class="student-picker-hint">点击一行勾选；按住 Shift 点击可连续勾选或取消。</p>
      <div class="student-picker-roster">
        <div v-for="student in filtered" :key="student.student_id" class="student-picker-row" :class="{ 'is-selected': selected.includes(student.student_id) }" role="group" :aria-label="'学生' + student.student_name" tabindex="0" @click="toggle(student.student_id, $event)" @keydown.space.prevent="toggle(student.student_id, $event)" @keydown.enter.prevent="toggle(student.student_id, $event)">
          <input type="checkbox" :aria-label="'选择' + student.student_name" :checked="selected.includes(student.student_id)" @click.stop="toggle(student.student_id, $event)">
          <div class="student-picker-identity"><strong>{{ student.student_name }}</strong><small class="student-picker-meta"><span>{{ student.class_id || '未分班' }} · {{ student.student_code }}</span><button type="button" :aria-label="'查看' + student.student_name + '概况'" @click.stop="emit('show-student', student)" @keydown.stop>概况</button></small></div>
          <span class="student-picker-score" :class="scoreClass(student)">{{ score(student) }}</span><span class="student-picker-weak">薄弱 {{ counts.get(student.student_id) ?? 0 }}</span>
        </div>
        <StatePanel v-if="!filtered.length" kind="empty" compact title="没有符合筛选条件的学生。" />
      </div>
    </template>
    <slot v-else />
  </section>
</template>
<style scoped>
.student-picker-tools{display:grid;gap:var(--space-2);padding:var(--space-3);border-bottom:1px solid var(--color-border-subtle);font-size:var(--font-size-caption)}
.student-picker-tools>input{width:100%}.student-picker-tools label,.student-picker-range{display:flex;gap:var(--space-2);align-items:center;color:var(--color-text-secondary)}
.student-picker-classes,.student-picker-presets{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-2)}
button{border:0;background:transparent;cursor:pointer;color:inherit;padding:2px 4px;font:inherit}
button[aria-pressed=true]{background:var(--color-accent-subtle);color:var(--color-accent);border-radius:var(--radius-control)}
.student-picker-range input{width:56px;min-width:0;flex:1;padding:2px 4px;text-align:center}.student-picker-tools select{flex:1;min-width:0}
.student-picker-summary{display:flex;justify-content:space-between;align-items:center;padding:var(--space-2) var(--space-3);font-size:var(--font-size-caption);background:var(--color-bg-subtle)}
.student-picker-hint{font-size:var(--font-size-caption);color:var(--color-text-muted);margin:var(--space-2) var(--space-3)}
.student-picker-roster{max-height:calc(100vh - 460px);min-height:180px;overflow:auto;user-select:none}
.student-picker-row{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--color-border-subtle);cursor:pointer}
.student-picker-row:hover{background:var(--color-bg-subtle)}.student-picker-row.is-selected{background:var(--color-accent-subtle)}
input[type=checkbox]{accent-color:var(--color-accent);flex:none}.student-picker-identity{display:flex;flex-direction:column;gap:2px;min-width:0}.student-picker-identity strong{font-size:var(--font-size-dense);font-weight:var(--font-weight-semibold)}.student-picker-meta{display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center}.student-picker-meta button{font-size:var(--font-size-caption);color:var(--color-accent);padding:0}.student-picker-identity small{font-size:var(--font-size-caption);color:var(--color-text-muted)}
.student-picker-score{margin-left:auto;border-radius:var(--radius-control);padding:2px 5px;font-size:var(--font-size-caption);white-space:nowrap}.high{background:var(--color-success-subtle);color:var(--color-success)}.middle{background:var(--color-warning-subtle);color:var(--color-warning)}.low{background:var(--color-danger-subtle);color:var(--color-danger)}.none{background:var(--color-bg-subtle);color:var(--color-text-muted)}.student-picker-weak{font-size:var(--font-size-caption);color:var(--color-danger);white-space:nowrap}
@media(max-width:1199px){.student-picker-roster{max-height:420px}}
</style>
