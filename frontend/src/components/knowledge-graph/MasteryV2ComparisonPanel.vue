<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { GraphQueryInput } from '../../api/graph'
import {
  compareMasteryVersions,
  fetchMasteryRollout,
  reviewMasteryDifference,
  updateMasteryRollout,
  type MasteryComparisonItem,
  type MasteryComparisonResponse,
  type MasteryRolloutState,
} from '../../api/graph-v2'

const props = defineProps<{ query: GraphQueryInput | null }>()
const emit = defineEmits<{ rolloutChanged: [] }>()

const rollout = ref<MasteryRolloutState | null>(null)
const comparison = ref<MasteryComparisonResponse | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const action = ref<'idle' | 'comparing' | 'reviewing' | 'updating'>('idle')
const error = ref('')
const teacherRef = ref('')
const reviewReason = ref('')
const checkedItems = ref<Record<string, 'accepted' | 'rejected'>>({})
let stateController: AbortController | null = null
let compareController: AbortController | null = null

const canIdentifyTeacher = computed(() => (
  teacherRef.value.trim().length > 0 && reviewReason.value.trim().length > 0
))
const canEnable = computed(() => (
  rollout.value?.enabled === false &&
  comparison.value?.gate.passed === true &&
  canIdentifyTeacher.value
))

function percent(value: number | null): string {
  if (value === null) return '无可用值'
  return `${Math.round(value * 1000) / 10}%`
}

function deltaLabel(item: MasteryComparisonItem): string {
  if (item.signed_delta === null) return '口径语义变化'
  const prefix = item.signed_delta > 0 ? '+' : ''
  return `${prefix}${Math.round(item.signed_delta * 1000) / 10} 个百分点`
}

async function loadRollout(): Promise<void> {
  stateController?.abort()
  const controller = new AbortController()
  stateController = controller
  state.value = 'loading'
  error.value = ''
  try {
    rollout.value = await fetchMasteryRollout(controller.signal)
    state.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    rollout.value = null
    state.value = 'error'
    error.value = '掌握度开关状态暂时无法读取；系统继续按 v1 运行。'
  } finally {
    if (stateController === controller) stateController = null
  }
}

async function runComparison(): Promise<void> {
  if (!props.query) return
  compareController?.abort()
  const controller = new AbortController()
  compareController = controller
  action.value = 'comparing'
  error.value = ''
  checkedItems.value = {}
  try {
    comparison.value = await compareMasteryVersions(
      props.query,
      new Date().toISOString(),
      controller.signal,
    )
  } catch {
    if (controller.signal.aborted) return
    comparison.value = null
    error.value = 'v1/v2 对照暂时无法生成；正式口径没有改变。'
  } finally {
    if (compareController === controller) compareController = null
    action.value = 'idle'
  }
}

async function reviewItem(
  item: MasteryComparisonItem,
  decision: 'accepted' | 'rejected',
): Promise<void> {
  const current = comparison.value
  if (!current || !canIdentifyTeacher.value || checkedItems.value[item.item_hash]) return
  action.value = 'reviewing'
  error.value = ''
  try {
    const gate = await reviewMasteryDifference({
      evaluation_id: current.evaluation_id,
      item_hash: item.item_hash,
      decision,
      teacher_ref: teacherRef.value.trim(),
      reason: reviewReason.value.trim(),
      expected_revision: current.gate.revision,
    })
    comparison.value = { ...current, gate }
    checkedItems.value = { ...checkedItems.value, [item.item_hash]: decision }
  } catch {
    error.value = '抽检结论未保存，请重新生成对照后再核对。'
  } finally {
    action.value = 'idle'
  }
}

async function setRollout(enabled: boolean): Promise<void> {
  const current = rollout.value
  if (!current || !canIdentifyTeacher.value) return
  action.value = 'updating'
  error.value = ''
  try {
    rollout.value = await updateMasteryRollout({
      enabled,
      expected_revision: current.revision,
      teacher_ref: teacherRef.value.trim(),
      reason: reviewReason.value.trim(),
      ...(enabled && comparison.value
        ? { evaluation_id: comparison.value.evaluation_id }
        : {}),
    })
    emit('rolloutChanged')
  } catch {
    error.value = enabled
      ? 'v2 未启用：请确认所有必查差异都已接受，并重新读取最新状态。'
      : '回退未完成，请重新读取状态后再试。'
  } finally {
    action.value = 'idle'
  }
}

watch(
  () => props.query,
  () => {
    compareController?.abort()
    comparison.value = null
    checkedItems.value = {}
    error.value = ''
  },
  { deep: true },
)

onMounted(() => { void loadRollout() })
onBeforeUnmount(() => {
  stateController?.abort()
  compareController?.abort()
})
</script>

