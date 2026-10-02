<script setup lang="ts">
import { onMounted } from 'vue'
import StudentInspector from '../students/StudentInspector.vue'
import StudentRosterTable from '../students/StudentRosterTable.vue'
import { useStudentRosterStore } from '../../stores/students'
import '../../styles/students.css'
const roster = useStudentRosterStore()
onMounted(() => { void roster.load({ page: 1, page_size: 50 }) })
</script>
<template>
  <section aria-label="学生名单">
    <p v-if="roster.errorMessage && !roster.selectedStudent" class="settings-feedback is-error" role="alert">{{ roster.errorMessage }}</p>
    <p v-if="roster.noticeMessage" class="settings-feedback is-success" role="status">{{ roster.noticeMessage }}</p>
    <StudentRosterTable />
    <StudentInspector />
  </section>
</template>
