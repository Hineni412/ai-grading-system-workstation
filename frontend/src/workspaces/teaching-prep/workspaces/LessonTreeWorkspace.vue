<script setup lang="ts">
import { computed } from 'vue'

import type { LessonNode, SemesterLessonProgressStatus } from '../api/catalog'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'

const workbench = useTeachingPrepWorkbenchContext()
const lessons = computed(() => workbench.catalog.lessonNodes.filter(
  item => item.node_type === 'lesson' && item.is_active,
))
const statusMap = computed(() => new Map(
  workbench.lessonStatuses.value.map(item => [item.lesson_node_id, item]),
))

function manualLabel(status: SemesterLessonProgressStatus): string {
  return {
    not_started: '未开始',
    preparing: '备课中',
    ready: '已备好',
    taught: '已授课',
    skipped: '本学期跳过',
  }[status]
}

function preparationLabel(stage: string): string {
  return {
    select: '待选课时',
    materials: '资料核对中',
    plan: '方案准备中',
    slides: '课件审核中',
    package: '课件已可信',
  }[stage] ?? '待准备'
}

async function setProgress(
  lesson: LessonNode,
  event: Event,
): Promise<void> {
  await workbench.catalog.setSemesterLessonProgress(
    lesson.id,
    (event.target as HTMLSelectElement).value as SemesterLessonProgressStatus,
  )
  await workbench.refreshCurrentWorkspace()
}
</script>

<template>
  <section class="tp-workspace tp-lesson-tree-workspace">
    <header class="tp-workspace__header">
      <div>
        <p class="tp-eyebrow">个人课时树</p>
        <h1 data-workbench-title tabindex="-1">下一节新授课是什么？</h1>
        <p>按课时树顺序准备，不需要先填写日期或完整录入整册。</p>
      </div>
      <button class="tp-button tp-button--secondary" type="button" @click="workbench.catalog.load()">
        刷新课时树
      </button>
    </header>

    <div v-if="!lessons.length" class="tp-empty-state">
      <strong>还没有新授课课时</strong>
      <p>先在资料库登记教材，或使用已有的新增课时能力逐步建立近期课时。</p>
    </div>

    <div v-else class="tp-lesson-board">
      <div class="tp-lesson-board__list" role="list" aria-label="本学期新授课">
        <article
          v-for="(lesson, index) in lessons"
          :key="lesson.id"
          class="tp-lesson-row"
          :class="{
            'is-selected': lesson.id === workbench.catalog.selectedLessonId,
            'is-taught': statusMap.get(lesson.id)?.manual_progress === 'taught',
          }"
          role="listitem"
        >
          <button type="button" class="tp-lesson-row__main" @click="workbench.openLesson(lesson.id)">
            <span class="tp-lesson-row__order">{{ index + 1 }}</span>
            <span>
              <strong>{{ lesson.title }}</strong>
              <small>{{ lesson.duration_minutes ?? 45 }} 分钟 · {{ statusMap.get(lesson.id)?.next_action ?? '核对资料' }}</small>
            </span>
          </button>
          <span class="tp-status-pill" :class="`is-${statusMap.get(lesson.id)?.manual_progress ?? 'not_started'}`">
            {{ manualLabel(statusMap.get(lesson.id)?.manual_progress ?? 'not_started') }}
          </span>
          <span class="tp-preparation-state">
            系统准备度：{{ preparationLabel(statusMap.get(lesson.id)?.preparation_stage ?? 'select') }}
          </span>
          <select
            aria-label="修改授课状态"
            :value="statusMap.get(lesson.id)?.manual_progress ?? 'not_started'"
            @change="setProgress(lesson, $event)"
          >
            <option value="not_started">未开始</option>
            <option value="preparing">备课中</option>
            <option value="ready">已备好</option>
            <option value="taught">已授课</option>
            <option value="skipped">本学期跳过</option>
          </select>
        </article>
      </div>

      <aside class="tp-readiness-panel">
        <p class="tp-eyebrow">本节准备度</p>
        <h2>{{ workbench.catalog.selectedLesson?.title ?? '选择一节课' }}</h2>
        <ul v-if="workbench.selectedStatus.value">
          <li :class="{ 'is-ready': workbench.catalog.materialLinks.length }">
            <span>教材与参考资料</span>
            <strong>{{ workbench.catalog.materialLinks.length ? '已有确认范围' : '待核对' }}</strong>
          </li>
          <li :class="{ 'is-ready': workbench.selectedStatus.value.latest.resource_pack_id }">
            <span>授课资源包</span>
            <strong>{{ workbench.selectedStatus.value.latest.resource_pack_id ? '已冻结' : '未冻结' }}</strong>
          </li>
          <li :class="{ 'is-ready': workbench.selectedStatus.value.latest.lesson_draft_id }">
            <span>课堂方案</span>
            <strong>{{ workbench.selectedStatus.value.latest.lesson_draft_id ? '已有确认版本' : '待确定' }}</strong>
          </li>
          <li :class="{ 'is-ready': workbench.selectedStatus.value.latest.pptx_version_id }">
            <span>可信 PPTX</span>
            <strong>{{ workbench.selectedStatus.value.latest.pptx_version_id ? '可用' : '尚未生成' }}</strong>
          </li>
        </ul>
        <button
          class="tp-button tp-button--primary"
          type="button"
          :disabled="!workbench.catalog.selectedLessonId"
          @click="workbench.openStage('materials')"
        >
          {{ workbench.selectedStatus.value?.next_action ?? '开始核对资料' }}
        </button>
        <p v-if="workbench.selectedStatus.value?.blockers.length" class="tp-inline-guidance">
          {{ workbench.selectedStatus.value.blockers.join('；') }}
        </p>
      </aside>
    </div>
  </section>
</template>
