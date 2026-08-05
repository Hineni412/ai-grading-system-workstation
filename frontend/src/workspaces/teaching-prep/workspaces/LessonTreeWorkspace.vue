<script setup lang="ts">
import { computed } from 'vue'

import type { LessonNode, SemesterLessonProgressStatus } from '../api/catalog'
import { useTeachingPrepWorkbenchContext } from '../workbench/context'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'

const workbench = useTeachingPrepWorkbenchContext()
const aiTasks = useWorkspaceAITaskStore()
const statusMap = computed(() => new Map(
  workbench.lessonStatuses.value.map(item => [item.lesson_node_id, item]),
))
const lessons = computed(() => {
  const candidates = workbench.catalog.lessonNodes.filter(item => {
    const status = statusMap.value.get(item.id)
    return item.node_type === 'lesson' && item.is_active && status?.manual_progress !== 'skipped'
  })
  const focus = candidates.findIndex(item => statusMap.value.get(item.id)?.manual_progress !== 'taught')
  const focusIndex = focus < 0 ? candidates.length - 1 : focus
  let start = Math.max(0, focusIndex - 2)
  if (candidates.length - start < 5) start = Math.max(0, candidates.length - 5)
  return candidates.slice(start, start + 8)
})
const activeTaskLessons = computed(() => new Set(
  [
    ...workbench.lessonStatuses.value.flatMap(status => (
      status.ai_tasks.length ? [status.lesson_node_id] : []
    )),
    ...aiTasks.orderedTasks
      .filter(task => (
        ['prepared', 'queued', 'running', 'needs_input'].includes(task.status)
        || (task.status === 'proposal_ready' && task.pending_count > 0)
      ))
      .flatMap(task => [
      ...(task.source_ref.kind === 'lesson' ? [task.source_ref.id] : []),
      ...task.handoffs.flatMap(handoff => handoff.subject_refs.filter(ref => ref.kind === 'lesson').map(ref => ref.id)),
      ]),
  ],
))

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

async function openAt(lesson: LessonNode, cell: 'materials' | 'plan' | 'exercises' | 'slides') {
  await workbench.openLesson(lesson.id)
  await workbench.openPanel(cell === 'materials' ? 'sources' : cell)
}

const cellLabels = { materials: '核资料', plan: '课堂方案', exercises: '候选练习', slides: '课件（可选）' } as const
const stateLabels = { not_started: '未开始', in_progress: '进行中', needs_teacher: '待你处理', ready: '已就绪', stale: '来源已变化', failed: '未完成', not_applicable: '本节不需要' } as const
</script>

<template>
  <section class="tp-workspace tp-overview-workspace">
    <header class="tp-overview-hero">
      <div>
        <p class="tp-eyebrow">近期课时 · 一张表看清</p>
        <h1 data-workbench-title tabindex="-1">先把下一节课准备到“能上”。</h1>
        <p>教师授课状态与系统准备度分开呈现；点状态格，直接回到那一步。</p>
      </div>
      <div class="tp-overview-hero__next">
        <span>建议先处理</span>
        <strong>{{ workbench.lessonStatuses.value[0]?.next_action ?? '建立近期课时' }}</strong>
        <button class="tp-button tp-button--primary" type="button" :disabled="!lessons[0]" @click="lessons[0] && workbench.openLesson(lessons[0].id)">继续备课</button>
      </div>
    </header>

    <div v-if="!lessons.length" class="tp-empty-state">
      <strong>还没有新授课课时</strong>
      <p>先在资料库登记教材，或使用已有的新增课时能力逐步建立近期课时。</p>
    </div>

    <div v-else class="tp-readiness-matrix" role="table" aria-label="近期课时准备度">
      <div class="tp-readiness-matrix__head" role="row">
        <span role="columnheader">近期课时</span><span v-for="label in cellLabels" :key="label" role="columnheader">{{ label }}</span><span role="columnheader">授课状态</span>
      </div>
      <article
          v-for="(lesson, index) in lessons"
          :key="lesson.id"
          class="tp-readiness-matrix__row"
          role="row"
        >
          <button role="cell" type="button" class="tp-readiness-matrix__lesson" @click="workbench.openLesson(lesson.id)">
            <span>{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ lesson.title }}</strong><small>{{ activeTaskLessons.has(lesson.id) ? 'AI 任务处理中 · ' : '' }}{{ lesson.duration_minutes ?? 45 }} 分钟</small>
          </button>
          <button v-for="step in (['materials','plan','exercises','slides'] as const)" :key="step" role="cell" type="button" class="tp-readiness-cell" :class="`is-${statusMap.get(lesson.id)?.cells[step].status ?? 'not_started'}`" :aria-label="`${cellLabels[step]}：${statusMap.get(lesson.id)?.cells[step].summary ?? '未开始'}`" @click="openAt(lesson, step)">
            <span aria-hidden="true" /><strong>{{ stateLabels[statusMap.get(lesson.id)?.cells[step].status ?? 'not_started'] }}</strong><small>{{ statusMap.get(lesson.id)?.cells[step].summary ?? '打开查看' }}</small>
          </button>
          <select
            role="cell"
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
    <footer v-if="lessons.length" class="tp-overview-footnote"><span>系统准备度</span>只说明资料和版本是否齐全；“已授课”仍由教师亲自标记。</footer>
  </section>
</template>
