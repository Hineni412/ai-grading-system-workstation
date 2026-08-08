<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide } from 'vue'

import TeachingPrepLessonContext from '../components/TeachingPrepLessonContext.vue'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import { useTeachingPrepWorkbench } from '../workbench/state'
import LessonTreeWorkspace from '../workspaces/LessonTreeWorkspace.vue'
import MaterialLibraryWorkspace from '../workspaces/MaterialLibraryWorkspace.vue'
import LessonMaterialConfirmationWorkspace from '../workspaces/LessonMaterialConfirmationWorkspace.vue'
import PresentationVersionsWorkspace from '../workspaces/PresentationVersionsWorkspace.vue'
import '../styles/teaching-prep.css'

const workbench = useTeachingPrepWorkbench()
provide(teachingPrepWorkbenchKey, workbench)

const workspaceComponent = computed(() => ({
  'lesson-tree': LessonTreeWorkspace,
  materials: MaterialLibraryWorkspace,
  'lesson-prep': LessonMaterialConfirmationWorkspace,
  versions: PresentationVersionsWorkspace,
}[workbench.workspace.value]))

const lessonStageLabels = {
  materials: '确认资料',
  slides: '审核改编',
  package: '生成副本',
} as const

const visibleNextAction = computed(() => {
  const status = workbench.selectedStatus.value
  if (!status || status.cells.materials.status !== 'ready') return '确认主课件和参考资料'
  if (status.cells.slides.status !== 'ready') return '查看 AI 改编并逐页确认'
  if (!status.latest.pptx_version_id) return '生成新的 PPTX 副本'
  return 'PPTX 副本已就绪'
})

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
    <TeachingPrepLessonContext
      v-if="workbench.view.value === 'lesson'"
      :compact="workbench.workspace.value === 'versions'"
    />

    <div v-if="workbench.workbenchError.value" class="tp-global-notice" role="alert">
      <strong>当前状态未完全载入</strong>
      <span>{{ workbench.workbenchError.value }}</span>
      <button type="button" @click="workbench.refreshCurrentWorkspace()">重新载入当前工作面</button>
    </div>

    <div v-if="workbench.loading.value" class="tp-loading" role="status">
      <span aria-hidden="true" />正在整理课时与本地资料……
    </div>
    <template v-else>
      <LessonTreeWorkspace v-if="workbench.view.value === 'overview'" />
      <MaterialLibraryWorkspace v-else-if="workbench.view.value === 'library'" />
      <div v-else class="tp-lesson-frame">
        <aside class="tp-lesson-frame__rail" aria-label="单课时阶段">
          <p class="tp-eyebrow">本节路径</p>
          <button
            v-for="item in workbench.stages.value.filter(item => !['select', 'plan'].includes(item.id))"
            :key="item.id"
            type="button"
            :class="{ 'is-active': item.id === workbench.stage.value }"
            @click="workbench.openStage(item.id)"
          >
            <span>{{ lessonStageLabels[item.id as keyof typeof lessonStageLabels] }}</span>
            <small>{{ item.explanation }}</small>
          </button>
          <button type="button" class="tp-lesson-frame__back" @click="workbench.openStage('select')">
            返回备课首页
          </button>
        </aside>
        <section class="tp-lesson-frame__canvas" aria-label="当前课时工作面">
          <component :is="workspaceComponent" />
        </section>
        <aside class="tp-lesson-frame__inspector" aria-label="当前课时准备摘要">
          <p class="tp-eyebrow">准备摘要</p>
          <h2>{{ workbench.catalog.selectedLesson?.title ?? '当前课时' }}</h2>
          <dl>
            <div><dt>授课状态</dt><dd>{{ workbench.selectedStatus.value?.manual_progress ?? '未开始' }}</dd></div>
            <div><dt>当前步骤</dt><dd>{{ lessonStageLabels[workbench.stage.value as keyof typeof lessonStageLabels] ?? '确认资料' }}</dd></div>
            <div><dt>下一动作</dt><dd>{{ visibleNextAction }}</dd></div>
          </dl>
          <p v-if="workbench.dirtyReason.value" class="tp-inline-guidance" role="status">
            尚未保存：{{ workbench.dirtyReason.value }}
          </p>
          <p v-else class="tp-inline-guidance">所有已确认修改均已保存为本地版本。</p>
        </aside>
      </div>
    </template>
  </main>
</template>
