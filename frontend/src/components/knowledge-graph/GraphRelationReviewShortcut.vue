<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import {
  fetchRelationReviewQueue,
  type RelationReviewQueueResponse,
} from '../../api/graph-v2'
import { relationTypeLabel } from '../../features/knowledge-graph/v2-model'

const queue = ref<RelationReviewQueueResponse | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const expanded = ref(false)
let controller: AbortController | null = null

async function loadQueue(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  state.value = 'loading'
  try {
    queue.value = await fetchRelationReviewQueue(controller.signal)
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
      <p class="knowledge-graph-inspector__eyebrow">教师确认后才生效</p>
      <h2 id="relation-review-shortcut-title">关系审核入口</h2>
      <p v-if="state === 'loading'" role="status">正在读取待审核关系…</p>
      <p v-else-if="state === 'error'">待审核队列暂时无法读取，已确认关系图不受影响。</p>
      <p v-else-if="queue?.total === 0">当前没有待审核关系。候选关系不会进入活动图谱。</p>
      <p v-else>有 {{ queue?.total }} 条候选关系等待教师审核；当前列出前 {{ queue?.items.length }} 条。</p>
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
        {{ expanded ? '收起候选关系' : '查看待审核关系' }}
      </button>
    </div>
    <div v-if="expanded && queue?.items.length" id="relation-review-quick-list" class="knowledge-graph-review-list">
      <p role="note">
        此处用于快速核对候选方向、理由和冲突；确认、拒绝或修订必须进入带教师身份与影响预览的正式审核流程。
      </p>
      <ul>
        <li v-for="item in queue.items" :key="item.relation_id">
          <header>
            <strong>{{ item.source_name }} → {{ item.target_name }}</strong>
            <span>{{ relationTypeLabel(item.relation_type) }}</span>
          </header>
          <p>{{ item.rationale || '未填写候选理由' }}</p>
          <p>
            来源：{{ item.source_kind }}
            <template v-if="item.confidence !== null"> · 置信度 {{ Math.round(item.confidence * 100) }}%</template>
            · 版本 {{ item.revision }}
          </p>
          <p v-if="item.conflict_codes.length" class="knowledge-graph-review-list__conflict">
            需先处理冲突：{{ item.conflict_codes.join('、') }}
          </p>
        </li>
      </ul>
    </div>
  </section>
</template>
