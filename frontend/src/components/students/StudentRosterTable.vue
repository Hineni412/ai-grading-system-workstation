<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { useStudentRosterStore } from '../../stores/students'
import StudentImportDesk from './StudentImportDesk.vue'
const roster = useStudentRosterStore()
let timer: ReturnType<typeof setTimeout> | undefined
function applyFilters() { void roster.load({ search: roster.search, class_name: roster.className, page: 1 }) }
watch(() => roster.search, () => { clearTimeout(timer); timer = setTimeout(applyFilters, 300) })
watch(() => roster.className, () => { clearTimeout(timer); applyFilters() })
onBeforeUnmount(() => clearTimeout(timer))
function clearFilters() { roster.search = ''; roster.className = ''; clearTimeout(timer); applyFilters() }
</script>
<template>
  <section class="settings-panel student-ledger" aria-label="当前学生">
    <div class="student-ledger__toolbar">
      <input v-model="roster.search" class="app-input" type="search" aria-label="搜索学生" placeholder="搜索学号、姓名或班级">
      <select v-model="roster.className" class="app-input" aria-label="筛选班级">
        <option value="">全部班级</option>
        <option v-for="name in roster.workspace?.class_names" :key="name">{{ name }}</option>
      </select>
      <span class="settings-note">共 {{ roster.workspace?.total ?? 0 }} 名</span>
      <StudentImportDesk />
    </div>
    <StatePanel v-if="roster.loadState === 'loading' && !roster.workspace" kind="loading" title="正在读取学生名单…" description="" />
    <StatePanel v-else-if="roster.loadState === 'error'" kind="error" title="学生名单暂时无法读取" :description="roster.errorMessage" retry-label="重新读取" @retry="roster.load()" />
    <div v-else-if="roster.workspace?.total === 0" class="student-empty"><p>没有符合条件的学生</p><AppButton variant="ghost" @click="clearFilters">清除筛选</AppButton></div>
    <table v-else class="settings-table" data-testid="student-roster-table">
      <colgroup><col style="width:150px"><col style="width:160px"><col style="width:140px"><col></colgroup>
      <thead><tr><th>学号</th><th>姓名</th><th>班级</th><th>操作</th></tr></thead>
      <tbody><tr v-for="student in roster.workspace?.items" :key="student.id">
        <td>{{ student.student_code }}</td><td>{{ student.name }}</td><td>{{ student.class_name || '—' }}</td>
        <td><AppButton variant="ghost" :data-student-id="student.id" :aria-label="`编辑 ${student.name}`" @click="roster.selectStudent(student.id)">编辑</AppButton></td>
      </tr></tbody>
    </table>
    <div v-if="roster.workspace && roster.workspace.total_pages > 1" class="student-pagination">
      <AppButton variant="ghost" :disabled="roster.workspace.page <= 1" @click="roster.load({ page: roster.workspace.page - 1 })">上一页</AppButton>
      <span>第 {{ roster.workspace.page }} / {{ roster.workspace.total_pages }} 页</span>
      <AppButton variant="ghost" :disabled="roster.workspace.page >= roster.workspace.total_pages" @click="roster.load({ page: roster.workspace.page + 1 })">下一页</AppButton>
    </div>
  </section>
</template>
