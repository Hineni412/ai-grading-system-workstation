<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  questionBankApi,
  type QuestionSolutionEvidenceResponse,
  type SolutionEvidenceFineTermLink,
} from '../../api/question-bank'
import StatusBadge, { type StatusTone } from '../design-system/StatusBadge.vue'

const props = withDefaults(defineProps<{
  questionId: number
  embedded?: boolean
  loader?: (
    questionId: number,
    signal?: AbortSignal,
  ) => Promise<QuestionSolutionEvidenceResponse>
}>(), {
  embedded: false,
  loader: (questionId: number, signal?: AbortSignal) => (
    questionBankApi.getSolutionEvidence(questionId, signal)
  ),
})

const state = ref<'loading' | 'ready' | 'error'>('loading')
const response = ref<QuestionSolutionEvidenceResponse | null>(null)
const error = ref('')
let controller: AbortController | null = null

const evidence = computed(() => response.value?.evidence ?? null)
const classification = computed(() => evidence.value?.whole_question_classification ?? null)

function resolutionCopy(link: SolutionEvidenceFineTermLink): string {
  const resolution = link.core_resolution
  if (resolution.status === 'resolved') return '已映射到知识图谱'
  if (resolution.status === 'ambiguous') return '存在多个图谱候选，待治理确认'
  return '尚未建立图谱映射'
}

function statusCopy(status: QuestionSolutionEvidenceResponse['status']): string {
  if (status === 'approved') return '教师已确认'
  if (status === 'rejected') return '已驳回'
  if (status === 'superseded') return '已有新版'
  if (status === 'stale') return '题目已变化，需重算'
  return 'AI 草稿，待教师核对'
}

function statusTone(status: QuestionSolutionEvidenceResponse['status']): StatusTone {
  if (status === 'approved') return 'teacher'
  if (status === 'rejected') return 'danger'
  if (status === 'superseded') return 'neutral'
  if (status === 'stale') return 'warning'
  return 'ai'
}

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  state.value = 'loading'
  response.value = null
  error.value = ''
  try {
    response.value = await props.loader(props.questionId, controller.signal)
    state.value = 'ready'
  } catch (reason) {
    if (controller.signal.aborted) return
    error.value = props.embedded
      ? '知识细项暂时无法展示，不影响判定点核对。'
      : reason instanceof Error ? reason.message : '知识细项暂时无法读取'
    state.value = 'error'
  }
}

