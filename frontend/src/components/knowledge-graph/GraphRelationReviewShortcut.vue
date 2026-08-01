<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  fetchRelationReviewQueue,
  reviewRelationExceptions,
  type RelationReviewQueueResponse,
} from '../../api/graph-v2'
import { relationTypeLabel } from '../../features/knowledge-graph/v2-model'

const queue = ref<RelationReviewQueueResponse | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const expanded = ref(false)
const selectedIds = ref<Set<string>>(new Set())
const busy = ref(false)
const notice = ref('')
let controller: AbortController | null = null

const selectedItems = computed(() => (
  queue.value?.items.filter((item) => selectedIds.value.has(item.relation_id)) ?? []
))

function toggle(itemId: string): void {
  const next = new Set(selectedIds.value)
  if (next.has(itemId)) next.delete(itemId)
  else next.add(itemId)
  selectedIds.value = next
}

function selectAll(): void {
  selectedIds.value = new Set(queue.value?.items.map((item) => item.relation_id) ?? [])
}

async function reviewSelected(action: 'confirm' | 'reject'): Promise<void> {
  const items = action === 'confirm'
    ? selectedItems.value.filter((item) => item.conflict_codes.length === 0)
    : selectedItems.value
  if (!items.length) return
  busy.value = true
  notice.value = ''
  try {
    const result = await reviewRelationExceptions({ items, action })
    notice.value = action === 'confirm'
      ? `已采用 ${result.applied_count} 条建议${result.failed_count ? `，${result.failed_count} 条因状态变化未处理` : ''}。`
      : `已排除 ${result.applied_count} 条建议${result.failed_count ? `，${result.failed_count} 条因状态变化未处理` : ''}。`
    selectedIds.value = new Set()
    await loadQueue()
  } catch {
    notice.value = '本次异常处理没有完成，现有知识图谱没有改变。'
  } finally {
    busy.value = false
  }
}

async function loadQueue(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  state.value = 'loading'
  try {
    queue.value = await fetchRelationReviewQueue(controller.signal)
    selectedIds.value = new Set()
    state.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    state.value = 'error'
  }
}

onMounted(() => { void loadQueue() })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section class="knowledge-graph-review-shortcut" aria-labelledby="relation-review-shortcut-title">
    <div>
      <p class="knowledge-graph-inspector__eyebrow">AI 自动治理 · 教师只看异常</p>
      <h2 id="relation-review-shortcut-title">知识关系异常</h2>
      <p v-if="state === 'loading'" role="status">正在读取待审核关系…</p>
      <p v-else-if="state === 'error'">待审核队列暂时无法读取，已确认关系图不受影响。</p>
      <p v-else-if="queue?.total === 0">当前没有需要教师处理的关系异常。</p>
      <p v-else>AI 与规则已处理可确定关系；还有 {{ queue?.total }} 条不确定或冲突关系需要判断，当前列出前 {{ queue?.items.length }} 条。</p>
    </div>
    <div class="knowledge-graph-review-shortcut__actions">
      <button v-if="state === 'error'" type="button" @click="loadQueue">重新读取队列</button>
      <button
        v-if="queue?.items.length"
        type="button"
        :aria-expanded="expanded"
        aria-controls="relation-review-quick-list"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起关系异常' : '处理关系异常' }}
      </button>
    </div>
    <div v-if="expanded && queue?.items.length" id="relation-review-quick-list" class="knowledge-graph-review-list">
      <p role="note">只显示未达到自动采用条件的少量项目。存在环路或方向冲突的项目不能直接采用。</p>
      <div class="knowledge-graph-review-list__toolbar">
        <button type="button" @click="selectAll">选择当前页</button>
        <span>已选 {{ selectedItems.length }} 条</span>
        <button
          type="button"
          :disabled="busy || !selectedItems.some((item) => item.conflict_codes.length === 0)"
          @click="reviewSelected('confirm')"
        >采用无冲突项</button>
        <button type="button" :disabled="busy || !selectedItems.length" @click="reviewSelected('reject')">排除所选</button>
      </div>
      <p v-if="notice" role="status">{{ notice }}</p>
      <ul>
        <li v-for="item in queue.items" :key="item.relation_id">
          <header>
            <label>
              <input
                type="checkbox"
                :checked="selectedIds.has(item.relation_id)"
                @change="toggle(item.relation_id)"
              >
              <strong>{{ item.source_name }} → {{ item.target_name }}</strong>
            </label>
            <span>{{ relationTypeLabel(item.relation_type) }}</span>
          </header>
          <p>{{ item.rationale || '未填写候选理由' }}</p>
          <p>
            来源：{{ item.source_kind }}
            <template v-if="item.confidence !== null"> · 置信度 {{ Math.round(item.confidence * 100) }}%</template>
            · {{ item.conflict_codes.length ? '规则发现冲突' : '证据不足，等待判断' }}
          </p>
          <p v-if="item.conflict_codes.length" class="knowledge-graph-review-list__conflict">
            需先处理冲突：{{ item.conflict_codes.join('、') }}
          </p>
        </li>
      </ul>
    </div>
  </section>
</template>
