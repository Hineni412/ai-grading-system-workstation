<script setup lang="ts">
import { computed } from 'vue'

import type { GraphEvidenceResponse, GraphNode } from '../../api/graph'
import { masteryBand, masteryBandLabel } from '../../features/knowledge-graph/model'
import type { ResourceState } from '../../stores/workbench'

const props = defineProps<{
  node: GraphNode | null
  evidence: GraphEvidenceResponse | null
  evidenceState: ResourceState
  evidenceError: string
}>()

const emit = defineEmits<{
  loadMoreEvidence: []
  retryEvidence: []
}>()

const bandLabel = computed(() => props.node
  ? masteryBandLabel(masteryBand(props.node.average_mastery))
  : '')

const masteryLabel = computed(() => {
  if (!props.node) return ''
  const value = Math.round(props.node.average_mastery * 1000) / 10
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`
})

const contextEntries = computed(() => {
  if (!props.node) return []
  return Object.entries(props.node.tag_context).flatMap(([group, values]) => (
    values.map((value) => ({ group, value }))
  ))
})

const errorEntries = computed(() => {
  if (!props.node) return []
  return Object.entries(props.node.error_counts).flatMap(([group, values]) => (
    Object.entries(values).map(([label, count]) => ({ group, label, count }))
  ))
})

function scoreRateLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '得分率暂不可用'
  return `得分率 ${Math.round(value * 1000) / 10}%`
}
</script>

<template>
  <aside class="knowledge-graph-inspector" aria-labelledby="knowledge-graph-inspector-title">
    <header>
      <p class="knowledge-graph-inspector__eyebrow">只读事实与证据</p>
      <h2 id="knowledge-graph-inspector-title">知识点详情</h2>
    </header>

    <p v-if="node === null" class="knowledge-graph-empty-copy">请选择一个知识标签查看事实和题目证据</p>
    <template v-else>
      <section class="knowledge-graph-inspector__facts" aria-labelledby="knowledge-node-facts-title">
        <h3 id="knowledge-node-facts-title">{{ node.knowledge_label }}</h3>
        <dl>
          <div><dt>得分率</dt><dd>{{ masteryLabel }}（按当前标签证据加权）</dd></div>
          <div><dt>文字等级</dt><dd>{{ bandLabel }}</dd></div>
          <div><dt>证据数量</dt><dd>{{ node.item_count }} 条证据</dd></div>
          <div><dt>涉及学生</dt><dd>{{ node.student_count }} 名</dd></div>
          <div><dt>扣分记录</dt><dd>{{ node.deduction_count }} 次</dd></div>
        </dl>
      </section>

      <section aria-labelledby="knowledge-node-context-title">
        <h3 id="knowledge-node-context-title">支持标签</h3>
        <p class="knowledge-graph-context-note">这些是题目附带标签，不表示知识点之间的关系。</p>
        <p v-if="contextEntries.length === 0" class="knowledge-graph-empty-copy">暂无支持标签</p>
        <ul v-else class="knowledge-graph-compact-list">
          <li v-for="item in contextEntries" :key="`${item.group}:${item.value}`">
            <span>{{ item.group }}</span><strong>{{ item.value }}</strong>
          </li>
        </ul>
      </section>

      <section aria-labelledby="knowledge-node-errors-title">
        <h3 id="knowledge-node-errors-title">扣分原因记录</h3>
        <p v-if="errorEntries.length === 0" class="knowledge-graph-empty-copy">未记录扣分原因</p>
        <ul v-else class="knowledge-graph-compact-list">
          <li v-for="item in errorEntries" :key="`${item.group}:${item.label}`">
            <span>{{ item.group }} · {{ item.label }}</span><strong>{{ item.count }} 次</strong>
          </li>
        </ul>
      </section>

      <section class="knowledge-graph-evidence" aria-labelledby="knowledge-node-evidence-title">
        <header>
          <h3 id="knowledge-node-evidence-title">题目证据</h3>
          <span v-if="evidence">{{ evidence.items.length }} / {{ evidence.total }} 条</span>
        </header>

        <p v-if="evidenceState === 'loading' && evidence === null" class="knowledge-graph-state-copy" role="status">
          正在读取题目证据…
        </p>
        <div v-if="evidenceState === 'error'" class="knowledge-graph-inline-error" role="alert">
          <p>{{ evidenceError || '题目证据暂时无法读取' }}</p>
          <button type="button" data-testid="retry-graph-evidence" @click="emit('retryEvidence')">重新加载证据</button>
        </div>
        <div v-else-if="evidenceState === 'stale-error'" class="knowledge-graph-stale" role="alert">
          <p>上次读取的证据仍可查看，最新内容暂时无法确认。</p>
          <button type="button" data-testid="retry-graph-evidence" @click="emit('retryEvidence')">重新加载证据</button>
        </div>
        <p v-if="evidenceState === 'empty'" class="knowledge-graph-empty-copy">当前范围没有可显示的题目证据</p>

        <ul v-if="evidence?.items.length" class="knowledge-graph-evidence-list">
          <li v-for="item in evidence.items" :key="`${item.session_id}:${item.student_id}:${item.question_id}:${item.bank_question_id}`">
            <header><strong>{{ item.student_code }} {{ item.student_name }}</strong><span>{{ item.session_name }}</span></header>
            <p>{{ item.question_id }} · {{ item.score_awarded }} / {{ item.full_score }} 分 · {{ scoreRateLabel(item.score_rate) }}</p>
            <p>{{ item.actionable_reasons.length ? item.actionable_reasons.join('；') : '未记录扣分原因' }}</p>
          </li>
        </ul>

        <button
          v-if="evidence && evidence.page < evidence.total_pages"
          type="button"
          class="knowledge-graph-load-more"
          :disabled="evidenceState === 'loading'"
          @click="emit('loadMoreEvidence')"
        >
          {{ evidenceState === 'loading' ? '正在加载更多证据…' : '加载更多证据' }}
        </button>
      </section>
    </template>
  </aside>
</template>
