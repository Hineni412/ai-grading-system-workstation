<script setup lang="ts">
import { computed } from 'vue'

import type { AiAssemblyGap, AiAssemblySpec, AiAssemblySpecRow } from '../../api/ai-assembly'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  spec: AiAssemblySpec
  selections: Record<number, number[]>
  lockedQuestionIds: number[]
  gapByRow: Map<number, AiAssemblyGap>
}>()

const emit = defineEmits<{
  editRow: [rowIndex: number, patch: Partial<AiAssemblySpecRow>]
  showGap: [gap: AiAssemblyGap]
}>()

const totalQuestions = computed(() => (
  props.spec.rows.reduce((sum, row) => sum + row.count, 0)
))

const difficultySummary = computed(() => {
  const buckets = { easy: 0, medium: 0, hard: 0, unset: 0 }
  for (const row of props.spec.rows) {
    if (row.difficulty === null) buckets.unset += row.count
    else if (row.difficulty <= 3) buckets.easy += row.count
    else if (row.difficulty <= 6) buckets.medium += row.count
    else buckets.hard += row.count
  }
  return buckets
})

const knowledgeCoverage = computed(() => (
  new Set(props.spec.rows.flatMap((row) => row.knowledge_points)).size
))

const lockedSet = computed(() => new Set(props.lockedQuestionIds))

function lockedCount(rowIndex: number): number {
  return (props.selections[rowIndex] ?? []).filter((id) => lockedSet.value.has(id)).length
}

function onCount(rowIndex: number, event: Event): void {
  const value = Number((event.target as HTMLInputElement).value)
  if (Number.isSafeInteger(value)) emit('editRow', rowIndex, { count: value })
}

function onDifficulty(rowIndex: number, event: Event): void {
  const raw = (event.target as HTMLSelectElement).value
  emit('editRow', rowIndex, { difficulty: raw === '' ? null : Number(raw) })
}

function onScore(rowIndex: number, event: Event): void {
  const raw = (event.target as HTMLInputElement).value.trim()
  const value = Number(raw)
  emit('editRow', rowIndex, {
    score: raw === '' || !Number.isFinite(value) || value <= 0 ? null : value,
  })
}

function onKnowledgePoints(rowIndex: number, event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  emit('editRow', rowIndex, {
    knowledge_points: raw.split(/[,，、]/).map((item) => item.trim()).filter(Boolean),
  })
}
</script>

<template>
  <div class="ai-spec-table" data-testid="ai-spec-table">
    <div class="ai-spec-table__summary" aria-label="细目表汇总">
      <span><strong>{{ totalQuestions }}</strong> 道题</span>
      <span>易 {{ difficultySummary.easy }} · 中 {{ difficultySummary.medium }} · 难 {{ difficultySummary.hard }}<template v-if="difficultySummary.unset"> · 未指定 {{ difficultySummary.unset }}</template></span>
      <span>覆盖 {{ knowledgeCoverage }} 个知识点</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>题型</th>
          <th>数量</th>
          <th>知识点</th>
          <th>难度</th>
          <th>分值</th>
          <th>锁定</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, rowIndex) in spec.rows"
          :key="rowIndex"
          :class="{ 'has-gap': gapByRow.has(rowIndex) }"
        >
          <td>{{ row.question_type }}</td>
          <td>
            <input
              type="number"
              min="1"
              max="200"
              :value="row.count"
              :aria-label="`第 ${rowIndex + 1} 行数量`"
              @change="onCount(rowIndex, $event)"
            >
          </td>
          <td>
            <input
              type="text"
              :value="row.knowledge_points.join('、')"
              placeholder="逗号分隔，可留空"
              :aria-label="`第 ${rowIndex + 1} 行知识点`"
              @change="onKnowledgePoints(rowIndex, $event)"
            >
          </td>
          <td>
            <select
              :value="row.difficulty === null ? '' : String(row.difficulty)"
              :aria-label="`第 ${rowIndex + 1} 行难度`"
              @change="onDifficulty(rowIndex, $event)"
            >
              <option value="">不限</option>
              <option v-for="level in 9" :key="level" :value="String(level)">{{ level }}</option>
            </select>
          </td>
          <td>
            <input
              type="number"
              min="0.5"
              step="0.5"
              :value="row.score ?? ''"
              :aria-label="`第 ${rowIndex + 1} 行分值`"
              @change="onScore(rowIndex, $event)"
            >
          </td>
          <td class="ai-spec-table__lock">
            <span v-if="lockedCount(rowIndex)" :title="`已锁定 ${lockedCount(rowIndex)} 题`">
              🔒 {{ lockedCount(rowIndex) }}
            </span>
            <AppButton
              v-if="gapByRow.has(rowIndex)"
              variant="ghost"
              @click="emit('showGap', gapByRow.get(rowIndex)!)"
            >
              缺 {{ gapByRow.get(rowIndex)!.missing }} 题 · 放宽建议
            </AppButton>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.ai-spec-table {
  display: grid;
  gap: 10px;
}

.ai-spec-table__summary {
  color: var(--color-text-secondary);
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 16px;
}

.ai-spec-table__summary strong {
  color: var(--color-text-primary);
  font-size: 16px;
}

.ai-spec-table table {
  border-collapse: collapse;
  font-size: 13px;
  width: 100%;
}

.ai-spec-table th,
.ai-spec-table td {
  border-bottom: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}

.ai-spec-table th {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.ai-spec-table tr.has-gap td {
  background: color-mix(in srgb, var(--color-danger, #dc2626) 8%, transparent);
}

.ai-spec-table input,
.ai-spec-table select {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--color-text-primary);
  font-size: 13px;
  padding: 4px 6px;
  width: 100%;
}

.ai-spec-table input[type='number'] {
  max-width: 72px;
}

.ai-spec-table__lock {
  white-space: nowrap;
}
</style>
