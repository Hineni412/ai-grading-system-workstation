<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { studentR1Api, type DirectorySubject, type StudentCard } from '../api/r1'

const props = defineProps<{ token: string; subject: DirectorySubject }>()
const emit = defineEmits<{ close: []; open: [panel: 'support' | 'academic'] }>()
const card = ref<StudentCard | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const latest = computed(() => {
  const entries = card.value?.entries ?? []
  return entries[entries.length - 1] ?? null
})

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    card.value = await studentR1Api.studentCard(props.token, props.subject.subject_id)
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

watch(() => props.subject.subject_id, () => { void load() })
onMounted(() => { void load() })
</script>

<template>
  <aside class="student-structure" aria-label="学生结构化概览">
    <header><div><small>结构化学生概览</small><h2>{{ subject.display_name }}</h2><span>{{ subject.class_label || '未分班' }} · 学号 {{ subject.source_student_id }}</span></div><button type="button" aria-label="关闭学生概览" @click="emit('close')">×</button></header>
    <div v-if="state === 'loading'" class="student-structure__state" role="status">正在读取已确认概览…</div>
    <div v-else-if="state === 'error'" class="student-structure__state"><strong>概览暂时无法读取</strong><p>学生基本信息已保留，其他学生和原记录不受影响。</p><button type="button" @click="load">重新读取概览</button></div>
    <main v-else-if="latest">
      <p class="student-structure__confirmed">教师已确认 · AI 结构化整理</p>
      <section><h3>目前可以确定</h3><p>{{ latest.portrait.summary }}</p></section>
      <section><h3>优势与已有基础</h3><ul><li v-for="item in latest.portrait.strengths" :key="item">{{ item }}</li></ul></section>
      <section><h3>需要继续支持</h3><ul><li v-for="item in latest.portrait.needs" :key="item">{{ item }}</li></ul></section>
      <section><h3>仍待核实</h3><ul><li v-for="item in latest.portrait.open_questions" :key="item">{{ item }}</li></ul></section>
      <section><h3>建议下一步</h3><strong>{{ latest.sop.title }}</strong><ol><li v-for="item in latest.sop.steps" :key="item">{{ item }}</li></ol></section>
    </main>
    <div v-else class="student-structure__state"><strong>还没有可展示的结构化概览</strong><p>点击卡片不会自动调用 AI。可以先查看已确认记录和学业证据。</p></div>
    <footer><button type="button" @click="emit('open','support')">查看支持记录</button><button type="button" @click="emit('open','academic')">查看学业证据</button></footer>
  </aside>
</template>

<style scoped>
.student-structure{position:fixed;z-index:65;inset:var(--shell-topbar-height) 0 0 auto;display:flex;width:min(620px,100vw);flex-direction:column;overflow:auto;border-inline-start:1px solid var(--color-border-default);background:var(--color-bg-surface);box-shadow:var(--shadow-floating)}header{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-3);padding:var(--space-5);border-bottom:1px solid var(--color-border-default)}header div{display:grid;gap:3px}header h2,header small,header span{margin:0}header small{color:var(--color-accent);font-weight:700}header span{color:var(--color-text-secondary)}header button{border:0;background:transparent;font-size:28px}main{display:grid;gap:var(--space-4);padding:var(--space-5)}main section{padding-inline-start:var(--space-4);border-inline-start:4px solid var(--color-accent)}main h3,main p,main ul,main ol{margin-block:0 var(--space-2)}.student-structure__confirmed{margin:0;padding:10px 12px;background:var(--color-success-subtle);color:var(--color-success);font-weight:700}.student-structure__state{display:grid;min-height:280px;place-content:center;justify-items:center;gap:8px;padding:var(--space-5);text-align:center}.student-structure__state p{max-width:430px;color:var(--color-text-secondary)}footer{display:flex;gap:var(--space-2);margin-top:auto;padding:var(--space-5);border-top:1px solid var(--color-border-default)}footer button,.student-structure__state button{min-height:40px;padding:0 var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}footer button:first-child{border-color:var(--color-accent);color:var(--color-accent-active)}
</style>
