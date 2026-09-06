<script setup lang="ts">
import { onMounted } from 'vue'
import StudentInspector from '../components/students/StudentInspector.vue'
import StudentRosterTable from '../components/students/StudentRosterTable.vue'
import { useStudentRosterStore } from '../stores/students'
import '../styles/students.css'

const roster = useStudentRosterStore()
onMounted(() => { void roster.load({ page: 1, page_size: 50, class_name: '' }) })
</script>

<template>
  <section class="student-roster">
    <header class="student-roster__hero">
      <div>
        <p class="student-eyebrow">Roster ledger</p>
        <h1 tabindex="-1">学生名单</h1>
        <p>先预览每一处变化，再把名单用于后续答卷匹配与阅卷。</p>
      </div>
    </header>


    <p v-if="roster.errorMessage" class="student-feedback is-error" role="alert">
      {{ roster.errorMessage }}
    </p>
    <p v-if="roster.noticeMessage" class="student-feedback is-success" role="status">
      {{ roster.noticeMessage }}
    </p>

    <div
      class="student-roster__workspace"
      :class="{
        'has-inspector': roster.selectedStudent,
        'is-importing': Boolean(roster.preview),
      }"
    >
      <StudentRosterTable />
      <StudentInspector v-if="roster.selectedStudent && !roster.preview" />
    </div>
  </section>
</template>
