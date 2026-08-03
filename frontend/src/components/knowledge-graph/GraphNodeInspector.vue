<script setup lang="ts">
import { computed } from 'vue'

import type {
  CurrentGraphStandard,
  GraphEdge,
  GraphEvidenceResponse,
  GraphNode,
  GraphRelationBasis,
  GraphRelationStrength,
} from '../../api/graph'
import {
  graphNodeState,
  graphNodeStateLabel,
  relationTypeLabel,
} from '../../features/knowledge-graph/model'
import type { ResourceState } from '../../stores/workbench'

const props = defineProps<{
  node: GraphNode | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  currentStandard: CurrentGraphStandard
  evidence: GraphEvidenceResponse | null
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
        description = '两个知识点经治理后确认为相关'
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
  ? graphNodeStateLabel(graphNodeState(props.node))
  : '')
function masteryLabel(mastery: GraphNode['mastery'] | undefined): string {
  if (!mastery || mastery.status !== 'available' || mastery.value === null) return '当前无可用证据'
  const percent = Math.round(mastery.value * 1000) / 10
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}
const activeMasteryLabel = computed(() => masteryLabel(
  props.node?.mastery,
))
const missingReasons = computed(() => {
  return [...new Set(props.node?.missing_reasons ?? [])]
})

function missingReasonLabel(reason: string): string {
  if (reason === 'no_evidence_in_scope') return '当前考试和学生范围内没有题目证据'
  return reason
}

function scoreRateLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '得分率暂不可用'
  return `得分率 ${Math.round(value * 1000) / 10}%`
}

function basisLabel(value: GraphRelationBasis): string {
  return {
    mathematical_logic: '数学逻辑',
    curriculum_structure: '课程结构',
    multi_textbook_sequence: '多教材顺序',
    teacher_judgment: '教师判断',
    empirical_evidence: '经验数据',
  }[value]
}

function strengthLabel(value: GraphRelationStrength): string {
  return { required: '必要关系', recommended: '推荐关系', contextual: '情境关系' }[value]
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
        <p class="knowledge-graph-stable-key">稳定知识标识 {{ node.stable_key }}</p>
        <p>{{ node.definition || '当前知识点尚未补充定义。' }}</p>
        <dl>
          <div><dt>当前掌握证据</dt><dd>{{ activeMasteryLabel }} · {{ stateLabel }}</dd></div>
          <div><dt>样本数量</dt><dd>{{ node.mastery.evidence_count }} 条</dd></div>
          <div><dt>涉及学生</dt><dd>{{ node.evidence.student_count }} 名</dd></div>
          <div><dt>扣分记录</dt><dd>{{ node.evidence.deduction_count }} 次</dd></div>
          <div><dt>包含范围</dt><dd>{{ node.include_scope || '暂未说明' }}</dd></div>
          <div><dt>不包含范围</dt><dd>{{ node.exclude_scope || '暂未说明' }}</dd></div>
          <div><dt>课程依据</dt><dd>{{ node.curriculum_anchors.join('；') || '暂未说明' }}</dd></div>
          <div><dt>可观察证据</dt><dd>{{ node.observable_evidence || '暂未说明' }}</dd></div>
          <div><dt>当前标准</dt><dd>已按当前课程与知识分类标准核验</dd></div>
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
          <li v-for="relation in relations" :key="relation.relation_key" :data-relation="relation.relation_type">
            <div>
              <strong>{{ relationTypeLabel(relation.relation_type) }}</strong>
              <span>{{ relation.description }}</span>
            </div>
            <button type="button" @click="emit('selectNode', relation.otherKey)">
              {{ relation.otherName }}
            </button>
            <p>{{ relation.rationale || '未填写关系理由' }}</p>
            <p>依据：{{ basisLabel(relation.basis_kind) }} · {{ strengthLabel(relation.strength) }}</p>
            <p>来源：{{ relation.source_locator || relation.evidence_source_ids.join('、') || '当前标准未提供定位' }}</p>
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
          <button type="button" data-testid="retry-graph-evidence" @click="emit('retryEvidence')">重新加载证据</button>
        </div>
        <div v-else-if="evidenceState === 'stale-error'" class="knowledge-graph-stale" role="alert">
          <p>上次读取的证据仍可查看，最新内容暂时无法确认。</p>
          <button type="button" data-testid="retry-graph-evidence" @click="emit('retryEvidence')">重新加载证据</button>
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
