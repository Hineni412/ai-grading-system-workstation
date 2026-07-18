<script setup lang="ts">
import { onMounted } from 'vue'

import StudentImportDesk from '../components/students/StudentImportDesk.vue'
import StudentInspector from '../components/students/StudentInspector.vue'
import StudentRosterTable from '../components/students/StudentRosterTable.vue'
import { useStudentRosterStore } from '../stores/students'

const roster = useStudentRosterStore()

onMounted(() => {
  void roster.load({ page: 1, page_size: 50 })
})
</script>

<template>
  <section class="student-roster">
    <header class="student-roster__hero">
      <div>
        <p class="student-eyebrow">Roster ledger</p>
        <h1 tabindex="-1">学生名单</h1>
        <p>先预览每一处变化，再把名单用于后续答卷匹配与阅卷。</p>
      </div>
      <span class="student-roster__registration">
        <i aria-hidden="true" />
        名单登记线
      </span>
    </header>

    <p v-if="roster.errorMessage" class="student-feedback is-error" role="alert">
      {{ roster.errorMessage }}
    </p>
    <p v-if="roster.noticeMessage" class="student-feedback is-success" role="status">
      {{ roster.noticeMessage }}
    </p>

    <StudentImportDesk />

    <div class="student-roster__workspace">
      <StudentRosterTable />
      <StudentInspector />
    </div>
  </section>
</template>
