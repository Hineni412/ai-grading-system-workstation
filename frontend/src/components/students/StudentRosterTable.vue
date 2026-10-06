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
    <StatePanel v-if="roster.loadState === 'loading' && !roster.workspace" kind="loading" title="正在读取学生名单…" />
    <StatePanel v-else-if="roster.loadState === 'error'" kind="error" title="学生名单暂时无法读取" :description="roster.errorMessage" retry-label="重新加载" @retry="roster.load()" />
    <StatePanel v-else-if="roster.workspace?.total === 0" kind="empty" compact title="没有符合条件的学生">
      <template #actions><AppButton variant="ghost" @click="clearFilters">清除筛选</AppButton></template>
    </StatePanel>
    <ul v-else class="student-roster-grid" data-testid="student-roster-table" aria-label="学生名单">
      <li v-for="student in roster.workspace?.items" :key="student.id" class="student-roster-entry">
        <div class="student-roster-entry__identity"><strong>{{ student.name }}</strong><span>{{ student.student_code }}</span></div>
        <span class="student-roster-entry__class" :title="student.class_name || '未分班'">{{ student.class_name ? student.class_name.endsWith('班') ? student.class_name : `${student.class_name} 班` : '未分班' }}</span>
        <AppButton variant="ghost" :data-student-id="student.id" :aria-label="`编辑 ${student.name}`" @click="roster.selectStudent(student.id)">编辑</AppButton>
      </li>
    </ul>
    <div v-if="roster.workspace && roster.workspace.total_pages > 1" class="student-pagination">
      <AppButton variant="ghost" :disabled="roster.workspace.page <= 1" @click="roster.load({ page: roster.workspace.page - 1 })">上一页</AppButton>
      <span>第 {{ roster.workspace.page }} / {{ roster.workspace.total_pages }} 页</span>
      <AppButton variant="ghost" :disabled="roster.workspace.page >= roster.workspace.total_pages" @click="roster.load({ page: roster.workspace.page + 1 })">下一页</AppButton>
    </div>
  </section>
</template>
