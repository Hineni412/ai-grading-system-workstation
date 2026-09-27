<script setup lang="ts">
import { computed, reactive } from 'vue'

import type { AiAssemblyGap, AiAssemblySpec, AiAssemblySpecRow } from '../../api/ai-assembly'
import AppButton from '../design-system/AppButton.vue'

const props = defineProps<{
  spec: AiAssemblySpec
  selections: Record<number, number[]>
  lockedQuestionIds: number[]
  gapByRow: Map<number, AiAssemblyGap>
  actualKnowledgeCount?: number
  templateMode?: boolean
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

const groups = computed(() => {
  const grouped = new Map<string, Array<{ row: AiAssemblySpecRow; rowIndex: number }>>()
  props.spec.rows.forEach((row, rowIndex) => {
    const entries = grouped.get(row.question_type) ?? []
    entries.push({ row, rowIndex })
    grouped.set(row.question_type, entries)
  })
  return [...grouped].map(([type, rows]) => {
    const levels = { easy: 0, medium: 0, hard: 0, unset: 0 }
    const scores = rows.flatMap(({ row }) => row.score === null ? [] : [row.score])
    rows.forEach(({ row }) => {
      const level = row.difficulty === null ? 'unset' : row.difficulty <= 3 ? 'easy' : row.difficulty <= 6 ? 'medium' : 'hard'
      levels[level] += row.count
    })
    return {
      type, rows, levels,
      count: rows.reduce((sum, { row }) => sum + row.count, 0),
      selected: rows.reduce((sum, { rowIndex }) => sum + (props.selections[rowIndex]?.length ?? 0), 0),
      missing: rows.reduce((sum, { rowIndex }) => sum + (props.gapByRow.get(rowIndex)?.missing ?? 0), 0),
      scoreLabel: !scores.length ? '分值待定' : scores.length !== rows.length ? '部分分值待定'
        : Math.min(...scores) === Math.max(...scores) ? `每题 ${scores[0]} 分`
          : `每题 ${Math.min(...scores)}～${Math.max(...scores)} 分`,
    }
  })
})

const lockedSet = computed(() => new Set(props.lockedQuestionIds))

// 选题前 selections 为空，整列（含表头）不渲染。
const showLockColumn = computed(() => Object.keys(props.selections).length > 0)

// 名录联想候选：全卷考察范围里的知识点全路径。
const knowledgePointOptions = computed(() => props.spec.scope_knowledge_points)

const knowledgeDrafts = reactive<Record<number, string>>({})

function leafName(path: string): string {
  const segments = path.split('｜')
  return (segments[segments.length - 1] ?? path).trim() || path
}

function resolveKnowledgeInput(raw: string): string {
  const value = raw.trim()
  if (!value) return ''
  if (knowledgePointOptions.value.includes(value)) return value
  // 末端名唯一匹配名录时转回全路径；匹配不上保留原文，后端按原文精确匹配。
  const matches = knowledgePointOptions.value.filter((option) => leafName(option) === value)
  return matches.length === 1 ? (matches[0] ?? value) : value
}

function removeKnowledgePoint(rowIndex: number, pointIndex: number): void {
  const row = props.spec.rows[rowIndex]
  if (!row) return
  emit('editRow', rowIndex, {
    knowledge_points: row.knowledge_points.filter((_, index) => index !== pointIndex),
  })
}

function addKnowledgePoint(rowIndex: number): void {
  const row = props.spec.rows[rowIndex]
  const resolved = resolveKnowledgeInput(knowledgeDrafts[rowIndex] ?? '')
  knowledgeDrafts[rowIndex] = ''
  if (!row || !resolved || row.knowledge_points.includes(resolved)) return
  emit('editRow', rowIndex, {
    knowledge_points: [...row.knowledge_points, resolved],
  })
}

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
</script>

<template>
  <div class="ai-spec-table" data-testid="ai-spec-table">
    <div class="ai-spec-table__summary" aria-label="细目表汇总">
      <span><strong>{{ totalQuestions }}</strong> 道题</span>
      <span>易 {{ difficultySummary.easy }} · 中 {{ difficultySummary.medium }} · 难 {{ difficultySummary.hard }}<template v-if="difficultySummary.unset"> · 未指定 {{ difficultySummary.unset }}</template></span>
      <span>计划覆盖 {{ knowledgeCoverage }} 个知识点</span>
      <span v-if="actualKnowledgeCount !== undefined">已选题实际覆盖 {{ actualKnowledgeCount }} 个范围内知识点</span>
    </div>
    <p class="ai-spec-table__help">按题型查看整卷安排，展开可调整配置。优先知识点不足时，在所选考察范围内补位。<template v-if="templateMode">模板题型和题量保持不变。</template>分值为规划建议，当前落卷沿用原题分值。</p>
    <details v-for="group in groups" :key="group.type" class="ai-spec-table__group">
      <summary>
        <strong>{{ group.type }} · 共 {{ group.count }} 题</strong>
        <span>易 {{ group.levels.easy }} · 中 {{ group.levels.medium }} · 难 {{ group.levels.hard }}<template v-if="group.levels.unset"> · 不限 {{ group.levels.unset }}</template></span>
        <span>{{ group.scoreLabel }}</span>
        <span v-if="showLockColumn" :class="{ 'ai-spec-table__gap-count': group.missing }">已选 {{ group.selected }}/{{ group.count }}<template v-if="group.missing"> · 缺 {{ group.missing }} 题</template></span>
        <span class="ai-spec-table__expand">展开调整</span>
        <span class="ai-spec-table__collapse">收起调整</span>
      </summary>
      <div class="ai-spec-table__scroll">
      <table>
      <thead>
        <tr>
          <th>配置</th>
          <th>数量</th>
          <th>优先知识点</th>
          <th>难度</th>
          <th>建议分值</th>
          <th v-if="showLockColumn">锁定</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="{ row, rowIndex } in group.rows"
          :key="rowIndex"
          :class="{ 'has-gap': gapByRow.has(rowIndex) }"
        >
          <td class="ai-spec-table__row-label">配置 {{ rowIndex + 1 }}<small v-if="row.essay_subtype">{{ row.essay_subtype }}</small></td>
          <td>
            <input
              type="number"
              min="1"
              max="200"
              :value="row.count"
              :disabled="templateMode"
              :aria-label="`第 ${rowIndex + 1} 行数量`"
              @change="onCount(rowIndex, $event)"
            >
          </td>
          <td class="ai-spec-table__knowledge">
            <div class="ai-spec-table__chips">
              <span
                v-for="(point, pointIndex) in row.knowledge_points"
                :key="point"
                class="ai-spec-table__chip"
                :title="point"
              >
                {{ leafName(point) }}
                <button
                  type="button"
                  class="ai-spec-table__chip-remove"
                  :aria-label="`删除知识点 ${leafName(point)}`"
                  @click="removeKnowledgePoint(rowIndex, pointIndex)"
                >
                  ×
                </button>
              </span>
              <input
                v-model="knowledgeDrafts[rowIndex]"
                type="text"
                :list="`ai-spec-kp-options-${rowIndex}`"
                placeholder="添加知识点"
                :aria-label="`第 ${rowIndex + 1} 行添加知识点`"
                @keydown.enter.prevent="addKnowledgePoint(rowIndex)"
                @change="addKnowledgePoint(rowIndex)"
              >
              <datalist :id="`ai-spec-kp-options-${rowIndex}`">
                <option
                  v-for="option in knowledgePointOptions"
                  :key="option"
                  :value="leafName(option)"
                  :label="option"
                />
              </datalist>
            </div>
          </td>
          <td>
            <select
              :value="row.difficulty === null ? '' : String(row.difficulty)"
              :aria-label="`第 ${rowIndex + 1} 行难度`"
              @change="onDifficulty(rowIndex, $event)"
            >
              <option value="">不限</option>
              <option v-for="level in 10" :key="level" :value="String(level)">{{ level }}</option>
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
          <td v-if="showLockColumn" class="ai-spec-table__lock">
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
    </details>
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
  background: color-mix(in srgb, var(--color-danger) 8%, transparent);
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

.ai-spec-table__chips {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ai-spec-table__chip {
  align-items: center;
  background: var(--muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-text-primary);
  display: inline-flex;
  font-size: 12px;
  gap: 4px;
  padding: 2px 6px 2px 10px;
  white-space: nowrap;
}

.ai-spec-table__chip-remove {
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  padding: 0 2px;
}

.ai-spec-table__chip-remove:hover {
  color: var(--color-danger);
}

.ai-spec-table__knowledge input {
  flex: 1;
  min-width: 120px;
}

.ai-spec-table__help { color: var(--color-text-secondary); font-size: 12px; margin: 0; }
.ai-spec-table__group { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.ai-spec-table__group summary { align-items: center; background: var(--muted); cursor: pointer; display: flex; flex-wrap: wrap; gap: 12px 24px; padding: 14px 16px; font-size: 13px; }
.ai-spec-table__group summary strong { min-width: 150px; }
.ai-spec-table__group summary:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.ai-spec-table__expand, .ai-spec-table__collapse { color: var(--primary); margin-left: auto; }
.ai-spec-table__collapse, .ai-spec-table__group[open] .ai-spec-table__expand { display: none; }
.ai-spec-table__group[open] .ai-spec-table__collapse { display: inline; }
.ai-spec-table__gap-count { color: var(--color-danger); }
.ai-spec-table__scroll { overflow-x: auto; padding: 0 8px 8px; }
.ai-spec-table__row-label { min-width: 50px; }
.ai-spec-table__row-label small { display: block; color: var(--color-text-secondary); }
</style>
