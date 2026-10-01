<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/design-system/PageHeader.vue'
import { useAssemblyStore } from '../stores/assembly'
import { useJobStore } from '../stores/jobs'
import '../styles/question-bank.css'
import '../styles/question-assembly.css'
const AssemblyEditorWorkspace = defineAsyncComponent(() => import('../components/question-bank/AssemblyEditorWorkspace.vue'))
const AssemblyAssistantPanel = defineAsyncComponent(() => import('../components/question-assembly/AssemblyAssistantPanel.vue'))
const route = useRoute() as ReturnType<typeof useRoute> | undefined
const router = useRouter() as ReturnType<typeof useRouter> | undefined
const assembly = useAssemblyStore()
const jobs = useJobStore()
const localMode = ref<'assistant' | 'edit'>('edit')
const mode = computed(() => route ? (route.query.mode === 'assistant' || route.query.mode === 'ai' ? 'assistant' : 'edit') : localMode.value)
const initialSkill = computed(() => typeof route?.query.skill === 'string' ? route.query.skill : '')
function setMode(next: 'assistant' | 'edit') { if (router && route) void router.replace({ query: { ...route.query, mode: next } }); else localMode.value = next }
function goBank() { if (router) void router.push({ path: '/question-bank', query: { tab: 'skill' } }) }
onMounted(() => { if (assembly.loadState === 'idle') void assembly.load(); void jobs.initialize() })
</script>
<template>
  <section class="assembly is-workspace-wide">
    <PageHeader title="组卷工作台"><template #meta>{{ assembly.selectedQuestionCount }} 道题 · {{ assembly.totalScore }} 已识别分值</template><template #actions><button class="qb-button" @click="goBank">去题库选题</button></template></PageHeader>
    <nav class="page-tabs" aria-label="组卷流程"><button :class="{ 'is-active': mode === 'edit' }" :aria-current="mode === 'edit' ? 'page' : undefined" @click="setMode('edit')">试卷篮与导出</button><button :class="{ 'is-active': mode === 'assistant' }" :aria-current="mode === 'assistant' ? 'page' : undefined" @click="setMode('assistant')">学情组卷助手</button></nav>
    <AssemblyAssistantPanel v-if="mode === 'assistant'" :initial-skill="initialSkill" @edit="setMode('edit')" />
    <AssemblyEditorWorkspace v-else @browse="goBank" />
  </section>
</template>
