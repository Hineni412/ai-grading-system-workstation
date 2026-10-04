<script setup lang="ts">
import { computed } from 'vue'
import type { TrainingScanBatch, TrainingScanBatchSummary } from '../../api/training'
import { batchLabel, queueCounts, stageLabels, summaryActions, type ReturnRow } from '../../features/training/training-return'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ batch: TrainingScanBatch; batches: TrainingScanBatchSummary[]; rows: ReturnRow[]; busy: boolean; filter: string; bulk: { kind: string; current: number; total: number } | null }>()
const emit = defineEmits<{ batch: [id: string]; filter: [stage: string]; refresh: []; supplement: []; assess: []; publish: []; issue: []; stop: [] }>()
const stats = computed(() => queueCounts(props.rows))
const actions = computed(() => summaryActions(props.rows))
const counters = computed(() => ['issue', 'scan_issue', 'unjudged', 'running', 'failed', 'review', 'update', 'done'] as const)
</script>
<template>
  <div class="return-summary" aria-label="回收批改汇总">
    <div class="return-summary__batch"><select class="app-input" aria-label="扫描批次" :value="batch.batch_id" :disabled="busy" @change="emit('batch', ($event.target as HTMLSelectElement).value)"><option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">{{ batchLabel(b.batch_id === batch.batch_id ? { ...b, status: batch.status } : b) }}</option><option value="">新建批次…</option></select><AppButton variant="ghost" :disabled="busy || Boolean(bulk)" @click="emit('refresh')">刷新</AppButton></div>
    <div class="return-summary__counts"><template v-for="stage in counters" :key="stage"><button v-if="stats.counts[stage]" type="button" :aria-pressed="filter === stage" @click="emit('filter', stage)">{{ stageLabels[stage] }} <b>{{ stats.counts[stage] }}</b> {{ stage === 'issue' ? '页' : '份' }}</button></template></div>
    <div class="return-summary__progress"><span class="completion-bar" aria-hidden="true"><i :style="{ width: `${stats.total ? stats.done / stats.total * 100 : 0}%` }" /></span><span>已完成 {{ stats.done }}/{{ stats.total }} 份</span></div>
    <div class="return-summary__actions">
      <AppButton variant="ghost" :disabled="busy || Boolean(bulk)" @click="emit('supplement')">补传答卷</AppButton>
      <AppButton v-if="bulk" variant="ghost" @click="emit('stop')">停止发送剩余</AppButton>
      <AppButton v-if="actions.secondaryPublish && !bulk" variant="secondary" :disabled="busy" @click="emit('publish')">确认并更新 {{ stats.counts.update }} 份掌握度</AppButton>
      <AppButton v-if="bulk" variant="primary" disabled>{{ bulk.kind === 'assess' ? '正在判定' : '正在更新' }} {{ bulk.current }}/{{ bulk.total }} 份</AppButton>
      <AppButton v-else-if="actions.primary === 'issue'" variant="primary" :disabled="busy" @click="emit('issue')">处理异常页（{{ stats.counts.issue }}）</AppButton>
      <AppButton v-else-if="actions.primary === 'assess'" variant="primary" :disabled="busy" @click="emit('assess')">判定 {{ stats.counts.unjudged }} 份（{{ stats.counts.unjudged }} 次模型请求）</AppButton>
      <AppButton v-else-if="actions.primary === 'publish'" variant="primary" :disabled="busy" @click="emit('publish')">确认并更新 {{ stats.counts.update }} 份掌握度</AppButton>
    </div>
  </div>
</template>
<style scoped>
.return-summary{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2) var(--space-3);padding:var(--space-3);border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);font-size:var(--font-size-caption)}
.return-summary__batch{display:flex;align-items:center;min-width:0;max-width:310px;gap:var(--space-1)}.return-summary__batch select{min-width:0;width:220px;font-size:var(--font-size-caption)}
.return-summary__counts{display:flex;flex-wrap:wrap;gap:var(--space-1) var(--space-2);flex:1;min-width:160px}.return-summary__counts button{border:0;background:transparent;color:var(--color-text-secondary);font:inherit;padding:var(--space-1);cursor:pointer}.return-summary__counts button[aria-pressed=true]{color:var(--color-accent);background:var(--color-accent-subtle);border-radius:var(--radius-control)}
.return-summary__progress{display:flex;align-items:center;gap:var(--space-2);color:var(--color-text-muted);white-space:nowrap}.completion-bar{height:4px;width:48px;background:var(--color-bg-subtle);border-radius:3px;overflow:hidden}.completion-bar i{display:block;height:100%;background:var(--color-success)}
.return-summary__actions{display:flex;gap:var(--space-2);margin-left:auto;flex:none;white-space:nowrap}.return-summary__actions :deep(button){font-size:var(--font-size-caption);padding-inline:var(--space-2)}
@media(min-width:1024px){.return-summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto}.return-summary__batch{grid-column:1;grid-row:1}.return-summary__actions{grid-column:3;grid-row:1/3;align-self:start}.return-summary__counts{min-width:0;grid-column:2;grid-row:1}.return-summary__progress{grid-column:1/3;grid-row:2}}
@media(min-width:1600px){.return-summary{display:flex}.return-summary__counts{min-width:0}.return-summary__progress{grid-column:auto}}
@media(max-width:1023px){.return-summary__actions{width:100%;justify-content:flex-end;white-space:normal;flex-wrap:wrap}.return-summary__batch{max-width:100%}}
</style>
