<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import AppButton from '@/components/design-system/AppButton.vue'
import StatusBadge from '@/components/design-system/StatusBadge.vue'
import type { WorkNode } from '../api/work'
import type { OrdinaryWorkModule } from './createOrdinaryWorkModule'

const props = defineProps<{ module: OrdinaryWorkModule }>()
const emit = defineEmits<{ openRestricted: [projectionId: string, projectionType: string | null] }>()
const progress = ref('')
const dueDate = ref('')
const busy = ref(false)
const message = ref('')
const expectedCount = ref(0)
const receivedCount = ref(0)
const needsReviewCount = ref(0)
const confirmingCancel = ref(false)
const detail = computed(() => props.module.selected.value)

watch(detail, (value) => {
  dueDate.value = value?.node.due_date ?? ''
  progress.value = ''
  expectedCount.value = value?.collection_summary?.expected_count ?? 0
  receivedCount.value = value?.collection_summary?.received_count ?? 0
  needsReviewCount.value = value?.collection_summary?.needs_review_count ?? 0
  message.value = ''
  confirmingCancel.value = false
})

async function run(node: WorkNode, command: string, fields: Record<string, unknown> = {}) {
  busy.value = true
  message.value = ''
  try {
    const result = await props.module.command(node, command, fields)
    if (command === 'open_restricted_projection') {
      const projectionId = String(result.projection_id ?? detail.value?.projection_id ?? '')
      if (projectionId) emit('openRestricted', projectionId, detail.value?.node.projection_type ?? null)
    } else if (command === 'update_status' && fields.status === 'cancelled') {
      message.value = '已移出默认日历；工作记录仍保留，可以恢复。'
      confirmingCancel.value = false
    } else if (command === 'update_status' && fields.status === 'pending') {
      message.value = '事项已经恢复到日历。'
    } else {
      message.value = '工作状态已更新。'
    }
  } catch {
    message.value = '操作没有完成，事项可能已在其他页面发生变化。请刷新后再试。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <aside class="inspector" aria-label="工作详情">
    <template v-if="detail">
      <AppButton class="inspector__close" variant="ghost" @click="module.clearSelection()">关闭</AppButton>
      <p class="eyebrow">工作轨迹</p>
      <h2>{{ detail.node.title }}</h2>
      <p class="muted">{{ detail.node.details || '这项工作没有补充说明。' }}</p>
      <dl class="facts">
        <div><dt>状态</dt><dd><StatusBadge tone="neutral" :label="detail.node.status" /></dd></div>
        <div><dt>到期</dt><dd>{{ detail.node.due_date || '未设置' }}</dd></div>
        <div><dt>上游 / 下游</dt><dd>{{ detail.upstream.length }} / {{ detail.downstream.length }}</dd></div>
      </dl>

      <AppButton
        v-if="detail.node.classification === 'restricted_projection'"
        variant="primary"
        block
        class="primary-action"
        :disabled="busy"
        @click="run(detail.node, 'open_restricted_projection')"
      >打开相关学生事项</AppButton>

      <template v-else>
        <div v-if="detail.node.status === 'cancelled'" class="restore-panel">
          <p>这项工作已移出默认日历，历史记录仍然保留。</p>
          <AppButton variant="secondary" :disabled="busy" @click="run(detail.node, 'update_status', { status: 'pending' })">恢复到日历</AppButton>
        </div>
        <div v-else class="command-row">
          <AppButton variant="secondary" :disabled="busy" @click="run(detail.node, 'update_status', { status: 'in_progress' })">开始处理</AppButton>
          <AppButton variant="secondary" :disabled="busy" @click="run(detail.node, 'update_status', { status: 'completed' })">标为完成</AppButton>
          <AppButton v-if="!confirmingCancel" variant="secondary" :disabled="busy" @click="confirmingCancel = true">移出日历</AppButton>
          <template v-else><span class="confirm-copy">确定移出？记录会保留。</span><AppButton variant="danger" :disabled="busy" @click="run(detail.node, 'update_status', { status: 'cancelled' })">确认移出</AppButton><AppButton variant="ghost" :disabled="busy" @click="confirmingCancel = false">不移出</AppButton></template>
        </div>
        <label>
          <span>调整到期日期</span>
          <span class="inline"><input v-model="dueDate" type="date"><AppButton variant="secondary" :disabled="busy || !dueDate" @click="run(detail.node, 'reschedule', { due_date: dueDate })">保存</AppButton></span>
        </label>
        <label>
          <span>记录进展</span>
          <textarea v-model="progress" rows="3" maxlength="240" placeholder="只记录普通工作信息"></textarea>
          <AppButton variant="secondary" :disabled="busy || !progress.trim()" @click="run(detail.node, 'record_progress', { progress: progress })">加入轨迹</AppButton>
        </label>
        <section v-if="detail.node.kind === 'collection'" class="collection-summary">
          <h3>收集汇总</h3>
          <label><span>应收</span><input v-model.number="expectedCount" type="number" min="0"></label>
          <label><span>已收</span><input v-model.number="receivedCount" type="number" min="0"></label>
          <label><span>待复查</span><input v-model.number="needsReviewCount" type="number" min="0"></label>
          <AppButton variant="secondary" :disabled="busy || receivedCount > expectedCount || needsReviewCount > receivedCount" @click="run(detail.node, 'update_collection_summary', { expected_count: expectedCount, received_count: receivedCount, needs_review_count: needsReviewCount })">保存汇总</AppButton>
        </section>
      </template>

      <section class="relations" aria-label="工作关系">
        <h3>目标、步骤与依赖</h3>
        <p><strong>上游：</strong>{{ detail.upstream.map((item) => item.title).join('；') || '无' }}</p>
        <p><strong>下游：</strong>{{ detail.downstream.map((item) => item.title).join('；') || '无' }}</p>
      </section>

      <section class="ai-branches" aria-label="待确认 AI 分支">
        <h3>待确认 AI 分支</h3>
        <article v-for="branch in (detail.pending_ai_branches ?? [])" :key="branch.operation_id">
          <strong>AI 草案 · 尚未进入正式工作图</strong>
          <ul><li v-for="node in branch.nodes" :key="`${branch.operation_id}-${node.title}`">{{ node.title }} · {{ node.due_date || '日期待定' }}</li></ul>
        </article>
        <p v-if="!(detail.pending_ai_branches?.length)" class="muted">当前没有待教师确认的 AI 分支。</p>
      </section>

      <section class="trajectory" aria-label="进展记录">
        <h3>连续轨迹</h3>
        <ol>
          <li v-for="(event, index) in detail.progress_events" :key="index">
            {{ event.event_type || event.kind || '更新' }}
            <small>{{ event.created_at || '' }}</small>
          </li>
          <li v-if="!detail.progress_events.length" class="muted">尚无进展记录</li>
        </ol>
      </section>
      <p v-if="message" class="success" role="status">{{ message }}</p>
    </template>
    <div v-else class="inspector__empty">
      <span aria-hidden="true">↗</span>
      <strong>选择一项工作</strong>
      <p>这里会显示上下游、进展和可执行操作。</p>
    </div>
  </aside>
</template>

<style scoped>
.inspector { min-height: 460px; padding: var(--space-5); border-left: 1px solid var(--border); background: var(--muted); }
.inspector__close { float: right; }
.primary-action { margin-top: var(--space-4); }
.eyebrow { margin: 0 0 var(--space-1); color: var(--primary); font-size: var(--font-size-caption); font-weight: 700; letter-spacing: .08em; }
h2 { margin: 0 0 var(--space-2); font-size: var(--font-size-h2); }
.muted { color: var(--color-text-secondary); }
.facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2); padding: var(--space-3) 0; border-block: 1px solid var(--border); }
.facts div { display: grid; gap: 2px; justify-items: start; }.facts dt { color: var(--muted-foreground); font-size: var(--font-size-caption); }.facts dd { margin: 0; font-weight: 650; }
label { display: grid; gap: var(--space-2); margin-top: var(--space-4); font-size: var(--font-size-dense); font-weight: 650; }
.inline,.command-row { display: flex; gap: var(--space-2); }.inline input { flex: 1; }
.command-row{flex-wrap:wrap;align-items:center}.confirm-copy{color:var(--color-text-secondary);font-size:var(--font-size-caption)}.restore-panel{margin-top:var(--space-4);padding:var(--space-3);border-left:3px solid var(--color-warning);border-radius:0 var(--radius) var(--radius) 0;background:var(--color-warning-subtle)}.restore-panel p{margin-top:0;color:var(--color-text-secondary)}
button,input,textarea { font: inherit; }
input,textarea { padding: var(--space-2); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
input { min-height: 36px; }
input:focus-visible,textarea:focus-visible { border-color: var(--ring); box-shadow: var(--focus-ring); outline: 0; }
textarea { resize: vertical; }
  .trajectory { margin-top: var(--space-5); }.trajectory ol { margin: 0; padding-left: 24px; border-left: 2px solid var(--accent); }.trajectory li { padding: 0 0 var(--space-3) var(--space-2); }.trajectory small { display: block; color: var(--muted-foreground); }
  .collection-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2); margin-top: var(--space-4); padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }.collection-summary h3,.collection-summary .app-button { grid-column: 1/-1; }.collection-summary label { margin-top: 0; }.collection-summary input { width: 100%; box-sizing: border-box; }.relations,.ai-branches { margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--border); }.relations p,.ai-branches li { color: var(--color-text-secondary); font-size: var(--font-size-dense); }.ai-branches article { padding: var(--space-3); border-left: 3px dashed var(--color-ai); border-radius: 0 var(--radius) var(--radius) 0; background: var(--color-ai-subtle); }
.inspector__empty { display: grid; place-items: center; align-content: center; min-height: 390px; text-align: center; color: var(--color-text-secondary); }.inspector__empty span { font-size: 36px; color: var(--primary); }.inspector__empty p { max-width: 230px; }
.success { color: var(--color-success); }
@media (max-width: 980px) { .inspector { border-top: 1px solid var(--border); border-left: 0; }.facts { grid-template-columns: 1fr; } }
</style>
