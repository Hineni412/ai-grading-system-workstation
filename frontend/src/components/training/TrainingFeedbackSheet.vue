<script setup lang="ts">
import { computed } from 'vue'
import type { TrainingFeedback } from '../../api/training'
import { knowledgeLeafLabel } from '../../api/question-bank'
import { masteryDetail } from '../knowledge-overview/model'
import AppButton from '../design-system/AppButton.vue'
const props = defineProps<{ feedback: TrainingFeedback; busy: boolean }>()
const emit = defineEmits<{ replay: []; withdraw: []; openDraft: [id: string] }>()
const skillChanges = computed(() => {
  const changes = props.feedback.mastery_changes
  const skills = changes.filter(item => /^(sk_|ki_)/.test(String(item.stable_key)))
  return skills.length ? skills : changes
})
const aggregateChanges = computed(() => props.feedback.mastery_changes.filter(item => !skillChanges.value.includes(item)))
function masteryTitle(item: Record<string, unknown>): string { return knowledgeLeafLabel(String(item.display_name || item.stable_key || '训练目标')) }
function masteryValue(item: Record<string, unknown>, key: 'mastery_before' | 'mastery_after'): string {
  const snapshot = item[key] ?? item[key === 'mastery_before' ? 'v2_before' : 'v2_after']
  if (!snapshot || typeof snapshot !== 'object' || !('value' in snapshot) || typeof snapshot.value !== 'number') return '暂无'
  if ('tier' in snapshot) return masteryDetail(snapshot as Parameters<typeof masteryDetail>[0])
  return `${Math.round(snapshot.value * 100)}%`
}
</script>
<template>
  <section class="return-feedback" aria-label="学生训练反馈">
    <p>已保存 {{ feedback.summary.published_question_count }}/{{ feedback.summary.total_question_count }} 题证据</p>
    <p v-if="feedback.status !== 'complete'">{{ feedback.summary.message }}</p>
    <AppButton v-if="feedback.status === 'publication_pending'" variant="secondary" :disabled="busy" @click="emit('replay')">安全补发未完成证据</AppButton>
    <table v-if="skillChanges.length"><thead><tr><th>训练目标</th><th>训练前</th><th>训练后</th></tr></thead><tbody><tr v-for="item in skillChanges" :key="String(item.stable_key)"><td><details><summary>{{ masteryTitle(item) }}</summary><p>{{ String(item.display_name || '') }}</p><p>{{ String(item.reason || '') }}</p></details></td><td>{{ masteryValue(item, 'mastery_before') }}</td><td>{{ masteryValue(item, 'mastery_after') }}</td></tr></tbody></table>
    <details v-if="aggregateChanges.length" class="aggregate-feedback"><summary>章节与小节汇总（{{ aggregateChanges.length }} 项）</summary><table><tbody><tr v-for="item in aggregateChanges" :key="String(item.stable_key)"><td>{{ masteryTitle(item) }}</td><td>{{ masteryValue(item, 'mastery_before') }} → {{ masteryValue(item, 'mastery_after') }}</td></tr></tbody></table></details>
    <div class="return-next-round"><p>下一轮补练：{{ feedback.next_round.message }}</p><AppButton v-if="feedback.next_round.draft_id" variant="secondary" @click="emit('openDraft', feedback.next_round.draft_id)">打开下一轮草稿</AppButton></div>
    <details v-if="feedback.status !== 'withdrawn'" class="feedback-maintenance"><summary>更正</summary><AppButton variant="danger" :disabled="busy" @click="emit('withdraw')">撤回本次证据</AppButton></details>
  </section>
</template>
<style scoped>
.return-feedback{min-width:0;font-size:var(--font-size-dense);line-height:var(--line-height-body)}p{margin:var(--space-2) 0;color:var(--color-text-secondary)}table{width:100%;border-collapse:collapse;table-layout:fixed;margin-block:var(--space-3)}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--color-border-subtle);padding:var(--space-2);overflow-wrap:anywhere}th{color:var(--color-text-muted);font-weight:500;background:var(--color-bg-subtle)}th:first-child{width:40%}td:nth-child(n+2){font-size:var(--font-size-caption);font-variant-numeric:tabular-nums}summary{cursor:pointer}td p{font-size:var(--font-size-caption)}.aggregate-feedback,.feedback-maintenance{margin-top:var(--space-3);font-size:var(--font-size-caption);color:var(--color-text-secondary)}.feedback-maintenance button{margin-top:var(--space-2)}.return-next-round{margin-top:var(--space-4);padding-top:var(--space-3);border-top:1px solid var(--color-border-default)}
</style>
