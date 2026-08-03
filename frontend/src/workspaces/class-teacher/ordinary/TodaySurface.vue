<script setup lang="ts">
import { computed, onMounted } from 'vue'

import type { HomeIntakeHandoff } from '../api/homeIntake'
import type { WorkNode } from '../api/work'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'
import WorkNodeInspector from './WorkNodeInspector.vue'
import QuickWorkCapture from './QuickWorkCapture.vue'

const props = defineProps<{ module: OrdinaryWorkModule; token?: string }>()
const emit = defineEmits<{
  openRestricted: [projectionId: string, projectionType: string | null]
  handoff: [value: HomeIntakeHandoff]
}>()
const groups = computed(() => [
  { key: 'overdue', label: '已经逾期', items: props.module.snapshot.value?.overdue ?? [] },
  { key: 'today', label: '今天要推进', items: props.module.snapshot.value?.today ?? [] },
  { key: 'review_due', label: '待复查', items: props.module.snapshot.value?.review_due ?? [] },
  {
    key: 'unscheduled',
    label: '日期待定',
    items: props.module.snapshot.value?.nodes.filter((item) => (
      !item.due_date && !['waiting', 'completed', 'cancelled'].includes(item.status)
    )) ?? [],
  },
  { key: 'waiting', label: '等待中', items: props.module.snapshot.value?.waiting ?? [] },
])
function select(node: WorkNode) { void props.module.inspect(node) }
function forwardRestricted(projectionId: string, projectionType: string | null) { emit('openRestricted', projectionId, projectionType) }
onMounted(() => { void props.module.load('today') })
</script>

<template>
  <section class="surface">
    <header class="surface__head">
      <div><p>每日工作脉络</p><h2>今天先处理什么</h2></div>
      <button type="button" @click="module.load('today')">刷新</button>
    </header>
    <QuickWorkCapture :module="module" :token="token" @handoff="emit('handoff', $event)" />
    <div class="summary" aria-label="工作摘要">
      <div><strong>{{ module.snapshot.value?.summary?.today ?? 0 }}</strong><span>今天</span></div>
      <div><strong>{{ module.snapshot.value?.summary?.overdue ?? 0 }}</strong><span>逾期</span></div>
      <div><strong>{{ module.snapshot.value?.summary?.waiting ?? 0 }}</strong><span>等待</span></div>
      <div><strong>{{ module.snapshot.value?.summary?.review_due ?? 0 }}</strong><span>待复查</span></div>
      <div><strong>{{ groups.find((item) => item.key === 'unscheduled')?.items.length ?? 0 }}</strong><span>日期待定</span></div>
    </div>
    <div class="workspace">
      <div class="lanes" :aria-busy="module.loading.value">
        <p v-if="module.error.value" class="error" role="alert">{{ module.error.value }}</p>
        <section v-for="group in groups" :key="group.key" class="lane">
          <h3>{{ group.label }} <span>{{ group.items.length }}</span></h3>
          <button
            v-for="node in group.items"
            :key="node.node_id"
            class="work-row"
            :class="{ 'work-row--restricted': node.classification === 'restricted_projection' }"
            type="button"
            @click="select(node)"
          >
            <span class="work-row__line" aria-hidden="true"></span>
            <span><strong>{{ node.title }}</strong><small>{{ node.details || (node.classification === 'restricted_projection' ? '解锁后查看具体内容' : '无补充说明') }}</small></span>
            <time>{{ node.due_date || '未定日期' }}</time>
          </button>
          <p v-if="!group.items.length" class="empty">这一段目前没有工作。</p>
        </section>
      </div>
      <WorkNodeInspector :module="module" @open-restricted="forwardRestricted" />
    </div>
  </section>
</template>

<style scoped>
.surface { overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); }
.surface__head { display: flex; align-items: end; justify-content: space-between; padding: var(--space-5); border-bottom: 1px solid var(--color-border-subtle); }.surface__head p { margin: 0 0 2px; color: var(--color-accent); font-size: var(--font-size-caption); font-weight: 700; letter-spacing: .08em; }.surface__head h2 { margin: 0; font-size: var(--font-size-h2); }.surface__head button { min-height: 36px; padding: 0 var(--space-3); border: 1px solid var(--color-border-strong); border-radius: var(--radius-control); background: transparent; }
.summary { display: grid; grid-template-columns: repeat(5, 1fr); border-bottom: 1px solid var(--color-border-subtle); }.summary div { display: flex; align-items: baseline; gap: var(--space-2); padding: var(--space-3) var(--space-5); border-right: 1px solid var(--color-border-subtle); }.summary strong { font-size: var(--font-size-h2); }.summary span { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr) 380px; }.lanes { padding: var(--space-3) var(--space-5) var(--space-6); }.lane h3 { display: flex; justify-content: space-between; margin: var(--space-4) 0 var(--space-2); font-size: var(--font-size-body); }.lane h3 span { color: var(--color-text-muted); }
.work-row { position: relative; display: grid; grid-template-columns: 4px minmax(0, 1fr) auto; width: 100%; align-items: center; gap: var(--space-3); padding: var(--space-3) 0; border: 0; border-bottom: 1px solid var(--color-border-subtle); background: transparent; text-align: left; cursor: pointer; }.work-row:hover { background: var(--color-bg-subtle); }.work-row__line { align-self: stretch; border-radius: 4px; background: var(--color-accent); }.work-row--restricted .work-row__line { background: var(--color-warning); }.work-row span:nth-child(2) { display: grid; gap: 3px; }.work-row small,.work-row time,.empty { color: var(--color-text-secondary); font-size: var(--font-size-dense); }.empty { padding: var(--space-3) 0; }.error { color: var(--color-danger); }
@media (max-width: 980px) { .workspace { grid-template-columns: 1fr; }.summary { grid-template-columns: repeat(2,1fr); } }
</style>
