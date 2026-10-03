<script setup lang="ts">
import { computed } from 'vue'
import type { TrainingOverview } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
import { compareFocusNodes, shortNodeName } from '../knowledge-overview/model'
import { volumeItems, nodeLocation, overviewMetrics, weakRate } from '../knowledge-overview/metrics'
const props = defineProps<{ overview: TrainingOverview | null; state: string; volumeId: string | null; termLabel: string; scope: string }>()
defineEmits<{ open: [path: string]; retry: [] }>()
const skills = computed(() => volumeItems(props.overview?.nodes ?? []).filter(n => n.kind === 'skill' && n.distribution.weak > 0).sort(compareFocusNodes).slice(0, 3))
const metrics = computed(() => props.overview ? overviewMetrics(props.overview) : null)
const scopeLabel = computed(() => props.scope === 'all' ? '全部学生' : /^\d+$/.test(props.scope) ? `${props.scope} 班` : props.scope)
</script>
<template>
  <section class="workbench-panel workbench-insight" aria-labelledby="workbench-insight-title">
    <header class="workbench-panel-heading"><h2 id="workbench-insight-title">本学期学情</h2></header>
    <p v-if="!volumeId" class="workbench-quiet">选择教学学期后显示学情</p>
    <div v-else-if="(state === 'idle' || state === 'loading') && !overview" class="workbench-skeleton" role="status" aria-label="正在读取本学期学情" aria-busy="true"><span v-for="n in 3" :key="n" /></div>
    <div v-else-if="state === 'error'" class="workbench-quiet" role="alert"><p>学情暂时无法读取</p><AppButton @click="$emit('retry')">重试</AppButton></div>
    <template v-else-if="overview && metrics">
      <p class="workbench-meta">{{ termLabel }} · {{ scopeLabel }}</p>
      <div v-if="state === 'stale-error'" class="workbench-source-error" role="status"><span>学情可能不是最新</span><AppButton variant="ghost" @click="$emit('retry')">重试</AppButton></div>
      <p v-if="!metrics.evidence" class="workbench-quiet">本学期还没有学情证据</p>
      <p v-else-if="!skills.length" class="workbench-quiet">当前范围没有明显薄弱的技能</p>
      <button v-for="node in skills" :key="node.knowledge_key" type="button" class="workbench-skill" :data-knowledge="node.knowledge_key" @click="$emit('open', '/knowledge-overview')">
        <span class="workbench-skill-top"><span class="workbench-skill-name"><strong>{{ shortNodeName(node) }}</strong><small>{{ nodeLocation(node, overview.nodes) }}</small></span><span class="workbench-numeric">{{ node.distribution.weak }}/{{ node.evidence_student_count }} 人明显薄弱</span></span>
        <span class="workbench-bar" aria-hidden="true"><i :style="{ width: `${(weakRate(node) ?? 0) * 100}%` }" /></span>
      </button>
      <p class="workbench-meta workbench-insight-total">本册 {{ metrics.total }} 项，{{ metrics.evidence }} 项有证据</p>
    </template>
    <AppButton variant="ghost" class="workbench-footer-link" @click="$emit('open', '/knowledge-overview')">学情总览 →</AppButton>
  </section>
</template>
