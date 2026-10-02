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
const trainingPurpose = computed(() => typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules?.purpose === 'training')
function setMode(next: 'assistant' | 'edit') { if (router && route) void router.replace({ query: { ...route.query, mode: next } }); else localMode.value = next }
function goBank() { if (router) void router.push({ path: '/question-bank', query: { tab: 'skill' } }) }
onMounted(() => { if (assembly.loadState === 'idle') void assembly.load(); void jobs.initialize() })
</script>
<template>
  <section class="assembly is-workspace-wide">
    <PageHeader title="组卷工作台"><template #meta>当前试卷篮 {{ assembly.selectedQuestionCount }} 题</template><template #navigation><nav class="page-tabs" aria-label="组卷流程"><button :class="{ 'is-active': mode === 'assistant' }" :aria-current="mode === 'assistant' ? 'page' : undefined" @click="setMode('assistant')">班级组卷</button><button :class="{ 'is-active': mode === 'edit' }" :aria-current="mode === 'edit' ? 'page' : undefined" @click="setMode('edit')">{{ trainingPurpose ? '审核与回收' : '整理与导出' }}</button></nav></template><template #actions><button class="qb-button" @click="goBank">去题库选题</button></template></PageHeader>
    <div class="qb-workspace">
    <AssemblyAssistantPanel v-if="mode === 'assistant'" :initial-skill="initialSkill" @edit="setMode('edit')" />
    <section v-else-if="trainingPurpose" class="ca-panel ca-body"><h2>审核与回收</h2><p>全班同一套卷在个性化训练页继续：审核题目 → 打印试卷 → 回收批改。</p><p>先在班级组卷中生成训练卷，生成后会自动打开对应草稿。回收批改前确认模型费用。</p><button class="qb-button" @click="router?.push({ path: '/training', query: { mode: 'paper', ...(assembly.trainingDraftId ? { draft: assembly.trainingDraftId } : {}) } })">{{assembly.trainingDraftId?'继续这张训练卷 →':'打开训练页 →'}}</button></section>
    <AssemblyEditorWorkspace v-else />
    </div>
  </section>
</template>
