<script setup lang="ts">
import { computed } from 'vue'

import type {
  GraphV2Edge,
  GraphV2EvidenceResponse,
  GraphV2Node,
} from '../../api/graph-v2'
import {
  graphV2NodeState,
  graphV2NodeStateLabel,
  relationTypeLabel,
} from '../../features/knowledge-graph/v2-model'
import type { ResourceState } from '../../stores/workbench'

const props = defineProps<{
  node: GraphV2Node | null
  nodes: GraphV2Node[]
  edges: GraphV2Edge[]
  evidence: GraphV2EvidenceResponse | null
  evidenceState: ResourceState
  evidenceError: string
}>()

const emit = defineEmits<{
  selectNode: [stableKey: string]
  loadMoreEvidence: []
  retryEvidence: []
}>()

const names = computed(() => new Map(
  props.nodes.map((node) => [node.stable_key, node.display_name]),
))
const relations = computed(() => {
  if (!props.node) return []
  return props.edges
    .filter((edge) => (
      edge.source_key === props.node?.stable_key ||
      edge.target_key === props.node?.stable_key
    ))
    .map((edge) => {
      const outgoing = edge.source_key === props.node?.stable_key
      const otherKey = outgoing ? edge.target_key : edge.source_key
      let description: string
      if (edge.relation_type === 'parent') {
        description = outgoing ? '本知识点属于该上位知识点' : '该子知识点属于本知识点'
      } else if (edge.relation_type === 'prerequisite') {
        description = outgoing ? '学习本知识点前通常需要先掌握它' : '本知识点是该目标知识点的先修'
      } else {
        description = '两个知识点经教师确认相关'
      }
      return {
        ...edge,
        otherKey,
        otherName: names.value.get(otherKey) ?? otherKey,
        description,
      }
    })
    .sort((left, right) => (
      left.relation_type.localeCompare(right.relation_type) ||
      left.otherName.localeCompare(right.otherName, 'zh-CN')
    ))
})
const contextEntries = computed(() => {
  if (!props.node) return []
  return Object.entries(props.node.evidence.tag_context).flatMap(([group, values]) => (
    values.map((value) => ({ group, value }))
  ))
})
const errorEntries = computed(() => {
  if (!props.node) return []
  return Object.entries(props.node.evidence.error_counts).flatMap(([group, values]) => (
    Object.entries(values).map(([label, count]) => ({ group, label, count }))
  ))
})
const stateLabel = computed(() => props.node
  ? graphV2NodeStateLabel(graphV2NodeState(props.node))
  : '')
const masteryLabel = computed(() => {
  const mastery = props.node?.mastery_v1
  if (!mastery || mastery.status !== 'available' || mastery.value === null) return '当前无可用证据'
  const percent = Math.round(mastery.value * 1000) / 10
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
})
const missingReasons = computed(() => {
  const result = [...(props.node?.missing_reasons ?? [])]
  if (props.node?.mastery_v2.status === 'unavailable') result.push('mastery_v2_not_enabled')
  return [...new Set(result)]
})

function missingReasonLabel(reason: string): string {
  if (reason === 'no_evidence_in_scope') return '当前考试和学生范围内没有题目证据'
  if (reason === 'mastery_v2_not_enabled') return '掌握度 v2 尚未启用，当前仍显示 v1 证据结果'
  return reason
}

function scoreRateLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '得分率暂不可用'
  return `得分率 ${Math.round(value * 1000) / 10}%`
}
</script>

