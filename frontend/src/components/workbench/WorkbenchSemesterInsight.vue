<script setup lang="ts">
import { computed } from 'vue'
import type { TrainingOverview } from '../../api/training'
import AppButton from '../design-system/AppButton.vue'
import { compareFocusNodes, shortNodeName } from '../knowledge-overview/model'
import { volumeItems, nodeLocation } from '../knowledge-overview/metrics'

const props = defineProps<{ overview: TrainingOverview | null; state: string; volumeId: string | null; termLabel: string; scope: string }>()
defineEmits<{ open: [path: string]; retry: [] }>()
const typeMode = computed(() => props.overview?.target_kind === 'type')
const targets = computed(() => volumeItems(props.overview?.nodes ?? [])
  .filter(node => node.target_kind === 'knowledge' ? node.kind === 'topic'
    : node.kind === (node.target_kind === 'type' || typeMode.value ? 'type' : 'skill')))
const targetLabel = computed(() => typeMode.value ? '题型' : '训练目标')
const evidenceCount = computed(() => targets.value.filter(node => node.evidence_student_count > 0).length)
const attention = computed(() => targets.value
  .filter(node => node.distribution.weak + node.distribution.unsteady > 0)
  .sort(compareFocusNodes))
const scopeLabel = computed(() => props.scope === 'all' ? '全部学生' : /^\d+$/.test(props.scope) ? `${props.scope} 班` : props.scope)
</script>
<template>
  <section class="workbench-panel workbench-insight" aria-labelledby="workbench-insight-title">
    <header class="workbench-panel-heading"><h2 id="workbench-insight-title">本学期学情</h2></header>
    <p v-if="!volumeId" class="workbench-quiet">选择教学学期后显示学情</p>
    <div v-else-if="(state === 'idle' || state === 'loading') && !overview" class="workbench-skeleton" role="status" aria-label="正在读取本学期学情" aria-busy="true"><span v-for="n in 3" :key="n" /></div>
    <div v-else-if="state === 'error'" class="workbench-quiet" role="alert"><p>学情暂时无法读取</p><AppButton @click="$emit('retry')">重试</AppButton></div>
    <template v-else-if="overview">
      <p class="workbench-meta">{{ termLabel }} · {{ scopeLabel }}</p>
      <div v-if="state === 'stale-error'" class="workbench-source-error" role="status"><span>学情可能不是最新</span><AppButton variant="ghost" @click="$emit('retry')">重试</AppButton></div>
      <dl class="workbench-insight-stats">
        <div><dt>有作答证据</dt><dd>{{ evidenceCount }}<small>/{{ targets.length }} {{ targetLabel }}</small></dd></div>
        <div><dt>需关注{{ targetLabel }}</dt><dd>{{ attention.length }}<small>项</small></dd></div>
      </dl>
      <p v-if="!targets.length" class="workbench-insight-empty">本册暂未设置训练目标</p>
      <p v-else-if="!evidenceCount" class="workbench-insight-empty">当前范围还没有可用作答证据</p>
      <p v-else-if="!attention.length" class="workbench-insight-empty">已有证据的{{ targetLabel }}暂无明显薄弱或还不稳</p>
      <template v-else>
        <p class="workbench-insight-caption">优先关注</p>
        <button v-for="(node, index) in attention.slice(0, 3)" :key="node.knowledge_key" type="button"
          class="workbench-target" :data-knowledge="node.knowledge_key"
          :aria-label="`查看${shortNodeName(node)}学情`"
          @click="$emit('open', `/knowledge-overview?focus=${encodeURIComponent(node.knowledge_key)}`)">
          <span class="workbench-target-rank" aria-hidden="true">{{ index + 1 }}</span>
          <span class="workbench-target-name"><strong>{{ shortNodeName(node) }}</strong><small>{{ nodeLocation(node, overview.nodes) }}</small></span>
          <span class="workbench-target-count"><strong>{{ node.distribution.weak + node.distribution.unsteady }}/{{ node.evidence_student_count }}</strong><small>人需关注</small></span>
        </button>
        <p class="workbench-insight-caption workbench-insight-definition">需关注包括明显薄弱与还不稳；人数以该项有证据学生为分母。</p>
      </template>
      <p class="workbench-insight-source">依据本学期考试与已发布训练</p>
    </template>
    <AppButton variant="ghost" class="workbench-footer-link" @click="$emit('open', '/knowledge-overview')">查看学情 →</AppButton>
  </section>
</template>
