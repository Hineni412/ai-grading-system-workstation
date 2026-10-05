<script setup lang="ts">
import StatePanel from '../design-system/StatePanel.vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { TrainingOverviewStudent } from '../../api/training'
import { defaultStudentSort, formatPercent } from './model'

const props = defineProps<{
  students: TrainingOverviewStudent[]
}>()

const router = useRouter()
const search = ref('')
const sortKey = ref<'default' | 'score_rate' | 'weak'>('default')
const sortAsc = ref(false)

const filtered = computed(() => {
  const needle = search.value.trim().toLowerCase()
  const list = needle
    ? props.students.filter(student => (
      student.student_name.toLowerCase().includes(needle)
      || student.student_code.toLowerCase().includes(needle)
    ))
    : [...props.students]
  if (sortKey.value === 'score_rate') {
    return list.sort((left, right) => {
      const delta = (left.score_rate ?? Number.POSITIVE_INFINITY)
        - (right.score_rate ?? Number.POSITIVE_INFINITY)
      return (sortAsc.value ? delta : -delta) || left.student_code.localeCompare(right.student_code, 'zh')
    })
  }
  if (sortKey.value === 'weak') {
    return list.sort((left, right) => {
      const delta = (left.topics.weak + left.skills.weak)
        - (right.topics.weak + right.skills.weak)
      return (sortAsc.value ? delta : -delta) || left.student_code.localeCompare(right.student_code, 'zh')
    })
  }
  return defaultStudentSort(list)
})

function toggleSort(key: 'score_rate' | 'weak'): void {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = key === 'weak' ? false : true
  }
}

function openStudent(student: TrainingOverviewStudent): void {
  void router.push({
    name: 'student-evidence',
    params: { studentId: student.student_id },
    query: { from: 'overview' },
  })
}
</script>

<template>
  <section class="overview-card" aria-labelledby="overview-students-title">
    <header class="overview-card__header">
      <h2 id="overview-students-title">本学期学生</h2>
      <label class="overview-student-search">
        <span>搜索姓名或学号</span>
        <input class="app-input" v-model="search" type="search" placeholder="姓名或学号" />
      </label>
    </header>
    <div class="overview-table-wrap">
      <table class="overview-student-table">
        <thead>
          <tr>
            <th scope="col">姓名（学号）</th>
            <th scope="col">班级</th>
            <th scope="col">
              <button type="button" class="overview-sort" @click="toggleSort('score_rate')">
                考试得分率{{ sortKey === 'score_rate' ? (sortAsc ? ' ↑' : ' ↓') : '' }}
              </button>
            </th>
            <th scope="col">知识点</th>
            <th scope="col">技能</th>
            <th scope="col">
              <button type="button" class="overview-sort" @click="toggleSort('weak')">
                明显薄弱{{ sortKey === 'weak' ? (sortAsc ? ' ↑' : ' ↓') : '' }}
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="student in filtered"
            :key="student.student_id"
            tabindex="0"
            @click="openStudent(student)"
            @keydown.enter="openStudent(student)"
          >
            <td>{{ student.student_name }}（{{ student.student_code || '—' }}）</td>
            <td>{{ student.class_id || '—' }}</td>
            <td>{{ student.score_rate === null ? '—' : formatPercent(student.score_rate) }}</td>
            <td>
              <span v-if="student.topics.evidence === 0" class="overview-tier-insufficient">证据不足</span>
              <span v-else class="overview-tier-counts">
                <i class="is-weak">{{ student.topics.weak }}</i>
                <i class="is-unsteady">{{ student.topics.unsteady }}</i>
                <i class="is-stable">{{ student.topics.stable }}</i><i class="is-insufficient">{{ student.topics.insufficient }}</i>
              </span>
            </td>
            <td>
              <span v-if="student.skills.evidence === 0" class="overview-tier-insufficient">证据不足</span>
              <span v-else class="overview-tier-counts">
                <i class="is-weak">{{ student.skills.weak }}</i>
                <i class="is-unsteady">{{ student.skills.unsteady }}</i>
                <i class="is-stable">{{ student.skills.stable }}</i><i class="is-insufficient">{{ student.skills.insufficient }}</i>
              </span>
            </td>
            <td>{{ student.topics.weak + student.skills.weak }}</td>
          </tr>
        </tbody>
      </table>
      <StatePanel v-if="!filtered.length" kind="empty" compact title="没有匹配的学生。" />
    </div>
  </section>
</template>
