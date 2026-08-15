<script setup lang="ts">
import { useStudentRosterStore } from '../../stores/students'
import StudentImportDesk from './StudentImportDesk.vue'

const roster = useStudentRosterStore()

function applyFilters(): void {
  void roster.load({
    search: roster.search,
    class_name: roster.className,
    page: 1,
  })
}

function goToPage(nextPage: number): void {
  if (!roster.workspace || nextPage < 1 || nextPage > roster.workspace.total_pages) return
  void roster.load({ page: nextPage })
}
</script>

<template>
  <section class="student-ledger" aria-labelledby="student-ledger-title">
    <div class="student-ledger__toolbar">
      <div class="student-ledger__title">
        <h2 id="student-ledger-title">当前学生</h2>
        <strong>共 {{ roster.workspace?.total ?? 0 }} 名学生</strong>
      </div>
      <form class="student-ledger__filters" @submit.prevent="applyFilters">
        <label>
          <span class="sr-only">搜索学生</span>
          <input
            v-model="roster.search"
            type="search"
            aria-label="搜索学生"
            placeholder="搜索学号、姓名或班级"
          >
        </label>
        <label>
          <span class="sr-only">筛选班级</span>
          <select v-model="roster.className" aria-label="筛选班级">
            <option value="">全部班级</option>
            <option v-for="classItem in roster.workspace?.class_names" :key="classItem">
              {{ classItem }}
            </option>
          </select>
        </label>
        <button type="submit" class="student-button student-button--secondary">筛选</button>
      </form>
      <StudentImportDesk />
    </div>

    <div v-if="roster.loadState === 'loading' && !roster.workspace" class="student-empty" role="status">
      正在读取学生名单…
    </div>
    <div v-else-if="roster.loadState === 'error'" class="student-empty" role="alert">
      <span>{{ roster.errorMessage }}</span>
      <button type="button" class="student-link" @click="roster.load()">重新读取</button>
    </div>
    <div v-else-if="roster.workspace?.total === 0" class="student-empty">
      当前条件下没有学生。可清除筛选，或导入名单。
    </div>
    <div
      v-else
      class="student-ledger__grid"
      data-testid="student-roster-table"
      role="list"
      aria-label="当前学生"
    >
      <div
        v-for="student in roster.workspace?.items"
        :key="student.id"
        class="student-ledger__item"
        role="listitem"
        :class="{ 'is-selected': roster.selectedStudentId === student.id }"
      >
        <span class="student-ledger__code">{{ student.student_code }}</span>
        <strong class="student-ledger__name">{{ student.name }}</strong>
        <span class="student-ledger__class">{{ student.class_name || '未分班' }}</span>
        <button
          type="button"
          class="student-link"
          :data-student-id="student.id"
          :aria-label="`查看并编辑 ${student.name}`"
          @click="roster.selectStudent(student.id)"
        >
          查看
        </button>
      </div>
    </div>

    <div v-if="roster.workspace && roster.workspace.total_pages > 1" class="student-pagination">
      <button
        type="button"
        class="student-link"
        :disabled="roster.workspace.page <= 1"
        @click="goToPage(roster.workspace.page - 1)"
      >
        上一页
      </button>
      <span>第 {{ roster.workspace.page }} / {{ roster.workspace.total_pages }} 页</span>
      <button
        type="button"
        class="student-link"
        :disabled="roster.workspace.page >= roster.workspace.total_pages"
        @click="goToPage(roster.workspace.page + 1)"
      >
        下一页
      </button>
    </div>
  </section>
</template>
