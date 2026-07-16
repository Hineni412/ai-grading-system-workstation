<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { GraphQueryInput } from '../../api/graph'
import type { SessionSummary } from '../../api/sessions'
import type { StudentSummary } from '../../api/students'

type ExamMode = 'current' | 'manual' | 'cross_exam'
type StudentScopeMode = 'class' | 'student' | 'selected'

const props = defineProps<{
  sessions: SessionSummary[]
  currentSessionId: number | null
  students: StudentSummary[]
  modelValue: GraphQueryInput | null
  applying: boolean
}>()

const emit = defineEmits<{ apply: [query: GraphQueryInput] }>()

const examMode = ref<ExamMode>('current')
const manualSessionIds = ref<number[]>([])
const scopeMode = ref<StudentScopeMode>('class')
const classId = ref('')
const studentId = ref('')
const selectedStudentIds = ref<string[]>([])
const validationMessage = ref('')

const classes = computed(() => [...new Set(
  props.students.map((student) => student.class_name).filter((value): value is string => Boolean(value)),
)].sort((left, right) => left.localeCompare(right, 'zh-CN')))

function setMultipleNumbers(event: Event): void {
  const select = event.currentTarget as HTMLSelectElement
  manualSessionIds.value = [...select.selectedOptions].map((option) => Number(option.value))
}

function setMultipleStrings(event: Event): void {
  const select = event.currentTarget as HTMLSelectElement
  selectedStudentIds.value = [...select.selectedOptions].map((option) => option.value)
}

watch(
  () => props.modelValue,
  (query) => {
    if (!query) return
    examMode.value = query.exam_scope.mode
    manualSessionIds.value = query.exam_scope.mode === 'manual'
      ? [...query.exam_scope.session_ids]
      : []
    scopeMode.value = query.scope.mode
    classId.value = query.scope.mode === 'class' ? query.scope.class_id : ''
    studentId.value = query.scope.mode === 'student' ? query.scope.student_ids[0] ?? '' : ''
    selectedStudentIds.value = query.scope.mode === 'selected' ? [...query.scope.student_ids] : []
  },
  { immediate: true, deep: true },
)

function apply(): void {
  validationMessage.value = ''
  let examScope: GraphQueryInput['exam_scope']
  if (examMode.value === 'current') {
    if (props.currentSessionId === null) {
      validationMessage.value = '请先在顶部选择当前考试'
      return
    }
    examScope = { mode: 'current', session_ids: [props.currentSessionId] }
  } else if (examMode.value === 'manual') {
    if (manualSessionIds.value.length === 0) {
      validationMessage.value = '请至少选择一场考试'
      return
    }
    examScope = { mode: 'manual', session_ids: [...manualSessionIds.value] }
  } else {
    examScope = { mode: 'cross_exam' }
  }

  let scope: GraphQueryInput['scope']
  if (scopeMode.value === 'class') {
    if (!classId.value) {
      validationMessage.value = '请选择班级'
      return
    }
    scope = { mode: 'class', class_id: classId.value }
  } else if (scopeMode.value === 'student') {
    if (!studentId.value) {
      validationMessage.value = '请选择一名学生'
      return
    }
    scope = { mode: 'student', student_ids: [studentId.value] }
  } else {
    if (selectedStudentIds.value.length === 0) {
      validationMessage.value = '请至少选择一名学生'
      return
    }
    scope = { mode: 'selected', student_ids: [...selectedStudentIds.value] }
  }
  emit('apply', { scope, exam_scope: examScope })
}
</script>

<template>
  <section class="knowledge-graph-filters" aria-labelledby="graph-scope-title">
    <header>
      <div>
        <h2 id="graph-scope-title">查看范围</h2>
        <p>更改选项后，点击“应用范围”再读取图谱。</p>
      </div>
    </header>
    <div class="knowledge-graph-filter-grid">
      <label>
        <span>考试范围</span>
        <select id="graph-exam-mode" v-model="examMode">
          <option value="current">当前考试</option>
          <option value="manual">指定考试</option>
          <option value="cross_exam">全部可用考试</option>
        </select>
      </label>
      <label v-if="examMode === 'manual'">
        <span>选择考试（可多选）</span>
        <select id="graph-manual-sessions" multiple :value="manualSessionIds.map(String)" @change="setMultipleNumbers">
          <option v-for="session in sessions" :key="session.id" :value="session.id">{{ session.name }}</option>
        </select>
      </label>
      <div v-else class="knowledge-graph-filter-readonly">
        <span>{{ examMode === 'current' ? '当前考试' : '考试说明' }}</span>
        <strong>{{ examMode === 'current'
          ? sessions.find((session) => session.id === currentSessionId)?.name ?? '尚未选择考试'
          : '汇总全部可用考试' }}</strong>
      </div>

      <label>
        <span>学生范围</span>
        <select id="graph-student-scope" v-model="scopeMode">
          <option value="class">整个班级</option>
          <option value="student">单名学生</option>
          <option value="selected">已选学生</option>
        </select>
      </label>
      <label v-if="scopeMode === 'class'">
        <span>班级</span>
        <select id="graph-class" v-model="classId">
          <option value="">请选择班级</option>
          <option v-for="className in classes" :key="className" :value="className">{{ className }}</option>
        </select>
      </label>
      <label v-else-if="scopeMode === 'student'">
        <span>学生</span>
        <select id="graph-single-student" v-model="studentId">
          <option value="">请选择学生</option>
          <option v-for="student in students" :key="student.id" :value="String(student.id)">
            {{ student.student_code }} {{ student.name }} · {{ student.class_name ?? '班级暂不可用' }}
          </option>
        </select>
      </label>
      <label v-else>
        <span>选择学生（可多选）</span>
        <select id="graph-selected-students" multiple :value="selectedStudentIds" @change="setMultipleStrings">
          <option v-for="student in students" :key="student.id" :value="student.id">
            {{ student.student_code }} {{ student.name }} · {{ student.class_name ?? '班级暂不可用' }}
          </option>
        </select>
      </label>
    </div>
    <div class="knowledge-graph-filter-actions">
      <p v-if="validationMessage" role="alert">{{ validationMessage }}</p>
      <button type="button" data-testid="apply-graph-scope" :disabled="applying" @click="apply">
        {{ applying ? '正在应用范围…' : '应用范围' }}
      </button>
    </div>
  </section>
</template>
