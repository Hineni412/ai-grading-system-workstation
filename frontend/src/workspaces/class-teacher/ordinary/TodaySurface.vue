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
  openDraft: [draftId: string]
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
const visibleGroups = computed(() => groups.value.filter((group) => group.items.length > 0))
const visibleItemCount = computed(() => new Set(
  visibleGroups.value.flatMap((group) => group.items.map((item) => item.node_id)),
).size)
const summaryItems = computed(() => visibleGroups.value.map((group) => ({
  key: group.key,
  label: group.label.replace('已经', '').replace('要推进', ''),
  count: group.items.length,
})))
function select(node: WorkNode) { void props.module.inspect(node) }
function forwardRestricted(projectionId: string, projectionType: string | null) { emit('openRestricted', projectionId, projectionType) }
onMounted(() => { void props.module.load('today') })
</script>

<template>
  <section class="surface">
    <header class="surface__head">
      <div><h2>今天的工作</h2><span v-if="visibleItemCount">{{ visibleItemCount }} 项待推进</span></div>
      <button type="button" @click="module.load('today')">刷新</button>
    </header>
    <QuickWorkCapture :module="module" :token="token" @handoff="emit('handoff', $event)" @open-draft="emit('openDraft', $event)" />
    <div v-if="summaryItems.length" class="summary" aria-label="工作摘要">
      <div v-for="item in summaryItems" :key="item.key"><strong>{{ item.count }}</strong><span>{{ item.label }}</span></div>
    </div>
    <p v-if="module.error.value" class="error" role="alert">{{ module.error.value }}</p>
    <div v-if="visibleGroups.length" class="workspace">
      <div class="lanes" :aria-busy="module.loading.value">
        <section v-for="group in visibleGroups" :key="group.key" class="lane">
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
        </section>
      </div>
      <WorkNodeInspector :module="module" @open-restricted="forwardRestricted" />
    </div>
  </section>
</template>

<style scoped>
.surface { overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); }
.surface__head { display: flex; align-items: center; justify-content: space-between; padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--color-border-subtle); }.surface__head>div{display:flex;align-items:baseline;gap:var(--space-3)}.surface__head h2 { margin: 0; font-size: var(--font-size-h3); }.surface__head span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}.surface__head button { min-height: 34px; padding: 0 var(--space-3); border: 1px solid var(--color-border-strong); border-radius: var(--radius-control); background: transparent; }
.summary { display: flex; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-2) var(--space-5); border-bottom: 1px solid var(--color-border-subtle); }.summary div { display: flex; align-items: baseline; gap: var(--space-1); }.summary div+div::before{content:'\00b7';margin-right:var(--space-1);color:var(--color-text-muted)}.summary strong { font-size: var(--font-size-body); }.summary span { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr) 380px; }.lanes { padding: var(--space-3) var(--space-5) var(--space-6); }.lane h3 { display: flex; justify-content: space-between; margin: var(--space-4) 0 var(--space-2); font-size: var(--font-size-body); }.lane h3 span { color: var(--color-text-muted); }
.work-row { position: relative; display: grid; grid-template-columns: 4px minmax(0, 1fr) auto; width: 100%; align-items: center; gap: var(--space-3); padding: var(--space-3) 0; border: 0; border-bottom: 1px solid var(--color-border-subtle); background: transparent; text-align: left; cursor: pointer; }.work-row:hover { background: var(--color-bg-subtle); }.work-row__line { align-self: stretch; border-radius: 4px; background: var(--color-accent); }.work-row--restricted .work-row__line { background: var(--color-warning); }.work-row span:nth-child(2) { display: grid; gap: 3px; }.work-row small,.work-row time { color: var(--color-text-secondary); font-size: var(--font-size-dense); }.error { margin:var(--space-3) var(--space-5);color: var(--color-danger); }
@media (max-width: 980px) { .workspace { grid-template-columns: 1fr; } }
</style>
