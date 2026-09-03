<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'

import AssemblyQuestionBrowser from '../components/question-bank/AssemblyQuestionBrowser.vue'
import { useAssemblyStore } from '../stores/assembly'
import { useJobStore } from '../stores/jobs'
import '../styles/question-bank.css'
import '../styles/question-assembly.css'

const AssemblyEditorWorkspace = defineAsyncComponent(
  () => import('../components/question-bank/AssemblyEditorWorkspace.vue'),
)
const AiAssemblyPanel = defineAsyncComponent(
  () => import('../components/question-assembly/AiAssemblyPanel.vue'),
)

const assembly = useAssemblyStore()
const jobs = useJobStore()
const mode = ref<'browse' | 'ai' | 'edit'>('browse')

function showEditor(): void {
  void jobs.initialize()
  mode.value = 'edit'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showBrowser(): void {
  mode.value = 'browse'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function showAi(): void {
  mode.value = 'ai'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <section class="assembly is-workspace-wide">
    <header class="assembly__hero">
      <div>
        <p class="assembly-kicker">PAPER ASSEMBLY</p>
        <h1 tabindex="-1">组卷工作台</h1>
        <p>先从现有题库筛题并加入试卷篮，再统一调整顺序、分节和导出。</p>
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
      <button type="button" :class="{ 'is-active': mode === 'ai' }" @click="showAi">
        <span>AI</span>
        <strong>AI 组卷</strong>
        <small>需求生成细目表，题库选题</small>
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
    <AiAssemblyPanel v-else-if="mode === 'ai'" @settled="showEditor" />
    <AssemblyEditorWorkspace v-else @browse="showBrowser" />
  </section>
</template>
