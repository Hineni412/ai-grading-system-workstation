<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import TableListPanel from './tables/TableListPanel.vue'
import TimetablePanel from './timetable/TimetablePanel.vue'

type DailyTab = 'timetable' | 'tables'

const route = useRoute()
const router = useRouter()

const activeTab = computed<DailyTab>(() => (route.query.tab === 'tables' ? 'tables' : 'timetable'))

function selectTab(tab: DailyTab): void {
  if (tab === activeTab.value) return
  void router.replace({
    path: '/daily',
    query: tab === 'timetable' ? {} : { tab },
  })
}
</script>

<template>
  <section class="daily-management">
    <header class="daily-management__hero">
      <div>
        <p class="daily-eyebrow">Daily management</p>
        <h1 tabindex="-1">日常管理</h1>
        <p>课表调课、教学进度与班级临时表格记录。</p>
      </div>
    </header>

    <div class="daily-tabs" role="tablist" aria-label="日常管理功能">
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'timetable'"
        :class="{ 'is-active': activeTab === 'timetable' }"
        @click="selectTab('timetable')"
      >
        课表
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'tables'"
        :class="{ 'is-active': activeTab === 'tables' }"
        @click="selectTab('tables')"
      >
        表格
      </button>
    </div>

    <TimetablePanel v-if="activeTab === 'timetable'" />
    <TableListPanel v-else />
  </section>
</template>

<style scoped>
.daily-management {
  box-sizing: border-box;
  display: grid;
  gap: var(--space-3);
  width: 100%;
  max-width: none;
  margin: 0 auto;
  padding: 12px 16px;
}

.daily-management__hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  padding-bottom: var(--space-3);
  border-bottom: var(--border-width) solid var(--color-border-default);
}

.daily-management__hero h1 {
  margin: var(--space-1) 0 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-display);
}

.daily-management__hero p:not(.daily-eyebrow) {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
}

.daily-eyebrow {
  margin: 0;
  color: var(--color-text-muted);
}

.daily-tabs {
  display: flex;
  gap: var(--space-2);
}

.daily-tabs button {
  min-height: var(--control-height-default);
  padding: 0 16px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}

.daily-tabs button.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-medium);
}
</style>