<template>
  <aside class="knowledge-graph-inspector" aria-labelledby="knowledge-graph-inspector-title">
    <header>
      <p class="knowledge-graph-inspector__eyebrow">关系、事实与证据</p>
      <h2 id="knowledge-graph-inspector-title">知识点详情</h2>
    </header>

    <p v-if="node === null" class="knowledge-graph-empty-copy">
      请选择一个知识点查看已确认关系、证据来源和缺失说明
    </p>
    <template v-else>
      <section class="knowledge-graph-inspector__facts" aria-labelledby="knowledge-node-facts-title">
        <h3 id="knowledge-node-facts-title">{{ node.display_name }}</h3>
        <p class="knowledge-graph-stable-key">{{ node.stable_key }} · 身份版本 {{ node.identity_revision }}</p>
        <dl>
          <div><dt>当前结果</dt><dd>{{ masteryLabel }} · {{ stateLabel }}</dd></div>
          <div><dt>样本数量</dt><dd>{{ node.mastery_v1.evidence_count }} 条</dd></div>
          <div><dt>涉及学生</dt><dd>{{ node.evidence.student_count }} 名</dd></div>
          <div><dt>扣分记录</dt><dd>{{ node.evidence.deduction_count }} 次</dd></div>
          <div><dt>计算口径</dt><dd>掌握度 v1（只读）</dd></div>
        </dl>
      </section>

      <section v-if="missingReasons.length" class="knowledge-graph-missing" aria-labelledby="knowledge-node-missing-title">
        <h3 id="knowledge-node-missing-title">当前限制</h3>
        <ul>
          <li v-for="reason in missingReasons" :key="reason">{{ missingReasonLabel(reason) }}</li>
          <li>证据时间暂未由当前图谱接口提供，可通过考试名称确认来源。</li>
        </ul>
      </section>

      <section aria-labelledby="knowledge-node-relations-title">
        <h3 id="knowledge-node-relations-title">已确认关系</h3>
        <p v-if="relations.length === 0" class="knowledge-graph-empty-copy">
          当前没有已确认关系；它仍可作为独立知识点查看。
        </p>
        <ul v-else class="knowledge-graph-relation-list">
          <li v-for="relation in relations" :key="relation.relation_id" :data-relation="relation.relation_type">
            <div>
              <strong>{{ relationTypeLabel(relation.relation_type) }}</strong>
              <span>{{ relation.description }}</span>
            </div>
            <button type="button" @click="emit('selectNode', relation.otherKey)">
              {{ relation.otherName }}
            </button>
            <p>{{ relation.rationale || '未填写关系理由' }}</p>
          </li>
        </ul>
      </section>

      <section aria-labelledby="knowledge-node-context-title">
        <h3 id="knowledge-node-context-title">支持标签</h3>
        <p class="knowledge-graph-context-note">这些是题目附带标签，不表示知识点关系。</p>
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
        <p class="knowledge-graph-context-note">来源显示考试与题号；当前接口未提供单条证据时间。</p>

        <p v-if="evidenceState === 'loading' && evidence === null" class="knowledge-graph-state-copy" role="status">
          正在读取题目证据…
        </p>
        <div v-if="evidenceState === 'error'" class="knowledge-graph-inline-error" role="alert">
          <p>{{ evidenceError || '题目证据暂时无法读取' }}</p>
          <button type="button" data-testid="retry-graph-v2-evidence" @click="emit('retryEvidence')">重新加载证据</button>
        </div>
        <div v-else-if="evidenceState === 'stale-error'" class="knowledge-graph-stale" role="alert">
          <p>上次读取的证据仍可查看，最新内容暂时无法确认。</p>
          <button type="button" data-testid="retry-graph-v2-evidence" @click="emit('retryEvidence')">重新加载证据</button>
        </div>
        <p v-if="evidenceState === 'empty'" class="knowledge-graph-empty-copy">
          当前范围没有题目证据。可调整考试或学生范围后重试。
        </p>

        <ul v-if="evidence?.items.length" class="knowledge-graph-evidence-list">
          <li v-for="item in evidence.items" :key="`${item.session_id}:${item.student_id}:${item.question_id}:${item.bank_question_id}`">
            <header><strong>{{ item.student_code }} {{ item.student_name }}</strong><span>{{ item.session_name }}</span></header>
            <p>来源：{{ item.session_name }} · {{ item.question_id }}</p>
            <p>{{ item.score_awarded }} / {{ item.full_score }} 分 · {{ scoreRateLabel(item.score_rate) }}</p>
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