watch(() => props.questionId, () => void load(), { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section
    v-if="!embedded || evidence || state === 'error'"
    class="solution-evidence"
    :class="{ 'is-embedded': embedded }"
    :aria-labelledby="embedded ? undefined : 'solution-evidence-title'"
    :aria-label="embedded ? '知识细项与图谱映射' : undefined"
  >
    <header v-if="!embedded" class="solution-evidence__heading">
      <div>
        <h3 id="solution-evidence-title">知识细项与图谱映射</h3>
      </div>
      <StatusBadge
        v-if="response?.available"
        class="solution-evidence__status"
        :tone="statusTone(response.status)"
        :label="statusCopy(response.status)"
      />
    </header>

    <p v-if="state === 'loading' && !embedded" class="solution-evidence__empty" role="status">
      正在读取知识细项与图谱映射…
    </p>
    <div v-else-if="state === 'error'" class="solution-evidence__empty" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="qb-link" @click="load">重新读取</button>
    </div>
    <p v-else-if="!evidence && !embedded" class="solution-evidence__empty">
      这道题还没有知识细项映射。完成题目分析后，这里会按“小问 → 判定点 → 精细词条 → 核心图谱”展示。
    </p>
    <component
      v-else-if="evidence"
      :is="embedded ? 'details' : 'div'"
      class="solution-evidence__body"
    >
      <summary v-if="embedded" id="solution-evidence-embed-title">
        知识细项与图谱映射
        <StatusBadge
          v-if="response?.available"
          class="solution-evidence__status"
          :tone="statusTone(response.status)"
          :label="statusCopy(response.status)"
        />
      </summary>
      <p class="solution-evidence__help">
        “直接考查”用于掌握度统计；“支撑前置”只说明解题依赖，默认不计入本题掌握度。
      </p>

      <div class="solution-evidence__parts">
        <article v-for="(part, partIndex) in evidence.parts" :key="part.part_id" class="solution-evidence__part">
          <header>
            <span>小问 {{ partIndex + 1 }}</span>
            <strong>{{ part.label || part.part_id }}</strong>
            <small>{{ part.response_mode }}</small>
          </header>
          <div class="solution-evidence__points">
            <section v-for="(point, pointIndex) in part.evidence_points" :key="point.evidence_point_id" class="solution-evidence__point">
              <div class="solution-evidence__point-title">
                <span>{{ pointIndex + 1 }}</span>
                <div>
                  <strong>{{ point.target }}</strong>
                  <p>{{ point.observable_evidence }}</p>
                </div>
              </div>
              <div class="solution-evidence__terms">
                <span
                  v-for="link in point.fine_term_links"
                  :key="`${link.fine_term_id}:${link.role}`"
                  class="solution-evidence__term"
                  :data-role="link.role"
                  :title="resolutionCopy(link)"
                >
                  <b>{{ link.role === 'direct' ? '直接' : '前置' }}</b>
                  {{ link.fine_term_name }}
                  <small>{{ resolutionCopy(link) }}</small>
                </span>
              </div>
            </section>
          </div>
        </article>
      </div>

      <details v-if="classification" class="solution-evidence__summary">
        <summary>查看整题并集（只读汇总）</summary>
        <dl>
          <div>
            <dt>直接考查词条</dt>
            <dd>{{ classification.direct_fine_terms.map((item) => item.fine_term_name).join('、') || '无' }}</dd>
          </div>
          <div>
            <dt>支撑前置词条</dt>
            <dd>{{ classification.supporting_prerequisite_fine_terms.map((item) => item.fine_term_name).join('、') || '无' }}</dd>
          </div>
          <div>
            <dt>已映射核心知识</dt>
            <dd>{{ classification.resolved_core_node_ids.length ? `${classification.resolved_core_node_ids.length} 个` : '尚无' }}</dd>
          </div>
          <div v-if="classification.ambiguous_core_node_ids.length || classification.unmapped_fine_term_ids.length">
            <dt>待治理</dt>
            <dd>
              候选节点 {{ classification.ambiguous_core_node_ids.length }} 个；
              未映射词条 {{ classification.unmapped_fine_term_ids.length }} 个
            </dd>
          </div>
        </dl>
      </details>
    </component>
  </section>
</template>

<style scoped>
.solution-evidence { display: grid; gap: var(--space-3); padding: var(--space-4); border-block: var(--border-width) solid var(--border); }
.solution-evidence.is-embedded {
  padding: var(--space-3) 0 0;
  border-block: 0;
  border-block-start: var(--border-width) solid var(--color-border-subtle);
}
.solution-evidence.is-embedded summary {
  align-items: center;
  cursor: pointer;
  display: flex;
  flex-wrap: wrap;
  font-weight: var(--font-weight-medium);
  gap: var(--space-2);
}
.solution-evidence__body { display: grid; gap: var(--space-3); }
.solution-evidence__heading { display: flex; align-items: start; justify-content: space-between; gap: var(--space-3); }
.solution-evidence__heading h3,
.solution-evidence__heading p,
.solution-evidence__point p { margin: 0; }
.solution-evidence__status { flex-shrink: 0; }
.solution-evidence__empty,
.solution-evidence__help { margin: 0; color: var(--color-text-secondary); }
.solution-evidence__empty { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); padding: var(--space-3); border-radius: var(--radius-control); background: var(--secondary); }
.solution-evidence__parts { display: grid; gap: var(--space-3); }
.solution-evidence__part { border: var(--border-width) solid var(--border); border-radius: var(--radius-control); background: var(--card); overflow: hidden; }
.solution-evidence__part > header { display: grid; grid-template-columns: auto 1fr auto; align-items: baseline; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--secondary); }
.solution-evidence__part > header span,
.solution-evidence__part > header small { color: var(--color-text-secondary); font-size: var(--font-size-dense); }
.solution-evidence__points { display: grid; }
.solution-evidence__point { display: grid; grid-template-columns: minmax(12rem, 0.75fr) minmax(0, 1.25fr); gap: var(--space-3); padding: var(--space-3); border-block-start: var(--border-width) solid var(--color-border-subtle); }
.solution-evidence__point:first-child { border-block-start: 0; }
.solution-evidence__point-title { display: flex; align-items: start; gap: var(--space-2); }
.solution-evidence__point-title > span { display: grid; width: 1.7rem; aspect-ratio: 1; place-items: center; border-radius: 50%; background: var(--color-accent); color: var(--primary-foreground); font-size: var(--font-size-dense); }
.solution-evidence__point-title p { margin-block-start: var(--space-1); color: var(--color-text-secondary); line-height: var(--line-height-relaxed); }
.solution-evidence__terms { display: flex; flex-wrap: wrap; align-content: start; gap: var(--space-2); }
.solution-evidence__term { display: grid; gap: 0.15rem; min-width: 9rem; padding: var(--space-2); border: var(--border-width) solid var(--color-accent); border-radius: var(--radius-control); background: var(--color-accent-subtle); }
.solution-evidence__term[data-role='supporting_prerequisite'] { border-color: var(--border); background: var(--secondary); }
.solution-evidence__term b,
.solution-evidence__term small { font-size: var(--font-size-dense); }
.solution-evidence__term small { color: var(--color-text-secondary); }
.solution-evidence__summary { padding: var(--space-3); border: var(--border-width) solid var(--border); border-radius: var(--radius-control); background: var(--secondary); }
.solution-evidence__summary summary { cursor: pointer; font-weight: var(--font-weight-medium); }
.solution-evidence__summary dl { display: grid; gap: var(--space-2); margin: var(--space-3) 0 0; }
.solution-evidence__summary dl > div { display: grid; grid-template-columns: 9rem 1fr; gap: var(--space-2); }
.solution-evidence__summary dt { color: var(--color-text-secondary); }
.solution-evidence__summary dd { margin: 0; }
@media (max-width: 720px) {
  .solution-evidence__point { grid-template-columns: 1fr; }
  .solution-evidence__part > header { grid-template-columns: auto 1fr; }
  .solution-evidence__part > header small { grid-column: 2; }
  .solution-evidence__summary dl > div { grid-template-columns: 1fr; }
}
</style>
