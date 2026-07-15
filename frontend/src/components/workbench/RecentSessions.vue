<script setup lang="ts">
import type { RecentSessionSummary } from '../../api/workbench'

defineProps<{
  sessions: RecentSessionSummary[]
  currentSessionId: number | null
}>()

defineEmits<{
  select: [sessionId: number]
}>()

function sessionStatus(status: string): string {
  const labels: Record<string, string> = {
    created: '待开始',
    pending: '待开始',
    grading: '批改中',
    completed: '已完成',
    failed: '有失败记录',
  }
  return labels[status] ?? status
}

function displayTime(value: string | null): string {
  if (value === null) return '更新时间暂不可用'
  return `更新于 ${value.replace('T', ' ').replace('Z', '')}`
}
</script>

<template>
  <section class="workbench-section workbench-recent" aria-labelledby="recent-sessions-title">
    <header class="workbench-section__heading">
      <div>
        <p class="workbench-eyebrow">考试上下文</p>
        <h2 id="recent-sessions-title">最近考试</h2>
      </div>
    </header>
    <p v-if="sessions.length === 0" class="workbench-empty-copy">暂无最近考试</p>
    <ul v-else class="recent-session-list">
      <li v-for="item in sessions" :key="item.session.id">
        <button
          type="button"
          class="recent-session"
          :data-session-id="item.session.id"
          :aria-current="item.session.id === currentSessionId ? 'true' : undefined"
          @click="$emit('select', item.session.id)"
        >
          <span class="recent-session__name">{{ item.session.name }}</span>
          <span class="recent-session__meta">
            {{ sessionStatus(item.session.status) }} · 已批改
            {{ item.progress.graded_papers }} / {{ item.progress.total_papers }} 份
          </span>
          <span class="recent-session__time">{{ displayTime(item.session.updated_at) }}</span>
        </button>
      </li>
    </ul>
  </section>
</template>
