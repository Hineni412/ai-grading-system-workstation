<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import type { VaultStatus } from '../api/vault'
import type { DirectorySubject } from '../api/r1'
import { studentR1Api } from '../api/r1'
import AcademicAnalysisPanel from './AcademicAnalysisPanel.vue'
import SecurityPanel from './SecurityPanel.vue'
import StudentDirectoryPanel from './StudentDirectoryPanel.vue'
import SupportReviewPanel from './SupportReviewPanel.vue'

type Panel = 'directory' | 'support' | 'academic' | 'security'
const props = defineProps<{ token: string; panel: Panel; status: VaultStatus | null; subjectId?: string | null }>()
const emit = defineEmits<{ navigate: [panel: Panel]; locked: [reason: string] }>()
const selected = ref<DirectorySubject | null>(null)
const notice = ref('')
async function restoreSelection() {
  if (!props.subjectId) return
  const value = await studentR1Api.header(props.token, props.subjectId)
  selected.value = value as unknown as DirectorySubject
}
function choose(subject: DirectorySubject) { selected.value = subject; emit('navigate', 'support') }
function deleted(message: string) { selected.value = null; notice.value = message; emit('navigate', 'directory') }
watch(() => props.subjectId, () => { void restoreSelection() })
onMounted(() => { void restoreSelection() })
</script>

<template>
  <section class="students">
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <nav class="subnav" aria-label="学生工作区页面">
      <button v-for="item in ([['directory','学生目录'],['support','支持与 AI 复核'],['academic','学业证据'],['security','数据安全']] as const)" :key="item[0]" type="button" :aria-current="panel===item[0]?'page':undefined" @click="emit('navigate', item[0])">{{ item[1] }}</button>
      <span v-if="selected">当前学生：<strong>{{ selected.display_name }}</strong></span>
      <span v-else>当前学生只保存在页面内存中</span>
    </nav>
    <StudentDirectoryPanel v-if="panel==='directory'" :token="token" @select="choose" />
    <SupportReviewPanel v-else-if="panel==='support' && selected" :key="selected.subject_id" :token="token" :subject="selected" />
    <AcademicAnalysisPanel v-else-if="panel==='academic' && selected" :key="selected.subject_id" :token="token" :subject="selected" />
    <SecurityPanel v-else-if="panel==='security'" :token="token" :status="status" :subject="selected" @locked="emit('locked',$event)" @subject-deleted="deleted" />
    <section v-else class="reselect"><span aria-hidden="true">↶</span><h2>请重新选择学生</h2><p>刷新或直接打开此页面时，不会从网址恢复真实学生编号。请回到目录重新选择。</p><button type="button" @click="emit('navigate','directory')">返回学生目录</button></section>
  </section>
</template>

<style scoped>
.students{display:grid;gap:var(--space-3)}.subnav{display:flex;align-items:center;gap:var(--space-1);padding:var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}.subnav button{min-height:38px;padding:0 var(--space-3);border:0;border-radius:var(--radius-control);background:transparent;font:inherit}.subnav button[aria-current="page"]{background:var(--color-accent-subtle);color:var(--color-accent-active);font-weight:700}.subnav span{margin-left:auto;color:var(--color-text-secondary);font-size:var(--font-size-dense)}.reselect{display:grid;place-items:center;align-content:center;min-height:460px;padding:var(--space-6);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);text-align:center}.reselect>span{font-size:40px;color:var(--color-accent)}.reselect h2{margin-bottom:0}.reselect p{max-width:480px;color:var(--color-text-secondary)}.reselect button{min-height:40px;padding:0 var(--space-4);border:1px solid var(--color-accent);border-radius:var(--radius-control);background:var(--color-accent);color:white}@media(max-width:760px){.subnav{flex-wrap:wrap}.subnav span{width:100%;margin:var(--space-1) 0 0}}
.notice{margin:0;padding:var(--space-3);border-left:3px solid var(--color-accent);background:var(--color-accent-subtle);color:var(--color-text-primary)}
</style>
