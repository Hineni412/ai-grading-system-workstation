<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import TableListPanel from './tables/TableListPanel.vue'
import TimetablePanel from './timetable/TimetablePanel.vue'

type DailyTab = 'timetable' | 'tables'

const route = useRoute()
const router = useRouter()

const activeTab = computed<DailyTab>(() => (route.query.tab === 'tables' ? 'tables' : 'timetable'))
const customSlotsOpen = ref(false)

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
    <header class="daily-pagebar">
      <h1 tabindex="-1">日常管理</h1>
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
      <span class="daily-pagebar__spacer"></span>
      <button
        v-if="activeTab === 'timetable'"
        type="button"
        class="daily-pagebar__toggle"
        :aria-expanded="customSlotsOpen"
        @click="customSlotsOpen = !customSlotsOpen"
      >
        {{ customSlotsOpen ? '收起自定义时段' : '自定义时段' }}
      </button>
    </header>

    <TimetablePanel v-if="activeTab === 'timetable'" :custom-open="customSlotsOpen" />
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

/* 页头一行化：标题、页签与自定义时段开关共用一行。 */
.daily-pagebar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.daily-pagebar h1 {
  margin: 0;
  color: var(--color-text-primary);
  font-size: var(--font-size-h3);
}

.daily-pagebar__spacer {
  flex: 1;
}

.daily-pagebar__toggle {
  min-height: var(--control-height-small);
  padding: 0 12px;
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}

.daily-pagebar__toggle[aria-expanded='true'] {
  border-color: var(--color-accent);
  background: var(--color-accent-subtle);
  color: var(--color-text-primary);
}

.daily-tabs {
  display: flex;
  gap: var(--space-2);
}

.daily-tabs button {
  min-height: var(--control-height-small);
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
