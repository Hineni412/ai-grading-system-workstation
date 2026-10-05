<script setup lang="ts">
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
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
    <FeedbackBanner v-if="roster.errorMessage && !roster.selectedStudent" role="alert" tone="error" :description="roster.errorMessage" />
    <FeedbackBanner v-if="roster.noticeMessage" role="status" tone="success" :description="roster.noticeMessage" />
    <StudentRosterTable />
    <StudentInspector />
  </section>
</template>
