<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'

import type { DirectorySubject } from '../api/r1'
import { studentR1Api, studentRefOf } from '../api/r1'
import AcademicOverviewPanel from './AcademicOverviewPanel.vue'
import EvidenceSessionsPanel from './EvidenceSessionsPanel.vue'
import EvidenceUploadPanel from './EvidenceUploadPanel.vue'
import StudentDirectoryPanel from './StudentDirectoryPanel.vue'
import StudentOverviewPanel from './StudentOverviewPanel.vue'
import SupportActionPanel from './SupportActionPanel.vue'
import SupportOverviewPanel from './SupportOverviewPanel.vue'

const AcademicAnalysisPanel = defineAsyncComponent(() => import('./AcademicAnalysisPanel.vue'))

type Panel = 'directory' | 'support' | 'academic'
const props = defineProps<{
  panel: Panel
  subjectId?: string | null
}>()
const emit = defineEmits<{
  navigate: [panel: Panel, subjectId?: string | null]
}>()
const selected = ref<DirectorySubject | null>(null)
const profileOpen = ref(false)
const academicRefreshKey = ref(0)
const sessionsOpen = ref(false)
async function restoreSelection() {
  if (!props.subjectId) return
  try {
    const value = await studentR1Api.header(props.subjectId)
    selected.value = value as unknown as DirectorySubject
  } catch {
    selected.value = null
  }
}
function choose(subject: DirectorySubject) { selected.value = subject; profileOpen.value = false; emit('navigate', 'directory', studentRefOf(subject)) }
function openPanel(panel: 'support' | 'academic') { profileOpen.value = false; emit('navigate', panel, selected.value ? studentRefOf(selected.value) : null) }
async function chooseFromOverview(subjectId: string) {
  try {
    const value = await studentR1Api.header(subjectId)
    selected.value = value as unknown as DirectorySubject
    profileOpen.value = false
    emit('navigate', props.panel, subjectId)
  } catch {
    selected.value = null
  }
}
watch(() => props.subjectId, () => { profileOpen.value = false; void restoreSelection() })
watch(() => props.panel, () => { profileOpen.value = false; sessionsOpen.value = false })
onMounted(() => { void restoreSelection() })
</script>

<template>
  <section class="students">
    <nav class="subnav" aria-label="学生工作区页面">
      <button v-for="item in ([['directory','学生目录'],['support','支持行动'],['academic','学业证据']] as const)" :key="item[0]" type="button" :aria-current="panel===item[0]?'page':undefined" @click="emit('navigate', item[0], selected ? studentRefOf(selected) : null)">{{ item[1] }}</button>
      <span v-if="selected">当前学生：<strong>{{ selected.display_name }}</strong></span>
      <span v-else>选择学生后可进入这名学生的当前档案</span>
    </nav>
    <StudentDirectoryPanel v-if="panel==='directory'" @select="choose" />
    <StudentOverviewPanel
      v-if="panel === 'directory' && selected"
      :subject="selected"
      @close="selected = null; emit('navigate', 'directory', null)"
      @open="openPanel"
    />
    <SupportActionPanel
      v-if="panel==='support' && selected"
      :key="selected.subject_id"
      :subject="selected"
      @open-profile="profileOpen = true"
    />
    <StudentOverviewPanel
      v-if="panel==='support' && selected && profileOpen"
      :subject="selected"
      initial-tab="support"
      @close="profileOpen = false"
      @open="openPanel"
    />
    <SupportOverviewPanel
      v-if="panel==='support' && !selected"
      @select="chooseFromOverview"
    />
    <div v-if="panel==='academic'" class="academic-toolbar">
      <button type="button" @click="sessionsOpen = true">成绩管理</button>
    </div>
    <EvidenceUploadPanel v-if="panel==='academic'" />
    <EvidenceSessionsPanel v-if="panel==='academic'" :open="sessionsOpen" @close="sessionsOpen = false" @changed="academicRefreshKey += 1" />
    <AcademicAnalysisPanel v-if="panel==='academic' && selected" :key="`${selected.subject_id}:${academicRefreshKey}`" :subject="selected" />
    <AcademicOverviewPanel
      v-if="panel==='academic' && !selected"
      :key="academicRefreshKey"
      @select="chooseFromOverview"
    />
  </section>
</template>

<style scoped>
.students{display:grid;gap:var(--space-3)}.subnav{display:flex;align-items:center;gap:var(--space-1);padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.subnav button{min-height:36px;padding:0 var(--space-3);border:0;border-radius:var(--radius);background:transparent;color:var(--muted-foreground);font:inherit;cursor:pointer}.subnav button:hover{color:var(--foreground)}.subnav button[aria-current="page"]{background:var(--accent);color:var(--primary);font-weight:600}.subnav span{margin-left:auto;color:var(--color-text-secondary);font-size:var(--font-size-dense)}@media(max-width:760px){.subnav{flex-wrap:wrap}.subnav span{width:100%;margin:var(--space-1) 0 0}}
.academic-toolbar{display:flex;justify-content:flex-end}.academic-toolbar button{min-height:36px;padding:0 var(--space-3);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);color:var(--primary);font:inherit;font-weight:600;cursor:pointer}.academic-toolbar button:hover{background:var(--accent)}
</style>
