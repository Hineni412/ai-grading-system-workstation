<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'

import type { DirectorySubject } from '../api/r1'
import { studentR1Api } from '../api/r1'
import AcademicOverviewPanel from './AcademicOverviewPanel.vue'
import EvidenceUploadPanel from './EvidenceUploadPanel.vue'
import StudentDirectoryPanel from './StudentDirectoryPanel.vue'
import StudentOverviewPanel from './StudentOverviewPanel.vue'
import SupportOverviewPanel from './SupportOverviewPanel.vue'
import SupportReviewPanel from './SupportReviewPanel.vue'

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
async function restoreSelection() {
  if (!props.subjectId) return
  try {
    const value = await studentR1Api.header(props.subjectId)
    selected.value = value as unknown as DirectorySubject
  } catch {
    selected.value = null
  }
}
function choose(subject: DirectorySubject) { selected.value = subject; emit('navigate', 'directory', subject.subject_id) }
function openPanel(panel: 'support' | 'academic') { emit('navigate', panel, selected.value?.subject_id ?? null) }
async function chooseFromOverview(subjectId: string) {
  try {
    const value = await studentR1Api.header(subjectId)
    selected.value = value as unknown as DirectorySubject
    emit('navigate', props.panel, subjectId)
  } catch {
    selected.value = null
  }
}
watch(() => props.subjectId, () => { void restoreSelection() })
onMounted(() => { void restoreSelection() })
</script>

<template>
  <section class="students">
    <nav class="subnav" aria-label="学生工作区页面">
      <button v-for="item in ([['directory','学生目录'],['support','支持记录'],['academic','学业证据']] as const)" :key="item[0]" type="button" :aria-current="panel===item[0]?'page':undefined" @click="emit('navigate', item[0], selected?.subject_id ?? null)">{{ item[1] }}</button>
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
    <SupportReviewPanel
      v-if="panel==='support' && selected"
      :key="selected.subject_id"
      :subject="selected"
    />
    <SupportOverviewPanel
      v-if="panel==='support' && !selected"
      @select="chooseFromOverview"
    />
    <EvidenceUploadPanel v-if="panel==='academic'" />
    <AcademicAnalysisPanel v-if="panel==='academic' && selected" :key="selected.subject_id" :subject="selected" />
    <AcademicOverviewPanel
      v-if="panel==='academic' && !selected"
      @select="chooseFromOverview"
    />
  </section>
</template>

<style scoped>
.students{display:grid;gap:var(--space-3)}.subnav{display:flex;align-items:center;gap:var(--space-1);padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.subnav button{min-height:36px;padding:0 var(--space-3);border:0;border-radius:var(--radius);background:transparent;color:var(--muted-foreground);font:inherit;cursor:pointer}.subnav button:hover{color:var(--foreground)}.subnav button[aria-current="page"]{background:var(--accent);color:var(--primary);font-weight:600}.subnav span{margin-left:auto;color:var(--color-text-secondary);font-size:var(--font-size-dense)}@media(max-width:760px){.subnav{flex-wrap:wrap}.subnav span{width:100%;margin:var(--space-1) 0 0}}
</style>
