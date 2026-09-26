<script setup lang="ts">
import type { RecentSessionSummary } from '../../api/workbench'
import { sessionStatusLabel } from '../../lib/session-status'
import QuickArchiveButton from '../sessions/QuickArchiveButton.vue'

defineProps<{
  sessions: RecentSessionSummary[]
  currentSessionId: number | null
}>()

defineEmits<{
  select: [sessionId: number]
  archived: [sessionId: number]
}>()

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
        <div
          class="recent-session"
          :data-session-id="item.session.id"
          :aria-current="item.session.id === currentSessionId ? 'true' : undefined"
        >
          <button type="button" class="recent-session__select" @click="$emit('select', item.session.id)">
            <span class="recent-session__name">{{ item.session.name }}</span>
            <span class="recent-session__meta">
              {{ sessionStatusLabel(item.session.status) }} · 已批改
              {{ item.progress.graded_papers }} / {{ item.progress.total_papers }} 份
            </span>
            <span class="recent-session__time">{{ displayTime(item.session.updated_at) }}</span>
          </button>
          <QuickArchiveButton
            compact
            :session-id="item.session.id"
            :session-name="item.session.name"
            @archived="$emit('archived', $event)"
          />
        </div>
      </li>
    </ul>
  </section>
</template>
