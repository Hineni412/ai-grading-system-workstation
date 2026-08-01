<script setup lang="ts">
import { computed, onMounted, provide } from 'vue'

import TeachingPrepLessonContext from '../components/TeachingPrepLessonContext.vue'
import TeachingPrepStageRuler from '../components/TeachingPrepStageRuler.vue'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import { useTeachingPrepWorkbench } from '../workbench/state'
import LessonTreeWorkspace from '../workspaces/LessonTreeWorkspace.vue'
import MaterialLibraryWorkspace from '../workspaces/MaterialLibraryWorkspace.vue'
import LessonPreparationWorkspace from '../workspaces/LessonPreparationWorkspace.vue'
import PresentationVersionsWorkspace from '../workspaces/PresentationVersionsWorkspace.vue'
import '../styles/teaching-prep.css'

const workbench = useTeachingPrepWorkbench()
provide(teachingPrepWorkbenchKey, workbench)

const workspaceComponent = computed(() => ({
  'lesson-tree': LessonTreeWorkspace,
  materials: MaterialLibraryWorkspace,
  'lesson-prep': LessonPreparationWorkspace,
  versions: PresentationVersionsWorkspace,
}[workbench.workspace.value]))

const workspaceTabs = [
  { id: 'lesson-tree', label: '个人课时树' },
  { id: 'materials', label: '资料库' },
  { id: 'lesson-prep', label: '本节备课' },
  { id: 'versions', label: '课件版本' },
] as const

onMounted(() => workbench.load())
</script>

<template>
  <main class="teaching-prep-shell">
    <header class="tp-shell-header">
      <div class="tp-shell-header__brand">
        <span class="tp-shell-mark" aria-hidden="true">备</span>
        <div><p>教师工作台</p><strong>初中数学备课</strong></div>
      </div>
      <nav class="tp-workspace-tabs" aria-label="备课工作区">
        <button
          v-for="item in workspaceTabs"
          :key="item.id"
          type="button"
          :aria-current="workbench.workspace.value === item.id ? 'page' : undefined"
          :class="{ 'is-current': workbench.workspace.value === item.id }"
          @click="workbench.openWorkspace(item.id)"
        >
          {{ item.label }}
        </button>
      </nav>
      <span class="tp-local-only">本机工作区</span>
    </header>

    <TeachingPrepLessonContext :compact="workbench.workspace.value === 'versions'" />
    <TeachingPrepStageRuler />

    <div v-if="workbench.workbenchError.value" class="tp-global-notice" role="alert">
      <strong>当前状态未完全载入</strong>
      <span>{{ workbench.workbenchError.value }}</span>
      <button type="button" @click="workbench.refreshCurrentWorkspace()">重新载入当前工作面</button>
    </div>

    <div v-if="workbench.loading.value" class="tp-loading" role="status">
      <span aria-hidden="true" />正在整理课时与本地资料……
    </div>
    <component :is="workspaceComponent" v-else />
  </main>
</template>