<template>
  <section class="knowledge-graph-mastery-rollout" aria-labelledby="mastery-rollout-title">
    <header>
      <div>
        <p class="knowledge-graph-inspector__eyebrow">掌握度口径</p>
        <h2 id="mastery-rollout-title">v1 / v2 对照与回退</h2>
        <p>差异超过 10 个百分点或出现“有值/无值”变化时，必须逐项抽检。</p>
      </div>
      <strong
        class="knowledge-graph-mastery-rollout__mode"
        :data-mode="rollout?.active_mode ?? 'v1'"
      >
        {{ rollout?.active_mode === 'v2' ? '当前正式口径：v2' : '当前正式口径：v1' }}
      </strong>
    </header>

    <p v-if="state === 'loading'" class="knowledge-graph-state-copy" role="status">
      正在读取掌握度开关…
    </p>
    <div v-else-if="state === 'error'" class="knowledge-graph-inline-error" role="alert">
      <p>{{ error }}</p>
      <button type="button" @click="loadRollout">重新读取</button>
    </div>
    <template v-else>
      <p class="knowledge-graph-context-note">
        参数版本
        {{ rollout?.active_parameter_version?.slice(0, 12) ?? '候选尚未启用' }}；
        关闭开关后图谱和后续推荐立即只使用 v1。
      </p>
      <div class="knowledge-graph-mastery-rollout__actions">
        <button
          type="button"
          data-testid="compare-mastery-versions"
          :disabled="query === null || action !== 'idle'"
          @click="runComparison"
        >
          {{ action === 'comparing' ? '正在生成对照…' : '生成当前范围对照' }}
        </button>
        <button
          v-if="rollout?.enabled"
          type="button"
          class="is-danger"
          :disabled="!canIdentifyTeacher || action !== 'idle'"
          @click="setRollout(false)"
        >
          完整回退到 v1
        </button>
        <button
          v-else
          type="button"
          :disabled="!canEnable || action !== 'idle'"
          @click="setRollout(true)"
        >
          抽检通过后启用 v2
        </button>
      </div>

      <div class="knowledge-graph-mastery-rollout__identity">
        <label>
          <span>复核人标识</span>
          <input v-model="teacherRef" type="text" autocomplete="off" maxlength="200" placeholder="例如：数学组-张老师">
        </label>
        <label>
          <span>本次核对或回退理由</span>
          <input v-model="reviewReason" type="text" autocomplete="off" maxlength="500" placeholder="说明核对依据；保存后不可改写">
        </label>
      </div>

      <p v-if="error" class="knowledge-graph-inline-error" role="alert">{{ error }}</p>

      <template v-if="comparison">
        <dl class="knowledge-graph-mastery-rollout__summary">
          <div><dt>对照项目</dt><dd>{{ comparison.items.length }}</dd></div>
          <div><dt>必须抽检</dt><dd>{{ comparison.required_review_count }}</dd></div>
          <div><dt>待确认</dt><dd>{{ comparison.gate.pending_count }}</dd></div>
          <div><dt>最大差异</dt><dd>{{ percent(comparison.maximum_absolute_delta) }}</dd></div>
          <div><dt>计算耗时</dt><dd>{{ comparison.performance.duration_ms }} ms</dd></div>
          <div><dt>候选参数</dt><dd>{{ comparison.parameter_version.slice(0, 12) }}</dd></div>
        </dl>
        <p class="knowledge-graph-context-note">
          历史对照保留自己的参数版本与解释；创建新参数版本不会改写本次结果。
        </p>
        <ol class="knowledge-graph-mastery-difference-list" aria-label="掌握度差异榜">
          <li
            v-for="item in comparison.items"
            :key="item.item_hash"
            :data-review-required="item.requires_review"
          >
            <header>
              <div>
                <strong>{{ item.student_code || item.student_id }} · {{ item.display_name }}</strong>
                <span>{{ item.class_id || '未标班级' }}</span>
              </div>
              <b>{{ deltaLabel(item) }}</b>
            </header>
            <p>v1 {{ percent(item.mastery_v1) }} → v2 {{ percent(item.mastery_v2.value) }}</p>
            <ul>
              <li v-for="reason in item.reasons" :key="reason">{{ reason }}</li>
            </ul>
            <p v-if="!item.requires_review" class="knowledge-graph-context-note">
              差异未超过门槛，无需逐项确认。
            </p>
            <div v-else class="knowledge-graph-mastery-difference-list__review">
              <template v-if="checkedItems[item.item_hash]">
                <strong>
                  {{ checkedItems[item.item_hash] === 'accepted' ? '已确认可接受' : '已标记不可接受' }}
                </strong>
              </template>
              <template v-else>
                <button
                  type="button"
                  :disabled="!canIdentifyTeacher || action !== 'idle'"
                  @click="reviewItem(item, 'accepted')"
                >
                  确认可接受
                </button>
                <button
                  type="button"
                  class="is-danger"
                  :disabled="!canIdentifyTeacher || action !== 'idle'"
                  @click="reviewItem(item, 'rejected')"
                >
                  标记不可接受
                </button>
              </template>
            </div>
          </li>
        </ol>
      </template>
    </template>
  </section>
</template>
