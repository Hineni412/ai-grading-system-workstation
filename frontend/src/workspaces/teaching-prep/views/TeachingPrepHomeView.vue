<script setup lang="ts">
import { defineAsyncComponent, onMounted, provide, ref } from 'vue'

import FeedbackBanner from '../../../components/design-system/FeedbackBanner.vue'
import StatePanel from '../../../components/design-system/StatePanel.vue'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { teachingPrepRouteStateKey } from '../workbench/routeContext'
import { useTeachingPrepRouteState } from '../workbench/routeState'
import { useTeachingPrepSemesterScope } from '../workbench/semesterScope'
import OverviewPage from '../workspaces/OverviewPage.vue'
import '../styles/teaching-prep.css'

const LibraryPage = defineAsyncComponent(
  () => import('../workspaces/LibraryPage.vue'),
)
const LessonPage = defineAsyncComponent(
  () => import('../workspaces/LessonPage.vue'),
)

const routeState = useTeachingPrepRouteState()
provide(teachingPrepRouteStateKey, routeState)

const catalog = useTeachingPrepCatalogStore()
const curriculumScope = useCurriculumScopeStore()
const loading = ref(true)
const loadError = ref('')
const semesterScope = useTeachingPrepSemesterScope({
  onSemesterMissing: message => { loadError.value = message },
})

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const needsMaterials = routeState.currentView.value !== 'overview'
    await Promise.all([
      curriculumScope.initialize(),
      catalog.load(needsMaterials ? 'materials' : 'overview'),
    ])
    if (curriculumScope.loadState === 'ready') {
      await semesterScope.syncSemesterForGlobalScope(needsMaterials ? 'materials' : 'overview')
    }
    if (routeState.currentView.value === 'library') await catalog.ensureMaterialData()
  } catch {
    loadError.value = catalog.errorMessage || '备课工作台暂时无法载入。'
  } finally {
    loading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <main class="tp-shell">
    <FeedbackBanner
      v-if="loadError && !loading"
      tone="warning"
      title="备课工作台状态提示"
      :description="loadError"
      action-label="重新载入"
      dismissible
      @action="load"
      @dismiss="loadError = ''"
    />
    <StatePanel
      v-if="loading"
      kind="loading"
      title="正在载入备课工作台"
      description="正在整理学期、课时与本地资料……"
    />
    <template v-else>
      <OverviewPage v-if="routeState.currentView.value === 'overview'" />
      <LibraryPage v-else-if="routeState.currentView.value === 'library'" />
      <LessonPage v-else />
    </template>
  </main>
</template>
