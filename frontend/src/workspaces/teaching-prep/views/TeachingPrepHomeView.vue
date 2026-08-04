<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide } from 'vue'

import TeachingPrepLessonContext from '../components/TeachingPrepLessonContext.vue'
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

function handleTopbarNavigation(event: Event): void {
  const workspace = (event as CustomEvent<{ workspace?: unknown }>).detail?.workspace
  if (!['materials', 'lesson-tree', 'lesson-prep', 'versions'].includes(String(workspace))) return
  void workbench.openWorkspace(workspace as 'materials' | 'lesson-tree' | 'lesson-prep' | 'versions')
}

onMounted(() => {
  globalThis.addEventListener('teaching-prep:open-workspace', handleTopbarNavigation)
  void workbench.load()
})
onBeforeUnmount(() => {
  globalThis.removeEventListener('teaching-prep:open-workspace', handleTopbarNavigation)
})
</script>

<template>
  <main class="teaching-prep-shell">
    <TeachingPrepLessonContext :compact="workbench.workspace.value === 'versions'" />

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
