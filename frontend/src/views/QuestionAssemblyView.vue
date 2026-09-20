<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AssemblyQuestionBrowser from '../components/question-bank/AssemblyQuestionBrowser.vue'
import { useAssemblyStore } from '../stores/assembly'
import { useJobStore } from '../stores/jobs'
import '../styles/question-bank.css'
import '../styles/question-assembly.css'

const AssemblyEditorWorkspace = defineAsyncComponent(
  () => import('../components/question-bank/AssemblyEditorWorkspace.vue'),
)
const AssemblyAssistantPanel = defineAsyncComponent(
  () => import('../components/question-assembly/AssemblyAssistantPanel.vue'),
)

const route = useRoute() as ReturnType<typeof useRoute> | undefined
const router = useRouter() as ReturnType<typeof useRouter> | undefined

const assembly = useAssemblyStore()
const jobs = useJobStore()
type AssemblyMode = 'browse' | 'assistant' | 'edit'

// 部分既有单测在无 router 的环境直接挂载本视图，此时回退到内存模式。
const localMode = ref<AssemblyMode>('browse')
const selectionMode = ref<'browse' | 'assistant'>('browse')
// 兼容旧 ?mode=ai 链接，将其打开为当前本地学情助手。
const mode = computed<AssemblyMode>(() => {
  if (!route || !router) return localMode.value
  const raw = route.query.mode
  const value = Array.isArray(raw) ? raw[0] : raw
  if (value === 'ai') return 'assistant'
  return value === 'assistant' || value === 'edit' ? value : 'browse'
})

function setMode(next: AssemblyMode): void {
  if (route && router) {
    const query = { ...route.query }
    if (next === 'browse') delete query.mode
    else query.mode = next
    void router.replace({ query })
  } else {
    localMode.value = next
  }
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showEditor(): void {
  if (mode.value !== 'edit') selectionMode.value = mode.value
  void jobs.initialize()
  setMode('edit')
}

function returnToSelection(): void { setMode(selectionMode.value) }

function showBrowser(): void {
  setMode('browse')
}

function showAssistant(): void {
  setMode('assistant')
}
</script>

<template>
  <section class="assembly is-workspace-wide">
    <header class="assembly__hero">
      <div>
        <p class="assembly-kicker">PAPER ASSEMBLY</p>
        <h1 tabindex="-1">组卷工作台</h1>
        <p>从题库选题，或依据班级学情缩小候选范围；加入试卷篮后统一整理和导出。</p>
      </div>
      <div class="assembly__summary" aria-label="当前试卷概况">
        <span><strong>{{ assembly.selectedQuestionCount }}</strong> 道题</span>
        <span><strong>{{ assembly.totalScore }}</strong> 已识别分值</span>
      </div>
    </header>

    <nav class="assembly-steps" aria-label="组卷流程">
      <button type="button" :class="{ 'is-active': mode === 'browse' }" @click="showBrowser">
        <span>1</span>
        <strong>题库选题</strong>
        <small>章节、标签与相似题</small>
      </button>
      <i aria-hidden="true" />
      <button type="button" :class="{ 'is-active': mode === 'assistant' }" @click="showAssistant">
        <span>荐</span>
        <strong>学情组卷助手</strong>
        <small>班级薄弱点，少量候选题</small>
      </button>
      <i aria-hidden="true" />
      <button
        type="button"
        :class="{ 'is-active': mode === 'edit' }"
        :disabled="assembly.loadState === 'loading'"
        @click="showEditor"
      >
        <span>2</span>
        <strong>试卷篮与导出</strong>
        <small>{{ assembly.selectedQuestionCount }} 道题待编辑</small>
      </button>
    </nav>

    <AssemblyQuestionBrowser v-if="mode === 'browse'" @edit="showEditor" />
    <AssemblyAssistantPanel v-else-if="mode === 'assistant'" @edit="showEditor" />
    <AssemblyEditorWorkspace v-else @browse="returnToSelection" />
  </section>
</template>
