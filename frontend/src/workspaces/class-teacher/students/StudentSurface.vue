<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'

import type { DirectorySubject } from '../api/r1'
import { studentR1Api } from '../api/r1'
import AppButton from '@/components/design-system/AppButton.vue'
import StudentDirectoryPanel from './StudentDirectoryPanel.vue'
import StudentOverviewPanel from './StudentOverviewPanel.vue'
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
  const value = await studentR1Api.header(props.subjectId)
  selected.value = value as unknown as DirectorySubject
}
function choose(subject: DirectorySubject) { selected.value = subject; emit('navigate', 'directory', subject.subject_id) }
function openPanel(panel: 'support' | 'academic') { emit('navigate', panel, selected.value?.subject_id ?? null) }
watch(() => props.subjectId, () => { void restoreSelection() })
onMounted(() => { void restoreSelection() })
</script>

<template>
  <section class="students">
    <nav class="subnav" aria-label="学生工作区页面">
      <button v-for="item in ([['directory','学生目录'],['support','支持记录'],['academic','学业证据']] as const)" :key="item[0]" type="button" :aria-current="panel===item[0]?'page':undefined" @click="emit('navigate', item[0])">{{ item[1] }}</button>
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
    <AcademicAnalysisPanel v-if="panel==='academic' && selected" :key="selected.subject_id" :subject="selected" />
    <section v-if="panel !== 'directory' && !selected" class="reselect"><span aria-hidden="true">↶</span><h2>请重新选择学生</h2><p>刷新或直接打开此页面时，不会从网址恢复真实学生编号。请回到目录重新选择。</p><AppButton variant="primary" @click="emit('navigate','directory')">返回学生目录</AppButton></section>
  </section>
</template>

<style scoped>
.students{display:grid;gap:var(--space-3)}.subnav{display:flex;align-items:center;gap:var(--space-1);padding:var(--space-2);border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}.subnav button{min-height:36px;padding:0 var(--space-3);border:0;border-radius:var(--radius);background:transparent;color:var(--muted-foreground);font:inherit;cursor:pointer}.subnav button:hover{color:var(--foreground)}.subnav button[aria-current="page"]{background:var(--accent);color:var(--primary);font-weight:600}.subnav span{margin-left:auto;color:var(--color-text-secondary);font-size:var(--font-size-dense)}.reselect{display:grid;place-items:center;align-content:center;min-height:460px;padding:var(--space-6);border:1px solid var(--border);border-radius:var(--radius);background:var(--card);text-align:center}.reselect>span{font-size:40px;color:var(--primary)}.reselect h2{margin-bottom:0}.reselect p{max-width:480px;color:var(--color-text-secondary)}@media(max-width:760px){.subnav{flex-wrap:wrap}.subnav span{width:100%;margin:var(--space-1) 0 0}}
</style>
