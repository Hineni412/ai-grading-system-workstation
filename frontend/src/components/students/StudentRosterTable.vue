<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { useStudentRosterStore } from '../../stores/students'
import type { StudentSummary } from '../../api/students'
import StudentImportDesk from './StudentImportDesk.vue'

const roster = useStudentRosterStore()
let timer: ReturnType<typeof setTimeout> | undefined
function applyFilters() { void roster.load({ search: roster.search, class_name: roster.className, page: 1 }) }
watch(() => roster.search, () => { clearTimeout(timer); timer = setTimeout(applyFilters, 300) })
watch(() => roster.className, () => { clearTimeout(timer); applyFilters() })
onBeforeUnmount(() => clearTimeout(timer))
function clearFilters() { roster.search = ''; roster.className = ''; clearTimeout(timer); applyFilters() }

const classNames = computed(() => [...(roster.workspace?.class_names ?? [])]
  .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true })))
function classLabel(name: string): string {
  if (!name) return '未分班'
  return name.endsWith('班') ? name : `${name} 班`
}

interface StudentGroup { name: string; label: string; students: StudentSummary[] }
const groups = computed<StudentGroup[]>(() => {
  const byClass = new Map<string, StudentSummary[]>()
  for (const student of roster.workspace?.items ?? []) {
    const key = student.class_name ?? ''
    const list = byClass.get(key) ?? []
    list.push(student)
    byClass.set(key, list)
  }
  const names = [...byClass.keys()].filter(name => name !== '')
    .sort((a, b) => a.localeCompare(b, 'zh-CN', { numeric: true }))
  if (byClass.has('')) names.push('')
  return names.map(name => ({
    name,
    label: classLabel(name),
    students: (byClass.get(name) ?? []).slice()
      .sort((a, b) => a.student_code.localeCompare(b.student_code, 'zh-CN', { numeric: true })),
  }))
})
const showCounts = computed(() => (roster.workspace?.total_pages ?? 1) <= 1)

function highlightParts(text: string): Array<{ value: string; hit: boolean }> {
  const needle = roster.search.trim().toLowerCase()
  if (!needle) return [{ value: text, hit: false }]
  const parts: Array<{ value: string; hit: boolean }> = []
  const lower = text.toLowerCase()
  let index = 0
  for (;;) {
    const found = lower.indexOf(needle, index)
    if (found < 0) {
      parts.push({ value: text.slice(index), hit: false })
      break
    }
    if (found > index) parts.push({ value: text.slice(index, found), hit: false })
    parts.push({ value: text.slice(found, found + needle.length), hit: true })
    index = found + needle.length
  }
  return parts.filter(part => part.value !== '')
}
</script>
<template>
  <section class="settings-panel student-ledger" aria-label="当前学生">
    <div class="student-ledger__toolbar">
      <input v-model="roster.search" class="app-input" type="search" aria-label="搜索学生" placeholder="搜索学号、姓名或班级">
      <div class="app-segmented" role="group" aria-label="筛选班级">
        <button type="button" class="app-segmented__button" :class="{ 'is-active': roster.className === '' }" :aria-pressed="roster.className === ''" @click="roster.className = ''">全部</button>
        <button v-for="name in classNames" :key="name" type="button" class="app-segmented__button" :class="{ 'is-active': roster.className === name }" :aria-pressed="roster.className === name" @click="roster.className = name">{{ classLabel(name) }}</button>
      </div>
      <span class="settings-note">共 {{ roster.workspace?.total ?? 0 }} 名</span>
      <StudentImportDesk />
    </div>
    <StatePanel v-if="roster.loadState === 'loading' && !roster.workspace" kind="loading" title="正在读取学生名单…" />
    <StatePanel v-else-if="roster.loadState === 'error'" kind="error" title="学生名单暂时无法读取" :description="roster.errorMessage" retry-label="重新加载" @retry="roster.load()" />
    <StatePanel v-else-if="roster.workspace?.total === 0" kind="empty" compact title="没有符合条件的学生">
      <template #actions><AppButton variant="ghost" @click="clearFilters">清除筛选</AppButton></template>
    </StatePanel>
    <div v-else class="student-ledger__groups" data-testid="student-roster-table" aria-label="学生名单">
      <section v-for="group in groups" :key="group.name || 'unassigned'" class="student-ledger__group">
        <h3 class="student-ledger__group-title">{{ group.label }}<template v-if="showCounts"> · {{ group.students.length }} 人</template></h3>
        <div class="student-ledger__columns">
          <button
            v-for="student in group.students"
            :key="student.id"
            type="button"
            class="student-ledger__entry"
            :data-student-id="student.id"
            :aria-label="`编辑 ${student.name}`"
            @click="roster.selectStudent(student.id)"
          >
            <span class="student-ledger__code"><template v-for="(part, i) in highlightParts(student.student_code)" :key="i"><mark v-if="part.hit">{{ part.value }}</mark><template v-else>{{ part.value }}</template></template></span>
            <strong class="student-ledger__name"><template v-for="(part, i) in highlightParts(student.name)" :key="i"><mark v-if="part.hit">{{ part.value }}</mark><template v-else>{{ part.value }}</template></template></strong>
            <svg class="student-ledger__edit-icon" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /><path d="m15 5 4 4" /></svg>
          </button>
        </div>
      </section>
    </div>
    <div v-if="roster.workspace && roster.workspace.total_pages > 1" class="student-pagination">
      <AppButton variant="ghost" :disabled="roster.workspace.page <= 1" @click="roster.load({ page: roster.workspace.page - 1 })">上一页</AppButton>
      <span>第 {{ roster.workspace.page }} / {{ roster.workspace.total_pages }} 页</span>
      <AppButton variant="ghost" :disabled="roster.workspace.page >= roster.workspace.total_pages" @click="roster.load({ page: roster.workspace.page + 1 })">下一页</AppButton>
    </div>
  </section>
</template>
