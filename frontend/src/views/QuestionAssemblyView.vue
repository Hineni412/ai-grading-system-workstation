<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppButton from '../components/design-system/AppButton.vue'
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
const localMode = ref<'assistant' | 'edit'>('assistant')
const mode = computed(() => route ? (route.query.mode === 'edit' || route.query.mode === 'browse' ? 'edit' : 'assistant') : localMode.value)
const initialSkill = computed(() => typeof route?.query.skill === 'string' ? route.query.skill : '')
const trainingPurpose = computed(() => typeof assembly.draft.practice_rules === 'object' && assembly.draft.practice_rules?.purpose === 'training')
function setMode(next: 'assistant' | 'edit') { if (router && route) void router.replace({ query: { ...route.query, mode: next } }); else localMode.value = next }
function goBank() { if (router) void router.push({ path: '/question-bank', query: { tab: 'tag' } }) }
onMounted(() => { if (assembly.loadState === 'idle') void assembly.load(); void jobs.initialize() })
</script>
<template>
  <section class="assembly is-workspace-wide">
    <PageHeader title="组卷工作台"><template #meta>当前试卷篮 {{ assembly.selectedQuestionCount }} 题</template><template #navigation><nav class="page-tabs" aria-label="组卷流程"><button :class="{ 'is-active': mode === 'assistant' }" :aria-current="mode === 'assistant' ? 'page' : undefined" @click="setMode('assistant')">班级组卷</button><button :class="{ 'is-active': mode === 'edit' }" :aria-current="mode === 'edit' ? 'page' : undefined" @click="setMode('edit')">{{ trainingPurpose ? '整理与生成' : '整理与导出' }}</button></nav></template><template #actions><AppButton variant="secondary" @click="goBank">去题库选题</AppButton></template></PageHeader>
    <div class="qb-workspace">
    <AssemblyAssistantPanel v-if="mode === 'assistant'" :initial-skill="initialSkill" @edit="setMode('edit')" />
    <AssemblyEditorWorkspace v-else />
    </div>
  </section>
</template>
